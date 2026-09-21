"""
Tests for fundamental strategies (Value, Growth, Dividend, CashFlow, LargePE).

验证基本面策略筛选逻辑的正确性。
"""

import unittest

import pandas as pd
import polars as pl

from core.errors import StrategyParamError
from strategies.fundamental import (
    CashFlowStrategy,
    DividendStrategy,
    GrowthStrategy,
    LargePEStrategy,
    ValueStrategy,
)
import pytest


pytestmark = pytest.mark.unit


class TestValueStrategy(unittest.TestCase):
    """测试价值策略"""

    def setUp(self):
        self.strategy = ValueStrategy()
        self.sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "pe_ttm": 6.5,
                    "pb": 0.8,
                    "dv_ttm": 3.5,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "万科A",
                    "pe_ttm": 8.0,
                    "pb": 1.2,
                    "dv_ttm": 2.8,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "测试股票",
                    "pe_ttm": 25.0,
                    "pb": 2.5,
                    "dv_ttm": 1.0,
                },
                {
                    "ts_code": "000004.SZ",
                    "name": "高PE股",
                    "pe_ttm": 50.0,
                    "pb": 4.0,
                    "dv_ttm": 0.5,
                },
                {
                    "ts_code": "000005.SZ",
                    "name": "亏损股",
                    "pe_ttm": -5.0,
                    "pb": 0.5,
                    "dv_ttm": 0.0,
                },
            ]
        )

    def test_value_strategy_normal(self):
        """正常价值筛选"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 5, "pe_max": 20, "pb_max": 3, "dv_min": 2}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertTrue(result.height > 0)
        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)

    def test_value_strategy_pe_range(self):
        """PE 范围过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 7, "pe_max": 10, "pb_max": 5, "dv_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000002.SZ", ts_codes)
        self.assertNotIn("000001.SZ", ts_codes)

    def test_value_strategy_pb_filter(self):
        """PB 过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 0, "pe_max": 100, "pb_max": 1.0, "dv_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertLessEqual(row["pb"], 1.0)

    def test_value_strategy_dividend_yield(self):
        """股息率过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 0, "pe_max": 100, "pb_max": 10, "dv_min": 3.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreater(row["dv_ttm"], 3.0)

    def test_value_strategy_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 5, "pe_max": 6, "pb_max": 0.5, "dv_min": 5.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_value_strategy_missing_columns(self):
        """缺失列处理 - 应抛出异常"""
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "测试"},
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"pe_min": 5, "pe_max": 20, "pb_max": 3, "dv_min": 2}}
        with self.assertRaises(pl.exceptions.ColumnNotFoundError):
            self.strategy._filter_logic(lf, context).collect()

    def test_value_strategy_contradictory_pe_range(self):
        """矛盾区间参数 (pe_min > pe_max) 应抛 StrategyParamError，绝不静默返回空集。

        D3-3: 参数下限大于上限时，若静默返回空集，UI 会误读为
        "市场上没有这种股票"（虚假市场信息），而真相是参数矛盾。
        """
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 30, "pe_max": 20, "pb_max": 3, "dv_min": 2}}
        with self.assertRaises(StrategyParamError):
            self.strategy._filter_logic(lf, context).collect()

    def test_value_strategy_sort_by_dividend(self):
        """按股息率降序排列"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pe_min": 0, "pe_max": 100, "pb_max": 10, "dv_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        dv_values = result["dv_ttm"].to_list()
        self.assertEqual(dv_values, sorted(dv_values, reverse=True))


class TestGrowthStrategy(unittest.TestCase):
    """测试成长策略"""

    def setUp(self):
        self.strategy = GrowthStrategy()
        self.sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "高成长",
                    "or_yoy": 30.0,
                    "netprofit_yoy": 40.0,
                    "roe": 20.0,
                    "n_income": 5000.0,
                    "grossprofit_margin": 40.0,
                    "gpm_prev": 38.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "中成长",
                    "or_yoy": 25.0,
                    "netprofit_yoy": 30.0,
                    "roe": 18.0,
                    "n_income": 3000.0,
                    "grossprofit_margin": 35.0,
                    "gpm_prev": 36.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "低成长",
                    "or_yoy": 15.0,
                    "netprofit_yoy": 20.0,
                    "roe": 10.0,
                    "n_income": 1000.0,
                    "grossprofit_margin": 30.0,
                    "gpm_prev": 32.0,
                },
                {
                    "ts_code": "000004.SZ",
                    "name": "负增长",
                    "or_yoy": -5.0,
                    "netprofit_yoy": -10.0,
                    "roe": 5.0,
                    "n_income": -500.0,
                    "grossprofit_margin": 20.0,
                    "gpm_prev": 22.0,
                },
            ]
        )

    def test_growth_strategy_normal(self):
        """正常成长筛选"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"revenue_growth_min": 20, "profit_growth_min": 25, "roe_min": 15}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertTrue(result.height > 0)
        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)
        self.assertNotIn("000004.SZ", ts_codes)

    def test_growth_strategy_revenue_growth(self):
        """营收增长过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"revenue_growth_min": 28, "profit_growth_min": 0, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)

    def test_growth_strategy_profit_growth(self):
        """利润增长过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 35, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)

    def test_growth_strategy_roe_filter(self):
        """ROE 过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 0, "roe_min": 19}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreater(row["roe"], 19)

    def test_growth_strategy_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"revenue_growth_min": 50, "profit_growth_min": 50, "roe_min": 30}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_growth_strategy_sort_by_roe(self):
        """按 ROE 降序排列"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 0, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        roe_values = result["roe"].to_list()
        self.assertEqual(roe_values, sorted(roe_values, reverse=True))

    def test_growth_strategy_base_loss_loss_narrowing_excluded(self):
        """SC-03: 基期亏损"亏损收窄"（上年-1000万→本期-100万）netprofit_yoy=+90 无业务含义，
        绝对盈利下限 n_income > 0 必须剔除仍在亏损的股票。"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000009.SZ",
                    "name": "亏损收窄",
                    "or_yoy": 30.0,
                    "netprofit_yoy": 90.0,
                    "roe": 20.0,
                    "n_income": -100.0,
                    "grossprofit_margin": 40.0,
                    "gpm_prev": 38.0,
                }
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 25, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()
        self.assertEqual(result.height, 0)

    def test_growth_strategy_loss_to_profit_passes(self):
        """SC-03: 扭亏为盈（本期已盈利）n_income > 0 放行，netprofit_yoy 高位通过门槛。"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000010.SZ",
                    "name": "扭亏为盈",
                    "or_yoy": 30.0,
                    "netprofit_yoy": 300.0,
                    "roe": 20.0,
                    "n_income": 200.0,
                    "grossprofit_margin": 40.0,
                    "gpm_prev": 38.0,
                }
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 25, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()
        self.assertEqual(result.height, 1)

    def test_growth_strategy_null_net_income_passes(self):
        """SC-03: n_income 缺失（null）放行不伪造（R21），交数据/AI 后续处理。"""
        df = (
            pd.DataFrame(
                [
                    {
                        "ts_code": "000011.SZ",
                        "name": "数据缺失",
                        "or_yoy": 30.0,
                        "netprofit_yoy": 40.0,
                        "roe": 20.0,
                        "n_income": None,
                        "grossprofit_margin": 40.0,
                        "gpm_prev": 38.0,
                    }
                ]
            )
            # 全 None 列被 pandas 推断为 object，显式转 float64 模拟 SQL 数值列缺失（NaN→null）
            .astype({"n_income": "float64"})
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 25, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()
        self.assertEqual(result.height, 1)

    def test_growth_strategy_growth_quality_doubt_downranks(self):
        """SC-03: 增长质量存疑（净利增速 > 2 倍营收增速 且 毛利率未改善）降权置后，而非硬过滤。"""
        df = pd.DataFrame(
            [
                {
                    "ts_code": "000021.SZ",
                    "name": "存疑高增长",
                    "or_yoy": 20.0,
                    "netprofit_yoy": 60.0,
                    "roe": 22.0,
                    "n_income": 5000.0,
                    "grossprofit_margin": 35.0,
                    "gpm_prev": 38.0,
                },
                {
                    "ts_code": "000022.SZ",
                    "name": "正常高增长",
                    "or_yoy": 20.0,
                    "netprofit_yoy": 30.0,
                    "roe": 20.0,
                    "n_income": 4000.0,
                    "grossprofit_margin": 35.0,
                    "gpm_prev": 30.0,
                },
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"revenue_growth_min": 0, "profit_growth_min": 25, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()
        # 存疑行 netprofit_yoy=60 > 2*or_yoy=40 且 gpm(35) <= gpm_prev(38) → doubt=1，排后
        self.assertEqual(result["ts_code"].to_list(), ["000022.SZ", "000021.SZ"])
        doubts = dict(zip(result["ts_code"].to_list(), result["growth_quality_doubt"].to_list(), strict=True))
        self.assertEqual(doubts["000021.SZ"], 1)
        self.assertEqual(doubts["000022.SZ"], 0)

    def test_growth_strategy_build_attribution_doubt_condition(self):
        """SC-03: 归因仅对存疑行（doubt=1）追加 growth_quality_doubt 条件；正常行不渲染该条件。"""
        s = self.strategy
        attr_doubt = s.build_attribution(
            {"or_yoy": 20.0, "netprofit_yoy": 60.0, "roe": 22.0, "growth_quality_doubt": 1},
            total_candidates=10,
            context={"params": {}},
        )
        self.assertTrue(any(c.column == "growth_quality_doubt" for c in attr_doubt.conditions))
        attr_normal = s.build_attribution(
            {"or_yoy": 20.0, "netprofit_yoy": 30.0, "roe": 20.0, "growth_quality_doubt": 0},
            total_candidates=10,
            context={"params": {}},
        )
        self.assertFalse(any(c.column == "growth_quality_doubt" for c in attr_normal.conditions))


class TestDividendStrategy(unittest.TestCase):
    """测试红利策略"""

    def setUp(self):
        self.strategy = DividendStrategy()
        self.sample_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "高股息", "dv_ttm": 5.5, "roe": 15.0, "or_yoy": 10.0},
                {"ts_code": "000002.SZ", "name": "中股息", "dv_ttm": 3.5, "roe": 12.0, "or_yoy": 5.0},
                {"ts_code": "000003.SZ", "name": "低股息", "dv_ttm": 1.5, "roe": 8.0, "or_yoy": 0.0},
                {"ts_code": "000004.SZ", "name": "无股息", "dv_ttm": 0.0, "roe": 6.0, "or_yoy": -5.0},
            ]
        )

    def test_dividend_strategy_normal(self):
        """正常红利筛选"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"dv_min": 4.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 1)
        self.assertEqual(result["ts_code"][0], "000001.SZ")

    def test_dividend_strategy_yield_range(self):
        """股息率范围"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"dv_min": 2.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)

    def test_dividend_strategy_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"dv_min": 10.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_dividend_strategy_sort_by_yield(self):
        """按股息率降序排列"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"dv_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        dv_values = result["dv_ttm"].to_list()
        self.assertEqual(dv_values, sorted(dv_values, reverse=True))

    def test_dividend_strategy_fake_high_yield_roe_excluded(self):
        """SC-02: 假高息防护——roe<=0（盈利能力恶化）的高股息标的不属于"真高息"，必须剔除。"""
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "正常", "dv_ttm": 5.5, "roe": 15.0, "or_yoy": 10.0},
                {"ts_code": "000002.SZ", "name": "亏损", "dv_ttm": 6.0, "roe": 0.0, "or_yoy": 10.0},
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"dv_min": 4.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)

    def test_dividend_strategy_fake_high_yield_or_yoy_excluded(self):
        """SC-02: 假高息防护——or_yoy<=-20（成长性显著恶化）的高股息标的不属于"真高息"，必须剔除。"""
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "正常", "dv_ttm": 5.5, "roe": 15.0, "or_yoy": 10.0},
                {"ts_code": "000002.SZ", "name": "衰退", "dv_ttm": 6.0, "roe": 10.0, "or_yoy": -20.0},
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"dv_min": 4.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)

    def test_dividend_strategy_null_roe_or_yoy_passes(self):
        """SC-02: 假高息防护对财务缺失（null）放行——财报缺失属数据问题而非风险信号（R21 精神）。"""
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "有财报", "dv_ttm": 5.5, "roe": 15.0, "or_yoy": 10.0},
                {"ts_code": "000002.SZ", "name": "财报缺失", "dv_ttm": 6.0, "roe": None, "or_yoy": None},
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"dv_min": 4.0}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertEqual(sorted(ts_codes), ["000001.SZ", "000002.SZ"])

    def test_dividend_sort_for_ai_ascending(self):
        """SC-02: AI 截断前按 dv_ttm 升序重排，极端高息候选最后进入 AI 分析队列。"""
        df = self.sample_df.copy()
        out = self.strategy._sort_for_ai(df)
        self.assertEqual(out["dv_ttm"].to_list(), [0.0, 1.5, 3.5, 5.5])

    def test_dividend_sort_for_ai_empty(self):
        """SC-02: 空候选集直接返回，不排序不报错。"""
        out = self.strategy._sort_for_ai(pd.DataFrame())
        self.assertTrue(out.empty)

    def test_dividend_sort_for_ai_missing_column(self):
        """SC-02: 候选集缺 dv_ttm 列时原样返回（防御，正常路径必含该列）。"""
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["缺列"]})
        out = self.strategy._sort_for_ai(df)
        self.assertEqual(out["ts_code"].to_list(), ["000001.SZ"])


class TestCashFlowStrategy(unittest.TestCase):
    """测试现金流策略"""

    def setUp(self):
        self.strategy = CashFlowStrategy()
        self.sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "低负债高ROE",
                    "debt_to_assets": 30.0,
                    "roe": 15.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "中负债中ROE",
                    "debt_to_assets": 45.0,
                    "roe": 12.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "高负债低ROE",
                    "debt_to_assets": 70.0,
                    "roe": 5.0,
                },
                {
                    "ts_code": "000004.SZ",
                    "name": "低负债低ROE",
                    "debt_to_assets": 20.0,
                    "roe": 8.0,
                },
            ]
        )

    def test_cashflow_strategy_normal(self):
        """正常现金流筛选"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"debt_max": 50, "roe_min": 10}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)

    def test_cashflow_strategy_debt_filter(self):
        """负债率过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"debt_max": 35, "roe_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertLess(row["debt_to_assets"], 35)

    def test_cashflow_strategy_roe_filter(self):
        """ROE 过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"debt_max": 100, "roe_min": 13}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreater(row["roe"], 13)

    def test_cashflow_strategy_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"debt_max": 20, "roe_min": 20}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)


class TestLargePEStrategy(unittest.TestCase):
    """测试大盘低PE策略"""

    def test_get_parameters_declares_unit(self):
        """UX-03: market_cap_min 声明 yi_cny 单位 (total_mv 万元列, *10000=亿)."""
        unit_map = {p["name"]: p.get("unit") for p in self.strategy.get_parameters()}
        assert unit_map.get("market_cap_min") == "yi_cny"

    def setUp(self):
        self.strategy = LargePEStrategy()
        self.sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "大盘低PE",
                    "total_mv": 10000000,
                    "pe_ttm": 10.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "中盘中PE",
                    "total_mv": 3000000,
                    "pe_ttm": 15.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "小盘高PE",
                    "total_mv": 500000,
                    "pe_ttm": 25.0,
                },
                {
                    "ts_code": "000004.SZ",
                    "name": "大盘高PE",
                    "total_mv": 8000000,
                    "pe_ttm": 30.0,
                },
            ]
        )

    def test_large_pe_strategy_normal(self):
        """正常大盘低PE筛选"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"market_cap_min": 500, "pe_max": 15}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)

    def test_large_pe_strategy_market_cap_filter(self):
        """市值过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"market_cap_min": 800, "pe_max": 100}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreater(row["total_mv"], 800 * 10000)

    def test_large_pe_strategy_pe_filter(self):
        """PE 过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"market_cap_min": 0, "pe_max": 12}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertLessEqual(row["pe_ttm"], 12)

    def test_large_pe_strategy_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"market_cap_min": 2000, "pe_max": 5}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_large_pe_strategy_sort_by_market_cap(self):
        """按市值降序排列"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"market_cap_min": 0, "pe_max": 100}}
        result = self.strategy._filter_logic(lf, context).collect()

        mv_values = result["total_mv"].to_list()
        self.assertEqual(mv_values, sorted(mv_values, reverse=True))

    def test_large_pe_boundary_uses_converted_threshold(self):
        """R20/D2-M1: cap_min 经统一入口换算为精确阈值，严格 `>` 排除恰等于阈值的行。

        500(亿) 换算到万元列 = 500 * 10000 = 5_000_000 万元。恰等于的行应被严格 `>`
        排除、高于一个单位（5_000_001）的行保留，以固定换算返回值的精确性。
        """
        df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "恰等边界", "total_mv": 5_000_000, "pe_ttm": 10.0},
                {"ts_code": "000002.SZ", "name": "超边界", "total_mv": 5_000_001, "pe_ttm": 10.0},
            ]
        )
        lf = pl.from_pandas(df).lazy()
        context = {"params": {"market_cap_min": 500, "pe_max": 100}}
        ts_codes = self.strategy._filter_logic(lf, context).collect()["ts_code"].to_list()
        self.assertNotIn("000001.SZ", ts_codes)
        self.assertIn("000002.SZ", ts_codes)

    def test_large_pe_strategy_declares_dependencies(self):
        """LargePEStrategy 显式声明 required_context_keys 与 required_tables"""
        self.assertEqual(self.strategy.required_context_keys, ("screening_data",))
        self.assertEqual(self.strategy.required_tables, ("daily_quotes",))


if __name__ == "__main__":
    unittest.main()
