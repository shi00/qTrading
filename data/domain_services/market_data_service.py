"""
MarketDataService - 市场数据后台服务

负责定时获取市场概览数据（指数、北向资金、热点概念），
UI 只从内存缓存读取，类似 NewsSubscriptionService 架构。
"""

import asyncio
import datetime
import logging
import math
import threading
import typing

import pandas as pd

from core.i18n import I18n
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.singleton_registry import register_singleton
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now
from data.cache.cache_manager import CacheManager
from data.constants import get_column_unit
from data.domain_services.trade_calendar_service import TradeCalendarService
from data.external.news_fetcher import NewsFetcher
from data.external.tushare_client import TushareClient

logger = logging.getLogger(__name__)


@register_singleton
class MarketDataService:
    """
    后台服务：定时获取市场概览数据并缓存。
    单例模式，确保全局只有一个实例。
    """

    _instance = None
    _lock = threading.Lock()  # Thread-safe singleton

    # 刷新间隔（秒）
    # 指数配置：代码 -> I18n Key
    INDICES_CONFIG = [
        ("000001.SH", "home_index_sh"),
        ("399001.SZ", "home_index_sz"),
        ("399006.SZ", "home_index_cyb"),
    ]

    HOT_CONCEPTS_LIMIT = 8

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            inst = cls._instance
            cls._instance = None
            cls._initialized = False
        if inst is not None:
            inst._listeners.clear()

    @classmethod
    def _atexit_cleanup(cls):
        """Cleanup background tasks on process exit."""
        if cls._instance is None:
            return
        inst = cls._instance
        # Cancel main poll loop task
        task = getattr(inst, "_task", None)
        if task is not None and not task.done():
            task.cancel()
        # Cancel background tasks (e.g. scheduled stop_async)
        tasks = getattr(inst, "_background_tasks", None)
        if not isinstance(tasks, set):
            return
        for t in list(tasks):
            if not t.done():
                t.cancel()

    def __init__(self):
        if self._initialized:
            return

        self.api = TushareClient()
        self.cache = CacheManager()
        self.trade_calendar = TradeCalendarService(self.cache, self.api)
        self._running = False
        self._task = None
        self._cached_data = None
        self._listeners = set()
        self._background_tasks = set()
        self._initialized = True

    def add_listener(self, callback: typing.Callable | None):
        """Add a listener for market data updates"""
        self._listeners.add(callback)
        logger.info("[MarketDataService] Added listener: %s", callback)

    def remove_listener(self, callback: typing.Callable | None):
        """Remove a listener"""
        try:
            self._listeners.remove(callback)
            logger.info("[MarketDataService] Removed listener: %s", callback)
        except KeyError:
            pass

    async def start(self):
        """
        启动服务。

        Must be called within a running event loop (e.g. from async context).
        """
        if self._running:
            return

        self._running = True

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[MarketDataService] Started market data polling service")

    def stop(self):
        """Stop the market data polling service.

        Schedules stop_async() and returns immediately. The scheduled task
        is tracked in _background_tasks to prevent GC.

        Note: For guaranteed cleanup (e.g. during shutdown), use
        ``await stop_async()`` instead.
        """
        if not self._running:
            return
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                task = loop.create_task(self.stop_async())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            self._task = None
            self._cached_data = None

        logger.info("[MarketDataService] Stopped market data polling service")

    async def stop_async(self, timeout: float = 3.0):
        """停止服务并等待后台任务完成"""
        self._running = False
        if self._task:
            if not self._task.done():
                self._task.cancel()
                try:
                    await asyncio.wait_for(self._task, timeout=timeout)
                except asyncio.CancelledError:
                    # 区分：stop_async 本身被外部取消（必须传播 R2）vs
                    # _task 被主动取消后的预期 CancelledError（cancelling()==0 时吞没合理）
                    if asyncio.current_task().cancelling() > 0:
                        raise  # R2: stop_async 被外部取消，必须传播
                except TimeoutError:
                    logger.warning(
                        "[MarketDataService] stop_async timeout (%.1fs), poll loop task did not finish",
                        timeout,
                    )
            self._task = None

        self._cached_data = None
        logger.info("[MarketDataService] Stopped market data polling service (async graceful)")

    def get_cached_data(self) -> dict:
        """
        获取缓存的市场数据。

        Returns:
            dict: 包含 date, indices, hsgt, hot_concepts 的字典，
                  如果缓存为空则返回 None
        """
        return self._cached_data  # type: ignore[return-value]

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _poll_loop(self):
        """主轮询循环"""
        # 首次立即执行一次
        await self._safe_fetch()

        while self._running:
            try:
                # 动态获取配置的刷新间隔（默认30秒）
                interval = ConfigHandler.get_market_data_poll_interval()
                # token 已熔断时延长轮询间隔，避免 fast-fail 每 30s 刷屏 WARNING；
                # 用户重新 set_token 后 _token_invalid 自动重置为 False，下次轮询恢复常规间隔。
                if self.api.is_token_invalid:
                    interval = max(interval, 300)  # 5 分钟探测一次
                await asyncio.sleep(interval)
                if self._running:
                    await self._safe_fetch()
            except asyncio.CancelledError:
                logger.warning("[MarketDataService] Poll loop cancelled during shutdown.")
                raise

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _safe_fetch(self):
        """安全获取数据（带异常处理）"""
        if not self._running:
            return

        try:
            await self._fetch_market_data()
        except asyncio.CancelledError:
            logger.warning("[MarketDataService] Cancelled during fetch.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(
                    "[MarketDataService] SYSTEM-LEVEL failure: %s",
                    DataSanitizer.sanitize_error(e),
                )
            elif severity == "recoverable":
                logger.warning(
                    "[MarketDataService] Recoverable error (%s): %s",
                    error_info["code"],
                    DataSanitizer.sanitize_error(e),
                )
            else:
                logger.error(
                    "[MarketDataService] Operational error: %s",
                    DataSanitizer.sanitize_error(e),
                )
            logger.debug("[MarketDataService] Fetch error traceback", exc_info=True)

    @log_async_operation(
        operation_name="fetch_market_data",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def _fetch_market_data(self):
        """获取市场概览数据"""
        now = get_now()
        today_date = now.date()

        latest_date = await self.trade_calendar.get_latest_trade_date()
        if latest_date:
            date = latest_date.strftime("%Y%m%d")
        else:
            date = today_date.strftime("%Y%m%d")

        index_codes = [code for code, _ in self.INDICES_CONFIG]
        index_task = self._get_indices_batch(index_codes, date)
        hsgt_task = self._get_hsgt(date)
        concepts_task = NewsFetcher.get_hot_concepts(limit=self.HOT_CONCEPTS_LIMIT)

        results = await gather_return_exceptions_propagating_cancel(index_task, hsgt_task, concepts_task)
        results = list(results)

        data_stale = False
        eval_date = latest_date or today_date
        indices_empty = isinstance(results[0], Exception) or all(
            d.get("value") == "-" for d in (results[0] if not isinstance(results[0], Exception) else [])
        )
        hsgt_empty = isinstance(results[1], Exception) or (
            not isinstance(results[1], Exception) and results[1].get("value") == "-"
        )
        # MKT-002: Data readiness fallback.
        # ``get_latest_trade_date()`` uses MARKET_CLOSE_HOUR (15:00) to decide
        # whether "today" is the latest trade date. Between 15:00 and the
        # completion of data ingestion, querying today's data returns empty.
        # This block detects that gap (indices/hsgt empty on the latest trade
        # date) and transparently falls back to the previous trade date. The
        # ``data_stale`` flag is surfaced to the UI so users know the data is
        # not up-to-date. This is preferred over modifying
        # ``get_latest_trade_date()`` because "latest trade date" is a
        # calendar concept, while "data ready" is an ingestion concept —
        # conflating them would leak ingestion state into the calendar API.
        if indices_empty or hsgt_empty:
            prev_date_val = self.trade_calendar.get_prev_trade_date(eval_date)
            if isinstance(prev_date_val, datetime.date):
                prev_date = prev_date_val
            elif asyncio.iscoroutine(prev_date_val):
                prev_date = await prev_date_val
            else:
                prev_date = None

            if prev_date:
                prev_str = prev_date.strftime("%Y%m%d")
                fb_indices_task = self._get_indices_batch(index_codes, prev_str) if indices_empty else asyncio.sleep(0)
                fb_hsgt_task = self._get_hsgt(prev_str) if hsgt_empty else asyncio.sleep(0)
                fb_results = await gather_return_exceptions_propagating_cancel(fb_indices_task, fb_hsgt_task)
                if indices_empty:
                    results[0] = fb_results[0]
                if hsgt_empty:
                    results[1] = fb_results[1]
                data_stale = True
                if indices_empty and hsgt_empty:
                    date = prev_str

        indices = (
            results[0]
            if not isinstance(results[0], Exception)
            else [MarketDataService._get_empty_index_data_static(key) for _, key in self.INDICES_CONFIG]
        )
        if isinstance(results[0], Exception):
            logger.warning(
                "[MarketDataService] Indices batch fetch failed: %s", DataSanitizer.sanitize_error(results[0])
            )
        hsgt = results[1] if not isinstance(results[1], Exception) else MarketDataService._get_empty_hsgt_data_static()
        if isinstance(results[1], Exception):
            logger.warning("[MarketDataService] HSGT fetch failed: %s", DataSanitizer.sanitize_error(results[1]))
        hot_concepts = results[2] if not isinstance(results[2], Exception) else None
        if isinstance(results[2], Exception):
            logger.warning(
                "[MarketDataService] Hot concepts fetch failed: %s", DataSanitizer.sanitize_error(results[2])
            )
            # Preserve previous hot_concepts on failure; only update on success
            hot_concepts = self._cached_data.get("hot_concepts", []) if self._cached_data else []

        self._cached_data = {
            "date": date,
            "indices": indices,
            "hsgt": hsgt,
            "hot_concepts": hot_concepts,
            "stale": data_stale,
        }

        listener_count = len(self._listeners)
        if listener_count > 0:
            for listener in list(self._listeners):
                try:
                    await ThreadPoolManager().run_async(TaskType.IO, listener)
                except Exception as e:
                    logger.error("[MarketDataService] Listener error: %s", DataSanitizer.sanitize_error(e))

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_indices_batch(self, codes: list[str], date: str) -> list[dict]:
        """批量获取指数数据 - 1次DB/API调用替代N次"""
        if not codes:
            return []
        code_to_key = {code: key for code, key in self.INDICES_CONFIG}
        df = await self.cache.get_index_daily_range(codes, start_date=date, end_date=date)

        if df is None or df.empty:
            results = await gather_return_exceptions_propagating_cancel(
                *(self.api.get_index_daily(ts_code=code, trade_date=date) for code in codes),
            )
            valid = [r for r in results if isinstance(r, pd.DataFrame) and not r.empty]
            df = pd.concat(valid, ignore_index=True) if valid else pd.DataFrame()

        result_map: dict[str, dict] = {}
        if df is not None and not df.empty:
            for ts_code in codes:
                row_df = df[df["ts_code"] == ts_code]
                if row_df.empty:
                    continue
                row = row_df.iloc[0]
                c = self._safe_float(row.get("pct_chg"))
                v = self._safe_float(row.get("close"))
                name_key = code_to_key.get(ts_code, "")
                color = "red" if c > 0 else "green" if c < 0 else "grey"
                result_map[ts_code] = {
                    "name": I18n.get(name_key),
                    "value": f"{v:.2f}",
                    "change": f"{c:+.2f}%",
                    "color": color,
                }

        indices = []
        for code in codes:
            if code in result_map:
                indices.append(result_map[code])
            else:
                name_key = code_to_key.get(code, "")
                indices.append(MarketDataService._get_empty_index_data_static(name_key))
        return indices

    @staticmethod
    def _get_empty_index_data_static(name_key: str) -> dict:
        return {
            "name": I18n.get(name_key),
            "value": "-",
            "change": "-",
            "color": "grey",
        }

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_hsgt(self, date: str) -> dict:
        """获取北向资金数据 - 优先从缓存获取，缓存无数据时调用 API"""
        df = await self.cache.get_moneyflow_hsgt(trade_date=date)

        if df is None or df.empty:
            df = await self.api.get_moneyflow_hsgt(trade_date=date)

        if df is not None and not df.empty:
            val = self._safe_float(df.iloc[0].get("north_money"))
            # 校验单位元数据，检测上游变更
            unit = get_column_unit(df, "north_money", default="million_cny")
            if unit != "million_cny":
                logger.warning(
                    "[MarketDataService] north_money unit changed: expected='million_cny', got='%s'. "
                    "Display conversion may be incorrect.",
                    unit,
                )
            # north_money unit: 百万元 (1 Million CNY). >100 (=1亿) -> 亿 display.
            return {
                "name": I18n.get("home_northbound"),
                "value": f"{val / 100:.2f}{I18n.get('unit_yi')}"
                if abs(val) > 100
                else f"{val * 100:.0f}{I18n.get('unit_wan')}",
                "sub": I18n.get("home_inflow") if val > 0 else I18n.get("home_outflow"),
                "color": "red" if val > 0 else "green" if val < 0 else "grey",
            }
        return MarketDataService._get_empty_hsgt_data_static()

    @staticmethod
    def _get_empty_hsgt_data_static() -> dict:
        return {
            "name": I18n.get("home_northbound"),
            "value": "-",
            "sub": "-",
            "color": "grey",
        }

    @staticmethod
    def _safe_float(val: typing.Any) -> float:
        """Safely convert value to float, defaulting to 0.0 if None/NaN"""
        try:
            if val is None:
                return 0.0
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return 0.0
            return f
        except (ValueError, TypeError):
            return 0.0
