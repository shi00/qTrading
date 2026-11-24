"""首问/计划预览面板渲染单测（控件构建，无页面上下文）。"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import flet as ft
import pytest

from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import (
    PENDING_SCOPE_ALL,
    DispatchReasonView,
    PendingAnswerView,
    PendingDecision,
    PlanStepView,
    ResearchObserverState,
    SessionListItemView,
)
from ui.views.research_observer_artifacts import build_comparison_entry
from ui.views.research_observer_composer import (
    build_answer_preview as _build_answer_preview,
    build_composer as _build_composer,
    build_plan_preview as _build_plan_preview,
    dispatch_reason_text,
)
from ui.views.research_observer_decisions import build_decisions_section, build_pending_overview
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
    """深度遍历控件树（与面板渲染结构一致的三个挂载点；支持控件列表根）。"""

    if isinstance(root, (list, tuple)):
        for item in root:
            yield from _walk(item)
        return
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


def test_dispatch_reason_unknown_code_falls_back_to_generic_without_fake_success() -> None:
    reason = DispatchReasonView(code="unmapped_reason")

    assert dispatch_reason_text(reason) == I18n.get("agent_research_dispatch_zero")


def test_composer_renders_pending_error_and_structured_dispatch_reason() -> None:
    state = ResearchObserverState(
        available=True,
        composer_available=True,
        composer_pending=True,
        composer_error_key="agent_research_empty_draft",
        last_dispatch_reason=DispatchReasonView(code="dispatch_closed", params=(("mode", "execute"),)),
    )

    control = _build_composer(
        state,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
    )

    texts = [item.value for item in _walk(control) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_sending") in texts
    assert I18n.get("agent_research_empty_draft") in texts
    expected_reason = I18n.get("agent_research_dispatch_reason_detail_fmt").format(
        reason=I18n.get("agent_research_dispatch_reason_dispatch_closed"),
        detail="mode=execute",
    )
    assert expected_reason in texts
    field = next(item for item in _walk(control) if isinstance(item, ft.TextField))
    assert field.disabled is True
    buttons = [item for item in _walk(control) if isinstance(item, (ft.FilledButton, ft.OutlinedButton))]
    assert buttons and all(button.disabled for button in buttons), "在途时模式与发送入口都必须禁用"


def test_mcp_scope_empty_catalogue_shows_honest_hint() -> None:
    state = ResearchObserverState(
        available=True,
        composer_available=True,
        mcp_scope_available=True,
        mcp_scope_options=(),
    )

    control = _build_composer(
        state,
        on_composer_text=_noop_text,
        on_composer_mode=_noop_mode,
        on_send=_noop,
    )

    texts = [item.value for item in _walk(control) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_mcp_scope") in texts
    assert I18n.get("agent_research_mcp_scope_hint") in texts
    assert I18n.get("agent_research_mcp_scope_empty") in texts
    assert not any(isinstance(item, ft.Checkbox) for item in _walk(control)), "空选择不得渲染默认勾选"


def test_plan_preview_pending_error_and_empty_branches() -> None:
    pending = _build_plan_preview(ResearchObserverState(available=True, plan_pending=True), on_start_plan=_noop)
    assert pending is not None
    assert any(isinstance(item, ft.ProgressRing) for item in _walk(pending))

    failed = _build_plan_preview(
        ResearchObserverState(available=True, plan_error_key="agent_research_plan_missing"),
        on_start_plan=_noop,
    )
    assert failed is not None
    failed_texts = [item.value for item in _walk(failed) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_plan_missing") in failed_texts
    assert I18n.get("agent_research_plan_empty") in failed_texts

    empty_with_reference = _build_plan_preview(
        ResearchObserverState(available=True, plan_reference=("execution-1", 1)),
        on_start_plan=_noop,
    )
    assert empty_with_reference is not None
    empty_texts = [item.value for item in _walk(empty_with_reference) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_plan_empty") in empty_texts
    start = next(item for item in _walk(empty_with_reference) if isinstance(item, ft.FilledButton))
    assert start.content.value == I18n.get("agent_research_start_plan")


def _decision(decision_id: str = "d-1", *, kind: str = "user_choice", expires_at: str | None = None) -> PendingDecision:
    return PendingDecision(
        decision_id=decision_id,
        execution_id="e-1",
        round_id="r-1",
        decision_kind=kind,
        revision=2,
        created_at="2026-11-17T02:00:00.000000Z",
        expires_at=expires_at,
        question_text="是否继续？",
    )


def test_decisions_section_renders_answerable_card_and_forwards_actions() -> None:
    typed: list[tuple[str, str]] = []
    submitted: list[tuple[str, str, str]] = []
    state = ResearchObserverState(
        available=True,
        decisions=(_decision(expires_at="2026-11-17T03:00:00.000000Z"),),
        answer_drafts=(("d-1", "已有草稿"),),
        last_answer_delivery_state="delivered",
    )

    controls = build_decisions_section(
        state,
        on_draft_change=lambda decision_id, text: typed.append((decision_id, text)),
        on_submit=lambda decision_id, execution_id, round_id: submitted.append((decision_id, execution_id, round_id)),
    )

    texts = [item.value for item in _walk(controls) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_decision_expires_fmt").format(expires_at="2026-11-17T03:00:00.000000Z") in texts
    assert I18n.get("agent_research_delivery_state_fmt").format(state="delivered") in texts
    field = next(item for item in _walk(controls) if isinstance(item, ft.TextField))
    assert field.key == "agent_research_answer_d-1"
    assert field.label == I18n.get("agent_research_answer_label")
    assert field.value == "已有草稿"
    _invoke(field.on_change, SimpleNamespace(control=SimpleNamespace(value="改后的草稿")))
    submit = _find_text_button(controls, I18n.get("agent_research_submit"))
    _invoke(submit.on_click, SimpleNamespace(control=None))

    assert typed == [("d-1", "改后的草稿")]
    assert submitted == [("d-1", "e-1", "r-1")]


def test_decisions_section_readonly_authorization_and_submitting_state() -> None:
    readonly = build_decisions_section(
        ResearchObserverState(available=True, decisions=(_decision(kind="authorization"),)),
        on_draft_change=lambda _d, _t: None,
        on_submit=lambda _d, _e, _r: None,
    )
    readonly_texts = [item.value for item in _walk(readonly) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_decision_dedicated") in readonly_texts
    assert not any(isinstance(item, ft.TextField) for item in _walk(readonly))
    assert not any(isinstance(item, ft.TextButton) for item in _walk(readonly))

    submitting = build_decisions_section(
        ResearchObserverState(available=True, decisions=(_decision(),), submitting=True),
        on_draft_change=lambda _d, _t: None,
        on_submit=lambda _d, _e, _r: None,
    )
    submit = _find_text_button(submitting, I18n.get("agent_research_submitting"))
    assert submit.disabled is True


def test_decisions_section_renders_delivery_failure_reason() -> None:
    controls = build_decisions_section(
        ResearchObserverState(
            available=True,
            last_answer_delivery_state="failed",
            last_answer_delivery_reason="channel_closed",
        ),
        on_draft_change=lambda _d, _t: None,
        on_submit=lambda _d, _e, _r: None,
    )

    texts = [item.value for item in _walk(controls) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_delivery_state_fmt").format(state="failed") in texts
    assert I18n.get("agent_research_delivery_reason_fmt").format(reason="channel_closed") in texts
    assert I18n.get("agent_research_no_decisions") in texts


def _pending(
    stage: str,
    *,
    session_id: str = "s-2",
    session_title: str | None = "研究 B",
    decision_id: str = "d-2",
    delivery_state: str | None = None,
    consumption_state: str | None = None,
) -> PendingAnswerView:
    return PendingAnswerView(
        session_id=session_id,
        session_title=session_title,
        decision_id=decision_id,
        execution_id="e-2",
        round_id="r-2",
        decision_kind="user_choice",
        revision=2,
        stage=stage,
        created_at="2026-11-17T02:00:00.000000Z",
        expires_at=None,
        question_text="继续吗？",
        command_record_id="cmd-1" if stage != "answer" else None,
        delivery_state=delivery_state,
        consumption_state=consumption_state,
    )


def test_pending_overview_lists_other_sessions_and_retry_entry() -> None:
    retried: list[str] = []
    state = ResearchObserverState(
        available=True,
        session_id="s-1",
        pending_scope=PENDING_SCOPE_ALL,
        pending_count=4,
        pending_items=(
            _pending("answer", session_id="s-1", session_title="研究 A", decision_id="d-self"),
            _pending("answer", decision_id="d-other"),
            _pending("answer", session_title=None, decision_id="d-untitled"),
            _pending("delivery", decision_id="d-delivery", delivery_state="not_delivered"),
            _pending("consumption", decision_id="d-consumed", consumption_state="consumed"),
        ),
        pending_items_truncated=True,
    )

    control = build_pending_overview(state, on_retry_delivery=retried.append)

    texts = [item.value for item in _walk(control) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_pending_global") in texts
    assert I18n.get("agent_research_pending_fmt").format(count=4) in texts
    assert I18n.get("agent_research_pending_item_fmt").format(session="研究 B", kind="user_choice", revision=2) in texts
    assert (
        I18n.get("agent_research_pending_item_fmt").format(
            session=I18n.get("agent_research_pending_unknown_session"),
            kind="user_choice",
            revision=2,
        )
        in texts
    )
    assert I18n.get("agent_research_delivery_state_fmt").format(state="not_delivered") in texts
    assert I18n.get("agent_research_consumption_state_fmt").format(state="consumed") in texts
    assert I18n.get("agent_research_pending_truncated") in texts
    assert (
        I18n.get("agent_research_pending_item_fmt").format(session="研究 A", kind="user_choice", revision=2)
        not in texts
    ), "当前会话的待答复决定由决定卡呈现，概览不得重复"
    retry = _find_text_button(control, I18n.get("agent_research_delivery_retry"))
    _invoke(retry.on_click, SimpleNamespace(control=None))
    assert retried == ["d-delivery"]


def test_pending_overview_empty_scope_only() -> None:
    control = build_pending_overview(
        ResearchObserverState(available=True, session_id="s-1"),
        on_retry_delivery=_noop_text,
    )

    texts = [item.value for item in _walk(control) if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_pending_session_only") in texts
    assert I18n.get("agent_research_pending_fmt").format(count=0) in texts
    assert not any(isinstance(item, ft.TextButton) for item in _walk(control))
