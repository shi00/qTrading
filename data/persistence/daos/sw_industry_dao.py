"""DAO for sw_industry_classify + sw_industry_member (申万行业分类).

Phase 3F-1 §4.3.2：申万行业分类建表，对应 Tushare index_classify /
index_member_all 接口。全局快照，月度更新，供 AI 行业景气度分析与
screener 查询时计算 industry_sw_l2（DAT-08③，与 stock_basic.industry
Tushare 原始值拆列输出，不再混列覆写）。
"""

import asyncio
import logging

import pandas as pd

from data.persistence.models import (
    StockNameHistory,
    SwIndustryClassify,
    SwIndustryMember,
    get_model_columns,
    get_model_pk_columns,
)
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
        """按 ts_code 反查所属申万行业（v1.10.0 P2-6 命名，DATA-04 L2 更新列）。

        返回该 ts_code 关联的全部申万行业成分记录（含 L1/L2/L3 行业代码/名称、纳入/剔除
        日期与是否当前有效），按 l3_code 排序。空结果时返回空 DataFrame。

        Args:
            ts_code: 股票代码（如 "000001.SZ"）。

        Returns:
            DataFrame，包含 ts_code / l1_code..l3_name / name / in_date / out_date / is_new 列。
        """
        if not ts_code:
            return pd.DataFrame()

        try:
            return await self._read_db(
                """
                SELECT ts_code, l1_code, l1_name, l2_code, l2_name,
                       l3_code, l3_name, name, in_date, out_date, is_new
                FROM sw_industry_member
                WHERE ts_code = $1
                ORDER BY l3_code
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

    async def get_sw_l2_mapping(self, ts_codes: list[str] | None = None) -> dict[str, str]:
        """批量查询 ts_code → 申万二级行业名（l2_name）映射。

        用于 ``prefetch_auxiliary_data`` 批量预取 AI 行业上下文。同一 ts_code 在
        sw_industry_member（DATA-04 L2 后）可能因行业重分类对应多个 l2_name，此处取
        当前有效（out_date IS NULL）记录，按 ts_code 去重取任一非空值即可。

        Args:
            ts_codes: 股票代码列表，None 表示查询全表。

        Returns:
            {ts_code: l2_name} 字典。无映射或不在此列表的 ts_code 不包含在结果中。
        """
        try:
            if ts_codes is None:
                df = await self._read_db(
                    """
                    SELECT DISTINCT ts_code, l2_name
                    FROM sw_industry_member
                    WHERE out_date IS NULL
                      AND l2_name IS NOT NULL AND l2_name <> ''
                    """,
                )
            elif len(ts_codes) == 0:
                return {}
            else:
                df = await self.chunked_in_query(
                    self._read_db,
                    """
                    SELECT DISTINCT ts_code, l2_name
                    FROM sw_industry_member
                    WHERE out_date IS NULL
                      AND l2_name IS NOT NULL AND l2_name <> ''
                      AND ts_code IN ({placeholders})
                    """,
                    ts_codes,
                )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[SwIndustryMemberDao] Failed to get sw_l2 mapping: %s",
                DataSanitizer.sanitize_error(e),
            )
            return {}

        if df is None or df.empty:
            return {}
        return dict(zip(df["ts_code"], df["l2_name"], strict=False))


class StockNameHistoryDao(BaseDao):
    """DAO for stock_name_history table (股票名称变更历史，DATA-04 L3).

    记录每股历次名称（含 ST/*ST 前缀）生效区间，供 as-of 还原历史时点名称/ST 状态，
    消除回测引用当前名称（stock_basic.name）引入的前视偏差。
    """

    async def save_stock_name_history(self, df: pd.DataFrame):
        """UPSERT stock_name_history rows. R8: 使用 _save_upsert 而非 _write_db(is_many=True)。"""
        if df is None or df.empty:
            return 0
        cols = get_model_columns(StockNameHistory)
        pk_columns = get_model_pk_columns(StockNameHistory)
        return await self._save_upsert(
            df,
            "stock_name_history",
            cols,
            pk_columns=pk_columns,
        )

    async def get_name_as_of(self, ts_code: str, as_of) -> str | None:
        """按历史时点还原股票名称（as-of 查询）。

        ``start_date <= as_of AND (end_date IS NULL OR end_date > as_of)`` 定位该时点
        生效的名称（含 ST/*ST 前缀）。无记录或该时点无生效区间时返回 None。
        """
        if not ts_code:
            return None
        try:
            df = await self._read_db(
                """
                SELECT name
                FROM stock_name_history
                WHERE ts_code = $1
                  AND start_date <= $2
                  AND (end_date IS NULL OR end_date > $2)
                ORDER BY start_date DESC
                LIMIT 1
                """,
                (ts_code, as_of),
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.warning(
                "[StockNameHistoryDao] Failed to get name as_of: %s",
                DataSanitizer.sanitize_error(e),
            )
            return None

        if df is None or df.empty:
            return None
        return str(df["name"].iloc[0])
