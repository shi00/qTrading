"""
MVD 业务逻辑集成测试（Level 3）

基于 MVD 已知数据验证业务计算逻辑的正确性。
这些测试验证的是"数据注入 → DAO 查询 → 业务计算"的端到端正确性，
而非仅验证"有数据"。

MVD 数据特征（用于断言设计）：
- financial_reports: 8 期 ROE 递增（12.5 → 16.0）
- top10_holders: 3 条股东，持股比例合计 5.5%
- stk_holdernumber: 2 期，股东人数递减（300000 → 295000）
- shibor_daily: 利率递增（on=1.85 → y1=2.50）
"""

from decimal import Decimal

import pytest

from data.cache.cache_manager import CacheManager
from strategies.prompt_validator import (
    check_multi_period_data,
    check_field_exists,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("mvd_data")]


class TestFinancialTrendLogic:
    """Level 3: 财务趋势业务逻辑测试"""

    @pytest.mark.asyncio
    async def test_roe_trend_is_monotonic_increasing(self):
        """验证 MVD 设计的 8 期 ROE 递增趋势能被正确查询"""
        cache = CacheManager()
        df = await cache.get_financial_reports_history("000001.SZ", periods=8)

        assert df is not None
        assert len(df) == 8

        # 注意：DAO 返回 ORDER BY end_date DESC，显式按 end_date ASC 排序后再验证趋势（见约束 3）
        df_sorted = df.sort_values("end_date", ascending=True).reset_index(drop=True)
        roe_values = df_sorted["roe"].tolist()
        # MVD 设计为递增：12.5 → 13.0 → 13.5 → 14.0 → 14.5 → 15.0 → 15.5 → 16.0
        assert roe_values == sorted(roe_values), f"ROE 应递增，实际: {roe_values}"
        assert roe_values[0] == Decimal("12.5")
        assert roe_values[-1] == Decimal("16.0")

    @pytest.mark.asyncio
    async def test_prompt_validator_detects_roe_trend(self):
        """验证 prompt_validator 的 check_multi_period_data 正确识别 ROE 趋势"""
        result = await check_multi_period_data("roe")
        assert result is True

    @pytest.mark.asyncio
    async def test_cashflow_field_detected_by_validator(self):
        """验证 prompt_validator 的 check_field_exists 正确识别现金流字段"""
        result = await check_field_exists("n_cashflow_act")
        assert result is True

    @pytest.mark.asyncio
    async def test_goodwill_field_detected_by_validator(self):
        """验证 prompt_validator 的 check_field_exists 正确识别商誉字段"""
        result = await check_field_exists("goodwill")
        assert result is True


class TestHolderDataLogic:
    """Level 3: 股东数据业务逻辑测试"""

    @pytest.mark.asyncio
    async def test_top10_holders_ratio_sum(self):
        """验证前十大股东持股比例合计正确（2.5 + 1.8 + 1.2 = 5.5）"""
        cache = CacheManager()
        # 注意：必须使用 get_top10_holders（非 batch），batch 版本有 DISTINCT ON 去重（见约束 4）
        df = await cache.holder_dao.get_top10_holders("000001.SZ")

        assert df is not None
        assert len(df) == 3

        total_ratio = df["hold_ratio"].sum()
        assert total_ratio == Decimal("5.5")

    @pytest.mark.asyncio
    async def test_holder_number_decreasing_trend(self):
        """验证股东人数递减趋势（300000 → 295000）"""
        cache = CacheManager()
        df = await cache.holder_dao.get_stk_holdernumber("000001.SZ")

        assert df is not None
        assert len(df) == 2

        # 注意：DAO 返回 ORDER BY end_date DESC（最新期在前），显式按 end_date ASC 排序后
        # 第一行是 2025-06-30（holder_num=300000），第二行是 2025-12-31（holder_num=295000）
        df_sorted = df.sort_values("end_date", ascending=True).reset_index(drop=True)
        holder_nums = df_sorted["holder_num"].tolist()
        # 按时间顺序，股东人数应递减：300000 → 295000
        assert holder_nums[0] > holder_nums[1], f"股东人数应递减，实际: {holder_nums}"
        assert holder_nums[0] == 300000
        assert holder_nums[1] == 295000


class TestMacroDataLogic:
    """Level 3: 宏观数据业务逻辑测试"""

    @pytest.mark.asyncio
    async def test_shibor_rate_curve_ascending(self):
        """验证 Shibor 利率曲线递增（短期 < 长期）"""
        cache = CacheManager()
        df = await cache.macro_dao.get_shibor_latest()

        assert df is not None
        assert not df.empty

        # MVD 设计：on=1.85 < w1=1.95 < w2=2.10 < m1=2.25 < m3=2.35 < m6=2.40 < m9=2.45 < y1=2.50
        # 注意：返回列名是数据库列名（on/1w/2w/1m/3m/6m/9m/1y），非 Python 属性名（见约束 6）
        on_rate = df["on"].iloc[0]
        y1_rate = df["1y"].iloc[0]
        assert on_rate < y1_rate, f"短期利率应低于长期利率: on={on_rate}, 1y={y1_rate}"

    @pytest.mark.asyncio
    async def test_macro_economy_cpi_positive_ppi_negative(self):
        """验证宏观经济 CPI 为正、PPI 为负（MVD 设计场景）"""
        cache = CacheManager()
        df = await cache.macro_dao.get_macro_economy_latest()

        assert df is not None
        assert not df.empty

        cpi = df["cpi"].iloc[0]
        ppi = df["ppi"].iloc[0]
        assert cpi > 0, f"CPI 应为正: {cpi}"
        assert ppi < 0, f"PPI 应为负: {ppi}"
