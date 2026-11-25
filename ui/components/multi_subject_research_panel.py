"""MultiSubjectResearchPanel — 多标的多选与结构化对照（J05、J04、J06）。

本组件只渲染 ``MultiSubjectResearchState`` 并转发显式点击回调：

- 不 import ViewModel / 服务层（View = f(state)），不持有业务状态；
- 逐标的结论单列：缺失标的同样成行并标注缺失原因，批次显示「完整 / 部分完成」；
- 无结论时显示「尚无结论」而非 0/空结论（R21）。

入口（选股结果 / 资讯列表）负责构造候选并把「准备研究」命令交给宿主。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.multi_subject_research_view_model import (
    MultiSubjectResearchState,
    SubjectCandidate,
)

if TYPE_CHECKING:
    from services.agent_service.workflows import SubjectComparison, SubjectComparisonRow

PANEL_KEY = "multi-subject-research-panel"

#: 逐标的状态 → i18n key。
_STATE_LABEL_KEYS = {
    "complete": "agent_multi_subject_state_complete",
    "partial": "agent_multi_subject_state_partial",
    "unknown": "agent_multi_subject_state_unknown",
    "pending": "agent_multi_subject_state_pending",
    "missing": "agent_multi_subject_state_missing",
}

#: 缺失/缺口原因 → i18n key（未知原因原样展示，不隐藏）。
_GAP_LABEL_KEYS = {
    "missing_identity": "agent_multi_subject_gap_missing_identity",
    "over_limit": "agent_multi_subject_gap_over_limit",
    "no_result": "agent_multi_subject_gap_no_result",
    "prepare_failed": "agent_multi_subject_gap_prepare_failed",
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


def _gap_text(gap: str) -> str:
    key = _GAP_LABEL_KEYS.get(gap)
    return I18n.get(key) if key is not None else gap


def _candidate_row(
    candidate: SubjectCandidate,
    state: MultiSubjectResearchState,
    *,
    on_toggle: Callable[[str], None],
) -> ft.Control:
    selectable = candidate.selectable
    row: list[ft.Control] = [
        ft.Checkbox(
            key=f"multi-subject-candidate-{candidate.key}",
            value=candidate.key in state.selected_keys,
            label=candidate.display_name,
            disabled=not selectable,
            on_change=lambda _e, value=candidate.key: on_toggle(value),
        )
    ]
    if not selectable:
        reason = candidate.missing_reason or "missing_identity"
        row.append(_caption(_gap_text(reason), color=AppColors.WARNING))
    return ft.Row(row, spacing=AppStyles.SPACING_XS, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def _build_candidate_list(
    state: MultiSubjectResearchState,
    *,
    on_toggle: Callable[[str], None],
) -> ft.Control:
    if not state.candidates:
        return _caption(I18n.get("agent_multi_subject_no_candidates"))
    return ft.Container(
        content=ft.Column(
            [_candidate_row(item, state, on_toggle=on_toggle) for item in state.candidates],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
        ),
        height=180,
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=4,
        padding=AppStyles.SPACING_XS,
    )


def _build_toolbar(
    state: MultiSubjectResearchState,
    *,
    on_select_all: Callable[[], None],
    on_clear: Callable[[], None],
    on_submit: Callable[[], None],
) -> ft.Control:
    return ft.Row(
        [
            ft.TextButton(
                content=ft.Text(I18n.get("agent_multi_subject_select_all"), size=AppStyles.FONT_SIZE_CAPTION),
                on_click=lambda _e: on_select_all(),
            ),
            ft.TextButton(
                content=ft.Text(I18n.get("agent_multi_subject_clear"), size=AppStyles.FONT_SIZE_CAPTION),
                on_click=lambda _e: on_clear(),
            ),
            _caption(
                I18n.get("agent_multi_subject_selected_fmt").format(
                    selected=len(state.selected_keys), limit=state.limit
                )
            ),
            ft.FilledButton(
                content=ft.Text(I18n.get("agent_multi_subject_submit"), size=AppStyles.FONT_SIZE_CAPTION),
                disabled=not state.selected_keys,
                on_click=lambda _e: on_submit(),
            ),
        ],
        spacing=AppStyles.SPACING_XS,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _build_row_detail(row: SubjectComparisonRow) -> str:
    """逐标的行第 3 列：有结论用结论；无结论如实说「尚无结论」/缺失原因（R21）。"""

    if row.conclusion:
        return row.conclusion
    if row.gaps:
        return " · ".join(_gap_text(gap) for gap in row.gaps)
    return I18n.get("agent_multi_subject_no_conclusion")


def _build_comparison_table(comparison: SubjectComparison) -> ft.Control:
    rows: list[ft.DataRow] = []
    for row in comparison.rows:
        rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(
                        ft.Text(
                            row.display_name,
                            size=AppStyles.FONT_SIZE_CAPTION,
                            color=AppColors.TEXT_PRIMARY,
                        )
                    ),
                    ft.DataCell(
                        ft.Text(
                            I18n.get(_STATE_LABEL_KEYS.get(row.state, "agent_multi_subject_state_unknown")),
                            size=AppStyles.FONT_SIZE_CAPTION,
                            color=AppColors.TEXT_SECONDARY,
                        )
                    ),
                    ft.DataCell(
                        ft.Text(_build_row_detail(row), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_PRIMARY)
                    ),
                ]
            )
        )
    return ft.DataTable(
        columns=[
            ft.DataColumn(
                label=ft.Text(I18n.get("agent_multi_subject_column_subject"), size=AppStyles.FONT_SIZE_CAPTION)
            ),
            ft.DataColumn(
                label=ft.Text(I18n.get("agent_multi_subject_column_state"), size=AppStyles.FONT_SIZE_CAPTION)
            ),
            ft.DataColumn(
                label=ft.Text(I18n.get("agent_multi_subject_column_result"), size=AppStyles.FONT_SIZE_CAPTION)
            ),
        ],
        rows=rows,
        column_spacing=AppStyles.SPACING_MD,
    )


def _build_comparison(comparison: SubjectComparison) -> ft.Control:
    status = comparison.status
    missing = comparison.missing_objects
    if missing:
        banner = _caption(
            I18n.get("agent_multi_subject_status_partial_fmt").format(missing="、".join(missing)),
            color=AppColors.WARNING,
        )
    elif status == "complete":
        banner = _caption(I18n.get("agent_multi_subject_status_complete"), color=AppColors.SUCCESS)
    else:
        banner = _caption(I18n.get("agent_multi_subject_status_pending"), color=AppColors.TEXT_SECONDARY)
    return ft.Container(
        content=ft.Column(
            [
                _section_title("agent_multi_subject_comparison"),
                banner,
                ft.Row([_build_comparison_table(comparison)], scroll=ft.ScrollMode.AUTO),
            ],
            spacing=AppStyles.SPACING_XS,
        ),
        padding=AppStyles.SPACING_SM,
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=4,
    )


@ft.component
def MultiSubjectResearchPanel(
    state: MultiSubjectResearchState,
    *,
    on_toggle: Callable[[str], None],
    on_select_all: Callable[[], None],
    on_clear: Callable[[], None],
    on_submit: Callable[[], None],
    on_close: Callable[[], None],
) -> ft.Container:
    """多标的多选与对照面板（全选/清空/上限提示/逐标的对照）。"""

    rows: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(
                    I18n.get("agent_multi_subject_title"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
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
        ),
        _caption(I18n.get("agent_multi_subject_hint")),
        _build_candidate_list(state, on_toggle=on_toggle),
        _build_toolbar(state, on_select_all=on_select_all, on_clear=on_clear, on_submit=on_submit),
    ]
    if state.notice_key:
        color = AppColors.WARNING if state.notice_key.startswith("agent_multi_subject_partial") else AppColors.TEXT_HINT
        rows.append(_caption(I18n.get(state.notice_key), color=color))
    if state.comparison is not None:
        rows.append(_build_comparison(state.comparison))
    return ft.Container(
        key=PANEL_KEY,
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS, scroll=ft.ScrollMode.AUTO),
        padding=AppStyles.SPACING_SM,
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=4,
        bgcolor=AppColors.BACKGROUND,
    )


__all__ = ["PANEL_KEY", "MultiSubjectResearchPanel"]
