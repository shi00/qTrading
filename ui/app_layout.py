import asyncio
import logging
import time as _time
from enum import IntEnum

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors
from utils.log_decorators import UILogger
from ui.views.backtest_view import BacktestView
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.task_center_view import TaskCenterView

logger = logging.getLogger(__name__)


class NavTabs(IntEnum):
    MARKET = 0
    SCREENER = 1
    BACKTEST = 2
    DATA = 3
    TASKS = 4
    SETTINGS = 5


class AppLayout(ft.Container):
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
        self.page = page
        self.expand = True

        # State
        self._current_tab_index = NavTabs.MARKET
        self._pending_tab_index = None
        self._debounce_task = None
        self.DEBOUNCE_MS = 50

        # UI Components Placeholders
        self.nav_rail = None
        self.body = None
        self.main_layout = None

        # Lazy Loading Cache
        self._view_cache: dict[int, ft.Control] = {}

        # Initialize
        self._init_ui()
        self._subscribe_events()

        # Subscribe to Theme Changes (for custom business colors only)
        AppColors.subscribe(self.update_theme)

    def will_unmount(self):
        I18n.unsubscribe(self._on_locale_change)
        AppColors.unsubscribe(self.update_theme)

    def _init_ui(self):  # pragma: no cover
        """Initialize all UI components"""  # pragma: no cover

        # 1. Create Layout Structure (No Views yet)  # pragma: no cover
        logger.debug("[AppLayout] >>> Initializing Layout")  # pragma: no cover

        # 2. Brand Header — uses semantic token, auto-updates with theme  # pragma: no cover
        brand_header = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Image(  # pragma: no cover
                        src="/icon.png",  # pragma: no cover
                        width=48,  # pragma: no cover
                        height=48,  # pragma: no cover
                        fit=ft.ImageFit.CONTAIN,  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Text(  # pragma: no cover
                        I18n.get("app_brand"),  # pragma: no cover
                        size=14,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                        color=ft.Colors.ON_SURFACE,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
            padding=ft.padding.only(top=20, bottom=10),  # pragma: no cover
        )  # pragma: no cover

        # 3. Navigation Rail — uses semantic tokens  # pragma: no cover
        self.nav_rail = ft.NavigationRail(  # pragma: no cover
            selected_index=int(self._current_tab_index),  # pragma: no cover
            label_type=ft.NavigationRailLabelType.ALL,  # pragma: no cover
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
                    label=I18n.get("nav_market"),  # pragma: no cover
                    label_content=ft.Text(  # pragma: no cover
                        I18n.get("nav_market"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.FILTER_ALT_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.FILTER_ALT,  # pragma: no cover
                    label=I18n.get("nav_screener"),  # pragma: no cover
                    label_content=ft.Text(  # pragma: no cover
                        I18n.get("nav_screener"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.ASSESSMENT_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.ASSESSMENT,  # pragma: no cover
                    label=I18n.get("nav_backtest"),  # pragma: no cover
                    label_content=ft.Text(  # pragma: no cover
                        I18n.get("nav_backtest"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.STORAGE_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.STORAGE_ROUNDED,  # pragma: no cover
                    label=I18n.get("nav_data"),  # pragma: no cover
                    label_content=ft.Text(  # pragma: no cover
                        I18n.get("nav_data"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.FORMAT_LIST_BULLETED_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.FORMAT_LIST_BULLETED,  # pragma: no cover
                    label=I18n.get("nav_tasks"),  # pragma: no cover
                    label_content=ft.Text(  # pragma: no cover
                        I18n.get("nav_tasks"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        weight=ft.FontWeight.BOLD,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.NavigationRailDestination(  # pragma: no cover
                    icon=ft.Icons.SETTINGS_OUTLINED,  # pragma: no cover
                    selected_icon=ft.Icons.SETTINGS,  # pragma: no cover
                    label=I18n.get("nav_settings"),  # pragma: no cover
                    label_content=ft.Text(  # pragma: no cover
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

        logger.debug(f"[AppLayout] Lazy loading view for index {index}")
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
            f"[AppLayout] View {index} loaded in {(_time.perf_counter() - _t0) * 1000:.1f}ms",
        )
        return view

    def show(self):  # pragma: no cover
        """Mount this layout to the page"""  # pragma: no cover
        self.page.clean()  # type: ignore[untyped]  # pragma: no cover
        self.page.add(self)  # type: ignore[untyped]  # pragma: no cover
        self.page.update()  # type: ignore[untyped]  # pragma: no cover

    def _subscribe_events(self):
        """Subscribe to global events"""
        I18n.subscribe(self._on_locale_change)

    def _on_locale_change(self):  # pragma: no cover
        """Handle i18n locale change"""  # pragma: no cover
        self.page.title = I18n.get("app_title")  # type: ignore[untyped]  # pragma: no cover
        if self.nav_rail:  # pragma: no cover
            nav_keys = [  # pragma: no cover
                "nav_market",  # pragma: no cover
                "nav_screener",  # pragma: no cover
                "nav_backtest",  # pragma: no cover
                "nav_data",  # pragma: no cover
                "nav_tasks",  # pragma: no cover
                "nav_settings",  # pragma: no cover
            ]  # pragma: no cover
            for i, key in enumerate(nav_keys):  # pragma: no cover
                if i < len(self.nav_rail.destinations):  # type: ignore[untyped]  # pragma: no cover
                    text = I18n.get(key)  # pragma: no cover
                    self.nav_rail.destinations[i].label = text  # type: ignore[untyped]  # pragma: no cover
                    self.nav_rail.destinations[i].label_content.value = text  # type: ignore[untyped]  # pragma: no cover
            self.nav_rail.update()  # pragma: no cover
        self.page.update()  # type: ignore[untyped]  # pragma: no cover

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
                        f"[AppLayout] Failed to update custom colors for {type(view).__name__}: {e}",  # pragma: no cover
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
        logger.debug(f"[AppLayout] Switching to tab index {index}")

        # Optimize HomeView visibility for background resource saving
        home_view = self._get_view(NavTabs.MARKET)
        if hasattr(home_view, "set_visible"):
            home_view.set_visible(index == NavTabs.MARKET)  # type: ignore[untyped]
        # Switch Content (Lazy Load here)
        new_view = self._get_view(index)
        self.body.content = new_view  # type: ignore[untyped]
        self._current_tab_index = index
        self.nav_rail.selected_index = index  # type: ignore[untyped]
        self.body.update()  # type: ignore[untyped]
        self.nav_rail.update()  # type: ignore[untyped]
        logger.debug(
            f"[AppLayout] Tab switch done in {(_time.perf_counter() - _t0) * 1000:.1f}ms",
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
