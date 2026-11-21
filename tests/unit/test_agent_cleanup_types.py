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
    CLEANUP_VERIFIED_DOMAINS,
    AgentValidationError,
    CleanupAdmission,
    CleanupIdentitySet,
    CleanupJobRecord,
    CleanupPhase,
    CleanupPrecondition,
    CleanupState,
    decode_cleanup_identity_set,
    encode_cleanup_identity_set,
    evaluate_cleanup_admission,
    normalize_cleanup_asset_keys,
    normalize_cleanup_ids,
    tombstone_text,
    validate_cleanup_phase_write,
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
            "unknown_deliveries": False,
        }
        values.update(overrides)
        return CleanupPrecondition(**values)  # type: ignore[arg-type]  # 测试受控覆盖

    def test_all_conditions_allow(self) -> None:
        decision = evaluate_cleanup_admission(self._precondition())
        assert decision == CleanupAdmission(allowed=True, reason=None)

    def test_unknown_delivery_blocks_before_other_conditions(self) -> None:
        decision = evaluate_cleanup_admission(self._precondition(unknown_deliveries=True))
        assert decision.allowed is False
        assert decision.reason == "unknown_delivery_pending"

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

    def test_can_mark_verified_rejects_bool_as_zero(self) -> None:
        """``bool`` 是 ``int`` 子类且 ``False == 0``，不得冒充已清零（D31）。"""

        state = CleanupState(phase=CleanupPhase.PREPARED, fence_committed=True)
        counts: dict[str, object] = dict.fromkeys(CLEANUP_VERIFIED_DOMAINS, 0)
        counts["dbos.streams"] = False
        assert state.can_mark_verified(counts) is False


def _zeros(overrides: dict[str, object] | None = None) -> dict[str, object]:
    counts: dict[str, object] = dict.fromkeys(CLEANUP_VERIFIED_DOMAINS, 0)
    counts.update(overrides or {})
    return counts


class TestCleanupVerificationWrite:
    """D31：verified 写入必须九域齐备零值且 complete 一致。"""

    def test_verified_domains_cover_nine_native_targets_including_application(self) -> None:
        assert tuple(table for table, _key in CLEANUP_NATIVE_TARGETS) == CLEANUP_VERIFIED_DOMAINS
        assert len(CLEANUP_VERIFIED_DOMAINS) == 9
        assert "agent_ds.datasource_outputs" in CLEANUP_VERIFIED_DOMAINS

    def test_all_zero_with_complete_true_is_accepted(self) -> None:
        validate_cleanup_phase_write(CleanupPhase.VERIFIED, _zeros(), complete=True)

    def test_all_zero_with_complete_false_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.VERIFIED, _zeros(), complete=False)
        assert exc.value.code == "invalid_cleanup_state"

    def test_missing_domain_is_rejected(self) -> None:
        counts = _zeros()
        del counts["agent_ds.datasource_outputs"]
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.VERIFIED, counts, complete=True)
        assert exc.value.code == "missing_field"
        assert "agent_ds.datasource_outputs" in exc.value.field_path

    def test_missing_counts_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.VERIFIED, None, complete=True)
        assert exc.value.code == "missing_field"

    def test_unknown_domain_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.VERIFIED, _zeros({"extra.domain": 0}), complete=True)
        assert exc.value.code == "unknown_field"

    @pytest.mark.parametrize("bad", [False, True, 0.0, "0", None])
    def test_non_strict_int_count_is_rejected(self, bad: object) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.VERIFIED, _zeros({"dbos.streams": bad}), complete=True)
        assert exc.value.code == "invalid_type"

    def test_non_zero_count_with_complete_true_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(
                CleanupPhase.VERIFIED, _zeros({"agent_ds.datasource_outputs": 1}), complete=True
            )
        assert exc.value.code == "invalid_range"

    def test_retry_pending_allows_non_zero_and_partial_counts(self) -> None:
        validate_cleanup_phase_write(
            CleanupPhase.RETRY_PENDING,
            {"dbos.workflow_status": 2, "agent_ds.datasource_outputs": 0},
            complete=False,
        )

    def test_retry_pending_with_complete_true_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.RETRY_PENDING, _zeros(), complete=True)
        assert exc.value.code == "invalid_cleanup_state"

    def test_retry_pending_rejects_bool_count(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_cleanup_phase_write(CleanupPhase.RETRY_PENDING, {"dbos.streams": False}, complete=False)
        assert exc.value.code == "invalid_type"


class TestTombstone:
    def test_tombstone_never_contains_original_text(self) -> None:
        original = "私人原句：600519 明天开盘买入"
        replaced = tombstone_text(original)
        assert replaced == CLEANUP_TOMBSTONE
        assert original not in replaced

    def test_tombstone_is_stable_for_empty_and_none(self) -> None:
        assert tombstone_text("") == CLEANUP_TOMBSTONE
        assert tombstone_text(None) == CLEANUP_TOMBSTONE


class TestCleanupIdentitySetCodec:
    def _record(self, **overrides: object) -> CleanupJobRecord:
        values: dict[str, object] = {
            "user_scope": "owner-1",
            "cleanup_job_id": "job-1",
            "owner_ref": "session-1",
            "preview_digest": "a" * 64,
            "expected_revision": 3,
            "exact_ids": ("wf-b", "wf-a"),
            "phase": CleanupPhase.PREPARED,
            "fence_committed": False,
            "session_ids": ("session-2", "session-1"),
            "asset_keys": (("asset-b", "2"), ("asset-a", "1")),
        }
        values.update(overrides)
        return CleanupJobRecord(**values)  # type: ignore[arg-type]  # 测试受控覆盖

    def test_asset_keys_are_deduped_sorted_and_limited(self) -> None:
        assert normalize_cleanup_asset_keys([("b", "2"), ("a", "1"), ("b", "2")]) == (("a", "1"), ("b", "2"))
        with pytest.raises(AgentValidationError) as exc:
            normalize_cleanup_asset_keys([(f"asset-{index:03d}", "1") for index in range(CLEANUP_MAX_BATCH_IDS + 1)])
        assert exc.value.code == "cleanup_batch_limit_exceeded"

    @pytest.mark.parametrize("key", [("asset-1",), ("asset-1", ""), ("", "1"), "asset-1", ("asset-1", 1)])
    def test_invalid_asset_key_rejected(self, key: object) -> None:
        with pytest.raises(AgentValidationError) as exc:
            normalize_cleanup_asset_keys([key])
        assert exc.value.code == "invalid_cleanup_asset_key"

    def test_encode_is_canonical_and_roundtrips(self) -> None:
        encoded = encode_cleanup_identity_set(self._record())
        assert encoded["native_ids"] == ["wf-a", "wf-b"]
        assert encoded["session_ids"] == ["session-1", "session-2"]
        assert encoded["asset_keys"] == [["asset-a", "1"], ["asset-b", "2"]]
        decoded = decode_cleanup_identity_set(encoded)
        assert decoded == CleanupIdentitySet(
            native_ids=("wf-a", "wf-b"),
            session_ids=("session-1", "session-2"),
            asset_keys=(("asset-a", "1"), ("asset-b", "2")),
        )
        record = self._record()
        assert (
            decoded
            == CleanupIdentitySet(
                native_ids=normalize_cleanup_ids(record.exact_ids),
                session_ids=normalize_cleanup_ids(record.session_ids),
                asset_keys=normalize_cleanup_asset_keys(record.asset_keys),
            )
            == CleanupIdentitySet(
                native_ids=("wf-a", "wf-b"),
                session_ids=("session-1", "session-2"),
                asset_keys=(("asset-a", "1"), ("asset-b", "2")),
            )
        )

    def test_empty_domains_are_explicit_not_wildcards(self) -> None:
        decoded = decode_cleanup_identity_set({"native_ids": [], "session_ids": [], "asset_keys": []})
        assert decoded == CleanupIdentitySet()

    @pytest.mark.parametrize(
        "payload",
        [
            {"native_ids": ["a"], "session_ids": []},
            {"native_ids": ["a"], "session_ids": [], "asset_keys": [], "extra": 1},
            {"native_ids": "a", "session_ids": [], "asset_keys": []},
            {"native_ids": [], "session_ids": [], "asset_keys": [["a"]]},
            {"native_ids": [""], "session_ids": [], "asset_keys": []},
        ],
    )
    def test_decode_rejects_malformed_payloads(self, payload: object) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_cleanup_identity_set(payload)
        assert exc.value.code in {
            "missing_field",
            "unknown_field",
            "invalid_type",
            "invalid_cleanup_id",
            "invalid_cleanup_asset_key",
        }
