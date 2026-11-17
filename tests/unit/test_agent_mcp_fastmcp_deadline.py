"""I12：MCP 逐 operation deadline 单元测试。

连接期不再固化单一 tool deadline：每次 ``list_tools`` / ``call_tool`` 新建独立
deadline，闲置超过默认 30 秒后首次合法调用不被旧 deadline 拒绝；连续两次合法
调用均成功；progress 不续期；超时仍按单 operation deadline 在发送前拒绝。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, MCPBudgetProfile, MCPServiceIdentity, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_budget import MCPBudgetGuard, MCPDeadline
from services.agent_service.mcp_fastmcp import FastMCPSession, MCPSendGate, MCPSessionDiagnostics


class _Result:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class _FakeCallClient:
    """只记录调用并立即返回的假客户端（不触达 SDK/网络）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[Any]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> _Result:
        self.calls.append((name, arguments))
        return _Result({"echo": arguments.get("text")})

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def _profile() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=10_000,
        decoded_bytes=10_000,
        structure_depth=8,
        catalog_items=8,
        catalog_bytes=10_000,
        schema_bytes=10_000,
        single_result_bytes=10_000,
        retention_bytes=10_000,
    )


def _config() -> MCPServerConfig:
    return MCPServerConfig(
        identity=MCPServiceIdentity(service_id="svc-test", config_version=1),
        display_name="svc-test",
        transport=MCPTransportConfig(kind=MCPTransportKind.STDIO, command="server.exe"),
        budget=_profile(),
    )


def _session(
    client: _FakeCallClient,
    deadline_factory: Callable[[int], MCPDeadline],
) -> FastMCPSession:
    return FastMCPSession(
        cast(Any, client),
        config=_config(),
        guard=MCPBudgetGuard(_profile(), identity="svc-test"),
        gate=MCPSendGate(alive=lambda: True, deadline_factory=deadline_factory, identity="svc-test"),
        diagnostics=MCPSessionDiagnostics(),
        stderr_drain=None,
        on_close=lambda: None,
    )


class TestPerOperationDeadline:
    async def test_idle_beyond_default_deadline_still_allows_next_call(self) -> None:
        now = [0.0]
        client = _FakeCallClient()
        session = _session(client, lambda seconds: MCPDeadline(seconds=seconds, monotonic=lambda: now[0]))

        assert await session.call_tool("echo", {"text": "first"}, deadline_s=30) == {"echo": "first"}
        # 闲置 300 秒：旧连接期 deadline 早已过时，新 operation 必须重新起算。
        now[0] = 300.0
        assert await session.call_tool("echo", {"text": "after-idle"}, deadline_s=30) == {"echo": "after-idle"}

        assert [name for name, _ in client.calls] == ["echo", "echo"]
        assert session.gate.sends == 2

    async def test_two_consecutive_legal_calls_succeed(self) -> None:
        now = [0.0]
        client = _FakeCallClient()
        session = _session(client, lambda seconds: MCPDeadline(seconds=seconds, monotonic=lambda: now[0]))

        now[0] = 5.0
        assert await session.call_tool("echo", {"text": "a"}, deadline_s=30) == {"echo": "a"}
        now[0] = 29.0
        assert await session.call_tool("echo", {"text": "b"}, deadline_s=30) == {"echo": "b"}

        assert session.gate.sends == 2
        assert [arguments["text"] for _, arguments in client.calls] == ["a", "b"]

    def test_progress_does_not_extend_operation_deadline(self) -> None:
        now = [0.0]
        gate = MCPSendGate(
            alive=lambda: True,
            deadline_factory=lambda seconds: MCPDeadline(seconds=seconds, monotonic=lambda: now[0]),
            identity="svc-test",
        )

        deadline = gate.before_send("call_tool", deadline_s=30)
        now[0] = 10.0
        deadline.observe_progress()
        assert deadline.remaining() == 20
        assert deadline.expired() is False

        now[0] = 30.0
        assert deadline.expired() is True

    async def test_expired_operation_deadline_blocks_before_send(self) -> None:
        ticks = iter([0.0, 100.0, 100.0])
        client = _FakeCallClient()
        session = _session(
            client,
            lambda seconds: MCPDeadline(seconds=seconds, monotonic=lambda: next(ticks, 100.0)),
        )

        with pytest.raises(AgentServiceError) as error:
            await session.call_tool("echo", {"text": "x"}, deadline_s=30)

        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert client.calls == []
        assert session.gate.sends == 1
