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
from enum import IntEnum
from typing import Any

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors
from ui.views.backtest_view import BacktestView
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.task_center_view import TaskCenterView
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


def _build_view(tab_index: int) -> ft.Control:
    """根据 tab 索引构造子视图 (直接函数调用, 不缓存实例)。

    声明式范式: 每次重渲染重新构造子视图, 由 Flet diff 算法决定实际 DOM 更新。
    子视图内部用 ``use_state``/``use_viewmodel`` 持久化自身状态, 重建不丢失。
    """
    if tab_index == NavTabs.MARKET:
        return HomeView()
    if tab_index == NavTabs.SCREENER:
        return ScreenerView()
    if tab_index == NavTabs.BACKTEST:
        return BacktestView()
    if tab_index == NavTabs.DATA:
        return DataExplorerView()
    if tab_index == NavTabs.TASKS:
        return TaskCenterView()
    if tab_index == NavTabs.SETTINGS:
        return SettingsView()
    return ft.Text(I18n.get("view_unknown"))


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
    ft.use_state(I18n.get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Pure UI state ---
    current_tab, set_current_tab = ft.use_state(NavTabs.MARKET)
    nav_collapsed, set_nav_collapsed = ft.use_state(False)
    # 窗口尺寸快照 (resize 事件驱动, 触发重渲染让子视图按新尺寸布局)
    _, set_window_size = ft.use_state((0.0, 0.0))

    # --- Tab 切换 (防抖, R2: CancelledError 必须 raise) ---
    async def _do_tab_switch(new_tab: int) -> None:
        try:
            await asyncio.sleep(DEBOUNCE_MS / 1000)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        if new_tab != current_tab:
            tab_name = NavTabs(new_tab).name.lower()
            UILogger.log_action("AppLayout", "Navigate", f"tab={tab_name}")
            set_current_tab(new_tab)

    def _on_nav_change(e: ft.ControlEvent) -> None:
        selected = e.control.selected_index
        if selected == int(current_tab):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_tab_switch, selected)

    def _toggle_nav(e: ft.ControlEvent) -> None:
        set_nav_collapsed(not nav_collapsed)

    # --- Resize 处理 (use_effect + page.on_resize, 防抖 + state 更新) ---
    def _setup_resize() -> None:
        page = _get_page()
        if page is None:
            return

        # 防抖任务跟踪 (闭包变量, effect 只运行一次, 跨 resize 事件持久)
        # page.run_task 返回 Future, 用 Any 注解避免 pyright 协变/逆变推断冲突
        debounce_task: Any = None

        async def _do_resize(width: float, height: float) -> None:
            nonlocal debounce_task
            try:
                await asyncio.sleep(RESIZE_DEBOUNCE_MS / 1000)
            except asyncio.CancelledError:
                raise  # R2: 必须传播
            set_window_size((width, height))
            debounce_task = None

        def _on_resize(e: ft.ControlEvent) -> None:
            nonlocal debounce_task
            if debounce_task is not None:
                debounce_task.cancel()
            width = float(getattr(e, "width", 0) or 0)
            height = float(getattr(e, "height", 0) or 0)
            debounce_task = page.run_task(_do_resize, width, height)

        page.on_resize = _on_resize

    def _cleanup_resize() -> None:
        page = _get_page()
        if page is not None:
            page.on_resize = None

    ft.use_effect(_setup_resize, dependencies=[], cleanup=_cleanup_resize)

    # --- 渲染 ---
    collapse_btn = ft.IconButton(
        icon=ft.Icons.MENU_OPEN,
        selected=nav_collapsed,
        selected_icon=ft.Icons.MENU,
        on_click=_toggle_nav,
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
        on_change=_on_nav_change,
    )

    body = ft.Container(
        content=_build_view(int(current_tab)),
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
