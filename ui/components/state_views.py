"""state_views — 声明式空态/错误态组件 (P1-3 批次 2).

提供可复用的 EmptyState / ErrorState 声明式组件，供 screener_view / data_view /
home_view 等消费方在数据为空或加载失败时显示统一占位 UI。

契约 (CLAUDE.md §3.2 MVVM + §3.3 声明式 UI):
- ``@ft.component`` 函数组件，无 class 子类
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 颜色全部使用 ``AppColors`` 语义 token (Layer 1 自动切换 + Layer 2 业务色)
- ``on_cta`` / ``on_retry`` 回调由消费方注入，组件不持有业务状态
"""

from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles

# GitHub Issues 入口 (ErrorState on_cta "反馈问题" 默认目标; 消费方共享避免 DRY 违反)
GITHUB_ISSUES_URL = "https://github.com/shi00/qTrading/issues"


@ft.component
def EmptyState(
    icon: str = "",
    title: str = "",
    message: str = "",
    on_cta: Callable[[], None] | None = None,
    cta_text: str | None = None,
    cta_icon: str | None = None,
) -> ft.Container:
    """空态占位组件 (P1-3).

    Args:
        icon: ft.Icons.* 字符串 (如 ``ft.Icons.INBOX``); 空字符串不渲染图标。
        title: 标题文案 (已翻译字符串，由消费方调用 ``I18n.get``)。
        message: 描述文案 (已翻译字符串)。
        on_cta: 主操作回调 (可选); None 时不渲染 CTA 按钮。
        cta_text: CTA 按钮文案 (已翻译字符串); ``on_cta`` 非空时必填。
        cta_icon: CTA 按钮图标 (可选, UX-03 P2-09); None 时不显示图标,
            传入时渲染该图标 — 由消费方按动作语义提供, 避免固定图标误导。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    def _on_click(_e: ft.ControlEvent) -> None:
        if on_cta is not None:
            on_cta()

    column_controls: list[ft.Control] = []
    if icon:
        column_controls.append(
            ft.Icon(icon, size=AppStyles.ICON_SIZE_XL, color=AppColors.TEXT_SECONDARY),
        )
    if title:
        column_controls.append(
            ft.Text(
                title,
                size=AppStyles.FONT_SIZE_HEADLINE,
                weight=ft.FontWeight.W_500,
                color=AppColors.TEXT_PRIMARY,
                text_align=ft.TextAlign.CENTER,
            ),
        )
    if message:
        column_controls.append(
            ft.Text(
                message,
                size=AppStyles.FONT_SIZE_BODY,
                color=AppColors.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            ),
        )
    if on_cta is not None and cta_text:
        column_controls.append(
            ft.TextButton(
                content=cta_text,
                icon=cta_icon,  # UX-03 (P2-09): None → 无图标; 传入 → 按动作语义渲染
                on_click=safe_on_click(_on_click),
                style=ft.ButtonStyle(color=AppColors.PRIMARY),
            ),
        )

    return ft.Container(
        content=ft.Column(
            column_controls,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=12,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=AppStyles.EMPTY_STATE_PADDING,
    )


@ft.component
def ErrorState(
    icon: str = "",
    title: str = "",
    message: str = "",
    detail: str | None = None,
    on_retry: Callable[[], None] | None = None,
    retry_text: str | None = None,
    on_cta: Callable[[], None] | None = None,
    cta_text: str | None = None,
    cta_icon: str | None = None,
) -> ft.Container:
    """错误态占位组件 (P1-3).

    Args:
        icon: ft.Icons.* 字符串 (如 ``ft.Icons.ERROR_OUTLINE``); 空字符串不渲染图标。
        title: 标题文案 (已翻译字符串)。
        message: 描述文案 (已翻译字符串)。
        detail: 错误详情 (已脱敏字符串, 由 VM 经 ``DataSanitizer.sanitize_error()`` 产出);
            None 时不渲染详情展开按钮。
        on_retry: 重试回调 (可选); None 时不渲染重试按钮。
        retry_text: 重试按钮文案 (已翻译字符串); ``on_retry`` 非空时必填。
        on_cta: 次操作回调 (可选, 如反馈问题/导航到设置页); None 时不渲染 CTA 按钮。
        cta_text: CTA 按钮文案 (已翻译字符串); ``on_cta`` 非空时必填。
        cta_icon: 次 CTA 按钮图标 (可选, UX-03 P2-09); None 时不显示图标,
            传入时渲染该图标 — 由消费方按动作语义提供, 避免固定图标误导。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)
    is_expanded, set_is_expanded = ft.use_state(False)

    def _on_retry_click(_e: ft.ControlEvent) -> None:
        if on_retry is not None:
            on_retry()

    def _on_cta_click(_e: ft.ControlEvent) -> None:
        if on_cta is not None:
            on_cta()

    def _on_toggle_detail(_e: ft.ControlEvent) -> None:
        set_is_expanded(not is_expanded)

    column_controls: list[ft.Control] = []
    if icon:
        column_controls.append(
            ft.Icon(icon, size=AppStyles.ICON_SIZE_XL, color=AppColors.ERROR),
        )
    if title:
        column_controls.append(
            ft.Text(
                title,
                size=AppStyles.FONT_SIZE_HEADLINE,
                weight=ft.FontWeight.W_500,
                color=AppColors.TEXT_PRIMARY,
                text_align=ft.TextAlign.CENTER,
            ),
        )
    if message:
        column_controls.append(
            ft.Text(
                message,
                size=AppStyles.FONT_SIZE_BODY,
                color=AppColors.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            ),
        )
    if on_retry is not None and retry_text:
        column_controls.append(
            ft.TextButton(
                content=retry_text,
                icon=ft.Icons.REFRESH,
                on_click=safe_on_click(_on_retry_click),
                style=ft.ButtonStyle(color=AppColors.PRIMARY),
            ),
        )
    if on_cta is not None and cta_text:
        column_controls.append(
            ft.TextButton(
                content=cta_text,
                icon=cta_icon,  # UX-03 (P2-09): None → 无图标; 传入 → 按动作语义渲染
                on_click=safe_on_click(_on_cta_click),
                style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
            ),
        )
    if detail:
        toggle_text = I18n.get("error_state_hide_details") if is_expanded else I18n.get("error_state_show_details")
        toggle_icon = ft.Icons.EXPAND_LESS if is_expanded else ft.Icons.EXPAND_MORE
        column_controls.append(
            ft.TextButton(
                content=toggle_text,
                icon=toggle_icon,
                on_click=safe_on_click(_on_toggle_detail),
                style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
            ),
        )
        if is_expanded:
            column_controls.append(
                ft.Container(
                    content=ft.Text(
                        detail,
                        selectable=True,
                        max_lines=10,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    width=600,
                ),
            )

    return ft.Container(
        content=ft.Column(
            column_controls,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=12,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=AppStyles.EMPTY_STATE_PADDING,
    )


__all__ = ["EmptyState", "ErrorState", "GITHUB_ISSUES_URL"]
