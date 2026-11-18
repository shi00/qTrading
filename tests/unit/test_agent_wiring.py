"""生产组合根 Agent 接线单测（P01；DD §7.4/§10.6）。

覆盖：facade 复用共享引擎/账本、launch 门闩（needs-spike 前零派发）、隔离失败
零 launch、未就绪不构造关闭链、关闭前置先于共享引擎释放。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa

from app import agent_wiring as wiring_mod
from app.agent_wiring import (
    AgentWiring,
    _PendingCloseFactSink,
    _ProcessDispatchGate,
    wire_agent_runtime,
)
from core.agent_types import DispatchCloseFact, stable_id
from services.agent_service import AgentService, CloseReadinessReceipt
from services.agent_service.recovery import InstanceGuardEvidence
from services.agent_service.runtime import OwnerLoopBridge

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_migration_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离迁移凭据来源：单测不读真实环境变量/keyring/用户配置（否则结果随开发机变化）。"""
    monkeypatch.setattr(wiring_mod, "get_agent_migration_db_url", lambda: None)
    monkeypatch.setattr(wiring_mod, "_embedded_single_user_mode", lambda: True)


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


class _FakeHandle:
    def __init__(self, *, client: _FakeClient) -> None:
        self.client = client
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
    """应用登记面替身（AgentDao 最小恢复面）。"""

    def __init__(self) -> None:
        self.finalized: list[str] = []

    async def list_recovery_executions(self, user_scope: str) -> tuple[Any, ...]:
        return ()

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
    assert launched == []  # needs-spike 门闩：不提前 launch


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
    assert service.aclose_calls == 1


async def test_close_prelude_failure_recorded_without_masking_shutdown() -> None:
    service = _FakeAgentService()
    runtime = _FakeRuntime(error=RuntimeError("prelude boom"))
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    assert await wiring.prepare_close() is None
    assert wiring._receipt is None
    assert service.aclose_calls == 1


async def test_close_prelude_not_ready_receipt_keeps_blocking_reasons() -> None:
    service = _FakeAgentService()
    runtime = _FakeRuntime(receipt=_receipt(ready=False))
    wiring = AgentWiring(service=service, bridge=OwnerLoopBridge.for_running_loop(), runtime=cast(Any, runtime))

    receipt = await wiring.prepare_close()

    assert receipt is not None and receipt.ready is False
    assert receipt.blocking_reasons == ("io_in_flight",)
    assert wiring._receipt is receipt


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
    migration_url = "postgresql+psycopg://agent_migrator@migrator-host/qtrading_agent_runtime"
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
