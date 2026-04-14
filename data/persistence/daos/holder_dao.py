import logging

import pandas as pd

from data.persistence.models import StkHoldernumber, Top10Holders, get_model_columns, get_model_pk_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class HolderDao(BaseDao):
    """DAO for Shareholder Data (Chip Concentration, Institutional Holdings)."""

    async def save_holder_number(self, df: pd.DataFrame):
        """Save Stock Holder Number. Table: stk_holdernumber"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(StkHoldernumber)
        pk_columns = get_model_pk_columns(StkHoldernumber)
        return await self._save_upsert(
            df,
            "stk_holdernumber",
            cols,
            pk_columns=pk_columns,
        )

    async def save_top10_holders(self, df: pd.DataFrame):
        """Save Top 10 Holders. Table: top10_holders"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(Top10Holders)
        pk_columns = get_model_pk_columns(Top10Holders)
        return await self._save_upsert(
            df,
            "top10_holders",
            cols,
            pk_columns=pk_columns,
        )

    async def get_top10_holders(self, ts_code: str) -> pd.DataFrame:
        """
        获取前十大股东。

        Args:
            ts_code: 股票代码

        Returns:
            DataFrame with top 10 holders
        """
        try:
            df = await self._read_db(
                """
                SELECT ts_code, end_date, ann_date, holder_name, hold_amount,
                       hold_ratio, hold_float_ratio, hold_change, holder_type
                FROM top10_holders
                WHERE ts_code = $1
                ORDER BY end_date DESC, hold_ratio DESC
                LIMIT 20
                """,
                (ts_code,),
            )
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[HolderDao] Failed to get top10 holders for {ts_code}: {e}")
            return pd.DataFrame()

    async def get_stk_holdernumber(self, ts_code: str) -> pd.DataFrame:
        """
        获取股东人数变化。

        Args:
            ts_code: 股票代码

        Returns:
            DataFrame with shareholder numbers
        """
        try:
            df = await self._read_db(
                """
                SELECT ts_code, end_date, ann_date, holder_num,
                       holder_num_change, holder_num_ratio
                FROM stk_holdernumber
                WHERE ts_code = $1
                ORDER BY end_date DESC
                LIMIT 5
                """,
                (ts_code,),
            )
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[HolderDao] Failed to get holder number for {ts_code}: {e}")
            return pd.DataFrame()

    async def get_top10_holders_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        批量获取前十大股东。

        Args:
            ts_codes: 股票代码列表

        Returns:
            DataFrame with top 10 holders for all codes
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ",".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT DISTINCT ON (ts_code, end_date)
                    ts_code, end_date, holder_name, hold_ratio
                FROM top10_holders
                WHERE ts_code IN ({placeholders})
                ORDER BY ts_code, end_date DESC, hold_ratio DESC
            """

            df = await self._read_db(sql, ts_codes)
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[HolderDao] Failed to get top10 holders batch: {e}")
            return pd.DataFrame()

    async def get_stk_holdernumber_batch(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        批量获取股东人数变化。

        Args:
            ts_codes: 股票代码列表

        Returns:
            DataFrame with shareholder numbers for all codes
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            placeholders = ",".join([f"${i + 1}" for i in range(len(ts_codes))])
            sql = f"""
                SELECT ts_code, end_date, ann_date, holder_num,
                       holder_num_change, holder_num_ratio
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) as rn
                    FROM stk_holdernumber
                    WHERE ts_code IN ({placeholders})
                ) sub
                WHERE rn <= 5
                ORDER BY ts_code, end_date DESC
            """

            df = await self._read_db(sql, ts_codes)
            if df is not None and not df.empty:
                df = df.drop(columns=["rn"])
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            logger.warning(f"[HolderDao] Failed to get holder number batch: {e}")
            return pd.DataFrame()
