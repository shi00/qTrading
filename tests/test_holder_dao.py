"""
股东数据完整性测试

测试 Phase 0.5: DAO 分层修复
测试 Phase 1: AI Prompt 数据注入增强
- L2: 批量预取避免 N+1 查询
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.persistence.daos.holder_dao import HolderDao


class TestHolderDaoIntegrity:
    """测试股东数据完整性检查方法"""

    @pytest.fixture
    def holder_dao(self):
        return HolderDao()

    @pytest.mark.asyncio
    async def test_get_top10_holders_batch(self, holder_dao):
        """
        L2 测试：批量获取前十大股东
        """
        ts_codes = ["000001.SZ", "600000.SH"]
        df = await holder_dao.get_top10_holders_batch(ts_codes)

        if df is not None and not df.empty:
            assert "ts_code" in df.columns
            assert "holder_name" in df.columns

    @pytest.mark.asyncio
    async def test_get_top10_holders_batch_empty(self, holder_dao):
        """
        测试空股票列表
        """
        df = await holder_dao.get_top10_holders_batch([])

        assert df is not None
        assert df.empty

    @pytest.mark.asyncio
    async def test_get_stk_holdernumber(self, holder_dao):
        """
        测试获取股东人数历史
        """
        df = await holder_dao.get_stk_holdernumber("000001.SZ")

        if df is not None and not df.empty:
            assert "ts_code" in df.columns
            assert "holder_num" in df.columns

    @pytest.mark.asyncio
    async def test_get_top10_holders(self, holder_dao):
        """
        测试获取单只股票前十大股东
        """
        df = await holder_dao.get_top10_holders("000001.SZ")

        if df is not None and not df.empty:
            assert "holder_name" in df.columns
            assert "hold_ratio" in df.columns


class TestHolderDaoBatchPerformance:
    """批量查询性能测试"""

    @pytest.fixture
    def holder_dao(self):
        return HolderDao()

    @pytest.mark.asyncio
    async def test_batch_vs_individual_queries(self, holder_dao):
        """
        性能测试：批量查询 vs 单独查询

        批量查询应该只执行 1 次 SQL，而非 N 次
        """
        call_count = 0

        async def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "holder_name": ["股东1", "股东2"],
                    "hold_ratio": [30.0, 10.0],
                }
            )

        with patch.object(
            holder_dao, "_read_db", new_callable=AsyncMock, side_effect=count_calls
        ):
            ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
            await holder_dao.get_top10_holders_batch(ts_codes)

            assert call_count == 1, f"Expected 1 DB call, got {call_count}"


class TestHolderDaoEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def holder_dao(self):
        return HolderDao()

    @pytest.mark.asyncio
    async def test_db_error_handling(self, holder_dao):
        """
        测试数据库错误处理
        """
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            side_effect=Exception("DB Error"),
        ):
            df = await holder_dao.get_top10_holders_batch(["000001.SZ"])

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_none_result_handling(self, holder_dao):
        """
        测试 None 结果处理
        """
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=None,
        ):
            df = await holder_dao.get_top10_holders_batch(["000001.SZ"])

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_empty_dataframe_handling(self, holder_dao):
        """
        测试空 DataFrame 处理
        """
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ):
            df = await holder_dao.get_top10_holders_batch(["000001.SZ"])

            assert df is not None
            assert df.empty


class TestHolderDataQuality:
    """股东数据质量测试"""

    @pytest.fixture
    def holder_dao(self):
        return HolderDao()

    @pytest.mark.asyncio
    async def test_holder_ratio_sum(self, holder_dao):
        """
        测试股东持股比例合计
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "end_date": ["20231231", "20231231", "20231231"],
                "holder_name": ["股东1", "股东2", "股东3"],
                "hold_ratio": [30.0, 20.0, 10.0],
            }
        )

        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await holder_dao.get_top10_holders_batch(["000001.SZ"])

            total_ratio = df["hold_ratio"].sum()
            assert total_ratio == 60.0

    @pytest.mark.asyncio
    async def test_holder_number_trend(self, holder_dao):
        """
        测试股东人数变化趋势
        """
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "end_date": ["20231231", "20230930"],
                "holder_num": [480000, 500000],
            }
        )

        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await holder_dao.get_stk_holdernumber("000001.SZ")

            if len(df) >= 2:
                curr_num = df.iloc[0]["holder_num"]
                prev_num = df.iloc[1]["holder_num"]
                change_pct = (curr_num - prev_num) / prev_num * 100

                assert change_pct < 0
