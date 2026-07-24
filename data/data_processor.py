import asyncio
import datetime
import logging
import os
import threading
import weakref

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.domain_services.trade_calendar_service import TradeCalendarService
from data.external.news_fetcher import NewsFetcher
from data.external.tushare_client import TushareClient
from data.mixins.calendar_mixin import CalendarMixin
from data.mixins.health_mixin import HealthCheckMixin
from data.sync.base import SyncContext, safe_error
from data.sync.financial import FinancialSyncStrategy
from data.sync.historical import HistoricalSyncStrategy
from data.sync.holder import HolderSyncStrategy
from data.sync.macro import MacroSyncStrategy
from data.sync.sw_industry import SwIndustrySyncStrategy
from core.i18n import I18n
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, classify_severity
from utils.loop_local import del_loop_local, get_loop_local
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date, to_yyyymmdd_str

logger = logging.getLogger(__name__)


from utils.singleton_registry import register_singleton


@register_singleton
class DataProcessor(HealthCheckMixin, CalendarMixin):
    """
    Main data processing class (Refactored Facade).
    Delegates complex sync logic to specific Strategies.
    Safeguarded with strict Singleton pattern.
    """

    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

        del_loop_local("processor_cancel_evt")

    @classmethod
    def _atexit_cleanup(cls):
        """C-P2-3: Centralized atexit cleanup via singleton_registry.
        Sets cancel event as a last-resort fallback when normal async
        shutdown is not taken. Uses strict=False because atexit runs
        outside any event loop.
        """
        inst = cls._instance
        if inst is not None:
            try:
                from utils.loop_local import get_loop_local

                evt = get_loop_local("processor_cancel_evt", lambda: None, strict=False)
                if evt is not None and hasattr(evt, "set"):
                    evt.set()
            except Exception as e:
                logger.warning("DataProcessor atexit cleanup failed: %s", safe_error(e), exc_info=True)

    def __init__(self):
        # Double-check initialization state with lock to prevent race conditions
        if self.__class__._initialized:
            # Check if token has changed since initialization
            current_token = ConfigHandler.get_token()
            if hasattr(self, "_current_token") and current_token != self._current_token:
                self.refresh_token(current_token)
            return

        with self.__class__._lock:
            if self.__class__._initialized:
                return

            self._health_cache = {"time": 0, "data": None}

            token = ConfigHandler.get_token()
            self._current_token = token
            self.api = TushareClient(token=token)
            self.cache = CacheManager()

            self.trade_calendar = TradeCalendarService(self.cache, self.api)

            self._first_news_sync = True
            self._cancel_event = None  # ST-01: Lazy initialization to avoid loop binding issues
            self._quality_tier = None  # None=Uninitialized, 0=Critical, 1=Bronze, 2=Silver, 3=Gold

            # Initialize Context & Strategies
            self.context = SyncContext(
                api=self.api,
                cache=self.cache,
                config=ConfigHandler,
            )
            self.context._processor_ref = weakref.ref(self)
            self.strategies = {
                "financial": FinancialSyncStrategy(self.context),
                "historical": HistoricalSyncStrategy(self.context),
                "macro": MacroSyncStrategy(self.context),
                "holder": HolderSyncStrategy(self.context),
                "sw_industry": SwIndustrySyncStrategy(self.context),
            }

            # Memory Cache for high-frequency small data
            self._trade_cal_cache = {}  # Cache structure: {'start': str, 'end': str, 'df': DataFrame}
            self._trade_date_cache = {
                "ts": 0,
                "val": None,
            }  # TTL cache for get_latest_trade_date

            # Concurrency Control (Cross-Loop safe) - kept for basic locking if needed
            self._sync_lock = threading.Lock()
            self._is_syncing_basic = False

            self.__class__._initialized = True

    def _get_cancel_event(self):
        """Get or create cancel event dynamically per event loop."""

        def _factory():
            return asyncio.Event()

        return get_loop_local("processor_cancel_evt", _factory)

    async def request_cancel(self):
        """
        Request cancellation of current operation.
        Called by UI when user clicks cancel or closes window.
        """
        logger.debug("[DataProcessor] Stop | Cancel requested")
        self._get_cancel_event().set()
        # A4: 传播到 TaskManager 注入的 task-level cancel_event，确保
        # TaskManager 视角能看到取消信号。context.cancel_event 来自 DI
        # （threading.Event，见 run_ai_concept_tagging docstring），非类属性，R11 合规。
        if self.context.cancel_event is not None:
            self.context.cancel_event.set()
        self._quality_tier = None  # Reset to uninitialized; will re-evaluate on next strategy run

        # Propagate to all strategies
        for name, strategy in self.strategies.items():
            try:
                strategy.cancel()
                logger.debug("[DataProcessor] Stop | Cancelled strategy: %s", name)
            except Exception as e:
                logger.error(
                    "[DataProcessor] Stop | ❌ Failed to cancel %s: %s",
                    name,
                    safe_error(e),
                    exc_info=True,
                )

    def is_cancelled(self):
        """Check if cancellation has been requested."""
        return self._get_cancel_event().is_set()

    def clear_cancel(self):
        """Clear cancel state before starting new operation."""
        self._get_cancel_event().clear()
        # A4: 同步 clear context.cancel_event，避免取消信号残留导致后续 sync 误判
        if self.context.cancel_event is not None:
            self.context.cancel_event.clear()

    async def stop(self):
        """Signal all running tasks to stop. Async to ensure proper cleanup."""
        logger.debug("[DataProcessor] Stop | Global stop signal received.")
        self._get_cancel_event().set()
        # A4: 同 request_cancel，传播到 TaskManager 注入的 task-level cancel_event。
        if self.context.cancel_event is not None:
            self.context.cancel_event.set()

        # Delegate cancellation to strategies
        try:
            for name, strategy in self.strategies.items():
                try:
                    strategy.cancel()
                    logger.debug("[DataProcessor] Stop | Cancelled strategy: %s", name)
                except Exception as e:
                    logger.error(
                        "[DataProcessor] Stop | ❌ Failed to cancel %s: %s",
                        name,
                        safe_error(e),
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(
                "[DataProcessor] Stop | ❌ Error during stop propagation: %s",
                safe_error(e),
                exc_info=True,
            )

    def refresh_token(self, new_token=None):
        """Refresh API token without recreating instance"""
        if new_token is None:
            new_token = ConfigHandler.get_token()
        self._current_token = new_token
        self.api = TushareClient(token=new_token)
        # Update context
        self.context.api = self.api
        logger.debug("[DataProcessor] Auth | Tushare token gracefully refreshed")

    async def close(self):
        """Gracefully close resources"""
        try:
            await self.stop()
            # ASYNC-009: Removed unconditional `await asyncio.sleep(1.0)`.
            # Pending task cancellation and DB lock release are handled by
            # shutdown step0 (``cancel_all_running_async(join_timeout=3.0)``)
            # in ``utils/shutdown.py``, which joins cancelled tasks with a
            # proper timeout. A fixed sleep here wastes shutdown budget.
            if self.cache:
                await self.cache.close()
        except Exception as e:
            logger.error(
                "[DataProcessor] State | ❌ Error during engine close: %s",
                safe_error(e),
                exc_info=True,
            )

    # ==========================================
    # Delegated Sync Methods
    # ==========================================

    @log_async_operation(
        operation_name="sync_historical",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def sync_historical_data(self, days=250, progress_callback=None):
        """Delegated to HistoricalSyncStrategy. `days` is trading days (250 ≈ 1 year)."""
        result = await self.strategies["historical"].run(
            days=days,
            progress_callback=progress_callback,
        )
        self._quality_tier = None
        self._health_cache = {"time": 0, "data": None}
        return result

    @log_async_operation(
        operation_name="sync_financial",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def sync_financial_reports(
        self,
        periods=None,
        progress_callback=None,
        force=False,
    ):
        """Delegated to FinancialSyncStrategy"""
        result = await self.strategies["financial"].run(
            periods=periods,
            force=force,
            progress_callback=progress_callback,
        )
        self._quality_tier = None
        self._health_cache = {"time": 0, "data": None}
        return result.added

    async def sync_comprehensive_fundamentals(
        self,
        progress_callback=None,
        force=False,
    ):
        """Delegated to FinancialSyncStrategy (Full Sync Mode)

        Returns:
            SyncResult: Full result object with status, added count, and errors
        """
        result = await self.strategies["financial"].run(
            force=force,
            progress_callback=progress_callback,
        )
        self._quality_tier = None
        self._health_cache = {"time": 0, "data": None}
        return result

    async def repair_financial_data(self, ts_codes, progress_callback=None):
        """Delegated to FinancialSyncStrategy"""
        return await self.strategies["financial"].repair_financial_data(
            ts_codes,
            progress_callback,
        )

    async def sync_daily_market_snapshot(self, trade_date=None, force=False):
        """Delegated to HistoricalSyncStrategy"""
        if trade_date is None:
            trade_date = await self.trade_calendar.get_latest_trade_date()
        if trade_date is None:
            logger.error(
                "[DataProcessor] sync_daily_market_snapshot | All calendar sources unavailable. "
                "Returning cached screening data.",
            )
            return await self.get_screening_data(get_now().date())

        await self.strategies["historical"].sync_daily_market_snapshot(
            trade_date,
            force=force,
        )

        self._quality_tier = None
        self._health_cache = {"time": 0, "data": None}

        # Clear caches to ensure fresh data visibility
        if hasattr(self, "_trade_date_cache"):
            self._trade_date_cache = {"ts": 0, "val": None}
        # For compatibility with some callers expecting data, we fetch it back from cache
        return await self.get_screening_data(trade_date)

    async def run_daily_update(self, progress_callback=None):
        from data.persistence.review_manager import ReviewManager

        await self.init_data()

        if progress_callback:
            progress_callback(0.2, 1.0, I18n.get("init_sync_market_snapshot"))
        result = await self.sync_daily_market_snapshot()

        if progress_callback:
            progress_callback(0.5, 1.0, I18n.get("init_sync_financial"))
        await self.sync_financial_reports()

        if progress_callback:
            progress_callback(0.8, 1.0, I18n.get("init_sync_ai_review"))
        review_mgr = ReviewManager()
        await review_mgr.run_review()

        if progress_callback:
            progress_callback(1.0, 1.0, I18n.get("init_daily_update_done"))
        return result

    @log_async_operation(
        operation_name="run_ai_concept_tagging",
        threshold_ms=PerfThreshold.AI_INFERENCE,
    )
    async def run_ai_concept_tagging(
        self,
        task_id: str | None = None,
        cancel_event: threading.Event | None = None,
        *,
        manual_trigger: bool = False,
        **kwargs,
    ) -> str:
        """Orchestrate concept sync from multiple sources.

        Executes three strategies in sequence:
        1. AKShareConceptSyncStrategy — East-Money concept boards (always)
        2. LimitListSyncStrategy — Tushare limit_list (always)
        3. AIConceptTagSyncStrategy — LLM-driven tagging (only when manual_trigger=True)

        Args:
            task_id: Optional task identifier for logging.
            cancel_event: Optional threading.Event for cancellation signaling.
            manual_trigger: If True, execute LLM-based concept tagging.
            **kwargs: Additional arguments. Supports `ai_service` for LLM injection
                (R1: data/ must not import services/; AIService is passed via kwargs
                and stored on SyncContext.ai_service).

        Returns:
            Summary string, e.g. "akshare=success | limit_list=success | ai_tag=skipped".
        """
        from data.sync.concept_sync import (
            AIConceptTagSyncStrategy,
            AKShareConceptSyncStrategy,
            LimitListSyncStrategy,
        )

        # Inject ai_service into context for LLM-driven strategies (R1: DI, no direct import)
        ai_service = kwargs.get("ai_service")
        if ai_service is not None:
            self.context.ai_service = ai_service

        # Propagate cancel_event to context so that long-running strategies
        # (e.g. AIConceptTagSyncStrategy) can poll cancel state inside LLM
        # calls (~2s granularity), satisfying project memory's hard constraint.
        self.context.cancel_event = cancel_event

        def _cancelled() -> bool:
            if self.is_cancelled():
                return True
            return cancel_event is not None and hasattr(cancel_event, "is_set") and cancel_event.is_set()

        parts: list[str] = []

        # Step 1: AKShare concept boards
        if _cancelled():
            parts.append("akshare=cancelled")
        else:
            try:
                r = await AKShareConceptSyncStrategy(self.context).run()
                parts.append(f"akshare={r.status}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[DataProcessor] AIConceptTag | AKShare failed: %s", safe_error(e), exc_info=True)
                parts.append("akshare=failed")

        # Step 2: LimitList
        if _cancelled():
            parts.append("limit_list=cancelled")
        else:
            try:
                r = await LimitListSyncStrategy(self.context).run()
                parts.append(f"limit_list={r.status}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[DataProcessor] AIConceptTag | LimitList failed: %s", safe_error(e), exc_info=True)
                parts.append("limit_list=failed")

        # Step 3: AIConceptTag (only on manual trigger)
        if not manual_trigger:
            parts.append("ai_tag=skipped")
        elif _cancelled():
            parts.append("ai_tag=cancelled")
        else:
            try:
                r = await AIConceptTagSyncStrategy(self.context).run()
                parts.append(f"ai_tag={r.status}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[DataProcessor] AIConceptTag | AI tag failed: %s", safe_error(e), exc_info=True)
                parts.append("ai_tag=failed")

        return " | ".join(parts)

    # ==========================================
    # Core Logic (Business/Orchestration)
    # Note: get_latest_trade_date, get_trade_dates, ensure_trade_cal,
    #       _ensure_trade_cal_impl → CalendarMixin
    # Note: _assign_basic_tier, check_data_health, run_quality_scan
    #       → HealthCheckMixin
    # ==========================================

    @log_async_operation(
        operation_name="init_data",
        threshold_ms=PerfThreshold.GLOBAL_INIT,
    )
    async def init_data(self):
        """Initialize DB with enhanced schema"""
        await self.cache.init_db()
        await self.sync_stock_basic()

    async def should_sync_financials(self, force=False):
        """Check if financial data sync is needed."""
        if force:
            return True, "force=True"

        try:
            status = await self.cache.get_sync_status("financial_reports")
            if status is None or not isinstance(status, dict) or not status.get("last_sync_date"):
                return True, I18n.get("status_never_synced")

            last_sync = status.get("last_sync_date")
            if isinstance(last_sync, str):
                last_sync = parse_date(last_sync)
            days_since = (get_now() - last_sync.replace(tzinfo=None)).days  # type: ignore[union-attr]

            if days_since >= 30:
                return True, I18n.get("status_days_ago", days=days_since)

            current_month = get_now().month
            if current_month in [1, 4, 7, 10]:  # Earnings season
                if days_since >= 7:
                    return True, I18n.get("status_earnings_season")

            return False, I18n.get("status_recent")
        except Exception as e:
            return True, f"error: {e}"

    # ... Other simple sync/get methods ...

    @log_async_operation(
        operation_name="sync_stock_basic",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def sync_stock_basic(self):
        """Sync stock basic info (Step 1 of initialization).

        Syncs both active stocks (list_status='L') and delisted stocks (list_status='D')
        to ensure accurate historical stock counts for data integrity validation.
        """
        if self.is_cancelled():
            return 0

        # Deduplication Lock - prevent concurrent runs
        should_run = False
        with self._sync_lock:
            if not self._is_syncing_basic:
                self._is_syncing_basic = True
                should_run = True

        if not should_run:
            logger.debug("[DataProcessor] Sync Basic | Already running, skipping")
            return 0

        try:
            logger.debug("[DataProcessor] Sync Basic | Starting...")

            # Single API call for all stocks (active + delisted)
            df_all = await self.api.get_stock_basic_all()

            if df_all is None or df_all.empty:
                logger.warning(
                    "[DataProcessor] Sync Basic | ⚠️ Remote API returned empty dataset",
                )
                return 0

            # Phase 3F-2 轨道 A 写时替换：若 sw_industry_member 有该 ts_code 的申万二级
            # 映射则覆写 industry 列；无映射则保留 API 原始值（v1.9.0 M-4，避免
            # sw_industry_member 同步失败导致全部 NULL）。不新增 industry_raw 列（v1.7.0 S4）。
            if "industry" in df_all.columns and "ts_code" in df_all.columns:
                try:
                    sw_l2_map = await self.cache.get_sw_l2_mapping()
                except Exception as e:
                    logger.warning(
                        "[DataProcessor] Sync Basic | ⚠️ sw_l2_mapping query failed, keep API raw industry: %s",
                        safe_error(e),
                    )
                    sw_l2_map = {}
                if sw_l2_map:
                    mapped_mask = df_all["ts_code"].isin(sw_l2_map)
                    df_all.loc[mapped_mask, "industry"] = df_all.loc[mapped_mask, "ts_code"].map(sw_l2_map)
                    logger.debug(
                        "[DataProcessor] Sync Basic | SW industry overwrite: %s / %s stocks",
                        int(mapped_mask.sum()),
                        len(df_all),
                    )

            count = await self.cache.save_stock_basic(df_all)
            if count > 0:
                active_count = len(df_all[df_all["list_status"] == "L"])
                delisted_count = len(df_all[df_all["list_status"] == "D"])
                total_count = count
                await self.cache.update_sync_status(
                    "stock_basic",
                    get_now().date(),  # type: ignore[arg-type]
                    total_count,
                )
                self._quality_tier = None
                self._health_cache = {"time": 0, "data": None}
                logger.info(
                    "[DataProcessor] Sync Basic | ✅ %s active + %s delisted = %s total stocks",
                    active_count,
                    delisted_count,
                    total_count,
                )
                return total_count

            logger.warning(
                "[DataProcessor] Sync Basic | ⚠️ No stocks saved to database",
            )
            return 0

        except Exception as e:
            # D2: 接入 classify_error + classify_severity 区分 system 与 recoverable/operational。
            # system 级（DB 连接失败、MemoryError 等）raise 传播至 initialize_system
            # except 块；recoverable/operational 级（网络错误、限流、数据格式错误等）
            # 降级返回 0。注：调用方 initialize_system 在 stock_count==0 时会 abort
            # 整个 phase（baseline 行为，D2 未改变），故 recoverable/operational 错误
            # 实际会触发 abort。
            safe = safe_error(e)
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.error(
                    "[DataProcessor] Sync Basic | ❌ System error (%s): %s",
                    error_info["code"],
                    safe,
                    exc_info=True,
                )
                raise
            logger.warning(
                "[DataProcessor] Sync Basic | ⚠️ Recoverable/operational error (%s): %s",
                error_info["code"],
                safe,
            )
            return 0
        finally:
            with self._sync_lock:
                self._is_syncing_basic = False

    @log_async_operation(
        operation_name="sync_concepts",
        threshold_ms=PerfThreshold.GLOBAL_INIT,
    )
    async def sync_concepts(self):
        """Sync stock concepts from Tushare."""
        if self.is_cancelled():
            return 0

        try:
            logger.debug("[DataProcessor] Sync Concepts | Starting...")
            # Strategy:
            # 1. Get all concepts: `df_concepts = pro.concept()`
            # 2. Parallel fetch `pro.concept_detail(id=c)` for all `c` using ThreadPool and Semaphore.
            # 3. Merge results and save atomically.

            # Fetch Concept List
            # Fetch Concept List (uses _handle_api_call for rate limiting)
            df_c = await self.api.get_concept_list()
            if df_c is None or df_c.empty:
                return 0

            c_codes = df_c["code"].tolist()

            # concept_detail 有独立的严格速率限制（~20 req/min）
            # 使用极低并发 + 主动延迟避免触发 backoff
            CONCEPT_CONCURRENCY = 2
            CONCEPT_DELAY = 3.0  # 每请求间隔秒数
            sem = asyncio.Semaphore(CONCEPT_CONCURRENCY)

            async def fetch_one(c):
                # Check cancellation before acquiring semaphore to fail fast
                if self.is_cancelled():
                    return None
                async with sem:
                    # Double check inside semaphore
                    if self.is_cancelled():
                        return None
                    result = await self.api.get_concept_detail_by_id(c)
                    # A3: 用 wait_for(cancel_event.wait(), timeout) 替代 asyncio.sleep，
                    # 使 cancel_event 被 set 时立即响应（≤2s 红线），而非阻塞完整 CONCEPT_DELAY。
                    # TimeoutError = sleep 正常完成；event 被 set 则 wait() 立即返回，
                    # 随后 is_cancelled() 为 True 时 raise CancelledError，由
                    # gather_return_exceptions_propagating_cancel 重新抛出（R2 合规）。
                    try:
                        await asyncio.wait_for(
                            self._get_cancel_event().wait(),
                            timeout=CONCEPT_DELAY,
                        )
                    except TimeoutError:
                        pass  # 正常 sleep 完成
                    if self.is_cancelled():
                        raise asyncio.CancelledError()
                    return result

            # Create tasks eagerly but execute with semaphore
            # This avoids "coroutine never awaited" because we wrap in create_task
            tasks = [asyncio.create_task(fetch_one(c)) for c in c_codes]

            all_dfs = []
            try:
                # Wait for all tasks to complete or be cancelled
                results = await gather_return_exceptions_propagating_cancel(*tasks)

                # Check if we were cancelled during gather
                if self.is_cancelled():
                    return 0

                for r in results:
                    if isinstance(r, pd.DataFrame) and not r.empty:
                        all_dfs.append(r)
                    elif isinstance(r, Exception):
                        # Log but don't stop everything for one failed concept
                        logger.warning(
                            "[DataProcessor] Sync Concepts | ⚠️ Subtask failed: %s",
                            r,
                        )

            except asyncio.CancelledError:
                logger.debug("[DataProcessor] Sync Concepts | Cancelled during gather")
                raise
            finally:
                # Ensure all pending tasks are cancelled if we exit early
                for t in tasks:
                    if not t.done():
                        t.cancel()

            if not all_dfs:
                return 0

            full_df = pd.concat(all_dfs)

            # Rename cols to match Schema
            # API returns: id, concept_name, ts_code, name
            # Schema: ts_code, concept_name, concept_id

            full_df = full_df.rename(columns={"id": "concept_id"})
            # Ensure unique
            full_df = full_df[["ts_code", "concept_name", "concept_id"]].drop_duplicates()

            # Atomic overwrite (refresh)
            count = await self.cache.overwrite_concepts(full_df)
            self._quality_tier = None
            self._health_cache = {"time": 0, "data": None}
            logger.info(
                "[DataProcessor] Sync Concepts | ✅ Saved %s structured mappings",
                count,
            )
            return count

        except Exception as e:
            # D2: 接入 classify_error + classify_severity 区分 system 与 recoverable/operational。
            # system 级（DB 连接失败、MemoryError 等）raise 传播；recoverable/operational
            # 级（网络错误、限流、数据格式错误等）降级返回 0。调用方 initialize_system
            # 未检查 sync_concepts 返回值，故 recoverable/operational 错误不打断初始化流程。
            safe = safe_error(e)
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.error(
                    "[DataProcessor] Sync Concepts | ❌ System error (%s): %s",
                    error_info["code"],
                    safe,
                    exc_info=True,
                )
                raise
            logger.warning(
                "[DataProcessor] Sync Concepts | ⚠️ Recoverable/operational error (%s): %s",
                error_info["code"],
                safe,
            )
            return 0

    async def prepare_market_data(self):
        now = get_now()
        today_date = now.date()
        latest = await self.trade_calendar.get_latest_trade_date()
        if latest is None:
            logger.error(
                "[DataProcessor] prepare_market_data | All calendar sources unavailable. Syncing today as last resort.",
            )
            await self.sync_daily_market_snapshot(today_date)
            return today_date
        if latest != today_date:
            return latest

        cached_date = await self.cache.get_latest_trade_date()
        if cached_date is not None and cached_date != today_date:
            await self.sync_daily_market_snapshot(today_date)

        return today_date

    async def get_market_overview(self):
        """
        Get market overview data for Home Screen.
        Returns Indices (SH, SZ, CYB) and Northbound Money Flow.
        优先从缓存获取，缓存无数据时调用 API。
        Uses batch query for indices to reduce DB/API round trips.
        """
        try:
            latest_date = await self.trade_calendar.get_latest_trade_date()
            if latest_date is None:
                logger.error(
                    "[DataProcessor] get_market_overview | All calendar sources unavailable. Skipping.",
                )
                return {}
            date = latest_date

            INDICES_CONFIG = [
                ("000001.SH", "home_index_sh"),
                ("399001.SZ", "home_index_sz"),
                ("399006.SZ", "home_index_cyb"),
            ]

            async def get_indices_batch():
                codes = [code for code, _ in INDICES_CONFIG]
                code_to_key = {code: key for code, key in INDICES_CONFIG}
                date_str = date.strftime("%Y%m%d") if isinstance(date, datetime.date) else str(date)
                df = await self.cache.get_index_daily_range(codes, start_date=date_str, end_date=date_str)

                if df is None or df.empty:
                    df = await self.api.get_index_daily(trade_date=date_str)

                result_map: dict[str, dict] = {}
                if df is not None and not df.empty:
                    for ts_code in codes:
                        row_df = df[df["ts_code"] == ts_code]
                        if row_df.empty:
                            continue
                        row = row_df.iloc[0]
                        c = row.get("pct_chg", 0)
                        v = row.get("close", 0)
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
                        indices.append({"name": I18n.get(name_key), "value": "-", "change": "-", "color": "grey"})
                return indices

            async def get_hsgt():
                df = await self.cache.get_moneyflow_hsgt(trade_date=date)

                if df is None or df.empty:
                    df = await self.api.get_moneyflow_hsgt(trade_date=date)  # type: ignore[arg-type]

                name = I18n.get("home_northbound")
                if df is not None and not df.empty:
                    val = float(df.iloc[0]["north_money"])
                    val_str = (
                        f"{val / 100:.2f}{I18n.get('unit_yi')}"
                        if abs(val) > 100
                        else f"{val * 100:.0f}{I18n.get('unit_wan')}"
                    )
                    sub_str = I18n.get("home_inflow") if val > 0 else I18n.get("home_outflow")
                    return {"name": name, "value": val_str, "sub": sub_str}
                return {"name": name, "value": "-", "sub": "-"}

            hot_concepts_task = NewsFetcher.get_hot_concepts(limit=8)

            results = await asyncio.gather(
                get_indices_batch(),
                get_hsgt(),
                hot_concepts_task,
            )

            return {
                "date": date,
                "indices": results[0],
                "hsgt": results[1],
                "hot_concepts": results[2],
            }

        except Exception as e:
            logger.error(
                "[DataProcessor] Config | ❌ Unexpected error fetching market summary: %s",
                safe_error(e),
                exc_info=True,
            )
            return None

    async def get_screening_data(self, trade_date=None):
        return await self.cache.get_screening_data(trade_date)

    async def get_fundamental_screening_data(self, trade_date=None):
        return await self.cache.get_fundamental_screening_data(trade_date)

    async def initialize_system(self, progress_callback=None, quick=False):
        """
        Orchestrate system initialization with 6 distinct steps.

        Args:
            progress_callback: Optional callback for progress updates
            quick: If True, perform quick sync (skip historical data, only sync essential data)

        Steps and weights:
        - Step 1 (1%):  Sync stock list
        - Step 2 (1%):  Sync trade calendar
        - Step 3 (45%): Sync historical data (skipped if quick=True)
        - Step 4 (38%): Sync financial data (skipped if quick=True)
        - Step 5 (10%): Sync AI core data (macro/holders)
        - Step 6 (5%):  Health check

        Returns:
            dict: Health check result on success
            None: If cancelled or critical failure

        Note: Call request_cancel() to cancel this operation.
        """
        from data.data_dictionary import validate_schema_definitions
        from core.i18n import I18n

        # Run schema validation (DD-01)
        # STRICT_SCHEMA_GATE defaults to "1" (strict enabled) to fail fast on
        # ORM ↔ data dictionary drift; set STRICT_SCHEMA_GATE=0 to bypass.
        validate_schema_definitions(strict=os.environ.get("STRICT_SCHEMA_GATE", "1") == "1")

        # Clear any previous cancel state
        self.clear_cancel()

        # Step weights (must sum to 100)
        # Optimized based on user feedback (Steps 1 & 2 represent 2% total)
        # Added Step 5 (AI Data) -> 10%
        # For quick mode, redistribute weights (skip steps 3 & 4)
        STEP_WEIGHTS = [10, 10, 0, 0, 50, 30] if quick else [1, 1, 45, 38, 10, 5]
        current_step = 0

        def report_step(step_num, sub_progress=0, sub_total=1, sub_msg=""):
            """Report progress with weighted calculation."""
            nonlocal current_step
            current_step = step_num

            if not progress_callback:
                return

            # Calculate base progress (sum of completed step weights)
            base = sum(STEP_WEIGHTS[: step_num - 1])

            # Add sub-progress within current step
            step_weight = STEP_WEIGHTS[step_num - 1]
            current = base + (sub_progress / max(sub_total, 1)) * step_weight

            step_label = I18n.get(f"init_step_{step_num}")
            msg = f"{step_label}" + (f" - {sub_msg}" if sub_msg else "")
            progress_callback(current, 100, msg)

        try:
            # ===== Step 1: Stock List (5%) =====
            report_step(1)
            stock_count = await self.sync_stock_basic()
            if stock_count == 0:
                logger.error(
                    "[DataProcessor] Init | ❌ Critical failure: Stock Basic sync returned empty, aborting entire phase.",
                )
                return None
            if self.is_cancelled():
                return None

            # ===== Step 1.5: Concepts (runs as part of Step 1) =====
            report_step(1, 0.5, 1, I18n.get("init_sync_concepts"))  # type: ignore[attr-defined]
            await self.sync_concepts()

            # ===== Step 2: Trade Calendar (5%) =====
            report_step(2)
            from utils.config_handler import ConfigHandler

            years = ConfigHandler.get_init_history_years()
            end_date = get_now().date()
            rough_start = (get_now() - datetime.timedelta(days=365 * years + 30)).date()
            cal_success = await self.trade_calendar.ensure_calendar_range(
                rough_start,
                end_date,
            )
            if not cal_success:
                logger.error(
                    "[DataProcessor] Init | ❌ Critical failure: Trade calendar sync failed, aborting entire phase.",
                )
                return None
            if self.is_cancelled():
                return None

            # ===== Step 3: Historical Data (50%) =====
            if not quick:

                def step3_callback(current, total, msg):
                    report_step(3, current, total, msg)

                trade_days = 250 * years
                history_result = await self.sync_historical_data(
                    days=trade_days,
                    progress_callback=step3_callback,
                )
                if history_result and history_result.status == "failed":
                    logger.error(
                        "[DataProcessor] Init | ❌ Historical daily sync encountered errors: %s",
                        history_result.errors,
                    )
                    return None
                if self.is_cancelled():
                    return None

            # ===== Step 4: Financial Data (35%) =====
            if not quick:

                def step4_callback(current, total, msg):
                    report_step(4, current, total, msg)

                financial_result = await self.sync_comprehensive_fundamentals(
                    progress_callback=step4_callback,
                )
                if financial_result and financial_result.status == "failed":
                    logger.error(
                        "[DataProcessor] Init | ❌ Financial sync encountered errors: %s",
                        financial_result.errors,
                    )
                    return None
                if self.is_cancelled():
                    return None

            # ===== Step 5: AI Alpha Data (10%) =====
            report_step(5, 0, 3, I18n.get("init_sync_macro"))

            macro_res = await self.strategies["macro"].run()
            if macro_res.status == "failed":
                logger.warning(
                    "[DataProcessor] Init | ⚠️ Macro sync failed: %s",
                    macro_res.errors,
                )

            report_step(5, 1, 3, I18n.get("init_sync_holders"))

            holder_res = await self.strategies["holder"].run()
            if holder_res.status == "failed":
                logger.warning(
                    "[DataProcessor] Init | ⚠️ Holder sync failed: %s",
                    holder_res.errors,
                )

            report_step(5, 2, 3, I18n.get("init_step_5_done"))

            if self.is_cancelled():
                return None

            # ===== Step 6: Health Check (5%) =====
            report_step(6)
            result = await self.check_data_health()

            # Report completion
            if progress_callback:
                progress_callback(100, 100, I18n.get("init_step_complete"))

            return result

        except Exception as e:
            step_label = I18n.get(f"init_step_{current_step}") if current_step > 0 else "Initialization"
            logger.error(
                "[DataProcessor] Init | ❌ System init unexpectedly failed at %s: %s",
                step_label,
                safe_error(e),
                exc_info=True,
            )
            raise  # Re-raise so UI can catch and display

    # ... get_stock_history, get_strategy_data ...
    async def get_stock_history(self, ts_code, days=365, end_date=None):
        try:
            if end_date is None:
                latest_closed_trade_date = await self.trade_calendar.get_latest_trade_date()
                if isinstance(latest_closed_trade_date, datetime.datetime):
                    end = latest_closed_trade_date.date()
                elif isinstance(latest_closed_trade_date, datetime.date):
                    end = latest_closed_trade_date
                elif latest_closed_trade_date:
                    end = parse_date(str(latest_closed_trade_date))
                else:
                    end = get_now().date()
                    logger.warning(
                        "[DataProcessor] get_stock_history | All calendar sources unavailable, using %s.",
                        end,
                    )
            else:
                if hasattr(end_date, "year"):
                    end = end_date if isinstance(end_date, datetime.date) else end_date.date()
                else:
                    end = parse_date(str(end_date))
        except Exception as e:
            logger.warning("[DataProcessor] get_stock_history fallback to natural date: %s", safe_error(e))
            end = get_now().date()

        # 2.0 multiplier ensures we fetch enough natural days to cover `days` number of trade days
        rough_start = end - datetime.timedelta(days=int(days * 2.0))
        all_dates = await self.trade_calendar.get_trade_dates(start_date=rough_start, end_date=end)
        start = all_dates[-days] if len(all_dates) >= days else (all_dates[0] if all_dates else rough_start)
        return await self.cache.get_daily_quotes(
            ts_code=ts_code,
            start_date=start,
            end_date=end,
        )

    async def get_strategy_data(self, trade_date=None):
        return await self.prepare_screening_context(trade_date=trade_date)

    @staticmethod
    def _normalize_context_trade_date(value):
        """Normalize trade_date values used in screening context to YYYYMMDD strings."""
        if value is None or pd.isna(value):
            return None
        return to_yyyymmdd_str(value)

    @classmethod
    def _resolve_screening_trade_date(cls, explicit_trade_date, screening_data: pd.DataFrame):
        """Ensure context trade_date is present and consistent with screening data."""
        resolved_trade_date = cls._normalize_context_trade_date(explicit_trade_date)
        df_trade_date = None

        if screening_data is not None and not screening_data.empty and "trade_date" in screening_data.columns:
            unique_dates = {
                cls._normalize_context_trade_date(v) for v in screening_data["trade_date"].dropna().unique().tolist()
            }
            unique_dates.discard(None)
            if len(unique_dates) > 1:
                raise RuntimeError(
                    "Screening data contains multiple trade_date values; context cannot be built safely",
                )
            if unique_dates:
                df_trade_date = next(iter(unique_dates))

        if resolved_trade_date is None:
            resolved_trade_date = df_trade_date
        elif df_trade_date is not None and resolved_trade_date != df_trade_date:
            raise RuntimeError(
                f"Screening context trade_date mismatch: cache={resolved_trade_date} data={df_trade_date}",
            )

        if resolved_trade_date is None:
            raise RuntimeError("No analysis trade_date available for screening context")

        return resolved_trade_date

    @log_async_operation(
        operation_name="prepare_screening_context",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def prepare_screening_context(self, trade_date=None):
        """Prepare context for screening execution."""

        if self._quality_tier is None:
            await self._assign_basic_tier()

        quality_tier = self._quality_tier
        if quality_tier is not None and quality_tier <= 1:
            try:
                await self.check_data_health()
            except Exception as e:
                logger.warning(
                    "[DataProcessor] Deep health check during screening prep failed, keeping fast-path tier=%s: %s",
                    quality_tier,
                    safe_error(e),
                )

        context = {}
        diagnostics = {
            "quality_tier": self._quality_tier,
            "trade_date": None,
            "base_complete": False,
            "strategy_ready": False,
            "table_status": {},
        }

        context_trade_date = trade_date
        if context_trade_date is None:
            latest_closed_trade_date = await self.trade_calendar.get_latest_trade_date()
            context_trade_date = self._normalize_context_trade_date(latest_closed_trade_date)
            if context_trade_date is None:
                cache_date = await self.cache.get_latest_trade_date()
                if cache_date is not None:
                    context_trade_date = self._normalize_context_trade_date(cache_date)
        screening_data = await self.get_screening_data(context_trade_date)
        resolved_trade_date = self._resolve_screening_trade_date(context_trade_date, screening_data)

        if screening_data is not None and not screening_data.empty and "is_tradable" in screening_data.columns:
            suspended_count = int((~screening_data["is_tradable"]).sum())
            screening_data = screening_data[screening_data["is_tradable"]].copy()
            if suspended_count > 0:
                diagnostics["suspended_filtered"] = suspended_count
        elif screening_data is not None and not screening_data.empty and "is_tradable" not in screening_data.columns:
            logger.warning(
                "[DataProcessor] is_tradable column missing from screening_data; suspended stocks will NOT be filtered"
            )

        context["screening_data"] = screening_data
        context["trade_date"] = resolved_trade_date
        diagnostics["trade_date"] = resolved_trade_date

        base_complete = screening_data is not None and not screening_data.empty
        diagnostics["base_complete"] = base_complete

        fundamental_data = await self.get_fundamental_screening_data(resolved_trade_date)
        if fundamental_data is not None and not fundamental_data.empty:
            if "is_tradable" in fundamental_data.columns:
                fundamental_data = fundamental_data[fundamental_data["is_tradable"]].copy()
            context["fundamental_screening_data"] = fundamental_data
            diagnostics["table_status"]["fundamental_screening_data"] = {
                "ready": not fundamental_data.empty,
                "rows": len(fundamental_data),
            }
        else:
            diagnostics["table_status"]["fundamental_screening_data"] = {"ready": False, "rows": 0}

        diagnostics["table_status"]["screening_data"] = {
            "ready": base_complete,
            "rows": len(screening_data) if screening_data is not None else 0,
        }

        auxiliary_tables = {
            "northbound_data": self.cache.get_northbound,
            "northbound_flow_data": self.cache.get_moneyflow_hsgt,
            "moneyflow_data": self.cache.get_moneyflow,
            "top_list": self.cache.get_top_list,
            "block_trade": self.cache.get_block_trade,
        }

        all_aux_ready = True
        for key, fetch_func in auxiliary_tables.items():
            data = await fetch_func(trade_date=resolved_trade_date)
            if data is not None:
                context[key] = data
                is_empty = hasattr(data, "empty") and data.empty
                diagnostics["table_status"][key] = {"ready": True, "rows": len(data) if not is_empty else 0}
            else:
                diagnostics["table_status"][key] = {"ready": False, "rows": 0}
                all_aux_ready = False

        diagnostics["strategy_ready"] = base_complete and all_aux_ready
        context["_diagnostics"] = diagnostics

        return context
