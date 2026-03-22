import asyncio
import logging
import time as _time
from enum import IntEnum

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.task_center_view import TaskCenterView

logger = logging.getLogger(__name__)


class NavTabs(IntEnum):
    MARKET = 0
    SCREENER = 1
    DATA = 2
    TASKS = 3
    SETTINGS = 4


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
        AppColors.unsubscribe(self.update_theme)

    def _init_ui(self):
        """Initialize all UI components"""

        # 1. Create Layout Structure (No Views yet)
        logger.debug("[AppLayout] >>> Initializing Layout")

        # 2. Brand Header — uses semantic token, auto-updates with theme
        brand_header = ft.Container(
            content=ft.Column(
                [
                    ft.Image(
                        src="/icon.png",
                        width=48,
                        height=48,
                        fit=ft.ImageFit.CONTAIN,
                    ),
                    ft.Text(
                        I18n.get("app_brand"),
                        size=14,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.ON_SURFACE,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=5,
            ),
            padding=ft.padding.only(top=20, bottom=10),
        )

        # 3. Navigation Rail — uses semantic tokens
        self.nav_rail = ft.NavigationRail(
            selected_index=int(self._current_tab_index),
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            min_extended_width=180,
            bgcolor=ft.Colors.SURFACE,
            indicator_color=ft.Colors.PRIMARY,
            indicator_shape=ft.RoundedRectangleBorder(radius=4),
            leading=brand_header,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.DASHBOARD_OUTLINED,
                    selected_icon=ft.Icons.DASHBOARD,
                    label=I18n.get("nav_market"),
                    label_content=ft.Text(
                        I18n.get("nav_market"),
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FILTER_ALT_OUTLINED,
                    selected_icon=ft.Icons.FILTER_ALT,
                    label=I18n.get("nav_screener"),
                    label_content=ft.Text(
                        I18n.get("nav_screener"),
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.STORAGE_OUTLINED,
                    selected_icon=ft.Icons.STORAGE_ROUNDED,
                    label=I18n.get("nav_data"),
                    label_content=ft.Text(
                        I18n.get("nav_data"),
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FORMAT_LIST_BULLETED_OUTLINED,
                    selected_icon=ft.Icons.FORMAT_LIST_BULLETED,
                    label=I18n.get("nav_tasks", "任务"),
                    label_content=ft.Text(
                        I18n.get("nav_tasks", "任务"),
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label=I18n.get("nav_settings"),
                    label_content=ft.Text(
                        I18n.get("nav_settings"),
                        size=12,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
            ],
            on_change=self._on_nav_change,
        )

        # 4. Body Container — uses semantic token for background
        home_view = self._get_view(NavTabs.MARKET)

        self.body = ft.Container(
            content=home_view,
            expand=True,
            padding=20,
            bgcolor=AppColors.BACKGROUND,
        )

        # 5. Main Layout Row
        self.content = ft.Row(
            [self.nav_rail, ft.VerticalDivider(width=1), self.body],
            expand=True,
        )

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
            view = ScreenerView(self.page)  # type: ignore
        elif index == NavTabs.DATA:
            view = DataExplorerView()
        elif index == NavTabs.TASKS:
            view = TaskCenterView(self.page)  # type: ignore
        elif index == NavTabs.SETTINGS:
            view = SettingsView()
        else:
            view = ft.Text(I18n.get("view_unknown"))

        self._view_cache[index] = view
        logger.debug(
            f"[AppLayout] View {index} loaded in {(_time.perf_counter() - _t0) * 1000:.1f}ms",
        )
        return view

    def show(self):
        """Mount this layout to the page"""
        self.page.clean()  # type: ignore
        self.page.add(self)  # type: ignore
        self.page.update()  # type: ignore

    def _subscribe_events(self):
        """Subscribe to global events"""
        I18n.subscribe(self._on_locale_change)

    def _on_locale_change(self):
        """Handle i18n locale change"""
        self.page.title = I18n.get("app_title")  # type: ignore
        if self.nav_rail:
            nav_keys = [
                "nav_market",
                "nav_screener",
                "nav_data",
                "nav_tasks",
                "nav_settings",
            ]
            for i, key in enumerate(nav_keys):
                if i < len(self.nav_rail.destinations):  # type: ignore
                    text = I18n.get(key)
                    self.nav_rail.destinations[i].label = text  # type: ignore
                    self.nav_rail.destinations[i].label_content.value = text  # type: ignore
            self.nav_rail.update()
        self.page.update()  # type: ignore

    def _on_nav_change(self, e):
        """Handle Navigation Rail Change"""
        selected_index = e.control.selected_index
        self.change_tab(selected_index)

    def update_theme(self):
        """
        Handle global theme change event.

        Architecture:
          - Standard colors (SURFACE, PRIMARY, TEXT) use semantic tokens and
            auto-update when page.theme/page.dark_theme changes — NO manual work.
          - Only custom business colors (UP/DOWN, TABLE_*) need manual propagation
            to views that use them (tables, charts, market dashboard).
        """
        logger.info("[AppLayout] Updating theme...")

        # 1. Apply new theme to page (sets page.theme, page.dark_theme, page.theme_mode)
        # 2. Propagate custom color updates to ALL views that have update_theme
        #    (Tables, charts, settings inputs — anything with Layer 2 colors)
        for tab_index, view in self._view_cache.items():
            if hasattr(view, "update_theme"):
                try:
                    view.update_theme()  # type: ignore
                except Exception as e:
                    logger.error(
                        f"[AppLayout] Failed to update custom colors for {type(view).__name__}: {e}",
                    )

        # 3. Single page update — Flet redraws all semantic-token-based colors automatically
        self.page.update()  # type: ignore

    def change_tab(self, index: int):
        """Change tab with debounce logic"""
        if index == self._current_tab_index:
            return

        self._pending_tab_index = index

        # Cancel previous pending switch
        if self._debounce_task:
            self._debounce_task.cancel()

        # Schedule new switch
        self._debounce_task = self.page.run_task(self._execute_tab_switch)  # type: ignore

    async def _execute_tab_switch(self):
        """Async execution of tab switch"""
        try:
            await asyncio.sleep(self.DEBOUNCE_MS / 1000)
        except asyncio.CancelledError:
            return

        index = self._pending_tab_index
        if index is None or index == self._current_tab_index:
            return

        _t0 = _time.perf_counter()
        logger.debug(f"[AppLayout] Switching to tab index {index}")

        # Optimize HomeView visibility for background resource saving
        home_view = self._get_view(NavTabs.MARKET)
        if hasattr(home_view, "set_visible"):
            home_view.set_visible(index == NavTabs.MARKET)  # type: ignore

        # Switch Content (Lazy Load here)
        new_view = self._get_view(index)
        self.body.content = new_view  # type: ignore

        self._current_tab_index = index
        self.nav_rail.selected_index = index  # type: ignore

        self.body.update()  # type: ignore
        self.nav_rail.update()  # type: ignore

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
