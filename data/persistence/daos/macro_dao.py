import logging

import pandas as pd

from data.persistence.daos.base_dao import BaseDao

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

        columns = [
            "period",
            "m2",
            "m2_yoy",
            "m1",
            "m1_yoy",
            "m0",
            "m0_yoy",
            "cpi",
            "ppi",
        ]

        return await self._save_upsert(
            df,
            "macro_economy",
            columns,
            pk_columns=["period"],
        )

    async def save_shibor_daily(self, df: pd.DataFrame):
        """
        Save Daily Shibor rates.
        Table: shibor_daily
        Tushare 'shibor' API fields: date, on, 1w, 2w, 1m, 3m, 6m, 9m, 1y
        """
        if df is None or df.empty:
            return 0

        # Tushare shibor API columns (schema must match exactly)
        columns = ["date", "on", "1w", "2w", "1m", "3m", "6m", "9m", "1y"]
        available = [c for c in columns if c in df.columns]
        return await self._save_upsert(
            df,
            "shibor_daily",
            available,
            pk_columns=["date"],
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
