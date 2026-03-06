
import asyncio
import logging
from utils.log_decorators import UILogger
import flet as ft
import pandas as pd

from ui.viewmodels.home_view_model import HomeViewModel
from ui.i18n import I18n
from ui.theme import AppColors
from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed

logger = logging.getLogger(__name__)

class HomeView(ft.Container):
    """
    HomeView with MVVM Architecture.
    Responsibility: Rendering and User Interaction.
    State & Logic: Delegated to HomeViewModel.
    """
    
    def __init__(self, on_run_strategy=None):
        super().__init__()
        self.expand = True
        self.on_run_strategy = on_run_strategy
        
        # Dependency Injection
        self.vm = HomeViewModel()
        
        # View State (UI only)
        self._init_task = None
        self._is_mounted = False
        self._is_visible = True
        self._pubsub_subscribed = False
        
        # --- Initialize Components ---
        self.header_title = ft.Text(I18n.get("home_title"), size=24, weight=ft.FontWeight.BOLD)
        self.header = self._build_header()
        self.dashboard = MarketDashboard()
        self.news_feed = NewsFeed(on_load_more_click=self._on_load_more_click)
        self.news_header = ft.Text(I18n.get("home_live_news"), size=20, weight=ft.FontWeight.BOLD)
        
        # Assemble Layout
        self.content = ft.Column(
            scroll=None, 
            expand=True,
            controls=[
                self.header,
                ft.Divider(),
                self.dashboard,
                self.news_header,
                self.news_feed
            ]
        )

    def _build_header(self):
        self.date_text = ft.Text("--", size=12, color=ft.Colors.GREY)
        return ft.Row([
            self.header_title,
            ft.Container(expand=True),
            self.date_text,
            ft.IconButton(ft.Icons.REFRESH, on_click=self._refresh_clicked, tooltip=I18n.get("home_refresh"))
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # --- Lifecycle ---
    
    def did_mount(self):
        self._is_mounted = True
        
        # Subscribe to PubSub (Global Events)
        if self.page and not self._pubsub_subscribed:
            self.page.pubsub.subscribe(self._on_broadcast_message)
            self._pubsub_subscribed = True

        # Initialize ViewModel
        # We pass *callbacks* that the VM calls when data is ready/updated
        logger.debug("[HomeView] Initializing ViewModel and Listeners")
        self.vm.init(
            on_news_update=self.refresh_news_if_visible,
            on_market_update=self.refresh_market_if_visible
        )
        
        # Subscribe to I18n
        I18n.subscribe(self.refresh_locale)

        # Load Data
        if self.page:
            self._init_task = self.page.run_task(self._init_and_load)

    def will_unmount(self):
        self._is_mounted = False
        self.vm.dispose()
        # Fix P1-9: Unsubscribe PubSub to prevent ghost event handling
        if self.page and self._pubsub_subscribed:
            try:
                self.page.pubsub.unsubscribe(self._on_broadcast_message)
            except Exception:
                pass
            self._pubsub_subscribed = False
        try:
            I18n.unsubscribe(self.refresh_locale)
        except Exception:
            pass
        if self._init_task:
            self._init_task.cancel()
            
    # --- Event Handlers & Callbacks ---

    def set_visible(self, visible: bool):
        if self._is_visible != visible:
            self._is_visible = visible
            logger.debug(f"[HomeView] Visibility | changed to: {visible}")

    def refresh_news_if_visible(self):
        self._run_if_visible(self._refresh_news_data, "Refreshing news list")
    
    def refresh_market_if_visible(self):
        self._run_if_visible(self._refresh_market_data, "Refreshing market data")

    def _run_if_visible(self, task_func, log_msg="Refreshing"):
        if not self._is_visible or not self._is_mounted:
            return
        if self.page:
            self.page.run_task(task_func)

    def _on_broadcast_message(self, message):
        if message == "cache_cleared":
            self.vm.clear_state()
            # Only update UI if mounted
            if self.page and self._is_mounted:
                self.dashboard.update_data({})
                self.news_feed.set_news(None, False)
                self.update()

    def _refresh_clicked(self, e):
        UILogger.log_action("HomeView", "Click", "btn_refresh")
        if self.page:
            self.page.run_task(self._load_data)

    async def _on_load_more_click(self, e):
        # Delegate to VM
        new_batch, has_more = await self.vm.load_next_page()
        if new_batch is not None and not new_batch.empty:
            self.news_feed.append_news(new_batch, has_more)
        else:
            # Update button state (remove it if no more)
            self.news_feed.append_news(pd.DataFrame(), has_more)

    def refresh_locale(self):
        """Update strings on locale change"""
        try:
            self.header_title.value = I18n.get("home_title")
            self.news_header.value = I18n.get("home_live_news")
            self.dashboard.update_locale()
            self.news_feed.update_locale()
            self.update()
        except Exception as e:
            logger.error(f"[HomeView] Locale | ❌ Refresh failed: {e}", exc_info=True)

    def update_theme(self):
        """Handle theme change"""
        try:
            # 1. Update sub-components
            self.dashboard.update_theme()
            self.news_feed.update_theme()
            
            # 2. Update local controls
            self.header_title.color = None # Default
            self.date_text.color = AppColors.TEXT_SECONDARY 
            
            # Refresh button icon?
            # It's an IconButton, default icon color might need refresh if not set?
            # It usually picks up theme primary/on_surface.
        except Exception as e:
            logger.error(f"[HomeView] Theme | ❌ Update failed: {e}", exc_info=True)

    # --- Data Loading Logic ---

    async def _init_and_load(self):
        try:
            if not self._is_mounted: return
            await self.vm.init_data()
            if not self._is_mounted: return
            await self._load_data()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[HomeView] Init | ❌ Failed: {e}", exc_info=True)

    async def _load_data(self):
        """Initial Full Load (Market + News)"""
        await self._refresh_market_data()
        await self._refresh_news_data()

    async def _refresh_market_data(self):
        try:
            # VM handles retry logic if needed, or we just get cached
            data = await self.vm.load_market_data()
            if data:
                # Update UI
                # DEBUG: Log indices count to investigate RangeError
                indices = data.get('indices', [])
                logger.debug(f"[HomeView] Market Data Indices: {len(indices)}")
                
                date_str = data.get('date', '--')
                self.date_text.value = I18n.get("home_data_date").format(date=date_str)
                self.date_text.update()
                self.dashboard.update_data(data)
        except Exception as e:
            logger.error(f"[HomeView] Market | ❌ Load failed: {e}", exc_info=True)

    async def _refresh_news_data(self):
        try:
            news_data, has_more = await self.vm.refresh_news()
            self.news_feed.set_news(news_data, has_more)
        except Exception as e:
            logger.error(f"[HomeView] News | ❌ Load failed: {e}", exc_info=True)
