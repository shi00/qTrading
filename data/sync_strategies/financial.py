"""
Financial Sync Strategy.
Handles comprehensive fundamentals, incremental updates, and data repair.
"""
import asyncio
import datetime
import logging
import threading
from typing import List

import pandas as pd

from data.constants import FINANCIAL_REPORT_SCHEMA_COLS, FINANCIAL_BATCH_TABLES
from data.sync_strategies.base import ISyncStrategy, SyncResult
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)


class FinancialSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing financial reports and fundamental data.
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

        if not hasattr(current_loop, '_fina_shutdown_evt'):
            setattr(current_loop, '_fina_shutdown_evt', asyncio.Event())
            
        return getattr(current_loop, '_fina_shutdown_evt')

    async def cancel(self):
        """Signal cancellation."""
        self._shutdown_event.set()
        logger.info("[FinancialSyncStrategy] Cancellation signal received.")
        # Cancel active tasks
        with self._tasks_lock:
            for task in self._active_tasks:
                if not task.done():
                    task.cancel()

    async def run(self, periods: List[str] = None, force: bool = False, progress_callback=None, **kwargs) -> SyncResult:
        """
        Main entry point. Decides between Full vs Incremental sync.
        Note: Cancellation is handled via cancel() method called by DataProcessor.request_cancel()
        """
        self._shutdown_event.clear()
        result = SyncResult()

        try:
            should_full_sync = False
            if periods is not None:
                should_full_sync = True
                logger.info("[sync_financial] STRATEGY: Full Sync (manual periods)")
            elif force:
                should_full_sync = True
                logger.info("[sync_financial] STRATEGY: Full Sync (force=True)")
            else:
                status = await self.context.cache.get_sync_status('financial_reports')
                if not status or not status.get('last_sync_date'):
                    should_full_sync = True
                    logger.info("[sync_financial] STRATEGY: Full Sync (first run)")

            if should_full_sync:
                await self._run_full_sync(periods, progress_callback, force=force, result_accumulator=result)
            else:
                await self._run_incremental_sync(progress_callback, result_accumulator=result)

        except asyncio.CancelledError:
            logger.info("[FinancialSyncStrategy] Operation cancelled.")
            result.status = "cancelled"
        except Exception as e:
            logger.error(f"[FinancialSyncStrategy] Error: {e}", exc_info=True)
            result.status = "failed"
            result.errors.append(str(e))

        return result

    async def _run_full_sync(self, periods, progress_callback, force, result_accumulator: SyncResult):
        """
        Redirects to sync_comprehensive_fundamentals. 
        Note: 'periods' argument was historically used but the new comprehensive sync 
        fetching usually covers a wide range or latest. 
        If specific periods are requested, we might need to adjust logic, 
        but existing DataProcessor.sync_comprehensive_fundamentals fetches by stock, not by period.
        
        The 'periods' arg in original valid for 'sync_financial_reports' but 'sync_comprehensive_fundamentals' 
        ignores it and fetches EVERYTHING for active stocks.
        We will stick to the robust 'sync_comprehensive_fundamentals' logic.
        """
        if self._shutdown_event.is_set():
            return

        logger.info(f"[FinancialSyncStrategy] Starting Comprehensive Fundamentals Sync (force={force})...")

        # Force Logic: Reset sync status for resume
        if force:
            logger.warning("[sync_fundamentals] Force mode: Clearing previous sync status...")
            await self.context.cache.clear_step4_sync_status()

        # 1. Get Stock List
        df_basic = await self.context.cache.get_stock_basic()
        if df_basic.empty:
            logger.error("[FinancialSyncStrategy] No stocks found. Please run Basic Sync first.")
            result_accumulator.errors.append("No stocks found in cache")
            result_accumulator.status = "failed"
            return

        df_active = df_basic[df_basic['list_status'] == 'L']
        all_stocks = set(df_active['ts_code'].tolist())
        total_stocks = len(all_stocks)

        # 2. Concurrency Control
        concurrency = ConfigHandler.get_sync_max_concurrent_heavy()
        semaphore = asyncio.Semaphore(concurrency)

        # 3. Data-as-State Resume Logic
        synced_stocks = await self.context.cache.get_completed_step4_stocks(sync_version=1)
        pending_stocks = sorted([s for s in all_stocks if s not in synced_stocks])
        skipped_count = total_stocks - len(pending_stocks)

        # Update result stats
        result_accumulator.updated += skipped_count  # Technically skipped, but "done"

        if skipped_count > 0:
            logger.info(f"[sync_fundamentals] RESUME: {skipped_count} stocks completed, {len(pending_stocks)} pending")

        if not pending_stocks:
            logger.info("[sync_fundamentals] All stocks already synced.")
            if progress_callback:
                progress_callback(total_stocks, total_stocks,
                                  I18n.get('progress_sync_fundamentals').format(current=total_stocks,
                                                                                total=total_stocks,
                                                                                stock=I18n.get("status_ready")))
            return

        completed_count = skipped_count

        # Invariant Dates
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=365 * 3)).strftime('%Y%m%d')

        # Generate full date range for batch sync (run AFTER stock loop to avoid API contention)
        all_dates = []
        curr = datetime.datetime.strptime(start_date, '%Y%m%d')
        end_dt_obj = datetime.datetime.strptime(end_date, '%Y%m%d')
        while curr <= end_dt_obj:
            all_dates.append(curr.strftime('%Y%m%d'))
            curr += datetime.timedelta(days=1)

        # Inner processing function
        async def process_one_stock(ts_code):
            nonlocal completed_count
            if self._shutdown_event.is_set(): return

            processed = False
            try:
                async with semaphore:
                    if self._shutdown_event.is_set(): return

                    processed = True
                    has_error = False

                    # Fetch Helper
                    async def fetch_safe(func, kwargs):
                        nonlocal has_error
                        try:
                            # Use ThreadPoolManager for consistent thread pool management
                            return await ThreadPoolManager().run_async(TaskType.IO, lambda: func(**kwargs))
                        except (AttributeError, NameError, TypeError, ImportError) as e:
                            raise e  # Critical
                        except Exception as e:
                            has_error = True
                            logger.warning(f"Fetch failed for {ts_code} [{func.__name__}]: {e}")
                            return None

                    # Task Specs
                    # Args Type: 0=start+end, 1=end only, 2=start only
                    task_specs = [
                        (self.context.api.get_income, self.context.cache.save_financial_reports, 0),
                        (self.context.api.get_balancesheet, self.context.cache.save_financial_reports, 0),
                        (self.context.api.get_cashflow, self.context.cache.save_financial_reports, 0),
                        (self.context.api.get_fina_indicator, self.context.cache.save_financial_reports, 0),
                        (self.context.api.get_fina_audit, self.context.cache.save_fina_audit, 0),
                        (self.context.api.get_fina_mainbz, self.context.cache.save_fina_mainbz, 0),
                        # Note: Repurchase/Dividend removed from Stock Loop (moved to Batch Loop)
                        # But wait, original code had them. 
                        # If we keep them here, it's redundant but safe (Double Coverage).
                        # "Veteran" says: Remove redundancy to save API.
                        # BUT: Older historic data might be missed by batch window if we only sync 30 days.
                        # Full sync covers 3 years of dates, so batch handles it!
                        # So we REMOVE Repurchase/Dividend from here.
                    ]

                    futures = []
                    for fetch_func, _, arg_type in task_specs:
                        kw = {'ts_code': ts_code}
                        if arg_type == 0:
                            kw.update(start_date=start_date, end_date=end_date)
                        elif arg_type == 1:
                            kw.update(end_date=end_date)
                        elif arg_type == 2:
                            kw.update(start_date=start_date)
                        futures.append(fetch_safe(fetch_func, kw))

                    results = await asyncio.gather(*futures)

                    # Save Results
                    for i, result_data in enumerate(results):
                        if result_data is not None:
                            save_func = task_specs[i][1]
                            await save_func(result_data)

                    if not has_error:
                        await self.context.cache.mark_stock_step4_completed(ts_code, sync_version=1)
                        result_accumulator.added += 1
                    else:
                        logger.warning(f"Stock {ts_code} incomplete, will retry.")

            except Exception as e:
                logger.error(f"Failed Step 4 for {ts_code}: {e}")
                # Don't abort all, just this stock

            # Per-stock progress update (advance progress bar for processed stocks)
            if processed:
                completed_count += 1
                if progress_callback and completed_count % 5 == 0:
                    # Stock phase occupies 0→80% of Step 4 progress
                    pct = int(completed_count / total_stocks * 80)
                    progress_callback(pct, 100,
                        I18n.get('progress_sync_fundamentals').format(
                            current=completed_count, total=total_stocks, stock=ts_code))

        # Batch execution
        batch_size = ConfigHandler.get_max_batch_rows()
        for i in range(0, len(pending_stocks), batch_size):
            if self._shutdown_event.is_set(): break

            batch = pending_stocks[i: i + batch_size]
            tasks = [asyncio.create_task(process_one_stock(code)) for code in batch]

            with self._tasks_lock:
                self._active_tasks.update(tasks)

            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)

        # Batch phase: corporate actions sync (serial, AFTER stock loop to avoid API contention)
        logger.info("[FinancialSyncStrategy] Starting Batch Sync (Corporate Actions)...")

        def batch_progress(current, total, msg):
            if progress_callback:
                # Batch phase occupies 80→100% of Step 4 progress
                pct = 80 + int(current / max(total, 1) * 20)
                progress_callback(pct, 100, msg)

        await self._sync_corporate_actions_by_date(all_dates, batch_progress)

    async def _run_incremental_sync(self, progress_callback, result_accumulator: SyncResult):
        """
        Incremental Sync: Query disclosure date.
        """
        if self._shutdown_event.is_set(): return

        status = await self.context.cache.get_sync_status('financial_reports')
        last_sync_str = status.get('last_sync_date')

        try:
            last_sync_dt = datetime.datetime.strptime(last_sync_str, '%Y-%m-%d %H:%M:%S')
            start_date_dt = last_sync_dt + datetime.timedelta(days=1)
        except Exception:
            # Fallback
            start_date_dt = datetime.datetime.now() - datetime.timedelta(days=30)

        today_dt = datetime.datetime.now()
        dates_to_sync = []
        curr = start_date_dt
        while curr.date() <= today_dt.date():
            dates_to_sync.append(curr.strftime('%Y%m%d'))
            curr += datetime.timedelta(days=1)

        if not dates_to_sync:
            logger.debug("[incremental_sync] Data already up-to-date")
            return

        total_saved = 0
        concurrency = ConfigHandler.get_sync_max_concurrent_heavy()
        semaphore = asyncio.Semaphore(concurrency)

        for day_str in dates_to_sync:
            df_disclosure = await ThreadPoolManager().run_async(TaskType.IO,
                                                                lambda d=day_str: self.context.api.get_disclosure_date(
                                                                    date=d))

            if df_disclosure is None or df_disclosure.empty:
                continue

            target_list = df_disclosure[['ts_code', 'end_date']].drop_duplicates().to_dict('records')
            if not target_list:
                continue

            tasks = []

            async def sync_one_target(item):
                nonlocal total_saved
                ts_code = item['ts_code']
                period = item['end_date']

                async with semaphore:
                    try:
                        await asyncio.sleep(ConfigHandler.get_sync_request_delay(is_heavy=False))
                        # Use internal helper
                        df = await self._fetch_comprehensive_financial_data(ts_code, period=period)

                        if df is not None and not df.empty:
                            # Apply Schema
                            for col in FINANCIAL_REPORT_SCHEMA_COLS:
                                if col not in df.columns:
                                    df[col] = None

                            count = await self.context.cache.save_financial_reports(df[FINANCIAL_REPORT_SCHEMA_COLS])
                            if count > 0:
                                total_saved += count
                    except Exception as e:
                        logger.warning(f"Failed incremental sync for {ts_code} {period}: {e}")

            for item in target_list:
                tasks.append(sync_one_target(item))

            await asyncio.gather(*tasks)

            if progress_callback:
                progress_callback(0, 0, f"{I18n.get('progress_sync_done')} {day_str}")

        # --- Step 2: Batch Sync Corporate Actions (O(Time) Optimization) ---
        # Sync Forecasts, Dividends, etc. by date
        await self._sync_corporate_actions_by_date(dates_to_sync)

        await self.context.cache.update_sync_status('financial_reports', datetime.datetime.now().strftime('%Y%m%d'),
                                                    total_saved)
        result_accumulator.added = total_saved

    async def _sync_corporate_actions_by_date(self, dates: List[str], progress_callback=None):
        """
        Batch sync corporate actions (Forecast, Dividend, etc.) by date.
        Uses O(Time) strategy instead of O(Stock).
        Iterates per-date to avoid creating thousands of tasks at once.
        """
        if not dates: return

        total = len(dates)
        logger.info(f"[HybridSync] Batch syncing corporate actions for {total} days...")

        concurrency = ConfigHandler.get_sync_max_concurrent_heavy()
        semaphore = asyncio.Semaphore(concurrency)

        async def sync_one_date_table(date_str, table_name, table_cfg):
            async with semaphore:
                try:
                    api_method = getattr(self.context.api, table_cfg['api'])
                    df = await ThreadPoolManager().run_async(TaskType.IO, lambda: api_method(ann_date=date_str))

                    if df is not None and not df.empty:
                        save_map = {
                            'fina_forecast': self.context.cache.save_fina_forecast,
                            'dividend': self.context.cache.save_dividend,
                            'repurchase': self.context.cache.save_repurchase,
                        }

                        save_func = save_map.get(table_name)
                        if save_func:
                            await save_func(df)
                            logger.debug(f"[HybridSync] Synced {table_name} for {date_str}: {len(df)} records")

                except Exception as e:
                    err_str = str(e).lower()
                    if "permission" in err_str or "积分" in err_str or "no access" in err_str:
                        logger.warning(f"[HybridSync] ⛔ Permission Denied for {table_name}: {e}")
                        await self.context.cache.update_sync_status(table_name, date_str, 0, status='permission_denied')
                    else:
                        logger.warning(f"[HybridSync] Failed batch sync {table_name} on {date_str}: {e}")

        # Iterate per-date (not all-at-once) to avoid task explosion
        for i, d in enumerate(dates):
            if self._shutdown_event.is_set():
                logger.info("[HybridSync] Cancelled during corporate actions batch sync")
                break

            # Each date: 3 tables in parallel
            coros = [sync_one_date_table(d, tbl, cfg)
                     for tbl, cfg in FINANCIAL_BATCH_TABLES.items()]
            await asyncio.gather(*coros)

            # Report progress every 10 days
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(i + 1, total,
                    I18n.get('progress_sync_fundamentals').format(
                        current=i + 1, total=total, stock=f"batch:{d}"))

        # Final completion report
        if progress_callback:
            progress_callback(total, total, I18n.get("status_ready"))

    async def _fetch_comprehensive_financial_data(self, ts_code, start_date=None, end_date=None, period=None):
        """
        Helper: Fetch and merge Income, Balance Sheet, and Financial Indicators.
        """
        api = self.context.api

        # Use ThreadPoolManager for consistent thread pool management
        async def fetch_income():
            return await ThreadPoolManager().run_async(TaskType.IO,
                                                       lambda: api.get_income(ts_code=ts_code, start_date=start_date,
                                                                              end_date=end_date, period=period))

        async def fetch_balance():
            return await ThreadPoolManager().run_async(TaskType.IO, lambda: api.get_balancesheet(ts_code=ts_code,
                                                                                                 start_date=start_date,
                                                                                                 end_date=end_date,
                                                                                                 period=period))

        async def fetch_indicator():
            return await ThreadPoolManager().run_async(TaskType.IO, lambda: api.get_fina_indicator(ts_code=ts_code,
                                                                                                   start_date=start_date,
                                                                                                   end_date=end_date,
                                                                                                   period=period))

        # New: Fetch Stock-Based Auxiliary Tables (MainBz, Audit, Pledge)
        # We wrap these in individual try-except blocks to allow partial success (Graceful Degradation)
        async def fetch_aux(api_func, save_func, table_name, **kwargs):
            try:
                df = await ThreadPoolManager().run_async(TaskType.IO, lambda: api_func(**kwargs))
                if isinstance(df, pd.DataFrame) and not df.empty:
                    await save_func(df)
                    # Mark success status
                    # Note: We can't log date per row here easily, so we skip status update for aux tables per stock
                    # or update a general status?
                return True
            except Exception as e:
                err_str = str(e).lower()
                if "permission" in err_str or "积分" in err_str:
                    logger.debug(f"Permission denied for {table_name} on {ts_code}")
                return False

        try:
            aux_tasks = [
                fetch_aux(api.get_fina_mainbz, self.context.cache.save_fina_mainbz, 'fina_mainbz', ts_code=ts_code,
                          period=period, start_date=start_date, end_date=end_date),
                fetch_aux(api.get_fina_audit, self.context.cache.save_fina_audit, 'fina_audit', ts_code=ts_code,
                          start_date=start_date, end_date=end_date)
            ]

            # Parallel fetch core + aux
            results = await asyncio.gather(
                fetch_income(), fetch_balance(), fetch_indicator(),
                *aux_tasks,
                return_exceptions=True
            )

            # Unpack Core Results
            # results[0-2] are core, results[3-5] are aux (bools)
            df_inc, df_bal, df_fina = results[0], results[1], results[2]

            # 2. Proceed with Core Financial Merging (Income/Balance/Indicator)
            dfs = []
            if isinstance(df_inc, pd.DataFrame) and not df_inc.empty:
                dfs.append(df_inc.sort_values('end_date').drop_duplicates(subset=['end_date'], keep='last'))
            if isinstance(df_bal, pd.DataFrame) and not df_bal.empty:
                dfs.append(df_bal.sort_values('end_date').drop_duplicates(subset=['end_date'], keep='last'))
            if isinstance(df_fina, pd.DataFrame) and not df_fina.empty:
                dfs.append(df_fina.sort_values('end_date').drop_duplicates(subset=['end_date'], keep='last'))

            if not dfs: return None

            df_merged = dfs[0]
            for i in range(1, len(dfs)):
                df_merged = pd.merge(df_merged, dfs[i], on=['ts_code', 'end_date'], how='outer', suffixes=('', '_drop'))

            for col in df_merged.columns:
                if col.endswith('_drop'):
                    df_merged.drop(columns=[col], inplace=True)

            return df_merged

        except Exception as e:
            logger.warning(f"Failed to fetch comprehensive data for {ts_code}: {e}")
            return None

    async def repair_financial_data(self, ts_codes, progress_callback=None) -> int:
        """
        Targeted repair for specific stocks.
        Fixes ALL tables defined in constants.
        """
        if not ts_codes:
            return 0

        now = datetime.datetime.now()
        current_year = now.year
        p_cands = []
        for y in range(current_year, current_year - 4, -1):
            p_cands.extend([f"{y}0331", f"{y}0630", f"{y}0930", f"{y}1231"])
        periods = sorted([p for p in p_cands if p < now.strftime('%Y%m%d')], reverse=True)[:12]

        logger.info(f"[FinancialSyncStrategy] 🚑 Comprehensive Repairing {len(ts_codes)} stocks...")

        semaphore = asyncio.Semaphore(1)
        total_saved = 0

        # BUG FIX: Define repair_one OUTSIDE the for loop and pass period/period_idx as
        # default-argument parameters. Python closures capture variable names, not values,
        # so defining async def inside the loop causes every task to see the last iteration's
        # period once all tasks are scheduled and asyncio.gather begins executing them.
        async def repair_one(ts_code: str, idx: int, period: str, p_idx: int):
            nonlocal total_saved
            async with semaphore:
                try:
                    await asyncio.sleep(ConfigHandler.get_sync_request_delay(is_heavy=True))
                    await self._fetch_comprehensive_financial_data(ts_code, period=period)
                    total_saved += 1

                    if progress_callback and idx % 10 == 0:
                        progress_callback(
                            p_idx * len(ts_codes) + idx,
                            len(periods) * len(ts_codes),
                            I18n.get("status_repairing", period=period, code=ts_code)
                        )
                except Exception as e:
                    logger.debug(f"[Repair] Failed for {ts_code} period={period}: {e}")

        tasks = []
        for period_idx, period in enumerate(periods):
            for i, ts_code in enumerate(ts_codes):
                # Pass period and period_idx explicitly so each task captures its own values
                tasks.append(asyncio.create_task(repair_one(ts_code, i, period, period_idx)))

        if tasks:
            await asyncio.gather(*tasks)

        return total_saved
