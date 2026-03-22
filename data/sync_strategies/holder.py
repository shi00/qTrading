import datetime
import logging
import typing

from utils.log_decorators import PerfThreshold, log_async_operation

from .base import ISyncStrategy, SyncResult

logger = logging.getLogger(__name__)

# Circuit breaker: abort after this many consecutive errors
_MAX_ERRORS = 5


class HolderSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Shareholder & Pledge data using O(Quarter) approach.

    These tables (stk_holdernumber, top10_holders, pledge_stat) contain
    quarterly-disclosure data. Fetching by end_date (quarter-end snapshot)
    returns the entire market in a single paginated API call, making it
    vastly more efficient than per-stock iteration.

    Typical API call counts per sync:
      - stk_holdernumber: ~2 calls (5500 rows, paginated)
      - top10_holders:    ~9 calls (54000 rows, paginated at 6000/page)
      - pledge_stat:      ~2 calls (4100 rows, paginated at 3000/page)
      Total: ~13 API calls for 100% market coverage
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
            # Determine the latest 2 quarter-end dates to ensure coverage
            quarter_ends = self._get_recent_quarter_ends(count=2)
            logger.debug(
                f"[HolderSync] Run | Syncing quarterly snapshots: {quarter_ends}",
            )

            for qe in quarter_ends:
                if self._cancelled:
                    logger.debug("[HolderSync] Stop | Cancelled by user.")
                    break

                # --- stk_holdernumber ---
                count = await self._sync_one_table(
                    api_func=self.context.api.get_stk_holdernumber,
                    save_func=self.context.cache.save_holder_number,
                    table_name="stk_holdernumber",
                    end_date=qe,
                )
                if count < 0:
                    errors += 1
                else:
                    result.added += count
                    from datetime import datetime as dt_module

                    qe_date = dt_module.strptime(qe, "%Y%m%d").date()
                    await self.context.cache.update_sync_status(
                        "stk_holdernumber",
                        qe_date,
                        count,
                    )

                if errors >= _MAX_ERRORS or self._cancelled:
                    break

                # --- top10_holders ---
                count = await self._sync_one_table(
                    api_func=self.context.api.get_top10_holders,
                    save_func=self.context.cache.save_top10_holders,
                    table_name="top10_holders",
                    end_date=qe,
                )
                if count < 0:
                    errors += 1
                else:
                    result.added += count
                    from datetime import datetime as dt_module

                    qe_date = dt_module.strptime(qe, "%Y%m%d").date()
                    await self.context.cache.update_sync_status(
                        "top10_holders",
                        qe_date,
                        count,
                    )

                if errors >= _MAX_ERRORS or self._cancelled:
                    break

            # --- pledge_stat (independent of quarter-ends) ---
            # pledge_stat uses weekly trading dates, not quarter-ends.
            # We sync it once per run, outside the quarterly loop.
            if errors < _MAX_ERRORS and not self._cancelled:
                count = await self._sync_pledge_stat()
                if count < 0:
                    errors += 1
                else:
                    result.added += count
                    import datetime as dt_module

                    today = dt_module.date.today()
                    await self.context.cache.update_sync_status(
                        "pledge_stat",
                        today,
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
        pledge_stat snapshots are keyed by trading dates (typically weekly Fridays).
        We pass the most recent Friday as end_date to get a single snapshot,
        avoiding infinite pagination over all historical data.
        Returns row count on success, -1 on error.
        """
        try:
            # Use last Friday (or today if Friday) as end_date
            today = datetime.date.today()
            days_since_friday = (today.weekday() - 4) % 7
            last_friday = today - datetime.timedelta(days=days_since_friday)
            end_date = last_friday.strftime("%Y%m%d")

            df = await self.context.api.get_pledge_stat(end_date=end_date)
            if df is not None and not df.empty:
                await self.context.cache.save_pledge_stat(df)
                logger.debug(
                    f"[HolderSync] Table | pledge_stat end_date={end_date}: {len(df)} records",
                )
                return len(df)
            logger.debug(
                f"[HolderSync] Table | pledge_stat end_date={end_date}: no data",
            )
            return 0
        except Exception as e:
            err_str = str(e).lower()
            if "permission" in err_str or "积分" in err_str:
                logger.warning(
                    f"[HolderSync] ⛔ Permission denied for pledge_stat: {e}",
                )
            else:
                logger.warning(f"[HolderSync] Table | ⚠️ Error syncing pledge_stat: {e}")
            return -1

    @staticmethod
    def _get_recent_quarter_ends(count: typing.Any = 2):
        """
        Return the most recent `count` quarter-end dates (YYYYMMDD strings)
        that have already passed, ordered newest first.
        Example (today=2026-03-02): ['20251231', '20250930']
        """
        today = datetime.date.today()
        quarter_ends = []
        # Walk backwards from current year
        for year in range(today.year, today.year - 2, -1):
            for month, day in [(12, 31), (9, 30), (6, 30), (3, 31)]:
                qe = datetime.date(year, month, day)
                if qe < today:
                    quarter_ends.append(qe.strftime("%Y%m%d"))
                    if len(quarter_ends) >= count:
                        return quarter_ends
        return quarter_ends
