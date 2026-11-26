"""生产组合根 Agent 接线单测（P01；DD §7.4/§10.6）。

覆盖：facade 复用共享引擎/账本、launch 门闩（needs-spike 前零派发）、隔离失败
零 launch、未就绪不构造关闭链、关闭前置先于共享引擎释放。
"""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa

from app import agent_wiring as wiring_mod
from app.agent_wiring import (
    AgentWiring,
    _LaunchedCloseFactSink,
    _LaunchedWorkflowLedger,
    _NativeAnswerSender,
    _PendingCloseFactSink,
    _ProcessDispatchGate,
    wire_agent_runtime,
)
from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AnswerCommandFact,
    CommandRef,
    CommitState,
    ConsumeAnswerRequest,
    ConsumptionFact,
    ConsumptionState,
    DeliveryState,
    DispatchCloseFact,
    SubmissionReceipt,
    json_doc,
    stable_id,
)
from services.agent_service import AgentService, CloseReadinessReceipt
from services.agent_service.dispatch import DISPATCH_CLOSED
from services.agent_service.runtime import CloseCommandIdentity
from services.agent_service.skills import SkillCatalogService
from services.agent_service.recovery import InstanceGuardEvidence
from services.agent_service.runtime import OwnerLoopBridge
from services.agent_service.workflows import AnswerConsumerRegistry

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_migration_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离迁移凭据与版本元数据来源：单测不读真实环境变量/keyring/用户配置。"""
    monkeypatch.setattr(wiring_mod, "get_agent_migration_db_url", lambda: None)
    monkeypatch.setattr(wiring_mod, "_embedded_single_user_mode", lambda: True)
    monkeypatch.setattr(wiring_mod, "version", lambda _name: "0.11.7")


async def _direct_runner(fn: Any) -> Any:
    return fn()


class _FakeGuard:
    def __init__(self) -> None:
        self.released = 0

    async def acquire(self) -> InstanceGuardEvidence:
        return InstanceGuardEvidence(acquired=True, prior_writer_exited=True, detail="test")

    def release(self) -> None:
        self.released += 1


class _FakeClient:
    """原生控制面替身：``raise_on_list`` 模拟隔离列举失败。"""

    def __init__(self, *, raise_on_list: bool = False) -> None:
        self.raise_on_list = raise_on_list
        self.cancelled: list[list[str]] = []

    async def list_workflows_async(self, **_kwargs: Any) -> list[Any]:
        if self.raise_on_list:
            raise RuntimeError("native listing failed")
        return []

    async def cancel_workflows_async(self, workflow_ids: list[str], *, cancel_children: bool = False) -> None:
        self.cancelled.append(list(workflow_ids))

    async def resume_workflows_async(self, workflow_ids: list[str]) -> None:
        return None

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None:
        self.deleted = list(workflow_ids)


class _FakeHandle:
    def __init__(self, *, client: _FakeClient) -> None:
        self.client = client
        self.runtime_engine: Any = SimpleNamespace()
        self.close_calls = 0
        self._closed = False
        self._launched = False

    @property
    def launched(self) -> bool:
        return self._launched

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self.close_calls += 1
        self._closed = True


class _FakeRegistry:
    """应用登记面替身（AgentDao 最小恢复面 + 答复消费面）。"""

    def __init__(self, *, recovery: tuple[Any, ...] = ()) -> None:
        self.finalized: list[str] = []
        self.consumed: list[ConsumeAnswerRequest] = []
        self.recovery = recovery

    async def consume_answer(self, request: ConsumeAnswerRequest) -> ConsumptionFact:
        self.consumed.append(request)
        return ConsumptionFact(
            command_record_id=request.command_record_id,
            decision_id="d-1",
            consumption_state=ConsumptionState.CONSUMED,
            consumed_at="2026-11-23T00:00:00.000000Z",
            answer_consumed_at="2026-11-23T00:00:00.000000Z",
        )

    async def list_recovery_executions(self, user_scope: str) -> tuple[Any, ...]:
        return self.recovery

    async def mark_dispatch_closed(self, request: Any) -> DispatchCloseFact:
        return DispatchCloseFact(user_scope=request.user_scope, closed_execution_ids=())

    async def save_startup_isolation(self, record: Any) -> str:
        return stable_id("command", [record.user_scope, record.target_id, record.client_command_id])

    async def finalize_startup_isolation(
        self, *, user_scope: str, isolation_command_id: str, receipt_payload: str
    ) -> None:
        self.finalized.append(isolation_command_id)

    async def grant_recovery_epoch(self, request: Any) -> Any:  # pragma: no cover - 本片未用
        raise AssertionError("grant_recovery_epoch not expected")


def _cache_manager() -> Any:
    engine = SimpleNamespace(url=sa.make_url("postgresql+asyncpg://u:p@localhost:5432/qtrading"))
    return SimpleNamespace(agent_dao=_FakeRegistry(), engine=engine)


async def _wire(
    *,
    client: _FakeClient | None = None,
    launch_allowed: bool | None = None,
    handle: _FakeHandle | None = None,
    skills_root: str | None = None,
) -> tuple[AgentWiring, _FakeHandle, _FakeGuard]:
    fake_client = client or _FakeClient()
    fake_handle = handle or _FakeHandle(client=fake_client)
    guard = _FakeGuard()
    wiring = await wire_agent_runtime(
        _cache_manager(),
        isolation_id="startup-test",
        assemble_func=lambda _settings: fake_handle,
        instance_guard=guard,
        blocking_runner=_direct_runner,
        launch_allowed=launch_allowed,
        skills_root=skills_root,
    )
    return wiring, fake_handle, guard


async def test_facade_reuses_shared_engine_and_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Any] = []
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: launched.append(handle))

    cache = _cache_manager()
    fake_handle = _FakeHandle(client=_FakeClient())
    wiring = await wire_agent_runtime(
        cache,
        isolation_id="startup-test",
        assemble_func=lambda _settings: fake_handle,
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
        launch_allowed=False,
    )

    assert isinstance(wiring.service, AgentService)
    assert wiring.service._dao is cache.agent_dao  # 共享引擎/账本，不另起连接池
    assert isinstance(wiring.bridge, OwnerLoopBridge)
    # 角色目录与会话模型选择已接线（复用共享 app_state 配置域）。
    assert wiring.role_catalog is not None
    assert wiring.session_models is not None
    assert launched == []  # needs-spike 门闩：不提前 launch


async def test_unlaunched_handle_keeps_answer_delivery_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """未 launch（零 workflow）不注入答复发送器：交付入口保持 fail-closed。"""

    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda _handle: None)
    wiring, _handle, _guard = await _wire()

    assert wiring.launched is False
    assert wiring.service._answer_sender is None


async def test_launched_handle_wires_native_answer_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    """已 launch 宿主复用同一 DBOS client 注入答复发送器（P1-05 生产接线）。"""

    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda _handle: None)
    handle = _FakeHandle(client=_FakeClient())
    handle._launched = True

    wiring, returned_handle, _guard = await _wire(handle=handle)

    assert returned_handle is handle
    assert wiring.launched is True
    assert isinstance(wiring.service._answer_sender, _NativeAnswerSender)


async def test_wiring_installs_answer_consumer_bound_to_shared_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """答复消费接线（P1-05）：消费者绑定同一用户域与共享账本，命令身份与保存一致。"""

    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda _handle: None)
    cache = _cache_manager()
    ledger = cache.agent_dao

    wiring = await wire_agent_runtime(
        cache,
        isolation_id="startup-test",
        assemble_func=lambda _settings: _FakeHandle(client=_FakeClient()),
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
        launch_allowed=False,
    )

    consumer = AnswerConsumerRegistry.require()
    fact = await consumer("d-1", "ui-answer:d-1")

    expected_id = stable_id("command", ["local-desktop", "d-1", "ui-answer:d-1"])
    assert fact.command_record_id == expected_id
    assert [request.command_record_id for request in ledger.consumed] == [expected_id]
    assert [request.user_scope for request in ledger.consumed] == ["local-desktop"]
    assert wiring.service is not None


async def test_skills_root_wires_catalog_into_facade(tmp_path: Path) -> None:
    wiring, _handle, _guard = await _wire(skills_root=str(tmp_path / "skills"))

    assert isinstance(wiring.service, AgentService)
    assert isinstance(wiring.service._skills, SkillCatalogService)  # 用户级启停/角色版本选择已接线
    assert (tmp_path / "skills").is_dir()


async def test_unusable_skills_root_degrades_without_blocking(tmp_path: Path) -> None:
    blocker = tmp_path / "skills"
    blocker.write_text("not a directory")

    wiring, _handle, _guard = await _wire(skills_root=str(blocker))

    assert isinstance(wiring.service, AgentService)  # 目录不可用不阻断既有运行链
    assert wiring.service._skills is None


async def test_wiring_installs_leaf_runner_and_closed_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.agent_service import ResearchDispatcher
    from services.agent_service.workflows import LeafRuntimeRegistry

    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: None)

    wiring, _handle, _guard = await _wire(launch_allowed=False)

    assert callable(LeafRuntimeRegistry._instance)  # 生产组合根安装叶 runner
    assert isinstance(wiring.dispatcher, ResearchDispatcher)  # 派发入口已接线
    assert wiring.service._dispatcher is wiring.dispatcher
    assert wiring.dispatch_closed() is True  # 未 launch：派发门闩关闭（零派发）


async def test_leaf_runtime_install_failure_keeps_zero_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Any] = []
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: launched.append(handle))

    def _boom(_runtime: Any = None) -> None:
        raise RuntimeError("leaf wiring broken")

    monkeypatch.setattr(wiring_mod, "install_leaf_runtime", _boom)

    wiring = await wire_agent_runtime(
        _cache_manager(),
        isolation_id="startup-test",
        assemble_func=lambda _settings: _FakeHandle(client=_FakeClient()),
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
        launch_allowed=True,
    )

    assert launched == []
    assert wiring.failure == "leaf_runtime_install_failed:RuntimeError"
    assert wiring.runtime is None and wiring.dispatcher is None
    assert wiring.service is not None  # 只读面仍可用，但零派发


async def test_launch_gate_blocks_launch_and_reports_zero_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Any] = []
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: launched.append(handle))

    wiring, handle, _guard = await _wire(launch_allowed=False)

    assert launched == []
    assert handle._launched is False
    assert wiring.ready is True  # 宿主组装 + 隔离就绪
    assert wiring.launched is False
    assert wiring.dispatch_closed() is True


async def test_launch_allowed_after_needs_spike_calls_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Any] = []
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: launched.append(handle))

    wiring, handle, _guard = await _wire(launch_allowed=True)

    assert launched == [handle]
    # 门闩只封装 SDK 调用；句柄 launched 由 SDK 置位，本替身仍为 False
    assert wiring.ready is True


async def test_isolation_failure_zero_launch_and_no_close_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Any] = []
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: launched.append(handle))

    wiring, handle, guard = await _wire(client=_FakeClient(raise_on_list=True), launch_allowed=True)

    assert launched == []
    assert handle.close_calls == 1  # 未上线宿主已收口
    assert guard.released == 1
    assert wiring.isolation is not None and wiring.isolation.ready is False
    assert wiring.handle is None
    assert wiring.runtime is None  # 未就绪不构造关闭链
    assert wiring.ready is False
    assert wiring.failure is not None


async def test_prepare_close_is_noop_when_not_ready() -> None:
    wiring, handle, _guard = await _wire(client=_FakeClient(raise_on_list=True))

    receipt = await wiring.prepare_close()

    assert receipt is None
    assert handle.close_calls == 1  # 仅启动阶段收口一次，未额外启动关闭链


async def test_close_prelude_closes_host_before_shared_engine_release() -> None:
    wiring, handle, _guard = await _wire()

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is True
    assert handle.closed is True
    assert handle.close_calls == 1


async def test_prepare_close_order_precedes_shared_engine_disposal() -> None:
    from app.application import ApplicationSession

    calls: list[str] = []

    class _FakeWiring:
        async def prepare_close(self) -> None:
            calls.append("agent")

        def close_ready(self) -> bool:
            return True

    class _FakeCoordinator:
        async def do_cleanup(self, **_kwargs: Any) -> bool:
            calls.append("cleanup")
            return True

    class _FakeCache:
        async def close(self) -> None:
            calls.append("engine")

    session = ApplicationSession(SimpleNamespace())  # type: ignore[arg-type]  # [reason: 回滚路径不使用 page]
    session.agent_wiring = _FakeWiring()
    session.coordinator = _FakeCoordinator()  # type: ignore[assignment]  # [reason: 测试替身]
    session.cache_manager = _FakeCache()  # type: ignore[assignment]  # [reason: 测试替身]

    await session._rollback()

    assert calls == ["agent", "cleanup", "engine"]


async def test_prepare_close_propagates_cancelled_error() -> None:
    calls: list[str] = []

    class _CancellingWiring:
        async def prepare_close(self) -> None:
            calls.append("agent")
            raise asyncio.CancelledError()

    from app.application import ApplicationSession

    session = ApplicationSession(SimpleNamespace())  # type: ignore[arg-type]  # [reason: 回滚路径不使用 page]
    session.agent_wiring = _CancellingWiring()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await session.prepare_agent_close()

    assert isinstance(exc_info.value, asyncio.CancelledError)
    assert calls == ["agent"]


async def test_close_fact_sink_never_lies_after_launch() -> None:
    from services.agent_service.runtime import CloseCommandIdentity

    command = CloseCommandIdentity(user_scope="local-desktop", close_command_id="c1")
    assert (await _PendingCloseFactSink(launched=False).save_end_or_unknown(command)).saved is True
    assert (await _PendingCloseFactSink(launched=True).save_end_or_unknown(command)).saved is False


async def test_cleanup_ports_wired_only_when_host_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: None)
    wiring, _handle, _guard = await _wire(launch_allowed=False)
    assert wiring.ready is True
    assert wiring.cleanup is not None
    assert wiring.cleanup_failure is None


async def test_cleanup_not_wired_when_host_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring_mod, "launch_agent_runtime", lambda handle: None)
    wiring, _handle, _guard = await _wire(client=_FakeClient(raise_on_list=True))
    assert wiring.ready is False
    assert wiring.cleanup is None


def test_process_dispatch_gate_is_closed_while_unlaunched() -> None:
    unlaunched = _ProcessDispatchGate(launched=False)
    assert unlaunched.dispatch_closed() is True

    launched = _ProcessDispatchGate(launched=True)
    assert launched.dispatch_closed() is False
    assert launched.close_dispatch() is True
    assert launched.dispatch_closed() is True


def test_unit_converter_exposes_strategy_threshold_entry() -> None:
    """单位换算注入点即策略层统一入口（app 层唯一合法注入位置）。"""

    from strategies.utils import threshold_in_data_unit

    assert wiring_mod.agent_unit_converter() is threshold_in_data_unit


# ---------------------------------------------------------------------------
# 关闭前置（prepare_close）分支
# ---------------------------------------------------------------------------


class _FakeAgentService:
    """facade 替身：只记录 ``aclose`` 调用次数。"""

    def __init__(self) -> None:
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


class _CountingGuard:
    """唯一执行锁替身：记录 ``release`` 次数。"""

    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


class _FakeRuntime:
    """关闭链替身：按需返回回执、抛异常或延迟（触发超时分支）。"""

    def __init__(
        self,
        *,
        receipt: CloseReadinessReceipt | None = None,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.receipt = receipt
        self.error = error
        self.delay = delay
        self.calls = 0

    async def prepare_close(self, command: Any) -> CloseReadinessReceipt:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        assert self.receipt is not None
        return self.receipt


class _CloseableHandle:
    """未收口宿主替身：``close`` 可成功或按需抛错。"""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.error is not None:
            raise self.error


class _DirectPool:
    """线程池替身：直接在当前协程内执行同步关闭函数。"""

    async def run_async(self, task_type: Any, fn: Any) -> Any:
        return fn()


class _SlowDirectPool:
    """线程池替身：延迟执行同步关闭（模拟在途关闭），并记录投递次数。"""

    def __init__(self, *, delay: float = 0.05) -> None:
        self.delay = delay
        self.calls = 0

    async def run_async(self, task_type: Any, fn: Any) -> Any:
        self.calls += 1
        await asyncio.sleep(self.delay)
        return fn()


class _HangingPool:
    """线程池替身：同步关闭一直不返回（模拟被延迟工作流拖住的退出路径）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run_async(self, task_type: Any, fn: Any) -> Any:
        self.calls += 1
        await asyncio.sleep(3600)


class _FailingPool:
    """线程池替身：同步关闭抛错，并记录投递次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run_async(self, task_type: Any, fn: Any) -> Any:
        self.calls += 1
        raise RuntimeError("close boom")


def _unclosed_wiring(*, handle: Any, guard: Any | None = None) -> AgentWiring:
    """隔离失败保留句柄的接线替身（无 runtime、无关闭链，只有待收口句柄）。"""

    return AgentWiring(
        service=cast(Any, _FakeAgentService()),
        bridge=OwnerLoopBridge.for_running_loop(),
        unclosed_handle=cast(Any, handle),
        instance_guard=guard,
    )


def _patch_pool(monkeypatch: pytest.MonkeyPatch, pool: Any) -> None:
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", lambda: pool)


def _receipt(*, ready: bool) -> CloseReadinessReceipt:
    from services.agent_service.runtime import CloseCommandIdentity

    return CloseReadinessReceipt(
        command=CloseCommandIdentity(user_scope="local-desktop", close_command_id="app-close-test"),
        ready=ready,
        blocking_reasons=() if ready else ("io_in_flight",),
        unknown_associations=(),
        inflight_task_count=0,
        checked_at="2026-11-18T00:00:00.000000Z",
    )


async def test_prepare_close_without_service_is_noop() -> None:
    wiring = AgentWiring(service=None, bridge=OwnerLoopBridge.for_running_loop())

    assert await wiring.prepare_close() is None
    assert wiring._receipt is None


async def test_close_prelude_cancellation_propagates() -> None:
    service = _FakeAgentService()
    runtime = _FakeRuntime(error=asyncio.CancelledError())
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await wiring.prepare_close()

    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert service.aclose_calls == 0


async def test_close_prelude_timeout_records_unready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wiring_mod, "AGENT_CLOSE_PRELUDE_TIMEOUT_S", 0.01)
    service = _FakeAgentService()
    runtime = _FakeRuntime(receipt=_receipt(ready=True), delay=0.2)
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    assert await wiring.prepare_close() is None
    assert wiring._receipt is None
    assert service.aclose_calls == 0  # 未就绪不关 facade，保留可用面（R2-I35）


async def test_close_prelude_failure_recorded_without_masking_shutdown() -> None:
    service = _FakeAgentService()
    runtime = _FakeRuntime(error=RuntimeError("prelude boom"))
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    assert await wiring.prepare_close() is None
    assert wiring._receipt is None
    assert service.aclose_calls == 0  # 未就绪不关 facade，保留可用面（R2-I35）


async def test_close_prelude_not_ready_receipt_keeps_blocking_reasons() -> None:
    service = _FakeAgentService()
    runtime = _FakeRuntime(receipt=_receipt(ready=False))
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is False
    assert receipt.blocking_reasons == ("io_in_flight",)
    assert wiring._receipt is receipt
    assert wiring.close_ready() is False
    assert service.aclose_calls == 0


async def test_close_gate_probe_supplies_residual_evidence() -> None:
    """生产接线必须给出残留 IO 证据，不得省略（R2-I35）。"""
    wiring, handle, _guard = await _wire()

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is True
    assert receipt.residual_io_probed is True
    assert handle.closed is True
    assert wiring.close_ready() is True


async def test_normal_close_releases_instance_guard() -> None:
    """正常关闭必须把唯一执行锁交给关闭链，就绪后释放（同进程切库可接管）。"""
    wiring, handle, guard = await _wire()

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is True
    assert handle.closed is True
    assert guard.released == 1
    assert wiring.instance_guard is None


class _FakeMCPIdentity:
    def stable_key(self) -> str:
        return "stdio:demo"


class _FakeMCPLifecycle:
    """MCP 生命周期替身：按需让 disconnect 失败。"""

    def __init__(self, *, failures: int = 0) -> None:
        self._remaining_failures = failures
        self.disconnects = 0

    def connected_identities(self) -> tuple[Any, ...]:
        return (_FakeMCPIdentity(),)

    async def disconnect(self, identity: Any) -> None:
        self.disconnects += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise RuntimeError("disconnect failed")


async def test_mcp_disconnect_failure_blocks_close_ready() -> None:
    """MCP 关停失败属关闭回执未核事实：不得报「已安全关闭」。"""
    service = _FakeAgentService()
    wiring = AgentWiring(
        service=service,
        bridge=OwnerLoopBridge.for_running_loop(),
        runtime=cast(Any, _FakeRuntime(receipt=_receipt(ready=True))),
        mcp_lifecycle=cast(Any, _FakeMCPLifecycle(failures=1)),
    )

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is False
    assert "mcp_disconnect_failed:stdio:demo" in receipt.blocking_reasons
    assert wiring.close_ready() is False
    assert service.aclose_calls == 0  # 未核收尾：保留可用面，不先关 facade


async def test_mcp_disconnect_failure_blocks_unlaunched_close() -> None:
    """未 launch（无关闭链）时 MCP 未确认断开同样阻止释放依赖。"""
    service = _FakeAgentService()
    wiring = AgentWiring(
        service=service,
        bridge=OwnerLoopBridge.for_running_loop(),
        mcp_lifecycle=cast(Any, _FakeMCPLifecycle(failures=1)),
    )

    assert await wiring.prepare_close() is None
    assert wiring.close_ready() is False
    assert service.aclose_calls == 0


async def test_mcp_disconnect_retry_success_releases_close_gate() -> None:
    service = _FakeAgentService()
    lifecycle = _FakeMCPLifecycle(failures=1)
    wiring = AgentWiring(
        service=service,
        bridge=OwnerLoopBridge.for_running_loop(),
        runtime=cast(Any, _FakeRuntime(receipt=_receipt(ready=True))),
        mcp_lifecycle=cast(Any, lifecycle),
    )

    first = await wiring.prepare_close()
    second = await wiring.prepare_close()

    assert first is not None and first.ready is False
    assert second is not None and second.ready is True
    assert wiring.close_ready() is True
    assert lifecycle.disconnects == 2
    assert service.aclose_calls == 1


# ---------------------------------------------------------------------------
# 未收口句柄关闭（N05）：有界、单次投递、可报告的失效/在途结果
# ---------------------------------------------------------------------------


async def test_unclosed_handle_close_is_time_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """延迟的工作流执行不得拖死关闭：单次关闭有界，超时如实报告失效。"""
    monkeypatch.setattr(wiring_mod, "AGENT_UNCLOSED_HANDLE_CLOSE_TIMEOUT_S", 0.01)
    pool = _HangingPool()
    _patch_pool(monkeypatch, pool)
    guard = _CountingGuard()
    wiring = _unclosed_wiring(handle=_CloseableHandle(), guard=guard)

    receipt = await asyncio.wait_for(wiring.prepare_close(), timeout=1.0)

    assert receipt is None
    assert pool.calls == 1
    assert wiring.close_report.state == "failed"
    assert wiring.close_report.detail == "close_timeout"
    assert wiring.close_ready() is False  # 未确认关闭：保锁、不报就绪
    assert guard.released == 0


async def test_concurrent_close_entries_deliver_unclosed_close_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """多入口并发关闭共享同一次投递（窗口/断连不得各关一次）。"""
    pool = _SlowDirectPool(delay=0.05)
    _patch_pool(monkeypatch, pool)
    guard = _CountingGuard()
    handle = _CloseableHandle()
    wiring = _unclosed_wiring(handle=handle, guard=guard)

    await asyncio.gather(wiring.prepare_close(), wiring.prepare_close())

    assert pool.calls == 1
    assert handle.close_calls == 1
    assert guard.released == 1
    assert wiring.close_report.state == "closed"
    assert wiring.close_ready() is True


async def test_unclosed_close_reports_in_flight_while_dispatching(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭投递在途时结果可报告为 in_flight，不伪装成已关闭/未开始。"""
    pool = _SlowDirectPool(delay=0.1)
    _patch_pool(monkeypatch, pool)
    wiring = _unclosed_wiring(handle=_CloseableHandle(), guard=_CountingGuard())

    task = asyncio.create_task(wiring.prepare_close())
    await asyncio.sleep(0.02)

    assert wiring.close_report.state == "in_flight"
    await task
    assert wiring.close_report.state == "closed"


async def test_unclosed_close_failure_is_reported_without_unbounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失效后重试有界：同一句柄只投递一次，后续入口得到同一失效报告。"""
    pool = _FailingPool()
    _patch_pool(monkeypatch, pool)
    guard = _CountingGuard()
    wiring = _unclosed_wiring(handle=_CloseableHandle(), guard=guard)

    first = await wiring.prepare_close()
    second = await wiring.prepare_close()

    assert first is None and second is None
    assert pool.calls == 1  # 一次关闭只投递一次
    assert wiring.close_report.state == "failed"
    assert wiring.close_report.detail == "RuntimeError"
    assert wiring.close_ready() is False
    assert guard.released == 0


async def test_unclosed_close_after_cancellation_reports_exhausted_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消后再点关闭：不重投（次数用尽），如实报告失效而非无限等待。"""

    class _CancellingPool:
        def __init__(self) -> None:
            self.calls = 0

        async def run_async(self, task_type: Any, fn: Any) -> Any:
            self.calls += 1
            raise asyncio.CancelledError()

    pool = _CancellingPool()
    _patch_pool(monkeypatch, pool)
    wiring = _unclosed_wiring(handle=_CloseableHandle(), guard=_CountingGuard())

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await wiring.prepare_close()
    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert wiring.close_report.state == "pending"  # 取消不把在途当已关闭

    assert await wiring.prepare_close() is None
    assert pool.calls == 1  # 一次关闭只投递一次
    assert wiring.close_report.state == "failed"
    assert wiring.close_report.detail == "close_attempts_exhausted"


async def test_prepare_agent_close_reads_current_wiring_not_startup_copy() -> None:
    """首次向导/重试成功后，退出门必须读当前接线而非启动时复制的快照。"""
    from app.application import ApplicationSession

    calls: list[str] = []

    class _Wiring:
        def __init__(self, label: str) -> None:
            self._label = label

        async def prepare_close(self) -> None:
            calls.append(self._label)

        def close_ready(self) -> bool:
            return True

    session = ApplicationSession(SimpleNamespace())  # type: ignore[arg-type]  # [reason: 该路径不使用 page]
    session.agent_wiring = _Wiring("startup-copy")
    session.agent_wiring_provider = lambda: _Wiring("current")

    assert await session.prepare_agent_close() is True
    assert calls == ["current"]


async def test_prepare_agent_close_follows_wiring_created_after_start() -> None:
    """启动时为未接线（None）：向导完成后新建的接线仍被退出门消费。"""
    from app.application import ApplicationSession

    calls: list[str] = []

    class _Wiring:
        async def prepare_close(self) -> None:
            calls.append("current")

        def close_ready(self) -> bool:
            return True

    current: Any | None = None
    session = ApplicationSession(SimpleNamespace())  # type: ignore[arg-type]  # [reason: 该路径不使用 page]
    session.agent_wiring = None
    session.agent_wiring_provider = lambda: current

    assert await session.prepare_agent_close() is True
    assert calls == []

    current = _Wiring()
    assert await session.prepare_agent_close() is True
    assert calls == ["current"]


async def test_prepare_agent_close_requires_evidence_accessor() -> None:
    """无就绪证据访问器的接线不得默认放行依赖释放。"""

    class _NoEvidenceWiring:
        async def prepare_close(self) -> None:
            return None

    from app.application import ApplicationSession

    session = ApplicationSession(SimpleNamespace())  # type: ignore[arg-type]  # [reason: 该路径不使用 page]
    session.agent_wiring = _NoEvidenceWiring()

    assert await session.prepare_agent_close() is False


async def test_blocked_close_gate_keeps_session_resources_alive() -> None:
    """关闭门未就绪：启动回滚不得 cleanup / dispose 引擎（R2-I35）。"""
    calls: list[str] = []

    class _BlockedWiring:
        async def prepare_close(self) -> None:
            calls.append("agent")

        def close_ready(self) -> bool:
            return False

    class _Coordinator:
        async def do_cleanup(self, **_kwargs: Any) -> bool:
            calls.append("cleanup")
            return True

    class _Cache:
        async def close(self) -> None:
            calls.append("engine")

    from app.application import ApplicationSession

    session = ApplicationSession(SimpleNamespace())  # type: ignore[arg-type]  # [reason: 回滚路径不使用 page]
    session.agent_wiring = _BlockedWiring()
    session.coordinator = _Coordinator()  # type: ignore[assignment]  # [reason: 测试替身]
    session.cache_manager = _Cache()  # type: ignore[assignment]  # [reason: 测试替身]

    await session._rollback()

    assert calls == ["agent"]


async def test_second_prepare_close_reuses_close_command_identity() -> None:
    service = _FakeAgentService()
    runtime = _FakeRuntime(receipt=_receipt(ready=True))
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    first = await wiring.prepare_close()
    command = wiring._close_command
    second = await wiring.prepare_close()

    assert first is second
    assert wiring._close_command is command
    assert runtime.calls == 1  # 已就绪回执不重复启动关闭链
    assert service.aclose_calls == 2


async def test_prepare_close_retries_unclosed_handle_then_releases_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", _DirectPool)
    service = _FakeAgentService()
    handle = _CloseableHandle()
    guard = _CountingGuard()
    wiring = AgentWiring(
        service=service,
        bridge=OwnerLoopBridge.for_running_loop(),
        unclosed_handle=cast(Any, handle),
        instance_guard=guard,
    )

    assert await wiring.prepare_close() is None
    assert handle.close_calls == 1
    assert guard.released == 1
    assert wiring.unclosed_handle is None
    assert wiring.instance_guard is None
    assert service.aclose_calls == 1


async def test_close_unclosed_handle_without_handle_is_noop() -> None:
    wiring = AgentWiring(service=_FakeAgentService(), bridge=OwnerLoopBridge.for_running_loop())

    await wiring._close_unclosed_handle()

    assert wiring.unclosed_handle is None


async def test_close_unclosed_handle_failure_keeps_handle_and_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", _DirectPool)
    handle = _CloseableHandle(error=RuntimeError("close failed"))
    guard = _CountingGuard()
    wiring = AgentWiring(
        service=_FakeAgentService(),
        bridge=OwnerLoopBridge.for_running_loop(),
        unclosed_handle=cast(Any, handle),
        instance_guard=guard,
    )

    await wiring._close_unclosed_handle()

    assert wiring.unclosed_handle is handle
    assert guard.released == 0


async def test_close_unclosed_handle_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", _DirectPool)
    handle = _CloseableHandle(error=asyncio.CancelledError())
    wiring = AgentWiring(
        service=_FakeAgentService(),
        bridge=OwnerLoopBridge.for_running_loop(),
        unclosed_handle=cast(Any, handle),
    )

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await wiring._close_unclosed_handle()

    assert isinstance(excinfo.value, asyncio.CancelledError)


# ---------------------------------------------------------------------------
# 接线失败降级（零派发）
# ---------------------------------------------------------------------------


async def test_missing_shared_ledger_reports_failure() -> None:
    wiring = await wire_agent_runtime(SimpleNamespace())

    assert wiring.service is None
    assert wiring.failure == "shared_ledger_unavailable"
    assert wiring.ready is False


async def test_unresolvable_runtime_url_degrades_zero_dispatch() -> None:
    cache = SimpleNamespace(agent_dao=_FakeRegistry(), engine=SimpleNamespace(url=object()))

    wiring = await wire_agent_runtime(cache)

    assert wiring.failure == "runtime_url_unresolved"
    assert isinstance(wiring.service, AgentService)
    assert wiring.runtime is None


async def test_startup_exception_degrades_zero_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("startup boom")

    monkeypatch.setattr(wiring_mod, "start_agent_runtime", _boom)

    wiring = await wire_agent_runtime(_cache_manager())

    assert wiring.failure == "startup_failed:RuntimeError"
    assert wiring.handle is None
    assert wiring.runtime is None


async def test_startup_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _cancel(*_args: Any, **_kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(wiring_mod, "start_agent_runtime", _cancel)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await wire_agent_runtime(_cache_manager())

    assert isinstance(excinfo.value, asyncio.CancelledError)


async def test_startup_outcome_failure_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.agent_service.recovery import StartupOutcome

    async def _outcome(*_args: Any, **_kwargs: Any) -> StartupOutcome:
        return StartupOutcome(handle=None, isolation=None, failure="isolation_not_ready:x")

    monkeypatch.setattr(wiring_mod, "start_agent_runtime", _outcome)

    wiring = await wire_agent_runtime(_cache_manager())

    assert wiring.failure == "isolation_not_ready:x"
    assert wiring.ready is False


# ---------------------------------------------------------------------------
# 迁移/运行凭据分离（R2-I38）
# ---------------------------------------------------------------------------


async def test_application_version_unavailable_degrades_zero_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """③：发行版元数据不可得时不回落共享 dev，必须 fail-closed 说明。"""
    monkeypatch.setattr(wiring_mod, "_application_version", lambda: None)
    assembled: list[Any] = []

    wiring = await wire_agent_runtime(
        _cache_manager(),
        isolation_id="startup-test",
        assemble_func=assembled.append,
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
    )

    assert wiring.failure == "application_version_unavailable"
    assert wiring.handle is None
    assert wiring.runtime is None
    assert assembled == []


def test_application_version_missing_or_blank_metadata_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """元数据缺失/空版本都返回 None（不回落共享 dev）。"""

    def _missing(_name: str) -> str:
        raise PackageNotFoundError("astock-screener")

    monkeypatch.setattr(wiring_mod, "version", _missing)
    assert wiring_mod._application_version() is None

    monkeypatch.setattr(wiring_mod, "version", lambda _name: "   ")
    assert wiring_mod._application_version() is None


async def test_external_mode_without_migration_credentials_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外置库未配置独立迁移凭据：启动门闩明确拒绝，零组装/零派发。"""
    monkeypatch.setattr(wiring_mod, "_embedded_single_user_mode", lambda: False)
    assembled: list[Any] = []

    wiring = await wire_agent_runtime(
        _cache_manager(),
        isolation_id="startup-test",
        assemble_func=assembled.append,
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
    )

    assert wiring.failure == "migration_credentials_missing"
    assert wiring.handle is None
    assert wiring.runtime is None
    assert assembled == []


async def test_dedicated_migration_url_wired_into_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置了独立迁移 URL：原样传入 settings，且不打开共用开关。"""
    migration_url = "postgresql+psycopg://agent_migrator@localhost/qtrading_agent_runtime"
    captured: list[Any] = []
    monkeypatch.setattr(wiring_mod, "get_agent_migration_db_url", lambda: migration_url)

    def _assemble(settings: Any) -> _FakeHandle:
        captured.append(settings)
        return _FakeHandle(client=_FakeClient())

    wiring = await wire_agent_runtime(
        _cache_manager(),
        isolation_id="startup-test",
        assemble_func=_assemble,
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
        launch_allowed=False,
    )

    assert wiring.handle is not None
    assert captured[0].migration_database_url == migration_url
    assert captured[0].allow_shared_migration_credentials is False
    assert captured[0].system_database_url != captured[0].migration_database_url


async def test_embedded_mode_missing_url_opts_in_to_shared_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内置单用户模式未配置迁移凭据：显式打开共用开关（由服务层告警），不静默。"""
    captured: list[Any] = []
    monkeypatch.setattr(wiring_mod, "_embedded_single_user_mode", lambda: True)

    def _assemble(settings: Any) -> _FakeHandle:
        captured.append(settings)
        return _FakeHandle(client=_FakeClient())

    await wire_agent_runtime(
        _cache_manager(),
        isolation_id="startup-test",
        assemble_func=_assemble,
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
        launch_allowed=False,
    )

    assert captured[0].migration_database_url is None
    assert captured[0].allow_shared_migration_credentials is True


# ---------------------------------------------------------------------------
# P1-06：已 launch 宿主的关闭/切库门必须基于真实在途事实收敛
# ---------------------------------------------------------------------------


class _StatusClient:
    """原生控制面替身：按 ID 返回状态；``status`` 过滤返回在途集合。"""

    def __init__(
        self,
        *,
        active: list[tuple[str, str]] | None = None,
        by_id: dict[str, str] | None = None,
        error: BaseException | None = None,
        error_after: int = 0,
        delay: float = 0.0,
    ) -> None:
        self._active = active or []
        self._by_id = by_id or {}
        self._error = error
        self._error_after = error_after
        self._delay = delay
        self.list_calls = 0

    async def list_workflows_async(
        self,
        *,
        workflow_ids: list[str] | None = None,
        status: Any = None,
        **_kwargs: Any,
    ) -> list[Any]:
        self.list_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None and self.list_calls > self._error_after:
            raise self._error
        if workflow_ids is not None:
            return [
                SimpleNamespace(workflow_id=wid, status=self._by_id[wid], parent_workflow_id=None)
                for wid in workflow_ids
                if wid in self._by_id
            ]
        if status is not None:
            return [
                SimpleNamespace(workflow_id=wid, status=state, parent_workflow_id=None) for wid, state in self._active
            ]
        return []

    async def cancel_workflows_async(self, workflow_ids: list[str], *, cancel_children: bool = False) -> None:
        # 模拟 DBOS 真实语义：取消把非 SUCCESS/ERROR 行改写为 CANCELLED（行仍在）。
        for workflow_id in workflow_ids:
            if self._by_id.get(workflow_id) not in {"SUCCESS", "ERROR"}:
                self._by_id[workflow_id] = "CANCELLED"

    async def resume_workflows_async(self, workflow_ids: list[str]) -> None:
        return None

    def delete_workflows(self, workflow_ids: list[str], *, delete_children: bool = False) -> None:
        return None


class _RecordingDao(_FakeRegistry):
    """记录持久派发关闭事实的登记面替身。"""

    def __init__(self, *, error: BaseException | None = None) -> None:
        super().__init__()
        self._error = error
        self.close_requests: list[Any] = []

    async def mark_dispatch_closed(self, request: Any) -> DispatchCloseFact:
        self.close_requests.append(request)
        if self._error is not None and request.workflow_ids:
            raise self._error
        return DispatchCloseFact(user_scope=request.user_scope, closed_execution_ids=())


def _ledger(client: Any, *, timeout_s: float = 1.0) -> _LaunchedWorkflowLedger:
    return _LaunchedWorkflowLedger(client=client, application_version="1.0.0", timeout_s=timeout_s)


def _command() -> CloseCommandIdentity:
    return CloseCommandIdentity(user_scope="local-desktop", close_command_id="close-p1-06")


async def _wire_launched(
    *,
    client: _StatusClient,
    dao: _FakeRegistry | None = None,
) -> tuple[AgentWiring, _FakeHandle]:
    fake_handle = _FakeHandle(client=cast(Any, client))

    def _launch(handle: Any) -> None:
        handle._launched = True

    cache = SimpleNamespace(
        agent_dao=dao or _FakeRegistry(),
        engine=SimpleNamespace(url=sa.make_url("postgresql+asyncpg://u:p@localhost:5432/qtrading")),
    )
    wiring = await wire_agent_runtime(
        cache,
        isolation_id="startup-test",
        assemble_func=lambda _settings: fake_handle,
        instance_guard=_FakeGuard(),
        blocking_runner=_direct_runner,
        launch_allowed=True,
        launch_func=_launch,
    )
    assert wiring.launched is True
    return wiring, fake_handle


async def test_launched_idle_host_can_close_after_native_verification() -> None:
    """已启动空宿主（无在途 workflow）必须可关/切库，而非恒被常量阻断。"""
    dao = _RecordingDao()
    wiring, handle = await _wire_launched(client=_StatusClient(), dao=dao)

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is True
    assert receipt.residual_io == ()
    assert handle.closed is True
    assert wiring.close_ready() is True
    assert [request for request in dao.close_requests if request.workflow_ids] == []  # 无 workflow 可落，不伪造写入


async def test_launched_host_with_active_workflow_still_blocks_close() -> None:
    """真实仍在途的 workflow（含叶）继续阻断关闭，给出精确原因与计数。"""
    dao = _RecordingDao()
    # 启动隔离按当前业务库登记归属解析取消集（N06）：在途行须先登记才算本库归属
    dao.recovery = (
        SimpleNamespace(workflow_id="wf-active"),
        SimpleNamespace(workflow_id="leaf-active"),
    )
    wiring, handle = await _wire_launched(
        client=_StatusClient(active=[("wf-active", "PENDING"), ("leaf-active", "ENQUEUED")]),
        dao=dao,
    )

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is False
    assert "residual_io_unsettled:launched_workflows_in_flight:2" in receipt.blocking_reasons
    assert handle.closed is False
    assert wiring.close_ready() is False


async def test_launched_workflow_ledger_settles_terminal_workflows() -> None:
    client = _StatusClient(by_id={"wf-1": "SUCCESS"})
    ledger = _ledger(client)
    ledger.track("wf-1")

    await ledger.verify()

    assert ledger.residual_names() == ()
    assert ledger.unresolved_ids() == ()


async def test_launched_workflow_ledger_reports_in_flight_count() -> None:
    client = _StatusClient(by_id={"wf-1": "ENQUEUED", "wf-2": "SUCCESS"})
    ledger = _ledger(client)
    ledger.track("wf-1")
    ledger.track("wf-2")

    await ledger.verify()

    assert ledger.residual_names() == ("launched_workflows_in_flight:1",)
    assert ledger.unresolved_ids() == ("wf-1",)


async def test_launched_workflow_ledger_marks_missing_native_row_unsettled() -> None:
    """已投递但无原生行：无终态证据，按未定项阻断（不伪造成已结束）。"""
    ledger = _ledger(_StatusClient(by_id={}))
    ledger.track("wf-missing")

    await ledger.verify()

    assert ledger.residual_names() == ("launched_workflows_unsettled:1",)
    assert ledger.unresolved_ids() == ("wf-missing",)


async def test_launched_workflow_ledger_query_failure_blocks_with_reason() -> None:
    ledger = _ledger(_StatusClient(error=RuntimeError("native unavailable")))
    ledger.track("wf-1")

    await ledger.verify()

    assert ledger.residual_names() == ("launched_workflows_query_failed:RuntimeError",)
    assert ledger.unresolved_ids() == ("wf-1",)


async def test_launched_workflow_ledger_query_timeout_blocks_with_reason() -> None:
    ledger = _ledger(_StatusClient(delay=0.05), timeout_s=0.01)
    ledger.track("wf-1")

    await ledger.verify()

    assert ledger.residual_names() == ("launched_workflows_query_failed:TimeoutError",)
    assert ledger.unresolved_ids() == ("wf-1",)


async def test_launched_workflow_ledger_tracks_workflows_added_after_verification() -> None:
    """关闭中再投递：核对之后才出现的 workflow 无终态证据，不得被放行。"""
    ledger = _ledger(_StatusClient())
    await ledger.verify()
    assert ledger.residual_names() == ()

    ledger.track("wf-late")

    assert ledger.residual_names() == ("launched_workflows_unsettled:1",)
    assert ledger.unresolved_ids() == ("wf-late",)


async def test_launched_close_fact_sink_persists_dispatch_close_for_settled_workflows() -> None:
    dao = _RecordingDao()
    ledger = _ledger(_StatusClient(by_id={"wf-1": "SUCCESS", "wf-2": "CANCELLED"}))
    ledger.track("wf-1")
    ledger.track("wf-2")
    sink = _LaunchedCloseFactSink(ledger=ledger, dao=dao, user_scope="local-desktop")

    outcome = await sink.save_end_or_unknown(_command())

    assert outcome.saved is True
    assert [tuple(item.workflow_ids) for item in dao.close_requests] == [("wf-1", "wf-2")]


async def test_launched_close_fact_sink_reports_unresolved_as_unknown() -> None:
    dao = _RecordingDao()
    ledger = _ledger(_StatusClient(by_id={"wf-1": "DELAYED"}))
    ledger.track("wf-1")
    sink = _LaunchedCloseFactSink(ledger=ledger, dao=dao, user_scope="local-desktop")

    outcome = await sink.save_end_or_unknown(_command())

    assert outcome.saved is False
    assert outcome.unknown_associations == ("wf-1",)
    assert dao.close_requests == []  # 未核清即不得落「已结束」事实


async def test_launched_close_fact_sink_propagates_persistence_failure() -> None:
    dao = _RecordingDao(error=RuntimeError("db down"))
    ledger = _ledger(_StatusClient(by_id={"wf-1": "SUCCESS"}))
    ledger.track("wf-1")
    sink = _LaunchedCloseFactSink(ledger=ledger, dao=dao, user_scope="local-desktop")

    with pytest.raises(RuntimeError, match="db down"):
        await sink.save_end_or_unknown(_command())

    assert [tuple(item.workflow_ids) for item in dao.close_requests] == [("wf-1",)]


async def test_launched_host_close_persistence_failure_keeps_gate_blocked() -> None:
    """落库失败：关闭门继续阻断并保活宿主（不取消既有防护）。"""
    dao = _RecordingDao(error=RuntimeError("db down"))
    wiring, handle = await _wire_launched(client=_StatusClient(by_id={"wf-1": "SUCCESS"}), dao=dao)
    ledger = wiring.launched_ledger
    assert ledger is not None
    ledger.track("wf-1")

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is False
    assert "close_facts_save_failed" in receipt.blocking_reasons
    assert handle.closed is False
    assert wiring.close_ready() is False


async def test_launched_close_propagates_cancellation_from_verification() -> None:
    """原生核对被取消：``CancelledError`` 原样传播（R2），不转成阻塞码。"""
    wiring, handle = await _wire_launched(client=_StatusClient(error=asyncio.CancelledError(), error_after=1))

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await wiring.prepare_close()

    assert isinstance(exc_info.value, asyncio.CancelledError)
    assert handle.closed is False
    assert wiring.close_ready() is False


async def test_dispatch_stays_closed_after_launched_host_close() -> None:
    """关闭中/关闭后新提交零派发（门闩先于残余核对关闭）。"""
    wiring, _handle = await _wire_launched(client=_StatusClient())
    assert wiring.dispatcher is not None

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is True
    submission = SubmissionReceipt(
        command=CommandRef(
            user_scope="local-desktop",
            source_module="test",
            business_request_id="req-1",
            command_id="cmd-1",
            expected_revision=0,
        ),
        command_record_id="cmd-rec-1",
        session_id="session-1",
        execution_id="exec-1",
        workflow_id="wf-new",
        intent_digest="0" * 64,
        commit_state=CommitState.COMMITTED,
        verified_at="2026-11-23T00:00:00.000000Z",
        next_action=None,
    )

    outcome = await wiring.dispatcher.dispatch(submission, user_scope="local-desktop")

    assert outcome.submitted is False
    assert outcome.reason == DISPATCH_CLOSED


class _RecordingSendClient:
    """答复消息投递替身：记录 send_async 的目标/消息/幂等键。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def send_async(
        self,
        destination_id: str,
        message: Any,
        *,
        topic: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "destination_id": destination_id,
                "message": message,
                "topic": topic,
                "idempotency_key": idempotency_key,
            }
        )
        if self.error is not None:
            raise self.error


def _answer_fact(*, workflow_id: str = "wf-1", payload: str | None = None) -> AnswerCommandFact:
    return AnswerCommandFact(
        command_record_id="cmd-1",
        client_command_id="ui-answer:d-1",
        decision_id="d-1",
        session_id="s1",
        execution_id="exec-1",
        round_id="round-1",
        workflow_id=workflow_id,
        delivery_state=DeliveryState.NOT_DELIVERED,
        consumption_state=ConsumptionState.NOT_CONSUMED,
        answer_digest="sha256:abc",
        request_payload=payload if payload is not None else json_doc({"text": "选择第一家"}),
        verified_at=None,
    )


class TestNativeAnswerSender:
    """生产答复发送器（P1-05）：复用 DBOS client 投递，门闩关闭即拒绝。"""

    @pytest.mark.asyncio
    async def test_sends_answer_message_with_idempotent_command_key(self) -> None:
        client = _RecordingSendClient()
        sender = _NativeAnswerSender(
            client=client,
            gate=_ProcessDispatchGate(launched=True),
            port_io=wiring_mod._InFlightPortIo(),
        )

        await sender.send_answer(_answer_fact())

        assert [call["destination_id"] for call in client.calls] == ["wf-1"]
        call = client.calls[0]
        assert call["destination_id"] == "wf-1"
        assert call["topic"] == "answer"
        assert call["idempotency_key"] == "ui-answer:d-1"
        assert call["message"]["decision_id"] == "d-1"
        assert call["message"]["client_command_id"] == "ui-answer:d-1"
        assert call["message"]["answer"] == {"text": "选择第一家"}

    @pytest.mark.asyncio
    async def test_closed_dispatch_gate_refuses_before_native_send(self) -> None:
        client = _RecordingSendClient()
        sender = _NativeAnswerSender(
            client=client,
            gate=_ProcessDispatchGate(launched=False),
            port_io=wiring_mod._InFlightPortIo(),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await sender.send_answer(_answer_fact())

        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_native_send_failure_propagates_without_port_residue(self) -> None:
        client = _RecordingSendClient(error=RuntimeError("native send failed"))
        port_io = wiring_mod._InFlightPortIo()
        sender = _NativeAnswerSender(
            client=client,
            gate=_ProcessDispatchGate(launched=True),
            port_io=port_io,
        )

        with pytest.raises(RuntimeError) as excinfo:
            await sender.send_answer(_answer_fact())

        assert str(excinfo.value) == "native send failed"
        assert [call["destination_id"] for call in client.calls] == ["wf-1"]
        assert port_io.residual_names() == ()
