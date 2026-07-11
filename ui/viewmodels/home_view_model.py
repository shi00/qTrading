import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from data.data_processor import DataProcessor
from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HomeState:
    """HomeViewModel 的不可变状态快照。

    大体积数据 (last_market_data / news_data) 不放入 state,
    通过方法返回值或 last_* property 拉取 (dual-track)。
    """

    news_page: int = 0
    has_more_news: bool = False
    is_loading_more: bool = False
    # dual-track versions (瞬态事件通知)
    news_update_version: int = 0
    market_update_version: int = 0


class HomeViewModel:
    """
    ViewModel for HomeView.
    Handles data fetching, state management, and service subscriptions.
    Follows "Supervising Controller" pattern.
    """

    PAGE_SIZE = 20  # 常量,不放入 state

    def __init__(self):
        self.processor = DataProcessor()

        # Internal state (frozen snapshot)
        self._state = HomeState()
        self._subscribers: list[Callable[[HomeState], None]] = []

        # Data Cache (大体积数据,内部持有; View 通过方法返回值或 last_* property 拉取)
        self.last_market_data: dict = {}
        self.news_data: pd.DataFrame | None = None
        self._last_news_update: tuple[Any, Any] | None = None

        # Concurrency Control
        self._load_generation = 0  # Prevent race conditions

    @property
    def state(self) -> HomeState:
        return self._state

    def subscribe(self, callback: Callable[[HomeState], None]) -> Callable[[], None]:
        """订阅 state 变更,返回取消订阅函数。"""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def _notify(self) -> None:
        for cb in self._subscribers:
            try:
                cb(self._state)
            except Exception as e:
                logger.warning("[HomeVM] Subscriber error: %s", e, exc_info=True)

    def _set_state(self, **changes: Any) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    @property
    def last_news_update(self) -> tuple[Any, Any] | None:
        """最近一次新闻服务更新事件 (update_type, data),dual-track 拉取。"""
        return self._last_news_update

    def init(self) -> None:
        """Initialize subscriptions (无回调参数,View 通过 subscribe 订阅 state)。"""
        NewsSubscriptionService().add_listener(self._on_news_service_update)
        MarketDataService().add_listener(self._on_market_service_update)

    def dispose(self) -> None:
        """Cleanup subscriptions"""
        try:
            NewsSubscriptionService().remove_listener(self._on_news_service_update)
            MarketDataService().remove_listener(self._on_market_service_update)
        except Exception as e:
            logger.warning("[HomeVM] Dispose error: %s", e, exc_info=True)
        self._subscribers.clear()

    # --- Service Event Handlers ---
    def _on_news_service_update(self, update_type=None, data=None):
        self._last_news_update = (update_type, data)
        self._set_state(news_update_version=self._state.news_update_version + 1)

    def _on_market_service_update(self):
        self._set_state(market_update_version=self._state.market_update_version + 1)

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
        batch = await self._fetch_news_batch(0)

        has_more = self._state.has_more_news  # batch 为 None 时保持不变
        if batch is not None:
            if batch.empty:
                self.news_data = None
                has_more = False
            else:
                self.news_data = batch
                has_more = len(batch) >= self.PAGE_SIZE

        self._set_state(news_page=0, has_more_news=has_more)
        return self.news_data, has_more

    async def load_next_page(self):
        """
        Load next page of news.
        Returns: (new_batch_df, has_more) or (None, False)
        """
        if self._state.is_loading_more or not self._state.has_more_news:
            return None, self._state.has_more_news

        self._set_state(is_loading_more=True)
        current_gen = self._load_generation

        try:
            next_page = self._state.news_page + 1
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

                has_more = len(new_batch) >= self.PAGE_SIZE
                self._set_state(news_page=next_page, has_more_news=has_more)
                return new_batch, has_more

            self._set_state(has_more_news=False)
            return pd.DataFrame(), False

        finally:
            self._set_state(is_loading_more=False)

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
        self._set_state(news_page=0, has_more_news=False)
