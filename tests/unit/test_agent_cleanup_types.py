"""E-06-1 联合清理契约单元测试（静态可验证部分）。

覆盖：九类原生目标常量；≤200 准确集合（201 拒绝、去重排序、空 ID 拒绝）；
准入谓词（scope/writers/closure/preview/new-reference fence）；
fence 未提交不得删除；逐域查 0 才 verified；NOT NULL 固定 tombstone 不留原文。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    CLEANUP_MAX_BATCH_IDS,
    CLEANUP_NATIVE_TARGETS,
    CLEANUP_TOMBSTONE,
    AgentValidationError,
    CleanupAdmission,
    CleanupPhase,
    CleanupPrecondition,
    CleanupState,
    evaluate_cleanup_admission,
    normalize_cleanup_ids,
    tombstone_text,
)


class TestCleanupTargets:
    def test_nine_native_targets_with_fixed_keys(self) -> None:
        assert len(CLEANUP_NATIVE_TARGETS) == 9
        assert ("dbos.notifications", "destination_uuid") in CLEANUP_NATIVE_TARGETS
        assert ("agent_ds.datasource_outputs", "workflow_id") in CLEANUP_NATIVE_TARGETS
        workflow_tables = [table for table, key in CLEANUP_NATIVE_TARGETS if key == "workflow_uuid"]
        assert len(workflow_tables) == 7
        assert "dbos.streams" in workflow_tables


class TestCleanupIds:
    def test_ids_are_deduped_sorted_and_non_empty(self) -> None:
        assert normalize_cleanup_ids(["b", "a", "b"]) == ("a", "b")

    def test_exactly_two_hundred_is_allowed(self) -> None:
        ids = [f"id-{index:03d}" for index in range(CLEANUP_MAX_BATCH_IDS)]
        assert len(normalize_cleanup_ids(ids)) == CLEANUP_MAX_BATCH_IDS

    def test_two_hundred_and_one_requires_separate_batches(self) -> None:
        ids = [f"id-{index:03d}" for index in range(CLEANUP_MAX_BATCH_IDS + 1)]
        with pytest.raises(AgentValidationError) as exc:
            normalize_cleanup_ids(ids)
        assert exc.value.code == "cleanup_batch_limit_exceeded"

    def test_empty_or_blank_ids_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            normalize_cleanup_ids(["ok", ""])
        assert exc.value.code == "invalid_cleanup_id"
        with pytest.raises(AgentValidationError) as exc:
            normalize_cleanup_ids(["ok", 123])  # type: ignore[list-item]  # 非法类型输入
        assert exc.value.code == "invalid_cleanup_id"


class TestAdmissionPredicate:
    def _precondition(self, **overrides: bool) -> CleanupPrecondition:
        values: dict[str, bool] = {
            "scope_settled": True,
            "writers_exited": True,
            "closure_known": True,
            "preview_valid": True,
            "new_reference_conflict": False,
        }
        values.update(overrides)
        return CleanupPrecondition(**values)  # type: ignore[arg-type]  # 测试受控覆盖

    def test_all_conditions_allow(self) -> None:
        decision = evaluate_cleanup_admission(self._precondition())
        assert decision == CleanupAdmission(allowed=True, reason=None)

    @pytest.mark.parametrize(
        ("flag", "reason"),
        [
            ("scope_settled", "scope_not_settled"),
            ("writers_exited", "writers_still_running"),
            ("closure_known", "closure_unknown"),
            ("preview_valid", "preview_invalid"),
        ],
    )
    def test_each_missing_condition_blocks_with_stable_reason(self, flag: str, reason: str) -> None:
        decision = evaluate_cleanup_admission(self._precondition(**{flag: False}))
        assert decision.allowed is False
        assert decision.reason == reason

    def test_new_reference_after_fence_blocks(self) -> None:
        decision = evaluate_cleanup_admission(self._precondition(new_reference_conflict=True))
        assert decision.allowed is False
        assert decision.reason == "new_reference_conflict"


class TestStateMachine:
    def test_delete_requires_committed_fence(self) -> None:
        state = CleanupState(phase=CleanupPhase.PREPARED, fence_committed=False)
        assert state.can_delete() is False
        state = CleanupState(phase=CleanupPhase.PREPARED, fence_committed=True)
        assert state.can_delete() is True

    def test_verified_requires_every_domain_zero(self) -> None:
        state = CleanupState(phase=CleanupPhase.PREPARED, fence_committed=True)
        assert state.can_mark_verified(dict.fromkeys((t for t, _ in CLEANUP_NATIVE_TARGETS), 0)) is True
        partial = {table: 0 for table, _ in CLEANUP_NATIVE_TARGETS}
        partial["dbos.streams"] = 1
        assert state.can_mark_verified(partial) is False

    def test_verified_requires_all_nine_domains_checked(self) -> None:
        state = CleanupState(phase=CleanupPhase.PREPARED, fence_committed=True)
        assert state.can_mark_verified({"dbos.workflow_status": 0}) is False

    def test_retry_pending_preserves_original_set(self) -> None:
        state = CleanupState(
            phase=CleanupPhase.RETRY_PENDING,
            fence_committed=True,
            exact_ids=("a", "b"),
        )
        assert state.can_delete() is True
        assert state.exact_ids == ("a", "b")


class TestTombstone:
    def test_tombstone_never_contains_original_text(self) -> None:
        original = "私人原句：600519 明天开盘买入"
        replaced = tombstone_text(original)
        assert replaced == CLEANUP_TOMBSTONE
        assert original not in replaced

    def test_tombstone_is_stable_for_empty_and_none(self) -> None:
        assert tombstone_text("") == CLEANUP_TOMBSTONE
        assert tombstone_text(None) == CLEANUP_TOMBSTONE
