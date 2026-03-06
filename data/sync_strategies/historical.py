"""
Historical Sync Strategy.
Handles daily market snapshots, historical backfill, and retry logic.
"""
import asyncio
import datetime
import logging
import threading

import inspect
import pandas as pd

from data.constants import MAJOR_INDICES
from data.sync_strategies.base import ISyncStrategy, SyncResult
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType
from utils.log_decorators import log_async_operation, PerfThreshold
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class HistoricalSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing historical market data (Quotes, Indicators, MoneyFlow, etc.)
    """

    def __init__(self, context):
        super().__init__(context)
        self._lazy_event = None  # ST-01: Lazy init
        self._tasks_lock = threading.Lock()
        self._active_tasks = set()

    @property
    def _shutdown_event(self):
        """Get or create shutdown event dynamically per event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Event()

        if not hasattr(current_loop, '_hist_shutdown_evt'):
            setattr(current_loop, '_hist_shutdown_evt', asyncio.Event())
            
        return getattr(current_loop, '_hist_shutdown_evt')

    async def cancel(self):
        """Signal cancellation."""
        self._shutdown_event.set()
        logger.debug("[HistoricalSync] Stop | Cancellation signal received.")
        with self._tasks_lock:
            for task in self._active_tasks:
                if not task.done():
                    task.cancel()

    @log_async_operation(operation_name="HistoricalSyncStrategy.run", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def run(self, days: int = 365, progress_callback=None, **kwargs) -> SyncResult:
        """
        Main entry point for historical sync.
        Note: Cancellation is handled via cancel() method called by DataProcessor.request_cancel()
        """
        self._shutdown_event.clear()
        result = SyncResult()

        try:
            await self._run_historical_sync(days, progress_callback, result)
        except asyncio.CancelledError:
            result.status = "cancelled"
        except Exception as e:
            logger.error(f"[HistoricalSync] Run | ❌ Top-level failure: {e}", exc_info=True)
            result.status = "failed"
            result.errors.append(str(e))

        return result

    async def _run_historical_sync(self, days, progress_callback, result: SyncResult):
        """
        Sync historical data for the last N days.
        """
        end_date = get_now().strftime('%Y%m%d')
        start_date = (get_now() - datetime.timedelta(days=days)).strftime('%Y%m%d')

        # Use cached trade calendar (Step 2 already ensured calendar is available)
        try:
            df_cal = await self.context.cache.get_trade_cal(start_date=start_date, end_date=end_date, is_open=1)
            if df_cal is not None and not df_cal.empty:
                trade_dates = sorted(df_cal['cal_date'].tolist(), reverse=True)
            else:
                trade_dates = []
        except Exception as e:
            logger.warning(f"[HistoricalSync] Calendar | ⚠️ Cache calendar retrieval failed: {e}")
            trade_dates = []

        if not trade_dates:
            result.status = "failed"
            result.errors.append("No trade dates found")
            return

        # Breakpoint Resume (Check Cache)
        try:
            cached_quotes = await self.context.cache.get_cached_trade_dates()
            cached_inds = await self.context.cache.get_cached_indicator_dates()
            existing = cached_quotes.intersection(cached_inds)

            original_count = len(trade_dates)
            trade_dates = [d for d in trade_dates if d not in existing]
            skipped = original_count - len(trade_dates)
            result.updated += skipped

            if skipped > 0:
                logger.debug(f"[HistoricalSync] Resume | Skipped {skipped} cached dates.")
        except Exception as e:
            logger.warning(f"[HistoricalSync] Resume | ⚠️ Cache check failed: {e}")

        total_days = len(trade_dates)
        concurrency = ConfigHandler.get_sync_max_concurrent_heavy()
        semaphore = asyncio.Semaphore(max(1, concurrency))  # Use config

        # if concurrency > 3:
        #      logger.warning(f"[HistoricalSync] High concurrency {concurrency} detected.")

        failed_dates = []
        CB_THRESHOLD = max(20, int(total_days * 0.1) if total_days > 0 else 20)
        abort_sync = False
        processed_count = 0
        BATCH_SIZE = 20

        async def sync_one_day(date):
            nonlocal abort_sync, processed_count
            if self._shutdown_event.is_set() or abort_sync:
                return

            async with semaphore:
                if self._shutdown_event.is_set() or abort_sync: return

                # Circuit Breaker Check
                if len(failed_dates) > CB_THRESHOLD:
                    abort_sync = True
                    result.status = "failed"
                    result.errors.append(f"Circuit breaker triggered: {len(failed_dates)} failures")
                    logger.error(f"[HistoricalSync] CircuitBreaker | ❌ Abort: {len(failed_dates)} consecutive failures exceeded threshold {CB_THRESHOLD}")
                    return

                try:
                    await self.sync_daily_market_snapshot(date)
                    processed_count += 1
                    result.added += 1
                    if progress_callback:
                        progress_callback(processed_count, total_days,
                                          I18n.get('progress_sync_market').format(date=date))
                except Exception as e:
                    # Specific error handling
                    logger.warning(f"[HistoricalSync] DaySync | ⚠️ Failed {date}: {e}", exc_info=True)
                    failed_dates.append(date)

        # Batch Processing
        for batch_start in range(0, len(trade_dates), BATCH_SIZE):
            if self._shutdown_event.is_set() or abort_sync:
                break

            batch = trade_dates[batch_start:batch_start + BATCH_SIZE]
            tasks = [asyncio.create_task(sync_one_day(d)) for d in batch]

            with self._tasks_lock:
                self._active_tasks.update(tasks)

            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)

        # Smart Retry
        if failed_dates and not self._shutdown_event.is_set() and not abort_sync:
            MAX_RETRIES = ConfigHandler.get_sync_retry_count()
            logger.debug(f"[HistoricalSync] Retry | Retrying {len(failed_dates)} failed dates...")

            for retry_round in range(MAX_RETRIES):
                if not failed_dates or self._shutdown_event.is_set():
                    break
                await asyncio.sleep(2)

                current_batch = failed_dates[:]
                failed_dates = []
                retry_sem = asyncio.Semaphore(2)

                async def retry_one(date):
                    if self._shutdown_event.is_set(): return
                    async with retry_sem:
                        try:
                            await self.sync_daily_market_snapshot(date)
                            logger.debug(f"[HistoricalSync] Retry | ✅ Recovered {date}")
                            result.added += 1
                        except Exception:
                            failed_dates.append(date)

                # Batch Retry
                for r_start in range(0, len(current_batch), BATCH_SIZE):
                    if self._shutdown_event.is_set(): break
                    r_batch = current_batch[r_start:r_start + BATCH_SIZE]
                    r_tasks = [asyncio.create_task(retry_one(d)) for d in r_batch]

                    with self._tasks_lock:
                        self._active_tasks.update(r_tasks)
                    try:
                        await asyncio.gather(*r_tasks, return_exceptions=True)
                    finally:
                        with self._tasks_lock:
                            self._active_tasks.difference_update(r_tasks)

        if failed_dates:
            result.errors.append(f"{len(failed_dates)} dates failed after retries")
            result.status = "partial"

        if result.status == "failed":
            pass  # Already logged by CircuitBreaker ERROR above
        elif result.status == "partial":
            logger.warning(f"[HistoricalSync] Run | ⚠️ Partial. Added={result.added}, FailedDates={len(failed_dates)}")
        else:
            logger.info(f"[HistoricalSync] Run | ✅ Complete. Added={result.added}, FailedDates={len(failed_dates)}")

    async def sync_daily_market_snapshot(self, trade_date, force=False):
        """
        Sync ALL data types for a single day.
        """
        # Check cache (Test compatibility & Efficiency)
        if not force:
            # Fast check using sync status first (if implemented in cache/test)
            # Fallback to checking data existence (as per test expectation)
            # Check quotes as proxy for daily data
            if await self.context.cache.check_data_exists(trade_date):
                logger.debug(f"[HistoricalSync] DaySync | Cache hit for {trade_date}, skipping.")
                return True

        # Define fetch config
        # (key, func, name)
        task_configs = [
            ("quotes", self.context.api.get_daily_quotes, "Daily Quotes"),
            ("basic", self.context.api.get_daily_basic, "Daily Indicators"),
            ("limit", self.context.api.get_limit_list, "Limit List"),
            ("suspend", self.context.api.get_suspend_d, "Suspend List"),
            ("margin", self.context.api.get_margin_detail, "Margin Detail"),
            ("mf", self.context.api.get_moneyflow, "Money Flow"),
            ("north", self.context.api.get_hk_hold, "Northbound"),
            ("hsgt_flow", self.context.api.get_moneyflow_hsgt, "HSGT Flow"),
            ("lhb", self.context.api.get_top_list, "Dragon Tiger"),
            ("block", self.context.api.get_block_trade, "Block Trade"),
            ("index_basic", self.context.api.get_index_dailybasic, "Index Indicators")
        ]

        async def fetch_wrapper(key, func, name):
            try:
                # Return (key, data, error)
                return (key, await func(trade_date=trade_date), None)
            except Exception as e:
                logger.warning(f"[HistoricalSync] DaySync | ⚠️ Fetch {name} failed for {trade_date}: {e}")
                return (key, None, e)

        async def fetch_indices():
            try:
                tasks = [self.context.api.get_index_daily(ts_code=c,
                                                       trade_date=trade_date) for c in MAJOR_INDICES]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                valid = [r for r in results if isinstance(r, pd.DataFrame) and not r.empty]
                if valid:
                    return ("index", pd.concat(valid, ignore_index=True), None)
                return ("index", None, None)
            except Exception as e:
                return ("index", None, e)

        # Launch
        futures = [fetch_wrapper(*c) for c in task_configs]
        futures.append(fetch_indices())

        results_list = await asyncio.gather(*futures)
        # Parse results: Map key -> (data, error)
        data_map = {k: v for k, v, e in results_list}
        error_map = {k: e for k, v, e in results_list if e is not None}

        # CRITICAL CHECK: If Quotes or Basic failed, we MUST raise exception to trigger retry
        if 'quotes' in error_map:
            raise error_map['quotes']
        if 'basic' in error_map:
            raise error_map['basic']

        # Save Logic
        cache = self.context.cache

        async def save_if_ok(key, method, critical=False):
            df = data_map.get(key)
            if df is not None and not df.empty:
                target_func = method
                while hasattr(target_func, '__wrapped__'):
                    target_func = target_func.__wrapped__

                try:
                    sig = inspect.signature(target_func)
                    
                    if 'suppress_errors' in sig.parameters:
                        row_count = await method(df, suppress_errors=not critical)
                    else:
                        row_count = await method(df)
                    return row_count if row_count is not None else len(df)
                except Exception as e:
                    if critical:
                        logger.error(f"[HistoricalSync] DaySync | ❌ Critical save failed for {key}: {e}", exc_info=True)
                        raise e 
                    else:
                        logger.warning(f"[HistoricalSync] DaySync | ⚠️ Non-critical save {key} failed: {e}")
            return 0

        # 1. Quotes (Critical)
        quotes_rows = await save_if_ok("quotes", cache.save_daily_quotes, critical=True)
        
        # 2. Adj Factor (Critical, derived from quotes)
        df_quotes = data_map.get("quotes")
        if df_quotes is not None and not df_quotes.empty and 'adj_factor' in df_quotes.columns:
            try:
                await cache.save_adj_factors(df_quotes[['ts_code', 'trade_date', 'adj_factor']], suppress_errors=False)
            except Exception as e:
                logger.error(f"[HistoricalSync] DaySync | ❌ Critical save failed for adj_factor: {e}", exc_info=True)
                raise e

        # 3. Basic / Daily Indicators (Critical)
        basic_rows = await save_if_ok("basic", cache.save_daily_indicators, critical=True)

        # 4. Others (Non-critical, can fail silently or log)
        await save_if_ok("limit", cache.save_limit_list)
        await save_if_ok("suspend", cache.save_suspend_d)
        await save_if_ok("margin", cache.save_margin_daily)
        await save_if_ok("lhb", cache.save_top_list)
        await save_if_ok("block", cache.save_block_trade)
        await save_if_ok("mf", cache.save_moneyflow)
        await save_if_ok("hsgt_flow", cache.save_moneyflow_hsgt)
        await save_if_ok("index", cache.save_index_daily)
        await save_if_ok("index_basic", cache.save_index_dailybasic)

        # Northbound special filter
        try:
            df_north = data_map.get("north")
            if df_north is not None and not df_north.empty:
                df_north = df_north[df_north['ts_code'].astype(str).str.endswith(('.SH', '.SZ'))]
                if not df_north.empty:
                    await cache.save_northbound(df_north)
        except Exception as e:
            logger.warning(f"[HistoricalSync] DaySync | ⚠️ Northbound save failed (non-critical): {e}")

        # Strictly update sync status using actual rows written, protecting against silent DB failures
        if quotes_rows > 0:
            await cache.update_sync_status('daily_quotes', trade_date, quotes_rows)

        if basic_rows > 0:
            await cache.update_sync_status('daily_indicators', trade_date, basic_rows)

    async def sync_moneyflow(self, trade_date=None):
        """Sync money flow for a specific date (Standalone)."""
        if trade_date is None:
            # This requires getting latest date. Strategy doesn't have it easily.
            # Assume caller provides it or we use context to get it?
            # For standalone, let's use datetime.now or assume today if not provided
            # But better to let caller handle default.
            trade_date = get_now().strftime('%Y%m%d')

        try:
            df = await self.context.api.get_moneyflow(trade_date=trade_date)
            if df is not None and not df.empty:
                count = await self.context.cache.save_moneyflow(df)
                if count is not None and count > 0:
                    await self.context.cache.update_sync_status('moneyflow_daily', trade_date, count)
                return count
        except Exception as e:
            logger.warning(f"[HistoricalSync] MoneyFlow | ⚠️ Standalone sync failed: {e}")
        return 0

    async def sync_northbound(self, trade_date=None):
        """Sync northbound holding for a specific date (Standalone)."""
        if trade_date is None:
            trade_date = get_now().strftime('%Y%m%d')

        try:
            df = await self.context.api.get_hk_hold(trade_date=trade_date)
            if df is not None and not df.empty:
                df = df[df['ts_code'].astype(str).str.endswith(('.SH', '.SZ'))]
                if not df.empty:
                    count = await self.context.cache.save_northbound(df)
                    if count is not None and count > 0:
                        await self.context.cache.update_sync_status('northbound_holding', trade_date, count)
                    return count
        except Exception as e:
            logger.warning(f"[HistoricalSync] Northbound | ⚠️ Standalone sync failed: {e}")
        return 0
