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


