"""DAO for pledge_detail (Share Pledge Detail).

Phase 3B §3.2：股权质押明细，与 pledge_stat（统计）互补，提供更细粒度的
质押信息供 AI 分析。
"""

import asyncio
import logging

import pandas as pd

from data.persistence.models import PledgeDetail, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

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
            as_of_date: 截止日期（YYYYMMDD 或 date），仅返回 end_date <= as_of_date 的记录；
                None 时不过滤日期。

        Returns:
            DataFrame，每个 ts_code 取最近一条 end_date 记录。
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is not None:
                return await self.chunked_in_query(
                    self._read_db,
                    lambda placeholders, chunk_len, start_idx: (
                        f"""
                        SELECT DISTINCT ON (ts_code)
                            ts_code, end_date, pledge_amount,
                            unlimited_pledge_amount, limited_pledge_amount,
                            total_pledge_amount, pledge_ratio
                        FROM pledge_detail
                        WHERE ts_code IN ({placeholders})
                          AND end_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, end_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            return await self.chunked_in_query(
                self._read_db,
                """
                SELECT DISTINCT ON (ts_code)
                    ts_code, end_date, pledge_amount,
                    unlimited_pledge_amount, limited_pledge_amount,
                    total_pledge_amount, pledge_ratio
                FROM pledge_detail
                WHERE ts_code IN ({placeholders})
                ORDER BY ts_code, end_date DESC
                """,
                ts_codes,
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[PledgeDetailDao] Failed to get pledge_detail batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()
