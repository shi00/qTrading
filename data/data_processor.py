import asyncio
import datetime
import logging
import threading
import time

import pandas as pd

from data.cache_manager import CacheManager
from data.mixins.health_mixin import HealthCheckMixin
from data.mixins.calendar_mixin import CalendarMixin
from data.news_fetcher import NewsFetcher
from data.sync_strategies.base import SyncContext
from data.sync_strategies.financial import FinancialSyncStrategy
from data.sync_strategies.historical import HistoricalSyncStrategy
from data.sync_strategies.macro import MacroSyncStrategy
from data.sync_strategies.holder import HolderSyncStrategy
from data.tushare_client import TushareClient
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

class DataProcessor(HealthCheckMixin, CalendarMixin):
    """
    Main data processing class (Refactored Facade).
    Delegates complex sync logic to specific Strategies.
    Safeguarded with strict Singleton pattern.
    """
    _instance = None
    _lock = threading.Lock()
    _is_initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Double-check initialization state with lock to prevent race conditions
        if self.__class__._is_initialized:
            # Check if token has changed since initialization
            current_token = ConfigHandler.get_token()
            if hasattr(self, '_current_token') and current_token != self._current_token:
                self.refresh_token(current_token)
            return

        with self.__class__._lock:
            if self.__class__._is_initialized:
                return

            self._health_cache = {'time': 0, 'data': None}

            token = ConfigHandler.get_token()
            self._current_token = token
            self.api = TushareClient(token=token)
            self.cache = CacheManager()
            self._first_news_sync = True
            self._cancel_event = None  # ST-01: Lazy initialization to avoid loop binding issues
            self._quality_tier = None  # None=Uninitialized, 0=Critical, 1=Bronze, 2=Silver, 3=Gold

            # Initialize Context & Strategies
            self.context = SyncContext(api=self.api, cache=self.cache, config=ConfigHandler)
            self.strategies = {
                'financial': FinancialSyncStrategy(self.context),
                'historical': HistoricalSyncStrategy(self.context),
                'macro': MacroSyncStrategy(self.context),
                'holder': HolderSyncStrategy(self.context)
            }

            # Memory Cache for high-frequency small data
            self._trade_cal_cache = {}  # Cache structure: {'start': str, 'end': str, 'df': DataFrame}
            self._trade_date_cache = {'ts': 0, 'val': None}  # TTL cache for get_latest_trade_date

            # Concurrency Control (Cross-Loop safe) - kept for basic locking if needed
            self._sync_lock = threading.Lock()
            self._is_syncing_basic = False

            self.__class__._is_initialized = True

    def _get_cancel_event(self):
        """Get or create cancel event dynamically per event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Event()

        if not hasattr(current_loop, '_processor_cancel_evt'):
            setattr(current_loop, '_processor_cancel_evt', asyncio.Event())

        return getattr(current_loop, '_processor_cancel_evt')

    async def request_cancel(self):
        """
        Request cancellation of current operation.
        Called by UI when user clicks cancel or closes window.
        """
        logger.info("[DataProcessor] Cancel requested")
        self._get_cancel_event().set()
        self._quality_tier = None  # Reset to uninitialized; will re-evaluate on next strategy run

        # Propagate to all strategies
        for name, strategy in self.strategies.items():
            try:
                await strategy.cancel()
                logger.debug(f"[DataProcessor] Cancelled strategy: {name}")
            except Exception as e:
                logger.warning(f"[DataProcessor] Failed to cancel {name}: {e}")

    def is_cancelled(self):
        """Check if cancellation has been requested."""
        return self._get_cancel_event().is_set()

    def clear_cancel(self):
        """Clear cancel state before starting new operation."""
        self._get_cancel_event().clear()

    async def stop(self):
        """Signal all running tasks to stop. Async to ensure proper cleanup."""
        logger.info("[DataProcessor] Global stop signal received.")
        self._get_cancel_event().set()

        # Delegate cancellation to strategies
        try:
            tasks = []
            for name, strategy in self.strategies.items():
                tasks.append(strategy.cancel())

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("[DataProcessor] All strategies cancelled.")

        except Exception as e:
            logger.warning(f"[DataProcessor] Error during stop propagation: {e}")

    def refresh_token(self, new_token=None):
        """Refresh API token without recreating instance"""
        if new_token is None:
            new_token = ConfigHandler.get_token()
        self._current_token = new_token
        self.api = TushareClient(token=new_token)
        # Update context
        self.context.api = self.api
        logger.info("[DataProcessor] Token refreshed")

    async def close(self):
        """Gracefully close resources"""
        try:
            await self.stop()
            # Give pending tasks a moment to catch cancellation and release DB locks
            await asyncio.sleep(1.0)

            if self.cache:
                await self.cache.close()
        except Exception as e:
            logger.error(f"[DataProcessor] Error during close: {e}")

    # ==========================================
    # Delegated Sync Methods
    # ==========================================

    @log_async_operation(operation_name="sync_historical")
    async def sync_historical_data(self, days=365, progress_callback=None):
        """Delegated to HistoricalSyncStrategy"""
        result = await self.strategies['historical'].run(days=days, progress_callback=progress_callback)
        return result

    @log_async_operation(operation_name="sync_financial")
    async def sync_financial_reports(self, periods=None, progress_callback=None, force=False):
        """Delegated to FinancialSyncStrategy"""
        result = await self.strategies['financial'].run(periods=periods, force=force,
                                                        progress_callback=progress_callback)
        return result.added

    async def sync_comprehensive_fundamentals(self, progress_callback=None, force=False):
        """Delegated to FinancialSyncStrategy (Full Sync Mode)
        
        Returns:
            SyncResult: Full result object with status, added count, and errors
        """
        result = await self.strategies['financial'].run(force=force, progress_callback=progress_callback)
        return result

    async def repair_financial_data(self, ts_codes, progress_callback=None):
        """Delegated to FinancialSyncStrategy"""
        return await self.strategies['financial'].repair_financial_data(ts_codes, progress_callback)

    async def sync_daily_market_snapshot(self, trade_date=None, force=False):
        """Delegated to HistoricalSyncStrategy"""
        if trade_date is None:
            trade_date = await self.get_latest_trade_date()

        await self.strategies['historical'].sync_daily_market_snapshot(trade_date, force=force)

        # Clear caches to ensure fresh data visibility
        if hasattr(self, '_trade_date_cache'):
            self._trade_date_cache = {'ts': 0, 'val': None}
        # For compatibility with some callers expecting data, we fetch it back from cache
        return await self.get_screening_data(trade_date)

    # ==========================================
    # Core Logic (Business/Orchestration)
    # Note: get_latest_trade_date, get_trade_dates, ensure_trade_cal,
    #       _ensure_trade_cal_impl → CalendarMixin
    # Note: _assign_basic_tier, check_data_health, run_quality_scan
    #       → HealthCheckMixin
    # ==========================================

    @log_async_operation(operation_name="init_data")
    async def init_data(self):
        """Initialize DB with enhanced schema"""
        await self.cache.init_db()
        await self.sync_stock_basic()

    async def should_sync_financials(self, force=False):
        """Check if financial data sync is needed."""
        if force:
            return True, "force=True"

        try:
            status = await self.cache.get_sync_status('financial_reports')
            if status is None or not status.get('last_sync_date'):
                return True, I18n.get("status_never_synced")

            last_sync = datetime.datetime.strptime(status.get('last_sync_date'), '%Y-%m-%d %H:%M:%S')
            days_since = (get_now() - last_sync).days

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

    @log_async_operation(operation_name="sync_stock_basic")
    async def sync_stock_basic(self):
        """Sync stock basic info (Step 1 of initialization)."""
        if self.is_cancelled():
            return 0

        # Deduplication Lock - prevent concurrent runs
        should_run = False
        with self._sync_lock:
            if not self._is_syncing_basic:
                self._is_syncing_basic = True
                should_run = True

        if not should_run:
            logger.debug("[sync_stock_basic] Already running, skipping")
            return 0

        try:
            logger.info("[sync_stock_basic] Starting stock list sync...")
            df = await self.api.get_stock_list()

            if df is not None and not df.empty:
                count = await self.cache.save_stock_basic(df)
                await self.cache.update_sync_status('stock_basic', get_now().strftime('%Y%m%d'), count)
                logger.info(f"[sync_stock_basic] ✅ Synced {count} stocks")
                return count
            else:
                logger.warning("[sync_stock_basic] API returned empty data")
                return 0

        except Exception as e:
            logger.error(f"[sync_stock_basic] ❌ Failed: {e}", exc_info=True)
            return 0
        finally:
            with self._sync_lock:
                self._is_syncing_basic = False

    @log_async_operation(operation_name="sync_concepts")
    async def sync_concepts(self):
        """Sync stock concepts from Tushare."""
        if self.is_cancelled():
            return 0

        try:
            logger.info("[sync_concepts] Starting concept sync...")
            # Strategy:
            # 1. Get all concepts: `df_concepts = pro.concept()`
            # 2. Parallel fetch `pro.concept_detail(id=c)` for all `c` using ThreadPool and Semaphore.
            # 3. Merge results and save atomically.

            # Fetch Concept List
            # Fetch Concept List (uses _handle_api_call for rate limiting)
            df_c = await self.api.get_concept_list()
            if df_c is None or df_c.empty:
                return 0

            c_codes = df_c['code'].tolist()

            # Use Semaphore to limit concurrency (Architecturally better than chunking)
            # TushareClient has internal rate limiting, so we just limit concurrency to avoid
            # overloading the ThreadPool or local resources.
            concurrency = ConfigHandler.get_sync_concurrency_light()
            sem = asyncio.Semaphore(concurrency or 20)

            async def fetch_one(c):
                # Check cancellation before acquiring semaphore to fail fast
                if self.is_cancelled(): return None
                async with sem:
                    # Double check inside semaphore
                    if self.is_cancelled(): return None
                    return await self.api.get_concept_detail_by_id(c)

            # Create tasks eagerly but execute with semaphore
            # This avoids "coroutine never awaited" because we wrap in create_task
            tasks = [asyncio.create_task(fetch_one(c)) for c in c_codes]

            all_dfs = []
            try:
                # Wait for all tasks to complete or be cancelled
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check if we were cancelled during gather
                if self.is_cancelled():
                    return 0

                for r in results:
                    if isinstance(r, pd.DataFrame) and not r.empty:
                        all_dfs.append(r)
                    elif isinstance(r, Exception):
                        # Log but don't stop everything for one failed concept
                        logger.warning(f"[sync_concepts] Task failed: {r}")

            except asyncio.CancelledError:
                logger.info("[sync_concepts] Cancelled during gather")
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

            full_df = full_df.rename(columns={'id': 'concept_id'})
            # Ensure unique
            full_df = full_df[['ts_code', 'concept_name', 'concept_id']].drop_duplicates()

            # Atomic overwrite (refresh)
            count = await self.cache.overwrite_concepts(full_df)
            logger.info(f"[sync_concepts] ✅ Synced {count} concept mappings (Atomic)")
            return count

        except Exception as e:
            logger.error(f"[sync_concepts] ❌ Failed: {e}", exc_info=True)
            return 0


    async def prepare_market_data(self):
        """Prepare data for AI Analysis."""
        now = get_now()
        today_str = now.strftime('%Y%m%d')
        # Check if trading day, if not fallback to latest
        # Simplified logic compared to original for brevity but keeping intent

        latest = await self.get_latest_trade_date()
        if latest != today_str:
            return latest

        # If it IS today, check if we have data
        cached_date = await self.cache.get_latest_trade_date()
        if cached_date != today_str:
            # Sync today
            await self.sync_daily_market_snapshot(today_str)

        return today_str



    async def get_market_overview(self):
        """
        Get market overview data for Home Screen.
        Returns Indices (SH, SZ, CYB) and Northbound Money Flow.
        """
        try:
            now = get_now()
            today_str = now.strftime('%Y%m%d')
            start_str = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')

            # Calendar Check (Cached inside ensure_trade_cal)
            await self.ensure_trade_cal(today_str)

            # Find latest valid date
            cache_df = await self.cache.get_trade_cal(start_date=start_str, end_date=today_str, is_open=1)
            date = today_str
            if not cache_df.empty:
                date = sorted(cache_df['cal_date'].tolist())[-1]

            # Parallel Fetch
            async def get_idx(code, name_key):
                df = await self.api.get_index_daily(ts_code=code, trade_date=date)
                name = I18n.get(name_key)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    c = row.get('pct_chg', 0)
                    v = row.get('close', 0)
                    return {'name': name, 'value': f"{v:.2f}", 'change': f"{c:+.2f}%",
                            'color': 'red' if c >= 0 else 'green'}
                return {'name': name, 'value': '-', 'change': '-', 'color': 'grey'}

            async def get_hsgt():
                df = await self.api.get_moneyflow_hsgt(trade_date=date)
                name = I18n.get('home_northbound')
                if df is not None and not df.empty:
                    val = float(df.iloc[0]['north_money'])
                    val_str = f"{val / 100:.2f}{I18n.get('unit_yi')}" if abs(
                        val) > 100 else f"{val:.0f}{I18n.get('unit_wan')}"
                    sub_str = I18n.get('home_inflow') if val > 0 else I18n.get('home_outflow')
                    return {'name': name, 'value': val_str, 'sub': sub_str}
                return {'name': name, 'value': '-', 'sub': '-'}

            hot_concepts_task = NewsFetcher.get_hot_concepts(limit=8)

            results = await asyncio.gather(
                get_idx('000001.SH', 'home_index_sh'),
                get_idx('399001.SZ', 'home_index_sz'),
                get_idx('399006.SZ', 'home_index_cyb'),
                get_hsgt(),
                hot_concepts_task
            )

            return {
                'date': date,
                'indices': results[:3],
                'hsgt': results[3],
                'hot_concepts': results[4]
            }

        except Exception as e:
            logger.error(f"Failed to get market overview: {e}", exc_info=True)
            return None

    async def get_screening_data(self, trade_date=None):
        return await self.cache.get_screening_data(trade_date)

    async def initialize_system(self, progress_callback=None):
        """
        Orchestrate system initialization with 6 distinct steps.
        
        Steps and weights:
        - Step 1 (1%):  Sync stock list
        - Step 2 (1%):  Sync trade calendar
        - Step 3 (45%): Sync historical data
        - Step 4 (38%): Sync financial data
        - Step 5 (10%): Sync AI core data (macro/holders)
        - Step 6 (5%):  Health check
        
        Returns:
            dict: Health check result on success
            None: If cancelled or critical failure
            
        Note: Call request_cancel() to cancel this operation.
        """
        from ui.i18n import I18n
        from data.data_dictionary import validate_schema_definitions

        # Run schema validation (DD-01)
        validate_schema_definitions()

        # Clear any previous cancel state
        self.clear_cancel()

        # Step weights (must sum to 100)
        # Optimized based on user feedback (Steps 1 & 2 represent 2% total)
        # Added Step 5 (AI Data) -> 10%
        STEP_WEIGHTS = [1, 1, 45, 38, 10, 5]
        current_step = 0

        def report_step(step_num, sub_progress=0, sub_total=1, sub_msg=""):
            """Report progress with weighted calculation."""
            nonlocal current_step
            current_step = step_num

            if not progress_callback:
                return

            # Calculate base progress (sum of completed step weights)
            base = sum(STEP_WEIGHTS[:step_num - 1])

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
                logger.error("[initialize_system] Step 1 failed: No stocks synced, aborting")
                return None
            if self.is_cancelled(): return None

            # ===== Step 1.5: Concepts (runs as part of Step 1) =====
            report_step(1, 0.5, 1, I18n.get("init_sync_concepts"))
            await self.sync_concepts()

            # ===== Step 2: Trade Calendar (5%) =====
            report_step(2)
            end_date = get_now().strftime('%Y%m%d')
            start_date = (get_now() - datetime.timedelta(days=365 * 3)).strftime('%Y%m%d')
            cal_success = await self.ensure_trade_cal(end_date, required_start_date=start_date)
            if not cal_success:
                logger.error("[initialize_system] Step 2 failed: Trade calendar sync failed, aborting")
                return None
            if self.is_cancelled(): return None

            # ===== Step 3: Historical Data (50%) =====
            def step3_callback(current, total, msg):
                report_step(3, current, total, msg)

            history_result = await self.sync_historical_data(
                days=365 * 3,
                progress_callback=step3_callback
            )
            if history_result and history_result.status == "failed":
                logger.error(f"[initialize_system] Step 3 failed: {history_result.errors}")
                return None
            if self.is_cancelled(): return None

            # ===== Step 4: Financial Data (35%) =====
            def step4_callback(current, total, msg):
                report_step(4, current, total, msg)

            financial_result = await self.sync_comprehensive_fundamentals(
                progress_callback=step4_callback
            )
            if financial_result and financial_result.status == "failed":
                logger.error(f"[initialize_system] Step 4 failed: {financial_result.errors}")
                return None
            if self.is_cancelled(): return None

            # ===== Step 5: AI Alpha Data (10%) =====
            report_step(5, 0, 3, I18n.get("init_sync_macro"))

            macro_res = await self.strategies['macro'].run()
            if macro_res.status == "failed":
                logger.warning(f"[initialize_system] Macro sync failed: {macro_res.errors}")

            report_step(5, 1, 3, I18n.get("init_sync_holders"))

            holder_res = await self.strategies['holder'].run()
            if holder_res.status == "failed":
                logger.warning(f"[initialize_system] Holder sync failed: {holder_res.errors}")

            report_step(5, 2, 3, I18n.get("init_step_5_done"))

            if self.is_cancelled(): return None

            # ===== Step 6: Health Check (5%) =====
            report_step(6)
            result = await self.check_data_health()

            # Report completion
            if progress_callback:
                progress_callback(100, 100, I18n.get("init_step_complete"))

            return result

        except Exception as e:
            step_label = I18n.get(f"init_step_{current_step}") if current_step > 0 else "Initialization"
            logger.error(f"[initialize_system] ❌ Failed at {step_label}: {e}")
            raise  # Re-raise so UI can catch and display

    # ... get_stock_history, get_strategy_data ...
    async def get_stock_history(self, ts_code, days=365):
        end = get_now().strftime('%Y%m%d')
        start = (get_now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
        return await self.cache.get_daily_quotes(ts_code=ts_code, start_date=start, end_date=end)

    async def get_strategy_data(self):
        return await self.prepare_screening_context()

    @log_async_operation(operation_name="prepare_screening_context")
    async def prepare_screening_context(self):
        """Prepare context for screening execution."""

        # Ensure quality tier has been initialized before screening
        # None = never checked; runs the lightweight fast-path exactly once per app lifecycle
        if self._quality_tier is None:
            await self._assign_basic_tier()

        context = {}
        # Decorator handles exception logging. Exceptions will bubble up to ViewModel.

        # 1. Main Screening Data (Quotes + Indicators)
        # Use actual latest trade date available in the database
        trade_date = await self.cache.get_latest_trade_date()
        context['screening_data'] = await self.get_screening_data(trade_date)

        # 2. Auxiliary Data
        # Northbound
        nb = await self.cache.get_northbound(trade_date=trade_date)
        if nb is not None:
            context['northbound_data'] = nb

        # Moneyflow
        mf = await self.cache.get_moneyflow(trade_date=trade_date)
        if mf is not None:
            context['moneyflow_data'] = mf

        # Top List (LHB)
        lhb = await self.cache.get_top_list(trade_date=trade_date)
        if lhb is not None:
            context['top_list'] = lhb

        # Block Trade
        blk = await self.cache.get_block_trade(trade_date=trade_date)
        if blk is not None:
            context['block_trade'] = blk

        return context
