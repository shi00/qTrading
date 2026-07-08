"""
股东数据完整性测试

测试 Phase 0.5: DAO 分层修复
测试 Phase 1: AI Prompt 数据注入增强
- L2: 批量预取避免 N+1 查询
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from data.persistence.daos.holder_dao import HolderDao

pytestmark = pytest.mark.integration


class TestHolderDaoIntegrity:
    """测试股东数据完整性检查方法（基于 MVD 真实数据）"""

    pytestmark = pytest.mark.usefixtures("mvd_data")

    @pytest.fixture
    def holder_dao(self, test_engine):
        return HolderDao(test_engine)

    @pytest.mark.asyncio
    async def test_get_top10_holders_batch(self, holder_dao):
        """Level 2: 验证批量前十大股东查询"""
        ts_codes = ["000001.SZ", "600000.SH"]
        df = await holder_dao.get_top10_holders_batch(ts_codes)

        assert df is not None
        assert not df.empty
        assert "ts_code" in df.columns
        assert "holder_name" in df.columns
        # Level 2: 验证 000001.SZ 的记录数
        # 注意：get_top10_holders_batch 使用 DISTINCT ON (ts_code, end_date)，
        # MVD 中 000001.SZ 的 3 条记录 end_date 均为 2025-12-31，仅返回 1 条（见约束 4）
        rows = df[df["ts_code"] == "000001.SZ"]
        assert len(rows) == 1
        # 返回的是 hold_ratio 最大的那条
        assert rows["hold_ratio"].iloc[0] == Decimal("2.5")

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
        """Level 2: 验证股东人数历史查询"""
        df = await holder_dao.get_stk_holdernumber("000001.SZ")

        assert df is not None
        assert not df.empty
        assert "ts_code" in df.columns
        assert "holder_num" in df.columns
        # Level 2: 验证有 2 期数据
        assert len(df) == 2

    @pytest.mark.asyncio
    async def test_get_top10_holders(self, holder_dao):
        """Level 2: 验证单只股票前十大股东查询"""
        df = await holder_dao.get_top10_holders("000001.SZ")

        assert df is not None
        assert not df.empty
        assert "holder_name" in df.columns
        assert "hold_ratio" in df.columns
        # Level 2: 验证持股比例合计 = 2.5 + 1.8 + 1.2 = 5.5
        # 注意：get_top10_holders（非 batch）无 DISTINCT ON，返回全部 3 条记录
        assert len(df) == 3
        total_ratio = df["hold_ratio"].sum()
        assert total_ratio == Decimal("5.5")


class TestHolderDaoBatchPerformance:
    """批量查询性能测试"""

    @pytest.fixture
    def holder_dao(self, test_engine):
        return HolderDao(test_engine)

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

        with patch.object(holder_dao, "_read_db", new_callable=AsyncMock, side_effect=count_calls):
            ts_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
            await holder_dao.get_top10_holders_batch(ts_codes)

            assert call_count == 1, f"Expected 1 DB call, got {call_count}"


class TestHolderDaoEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def holder_dao(self, test_engine):
        return HolderDao(test_engine)

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
    def holder_dao(self, test_engine):
        return HolderDao(test_engine)

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
                df = df.sort_values("end_date", ascending=False).reset_index(drop=True)
                curr_num = df.iloc[0]["holder_num"]
                prev_num = df.iloc[1]["holder_num"]
                change_pct = (curr_num - prev_num) / prev_num * 100

                assert change_pct < 0


class TestHolderDaoIncremental:
    """测试增量同步查询方法"""

    @pytest.fixture
    def holder_dao(self, test_engine):
        return HolderDao(test_engine)

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_with_data(self, holder_dao):
        """有数据时返回 ts_code 集合"""
        mock_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "600000.SH"]})

        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            result = await holder_dao.get_existing_top10_ts_codes("20231231")

            assert result == {"000001.SZ", "000002.SZ", "600000.SH"}

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_empty(self, holder_dao):
        """无数据时返回空集合"""
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ):
            result = await holder_dao.get_existing_top10_ts_codes("20231231")

            assert result == set()

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_none_result(self, holder_dao):
        """数据库返回 None 时返回空集合"""
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await holder_dao.get_existing_top10_ts_codes("20231231")

            assert result == set()

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_db_error(self, holder_dao):
        """数据库错误时返回空集合（降级到全量同步）"""
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            side_effect=Exception("DB Error"),
        ):
            result = await holder_dao.get_existing_top10_ts_codes("20231231")

            assert result == set()

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_empty_period(self, holder_dao):
        """空 period 返回空集合"""
        result = await holder_dao.get_existing_top10_ts_codes("")

        assert result == set()

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_correct_sql(self, holder_dao):
        """验证 SQL 查询使用了正确的 period 参数"""
        with patch.object(
            holder_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}),
        ) as mock_read:
            await holder_dao.get_existing_top10_ts_codes("20230930")

            mock_read.assert_called_once()
            call_args = mock_read.call_args
            assert "top10_holders" in call_args[0][0]
            assert "end_date" in call_args[0][0]
            assert call_args[0][1] == ("20230930",)
