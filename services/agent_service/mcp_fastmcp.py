"""真实 FastMCP 传输适配（E-02-5 needs-spike 实证）。

插入点与隔离边界
----------------
- :class:`FastMCPClientFactory` 实现 ``MCPClientSessionPort`` / ``MCPClientFactoryPort``
  （:mod:`services.agent_service.mcp`），由共享连接管理器在**用户显式启连/测试**
  时调用；保存配置与普通读取不触发任何进程/网络。
- stdio：``StdioTransport(绝对 command, args, 最小环境, cwd, log_file=<临时文件>)``，
  不经 shell；``log_file`` 把子进程 stderr 重定向到文件描述符（FD 级），避免
  管道不读导致阻塞。
- streamable_http / sse：``StreamableHttpTransport`` / ``SSETransport``，只用已冻结
  的 URL；SSE 仍须显式核准（由 ``validate_transport_config`` 保证）。

逐 send 门
----------
:class:`MCPSendGate` 在**每次**出站 SDK 请求（``list_tools`` / ``call_tool``）前
检查会话是否仍存活与单一 deadline 是否已过；断线后拒绝发送，不自动重连。

四诊断通道
----------
:class:`MCPSessionDiagnostics` 固定四路：``stderr_tail``（FD 级尾部）、
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
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport

from core.agent_types import AgentErrorCode, AgentServiceError, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPToolDescriptor
from services.agent_service.mcp_budget import MCPBudgetGuard, MCPDeadline

#: 子进程最小 OS 基线（Windows 缺少这些值会导致解释器无法启动）；不含应用环境副本。
_OS_BASELINE_KEYS: tuple[str, ...] = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "TEMP", "TMP")

#: stderr 尾部保留字节（只读尾部，不把整份日志读入内存）。
_STDERR_TAIL_BYTES = 4096


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


class MCPSendGate:
    """每次出站请求前的门：会话存活 + 单一 deadline 未过；记录出站次数。"""

    def __init__(
        self,
        *,
        alive: Callable[[], bool],
        deadline: MCPDeadline,
        identity: str,
    ) -> None:
        self._alive = alive
        self._deadline = deadline
        self._identity = identity
        self._sends = 0

    @property
    def sends(self) -> int:
        return self._sends

    def before_send(self, operation: str) -> None:
        self._sends += 1
        if not self._alive():
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE, "mcp session is lost; refusing to send", self._identity
            )
        if self._deadline.expired():
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED, f"mcp deadline exceeded before send: {operation}", self._identity
            )


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
        stderr_path: Path | None,
        on_close: Callable[[], None],
    ) -> None:
        self._client = client
        self._config = config
        self._guard = guard
        self._gate = gate
        self._diagnostics = diagnostics
        self._stderr_path = stderr_path
        self._on_close = on_close
        self._closed = False

    @property
    def diagnostics(self) -> MCPSessionDiagnostics:
        return self._diagnostics

    @property
    def gate(self) -> MCPSendGate:
        return self._gate

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        self._gate.before_send("list_tools")
        try:
            async with asyncio.timeout(deadline_s):
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
        self._gate.before_send("call_tool")
        try:
            async with asyncio.timeout(deadline_s):
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
        self._closed = True
        try:
            await self._client.__aexit__(None, None, None)
        finally:
            self._on_close()
            self._diagnostics.stderr_tail = _read_tail(self._stderr_path)
            if self._stderr_path is not None:
                self._stderr_path.unlink(missing_ok=True)


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


def _read_tail(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - _STDERR_TAIL_BYTES))
        return handle.read().decode("utf-8", errors="replace")


class FastMCPClientFactory:
    """``MCPClientFactoryPort`` 真实实现（仅在显式启连/测试时被调用）。"""

    def __init__(
        self,
        *,
        stderr_dir: str | Path | None = None,
        deadline_factory: Callable[[int], MCPDeadline] | None = None,
    ) -> None:
        self._stderr_dir = Path(stderr_dir) if stderr_dir is not None else Path(tempfile.gettempdir())
        self._deadline_factory = deadline_factory or (lambda seconds: MCPDeadline(seconds=seconds))

    async def open(self, config: MCPServerConfig) -> FastMCPSession:
        """建立真实连接（仅显式启连/测试调用）；失败映射为 ``CONFIG_UNAVAILABLE``。"""
        label = config.identity.stable_key()
        stderr_path = self._new_stderr_path(label) if config.transport.kind is MCPTransportKind.STDIO else None
        transport = self._transport(config, stderr_path)
        client: Client[Any] = Client(
            transport,
            timeout=config.tool_deadline_s,
            init_timeout=config.connect_deadline_s,
        )
        try:
            await client.__aenter__()
        except Exception as exc:
            if stderr_path is not None:
                stderr_path.unlink(missing_ok=True)
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                f"mcp connection failed: {type(exc).__name__}",
                label,
            ) from exc
        closed = False

        def _mark_closed() -> None:
            nonlocal closed
            closed = True

        def _alive() -> bool:
            return not closed

        diagnostics = MCPSessionDiagnostics()
        return FastMCPSession(
            client,
            config=config,
            guard=MCPBudgetGuard(config.budget, identity=label),
            gate=MCPSendGate(
                alive=_alive,
                deadline=self._deadline_factory(config.tool_deadline_s),
                identity=label,
            ),
            diagnostics=diagnostics,
            stderr_path=stderr_path,
            on_close=_mark_closed,
        )

    # -- 内部 ---------------------------------------------------------------

    def _new_stderr_path(self, label: str) -> Path:
        safe = label.replace("/", "_").replace(":", "_").replace("\\", "_")[:80]
        with tempfile.NamedTemporaryFile(
            prefix=f"qtrading-mcp-{safe}-", suffix=".log", dir=str(self._stderr_dir), delete=False
        ) as handle:
            return Path(handle.name)

    def _transport(self, config: MCPServerConfig, stderr_path: Path | None) -> Any:
        transport = config.transport
        if transport.kind is MCPTransportKind.STDIO:
            command = transport.command
            assert command is not None
            return StdioTransport(
                command=command,
                args=list(transport.args),
                env=self._build_env(transport.env),
                cwd=transport.cwd,
                keep_alive=False,
                log_file=stderr_path,
            )
        assert transport.url is not None
        if transport.kind is MCPTransportKind.STREAMABLE_HTTP:
            return StreamableHttpTransport(transport.url)
        return SSETransport(transport.url)

    @staticmethod
    def _build_env(configured: tuple[tuple[str, str], ...]) -> dict[str, str]:
        """最小 OS 基线 + 显式配置项（保序）；不复制应用环境。"""

        env = {key: os.environ[key] for key in _OS_BASELINE_KEYS if key in os.environ}
        env.update(configured)
        return env
