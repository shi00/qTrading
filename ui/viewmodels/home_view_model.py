import asyncio
import logging

import pandas as pd

from data.data_processor import DataProcessor
from data.domain_services.market_data_service import MarketDataService
from data.external.news_subscription import NewsSubscriptionService
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class HomeViewModel:
    """
    ViewModel for HomeView.
    Handles data fetching, state management, and service subscriptions.
    Follows "Supervising Controller" pattern.
    """

    def __init__(self):
        self.processor = DataProcessor()

        # Pagination State
        self.news_page = 0
        self.PAGE_SIZE = 20
        self.has_more_news = False
        self.is_loading_more = False

        # Data Cache
        self.last_market_data = {}
        self.news_data = None

        # Callbacks (View binders)
        self.on_news_update = None
        self.on_market_update = None

        # Concurrency Control
        self._load_generation = 0  # Prevent race conditions

    def init(self, on_news_update, on_market_update):
        """Initialize subscriptions and bind callbacks"""
        self.on_news_update = on_news_update
        self.on_market_update = on_market_update

        # Subscriptions
        NewsSubscriptionService().add_listener(self._on_news_service_update)
        MarketDataService().add_listener(self._on_market_service_update)

    def dispose(self):
        """Cleanup subscriptions"""
        try:
            NewsSubscriptionService().remove_listener(self._on_news_service_update)
            MarketDataService().remove_listener(self._on_market_service_update)
        except Exception as e:
            logger.warning(f"[HomeVM] Dispose error: {e}", exc_info=True)

    # --- Service Event Handlers ---
    def _on_news_service_update(self, update_type=None, data=None):
        if self.on_news_update:
            self.on_news_update(update_type, data)

    def _on_market_service_update(self):
        if self.on_market_update:
            self.on_market_update()

    # --- Data Actions ---

    async def init_data(self):
        """Initialize data processor"""
        await self.processor.init_data()

    async def load_market_data(self):
        """
        Fetch latest market data with retry logic.
        Returns: dict or None
        """
        data = None
        for _ in range(5):
            data = MarketDataService().get_cached_data()
            if data:
                break
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise

        if data:
            self.last_market_data = data

        return data

    async def get_cached_market_data(self):
        """Get data immediately from service cache"""
        data = MarketDataService().get_cached_data()
        if data:
            self.last_market_data = data
        return data

    async def refresh_news(self):
        """
        Full refresh of news (Page 0).
        Returns: (DataFrame, has_more)
        """
        self._load_generation += 1  # Invalidate pending loads
        self.news_page = 0
        await self._fetch_news_page(0)
        return self.news_data, self.has_more_news

    async def load_next_page(self):
        """
        Load next page of news.
        Returns: (new_batch_df, has_more) or (None, False)
        """
        if self.is_loading_more or not self.has_more_news:
            return None, self.has_more_news

        self.is_loading_more = True
        current_gen = self._load_generation

        try:
            next_page = self.news_page + 1
            new_batch = await self._fetch_news_batch(next_page)

            # Check if generation changed (e.g. Refresh clicked while loading)
            if current_gen != self._load_generation:
                logger.info("[HomeVM] Load next page aborted due to generation change")
                return None, False

            if new_batch is not None and not new_batch.empty:
                # Update State
                if self.news_data is not None:
                    # Offload concatenation to thread pool to avoid blocking UI
                    self.news_data = await ThreadPoolManager().run_async(
                        TaskType.CPU,
                        pd.concat,
                        [self.news_data, new_batch],
                        ignore_index=True,
                    )
                else:
                    self.news_data = new_batch

                self.news_page = next_page

                # Check has more
                self.has_more_news = len(new_batch) >= self.PAGE_SIZE
                return new_batch, self.has_more_news
            self.has_more_news = False
            return pd.DataFrame(), False

        finally:
            self.is_loading_more = False

    async def _fetch_news_page(self, page):
        """Helper to fetch specific page and update internal state"""
        batch = await self._fetch_news_batch(page)

        if batch is None:
            return

        if page == 0:
            self.news_data = batch if not batch.empty else None

        self.has_more_news = not batch.empty and len(batch) >= self.PAGE_SIZE

    async def _fetch_news_batch(self, page):
        try:
            offset = page * self.PAGE_SIZE
            return await self.processor.cache.get_market_news(
                limit=self.PAGE_SIZE,
                offset=offset,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[HomeVM] Error fetching news: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[HomeVM] Error fetching news traceback", exc_info=True)
            return None

    def clear_state(self):
        """Reset state (e.g. on cache clear)"""
        self.last_market_data = {}
        self.news_data = None
        self.has_more_news = False
        self.news_page = 0
