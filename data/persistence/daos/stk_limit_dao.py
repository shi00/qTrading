"""DAO for stk_limit (Daily Limit Up/Down Price).

Phase 2G §3.2：stk_limit 涨跌停价格，仅数据层，不注入 AI。
"""

import logging

import pandas as pd
import sqlalchemy as sa

from data.persistence.models import StkLimit, get_model_columns, get_model_pk_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class StkLimitDao(BaseDao):
    """DAO for stk_limit table (每日涨跌停价格)."""

    async def save_stk_limit(self, df: pd.DataFrame):
        """UPSERT stk_limit rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        # R17: Tushare API 字段名为 "limit"（SQL 保留字），数据库列名映射为 "limit_type"。
        # _save_upsert 按 get_model_columns 返回的数据库列名从 df 取列，需在写入前重命名。
        if "limit" in df.columns and "limit_type" not in df.columns:
            df = df.rename(columns={"limit": "limit_type"})
        cols = get_model_columns(StkLimit)
        pk_columns = get_model_pk_columns(StkLimit)
        return await self._save_upsert(
            df,
            "stk_limit",
            cols,
            pk_columns=pk_columns,
        )

    async def get_stk_limit_range(self, start_date: str, end_date: str) -> pd.DataFrame | None:
        """区间涨跌停价格（ts_code/trade_date/up_limit/down_limit），供回测撮合层按执行价判定可成交性。

        涨跌停价是名义价，与 raw_open/raw_close 同口径；stk_limit 缺失时由调用方
        _enrich_limit_status 显式告警（limit_data_absent），不做静默回退。
        """
        stmt = sa.select(StkLimit.ts_code, StkLimit.trade_date, StkLimit.up_limit, StkLimit.down_limit).where(
            StkLimit.trade_date.between(self._to_db_date(start_date), self._to_db_date(end_date))
        )
        return await self._read_db_select(stmt)
