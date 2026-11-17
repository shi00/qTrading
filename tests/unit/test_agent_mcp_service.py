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
import tempfile
from collections.abc import Callable
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
    MCPClientFactoryPort,
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

    async def open(self, config: MCPServerConfig) -> _FakeSession:
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

    async def open(self, config: MCPServerConfig) -> _FakeSession:
        self.opened.append(config)
        await self.gate(config.identity.stable_key()).wait()
        session = _FakeSession((MCPToolDescriptor("read_file", _SCHEMA),))
        self.sessions.append(session)
        return session


def _factory_manager(factory: MCPClientFactoryPort) -> tuple[SharedMCPConnectionManager, _FakeStore]:
    store = _FakeStore()
    manager = SharedMCPConnectionManager(store=store, factory=factory, probe=_FakeProbe(_FINGERPRINT))
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
    async def test_concurrent_waiters_share_one_total_budget(self) -> None:
        clock = _FakeClock()
        factory = _GatedTimedFactory(clock)
        store = _FakeStore()
        manager = SharedMCPConnectionManager(
            store=store, factory=factory, probe=_FakeProbe(_FINGERPRINT), monotonic=clock
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

    async def open(self, config: MCPServerConfig) -> _TimedSession:
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

    async def open(self, config: MCPServerConfig) -> _TimedSession:
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
    manager = SharedMCPConnectionManager(store=store, factory=factory, probe=_FakeProbe(_FINGERPRINT), monotonic=clock)
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
