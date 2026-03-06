import datetime
import logging
from .base import ISyncStrategy, SyncResult
from data.daos.macro_dao import MacroDao
from data.constants import MAJOR_INDICES
from utils.log_decorators import log_async_operation, PerfThreshold
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

# Default lookback for Shibor history when no prior data exists
_SHIBOR_DEFAULT_LOOKBACK_DAYS = 365 * 3
# Shibor skip threshold: start from next day after latest
_SHIBOR_RESUME_OFFSET_DAYS = 1
# Fallback lookback when date parsing fails
_SHIBOR_FALLBACK_LOOKBACK_DAYS = 365


class MacroSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Macroeconomic data (M2, CPI, PPI, Shibor).
    Runs efficiently by checking the latest available data date.
    """

    _M2_COLUMNS = ['period', 'm2', 'm2_yoy', 'm1', 'm1_yoy', 'm0', 'm0_yoy']

    def __init__(self, context):
        super().__init__(context)
        self.dao = MacroDao(context.cache.engine)
        self._cancelled = False

    async def cancel(self):
        """Handle cancellation request."""
        self._cancelled = True
        logger.debug("[MacroSync] Stop | Cancellation requested.")

    @log_async_operation(operation_name="MacroSyncStrategy.run", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def run(self, **kwargs) -> SyncResult:
        result = SyncResult()
        self._cancelled = False

        try:
            await self._sync_macro_monthly(result)
            if self._cancelled:
                return result
            await self._sync_shibor_daily(result)
            if self._cancelled:
                return result
            await self._sync_index_weights(result)
        except Exception as e:
            logger.error(f"[MacroSync] Failed: {e}", exc_info=True)
            result.status = "failed"
            result.errors.append(str(e))
        if result.status != "failed":
            logger.info(f"[MacroSync] Run | ✅ Complete. Added={result.added}, Errors={len(result.errors)}")
        return result
    async def _sync_macro_monthly(self, result):
        """
        Fetch M2, CPI, PPI and merge into a single DataFrame before save.
        Merging in-memory avoids INSERT OR REPLACE wiping other columns.
        """
        try:
            latest = await self.dao.get_macro_latest_date()

            df_m2 = await self.context.api.get_macro_data('cn_m', start_m=latest)
            df_cpi = await self.context.api.get_macro_data('cn_cpi', start_m=latest)
            df_ppi = await self.context.api.get_macro_data('cn_ppi', start_m=latest)

            merged = self._merge_macro_data(df_m2, df_cpi, df_ppi)

            if merged is not None and not merged.empty:
                count = await self.dao.save_macro_economy(merged)
                result.added += count if count else 0
                logger.debug(f"[MacroSync] Monthly | Saved {count} macro records")

        except Exception as e:
            logger.warning(f"[MacroSync] Monthly | ⚠️ Error: {e}", exc_info=True)
            result.errors.append(f"Macro Monthly: {e}")

    @classmethod
    def _merge_macro_data(cls, df_m2, df_cpi, df_ppi):
        """Merge M2/CPI/PPI DataFrames on period column."""
        merged = None

        if df_m2 is not None and not df_m2.empty:
            df_m2 = df_m2.rename(columns={'month': 'period'})
            available = [c for c in cls._M2_COLUMNS if c in df_m2.columns]
            merged = df_m2[available].copy()

        merged = cls._merge_indicator(merged, df_cpi, 'nt_val', 'cpi')
        merged = cls._merge_indicator(merged, df_ppi, 'ppi_yoy', 'ppi')

        return merged

    @staticmethod
    def _merge_indicator(merged, df, source_col, target_col):
        """Merge a single indicator DataFrame into the merged result."""
        if df is None or df.empty:
            return merged

        df = df.rename(columns={'month': 'period', source_col: target_col})
        if target_col not in df.columns:
            return merged

        indicator = df[['period', target_col]].drop_duplicates(subset='period')
        if merged is not None:
            return merged.merge(indicator, on='period', how='outer')
        return indicator

    async def _sync_shibor_daily(self, result):
        """Fetch and save daily Shibor rates."""
        try:
            latest = await self.dao.get_shibor_latest_date()
            today = get_now().strftime('%Y%m%d')

            start_date = self._compute_shibor_start(latest)
            if start_date > today:
                logger.debug("[MacroSync] Shibor already up to date.")
                return

            df = await self.context.api.get_shibor(start_date=start_date, end_date=today)
            if df is not None and not df.empty:
                count = await self.dao.save_shibor_daily(df)
                result.added += count if count else 0
                logger.debug(f"[MacroSync] Shibor | Saved {count} records")

        except Exception as e:
            logger.warning(f"[MacroSync] Shibor | ⚠️ Error: {e}", exc_info=True)
            result.errors.append(f"Shibor: {e}")

    @staticmethod
    def _compute_shibor_start(latest):
        """Compute start_date for Shibor sync based on last available date."""
        if not latest:
            return (get_now() - datetime.timedelta(days=_SHIBOR_DEFAULT_LOOKBACK_DAYS)).strftime('%Y%m%d')
        try:
            last_dt = datetime.datetime.strptime(str(latest), '%Y%m%d')
            return (last_dt + datetime.timedelta(days=_SHIBOR_RESUME_OFFSET_DAYS)).strftime('%Y%m%d')
        except ValueError:
            return (get_now() - datetime.timedelta(days=_SHIBOR_FALLBACK_LOOKBACK_DAYS)).strftime('%Y%m%d')

    async def _sync_index_weights(self, result):
        """Sync Index Weights for Major Indices (Monthly)."""
        try:
            # Access MarketDao via cache manager
            market_dao = self.context.cache.market_dao
            latest = await market_dao.get_latest_index_weight_date()
            
            # Simple monthly check: if latest is > 30 days ago, fetch "current" weights
            # Tushare index_weight: trade_date (transcation date), start_date, end_date
            # We just fetch by trade_date range or just 'latest' snapshot logic?
            # Tushare index_weight(index_code, start_date, end_date)
            
            today = get_now()
            should_update = False
            
            if not latest:
                should_update = True
                start_date = (today - datetime.timedelta(days=365)).strftime('%Y%m%d') # Backfill 1 year
            else:
                last_dt = datetime.datetime.strptime(str(latest),('%Y%m%d'))
                if (today - last_dt).days > 30:
                    should_update = True
                    start_date = (last_dt + datetime.timedelta(days=1)).strftime('%Y%m%d')
                else:
                    start_date = today.strftime('%Y%m%d')

            if not should_update:
                logger.debug("[MacroSync] Index weights up to date (monthly).")
                return

            end_date = today.strftime('%Y%m%d')
            logger.debug(f"[MacroSync] IndexWeight | Syncing {len(MAJOR_INDICES)} indices...")

            for idx_code in MAJOR_INDICES:
                if self._cancelled: break
                
                # Fetch for this index
                # Note: index_weight returns monthly weights usually. 
                # Be careful not to fetch too much history unless needed.
                # If backfilling, maybe just get latest?
                # Tushare: index_weight(index_code='399300.SZ', start_date='20180901', end_date='20180930')
                
                try:
                    df = await self.context.api.get_index_weight(
                        index_code=idx_code, start_date=start_date, end_date=end_date
                    )
                    
                    if df is not None and not df.empty:
                        count = await self.context.cache.save_index_weights(df)
                        result.added += count if count else 0
                except Exception as e:
                    logger.warning(f"[MacroSync] IndexWeight | ⚠️ Failed {idx_code}: {e}")
                    # Continue to next index

        except Exception as e:
            logger.warning(f"[MacroSync] IndexWeight | ⚠️ Flow-level error: {e}", exc_info=True)
            result.errors.append(f"IndexWeight: {e}")
