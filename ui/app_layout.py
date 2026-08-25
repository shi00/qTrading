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
- 异步任务: ``page.run_task`` 调度; R2 CancelledError 必须 raise
- PageRefMixin 兼容桩已在 Phase G.3 删除 (声明式改造收官)
- Phase 10.2: ViewportState/resize 重渲染链删除 — 7 视图全部 ``_ = viewport`` 零消费,
  resize 防抖仅驱动无消费者的全树重渲染 (YAGNI); 布局响应由 ResponsiveRow 客户端断点承担
"""

import asyncio
import logging
from enum import IntEnum

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_attr,
    safe_controls,
    safe_on_change,
    safe_on_click,
)
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS, Eid
from ui.theme import AppColors, AppStyles
from ui.viewmodels.nav_badge_view_model import NavBadgeViewModel
from ui.views.backtest_view import BacktestView
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SETTINGS_SUBTAB_INDEX, SettingsView
from ui.views.task_center_view import TaskCenterView
from ui.views.watchlist_view import WatchlistView
from utils.app_env import is_e2e_mode
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)

# Tab 切换防抖 (ms) — 快速连续点击导航时, 最后一次点击生效
DEBOUNCE_MS = 50


class NavTabs(IntEnum):
    MARKET = 0
    SCREENER = 1
    BACKTEST = 2
    DATA = 3
    TASKS = 4
    SETTINGS = 5
    WATCHLIST = 6


# nav i18n key → EIDS.NAV.{role} 映射 (PR-4 Task 4.0/4.1: nav label anchor 化)
# 与 _build_nav_destinations 的 nav_items 顺序对齐
_NAV_EIDS: dict[str, Eid] = {
    "nav_market": EIDS.NAV.MARKET,
    "nav_screener": EIDS.NAV.SCREENER,
    "nav_backtest": EIDS.NAV.BACKTEST,
    "nav_data": EIDS.NAV.DATA,
    "nav_tasks": EIDS.NAV.TASKS,
    "nav_settings": EIDS.NAV.SETTINGS,
    "nav_watchlist": EIDS.NAV.WATCHLIST,
}


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _parse_navigate_message(message: str) -> tuple[str, str | None]:
    """解析 TOPIC_NAVIGATE 消息 "<tab>" / "<tab>:<subtab>" (UX-01 导航深链协议).

    Args:
        message: 协议消息。tab 段保持原样 (后续 upper 查 NavTabs),
            subtab 段归一为小写。

    Returns:
        ``(tab_name, subtab | None)``; 格式非法 (多段 / 空 tab / 空 subtab)
        返回 ``("", None)``。
    """
    parts = message.split(":")
    if not parts[0] or len(parts) > 2 or (len(parts) == 2 and not parts[1]):
        return ("", None)
    return (parts[0], parts[1].lower() if len(parts) == 2 else None)


@ft.component
def _build_pages_stack(
    current_tab: int,
    settings_subtab_request: tuple[str, int] | None = None,
    screener_stock_request: tuple[str, int] | None = None,
) -> ft.Stack:
    """构造所有页面控件的 ``ft.Stack`` (``visible`` prop 控制显示/隐藏)。

    项目内存硬约束 #34: state-driven rendering (ft.Stack + visible prop)
    替代条件渲染 (if/else 创建不同控件)。所有页面控件预先创建并放入 Stack,
    通过 ``visible`` prop 切换显示/隐藏, 不再动态创建/销毁控件。

    声明式范式: 每次重渲染重新构造控件树, 由 Flet diff 算法决定实际 DOM 更新。
    子视图内部用 ``use_state``/``use_viewmodel`` 持久化自身状态, 重建不丢失。

    E2E 模式优化: ``E2E_TESTING=true`` 时只构造当前激活视图, 非激活视图用空
    ``ft.Container`` 占位。根因: 多视图 VM 构造链 (DataSourceViewModel →
    AIService → litellm import 18s+; ScreenerViewModel → DataProcessor;
    DataExplorerViewModel → DataExplorerQueryClient 等) 在 MainThread 同步执行,
    阻塞 Flet patch 下发导致 E2E 浏览器超时。E2E 测试不需要非激活视图的 VM 状态,
    跳过是安全的 (与 home_view.py L142-154 E2E_TESTING 跳过范式一致)。

    Args:
        current_tab: 当前激活的 NavTabs 值, 控制 visible prop。
        settings_subtab_request: UX-01 导航深链的 settings 子页请求
            ``(subtab_key, seq)``, 透传给 SettingsView。
        screener_stock_request: UX-04 导航深链的 screener 股票代码过滤请求
            ``(code, seq)``, 透传给 ScreenerView。
    """
    is_e2e = is_e2e_mode()

    def _make_content(view_factory, is_active: bool) -> ft.Control:
        # E2E 模式下非激活视图返回空 Container, 避免调用 view_factory() 触发 VM 构造链
        # (DataSourceViewModel → AIService → litellm import 18s+ 等) 阻塞 Flet patch 下发
        if is_e2e and not is_active:
            return ft.Container(expand=True)
        return view_factory()

    pages = [
        ft.Container(
            content=_make_content(
                lambda: HomeView(active=current_tab == NavTabs.MARKET),
                current_tab == NavTabs.MARKET,
            ),
            expand=True,
            visible=current_tab == NavTabs.MARKET,
        ),
        ft.Container(
            content=_make_content(
                lambda: ScreenerView(
                    active=current_tab == NavTabs.SCREENER,
                    stock_filter_request=screener_stock_request,
                ),
                current_tab == NavTabs.SCREENER,
            ),
            expand=True,
            visible=current_tab == NavTabs.SCREENER,
        ),
        ft.Container(
            content=_make_content(
                lambda: BacktestView(active=current_tab == NavTabs.BACKTEST),
                current_tab == NavTabs.BACKTEST,
            ),
            expand=True,
            visible=current_tab == NavTabs.BACKTEST,
        ),
        ft.Container(
            content=_make_content(
                lambda: DataExplorerView(active=current_tab == NavTabs.DATA),
                current_tab == NavTabs.DATA,
            ),
            expand=True,
            visible=current_tab == NavTabs.DATA,
        ),
        ft.Container(
            content=_make_content(
                lambda: TaskCenterView(active=current_tab == NavTabs.TASKS),
                current_tab == NavTabs.TASKS,
            ),
            expand=True,
            visible=current_tab == NavTabs.TASKS,
        ),
        ft.Container(
            content=_make_content(
                lambda: SettingsView(
                    active=current_tab == NavTabs.SETTINGS,
                    target_subtab=settings_subtab_request,
                ),
                current_tab == NavTabs.SETTINGS,
            ),
            expand=True,
            visible=current_tab == NavTabs.SETTINGS,
        ),
        ft.Container(
            content=_make_content(
                lambda: WatchlistView(active=current_tab == NavTabs.WATCHLIST),
                current_tab == NavTabs.WATCHLIST,
            ),
            expand=True,
            visible=current_tab == NavTabs.WATCHLIST,
        ),
    ]
    # 根因防范 (PR #472 E2E 修复): 显式 fit=StackFit.EXPAND。默认 LOOSE 会按子项 Intrinsic
    # Preferred Height 自底向上塌缩，含 expand=True 弹性空间的子视图会被挤压至 0 高度。
    return ft.Stack(safe_controls(pages), expand=True, fit=ft.StackFit.EXPAND)


def _build_nav_destinations(running_count: int = 0) -> list[ft.NavigationRailDestination]:
    """构造导航栏目的地列表 (i18n 变化时由组件重渲染自动刷新)。

    Args:
        running_count: TaskManager 中 RUNNING 状态任务数 (Phase 6.1, FR-UX-006)。
            ``nav_tasks`` 项 icon 上叠加数字角标, >0 时显示。
    """
    nav_items = [
        (ft.Icons.DASHBOARD_OUTLINED, ft.Icons.DASHBOARD, "nav_market"),
        (ft.Icons.FILTER_ALT_OUTLINED, ft.Icons.FILTER_ALT, "nav_screener"),
        (ft.Icons.ASSESSMENT_OUTLINED, ft.Icons.ASSESSMENT, "nav_backtest"),
        (ft.Icons.STORAGE_OUTLINED, ft.Icons.STORAGE_ROUNDED, "nav_data"),
        (ft.Icons.FORMAT_LIST_BULLETED_OUTLINED, ft.Icons.FORMAT_LIST_BULLETED, "nav_tasks"),
        (ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS, "nav_settings"),
        (ft.Icons.STAR_OUTLINE, ft.Icons.STAR, "nav_watchlist"),
    ]
    destinations: list[ft.NavigationRailDestination] = []
    for icon, selected_icon, label_key in nav_items:
        # Phase 6.1 FR-UX-006: nav_tasks icon 上叠加 running_count 角标
        icon_content: ft.IconData | ft.Stack = icon
        if label_key == "nav_tasks" and running_count > 0:
            icon_content = _build_nav_badge_icon(icon, running_count)
        # PR-4 Task 4.0/4.1: nav label anchor 化 (LABEL kind, 走 textContent 通道)
        # Task 4.0 PoC 验证 T-3: 折叠态 (extended=False) 下 anchor 是否仍暴露到 DOM
        label_control = anchored(
            _NAV_EIDS[label_key],
            ft.Text(
                I18n.get(label_key),
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.BOLD,
            ),
        )
        destinations.append(
            ft.NavigationRailDestination(
                icon=icon_content,
                selected_icon=selected_icon,
                label=label_control,
            )
        )
    return destinations


def _build_nav_badge_icon(icon: str, running_count: int) -> ft.Stack:
    """构造带运行中任务数角标的 nav_tasks icon (Phase 6.1, FR-UX-006).

    ``ft.Badge`` 在 NavigationRailDestination 上无 ``badge`` 属性可挂载,
    故用 ``ft.Stack`` 在 icon 右上角叠加数字小圆点。``running_count`` 上限
    99, 超过显示 "99+"。

    Args:
        icon: 基础 icon 名称 (ft.Icons.FORMAT_LIST_BULLETED_OUTLINED 等)。
        running_count: RUNNING 状态任务数 (>0 由调用方保证)。
    """
    badge_text = str(running_count) if running_count <= 99 else "99+"  # pragma: no cover
    # 数字宽度自适应: 1 位数 16px, 2 位数/99+ 用 22px
    badge_w = 16 if len(badge_text) == 1 else 22  # pragma: no cover
    return ft.Stack(  # pragma: no cover
        [
            ft.Icon(icon, size=AppStyles.FONT_SIZE_LG),
            ft.Container(
                content=ft.Text(
                    badge_text,
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=ft.Colors.ON_ERROR,
                    text_align=ft.TextAlign.CENTER,
                    weight=ft.FontWeight.BOLD,
                ),
                width=badge_w,
                height=16,
                border_radius=8,
                bgcolor=AppColors.ERROR,
                alignment=ft.Alignment.CENTER,
                left=12,
                top=-2,
                padding=ft.Padding.symmetric(horizontal=2, vertical=0),
            ),
        ],
        width=24,
        height=24,
    )


@ft.component
def AppLayout() -> ft.Container:
    """主应用布局 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 状态驱动: current_tab / nav_collapsed 用 ``use_state`` (纯 UI 状态, YAGNI 不建 VM)
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 子视图直接函数调用消费 (无 use_ref cache), 每次重渲染重新构造
    - 异步任务: ``page.run_task`` 调度; R2 CancelledError 必须 raise
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Phase 6.1 FR-UX-006: nav_tasks 运行中任务数角标 (TaskManager.subscribe 驱动) ---
    nav_badge_state, _ = use_viewmodel(factory=NavBadgeViewModel)

    # --- Pure UI state ---
    current_tab, set_current_tab = ft.use_state(NavTabs.MARKET)
    nav_collapsed, set_nav_collapsed = ft.use_state(False)
    # UX-01 导航深链: settings 子页请求 (subtab_key, seq), seq 递增使重复深链可重触发
    settings_subtab_request: tuple[str, int] | None = None
    settings_subtab_request, set_settings_subtab_request = ft.use_state(settings_subtab_request)
    # UX-04 导航深链: screener 股票代码过滤请求 (code, seq), seq 递增使重复深链可重触发
    screener_stock_request: tuple[str, int] | None = None
    screener_stock_request, set_screener_stock_request = ft.use_state(screener_stock_request)

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

    # --- PubSub 导航订阅 (P1-3 批次 2 #55): home_view ErrorState CTA 通过 TOPIC_NAVIGATE 广播 ---

    def _on_navigate(topic: str, message: str) -> None:
        """TOPIC_NAVIGATE 事件处理: 切换 NavigationRail selected_index (UX-01 深链协议).

        消息格式: "<tab>" 或 "<tab>:<subtab>" (subtab 段语义由目标 tab 定义:
        settings = 子页 key 白名单, screener = 股票代码任意非空段 UX-04)。
        未知子页降级为切主 tab, 不吞导航; 格式非法整体忽略。

        Note:
            本 handler 是订阅时闭包 (use_effect(dependencies=[]) 只执行一次),
            捕获的 state 值读取是 stale 的 → 内部禁止读取 state 当前值做逻辑判断,
            state 写入必须用函数式更新。
        """
        if topic != TOPIC_NAVIGATE:
            return
        tab_name, subtab = _parse_navigate_message(message)
        if not tab_name:
            logger.warning("[AppLayout] Invalid navigation message format: %s", message)
            return
        try:
            target_tab = NavTabs[tab_name.upper()]
        except KeyError:
            logger.warning("[AppLayout] Unknown navigation target: %s", message)
            return
        # UX-01/UX-04: 深链 subtab 分派 (settings=子页 key / screener=股票代码);
        # 未知/越页子页降级为切主 tab (不吞导航)
        subtab_handled = False
        if subtab is not None and int(target_tab) == int(NavTabs.SETTINGS):
            if subtab in SETTINGS_SUBTAB_INDEX:
                # 函数式更新: 读取 hook.value 动态值递增 seq, 防订阅时闭包 stale 快照
                set_settings_subtab_request(lambda old: (subtab, (old[1] + 1) if old else 1))
                subtab_handled = True
            # settings 未知子页 → 落入下方 warning 降级
        elif subtab is not None and int(target_tab) == int(NavTabs.SCREENER):
            # UX-04: screener 段语义 = 股票代码 (任意非空字符串, 无白名单)
            set_screener_stock_request(lambda old: (subtab, (old[1] + 1) if old else 1))
            subtab_handled = True
        if subtab is not None and not subtab_handled:
            logger.warning(
                "[AppLayout] Unknown subtab %r for tab %s, fallback to main tab",
                subtab,
                tab_name,
            )
            subtab = None
        if int(target_tab) == int(current_tab):
            # stale 快照下几乎不命中; 命中时子页请求已先行 set, 深链不被吞
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

    # --- 渲染 ---
    logger.info("[AppLayout] construction start, current_tab=%s", current_tab)
    collapse_btn = ft.IconButton(
        icon=ft.Icons.MENU_OPEN,
        selected=nav_collapsed,
        selected_icon=ft.Icons.MENU,
        on_click=safe_on_click(_toggle_nav),
        tooltip=I18n.get("nav_toggle_collapse"),
        icon_size=AppStyles.FONT_SIZE_HEADLINE,
    )
    brand_text = ft.Text(
        I18n.get("app_brand"),
        size=AppStyles.FONT_SIZE_LG,
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
        destinations=_build_nav_destinations(running_count=nav_badge_state.running_count),
        on_change=safe_on_change(_on_nav_change),
    )

    logger.info("[AppLayout] building pages stack")
    body = ft.Container(
        content=_build_pages_stack(int(current_tab), settings_subtab_request, screener_stock_request),
        expand=True,
        padding=AppStyles.SPACING_XL,
        bgcolor=AppColors.BACKGROUND,
    )
    logger.info("[AppLayout] construction complete, returning Container")

    return ft.Container(
        content=ft.Row(
            [nav_rail, ft.VerticalDivider(width=1), body],
            expand=True,
        ),
        expand=True,
    )
