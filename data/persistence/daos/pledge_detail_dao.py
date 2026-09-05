"""DAO for pledge_detail (Share Pledge Detail).

Phase 3B §3.2：股权质押明细，与 pledge_stat（统计）互补，提供更细粒度的
质押信息供 AI 分析。
"""

import logging

import pandas as pd

from data.persistence.models import PledgeDetail, get_model_columns, get_model_pk_columns

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class PledgeDetailDao(BaseDao):
    """DAO for pledge_detail table (股权质押明细)."""

    async def save_pledge_detail(self, df: pd.DataFrame):
        """UPSERT pledge_detail rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(PledgeDetail)
        pk_columns = get_model_pk_columns(PledgeDetail)
        return await self._save_upsert(
            df,
            "pledge_detail",
            cols,
            pk_columns=pk_columns,
        )

    async def get_pledge_detail_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        """批量查询股票的股权质押明细数据。

        Args:
            ts_codes: 股票代码列表
            as_of_date: 截止日期（YYYYMMDD 或 date），仅返回 ann_date <= as_of_date 的
                记录（DAT-09：官方契约含 ann_date，DAT-05 的保守滞后不再需要）；
                None 时不过滤日期。

        Returns:
            DataFrame，每个 ts_code 取最近一条 ann_date 记录。
        """

        def sql_fn(as_of):
            if as_of is not None:
                return (
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT DISTINCT ON (ts_code)
                            ts_code, ann_date, holder_name, pledge_amount,
                            start_date, end_date, is_release, release_date, pledgor,
                            holding_amount, pledged_amount, p_total_ratio,
                            h_total_ratio, is_buyback
                        FROM pledge_detail
                        WHERE ts_code IN ({placeholders})
                          AND ann_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, ann_date DESC, holder_name
                        """
                    ),
                    lambda chunk: [as_of],
                )
            return (
                """
                SELECT DISTINCT ON (ts_code)
                    ts_code, ann_date, holder_name, pledge_amount,
                    start_date, end_date, is_release, release_date, pledgor,
                    holding_amount, pledged_amount, p_total_ratio,
                    h_total_ratio, is_buyback
                FROM pledge_detail
                WHERE ts_code IN ({placeholders})
                ORDER BY ts_code, ann_date DESC, holder_name
                """,
                None,
            )

        return await self._batch_get_with_as_of_date(sql_fn, ts_codes, as_of_date, "Failed to get pledge_detail batch")
