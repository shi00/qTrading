"""
Financial Sync Strategy.
Handles comprehensive fundamentals, incremental updates, and data repair.
"""

import asyncio
import datetime
import logging
import threading

import pandas as pd

from data.constants import FINANCIAL_BATCH_TABLES, FINANCIAL_REPORT_SCHEMA_COLS, SYNC_RESULT_SKIPPED_PERMISSION
from data.sync.base import ISyncStrategy, SyncResult, _get_seasonal_adjustments, _is_peak_disclosure_season, safe_error
from data.persistence.daos.base_dao import EngineDisposedError
from data.external.tushare_client import TushareAPIPermissionError
from core.i18n import I18n
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.error_classifier import classify_error, classify_severity
from utils.loop_local import get_loop_local
from utils.config_handler import ConfigHandler
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date

logger = logging.getLogger(__name__)


def _dedup_financial_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicate financial DataFrame by end_date, preferring the latest disclosure.

    For DataFrames with 'ann_date' column, sorts by [end_date, ann_date, update_flag]
    ascending and keeps the last row per end_date. This ensures we select the most
    recently disclosed report for each financial period (handles revised reports).

    update_flag: "1" means revised data, should be preferred over original ("0" or None).

    For DataFrames without 'ann_date', falls back to simple end_date dedup.
    """
    if df is None or df.empty:
        return df

    if "ann_date" in df.columns:
        sort_cols = ["end_date", "ann_date"]
        ascending = [True, True]
        if "update_flag" in df.columns:
            sort_cols.append("update_flag")
            ascending.append(True)
        return df.sort_values(by=sort_cols, ascending=ascending).drop_duplicates(subset=["end_date"], keep="last")
    return df.sort_values("end_date").drop_duplicates(subset=["end_date"], keep="last")


class FinancialSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing financial reports and fundamental data.
    """

    def __init__(self, context):
        super().__init__(context)
        self._lazy_event = None  # ST-01: Lazy init
        self._tasks_lock = threading.Lock()
        self._active_tasks = set()

    def _get_request_delay(self, is_heavy: bool) -> float:
        """Read request delay from injected provider or fall back to ConfigHandler."""
        provider = self.context.request_delay_provider
        if provider is not None:
            return provider(is_heavy)
        return ConfigHandler.get_sync_request_delay(is_heavy=is_heavy)

    @property
    def _shutdown_event(self):
        """Get or create shutdown event dynamically per event loop."""

        def _factory():
            return asyncio.Event()

        return get_loop_local("fina_shutdown_evt", _factory)

    def cancel(self):
        """Signal cancellation."""
        super().cancel()
        try:
            self._shutdown_event.set()
        except RuntimeError:
            logger.debug("[FinancialSync] Shutdown event unavailable (no event loop).")
        logger.debug("[FinancialSync] Stop | Cancellation signal received.")
        with self._tasks_lock:
            for task in self._active_tasks:
                if not task.done():
                    task.cancel()

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_effective_trade_date(self) -> datetime.date:
        """Prefer the latest closed trade date for default sync windows."""
        try:
            trade_date = await self.context.processor.trade_calendar.get_latest_trade_date()  # type: ignore[union-attr]
            if trade_date is None:
                logger.warning("[FinancialSync] get_latest_trade_date returned None, falling back to today.")
            elif isinstance(trade_date, datetime.datetime):
                return trade_date.date()
            elif isinstance(trade_date, datetime.date):
                return trade_date
            elif trade_date:
                return parse_date(str(trade_date))
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(
                    "[FinancialSync] Effective trade date | SYSTEM-LEVEL failure: %s", safe_error(e), exc_info=True
                )
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[FinancialSync] Effective trade date fallback (%s): %s",
                    error_info["code"],
                    safe_error(e),
                    exc_info=True,
                )
            else:
                logger.error(
                    "[FinancialSync] Effective trade date fallback (%s): %s",
                    error_info["code"],
                    safe_error(e),
                    exc_info=True,
                )
        return get_now().date()

    @log_async_operation(
        operation_name="FinancialSyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def _run_impl(
        self,
        periods: list[str] | None = None,
        force: bool = False,
        progress_callback=None,
        **kwargs,
    ) -> SyncResult:
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
                logger.debug("[FinancialSync] Strategy | Full Sync (manual periods)")
            elif force:
                should_full_sync = True
                logger.debug("[FinancialSync] Strategy | Full Sync (force=True)")
            else:
                status = await self.context.cache.get_sync_status("financial_reports")
                if not status or not status.get("last_sync_date"):
                    should_full_sync = True
                    logger.debug("[FinancialSync] Strategy | Full Sync (first run)")

            if should_full_sync:
                await self._run_full_sync(
                    periods,
                    progress_callback,
                    force=force,
                    result_accumulator=result,
                )
            else:
                await self._run_incremental_sync(
                    progress_callback,
                    result_accumulator=result,
                )

        except asyncio.CancelledError:
            result.status = "cancelled"
            raise
        except EngineDisposedError:
            logger.warning("[FinancialSync] Run | Engine disposed, stopping sync.")
            result.status = "failed"
            result.errors.append("Engine disposed during sync")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[FinancialSync] SYSTEM-LEVEL failure: %s", safe_error(e), exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[FinancialSync] Recoverable error (%s): %s", error_info["code"], safe_error(e), exc_info=True
                )
            else:
                logger.error("[FinancialSync] Operational error: %s", safe_error(e), exc_info=True)
            result.status = "failed"
            result.errors.append(error_info["message_key"])

        return result

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _run_full_sync(
        self,
        periods,
        progress_callback,
        force,
        result_accumulator: SyncResult,
    ):
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

        logger.debug(
            "[FinancialSync] FullSync | Starting comprehensive fundamentals (force=%s)...",
            force,
        )

        # Local accumulators for aux tables (NOT self variables to avoid AttributeError)
        total_mainbz_rows = 0
        total_audit_rows = 0
        _counter_lock = get_loop_local("financial_counter_lock", asyncio.Lock)

        # Force Logic: Reset sync status for resume
        if force:
            logger.debug(
                "[FinancialSync] FullSync | Force mode: Clearing previous sync status...",
            )
            await self.context.cache.clear_step4_sync_status()

        # 1. Get Stock List
        df_basic = await self.context.cache.get_stock_basic()
        if df_basic.empty:
            logger.error(
                "[FinancialSync] FullSync | ❌ No stocks found. Run Stock Basic sync first.",
            )
            result_accumulator.errors.append("No stocks found in cache")
            result_accumulator.status = "failed"
            return

        df_active = df_basic[df_basic["list_status"] == "L"]
        all_stocks = set(df_active["ts_code"].tolist())
        total_stocks = len(all_stocks)

        # 2. Concurrency Control
        concurrency_factor, _ = _get_seasonal_adjustments()
        concurrency = max(1, ConfigHandler.get_sync_max_concurrent_heavy() // concurrency_factor)
        semaphore = asyncio.Semaphore(concurrency)

        # 3. Data-as-State Resume Logic
        synced_stocks = await self.context.cache.get_completed_step4_stocks(
            sync_version=1,
        )

        # === [断点续传逻辑增强验证] ===
        # 找出虽然标记为完成，但实际期数不够的残缺股票
        MIN_PERIODS = ConfigHandler.get_sync_integrity_config().get("financial_min_periods", 4)
        incomplete_stocks = await self.context.cache.get_incomplete_financial_stocks(MIN_PERIODS)

        if incomplete_stocks:
            logger.info(
                "[FinancialSync] IntegrityCheck | Found %s "
                "conceptually complete but actually incomplete stocks. Forcing re-sync.",
                len(incomplete_stocks),
            )
            synced_stocks = set(synced_stocks) - incomplete_stocks
        # ===================================

        pending_stocks = sorted([s for s in all_stocks if s not in synced_stocks])
        skipped_count = total_stocks - len(pending_stocks)

        # Update result stats
        result_accumulator.updated += skipped_count  # Technically skipped, but "done"

        if skipped_count > 0:
            logger.debug(
                "[FinancialSync] FullSync | Resume: %s done, %s pending",
                skipped_count,
                len(pending_stocks),
            )

        if not pending_stocks:
            logger.debug("[FinancialSync] FullSync | All stocks already synced.")
            if progress_callback:
                progress_callback(
                    total_stocks,
                    total_stocks,
                    I18n.get("progress_sync_fundamentals").format(
                        current=total_stocks,
                        total=total_stocks,
                        stock=I18n.get("status_ready"),
                    ),
                )
            return

        completed_count = skipped_count

        end_date = await self._get_effective_trade_date()
        years = ConfigHandler.get_init_history_years()
        rough_start_date = end_date - datetime.timedelta(days=int(250 * years * 2.0))
        all_dates = await self.context.processor.trade_calendar.get_trade_dates(  # type: ignore[union-attr]
            start_date=rough_start_date,
            end_date=end_date,
        )
        start_date = (
            all_dates[-(250 * years)]
            if len(all_dates) >= (250 * years)
            else (all_dates[0] if all_dates else (end_date - datetime.timedelta(days=365 * years)))
        )

        all_dates = []
        curr_dt = parse_date(start_date)
        end_dt_obj = parse_date(end_date)
        while curr_dt <= end_dt_obj:
            all_dates.append(curr_dt.strftime("%Y%m%d"))
            curr_dt += datetime.timedelta(days=1)

        # Inner processing function
        async def process_one_stock(ts_code):
            nonlocal completed_count, total_mainbz_rows, total_audit_rows
            if self._shutdown_event.is_set():
                return

            processed = False
            try:
                async with semaphore:
                    if self._shutdown_event.is_set():
                        return

                    processed = True
                    has_error = False

                    try:
                        df_merged, aux_counts = await self._fetch_comprehensive_financial_data(
                            ts_code,
                            start_date=start_date,
                            end_date=end_date,
                        )

                        async with _counter_lock:
                            total_mainbz_rows += aux_counts["mainbz"]
                            total_audit_rows += aux_counts["audit"]

                        if df_merged is not None and not df_merged.empty:
                            for col in FINANCIAL_REPORT_SCHEMA_COLS:
                                if col not in df_merged.columns:
                                    df_merged[col] = None

                    except (AttributeError, NameError, TypeError, ImportError):
                        raise
                    except EngineDisposedError:
                        raise
                    except Exception as e:
                        has_error = True
                        error_info = classify_error(e, context="general")
                        severity = classify_severity(e, context="general")
                        if severity == "system":
                            logger.critical(
                                "[FinancialSync] StockSync | SYSTEM-LEVEL failure for %s: %s",
                                ts_code,
                                safe_error(e),
                                exc_info=True,
                            )
                            raise
                        elif severity == "recoverable":
                            logger.warning(
                                "[FinancialSync] StockSync | Failed for %s (%s): %s",
                                ts_code,
                                error_info["code"],
                                safe_error(e),
                                exc_info=True,
                            )
                        else:
                            logger.error(
                                "[FinancialSync] StockSync | Failed for %s (%s): %s",
                                ts_code,
                                error_info["code"],
                                safe_error(e),
                                exc_info=True,
                            )

                    if not has_error:
                        has_actual_data = df_merged is not None and not df_merged.empty
                        if has_actual_data:
                            async with self.context.cache.financial_transaction() as tx_conn:
                                await self.context.cache.save_financial_reports(
                                    df_merged[FINANCIAL_REPORT_SCHEMA_COLS],  # type: ignore[optional-subscript]
                                    conn=tx_conn,
                                )
                                await self.context.cache.mark_stock_step4_completed(
                                    ts_code,
                                    sync_version=1,
                                    conn=tx_conn,
                                )
                            result_accumulator.added += 1
                        else:
                            logger.info(
                                "[FinancialSync] StockSync | %s returned empty data (suspended/delisted/no report yet). "
                                "NOT marking complete to allow future retry.",
                                ts_code,
                            )
                    else:
                        logger.debug("[FinancialSync] StockSync | %s incomplete, pending retry.", ts_code)

            except EngineDisposedError:
                raise
            except Exception as e:
                error_info = classify_error(e, context="general")
                severity = classify_severity(e, context="general")
                if severity == "system":
                    logger.critical(
                        "[FinancialSync] StockSync | SYSTEM-LEVEL failure for %s: %s",
                        ts_code,
                        safe_error(e),
                        exc_info=True,
                    )
                    raise
                elif severity == "recoverable":
                    logger.warning(
                        "[FinancialSync] StockSync | Failed for %s (%s): %s",
                        ts_code,
                        error_info["code"],
                        safe_error(e),
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "[FinancialSync] StockSync | Failed for %s (%s): %s",
                        ts_code,
                        error_info["code"],
                        safe_error(e),
                        exc_info=True,
                    )

            # Per-stock progress update (advance progress bar for processed stocks)
            if processed:
                async with _counter_lock:
                    completed_count += 1
                if progress_callback and completed_count % 5 == 0:
                    # Stock phase occupies 0→80% of Step 4 progress
                    pct = int(completed_count / total_stocks * 80)
                    progress_callback(
                        pct,
                        100,
                        I18n.get("progress_sync_fundamentals").format(
                            current=completed_count,
                            total=total_stocks,
                            stock=ts_code,
                        ),
                    )

        # Batch execution
        batch_size = ConfigHandler.get_sync_full_batch_size()
        for i in range(0, len(pending_stocks), batch_size):
            if self._shutdown_event.is_set():
                break

            batch = pending_stocks[i : i + batch_size]
            tasks = [asyncio.create_task(process_one_stock(code)) for code in batch]

            with self._tasks_lock:
                self._active_tasks.update(tasks)

            try:
                await gather_return_exceptions_propagating_cancel(*tasks)
            finally:
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)

        # Batch phase: corporate actions sync (serial, AFTER stock loop to avoid API contention)
        logger.debug(
            "[FinancialSync] BatchSync | Starting corporate actions batch phase...",
        )

        # Update sync status for aux tables (after all stocks processed)
        today = get_now().date()
        if total_mainbz_rows > 0:
            await self.context.cache.update_sync_status(
                "fina_mainbz",
                today,
                total_mainbz_rows,
            )
            logger.debug("[FinancialSync] fina_mainbz total: %s", total_mainbz_rows)

        if total_audit_rows > 0:
            await self.context.cache.update_sync_status(
                "fina_audit",
                today,
                total_audit_rows,
            )
            logger.debug("[FinancialSync] fina_audit total: %s", total_audit_rows)

        def batch_progress(current, total, msg):
            if progress_callback:
                # Batch phase occupies 80→100% of Step 4 progress
                pct = 80 + int(current / max(total, 1) * 20)
                progress_callback(pct, 100, msg)

        await self._sync_corporate_actions_by_date(all_dates, batch_progress)

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _run_incremental_sync(
        self,
        progress_callback,
        result_accumulator: SyncResult,
    ):
        """
        Incremental Sync: Query disclosure date.
        """
        if self._shutdown_event.is_set():
            return

        # Local accumulators for aux tables (NOT self variables to avoid AttributeError)
        total_mainbz_rows = 0
        total_audit_rows = 0

        status = await self.context.cache.get_sync_status("financial_reports")
        last_sync_str = status.get("last_sync_date")

        try:
            if isinstance(last_sync_str, datetime.datetime):
                last_sync_dt = last_sync_str
            else:
                last_sync_dt = datetime.datetime.strptime(
                    last_sync_str,
                    "%Y-%m-%d %H:%M:%S",
                )
            start_date_dt = last_sync_dt + datetime.timedelta(days=1)
        except EngineDisposedError:
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(
                    "[FinancialSync] Date parse | SYSTEM-LEVEL failure: %s",
                    safe_error(e),
                    exc_info=True,
                )
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[FinancialSync] Date parse | Failed to parse last_sync_date, using 30-day fallback (%s): %s",
                    error_info["code"],
                    safe_error(e),
                    exc_info=True,
                )
            else:
                logger.error(
                    "[FinancialSync] Date parse | Failed to parse last_sync_date, using 30-day fallback (%s): %s",
                    error_info["code"],
                    safe_error(e),
                    exc_info=True,
                )
            start_date_dt = get_now() - datetime.timedelta(days=30)

        today_dt = get_now()
        dates_to_sync = []
        curr = start_date_dt
        while curr.date() <= today_dt.date():
            dates_to_sync.append(curr.strftime("%Y%m%d"))
            curr += datetime.timedelta(days=1)

        if not dates_to_sync:
            logger.debug("[FinancialSync] Incremental | Data already up-to-date")
            return

        total_saved = 0
        total_mainbz_rows = 0
        total_audit_rows = 0

        concurrency_factor, delay_multiplier = _get_seasonal_adjustments()
        base_concurrency = ConfigHandler.get_sync_max_concurrent_heavy()
        adjusted_concurrency = max(1, base_concurrency // concurrency_factor)
        base_delay = self._get_request_delay(is_heavy=False)
        adjusted_delay = base_delay * delay_multiplier

        if _is_peak_disclosure_season():
            logger.info(
                "[FinancialSync] Peak disclosure season detected. Adjusting concurrency: %s → %s, delay: %ss → %ss",
                base_concurrency,
                adjusted_concurrency,
                base_delay,
                adjusted_delay,
            )

        semaphore = asyncio.Semaphore(adjusted_concurrency)

        for day_str in dates_to_sync:
            df_disclosure = await self.context.api.get_disclosure_date(date=day_str)

            if df_disclosure is None or df_disclosure.empty:
                continue

            target_list = df_disclosure[["ts_code", "end_date"]].drop_duplicates().to_dict("records")
            if not target_list:
                continue

            async def sync_one_target(item):
                ts_code = item["ts_code"]
                period = item["end_date"]
                result = {"saved": 0, "mainbz": 0, "audit": 0}

                async with semaphore:
                    try:
                        await asyncio.sleep(adjusted_delay)
                        df, aux_counts = await self._fetch_comprehensive_financial_data(
                            ts_code,
                            period=period,
                        )

                        result["mainbz"] = aux_counts["mainbz"]
                        result["audit"] = aux_counts["audit"]

                        if df is not None and not df.empty:
                            for col in FINANCIAL_REPORT_SCHEMA_COLS:
                                if col not in df.columns:
                                    df[col] = None

                            count = await self.context.cache.save_financial_reports(
                                df[FINANCIAL_REPORT_SCHEMA_COLS],
                            )
                            if count > 0:
                                result["saved"] = count
                    except EngineDisposedError:
                        raise
                    except Exception as e:
                        error_info = classify_error(e, context="general")
                        severity = classify_severity(e, context="general")
                        if severity == "system":
                            logger.critical(
                                "[FinancialSync] Incremental | SYSTEM-LEVEL failure for %s period=%s: %s",
                                ts_code,
                                period,
                                safe_error(e),
                                exc_info=True,
                            )
                            raise
                        elif severity == "recoverable":
                            logger.warning(
                                "[FinancialSync] Incremental | Failed %s period=%s (%s): %s",
                                ts_code,
                                period,
                                error_info["code"],
                                safe_error(e),
                                exc_info=True,
                            )
                        else:
                            logger.error(
                                "[FinancialSync] Incremental | Failed %s period=%s (%s): %s",
                                ts_code,
                                period,
                                error_info["code"],
                                safe_error(e),
                                exc_info=True,
                            )

                return result

            day_saved = 0
            # Incremental 半批：使用 sync_batch_size // 2 降低 429 风险（高峰期披露密集，单日 target 量大）。
            _BATCH_SIZE = max(5, ConfigHandler.get_sync_batch_size() // 2)
            for batch_start in range(0, len(target_list), _BATCH_SIZE):
                batch_targets = target_list[batch_start : batch_start + _BATCH_SIZE]
                batch = [sync_one_target(item) for item in batch_targets]
                batch_results = await gather_return_exceptions_propagating_cancel(*batch)
                for r in batch_results:
                    if isinstance(r, Exception):
                        logger.warning("[FinancialSync] Batch task failed: %s", r)
                        continue
                    day_saved += r["saved"]  # type: ignore[index]
                    total_saved += r["saved"]  # type: ignore[index]
                    total_mainbz_rows += r["mainbz"]  # type: ignore[index]
                    total_audit_rows += r["audit"]  # type: ignore[index]

            day_date = datetime.datetime.strptime(day_str, "%Y%m%d").date()
            await self.context.cache.update_sync_status(
                "financial_reports",
                day_date,
                day_saved,
            )

            if progress_callback:
                progress_callback(0, 0, f"{I18n.get('progress_sync_done')} {day_str}")

        # --- Step 2: Batch Sync Corporate Actions (O(Time) Optimization) ---
        # Sync Forecasts, Dividends, etc. by date
        await self._sync_corporate_actions_by_date(dates_to_sync)

        # Update sync status for aux tables (after all stocks processed)
        today = get_now().date()
        if total_mainbz_rows > 0:
            await self.context.cache.update_sync_status(
                "fina_mainbz",
                today,
                total_mainbz_rows,
            )
            logger.debug("[FinancialSync] fina_mainbz total: %s", total_mainbz_rows)

        if total_audit_rows > 0:
            await self.context.cache.update_sync_status(
                "fina_audit",
                today,
                total_audit_rows,
            )
            logger.debug("[FinancialSync] fina_audit total: %s", total_audit_rows)

        result_accumulator.added = total_saved

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _sync_corporate_actions_by_date(
        self,
        dates: list[str],
        progress_callback=None,
    ):
        """
        Batch sync corporate actions (Forecast, Dividend, etc.) by date.
        Uses O(Time) strategy instead of O(Stock).
        Iterates per-date to avoid creating thousands of tasks at once.
        """
        if not dates:
            return

        total = len(dates)
        logger.debug(
            "[FinancialSync] BatchSync | Syncing corporate actions across %s days...",
            total,
        )

        concurrency_factor, _ = _get_seasonal_adjustments()
        concurrency = max(1, ConfigHandler.get_sync_max_concurrent_heavy() // concurrency_factor)
        semaphore = asyncio.Semaphore(concurrency)

        async def sync_one_date_table(date_str, table_name, table_cfg):
            async with semaphore:
                try:
                    api_method = getattr(self.context.api, table_cfg["api"])
                    df = await api_method(ann_date=date_str)

                    row_count = 0
                    if df is not None and not df.empty:
                        save_map = {
                            "fina_forecast": self.context.cache.save_fina_forecast,
                            # Phase 3G §4.3.4：业绩快报（参考 forecast 模式）
                            "express": self.context.cache.save_express,
                            "dividend": self.context.cache.save_dividend,
                            "repurchase": self.context.cache.save_repurchase,
                        }

                        save_func = save_map.get(table_name)
                        if save_func:
                            row_count = await save_func(df)
                            row_count = row_count if row_count is not None else len(df)
                            logger.debug(
                                "[FinancialSync] BatchSync | Synced %s for %s: %s records",
                                table_name,
                                date_str,
                                len(df),
                            )

                    date_obj = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                    await self.context.cache.update_sync_status(
                        table_name,
                        date_obj,
                        row_count,
                    )

                except EngineDisposedError:
                    raise
                except TushareAPIPermissionError:
                    logger.warning(
                        "[FinancialSync] BatchSync | ⛔ Permission Denied for %s",
                        table_name,
                    )
                    date_obj = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                    await self.context.cache.update_sync_status(
                        table_name,
                        date_obj,
                        0,
                        status="skipped_permission",
                        last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
                    )
                except Exception as e:
                    error_info = classify_error(e, context="general")
                    severity = classify_severity(e, context="general")
                    if severity == "system":
                        logger.critical(
                            "[FinancialSync] BatchSync | SYSTEM-LEVEL failure for %s on %s: %s",
                            table_name,
                            date_str,
                            safe_error(e),
                            exc_info=True,
                        )
                        raise
                    elif severity == "recoverable":
                        logger.warning(
                            "[FinancialSync] BatchSync | Failed %s on %s (%s): %s",
                            table_name,
                            date_str,
                            error_info["code"],
                            safe_error(e),
                            exc_info=True,
                        )
                    else:
                        logger.error(
                            "[FinancialSync] BatchSync | Failed %s on %s (%s): %s",
                            table_name,
                            date_str,
                            error_info["code"],
                            safe_error(e),
                            exc_info=True,
                        )

        # Iterate per-date (not all-at-once) to avoid task explosion
        for i, d in enumerate(dates):
            if self._shutdown_event.is_set():
                logger.debug(
                    "[FinancialSync] BatchSync | Cancelled during corporate actions.",
                )
                break

            # Each date: 3 tables in parallel
            coros = [sync_one_date_table(d, tbl, cfg) for tbl, cfg in FINANCIAL_BATCH_TABLES.items()]
            gather_results = await gather_return_exceptions_propagating_cancel(*coros)
            for gr in gather_results:
                if isinstance(gr, Exception):
                    logger.warning("[FinancialSync] Batch table sync failed for date %s: %s", d, gr, exc_info=True)

            # Report progress every 10 days
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(
                    i + 1,
                    total,
                    I18n.get("progress_sync_fundamentals").format(
                        current=i + 1,
                        total=total,
                        stock=f"batch:{d}",
                    ),
                )

        # Final completion report
        if progress_callback:
            progress_callback(total, total, I18n.get("status_ready"))

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _fetch_comprehensive_financial_data(
        self,
        ts_code,
        start_date=None,
        end_date=None,
        period=None,
    ) -> tuple:
        """
        Helper: Fetch and merge Income, Balance Sheet, Cashflow, and Financial Indicators.
        Returns: (merged_df, aux_counts) where aux_counts = {"mainbz": 0, "audit": 0}
        """
        api = self.context.api

        # P0-4: directly await async API methods
        async def fetch_income():
            return await api.get_income(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )

        async def fetch_balance():
            return await api.get_balancesheet(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )

        async def fetch_indicator():
            return await api.get_fina_indicator(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )

        async def fetch_cashflow():
            return await api.get_cashflow(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )

        # Note: Auxiliary tables (mainbz, audit) are written best-effort (尽力而为)
        # during the fetch phase and are not atomic with the main financial tables transaction.
        # However, they are fully idempotent when re-run.
        async def fetch_aux(api_func, save_func, **kwargs) -> int:
            try:
                df = await api_func(**kwargs)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    row_count = await save_func(df)
                    return row_count if row_count is not None else len(df)
                return 0
            except EngineDisposedError:
                raise
            except TushareAPIPermissionError:
                logger.debug(
                    "[FinancialSync] Fetch | Permission denied for aux table on %s",
                    ts_code,
                )
                return 0
            except Exception as e:
                error_info = classify_error(e, context="general")
                severity = classify_severity(e, context="general")
                if severity == "system":
                    logger.critical(
                        "[FinancialSync] Fetch | SYSTEM-LEVEL failure for aux table on %s: %s",
                        ts_code,
                        safe_error(e),
                        exc_info=True,
                    )
                    raise
                elif severity == "recoverable":
                    logger.warning(
                        "[FinancialSync] Fetch | Failed to fetch aux table on %s (%s): %s",
                        ts_code,
                        error_info["code"],
                        safe_error(e),
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "[FinancialSync] Fetch | Failed to fetch aux table on %s (%s): %s",
                        ts_code,
                        error_info["code"],
                        safe_error(e),
                        exc_info=True,
                    )
                return 0

        try:
            aux_tasks = [
                fetch_aux(
                    api.get_fina_mainbz,
                    self.context.cache.save_fina_mainbz,
                    ts_code=ts_code,
                    period=period,
                    start_date=start_date,
                    end_date=end_date,
                ),
                fetch_aux(
                    api.get_fina_audit,
                    self.context.cache.save_fina_audit,
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                ),
            ]

            # Parallel fetch core + aux
            results = await gather_return_exceptions_propagating_cancel(
                fetch_income(),
                fetch_balance(),
                fetch_indicator(),
                fetch_cashflow(),
                *aux_tasks,
            )

            # Unpack Core Results
            # results[0-3] are core, results[4-5] are aux (row_counts)
            df_inc, df_bal, df_fina, df_cf = results[0], results[1], results[2], results[3]

            core_names = ["income", "balance", "indicator", "cashflow"]
            for name, result in zip(core_names, results[:4], strict=False):
                if isinstance(result, Exception):
                    logger.warning(
                        "[FinancialSync] Fetch | Core table '%s' failed for %s: %s",
                        name,
                        ts_code,
                        result,
                    )

            # Return aux counts as dict (for caller to accumulate)
            aux_counts = {
                "mainbz": results[4] if isinstance(results[4], int) else 0,
                "audit": results[5] if isinstance(results[5], int) else 0,
            }
            for aux_name, aux_idx in [("mainbz", 4), ("audit", 5)]:
                if isinstance(results[aux_idx], Exception):
                    logger.debug(
                        "[FinancialSync] Fetch | Aux table '%s' failed for %s: %s",
                        aux_name,
                        ts_code,
                        results[aux_idx],
                    )

            # 2. Proceed with Core Financial Merging (Income/Balance/Indicator)
            dfs = []
            if isinstance(df_inc, pd.DataFrame) and not df_inc.empty:
                dfs.append(_dedup_financial_df(df_inc))
            if isinstance(df_bal, pd.DataFrame) and not df_bal.empty:
                dfs.append(_dedup_financial_df(df_bal))
            if isinstance(df_fina, pd.DataFrame) and not df_fina.empty:
                dfs.append(_dedup_financial_df(df_fina))
            if isinstance(df_cf, pd.DataFrame) and not df_cf.empty:
                dfs.append(_dedup_financial_df(df_cf))

            if not dfs:
                return None, aux_counts

            df_merged = dfs[0]
            for i in range(1, len(dfs)):
                df_merged = pd.merge(
                    df_merged,
                    dfs[i],
                    on=["ts_code", "end_date"],
                    how="outer",
                    suffixes=("", "_drop"),
                )
                # Immediately remove _drop columns to prevent duplicate suffixes in subsequent merges
                df_merged = df_merged[[c for c in df_merged.columns if not c.endswith("_drop")]]

            return df_merged, aux_counts

        except EngineDisposedError:
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical(
                    "[FinancialSync] Fetch | SYSTEM-LEVEL failure for %s: %s",
                    ts_code,
                    safe_error(e),
                    exc_info=True,
                )
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[FinancialSync] Fetch | Comprehensive data failed for %s (%s): %s",
                    ts_code,
                    error_info["code"],
                    safe_error(e),
                    exc_info=True,
                )
            else:
                logger.error(
                    "[FinancialSync] Fetch | Comprehensive data failed for %s (%s): %s",
                    ts_code,
                    error_info["code"],
                    safe_error(e),
                    exc_info=True,
                )
            return None, {"mainbz": 0, "audit": 0}

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def repair_financial_data(self, ts_codes, progress_callback=None) -> int:
        """
        Targeted repair for specific stocks.
        Fixes ALL tables defined in constants.
        """
        if not ts_codes:
            return 0

        now = get_now()
        current_year = now.year
        p_cands = []
        for y in range(current_year, current_year - 4, -1):
            p_cands.extend([f"{y}0331", f"{y}0630", f"{y}0930", f"{y}1231"])
        periods = sorted(
            [p for p in p_cands if p < now.strftime("%Y%m%d")],
            reverse=True,
        )[:12]

        logger.debug("[FinancialSync] Repair | Repairing %s stocks...", len(ts_codes))

        total_saved = 0

        for period_idx, period in enumerate(periods):
            for i, ts_code in enumerate(ts_codes):
                try:
                    await asyncio.sleep(
                        self._get_request_delay(is_heavy=True),
                    )
                    merged_df, aux_counts = await self._fetch_comprehensive_financial_data(
                        ts_code,
                        period=period,
                    )
                    saved = 0
                    if merged_df is not None and not merged_df.empty:
                        for col in FINANCIAL_REPORT_SCHEMA_COLS:
                            if col not in merged_df.columns:
                                merged_df[col] = None
                        saved = (
                            await self.context.cache.save_financial_reports(
                                merged_df[FINANCIAL_REPORT_SCHEMA_COLS],
                            )
                            or 0
                        )
                    delta = saved + aux_counts.get("mainbz", 0) + aux_counts.get("audit", 0)
                    total_saved += delta

                    if progress_callback and i % 10 == 0:
                        progress_callback(
                            period_idx * len(ts_codes) + i,
                            len(periods) * len(ts_codes),
                            I18n.get("status_repairing", period=period, code=ts_code),
                        )
                except EngineDisposedError:
                    raise
                except Exception as e:
                    error_info = classify_error(e, context="general")
                    severity = classify_severity(e, context="general")
                    if severity == "system":
                        logger.critical(
                            "[FinancialSync] Repair | SYSTEM-LEVEL failure for %s period=%s: %s",
                            ts_code,
                            period,
                            safe_error(e),
                            exc_info=True,
                        )
                        raise
                    elif severity == "recoverable":
                        logger.warning(
                            "[FinancialSync] Repair | Failed for %s period=%s (%s): %s",
                            ts_code,
                            period,
                            error_info["code"],
                            safe_error(e),
                            exc_info=True,
                        )
                    else:
                        logger.error(
                            "[FinancialSync] Repair | Failed for %s period=%s (%s): %s",
                            ts_code,
                            period,
                            error_info["code"],
                            safe_error(e),
                            exc_info=True,
                        )

        return total_saved
