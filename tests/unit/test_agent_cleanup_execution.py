"""E-06-3b 清理编排单元测试（替代端口，不连数据库）。

覆盖：确认→预检→删除→tombstone→verified 顺序与参数；缺失原生目标不删除；
逐域非 0 进入 retry_pending；tombstone 失败保留原集合并提取 SQLSTATE；
已 verified 零副作用；retry_pending 复用原集合；空原生集合按九域可核零验证。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    CLEANUP_NATIVE_TARGETS,
    AgentErrorCode,
    AgentServiceError,
    CleanupJobRecord,
    CleanupPhase,
    CleanupPrecondition,
)
from services.agent_service.cleanup import (
    CleanupConfirmCommand,
    CleanupService,
    preview_digest,
)
from services.agent_service.cleanup_execution import CleanupExecutionService
from services.agent_service.cleanup_native import NativeCleanupResult, NativePreflight

# 与生产 ``AgentDao.load_cleanup_job`` 一致：读取即规范化（排序/去重）。
_IDS = ("wf-a", "wf-b")
_SESSIONS = ("session-1", "session-2")
_ASSETS = (("asset-a", "1"), ("asset-b", "2"))


def _zeros() -> dict[str, int]:
    return {table: 0 for table, _key in CLEANUP_NATIVE_TARGETS}


def _record(**overrides: object) -> CleanupJobRecord:
    values: dict[str, object] = {
        "user_scope": "owner-1",
        "cleanup_job_id": "job-1",
        "owner_ref": "session-owner",
        "preview_digest": preview_digest(("session-owner",), _IDS),
        "expected_revision": 2,
        "exact_ids": _IDS,
        "phase": CleanupPhase.PREPARED,
        "fence_committed": False,
        "session_ids": _SESSIONS,
        "asset_keys": _ASSETS,
    }
    values.update(overrides)
    return CleanupJobRecord(**values)  # type: ignore[arg-type]  # 测试受控覆盖


def _precondition(**overrides: bool) -> CleanupPrecondition:
    values: dict[str, bool] = {
        "scope_settled": True,
        "writers_exited": True,
        "closure_known": True,
        "preview_valid": True,
        "new_reference_conflict": False,
    }
    values.update(overrides)
    return CleanupPrecondition(**values)  # type: ignore[arg-type]  # 测试受控覆盖


class _Store:
    def __init__(self, record: CleanupJobRecord) -> None:
        self.record = record
        self.fences: list[CleanupJobRecord] = []

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        if (self.record.user_scope, self.record.cleanup_job_id) != (user_scope, cleanup_job_id):
            return None
        return self.record

    async def commit_fence(self, record: CleanupJobRecord) -> None:
        self.fences.append(record)
        self.record = record


class _Dao:
    def __init__(
        self,
        *,
        tombstone_error: Exception | None = None,
        fail_categories: frozenset[str] = frozenset(),
    ) -> None:
        self.phases: list[dict[str, object]] = []
        self.tombstones: list[tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] = []
        self._tombstone_error = tombstone_error
        self._fail_categories = fail_categories

    async def save_cleanup_phase(
        self,
        user_scope: str,
        cleanup_job_id: str,
        *,
        phase: CleanupPhase,
        verified_counts: dict[str, int] | None = None,
        complete: bool = False,
        error_category: str | None = None,
        sqlstate: str | None = None,
    ) -> None:
        if error_category in self._fail_categories:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"phase write rejected: {error_category}",
                f"{user_scope}/{cleanup_job_id}",
            )
        self.phases.append(
            {
                "user_scope": user_scope,
                "cleanup_job_id": cleanup_job_id,
                "phase": phase,
                "verified_counts": verified_counts,
                "complete": complete,
                "error_category": error_category,
                "sqlstate": sqlstate,
            }
        )

    async def tombstone_cleanup_text(
        self,
        user_scope: str,
        *,
        session_ids: tuple[str, ...] = (),
        asset_keys: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, int]:
        self.tombstones.append((user_scope, tuple(session_ids), tuple(asset_keys)))
        if self._tombstone_error is not None:
            raise self._tombstone_error
        return {"agent_session": len(session_ids), "agent_asset_version": len(asset_keys)}


class _Executor:
    def __init__(
        self,
        *,
        counts: dict[str, int] | None = None,
        missing: tuple[str, ...] = (),
        error: Exception | None = None,
        preflight_error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._counts = _zeros() if counts is None else counts
        self._missing = missing
        self._error = error
        self._preflight_error = preflight_error

    async def preflight(self, ids) -> NativePreflight:
        self.calls.append(("preflight", tuple(ids)))
        if self._preflight_error is not None:
            raise self._preflight_error
        return NativePreflight(tuple(ids), dict(self._counts), self._missing)

    async def delete(self, ids) -> NativeCleanupResult:
        self.calls.append(("delete", tuple(ids)))
        if self._error is not None:
            raise self._error
        return NativeCleanupResult(tuple(ids), dict(self._counts))


def _service(
    record: CleanupJobRecord,
    *,
    executor: _Executor | None = None,
    dao: _Dao | None = None,
) -> tuple[CleanupExecutionService, _Store, _Dao, _Executor]:
    store = _Store(record)
    dao = dao or _Dao()
    executor = executor or _Executor()
    service = CleanupExecutionService(jobs=CleanupService(store), store=store, dao=dao, executor=executor)
    return service, store, dao, executor


def _command(record: CleanupJobRecord) -> CleanupConfirmCommand:
    return CleanupConfirmCommand(
        user_scope=record.user_scope,
        cleanup_job_id=record.cleanup_job_id,
        command_id="cmd-1",
        preview_digest=record.preview_digest,
        expected_revision=record.expected_revision,
        confirmed=True,
    )


class TestCleanupExecutionHappyPath:
    @pytest.mark.asyncio
    async def test_confirms_deletes_tombstones_then_verifies(self) -> None:
        record = _record()
        service, store, dao, executor = _service(record)

        receipt = await service.confirm_and_execute(_command(record), _precondition())

        assert [call[0] for call in executor.calls] == ["preflight", "delete"]
        assert executor.calls[1][1] == ("wf-a", "wf-b")
        assert len(store.fences) == 1
        assert store.fences[0].fence_committed is True
        assert dao.tombstones == [("owner-1", ("session-1", "session-2"), (("asset-a", "1"), ("asset-b", "2")))]
        assert len(dao.phases) == 1
        assert dao.phases[0]["phase"] is CleanupPhase.VERIFIED
        assert dao.phases[0]["complete"] is True
        assert dao.phases[0]["verified_counts"] == _zeros()
        assert receipt.phase is CleanupPhase.VERIFIED
        assert receipt.verified_counts == _zeros()
        assert receipt.tombstoned == {"agent_session": 2, "agent_asset_version": 2}

    @pytest.mark.asyncio
    async def test_empty_native_set_verifies_with_nine_zero_domains(self) -> None:
        record = _record(exact_ids=())
        service, _, dao, executor = _service(record)

        receipt = await service.confirm_and_execute(_command(record), _precondition())

        assert executor.calls[0][1] == ()
        assert dao.phases[0]["phase"] is CleanupPhase.VERIFIED
        assert set(receipt.verified_counts.values()) == {0}
        assert len(receipt.verified_counts) == 9


class TestCleanupExecutionFailures:
    @pytest.mark.asyncio
    async def test_missing_native_target_never_deletes(self) -> None:
        record = _record()
        executor = _Executor(missing=("wf-a",))
        service, _, dao, executor = _service(record, executor=executor)

        with pytest.raises(AgentServiceError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert [call[0] for call in executor.calls] == ["preflight"]
        assert dao.tombstones == []
        assert dao.phases[0]["phase"] is CleanupPhase.RETRY_PENDING
        assert dao.phases[0]["error_category"] == "native_target_missing"

    @pytest.mark.asyncio
    async def test_retry_after_prepared_allows_missing_native_rows(self) -> None:
        """§10.7：已有可靠 prepared 且 fence 持续的重试允许原生行已缺失。"""

        record = _record(
            phase=CleanupPhase.RETRY_PENDING,
            fence_committed=True,
            prior_error_category="cleanup_execution_failed",
        )
        executor = _Executor(missing=("wf-a",))
        service, _, dao, executor = _service(record, executor=executor)

        receipt = await service.confirm_and_execute(_command(record), _precondition())

        assert [call[0] for call in executor.calls] == ["preflight", "delete"]
        assert receipt.phase is CleanupPhase.VERIFIED
        assert dao.phases[-1]["phase"] is CleanupPhase.VERIFIED
        assert dao.tombstones[0][0] == "owner-1"

    @pytest.mark.asyncio
    async def test_retry_after_preflight_missing_still_refuses(self) -> None:
        """§10.7 反例：上次失败在预检阶段（从未删过）时，重试缺失仍不得放行。"""

        record = _record(
            phase=CleanupPhase.RETRY_PENDING,
            fence_committed=True,
            prior_error_category="native_target_missing",
        )
        executor = _Executor(missing=("wf-a",))
        service, _, dao, _ = _service(record, executor=executor)

        with pytest.raises(AgentServiceError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert [call[0] for call in executor.calls] == ["preflight"]
        assert dao.phases[0]["error_category"] == "native_target_missing"

    @pytest.mark.asyncio
    async def test_first_missing_then_second_confirm_still_refuses(self) -> None:
        """端到端：首次缺失 → 落 retry_pending(native_target_missing) → 二次确认仍拒绝。"""

        first = _record(phase=CleanupPhase.PREPARED, fence_committed=False)
        service, _, dao, _ = _service(first, executor=_Executor(missing=("wf-a",)))
        with pytest.raises(AgentServiceError) as first_exc:
            await service.confirm_and_execute(_command(first), _precondition())
        assert first_exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        recorded = dao.phases[0]["error_category"]
        assert recorded == "native_target_missing"

        again = _record(
            phase=CleanupPhase.RETRY_PENDING,
            fence_committed=True,
            prior_error_category=recorded,
        )
        retry_executor = _Executor(missing=("wf-a",))
        service2, _, dao2, _ = _service(again, executor=retry_executor)
        with pytest.raises(AgentServiceError) as retry_exc:
            await service2.confirm_and_execute(_command(again), _precondition())
        assert retry_exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

        assert [call[0] for call in retry_executor.calls] == ["preflight"]
        assert dao2.phases[0]["error_category"] == "native_target_missing"

    @pytest.mark.asyncio
    async def test_preflight_failure_is_not_recorded_as_execution_failure(self) -> None:
        """预检异常不得记为 cleanup_execution_failed，否则下次缺失会被误放行。"""

        record = _record(phase=CleanupPhase.PREPARED, fence_committed=True)
        executor = _Executor(preflight_error=RuntimeError("preflight boom"))
        service, _, dao, _ = _service(record, executor=executor)

        with pytest.raises(RuntimeError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())
        assert str(exc.value) == "preflight boom"

        assert dao.phases[0]["error_category"] == "cleanup_preflight_failed"
        assert len(dao.phases) == 1
        assert [call[0] for call in executor.calls] == ["preflight"]

    @pytest.mark.asyncio
    async def test_refusal_write_failure_never_leaves_allowing_category(self) -> None:
        """拒绝写入自身失败时，最终也不得留下可触发缺失放行的类别。"""

        record = _record(phase=CleanupPhase.PREPARED, fence_committed=True)
        dao = _Dao(fail_categories=frozenset({"native_target_missing"}))
        service, _, _, executor = _service(record, dao=dao, executor=_Executor(missing=("wf-a",)))

        with pytest.raises(AgentServiceError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert "native_target_missing" in str(exc.value)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        # 拒绝类别自身写不进去：不落任何类别（尤其不落允许缺失放行的类别），不删。
        assert dao.phases == []
        assert [call[0] for call in executor.calls] == ["preflight"]

    @pytest.mark.asyncio
    async def test_missing_without_prior_deletion_failure_still_refuses(self) -> None:
        """未提交 fence 的首次预检缺失仍判失败，不得以缺失冒充已清理。"""

        record = _record(phase=CleanupPhase.PREPARED, fence_committed=False)
        executor = _Executor(missing=("wf-a",))
        service, _, dao, _ = _service(record, executor=executor)

        with pytest.raises(AgentServiceError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert [call[0] for call in executor.calls] == ["preflight"]
        assert dao.phases[0]["error_category"] == "native_target_missing"

    @pytest.mark.asyncio
    async def test_rows_remaining_marks_retry_pending_without_tombstone(self) -> None:
        record = _record()
        counts = _zeros()
        counts["dbos.workflow_status"] = 1
        service, _, dao, _ = _service(record, executor=_Executor(counts=counts))

        receipt = await service.confirm_and_execute(_command(record), _precondition())

        assert receipt.phase is CleanupPhase.RETRY_PENDING
        assert receipt.verified_counts["dbos.workflow_status"] == 1
        assert dao.tombstones == []
        assert dao.phases[0]["phase"] is CleanupPhase.RETRY_PENDING
        assert dao.phases[0]["error_category"] == "native_rows_remaining"

    @pytest.mark.asyncio
    async def test_tombstone_failure_keeps_set_and_records_sqlstate(self) -> None:
        class _DbError(Exception):
            def __init__(self) -> None:
                super().__init__("boom")
                self.sqlstate = "40001"

        record = _record()
        service, store, dao, _ = _service(record, dao=_Dao(tombstone_error=_DbError()))

        with pytest.raises(_DbError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert exc.value.sqlstate == "40001"
        assert len(store.fences) == 1
        assert dao.phases[0]["phase"] is CleanupPhase.RETRY_PENDING
        assert dao.phases[0]["error_category"] == "cleanup_execution_failed"
        assert dao.phases[0]["sqlstate"] == "40001"
        assert [phase["phase"] for phase in dao.phases] == [CleanupPhase.RETRY_PENDING]

    @pytest.mark.asyncio
    async def test_delete_exception_marks_retry_pending_and_reraises(self) -> None:
        record = _record()
        service, _, dao, _ = _service(record, executor=_Executor(error=ConnectionError("lost")))

        with pytest.raises(ConnectionError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert str(exc.value) == "lost"
        assert dao.phases[0]["phase"] is CleanupPhase.RETRY_PENDING
        assert dao.phases[0]["error_category"] == "cleanup_execution_failed"

    @pytest.mark.asyncio
    async def test_concurrent_verified_keeps_original_error_when_retry_write_rejected(self) -> None:
        """并发方已完成清理：阶段写入被拒不得掩盖原始失败，也不得回退 verified。"""

        record = _record()
        store = _Store(record)

        class _VerifyingExecutor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[str, ...]]] = []

            async def preflight(self, ids) -> NativePreflight:
                self.calls.append(("preflight", tuple(ids)))
                return NativePreflight(tuple(ids), dict(_zeros()), ())

            async def delete(self, ids) -> NativeCleanupResult:
                self.calls.append(("delete", tuple(ids)))
                store.record = _record(phase=CleanupPhase.VERIFIED, fence_committed=True)
                raise ConnectionError("lost during delete")

        dao = _Dao(fail_categories=frozenset({"cleanup_execution_failed"}))
        service = CleanupExecutionService(
            jobs=CleanupService(store), store=store, dao=dao, executor=_VerifyingExecutor()
        )

        with pytest.raises(ConnectionError) as exc:
            await service.confirm_and_execute(_command(record), _precondition())

        assert str(exc.value) == "lost during delete"
        assert dao.phases == []
        assert store.record.phase is CleanupPhase.VERIFIED


class TestCleanupExecutionReplays:
    @pytest.mark.asyncio
    async def test_already_verified_has_no_side_effects(self) -> None:
        record = _record(phase=CleanupPhase.VERIFIED, fence_committed=True)
        service, _, dao, executor = _service(record)

        receipt = await service.confirm_and_execute(_command(record), _precondition())

        assert receipt.replayed is True
        assert receipt.phase is CleanupPhase.VERIFIED
        assert executor.calls == []
        assert dao.phases == []
        assert dao.tombstones == []

    @pytest.mark.asyncio
    async def test_retry_pending_reuses_original_frozen_set(self) -> None:
        record = _record(phase=CleanupPhase.RETRY_PENDING, fence_committed=True)
        service, _, dao, executor = _service(record)

        receipt = await service.confirm_and_execute(_command(record), _precondition())

        assert [call[1] for call in executor.calls] == [("wf-a", "wf-b"), ("wf-a", "wf-b")]
        assert dao.tombstones[0][1:] == (("session-1", "session-2"), (("asset-a", "1"), ("asset-b", "2")))
        assert receipt.phase is CleanupPhase.VERIFIED
