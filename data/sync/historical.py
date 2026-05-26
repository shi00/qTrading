import typing

"""
Historical Sync Strategy.
Handles daily market snapshots, historical backfill, and retry logic.
"""

import asyncio
import datetime
import inspect
import logging
import threading

import pandas as pd

from data.constants import (
    CRITICAL_EMPTY_TABLES,
    MAJOR_INDICES,
    SYNC_RESULT_EMPTY,
    SYNC_RESULT_FETCH_FAILED,
    SYNC_RESULT_HAS_DATA,
    SYNC_RESULT_SAVE_FAILED,
    SYNC_RESULT_SKIPPED_PERMISSION,
)
from data.sync.base import ISyncStrategy, SyncResult
from data.persistence.daos.base_dao import EngineDisposedError
from data.external.tushare_client import TushareAPIPermissionError
from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.loop_local import get_loop_local
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class HistoricalSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing historical market data (Quotes, Indicators, MoneyFlow, etc.)
    """

    SYNCED_TABLES = [
        "daily_quotes",
        "daily_indicators",
        "moneyflow_daily",
        "limit_list",
        "suspend_d",
        "margin_daily",
        "northbound_holding",
        "moneyflow_hsgt",
        "top_list",
        "block_trade",
        "index_daily",
        "index_dailybasic",
    ]

    CORE_RESUME_TABLES = SYNCED_TABLES

    def __init__(self, context: typing.Any):
        super().__init__(context)
        self._lazy_event = None  # ST-01: Lazy init
        self._tasks_lock = threading.Lock()
        self._active_tasks = set()

    @property
    def _shutdown_event(self):
        """Get or create shutdown event dynamically per event loop."""

        def _factory():
            return asyncio.Event()

        return get_loop_local("hist_shutdown_evt", _factory)

    def cancel(self):
        """Signal cancellation."""
        super().cancel()
        try:
            self._shutdown_event.set()
        except RuntimeError:
            logger.debug("[HistoricalSync] Shutdown event unavailable (no event loop).")
        logger.debug("[HistoricalSync] Stop | Cancellation signal received.")
        with self._tasks_lock:
            for task in self._active_tasks:
                if not task.done():
                    task.cancel()

    async def _get_effective_trade_date(self) -> datetime.date:
        """Prefer the latest closed trade date for default sync operations."""
        try:
            trade_date = await self.context.processor.trade_calendar.get_latest_trade_date()  # type: ignore[union-attr]
            if trade_date is not None:
                return trade_date
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug(f"[HistoricalSync] get_latest_trade_date failed, using today: {e}")
        return get_now().date()

    @log_async_operation(
        operation_name="HistoricalSyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def run(
        self,
        days: int = 250,
        progress_callback: typing.Callable | None = None,
        **kwargs: typing.Any,
    ) -> SyncResult:
        """
        Main entry point for historical sync.
        Note: Cancellation is handled via cancel() method called by DataProcessor.request_cancel()
        """
        self._shutdown_event.clear()
        result = SyncResult()

        try:
            await self._run_historical_sync(days, progress_callback, result)

            if result.status in ["success", "partial"]:
                end_date = await self._get_effective_trade_date()
                calendar_days = int(days * 365 / 250) + 30
                start_date = end_date - datetime.timedelta(days=calendar_days)
                try:
                    quality_results = await self.context.cache.get_bulk_sync_quality_scores(
                        start_date=start_date,
                        end_date=end_date,
                        tables=list(self.CORE_RESUME_TABLES),
                    )
                    for date, quality in quality_results.items():
                        result.quality_scores[date] = quality.get("score", 0)
                        result.expected_bases[date] = quality.get("expected_base", 0)
                        if quality.get("issues"):
                            result.warnings.extend([f"{date}: {issue}" for issue in quality["issues"][:2]])

                        tables_info = quality.get("tables", {})
                        for table, info in tables_info.items():
                            if table not in result.table_stats:
                                result.table_stats[table] = {"count": 0}
                            result.table_stats[table]["count"] += info.get("count", 0)

                except EngineDisposedError:
                    raise
                except Exception as qe:
                    logger.warning(f"[HistoricalSync] Report | Failed to collect quality scores: {qe}")

        except asyncio.CancelledError:
            result.status = "cancelled"
            raise
        except EngineDisposedError:
            logger.warning("[HistoricalSync] Run | Engine disposed, stopping sync.")
            result.status = "failed"
            result.errors.append("Engine disposed during sync")
        except Exception as e:
            logger.error(
                f"[HistoricalSync] Run | ❌ Top-level failure: {e}",
                exc_info=True,
            )
            result.status = "failed"
            result.errors.append(str(e))

        if self._cancelled and result.status not in ("failed", "cancelled"):
            result.status = "cancelled"

        return result

    async def _run_historical_sync(
        self,
        days: typing.Any,
        progress_callback: typing.Callable | None,
        result: SyncResult,
    ):
        """
        Sync historical data for the last N trading days.
        `days` is interpreted as trading days (250 ≈ 1 year).
        We convert to calendar days using a ~1.46x multiplier (365/250)
        to ensure the date range covers enough trading days.
        """
        # Reset any loop-local cancellation state so direct calls remain reusable.
        self._shutdown_event.clear()

        end_date = await self._get_effective_trade_date()
        calendar_days = int(days * 365 / 250) + 30
        start_date = end_date - datetime.timedelta(days=calendar_days)

        try:
            trade_date_objs = await self.context.processor.trade_calendar.get_trade_dates(  # type: ignore[union-attr]
                start_date, end_date
            )
            trade_dates = list(reversed(trade_date_objs))
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                f"[HistoricalSync] Calendar | ⚠️ Trade calendar retrieval failed: {e}",
            )
            trade_dates = []

        if not trade_dates:
            result.status = "failed"
            result.errors.append("No trade dates found")
            return

        # Breakpoint Resume (Check Cache for all Critical tables)
        # All tables that are synced in sync_daily_market_snapshot
        # Enhanced: Quality Score Verification (Phase 2.4)
        try:
            sync_integrity_config = ConfigHandler.get_sync_integrity_config()
            QUALITY_THRESHOLD = sync_integrity_config.get("quality_threshold", 80)
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(f"[HistoricalSync] Config | ⚠️ Failed to load integrity config, using defaults: {e}")
            QUALITY_THRESHOLD = 80

        from data.external.tushare_client import TushareClient

        effective_synced_tables = TushareClient().get_effective_synced_tables(self.SYNCED_TABLES)
        effective_resume_tables = effective_synced_tables

        try:
            cached_dates_per_table = {}
            for table in effective_synced_tables:
                cached_dates_per_table[table] = await self.context.cache.get_cached_dates_for_table(table)

            existing = set()
            core_dates = [cached_dates_per_table.get(t, set()) for t in effective_resume_tables]
            if core_dates and all(core_dates):
                existing = set.intersection(*core_dates)

            def normalize_date(d):
                if isinstance(d, datetime.date):
                    return d.strftime("%Y%m%d")
                return str(d)

            def to_date_key(d):
                if isinstance(d, datetime.date):
                    return d
                if isinstance(d, str):
                    try:
                        return datetime.datetime.strptime(d, "%Y%m%d").date()
                    except ValueError:
                        return d
                return d

            existing_str = {normalize_date(d) for d in existing}
            trade_dates_str = {normalize_date(d) for d in trade_dates}
            existing_in_range = existing_str & trade_dates_str

            dates_to_verify = sorted([d for d in trade_dates if normalize_date(d) in existing_in_range])
            low_quality_dates = []

            if dates_to_verify:
                try:
                    quality_results = await self.context.cache.get_bulk_sync_quality_scores(
                        start_date=dates_to_verify[0],
                        end_date=dates_to_verify[-1],
                        tables=list(effective_resume_tables),
                    )

                    for date in dates_to_verify:
                        date_key = to_date_key(date)
                        quality = quality_results.get(date_key)
                        if quality is None:
                            low_quality_dates.append(date)
                            logger.debug(
                                f"[HistoricalSync] QualityCheck | {date} missing quality result, mark for re-sync"
                            )
                            continue

                        if quality.get("score", 0) < QUALITY_THRESHOLD:
                            low_quality_dates.append(date)
                            issues_str = ", ".join(quality.get("issues", [])[:3])
                            logger.debug(
                                f"[HistoricalSync] QualityCheck | {date} score={quality.get('score')} < {QUALITY_THRESHOLD}, "
                                f"expected_base={quality.get('expected_base')}, issues: [{issues_str}]"
                            )

                    if low_quality_dates:
                        logger.info(
                            f"[HistoricalSync] QualityCheck | Found {len(low_quality_dates)} low-quality dates, will re-sync"
                        )
                        for d in low_quality_dates:
                            existing.discard(d)
                            existing_str.discard(normalize_date(d))
                except EngineDisposedError:
                    raise
                except Exception as qe:
                    logger.warning(f"[HistoricalSync] QualityCheck | Failed: {qe}")

            original_count = len(trade_dates)
            trade_dates = [d for d in trade_dates if normalize_date(d) not in existing_str]
            skipped = original_count - len(trade_dates)
            result.updated += skipped

            if skipped > 0:
                logger.debug(
                    f"[HistoricalSync] Resume | Skipped {skipped} high-quality dates (threshold={QUALITY_THRESHOLD}).",
                )
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(f"[HistoricalSync] Resume | ⚠️ Cache check failed: {e}")

        total_days = len(trade_dates)
        concurrency = ConfigHandler.get_sync_max_concurrent_heavy()
        semaphore = asyncio.Semaphore(max(1, concurrency))

        failed_dates = []
        consecutive_failures = 0
        CB_THRESHOLD = min(50, max(3, int(total_days * 0.2) if total_days > 0 else 3))
        retry_round = 0
        abort_sync = False
        processed_count = 0
        BATCH_SIZE = 20

        async def sync_one_day(date: datetime.date):
            nonlocal abort_sync, processed_count, consecutive_failures
            if self._shutdown_event.is_set() or abort_sync:
                return

            async with semaphore:
                if self._shutdown_event.is_set() or abort_sync:
                    return

                if consecutive_failures > CB_THRESHOLD:
                    abort_sync = True
                    result.status = "failed"
                    result.errors.append(
                        f"Circuit breaker triggered: {consecutive_failures} consecutive failures",
                    )
                    logger.error(
                        f"[HistoricalSync] CircuitBreaker | ❌ Abort: {consecutive_failures} consecutive failures exceeded threshold {CB_THRESHOLD}",
                    )
                    return

                try:
                    await self.sync_daily_market_snapshot(date, force=True, sync_result=result)
                    processed_count += 1
                    result.added += 1
                    consecutive_failures = 0
                    if progress_callback:
                        progress_callback(
                            processed_count,
                            total_days,
                            I18n.get("progress_sync_market").format(date=date.strftime("%Y%m%d")),
                        )
                except EngineDisposedError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"[HistoricalSync] DaySync | ⚠️ Failed {date}: {e}",
                        exc_info=True,
                    )
                    consecutive_failures += 1
                    failed_dates.append(date)

        # Batch Processing
        for batch_start in range(0, len(trade_dates), BATCH_SIZE):
            if self._shutdown_event.is_set() or abort_sync or self._check_cancelled(result):
                break

            batch = trade_dates[batch_start : batch_start + BATCH_SIZE]
            tasks = [asyncio.create_task(sync_one_day(d)) for d in batch]

            with self._tasks_lock:
                self._active_tasks.update(tasks)

            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)

            batch_failures = sum(1 for d in batch if d in set(failed_dates))
            if batch_failures > 0:
                retry_round += 1
                backoff = min(30, 2**retry_round)
                logger.info(
                    f"[HistoricalSync] Backoff | {batch_failures} failures in batch, waiting {backoff}s (round {retry_round})",
                )
                await asyncio.sleep(backoff)
            else:
                retry_round = 0

            await asyncio.sleep(0)

        # Smart Retry
        if failed_dates and not self._shutdown_event.is_set() and not abort_sync and not self._cancelled:
            MAX_RETRIES = ConfigHandler.get_sync_retry_count()
            logger.debug(
                f"[HistoricalSync] Retry | Retrying {len(failed_dates)} failed dates...",
            )

            for _retry_round in range(MAX_RETRIES):
                if not failed_dates or self._shutdown_event.is_set():
                    break
                await asyncio.sleep(2)

                current_batch = failed_dates[:]
                failed_dates = []
                retry_sem = asyncio.Semaphore(2)

                async def retry_one(date: str, sem: asyncio.Semaphore, failed_list: list):
                    if self._shutdown_event.is_set():
                        return
                    async with sem:
                        try:
                            await self.sync_daily_market_snapshot(date, force=True, sync_result=result)  # type: ignore[arg-type]
                            logger.debug(
                                f"[HistoricalSync] Retry | ✅ Recovered {date}",
                            )
                            result.added += 1
                        except EngineDisposedError:
                            raise
                        except Exception as retry_e:
                            logger.warning(
                                f"[HistoricalSync] Retry | ⚠️ Failed {date}: {retry_e}",
                            )
                            failed_list.append(date)

                # Batch Retry
                for r_start in range(0, len(current_batch), BATCH_SIZE):
                    if self._shutdown_event.is_set():
                        break
                    r_batch = current_batch[r_start : r_start + BATCH_SIZE]
                    r_tasks = [asyncio.create_task(retry_one(d, retry_sem, failed_dates)) for d in r_batch]

                    with self._tasks_lock:
                        self._active_tasks.update(r_tasks)
                    try:
                        await asyncio.gather(*r_tasks, return_exceptions=True)
                    finally:
                        with self._tasks_lock:
                            self._active_tasks.difference_update(r_tasks)

                    # Cooperative yield: same as main batch path
                    await asyncio.sleep(0)

        if failed_dates:
            result.errors.append(f"{len(failed_dates)} dates failed after retries")
            result.status = "partial"

        if result.status == "failed":
            pass  # Already logged by CircuitBreaker ERROR above
        elif result.status == "partial":
            logger.warning(
                f"[HistoricalSync] Run | ⚠️ Partial. Added={result.added}, FailedDates={len(failed_dates)}",
            )
        else:
            logger.info(
                f"[HistoricalSync] Run | ✅ Complete. Added={result.added}, FailedDates={len(failed_dates)}",
            )

    async def sync_daily_market_snapshot(
        self,
        trade_date: datetime.date | None,
        force: bool = False,
        sync_result: SyncResult | None = None,
    ):
        """
        Sync ALL data types for a single day.
        """
        # Check cache (Test compatibility & Efficiency)
        if not force:
            # Check ALL synced tables exist before skipping
            # This ensures data integrity - only skip if all tables have data
            if await self.context.cache.check_data_exists(trade_date):
                logger.debug(
                    f"[HistoricalSync] DaySync | Cache hit for {trade_date}, skipping.",
                )
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
            ("index_basic", self.context.api.get_index_dailybasic, "Index Indicators"),
        ]

        async def fetch_wrapper(key: typing.Any, func: typing.Callable, name: typing.Any):
            try:
                return (key, await func(trade_date=trade_date), None)
            except EngineDisposedError:
                raise
            except TushareAPIPermissionError as e:
                logger.warning(
                    f"[HistoricalSync] DaySync | ⚠️ PERMISSION_DENIED for {name}: {e}",
                )
                return (key, None, "permission_denied")
            except Exception as e:
                logger.warning(
                    f"[HistoricalSync] DaySync | ⚠️ Fetch {name} failed for {trade_date}: {e}",
                )
                return (key, None, e)

        async def fetch_indices():
            try:
                tasks = [self.context.api.get_index_daily(ts_code=c, trade_date=trade_date) for c in MAJOR_INDICES]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                valid = [r for r in results if isinstance(r, pd.DataFrame) and not r.empty]
                if valid:
                    return ("index", pd.concat(valid, ignore_index=True), None)
                return ("index", None, None)
            except EngineDisposedError:
                raise
            except Exception as e:
                return ("index", None, e)

        # Launch
        futures = [fetch_wrapper(*c) for c in task_configs]
        futures.append(fetch_indices())

        results_list = await asyncio.gather(*futures)
        # Parse results: Map key -> (data, error_type)
        # error_type can be: None, "permission_denied", or Exception
        data_map = {k: v for k, v, e in results_list}
        permission_denied_map = {k: e for k, v, e in results_list if e == "permission_denied"}
        error_map = {k: e for k, v, e in results_list if isinstance(e, Exception)}

        if "quotes" in error_map:
            raise error_map["quotes"]
        if "basic" in error_map:
            raise error_map["basic"]

        # Save Logic
        cache = self.context.cache

        async def save_if_ok(key: typing.Any, method: typing.Any, critical: typing.Any = False):
            """
            Save data for a given key if available.
            Returns a dict with:
              - 'saved': number of rows saved (0 if empty, None if failed)
              - 'fetched': number of rows fetched from API
              - 'success': True if save succeeded (including empty data), False if failed
              - 'result_status': SYNC_RESULT_* constant indicating the outcome
            """
            df = data_map.get(key)

            # Check for permission denied first
            if key in permission_denied_map:
                return {
                    "saved": None,
                    "fetched": 0,
                    "success": False,
                    "result_status": SYNC_RESULT_SKIPPED_PERMISSION,
                    "error_type": "permission_denied",
                }

            if df is None:
                if key in error_map:
                    logger.warning(f"[HistoricalSync] DaySync | ⚠️ Fetch failed for {key}, skipping sync_status update")
                return {"saved": None, "fetched": 0, "success": False, "result_status": SYNC_RESULT_FETCH_FAILED}

            fetched_count = len(df)

            if df is not None and not df.empty:
                target_func = method
                while hasattr(target_func, "__wrapped__"):
                    target_func = target_func.__wrapped__

                try:
                    sig = inspect.signature(target_func)

                    if "suppress_errors" in sig.parameters:
                        row_count = await method(df, suppress_errors=not critical)
                    else:
                        row_count = await method(df)
                    saved = row_count if row_count is not None else len(df)
                    return {
                        "saved": saved,
                        "fetched": fetched_count,
                        "success": True,
                        "result_status": SYNC_RESULT_HAS_DATA,
                    }
                except EngineDisposedError:
                    raise
                except Exception as e:
                    if critical:
                        logger.error(
                            f"[HistoricalSync] DaySync | ❌ Critical save failed for {key}: {e}",
                            exc_info=True,
                        )
                        raise e
                    logger.warning(
                        f"[HistoricalSync] DaySync | ⚠️ Non-critical save {key} failed: {e}, skipping sync_status update",
                    )
                    return {
                        "saved": None,
                        "fetched": fetched_count,
                        "success": False,
                        "result_status": SYNC_RESULT_SAVE_FAILED,
                    }

            if df is not None and df.empty:
                logger.debug(f"[HistoricalSync] DaySync | {key} returned empty data, will update sync_status with 0")
            return {"saved": 0, "fetched": fetched_count, "success": True, "result_status": SYNC_RESULT_EMPTY}

        # 1. Quotes (Critical)
        quotes_rows = await save_if_ok("quotes", cache.save_daily_quotes, critical=True)

        # Yield control between heavy table saves
        await asyncio.sleep(0)

        # 2. Check critical columns for data integrity
        df_quotes = data_map.get("quotes")
        if df_quotes is not None and not df_quotes.empty:
            required_quote_cols = ["ts_code", "trade_date", "close", "pct_chg", "vol"]
            missing_cols = [c for c in required_quote_cols if c not in df_quotes.columns]
            if missing_cols:
                logger.warning(
                    f"[HistoricalSync] DaySync | ⚠️ Critical columns missing in quotes for {trade_date}: {missing_cols}"
                )
            if "adj_factor" not in df_quotes.columns:
                logger.warning(
                    f"[HistoricalSync] DaySync | ⚠️ adj_factor column missing in quotes for {trade_date}. "
                    "This may affect price adjustment calculations.",
                )
                if sync_result is not None:
                    sync_result.warnings.append(f"adj_factor missing for {trade_date}")

        df_basic = data_map.get("basic")
        if df_basic is not None and not df_basic.empty:
            required_basic_cols = ["ts_code", "trade_date", "pe", "pb"]
            missing_basic_cols = [c for c in required_basic_cols if c not in df_basic.columns]
            if missing_basic_cols:
                logger.warning(
                    f"[HistoricalSync] DaySync | ⚠️ Critical columns missing in basic for {trade_date}: {missing_basic_cols}"
                )

        # Yield control
        await asyncio.sleep(0)

        # 3. Basic / Daily Indicators (Critical)
        basic_rows = await save_if_ok(
            "basic",
            cache.save_daily_indicators,
            critical=True,
        )

        # Yield control
        await asyncio.sleep(0)

        # 4. Others (Non-critical, can fail silently or log)
        limit_result = await save_if_ok("limit", cache.save_limit_list)
        suspend_result = await save_if_ok("suspend", cache.save_suspend_d)
        await asyncio.sleep(0)
        margin_result = await save_if_ok("margin", cache.save_margin_daily)
        lhb_result = await save_if_ok("lhb", cache.save_top_list)
        await asyncio.sleep(0)
        block_result = await save_if_ok("block", cache.save_block_trade)
        mf_result = await save_if_ok("mf", cache.save_moneyflow)
        await asyncio.sleep(0)
        hsgt_result = await save_if_ok("hsgt_flow", cache.save_moneyflow_hsgt)
        index_result = await save_if_ok("index", cache.save_index_daily)
        index_basic_result = await save_if_ok("index_basic", cache.save_index_dailybasic)

        # Yield before northbound special processing
        await asyncio.sleep(0)

        north_result = {"saved": None, "fetched": 0, "success": False}
        try:
            df_north = data_map.get("north")
            if df_north is not None and not df_north.empty:
                original_count = len(df_north)
                df_north = df_north[df_north["ts_code"].astype(str).str.endswith((".SH", ".SZ"))]
                filtered_count = original_count - len(df_north)
                if filtered_count > 0:
                    if len(df_north) == 0:
                        sample_codes = data_map.get("north")["ts_code"].head(5).tolist()  # type: ignore[optional-subscript]
                        logger.debug(
                            f"[HistoricalSync] DaySync | {filtered_count} records filtered (southbound HK stocks, not northbound A-shares). "
                            f"Sample ts_codes: {sample_codes}"
                        )
                    else:
                        logger.debug(
                            f"[HistoricalSync] DaySync | Filtered {filtered_count} non-A-share records from northbound data"
                        )
                if not df_north.empty:
                    north_rows = await cache.save_northbound(df_north)
                    north_result = {
                        "saved": north_rows if north_rows is not None else len(df_north),
                        "fetched": len(df_north),
                        "success": True,
                    }
                else:
                    north_result = {
                        "saved": 0,
                        "fetched": 0,
                        "success": True,
                    }
            elif df_north is None and "north" in error_map:
                north_result = {"saved": None, "fetched": 0, "success": False}
            else:
                north_result = {"saved": 0, "fetched": 0, "success": True}
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                f"[HistoricalSync] DaySync | ⚠️ Northbound save failed (non-critical): {e}",
            )
            north_result = {"saved": None, "fetched": 0, "success": False}

        async def safe_update_status(table_name: str, result: typing.Any, trade_date: str | datetime.date | None):
            saved = result.get("saved") if isinstance(result, dict) else result
            result_status = result.get("result_status") if isinstance(result, dict) else None
            error_type = result.get("error_type") if isinstance(result, dict) else None

            # Handle permission denied - mark as skipped_permission, not as failure
            if error_type == "permission_denied" or result_status == SYNC_RESULT_SKIPPED_PERMISSION:
                await cache.update_sync_status(
                    table_name,
                    trade_date,
                    0,
                    status="skipped_permission",
                    last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
                )
                logger.debug(f"[HistoricalSync] Recorded skipped_permission for {table_name}")
                return

            if saved is not None:
                effective_status = "success"
                if table_name in CRITICAL_EMPTY_TABLES and result_status == SYNC_RESULT_EMPTY:
                    effective_status = "empty"
                    logger.warning(
                        f"[HistoricalSync] DaySync | ⚠️ Critical table {table_name} returned EMPTY for {trade_date}, "
                        f"marking sync_status as 'empty' instead of 'success'"
                    )
                await cache.update_sync_status(
                    table_name,
                    trade_date,
                    saved or 0,
                    status=effective_status,
                    last_result_status=result_status,
                )
            else:
                is_save_failed = result_status == SYNC_RESULT_SAVE_FAILED
                await cache.update_sync_status(
                    table_name,
                    trade_date or get_now().date(),
                    0,
                    status="save_failed" if is_save_failed else "fetch_failed",
                    last_result_status=result_status
                    or (SYNC_RESULT_SAVE_FAILED if is_save_failed else SYNC_RESULT_FETCH_FAILED),
                )
                logger.debug(
                    f"[HistoricalSync] Recorded {'save_failed' if is_save_failed else 'fetch_failed'} for {table_name}"
                )

        def verify_data_integrity(key: str, result_data: typing.Any):
            if not isinstance(result_data, dict):
                return
            saved = result_data.get("saved")
            fetched = result_data.get("fetched")
            if saved is not None and fetched is not None and fetched > 0 and saved != fetched:
                warning_msg = (
                    f"[HistoricalSync] DaySync | ⚠️ Data integrity issue for {key} on {trade_date}: "
                    f"fetched={fetched} rows but saved={saved} rows"
                )
                logger.warning(warning_msg)
                if sync_result is not None:
                    sync_result.warnings.append(warning_msg)

        await safe_update_status("daily_quotes", quotes_rows, trade_date)
        verify_data_integrity("quotes", quotes_rows)
        await safe_update_status("daily_indicators", basic_rows, trade_date)
        verify_data_integrity("basic", basic_rows)
        await safe_update_status("moneyflow_daily", mf_result, trade_date)
        verify_data_integrity("mf", mf_result)
        await safe_update_status("northbound_holding", north_result, trade_date)
        verify_data_integrity("north", north_result)
        await safe_update_status("moneyflow_hsgt", hsgt_result, trade_date)
        verify_data_integrity("hsgt_flow", hsgt_result)
        await safe_update_status("margin_daily", margin_result, trade_date)
        verify_data_integrity("margin", margin_result)
        await safe_update_status("suspend_d", suspend_result, trade_date)
        verify_data_integrity("suspend", suspend_result)
        await safe_update_status("limit_list", limit_result, trade_date)
        verify_data_integrity("limit", limit_result)
        await safe_update_status("top_list", lhb_result, trade_date)
        verify_data_integrity("lhb", lhb_result)
        await safe_update_status("block_trade", block_result, trade_date)
        verify_data_integrity("block", block_result)
        await safe_update_status("index_daily", index_result, trade_date)
        verify_data_integrity("index", index_result)
        await safe_update_status("index_dailybasic", index_basic_result, trade_date)
        verify_data_integrity("index_basic", index_basic_result)

        logger.debug(
            f"[HistoricalSync] Sync status update for {trade_date}: "
            f"quotes={quotes_rows}, basic={basic_rows}, mf={mf_result}, "
            f"limit={limit_result}, lhb={lhb_result}, block={block_result}, "
            f"index={index_result}, index_basic={index_basic_result}"
        )
        return True

    async def sync_moneyflow(self, trade_date: datetime.date | None = None):
        """Sync money flow for a specific date (Standalone)."""
        if trade_date is None:
            trade_date = await self._get_effective_trade_date()

        try:
            df = await self.context.api.get_moneyflow(trade_date=trade_date)
            if df is not None and not df.empty:
                count = await self.context.cache.save_moneyflow(df)
                if count is not None and count > 0:
                    await self.context.cache.update_sync_status(
                        "moneyflow_daily",
                        trade_date,
                        count,
                    )
                return count
        except (ConnectionError, TimeoutError) as e:
            logger.error(
                f"[HistoricalSync] MoneyFlow | ❌ Network error: {e}",
            )
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                f"[HistoricalSync] MoneyFlow | ⚠️ Standalone sync failed: {e}",
            )
        return 0

    async def sync_northbound(self, trade_date: datetime.date | None = None):
        """Sync northbound holding for a specific date (Standalone)."""
        if trade_date is None:
            trade_date = await self._get_effective_trade_date()

        try:
            df = await self.context.api.get_hk_hold(trade_date=trade_date)
            if df is not None and not df.empty:
                df = df[df["ts_code"].astype(str).str.endswith((".SH", ".SZ"))]
                if not df.empty:
                    count = await self.context.cache.save_northbound(df)
                    if count is not None and count > 0:
                        await self.context.cache.update_sync_status(
                            "northbound_holding",
                            trade_date,
                            count,
                        )
                    return count
        except (ConnectionError, TimeoutError) as e:
            logger.error(
                f"[HistoricalSync] Northbound | ❌ Network error: {e}",
            )
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                f"[HistoricalSync] Northbound | ⚠️ Standalone sync failed: {e}",
            )
        return 0
