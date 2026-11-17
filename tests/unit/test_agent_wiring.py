"""生产组合根 Agent 接线单测（P01；DD §7.4/§10.6）。

覆盖：facade 复用共享引擎/账本、launch 门闩（needs-spike 前零派发）、隔离失败
零 launch、未就绪不构造关闭链、关闭前置先于共享引擎释放。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

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
from services.agent_service import AgentService
from services.agent_service.recovery import InstanceGuardEvidence
from services.agent_service.runtime import OwnerLoopBridge

pytestmark = pytest.mark.unit


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
