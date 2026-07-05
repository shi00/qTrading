"""DAO for express (Performance Express / 业绩快报).

Phase 3G §4.3.4：业绩快报数据，早于正式财报 30-60 天公告，
供 AI 提前反应业绩拐点。
"""

import asyncio
import logging

import pandas as pd

from data.persistence.models import Express, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class ExpressDao(BaseDao):
    """DAO for express table (业绩快报)."""

    async def save_express(self, df: pd.DataFrame):
        """UPSERT express rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(Express)
        pk_columns = get_model_pk_columns(Express)
        return await self._save_upsert(
            df,
            "express",
            cols,
            pk_columns=pk_columns,
        )

    async def get_express_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        """批量查询股票的业绩快报记录。

        Args:
            ts_codes: 股票代码列表
            as_of_date: 截止日期（含），用于历史回放场景防止前视偏差。
                None 表示不限制（取最新一期）。

        Returns:
            DataFrame，每只股票返回最新一期业绩快报（DISTINCT ON ts_code），
            按 end_date DESC, ann_date DESC 排序。
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
                            ts_code, end_date, ann_date, type,
                            revenue, n_income, total_profit,
                            yoy_sales, yoy_profit, yoy_dedu_np, deduct_profit
                        FROM express
                        WHERE ts_code IN ({placeholders})
                          AND ann_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, end_date DESC, ann_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            else:
                return await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT DISTINCT ON (ts_code)
                        ts_code, end_date, ann_date, type,
                        revenue, n_income, total_profit,
                        yoy_sales, yoy_profit, yoy_dedu_np, deduct_profit
                    FROM express
                    WHERE ts_code IN ({placeholders})
                    ORDER BY ts_code, end_date DESC, ann_date DESC
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            # R5 红线：僵尸引擎操作必须传播，不得被下面的 Exception 吞没
            raise
        except Exception as e:
            logger.warning("[ExpressDao] Failed to get express batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()
