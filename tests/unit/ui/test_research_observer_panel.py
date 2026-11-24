"""首问/计划预览面板渲染单测（控件构建，无页面上下文）。"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import flet as ft
import pytest

from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import PlanStepView, ResearchObserverState, SessionListItemView
from ui.views.research_observer_artifacts import build_comparison_entry
from ui.views.research_observer_composer import (
    build_answer_preview as _build_answer_preview,
    build_composer as _build_composer,
    build_plan_preview as _build_plan_preview,
)
from ui.views.research_observer_panel import _build_body
from ui.views.research_observer_session_list import build_session_list

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


def test_composer_renders_mcp_scope_checkboxes_and_error() -> None:
    toggled: list[str] = []
    state = ResearchObserverState(
        available=True,
        composer_available=True,
        mcp_scope_available=True,
        mcp_scope_options=(("svc-a@1", "行情服务"), ("svc-b@2", "新闻服务")),
        mcp_scope_selected=("svc-a@1",),
    )

    control = _build_composer(
        state,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
        on_toggle_mcp_scope=toggled.append,
    )
    assert isinstance(control, ft.Container)
    rows = control.content.controls
    checkboxes = [row for row in rows if isinstance(row, ft.Checkbox)]
    assert [(box.label, box.value) for box in checkboxes] == [("行情服务", True), ("新闻服务", False)]
    callback = checkboxes[1].on_change
    assert callable(callback)
    cast(Any, callback)(None)
    assert toggled == ["svc-b@2"]

    failed = _build_composer(
        ResearchObserverState(
            available=True,
            composer_available=True,
            mcp_scope_available=True,
            mcp_scope_error_key="agent_research_mcp_scope_error",
        ),
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
    )
    assert isinstance(failed, ft.Container)
    texts = [row.value for row in failed.content.controls if isinstance(row, ft.Text)]
    assert "MCP 服务目录读取失败，请稍后重试。" in texts


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


def _walk(root: Any) -> Iterator[Any]:
    """深度遍历控件树（与面板渲染结构一致的三个挂载点）。"""

    if not isinstance(root, ft.Control):
        return
    yield root
    for attr in ("content", "controls", "leading", "trailing"):
        value = getattr(root, attr, None)
        if isinstance(value, ft.Control):
            yield from _walk(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, ft.Control):
                    yield from _walk(item)


def _find_text_button(root: Any, label: str) -> ft.TextButton:
    for control in _walk(root):
        if isinstance(control, ft.TextButton):
            content = control.content
            if isinstance(content, ft.Text) and content.value == label:
                return control
    raise AssertionError(f"未找到按钮 {label}")


def _find_icon_button(root: Any, tooltip: str) -> ft.IconButton:
    for control in _walk(root):
        if isinstance(control, ft.IconButton) and control.tooltip == tooltip:
            return control
    raise AssertionError(f"未找到图标按钮 {tooltip}")


def _invoke(handler: Any, event: Any) -> None:
    """按 Flet 事件签名调用处理器（测试替身事件，避免声明式控件类型噪声）。"""

    assert handler is not None
    handler(event)


def _session(session_id: str, title: str | None = None, **overrides: Any) -> SessionListItemView:
    base: dict[str, Any] = {
        "session_id": session_id,
        "title": title,
        "updated_at": "2026-11-17T02:00:00.000000Z",
        "revision": 3,
        "pinned": False,
        "archived": False,
        "cleaned": False,
    }
    base.update(overrides)
    return SessionListItemView(**base)


def _build_list(state: ResearchObserverState, calls: dict[str, list[Any]] | None = None) -> ft.Control:
    calls = calls if calls is not None else {}
    return build_session_list(
        state,
        on_open_session=lambda session_id: calls.setdefault("open", []).append(session_id),
        on_new_session_title=lambda text: calls.setdefault("new_title", []).append(text),
        on_create_session=lambda: calls.setdefault("create", []).append(None),
        on_begin_rename=lambda session_id: calls.setdefault("begin_rename", []).append(session_id),
        on_rename_text=lambda text: calls.setdefault("rename_text", []).append(text),
        on_commit_rename=lambda: calls.setdefault("commit_rename", []).append(None),
        on_cancel_rename=lambda: calls.setdefault("cancel_rename", []).append(None),
        on_pin_session=lambda session_id: calls.setdefault("pin", []).append(session_id),
        on_archive_session=lambda session_id: calls.setdefault("archive", []).append(session_id),
    )


def test_session_list_renders_title_time_status_and_current_marker() -> None:
    state = ResearchObserverState(
        available=True,
        session_id="s-1",
        sessions=(
            _session("s-1", "研究 A"),
            _session("s-2", "研究 B", pinned=True),
            _session("s-3", None, revision=None, status_unknown=True),
        ),
    )

    control = _build_list(state)

    texts = [item.value for item in _walk(control) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_sessions") in texts
    assert "研究 A" in texts
    assert "研究 B" in texts
    assert I18n.get("agent_research_session_untitled") in texts
    assert I18n.get("agent_research_session_pinned") in texts
    assert I18n.get("agent_research_session_status_active") in texts
    assert I18n.get("agent_research_session_status_unknown") in texts
    current = [item for item in _walk(control) if isinstance(item, ft.Icon)]
    assert any(icon.tooltip == I18n.get("agent_research_session_current") for icon in current)
    assert any(
        isinstance(item, ft.TextField) and item.key == "agent_research_session_new_input" for item in _walk(control)
    )


def test_session_list_forwards_switch_pin_archive_and_new_session() -> None:
    calls: dict[str, list[Any]] = {}
    state = ResearchObserverState(
        available=True,
        session_id="s-1",
        new_session_title="待建标题",
        sessions=(_session("s-1", "研究 A"), _session("s-2", "研究 B")),
    )
    control = _build_list(state, calls)

    _invoke(
        _find_text_button(control, I18n.get("agent_research_session_switch")).on_click, SimpleNamespace(control=None)
    )
    _invoke(_find_icon_button(control, I18n.get("agent_research_session_pin")).on_click, SimpleNamespace(control=None))
    _invoke(
        _find_icon_button(control, I18n.get("agent_research_session_archive")).on_click, SimpleNamespace(control=None)
    )
    _invoke(_find_icon_button(control, I18n.get("agent_research_session_new")).on_click, SimpleNamespace(control=None))
    new_field = next(
        item
        for item in _walk(control)
        if isinstance(item, ft.TextField) and item.key == "agent_research_session_new_input"
    )
    assert new_field.value == "待建标题"
    _invoke(new_field.on_change, SimpleNamespace(control=SimpleNamespace(value="新标题")))

    assert calls["open"] == ["s-2"]
    assert calls["pin"] == ["s-1"]
    assert calls["archive"] == ["s-1"]
    assert calls["create"] == [None]
    assert calls["new_title"] == ["新标题"]


def test_session_list_rename_editor_wires_text_and_actions() -> None:
    calls: dict[str, list[Any]] = {}
    state = ResearchObserverState(
        available=True,
        session_id="s-1",
        rename_target_session_id="s-1",
        rename_text="旧标题",
        sessions=(_session("s-1", "研究 A"),),
    )
    control = _build_list(state, calls)

    field = next(
        item
        for item in _walk(control)
        if isinstance(item, ft.TextField) and item.key == "agent_research_session_rename_input"
    )
    assert field.value == "旧标题"
    _invoke(field.on_change, SimpleNamespace(control=SimpleNamespace(value="新标题")))
    _invoke(
        _find_text_button(control, I18n.get("agent_research_session_rename_confirm")).on_click,
        SimpleNamespace(control=None),
    )
    _invoke(
        _find_text_button(control, I18n.get("agent_research_session_rename_cancel")).on_click,
        SimpleNamespace(control=None),
    )

    assert calls["rename_text"] == ["新标题"]
    assert calls["commit_rename"] == [None]
    assert calls["cancel_rename"] == [None]


def test_session_list_actions_disabled_without_revision() -> None:
    state = ResearchObserverState(
        available=True,
        session_id="s-1",
        sessions=(_session("s-2", "研究 B", revision=None, status_unknown=False),),
    )
    control = _build_list(state)

    assert _find_icon_button(control, I18n.get("agent_research_session_pin")).disabled is True
    assert _find_icon_button(control, I18n.get("agent_research_session_archive")).disabled is True


def test_comparison_entry_is_disabled_honest_placeholder() -> None:
    control = build_comparison_entry()

    texts = [item.value for item in _walk(control) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_comparison") in texts
    assert I18n.get("agent_research_comparison_placeholder") in texts
    button = next(item for item in _walk(control) if isinstance(item, ft.OutlinedButton))
    assert button.disabled is True
    assert button.tooltip == I18n.get("agent_research_comparison_pending")


@pytest.mark.parametrize(
    ("module_name", "builder"),
    [
        ("ui.views.research_observer_session_list", "build_session_list"),
        ("ui.views.research_observer_composer", "build_composer"),
        ("ui.views.research_observer_conversation", "build_messages_section"),
        ("ui.views.research_observer_artifacts", "build_artifacts_section"),
        ("ui.views.research_observer_decisions", "build_decisions_section"),
        ("ui.views.research_observer_widgets", "section_card"),
    ],
)
def test_panel_partitioned_into_subview_modules(module_name: str, builder: str) -> None:
    module = importlib.import_module(module_name)
    assert callable(getattr(module, builder))
