"""SettingsView — 声明式壳容器 (Phase C.3).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 class 子类 → ``@ft.component def SettingsView()``
- Tab 切换由 ``use_state(current_tab)`` 驱动，条件渲染当前激活 tab
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除所有命令式生命周期回调与手动刷新方法
- 3 个命令式 tabs (DataSourceTab/AIBrainTab/SystemTab) 仍命令式实例化（Phase E 待重写）
- DatabaseTab/AutomationTab/NotificationsTab 已声明式 (Phase A.2/D.4)，直接函数调用
- ``show_snack`` 用 ``ft.context.page`` 访问 page（try/except 守卫 RuntimeError）
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors
from ui.views.settings_tabs.ai_brain_tab import AIBrainTab
from ui.views.settings_tabs.automation_tab import AutomationTab, NotificationsTab
from ui.views.settings_tabs.data_source_tab import DataSourceTab
from ui.views.settings_tabs.database_tab import DatabaseTab
from ui.views.settings_tabs.system_tab import SystemTab

logger = logging.getLogger(__name__)


# Tab configuration: (i18n_key, icon)
_TAB_CONFIG = [
    ("settings_tab_data", ft.Icons.STORAGE),
    ("settings_tab_database", ft.Icons.DNS),
    ("settings_tab_ai", ft.Icons.SMART_TOY),
    ("settings_tab_tasks", ft.Icons.SCHEDULE),
    ("settings_tab_notify", ft.Icons.NOTIFICATIONS),
    ("settings_tab_system", ft.Icons.TUNE),
]


def _get_tab_button_style(is_selected: bool) -> ft.ButtonStyle:
    """Centralized tab button style factory."""
    return ft.ButtonStyle(
        color=AppColors.TEXT_ON_PRIMARY if is_selected else AppColors.TEXT_SECONDARY,
        icon_color=AppColors.TEXT_ON_PRIMARY if is_selected else AppColors.TEXT_SECONDARY,
        bgcolor=AppColors.PRIMARY if is_selected else ft.Colors.TRANSPARENT,
        elevation=0,
        shape=ft.RoundedRectangleBorder(radius=8),
        alignment=ft.Alignment.CENTER,
    )


def _build_tabs(show_snack: Callable) -> list[ft.Control]:
    """Instantiate all 6 tabs (DataSourceTab/DatabaseTab/AIBrainTab/AutomationTab/NotificationsTab/SystemTab)."""
    return [
        DataSourceTab(show_snack),
        DatabaseTab(show_snack),
        AIBrainTab(show_snack),
        AutomationTab(show_snack),
        NotificationsTab(show_snack),
        SystemTab(show_snack),
    ]


def _show_snack_impl(
    page: ft.Page | None,
    message: str,
    color: str | None = None,
    **kwargs: object,
) -> None:
    """显示 toast/snackbar (纯逻辑, 供 SettingsView 闭包与单元测试调用).

    Args:
        page: 渲染时捕获的 ft.Page 引用 (None 时静默返回).
        message: toast 文本.
        color: AppColors token 或 "error"/"success"/"warning" 字符串, 决定 msg_type.

    Note:
        page 在 SettingsView 渲染时捕获, 供 run_task 回调中使用
        (ft.context.page 在 run_task 回调中不可用, 见 SettingsView docstring).
    """
    if page is None or not hasattr(page, "show_toast"):
        logger.warning("[SettingsView] show_toast unavailable: %s", message)
        return
    msg_type = "info"
    if color == AppColors.ERROR or color == "error":
        msg_type = "error"
    elif color == AppColors.SUCCESS or color == "success":
        msg_type = "success"
    elif color == AppColors.WARNING or color == "warning":
        msg_type = "warning"
    page.show_toast(message, type=msg_type)  # type: ignore[untyped]  # [reason: main.py 动态挂载, ft.Page 存根未声明]


@ft.component
def SettingsView(active: bool = True) -> ft.Container:
    """Settings view — declarative shell container.

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - ``use_state(current_tab)`` 驱动 tab 切换（条件渲染）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 VM（纯 UI 容器）
    - page 在渲染时捕获 (供 _show_snack 闭包在 run_task 回调中使用)
    """
    current_tab, set_current_tab = ft.use_state(0)
    ft.use_state(get_observable_state)

    # --- Capture page at render time for _show_snack closure ---
    # ft.context.page 在 page.run_task 回调中不可用 (Renderer 上下文未跨 run_task 传播),
    # 在渲染时捕获 page 引用, 供异步回调中的 snackbar/toast 使用。
    try:
        _page = ft.context.page
    except RuntimeError:
        _page = None

    def _show_snack(message: str, color: str | None = None, **kwargs: object) -> None:
        _show_snack_impl(_page, message, color, **kwargs)

    # --- Build tabs ---
    tabs = _build_tabs(_show_snack)
    assert len(_TAB_CONFIG) == len(tabs), f"_TAB_CONFIG ({len(_TAB_CONFIG)}) and tabs ({len(tabs)}) length mismatch!"

    # --- Tab click handler ---
    def _on_tab_click(e: ft.ControlEvent) -> None:
        try:
            idx = int(e.control.data)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "[SettingsView] Invalid tab index data: %s, error: %s",
                e.control.data,
                exc,
                exc_info=True,
            )
            return
        if not (0 <= idx < len(tabs)):
            logger.warning("[SettingsView] Tab index out of range: %s", idx)
            return
        logger.debug("[SettingsView] Switching to tab index: %s", idx)
        set_current_tab(idx)

    # --- Tab bar ---
    tab_buttons = [
        ft.Button(
            content=I18n.get(key),
            icon=icon,
            tooltip=I18n.get(key),
            data=str(i),
            on_click=_on_tab_click,
            style=_get_tab_button_style(is_selected=(i == current_tab)),
        )
        for i, (key, icon) in enumerate(_TAB_CONFIG)
    ]

    tab_bar = ft.Container(
        content=ft.Row(
            tab_buttons,
            alignment=ft.MainAxisAlignment.START,
            spacing=10,
            scroll=ft.ScrollMode.HIDDEN,
        ),
        padding=ft.Padding.only(bottom=10),
    )

    # --- Header ---
    header_title = ft.Text(
        I18n.get("settings_title"),
        size=24,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    # --- Tab body (conditional rendering) ---
    tab_body = ft.Container(content=tabs[current_tab], expand=True)

    # --- Assembly ---
    return ft.Container(
        content=ft.Column(
            [
                header_title,
                tab_bar,
                ft.Divider(height=1, thickness=1),
                tab_body,
            ],
            expand=True,
        ),
        expand=True,
    )
