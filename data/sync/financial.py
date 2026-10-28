"""
Financial Sync Strategy.
Handles comprehensive fundamentals, incremental updates, and data repair.
"""

import asyncio
import datetime
import json
import logging
import threading
import time

import pandas as pd

from data.constants import FINANCIAL_BATCH_TABLES, FINANCIAL_REPORT_SCHEMA_COLS, SYNC_RESULT_SKIPPED_PERMISSION
from data.sync.base import (
    ISyncStrategy,
    SyncResult,
    SyncStatus,
    _get_seasonal_adjustments,
    _is_peak_disclosure_season,
    safe_error,
)
from data.persistence.app_state_service import get_app_state, set_app_state, set_app_state_max
from data.persistence.daos.base_dao import EngineDisposedError
from data.external.tushare_client import TushareAPIPermissionError
from core.i18n import Message
from utils.async_utils import gather_return_exceptions_propagating_cancel
from utils.error_classifier import classify_severity, log_classified
from utils.loop_local import get_loop_local
from utils.config_handler import ConfigHandler
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date

logger = logging.getLogger(__name__)

# M7.3/M7.4: 循环体取消检查的时间间隔（秒）。
# 项目硬约束："long-running 操作必须每 2 秒检查 cancel_event"。旧实现完全无检查，
# 嵌套循环可运行数小时不响应取消信号，违反 2s 红线。改用 time.monotonic()
# 时间维度测量，参考 data/sync/sw_industry.py 模式。
_CANCEL_CHECK_INTERVAL_SECONDS = 2.0

# UIR-F14-004: 增量披露同步检查点。
# - 检查点为「连续成功披露日」（YYYYMMDD），只在早于该日的全部披露目标均已解决
#   （成功或合法空）后单调推进，经 set_app_state_max 保证 R22 水位单调；
# - pending 为「未解决披露目标」集合（按披露日分组），失败/取消未处理的目标持久化后
#   在后续轮次优先重试，避免被后续成功日跨过；
# - last_sync_date 仅作遥测（最近尝试时间），不再作为增量窗口完成水位。
_FINANCIAL_DISCLOSURE_CHECKPOINT_KEY = "financial_disclosure_checkpoint"
_FINANCIAL_DISCLOSURE_PENDING_KEY = "financial_disclosure_pending"
# 无检查点（升级/新建）时选定的历史补抓窗口，避免沿用「最近尝试时间」直接跳过旧失败日。
_LEGACY_DISCLOSURE_BACKFILL_DAYS = 30


def _parse_disclosure_checkpoint(raw) -> datetime.date | None:
    """Parse the persisted contiguous-complete disclosure checkpoint (YYYYMMDD)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.datetime.strptime(raw.strip(), "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
    except ValueError:
        logger.warning("[FinancialSync] Checkpoint | Invalid disclosure checkpoint %r, ignoring.", raw)
        return None


def _parse_disclosure_day(day: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime(day, "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
    except (TypeError, ValueError):
        return None


def _load_pending_disclosure_targets(raw) -> dict[str, set[tuple[str, str]]]:
    """Load unresolved disclosure targets: {YYYYMMDD: {(ts_code, period), ...}}.

    Invalid/legacy payloads degrade to an empty set (fail-open: the day is re-fetched
    from the checkpoint, so no completed day is skipped).
    """
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("[FinancialSync] Checkpoint | Invalid pending disclosure payload, resetting to empty.")
        return {}
    if not isinstance(payload, dict):
        return {}
    pending: dict[str, set[tuple[str, str]]] = {}
    for day, entries in payload.items():
        if not isinstance(day, str) or not isinstance(entries, list) or _parse_disclosure_day(day) is None:
            continue
        targets = {
            (entry["ts_code"], entry["period"])
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("ts_code"), str)
            and isinstance(entry.get("period"), str)
        }
        if targets:
            pending[day] = targets
    return pending


def _dump_pending_disclosure_targets(pending: dict[str, set[tuple[str, str]]]) -> str:
    return json.dumps(
        {
            day: [{"ts_code": code, "period": period} for code, period in sorted(targets)]
            for day, targets in sorted(pending.items())
        },
        ensure_ascii=False,
    )


def _status_data_date(status: dict | None) -> datetime.date | None:
    """Business water level from sync_status: last_data_date, fallback last_sync_date."""
    if not status:
        return None
    raw = status.get("last_data_date")
    if raw is None or pd.isna(raw):
        raw = status.get("last_sync_date")
    if raw is None or pd.isna(raw):
        return None
    try:
        return parse_date(raw).date()
    except (TypeError, ValueError):
        logger.warning("[FinancialSync] Checkpoint | Failed to parse sync status date %r, using backfill window.", raw)
        return None


def _dedup_financial_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicate financial DataFrame by full version key (end_date + ann_date).

    DATA-05: 保留同一报告期（end_date）的全部公告版本（ann_date），不再按 end_date
    去重，使财报更正/追溯调整能被持久化而非 UPSERT 覆盖。主键已扩展为
    (ts_code, end_date, ann_date)，同一 (end_date, ann_date) 只保留一行即可。

    For DataFrames with 'ann_date' column, sorts by [end_date, ann_date] ascending
    and keeps the last row per (end_date, ann_date). This preserves every disclosed
    version; identical duplicated rows (same end_date + ann_date) collapse to one.

    For DataFrames without 'ann_date', falls back to simple end_date dedup (no
    version dimension available on the payload).
    """
    if df is None or df.empty:
        return df

    if "ann_date" in df.columns:
        sort_cols = ["end_date", "ann_date"]
        ascending = [True, True]
        return df.sort_values(by=sort_cols, ascending=ascending, na_position="first").drop_duplicates(
            subset=["end_date", "ann_date"], keep="last"
        )
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
        """Prefer the latest closed trade date for default sync windows.

        review03-C14: 委托共享回退链（交易日历服务 → 已同步行情最大日期 → 显式抛错），
        不再回退到本地日历日。
        """
        from data.domain_services.trade_calendar_service import get_effective_trade_date

        return await get_effective_trade_date(getattr(self.context, "processor", None))

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
                status = await self.context.cache.sync_dao.get_sync_status("financial_reports")
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
            severity = classify_severity(e, context="general")
            error_info = log_classified(
                logger,
                e,
                "general",
                "[FinancialSync] Run | failed (%s): %s",
                exc_info=True,
            )
            if severity == "system":
                raise
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
        # M-DATA-11/D1: 可恢复失败股票计数，批次结束后反映到 SyncResult（S17 同模式）
        failed_stock_count = 0
        _counter_lock = get_loop_local("financial_counter_lock", asyncio.Lock)

        # Force Logic: Reset sync status for resume
        if force:
            logger.debug(
                "[FinancialSync] FullSync | Force mode: Clearing previous sync status...",
            )
            await self.context.cache.sync_dao.clear_step4_sync_status()

        # 1. Get Stock List
        df_basic = await self.context.cache.stock_dao.get_stock_basic()
        if df_basic.empty:
            logger.error(
                "[FinancialSync] FullSync | ❌ No stocks found. Run Stock Basic sync first.",
            )
            result_accumulator.errors.append("No stocks found in cache")
            result_accumulator.status = "failed"
            return

        # DAT-04: 财务同步股票池覆盖「曾经在市」股票（list_status IN ('L','D')），
        # 消除基本面生存者偏差。stock_basic 已由 data_processor.sync_stock_basic
        # 通过 get_stock_basic_all() 同步 L/D 双状态，此处直接按状态过滤。
        df_pool = df_basic[df_basic["list_status"].isin(["L", "D"])]
        all_stocks = set(df_pool["ts_code"].tolist())
        total_stocks = len(all_stocks)

        # 2. Concurrency Control
        concurrency_factor, _ = _get_seasonal_adjustments()
        concurrency = max(1, ConfigHandler.get_sync_max_concurrent_heavy() // concurrency_factor)
        semaphore = asyncio.Semaphore(concurrency)

        # 3. Data-as-State Resume Logic
        synced_stocks = await self.context.cache.sync_dao.get_completed_step4_stocks(
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
                    Message(
                        "progress_sync_fundamentals",
                        {"current": total_stocks, "total": total_stocks, "stock_key": "status_ready"},
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
            nonlocal completed_count, total_mainbz_rows, total_audit_rows, failed_stock_count
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

                        # M-DATA-11/D1: fetch 内部对 recoverable 失败降级返回
                        # (None/部分df, core_failed=True)，此非合法空数据。计入失败
                        # 并按「incomplete, pending retry」处理：不保存残缺数据、
                        # 不标记完成，允许下次重试补全。
                        if aux_counts.get("core_failed"):
                            has_error = True
                            failed_stock_count += 1

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
                        severity = classify_severity(e, context="general")
                        log_classified(
                            logger,
                            e,
                            "general",
                            "[FinancialSync] StockSync | Failed (%s): %s for %s",
                            ts_code,
                            exc_info=True,
                        )
                        if severity == "system":
                            raise
                        # M-DATA-11/D1: recoverable/operational 失败计入 failed_stock_count，
                        # 由批次结束后统一反映到 SyncResult（S17 增量同模式），避免
                        # 「有失败但 status=success」假终态。
                        failed_stock_count += 1

                    if self._shutdown_event.is_set():
                        return

                    if not has_error:
                        has_actual_data = df_merged is not None and not df_merged.empty
                        if has_actual_data:
                            async with self.context.cache.financial_transaction() as tx_conn:
                                await self.context.cache.financial_dao.save_financial_reports(
                                    df_merged[FINANCIAL_REPORT_SCHEMA_COLS],  # type: ignore[optional-subscript]
                                    conn=tx_conn,
                                )
                                await self.context.cache.sync_dao.mark_stock_step4_completed(
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
                severity = classify_severity(e, context="general")
                log_classified(
                    logger,
                    e,
                    "general",
                    "[FinancialSync] StockSync | Failed (%s): %s for %s",
                    ts_code,
                    exc_info=True,
                )
                if severity == "system":
                    raise
                # M-DATA-11/D1: 内层 try 之外的 save/进度阶段 recoverable 失败
                # 同样计入 failed_stock_count，避免「save 失败仅记日志无痕」。
                failed_stock_count += 1

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
                        Message(
                            "progress_sync_fundamentals",
                            {"current": completed_count, "total": total_stocks, "stock": ts_code},
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
                stock_results = await gather_return_exceptions_propagating_cancel(*tasks)
                # R5: 显式传播 EngineDisposedError，避免被静默吞没。
                # M-DATA-11/D1: process_one_stock 对系统级异常（severity==system 与
                # AttributeError/NameError/TypeError/ImportError 编程错误）会 re-raise，
                # 经 gather 封装转为返回值；旧实现仅回检 EngineDisposedError，其余系统级
                # 异常被静默丢弃（失败无痕、result_accumulator 保持 success 假终态）。
                # 对齐 historical.sync_daily_market_snapshot 的统一回检：BaseException 一律重抛。
                for sr in stock_results:
                    if isinstance(sr, BaseException):
                        raise sr
            finally:
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)

        # M-DATA-11/D1: 批次结束后把可恢复失败反映到 SyncResult（S17 增量同模式），
        # 避免「失败股票仅记日志但 result_accumulator 恒 success」。不覆盖更严重终态。
        if failed_stock_count > 0 and result_accumulator.status not in (
            SyncStatus.FAILED.value,
            SyncStatus.CANCELLED.value,
        ):
            result_accumulator.status = SyncStatus.PARTIAL.value
            result_accumulator.errors.append(
                f"FinancialSync: {failed_stock_count}/{total_stocks} stock(s) failed during full sync"
            )

        # Batch phase: corporate actions sync (serial, AFTER stock loop to avoid API contention)
        logger.debug(
            "[FinancialSync] BatchSync | Starting corporate actions batch phase...",
        )

        # Update sync status for aux tables (after all stocks processed)
        today = get_now().date()
        if total_mainbz_rows > 0:
            await self.context.cache.sync_dao.update_sync_status(
                "fina_mainbz",
                today,
                total_mainbz_rows,
            )
            logger.debug("[FinancialSync] fina_mainbz total: %s", total_mainbz_rows)

        if total_audit_rows > 0:
            await self.context.cache.sync_dao.update_sync_status(
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

        await self._sync_corporate_actions_by_date(all_dates, batch_progress, result_accumulator=result_accumulator)

        # M-DATA-11/D4: 全量完成后写 financial_reports 同步状态。旧实现从不写该表
        # sync_status：a) _run_impl 增量分支（依赖 last_sync_date 存在）对新装用户永不可达，
        # 每次都退化为全量遍历；b) should_sync_financials 的 30 天/财报季节流判定失效。
        # record_count 记完成股票数（全量以股票为处理单元，非行数口径）；有失败记 partial
        # （partial 不推进 last_data_date，下次增量窗口将覆盖当日重试，UPSERT 幂等）。
        _full_status = (
            SyncStatus.PARTIAL.value
            if failed_stock_count > 0 or result_accumulator.status == SyncStatus.PARTIAL.value
            else "success"
        )
        await self.context.cache.sync_dao.update_sync_status(
            "financial_reports",
            today,
            result_accumulator.added,
            status=_full_status,
        )

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _run_incremental_sync(
        self,
        progress_callback,
        result_accumulator: SyncResult,
    ):
        """
        Incremental Sync: Query disclosure date.

        UIR-F14-004/R21/R22: 增量窗口起点为「连续成功披露日」检查点（app_state），
        而非最近尝试时间（last_sync_date 仅作遥测）。逐 target 显式返回
        success/empty/failed；失败或取消未处理的目标写入 pending 集合并在后续轮次
        优先重试。检查点仅当早于候选日的全部披露目标均已解决后才单调推进，因此
        任一失败日不会被后续成功日跨过。
        """
        if self._shutdown_event.is_set():
            return

        # Local accumulators for aux tables (NOT self variables to avoid AttributeError)
        total_mainbz_rows = 0
        total_audit_rows = 0

        engine = getattr(self.context.cache, "engine", None)
        status = await self.context.cache.sync_dao.get_sync_status("financial_reports")
        checkpoint = _parse_disclosure_checkpoint(await get_app_state(engine, _FINANCIAL_DISCLOSURE_CHECKPOINT_KEY))
        pending = _load_pending_disclosure_targets(await get_app_state(engine, _FINANCIAL_DISCLOSURE_PENDING_KEY))

        today_date = get_now().date()
        if checkpoint is not None:
            window_start = checkpoint + datetime.timedelta(days=1)
        else:
            # 无检查点（升级/新建）：以 sync_status 的业务水位选窗口，但至少回看固定
            # 历史窗口，避免沿用「最近尝试时间」语义直接跳过旧失败披露日。
            backfill_floor = today_date - datetime.timedelta(days=_LEGACY_DISCLOSURE_BACKFILL_DAYS)
            legacy_date = _status_data_date(status)
            if legacy_date is not None and legacy_date + datetime.timedelta(days=1) > backfill_floor:
                window_start = legacy_date + datetime.timedelta(days=1)
            else:
                if legacy_date is not None:
                    logger.warning(
                        "[FinancialSync] Checkpoint | No disclosure checkpoint and legacy data date %s is stale; "
                        "backfilling last %s days.",
                        legacy_date,
                        _LEGACY_DISCLOSURE_BACKFILL_DAYS,
                    )
                window_start = backfill_floor

        window_days: list[datetime.date] = []
        curr_date = window_start
        while curr_date <= today_date:
            window_days.append(curr_date)
            curr_date += datetime.timedelta(days=1)

        pending_days = {d for d in (_parse_disclosure_day(day) for day in pending) if d is not None}
        days_to_process = sorted(set(window_days) | pending_days)
        if not days_to_process:
            logger.debug("[FinancialSync] Incremental | Data already up-to-date")
            return

        dates_to_sync = [day.strftime("%Y%m%d") for day in days_to_process]
        total_saved = 0

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
        # Incremental 半批：使用 sync_batch_size // 2 降低 429 风险（高峰期披露密集，单日 target 量大）。
        _BATCH_SIZE = max(5, ConfigHandler.get_sync_batch_size() // 2)

        async def sync_one_target(ts_code: str, period: str) -> dict:
            """Fetch/save one disclosure target; returns explicit success/empty/failed."""
            result = {"status": "failed", "saved": 0, "mainbz": 0, "audit": 0}

            async with semaphore:
                try:
                    await asyncio.sleep(adjusted_delay)
                    df, aux_counts = await self._fetch_comprehensive_financial_data(
                        ts_code,
                        period=period,
                    )

                    result["mainbz"] = aux_counts["mainbz"]
                    result["audit"] = aux_counts["audit"]

                    if aux_counts.get("core_failed"):
                        # 部分 core 表失败不得当全字段完整：不落库残缺数据，保留待重试。
                        logger.warning(
                            "[FinancialSync] Incremental | Core tables incomplete for %s period=%s; pending retry.",
                            ts_code,
                            period,
                        )
                        return result

                    if df is not None and not df.empty:
                        for col in FINANCIAL_REPORT_SCHEMA_COLS:
                            if col not in df.columns:
                                df[col] = None

                        count = await self.context.cache.financial_dao.save_financial_reports(
                            df[FINANCIAL_REPORT_SCHEMA_COLS],
                        )
                        # 事务/保存成功后才算解决；保存异常由下方 except 归为 failed。
                        # 非空载荷却零落库（如表未注册/主键缺失返回 0）视为未解决，不宣称完整。
                        if isinstance(count, int) and count > 0:
                            result["saved"] = count
                            result["status"] = "success"
                        else:
                            logger.warning(
                                "[FinancialSync] Incremental | Save persisted no rows for %s period=%s; pending retry.",
                                ts_code,
                                period,
                            )
                    else:
                        # 取数成功且无数据：合法空（未披露/无报告），与失败严格区分。
                        result["status"] = "empty"
                except EngineDisposedError:
                    raise
                except Exception as e:
                    severity = classify_severity(e, context="general")
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[FinancialSync] Incremental | Failed (%s): %s for %s period=%s",
                        ts_code,
                        period,
                        exc_info=True,
                    )
                    if severity == "system":
                        raise

            return result

        # M7.6: day 循环转时间维度取消检查（每 2s），参考 sw_industry.py 模式。
        # 旧实现每迭代检查，快速迭代时冗余开销；慢迭代（单日 5-30s）时不影响响应。
        last_cancel_check = time.monotonic()
        # 本轮已处理但未解决的披露日，用于阻止检查点跨过失败日。
        blocking_days: set[datetime.date] = set()
        pending_dirty = False

        try:
            for day in days_to_process:
                now = time.monotonic()
                if now - last_cancel_check >= _CANCEL_CHECK_INTERVAL_SECONDS:
                    last_cancel_check = now
                    if self._shutdown_event.is_set():
                        logger.debug("[FinancialSync] Incremental | Cancelled during day loop.")
                        break

                day_str = day.strftime("%Y%m%d")
                day_targets: list[tuple[str, str]] = []
                seen_targets: set[tuple[str, str]] = set()
                # pending 目标优先重试，不受当日披露清单变化影响。
                for ts_code, period in sorted(pending.get(day_str, set())):
                    if (ts_code, period) not in seen_targets:
                        seen_targets.add((ts_code, period))
                        day_targets.append((ts_code, period))

                disclosure_failed = False
                try:
                    df_disclosure = await self.context.api.get_disclosure_date(date=day_str)
                except EngineDisposedError:
                    raise
                except Exception as e:
                    severity = classify_severity(e, context="general")
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[FinancialSync] Incremental | Disclosure fetch failed (%s): %s for %s",
                        day_str,
                        exc_info=True,
                    )
                    if severity == "system":
                        raise
                    disclosure_failed = True
                    df_disclosure = None

                if df_disclosure is not None and not df_disclosure.empty:
                    for item in df_disclosure[["ts_code", "end_date"]].drop_duplicates().to_dict("records"):
                        pair = (item["ts_code"], item["end_date"])
                        if not all(isinstance(v, str) for v in pair):
                            # 残缺披露行无法构成可解析的待补目标，跳过以免污染 pending。
                            logger.warning("[FinancialSync] Incremental | Skip malformed disclosure row: %s", item)
                            continue
                        if pair not in seen_targets:
                            seen_targets.add(pair)
                            day_targets.append(pair)

                day_saved = 0
                attempted: set[tuple[str, str]] = set()
                failed_targets: set[tuple[str, str]] = set()

                for batch_start in range(0, len(day_targets), _BATCH_SIZE):
                    # M7.4: 内层 batch 循环补 _shutdown_event.is_set() 检查。
                    if self._shutdown_event.is_set():
                        logger.debug(
                            "[FinancialSync] Incremental | Cancelled during batch loop at batch_start=%s",
                            batch_start,
                        )
                        break

                    batch_targets = day_targets[batch_start : batch_start + _BATCH_SIZE]
                    batch = [sync_one_target(ts_code, period) for ts_code, period in batch_targets]
                    batch_results = await gather_return_exceptions_propagating_cancel(*batch)
                    for (ts_code, period), r in zip(batch_targets, batch_results, strict=False):
                        attempted.add((ts_code, period))
                        # R5: EngineDisposedError 必须优先传播，不作为普通 batch 失败
                        if isinstance(r, EngineDisposedError):
                            raise r
                        if isinstance(r, Exception):
                            logger.warning("[FinancialSync] Batch task failed: %s", r)
                            failed_targets.add((ts_code, period))
                            continue
                        if r["status"] == "failed":
                            failed_targets.add((ts_code, period))
                            continue
                        day_saved += r["saved"]  # type: ignore[index]
                        total_saved += r["saved"]  # type: ignore[index]
                        total_mainbz_rows += r["mainbz"]  # type: ignore[index]
                        total_audit_rows += r["audit"]  # type: ignore[index]

                # 取消导致未处理的目标同样保留为 pending，避免下次被检查点跨过。
                unresolved = failed_targets | (set(day_targets) - attempted)
                if unresolved:
                    pending[day_str] = unresolved
                    pending_dirty = True
                elif day_str in pending:
                    pending.pop(day_str, None)
                    pending_dirty = True

                pending_days = {d for d in (_parse_disclosure_day(key) for key in pending) if d is not None}

                day_has_error = disclosure_failed or bool(unresolved)
                if day_has_error and day_saved == 0:
                    # 全部失败/未完成：标记 failed；检查点不推进，下次重试
                    await self.context.cache.sync_dao.update_sync_status(
                        "financial_reports",
                        day,
                        0,
                        status="failed",
                    )
                elif day_has_error:
                    # 部分失败：标记 partial；检查点不推进，pending 目标下次重试
                    await self.context.cache.sync_dao.update_sync_status(
                        "financial_reports",
                        day,
                        day_saved,
                        status="partial",
                    )
                else:
                    await self.context.cache.sync_dao.update_sync_status(
                        "financial_reports",
                        day,
                        day_saved,
                    )

                # 检查点仅在「早于该日的所有披露目标均已解决」时推进（R22 单调写）。
                if day_has_error:
                    blocking_days.add(day)
                else:
                    blocking_days.discard(day)
                    if not any(d <= day for d in blocking_days) and not any(d <= day for d in pending_days):
                        await set_app_state_max(
                            engine,
                            _FINANCIAL_DISCLOSURE_CHECKPOINT_KEY,
                            day_str,
                        )

                if pending_dirty:
                    await set_app_state(
                        engine,
                        _FINANCIAL_DISCLOSURE_PENDING_KEY,
                        _dump_pending_disclosure_targets(pending),
                    )
                    pending_dirty = False

                # S17 同模式：当日 fetch/save 失败必须反映到 SyncResult，
                # 避免「sync_status 表已记 failed/partial 但 SyncResult 仍 success」
                # 导致调用方与调度器误判本次同步完全成功。不覆盖更严重的终态。
                if day_has_error and result_accumulator.status not in (
                    SyncStatus.FAILED.value,
                    SyncStatus.CANCELLED.value,
                ):
                    result_accumulator.status = SyncStatus.PARTIAL.value

                if progress_callback:
                    progress_callback(0, 0, Message("progress_sync_done", {"day": day_str}))
        finally:
            # 取消/异常时也持久化已知未解决目标（检查点未推进，下次从该日重试）。
            if pending_dirty:
                await set_app_state(
                    engine,
                    _FINANCIAL_DISCLOSURE_PENDING_KEY,
                    _dump_pending_disclosure_targets(pending),
                )

        # --- Step 2: Batch Sync Corporate Actions (O(Time) Optimization) ---
        # Sync Forecasts, Dividends, etc. by date
        await self._sync_corporate_actions_by_date(dates_to_sync, result_accumulator=result_accumulator)

        # Update sync status for aux tables (after all stocks processed)
        today = get_now().date()
        if total_mainbz_rows > 0:
            await self.context.cache.sync_dao.update_sync_status(
                "fina_mainbz",
                today,
                total_mainbz_rows,
            )
            logger.debug("[FinancialSync] fina_mainbz total: %s", total_mainbz_rows)

        if total_audit_rows > 0:
            await self.context.cache.sync_dao.update_sync_status(
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
        result_accumulator: SyncResult | None = None,
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
                            "fina_forecast": self.context.cache.financial_dao.save_fina_forecast,
                            # Phase 3G §4.3.4：业绩快报（参考 forecast 模式）
                            "express": self.context.cache.express_dao.save_express,
                            "dividend": self.context.cache.financial_dao.save_dividend,
                            "repurchase": self.context.cache.financial_dao.save_repurchase,
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

                    date_obj = datetime.datetime.strptime(date_str, "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
                    await self.context.cache.sync_dao.update_sync_status(
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
                    date_obj = datetime.datetime.strptime(date_str, "%Y%m%d").date()  # noqa: DTZ007  YYYYMMDD 业务日期字符串无时区语义
                    await self.context.cache.sync_dao.update_sync_status(
                        table_name,
                        date_obj,
                        0,
                        status="skipped_permission",
                        last_result_status=SYNC_RESULT_SKIPPED_PERMISSION,
                    )
                except Exception as e:
                    severity = classify_severity(e, context="general")
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[FinancialSync] BatchSync | Failed (%s): %s for %s on %s",
                        table_name,
                        date_str,
                        exc_info=True,
                    )
                    if severity == "system":
                        raise

        # Iterate per-date (not all-at-once) to avoid task explosion
        # M7.6: corporate actions 循环转时间维度取消检查（每 2s），参考 sw_industry.py 模式。
        last_cancel_check = time.monotonic()
        for i, d in enumerate(dates):
            now = time.monotonic()
            if now - last_cancel_check >= _CANCEL_CHECK_INTERVAL_SECONDS:
                last_cancel_check = now
                if self._shutdown_event.is_set():
                    logger.debug(
                        "[FinancialSync] BatchSync | Cancelled during corporate actions.",
                    )
                    break

            # Each date: 3 tables in parallel
            coros = [sync_one_date_table(d, tbl, cfg) for tbl, cfg in FINANCIAL_BATCH_TABLES.items()]
            gather_results = await gather_return_exceptions_propagating_cancel(*coros)
            for gr in gather_results:
                # R5: EngineDisposedError 必须优先传播，不作为普通 batch table 失败
                if isinstance(gr, EngineDisposedError):
                    raise gr
                if isinstance(gr, Exception):
                    logger.warning("[FinancialSync] Batch table sync failed for date %s: %s", d, gr, exc_info=True)
                    # M-DATA-11/D1: 批次表失败必须反映到 SyncResult（增量 day_has_error
                    # 同模式），否则 corporate actions 失败后调用方仍看到 success 假终态。
                    if result_accumulator is not None and result_accumulator.status not in (
                        SyncStatus.FAILED.value,
                        SyncStatus.CANCELLED.value,
                    ):
                        result_accumulator.status = SyncStatus.PARTIAL.value
                        result_accumulator.errors.append(
                            f"FinancialSync corporate actions failed on {d}: {safe_error(gr)}"
                        )

            # Report progress every 10 days
            if progress_callback and (i + 1) % 10 == 0:
                progress_callback(
                    i + 1,
                    total,
                    Message(
                        "progress_sync_fundamentals",
                        {"current": i + 1, "total": total, "stock": f"batch:{d}"},
                    ),
                )

        # Final completion report
        if progress_callback:
            progress_callback(total, total, Message("status_ready"))

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
        Returns: (merged_df, aux_counts) where aux_counts = {"mainbz": 0, "audit": 0,
        "core_failed": bool}；core_failed=True 表示 core 表 recoverable 失败（非合法空数据）。
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
                severity = classify_severity(e, context="general")
                log_classified(
                    logger,
                    e,
                    "general",
                    "[FinancialSync] Fetch | Failed to fetch aux table (%s): %s for %s",
                    ts_code,
                    exc_info=True,
                )
                if severity == "system":
                    raise
                return 0

        # data-P1-6: 预初始化 aux_counts，确保 except 块能返回已保存的 aux 行数
        # 而非硬编码 0。aux 表通过 fetch_aux 内部 save_func 已写入 DB，
        # 即使 core 表合并失败也不应丢弃已保存的行数。
        aux_counts = {"mainbz": 0, "audit": 0}

        try:
            aux_tasks = [
                fetch_aux(
                    api.get_fina_mainbz,
                    self.context.cache.financial_dao.save_fina_mainbz,
                    ts_code=ts_code,
                    period=period,
                    start_date=start_date,
                    end_date=end_date,
                ),
                fetch_aux(
                    api.get_fina_audit,
                    self.context.cache.financial_dao.save_fina_audit,
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

            # R5: gather_return_exceptions_propagating_cancel 仅传播 CancelledError，
            # EngineDisposedError 作为普通异常保留在 results 中。若不显式 re-raise，
            # 后续 isinstance(r, int) 检查会静默吞没为 aux_counts=0 / core 降级跳过，
            # 违反 R5 僵尸引擎操作红线。
            # M-DATA-11/D1: system 级异常（如 MemoryError）与非 Exception 的
            # BaseException 同样必须传播，不得吞成 core 降级 warning / aux_counts=0，
            # 否则调用方无法区分「合法空数据」与「系统级失败」，失败计数与批次
            # 边界回检均失效。
            for r in results:
                if not isinstance(r, BaseException):
                    continue
                if isinstance(r, EngineDisposedError):
                    raise r
                if not isinstance(r, Exception):
                    raise r
                if classify_severity(r, context="general") == "system":
                    raise r

            # Unpack Core Results
            # results[0-3] are core, results[4-5] are aux (row_counts)
            df_inc, df_bal, df_fina, df_cf = results[0], results[1], results[2], results[3]

            core_names = ["income", "balance", "indicator", "cashflow"]
            # M-DATA-11/D1: recoverable 失败降级保留（其余 core 表数据仍可合并），
            # 但必须以 core_failed 标记传给调用方——返回 (None/部分df, core_failed=True)
            # 区别于「合法空数据」，由 process_one_stock 计入失败（pending retry）。
            # system 级已在上方回检中传播，不会到达此处。
            core_failed = False
            for name, result in zip(core_names, results[:4], strict=False):
                if isinstance(result, Exception):
                    logger.warning(
                        "[FinancialSync] Fetch | Core table '%s' failed for %s: %s",
                        name,
                        ts_code,
                        result,
                    )
                    core_failed = True

            # Return aux counts as dict (for caller to accumulate)
            aux_counts = {
                "mainbz": results[4] if isinstance(results[4], int) else 0,
                "audit": results[5] if isinstance(results[5], int) else 0,
                "core_failed": core_failed,
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

            # DATA-05: 仅当全部 core 表都携带 ann_date 版本维度时按 (ts_code, end_date,
            # ann_date) 对齐，避免同一报告期多版本合并产生笛卡尔积；否则回退到
            # (ts_code, end_date) 合并，兼容无版本维度的 payload（测试 mock / 特殊 API）。
            has_ann_dimension = all("ann_date" in df.columns for df in dfs)
            merge_on = ["ts_code", "end_date", "ann_date"] if has_ann_dimension else ["ts_code", "end_date"]

            df_merged = dfs[0]
            for i in range(1, len(dfs)):
                df_merged = pd.merge(
                    df_merged,
                    dfs[i],
                    on=merge_on,
                    how="outer",
                    suffixes=("", "_drop"),
                )
                # Immediately remove _drop columns to prevent duplicate suffixes in subsequent merges
                df_merged = df_merged[[c for c in df_merged.columns if not c.endswith("_drop")]]

            # DATA-05: 主键含 ann_date 且 NOT NULL。缺失公告日的行无法落库
            # （与 0022 迁移/DAT-06 语义一致），在返回前过滤，避免约束冲突。
            if "ann_date" in df_merged.columns:
                df_merged = df_merged[df_merged["ann_date"].notna()]

            return df_merged, aux_counts

        except EngineDisposedError:
            raise
        except Exception as e:
            severity = classify_severity(e, context="general")
            log_classified(
                logger,
                e,
                "general",
                "[FinancialSync] Fetch | Comprehensive data failed (%s): %s for %s",
                ts_code,
                exc_info=True,
            )
            if severity == "system":
                raise
            # M-DATA-11/D1: merge 阶段 recoverable 失败同样以 core_failed 标记返回，
            # 避免「合并失败」被调用方当作合法空数据。
            return None, {**aux_counts, "core_failed": True}

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

        # M7.3: 嵌套循环按时间维度（每 2 秒）检查 _shutdown_event。旧实现完全无
        # 取消检查，可运行数小时不响应取消信号，违反 2s 红线。参考
        # data/sync/sw_industry.py 模式，使用 time.monotonic() 时间维度测量。
        last_cancel_check = time.monotonic()

        for period_idx, period in enumerate(periods):
            for i, ts_code in enumerate(ts_codes):
                now = time.monotonic()
                if now - last_cancel_check >= _CANCEL_CHECK_INTERVAL_SECONDS:
                    last_cancel_check = now
                    if self._shutdown_event.is_set():
                        logger.debug(
                            "[FinancialSync] Repair | Cancelled at period_idx=%s (%s) i=%s/%s",
                            period_idx,
                            period,
                            i,
                            len(ts_codes),
                        )
                        return total_saved

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
                            await self.context.cache.financial_dao.save_financial_reports(
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
                            Message("status_repairing", {"period": period, "code": ts_code}),
                        )
                except EngineDisposedError:
                    raise
                except Exception as e:
                    severity = classify_severity(e, context="general")
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[FinancialSync] Repair | Failed (%s): %s for %s period=%s",
                        ts_code,
                        period,
                        exc_info=True,
                    )
                    if severity == "system":
                        raise

        return total_saved
