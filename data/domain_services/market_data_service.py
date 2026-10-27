"""
MarketDataService - 市场数据后台服务

负责定时获取市场概览数据（指数、北向资金、热点概念），
UI 只从内存缓存读取，类似 NewsSubscriptionService 架构。
"""

import asyncio
import datetime
import functools
import inspect
import logging
import math
import threading
import typing

import pandas as pd

from core.i18n import I18n
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.config_handler import ConfigHandler
from utils.error_classifier import log_classified
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
    INDICES_CONFIG: typing.ClassVar[list] = [
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
        # CON-01: double-checked locking。__new__ 持锁创建实例，但 __init__ 在锁外执行会
        # 导致并发首次访问时两线程都见 _initialized=False 而重复初始化。初始化整体移入
        # 锁内并二次检查，保证恰好执行一次。
        if self._initialized:
            return
        with self._lock:
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
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
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
            log_classified(
                logger,
                e,
                "general",
                "[MarketDataService] Fetch failed (%s): %s",
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
        # UIR-F03-002: 逐分量 as_of 日期。初始为当次查询日期（date），
        # 仅当该分量回退取到有效数据时才更新为 prev_str；分量无有效数据
        # （回退仍空/失败/日历未知）置 None —— 不伪称存在任何交易日的数据（R21）。
        indices_date: str | None = date
        hsgt_date: str | None = date
        eval_date = latest_date or today_date
        indices_empty = isinstance(results[0], Exception) or all(
            d.get("value") == "-" for d in (results[0] if not isinstance(results[0], Exception) else [])
        )
        hsgt_empty = isinstance(results[1], Exception) or (
            not isinstance(results[1], Exception) and results[1].get("value") == "-"
        )
        # MKT-002: Data readiness fallback.
        # ``get_latest_trade_date()`` uses MARKET_CLOSE_HOUR (16) to decide
        # whether "today" is the latest trade date. Between 16:00 and the
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
            elif inspect.iscoroutine(prev_date_val):
                prev_date = await prev_date_val
            else:
                prev_date = None

            if prev_date:
                prev_str = prev_date.strftime("%Y%m%d")
                fb_indices_task = self._get_indices_batch(index_codes, prev_str) if indices_empty else asyncio.sleep(0)
                fb_hsgt_task = self._get_hsgt(prev_str) if hsgt_empty else asyncio.sleep(0)
                fb_results = await gather_return_exceptions_propagating_cancel(fb_indices_task, fb_hsgt_task)
                fb_indices_valid = False
                fb_hsgt_valid = False
                if indices_empty:
                    results[0] = fb_results[0]
                    # UIR-F03-002: 回退结果有效才归属 prev_str；回退仍空/失败 → None
                    fb_indices_valid = not isinstance(fb_results[0], Exception) and any(
                        d.get("value") != "-" for d in fb_results[0]
                    )
                    indices_date = prev_str if fb_indices_valid else None
                if hsgt_empty:
                    results[1] = fb_results[1]
                    fb_hsgt_valid = not isinstance(fb_results[1], Exception) and fb_results[1].get("value") != "-"
                    hsgt_date = prev_str if fb_hsgt_valid else None
                # UIR-F03-002: 回退成功才把整批标记为“前日快照”（方案 b）；
                # 回退仍空/失败不伪装为缓存数据（方案 d / R21）。
                if (indices_empty and fb_indices_valid) or (hsgt_empty and fb_hsgt_valid):
                    data_stale = True
                # 页头日期仅当两个分量都回退有效时改为前日（整批快照确实来自前日）。
                if indices_empty and hsgt_empty and fb_indices_valid and fb_hsgt_valid:
                    date = prev_str
            else:
                # UIR-F03-002: 日历未知/无前日 → 回退未发生，空分量无 as_of（不猜日期，R21）
                if indices_empty:
                    indices_date = None
                if hsgt_empty:
                    hsgt_date = None

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
            # UIR-F03-002: 逐分量 as_of 交易日（str | None）。None = 该分量无有效数据
            # 或日期未知；旧消费方按缺省键处理保持兼容。
            "indices_date": indices_date,
            "hsgt_date": hsgt_date,
        }

        await self._notify_listeners()

    async def _notify_listeners(self):
        """通知所有注册的 listener 数据已更新。

        async listener 在事件循环线程执行（Flet set_state 必须在主线程），
        sync listener 提交到 IO 线程池。async listener 加 10s 超时防卡死。
        """
        listener_count = len(self._listeners)
        if listener_count > 0:
            for listener in list(self._listeners):
                try:
                    # 识别 partial 包装的 async function（修复 inspect.iscoroutinefunction 误判）
                    unwrapped = listener
                    while isinstance(unwrapped, functools.partial):
                        unwrapped = unwrapped.func

                    # Python 3.16 将移除 asyncio.iscoroutinefunction，统一用 inspect 版本
                    # （Python 3.14 下前者触发 DeprecationWarning，被 warning 配置升格为异常，
                    # 曾导致所有 listener 通知静默失败）
                    if inspect.iscoroutinefunction(unwrapped):
                        # async listener: 在事件循环线程执行（Flet set_state 必须在主线程）
                        # 加超时防止单个 listener 卡死阻塞整个 fetch 周期
                        await asyncio.wait_for(listener(), timeout=10.0)
                    else:
                        # sync listener: 提交到 IO 线程池（避免阻塞事件循环）
                        await ThreadPoolManager().run_async(TaskType.IO, listener)
                except TimeoutError:
                    logger.warning(
                        "[MarketDataService] Listener timeout (10s), skipped: %s",
                        getattr(listener, "__name__", listener),
                    )
                except asyncio.CancelledError:
                    raise  # R2: 传播取消
                except Exception as e:
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[MarketDataService] Listener error (%s): %s",
                        exc_info=True,
                    )

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
                if v is None:
                    # R21: 主值缺失 → 整卡空态, 不产出伪 "0.00" (缺失 ≠ 0)
                    result_map[ts_code] = MarketDataService._get_empty_index_data_static(name_key)
                    continue
                color = "red" if c is not None and c > 0 else "green" if c is not None and c < 0 else "grey"
                result_map[ts_code] = {
                    "name": I18n.get(name_key),
                    "value": f"{v:.2f}",
                    # R21: 涨跌缺失独立显示 "-", 不冒充 "+0.00%", 也不牵连合法价格
                    "change": f"{c:+.2f}%" if c is not None else "-",
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
        df = await self.cache.market_dao.get_moneyflow_hsgt(trade_date=date)

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
            if val is None:
                # R21: north_money 缺失 → 空卡占位, 不伪装为 0 (更不会误附「流出」方向)
                return MarketDataService._get_empty_hsgt_data_static()
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
    def _safe_float(val: typing.Any) -> float | None:
        """Safely convert value to a finite float, or None when missing/invalid (R21).

        缺失（None/NaN/Inf/不可解析/缺列）返回 None，不折叠为 0.0 —— 缺失 ≠ 0；
        真实 0 与合法负数原样传递，由调用方区分「无数据」与「数值为零」。
        """
        if val is None:
            return None
        try:
            f = float(val)
        except (ValueError, TypeError):
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return f
