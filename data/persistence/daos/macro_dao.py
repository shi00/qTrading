import logging

import pandas as pd

from data.persistence.models import MacroEconomy, ShiborDaily, get_model_columns, get_model_pk_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class MacroDao(BaseDao):
    """DAO for Macroeconomic data (M2, CPI, PPI) and Interbank Rates (Shibor)."""

    async def save_macro_economy(self, df: pd.DataFrame):
        """
        Save Macro Economy data (M2, CPI, etc.)
        Table: macro_economy
        Note: created_at is handled by DB-level server_default, not injected here.
        """
        if df is None or df.empty:
            return 0

        cols = get_model_columns(MacroEconomy)
        pk_columns = get_model_pk_columns(MacroEconomy)

        return await self._save_upsert(
            df,
            "macro_economy",
            cols,
            pk_columns=pk_columns,
        )

    async def save_shibor_daily(self, df: pd.DataFrame):
        """
        Save Daily Shibor rates.
        Table: shibor_daily
        Tushare 'shibor' API fields: date, on, 1w, 2w, 1m, 3m, 6m, 9m, 1y
        """
        if df is None or df.empty:
            return 0

        cols = get_model_columns(ShiborDaily)
        pk_columns = get_model_pk_columns(ShiborDaily)
        available = [c for c in cols if c in df.columns]
        return await self._save_upsert(
            df,
            "shibor_daily",
            available,
            pk_columns=pk_columns,
        )

    async def get_macro_latest_date(self):
        """Get latest period in macro_economy."""
        df = await self._read_db("SELECT MAX(period) as max_date FROM macro_economy")
        if not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    async def get_shibor_latest_date(self):
        """Get latest date in shibor_daily."""
        df = await self._read_db("SELECT MAX(date) as max_date FROM shibor_daily")
        if not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    async def get_shibor_latest(self, as_of_date=None) -> pd.DataFrame:
        """
        获取Shibor利率数据。

        Args:
            as_of_date: 截止日期（含），用于历史回放场景防止前视偏差。
                        None 表示不限制（取最新一期）。

        Returns:
            DataFrame with latest shibor rates (date, on, 1w, 2w, 1m, 3m, 6m, 9m, 1y)
        """
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    'SELECT date, "on", "1w", "2w", "1m", "3m", "6m", "9m", "1y" FROM shibor_daily WHERE date <= $1 ORDER BY date DESC LIMIT 1',
                    as_of_date,
                )
            else:
                df = await self._read_db(
                    'SELECT date, "on", "1w", "2w", "1m", "3m", "6m", "9m", "1y" FROM shibor_daily ORDER BY date DESC LIMIT 1'
                )
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[MacroDao] Failed to get shibor latest: {e}")
            return pd.DataFrame()

    async def get_macro_economy_latest(self, as_of_date=None) -> pd.DataFrame:
        """
        获取宏观经济数据。

        Args:
            as_of_date: 截止日期（含），用于历史回放场景防止前视偏差。
                        None 表示不限制（取最新一期）。

        Returns:
            DataFrame with latest macro economy data (period, m2, m2_yoy, m1, m1_yoy, cpi, ppi, etc.)
        """
        try:
            if as_of_date is not None:
                df = await self._read_db(
                    "SELECT period, m2, m2_yoy, m1, m1_yoy, cpi, ppi FROM macro_economy WHERE period <= $1 ORDER BY period DESC LIMIT 1",
                    as_of_date,
                )
            else:
                df = await self._read_db(
                    "SELECT period, m2, m2_yoy, m1, m1_yoy, cpi, ppi FROM macro_economy ORDER BY period DESC LIMIT 1"
                )
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[MacroDao] Failed to get macro economy latest: {e}")
            return pd.DataFrame()
