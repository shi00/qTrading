"""
宏观经济数据完整性测试

测试 Phase 0.5: DAO 分层修复
测试 Phase 1: AI Prompt 数据注入增强
- F3: 宏观经济指标字段映射
- L3: Shibor 利率注入
"""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from data.persistence.daos.macro_dao import MacroDao

pytestmark = pytest.mark.integration


class TestMacroDaoIntegrity:
    """测试宏观经济数据完整性检查方法（基于 MVD 真实数据）"""

    pytestmark = pytest.mark.usefixtures("mvd_data")

    @pytest.fixture
    def macro_dao(self, test_engine):
        return MacroDao(test_engine)

    @pytest.mark.asyncio
    async def test_get_shibor_latest(self, macro_dao):
        """Level 2: 验证最新 Shibor 利率查询（含 Phase 3G LPR 扩列字段）"""
        df = await macro_dao.get_shibor_latest()

        assert df is not None
        assert not df.empty
        # Level 2: 验证字段存在和具体值
        # R17（迁移 0015）：列名 on_rate/year_1 等非保留字，属性名与列名一致
        assert "on_rate" in df.columns
        assert "year_1" in df.columns
        assert df["on_rate"].iloc[0] == Decimal("1.85")
        assert df["year_1"].iloc[0] == Decimal("2.50")
        # Phase 3G §4.3.3：LPR 扩列字段验证
        assert "lpr_1y" in df.columns, "lpr_1y 字段未在查询结果中"
        assert "lpr_5y" in df.columns, "lpr_5y 字段未在查询结果中"
        assert df["lpr_1y"].iloc[0] == Decimal("3.10")
        assert df["lpr_5y"].iloc[0] == Decimal("3.60")

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest(self, macro_dao):
        """Level 2: 验证最新宏观经济指标查询"""
        df = await macro_dao.get_macro_economy_latest()

        assert df is not None
        assert not df.empty
        assert "m2_yoy" in df.columns
        assert "cpi" in df.columns
        assert "ppi" in df.columns
        # Level 2: 验证具体值
        assert df["m2_yoy"].iloc[0] == Decimal("8.5")
        assert df["cpi"].iloc[0] == Decimal("1.8")
        assert df["ppi"].iloc[0] == Decimal("-1.2")

    @pytest.mark.asyncio
    async def test_get_macro_economy_latest_includes_gdp(self, macro_dao):
        """Phase 2D §3.2.6：验证 get_macro_economy_latest 返回 8 个 GDP 字段。"""
        df = await macro_dao.get_macro_economy_latest()

        assert df is not None
        assert not df.empty
        # Phase 2D: 8 个 GDP 字段必须在 SELECT 列表中
        for col in ("gdp", "gdp_yoy", "pi", "pi_yoy", "si", "si_yoy", "ti", "ti_yoy"):
            assert col in df.columns, f"GDP 字段 {col} 未在查询结果中"
        # 验证 MVD 注入的具体值
        assert df["gdp_yoy"].iloc[0] == Decimal("5.2")
        assert df["pi_yoy"].iloc[0] == Decimal("3.1")
        assert df["si_yoy"].iloc[0] == Decimal("5.0")
        assert df["ti_yoy"].iloc[0] == Decimal("5.8")

    @pytest.mark.asyncio
    async def test_get_macro_latest_date(self, macro_dao):
        """Level 2: 验证宏观经济数据最新日期"""
        result = await macro_dao.get_macro_latest_date()

        # Level 2: MVD 注入了 2026-05-31，应返回该日期
        # 注意：返回 datetime.date 对象，非 str（见约束 5）
        assert result is not None
        assert isinstance(result, datetime.date)
        assert result == datetime.date(2026, 5, 31)

    @pytest.mark.asyncio
    async def test_get_shibor_latest_date(self, macro_dao):
        """Level 2: 验证 Shibor 数据最新日期"""
        result = await macro_dao.get_shibor_latest_date()

        # Level 2: MVD 注入了 2026-06-24，应返回该日期
        # 注意：返回 datetime.date 对象，非 str（见约束 5）
        assert result is not None
        assert isinstance(result, datetime.date)
        assert result == datetime.date(2026, 6, 24)


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
            "_read_db_select",
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
            "_read_db_select",
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
            "_read_db_select",
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
                "record_date": ["20240101"],
                "on_rate": [2.0],
                "week_1": [2.5],
                "week_2": [2.8],
                "month_1": [3.0],
                "month_3": [3.5],
                "month_6": [4.0],
                "month_9": [4.2],
                "year_1": [4.5],
            }
        )

        with patch.object(
            macro_dao,
            "_read_db_select",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_shibor_latest()

            assert "on_rate" in df.columns
            assert "week_1" in df.columns
            assert "month_3" in df.columns

    @pytest.mark.asyncio
    async def test_shibor_rate_range(self, macro_dao):
        """
        测试 Shibor 利率数值范围
        """
        mock_df = pd.DataFrame(
            {
                "record_date": ["20240101"],
                "on_rate": [2.0],
                "week_1": [2.5],
                "month_3": [3.5],
            }
        )

        with patch.object(
            macro_dao,
            "_read_db_select",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_shibor_latest()

            on_rate = df["on_rate"].iloc[0]
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
            "_read_db_select",
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
            "_read_db_select",
            new_callable=AsyncMock,
            return_value=mock_df,
        ):
            df = await macro_dao.get_macro_economy_latest()

            m2_yoy = df["m2_yoy"].iloc[0]
            assert -20 < m2_yoy < 50

            cpi = df["cpi"].iloc[0]
            assert -10 < cpi < 20
