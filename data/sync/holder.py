import datetime
import logging
import typing

import pandas as pd

from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now

from .base import ISyncStrategy, SyncResult

logger = logging.getLogger(__name__)

_MAX_ERRORS = 5

_PROGRESS_LOG_INTERVAL = 200


class HolderSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Shareholder & Pledge data using O(Quarter) approach.

    These tables (stk_holdernumber, top10_holders, pledge_stat) contain
    quarterly-disclosure data.

    API call patterns:
      - stk_holdernumber: full-market by enddate, ~2 paginated calls
      - top10_holders: per-stock iteration (ts_code required), ~5000 calls
      - pledge_stat: full-market by end_date, ~2 paginated calls

    Rate Limiting:
      top10_holders uses a dedicated slow-API rate limiter in TushareClient
      (configured via _SLOW_API_OVERRIDES). This ensures it runs at a
      sustainable pace without triggering server-side 429 errors.
    """

    def __init__(self, context: typing.Any):
        super().__init__(context)
        self._cancelled = False

    async def cancel(self):
        """Handle cancellation request."""
        self._cancelled = True
        logger.debug("[HolderSync] Stop | Cancellation requested.")

    @log_async_operation(
        operation_name="HolderSyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def run(self, **kwargs: typing.Any) -> SyncResult:
        result = SyncResult()
        self._cancelled = False
        errors = 0

        try:
            quarter_ends = self._get_recent_quarter_ends(count=2)
            logger.info(
                f"[HolderSync] Run | Syncing quarterly snapshots: {quarter_ends}",
            )

            for qe in quarter_ends:
                if self._cancelled:
                    logger.debug("[HolderSync] Stop | Cancelled by user.")
                    break

                count = await self._sync_stk_holdernumber(qe)
                if count < 0:
                    errors += 1
                else:
                    result.added += count
                    if not self._cancelled:
                        qe_date = datetime.datetime.strptime(qe, "%Y%m%d").date()
                        await self.context.cache.update_sync_status(
                            "stk_holdernumber",
                            qe_date,
                            count,
                        )

                if errors >= _MAX_ERRORS or self._cancelled:
                    break

                count = await self._sync_top10_holders(qe)
                if count < 0:
                    errors += 1
                else:
                    result.added += count
                    if not self._cancelled:
                        qe_date = datetime.datetime.strptime(qe, "%Y%m%d").date()
                        await self.context.cache.update_sync_status(
                            "top10_holders",
                            qe_date,
                            count,
                        )

                if errors >= _MAX_ERRORS or self._cancelled:
                    break

            if errors < _MAX_ERRORS and not self._cancelled:
                count, actual_date = await self._sync_pledge_stat()
                if count < 0:
                    errors += 1
                elif count > 0:
                    result.added += count
                    await self.context.cache.update_sync_status(
                        "pledge_stat",
                        actual_date or get_now().date(),
                        count,
                    )

            if errors >= _MAX_ERRORS:
                result.status = "partial"
                result.errors.append(f"Aborted after {errors} errors")

            logger.info(
                f"[HolderSync] Run | ✅ Complete. Synced={result.added}, Errors={errors}",
            )

        except Exception as e:
            logger.error(f"[HolderSync] Run | ❌ Top-level failure: {e}", exc_info=True)
            result.status = "failed"
            result.errors.append(str(e))

        return result

    async def _sync_stk_holdernumber(self, enddate: str):
        """
        Fetch full-market stk_holdernumber snapshot by enddate.
        Returns row count on success, -1 on error.
        """
        try:
            df = await self.context.api.get_stk_holdernumber(enddate=enddate)
            if df is not None and not df.empty:
                await self.context.cache.save_holder_number(df)
                logger.debug(
                    f"[HolderSync] Table | stk_holdernumber enddate={enddate}: {len(df)} records",
                )
                return len(df)
            logger.debug(
                f"[HolderSync] Table | stk_holdernumber enddate={enddate}: no data",
            )
            return 0
        except Exception as e:
            self._log_sync_error("stk_holdernumber", enddate, e)
            return -1

    async def _sync_top10_holders(self, period: str):
        """
        Fetch top10_holders for all stocks by iterating per-stock.
        Tushare requires ts_code as a mandatory parameter for this API.

        This is the most API-intensive operation (~5500 calls per quarter).
        The dedicated slow-API rate limiter in TushareClient handles pacing.

        Returns total row count on success, -1 on error.
        """
        try:
            stock_df = await self.context.cache.get_stock_basic()
            if stock_df is None or stock_df.empty:
                logger.warning("[HolderSync] No stock list available for top10_holders sync")
                return -1

            ts_codes = stock_df["ts_code"].tolist()
            total = len(ts_codes)
            all_dfs = []
            stock_errors = 0
            consecutive_errors = 0
            rate_limit_hits = 0

            logger.info(
                f"[HolderSync] top10_holders | Starting per-stock sync: {total} stocks, period={period}",
            )

            for i, ts_code in enumerate(ts_codes):
                if self._cancelled:
                    logger.debug("[HolderSync] Stop | Cancelled during top10_holders iteration.")
                    break

                try:
                    df = await self.context.api.get_top10_holders(
                        ts_code=ts_code,
                        period=period,
                    )
                    if df is not None and not df.empty:
                        all_dfs.append(df)
                    consecutive_errors = 0
                except Exception as e:
                    stock_errors += 1
                    consecutive_errors += 1
                    err_str = str(e).lower()
                    is_rate_limit = (
                        "每分钟最多访问" in err_str or "抱歉" in err_str or "频次超限" in err_str or "429" in err_str
                    )
                    if is_rate_limit:
                        rate_limit_hits += 1

                    if stock_errors <= 3 or is_rate_limit:
                        logger.debug(
                            f"[HolderSync] top10_holders | Skip {ts_code} period={period}: {e}",
                        )
                    if consecutive_errors >= _MAX_ERRORS:
                        logger.warning(
                            f"[HolderSync] top10_holders | {consecutive_errors} consecutive errors, aborting",
                        )
                        return -1

                if (i + 1) % _PROGRESS_LOG_INTERVAL == 0:
                    elapsed_info = ""
                    api_client = getattr(self.context, "api", None)
                    if api_client:
                        slow_limiter = getattr(api_client, "_slow_api_limiters", {}).get("top10_holders")
                        if slow_limiter:
                            elapsed_info = f", rate={slow_limiter.current_rate_per_min:.0f}/min"

                    logger.info(
                        f"[HolderSync] top10_holders | Progress: {i + 1}/{total} "
                        f"({(i + 1) * 100 // total}%), "
                        f"errors={stock_errors}, rate_limits={rate_limit_hits}{elapsed_info}",
                    )

            if all_dfs:
                combined = pd.concat(all_dfs, ignore_index=True)
                await self.context.cache.save_top10_holders(combined)
                logger.info(
                    f"[HolderSync] Table | top10_holders period={period}: "
                    f"{len(combined)} records from {len(all_dfs)}/{total} stocks, "
                    f"errors={stock_errors}, rate_limits={rate_limit_hits}",
                )
                return len(combined)

            logger.debug(
                f"[HolderSync] Table | top10_holders period={period}: no data",
            )
            return 0
        except Exception as e:
            self._log_sync_error("top10_holders", period, e)
            return -1

    def _log_sync_error(self, table_name: str, date_str: str, e: Exception):
        err_str = str(e).lower()
        if "permission" in err_str or "积分" in err_str:
            logger.warning(
                f"[HolderSync] ⛔ Permission denied for {table_name}: {e}",
            )
        else:
            logger.warning(
                f"[HolderSync] Table | ⚠️ Error syncing {table_name} date={date_str}: {e}",
            )

    async def _sync_one_table(
        self,
        api_func: typing.Any,
        save_func: typing.Any,
        table_name: str,
        end_date: str | None,
    ):
        """
        Fetch a full-market snapshot for one table by end_date.
        Returns row count on success, -1 on error.
        """
        try:
            df = await api_func(end_date=end_date)
            if df is not None and not df.empty:
                await save_func(df)
                logger.debug(
                    f"[HolderSync] Table | {table_name} end_date={end_date}: {len(df)} records",
                )
                return len(df)
            logger.debug(
                f"[HolderSync] Table | {table_name} end_date={end_date}: no data",
            )
            return 0
        except Exception as e:
            err_str = str(e).lower()
            if "permission" in err_str or "积分" in err_str:
                logger.warning(
                    f"[HolderSync] ⛔ Permission denied for {table_name}: {e}",
                )
            else:
                logger.warning(
                    f"[HolderSync] Table | ⚠️ Error syncing {table_name} end_date={end_date}: {e}",
                )
            return -1

    async def _sync_pledge_stat(self):
        """
        Fetch the latest full-market pledge_stat snapshot.
        pledge_stat snapshots are keyed by end_date (typically weekly Fridays).

        Strategy: Try the most recent 4 Fridays as end_date to find a valid snapshot.
        Returns (row_count, actual_end_date) on success, (-1, None) on error.
        """
        try:
            today = get_now().date()
            days_since_friday = (today.weekday() - 4) % 7
            last_friday = today - datetime.timedelta(days=days_since_friday)

            df = None
            actual_end_date = None
            all_api_failed = True
            for attempt in range(4):
                if self._cancelled:
                    logger.debug("[HolderSync] pledge_stat | Cancelled during retry loop.")
                    return -1, None

                candidate = last_friday - datetime.timedelta(weeks=attempt)
                end_date = candidate.strftime("%Y%m%d")
                try:
                    df = await self.context.api.get_pledge_stat(end_date=end_date)
                    all_api_failed = False
                except Exception as api_err:
                    logger.debug(
                        f"[HolderSync] pledge_stat | API error for end_date={end_date}: {api_err}",
                    )
                    continue

                if df is not None and not df.empty:
                    actual_end_date = candidate
                    logger.debug(
                        f"[HolderSync] pledge_stat | Got data for end_date={end_date}",
                    )
                    break
                logger.debug(
                    f"[HolderSync] pledge_stat | No data for end_date={end_date}",
                )

            if df is not None and not df.empty:
                await self.context.cache.save_pledge_stat(df)
                logger.debug(
                    f"[HolderSync] Table | pledge_stat: {len(df)} records",
                )
                return len(df), actual_end_date

            if all_api_failed:
                logger.warning("[HolderSync] Table | ⚠️ pledge_stat: all API calls failed")
                return -1, None

            logger.debug(
                "[HolderSync] Table | pledge_stat: no data",
            )
            return 0, None
        except Exception as e:
            err_str = str(e).lower()
            if "permission" in err_str or "积分" in err_str:
                logger.warning(
                    f"[HolderSync] ⛔ Permission denied for pledge_stat: {e}",
                )
            else:
                logger.warning(f"[HolderSync] Table | ⚠️ Error syncing pledge_stat: {e}")
            return -1, None

    @staticmethod
    def _get_recent_quarter_ends(count: typing.Any = 2):
        """
        Return the most recent `count` quarter-end dates (YYYYMMDD strings)
        that have already passed, ordered newest first.
        Example (today=2026-03-02): ['20251231', '20250930']
        """
        today = get_now().date()
        quarter_ends = []
        for year in range(today.year, today.year - 2, -1):
            for month, day in [(12, 31), (9, 30), (6, 30), (3, 31)]:
                qe = datetime.date(year, month, day)
                if qe < today:
                    quarter_ends.append(qe.strftime("%Y%m%d"))
                    if len(quarter_ends) >= count:
                        return quarter_ends
        return quarter_ends
