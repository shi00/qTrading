"""DAO for share_float (Share Float / Unlock).

Phase 3D §3.2：限售解禁数据，供 AI 分析解禁压力与减持风险。
"""

import datetime
import logging

import pandas as pd

from data.persistence.models import ShareFloat, get_model_columns, get_model_pk_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)

# 默认前瞻天数：读取未来 90 天内的解禁记录
_DEFAULT_UPCOMING_DAYS = 90


class ShareFloatDao(BaseDao):
    """DAO for share_float table (限售解禁)."""

    async def save_share_float(self, df: pd.DataFrame):
        """UPSERT share_float rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。

        DAT-09：API fields 已补 holder_name（官方契约 doc_id=160），不再排除该列。
        """
        if df is None or df.empty:
            return 0
        cols = get_model_columns(ShareFloat)
        pk_columns = get_model_pk_columns(ShareFloat)
        return await self._save_upsert(
            df,
            "share_float",
            cols,
            pk_columns=pk_columns,
        )

    async def get_share_float_upcoming_batch(
        self,
        ts_codes: list[str],
        as_of_date=None,
        days: int = _DEFAULT_UPCOMING_DAYS,
    ) -> pd.DataFrame:
        """批量查询股票的未来解禁记录。

        Args:
            ts_codes: 股票代码列表
            as_of_date: 起始日期（YYYYMMDD 或 date），仅返回 float_date >= as_of_date 的记录；
                None 时使用今天。
            days: 前瞻天数，仅返回 float_date <= as_of_date + days 的记录。

        Returns:
            DataFrame，包含所有匹配的解禁记录，按 ts_code, float_date 排序。
        """

        def sql_fn(as_of):
            if as_of is None:
                start_date = datetime.date.today()
            elif isinstance(as_of, str):
                start_date = datetime.datetime.strptime(as_of, "%Y%m%d").date()
            elif isinstance(as_of, datetime.datetime):
                start_date = as_of.date()
            else:
                start_date = as_of
            end_date = start_date + datetime.timedelta(days=days)
            return (
                lambda placeholders, chunk_len, start_idx: (
                    f"""
                    SELECT ts_code, ann_date, float_date, float_share,
                           float_ratio, holder_name, share_type
                    FROM share_float
                    WHERE ts_code IN ({placeholders})
                      AND float_date >= ${start_idx + chunk_len}
                      AND float_date <= ${start_idx + chunk_len + 1}
                    ORDER BY ts_code, float_date
                    """
                ),
                lambda chunk: [start_date, end_date],
            )

        return await self._batch_get_with_as_of_date(
            sql_fn, ts_codes, as_of_date, "Failed to get share_float upcoming batch"
        )
