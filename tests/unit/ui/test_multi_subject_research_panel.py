"""MultiSubjectResearchPanel 渲染单测（逐标的对照、缺失列举、无结论不填 0）。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import flet as ft
import pytest

import ui.components.multi_subject_research_panel as panel_module
from services.agent_service.workflows import (
    MISSING_REASON_IDENTITY,
    SUBJECT_STATE_COMPLETE,
    SUBJECT_STATE_MISSING,
    SUBJECT_STATE_PENDING,
    ComparisonSubject,
    MissingSubject,
    SubjectComparison,
    SubjectComparisonRow,
    SubjectResearchResult,
    build_subject_comparison,
    normalize_subjects,
)
from tests.unit.ui.render_helper import render_component
from ui.viewmodels.multi_subject_research_view_model import (
    MultiSubjectResearchState,
    SubjectCandidate,
)
from ui.components.multi_subject_research_panel import MultiSubjectResearchPanel

pytestmark = pytest.mark.unit


class _FakeI18n:
    @staticmethod
    def get(key: str, *args: Any, **kwargs: Any) -> str:
        return key


@pytest.fixture(autouse=True)
def _patch_i18n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(panel_module, "I18n", _FakeI18n)


def _callbacks() -> dict[str, Any]:
    return {
        "on_toggle": lambda key: None,
        "on_select_all": lambda: None,
        "on_clear": lambda: None,
        "on_submit": lambda: None,
        "on_close": lambda: None,
    }


def _walk(control: Any) -> Iterator[Any]:
    yield control
    if isinstance(control, ft.Container):
        inner = control.content
        if inner is not None:
            yield from _walk(inner)
    elif isinstance(control, (ft.Row, ft.Column)):
        for child in control.controls:
            yield from _walk(child)
    elif isinstance(control, ft.DataTable):
        for column in control.columns:
            yield from _walk(column)
        for row in control.rows:
            yield from _walk(row)
    elif isinstance(control, ft.DataColumn):
        label = control.label
        if label is not None:
            yield from _walk(label)
    elif isinstance(control, ft.DataRow):
        for cell in control.cells:
            yield from _walk(cell)
    elif isinstance(control, ft.DataCell):
        inner = control.content
        if inner is not None:
            yield from _walk(inner)


def _texts(root: Any) -> list[str]:
    return [node.value for node in _walk(root) if isinstance(node, ft.Text)]


def _pending_state() -> MultiSubjectResearchState:
    subject = ComparisonSubject("stock", "1", "标的A")
    selection = normalize_subjects([subject])
    comparison = build_subject_comparison(
        selection,
        {"stock:1": SubjectResearchResult(state=SUBJECT_STATE_PENDING)},
    )
    return MultiSubjectResearchState(
        open=True,
        candidates=(SubjectCandidate("stock", "1", "标的A"),),
        selected_keys=("stock:1",),
        comparison=comparison,
    )


def _missing_state() -> MultiSubjectResearchState:
    selection = normalize_subjects(
        [ComparisonSubject("stock", "1", "标的A")],
        missing=(MissingSubject("无代码标的", MISSING_REASON_IDENTITY),),
    )
    comparison = build_subject_comparison(
        selection,
        {"stock:1": SubjectResearchResult(state=SUBJECT_STATE_COMPLETE, conclusion="A 结论")},
    )
    return MultiSubjectResearchState(
        open=True,
        candidates=(
            SubjectCandidate("stock", "1", "标的A"),
            SubjectCandidate("stock", "", "无代码标的", MISSING_REASON_IDENTITY),
        ),
        selected_keys=("stock:1",),
        comparison=comparison,
    )


def test_component_is_declarative() -> None:
    assert hasattr(MultiSubjectResearchPanel, "__wrapped__")


def test_renders_candidates_and_selected_count() -> None:
    control = render_component(MultiSubjectResearchPanel, state=_pending_state(), **_callbacks())

    texts = _texts(control)
    assert "agent_multi_subject_title" in texts
    assert "标的A" in texts
    assert "agent_multi_subject_selected_fmt" in texts


def test_pending_comparison_shows_no_conclusion_not_zero() -> None:
    control = render_component(MultiSubjectResearchPanel, state=_pending_state(), **_callbacks())

    texts = _texts(control)
    joined = "\n".join(texts)
    assert "agent_multi_subject_status_pending" in texts
    assert "agent_multi_subject_state_pending" in texts
    assert "agent_multi_subject_no_conclusion" in joined


def test_missing_subject_is_listed_in_partial_banner_and_row() -> None:
    control = render_component(MultiSubjectResearchPanel, state=_missing_state(), **_callbacks())

    texts = _texts(control)
    joined = "\n".join(texts)
    assert "agent_multi_subject_status_partial_fmt" in texts
    assert "无代码标的" in joined
    assert "agent_multi_subject_gap_missing_identity" in joined
    assert "agent_multi_subject_state_missing" in texts


def test_comparison_table_renders_one_row_per_subject() -> None:
    control = render_component(MultiSubjectResearchPanel, state=_missing_state(), **_callbacks())

    tables = [node for node in _walk(control) if isinstance(node, ft.DataTable)]
    assert len(tables) == 1
    assert len(tables[0].rows) == 2


def test_rows_are_subject_comparison_rows() -> None:
    """确认渲染输入类型与业务字段一致（避免用展示字典冒充对照行）。"""

    state = _missing_state()
    assert state.comparison is not None
    assert all(isinstance(row, SubjectComparisonRow) for row in state.comparison.rows)
    assert isinstance(state.comparison, SubjectComparison)
    assert state.comparison.rows[1].state == SUBJECT_STATE_MISSING
