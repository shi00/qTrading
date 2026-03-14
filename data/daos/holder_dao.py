import logging

from data.daos.base_dao import BaseDao

logger = logging.getLogger(__name__)


class HolderDao(BaseDao):
    """DAO for Shareholder Data (Chip Concentration, Institutional Holdings)."""

    async def save_holder_number(self, df):
        """Save Stock Holder Number. Table: stk_holdernumber"""
        if df is None or df.empty:
            return 0
        columns = [
            "ts_code",
            "end_date",
            "ann_date",
            "holder_num",
            "holder_num_change",
            "holder_num_ratio",
        ]
        return await self._save_upsert(
            df, "stk_holdernumber", columns, pk_columns=["ts_code", "end_date"],
        )

    async def save_top10_holders(self, df):
        """Save Top 10 Holders. Table: top10_holders"""
        if df is None or df.empty:
            return 0
        columns = [
            "ts_code",
            "end_date",
            "ann_date",
            "holder_name",
            "hold_amount",
            "hold_ratio",
            "holder_type",
        ]
        return await self._save_upsert(
            df,
            "top10_holders",
            columns,
            pk_columns=["ts_code", "end_date", "holder_name"],
        )

    async def get_latest_holder_date(self, ts_code):
        """Get latest end_date for a specific stock."""
        sql = (
            "SELECT MAX(end_date) as max_date FROM stk_holdernumber WHERE ts_code = $1"
        )
        df = await self._read_db(sql, (ts_code,))
        if not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    async def check_holder_data_coverage(self, ts_code_list):
        """
        Get last update date for all stocks with holder data.
        Returns {ts_code: last_end_date} dict.
        """
        if not ts_code_list:
            return {}

        sql = """
        SELECT ts_code, MAX(end_date) as last_date 
        FROM stk_holdernumber 
        GROUP BY ts_code
        """
        df = await self._read_db(sql)
        if df.empty:
            return {}
        return dict(zip(df["ts_code"], df["last_date"]))
