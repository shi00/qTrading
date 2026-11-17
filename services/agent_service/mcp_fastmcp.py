"""真实 FastMCP 传输适配（E-02-5 needs-spike 实证）。

插入点与隔离边界
----------------
- :class:`FastMCPClientFactory` 实现 ``MCPClientSessionPort`` / ``MCPClientFactoryPort``
  （:mod:`services.agent_service.mcp`），由共享连接管理器在**用户显式启连/测试**
  时调用；保存配置与普通读取不触发任何进程/网络。
- stdio：``StdioTransport(绝对 command, args, 最小环境, cwd, log_file=<有界排空器>)``，
  不经 shell；``log_file`` 接受一个 OS 管道写端，由后台线程持续读取子进程 stderr
  （FD 级，避免管道不读导致阻塞），逐行先脱敏再进入有界内存缓冲，**不落盘**。
- streamable_http / sse：``StreamableHttpTransport`` / ``SSETransport``，只用已冻结
  的 URL；SSE 仍须显式核准（由 ``validate_transport_config`` 保证）。两条 HTTP 传输
  都注入显式 ``httpx_client_factory``（见下），不依赖 SDK 默认 client。

逐 send 门
----------
:class:`MCPSendGate` 在**每次**出站 SDK 请求（``list_tools`` / ``call_tool``）前
检查会话是否仍存活，并为**本次 operation** 新建独立 deadline（不复用连接期起点，
故闲置后新调用不会因旧 deadline 过时而拒绝）；断线后拒绝发送，不自动重连。

出站目标核验（真实插入点）
--------------------------
目标核验不在 operation 层（那里看不到 SDK 内部实际发出的请求），而在
``httpx2.AsyncClient`` 的传输层：:class:`_GuardedHTTPTransport` 包装底层
``AsyncHTTPTransport``，``AsyncClient.send`` 的**每个**请求（含 ``initialize``
握手、POST/GET/DELETE、SDK 自己的重定向跟随）都先经它核验，再委托真实传输。
核验内容与来源：``config.transport.url`` 在启连时冻结为 scheme/host/port/path，
请求与冻结值不一致即拒绝（``POLICY_DENIED``，零字节出站）；带 ``Last-Event-ID``
的恢复 GET 同样拒绝。client 另以 ``trust_env=False``（禁代理/环境证书改写）、
``follow_redirects=False`` 与 ``max_redirects=0`` 构造，重定向不静默跟随。
SSE 兼容模式下若服务端上报的消息端点路径与配置端点不同，该路径未获批，按同一
规则拒绝并如实上报（不放宽路径）。

仍属残留（不得宣称已验证）
--------------------------
原始请求字节/逐请求往返计量仍不可观测（SDK 类型化客户端在库内完成）；HTTP 侧
拒绝发送以 ``POLICY_DENIED`` 抛出，若 SDK 内部任务组先行收尾，调用方可能先看到
超时——但诊断通道已记录拒绝原因，不构成「静默放行」。

四诊断通道
----------
:class:`MCPSessionDiagnostics` 固定四路：``stderr_tail``（FD 级、有界且已脱敏的尾部）、
``last_error``（最后一次错误类别）、``protocol_errors``（按序记录）、
``exit_status``（**经 FastMCP 托管进程时不可观测，保持 None「未知」而不是 0**）。

仍属残留（不得宣称已验证）
--------------------------
FastMCP 类型化客户端在库内完成解析，本层无法看到原始字节流：``receive_bytes``
族仍无真实计量入口（仅能以解码后表示计量 ``decoded_bytes``）；进程退出码不可
观测。见 ``docs/debt/known-technical-debt.md`` 的 P2-AgentMCP-Residual-Risks。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import httpx2
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport

from core.agent_types import AgentErrorCode, AgentServiceError, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPToolDescriptor
from services.agent_service.mcp_budget import MCPBudgetGuard, MCPDeadline
from utils.sanitizers import DataSanitizer

if TYPE_CHECKING:
    from typing import TextIO

#: 子进程最小 OS 基线（Windows 缺少这些值会导致解释器无法启动）；不含应用环境副本。
_OS_BASELINE_KEYS: tuple[str, ...] = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "TEMP", "TMP")

#: stderr 诊断尾部保留字节上限（内存缓冲与公开属性共用同一上限）。
_STDERR_TAIL_BYTES = 4096
#: 单行保留字节上限；超长行截断并丢弃该行剩余部分。
_STDERR_LINE_BYTES = 2048
#: 关闭时等待 drain 线程收到 EOF 的上限。
_STDERR_DRAIN_JOIN_TIMEOUT_S = 2.0
#: 超长行截断标记。
_STDERR_TRUNCATED_MARKER = "…[truncated]"


@dataclass
class MCPSessionDiagnostics:
    """四诊断通道（固定形状；不可观测的通道保持 ``None`` 而非伪造值）。"""

    stderr_tail: str = ""
    last_error: str | None = None
    protocol_errors: list[str] = field(default_factory=list)
    exit_status: int | None = None

    def record(self, error: str) -> None:
        self.last_error = error
        self.protocol_errors.append(error)


class _SanitizedStderrDrain:
    """有界、先脱敏的 stderr 排空器（取代无界落盘日志文件）。

    子进程 stderr 写入 OS 管道写端（``log_file`` 文件对象），后台守护线程持续
    读取：每行先经 :class:`DataSanitizer` 脱敏，再进入受 ``max_bytes`` 约束的
    内存缓冲；超长行截断并丢弃剩余部分，超额时丢弃最旧行。任何内容都不落盘；
    关闭时冻结尾部并清空缓冲（运行期留存归零）。
    """

    def __init__(
        self,
        *,
        max_bytes: int = _STDERR_TAIL_BYTES,
        max_line_bytes: int = _STDERR_LINE_BYTES,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_line_bytes = max_line_bytes
        read_fd, write_fd = os.pipe()
        self._reader = os.fdopen(read_fd, "rb")
        self._writer = os.fdopen(write_fd, "wb", buffering=0)
        self._lines: deque[str] = deque()
        self._bytes = 0
        self._lock = threading.Lock()
        self._closing = False
        self._thread = threading.Thread(target=self._pump, name="mcp-stderr-drain", daemon=True)
        self._thread.start()

    # -- 子进程 stderr 目标（file-like） ---------------------------------

    def fileno(self) -> int:
        """返回管道写端 FD（交给 ``subprocess`` 的 ``stderr``）。"""
        return self._writer.fileno()

    def write(self, text: str) -> int:
        """兼容 TextIO 接口：与后台线程同路径脱敏并入有界缓冲。"""
        self._store(text.encode("utf-8", errors="replace"))
        return len(text)

    def flush(self) -> None:
        """无缓冲需要刷新（脱敏在写入时已完成）。"""

    # -- 生命周期与读取 ---------------------------------------------------

    def close(self) -> None:
        """关闭写端等 EOF、冻结尾部；可重复调用，不抛异常。"""
        with suppress(OSError, ValueError):
            self._writer.close()
        if self._thread.is_alive():
            self._thread.join(timeout=_STDERR_DRAIN_JOIN_TIMEOUT_S)
        with self._lock:
            self._closing = True
        with suppress(OSError, ValueError):
            self._reader.close()

    def tail(self) -> str:
        """返回已脱敏、有界的尾部（字节总量不超过 ``max_bytes``）。"""
        with self._lock:
            return "\n".join(self._lines)

    def clear(self) -> None:
        """清空内存缓冲（关闭后调用，消除运行期留存）。"""
        with self._lock:
            self._lines.clear()
            self._bytes = 0

    @property
    def retained_bytes(self) -> int:
        """当前内存缓冲占用字节数（有界性断言用）。"""
        with self._lock:
            return self._bytes

    def _pump(self) -> None:
        while True:
            try:
                line = self._reader.readline(self._max_line_bytes)
            except (OSError, ValueError):
                return
            if not line:
                return
            if len(line) >= self._max_line_bytes and not line.endswith(b"\n"):
                self._store(line, truncated=True)
                if not self._skip_overlong_remainder():
                    return
                continue
            self._store(line)

    def _skip_overlong_remainder(self) -> bool:
        """丢弃超长行剩余字节直到换行；返回 False 表示流已结束。"""
        while True:
            try:
                rest = self._reader.readline(self._max_line_bytes)
            except (OSError, ValueError):
                return False
            if not rest:
                return False
            if rest.endswith(b"\n"):
                return True

    def _store(self, raw: bytes, *, truncated: bool = False) -> None:
        text = DataSanitizer.sanitize_error(raw.decode("utf-8", errors="replace")).rstrip("\r\n")
        if truncated:
            text = f"{text}{_STDERR_TRUNCATED_MARKER}"
        encoded = text.encode("utf-8")
        if len(encoded) > self._max_bytes:
            text = encoded[: self._max_bytes].decode("utf-8", errors="ignore")
            encoded = text.encode("utf-8")
        with self._lock:
            if self._closing:
                return
            self._lines.append(text)
            self._bytes += len(encoded) + 1
            while self._bytes > self._max_bytes and len(self._lines) > 1:
                self._bytes -= len(self._lines.popleft().encode("utf-8")) + 1


def _discard_drain(drain: _SanitizedStderrDrain | None) -> None:
    """连接受阻/失败时收尾排空器：关管道并清空缓冲，不留运行期留存。"""
    if drain is None:
        return
    drain.close()
    drain.clear()


#: 目标与冻结值不一致（含内嵌 userinfo）时的诊断标签。
_TARGET_NOT_APPROVED = "http_target_not_approved"
#: scheme/host/port 不一致（跨源）时的诊断标签。
_ORIGIN_NOT_APPROVED = "http_origin_not_approved"
#: 同源但路径未获批时的诊断标签。
_PATH_NOT_APPROVED = "http_path_not_approved"
#: 恢复 GET（带 ``Last-Event-ID``）未获批时的诊断标签。
_RESUMPTION_NOT_APPROVED = "http_resumption_not_approved"


@dataclass(frozen=True, slots=True)
class _ApprovedHTTPTarget:
    """启连时冻结的 HTTP 目标（scheme/host/port/path 精确匹配；不含查询串）。"""

    scheme: str
    host: str
    port: int | None
    raw_path: bytes

    @classmethod
    def from_url(cls, url: str) -> _ApprovedHTTPTarget:
        """按 httpx2 规范化规则解析配置 URL（默认端口归一为 ``None``、host 小写）。"""
        parsed = httpx2.URL(url)
        return cls(scheme=parsed.scheme, host=parsed.host, port=parsed.port, raw_path=parsed.raw_path)

    def refusal(self, target: httpx2.URL) -> str | None:
        """返回拒绝原因标签；``None`` 表示请求目标与冻结值一致。"""
        if target.userinfo:
            # 重定向 Location 自带 userinfo 会注入 Basic auth，一律不批。
            return _TARGET_NOT_APPROVED
        if (target.scheme, target.host, target.port) != (self.scheme, self.host, self.port):
            return _ORIGIN_NOT_APPROVED
        if target.raw_path != self.raw_path:
            return _PATH_NOT_APPROVED
        return None


class _GuardedHTTPTransport(httpx2.AsyncBaseTransport):
    """每次 HTTP 发送前的目标核验（``AsyncClient.send`` 的真实传输插入点）。

    包装底层传输：与冻结目标不一致（scheme/host/port/path）、或带 ``Last-Event-ID``
    的恢复 GET，在委托底层传输前抛 ``POLICY_DENIED``，**不产生任何字节出站**；
    重定向因此不会被静默跟随（307/308、跨源、新路径的后续请求同样被拒）。
    """

    def __init__(
        self,
        *,
        approved: _ApprovedHTTPTarget,
        diagnostics: MCPSessionDiagnostics,
        identity: str,
        inner: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._approved = approved
        self._diagnostics = diagnostics
        self._identity = identity
        self._inner = inner if inner is not None else httpx2.AsyncHTTPTransport(trust_env=False)

    @property
    def inner(self) -> httpx2.AsyncBaseTransport:
        """底层传输（测试断言「零发送」用）。"""
        return self._inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        reason = self._refusal(request)
        if reason is not None:
            self._diagnostics.record(reason)
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, f"mcp http send refused: {reason}", self._identity)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()

    def _refusal(self, request: httpx2.Request) -> str | None:
        if request.headers.get("last-event-id") is not None:
            return _RESUMPTION_NOT_APPROVED
        return self._approved.refusal(request.url)


def _guarded_http_client(
    approved: _ApprovedHTTPTarget,
    diagnostics: MCPSessionDiagnostics,
    identity: str,
    *,
    inner: httpx2.AsyncBaseTransport | None = None,
) -> Callable[..., httpx2.AsyncClient]:
    """构造注入 SDK 的 ``httpx_client_factory``：目标核验 + 禁代理环境 + 不跟随重定向。

    SDK（fastmcp/mcp）把 ``follow_redirects=True`` 传给工厂；此处显式忽略，恒为 False，
    并以 ``max_redirects=0`` 让 SDK 自身的「同源重定向跟随」也一步都不发。
    """

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        *,
        follow_redirects: bool = False,
    ) -> httpx2.AsyncClient:
        del follow_redirects
        return httpx2.AsyncClient(
            headers=headers,
            auth=auth,
            timeout=timeout if timeout is not None else httpx2.Timeout(30.0, read=300.0),
            trust_env=False,
            follow_redirects=False,
            max_redirects=0,
            transport=_GuardedHTTPTransport(
                approved=approved,
                diagnostics=diagnostics,
                identity=identity,
                inner=inner,
            ),
        )

    return factory


class MCPSendGate:
    """每次出站请求前的门：会话存活 + 本次 operation 独立 deadline；记录出站次数。"""

    def __init__(
        self,
        *,
        alive: Callable[[], bool],
        deadline_factory: Callable[[int], MCPDeadline],
        identity: str,
    ) -> None:
        self._alive = alive
        self._deadline_factory = deadline_factory
        self._identity = identity
        self._sends = 0

    @property
    def sends(self) -> int:
        return self._sends

    def before_send(self, operation: str, *, deadline_s: int) -> MCPDeadline:
        """计数，并为本次 operation 新建 deadline（不从连接期继承起点）。"""
        self._sends += 1
        if not self._alive():
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE, "mcp session is lost; refusing to send", self._identity
            )
        deadline = self._deadline_factory(deadline_s)
        if deadline.expired():
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED, f"mcp deadline exceeded before send: {operation}", self._identity
            )
        return deadline


class FastMCPSession:
    """``MCPClientSessionPort`` 的真实实现（FastMCP 类型化客户端 + 逐 send 门）。"""

    def __init__(
        self,
        client: Client[Any],
        *,
        config: MCPServerConfig,
        guard: MCPBudgetGuard,
        gate: MCPSendGate,
        diagnostics: MCPSessionDiagnostics,
        stderr_drain: _SanitizedStderrDrain | None,
        on_close: Callable[[], None],
    ) -> None:
        self._client = client
        self._config = config
        self._guard = guard
        self._gate = gate
        self._diagnostics = diagnostics
        self._stderr_drain = stderr_drain
        self._on_close = on_close
        self._closed = False
        self._stderr_drain_closed = False

    @property
    def diagnostics(self) -> MCPSessionDiagnostics:
        return self._diagnostics

    @property
    def gate(self) -> MCPSendGate:
        return self._gate

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        deadline = self._gate.before_send("list_tools", deadline_s=deadline_s)
        try:
            async with asyncio.timeout(deadline.remaining()):
                tools = await self._client.list_tools()
        except TimeoutError:
            self._diagnostics.record("list_tools_timeout")
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diagnostics.record(f"list_tools_failed:{type(exc).__name__}")
            raise
        descriptors: list[MCPToolDescriptor] = []
        for tool in tools:
            schema_json = json.dumps(tool.input_schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            self._guard.catalog_item()
            self._guard.catalog_bytes(len(tool.name.encode("utf-8")) + len(schema_json.encode("utf-8")))
            self._guard.schema_bytes(len(schema_json.encode("utf-8")))
            descriptors.append(MCPToolDescriptor(name=tool.name, schema_json=schema_json))
        return tuple(descriptors)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        deadline = self._gate.before_send("call_tool", deadline_s=deadline_s)
        try:
            async with asyncio.timeout(deadline.remaining()):
                result = await self._client.call_tool(name, arguments)
        except TimeoutError:
            self._diagnostics.record("call_tool_timeout")
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diagnostics.record(f"call_tool_failed:{type(exc).__name__}")
            raise
        payload = _result_payload(result)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
        self._guard.decoded_bytes(len(encoded.encode("utf-8")))
        return payload

    async def aclose(self) -> None:
        if self._closed:
            return
        try:
            await self._client.__aexit__(None, None, None)
        finally:
            # 关闭一经尝试即不再接受新发送；stderr 清理只做一次，失败重试不覆盖已冻结尾部。
            self._on_close()
            if not self._stderr_drain_closed:
                self._stderr_drain_closed = True
                drain = self._stderr_drain
                if drain is not None:
                    drain.close()
                    try:
                        self._diagnostics.stderr_tail = drain.tail()
                    finally:
                        # 属性已持有有界脱敏副本；清空内存缓冲，不留运行期留存。
                        drain.clear()
        # 只有实际退出成功才标记已关闭：失败/取消后的再次 aclose 仍会真正重试退出（I17）。
        self._closed = True


def _result_payload(result: Any) -> dict[str, Any]:
    """FastMCP ``CallToolResult`` → 合同内字典（保留结构；不丢原值）。"""

    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    blocks = [block.model_dump(mode="json") if hasattr(block, "model_dump") else str(block) for block in content]
    if data is not None:
        return {"data": data, "content": blocks}
    return {"content": blocks}


class FastMCPClientFactory:
    """``MCPClientFactoryPort`` 真实实现（仅在显式启连/测试时被调用）。"""

    def __init__(
        self,
        *,
        deadline_factory: Callable[[int], MCPDeadline] | None = None,
    ) -> None:
        self._deadline_factory = deadline_factory or (lambda seconds: MCPDeadline(seconds=seconds))

    async def open(self, config: MCPServerConfig) -> FastMCPSession:
        """建立真实连接（仅显式启连/测试调用）；失败映射为 ``CONFIG_UNAVAILABLE``。"""
        label = config.identity.stable_key()
        # 诊断先于 client 构造：HTTP 目标核验在传输层记录，须与连接期共用同一实例。
        diagnostics = MCPSessionDiagnostics()
        drain = _SanitizedStderrDrain() if config.transport.kind is MCPTransportKind.STDIO else None
        transport = self._transport(config, drain, diagnostics=diagnostics, label=label)
        client: Client[Any] = Client(
            transport,
            timeout=config.tool_deadline_s,
            init_timeout=config.connect_deadline_s,
        )
        try:
            await client.__aenter__()
        except Exception as exc:
            _discard_drain(drain)
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                f"mcp connection failed: {type(exc).__name__}",
                label,
            ) from exc
        except BaseException:
            # 取消/键盘中断等不转译，但必须收尾排空器（不吞 CancelledError）。
            _discard_drain(drain)
            raise
        closed = False

        def _mark_closed() -> None:
            nonlocal closed
            closed = True

        def _alive() -> bool:
            return not closed

        return FastMCPSession(
            client,
            config=config,
            guard=MCPBudgetGuard(config.budget, identity=label),
            gate=MCPSendGate(
                alive=_alive,
                deadline_factory=self._deadline_factory,
                identity=label,
            ),
            diagnostics=diagnostics,
            stderr_drain=drain,
            on_close=_mark_closed,
        )

    # -- 内部 ---------------------------------------------------------------

    def _transport(
        self,
        config: MCPServerConfig,
        drain: _SanitizedStderrDrain | None,
        *,
        diagnostics: MCPSessionDiagnostics,
        label: str,
    ) -> Any:
        transport = config.transport
        if transport.kind is MCPTransportKind.STDIO:
            command = transport.command
            assert command is not None
            assert drain is not None
            return StdioTransport(
                command=command,
                args=list(transport.args),
                env=self._build_env(transport.env),
                cwd=transport.cwd,
                keep_alive=False,
                log_file=cast("TextIO", drain),
            )
        assert transport.url is not None
        factory = _guarded_http_client(_ApprovedHTTPTarget.from_url(transport.url), diagnostics, label)
        if transport.kind is MCPTransportKind.STREAMABLE_HTTP:
            return StreamableHttpTransport(transport.url, httpx_client_factory=factory)
        return SSETransport(transport.url, httpx_client_factory=factory)

    @staticmethod
    def _build_env(configured: tuple[tuple[str, str], ...]) -> dict[str, str]:
        """最小 OS 基线 + 显式配置项（保序）；不复制应用环境。"""

        env = {key: os.environ[key] for key in _OS_BASELINE_KEYS if key in os.environ}
        env.update(configured)
        return env
