"""app_layout — 声明式组件 (Phase F.4).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 ``class AppLayout(PageRefMixin, ft.Container)`` → ``@ft.component def AppLayout()``
- 移除 PageRefMixin / _view_cache / did_mount / will_unmount / 防抖级联 / locale 命令式刷新 / update_theme / change_tab / run_strategy_from_home
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 状态驱动: current_tab / nav_collapsed 用 ``use_state`` (纯 UI 状态, YAGNI 不建 VM)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 子视图直接函数调用消费 (HomeView()/ScreenerView()/...), 无 use_ref cache
- resize 用 ``use_effect`` + ``page.on_resize`` (防抖 + state 更新触发重渲染)
- 异步任务: ``page.run_task`` 调度; R2 CancelledError 必须 raise
- PageRefMixin 兼容桩已在 Phase G.3 删除 (声明式改造收官)
"""

import asyncio
import logging
import typing
from enum import IntEnum

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_attr,
    safe_controls,
    safe_on_change,
    safe_on_click,
)
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.theme import AppColors
from ui.views.backtest_view import BacktestView
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.task_center_view import TaskCenterView
from ui.views.viewport_state import ViewportState
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)

# Tab 切换防抖 (ms) — 快速连续点击导航时, 最后一次点击生效
DEBOUNCE_MS = 50
# Resize 防抖 (ms) — 窗口拖拽时, 停止后触发一次重渲染
RESIZE_DEBOUNCE_MS = 100


class NavTabs(IntEnum):
    MARKET = 0
    SCREENER = 1
    BACKTEST = 2
    DATA = 3
    TASKS = 4
    SETTINGS = 5


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


@ft.component
def _build_pages_stack(current_tab: int, viewport: ViewportState) -> ft.Stack:
    """构造所有页面控件的 ``ft.Stack`` (``visible`` prop 控制显示/隐藏)。

    项目内存硬约束 #34: state-driven rendering (ft.Stack + visible prop)
    替代条件渲染 (if/else 创建不同控件)。所有页面控件预先创建并放入 Stack,
    通过 ``visible`` prop 切换显示, 不再动态创建/销毁控件。

    声明式范式: 每次重渲染重新构造控件树, 由 Flet diff 算法决定实际 DOM 更新。
    子视图内部用 ``use_state``/``use_viewmodel`` 持久化自身状态, 重建不丢失。

    Args:
        current_tab: 当前激活的 NavTabs 值, 控制 visible prop。
        viewport: AppLayout 维护的窗口尺寸快照, 下发给所有子视图 (Phase 6.2 P2-1)。
    """
    pages = [
        ft.Container(
            content=HomeView(active=current_tab == NavTabs.MARKET, viewport=viewport),
            expand=True,
            visible=current_tab == NavTabs.MARKET,
        ),
        ft.Container(
            content=ScreenerView(active=current_tab == NavTabs.SCREENER, viewport=viewport),
            expand=True,
            visible=current_tab == NavTabs.SCREENER,
        ),
        ft.Container(
            content=BacktestView(active=current_tab == NavTabs.BACKTEST, viewport=viewport),
            expand=True,
            visible=current_tab == NavTabs.BACKTEST,
        ),
        ft.Container(
            content=DataExplorerView(active=current_tab == NavTabs.DATA, viewport=viewport),
            expand=True,
            visible=current_tab == NavTabs.DATA,
        ),
        ft.Container(
            content=TaskCenterView(active=current_tab == NavTabs.TASKS, viewport=viewport),
            expand=True,
            visible=current_tab == NavTabs.TASKS,
        ),
        ft.Container(
            content=SettingsView(active=current_tab == NavTabs.SETTINGS, viewport=viewport),
            expand=True,
            visible=current_tab == NavTabs.SETTINGS,
        ),
    ]
    return ft.Stack(safe_controls(pages), expand=True)


def _build_nav_destinations() -> list[ft.NavigationRailDestination]:
    """构造导航栏目的地列表 (i18n 变化时由组件重渲染自动刷新)。"""
    nav_items = [
        (ft.Icons.DASHBOARD_OUTLINED, ft.Icons.DASHBOARD, "nav_market"),
        (ft.Icons.FILTER_ALT_OUTLINED, ft.Icons.FILTER_ALT, "nav_screener"),
        (ft.Icons.ASSESSMENT_OUTLINED, ft.Icons.ASSESSMENT, "nav_backtest"),
        (ft.Icons.STORAGE_OUTLINED, ft.Icons.STORAGE_ROUNDED, "nav_data"),
        (ft.Icons.FORMAT_LIST_BULLETED_OUTLINED, ft.Icons.FORMAT_LIST_BULLETED, "nav_tasks"),
        (ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS, "nav_settings"),
    ]
    return [
        ft.NavigationRailDestination(
            icon=icon,
            selected_icon=selected_icon,
            label=ft.Text(
                I18n.get(label_key),
                size=12,
                weight=ft.FontWeight.BOLD,
            ),
        )
        for icon, selected_icon, label_key in nav_items
    ]


@ft.component
def AppLayout() -> ft.Container:
    """主应用布局 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 状态驱动: current_tab / nav_collapsed 用 ``use_state`` (纯 UI 状态, YAGNI 不建 VM)
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 子视图直接函数调用消费 (无 use_ref cache), 每次重渲染重新构造
    - resize 用 ``use_effect`` + ``page.on_resize`` (防抖 + state 更新触发重渲染)
    - 异步任务: ``page.run_task`` 调度; R2 CancelledError 必须 raise
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Pure UI state ---
    current_tab, set_current_tab = ft.use_state(NavTabs.MARKET)
    nav_collapsed, set_nav_collapsed = ft.use_state(False)
    # 窗口尺寸快照 (resize 事件驱动, 触发重渲染让子视图按新尺寸布局)
    window_size, set_window_size = ft.use_state((0.0, 0.0))

    # --- Tab 切换 (防抖, R2: CancelledError 必须 raise) ---
    async def _do_tab_switch(new_tab: int) -> None:
        try:
            await asyncio.sleep(DEBOUNCE_MS / 1000)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        if new_tab != current_tab:
            tab_name = NavTabs(new_tab).name.lower()
            UILogger.log_action("AppLayout", "Navigate", f"tab={tab_name}")
            set_current_tab(NavTabs(new_tab))

    def _on_nav_change(e: ft.ControlEvent) -> None:
        selected = get_control_attr(e.control, ft.NavigationRail, "selected_index")
        if selected == int(current_tab):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_tab_switch, selected)

    def _toggle_nav(e: ft.ControlEvent) -> None:
        set_nav_collapsed(not nav_collapsed)

    # --- Resize 处理 (use_effect + page.on_resize, 防抖 + state 更新) ---
    # debounce_task 用 use_ref 持有（跨 re-render 持久 + cleanup 可访问，非命令式控件实例）
    debounce_task_ref = ft.use_ref(None)

    def _setup_resize() -> None:
        page = _get_page()
        if page is None:
            return

        async def _do_resize(width: float, height: float) -> None:
            try:
                await asyncio.sleep(RESIZE_DEBOUNCE_MS / 1000)
            except asyncio.CancelledError:
                raise  # R2: 必须传播
            set_window_size((width, height))
            debounce_task_ref.current = None

        def _on_resize(e: ft.ControlEvent) -> None:
            if debounce_task_ref.current is not None:
                debounce_task_ref.current.cancel()
            width = float(getattr(e, "width", 0) or 0)
            height = float(getattr(e, "height", 0) or 0)
            debounce_task_ref.current = page.run_task(_do_resize, width, height)

        page.on_resize = typing.cast("ft.EventHandler[ft.PageResizeEvent] | None", _on_resize)

    def _cleanup_resize() -> None:
        page = _get_page()
        if page is not None:
            page.on_resize = None
        if debounce_task_ref.current is not None:
            debounce_task_ref.current.cancel()
            debounce_task_ref.current = None

    ft.use_effect(_setup_resize, dependencies=[], cleanup=_cleanup_resize)

    # --- PubSub 导航订阅 (P1-3 批次 2 #55): home_view ErrorState CTA 通过 TOPIC_NAVIGATE 广播 ---

    def _on_navigate(topic: str, message: str) -> None:
        """TOPIC_NAVIGATE 事件处理: 切换 NavigationRail selected_index."""
        if topic != TOPIC_NAVIGATE:
            return
        try:
            target_tab = NavTabs[message.upper()]
        except KeyError:
            logger.warning("[AppLayout] Unknown navigation target: %s", message)
            return
        if int(target_tab) == int(current_tab):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_tab_switch, int(target_tab))

    def _setup_navigate() -> None:
        page = _get_page()
        if page is None:
            return
        page.pubsub.subscribe_topic(TOPIC_NAVIGATE, _on_navigate)

    def _cleanup_navigate() -> None:
        page = _get_page()
        if page is not None:
            page.pubsub.unsubscribe_topic(TOPIC_NAVIGATE)

    ft.use_effect(_setup_navigate, dependencies=[], cleanup=_cleanup_navigate)

    # --- ViewportState (Phase 6.2 P2-1): 基于 window_size 计算响应式断点 ---
    viewport = ViewportState(
        width=window_size[0],
        height=window_size[1],
        breakpoint="compact" if window_size[0] < 600 else "medium" if window_size[0] < 840 else "expanded",
    )

    # --- 渲染 ---
    collapse_btn = ft.IconButton(
        icon=ft.Icons.MENU_OPEN,
        selected=nav_collapsed,
        selected_icon=ft.Icons.MENU,
        on_click=safe_on_click(_toggle_nav),
        tooltip=I18n.get("nav_toggle_collapse"),
        icon_size=20,
    )
    brand_text = ft.Text(
        I18n.get("app_brand"),
        size=14,
        weight=ft.FontWeight.BOLD,
        color=ft.Colors.ON_SURFACE,
        visible=not nav_collapsed,
    )
    brand_header = ft.Container(
        content=ft.Column(
            [
                collapse_btn,
                ft.Image(
                    src="/icon.png",
                    width=48,
                    height=48,
                    fit=ft.BoxFit.CONTAIN,
                ),
                brand_text,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=5,
        ),
        padding=ft.Padding.only(top=10, bottom=10),
    )

    nav_rail = ft.NavigationRail(
        selected_index=int(current_tab),
        label_type=ft.NavigationRailLabelType.ALL,
        extended=not nav_collapsed,
        min_width=80,
        min_extended_width=180,
        bgcolor=ft.Colors.SURFACE,
        indicator_color=ft.Colors.PRIMARY,
        indicator_shape=ft.RoundedRectangleBorder(radius=4),
        leading=brand_header,
        destinations=_build_nav_destinations(),
        on_change=safe_on_change(_on_nav_change),
    )

    body = ft.Container(
        content=_build_pages_stack(int(current_tab), viewport),
        expand=True,
        padding=20,
        bgcolor=AppColors.BACKGROUND,
    )

    return ft.Container(
        content=ft.Row(
            [nav_rail, ft.VerticalDivider(width=1), body],
            expand=True,
        ),
        expand=True,
    )
