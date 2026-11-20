"""MCP 生命周期全链（管理器 + 门面 + 工具工厂）：保存 → 测试 → 启连 → 零工具回退。

本文件用**受控传输替身**验证装配语义（准入、授权冻结、目录漂移、断开后零工具），
不重复验证真实 FastMCP 传输——后者已由 ``test_agent_mcp_fastmcp_transport.py`` 覆盖。
注意：当前准入规则（I03）下 stdio 因缺少解析前插入点不可启连、remote 实现指纹本机
不可核定，故「真实服务的共享连接」仍受 E-02 门禁约束，本文件不宣称已打通。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service.mcp import (
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
)
from services.agent_service.mcp_config_store import AppStateMCPConfigStore
from services.agent_service.mcp_grant import AppStateMCPGrantStore
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle
from services.agent_service.tool_runtime import ToolRunScope, build_tool_runtime_factory

pytestmark = [pytest.mark.integration, pytest.mark.no_db, pytest.mark.asyncio]

_IDENTITY = MCPServiceIdentity(service_id="svc-flow", config_version=1)
_FINGERPRINT = sha256_hex(b"flow-impl")
_SCHEMA = '{"type":"object","properties":{"query":{"type":"string"}},"additionalProperties":false}'


def _config() -> MCPServerConfig:
    return MCPServerConfig(
        identity=_IDENTITY,
        display_name="流程服务",
        transport=MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.test/tools",
            http_risk_approved=True,
        ),
        budget=MCPBudgetProfile(
            receive_bytes=1024,
            decoded_bytes=2048,
            structure_depth=32,
            catalog_items=10,
            catalog_bytes=1024,
            schema_bytes=2048,
            single_result_bytes=1024,
            retention_bytes=2048,
        ),
        audit=MCPReadOnlyAudit(
            service=_IDENTITY,
            tool_scope=("search",),
            implementation_fingerprint=_FINGERPRINT,
            evidence_source="integration",
            reviewed_at="2026-11-20T00:00:00.000000Z",
            invalidation_conditions=("executable",),
        ),
    )


class _Session:
    def __init__(self) -> None:
        self.closed = False
        self.list_calls = 0

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        self.list_calls += 1
        return (MCPToolDescriptor("search", _SCHEMA),)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        return {"content": []}

    async def aclose(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.session = _Session()
        self.opened = 0

    async def open(self, config: MCPServerConfig) -> _Session:
        self.opened += 1
        return self.session


class _Probe:
    async def probe(self, config: MCPServerConfig) -> str | None:
        return _FINGERPRINT


class _Usage:
    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ()


class _Dao:
    async def save_tool_decision(self, request: Any) -> Any:
        raise AssertionError("not used")

    async def save_tool_result(self, request: Any) -> None:
        raise AssertionError("not used")

    async def read_operation_grant_scope(self, *, operation_id: str) -> Any:
        return None

    async def claim_tool_dispatch(self, request: Any) -> Any:
        raise AssertionError("not used")


class _Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def read(self, key: str) -> str | None:
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        self.values[key] = value

    async def scan(self, prefix: str) -> dict[str, str]:
        return {key: value for key, value in self.values.items() if key.startswith(prefix)}


def _build() -> tuple[MCPServiceLifecycle, _Factory]:
    memory = _Memory()
    factory = _Factory()
    manager = SharedMCPConnectionManager(
        store=AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan),
        factory=factory,
        probe=_Probe(),
        usage=_Usage(),
    )
    lifecycle = MCPServiceLifecycle(
        manager=manager,
        configs=manager_store(memory),
        grants=AppStateMCPGrantStore(read=memory.read, write=memory.write),
    )
    return lifecycle, factory


def manager_store(memory: _Memory) -> AppStateMCPConfigStore:
    return AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan)


class TestLifecycleFlow:
    async def test_save_test_connect_factory_disconnect(self) -> None:
        lifecycle, factory = _build()
        scope = ToolRunScope(
            user_scope="user-1",
            session_id="session-1",
            execution_id="execution-1",
            round_id="round-1",
            node_id="n1",
        )

        await lifecycle.save_config(_config())
        assert [item.identity for item in await lifecycle.list_services()] == [_IDENTITY]

        receipt = await lifecycle.connection_test(_IDENTITY)
        assert receipt.closed is True
        assert receipt.research_calls == 0
        assert receipt.tool_names == ("search",)
        assert lifecycle.manager.is_connected(_IDENTITY) is False

        tool_factory = build_tool_runtime_factory(dao=_Dao(), lifecycle=lifecycle)
        assert tool_factory(scope) is None

        connected = await lifecycle.connect(_IDENTITY, approved_tools=["search"])
        assert connected.tools == ("search",)
        assert lifecycle.manager.is_connected(_IDENTITY) is True
        runtime = tool_factory(scope)
        assert runtime is not None
        assert [tool.name for tool in runtime.tools] == ["search"]

        await lifecycle.disconnect(_IDENTITY)
        assert lifecycle.manager.is_connected(_IDENTITY) is False
        assert tool_factory(scope) is None

    async def test_disconnect_blocked_by_frozen_users_keeps_connection(self) -> None:
        memory = _Memory()
        factory = _Factory()

        class _Frozen(_Usage):
            users: tuple[str, ...] = ()

            async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
                return self.users

        usage = _Frozen()
        manager = SharedMCPConnectionManager(
            store=manager_store(memory),
            factory=factory,
            probe=_Probe(),
            usage=usage,
        )
        lifecycle = MCPServiceLifecycle(
            manager=manager,
            configs=manager_store(memory),
            grants=AppStateMCPGrantStore(read=memory.read, write=memory.write),
        )
        await lifecycle.save_config(_config())
        await lifecycle.connect(_IDENTITY, approved_tools=["search"])
        # 启连后出现排队/暂停研究：断开前必须核持久化使用者并拒绝。
        usage.users = ("execution-paused",)

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.disconnect(_IDENTITY)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert lifecycle.manager.is_connected(_IDENTITY) is True
