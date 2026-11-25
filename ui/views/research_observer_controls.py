"""研究观察面板·执行控制表子视图（W-01）。

只呈现「动作是否可用 + 原因」并回调；不持有状态、不直连服务（动作由 VM 提交）。
不可用动作一律显式禁用并给原因，不静默隐藏（R21）；控制结果按事实分列。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    ControlActionView,
    ControlResultView,
    ResearchObserverState,
)
from ui.views.research_observer_widgets import error_text, hint_text, section_card, section_title

#: 原因类动作（点击后进入原因输入，不直接提交）。
_REASON_ACTIONS = frozenset({"terminate", "close_unknown"})


def _action_button(
    action: ControlActionView,
    *,
    execution_id: str,
    root_execution_id: str,
    node_id: str,
    pending: bool,
    on_control: Callable[[str, str], None],
    on_retry: Callable[[str, str], None],
    on_prompt_reason: Callable[[str, str], None],
) -> ft.Control:
    disabled = pending or not action.enabled
    tooltip = I18n.get(action.disabled_reason_key) if action.disabled_reason_key is not None else None

    if action.kind == "retry":

        def on_click(_event: ft.Event[ft.TextButton]) -> None:
            on_retry(root_execution_id, node_id)

    elif action.kind in _REASON_ACTIONS:
        kind = action.kind

        def on_click(_event: ft.Event[ft.TextButton]) -> None:
            on_prompt_reason(execution_id, kind)

    else:
        kind = action.kind

        def on_click(_event: ft.Event[ft.TextButton]) -> None:
            on_control(kind, execution_id)

    return ft.TextButton(
        content=ft.Text(
            I18n.get(action.label_key),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_HINT if disabled else AppColors.PRIMARY,
        ),
        disabled=disabled,
        tooltip=tooltip,
        on_click=on_click,
    )


def _result_text(result: ControlResultView) -> ft.Text:
    """控制结果如实分列：错误码/冲突优先，其次状态与幂等/名额事实。"""

    if result.error_code is not None:
        code = (
            I18n.get("agent_research_control_reason_required")
            if result.error_code == "CONTROL_REASON_REQUIRED"
            else result.error_code
        )
        return error_text(I18n.get("agent_research_control_conflict_fmt").format(code=code))
    label = I18n.get(f"agent_research_control_{result.kind}")
    detail_key = "agent_research_control_replayed" if result.replayed else "agent_research_control_applied"
    return ft.Text(
        I18n.get("agent_research_control_result_fmt").format(
            kind=label,
            state=result.display_state,
            detail=I18n.get(detail_key),
        ),
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_PRIMARY,
    )


def build_control_section(
    state: ResearchObserverState,
    *,
    control_actions: Callable[[str], tuple[ControlActionView, ...]],
    on_control: Callable[[str, str], None],
    on_retry: Callable[[str, str], None],
    on_prompt_reason: Callable[[str, str], None],
    on_reason_change: Callable[[str], None],
    on_reason_submit: Callable[[], None],
    on_reason_cancel: Callable[[], None],
) -> list[ft.Control]:
    """执行控制表（动作/不可用原因/结果三态；不持有状态）。"""

    rows: list[ft.Control] = [section_title("agent_research_controls")]
    rows.append(hint_text("agent_research_controls_hint"))
    nodes = list(state.execution_nodes)
    if not nodes:
        rows.append(hint_text("agent_research_no_executions"))
        return rows
    roots = {node.execution_id: node for node in nodes if node.parent_execution_id is None}
    for node in nodes:
        actions = control_actions(node.execution_id)
        root = roots.get(node.parent_execution_id or "") if node.parent_execution_id else node
        pending = state.control_pending_execution_id is not None
        buttons: list[ft.Control] = []
        for action in actions:
            buttons.append(
                _action_button(
                    action,
                    execution_id=node.execution_id,
                    root_execution_id=root.execution_id if root is not None else node.execution_id,
                    node_id=node.node_id,
                    pending=pending,
                    on_control=on_control,
                    on_retry=on_retry,
                    on_prompt_reason=on_prompt_reason,
                )
            )
        row_rows: list[ft.Control] = [
            ft.Row(
                [
                    ft.Text(
                        f"{node.execution_id} · {node.display_state}",
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Row(buttons, spacing=AppStyles.SPACING_XS, wrap=True, expand=True),
                ],
                spacing=AppStyles.SPACING_SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        ]
        if state.control_reason_execution_id == node.execution_id and state.control_reason_kind is not None:
            reason_label_key = (
                "agent_research_control_terminate_reason"
                if state.control_reason_kind == "terminate"
                else "agent_research_control_close_unknown_reason"
            )
            row_rows.append(
                ft.Text(
                    I18n.get(reason_label_key),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
            row_rows.append(
                ft.TextField(
                    key=f"agent_research_control_reason_{node.execution_id}",
                    value=state.control_reason_text,
                    label=I18n.get(reason_label_key),
                    tooltip=I18n.get(reason_label_key),
                    multiline=False,
                    text_size=AppStyles.FONT_SIZE_BODY_SM,
                    on_change=lambda event: on_reason_change(event.control.value or ""),
                )
            )
            if state.control_reason_error_key is not None:
                row_rows.append(error_text(I18n.get(state.control_reason_error_key)))
            row_rows.append(
                ft.Row(
                    [
                        ft.TextButton(
                            content=ft.Text(
                                I18n.get("agent_research_control_confirm"), size=AppStyles.FONT_SIZE_BODY_SM
                            ),
                            on_click=lambda _event: on_reason_submit(),
                        ),
                        ft.TextButton(
                            content=ft.Text(
                                I18n.get("agent_research_control_cancel"), size=AppStyles.FONT_SIZE_BODY_SM
                            ),
                            on_click=lambda _event: on_reason_cancel(),
                        ),
                    ],
                    spacing=AppStyles.SPACING_XS,
                )
            )
        rows.append(section_card(row_rows))
    if state.control_pending_execution_id is not None:
        rows.append(hint_text("agent_research_control_pending"))
    result = state.control_result
    if result is not None:
        rows.append(_result_text(result))
        if result.released_slot_count:
            rows.append(
                ft.Text(
                    I18n.get("agent_research_control_slots_released_fmt").format(count=result.released_slot_count),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        if result.open_slot_count:
            rows.append(
                ft.Text(
                    I18n.get("agent_research_control_slots_open_fmt").format(count=result.open_slot_count),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        if result.native_control_state == "not_wired":
            rows.append(hint_text("agent_research_control_native_not_wired"))
    return rows


__all__ = ["build_control_section"]
