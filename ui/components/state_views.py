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
from ui.i18n import get_observable_state
from ui.theme import AppColors, AppStyles


@ft.component
def EmptyState(
    icon: str = "",
    title: str = "",
    message: str = "",
    on_cta: Callable[[], None] | None = None,
    cta_text: str | None = None,
) -> ft.Container:
    """空态占位组件 (P1-3).

    Args:
        icon: ft.Icons.* 字符串 (如 ``ft.Icons.INBOX``); 空字符串不渲染图标。
        title: 标题文案 (已翻译字符串，由消费方调用 ``I18n.get``)。
        message: 描述文案 (已翻译字符串)。
        on_cta: 主操作回调 (可选); None 时不渲染 CTA 按钮。
        cta_text: CTA 按钮文案 (已翻译字符串); ``on_cta`` 非空时必填。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    def _on_click(_e: ft.ControlEvent) -> None:
        if on_cta is not None:
            on_cta()

    column_controls: list[ft.Control] = []
    if icon:
        column_controls.append(
            ft.Icon(icon, size=48, color=AppColors.TEXT_SECONDARY),
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
                icon=ft.Icons.REFRESH,
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
        padding=40,
    )


@ft.component
def ErrorState(
    icon: str = "",
    title: str = "",
    message: str = "",
    on_retry: Callable[[], None] | None = None,
    retry_text: str | None = None,
    on_cta: Callable[[], None] | None = None,
    cta_text: str | None = None,
) -> ft.Container:
    """错误态占位组件 (P1-3).

    Args:
        icon: ft.Icons.* 字符串 (如 ``ft.Icons.ERROR_OUTLINE``); 空字符串不渲染图标。
        title: 标题文案 (已翻译字符串)。
        message: 描述文案 (已翻译字符串)。
        on_retry: 重试回调 (可选); None 时不渲染重试按钮。
        retry_text: 重试按钮文案 (已翻译字符串); ``on_retry`` 非空时必填。
        on_cta: 次操作回调 (可选, 如导航到设置页); None 时不渲染 CTA 按钮。
        cta_text: CTA 按钮文案 (已翻译字符串); ``on_cta`` 非空时必填。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    def _on_retry_click(_e: ft.ControlEvent) -> None:
        if on_retry is not None:
            on_retry()

    def _on_cta_click(_e: ft.ControlEvent) -> None:
        if on_cta is not None:
            on_cta()

    column_controls: list[ft.Control] = []
    if icon:
        column_controls.append(
            ft.Icon(icon, size=48, color=AppColors.ERROR),
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
                icon=ft.Icons.SETTINGS,
                on_click=safe_on_click(_on_cta_click),
                style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
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
        padding=40,
    )


__all__ = ["EmptyState", "ErrorState"]
