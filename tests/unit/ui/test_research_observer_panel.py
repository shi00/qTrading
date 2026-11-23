"""首问/计划预览面板渲染单测（控件构建，无页面上下文）。"""

from __future__ import annotations

import flet as ft
import pytest

from ui.viewmodels.research_observer_view_model import PlanStepView, ResearchObserverState
from ui.views.research_observer_panel import (
    _build_answer_preview,
    _build_body,
    _build_composer,
    _build_plan_preview,
)

pytestmark = pytest.mark.unit


def _noop_text(_value: str) -> None:
    return None


def _noop_mode(_value: str) -> None:
    return None


def _noop() -> None:
    return None


def test_composer_disabled_without_intake_and_shows_hint() -> None:
    state = ResearchObserverState(available=True, composer_available=False)

    control = _build_composer(
        state,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
    )

    assert isinstance(control, ft.Container)
    rows = control.content.controls
    texts = [row.value for row in rows if isinstance(row, ft.Text)]
    assert any("首问入口未接线" in text for text in texts)
    field = next(row for row in rows if isinstance(row, ft.TextField))
    assert field.disabled is True


def test_composer_renders_three_modes_and_send_button() -> None:
    state = ResearchObserverState(
        available=True,
        composer_available=True,
        composer_text="研究银行股",
        composer_mode="plan",
    )

    control = _build_composer(
        state,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
    )
    assert isinstance(control, ft.Container)
    rows = control.content.controls

    field = next(row for row in rows if isinstance(row, ft.TextField))
    assert field.value == "研究银行股"
    mode_row = next(row for row in rows if isinstance(row, ft.Row) and len(row.controls) == 3)
    labels = [button.content.value for button in mode_row.controls]
    assert labels == ["讨论", "计划", "执行"]
    send_row = next(row for row in rows if isinstance(row, ft.Row) and len(row.controls) == 1)
    assert isinstance(send_row.controls[0], ft.FilledButton)


def test_plan_preview_hidden_without_plan() -> None:
    assert _build_plan_preview(ResearchObserverState(available=True), on_start_plan=_noop) is None


def test_plan_preview_lists_steps_and_start_button_only_with_reference() -> None:
    with_reference = ResearchObserverState(
        available=True,
        composer_available=True,
        plan_steps=(PlanStepView("s1", "步骤一", ()), PlanStepView("s2", "步骤二", ("s1",))),
        plan_reference=("execution-0", 1),
    )
    control = _build_plan_preview(with_reference, on_start_plan=_noop)

    assert isinstance(control, ft.Container)
    texts = [
        child.value
        for row in control.content.controls
        if isinstance(row, ft.Column)
        for child in row.controls
        if isinstance(child, ft.Text)
    ]
    assert any("步骤一" in text for text in texts)
    assert any("依赖" in text for text in texts)
    assert any(
        isinstance(row, ft.Row) and any(isinstance(c, ft.FilledButton) for c in row.controls)
        for row in control.content.controls
    )

    without_reference = ResearchObserverState(
        available=True,
        plan_steps=(PlanStepView("s1", "步骤一", ()),),
    )
    control = _build_plan_preview(without_reference, on_start_plan=_noop)
    assert control is not None
    assert not any(
        isinstance(row, ft.Row) and any(isinstance(c, ft.FilledButton) for c in row.controls)
        for row in control.content.controls
    )


def test_body_keeps_composer_visible_without_session() -> None:
    state = ResearchObserverState(available=True, composer_available=True)

    body = _build_body(
        state,
        on_draft_change=lambda _d, _t: None,
        on_submit=lambda _d, _e, _r: None,
        on_toggle_execution=lambda _e: None,
        on_select_execution=lambda _e: None,
        on_load_artifact=lambda _a, _v, _e, _r: None,
        on_load_draft=_noop,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
        on_start_plan=_noop,
    )

    assert isinstance(body, ft.Column)
    assert isinstance(body.controls[0], ft.Container)
    texts = [row.value for row in body.controls if isinstance(row, ft.Text)]
    assert any("暂无" in text or "无" in text for text in texts)


def test_answer_preview_renders_real_text_only() -> None:
    state = ResearchObserverState(available=True, last_answer_text="银行股要看息差")

    control = _build_answer_preview(state)

    assert isinstance(control, ft.Container)
    texts = [child.value for child in control.content.controls if isinstance(child, ft.Text)]
    assert "银行股要看息差" in texts


def test_body_omits_answer_preview_when_empty() -> None:
    state = ResearchObserverState(available=True, composer_available=True, session_id="s1")

    body = _build_body(
        state,
        on_draft_change=lambda _d, _t: None,
        on_submit=lambda _d, _e, _r: None,
        on_toggle_execution=lambda _e: None,
        on_select_execution=lambda _e: None,
        on_load_artifact=lambda _a, _v, _e, _r: None,
        on_load_draft=_noop,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
        on_start_plan=_noop,
    )

    labels = [
        child.value
        for row in body.controls
        if isinstance(row, ft.Container)
        for child in getattr(row.content, "controls", [])
        if isinstance(child, ft.Text)
    ]
    assert "主回合回答" not in labels
