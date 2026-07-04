"""DAO for stk_holdertrade (Shareholder Trade / 产业资本增减持).

Phase 3E §3.2：股东增减持数据，供 AI 分析产业资本信号。
"""

import asyncio
import datetime
import logging

import pandas as pd

from data.persistence.models import StkHoldertrade, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)

# 默认回溯天数：读取近 180 天的增减持记录
_DEFAULT_LOOKBACK_DAYS = 180


class StkHoldertradeDao(BaseDao):
    """DAO for stk_holdertrade table (股东增减持)."""

    async def save_stk_holdertrade(self, df: pd.DataFrame):
        """UPSERT stk_holdertrade rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(StkHoldertrade)
        pk_columns = get_model_pk_columns(StkHoldertrade)
        return await self._save_upsert(
            df,
            "stk_holdertrade",
            cols,
            pk_columns=pk_columns,
        )

    async def get_stk_holdertrade_batch(
        self,
        ts_codes: list[str],
        as_of_date=None,
        days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> pd.DataFrame:
        """批量查询股票的近期增减持记录。

        Args:
            ts_codes: 股票代码列表
            as_of_date: 截止日期（YYYYMMDD 或 date），仅返回 ann_date <= as_of_date 的记录；
                None 时使用今天。
            days: 回溯天数，仅返回 ann_date >= as_of_date - days 的记录。

        Returns:
            DataFrame，包含所有匹配的增减持记录，按 ts_code, ann_date 排序。
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            if as_of_date is None:
                end_date = datetime.date.today()
            elif isinstance(as_of_date, str):
                end_date = datetime.datetime.strptime(as_of_date, "%Y%m%d").date()
            elif isinstance(as_of_date, datetime.datetime):
                end_date = as_of_date.date()
            else:
                end_date = as_of_date
            start_date = end_date - datetime.timedelta(days=days)

            return await self.chunked_in_query(
                self._read_db,
                lambda placeholders, chunk_len, start_idx: (
                    f"""
                    SELECT ts_code, ann_date, holder_name, holder_type, in_de,
                           change_vol, change_ratio, after_share, after_ratio
                    FROM stk_holdertrade
                    WHERE ts_code IN ({placeholders})
                      AND ann_date >= ${start_idx + chunk_len}
                      AND ann_date <= ${start_idx + chunk_len + 1}
                    ORDER BY ts_code, ann_date
                """
                ),
                ts_codes,
                params_fn=lambda chunk: [start_date, end_date],
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[StkHoldertradeDao] Failed to get stk_holdertrade batch: %s", DataSanitizer.sanitize_error(e)
            )
            return pd.DataFrame()
