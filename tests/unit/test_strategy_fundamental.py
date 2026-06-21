"""
Tests for fundamental strategies (Value, Growth, Dividend, CashFlow, LargePE).

验证基本面策略筛选逻辑的正确性。
"""

import unittest

import pandas as pd
import polars as pl

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
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "中成长",
                    "or_yoy": 25.0,
                    "netprofit_yoy": 30.0,
                    "roe": 18.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "低成长",
                    "or_yoy": 15.0,
                    "netprofit_yoy": 20.0,
                    "roe": 10.0,
                },
                {
                    "ts_code": "000004.SZ",
                    "name": "负增长",
                    "or_yoy": -5.0,
                    "netprofit_yoy": -10.0,
                    "roe": 5.0,
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


class TestDividendStrategy(unittest.TestCase):
    """测试红利策略"""

    def setUp(self):
        self.strategy = DividendStrategy()
        self.sample_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "高股息", "dv_ttm": 5.5},
                {"ts_code": "000002.SZ", "name": "中股息", "dv_ttm": 3.5},
                {"ts_code": "000003.SZ", "name": "低股息", "dv_ttm": 1.5},
                {"ts_code": "000004.SZ", "name": "无股息", "dv_ttm": 0.0},
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

    def test_large_pe_strategy_declares_dependencies(self):
        """LargePEStrategy 显式声明 required_context_keys 与 required_tables"""
        self.assertEqual(self.strategy.required_context_keys, ("screening_data",))
        self.assertEqual(self.strategy.required_tables, ("daily_quotes",))


if __name__ == "__main__":
    unittest.main()
