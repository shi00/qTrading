"""DAO for sw_industry_classify + sw_industry_member (申万行业分类).

Phase 3F-1 §4.3.2：申万行业分类建表，对应 Tushare index_classify /
index_member_all 接口。全局快照，月度更新，供 AI 行业景气度分析与
stock_basic.industry 字段切换（Phase 3F-2 轨道 A/B）。
"""

import asyncio
import logging

import pandas as pd

from data.persistence.models import SwIndustryClassify, SwIndustryMember, get_model_columns, get_model_pk_columns
from utils.sanitizers import DataSanitizer

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class SwIndustryClassifyDao(BaseDao):
    """DAO for sw_industry_classify table (申万行业分类)."""

    async def save_sw_industry_classify(self, df: pd.DataFrame):
        """UPSERT sw_industry_classify rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(SwIndustryClassify)
        pk_columns = get_model_pk_columns(SwIndustryClassify)
        return await self._save_upsert(
            df,
            "sw_industry_classify",
            cols,
            pk_columns=pk_columns,
        )


class SwIndustryMemberDao(BaseDao):
    """DAO for sw_industry_member table (申万行业成分股映射)."""

    async def save_sw_industry_member(self, df: pd.DataFrame):
        """UPSERT sw_industry_member rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(SwIndustryMember)
        pk_columns = get_model_pk_columns(SwIndustryMember)
        return await self._save_upsert(
            df,
            "sw_industry_member",
            cols,
            pk_columns=pk_columns,
        )

    async def get_sw_industry_by_ts_code(self, ts_code: str) -> pd.DataFrame:
        """按 ts_code 反查所属申万行业（v1.10.0 P2-6 命名）。

        返回该 ts_code 关联的所有申万行业成分记录（含 L1/L2/L3 行业代码与名称），
        按 index_code 排序。空结果时返回空 DataFrame。

        Args:
            ts_code: 股票代码（如 "000001.SZ"）。

        Returns:
            DataFrame，包含 ts_code / index_code / index_name / sw_l1_code..sw_l3_name 列。
        """
        if not ts_code:
            return pd.DataFrame()

        try:
            return await self._read_db(
                """
                SELECT ts_code, index_code, index_name,
                       sw_l1_code, sw_l1_name, sw_l2_code, sw_l2_name,
                       sw_l3_code, sw_l3_name
                FROM sw_industry_member
                WHERE ts_code = $1
                ORDER BY index_code
                """,
                (ts_code,),
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[SwIndustryMemberDao] Failed to get sw_industry by ts_code: %s",
                DataSanitizer.sanitize_error(e),
            )
            return pd.DataFrame()
