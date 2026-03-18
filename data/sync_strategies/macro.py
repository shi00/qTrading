import datetime
import logging

from data.constants import MAJOR_INDICES
from data.daos.macro_dao import MacroDao
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date

from .base import ISyncStrategy, SyncResult

logger = logging.getLogger(__name__)

# Default lookback removed in favor of dynamic config.
# Shibor skip threshold: start from next day after latest
_SHIBOR_RESUME_OFFSET_DAYS = 1
# Fallback lookback when date parsing fails
_SHIBOR_FALLBACK_LOOKBACK_DAYS = 365


class MacroSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Macroeconomic data (M2, CPI, PPI, Shibor).
    Runs efficiently by checking the latest available data date.
    """

    _M2_COLUMNS = ["period", "m2", "m2_yoy", "m1", "m1_yoy", "m0", "m0_yoy"]

    def __init__(self, context):
        super().__init__(context)
        self.dao = MacroDao(context.cache.engine)
        self._cancelled = False

    async def cancel(self):
        """Handle cancellation request."""
        self._cancelled = True
        logger.debug("[MacroSync] Stop | Cancellation requested.")

    @log_async_operation(
        operation_name="MacroSyncStrategy.run", threshold_ms=PerfThreshold.DB_BULK_IO,
    )
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
            logger.info(
                f"[MacroSync] Run | ✅ Complete. Added={result.added}, Errors={len(result.errors)}",
            )
        return result

    async def _sync_macro_monthly(self, result):
        """
        Fetch M2, CPI, PPI and merge into a single DataFrame before save.
        Merging in-memory avoids INSERT OR REPLACE wiping other columns.
        """
        try:
            latest = await self.dao.get_macro_latest_date()

            df_m2 = await self.context.api.get_macro_data("cn_m", start_m=latest)
            df_cpi = await self.context.api.get_macro_data("cn_cpi", start_m=latest)
            df_ppi = await self.context.api.get_macro_data("cn_ppi", start_m=latest)

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
            df_m2 = df_m2.rename(columns={"month": "period"})
            available = [c for c in cls._M2_COLUMNS if c in df_m2.columns]
            merged = df_m2[available].copy()

        merged = cls._merge_indicator(merged, df_cpi, "nt_val", "cpi")
        merged = cls._merge_indicator(merged, df_ppi, "ppi_yoy", "ppi")

        return merged

    @staticmethod
    def _merge_indicator(merged, df, source_col, target_col):
        """Merge a single indicator DataFrame into the merged result."""
        if df is None or df.empty:
            return merged

        df = df.rename(columns={"month": "period", source_col: target_col})
        if target_col not in df.columns:
            return merged

        indicator = df[["period", target_col]].drop_duplicates(subset="period")
        if merged is not None:
            return merged.merge(indicator, on="period", how="outer")
        return indicator

    async def _sync_shibor_daily(self, result):
        try:
            latest = await self.dao.get_shibor_latest_date()
            today = get_now().date()

            if not latest:
                from utils.config_handler import ConfigHandler

                years = ConfigHandler.get_init_history_years()
                rough_start_date = get_now().date() - datetime.timedelta(days=int(250 * years * 2.0))
                all_dates = await self.context.processor.get_trade_dates(
                    start_date=rough_start_date, end_date=today,
                )
                start_date = (
                    all_dates[-(250 * years)]
                    if len(all_dates) >= (250 * years)
                    else (
                        all_dates[0]
                        if all_dates
                        else (get_now().date() - datetime.timedelta(days=365 * years))
                    )
                )
            else:
                try:
                    last_dt = parse_date(latest)
                    start_date = last_dt.date() + datetime.timedelta(days=_SHIBOR_RESUME_OFFSET_DAYS)
                except ValueError:
                    logger.warning(
                        f"[MacroSync] Invalid latest date '{latest}', fallback to 1 year.",
                    )
                    start_date = (
                        get_now().date()
                        - datetime.timedelta(days=_SHIBOR_FALLBACK_LOOKBACK_DAYS)
                    )

            if start_date > today:
                logger.debug("[MacroSync] Shibor already up to date.")
                return

            start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, 'strftime') else str(start_date)
            end_str = today.strftime("%Y%m%d") if hasattr(today, 'strftime') else str(today)
            df = await self.context.api.get_shibor(
                start_date=start_str, end_date=end_str,
            )
            if df is not None and not df.empty:
                count = await self.dao.save_shibor_daily(df)
                result.added += count if count else 0
                logger.debug(f"[MacroSync] Shibor | Saved {count} records")

        except Exception as e:
            logger.warning(f"[MacroSync] Shibor | ⚠️ Error: {e}", exc_info=True)
            result.errors.append(f"Shibor: {e}")

    async def _sync_index_weights(self, result):
        try:
            market_dao = self.context.cache.market_dao
            latest = await market_dao.get_latest_index_weight_date()

            today = get_now()
            today_date = today.date()
            should_update = False

            if not latest:
                should_update = True
                from utils.config_handler import ConfigHandler

                years = ConfigHandler.get_init_history_years()
                rough_start_date = today_date - datetime.timedelta(days=int(250 * years * 2.0))
                all_dates = await self.context.processor.get_trade_dates(
                    start_date=rough_start_date, end_date=today_date,
                )
                start_date = (
                    all_dates[-(250 * years)]
                    if len(all_dates) >= (250 * years)
                    else (
                        all_dates[0]
                        if all_dates
                        else (today_date - datetime.timedelta(days=365 * years))
                    )
                )
            else:
                last_dt = parse_date(latest)
                if (today - last_dt).days > 30:
                    should_update = True
                    start_date = last_dt.date() + datetime.timedelta(days=1)
                else:
                    start_date = today_date

            if not should_update:
                logger.debug("[MacroSync] Index weights up to date (monthly).")
                return

            start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, 'strftime') else str(start_date)
            end_date = today.strftime("%Y%m%d")
            logger.debug(
                f"[MacroSync] IndexWeight | Syncing {len(MAJOR_INDICES)} indices...",
            )

            for idx_code in MAJOR_INDICES:
                if self._cancelled:
                    break

                # Fetch for this index
                # Note: index_weight returns monthly weights usually.
                # Be careful not to fetch too much history unless needed.
                # If backfilling, maybe just get latest?
                # Tushare: index_weight(index_code='399300.SZ', start_date='20180901', end_date='20180930')

                try:
                    df = await self.context.api.get_index_weight(
                        index_code=idx_code, start_date=start_str, end_date=end_date,
                    )

                    if df is not None and not df.empty:
                        count = await self.context.cache.save_index_weights(df)
                        result.added += count if count else 0
                except Exception as e:
                    logger.warning(
                        f"[MacroSync] IndexWeight | ⚠️ Failed {idx_code}: {e}",
                    )
                    # Continue to next index

        except Exception as e:
            logger.warning(
                f"[MacroSync] IndexWeight | ⚠️ Flow-level error: {e}", exc_info=True,
            )
            result.errors.append(f"IndexWeight: {e}")
