"""研究观察面板的共享小构件（标题/卡片容器/只读提示）。

拆分子视图后，样式令牌与卡片外观收敛到本模块，避免各子视图各建一套视觉体系
（ui-ux-best-practices §3.4：复用项目设计令牌）。
"""

from __future__ import annotations

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles


def section_title(key: str) -> ft.Text:
    """分区标题（统一字号/字重/颜色，View 只传 i18n key）。"""

    return ft.Text(
        I18n.get(key),
        size=AppStyles.FONT_SIZE_BODY_SM,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )


def hint_text(key: str) -> ft.Text:
    """次要说明文本（缺省/空态提示）。"""

    return ft.Text(
        I18n.get(key),
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_HINT,
    )


def error_text(value: str) -> ft.Text:
    """错误提示文本（value 已是渲染后文案，不在此处再取 i18n）。"""

    return ft.Text(
        value,
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.ERROR,
    )


def section_card(rows: list[ft.Control]) -> ft.Container:
    """分区卡片容器（统一内边距/底色/圆角/边框）。"""

    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )
