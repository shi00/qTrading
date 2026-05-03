"""
MarketDataService - 市场数据后台服务

负责定时获取市场概览数据（指数、北向资金、热点概念），
UI 只从内存缓存读取，类似 NewsSubscriptionService 架构。
"""

import asyncio
import logging
import math
import threading
import typing

from data.cache.cache_manager import CacheManager
from data.domain_services.trade_calendar_service import TradeCalendarService
from data.external.news_fetcher import NewsFetcher
from data.external.tushare_client import TushareClient
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


from utils.singleton_registry import register_singleton


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
            cls._instance = None
            cls._initialized = False

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
        self._initialized = True

    def add_listener(self, callback: typing.Callable | None):
        """Add a listener for market data updates"""
        self._listeners.add(callback)
        logger.info(f"[MarketDataService] Added listener: {callback}")

    def remove_listener(self, callback: typing.Callable | None):
        """Remove a listener"""
        try:
            self._listeners.remove(callback)
            logger.info(f"[MarketDataService] Removed listener: {callback}")
        except KeyError:
            pass

    def start(self):
        """
        启动服务。
        """
        if self._running:
            return

        self._running = True

        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[MarketDataService] Started market data polling service")

    def stop(self):
        """停止服务并重置状态"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

        # 清理状态
        # 清理状态
        self._cached_data = None
        # Note: Do not clear listeners, let components unregister themselves

        logger.info("[MarketDataService] Stopped market data polling service")

    def get_cached_data(self) -> dict:
        """
        获取缓存的市场数据。

        Returns:
            dict: 包含 date, indices, hsgt, hot_concepts 的字典，
                  如果缓存为空则返回 None
        """
        return self._cached_data  # type: ignore

    async def _poll_loop(self):
        """主轮询循环"""
        # 首次立即执行一次
        await self._safe_fetch()

        while self._running:
            try:
                # 动态获取配置的刷新间隔（默认30秒）
                interval = ConfigHandler.get_market_data_poll_interval()
                await asyncio.sleep(interval)
                if self._running:
                    await self._safe_fetch()
            except asyncio.CancelledError:
                break

    async def _safe_fetch(self):
        """安全获取数据（带异常处理）"""
        if not self._running:
            return

        try:
            await self._fetch_market_data()
        except Exception as e:
            logger.error(
                f"[MarketDataService] Error fetching market data: {e}",
                exc_info=True,
            )

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

        results = await asyncio.gather(index_task, hsgt_task, concepts_task, return_exceptions=True)

        indices = (
            results[0]
            if not isinstance(results[0], Exception)
            else [MarketDataService._get_empty_index_data_static(key) for _, key in self.INDICES_CONFIG]
        )
        if isinstance(results[0], Exception):
            logger.warning(f"[MarketDataService] Indices batch fetch failed: {results[0]}")
        hsgt = results[1] if not isinstance(results[1], Exception) else MarketDataService._get_empty_hsgt_data_static()
        if isinstance(results[1], Exception):
            logger.warning(f"[MarketDataService] HSGT fetch failed: {results[1]}")
        hot_concepts = results[2] if not isinstance(results[2], Exception) else []
        if isinstance(results[2], Exception):
            logger.warning(f"[MarketDataService] Hot concepts fetch failed: {results[2]}")

        self._cached_data = {
            "date": date,
            "indices": indices,
            "hsgt": hsgt,
            "hot_concepts": hot_concepts,
        }

        listener_count = len(self._listeners)
        if listener_count > 0:
            for listener in list(self._listeners):
                try:
                    listener()
                except Exception as e:
                    logger.error(f"[MarketDataService] Listener error: {e}")

    async def _get_indices_batch(self, codes: list[str], date: str) -> list[dict]:
        """批量获取指数数据 - 1次DB/API调用替代N次"""
        if not codes:
            return []
        code_to_key = {code: key for code, key in self.INDICES_CONFIG}
        df = await self.cache.get_index_daily_range(codes, start_date=date, end_date=date)

        if df is None or df.empty:
            df = await self.api.get_index_daily(trade_date=date)

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

    async def _get_hsgt(self, date: str) -> dict:
        """获取北向资金数据 - 优先从缓存获取，缓存无数据时调用 API"""
        df = await self.cache.get_moneyflow_hsgt(trade_date=date)

        if df is None or df.empty:
            df = await self.api.get_moneyflow_hsgt(trade_date=date)

        if df is not None and not df.empty:
            val = self._safe_float(df.iloc[0].get("north_money"))
            # north_money unit: 百万元 (1 Million CNY). >100 (=1亿) -> 亿 display.
            return {
                "name": I18n.get("home_northbound"),
                "value": f"{val / 100:.2f}{I18n.get('unit_yi')}"
                if abs(val) > 100
                else f"{val:.0f}{I18n.get('unit_wan')}",
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
            if math.isnan(f):
                return 0.0
            return f
        except (ValueError, TypeError):
            return 0.0
