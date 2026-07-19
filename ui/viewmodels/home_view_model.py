import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from data.data_processor import DataProcessor
from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsRow:
    """新闻行数据 frozen dataclass (L771 合规: 行数据用 frozen dataclass).

    替代 dual-track 的 DataFrame 持有模式, 直接放入 state.
    """

    content: str = ""
    tags: str = ""
    source: str = ""
    publish_time: str = ""


@dataclass(frozen=True)
class MarketIndexRow:
    """大盘指数行数据 (SH/SZ/CYB)."""

    name: str = ""
    value: str = "--"
    change: str = "--"
    color: str = ""


@dataclass(frozen=True)
class HsgtRow:
    """北向资金行数据."""

    value: str = "--"
    color: str = ""
    sub: str = "--"


@dataclass(frozen=True)
class HotConceptRow:
    """热门概念行数据."""

    name: str = "--"
    change: str = "0.00%"
    color: str = ""


@dataclass(frozen=True)
class HomeState:
    """HomeViewModel 的不可变状态快照 (L770/L771 合规).

    所有业务数据直接放入 state, 用 tuple[Row, ...] 替代 DataFrame/dict.
    View = f(state), 无 dual-track.
    """

    news_page: int = 0
    has_more_news: bool = False
    is_loading_more: bool = False
    # 业务数据 (tuple[Row, ...], 符合 L771)
    news_rows: tuple[NewsRow, ...] = ()
    market_indices: tuple[MarketIndexRow, ...] = ()
    market_hsgt: HsgtRow = field(default_factory=HsgtRow)
    market_hot_concepts: tuple[HotConceptRow, ...] = ()
    market_date: str = "--"
    market_stale: bool = False


class HomeViewModel(ObservableViewModelMixin[HomeState]):
    """
    ViewModel for HomeView.
    Handles data fetching, state management, and service subscriptions.
    Follows "Supervising Controller" pattern.

    L771 合规: state 字段全部用 tuple[Row, ...] / frozen dataclass,
    VM 内部不持有 DataFrame/dict 作为业务状态 (移除 dual-track).
    """

    PAGE_SIZE = 20  # 常量,不放入 state

    def __init__(self):
        self.processor = DataProcessor()

        # Internal state (frozen snapshot)
        self._state = HomeState()
        self._subscribers: list[Callable[[HomeState], None]] = []

        # Concurrency Control
        self._load_generation = 0  # Prevent race conditions

    def _notify(self) -> None:
        """覆盖 _notify: 用 try/except 包裹 subscriber 调用, 不让单个异常中断通知。"""
        for cb in list(self._subscribers):
            try:
                cb(self._state)
            except Exception as e:
                logger.warning("[HomeVM] Subscriber error: %s", e, exc_info=True)

    def init(self) -> None:
        """Initialize subscriptions (无回调参数,View 通过 subscribe 订阅 state)。"""
        NewsSubscriptionService().add_listener(self._on_news_service_update)
        MarketDataService().add_listener(self._on_market_service_update)

    def dispose(self) -> None:
        """覆盖 dispose: 移除 service listener, 再清订阅者。"""
        try:
            NewsSubscriptionService().remove_listener(self._on_news_service_update)
            MarketDataService().remove_listener(self._on_market_service_update)
        except Exception as e:
            logger.warning("[HomeVM] Dispose error: %s", e, exc_info=True)
        super().dispose()

    @staticmethod
    def register_news_alert_listener(callback: Callable[[str], None]) -> None:
        """注册新闻告警监听 (View 通过 VM 命令转发, 不直调 NewsSubscriptionService).

        参照 ``init`` 范例 (CLAUDE.md §3.2 MVVM): View 持有需要 page 访问的 toast 回调,
        通过本命令转发注册到 NewsSubscriptionService 的 alert listener 集合。

        P2-2: 改为 staticmethod 避免调用方临时实例化 HomeViewModel (startup_views.py
        启动期无业务状态需求, 仅需转发到 NewsSubscriptionService 单例)。
        """
        NewsSubscriptionService().add_listener(callback, is_alert=True)

    @staticmethod
    def unregister_news_alert_listener(callback: Callable[[str], None]) -> None:
        """退订新闻告警监听 (与 register_news_alert_listener 配对)。"""
        NewsSubscriptionService().remove_listener(callback, is_alert=True)

    # --- Service Event Handlers ---

    async def _on_news_service_update(self, update_type=None, data=None):
        """NewsSubscriptionService listener (async, 在事件循环线程执行).

        NewsSubscriptionService._notify_listeners 支持 async listener
        (inspect.iscoroutinefunction 检测), 在事件循环线程 await 调用,
        可以直接调 await refresh_news() 处理 INITIAL 类型.

        竞态安全: tuple 不可变, _set_state 创建新 HomeState 实例.
        load_next_page 在 await 后重新读取 self._state.news_rows,
        获取最新快照, 不会丢失 NEW_ITEM 追加.
        """
        from services.news_subscription_service import NewsUpdateType

        if update_type == NewsUpdateType.NEW_ITEM and data:
            new_rows = tuple(_news_item_to_row(item) for item in data)
            if new_rows:
                # tuple 拼接 (同步, O(n), n 是当前新闻数量)
                self._set_state(news_rows=new_rows + self._state.news_rows)
        elif update_type == NewsUpdateType.TAG_UPDATE and data:
            content = str(data.get("content", "") or "")
            tags = str(data.get("tags", "") or "")
            if content:
                # 遍历 tuple 更新匹配项 (同步, O(n))
                updated = tuple(
                    replace(row, tags=tags) if row.content == content else row for row in self._state.news_rows
                )
                if updated != self._state.news_rows:
                    self._set_state(news_rows=updated)
        elif update_type == NewsUpdateType.INITIAL:
            # INITIAL: 全量刷新 (async, 在事件循环线程执行)
            await self.refresh_news()

    def _on_market_service_update(self):
        """MarketDataService listener (sync, 在 IO 线程池执行).

        MarketDataService._notify_listeners 总是通过
        ThreadPoolManager().run_async(TaskType.IO, listener) 提交,
        不支持 async listener. 保持 sync 签名.

        竞态安全: get_cached_data 返回引用, _set_state 创建新 HomeState.
        与 load_market_data 并发时 last-write-wins (相同数据, 无不一致).
        """
        data = MarketDataService().get_cached_data()
        if data:
            self._set_state(**_market_data_to_state_fields(data))

    # --- Data Actions ---

    async def init_data(self):
        """Initialize data processor"""
        await self.processor.init_data()

    async def load_market_data(self):
        """
        Fetch latest market data with retry logic.
        Updates state directly (无 dual-track).
        Returns: dict or None (保留返回值兼容测试, 但 View 不依赖返回值)
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
            self._set_state(**_market_data_to_state_fields(data))

        return data

    async def get_cached_market_data(self):
        """Get data immediately from service cache, update state."""
        data = MarketDataService().get_cached_data()
        if data:
            self._set_state(**_market_data_to_state_fields(data))
        return data

    async def refresh_news(self):
        """
        Full refresh of news (Page 0).
        Updates state.news_rows directly (无 dual-track).
        Returns: (DataFrame, has_more) — 保留返回值兼容测试
        """
        self._load_generation += 1  # Invalidate pending loads
        batch = await self._fetch_news_batch(0)

        has_more = self._state.has_more_news  # batch 为 None 时保持不变
        if batch is not None:
            if batch.empty:
                self._set_state(news_rows=(), news_page=0, has_more_news=False)
                has_more = False
            else:
                new_rows = _df_to_news_rows(batch)
                has_more = len(batch) >= self.PAGE_SIZE
                self._set_state(
                    news_rows=new_rows,
                    news_page=0,
                    has_more_news=has_more,
                )

        return batch, has_more

    async def load_next_page(self):
        """
        Load next page of news.
        Updates state.news_rows directly (无 dual-track).

        竞态安全: await 后重新读取 self._state.news_rows 获取最新快照,
        包含 await 期间 _on_news_service_update 可能追加的 NEW_ITEM.

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
                new_rows = _df_to_news_rows(new_batch)
                # 关键: await 后重新读取 self._state.news_rows (最新快照)
                # 可能被 _on_news_service_update(NEW_ITEM) 追加过
                current_rows = self._state.news_rows
                self._set_state(
                    news_rows=current_rows + new_rows,
                    news_page=next_page,
                    has_more_news=len(new_batch) >= self.PAGE_SIZE,
                )
                return new_batch, len(new_batch) >= self.PAGE_SIZE

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
        self._set_state(
            news_rows=(),
            news_page=0,
            has_more_news=False,
            market_indices=(),
            market_hsgt=HsgtRow(),
            market_hot_concepts=(),
            market_date="--",
            market_stale=False,
        )


# ============================================================
# 纯转换函数 (DataFrame/dict → tuple[Row, ...])
# 模块级, 无副作用, 可独立测试
# ============================================================


def _news_item_to_row(item: Any) -> NewsRow:
    """单个 news item (dict) → NewsRow.

    复用 CacheManager.normalize_news_item 保证字段一致.
    """
    from data.cache.cache_manager import CacheManager

    normalized = CacheManager.normalize_news_item(item, default_source="CLS")
    return NewsRow(
        content=str(normalized.get("content", "") or ""),
        tags=str(normalized.get("tags", "") or ""),
        source=str(normalized.get("source", "") or ""),
        publish_time=str(normalized.get("publish_time", "") or ""),
    )


def _df_to_news_rows(df: pd.DataFrame | None) -> tuple[NewsRow, ...]:
    """DataFrame → tuple[NewsRow, ...] (L771 合规)."""
    if df is None or df.empty:
        return ()
    return tuple(
        NewsRow(
            content=str(row.get("content", "") or ""),
            tags=str(row.get("tags", "") or ""),
            source=str(row.get("source", "") or ""),
            publish_time=str(row.get("publish_time", "") or ""),
        )
        for row in df.to_dict("records")
    )


def _market_data_to_state_fields(data: dict) -> dict[str, Any]:
    """dict → HomeState 字段 dict (供 _set_state 使用).

    将 market_data dict 转换为 tuple[MarketIndexRow, ...] 等不可变字段.
    """
    indices = data.get("indices") or []
    market_indices = tuple(
        MarketIndexRow(
            name=str(idx.get("name", "")),
            value=str(idx.get("value", "--")),
            change=str(idx.get("change", "--")),
            color=str(idx.get("color", "")),
        )
        for idx in indices
        if isinstance(idx, dict)
    )

    hsgt = data.get("hsgt") or {}
    market_hsgt = HsgtRow(
        value=str(hsgt.get("value", "--")),
        color=str(hsgt.get("color", "")),
        sub=str(hsgt.get("sub", "--")),
    )

    hot_concepts = data.get("hot_concepts") or []
    market_hot_concepts = tuple(
        HotConceptRow(
            name=str(item.get("name", "--")),
            change=str(item.get("change", "0.00%")),
            color=str(item.get("color", "")),
        )
        for item in hot_concepts
        if isinstance(item, dict)
    )

    return {
        "market_indices": market_indices,
        "market_hsgt": market_hsgt,
        "market_hot_concepts": market_hot_concepts,
        "market_date": str(data.get("date", "--")),
        "market_stale": bool(data.get("stale", False)),
    }
