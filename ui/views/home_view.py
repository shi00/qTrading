import asyncio
import logging

import flet as ft
import pandas as pd

from data.data_processor import DataProcessor
from data.market_data_service import MarketDataService
from data.news_subscription import NewsSubscriptionService
from ui.i18n import I18n
from ui.theme import AppColors

# New Components
from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed

logger = logging.getLogger(__name__)


class HomeView(ft.Container):
    def __init__(self, on_run_strategy=None):
        super().__init__()
        self.expand = True
        self.processor = DataProcessor()
        self.on_run_strategy = on_run_strategy

        # Pagination State (Controller Logic)
        self.news_page = 0
        self.PAGE_SIZE = 20
        self.has_more_news = False
        self._is_loading_more = False

        self._init_task = None
        self._is_mounted = False
        self._is_visible = True
        self._data_loaded = False
        self._pubsub_subscribed = False

        # Data Cache
        self.last_data = {}
        self.news_data = None

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

        # Subscribe to locale changes
        # Moved to did_mount to avoid updates when unmounted
        # I18n.subscribe(self.refresh_locale) 

    def _build_header(self):
        # We need dynamic date, so we keep a ref to the date text
        self.date_text = ft.Text("--", size=12, color=ft.Colors.GREY)
        return ft.Row([
            self.header_title,
            ft.Container(expand=True),
            self.date_text,
            ft.IconButton(ft.Icons.REFRESH, on_click=self._refresh_data, tooltip=I18n.get("home_refresh"))
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def refresh_locale(self):
        """Rebuild/Update UI on locale change"""
        try:
            # Update Header/static texts in HomeView
            self.header_title.value = I18n.get("home_title")
            self.header_title.update()
            
            self.news_header.value = I18n.get("home_live_news")
            self.news_header.update()

            # Delegate localization to components
            self.dashboard.update_locale()
            self.news_feed.update_locale()

            # Re-bind data if available to ensure localized formatters/tags run
            if self.last_data:
                self.dashboard.update_data(self.last_data)
            
            if self.news_data is not None:
                self.news_feed.set_news(self.news_data, self.has_more_news)
                
        except Exception as e:
            logger.error(f"Error refreshing locale: {e}")

    def _run_if_visible(self, task_func, log_msg="Refreshing"):
        if not self._is_visible: 
            logger.debug(f"[HomeView] Skipping {log_msg} - not visible")
            return
        if not self._is_mounted:
            logger.debug(f"[HomeView] Skipping {log_msg} - not mounted")
            return
        
        logger.debug(f"[HomeView] {log_msg}")
        if self.page:
            self.page.run_task(task_func)

    def refresh_news_if_visible(self):
        self._run_if_visible(self._refresh_news_only, "Refreshing news list")
    
    def refresh_market_if_visible(self):
        self._run_if_visible(self._refresh_from_cache, "Refreshing market data from cache")

    def did_mount(self):
        self._is_mounted = True
        
        # Subscribe to broadcast messages
        if self.page and not self._pubsub_subscribed:
            self.page.pubsub.subscribe(self._on_broadcast_message)
            self._pubsub_subscribed = True

        # Observer Pattern
        logger.debug(f"[HomeView] Registering listeners")
        NewsSubscriptionService().add_listener(self.refresh_news_if_visible)
        MarketDataService().add_listener(self.refresh_market_if_visible)
        # Subscribe to locale changes only while mounted
        I18n.subscribe(self.refresh_locale)

        # Init Data
        if not self._data_loaded:
            if self.page:
                self._init_task = self.page.run_task(self._init_and_load)
        else:
            # Restore state
            if self.last_data:
                self.dashboard.update_data(self.last_data)
            if self.news_data is not None:
                self.news_feed.set_news(self.news_data, self.has_more_news)
            self.update()

    def will_unmount(self):
        self._is_mounted = False
        try:
            NewsSubscriptionService().remove_listener(self.refresh_news_if_visible)
            MarketDataService().remove_listener(self.refresh_market_if_visible)
            I18n.unsubscribe(self.refresh_locale)
        except Exception:
            pass
        if self._init_task:
            self._init_task.cancel()

    def set_visible(self, visible: bool):
        if self._is_visible != visible:
            self._is_visible = visible
            logger.info(f"[HomeView] Visibility changed to: {visible}")

    def _on_broadcast_message(self, message):
        if message == "cache_cleared":
            self.last_data = {}
            self.news_data = None
            self.has_more_news = False
            self.news_page = 0
            self._data_loaded = False
            
            # Clear UI
            self.dashboard.update_data({}) # Or clear
            self.news_feed.set_news(None, False)
            
            if self.page and self._is_mounted:
                self.update()

    def _refresh_data(self, e):
        if self.page:
            self.page.run_task(self._load_data)

    async def _init_and_load(self):
        try:
            if not self._is_mounted: return
            await self.processor.init_data()
            if not self._is_mounted: return
            await self._load_data()
            if self._is_mounted:
                self._data_loaded = True
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[HomeView] Init failed: {e}")

    async def _load_data(self):
        try:
            # 1. Market Data
            data = None
            for _ in range(5):
                data = MarketDataService().get_cached_data()
                if data: break
                await asyncio.sleep(0.5)
            
            if not data: return

            self.last_data = data
            # Update Header Date
            date_str = data.get('date', '--')
            self.date_text.value = I18n.get("home_data_date").format(date=date_str)
            self.date_text.update()
            
            # Update Dashboard Component
            self.dashboard.update_data(data)

            # 2. News Data
            self.news_page = 0
            self.has_more_news = True
            await self._load_news_data() # This updates self.news_data
            
            # Update News Feed Component
            self.news_feed.set_news(self.news_data, self.has_more_news)
            
            # We don't need self.update() anymore because components updated themselves!
            # But just in case for the layout container:
            # self.update() 

        except Exception as e:
            logger.error(f"Error loading home data: {e}")

    async def _refresh_from_cache(self):
        """Quick refresh for market data only"""
        try:
            data = MarketDataService().get_cached_data()
            if not data: return
            
            self.last_data = data
            
            # Update Header
            date_str = data.get('date', '--')
            self.date_text.value = I18n.get("home_data_date").format(date=date_str)
            self.date_text.update()
            
            # Update Dashboard
            self.dashboard.update_data(data)
            
            logger.debug("[HomeView] UI refreshed from cache (Efficient)")
        except Exception as e:
            logger.error(f"[HomeView] Failed to refresh from cache: {e}")

    async def _refresh_news_only(self):
        """Refresh only news section"""
        try:
            # Refresh Page 0
            await self._load_news_data(0)
            self.news_feed.set_news(self.news_data, self.has_more_news)
            logger.debug("[HomeView] News section refreshed (Efficient)")
        except Exception as e:
            logger.error(f"[HomeView] Failed to refresh news: {e}")

    async def _on_load_more_click(self, e):
        if self._is_loading_more or not self.has_more_news:
            return

        self._is_loading_more = True
        try:
            next_page = self.news_page + 1
            # Fetch next page
            new_batch = await self.fetch_news_batch(next_page)
            
            if new_batch is not None and not new_batch.empty:
                # Update State
                self.news_data = pd.concat([self.news_data, new_batch], ignore_index=True)
                self.news_page = next_page
                
                # Check has more
                if len(new_batch) < self.PAGE_SIZE:
                    self.has_more_news = False
                else:
                    self.has_more_news = True
                
                # Append to Component
                self.news_feed.append_news(new_batch, self.has_more_news)
            else:
                self.has_more_news = False
                # Update component to remove button if needed
                self.news_feed.append_news(pd.DataFrame(), False)

        finally:
            self._is_loading_more = False

    async def _load_news_data(self, target_page=0):
        """Helper to load page 0 and set state"""
        new_batch = await self.fetch_news_batch(target_page)
        if new_batch is None: return False
        
        if new_batch.empty:
            self.has_more_news = False
            if target_page == 0:
                self.news_data = None
        else:
            if target_page == 0:
                self.news_data = new_batch
                self.news_page = 0
            
            if len(new_batch) < self.PAGE_SIZE:
                self.has_more_news = False
            else:
                self.has_more_news = True
        return True

    async def fetch_news_batch(self, page):
        try:
            offset = page * self.PAGE_SIZE
            return await self.processor.cache.get_market_news(
                limit=self.PAGE_SIZE,
                offset=offset
            )
        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            return None
