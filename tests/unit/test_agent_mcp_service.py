"""E-02 共享 MCP 连接管理单元测试（静态可验证部分，不接入真实 SDK）。

覆盖：
- transport 合同：stdio 绝对可执行文件/不经 shell、HTTP 必须有 URL、SSE 需显式兼容核准；
- 保存配置 0 连接、0 工具发现；
- 连接测试只初始化/发现：0 研究工具调用、不落权限、结束关闭；
- 研究启连前必须预算冻结 + 只读审核绑定实际实现；证据不足拒启；
- 无自动重连：断线后不自动重新 open；显式 disconnect 在有在途调用时被拒。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from core.agent_types import (
    MCP_DEFAULT_DEADLINE_S,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service.mcp import (
    MCPConnectionTestReceipt,
    MCPImplementationProbe,
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
    validate_transport_config,
)

_FINGERPRINT = sha256_hex(b"mcp-local-implementation")
_SCHEMA = '{"type":"object","properties":{}}'
# 平台中性绝对路径：夹具不得假设 Windows（Linux CI 上 "C:\\..." 非 os.path.isabs）
_ABS_EXE = os.path.join(str(Path(tempfile.gettempdir())), "mcp-server.exe")


def _identity(config_version: int = 1) -> MCPServiceIdentity:
    return MCPServiceIdentity(service_id="svc-local-fs", config_version=config_version)


def _transport(kind: MCPTransportKind = MCPTransportKind.STDIO, **overrides: object) -> MCPTransportConfig:
    values: dict[str, object] = {"kind": kind}
    if kind is MCPTransportKind.STDIO:
        values["command"] = _ABS_EXE
    else:
        values["url"] = "https://mcp.example.test/tools"
    values.update(overrides)
    return MCPTransportConfig(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=1024,
        decoded_bytes=2048,
        structure_depth=32,
        catalog_items=100,
        catalog_bytes=65536,
        schema_bytes=131072,
        single_result_bytes=65536,
        retention_bytes=131072,
    )


def _audit() -> MCPReadOnlyAudit:
    return MCPReadOnlyAudit(
        service=_identity(),
        tool_scope=("read_file",),
        implementation_fingerprint=_FINGERPRINT,
        evidence_source="local-executable-inspection",
        reviewed_at="2026-11-13T00:00:00.000000Z",
        invalidation_conditions=("executable", "entry_script"),
    )


def _config(**overrides: object) -> MCPServerConfig:
    values: dict[str, object] = {
        "identity": _identity(),
        "display_name": "本地文件服务",
        "transport": _transport(),
        "budget": _budget(),
        "audit": _audit(),
    }
    values.update(overrides)
    return MCPServerConfig(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


class _FakeStore:
    def __init__(self) -> None:
        self.saved: dict[str, MCPServerConfig] = {}

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None:
        return self.saved.get(identity.stable_key())

    async def save(self, config: MCPServerConfig) -> None:
        self.saved[config.identity.stable_key()] = config


class _FakeSession:
    def __init__(self, tools: tuple[MCPToolDescriptor, ...], *, fail_list: bool = False) -> None:
        self.tools = tools
        self.fail_list = fail_list
        self.list_calls = 0
        self.call_calls: list[str] = []
        self.closed = False

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        self.list_calls += 1
        if self.fail_list:
            raise RuntimeError("transport dropped")
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        self.call_calls.append(name)
        return {"content": []}

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class _FakeFactory:
    session: _FakeSession
    opened: int = 0
    opened_configs: list[MCPServerConfig] = field(default_factory=list)

    async def open(self, config: MCPServerConfig) -> _FakeSession:
        self.opened += 1
        self.opened_configs.append(config)
        return self.session


class _FakeProbe:
    def __init__(self, fingerprint: str | None) -> None:
        self.fingerprint = fingerprint
        self.calls = 0

    async def probe(self, config: MCPServerConfig) -> str | None:
        self.calls += 1
        return self.fingerprint


def _manager(
    *, session: _FakeSession | None = None, fingerprint: str | None = _FINGERPRINT
) -> tuple[SharedMCPConnectionManager, _FakeStore, _FakeFactory, _FakeProbe]:
    store = _FakeStore()
    factory = _FakeFactory(session or _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),)))
    probe = _FakeProbe(fingerprint)
    manager = SharedMCPConnectionManager(store=store, factory=factory, probe=probe)
    return manager, store, factory, probe


class TestTransportConfig:
    def test_stdio_requires_absolute_executable(self) -> None:
        validate_transport_config(_transport())
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_transport(command="relative/tool.exe"))
        assert exc.value.code == "invalid_transport"

    def test_stdio_rejects_http_url(self) -> None:
        config = MCPTransportConfig(kind=MCPTransportKind.STDIO, command=_ABS_EXE, url="https://x.test")
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(config)
        assert exc.value.code == "invalid_transport"

    def test_streamable_http_requires_url_and_no_userinfo(self) -> None:
        validate_transport_config(_transport(MCPTransportKind.STREAMABLE_HTTP))
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(MCPTransportConfig(kind=MCPTransportKind.STREAMABLE_HTTP))
        assert exc.value.code == "invalid_transport"
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_transport(MCPTransportKind.STREAMABLE_HTTP, url="https://user:pw@x.test"))
        assert exc.value.code == "invalid_transport"

    def test_sse_requires_explicit_compat_approval(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_transport(MCPTransportKind.SSE))
        assert exc.value.code == "sse_not_approved"
        validate_transport_config(_transport(MCPTransportKind.SSE, sse_compat_approved=True))


class TestSaveDoesNotConnect:
    @pytest.mark.asyncio
    async def test_save_opens_zero_connections_and_zero_discovery(self) -> None:
        manager, store, factory, _probe = _manager()
        receipt = await manager.save_config(_config())
        assert receipt.connections_opened == 0
        assert factory.opened == 0
        assert (await store.load(_identity())) is not None

    @pytest.mark.asyncio
    async def test_save_rejects_unfrozen_budget_profile(self) -> None:
        manager, store, _factory, _probe = _manager()
        with pytest.raises(AgentServiceError) as exc:
            await manager.save_config(_config(budget=MCPBudgetProfile()))
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert store.saved == {}

    @pytest.mark.asyncio
    async def test_save_rejects_audit_bound_to_other_service(self) -> None:
        manager, store, _factory, _probe = _manager()
        audit = MCPReadOnlyAudit(
            service=_identity(config_version=9),
            tool_scope=("read_file",),
            implementation_fingerprint=_FINGERPRINT,
            evidence_source="local-executable-inspection",
            reviewed_at="2026-11-13T00:00:00.000000Z",
            invalidation_conditions=("executable",),
        )
        with pytest.raises(AgentServiceError) as exc:
            await manager.save_config(_config(audit=audit))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert store.saved == {}


class TestConnectionTest:
    @pytest.mark.asyncio
    async def test_test_only_discovers_and_never_calls_research_tools(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())

        receipt = await manager.connection_test(_identity())

        assert isinstance(receipt, MCPConnectionTestReceipt)
        assert receipt.tool_names == ("read_file",)
        assert receipt.research_calls == 0
        assert session.list_calls == 1
        assert session.call_calls == []
        assert session.closed is True
        assert manager.is_connected(_identity()) is False
        # 测试不把发现的工具写进配置/许可。
        stored = await store.load(_identity())
        assert stored == _config()

    @pytest.mark.asyncio
    async def test_test_closes_session_even_when_discovery_fails(self) -> None:
        session = _FakeSession((), fail_list=True)
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())

        with pytest.raises(RuntimeError) as excinfo:
            await manager.connection_test(_identity())
        assert "transport dropped" in str(excinfo.value)
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_test_requires_saved_config(self) -> None:
        manager, _store, factory, _probe = _manager()
        with pytest.raises(AgentServiceError) as exc:
            await manager.connection_test(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert factory.opened == 0


class TestExplicitConnectAdmission:
    @pytest.mark.asyncio
    async def test_connect_requires_frozen_budget_and_matching_audit(self) -> None:
        manager, _store, _factory, _probe = _manager()
        await manager.save_config(_config())
        await manager.connect(_identity())
        assert manager.is_connected(_identity()) is True

    @pytest.mark.asyncio
    async def test_connect_refused_when_implementation_changed(self) -> None:
        manager, _store, factory, _probe = _manager(fingerprint=sha256_hex(b"replaced-payload"))
        await manager.save_config(_config())
        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "implementation_changed" in exc.value.sanitized_message
        assert factory.opened == 0

    @pytest.mark.asyncio
    async def test_connect_refused_when_implementation_unverifiable(self) -> None:
        manager, _store, factory, _probe = _manager(fingerprint=None)
        await manager.save_config(_config())
        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "implementation_unverified" in exc.value.sanitized_message
        assert factory.opened == 0


class TestNoAutoReconnectAndDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_is_explicit_and_closes_session(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, _store, factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())

        await manager.disconnect(_identity())

        assert session.closed is True
        assert manager.is_connected(_identity()) is False
        assert factory.opened == 1  # 断开不自动重连

    @pytest.mark.asyncio
    async def test_disconnect_refused_while_in_flight(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())
        manager.register_in_flight(_identity(), "op-1")

        with pytest.raises(AgentServiceError) as exc:
            await manager.disconnect(_identity())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert session.closed is False

        manager.release_in_flight(_identity(), "op-1")
        await manager.disconnect(_identity())
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_session_failure_marks_disconnected_without_reopen(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, _store, factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())

        manager.mark_session_lost(_identity())

        assert manager.is_connected(_identity()) is False
        assert factory.opened == 1

    @pytest.mark.asyncio
    async def test_probe_port_is_required_contract(self) -> None:
        # 探针端口仅声明形状：未实现真实本机载荷核定，禁止声称已验证。
        assert hasattr(MCPImplementationProbe, "probe")
        assert MCP_DEFAULT_DEADLINE_S == 30
