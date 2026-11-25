"""MultiSubjectResearchViewModel 单测（去重、上限、缺失显式、零发送）。"""

from __future__ import annotations

import pytest

from ui.viewmodels.multi_subject_research_view_model import (
    MISSING_REASON_IDENTITY,
    NOTICE_LIMIT_REACHED,
    NOTICE_NO_SELECTION,
    NOTICE_PARTIAL,
    NOTICE_SELECT_ALL_TRUNCATED,
    NOTICE_SUBMITTED,
    MultiSubjectResearchViewModel,
    SubjectCandidate,
)

pytestmark = pytest.mark.unit


def _candidate(object_id: str, *, kind: str = "screener_candidate") -> SubjectCandidate:
    return SubjectCandidate(object_kind=kind, object_id=object_id, display_name=f"标的{object_id}")


def _vm(limit: int = 10) -> MultiSubjectResearchViewModel:
    vm = MultiSubjectResearchViewModel(limit=limit)
    vm.open_candidates([_candidate(str(index)) for index in range(1, 4)])
    return vm


def test_open_dedupes_candidates() -> None:
    vm = MultiSubjectResearchViewModel()
    vm.open_candidates([_candidate("1"), _candidate("1"), _candidate("2")])

    assert [item.object_id for item in vm.state.candidates] == ["1", "2"]


def test_toggle_and_clear() -> None:
    vm = _vm()

    vm.toggle("screener_candidate:1")
    assert vm.state.selected_keys == ("screener_candidate:1",)

    vm.toggle("screener_candidate:1")
    assert vm.state.selected_keys == ()

    vm.toggle("screener_candidate:2")
    vm.clear()
    assert vm.state.selected_keys == ()
    assert vm.state.comparison is None


def test_toggle_respects_limit_and_reports_notice() -> None:
    vm = _vm(limit=1)

    vm.toggle("screener_candidate:1")
    vm.toggle("screener_candidate:2")

    assert vm.state.selected_keys == ("screener_candidate:1",)
    assert vm.state.notice_key == NOTICE_LIMIT_REACHED


def test_select_all_truncates_and_lists_overflow_as_missing() -> None:
    vm = _vm(limit=2)

    vm.select_all()

    assert vm.state.selected_keys == ("screener_candidate:1", "screener_candidate:2")
    assert vm.state.excluded_keys == ("screener_candidate:3",)
    assert vm.state.notice_key == NOTICE_SELECT_ALL_TRUNCATED

    prepared: list[str] = []
    vm.submit(lambda key: prepared.append(key) or True)

    assert prepared == ["screener_candidate:1", "screener_candidate:2"]
    comparison = vm.state.comparison
    assert comparison is not None
    assert "标的3" in comparison.missing_objects
    assert vm.state.notice_key == NOTICE_PARTIAL


def test_submit_without_selection_reports_notice_and_zero_prepare() -> None:
    vm = _vm()
    prepared: list[str] = []

    vm.submit(lambda key: prepared.append(key) or True)

    assert prepared == []
    assert vm.state.notice_key == NOTICE_NO_SELECTION
    assert vm.state.comparison is None


def test_submit_all_prepared_builds_pending_rows_without_missing() -> None:
    vm = _vm()
    vm.select_all()
    prepared: list[str] = []

    vm.submit(lambda key: prepared.append(key) or True)

    assert prepared == ["screener_candidate:1", "screener_candidate:2", "screener_candidate:3"]
    comparison = vm.state.comparison
    assert comparison is not None
    assert comparison.missing_objects == ()
    assert {row.state for row in comparison.rows} == {"pending"}
    assert vm.state.notice_key == NOTICE_SUBMITTED


def test_selection_change_discards_stale_comparison() -> None:
    vm = _vm()
    vm.select_all()
    vm.submit(lambda _key: True)
    assert vm.state.comparison is not None

    vm.toggle("screener_candidate:1")

    assert vm.state.submitted is False
    assert vm.state.comparison is None


def test_prepare_failure_is_explicit_missing() -> None:
    vm = _vm()
    vm.select_all()

    vm.submit(lambda key: key != "screener_candidate:2")

    comparison = vm.state.comparison
    assert comparison is not None
    assert comparison.missing_objects == ("标的2",)
    assert vm.state.notice_key == NOTICE_PARTIAL


def test_unselectable_candidate_is_listed_as_missing_after_submit() -> None:
    vm = MultiSubjectResearchViewModel()
    vm.open_candidates(
        [
            _candidate("1"),
            SubjectCandidate(
                object_kind="news_item",
                object_id="",
                display_name="无 id 资讯",
                missing_reason=MISSING_REASON_IDENTITY,
            ),
        ]
    )
    vm.toggle("screener_candidate:1")

    vm.submit(lambda _key: True)

    comparison = vm.state.comparison
    assert comparison is not None
    assert "无 id 资讯" in comparison.missing_objects
    assert vm.state.notice_key == NOTICE_PARTIAL
