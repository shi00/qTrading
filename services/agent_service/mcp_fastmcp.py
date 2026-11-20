"""真实 FastMCP 传输适配（E-02-5 needs-spike 实证）。

插入点与隔离边界
----------------
- :class:`FastMCPClientFactory` 实现 ``MCPClientSessionPort`` / ``MCPClientFactoryPort``
  （:mod:`services.agent_service.mcp`），由共享连接管理器在**用户显式启连/测试**
  时调用；保存配置与普通读取不触发任何进程/网络。
- stdio：``BudgetedStdioTransport``（:mod:`services.agent_service.mcp_stdio_transport`，
  绝对 command、args、显式环境、cwd、errlog=<有界排空器>），不经 shell；errlog 接受
  一个 OS 管道写端，由后台线程持续读取子进程 stderr（FD 级，避免管道不读导致阻塞），
  逐行先脱敏再进入有界内存缓冲，**不落盘**。自管子进程使入站限额有了解析前的真实插入点。
- stdio 环境（I42）：锁定版 SDK 的 ``mcp.client.stdio.stdio_client`` 以
  ``get_default_environment() | (server.env or {})`` 生成子进程环境（源码实证：
  ``mcp/client/stdio.py`` 的 ``stdio_client`` → ``_create_platform_compatible_process``），
  即**不复制父进程完整 ``os.environ``**，只继承一份固定的非秘密 OS 清单（Windows 12 键 /
  POSIX 6 键）。本模块仍**显式覆盖**该清单的全部键并把完整环境交给自管进程，使子进程
  环境完全由本模块决定（SDK 默认清单变化也不会引入新的隐式继承），且 ``TEMP`` / ``TMP``
  强制指向本次连接专属受管目录（非应用/用户临时目录），关闭或连接失败时删除，不留残留。
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

入站限额的真实插入点（I03）
----------------------------
- HTTP / SSE：响应字节先经 :class:`_BudgetedInboundStream`（替换 ``httpx2.Response.stream``，
  SDK 的 event source 从同一响应迭代）计入 ``receive_bytes`` 并以增量扫描器判定
  ``structure_depth``，超限即拒绝，**完整消息对象从未被构造**；streamable HTTP 另把
  已冻结的 ``receive_bytes`` 传给 SDK 的 ``max_sse_event_size``，形成解析前的第二道
  上限。压缩响应（非 identity 编码）无法前置判定帧长/深度，一律拒绝而非放行。
- stdio：``BudgetedStdioTransport`` 自管子进程，把 stdout 原始字节块先交给
  ``_BudgetedLineReader.feed``：``receive_bytes`` 按块计入、``structure_depth`` 由增量
  扫描器判定，二者都在切行/解码/解析之前执行，超限块不进入缓冲区，**完整消息对象从未
  被构造**。超限以 fail-closed 方式拆除连接（读流关闭，在途请求立即失败而非挂起），
  原因记入诊断 ``inbound_limit:<family>``；不承诺以 ``MCPBudgetExceeded`` 原类型抵达
  调用方（SDK 默认 ``message_handler`` 会消费读流中的 ``Exception`` 值）。

仍属残留（不得宣称已验证）
--------------------------
http/SSE 的逐请求往返耗时不可观测。目录/解码/schema/单条四族只能在解析后得到
（条目数、规范长度无法从字节流预知），故其拒绝点必然晚于接收；但计量本身改为
**增量计数**：累计超过该族剩余额度即中止，不再为比较而完整物化规范 JSON 文本，且总
接收量仍由解析前的 ``receive_bytes`` 门约束（超出的字节不会交给 SDK）。留存族当前
无调用点与释放。stdio 超限的失败原因是诊断记录而非原异常类型，调用方看到的是连接关闭
（``CONNECTION_CLOSED``）。

四诊断通道
----------
:class:`MCPSessionDiagnostics` 固定四路：``stderr_tail``（FD 级、有界且已脱敏的尾部）、
``last_error``（最后一次错误类别）、``protocol_errors``（按序记录，留存前脱敏且条数有界）、
``exit_status``（stdio 自管进程退出后可观测；未观测到保持 ``None``「未知」而不是 0；
HTTP/SSE 无子进程，保持 ``None``）。见 ``docs/debt/known-technical-debt.md`` 的
P2-AgentMCP-Residual-Risks。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx2
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

from core.agent_types import AgentErrorCode, AgentServiceError, AgentValidationError, MCPTransportKind
from services.agent_service.mcp import (
    MCPCallNotDispatched,
    MCPServerConfig,
    MCPToolDescriptor,
    validate_mcp_server_config,
)
from services.agent_service.mcp_budget import (
    JsonDepthScanner,
    MCPBudgetGuard,
    MCPDeadline,
    encode_json_text_capped,
    measure_json_bytes,
)
from services.agent_service.mcp_stdio_transport import BudgetedStdioTransport
from utils.sanitizers import DataSanitizer

if TYPE_CHECKING:
    from typing import TextIO

#: 子进程显式环境基线（覆盖 SDK ``get_default_environment()`` 在 Windows / POSIX 的
#: 全部可注入键）：只为进程启动所必需的 OS 非秘密变量，不含应用秘密副本。
_OS_BASELINE_KEYS: tuple[str, ...] = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "USERPROFILE",
    "USERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "HOME",
    "LOGNAME",
    "SHELL",
    "TERM",
    "USER",
)

#: 子进程临时目录键：强制指向本次连接专属受管目录，不继承应用/用户临时目录。
_STDIO_TEMP_KEYS: tuple[str, ...] = ("TEMP", "TMP")
#: 受管专属临时目录名前缀（``mkdtemp`` 追加随机后缀，天然保证唯一、无竞争）。
_STDIO_TEMP_PREFIX = "astock-mcp-stdio-"


def _resolve_http_headers(headers: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """把 HTTP Header 的**安全引用**解析为实际值（``env:`` / ``keyring:``）。

    只接受引用形态（明文值在配置校验阶段已拒绝），故本函数是唯一把秘密读入内存的
    位置；解析失败只抛 ``ValueError``（消息仅含引用名，不含秘密，R9）。
    """

    resolved: dict[str, str] = {}
    for name, ref in headers:
        if ref.startswith("env:"):
            var_name = ref[len("env:") :]
            value = os.environ.get(var_name)
            if not value:
                raise ValueError(f"secure header reference not found: env:{var_name}")
        else:
            item = ref[len("keyring:") :]
            import utils.config_handler as cfg

            value = cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, item)
            if not value:
                raise ValueError(f"secure header reference not found: keyring:{item}")
        resolved[name] = value
    return resolved


def _build_stdio_env(
    configured: tuple[tuple[str, str], ...],
    *,
    temp_dir: Path,
) -> dict[str, str]:
    """显式白名单基线 + 配置项 + 强制受管 ``TEMP`` / ``TMP``。

    基线只取 :data:`_OS_BASELINE_KEYS` 中实际存在的键（不含应用秘密副本）；
    ``TEMP`` / ``TMP`` 最后写入，配置项不得把子进程临时目录指回应用目录。
    """
    env = {key: os.environ[key] for key in _OS_BASELINE_KEYS if key in os.environ}
    env.update(configured)
    env.update({key: str(temp_dir) for key in _STDIO_TEMP_KEYS})
    return env


class _ManagedStdioEnvironment:
    """stdio 子进程环境：显式基线 + 本次连接专属 ``TEMP`` / ``TMP`` 受管目录。

    目录由 ``mkdtemp`` 创建（owner 为当前进程用户，POSIX 权限收紧到 0o700，
    Windows ACL 由用户配置目录继承），每次连接一个、天然并发安全；连接失败与
    正常关闭都调用 :meth:`cleanup`，可重复调用且不抛异常。
    """

    def __init__(self, env: dict[str, str], temp_dir: Path) -> None:
        self._env = env
        self._temp_dir = temp_dir

    @property
    def env(self) -> dict[str, str]:
        """交给 SDK 的完整子进程环境（键集合完全由本模块决定）。"""
        return dict(self._env)

    @property
    def temp_dir(self) -> Path:
        """本次连接的受管临时目录。"""
        return self._temp_dir

    @classmethod
    def create(cls, configured: tuple[tuple[str, str], ...]) -> _ManagedStdioEnvironment:
        """创建专属临时目录并构造环境；目录创建失败直接抛出（不静默降级）。"""
        temp_dir = Path(tempfile.mkdtemp(prefix=_STDIO_TEMP_PREFIX))
        with suppress(OSError):
            os.chmod(temp_dir, 0o700)
        return cls(_build_stdio_env(configured, temp_dir=temp_dir), temp_dir)

    def cleanup(self) -> None:
        """删除专属目录（幂等；已删除/不可删均不抛异常）。"""
        shutil.rmtree(self._temp_dir, ignore_errors=True)


#: stderr 诊断尾部保留字节上限（内存缓冲与公开属性共用同一上限）。
_STDERR_TAIL_BYTES = 4096
#: 单行保留字节上限；超长行截断并丢弃该行剩余部分。
_STDERR_LINE_BYTES = 2048
#: 关闭时等待 drain 线程收到 EOF 的上限。
_STDERR_DRAIN_JOIN_TIMEOUT_S = 2.0
#: 超长行截断标记。
_STDERR_TRUNCATED_MARKER = "…[truncated]"
#: 协议错误留存条数上限（超出丢弃最旧；条目留存前先脱敏）。
_MAX_PROTOCOL_ERRORS = 32


@dataclass
class MCPSessionDiagnostics:
    """四诊断通道（固定形状；不可观测的通道保持 ``None`` 而非伪造值）。"""

    stderr_tail: str = ""
    last_error: str | None = None
    protocol_errors: list[str] = field(default_factory=list)
    exit_status: int | None = None

    def set_exit_status(self, code: int | None) -> None:
        """写入实际观测到的子进程退出码；未观测到（``None``）保持「未知」，不伪造 0。"""
        if code is not None:
            self.exit_status = code

    def record(self, error: str) -> None:
        """记录一次协议错误：留存前脱敏，条数超上限时丢弃最旧。"""
        sanitized = DataSanitizer.sanitize_error(error)
        self.last_error = sanitized
        self.protocol_errors.append(sanitized)
        overflow = len(self.protocol_errors) - _MAX_PROTOCOL_ERRORS
        if overflow > 0:
            del self.protocol_errors[:overflow]


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
        """关闭写端等 EOF、冻结尾部；可重复调用，不抛异常。

        子进程仍持有写端时排空线程会阻塞在读取上，join 超时后**不在调用线程**关闭
        读端：``BufferedReader`` 的 close 会等待并发 read 持有的内部锁，从而把调用方
        （事件循环）无限期挂住。此时读端由排空线程在读到 EOF 后自行关闭，尾部已冻结。
        """
        with suppress(OSError, ValueError):
            self._writer.close()
        if self._thread.is_alive():
            self._thread.join(timeout=_STDERR_DRAIN_JOIN_TIMEOUT_S)
        with self._lock:
            self._closing = True
        if not self._thread.is_alive():
            # 排空线程已退出（读到 EOF），无并发读取，关闭读端不会阻塞。
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
        """后台排空：逐行脱敏入有界缓冲；退出时由本线程关闭读端（避免跨线程 close 阻塞）。"""
        try:
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
        finally:
            with suppress(OSError, ValueError):
                self._reader.close()

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


def _discard_stdio_env(stdio_env: _ManagedStdioEnvironment | None) -> None:
    """连接受阻/失败时删除受管临时目录，不留残留。"""
    if stdio_env is not None:
        stdio_env.cleanup()


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


class _BudgetedInboundStream(httpx2.AsyncByteStream):
    """SDK 解析前的入站字节核验（大帧 / 深 JSON 的真实插入点）。

    包装底层响应字节流：每块在交回 SDK 之前先计入接收字节、再以增量扫描器判定
    结构深度；任一超限即抛出 ``BUDGET_BLOCKED``，**该块不会交到 SDK**，故完整消息
    对象从未被构造。取消（``CancelledError``）原样传播，不吞。
    """

    def __init__(self, inner: httpx2.AsyncByteStream, *, guard: MCPBudgetGuard) -> None:
        self._inner = inner
        self._guard = guard
        self._scanner = JsonDepthScanner()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._guard.receive_bytes(len(chunk))
            self._guard.structure_depth(self._scanner.feed(chunk))
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _GuardedHTTPTransport(httpx2.AsyncBaseTransport):
    """每次 HTTP 发送前的目标核验（``AsyncClient.send`` 的真实传输插入点）。

    包装底层传输：与冻结目标不一致（scheme/host/port/path）、或带 ``Last-Event-ID``
    的恢复 GET，在委托底层传输前抛 ``POLICY_DENIED``，**不产生任何字节出站**；
    重定向因此不会被静默跟随（307/308、跨源、新路径的后续请求同样被拒）。
    入站方向同样在此插入：注入 ``inbound`` 时，响应字节先经
    :class:`_BudgetedInboundStream` 核验，再交给 SDK 解析。
    """

    def __init__(
        self,
        *,
        approved: _ApprovedHTTPTarget,
        diagnostics: MCPSessionDiagnostics,
        identity: str,
        inner: httpx2.AsyncBaseTransport | None = None,
        inbound: MCPBudgetGuard | None = None,
    ) -> None:
        self._approved = approved
        self._diagnostics = diagnostics
        self._identity = identity
        self._inner = inner if inner is not None else httpx2.AsyncHTTPTransport(trust_env=False)
        self._inbound = inbound

    @property
    def inner(self) -> httpx2.AsyncBaseTransport:
        """底层传输（测试断言「零发送」用）。"""
        return self._inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        reason = self._refusal(request)
        if reason is not None:
            self._diagnostics.record(reason)
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, f"mcp http send refused: {reason}", self._identity)
        response = await self._inner.handle_async_request(request)
        if self._inbound is None:
            return response
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if encoding not in ("", "identity"):
            # 压缩后的字节无法前置判定帧长/结构深度：拒绝而不是按「预算有值」放行。
            await response.aclose()
            self._diagnostics.record("inbound_content_encoding_unverifiable")
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED,
                "mcp inbound limits cannot be verified on a compressed response",
                self._identity,
            )
        stream = response.stream
        if not isinstance(stream, httpx2.AsyncByteStream):
            # 非异步字节流（如未接入的 AttachedStream）无法在此层前置核验：拒绝而非放行。
            await response.aclose()
            self._diagnostics.record("inbound_stream_unverifiable")
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED,
                "mcp inbound limits cannot be verified on a non-async response stream",
                self._identity,
            )
        response.stream = _BudgetedInboundStream(stream, guard=self._inbound)
        return response

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
    inbound: MCPBudgetGuard | None = None,
) -> Callable[..., httpx2.AsyncClient]:
    """构造注入 SDK 的 ``httpx_client_factory``：目标核验 + 入站预算 + 禁代理环境。

    SDK（fastmcp/mcp）把 ``follow_redirects=True`` 传给工厂；此处显式忽略，恒为 False，
    并以 ``max_redirects=0`` 让 SDK 自身的「同源重定向跟随」也一步都不发。
    ``Accept-Encoding: identity`` 强制成立：压缩响应无法在解析前判定帧长与结构深度，
    由 :class:`_GuardedHTTPTransport` 显式拒绝（不静默降级为「未限流」）。
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
            headers={**(headers or {}), "Accept-Encoding": "identity"},
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
                inbound=inbound,
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
        # 两处拒绝都发生在交给传输层之前：以 MCPCallNotDispatched 明确「效果确定未发生」（I15）。
        if not self._alive():
            raise MCPCallNotDispatched(
                AgentErrorCode.CONFIG_UNAVAILABLE, "mcp session is lost; refusing to send", self._identity
            )
        deadline = self._deadline_factory(deadline_s)
        if deadline.expired():
            raise MCPCallNotDispatched(
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
        stdio_env: _ManagedStdioEnvironment | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._guard = guard
        self._gate = gate
        self._diagnostics = diagnostics
        self._stderr_drain = stderr_drain
        self._on_close = on_close
        self._stdio_env = stdio_env
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
            # 先计入条目数，再以增量编码在剩余额度内产出 schema：超限即中止，
            # 超限 schema 不会被完整物化（账本不记未通过计量的字节）。
            name_cost = len(tool.name.encode("utf-8"))
            self._guard.catalog_item()
            schema_room = self._guard.remaining("schema_bytes")
            catalog_room = self._guard.remaining("catalog_bytes") - name_cost
            if catalog_room < 0:
                self._guard.exceed("catalog_bytes")
            schema_json, schema_size = encode_json_text_capped(tool.input_schema, limit=min(schema_room, catalog_room))
            if schema_json is None:
                self._guard.exceed("catalog_bytes" if catalog_room < schema_room else "schema_bytes")
            self._guard.catalog_bytes(name_cost + schema_size)
            self._guard.schema_bytes(schema_size)
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
        # 结果对象已由 SDK 解析；此处只做增量计量：超限即在片段边界中止，
        # 不为比较而物化整条结果的规范 JSON 文本或 UTF-8 副本（I56）。
        decoded_size = measure_json_bytes(payload, limit=self._guard.remaining("decoded_bytes"), default=str)
        if decoded_size is None:
            self._guard.exceed("decoded_bytes")
        self._guard.decoded_bytes(decoded_size)
        return payload

    async def aclose(self) -> None:
        if self._closed:
            return
        try:
            await self._client.__aexit__(None, None, None)
        except BaseException:
            # 退出未确认：只收尾「关闭尝试」（拒绝新发送/冻结诊断/删临时目录），不标记已关闭，
            # 句柄保留以便重试真实退出；不得把未知退出伪装成已退出（I17/I53）。
            self._finish_close_attempt()
            raise
        # 只有实际退出成功才标记已关闭：失败/取消后的再次 aclose 仍会真正重试退出（I17）。
        self._closed = True
        self._finish_close_attempt()

    def _finish_close_attempt(self) -> None:
        """关闭尝试的幂等收尾：拒绝新发送、冻结并清空 stderr 尾部、删除专属临时目录。"""

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
        if self._stdio_env is not None:
            # 退出尝试后即删专属临时目录（幂等）：失败重试也不留残留。
            self._stdio_env.cleanup()


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
        """建立真实连接（仅显式启连/测试调用）；失败映射为 ``CONFIG_UNAVAILABLE``。

        启连点**复用** ``validate_mcp_server_config``：配置可能由存储加载后被篡改，
        未通过即零启连（不建临时目录、不建线程、不解析秘密、不创建子进程/网络连接）。
        """

        label = config.identity.stable_key()
        try:
            validate_mcp_server_config(config)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"invalid mcp config: {exc.code}", label) from exc
        # 诊断先于 client 构造：HTTP 目标核验在传输层记录，须与连接期共用同一实例。
        diagnostics = MCPSessionDiagnostics()
        is_stdio = config.transport.kind is MCPTransportKind.STDIO
        http_headers: dict[str, str] | None = None
        if not is_stdio and config.transport.headers:
            try:
                http_headers = await asyncio.to_thread(_resolve_http_headers, config.transport.headers)
            except ValueError as exc:
                raise AgentServiceError(
                    AgentErrorCode.CONFIG_UNAVAILABLE, "mcp secure header reference unavailable", label
                ) from exc
        drain = _SanitizedStderrDrain() if is_stdio else None
        stdio_env: _ManagedStdioEnvironment | None = None
        guard = MCPBudgetGuard(config.budget, identity=label)
        try:
            if is_stdio:
                stdio_env = _ManagedStdioEnvironment.create(config.transport.env)
            transport = self._transport(
                config,
                drain,
                stdio_env=stdio_env,
                diagnostics=diagnostics,
                label=label,
                headers=http_headers,
                inbound=guard,
            )
        except Exception as exc:
            _discard_drain(drain)
            _discard_stdio_env(stdio_env)
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                f"mcp transport setup failed: {type(exc).__name__}",
                label,
            ) from exc
        except BaseException:
            _discard_drain(drain)
            _discard_stdio_env(stdio_env)
            raise
        client: Client[Any] = Client(
            transport,
            timeout=config.tool_deadline_s,
            init_timeout=config.connect_deadline_s,
        )
        try:
            await client.__aenter__()
        except Exception as exc:
            _discard_drain(drain)
            _discard_stdio_env(stdio_env)
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                f"mcp connection failed: {type(exc).__name__}",
                label,
            ) from exc
        except BaseException:
            # 取消/键盘中断等不转译，但必须收尾排空器（不吞 CancelledError）。
            _discard_drain(drain)
            _discard_stdio_env(stdio_env)
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
            guard=guard,
            gate=MCPSendGate(
                alive=_alive,
                deadline_factory=self._deadline_factory,
                identity=label,
            ),
            diagnostics=diagnostics,
            stderr_drain=drain,
            on_close=_mark_closed,
            stdio_env=stdio_env,
        )

    # -- 内部 ---------------------------------------------------------------

    def _transport(
        self,
        config: MCPServerConfig,
        drain: _SanitizedStderrDrain | None,
        *,
        stdio_env: _ManagedStdioEnvironment | None = None,
        diagnostics: MCPSessionDiagnostics,
        label: str,
        headers: dict[str, str] | None = None,
        inbound: MCPBudgetGuard | None = None,
    ) -> Any:
        transport = config.transport
        if transport.kind is MCPTransportKind.STDIO:
            command = transport.command
            assert command is not None
            assert drain is not None
            assert stdio_env is not None
            assert inbound is not None
            return BudgetedStdioTransport(
                command,
                list(transport.args),
                guard=inbound,
                env=stdio_env.env,
                cwd=transport.cwd,
                errlog=cast("TextIO", drain),
                on_exit_status=diagnostics.set_exit_status,
                on_limit_exceeded=lambda family: diagnostics.record(f"inbound_limit:{family}"),
            )
        assert transport.url is not None
        factory = _guarded_http_client(_ApprovedHTTPTarget.from_url(transport.url), diagnostics, label, inbound=inbound)
        if transport.kind is MCPTransportKind.STREAMABLE_HTTP:
            # 单事件上限 = 已冻结的接收预算：单个 SSE 事件不得大于整条连接的接收上限，
            # 由 SDK 在解析前按事件拒绝（与 httpx 层的前置计入互为冗余）。
            return StreamableHttpTransport(
                transport.url,
                headers=headers,
                httpx_client_factory=factory,
                max_sse_event_size=config.budget.receive_bytes,
            )
        return SSETransport(transport.url, headers=headers, httpx_client_factory=factory)
