import datetime
import logging
import typing

import pandas as pd

from data.constants import MAJOR_INDICES
from data.persistence.daos.macro_dao import MacroDao
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.time_utils import get_now, parse_date

from .base import ISyncStrategy, SyncResult

logger = logging.getLogger(__name__)

# Default lookback removed in favor of dynamic config.
# Shibor skip threshold: start from next day after latest
_SHIBOR_RESUME_OFFSET_DAYS = 1
# Fallback lookback when date parsing fails
_SHIBOR_FALLBACK_LOOKBACK_DAYS = 365


def _parse_period(p: typing.Any):
    """Parse Tushare macro period format (YYYYMM) to standard date string.

    Tushare macro APIs (cn_m, cn_cpi, cn_ppi) return period as 'YYYYMM' string.
    This function converts it to 'YYYY-MM-01' format for proper date parsing.

    Args:
        p: Period value (string, None, or NaN)

    Returns:
        str: 'YYYY-MM-01' format string, or original value if not YYYYMM format
        None: If input is NaN/None
    """
    if pd.isna(p):
        return None
    p_str = str(p).strip()
    if len(p_str) == 6 and p_str.isdigit():
        return f"{p_str[:4]}-{p_str[4:]}-01"
    return p_str


class MacroSyncStrategy(ISyncStrategy):
    """
    Strategy for syncing Macroeconomic data (M2, CPI, PPI, Shibor).
    Runs efficiently by checking the latest available data date.
    """

    _M2_COLUMNS = ["period", "m2", "m2_yoy", "m1", "m1_yoy", "m0", "m0_yoy"]

    def __init__(self, context: typing.Any):
        super().__init__(context)
        self.dao = MacroDao(context.cache.engine)

    async def _get_effective_trade_date(self) -> datetime.date:
        """Prefer the latest closed trade date for default sync windows."""
        try:
            trade_date = await self.context.processor.trade_calendar.get_latest_trade_date()  # type: ignore[union-attr]
            if trade_date is None:
                logger.warning("[MacroSync] get_latest_trade_date returned None, falling back to today.")
            elif isinstance(trade_date, datetime.datetime):
                return trade_date.date()
            elif isinstance(trade_date, datetime.date):
                return trade_date
            elif trade_date:
                parsed = parse_date(str(trade_date))
                return parsed.date() if hasattr(parsed, "date") else parsed
        except Exception as e:
            logger.debug(f"[MacroSync] Effective trade date fallback: {e}")
        return get_now().date()

    @log_async_operation(
        operation_name="MacroSyncStrategy.run",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def run(self, **kwargs: typing.Any) -> SyncResult:
        result = SyncResult()
        self._cancelled = False

        try:
            await self._sync_macro_monthly(result)
            if self._check_cancelled(result):
                return result
            await self._sync_shibor_daily(result)
            if self._check_cancelled(result):
                return result
            await self._sync_index_weights(result)
        except Exception as e:
            logger.error(f"[MacroSync] Failed: {e}", exc_info=True)
            result.status = "failed"
            result.errors.append(str(e))
        if self._cancelled and result.status not in ("failed", "cancelled"):
            result.status = "cancelled"
        if result.status == "cancelled":
            logger.info(
                f"[MacroSync] Run | ⚠️ Cancelled. Added={result.added}, Errors={len(result.errors)}",
            )
        elif result.status != "failed":
            logger.info(
                f"[MacroSync] Run | ✅ Complete. Added={result.added}, Errors={len(result.errors)}",
            )
        return result

    async def _sync_macro_monthly(self, result: typing.Any):
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
                latest_period = merged["period"].max() if "period" in merged.columns else get_now().date()
                if isinstance(latest_period, str):
                    if len(latest_period) == 6:
                        latest_period = parse_date(latest_period, "%Y%m").date()
                    else:
                        latest_period = parse_date(latest_period).date()
                await self.context.cache.update_sync_status(
                    "macro_economy",
                    latest_period,
                    count or 0,
                )

        except Exception as e:
            logger.warning(f"[MacroSync] Monthly | ⚠️ Error: {e}", exc_info=True)
            result.errors.append(f"Macro Monthly: {e}")

    @classmethod
    def _merge_macro_data(cls, df_m2: typing.Any, df_cpi: typing.Any, df_ppi: typing.Any):
        """
        Merge M2/CPI/PPI DataFrames on period column.

        Note: Column renaming is handled by TushareClient._COLUMN_RENAMES:
        - cn_m: month -> period
        - cn_cpi: month -> period, nt_val -> cpi
        - cn_ppi: month -> period, ppi_yoy -> ppi
        """
        merged = None

        if df_m2 is not None and not df_m2.empty:
            available = [c for c in cls._M2_COLUMNS if c in df_m2.columns]
            merged = df_m2[available].copy()

        merged = cls._merge_indicator(merged, df_cpi, "cpi")
        merged = cls._merge_indicator(merged, df_ppi, "ppi")

        if merged is not None and not merged.empty:
            if "period" not in merged.columns:
                logger.warning("[MacroSync] _merge_macro_data | 'period' column missing after merge, returning None")
                return None

            # Tushare macro APIs (cn_m, cn_cpi, cn_ppi) return period as 'YYYYMM' string.
            # base_dao.py's pd.to_datetime(format='mixed') parses 'YYYYMM' as NaT.
            # Here we ensure it's either cleanly parsed or dropped if completely invalid.
            merged["period"] = merged["period"].apply(_parse_period)
            merged["period"] = pd.to_datetime(merged["period"], format="mixed", errors="coerce").dt.date
            merged = merged.dropna(subset=["period"])

        return merged

    @staticmethod
    def _merge_indicator(merged: typing.Any, df: pd.DataFrame, target_col: typing.Any):
        """
        Merge a single indicator DataFrame into the merged result.

        Args:
            merged: Existing merged DataFrame or None
            df: Indicator DataFrame (columns already renamed by TushareClient._COLUMN_RENAMES)
            target_col: Target column name (e.g., 'cpi', 'ppi')
        """
        if df is None or df.empty:
            return merged

        if target_col not in df.columns:
            logger.warning(f"[MacroSync] _merge_indicator | '{target_col}' column not found in data, skipping merge")
            return merged
        if "period" not in df.columns:
            logger.warning(
                f"[MacroSync] _merge_indicator | 'period' column not found in {target_col} data, skipping merge"
            )
            return merged

        indicator = df[["period", target_col]].drop_duplicates(subset=["period"])  # type: ignore[untyped]
        if merged is not None:
            return merged.merge(indicator, on="period", how="outer")
        return indicator

    async def _sync_shibor_daily(self, result: typing.Any):
        try:
            latest = await self.dao.get_shibor_latest_date()
            today = await self._get_effective_trade_date()

            if not latest:
                from utils.config_handler import ConfigHandler

                years = ConfigHandler.get_init_history_years()
                rough_start_date = today - datetime.timedelta(days=int(250 * years * 2.0))
                all_dates = await self.context.processor.trade_calendar.get_trade_dates(  # type: ignore[union-attr]
                    start_date=rough_start_date,
                    end_date=today,
                )
                start_date = (
                    all_dates[-(250 * years)]
                    if len(all_dates) >= (250 * years)
                    else (all_dates[0] if all_dates else (today - datetime.timedelta(days=365 * years)))
                )
            else:
                try:
                    last_dt = parse_date(latest)
                    start_date = last_dt.date() + datetime.timedelta(days=_SHIBOR_RESUME_OFFSET_DAYS)
                except ValueError:
                    logger.warning(
                        f"[MacroSync] Invalid latest date '{latest}', fallback to 1 year.",
                    )
                    start_date = today - datetime.timedelta(days=_SHIBOR_FALLBACK_LOOKBACK_DAYS)

            if start_date > today:
                logger.debug("[MacroSync] Shibor already up to date.")
                return

            start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, "strftime") else str(start_date)
            end_str = today.strftime("%Y%m%d") if hasattr(today, "strftime") else str(today)
            df = await self.context.api.get_shibor(
                start_date=start_str,
                end_date=end_str,
            )
            if df is not None and not df.empty:
                count = await self.dao.save_shibor_daily(df)
                result.added += count if count else 0
                logger.debug(f"[MacroSync] Shibor | Saved {count} records")
                await self.context.cache.update_sync_status(
                    "shibor_daily",
                    today,
                    count or 0,
                )

        except Exception as e:
            logger.warning(f"[MacroSync] Shibor | ⚠️ Error: {e}", exc_info=True)
            result.errors.append(f"Shibor: {e}")

    async def _sync_index_weights(self, result: typing.Any):
        try:
            market_dao = self.context.cache.market_dao
            latest = await market_dao.get_latest_index_weight_date()

            today_date = await self._get_effective_trade_date()
            should_update = False

            if not latest:
                should_update = True
                from utils.config_handler import ConfigHandler

                years = ConfigHandler.get_init_history_years()
                rough_start_date = today_date - datetime.timedelta(days=int(250 * years * 2.0))
                all_dates = await self.context.processor.trade_calendar.get_trade_dates(  # type: ignore[union-attr]
                    start_date=rough_start_date,
                    end_date=today_date,
                )
                start_date = (
                    all_dates[-(250 * years)]
                    if len(all_dates) >= (250 * years)
                    else (all_dates[0] if all_dates else (today_date - datetime.timedelta(days=365 * years)))
                )
            else:
                last_dt = parse_date(latest)
                last_date = last_dt.date() if hasattr(last_dt, "date") else last_dt
                if (today_date - last_date).days > 30:
                    should_update = True
                    start_date = last_date + datetime.timedelta(days=1)
                else:
                    start_date = today_date

            if not should_update:
                logger.debug("[MacroSync] Index weights up to date (monthly).")
                return

            start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, "strftime") else str(start_date)
            end_date = today_date.strftime("%Y%m%d")
            logger.debug(
                f"[MacroSync] IndexWeight | Syncing {len(MAJOR_INDICES)} indices...",
            )

            iw_saved = 0
            for idx_code in MAJOR_INDICES:
                if self._cancelled:
                    break

                try:
                    df = await self.context.api.get_index_weight(
                        index_code=idx_code,
                        start_date=start_str,
                        end_date=end_date,
                    )

                    if df is not None and not df.empty:
                        count = await self.context.cache.save_index_weights(df)
                        if count:
                            iw_saved += count
                            result.added += count
                except Exception as e:
                    logger.warning(
                        f"[MacroSync] IndexWeight | ⚠️ Failed {idx_code}: {e}",
                    )

            await self.context.cache.update_sync_status(
                "index_weight",
                today_date,
                iw_saved,
            )
            logger.debug(f"[MacroSync] IndexWeight | Total: {iw_saved} records")

        except Exception as e:
            logger.warning(
                f"[MacroSync] IndexWeight | ⚠️ Flow-level error: {e}",
                exc_info=True,
            )
            result.errors.append(f"IndexWeight: {e}")
