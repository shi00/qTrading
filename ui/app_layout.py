import asyncio
import logging
import time as _time
from enum import IntEnum

import flet as ft

from ui.i18n import I18n
from ui.v1_compat import PageRefMixin
from ui.theme import AppColors
from utils.log_decorators import UILogger
from ui.views.backtest_view import BacktestView
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.task_center_view import TaskCenterView

logger = logging.getLogger(__name__)

# 高度维度阈值：低于此值视为紧凑高度，需调整图表最小高度/表格页大小
# 估算依据：min_height=720 - nav_rail(56) - tabs(40) - body_padding(64) ≈ 560
COMPACT_HEIGHT_THRESHOLD = 560


class NavTabs(IntEnum):
    MARKET = 0
    SCREENER = 1
    BACKTEST = 2
    DATA = 3
    TASKS = 4
    SETTINGS = 5


class AppLayout(PageRefMixin, ft.Container):
    """
    Main Application Layout Container.
    Manages Navigation Rail, Views, and State Switching.

    Theme Architecture:
        Standard colors use Flet semantic tokens (ft.Colors.SURFACE etc.)
        and update automatically when page.theme changes.
        Only custom business colors (UP/DOWN, TABLE_*) need manual propagation.
    """

    def __init__(self, page: ft.Page):
        super().__init__()
        self.page = page  # type: ignore[assignment]  # [reason: V1 Control.page read-only, PageRefMixin overrides]
        self.expand = True

        # State
        self._current_tab_index = NavTabs.MARKET
        self._pending_tab_index = None
        self._debounce_task = None
        self.DEBOUNCE_MS = 50
        self._resize_debounce_task = None
        self.RESIZE_DEBOUNCE_MS = 100
        self._nav_collapsed = False
        # 缓存来自 WindowResizeEvent 的实时尺寸 (page.width/page.window.width 仅连接时更新)
        self._current_width: float = 0
        self._current_height: float = 0

        # UI Components Placeholders
        self.nav_rail = None
        self.body = None
        self.main_layout = None

        # Lazy Loading Cache
        self._view_cache: dict[int, ft.Control] = {}

        # I18n subscription id (set in did_mount, cleared in will_unmount)
        self._locale_subscription_id = None
        self._mounted = False

        # Initialize
        self._init_ui()

        # Subscribe to Theme Changes (for custom business colors only)
        # AppColors.subscribe 不立即触发回调，在 __init__ 中订阅安全
        AppColors.subscribe(self.update_theme)

    def did_mount(self):
        """挂载后订阅 I18n，避免未入 page 时 sync_immediately 触发回调失败（§5.8 规范 1）"""
        if self._mounted:
            return
        self._mounted = True
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)

    def will_unmount(self):
        if self._resize_debounce_task:
            self._resize_debounce_task.cancel()
            self._resize_debounce_task = None
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
        self._mounted = False
        AppColors.unsubscribe(self.update_theme)

    def schedule_resize(self, width: float = 0, height: float = 0):
        """从 on_resize 回调入口，调度防抖处理。

        Args:
            width: 来自 ``WindowResizeEvent.width`` 的实时窗口宽度，0 表示未知
                (如 nav 折叠触发的内部 resize，此时复用缓存值)
            height: 来自 ``WindowResizeEvent.height`` 的实时窗口高度，0 表示未知
        """
        if width:
            self._current_width = width
        if height:
            self._current_height = height
        if self._resize_debounce_task:
            self._resize_debounce_task.cancel()
        self._resize_debounce_task = self.page.run_task(self._handle_resize)

    async def _handle_resize(self):
        """防抖后实际执行 resize 派发。"""
        try:
            await asyncio.sleep(self.RESIZE_DEBOUNCE_MS / 1000)
        except asyncio.CancelledError:
            raise  # R2: 必须传播

        if not self.page:
            return

        current_view = self._view_cache.get(self._current_tab_index)
        if current_view is None:
            return

        # 鸭子类型：视图自愿实现 handle_resize 即被通知
        # 传递缓存的实时尺寸 (来自 WindowResizeEvent)，不依赖 page.width (仅连接时更新)
        if hasattr(current_view, "handle_resize"):
            try:
                current_view.handle_resize(self._current_width, self._current_height)  # type: ignore[untyped]
            except Exception as e:
                logger.debug("[AppLayout] Resize handler error: %s", e, exc_info=True)

    def _toggle_nav(self, e):  # pragma: no cover
        """切换 NavigationRail 折叠/展开状态。"""
        self._nav_collapsed = not self._nav_collapsed
        self.nav_rail.extended = not self._nav_collapsed
        self.collapse_btn.selected = self._nav_collapsed
        self.brand_text.visible = not self._nav_collapsed
        self.nav_rail.update()
        # nav 折叠/展开改变内容区可用宽度 (180↔80)，必须通知视图重新布局
        self.schedule_resize()

    def _init_ui(self):  # pragma: no cover
        """Initialize all UI components"""  # pragma: no cover

        # 1. Create Layout Structure (No Views yet)  # pragma: no cover
        logger.debug("[AppLayout] >>> Initializing Layout")  # pragma: no cover

        # 2. Brand Header — uses semantic token, auto-updates with theme  # pragma: no cover
        self.collapse_btn = ft.IconButton(  # pragma: no cover
            icon=ft.Icons.MENU_OPEN,  # pragma: no cover
            selected=False,  # pragma: no cover
            selected_icon=ft.Icons.MENU,  # pragma: no cover
            on_click=self._toggle_nav,  # pragma: no cover
            tooltip=I18n.get("nav_toggle_collapse"),  # pragma: no cover
            icon_size=20,  # pragma: no cover
        )  # pragma: no cover
        self.brand_text = ft.Text(  # pragma: no cover
            I18n.get("app_brand"),  # pragma: no cover
            size=14,  # pragma: no cover
            weight=ft.FontWeight.BOLD,  # pragma: no cover
            color=ft.Colors.ON_SURFACE,  # pragma: no cover
        )  # pragma: no cover
        brand_header = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.collapse_btn,  # pragma: no cover
                    ft.Image(  # pragma: no cover
                        src="/icon.png",  # pragma: no cover
                        width=48,  # pragma: no cover
                        height=48,  # pragma: no cover
                        fit=ft.BoxFit.CONTAIN,  # pragma: no cover
                    ),  # pragma: no cover
                    self.brand_text,  # pragma: no cover
                ],  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
            padding=ft.Padding.only(top=10, bottom=10),  # pragma: no cover
        )  # pragma: no cover

        # 3. Navigation Rail — uses semantic tokens  # pragma: no cover
        self.nav_rail = ft.NavigationRail(  # pragma: no cover
            selected_index=int(self._current_tab_index),  # pragma: no cover
            label_type=ft.NavigationRailLabelType.ALL,  # pragma: no cover
            extended=True,  # pragma: no cover
            min_width=80,  # pragma: no cover
            min_extended_width=180,  # pragma: no cover
            bgcolor=ft.Colors.SURFACE,  # pragma: no cover
            indicator_color=ft.Colors.PRIMARY,  # pragma: no cover
            indicator_shape=ft.RoundedRectangleBorder(radius=4),  # pragma: no cover
            leading=brand_header,  # pragma: no cover
            destinations=[  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.DASHBOARD_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.DASHBOARD,  # pragma: no cover
                    label=ft.Text(  # pragma: no cover
                        I18n.get("nav_market"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.FILTER_ALT_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.FILTER_ALT,  # pragma: no cover
                    label=ft.Text(  # pragma: no cover
                        I18n.get("nav_screener"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.ASSESSMENT_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.ASSESSMENT,  # pragma: no cover
                    label=ft.Text(  # pragma: no cover
                        I18n.get("nav_backtest"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.STORAGE_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.STORAGE_ROUNDED,  # pragma: no cover
                    label=ft.Text(  # pragma: no cover
                        I18n.get("nav_data"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.FORMAT_LIST_BULLETED_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.FORMAT_LIST_BULLETED,  # pragma: no cover
                    label=ft.Text(  # pragma: no cover
                        I18n.get("nav_tasks"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.SETTINGS_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.SETTINGS,  # pragma: no cover
                    label=ft.Text(  # pragma: no cover
                        I18n.get("nav_settings"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            on_change=self._on_nav_change,  # pragma: no cover
        )  # pragma: no cover

        # 4. Body Container — uses semantic token for background  # pragma: no cover
        home_view = self._get_view(NavTabs.MARKET)  # pragma: no cover

        self.body = ft.Container(  # pragma: no cover
            content=home_view,  # pragma: no cover
            expand=True,  # pragma: no cover
            padding=20,  # pragma: no cover
            bgcolor=AppColors.BACKGROUND,  # pragma: no cover
        )  # pragma: no cover

        # 5. Main Layout Row  # pragma: no cover
        self.content = ft.Row(  # pragma: no cover
            [self.nav_rail, ft.VerticalDivider(width=1), self.body],  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

    def _get_view(self, index: int) -> ft.Control:
        """Lazy load view by index"""
        if index in self._view_cache:
            return self._view_cache[index]

        logger.debug("[AppLayout] Lazy loading view for index %s", index)
        _t0 = _time.perf_counter()

        view = None
        if index == NavTabs.MARKET:
            view = HomeView(on_run_strategy=self.run_strategy_from_home)
        elif index == NavTabs.SCREENER:
            view = ScreenerView(self.page)  # type: ignore[untyped]
        elif index == NavTabs.BACKTEST:
            view = BacktestView(self.page)  # type: ignore[untyped]
        elif index == NavTabs.DATA:
            view = DataExplorerView()
        elif index == NavTabs.TASKS:
            view = TaskCenterView(self.page)  # type: ignore[untyped]
        elif index == NavTabs.SETTINGS:
            view = SettingsView()
        else:
            view = ft.Text(I18n.get("view_unknown"))

        self._view_cache[index] = view
        logger.debug(
            "[AppLayout] View %s loaded in %.1fms",
            index,
            (_time.perf_counter() - _t0) * 1000,
        )
        return view

    def show(self):  # pragma: no cover
        """Mount this layout to the page"""  # pragma: no cover
        self.page.clean()  # type: ignore[untyped]  # pragma: no cover
        self.page.add(self)  # type: ignore[untyped]  # pragma: no cover
        self.page.update()  # type: ignore[untyped]  # pragma: no cover

    def _on_locale_change(self):
        """Handle i18n locale change"""
        try:
            self.page.title = I18n.get("app_title")  # type: ignore[untyped]
            # Brand header controls
            if self.brand_text:
                self.brand_text.value = I18n.get("app_brand")
            if self.collapse_btn:
                self.collapse_btn.tooltip = I18n.get("nav_toggle_collapse")
            if self.nav_rail:
                nav_keys = [
                    "nav_market",
                    "nav_screener",
                    "nav_backtest",
                    "nav_data",
                    "nav_tasks",
                    "nav_settings",
                ]
                for i, key in enumerate(nav_keys):
                    if i < len(self.nav_rail.destinations):  # type: ignore[untyped]
                        text = I18n.get(key)
                        # V1: label 是 ft.Text 控件（StrOrControl），更新 .value 即可；
                        # 不再赋字符串以免覆盖控件的 size/weight 样式（R12.b NavRail 双写修复）
                        self.nav_rail.destinations[i].label.value = text  # type: ignore[union-attr]
                self.nav_rail.update()
            if self.page:
                self.page.update()  # type: ignore[untyped]
        except Exception as e:
            logger.warning("[AppLayout] _on_locale_change failed: %s", e, exc_info=True)
        # 语言切换后文案长度变化可能导致布局溢出，触发 resize 重新验证布局
        self.schedule_resize()

    def _on_nav_change(self, e):
        """Handle Navigation Rail Change"""
        selected_index = e.control.selected_index
        self.change_tab(selected_index)

    def update_theme(self):  # pragma: no cover
        """# pragma: no cover
        Handle global theme change event.

        Architecture:
          - Standard colors (SURFACE, PRIMARY, TEXT) use semantic tokens and
            auto-update when page.theme/page.dark_theme changes — NO manual work.
          - Only custom business colors (UP/DOWN, TABLE_*) need manual propagation
            to views that use them (tables, charts, market dashboard).
        """  # pragma: no cover
        logger.info("[AppLayout] Updating theme...")  # pragma: no cover

        # 1. Apply new theme to page (sets page.theme, page.dark_theme, page.theme_mode)  # pragma: no cover
        # 2. Propagate custom color updates to ALL views that have update_theme  # pragma: no cover
        #    (Tables, charts, settings inputs — anything with Layer 2 colors)  # pragma: no cover
        for _tab_index, view in self._view_cache.items():  # pragma: no cover
            if hasattr(view, "update_theme"):  # pragma: no cover
                try:  # pragma: no cover
                    view.update_theme()  # type: ignore[untyped]  # pragma: no cover
                except Exception as e:  # pragma: no cover
                    logger.error(  # pragma: no cover
                        "[AppLayout] Failed to update custom colors for %s: %s",  # pragma: no cover
                        type(view).__name__,  # pragma: no cover
                        e,  # pragma: no cover
                        exc_info=True,  # pragma: no cover
                    )  # pragma: no cover

        # 3. Single page update — Flet redraws all semantic-token-based colors automatically  # pragma: no cover
        self.page.update()  # type: ignore[untyped]  # pragma: no cover

    def change_tab(self, index: int):
        """Change tab with debounce logic"""
        if index == self._current_tab_index:
            return

        self._pending_tab_index = index

        # Cancel previous pending switch
        if self._debounce_task:
            self._debounce_task.cancel()

        # Schedule new switch
        self._debounce_task = self.page.run_task(self._execute_tab_switch)  # type: ignore[untyped]

    async def _execute_tab_switch(self):
        """Async execution of tab switch"""
        try:
            await asyncio.sleep(self.DEBOUNCE_MS / 1000)
        except asyncio.CancelledError:
            raise

        index = self._pending_tab_index
        if index is None or index == self._current_tab_index:
            return

        tab_name = NavTabs(index).name.lower()
        UILogger.log_action("AppLayout", "Navigate", f"tab={tab_name}")

        _t0 = _time.perf_counter()
        logger.debug("[AppLayout] Switching to tab index %s", index)

        # Optimize HomeView visibility for background resource saving
        home_view = self._get_view(NavTabs.MARKET)
        if hasattr(home_view, "set_visible"):
            home_view.set_visible(index == NavTabs.MARKET)  # type: ignore[untyped]
        # Switch Content (Lazy Load here)
        new_view = self._get_view(index)
        self.body.content = new_view  # type: ignore[untyped]
        self._current_tab_index = index
        self.nav_rail.selected_index = index  # type: ignore[untyped]

        # 先挂载到 Flutter，再触发 locale 兜底（避免控件未挂载时调 update 触发 Null check）
        try:
            self.body.update()  # type: ignore[untyped]
        except Exception as ex:
            logger.error("[AppLayout] body.update() failed during tab switch: %s", ex, exc_info=True)
            try:
                if self.page:
                    self.page.update()
            except Exception as fallback_ex:
                logger.debug(
                    "[AppLayout] page.update() fallback also failed: %s",
                    fallback_ex,
                    exc_info=True,
                )

        self.nav_rail.update()  # type: ignore[untyped]

        # 生命周期兜底：缓存的视图可能错过 I18n 通知（例如视图未挂载时收到语言切换），
        # 挂载完成后再显式调用 refresh_locale，确保文案与当前 locale 一致。
        refresh_fn = getattr(new_view, "refresh_locale", None) or getattr(new_view, "_on_locale_change", None)
        if callable(refresh_fn):
            try:
                refresh_fn()
            except Exception as ex:
                logger.debug("[AppLayout] View locale refresh skipped: %s", ex, exc_info=True)

        # 响应式兜底：延迟挂载的视图首次显示时，缓存的尺寸可能已过时 (窗口在后台 resize)，
        # 挂载完成后显式调用 handle_resize，确保布局基于当前窗口尺寸。
        if hasattr(new_view, "handle_resize"):
            try:
                new_view.handle_resize(self._current_width, self._current_height)  # type: ignore[untyped]
            except Exception as ex:
                logger.debug("[AppLayout] View handle_resize on mount skipped: %s", ex, exc_info=True)
        logger.debug(
            "[AppLayout] Tab switch done in %.1fms",
            (_time.perf_counter() - _t0) * 1000,
        )

    async def run_strategy_from_home(self, strategy_key):
        """Callback to switch to Screener and run strategy"""
        self.change_tab(NavTabs.SCREENER)

        if self._debounce_task:
            self._debounce_task.cancel()

        self._pending_tab_index = NavTabs.SCREENER
        await self._execute_tab_switch()

        screener_view = self._get_view(NavTabs.SCREENER)
        if isinstance(screener_view, ScreenerView):
            await screener_view.select_and_run_strategy(strategy_key)
