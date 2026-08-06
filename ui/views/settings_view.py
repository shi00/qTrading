"""SettingsView — 声明式壳容器 (Phase C.3).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 class 子类 → ``@ft.component def SettingsView()``
- Tab 切换由 ``use_state(current_tabs)`` 驱动，条件渲染当前激活 tab
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除所有命令式生命周期回调与手动刷新方法
- 6 个 tab 均已声明式化 (``@ft.component`` + ``use_viewmodel``), 直接函数调用
- ``show_snack`` 用 ``ft.context.page`` 访问 page（try/except 守卫 RuntimeError）
- Issue #438: visited_tabs 跟踪已访问 Tab, 已访问 Tab 始终在 ``ft.Stack`` 中
  (``visible`` prop 控制显隐), 状态跨 Tab 切换保持 (与 ``app_layout._build_pages_stack``
  模式一致)。未访问 Tab 为空 Container, 避免首次构造触发 VM 构造链阻塞
  (DataSourceViewModel → AIService → litellm import 18s+)。
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_controls, safe_on_click
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
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


def _build_tabs(
    show_snack: Callable,
    current_tab: int = 0,
    visited_tabs: set[int] | None = None,
) -> list[ft.Control]:
    """构造已访问 Tab (``visible`` prop 控制显隐), 未访问 Tab 为空 Container。

    Issue #438: 用 ``visited_tabs`` 跟踪已访问 Tab, 已访问 Tab 始终在控件树中
    (``visible`` prop 控制显隐), 状态由 Flet diff 算法保持。未访问 Tab 为空
    Container, 避免首次构造触发 VM 构造链阻塞 (与 ``app_layout._build_pages_stack``
    模式一致)。

    Args:
        show_snack: snackbar 触发函数。
        current_tab: 当前激活 Tab 索引 (控制 ``visible`` prop)。
        visited_tabs: 已访问 Tab 索引集合。None 时默认 ``{current_tab}`` (惰性构造,
            向后兼容)。已访问 Tab 被构造并放入 Stack, 未访问 Tab 为空 Container。
    """
    if visited_tabs is None:
        visited_tabs = {current_tab}

    tab_factories: list[Callable[[], ft.Control]] = [
        lambda: DataSourceTab(show_snack),
        lambda: DatabaseTab(show_snack),
        lambda: AIBrainTab(show_snack),
        lambda: AutomationTab(show_snack),
        lambda: NotificationsTab(show_snack),
        lambda: SystemTab(show_snack),
    ]
    return [
        ft.Container(
            content=tab_factories[i]() if i in visited_tabs else None,
            visible=(i == current_tab),
            expand=True,
        )
        for i in range(len(tab_factories))
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
        action_text: Task 5.1 snack action 按钮文本 (None 无按钮).
        on_action: Task 5.1 snack action 回调 (action_text 非空时必填).

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
    # Task 5.1: action_text/on_action 仅在非 None 时透传 (保持无 action 场景调用签名不变)
    toast_kwargs: dict[str, object] = {"type": msg_type}
    if kwargs.get("action_text") is not None:
        toast_kwargs["action_text"] = kwargs["action_text"]
    if kwargs.get("on_action") is not None:
        toast_kwargs["on_action"] = kwargs["on_action"]
    page.show_toast(message, **toast_kwargs)  # type: ignore[untyped]  # [reason: main.py 动态挂载, ft.Page 存根未声明]


@ft.component
def SettingsView(active: bool = True) -> ft.Container:
    """Settings view — declarative shell container.

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - ``use_state(current_tab)`` 驱动 tab 切换（条件渲染）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 VM（纯 UI 容器）
    - page 在渲染时捕获 (供 _show_snack 闭包在 run_task 回调中使用)

    Issue #438: ``visited_tabs`` 跟踪已访问 Tab, 已访问 Tab 始终在 ``ft.Stack`` 中
    (``visible`` prop 控制显隐), 状态跨 Tab 切换保持 (与 ``AppLayout`` 模式一致)。

    Args:
        active: 当前 tab 是否激活 (控制副作用执行)。
    """
    current_tab, set_current_tab = ft.use_state(0)
    visited_tabs, set_visited_tabs = ft.use_state({0})
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
    # Issue #438: 已访问 Tab 构造并放入 Stack, 未访问 Tab 为空 Container (惰性构造)
    tabs = _build_tabs(_show_snack, current_tab=current_tab, visited_tabs=visited_tabs)
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
        # Issue #438: 首次访问的 Tab 加入 visited_tabs (不可变更新, 触发重渲染构造该 Tab)
        if idx not in visited_tabs:
            set_visited_tabs(visited_tabs | {idx})
        set_current_tab(idx)

    # --- Tab bar ---
    tab_buttons = [
        anchored(
            EIDS.SETTINGS.tab(key.replace("settings_tab_", "")),
            ft.Button(
                content=I18n.get(key),
                icon=icon,
                tooltip=I18n.get(key),
                data=str(i),
                on_click=safe_on_click(_on_tab_click),
                style=_get_tab_button_style(is_selected=(i == current_tab)),
            ),
        )
        for i, (key, icon) in enumerate(_TAB_CONFIG)
    ]

    tab_bar = ft.Container(
        content=ft.Row(
            safe_controls(tab_buttons),
            alignment=ft.MainAxisAlignment.START,
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        ),
        padding=ft.Padding.only(bottom=10),
    )

    # --- Header ---
    header_title = ft.Text(
        I18n.get("settings_title"),
        size=AppStyles.FONT_SIZE_XL,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    # --- Tab body (Issue #438: ft.Stack + visible prop 替代条件渲染, 已访问 Tab 状态保持) ---
    tab_body = ft.Stack(safe_controls(tabs), expand=True)

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
