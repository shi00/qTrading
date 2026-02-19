import datetime
import logging
from .base import ISyncStrategy, SyncResult
from data.daos.holder_dao import HolderDao
from utils.thread_pool import ThreadPoolManager, TaskType
from utils.log_decorators import log_async_operation

logger = logging.getLogger(__name__)

# How many days before holder data is considered stale
_STALE_THRESHOLD_DAYS = 90
# Maximum stocks to process per run to avoid API exhaustion
_BATCH_SIZE = 50
# Circuit breaker: abort after this many errors
_MAX_ERRORS = 10
# Suppress per-stock error logs after this count
_ERROR_LOG_THRESHOLD = 3


class HolderSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Shareholder data.
    Uses sparse update: only re-fetches stocks whose data is stale (> 90 days old).
    """

    def __init__(self, context):
        super().__init__(context)
        self.dao = HolderDao(context.cache.engine)
        self._cancelled = False

    async def cancel(self):
        """Handle cancellation request."""
        self._cancelled = True
        logger.info("[HolderSync] Cancellation requested.")

    @log_async_operation(operation_name="HolderSyncStrategy.run")
    async def run(self, **kwargs) -> SyncResult:
        result = SyncResult()
        self._cancelled = False

        try:
            stocks = await self.context.cache.get_stock_basic()
            if stocks is None or stocks.empty:
                return result

            ts_codes = stocks['ts_code'].tolist()
            coverage = await self.dao.check_holder_data_coverage(ts_codes)
            stale_stocks = self._find_stale_stocks(ts_codes, coverage)

            logger.info(f"[HolderSync] Found {len(stale_stocks)} stocks needing update")
            if not stale_stocks:
                return result

            completed, errors = await self._fetch_batch(stale_stocks[:_BATCH_SIZE], result)
            result.added = completed
            logger.info(f"[HolderSync] Completed: {completed}/{min(len(stale_stocks), _BATCH_SIZE)}, Errors: {errors}")

        except Exception as e:
            logger.error(f"[HolderSync] Failed: {e}", exc_info=True)
            result.status = "failed"
            result.errors.append(str(e))

        return result

    @staticmethod
    def _find_stale_stocks(ts_codes, coverage):
        """Identify stocks with stale or missing holder data."""
        today = datetime.datetime.now()
        stale = []
        for code in ts_codes:
            last_date_str = coverage.get(code)
            if not last_date_str:
                stale.append(code)
                continue
            try:
                last_dt = datetime.datetime.strptime(str(last_date_str), '%Y%m%d')
                if (today - last_dt).days > _STALE_THRESHOLD_DAYS:
                    stale.append(code)
            except ValueError:
                stale.append(code)
        return stale

    async def _fetch_batch(self, batch, result):
        """Fetch holder data for a batch of stocks. Returns (completed, errors)."""
        completed = 0
        errors = 0

        for code in batch:
            if self._cancelled:
                logger.info("[HolderSync] Cancelled by user.")
                break

            try:
                df_num = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    self.context.api.get_stk_holdernumber,
                    ts_code=code
                )
                if df_num is not None and not df_num.empty:
                    await self.dao.save_holder_number(df_num)
                    completed += 1

                # Also sync top10_holders for the same stock
                try:
                    df_top10 = await ThreadPoolManager().run_async(
                        TaskType.IO,
                        self.context.api.get_top10_holders,
                        ts_code=code
                    )
                    if df_top10 is not None and not df_top10.empty:
                        await self.dao.save_top10_holders(df_top10)
                except Exception as e_top10:
                    logger.debug(f"[HolderSync] top10_holders failed for {code}: {e_top10}")
            except Exception as e:
                errors += 1
                if errors <= _ERROR_LOG_THRESHOLD:
                    logger.warning(f"[HolderSync] Error for {code}: {e}")
                elif errors == _ERROR_LOG_THRESHOLD + 1:
                    logger.warning("[HolderSync] Suppressing further per-stock error logs...")
                if errors >= _MAX_ERRORS:
                    logger.error("[HolderSync] Too many errors, aborting batch.")
                    result.errors.append(f"Aborted after {errors} errors")
                    break

        return completed, errors
