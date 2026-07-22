"""EmbeddedStatusCard — 声明式只读状态卡片组件 (P3-9).

用于 Onboarding/Settings 中 embedded 模式的只读状态显示:
- 显示 "本地数据库已自动准备" 提示
- 显示 "无需配置主机/端口/密码" 说明

CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
- 内部 VM 模式 (factory=EmbeddedStatusCardViewModel): hook 实例化 + dispose on unmount
- View 通过 use_viewmodel(factory=...) 订阅 vm.state 变化触发重渲染
- i18n 通过 ft.use_state(get_observable_state) 自动重渲染
- View 不持有业务状态 (state 全部从 VM 读取)
"""

import logging

import flet as ft

from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.embedded_status_card_view_model import (
    EmbeddedStatusCardViewModel,
)

logger = logging.getLogger(__name__)

# --- Status display config (与 external_pg_form 共享同款 mapping) ---

_STATUS_ICON_MAP = {
    "success": ft.Icons.CHECK_CIRCLE,
    "error": ft.Icons.ERROR,
    "warning": ft.Icons.WARNING,
    "info": ft.Icons.INFO,
}

_STATUS_COLOR_MAP = {
    "success": AppColors.SUCCESS,
    "error": AppColors.ERROR,
    "warning": AppColors.WARNING,
    "info": AppColors.TEXT_SECONDARY,
}


def _render_message(msg: Message | None) -> str:
    """Render a Message to localized text via I18n.get."""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


@ft.component
def EmbeddedStatusCard() -> ft.Container:
    """Embedded 模式只读状态卡片 (声明式)。

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - 内部 VM 模式: hook 实例化 EmbeddedStatusCardViewModel + dispose on unmount
    - View 通过 use_viewmodel(factory=...) 订阅 vm.state 变化触发重渲染
    - i18n 通过 ft.use_state(get_observable_state) 自动重渲染
    - View 不持有业务状态, 全部从 VM state 读取

    Returns:
        ft.Container: 含 status icon + status text + info text 的只读卡片
    """
    # --- 内部 VM 模式: hook 实例化 + dispose on unmount ---
    state, _ = use_viewmodel(factory=EmbeddedStatusCardViewModel)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Status display (driven by state.status_message / status_type) ---
    status_text = _render_message(state.status_message)
    status_color = _STATUS_COLOR_MAP.get(state.status_type, AppColors.TEXT_SECONDARY)
    status_icon_name = _STATUS_ICON_MAP.get(state.status_type, ft.Icons.INFO)

    status_icon = ft.Icon(
        status_icon_name,
        visible=status_text != "",
        size=AppStyles.FONT_SIZE_TITLE,
        color=status_color,
    )
    status_text_ctrl = ft.Text(
        status_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=status_color,
    )

    # --- Info display (driven by state.info_message) ---
    info_text = _render_message(state.info_message)
    info_text_ctrl = ft.Text(
        info_text,
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_SECONDARY,
        text_align=ft.TextAlign.CENTER,
    )

    # --- Build UI layout ---
    return ft.Container(
        content=ft.Column(
            [
                ft.Container(height=20),
                ft.Row(
                    [status_icon, status_text_ctrl],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=5,
                ),
                ft.Container(height=12),
                ft.Row(
                    [info_text_ctrl],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
