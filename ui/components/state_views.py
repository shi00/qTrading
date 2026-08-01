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
        padding=AppStyles.EMPTY_STATE_PADDING,
    )


@ft.component
def ErrorState(
    icon: str = "",
    title: str = "",
    message: str = "",
    details: str = "",
    on_retry: Callable[[], None] | None = None,
    retry_text: str | None = None,
    on_cta: Callable[[], None] | None = None,
    cta_text: str | None = None,
    on_contact_support: Callable[[], None] | None = None,
    contact_text: str | None = None,
) -> ft.Container:
    """错误态占位组件 (P1-3 + Issue #448 增强).

    Issue #448 新增:
    - ``details``: 错误详情 (可展开/折叠, 已脱敏)
    - ``on_contact_support`` / ``contact_text``: 联系支持按钮 (打开 GitHub issues)

    Args:
        icon: ft.Icons.* 字符串 (如 ``ft.Icons.ERROR_OUTLINE``); 空字符串不渲染图标。
        title: 标题文案 (已翻译字符串)。
        message: 描述文案 (已翻译字符串)。
        details: 错误详情 (已脱敏字符串); 非空时渲染展开/折叠按钮 + 详情容器。
        on_retry: 重试回调 (可选); None 时不渲染重试按钮。
        retry_text: 重试按钮文案 (已翻译字符串); ``on_retry`` 非空时必填。
        on_cta: 次操作回调 (可选, 如导航到设置页); None 时不渲染 CTA 按钮。
        cta_text: CTA 按钮文案 (已翻译字符串); ``on_cta`` 非空时必填。
        on_contact_support: 联系支持回调 (可选); None 时不渲染联系支持按钮。
        contact_text: 联系支持按钮文案 (已翻译字符串); ``on_contact_support`` 非空时必填。
    """
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)
    is_expanded, set_is_expanded = ft.use_state(False)  # details 展开/折叠状态

    def _on_retry_click(_e: ft.ControlEvent) -> None:
        if on_retry is not None:
            on_retry()

    def _on_cta_click(_e: ft.ControlEvent) -> None:
        if on_cta is not None:
            on_cta()

    def _on_toggle_details(_e: ft.ControlEvent) -> None:
        set_is_expanded(not is_expanded)

    def _on_contact_support_click(_e: ft.ControlEvent) -> None:
        if on_contact_support is not None:
            on_contact_support()

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
                icon=ft.Icons.SETTINGS,
                on_click=safe_on_click(_on_cta_click),
                style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
            ),
        )
    # Issue #448: 错误详情展开/折叠 (details 非空时渲染)
    if details:
        column_controls.append(
            ft.TextButton(
                content=(I18n.get("common_collapse") if is_expanded else I18n.get("common_error_details")),
                icon=(ft.Icons.KEYBOARD_ARROW_UP if is_expanded else ft.Icons.KEYBOARD_ARROW_DOWN),
                on_click=safe_on_click(_on_toggle_details),
                style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
            ),
        )
        if is_expanded:
            column_controls.append(
                ft.Container(
                    content=ft.Text(
                        details,
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                        selectable=True,  # 方便复制
                        max_lines=10,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    padding=ft.Padding.all(8),
                    bgcolor=AppColors.SURFACE_VARIANT,
                    border_radius=6,
                    width=500,
                ),
            )
    # Issue #448: 联系支持按钮 (on_contact_support 非空时渲染)
    if on_contact_support is not None and contact_text:
        column_controls.append(
            ft.TextButton(
                content=contact_text,
                icon=ft.Icons.HELP_OUTLINE,
                on_click=safe_on_click(_on_contact_support_click),
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
        padding=AppStyles.EMPTY_STATE_PADDING,
    )


__all__ = ["EmptyState", "ErrorState"]
