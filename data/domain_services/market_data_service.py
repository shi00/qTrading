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
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


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

        # 构建所有任务
        tasks = [self._get_index(code, key, date) for code, key in self.INDICES_CONFIG]
        tasks.append(self._get_hsgt(date))
        tasks.append(NewsFetcher.get_hot_concepts(limit=self.HOT_CONCEPTS_LIMIT))  # type: ignore

        # 并行执行
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 将结果处理逻辑放入线程池，避免阻塞事件循环
        self._cached_data = await ThreadPoolManager().run_async(
            TaskType.CPU,
            self._process_fetch_results,
            results,
            date,
            self.INDICES_CONFIG,
        )

        # 通知 UI 更新
        listener_count = len(self._listeners)
        if listener_count > 0:
            for listener in list(self._listeners):
                try:
                    listener()
                except Exception as e:
                    logger.error(f"[MarketDataService] Listener error: {e}")

    @staticmethod
    def _process_fetch_results(results: typing.Any, date: str, indices_config: typing.Any):
        """
        静态处理方法，可在线程中运行。
        解析 asyncio.gather 的结果并构建最终数据字典。
        """
        # 处理指数结果 (前N个任务)
        indices = []
        for i, (code, key) in enumerate(indices_config):
            res = results[i]
            if isinstance(res, Exception):
                logger.warning(f"[MarketDataService] Index {code} fetch failed: {res}")
                indices.append(MarketDataService._get_empty_index_data_static(key))
            else:
                indices.append(res)

        # 处理北向资金 (倒数第2个)
        hsgt_res = results[-2]
        hsgt = hsgt_res if not isinstance(hsgt_res, Exception) else MarketDataService._get_empty_hsgt_data_static()

        # 处理热门概念 (最后1个)
        hot_res = results[-1]
        hot_concepts = hot_res if not isinstance(hot_res, Exception) else []

        return {
            "date": date,
            "indices": indices,
            "hsgt": hsgt,
            "hot_concepts": hot_concepts,
        }

    async def _get_index(self, code: str, name_key: str, date: str) -> dict:
        """获取指数数据 - 优先从缓存获取，缓存无数据时调用 API"""
        df = await self.cache.get_index_daily(ts_code=code, trade_date=date)

        if df is None or df.empty:
            df = await self.api.get_index_daily(ts_code=code, trade_date=date)

        if df is not None and not df.empty:
            row = df.iloc[0]
            c = self._safe_float(row.get("pct_chg"))
            v = self._safe_float(row.get("close"))

            color = "red" if c >= 0 else "green"
            return {
                "name": I18n.get(name_key),
                "value": f"{v:.2f}",
                "change": f"{c:+.2f}%",
                "color": color,
            }
        return MarketDataService._get_empty_index_data_static(name_key)

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
