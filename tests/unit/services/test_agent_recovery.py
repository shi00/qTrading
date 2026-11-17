"""D-03 启动隔离与手动恢复单元测试（HANDOFF §8.4；设计 §10.6）。

用假控制面/假登记面验证行为契约（无真实 DB/DBOS）：

- 启动隔离：读满分页后才取消（固定集合）、先保存原状态再取消、派发门闩先关、
  任一步失败不得 launch（``ready=False``）、复查仍在途即阻塞；
- 手动恢复：只 resume 被授予的精确集合（A 不动 B），用户终止/终态由 DAO 拒绝
  并进入 ``blocked``，resume 失败不冒充完整恢复；
- R2：``CancelledError`` 穿透（不转成 ready=False 吞掉）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from core.agent_types import (
    DispatchCloseFact,
    DispatchCloseRequest,
    RecoveryExecution,
    RecoveryGrantFact,
    RecoveryGrantRequest,
    StartupIsolationRecord,
)
from services.agent_service.recovery import (
    ABSENT_NATIVE_STATE,
    ISOLATION_PAGE_SIZE,
    STARTUP_WORKFLOW_NAMES,
    resume_selected_workflows,
    run_startup_isolation,
)

pytestmark = pytest.mark.unit

_APP = "qtrading-agent-runtime"
_VERSION = "0.11.7"


@dataclass
class _Native:
    workflow_id: str
    status: str
    queue_name: str | None = None


class _FakeClient:
    """假原生控制面：按分页返回在途集合，记录 cancel/resume。"""

    def __init__(self, active: list[_Native]) -> None:
        self._active = {item.workflow_id: item for item in active}
        self.list_calls: list[dict[str, Any]] = []
        self.cancelled: list[tuple[str, ...]] = []
        self.resumed: list[str] = []
        self.list_error: BaseException | None = None
        self.cancel_error: BaseException | None = None
        self.verify_error: BaseException | None = None
        self.resume_error: BaseException | None = None
        self.cancel_effect = True
        self.list_ignores_offset = False

    async def list_workflows_async(self, **kwargs: Any) -> list[Any]:
        self.list_calls.append(kwargs)
        if "workflow_ids" in kwargs:
            if self.verify_error is not None:
                raise self.verify_error
            ids = kwargs["workflow_ids"]
            return [_Native(workflow_id=item, status=self._active[item].status) for item in ids if item in self._active]
        if self.list_error is not None:
            raise self.list_error
        limit = kwargs.get("limit", ISOLATION_PAGE_SIZE)
        offset = 0 if self.list_ignores_offset else kwargs.get("offset", 0)
        items = sorted(self._active.values(), key=lambda item: item.workflow_id)
        return [_Native(workflow_id=item.workflow_id, status=item.status) for item in items[offset : offset + limit]]

    async def cancel_workflows_async(self, workflow_ids: list[str], *, cancel_children: bool = False) -> None:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(tuple(workflow_ids))
        if self.cancel_effect:
            for workflow_id in workflow_ids:
                if workflow_id in self._active:
                    self._active[workflow_id].status = "CANCELLED"

    async def resume_workflows_async(self, workflow_ids: list[str]) -> Any:
        if self.resume_error is not None:
            raise self.resume_error
        self.resumed.extend(workflow_ids)
        return []


@dataclass
class _FakeRegistry:
    """假应用登记面：记录门闩/保存/回填/授予，可注入失败。"""

    registered: tuple[RecoveryExecution, ...] = ()
    save_error: BaseException | None = None
    fence_error: BaseException | None = None
    finalize_error: BaseException | None = None
    grant: RecoveryGrantFact | None = None
    calls: list[str] = field(default_factory=list)
    saved_records: list[StartupIsolationRecord] = field(default_factory=list)
    finalized: list[tuple[str, str]] = field(default_factory=list)
    fence_requests: list[DispatchCloseRequest] = field(default_factory=list)
    grant_requests: list[RecoveryGrantRequest] = field(default_factory=list)

    async def list_recovery_executions(self, user_scope: str) -> tuple[RecoveryExecution, ...]:
        self.calls.append("list")
        return self.registered

    async def mark_dispatch_closed(self, request: DispatchCloseRequest) -> DispatchCloseFact:
        self.calls.append("fence")
        if self.fence_error is not None:
            raise self.fence_error
        self.fence_requests.append(request)
        return DispatchCloseFact(user_scope=request.user_scope, closed_execution_ids=())

    async def save_startup_isolation(self, record: StartupIsolationRecord) -> str:
        self.calls.append("save")
        if self.save_error is not None:
            raise self.save_error
        self.saved_records.append(record)
        from core.agent_types import stable_id

        return stable_id("command", [record.user_scope, record.target_id, record.client_command_id])

    async def finalize_startup_isolation(
        self, *, user_scope: str, isolation_command_id: str, receipt_payload: str
    ) -> None:
        self.calls.append("finalize")
        if self.finalize_error is not None:
            raise self.finalize_error
        self.finalized.append((isolation_command_id, receipt_payload))

    async def grant_recovery_epoch(self, request: RecoveryGrantRequest) -> RecoveryGrantFact:
        self.calls.append("grant")
        self.grant_requests.append(request)
        assert self.grant is not None
        return self.grant


def _execution(execution_id: str, workflow_id: str | None, **overrides: Any) -> RecoveryExecution:
    values: dict[str, Any] = {
        "execution_id": execution_id,
        "session_id": "session-1",
        "execution_kind": "root",
        "display_state": "running",
        "owner_epoch": 1,
        "dispatch_closed": False,
        "ended_at": None,
        "round_id": "round-1",
        "round_number": 1,
        "workflow_id": workflow_id,
        "run_epoch": 1,
        "stop_reason": None,
    }
    values.update(overrides)
    return RecoveryExecution(**values)


async def _isolate(client: _FakeClient, registry: _FakeRegistry, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "user_scope": "user-1",
        "isolation_id": "startup-batch-1",
        "target_id": _APP,
        "client": client,
        "registry": registry,
        "application_name": _APP,
        "app_version": _VERSION,
    }
    kwargs.update(overrides)
    return await run_startup_isolation(**kwargs)


class TestStartupIsolation:
    async def test_reads_every_page_before_cancel_and_unions_registered(self) -> None:
        active = [_Native(workflow_id=f"agent_leaf:wf-{index:03d}", status="PENDING") for index in range(150)]
        active.append(_Native(workflow_id="agent_research:root-1", status="DELAYED"))
        client = _FakeClient(active)
        registry = _FakeRegistry(registered=(_execution("exec-1", "agent_research:registered-1"),))

        receipt = await _isolate(client, registry)

        assert receipt.ready is True
        assert receipt.blocking_reasons == ()
        # 读满两页才取消（固定集合后再取消）
        assert [call.get("offset") for call in client.list_calls if "workflow_ids" not in call] == [
            0,
            ISOLATION_PAGE_SIZE,
        ]
        assert len(client.cancelled) == 1
        cancelled = client.cancelled[0]
        assert set(cancelled) == {item.workflow_id for item in active} | {"agent_research:registered-1"}
        assert list(cancelled) == sorted(cancelled)
        # 原状态快照包含登记但原生缺失的项（如实标 absent，不伪成功/失败）
        original = dict(receipt.original_states)
        assert original["agent_research:root-1"] == "DELAYED"
        assert original["agent_research:registered-1"] == ABSENT_NATIVE_STATE
        # 先保存再取消、先关门闩
        assert registry.calls[:3] == ["list", "save", "fence"]
        assert registry.saved_records[0].snapshot
        assert registry.fence_requests[0].workflow_ids == ("agent_research:registered-1",)

    async def test_empty_snapshot_is_ready_without_cancel(self) -> None:
        client = _FakeClient([])
        receipt = await _isolate(client, _FakeRegistry())
        assert receipt.ready is True
        assert receipt.isolated_workflow_ids == ()
        assert client.cancelled == []

    async def test_native_listing_failure_blocks_without_cancel(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        client.list_error = RuntimeError("pagination broken")
        receipt = await _isolate(client, _FakeRegistry())
        assert receipt.ready is False
        assert "native_listing_failed" in receipt.blocking_reasons
        assert client.cancelled == []

    async def test_partial_pagination_failure_does_not_cancel(self) -> None:
        """第一页成功、第二页失败：集合不完整即不得取消（避免漏项）。"""

        class _FlakyClient(_FakeClient):
            async def list_workflows_async(self, **kwargs: Any) -> list[Any]:
                if kwargs.get("offset"):
                    raise RuntimeError("second page broken")
                return await super().list_workflows_async(**kwargs)

        client = _FlakyClient([_Native(f"agent_leaf:wf-{i:03d}", "ENQUEUED") for i in range(120)])
        receipt = await _isolate(client, _FakeRegistry())
        assert receipt.ready is False
        assert client.cancelled == []

    async def test_unsaved_isolation_record_blocks_without_cancel(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        registry = _FakeRegistry(save_error=RuntimeError("insert failed"))
        receipt = await _isolate(client, registry)
        assert receipt.ready is False
        assert "isolation_record_unsaved" in receipt.blocking_reasons
        assert client.cancelled == []
        assert registry.calls == ["list", "save"]

    async def test_dispatch_fence_failure_blocks_without_cancel(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        registry = _FakeRegistry(fence_error=RuntimeError("fence failed"))
        receipt = await _isolate(client, registry)
        assert receipt.ready is False
        assert "dispatch_fence_unsaved" in receipt.blocking_reasons
        assert client.cancelled == []
        assert "finalize" in registry.calls

    async def test_cancel_failure_blocks(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        client.cancel_error = RuntimeError("cancel unknown")
        receipt = await _isolate(client, _FakeRegistry())
        assert receipt.ready is False
        assert "cancel_failed" in receipt.blocking_reasons

    async def test_cancel_unverified_blocks(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        client.cancel_effect = False  # 取消未生效（模拟未知/失败静默）
        receipt = await _isolate(client, _FakeRegistry())
        assert receipt.ready is False
        assert receipt.unverified_workflow_ids == ("agent_leaf:a",)
        assert "cancel_unverified" in receipt.blocking_reasons

    async def test_finalize_failure_blocks_launch(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        registry = _FakeRegistry(finalize_error=RuntimeError("update failed"))
        receipt = await _isolate(client, registry)
        assert receipt.ready is False
        assert "isolation_receipt_unsaved" in receipt.blocking_reasons

    async def test_cancelled_error_propagates(self) -> None:
        """R2：取消不吞（不转成 ready=False）。"""

        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        client.cancel_error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await _isolate(client, _FakeRegistry())
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert client.cancelled == []


class TestManualRecovery:
    async def test_resumes_only_granted_ids(self) -> None:
        client = _FakeClient([])
        registry = _FakeRegistry(
            grant=RecoveryGrantFact(
                user_scope="user-1",
                owner_epoch=9,
                run_epoch=9,
                granted_execution_ids=("exec-a",),
                workflow_ids=("agent_research:a",),
                blocked=(("exec-b", "user_termination"),),
            )
        )
        receipt = await resume_selected_workflows(
            user_scope="user-1",
            execution_ids=("exec-a", "exec-b"),
            owner_epoch=9,
            run_epoch=9,
            application_version=_VERSION,
            client=client,
            registry=registry,
        )
        assert receipt.ready is True
        assert client.resumed == ["agent_research:a"]
        assert receipt.blocked == (("exec-b", "user_termination"),)
        assert registry.grant_requests[0].execution_ids == ("exec-a", "exec-b")
        assert registry.grant_requests[0].application_version == _VERSION

    async def test_all_candidates_blocked_is_not_ready(self) -> None:
        client = _FakeClient([])
        registry = _FakeRegistry(
            grant=RecoveryGrantFact(
                user_scope="user-1",
                owner_epoch=9,
                run_epoch=9,
                granted_execution_ids=(),
                workflow_ids=(),
                blocked=(("exec-b", "user_termination"),),
            )
        )
        receipt = await resume_selected_workflows(
            user_scope="user-1",
            execution_ids=("exec-b",),
            owner_epoch=9,
            run_epoch=9,
            application_version=_VERSION,
            client=client,
            registry=registry,
        )
        assert receipt.ready is False
        assert receipt.blocking_reasons == ("no_granted_execution",)
        assert client.resumed == []

    async def test_resume_failure_marks_not_ready(self) -> None:
        client = _FakeClient([])
        client.resume_error = RuntimeError("resume failed")
        registry = _FakeRegistry(
            grant=RecoveryGrantFact(
                user_scope="user-1",
                owner_epoch=9,
                run_epoch=9,
                granted_execution_ids=("exec-a",),
                workflow_ids=("agent_research:a",),
                blocked=(),
            )
        )
        receipt = await resume_selected_workflows(
            user_scope="user-1",
            execution_ids=("exec-a",),
            owner_epoch=9,
            run_epoch=9,
            application_version=_VERSION,
            client=client,
            registry=registry,
        )
        assert receipt.ready is False
        assert receipt.resumed_workflow_ids == ()
        assert receipt.resume_failures == (("agent_research:a", "RuntimeError"),)

    async def test_cancelled_error_propagates_on_resume(self) -> None:
        client = _FakeClient([])
        client.resume_error = asyncio.CancelledError()
        registry = _FakeRegistry(
            grant=RecoveryGrantFact(
                user_scope="user-1",
                owner_epoch=9,
                run_epoch=9,
                granted_execution_ids=("exec-a",),
                workflow_ids=("agent_research:a",),
                blocked=(),
            )
        )
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await resume_selected_workflows(
                user_scope="user-1",
                execution_ids=("exec-a",),
                owner_epoch=9,
                run_epoch=9,
                application_version=_VERSION,
                client=client,
                registry=registry,
            )
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert client.resumed == []


class _FakeHandle:
    def __init__(self, client: _FakeClient) -> None:
        self.client = client
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _FakeGuard:
    def __init__(self, *, acquired: bool = True, exited: bool = True) -> None:
        self._acquired = acquired
        self._exited = exited
        self.released = 0
        self.acquired_calls = 0

    async def acquire(self) -> Any:
        from services.agent_service.recovery import InstanceGuardEvidence

        self.acquired_calls += 1
        return InstanceGuardEvidence(acquired=self._acquired, prior_writer_exited=self._exited, detail="fake")

    def release(self) -> None:
        self.released += 1


async def _direct_blocking(fn: Any) -> Any:
    return fn()


class TestStartupOrchestration:
    """启动编排接线：唯一执行锁 → 组装 → 隔离 → 就绪才 launch。"""

    def _settings(self) -> Any:
        from services.agent_service.runtime import AgentRuntimeSettings

        return AgentRuntimeSettings(
            system_database_url="postgresql+psycopg://u@host/db",
            application_version=_VERSION,
        )

    async def _start(self, **overrides: Any) -> Any:
        from services.agent_service.recovery import start_agent_runtime

        kwargs: dict[str, Any] = {
            "user_scope": "user-1",
            "isolation_id": "startup-1",
            "registry": _FakeRegistry(),
            "instance_guard": _FakeGuard(),
            "blocking_runner": _direct_blocking,
        }
        kwargs.update(overrides)
        return await start_agent_runtime(self._settings(), **kwargs)

    async def test_guard_blocked_stops_before_assemble(self) -> None:
        assemble_calls: list[Any] = []
        guard = _FakeGuard(acquired=False)
        outcome = await self._start(assemble_func=assemble_calls.append, instance_guard=guard)
        assert outcome.handle is None
        assert outcome.failure == "instance_guard_blocked:fake"
        assert assemble_calls == []
        assert guard.released == 1

    async def test_guard_without_prior_exit_evidence_blocks(self) -> None:
        outcome = await self._start(instance_guard=_FakeGuard(exited=False))
        assert outcome.failure == "instance_guard_blocked:fake"

    async def test_assemble_failure_stops_before_isolation(self) -> None:
        launch_calls: list[Any] = []
        guard = _FakeGuard()

        def _assemble(_settings: Any) -> Any:
            raise RuntimeError("dual migration failed")

        outcome = await self._start(assemble_func=_assemble, launch_func=launch_calls.append, instance_guard=guard)
        assert outcome.handle is None
        assert outcome.isolation is None
        assert outcome.failure == "assemble_failed:RuntimeError"
        assert launch_calls == []
        assert guard.released == 1

    async def test_not_ready_isolation_closes_handle_without_launch(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        client.list_error = RuntimeError("listing broken")
        handle = _FakeHandle(client)
        launch_calls: list[Any] = []
        guard = _FakeGuard()

        outcome = await self._start(
            assemble_func=lambda _settings: handle,
            launch_func=launch_calls.append,
            instance_guard=guard,
        )
        assert outcome.handle is None
        assert outcome.isolation is not None and outcome.isolation.ready is False
        assert handle.closed == 1
        assert launch_calls == []
        assert guard.released == 1

    async def test_ready_isolation_launches_once_and_holds_guard(self) -> None:
        handle = _FakeHandle(_FakeClient([]))
        launch_calls: list[Any] = []
        guard = _FakeGuard()

        outcome = await self._start(
            assemble_func=lambda _settings: handle,
            launch_func=launch_calls.append,
            instance_guard=guard,
        )
        assert outcome.handle is handle
        assert outcome.isolation is not None and outcome.isolation.ready is True
        assert outcome.instance_guard is guard
        assert launch_calls == [handle]
        assert guard.released == 0  # 成功后锁须持有到进程关闭

    async def test_launch_failure_closes_handle_and_releases_guard(self) -> None:
        handle = _FakeHandle(_FakeClient([]))
        guard = _FakeGuard()

        def _launch(_handle: Any) -> None:
            raise RuntimeError("launch failed")

        outcome = await self._start(assemble_func=lambda _settings: handle, launch_func=_launch, instance_guard=guard)
        assert outcome.failure == "launch_failed:RuntimeError"
        assert handle.closed == 1
        assert guard.released == 1

    async def test_cancellation_closes_assembled_handle(self) -> None:
        client = _FakeClient([_Native("agent_leaf:a", "PENDING")])
        client.cancel_error = asyncio.CancelledError()
        handle = _FakeHandle(client)
        guard = _FakeGuard()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await self._start(
                assemble_func=lambda _settings: handle,
                launch_func=lambda _handle: None,
                instance_guard=guard,
            )
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert handle.closed == 1
        assert guard.released == 1

    async def test_cancel_during_assemble_waits_worker_exit_then_closes_handle(self) -> None:
        """R06：取消落在组装等待点时，仍在跑的同步组装不得被遗弃（无主宿主）。"""

        from services.agent_service.recovery import start_agent_runtime

        handle = _FakeHandle(_FakeClient([]))
        guard = _FakeGuard()
        launched: list[Any] = []
        assemble_calls: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        def _assemble(_settings: Any) -> Any:
            assemble_calls.append("assembled")
            return handle

        async def _delayed_runner(fn: Any) -> Any:
            started.set()
            await release.wait()
            return fn()

        task = asyncio.create_task(
            start_agent_runtime(
                self._settings(),
                user_scope="user-1",
                isolation_id="startup-1",
                registry=_FakeRegistry(),
                assemble_func=_assemble,
                launch_func=launched.append,
                instance_guard=guard,
                blocking_runner=_delayed_runner,
            )
        )
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        # worker 未真实退出前不得传播取消（否则组装产物成为无主资源）
        assert task.done() is False
        release.set()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert assemble_calls == ["assembled"]
        assert handle.closed == 1
        assert guard.released == 1
        assert launched == []

    async def test_cancel_during_launch_closes_launched_handle(self) -> None:
        """R06：launch 已真实执行完时传播取消前仍须关闭宿主并释放实例锁。"""

        from services.agent_service.recovery import start_agent_runtime

        handle = _FakeHandle(_FakeClient([]))
        guard = _FakeGuard()
        launched: list[Any] = []
        launch_started = asyncio.Event()
        launch_gate = asyncio.Event()
        calls = 0

        async def _delayed_runner(fn: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:  # 第二个同步段是 launch：在此制造取消窗口
                launch_started.set()
                await launch_gate.wait()
            return fn()

        task = asyncio.create_task(
            start_agent_runtime(
                self._settings(),
                user_scope="user-1",
                isolation_id="startup-1",
                registry=_FakeRegistry(),
                assemble_func=lambda _settings: handle,
                launch_func=launched.append,
                instance_guard=guard,
                blocking_runner=_delayed_runner,
            )
        )
        await launch_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        launch_gate.set()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert launched == [handle]
        assert handle.closed == 1
        assert guard.released == 1


class _FakeLockScalar:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _FakeLockConnection:
    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired
        self.closed = 0

    def execute(self, statement: Any, parameters: Any) -> _FakeLockScalar:
        return _FakeLockScalar(self.acquired)

    def close(self) -> None:
        self.closed += 1


class _FakeLockEngine:
    def __init__(self, connection: _FakeLockConnection) -> None:
        self._connection = connection
        self.disposed = 0

    def connect(self) -> _FakeLockConnection:
        return self._connection

    def dispose(self) -> None:
        self.disposed += 1


class TestAdvisoryLockCancellation:
    """R06：拿锁等待被取消时，仍须等同步 worker 退出并释放连接（会话锁随之释放）。"""

    async def test_cancelled_acquire_waits_worker_then_releases_connection(self, monkeypatch: Any) -> None:
        from services.agent_service import recovery

        connection = _FakeLockConnection(acquired=True)
        engine = _FakeLockEngine(connection)

        def _fake_create_engine(*args: Any, **kwargs: Any) -> _FakeLockEngine:
            return engine

        monkeypatch.setattr(recovery.sa, "create_engine", _fake_create_engine)
        started = asyncio.Event()
        release = asyncio.Event()

        async def _delayed_runner(fn: Any) -> Any:
            started.set()
            await release.wait()
            return fn()

        guard = recovery.AdvisoryLockInstanceGuard("postgresql+psycopg://u@host/db", blocking_runner=_delayed_runner)
        task = asyncio.create_task(guard.acquire())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        release.set()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert connection.closed == 1
        assert engine.disposed == 1


def test_startup_workflow_names_match_published_constants() -> None:
    """启动隔离列举名须与 D-02 发布 workflow 名一致（防硬编码漂移）。"""

    from services.agent_service.workflows import LEAF_WORKFLOW_NAME, RESEARCH_WORKFLOW_NAME

    assert set(STARTUP_WORKFLOW_NAMES) == {RESEARCH_WORKFLOW_NAME, LEAF_WORKFLOW_NAME}
