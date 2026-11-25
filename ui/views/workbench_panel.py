"""研究工作台（非模态，四视角）— W-18。

四视角：概览 / 证据 / 结论 / 配置。视角切换只换渲染分支，各视角草稿与选中态由
``ResearchWorkbenchViewModel`` 的不可变 state 承担，切换后原地恢复（不由控件
临时状态承载）。

显式三态：
- 未接线：呈现 ``unavailable_key``（研究服务未装配的原因），不渲染空表冒充可用；
- 读取失败：呈现 ``error_key``，已有内容照常展示；
- 确实为空：呈现该视角的空态文案，与「未接线/失败」区分（R21）。

本文件不修改研究观察面板既有分区文件；数据经 ``ResearchObserverState`` 只读投影。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import ResearchObserverState
from ui.viewmodels.research_workbench_view_model import (
    CONFIG_ENTRY_HINT_KEY,
    PERSPECTIVE_CONFIG,
    PERSPECTIVE_ORDER,
    PERSPECTIVE_OVERVIEW,
    PERSPECTIVE_TITLE_KEYS,
    VALUE_MISSING_KEY,
    ResearchWorkbenchState,
    WorkbenchFact,
    WorkbenchPerspectiveView,
    WorkbenchRow,
    build_perspective_view,
)
from ui.views.workbench_command_menu import build_command_reference_menu

PANEL_WIDTH = 380


def _noop_text(_value: str) -> None:
    return None


def _noop_id(_row_id: str | None) -> None:
    return None


def _noop() -> None:
    return None


def _fact_value_text(fact: WorkbenchFact) -> str:
    """事实值渲染：i18n 值 / 原样值 / 显式「未提供」，不填占位业务值。"""
    if fact.value_key is not None:
        return I18n.get(fact.value_key)
    if fact.literal is not None:
        return fact.literal
    return I18n.get(VALUE_MISSING_KEY)


def build_facts_section(facts: tuple[WorkbenchFact, ...]) -> ft.Column:
    """事实列表（label → value），供概览与配置视角复用。"""
    rows: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(I18n.get(fact.label_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(
                    _fact_value_text(fact),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_HINT if fact.missing else AppColors.TEXT_PRIMARY,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
        )
        for fact in facts
    ]
    return ft.Column(rows, spacing=AppStyles.SPACING_XS)


def build_rows_section(
    rows: tuple[WorkbenchRow, ...],
    *,
    on_select: Callable[[str], None],
) -> ft.Column:
    """证据行列表：点选只改选中态（不触发取数）。"""
    controls: list[ft.Control] = [
        ft.TextButton(
            content=ft.Column(
                [
                    ft.Text(
                        row.title,
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.PRIMARY if row.selected else AppColors.TEXT_PRIMARY,
                    ),
                    ft.Text(row.subtitle or "", size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                ],
                spacing=2,
            ),
            on_click=safe_on_click(lambda _e, row_id=row.row_id: on_select(row_id)),
        )
        for row in rows
    ]
    return ft.Column(controls, spacing=AppStyles.SPACING_XS)


def build_perspective_content(
    view: WorkbenchPerspectiveView,
    *,
    on_select_evidence: Callable[[str], None],
) -> ft.Control:
    """单视角内容：未接线 / 失败 / 空态 / 事实与行。"""
    if view.unavailable_key is not None:
        return ft.Column(
            [
                ft.Text(I18n.get(view.unavailable_key), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.WARNING),
                *(
                    [ft.Text(I18n.get(view.error_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.ERROR)]
                    if view.error_key
                    else []
                ),
            ],
            spacing=AppStyles.SPACING_XS,
        )

    blocks: list[ft.Control] = []
    if view.error_key:
        blocks.append(ft.Text(I18n.get(view.error_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.ERROR))
    if view.empty_key is not None:
        blocks.append(ft.Text(I18n.get(view.empty_key), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_HINT))
    if view.facts:
        blocks.append(build_facts_section(view.facts))
    if view.rows:
        blocks.append(build_rows_section(view.rows, on_select=on_select_evidence))
    if view.perspective == PERSPECTIVE_CONFIG:
        blocks.append(
            ft.Text(I18n.get(CONFIG_ENTRY_HINT_KEY), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT)
        )
    return ft.Column(blocks, spacing=AppStyles.SPACING_SM)


def build_perspective_tabs(
    current: str,
    *,
    on_perspective: Callable[[str], None],
) -> ft.Row:
    """四视角切换按钮（当前视角高亮；只切换渲染分支）。"""
    buttons: list[ft.Control] = [
        ft.Button(
            content=I18n.get(PERSPECTIVE_TITLE_KEYS[perspective]),
            data=perspective,
            on_click=safe_on_click(lambda _e, key=perspective: on_perspective(key)),
            style=AppStyles.primary_button() if perspective == current else AppStyles.outline_button(),
        )
        for perspective in PERSPECTIVE_ORDER
    ]
    return ft.Row(buttons, spacing=AppStyles.SPACING_XS)


def build_workbench_body(
    state: ResearchObserverState,
    workbench_state: ResearchWorkbenchState,
    *,
    on_perspective: Callable[[str], None],
    on_overview_query: Callable[[str], None],
    on_select_evidence: Callable[[str], None],
    on_command: Callable[[], None] = _noop,
    on_reference: Callable[[], None] = _noop,
    on_skill: Callable[[], None] = _noop,
    on_mcp: Callable[[], None] = _noop,
) -> ft.Control:
    """工作台主体（可测试的纯构造，不含 hooks）。"""
    view = build_perspective_view(
        state,
        workbench_state.perspective,
        selected_id=workbench_state.evidence_selected_id,
    )
    blocks: list[ft.Control] = [build_perspective_tabs(workbench_state.perspective, on_perspective=on_perspective)]

    if workbench_state.perspective == PERSPECTIVE_OVERVIEW:
        title = ft.Text(
            I18n.get("agent_workbench_query_label"),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_SECONDARY,
        )
        query_row = ft.Row(
            [
                ft.TextField(
                    value=workbench_state.overview_query,
                    hint_text=I18n.get("agent_workbench_query_hint"),
                    expand=True,
                    dense=True,
                    on_change=lambda e: on_overview_query(str(e.control.value)),
                ),
                build_command_reference_menu(
                    on_command=on_command,
                    on_reference=on_reference,
                    on_skill=on_skill,
                    on_mcp=on_mcp,
                ),
            ],
            spacing=AppStyles.SPACING_XS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        blocks.extend([title, query_row])

    blocks.append(build_perspective_content(view, on_select_evidence=on_select_evidence))
    return ft.Column(blocks, spacing=AppStyles.SPACING_SM, scroll=ft.ScrollMode.AUTO, expand=True)


@ft.component
def WorkbenchPanel(
    state: ResearchObserverState,
    workbench_state: ResearchWorkbenchState,
    *,
    on_close: Callable[[], None] = _noop,
    on_perspective: Callable[[str], None] = _noop_text,
    on_overview_query: Callable[[str], None] = _noop_text,
    on_select_evidence: Callable[[str | None], None] = _noop_id,
    on_command: Callable[[], None] = _noop,
    on_reference: Callable[[], None] = _noop,
    on_skill: Callable[[], None] = _noop,
    on_mcp: Callable[[], None] = _noop,
) -> ft.Container:
    """非模态研究工作台外壳（标题 / 关闭 / 四视角主体）。"""
    header = ft.Row(
        [
            ft.Text(
                I18n.get("agent_workbench_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
                expand=True,
            ),
            ft.IconButton(
                icon=ft.Icons.CLOSE,
                tooltip=I18n.get("common_close"),
                on_click=safe_on_click(lambda _e: on_close()),
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )
    body = build_workbench_body(
        state,
        workbench_state,
        on_perspective=on_perspective,
        on_overview_query=on_overview_query,
        on_select_evidence=lambda row_id: on_select_evidence(row_id),
        on_command=on_command,
        on_reference=on_reference,
        on_skill=on_skill,
        on_mcp=on_mcp,
    )
    return ft.Container(
        content=ft.Column([header, ft.Divider(height=1, color=AppColors.BORDER), body], spacing=AppStyles.SPACING_SM),
        width=PANEL_WIDTH,
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.SURFACE,
        border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
    )
