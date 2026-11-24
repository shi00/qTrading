"""ResearchComparisonPanel — 多模型对比视图（F37）。

UX P07/F37：选择两路具体连接，同一份冻结输入各自独立研究，结果可并列或按表格
对照；组状态与逐路费用只读呈现，未知/未计价显式显示为「未知」而非 0（R21）。

本组件只渲染 ``ResearchComparisonState`` 并转发显式点击回调：

- 不 import ViewModel / 服务层，不拼装请求（MVVM View = f(ViewModel.state)）。
- 不绑定任何键盘隐式路径：提交/重试/停止只能由用户对具体按钮显式点击。
- 共享 MCP 断线只呈现服务层停止计划得出的受影响 lane，已完成成果不误标。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_comparison_view_model import (
    COMPARISON_MODES,
    VIEW_MODES,
    VIEW_SIDE_BY_SIDE,
    VIEW_TABLE,
    ComparisonGroupSummary,
    ComparisonLaneRow,
    ResearchComparisonState,
)

PANEL_WIDTH = 440

_MODE_LABEL_KEYS = {
    "discuss": "agent_research_mode_discuss",
    "plan": "agent_research_mode_plan",
    "execute": "agent_research_mode_execute",
}

_VIEW_MODE_LABEL_KEYS = {
    VIEW_SIDE_BY_SIDE: "agent_comparison_view_side_by_side",
    VIEW_TABLE: "agent_comparison_view_table",
}


def _section_title(key: str) -> ft.Text:
    return ft.Text(
        I18n.get(key),
        size=AppStyles.FONT_SIZE_BODY_SM,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )


def _caption(text: str, *, color: str = AppColors.TEXT_HINT) -> ft.Text:
    return ft.Text(text, size=AppStyles.FONT_SIZE_CAPTION, color=color)


def _format_money(value: Decimal | None) -> str:
    """金额展示：未知保持「未知」，不填 0（R21）。"""

    if value is None:
        return I18n.get("agent_comparison_value_unknown")
    return I18n.get("agent_comparison_money_fmt").format(amount=str(value))


def _format_duration(active_ms: int | None) -> str:
    """耗时展示：未知保持「未知」；真实 0ms 也如实显示。"""

    if active_ms is None:
        return I18n.get("agent_comparison_value_unknown")
    if active_ms < 1000:
        return I18n.get("agent_comparison_duration_ms_fmt").format(ms=active_ms)
    return I18n.get("agent_comparison_duration_s_fmt").format(seconds=f"{active_ms / 1000:.1f}")


def _lane_cost_text(lane: ComparisonLaneRow) -> str:
    parts = [I18n.get(lane.cost_key)]
    if lane.known_cny is not None:
        parts.append(I18n.get("agent_comparison_cost_known_fmt").format(amount=_format_money(lane.known_cny)))
    if lane.reserved_cny is not None and lane.reserved_cny > 0:
        parts.append(I18n.get("agent_comparison_cost_reserved_fmt").format(amount=_format_money(lane.reserved_cny)))
    if lane.unpriced_attempts is not None and lane.unpriced_attempts > 0:
        parts.append(I18n.get("agent_comparison_cost_unpriced_fmt").format(count=lane.unpriced_attempts))
    return " · ".join(parts)


def _lane_impact_text(lane: ComparisonLaneRow) -> str | None:
    if lane.mcp_impact_key is None:
        return None
    return I18n.get(lane.mcp_impact_key)


def _build_header(state: ResearchComparisonState, *, on_close: Callable[[], None]) -> ft.Control:
    return ft.Row(
        [
            ft.Text(
                I18n.get("agent_comparison_title"),
                size=AppStyles.FONT_SIZE_XL,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
                expand=True,
            ),
            ft.IconButton(
                icon=ft.Icons.CLOSE,
                icon_size=AppStyles.FONT_SIZE_BODY_SM,
                tooltip=I18n.get("common_close"),
                on_click=lambda _e: on_close(),
            ),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _build_connection_dropdown(
    state: ResearchComparisonState,
    slot: str,
    *,
    on_lane: Callable[[str, str | None], None],
) -> ft.Control:
    """一路连接选择：不合法连接带原因后缀，选中即由 VM 明确拒绝。"""

    options: list[ft.dropdown.Option] = []
    for option in state.connections:
        label = option.display_name
        if not option.eligible:
            reason = I18n.get(option.reject_key or "agent_comparison_connection_unknown")
            label = I18n.get("agent_comparison_option_unavailable_fmt").format(name=label, reason=reason)
        options.append(ft.dropdown.Option(key=option.connection_id, text=label))
    value = state.lane_a if slot == "a" else state.lane_b
    return ft.Dropdown(
        key=f"agent_comparison_lane_{slot}",
        value=value,
        options=options,
        label=I18n.get("agent_comparison_lane_a" if slot == "a" else "agent_comparison_lane_b"),
        hint_text=I18n.get("agent_comparison_connection_placeholder"),
        width=AppStyles.CONTROL_WIDTH_MD,
        text_size=AppStyles.FONT_SIZE_BODY_SM,
        disabled=not state.available,
        on_select=lambda e: on_lane(slot, e.control.value or None),
    )


def _build_composer(
    state: ResearchComparisonState,
    *,
    on_lane: Callable[[str, str | None], None],
    on_text: Callable[[str], None],
    on_mode: Callable[[str], None],
    on_submit: Callable[[], None],
) -> ft.Control:
    """两路连接 + 共同输入（同一份正文/模式一次冻结，各路独立执行）。"""

    rows: list[ft.Control] = [
        _section_title("agent_comparison_composer"),
        ft.Row(
            [
                _build_connection_dropdown(state, "a", on_lane=on_lane),
                _build_connection_dropdown(state, "b", on_lane=on_lane),
            ],
            spacing=AppStyles.SPACING_SM,
            wrap=True,
        ),
        ft.TextField(
            key="agent_comparison_text_input",
            value=state.text,
            label=I18n.get("agent_comparison_text_label"),
            hint_text=I18n.get("agent_comparison_text_hint"),
            multiline=True,
            min_lines=1,
            max_lines=4,
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            disabled=not state.available or state.submitting,
            on_change=lambda e: on_text(e.control.value or ""),
        ),
    ]
    mode_row: list[ft.Control] = []
    for mode in COMPARISON_MODES:
        selected = state.mode == mode
        button_cls = ft.FilledButton if selected else ft.OutlinedButton
        mode_row.append(
            button_cls(
                content=ft.Text(I18n.get(_MODE_LABEL_KEYS[mode]), size=AppStyles.FONT_SIZE_CAPTION),
                disabled=not state.available or state.submitting,
                on_click=lambda _e, value=mode: on_mode(value),
            )
        )
    rows.append(ft.Row(mode_row, spacing=AppStyles.SPACING_XS, wrap=True))
    submit_label = "agent_comparison_submitting" if state.submitting else "agent_comparison_submit"
    rows.append(
        ft.Row(
            [
                ft.FilledButton(
                    content=ft.Text(I18n.get(submit_label), size=AppStyles.FONT_SIZE_BODY_SM),
                    disabled=not state.available or state.submitting,
                    on_click=lambda _e: on_submit(),
                )
            ],
            alignment=ft.MainAxisAlignment.END,
        )
    )
    if state.selection_error_key:
        rows.append(_caption(I18n.get(state.selection_error_key), color=AppColors.ERROR))
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _build_group_summary(summary: ComparisonGroupSummary) -> ft.Control:
    counts = I18n.get("agent_comparison_group_counts_fmt").format(
        complete=summary.complete_count,
        partial=summary.partial_count,
        failed=summary.failed_count,
        pending=summary.pending_count,
        not_started=summary.not_started_count,
        unconfirmed=summary.unconfirmed_count,
    )
    cost_parts = [I18n.get(summary.cost_key)]
    if summary.known_cny is not None:
        cost_parts.append(I18n.get("agent_comparison_cost_known_fmt").format(amount=_format_money(summary.known_cny)))
    if summary.reserved_cny is not None and summary.reserved_cny > 0:
        cost_parts.append(
            I18n.get("agent_comparison_cost_reserved_fmt").format(amount=_format_money(summary.reserved_cny))
        )
    if summary.unpriced_attempts > 0:
        cost_parts.append(I18n.get("agent_comparison_cost_unpriced_fmt").format(count=summary.unpriced_attempts))
    return ft.Container(
        content=ft.Column(
            [
                _section_title("agent_comparison_group"),
                _caption(f"{I18n.get(summary.state_key)} · {I18n.get(summary.coverage_key)}"),
                _caption(counts),
                _caption(" · ".join(cost_parts)),
            ],
            spacing=AppStyles.SPACING_XS,
        ),
        padding=AppStyles.SPACING_SM,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _retry_button(
    lane: ComparisonLaneRow,
    state: ResearchComparisonState,
    *,
    on_retry_lane: Callable[[str], None],
) -> ft.Control:
    busy = state.retrying_lane is not None or state.stopping
    label = "agent_comparison_retrying" if state.retrying_lane == lane.lane_ref else "agent_comparison_retry_lane"
    return ft.TextButton(
        content=ft.Text(I18n.get(label), size=AppStyles.FONT_SIZE_CAPTION),
        disabled=busy or not lane.retryable,
        tooltip=None if lane.retryable else I18n.get("agent_comparison_retry_unavailable"),
        on_click=lambda _e, value=lane.lane_ref: on_retry_lane(value),
    )


def _build_lane_card(
    lane: ComparisonLaneRow,
    state: ResearchComparisonState,
    *,
    on_retry_lane: Callable[[str], None],
) -> ft.Control:
    details: list[ft.Control] = [
        ft.Text(
            lane.display_name,
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        _caption(I18n.get(lane.state_key), color=AppColors.TEXT_SECONDARY),
        _caption(I18n.get(lane.reason_key)),
        _caption(f"{I18n.get('agent_comparison_cost_label')}: {_lane_cost_text(lane)}"),
        _caption(f"{I18n.get('agent_comparison_duration_label')}: {_format_duration(lane.active_ms)}"),
    ]
    if lane.gap_key is not None:
        details.append(_caption(I18n.get(lane.gap_key), color=AppColors.WARNING))
    impact_text = _lane_impact_text(lane)
    if impact_text is not None:
        details.append(_caption(f"{I18n.get('agent_comparison_impact_label')}: {impact_text}"))
    details.append(_retry_button(lane, state, on_retry_lane=on_retry_lane))
    return ft.Container(
        content=ft.Column(details, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        width=AppStyles.CONTROL_WIDTH_MD,
        border_radius=4,
        bgcolor=AppColors.CARD_BG,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _build_side_by_side(
    state: ResearchComparisonState,
    *,
    on_retry_lane: Callable[[str], None],
) -> ft.Control:
    cards = [_build_lane_card(lane, state, on_retry_lane=on_retry_lane) for lane in state.lanes]
    return ft.Row(cards, spacing=AppStyles.SPACING_SM, wrap=True, vertical_alignment=ft.CrossAxisAlignment.START)


def _build_table(
    state: ResearchComparisonState,
    *,
    on_retry_lane: Callable[[str], None],
) -> ft.Control:
    columns = [
        ft.DataColumn(label=ft.Text(I18n.get("agent_comparison_column_lane"), size=AppStyles.FONT_SIZE_CAPTION)),
        ft.DataColumn(label=ft.Text(I18n.get("agent_comparison_column_status"), size=AppStyles.FONT_SIZE_CAPTION)),
        ft.DataColumn(label=ft.Text(I18n.get("agent_comparison_column_cost"), size=AppStyles.FONT_SIZE_CAPTION)),
        ft.DataColumn(label=ft.Text(I18n.get("agent_comparison_column_duration"), size=AppStyles.FONT_SIZE_CAPTION)),
        ft.DataColumn(label=ft.Text(I18n.get("agent_comparison_column_impact"), size=AppStyles.FONT_SIZE_CAPTION)),
        ft.DataColumn(label=ft.Text(I18n.get("agent_comparison_column_action"), size=AppStyles.FONT_SIZE_CAPTION)),
    ]
    rows: list[ft.DataRow] = []
    for lane in state.lanes:
        impact_text = _lane_impact_text(lane)
        rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.Text(lane.display_name, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_PRIMARY)
                    ),
                    ft.DataCell(
                        ft.Text(
                            I18n.get(lane.state_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY
                        )
                    ),
                    ft.DataCell(ft.Text(_lane_cost_text(lane), size=AppStyles.FONT_SIZE_CAPTION)),
                    ft.DataCell(ft.Text(_format_duration(lane.active_ms), size=AppStyles.FONT_SIZE_CAPTION)),
                    ft.DataCell(
                        ft.Text(
                            impact_text or I18n.get("agent_comparison_impact_unknown"), size=AppStyles.FONT_SIZE_CAPTION
                        )
                    ),
                    ft.DataCell(_retry_button(lane, state, on_retry_lane=on_retry_lane)),
                ]
            )
        )
    return ft.Row(
        [ft.DataTable(columns=columns, rows=rows, column_spacing=AppStyles.SPACING_MD)],
        scroll=ft.ScrollMode.AUTO,
    )


def _build_actions(
    state: ResearchComparisonState,
    *,
    on_refresh: Callable[[], None],
    on_stop: Callable[[], None],
    on_mcp_disconnect: Callable[[], None],
) -> ft.Control:
    return ft.Row(
        [
            ft.OutlinedButton(
                content=ft.Text(I18n.get("agent_comparison_refresh"), size=AppStyles.FONT_SIZE_CAPTION),
                disabled=state.loading,
                on_click=lambda _e: on_refresh(),
            ),
            ft.OutlinedButton(
                content=ft.Text(
                    I18n.get("agent_comparison_stopping" if state.stopping else "agent_comparison_stop"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                ),
                disabled=state.stopping,
                on_click=lambda _e: on_stop(),
            ),
            ft.TextButton(
                content=ft.Text(I18n.get("agent_comparison_mcp_report"), size=AppStyles.FONT_SIZE_CAPTION),
                on_click=lambda _e: on_mcp_disconnect(),
            ),
        ],
        spacing=AppStyles.SPACING_XS,
        wrap=True,
    )


@ft.component
def ResearchComparisonPanel(
    state: ResearchComparisonState,
    *,
    on_close: Callable[[], None],
    on_lane: Callable[[str, str | None], None],
    on_text: Callable[[str], None],
    on_mode: Callable[[str], None],
    on_view_mode: Callable[[str], None],
    on_submit: Callable[[], None],
    on_refresh: Callable[[], None],
    on_retry_lane: Callable[[str], None],
    on_stop: Callable[[], None],
    on_mcp_disconnect: Callable[[], None],
) -> ft.Container:
    """多模型对比面板（并列 / 表格两种对照，动作全部显式点击）。"""

    rows: list[ft.Control] = [_build_header(state, on_close=on_close)]
    if not state.available:
        rows.append(
            ft.Text(
                I18n.get(state.unavailable_reason_key or "agent_comparison_not_wired"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
        return _panel_container(rows)

    rows.append(
        _build_composer(
            state,
            on_lane=on_lane,
            on_text=on_text,
            on_mode=on_mode,
            on_submit=on_submit,
        )
    )
    if state.error_key:
        error_text = I18n.get(state.error_key)
        if state.error_code:
            error_text = f"{error_text} ({state.error_code})"
        rows.append(_caption(error_text, color=AppColors.ERROR))
    if state.message_key:
        rows.append(_caption(I18n.get(state.message_key)))
    if state.group is not None:
        rows.append(_build_group_summary(state.group))
        view_row: list[ft.Control] = []
        for view_mode in VIEW_MODES:
            selected = state.view_mode == view_mode
            button_cls = ft.FilledButton if selected else ft.OutlinedButton
            view_row.append(
                button_cls(
                    content=ft.Text(I18n.get(_VIEW_MODE_LABEL_KEYS[view_mode]), size=AppStyles.FONT_SIZE_CAPTION),
                    on_click=lambda _e, value=view_mode: on_view_mode(value),
                )
            )
        rows.append(ft.Row(view_row, spacing=AppStyles.SPACING_XS, wrap=True))
        if state.lanes:
            if state.view_mode == VIEW_TABLE:
                rows.append(_build_table(state, on_retry_lane=on_retry_lane))
            else:
                rows.append(_build_side_by_side(state, on_retry_lane=on_retry_lane))
        rows.append(_build_actions(state, on_refresh=on_refresh, on_stop=on_stop, on_mcp_disconnect=on_mcp_disconnect))
    else:
        rows.append(_caption(I18n.get("agent_comparison_no_group")))
    return _panel_container(rows)


def _panel_container(rows: list[ft.Control]) -> ft.Container:
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_SM, scroll=ft.ScrollMode.AUTO),
        width=PANEL_WIDTH,
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.SURFACE,
        border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
    )


__all__ = ["PANEL_WIDTH", "ResearchComparisonPanel"]
