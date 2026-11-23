"""E-02 共享 MCP 连接管理单元测试（静态可验证部分，不接入真实 SDK）。

覆盖：
- transport 合同：stdio 绝对可执行文件/不经 shell、HTTP 必须有 URL、SSE 需显式兼容核准；
- 保存配置 0 连接、0 工具发现；
- 连接测试只初始化/发现：0 研究工具调用、不落权限、结束关闭；
- 研究启连前必须预算冻结 + 只读审核绑定实际实现；证据不足拒启；
- 无自动重连：断线后不自动重新 open；显式 disconnect 在有在途调用时被拒；
- 启连总期限：open（启动/初始化/鉴权）与发现共用同一预算，发现只拿剩余额度。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
    MCPClientFactoryPort,
    MCPConnectionTestReceipt,
    MCPImplementationProbe,
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
    config_snapshot_digest,
    validate_transport_config,
)

_FINGERPRINT = sha256_hex(b"mcp-local-implementation")
_SCHEMA = '{"type":"object","properties":{}}'
# 平台中性的真实可执行文件与绝对 cwd：校验要求 command 存在且可执行、cwd 必填。
_ABS_EXE = sys.executable
_ABS_CWD = os.path.dirname(_ABS_EXE)


def _identity(config_version: int = 1) -> MCPServiceIdentity:
    return MCPServiceIdentity(service_id="svc-local-fs", config_version=config_version)


def _transport(kind: MCPTransportKind = MCPTransportKind.STDIO, **overrides: object) -> MCPTransportConfig:
    values: dict[str, object] = {"kind": kind}
    if kind is MCPTransportKind.STDIO:
        values["command"] = _ABS_EXE
        values["cwd"] = _ABS_CWD
    else:
        values["url"] = "https://mcp.example.test/tools"
        values["http_risk_approved"] = True
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


def _audit(identity: MCPServiceIdentity | None = None) -> MCPReadOnlyAudit:
    return MCPReadOnlyAudit(
        service=identity or _identity(),
        tool_scope=("read_file",),
        implementation_fingerprint=_FINGERPRINT,
        evidence_source="local-executable-inspection",
        reviewed_at="2026-11-13T00:00:00.000000Z",
        invalidation_conditions=("executable", "entry_script"),
    )


def _config(**overrides: object) -> MCPServerConfig:
    # 准入要求入站限额有真实插入点（I03）：HTTP 传输具备（字节层 + 单事件上限），
    # stdio 无前置插入点会被准入拒绝；本文件验证管理器行为，故用 HTTP 传输。
    values: dict[str, object] = {
        "identity": _identity(),
        "display_name": "本地文件服务",
        "transport": _transport(MCPTransportKind.STREAMABLE_HTTP),
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
    def __init__(
        self,
        tools: tuple[MCPToolDescriptor, ...],
        *,
        fail_list: bool = False,
        close_failures: int = 0,
        close_gate: asyncio.Event | None = None,
    ) -> None:
        self.tools = tools
        self.fail_list = fail_list
        self.list_calls = 0
        self.call_calls: list[str] = []
        self.closed = False
        self.close_calls = 0
        self.close_failures = close_failures
        self.close_gate = close_gate

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        self.list_calls += 1
        if self.fail_list:
            raise RuntimeError("transport dropped")
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        self.call_calls.append(name)
        return {"content": []}

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_gate is not None:
            await self.close_gate.wait()
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("client close failed")
        self.closed = True


@dataclass
class _FakeFactory:
    session: _FakeSession
    opened: int = 0
    opened_configs: list[MCPServerConfig] = field(default_factory=list)

    async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _FakeSession:
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


class _FrozenUsers:
    """持久化使用者查询替身：按身份（服务+配置版）注入排队/暂停研究标识或查询故障。"""

    def __init__(self, users: dict[str, tuple[str, ...]] | None = None) -> None:
        self.by_key: dict[str, tuple[str, ...]] = dict(users or {})
        self.fail: Exception | None = None
        self.calls: list[str] = []

    def freeze(self, identity: MCPServiceIdentity, *users: str) -> None:
        self.by_key[identity.stable_key()] = users

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        key = identity.stable_key()
        self.calls.append(key)
        if self.fail is not None:
            raise self.fail
        return self.by_key.get(key, ())


def _manager(
    *,
    session: _FakeSession | None = None,
    fingerprint: str | None = _FINGERPRINT,
    usage: _FrozenUsers | None = None,
) -> tuple[SharedMCPConnectionManager, _FakeStore, _FakeFactory, _FakeProbe]:
    store = _FakeStore()
    factory = _FakeFactory(session or _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),)))
    probe = _FakeProbe(fingerprint)
    manager = SharedMCPConnectionManager(store=store, factory=factory, probe=probe, usage=usage or _FrozenUsers())
    return manager, store, factory, probe


class TestTransportConfig:
    def test_stdio_requires_absolute_executable(self) -> None:
        validate_transport_config(_transport())
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_transport(command="relative/tool.exe"))
        assert exc.value.code == "invalid_transport"

    def test_stdio_rejects_http_url(self) -> None:
        config = MCPTransportConfig(kind=MCPTransportKind.STDIO, command=_ABS_EXE, cwd=_ABS_CWD, url="https://x.test")
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

    @pytest.mark.asyncio
    async def test_test_close_failure_keeps_locatable_residue(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),), close_failures=1)
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())

        with pytest.raises(AgentServiceError) as exc:
            await manager.connection_test(_identity())

        # 不回执 closed=True，也不谎称已关闭：错误类型化，残留含身份+错误类型+句柄。
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert "close is unconfirmed" in exc.value.sanitized_message
        residue = manager.connection_test_residue(_identity())
        assert len(residue) == 1
        assert residue[0].identity == _identity()
        assert residue[0].error == "RuntimeError"
        assert residue[0].session is session
        assert session.closed is False
        assert manager.is_connected(_identity()) is False  # 测试不建立共享连接

    @pytest.mark.asyncio
    async def test_test_discovery_failure_is_not_masked_by_close_failure(self) -> None:
        session = _FakeSession((), fail_list=True, close_failures=1)
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())

        with pytest.raises(RuntimeError) as excinfo:
            await manager.connection_test(_identity())

        assert "transport dropped" in str(excinfo.value)  # 主失败原因优先
        assert [item.error for item in manager.connection_test_residue(_identity())] == ["RuntimeError"]
        assert session.closed is False

    @pytest.mark.asyncio
    async def test_test_manages_only_its_own_session(self) -> None:
        shared = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        scratch = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))

        class _TwoSessionFactory:
            def __init__(self) -> None:
                self.calls = 0

            async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _FakeSession:
                self.calls += 1
                return shared if self.calls == 1 else scratch

        manager = SharedMCPConnectionManager(
            store=_FakeStore(), factory=_TwoSessionFactory(), probe=_FakeProbe(_FINGERPRINT), usage=_FrozenUsers()
        )
        await manager.save_config(_config())
        await manager.connect(_identity())

        receipt = await manager.connection_test(_identity())

        assert receipt.closed is True
        assert scratch.closed is True
        assert shared.closed is False  # 测试不关闭共享连接
        assert manager.session(_identity()) is shared


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

    @pytest.mark.asyncio
    async def test_connect_freezes_discovered_catalog_with_generation(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())

        await manager.connect(_identity())

        assert session.list_calls == 1
        assert manager.frozen_catalog(_identity()) == (MCPToolDescriptor("read_file", _SCHEMA),)
        assert manager.audit_tool_scope(_identity()) == ("read_file",)
        first = manager.connection_generation(_identity())
        assert first >= 1
        await manager.disconnect(_identity())
        await manager.connect(_identity())
        assert manager.connection_generation(_identity()) > first

    @pytest.mark.asyncio
    async def test_connect_closes_session_and_stays_down_when_discovery_fails(self) -> None:
        session = _FakeSession((), fail_list=True)
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())

        with pytest.raises(RuntimeError) as excinfo:
            await manager.connect(_identity())
        assert "transport dropped" in str(excinfo.value)

        assert session.closed is True
        assert manager.is_connected(_identity()) is False

    def test_frozen_catalog_state_requires_established_connection(self) -> None:
        manager, _store, _factory, _probe = _manager()
        with pytest.raises(AgentServiceError) as exc:
            manager.frozen_catalog(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        with pytest.raises(AgentServiceError) as exc:
            manager.connection_generation(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        with pytest.raises(AgentServiceError) as exc:
            manager.audit_tool_scope(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE


class TestExistingSessionRechecksAudit:
    """I06：已有 session 直返路径也必须重核审核证据，不得按旧证据继续服务。"""

    @pytest.mark.asyncio
    async def test_connect_on_existing_session_refuses_after_fingerprint_change(self) -> None:
        manager, store, factory, probe = _manager()
        await store.save(_config())
        await manager.connect(_identity())
        assert probe.calls == 1

        probe.fingerprint = sha256_hex(b"replaced-on-disk-payload")

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "implementation_changed" in exc.value.sanitized_message
        assert probe.calls == 2
        assert factory.opened == 1  # 重核失败不新开连接

    @pytest.mark.asyncio
    async def test_connect_on_existing_session_refuses_when_config_is_gone(self) -> None:
        manager, store, factory, _probe = _manager()
        await store.save(_config())
        await manager.connect(_identity())
        store.saved.clear()

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert factory.opened == 1

    @pytest.mark.asyncio
    async def test_connect_on_existing_session_refuses_after_audit_withdrawal(self) -> None:
        manager, store, factory, _probe = _manager()
        await store.save(_config())
        await manager.connect(_identity())
        await store.save(_config(audit=None))

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "audit_missing" in exc.value.sanitized_message
        assert factory.opened == 1

    @pytest.mark.asyncio
    async def test_recheck_refuses_tool_scope_changed_after_connect(self) -> None:
        """启连后存储中的审核工具范围变化：冻结态不再等于存储值，拒绝继续服务。"""

        manager, store, factory, _probe = _manager()
        await store.save(_config())
        await manager.connect(_identity())
        await store.save(_config(audit=replace(_audit(), tool_scope=("read_file", "write_file"))))

        evidence = await manager.recheck_audit_evidence(_identity())
        assert evidence.admitted is False
        assert evidence.reason == "config_changed_after_connect"

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert "config_changed_after_connect" in exc.value.sanitized_message
        assert factory.opened == 1  # 冻结态偏离即拒，不新开连接


class TestInFlightIsolation:
    @pytest.mark.asyncio
    async def test_duplicate_operation_registration_is_rejected(self) -> None:
        """同一 operation_id 的并发重复不得共享归属（INPUT_CONFLICT，原登记保持唯一）。"""

        manager, _store, _factory, _probe = _manager()
        manager.register_in_flight(_identity(), "op-1")

        with pytest.raises(AgentServiceError) as exc:
            manager.register_in_flight(_identity(), "op-1")

        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert manager.in_flight_operations(_identity()) == ("op-1",)


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

        await manager.mark_session_lost(_identity())

        assert manager.is_connected(_identity()) is False
        assert session.closed is True  # 失联也要确认实际退出后移除句柄
        assert factory.opened == 1

    @pytest.mark.asyncio
    async def test_probe_port_is_required_contract(self) -> None:
        # 探针端口仅声明形状：未实现真实本机载荷核定，禁止声称已验证。
        assert hasattr(MCPImplementationProbe, "probe")
        assert MCP_DEFAULT_DEADLINE_S == 30


async def _wait_until(predicate: Callable[[], bool], *, attempts: int = 200) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


class _CountingFactory:
    """open 首个 await 即让出，以便并发调用真正交错；记录实际 open 次数与存活 client。"""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        fail_times: int = 0,
        close_failures: int = 0,
        close_gate: asyncio.Event | None = None,
    ) -> None:
        self.delay = delay
        self.fail_times = fail_times
        self.close_failures = close_failures
        self.close_gate = close_gate
        self.opened: list[MCPServerConfig] = []
        self.sessions: list[_FakeSession] = []

    async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _FakeSession:
        self.opened.append(config)
        await asyncio.sleep(self.delay)
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("open failed")
        session = _FakeSession(
            (MCPToolDescriptor("read_file", _SCHEMA),),
            close_failures=self.close_failures,
            close_gate=self.close_gate,
        )
        # 关闭故障/闸门只作用于首个会话，便于验证关闭重试后的正常重连。
        self.close_failures = 0
        self.close_gate = None
        self.sessions.append(session)
        return session

    def live(self) -> list[_FakeSession]:
        return [session for session in self.sessions if not session.closed]


class _GatedFactory:
    """按服务身份/版本分闸的 open：用于验证不同身份互不阻塞。"""

    def __init__(self) -> None:
        self.opened: list[MCPServerConfig] = []
        self.sessions: list[_FakeSession] = []
        self.gates: dict[str, asyncio.Event] = {}

    def gate(self, key: str) -> asyncio.Event:
        return self.gates.setdefault(key, asyncio.Event())

    async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _FakeSession:
        self.opened.append(config)
        await self.gate(config.identity.stable_key()).wait()
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        self.sessions.append(session)
        return session


def _factory_manager(factory: MCPClientFactoryPort) -> tuple[SharedMCPConnectionManager, _FakeStore]:
    store = _FakeStore()
    manager = SharedMCPConnectionManager(
        store=store, factory=factory, probe=_FakeProbe(_FINGERPRINT), usage=_FrozenUsers()
    )
    return manager, store


class TestCloseConfirmation:
    """I17：closing/unknown 保留准确 handle；关闭未确认前重连不得绕过。"""

    @pytest.mark.asyncio
    async def test_close_failure_keeps_handle_and_reconnect_finishes_close(self) -> None:
        factory = _CountingFactory(close_failures=1)
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]

        with pytest.raises(RuntimeError) as excinfo:
            await manager.disconnect(_identity())
        assert "client close failed" in str(excinfo.value)

        assert first.close_calls == 1
        assert first.closed is False  # 句柄保留，不得先 pop 后关
        assert manager.is_connected(_identity()) is False
        with pytest.raises(AgentServiceError) as exc:
            manager.session(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

        await manager.connect(_identity())
        assert first.close_calls == 2
        assert first.closed is True  # 重连前先完成关闭
        assert len(factory.opened) == 2
        assert manager.session(_identity()) is factory.sessions[1]

    @pytest.mark.asyncio
    async def test_cancelled_disconnect_keeps_handle_until_close_confirmed(self) -> None:
        gate = asyncio.Event()
        factory = _CountingFactory(close_gate=gate)
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]

        closing = asyncio.create_task(manager.disconnect(_identity()))
        await _wait_until(lambda: first.close_calls == 1)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await closing
        assert isinstance(excinfo.value, asyncio.CancelledError)

        assert first.closed is False
        assert manager.is_connected(_identity()) is False

        gate.set()
        await _wait_until(lambda: first.closed is True)  # 后台关闭确认退出后才移除句柄
        await manager.connect(_identity())
        assert len(factory.opened) == 2
        assert manager.session(_identity()) is factory.sessions[1]

    @pytest.mark.asyncio
    async def test_reconnect_after_lost_refuses_when_close_remains_unconfirmed(self) -> None:
        factory = _CountingFactory(close_failures=2)
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]

        with pytest.raises(RuntimeError) as lost_exc:
            await manager.mark_session_lost(_identity())
        assert "client close failed" in str(lost_exc.value)
        assert first.closed is False

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert "close is unconfirmed" in exc.value.sanitized_message
        assert first.close_calls == 2  # 重连前重试关闭，不直接 open
        assert len(factory.opened) == 1

    @pytest.mark.asyncio
    async def test_normal_disconnect_removes_handle_only_after_confirmed_exit(self) -> None:
        factory = _CountingFactory()
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]

        await manager.disconnect(_identity())

        assert first.close_calls == 1
        assert first.closed is True
        with pytest.raises(AgentServiceError) as exc:
            manager.session(_identity())
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

        await manager.connect(_identity())
        assert len(factory.opened) == 2
        assert first.close_calls == 1  # 无未确认资源，重连不再重复关闭旧句柄


class TestUnknownOutcomeHoldsOwnership:
    """I53：后效未知的在途归属保留到 owner 真实退出被确认，不提前释放/不放开重连。"""

    @pytest.mark.asyncio
    async def test_unknown_operation_blocks_reconnect_until_close_confirmed(self) -> None:
        factory = _CountingFactory(close_failures=2)
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]
        manager.register_in_flight(_identity(), "op-1")
        manager.mark_in_flight_unknown(_identity(), "op-1")

        with pytest.raises(RuntimeError) as close_exc:
            await manager.disconnect(_identity())
        assert "client close failed" in str(close_exc.value)
        # 关闭未确认：句柄与未知归属都保留，不立即释放占用（I53）。
        assert first.closed is False
        assert manager.in_flight_operations(_identity()) == ("op-1",)

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert manager.in_flight_operations(_identity()) == ("op-1",)
        assert len(factory.opened) == 1

    @pytest.mark.asyncio
    async def test_unknown_ownership_released_after_confirmed_exit(self) -> None:
        factory = _CountingFactory(close_failures=1)
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]
        manager.register_in_flight(_identity(), "op-1")
        manager.mark_in_flight_unknown(_identity(), "op-1")

        with pytest.raises(RuntimeError) as close_exc:
            await manager.disconnect(_identity())
        assert "client close failed" in str(close_exc.value)
        assert manager.in_flight_operations(_identity()) == ("op-1",)

        await manager.connect(_identity())  # 重连前先确认关闭

        assert first.closed is True
        assert manager.in_flight_operations(_identity()) == ()
        assert len(factory.opened) == 2

    @pytest.mark.asyncio
    async def test_lost_session_holds_unknown_ownership_until_exit_confirmed(self) -> None:
        factory = _CountingFactory(close_failures=1)
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())
        first = factory.sessions[0]
        manager.register_in_flight(_identity(), "op-1")
        manager.mark_in_flight_unknown(_identity(), "op-1")

        with pytest.raises(RuntimeError) as lost_exc:
            await manager.mark_session_lost(_identity())
        assert "client close failed" in str(lost_exc.value)
        assert manager.in_flight_operations(_identity()) == ("op-1",)

        await manager.connect(_identity())

        assert first.closed is True
        assert manager.in_flight_operations(_identity()) == ()
        assert len(factory.opened) == 2

    @pytest.mark.asyncio
    async def test_still_executing_operation_still_refuses_disconnect(self) -> None:
        """未判未知（仍可能执行）的在途 operation 依旧拒停，只有未知者允许走关闭确认。"""

        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())
        manager.register_in_flight(_identity(), "op-1")

        with pytest.raises(AgentServiceError) as exc:
            await manager.disconnect(_identity())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert session.closed is False

    @pytest.mark.asyncio
    async def test_stale_unknown_mark_does_not_shadow_new_generation_operation(self) -> None:
        """归属已随 owner 退出释放后再标记未知必须无效，否则会遮蔽新一代同 id 在途调用。"""

        factory = _CountingFactory()
        manager, store = _factory_manager(factory)
        await store.save(_config())
        await manager.connect(_identity())

        manager.register_in_flight(_identity(), "op-1")
        await manager.mark_session_lost(_identity())  # 真实退出已确认，归属随之释放
        assert manager.in_flight_operations(_identity()) == ()

        manager.mark_in_flight_unknown(_identity(), "op-1")  # 迟到的未知标记：不得留下悬空项
        await manager.connect(_identity())
        second = factory.sessions[1]
        manager.register_in_flight(_identity(), "op-1")  # 新一代连接上仍有真实在途调用

        with pytest.raises(AgentServiceError) as exc:
            await manager.disconnect(_identity())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert second.closed is False


class TestFrozenUsageGuard:
    """I59：断开/更新共享配置前核持久化使用者；内存计数为 0 不构成无使用者证据。"""

    @pytest.mark.asyncio
    async def test_disconnect_refused_while_queued_research_freezes_config(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        usage = _FrozenUsers()
        manager, _store, _factory, _probe = _manager(session=session, usage=usage)
        await manager.save_config(_config())
        await manager.connect(_identity())
        usage.freeze(_identity(), "research-queued-1")  # 研究已排队并冻结该配置版（持久化记录）
        assert manager.in_flight_operations(_identity()) == ()  # 无执行中调用，足以说明必须另有持久化核对

        with pytest.raises(AgentServiceError) as exc:
            await manager.disconnect(_identity())

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "research-queued-1" in exc.value.sanitized_message
        assert manager.is_connected(_identity()) is True
        assert session.closed is False
        assert usage.calls  # 持久化端口被实际查询

    @pytest.mark.asyncio
    async def test_disconnect_proceeds_when_no_persisted_user_references_config(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())

        await manager.disconnect(_identity())

        assert session.closed is True
        assert manager.is_connected(_identity()) is False

    @pytest.mark.asyncio
    async def test_disconnect_refused_when_usage_store_is_unreadable(self) -> None:
        usage = _FrozenUsers()
        manager, _store, _factory, _probe = _manager(usage=usage)
        await manager.save_config(_config())
        await manager.connect(_identity())
        usage.fail = RuntimeError("research index unavailable")

        with pytest.raises(AgentServiceError) as exc:
            await manager.disconnect(_identity())

        # 无法证明无使用者即拒绝（fail-closed），不把未知当空。
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert manager.is_connected(_identity()) is True

    @pytest.mark.asyncio
    async def test_disconnect_rechecks_in_flight_after_usage_query(self) -> None:
        """并发对抗：持久化查询期间新登记的调用不得被「检查通过 → 摘下会话」缝隙漏掉。"""

        class _RacingUsage:
            def __init__(self) -> None:
                self.manager: SharedMCPConnectionManager | None = None

            async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
                assert self.manager is not None
                # 只在尚未登记时模拟「查询期间新到调用」：同一 operation 重复登记本身
                # 已被 INPUT_CONFLICT 拒绝（不共享归属），不构成本用例的并发场景。
                if "op-raced" not in self.manager.in_flight_operations(identity):
                    self.manager.register_in_flight(identity, "op-raced")
                return ()

        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        usage = _RacingUsage()
        manager = SharedMCPConnectionManager(
            store=_FakeStore(),
            factory=_FakeFactory(session),
            probe=_FakeProbe(_FINGERPRINT),
            usage=usage,
        )
        usage.manager = manager
        await manager.save_config(_config())
        await manager.connect(_identity())

        with pytest.raises(AgentServiceError) as exc:
            await manager.disconnect(_identity())

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert manager.is_connected(_identity()) is True
        assert session.closed is False

    @pytest.mark.asyncio
    async def test_save_config_refused_while_research_freezes_same_version(self) -> None:
        usage = _FrozenUsers()
        manager, store, _factory, _probe = _manager(usage=usage)
        await manager.save_config(_config())
        usage.freeze(_identity(), "research-paused-7")

        with pytest.raises(AgentServiceError) as exc:
            await manager.save_config(_config(display_name="改名后的服务"))

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "research-paused-7" in exc.value.sanitized_message
        stored = await store.load(_identity())
        assert stored is not None and stored.display_name == "本地文件服务"  # 未被就地改写

    @pytest.mark.asyncio
    async def test_new_config_version_is_not_blocked_by_old_version_users(self) -> None:
        usage = _FrozenUsers()
        manager, store, _factory, _probe = _manager(usage=usage)
        await manager.save_config(_config())
        usage.freeze(_identity(), "research-paused-7")

        receipt = await manager.save_config(
            _config(identity=_identity(config_version=2), audit=_audit(_identity(config_version=2)))
        )

        assert receipt.connections_opened == 0
        new_identity = _identity(config_version=2)
        assert await store.load(new_identity) == _config(identity=new_identity, audit=_audit(new_identity))


class TestConnectTotalDeadline:
    """I13：启动/初始化/鉴权/发现共用同一总期限，发现只拿剩余额度。"""

    @pytest.mark.asyncio
    async def test_discovery_is_capped_by_remaining_budget(self) -> None:
        # open 6 秒 + 发现 6 秒：各自 < 10 秒，合计 12 秒 > 总期限 10 秒。
        manager, store, _factory, session, _clock = _deadline_manager(open_s=6, work_s=6)
        await store.save(_config(connect_deadline_s=10))

        with pytest.raises(TimeoutError) as excinfo:
            await manager.connection_test(_identity())

        assert "exceeded granted budget" in str(excinfo.value)
        assert session.list_budgets == [4]  # 只拿到剩余 4 秒，不是又一个完整 10 秒
        assert session.closed is True  # 超时后仍收尾关闭

    @pytest.mark.asyncio
    async def test_total_budget_exactly_at_limit_succeeds(self) -> None:
        manager, store, _factory, session, _clock = _deadline_manager(open_s=6, work_s=4)
        await store.save(_config(connect_deadline_s=10))

        receipt = await manager.connection_test(_identity())

        assert receipt.tool_names == ("read_file",)
        assert session.list_budgets == [4]  # 发现恰好用满剩余额度，总耗时等于总期限
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_total_budget_over_by_one_millisecond_is_rejected(self) -> None:
        # open 用掉 6.001 秒：剩余 3.999 秒，整秒预算只余 3 秒，4 秒的发现必然超限。
        manager, store, _factory, session, _clock = _deadline_manager(open_s=6.001, work_s=4)
        await store.save(_config(connect_deadline_s=10))

        with pytest.raises(TimeoutError) as excinfo:
            await manager.connection_test(_identity())

        assert "exceeded granted budget" in str(excinfo.value)
        assert session.list_budgets == [3]
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_exhausted_budget_rejects_before_discovery_and_closes(self) -> None:
        # open 超出总期限 1 毫秒：发现阶段根本不得进入，且已开出的句柄必须关闭。
        manager, store, _factory, session, _clock = _deadline_manager(open_s=10.001, work_s=1)
        await store.save(_config(connect_deadline_s=10))

        with pytest.raises(AgentServiceError) as exc:
            await manager.connection_test(_identity())

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "deadline exceeded before discovery" in exc.value.sanitized_message
        assert session.list_budgets == []
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_connect_exhausted_budget_leaves_no_owner_or_handle(self) -> None:
        manager, store, _factory, session, _clock = _deadline_manager(open_s=10.001, work_s=1)
        await store.save(_config(connect_deadline_s=10))

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert session.closed is True
        assert manager.is_connected(_identity()) is False

        # 句柄已确认退出：再次启连先走到总期限检查，而不是「上次关闭未确认」。
        with pytest.raises(AgentServiceError) as retry:
            await manager.connect(_identity())
        assert retry.value.code is AgentErrorCode.POLICY_DENIED

    @pytest.mark.asyncio
    async def test_one_second_config_is_not_rejected_by_integer_truncation(self) -> None:
        # 合法最小配置 connect_deadline_s=1：open 用掉 0.4 秒后剩余 0.6 秒，
        # 整秒截断会得到 0 并误拒；应授予端口最小 1 秒并由真实剩余时长硬切。
        manager, store, _factory, session, _clock = _deadline_manager(open_s=0.4, work_s=0.5)
        await store.save(_config(connect_deadline_s=1))

        receipt = await manager.connection_test(_identity())

        assert receipt.tool_names == ("read_file",)
        assert session.list_budgets == [1]
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_concurrent_waiters_share_one_total_budget(self) -> None:
        clock = _FakeClock()
        factory = _GatedTimedFactory(clock)
        store = _FakeStore()
        manager = SharedMCPConnectionManager(
            store=store,
            factory=factory,
            probe=_FakeProbe(_FINGERPRINT),
            usage=_FrozenUsers(),
            monotonic=clock,
        )
        await store.save(_config(connect_deadline_s=10))

        leader = asyncio.create_task(manager.connect(_identity()))
        await _wait_until(lambda: len(factory.opened) == 1)
        waiter = asyncio.create_task(manager.connect(_identity()))
        await asyncio.sleep(0)

        clock.advance(10.001)  # 共享启连的 open 期间耗尽总预算
        factory.gate.set()
        results = await asyncio.gather(leader, waiter, return_exceptions=True)

        assert len(factory.opened) == 1  # 等待者不新开连接
        errors = [item for item in results if isinstance(item, AgentServiceError)]
        assert len(errors) == 2
        assert all(item.code is AgentErrorCode.POLICY_DENIED for item in errors)  # 共享同一总预算
        assert factory.sessions[0].closed is True
        assert manager.is_connected(_identity()) is False


class TestConnectionTestReceiptBinding:
    """启连必须使用本轮测试回执核对目录/配置：回执要能精确复现测试所见。"""

    @pytest.mark.asyncio
    async def test_receipt_binds_config_digest_and_monotonic_generation(self) -> None:
        manager, _store, _factory, _probe = _manager()
        await manager.save_config(_config())

        first = await manager.connection_test(_identity())
        second = await manager.connection_test(_identity())

        assert first.config_digest == config_snapshot_digest(_config())
        assert first.generation == 1
        assert second.generation == 2
        assert manager.test_generation(_identity()) == 2

    @pytest.mark.asyncio
    async def test_failed_test_still_advances_generation(self) -> None:
        manager, _store, _factory, _probe = _manager(session=_FakeSession((), fail_list=True))
        await manager.save_config(_config())

        with pytest.raises(RuntimeError) as exc:
            await manager.connection_test(_identity())
        assert str(exc.value) == "transport dropped"
        assert manager.test_generation(_identity()) == 1

    def test_never_tested_generation_is_unknown(self) -> None:
        manager, _store, _factory, _probe = _manager()
        assert manager.test_generation(_identity()) is None


class TestTestResidueRecovery:
    """关闭未知的测试残留必须可查、可收尾，且收尾未知时不得重新新开连接。"""

    @pytest.mark.asyncio
    async def test_connect_refused_while_test_residue_is_unconfirmed(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),), close_failures=1)
        manager, _store, factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        with pytest.raises(AgentServiceError) as test_exc:
            await manager.connection_test(_identity())
        assert test_exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert test_exc.value.sanitized_message == "connection test session close is unconfirmed"
        assert len(manager.connection_test_residue(_identity())) == 1

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())

        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert "retry close before connecting" in exc.value.sanitized_message
        assert factory.opened == 1  # 只有测试那一次 open，不得重新新开

    @pytest.mark.asyncio
    async def test_retry_close_clears_residue_and_unblocks_connect(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),), close_failures=1)
        manager, _store, factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        with pytest.raises(AgentServiceError) as test_exc:
            await manager.connection_test(_identity())
        assert test_exc.value.code is AgentErrorCode.OPERATION_UNKNOWN

        closed = await manager.retry_test_residue_close(_identity())

        assert closed == 1
        assert manager.connection_test_residue(_identity()) == ()
        assert session.closed is True
        await manager.connect(_identity())
        assert factory.opened == 2

    @pytest.mark.asyncio
    async def test_retry_close_failure_keeps_residue(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),), close_failures=2)
        manager, _store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        with pytest.raises(AgentServiceError) as test_exc:
            await manager.connection_test(_identity())
        assert test_exc.value.code is AgentErrorCode.OPERATION_UNKNOWN

        with pytest.raises(AgentServiceError) as exc:
            await manager.retry_test_residue_close(_identity())
        assert exc.value.code is AgentErrorCode.OPERATION_UNKNOWN
        residue = manager.connection_test_residue(_identity())
        assert [item.error for item in residue] == ["RuntimeError"]

        await manager.retry_test_residue_close(_identity())
        assert manager.connection_test_residue(_identity()) == ()

    @pytest.mark.asyncio
    async def test_retry_close_without_residue_is_noop(self) -> None:
        manager, _store, _factory, _probe = _manager()
        await manager.retry_test_residue_close(_identity())
        assert manager.connection_test_residue(_identity()) == ()


class TestSameVersionUpdateWhileConnected:
    """活动连接持有的配置版不得就地改写：改写会让「已连接」不再等于「按存储配置服务」。"""

    @pytest.mark.asyncio
    async def test_save_refused_when_active_connection_would_drift(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())

        with pytest.raises(AgentServiceError) as exc:
            await manager.save_config(_config(connect_deadline_s=10))

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "active connection" in exc.value.sanitized_message
        stored = await store.load(_identity())
        assert stored is not None and stored.connect_deadline_s == MCP_DEFAULT_DEADLINE_S
        assert manager.is_connected(_identity()) is True

    @pytest.mark.asyncio
    async def test_display_name_only_update_is_still_allowed(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())

        await manager.save_config(_config(display_name="改名后的展示名"))

        stored = await store.load(_identity())
        assert stored is not None and stored.display_name == "改名后的展示名"
        assert manager.is_connected(_identity()) is True

    @pytest.mark.asyncio
    async def test_new_config_version_is_allowed_while_connected(self) -> None:
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        manager, store, _factory, _probe = _manager(session=session)
        await manager.save_config(_config())
        await manager.connect(_identity())

        new_identity = _identity(config_version=2)
        await manager.save_config(_config(identity=new_identity, audit=_audit(new_identity), connect_deadline_s=10))

        stored = await store.load(new_identity)
        assert stored is not None and stored.connect_deadline_s == 10
        assert manager.is_connected(_identity()) is True


class _FakeClock:
    """可推进的单调时钟：超时用例不依赖真实 sleep，保证确定性。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _TimedSession:
    """发现阶段按被授予的整秒预算判定；工作耗时超预算即抛 ``TimeoutError``（同真实实现）。"""

    def __init__(self, clock: _FakeClock, work_s: float) -> None:
        self._clock = clock
        self._work_s = work_s
        self.list_budgets: list[int] = []
        self.closed = False

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        self.list_budgets.append(deadline_s)
        if self._work_s > deadline_s:
            raise TimeoutError("list_tools exceeded granted budget")
        self._clock.advance(self._work_s)
        return (MCPToolDescriptor("read_file", _SCHEMA),)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        raise AssertionError("connection test/discovery must not call research tools")

    async def aclose(self) -> None:
        self.closed = True


class _TimedFactory:
    def __init__(self, clock: _FakeClock, *, open_s: float, session: _TimedSession) -> None:
        self._clock = clock
        self._open_s = open_s
        self._session = session
        self.opened: list[MCPServerConfig] = []

    async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _TimedSession:
        self.opened.append(config)
        self._clock.advance(self._open_s)  # 启动/初始化/鉴权耗时
        return self._session


class _GatedTimedFactory:
    """按闸门阻塞 open，用于验证并发等待者共享同一次启连及其总预算。"""

    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        self.opened: list[MCPServerConfig] = []
        self.sessions: list[_TimedSession] = []
        self.gate = asyncio.Event()

    async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _TimedSession:
        self.opened.append(config)
        await self.gate.wait()
        session = _TimedSession(self._clock, 1.0)
        self.sessions.append(session)
        return session


def _deadline_manager(
    *, open_s: float, work_s: float
) -> tuple[SharedMCPConnectionManager, _FakeStore, _TimedFactory, _TimedSession, _FakeClock]:
    clock = _FakeClock()
    session = _TimedSession(clock, work_s)
    factory = _TimedFactory(clock, open_s=open_s, session=session)
    store = _FakeStore()
    manager = SharedMCPConnectionManager(
        store=store, factory=factory, probe=_FakeProbe(_FINGERPRINT), usage=_FrozenUsers(), monotonic=clock
    )
    return manager, store, factory, session, clock


class TestConnectSingleFlight:
    @pytest.mark.asyncio
    async def test_100_concurrent_connects_open_once_with_single_live_owner(self) -> None:
        factory = _CountingFactory()
        manager, store = _factory_manager(factory)
        await store.save(_config())

        await asyncio.gather(*(manager.connect(_identity()) for _ in range(100)))

        assert len(factory.opened) == 1
        assert len(factory.live()) == 1
        assert manager.is_connected(_identity()) is True
        assert manager.session(_identity()) is factory.live()[0]

        await manager.connect(_identity())
        assert len(factory.opened) == 1  # 已存活 owner 不再 open

    @pytest.mark.asyncio
    async def test_failed_open_is_shared_and_leaves_no_owner(self) -> None:
        factory = _CountingFactory(fail_times=1)
        manager, store = _factory_manager(factory)
        await store.save(_config())

        results = await asyncio.gather(*(manager.connect(_identity()) for _ in range(10)), return_exceptions=True)

        assert len(factory.opened) == 1  # 失败也只尝试一次，等待者共享该结果
        assert all(isinstance(item, RuntimeError) for item in results)
        assert manager.is_connected(_identity()) is False
        assert factory.live() == []

        await manager.connect(_identity())  # 失败后不留残留 single-flight 状态
        assert len(factory.opened) == 2
        assert len(factory.live()) == 1

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_abort_shared_connect(self) -> None:
        factory = _CountingFactory(delay=0.01)
        manager, store = _factory_manager(factory)
        await store.save(_config())

        leader = asyncio.create_task(manager.connect(_identity()))
        await _wait_until(lambda: len(factory.opened) == 1)
        waiter = asyncio.create_task(manager.connect(_identity()))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await waiter
        assert isinstance(excinfo.value, asyncio.CancelledError)
        assert waiter.cancelled() is True
        assert manager.is_connected(_identity()) is False  # 取消单个等待者不提前登记

        await asyncio.wait_for(leader, timeout=1.0)
        assert len(factory.opened) == 1
        assert len(factory.live()) == 1
        assert manager.session(_identity()) is factory.live()[0]

    @pytest.mark.asyncio
    async def test_different_identities_do_not_block_each_other(self) -> None:
        factory = _GatedFactory()
        manager, store = _factory_manager(factory)
        other = MCPServiceIdentity(service_id="svc-remote-tools", config_version=1)
        await store.save(_config())
        await store.save(_config(identity=other, audit=_audit(other)))
        factory.gate(other.stable_key()).set()

        blocked = asyncio.create_task(manager.connect(_identity()))
        await _wait_until(lambda: len(factory.opened) == 1)

        await asyncio.wait_for(manager.connect(other), timeout=1.0)
        assert blocked.done() is False  # 前一身份仍被自身 open 阻塞

        factory.gate(_identity().stable_key()).set()
        await asyncio.wait_for(blocked, timeout=1.0)
        assert len(factory.opened) == 2
        assert manager.is_connected(_identity()) is True
        assert manager.is_connected(other) is True
