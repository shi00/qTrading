"""研究观察面板·执行控制表子视图单测（W-01）。

覆盖：空态、动作可用/禁用与原因、retry/原因类/普通三类回调、
原因输入渲染（terminate / close_unknown）、在途提示、
控制结果三态（冲突 / 重放 / 已应用）与名额事实、原生控制面未接线提示。
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import flet as ft
import pytest

from ui.i18n import I18n
from ui.theme import AppColors
from ui.viewmodels.research_observer_view_model import (
    CONTROL_DISABLED_DISPATCHED,
    CONTROL_DISABLED_NOT_RESUMABLE,
    CONTROL_DISABLED_NO_ROUND,
    CONTROL_DISABLED_RETRY_ROOT,
    CONTROL_DISABLED_TERMINAL,
    CONTROL_LABEL_KEYS,
    ControlActionView,
    ControlResultView,
    ExecutionNodeView,
    ResearchObserverState,
    control_actions_for,
)
from ui.views.research_observer_controls import build_control_section

pytestmark = pytest.mark.unit


def _node(
    execution_id: str,
    *,
    parent_execution_id: str | None = None,
    display_state: str = "running",
    node_id: str | None = None,
    current_round_id: str | None = "r-1",
    dispatch_closed: bool = False,
    execution_kind: str = "root",
) -> ExecutionNodeView:
    return ExecutionNodeView(
        execution_id=execution_id,
        parent_execution_id=parent_execution_id,
        display_state=display_state,
        goal_coverage="partial",
        model_attempt_count=1,
        tool_call_count=0,
        active_ms=500,
        depth=0 if parent_execution_id is None else 1,
        has_children=parent_execution_id is None,
        node_id=node_id or execution_id,
        execution_kind=execution_kind,
        current_round_id=current_round_id,
        revision=3,
        dispatch_closed=dispatch_closed,
    )


def _action(kind: str, *, enabled: bool = True, reason_key: str | None = None) -> ControlActionView:
    return ControlActionView(
        kind=kind,
        label_key=CONTROL_LABEL_KEYS[kind],
        enabled=enabled,
        disabled_reason_key=None if enabled else reason_key,
    )


def _result(
    *,
    kind: str = "pause",
    display_state: str = "paused",
    applied: bool = True,
    replayed: bool = False,
    error_code: str | None = None,
    released_slot_count: int = 0,
    open_slot_count: int = 0,
    native_control_state: str = "not_wired",
) -> ControlResultView:
    return ControlResultView(
        execution_id="e-1",
        kind=kind,
        display_state=display_state,
        applied=applied,
        replayed=replayed,
        reason=None,
        error_code=error_code,
        released_slot_count=released_slot_count,
        open_slot_count=open_slot_count,
        native_control_state=native_control_state,
    )


def _build(
    state: ResearchObserverState,
    actions: Any = None,
    *,
    on_control: Any = None,
    on_retry: Any = None,
    on_prompt_reason: Any = None,
    on_reason_change: Any = None,
    on_reason_submit: Any = None,
    on_reason_cancel: Any = None,
) -> list[ft.Control]:
    return build_control_section(
        state,
        control_actions=actions or (lambda _execution_id: ()),
        on_control=on_control or (lambda _kind, _execution_id: None),
        on_retry=on_retry or (lambda _execution_id, _node_id: None),
        on_prompt_reason=on_prompt_reason or (lambda _execution_id, _kind: None),
        on_reason_change=on_reason_change or (lambda _text: None),
        on_reason_submit=on_reason_submit or (lambda: None),
        on_reason_cancel=on_reason_cancel or (lambda: None),
    )


def _iter_controls(root: Any) -> Iterator[Any]:
    if isinstance(root, (list, tuple)):
        for item in root:
            yield from _iter_controls(item)
        return
    if not isinstance(root, ft.Control):
        return
    yield root
    for attr in ("content", "controls", "leading", "trailing"):
        value = getattr(root, attr, None)
        if isinstance(value, ft.Control):
            yield from _iter_controls(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, ft.Control):
                    yield from _iter_controls(item)


def _texts(rows: list[ft.Control]) -> list[str]:
    return [cast(ft.Text, control).value for control in _iter_controls(rows) if isinstance(control, ft.Text)]


def _buttons(rows: list[ft.Control]) -> dict[str, ft.TextButton]:
    buttons: dict[str, ft.TextButton] = {}
    for control in _iter_controls(rows):
        if isinstance(control, ft.TextButton) and isinstance(control.content, ft.Text):
            buttons[str(control.content.value)] = control
    return buttons


def _buttons_named(rows: list[ft.Control], label: str) -> list[ft.TextButton]:
    """按标签取全部按钮（多节点时同一动作会重复出现，按渲染顺序返回）。"""

    return [
        cast(ft.TextButton, control)
        for control in _iter_controls(rows)
        if isinstance(control, ft.TextButton)
        and isinstance(control.content, ft.Text)
        and control.content.value == label
    ]


def _invoke(handler: Any, event: Any = None) -> None:
    assert callable(handler)
    handler(event)


class TestControlSectionStates:
    def test_empty_nodes_render_title_and_explicit_hint(self) -> None:
        rows = _build(ResearchObserverState(available=True))

        assert _texts(rows) == [
            I18n.get("agent_research_controls"),
            I18n.get("agent_research_controls_hint"),
            I18n.get("agent_research_no_executions"),
        ]

    def test_actions_render_availability_and_disabled_reasons(self) -> None:
        running = _node("e-1")
        terminal = _node("e-2", display_state="ended")

        def actions(execution_id: str) -> tuple[ControlActionView, ...]:
            node = {"e-1": running, "e-2": terminal}[execution_id]
            return control_actions_for(node, root=node)

        rows = _build(
            ResearchObserverState(available=True, session_id="s-1", execution_nodes=(running, terminal)),
            actions,
        )

        pauses = _buttons_named(rows, I18n.get("agent_research_control_pause"))
        assert [button.disabled for button in pauses] == [False, True], "运行节点可暂停，终态节点显式禁用"
        assert cast(ft.Text, pauses[0].content).color == AppColors.PRIMARY
        assert pauses[0].tooltip is None
        assert pauses[1].tooltip == I18n.get(CONTROL_DISABLED_TERMINAL)

        resumes = _buttons_named(rows, I18n.get("agent_research_control_resume"))
        assert resumes[0].disabled is True
        assert resumes[0].tooltip == I18n.get(CONTROL_DISABLED_NOT_RESUMABLE)

        withdraws = _buttons_named(rows, I18n.get("agent_research_control_withdraw"))
        assert withdraws[0].tooltip == I18n.get(CONTROL_DISABLED_DISPATCHED)
        assert _buttons_named(rows, I18n.get("agent_research_control_terminate"))[1].disabled is True
        assert "e-1 · running" in _texts(rows)
        assert "e-2 · ended" in _texts(rows)

    def test_pause_reason_when_no_round_and_retry_reason_keys(self) -> None:
        queued = _node("e-1", current_round_id=None)
        leaf = _node("e-2", parent_execution_id="e-1", display_state="failed", execution_kind="leaf")

        def actions(execution_id: str) -> tuple[ControlActionView, ...]:
            node = {"e-1": queued, "e-2": leaf}[execution_id]
            root = queued if execution_id == "e-2" else node
            return control_actions_for(node, root=root)

        rows = _build(ResearchObserverState(available=True, execution_nodes=(queued, leaf)), actions)

        pauses = _buttons_named(rows, I18n.get("agent_research_control_pause"))
        assert pauses[0].tooltip == I18n.get(CONTROL_DISABLED_NO_ROUND)
        retry = _buttons_named(rows, I18n.get("agent_research_control_retry"))
        assert len(retry) == 1
        assert retry[0].disabled is True
        assert retry[0].tooltip == I18n.get(CONTROL_DISABLED_RETRY_ROOT)

    def test_pending_disables_enabled_action_and_shows_hint(self) -> None:
        node = _node("e-1")
        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(node,), control_pending_execution_id="e-1"),
            lambda _execution_id: (_action("pause"),),
        )

        button = _buttons(rows)[I18n.get("agent_research_control_pause")]
        assert button.disabled is True, "在途命令期间禁用重复提交"
        assert I18n.get("agent_research_control_pending") in _texts(rows)


class TestControlCallbacks:
    def test_retry_forwards_root_execution_and_leaf_node_id(self) -> None:
        root = _node("e-root")
        leaf = _node("e-leaf", parent_execution_id="e-root", node_id="n-leaf", display_state="failed")
        retried: list[tuple[str, str]] = []

        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(root, leaf)),
            lambda execution_id: (_action("retry"),) if execution_id == "e-leaf" else (),
            on_retry=lambda execution_id, node_id: retried.append((execution_id, node_id)),
        )

        _invoke(_buttons(rows)[I18n.get("agent_research_control_retry")].on_click)
        assert retried == [("e-root", "n-leaf")], "重试必带根执行身份与精确节点，不在 UI 内猜范围"

    def test_retry_on_deep_node_forwards_walked_up_root(self) -> None:
        root = _node("e-root")
        mid = _node("e-mid", parent_execution_id="e-root", node_id="step-1", display_state="failed")
        deep = _node("e-deep", parent_execution_id="e-mid", node_id="step-2", display_state="failed")
        retried: list[tuple[str, str]] = []

        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(root, mid, deep)),
            lambda execution_id: (_action("retry"),) if execution_id == "e-deep" else (),
            on_retry=lambda execution_id, node_id: retried.append((execution_id, node_id)),
        )

        _invoke(_buttons(rows)[I18n.get("agent_research_control_retry")].on_click)
        assert retried == [("e-root", "step-2")], "深层叶须上溯到真实根，不能把中间执行当根"

    def test_retry_with_parent_outside_projection_falls_back_to_self(self) -> None:
        orphan = _node("e-orphan", parent_execution_id="e-gone", node_id="step-9")
        retried: list[tuple[str, str]] = []

        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(orphan,)),
            lambda _execution_id: (_action("retry"),),
            on_retry=lambda execution_id, node_id: retried.append((execution_id, node_id)),
        )

        _invoke(_buttons(rows)[I18n.get("agent_research_control_retry")].on_click)
        assert retried == [("e-orphan", "step-9")], "根不可证时交 VM 显式拒绝，不在 UI 内伪装根"

    def test_reason_actions_prompt_with_kind_and_execution(self) -> None:
        node = _node("e-1")
        prompted: list[tuple[str, str]] = []

        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(node,)),
            lambda _execution_id: (_action("terminate"), _action("close_unknown")),
            on_prompt_reason=lambda execution_id, kind: prompted.append((execution_id, kind)),
        )

        _invoke(_buttons(rows)[I18n.get("agent_research_control_terminate")].on_click)
        _invoke(_buttons(rows)[I18n.get("agent_research_control_close_unknown")].on_click)
        assert prompted == [("e-1", "terminate"), ("e-1", "close_unknown")]

    def test_plain_action_submits_kind_and_execution(self) -> None:
        node = _node("e-1")
        submitted: list[tuple[str, str]] = []

        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(node,)),
            lambda _execution_id: (_action("pause"), _action("resume"), _action("withdraw_queued")),
            on_control=lambda kind, execution_id: submitted.append((kind, execution_id)),
        )

        _invoke(_buttons(rows)[I18n.get("agent_research_control_pause")].on_click)
        _invoke(_buttons(rows)[I18n.get("agent_research_control_resume")].on_click)
        _invoke(_buttons(rows)[I18n.get("agent_research_control_withdraw")].on_click)
        assert submitted == [("pause", "e-1"), ("resume", "e-1"), ("withdraw_queued", "e-1")]

    def test_disabled_action_still_renders_click_handler_but_not_callable_via_ui(self) -> None:
        node = _node("e-1")

        rows = _build(
            ResearchObserverState(available=True, execution_nodes=(node,)),
            lambda _execution_id: (_action("resume", enabled=False, reason_key=CONTROL_DISABLED_TERMINAL),),
        )

        button = _buttons(rows)[I18n.get("agent_research_control_resume")]
        assert button.disabled is True
        assert button.tooltip == I18n.get(CONTROL_DISABLED_TERMINAL)


class TestReasonInput:
    def test_terminate_reason_input_binds_text_and_submit_callbacks(self) -> None:
        node = _node("e-1")
        changed: list[str] = []
        submitted: list[bool] = []
        cancelled: list[bool] = []

        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(node,),
                control_reason_execution_id="e-1",
                control_reason_kind="terminate",
                control_reason_text="用户终止：不再需要该路径",
            ),
            lambda _execution_id: (_action("terminate"),),
            on_reason_change=changed.append,
            on_reason_submit=lambda: submitted.append(True),
            on_reason_cancel=lambda: cancelled.append(True),
        )

        field = next(control for control in _iter_controls(rows) if isinstance(control, ft.TextField))
        assert field.key == "agent_research_control_reason_e-1"
        assert field.value == "用户终止：不再需要该路径"
        assert field.label == I18n.get("agent_research_control_terminate_reason")

        _invoke(field.on_change, SimpleNamespace(control=SimpleNamespace(value="补充证据")))
        assert changed == ["补充证据"]

        buttons = _buttons(rows)
        _invoke(buttons[I18n.get("agent_research_control_confirm")].on_click)
        _invoke(buttons[I18n.get("agent_research_control_cancel")].on_click)
        assert submitted == [True]
        assert cancelled == [True]

    def test_close_unknown_reason_uses_its_own_label_and_error_state(self) -> None:
        node = _node("e-1")
        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(node,),
                control_reason_execution_id="e-1",
                control_reason_kind="close_unknown",
                control_reason_error_key="agent_research_control_reason_required",
            ),
            lambda _execution_id: (_action("close_unknown"),),
        )

        field = next(control for control in _iter_controls(rows) if isinstance(control, ft.TextField))
        assert field.label == I18n.get("agent_research_control_close_unknown_reason")
        errors = [
            control
            for control in _iter_controls(rows)
            if isinstance(control, ft.Text) and control.color == AppColors.ERROR
        ]
        assert [error.value for error in errors] == [I18n.get("agent_research_control_reason_required")], (
            "缺原因提示必须是可读文案，不得渲染原始原因码"
        )


class TestControlResult:
    def test_conflict_result_shows_error_code_not_success(self) -> None:
        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(_node("e-1"),),
                control_result=_result(applied=False, error_code="REVISION_CONFLICT", display_state="running"),
            ),
            lambda _execution_id: (),
        )

        errors = [
            control
            for control in _iter_controls(rows)
            if isinstance(control, ft.Text) and control.color == AppColors.ERROR
        ]
        assert [error.value for error in errors] == [
            I18n.get("agent_research_control_conflict_fmt").format(code="REVISION_CONFLICT")
        ]

    def test_conflict_reason_required_renders_translated_hint(self) -> None:
        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(_node("e-1"),),
                control_result=_result(applied=False, error_code="CONTROL_REASON_REQUIRED"),
            )
        )

        errors = [
            control
            for control in _iter_controls(rows)
            if isinstance(control, ft.Text) and control.color == AppColors.ERROR
        ]
        message = I18n.get("agent_research_control_conflict_fmt").format(
            code=I18n.get("agent_research_control_reason_required")
        )
        assert [error.value for error in errors] == [message]
        assert "CONTROL_REASON_REQUIRED" not in errors[0].value, "不得把内部原因码暴露给用户"

    def test_applied_and_replayed_results_are_distinguished(self) -> None:
        node = _node("e-1")
        applied = _texts(
            _build(
                ResearchObserverState(
                    available=True,
                    execution_nodes=(node,),
                    control_result=_result(kind="terminate", display_state="ended"),
                )
            )
        )
        assert (
            I18n.get("agent_research_control_result_fmt").format(
                kind=I18n.get("agent_research_control_terminate"),
                state="ended",
                detail=I18n.get("agent_research_control_applied"),
            )
            in applied
        )

        replayed = _texts(
            _build(
                ResearchObserverState(
                    available=True,
                    execution_nodes=(node,),
                    control_result=_result(kind="terminate", display_state="ended", replayed=True),
                )
            )
        )
        assert (
            I18n.get("agent_research_control_result_fmt").format(
                kind=I18n.get("agent_research_control_terminate"),
                state="ended",
                detail=I18n.get("agent_research_control_replayed"),
            )
            in replayed
        )
        assert I18n.get("agent_research_control_applied") not in replayed

    def test_slot_facts_and_native_state_are_rendered_explicitly(self) -> None:
        node = _node("e-1")
        with_facts = _texts(
            _build(
                ResearchObserverState(
                    available=True,
                    execution_nodes=(node,),
                    control_result=_result(
                        kind="withdraw_queued",
                        display_state="queued",
                        released_slot_count=2,
                        open_slot_count=1,
                        native_control_state="wired",
                    ),
                )
            )
        )
        assert I18n.get("agent_research_control_slots_released_fmt").format(count=2) in with_facts
        assert I18n.get("agent_research_control_slots_open_fmt").format(count=1) in with_facts
        assert I18n.get("agent_research_control_native_not_wired") not in with_facts

        without_facts = _texts(
            _build(
                ResearchObserverState(
                    available=True,
                    execution_nodes=(node,),
                    control_result=_result(native_control_state="wired"),
                )
            )
        )
        assert I18n.get("agent_research_control_slots_released_fmt").format(count=0) not in without_facts
        assert I18n.get("agent_research_control_slots_open_fmt").format(count=0) not in without_facts

    def test_native_not_wired_result_states_design_choice(self) -> None:
        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(_node("e-1"),),
                control_result=_result(),
            )
        )

        assert I18n.get("agent_research_control_native_not_wired") in _texts(rows)

    @pytest.mark.parametrize(
        ("native_state", "hint_key"),
        [
            ("failed", "agent_research_control_retry_dispatch_failed"),
            ("unknown", "agent_research_control_retry_dispatch_unknown"),
        ],
    )
    def test_retry_dispatch_failed_and_unknown_are_rendered_not_as_success(
        self, native_state: str, hint_key: str
    ) -> None:
        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(_node("e-1"),),
                control_result=_result(
                    kind="retry",
                    display_state="planned",
                    native_control_state=native_state,
                ),
            )
        )

        texts = _texts(rows)
        assert I18n.get(hint_key) in texts, "未派发/投递未知必须显式说明，不静默读成成功"

    def test_retry_error_result_does_not_append_dispatch_hint(self) -> None:
        rows = _build(
            ResearchObserverState(
                available=True,
                execution_nodes=(_node("e-1"),),
                control_result=_result(
                    kind="retry",
                    applied=False,
                    error_code="CONTROL_TARGET_UNAVAILABLE",
                    native_control_state="unknown",
                ),
            )
        )

        texts = _texts(rows)
        assert I18n.get("agent_research_control_retry_dispatch_unknown") not in texts
