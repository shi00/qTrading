"""DAO for top_inst (LHB Institutional Seat Transaction Detail).

Phase 2E §3.2.7：top_inst 已封装 API 激活，仅数据层。
"""

import asyncio
import logging

import pandas as pd

from data.persistence.models import TopInst, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class TopInstDao(BaseDao):
    """DAO for top_inst table (龙虎榜机构席位交易明细)."""

    async def save_top_inst(self, df: pd.DataFrame):
        """UPSERT top_inst rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(TopInst)
        pk_columns = get_model_pk_columns(TopInst)
        return await self._save_upsert(
            df,
            "top_inst",
            cols,
            pk_columns=pk_columns,
        )

    async def get_top_inst_batch(self, ts_codes: list[str], as_of_date=None) -> pd.DataFrame:
        """批量查询股票的龙虎榜机构席位数据。

        Args:
            ts_codes: 股票代码列表
            as_of_date: 截止日期（YYYYMMDD 或 date），仅返回 trade_date <= as_of_date 的记录；
                None 时不过滤日期。

        Returns:
            DataFrame，每个 ts_code 取最近一条 trade_date 记录。
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
                            ts_code, trade_date, name, close, pct_change,
                            amount, net_amount, buy_amount, buy_value,
                            sell_amount, sell_value
                        FROM top_inst
                        WHERE ts_code IN ({placeholders})
                          AND trade_date <= ${start_idx + chunk_len}
                        ORDER BY ts_code, trade_date DESC
                    """
                    ),
                    ts_codes,
                    params_fn=lambda chunk: [as_of_date],
                )
            return await self.chunked_in_query(
                self._read_db,
                """
                SELECT DISTINCT ON (ts_code)
                    ts_code, trade_date, name, close, pct_change,
                    amount, net_amount, buy_amount, buy_value,
                    sell_amount, sell_value
                FROM top_inst
                WHERE ts_code IN ({placeholders})
                ORDER BY ts_code, trade_date DESC
                """,
                ts_codes,
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning("[TopInstDao] Failed to get top_inst batch: %s", DataSanitizer.sanitize_error(e))
            return pd.DataFrame()
