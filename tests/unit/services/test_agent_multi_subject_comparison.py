"""多标的对比契约单测（J05：逐标的结论单列、缺失显式、不静默降级）。"""

from __future__ import annotations

import pytest

from core.agent_types import AgentValidationError
from services.agent_service.workflows import (
    MISSING_REASON_IDENTITY,
    MISSING_REASON_OVER_LIMIT,
    SUBJECT_STATE_COMPLETE,
    SUBJECT_STATE_PENDING,
    ComparisonSubject,
    MissingSubject,
    SubjectResearchResult,
    build_pending_subject_comparison,
    build_subject_comparison,
    normalize_subjects,
)

pytestmark = pytest.mark.unit


def _subject(object_id: str, *, name: str | None = None) -> ComparisonSubject:
    return ComparisonSubject("stock", object_id, name or f"标的{object_id}")


def _complete(object_id: str, conclusion: str = "结论") -> SubjectResearchResult:
    return SubjectResearchResult(state=SUBJECT_STATE_COMPLETE, conclusion=conclusion)


class TestNormalizeSubjects:
    def test_dedupes_by_stable_identity_and_keeps_order(self) -> None:
        selection = normalize_subjects([_subject("1"), _subject("2"), _subject("1")])

        assert [item.object_id for item in selection.subjects] == ["1", "2"]
        assert selection.duplicate_count == 1
        assert selection.missing == ()

    def test_over_limit_becomes_explicit_missing(self) -> None:
        selection = normalize_subjects([_subject(str(index)) for index in range(3)], limit=2)

        assert [item.object_id for item in selection.subjects] == ["0", "1"]
        assert len(selection.missing) == 1
        assert selection.missing[0].reason == MISSING_REASON_OVER_LIMIT
        assert selection.missing[0].display_name == "标的2"

    def test_explicit_missing_is_preserved(self) -> None:
        selection = normalize_subjects(
            [_subject("1")],
            missing=(MissingSubject("无代码标的", MISSING_REASON_IDENTITY),),
        )

        assert len(selection.subjects) == 1
        assert selection.missing[0].reason == MISSING_REASON_IDENTITY

    def test_invalid_limit_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            normalize_subjects([_subject("1")], limit=0)

        assert exc.value.code == "invalid_limit"
        assert exc.value.field_path == "$.limit"


class TestBuildSubjectComparison:
    def test_all_subjects_complete(self) -> None:
        subjects = [_subject("1"), _subject("2")]
        selection = normalize_subjects(subjects)

        comparison = build_subject_comparison(
            selection,
            {"stock:1": _complete("1", "A 结论"), "stock:2": _complete("2", "B 结论")},
        )

        assert comparison.status == "complete"
        assert comparison.missing_objects == ()
        assert [row.conclusion for row in comparison.rows] == ["A 结论", "B 结论"]

    def test_missing_result_lists_object_and_degrades_to_partial(self) -> None:
        selection = normalize_subjects([_subject("1"), _subject("2"), _subject("3")])

        comparison = build_subject_comparison(
            selection,
            {"stock:1": _complete("1"), "stock:2": _complete("2"), "stock:3": None},
        )

        assert comparison.status == "partial"
        assert comparison.missing_objects == ("标的3",)
        missing_rows = [row for row in comparison.rows if row.state == "missing"]
        assert len(missing_rows) == 1
        assert missing_rows[0].display_name == "标的3"
        assert missing_rows[0].conclusion is None

    def test_absent_key_is_missing_not_silently_dropped(self) -> None:
        selection = normalize_subjects([_subject("1")])

        comparison = build_subject_comparison(selection, {})

        assert comparison.status == "partial"
        assert comparison.missing_objects == ("标的1",)
        assert len(comparison.rows) == 1

    def test_excluded_subject_stays_visible_as_missing(self) -> None:
        selection = normalize_subjects(
            [_subject("1")],
            missing=(MissingSubject("无代码标的", MISSING_REASON_IDENTITY),),
        )

        comparison = build_subject_comparison(selection, {"stock:1": _complete("1")})

        assert comparison.status == "partial"
        assert "无代码标的" in comparison.missing_objects
        assert any(row.display_name == "无代码标的" and row.state == "missing" for row in comparison.rows)

    def test_pending_comparison_has_no_missing_but_is_not_complete(self) -> None:
        selection = normalize_subjects([_subject("1"), _subject("2")])

        comparison = build_pending_subject_comparison(selection)

        assert comparison.missing_objects == ()
        assert comparison.status == "partial"
        assert {row.state for row in comparison.rows} == {SUBJECT_STATE_PENDING}

    def test_unknown_result_key_rejected(self) -> None:
        selection = normalize_subjects([_subject("1")])

        with pytest.raises(AgentValidationError) as exc:
            build_subject_comparison(selection, {"stock:9": _complete("9")})

        assert exc.value.code == "unknown_subject_result"
        assert exc.value.field_path == "$.results[stock:9]"

    def test_empty_selection_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            build_subject_comparison(normalize_subjects([]), {})

        assert exc.value.code == "empty_subjects"

    def test_complete_without_conclusion_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            SubjectResearchResult(state=SUBJECT_STATE_COMPLETE)

        assert exc.value.code == "complete_without_conclusion"

    def test_pending_with_conclusion_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            SubjectResearchResult(state=SUBJECT_STATE_PENDING, conclusion="提前下结论")

        assert exc.value.code == "pending_with_conclusion"
