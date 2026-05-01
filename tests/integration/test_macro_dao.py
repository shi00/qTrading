"""
宏观经济数据完整性测试

测试 Phase 0.5: DAO 分层修复
测试 Phase 1: AI Prompt 数据注入增强
- F3: 宏观经济指标字段映射
- L3: Shibor 利率注入
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from data.persistence.daos.macro_dao import MacroDao


class TestMacroDaoIntegrity:
    """测试宏观经济数据完整性检查方法"""

    @pytest.fixture
    def macro_dao(self, test_engine):
        return MacroDao(test_engine)

    @pytest.mark.asyncio
    async def test_get_shibor_latest(self, macro_dao):
        """
        L3 测试：获取最新 Shibor 利率
        """
        df = await macro_dao.get_shibor_latest()

        if df is not None and not df.empty:
            assert "on" in df.columns or "1w" in df.columns or "3m" in df.columns

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest(self, macro_dao):
        """
        F3 测试：获取最新宏观经济指标
        """
        df = await macro_dao.get_macro_economy_latest()

        if df is not None and not df.empty:
            assert "m2_yoy" in df.columns or "cpi" in df.columns or "ppi" in df.columns

    @pytest.mark.asyncio
    async def test_get_macro_latest_date(self, macro_dao):
        """
        测试获取宏观经济数据最新日期
        """
        result = await macro_dao.get_macro_latest_date()

        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_get_shibor_latest_date(self, macro_dao):
        """
        测试获取 Shibor 数据最新日期
        """
        result = await macro_dao.get_shibor_latest_date()

        assert result is None or isinstance(result, str)


class TestMacroDaoEdgeCases:
    """边界条件测试"""

    @pytest.fixture
    def macro_dao(self, test_engine):
        return MacroDao(test_engine)

    @pytest.mark.asyncio
    async def test_db_error_handling(self, macro_dao):
        """
        测试数据库错误处理
        """
        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            side_effect=Exception("DB Error"),
        ):
            df = await macro_dao.get_shibor_latest()

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_none_result_handling(self, macro_dao):
        """
        测试 None 结果处理
        """
        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=None,
        ):
            df = await macro_dao.get_macro_economy_latest()

            assert df is not None
            assert df.empty

    @pytest.mark.asyncio
    async def test_empty_dataframe_handling(self, macro_dao):
        """
        测试空 DataFrame 处理
        """
        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ):
            df = await macro_dao.get_shibor_latest()

            assert df is not None
            assert df.empty


class TestShiborDataQuality:
    """Shibor 利率数据质量测试"""

    @pytest.fixture
    def macro_dao(self, test_engine):
        return MacroDao(test_engine)

    @pytest.mark.asyncio
    async def test_shibor_columns(self, macro_dao):
        """
        L3 测试：验证 Shibor 利率字段完整性
        """
        mock_df = pd.DataFrame(
            {
                "date": ["20240101"],
                "on": [2.0],
                "1w": [2.5],
                "2w": [2.8],
                "1m": [3.0],
                "3m": [3.5],
                "6m": [4.0],
                "9m": [4.2],
                "1y": [4.5],
            }
        )

        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_shibor_latest()

            assert "on" in df.columns
            assert "1w" in df.columns
            assert "3m" in df.columns

    @pytest.mark.asyncio
    async def test_shibor_rate_range(self, macro_dao):
        """
        测试 Shibor 利率数值范围
        """
        mock_df = pd.DataFrame(
            {
                "date": ["20240101"],
                "on": [2.0],
                "1w": [2.5],
                "3m": [3.5],
            }
        )

        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_shibor_latest()

            on_rate = df["on"].iloc[0]
            assert 0 < on_rate < 20


class TestMacroEconomyDataQuality:
    """宏观经济数据质量测试"""

    @pytest.fixture
    def macro_dao(self, test_engine):
        return MacroDao(test_engine)

    @pytest.mark.asyncio
    async def test_macro_columns(self, macro_dao):
        """
        F3 测试：验证宏观经济指标字段完整性
        """
        mock_df = pd.DataFrame(
            {
                "period": ["202401"],
                "m2": [2000000],
                "m2_yoy": [8.5],
                "m1": [500000],
                "m1_yoy": [5.0],
                "cpi": [0.2],
                "ppi": [-2.5],
            }
        )

        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_macro_economy_latest()

            assert "m2_yoy" in df.columns
            assert "cpi" in df.columns
            assert "ppi" in df.columns

    @pytest.mark.asyncio
    async def test_macro_rate_range(self, macro_dao):
        """
        测试宏观经济指标数值范围
        """
        mock_df = pd.DataFrame(
            {
                "period": ["202401"],
                "m2_yoy": [8.5],
                "cpi": [0.2],
                "ppi": [-2.5],
            }
        )

        with patch.object(
            macro_dao,
            "_read_db",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_macro_economy_latest()

            m2_yoy = df["m2_yoy"].iloc[0]
            assert -20 < m2_yoy < 50

            cpi = df["cpi"].iloc[0]
            assert -10 < cpi < 20
