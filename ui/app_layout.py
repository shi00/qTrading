
import asyncio
import logging
import time as _time
from enum import IntEnum

import flet as ft

from data.market_data_service import MarketDataService
from data.news_subscription import NewsSubscriptionService
from ui.components.toast_manager import ToastManager
from ui.i18n import I18n
from ui.theme import AppColors
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView

logger = logging.getLogger(__name__)

class NavTabs(IntEnum):
    MARKET = 0
    SCREENER = 1
    DATA = 2
    SETTINGS = 3

class AppLayout(ft.Container):
    """
    Main Application Layout Container.
    Manages Navigation Rail, Views, and State Switching.
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
        self.views = []
        self.main_layout = None
        
        # Initialize
        self._init_ui()
        self._subscribe_events()

    def _init_ui(self):
        """Initialize all UI components"""
        
        # 1. Create Views
        logger.debug("[AppLayout] >>> Creating views START")
        _t0 = _time.perf_counter()
        
        self.home_view = HomeView(on_run_strategy=self.run_strategy_from_home)
        self.screener_view = ScreenerView()
        self.data_view = DataExplorerView()
        self.settings_view = SettingsView()
        
        self.views = [
            self.home_view,
            self.screener_view,
            self.data_view,
            self.settings_view
        ]
        
        logger.debug(f"[AppLayout] <<< Creating views END, TOTAL={(_time.perf_counter() - _t0) * 1000:.1f}ms")

        # 2. Brand Header
        brand_header = ft.Container(
            content=ft.Column([
                ft.Image(src="/icon.png", width=48, height=48, fit=ft.ImageFit.CONTAIN),
                ft.Text(I18n.get("app_brand"), size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_ON_PRIMARY),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=5),
            padding=ft.padding.only(top=20, bottom=10),
        )

        # 3. Navigation Rail
        self.nav_rail = ft.NavigationRail(
            selected_index=int(self._current_tab_index),
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            min_extended_width=200,
            bgcolor=AppColors.PRIMARY_DARK,
            indicator_color=AppColors.ACCENT,
            leading=brand_header,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.DASHBOARD_OUTLINED,
                    selected_icon=ft.Icons.DASHBOARD,
                    label=I18n.get("nav_market"),
                    label_content=ft.Text(I18n.get("nav_market"), color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FILTER_ALT_OUTLINED,
                    selected_icon=ft.Icons.FILTER_ALT,
                    label=I18n.get("nav_screener"),
                    label_content=ft.Text(I18n.get("nav_screener"), color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.STORAGE_OUTLINED,
                    selected_icon=ft.Icons.STORAGE_ROUNDED,
                    label=I18n.get("nav_data"),
                    label_content=ft.Text(I18n.get("nav_data"), color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label=I18n.get("nav_settings"),
                    label_content=ft.Text(I18n.get("nav_settings"), color=AppColors.TEXT_ON_PRIMARY),
                ),
            ],
            on_change=self._on_nav_change,
        )

        # 4. Body Container
        self.body = ft.Container(
            content=self.views[0],
            expand=True,
            padding=20,
            bgcolor=AppColors.BACKGROUND,
        )

        # 5. Main Layout Row
        self.content = ft.Row(
            [
                self.nav_rail,
                ft.VerticalDivider(width=1),
                self.body
            ],
            expand=True,
        )

    def show(self):
        """Mount this layout to the page"""
        self.page.clean()
        self.page.add(self)
        self.page.update()
        
        # Start Background Services
        self._start_services()

    def _start_services(self):
        """Start required background services"""
        # Subscribe to News Alerts
        NewsSubscriptionService().add_listener(self._on_news_alert, is_alert=True)
        NewsSubscriptionService().start()
        
        # Start Market Data
        MarketDataService().start()

    def _subscribe_events(self):
        """Subscribe to global events"""
        I18n.subscribe(self._on_locale_change)

    def _on_locale_change(self):
        """Handle i18n locale change"""
        self.page.title = I18n.get("app_title")
        if self.nav_rail:
            nav_keys = ["nav_market", "nav_screener", "nav_data", "nav_settings"]
            for i, key in enumerate(nav_keys):
                if i < len(self.nav_rail.destinations):
                    text = I18n.get(key)
                    self.nav_rail.destinations[i].label = text
                    self.nav_rail.destinations[i].label_content.value = text
            self.nav_rail.update()
        self.page.update()

    def _on_news_alert(self, msg):
        """Handle news alert snackbar"""
        try:
            snackbar = ft.SnackBar(
                content=ft.Text(f"📰 {msg}"),
                duration=5000,
                open=True
            )
            self.page.open(snackbar)
            self.page.update()
        except Exception as e:
            logger.error(f"[AppLayout] Failed to show news alert: {e}")

    def _on_nav_change(self, e):
        """Handle navigation/tab change event"""
        self.change_tab(e.control.selected_index)

    def change_tab(self, index: int):
        """Change tab with debounce logic"""
        if index == self._current_tab_index:
            return

        self._pending_tab_index = index

        # Cancel previous pending switch
        if self._debounce_task:
            self._debounce_task.cancel()

        # Schedule new switch
        self._debounce_task = self.page.run_task(self._execute_tab_switch)

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

        # Optimize HomeView visibility
        if hasattr(self.home_view, 'set_visible'):
            self.home_view.set_visible(index == NavTabs.MARKET)

        # Switch Content
        self.body.content = self.views[index]
        self._current_tab_index = index
        self.nav_rail.selected_index = index # Ensure UI sync if called programmatically
        
        self.body.update()
        self.nav_rail.update()
        
        logger.debug(f"[AppLayout] Tab switch done in {(_time.perf_counter() - _t0) * 1000:.1f}ms")

    async def run_strategy_from_home(self, strategy_key):
        """Callback to switch to Screener and run strategy"""
        # Switch to Screener Tab
        self.change_tab(NavTabs.SCREENER)
        
        # Wait for switch (Since change_tab is debounced/async, we might need to ensure it happens)
        # However, change_tab spawns disjoint task. 
        # Ideally we force immediate switch for this user interaction.
        
        # Force immediate switch logic for direct action
        if self._debounce_task: self._debounce_task.cancel()
        
        self._pending_tab_index = NavTabs.SCREENER
        await self._execute_tab_switch() # Await directly
        
        # Trigger Strategy
        await self.screener_view.select_and_run_strategy(strategy_key)
