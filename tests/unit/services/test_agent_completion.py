"""研究完成核定（R2-I10）：三项事实分别核定、对象级缺口与组级结论。

覆盖：完整/部分/失败/未知判定、缺证不得判完整、未知优先于失败、零匹配与
多源冲突/缺数据不伪完整、叶事实汇总到对象级缺口的稳定映射。
"""

from __future__ import annotations

import json

import pytest

from core.agent_types import (
    AgentValidationError,
    ResearchObjectGap,
    ResearchResult,
    ResearchResultDraft,
    assess_research_completion,
    encode_research_result_payload,
)
from services.agent_service.completion import assess_plan_completion, object_gaps_from_leaf_summary
from services.agent_service.workflows import NodeOutcome, PlanRunSummary

pytestmark = pytest.mark.unit


def _gap(
    object_ref: str = "600519.SH", kind: str = "missing_period", detail: str = "缺 2024 年报"
) -> ResearchObjectGap:
    return ResearchObjectGap(object_ref=object_ref, gap_kind=kind, detail=detail)


def _summary(**overrides: object) -> PlanRunSummary:
    values: dict[str, object] = {
        "succeeded": (),
        "failed": (),
        "withdrawn": (),
        "ended": (),
        "partial": (),
        "closed_unknown": (),
        "blocked": (),
        "pending": (),
        "block_reasons": (),
        "outcomes": (),
        "leaves_all_succeeded": False,
        "converged": False,
    }
    values.update(overrides)
    return PlanRunSummary(**values)  # type: ignore[arg-type]


def _draft(**overrides: object) -> ResearchResultDraft:
    values: dict[str, object] = {
        "user_scope": "u1",
        "session_id": "s1",
        "execution_id": "execution-1",
        "round_id": "round-1",
        "node_id": "root",
        "input_revision": 1,
        "actual_input_digest": "a" * 64,
        "actual_model_ref": "model-ref",
        "result_kind": "comparison",
        "summary": "结论",
        "key_points": (),
        "body_text": "正文",
        "structured_fields": None,
        "object_rows": (),
        "evidence_links": (),
        "source_available_state": "unknown",
        "coverage": None,
        "missing_fields": (),
        "failed_signal_dates": (),
        "data_warnings": (),
        "limitations": (),
        "verification_state": "unverified",
        "execution_state": "complete",
        "goal_coverage": "full",
        "convergence_confirmed": True,
        "unknown_operation_refs": (),
        "ledger_refs": (),
        "plan_version": None,
        "parent_artifact_refs": (),
        "source_refs": (),
    }
    values.update(overrides)
    return ResearchResultDraft(**values)  # type: ignore[arg-type]


class TestThreeFacts:
    def test_all_facts_hold_yields_complete(self) -> None:
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )

        assert (assessment.execution_ended, assessment.result_saved) == (True, True)
        assert assessment.goal_coverage == "full"
        assert assessment.outcome == "complete"
        assert assessment.object_gaps == ()

    def test_running_execution_is_not_failure(self) -> None:
        assessment = assess_research_completion(
            execution_state="running",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=False,
        )

        assert assessment.execution_ended is False
        assert assessment.outcome == "unknown"

    def test_unreadable_execution_state_stays_unverified(self) -> None:
        assessment = assess_research_completion(
            execution_state="unknown",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )

        assert assessment.execution_ended is None
        assert assessment.outcome == "unknown"

    def test_unknown_goal_coverage_never_reported_complete(self) -> None:
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="unknown",
            convergence_confirmed=True,
        )

        assert assessment.outcome == "unknown"

    def test_saved_result_without_confirmed_convergence_is_partial(self) -> None:
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=False,
        )

        assert assessment.goal_coverage == "partial"
        assert assessment.outcome == "partial"


class TestGoalCoverageIsConstrainedByGaps:
    def test_missing_company_gap_downgrades_full_claim(self) -> None:
        gap = _gap(object_ref="600519.SH", kind="missing_object", detail="缺第二家公司同期资料")
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
            object_gaps=(gap,),
        )

        assert assessment.goal_coverage == "partial"
        assert assessment.outcome == "partial"
        assert assessment.object_gaps == (gap,)

    def test_missing_fields_block_complete_even_without_object_identity(self) -> None:
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
            missing_fields=("revenue",),
        )

        assert assessment.goal_coverage == "partial"
        assert assessment.outcome == "partial"

    def test_data_warnings_block_complete(self) -> None:
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
            data_warnings=("source read failed",),
        )

        assert assessment.outcome == "partial"

    def test_zero_match_confirmed_can_be_complete(self) -> None:
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )

        assert assessment.outcome == "complete"

    def test_zero_match_unconfirmed_is_not_complete(self) -> None:
        gap = _gap(object_ref="pool", kind="result_unconfirmed", detail="读取失败不能在零匹配上冒充确定")
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
            object_gaps=(gap,),
        )

        assert assessment.outcome == "partial"

    def test_source_conflict_is_preserved_as_gap(self) -> None:
        gaps = (
            _gap(object_ref="000001.SZ", kind="source_conflict", detail="两来源营收不一致"),
            _gap(object_ref="000001.SZ", kind="unverified_claim", detail="引用存在但不支持结论"),
        )
        assessment = assess_research_completion(
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
            object_gaps=gaps,
        )

        assert assessment.object_gaps == gaps
        assert assessment.outcome == "partial"


class TestOutcomePrecedence:
    def test_closed_unknown_wins_over_failure(self) -> None:
        assessment = assess_research_completion(
            execution_state="closed_unknown",
            final_output_saved=False,
            goal_coverage="partial",
            convergence_confirmed=False,
        )

        assert assessment.outcome == "unknown"

    def test_ended_without_saved_result_is_failed(self) -> None:
        assessment = assess_research_completion(
            execution_state="failed",
            final_output_saved=False,
            goal_coverage="none",
            convergence_confirmed=False,
        )

        assert assessment.outcome == "failed"

    def test_saved_result_with_failed_execution_stays_partial(self) -> None:
        assessment = assess_research_completion(
            execution_state="failed",
            final_output_saved=True,
            goal_coverage="partial",
            convergence_confirmed=False,
        )

        assert assessment.outcome == "partial"

    def test_saved_result_without_any_coverage_is_partial_not_failed(self) -> None:
        assessment = assess_research_completion(
            execution_state="ended",
            final_output_saved=True,
            goal_coverage="none",
            convergence_confirmed=False,
        )

        assert assessment.outcome == "partial"


class TestObjectGapContract:
    def test_gap_requires_non_empty_identity_and_detail(self) -> None:
        with pytest.raises(AgentValidationError) as empty_ref:
            ResearchObjectGap(object_ref="", gap_kind="missing_object", detail="x")
        with pytest.raises(AgentValidationError) as empty_detail:
            ResearchObjectGap(object_ref="600519.SH", gap_kind="missing_object", detail="")

        assert empty_ref.value.code == "invalid_value"
        assert empty_ref.value.field_path == "$.object_ref"
        assert empty_detail.value.code == "invalid_value"
        assert empty_detail.value.field_path == "$.detail"

    def test_unknown_gap_kind_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            ResearchObjectGap(object_ref="600519.SH", gap_kind="made_up", detail="x")

        assert excinfo.value.code == "invalid_gap_kind"

    def test_duplicate_gap_in_draft_rejected(self) -> None:
        gap = _gap()

        with pytest.raises(AgentValidationError) as excinfo:
            _draft(object_gaps=(gap, gap))

        assert excinfo.value.code == "duplicate_object_gap"


class TestResultContractIntegration:
    def test_draft_assessment_separates_saved_fact(self) -> None:
        draft = _draft(object_gaps=(_gap(),))

        assert draft.assess_completion(final_output_saved=True).outcome == "partial"
        assert draft.assess_completion(final_output_saved=False).outcome == "failed"

    def test_saved_result_exposes_completion_assessment(self) -> None:
        saved = ResearchResult.from_draft(_draft(), artifact_id="artifact-1", artifact_version=1)

        assert saved.completion.execution_ended is True
        assert saved.completion.result_saved is True
        assert saved.completion.outcome == "complete"

    def test_plan_only_execution_never_counts_as_goal_met(self) -> None:
        draft = _draft(
            result_kind="plan", execution_state="planned", goal_coverage="unknown", convergence_confirmed=False
        )

        assert draft.assess_completion(final_output_saved=True).outcome == "unknown"

    def test_payload_persists_three_facts_and_gaps(self) -> None:
        gap = _gap(object_ref="600519.SH", kind="missing_object", detail="缺公司")
        payload = json.loads(encode_research_result_payload(_draft(object_gaps=(gap,)), final_output_saved=True))

        assert payload["schema_version"] == 2
        completion = payload["completion"]
        assert completion["execution_state"] == "complete"
        assert completion["execution_ended"] is True
        assert completion["result_saved"] is True
        assert completion["goal_coverage"] == "full"
        assert completion["goal_coverage_effective"] == "partial"
        assert completion["outcome"] == "partial"
        assert completion["object_gaps"] == [
            {"object_ref": "600519.SH", "gap_kind": "missing_object", "detail": "缺公司"}
        ]


class TestLeafSummaryBridge:
    def test_all_leaves_complete_has_no_gaps(self) -> None:
        summary = _summary(
            succeeded=("a", "b"),
            outcomes=(NodeOutcome("a", "complete"), NodeOutcome("b", "complete")),
            leaves_all_succeeded=True,
            converged=True,
        )

        assert object_gaps_from_leaf_summary(summary) == ()
        assessment = assess_plan_completion(
            summary,
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )
        assert assessment.outcome == "complete"

    def test_leaf_states_map_to_gap_kinds(self) -> None:
        summary = _summary(
            failed=("a",),
            partial=("b",),
            closed_unknown=("c",),
            ended=("d",),
            blocked=("e",),
            pending=("f",),
            block_reasons=(("e", "upstream_failed:a"),),
            outcomes=(
                NodeOutcome("a", "failed", error_code="leaf_workflow_error"),
                NodeOutcome("b", "partial"),
                NodeOutcome("c", "closed_unknown"),
                NodeOutcome("d", "ended"),
            ),
        )

        gaps = {gap.object_ref: gap for gap in object_gaps_from_leaf_summary(summary)}

        assert set(gaps) == {"a", "b", "c", "d", "e", "f"}
        assert gaps["a"].gap_kind == "no_result"
        assert "leaf_workflow_error" in gaps["a"].detail
        assert gaps["b"].gap_kind == "missing_data"
        assert gaps["c"].gap_kind == "result_unconfirmed"
        assert gaps["d"].gap_kind == "no_result"
        assert gaps["e"].gap_kind == "missing_object"
        assert gaps["e"].detail == "blocked:upstream_failed:a"
        assert gaps["f"].gap_kind == "result_unconfirmed"

    def test_non_terminal_leaf_fact_is_not_silently_dropped(self) -> None:
        summary = _summary(pending=("a",), outcomes=(NodeOutcome("a", "paused"),))

        gaps = object_gaps_from_leaf_summary(summary)

        assert len(gaps) == 1
        assert gaps[0].object_ref == "a"
        assert gaps[0].gap_kind == "result_unconfirmed"

    def test_partial_leaves_cannot_be_reported_complete(self) -> None:
        summary = _summary(partial=("a",), outcomes=(NodeOutcome("a", "partial"),))

        assessment = assess_plan_completion(
            summary,
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )

        assert assessment.goal_coverage == "partial"
        assert assessment.outcome == "partial"

    def test_pending_leaves_override_complete_execution_state(self) -> None:
        summary = _summary(pending=("a",), leaves_all_succeeded=False, converged=False)

        assessment = assess_plan_completion(
            summary,
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )

        assert assessment.outcome == "unknown"

    def test_blocked_object_keeps_reason_and_blocks_complete(self) -> None:
        summary = _summary(
            failed=("a",),
            blocked=("b",),
            block_reasons=(("b", "upstream_failed:a"),),
            outcomes=(NodeOutcome("a", "failed"),),
        )

        assessment = assess_plan_completion(
            summary,
            execution_state="complete",
            final_output_saved=True,
            goal_coverage="full",
            convergence_confirmed=True,
        )

        assert assessment.outcome == "partial"
        assert {(gap.object_ref, gap.gap_kind) for gap in assessment.object_gaps} == {
            ("a", "no_result"),
            ("b", "missing_object"),
        }
