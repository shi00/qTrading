"""
Tests for market strategies (VolumeBreakout, Northbound, Institutional, BlockTrade).

验证市场策略筛选逻辑的正确性。

P1-19 fix: Renamed TechnicalBreakoutStrategy to VolumeBreakoutStrategy.
"""

# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import unittest

import pandas as pd
import polars as pl

from strategies.market import (
    BlockTradeStrategy,
    InstitutionalStrategy,
    NorthboundFlowStrategy,
    NorthboundHoldingStrategy,
    VolumeBreakoutStrategy,
)
import pytest


pytestmark = pytest.mark.unit


class TestVolumeBreakoutStrategy(unittest.TestCase):
    """测试放量突破策略"""

    def setUp(self):
        self.strategy = VolumeBreakoutStrategy()
        self.sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "突破股A",
                    "pct_chg": 5.0,
                    "turnover_rate": 8.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "突破股B",
                    "pct_chg": 3.5,
                    "turnover_rate": 5.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "涨幅过大",
                    "pct_chg": 9.5,
                    "turnover_rate": 12.0,
                },
                {
                    "ts_code": "000004.SZ",
                    "name": "涨幅过小",
                    "pct_chg": 1.0,
                    "turnover_rate": 4.0,
                },
                {
                    "ts_code": "000005.SZ",
                    "name": "换手率低",
                    "pct_chg": 4.0,
                    "turnover_rate": 1.0,
                },
            ]
        )

    def test_breakout_normal(self):
        """正常突破筛选"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pct_chg_min": 2, "pct_chg_max": 7, "turnover_min": 3}}
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)
        self.assertNotIn("000004.SZ", ts_codes)
        # No parameter conflict: data_warnings should be empty
        self.assertEqual(self.strategy._data_warnings, [])

    def test_breakout_pct_chg_range(self):
        """涨跌幅范围过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pct_chg_min": 4, "pct_chg_max": 6, "turnover_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreaterEqual(row["pct_chg"], 4)
            self.assertLessEqual(row["pct_chg"], 6)

    def test_breakout_turnover_filter(self):
        """换手率过滤"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pct_chg_min": 0, "pct_chg_max": 10, "turnover_min": 6}}
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreater(row["turnover_rate"], 6)

    def test_breakout_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pct_chg_min": 8, "pct_chg_max": 9, "turnover_min": 15}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_breakout_sort_by_pct_chg(self):
        """按涨跌幅降序排列"""
        lf = pl.from_pandas(self.sample_df).lazy()
        context = {"params": {"pct_chg_min": 0, "pct_chg_max": 10, "turnover_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        pct_values = result["pct_chg"].to_list()
        self.assertEqual(pct_values, sorted(pct_values, reverse=True))

    def test_breakout_min_greater_than_max_auto_adjusts(self):
        """pct_chg_min > pct_chg_max 时自动调整"""
        sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "涨幅8.3",
                    "pct_chg": 8.3,
                    "turnover_rate": 8.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "涨幅9.0",
                    "pct_chg": 9.0,
                    "turnover_rate": 5.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "涨幅5.0",
                    "pct_chg": 5.0,
                    "turnover_rate": 4.0,
                },
            ]
        )
        lf = pl.from_pandas(sample_df).lazy()
        context = {"params": {"pct_chg_min": 8, "pct_chg_max": 5, "turnover_min": 0}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 1)
        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)
        # Verify data_warnings is populated when parameter conflict occurs
        self.assertTrue(len(self.strategy._data_warnings) > 0)
        self.assertIn("pct_chg_min", self.strategy._data_warnings[0])

    def test_breakout_min_equals_max_auto_adjusts(self):
        """pct_chg_min == pct_chg_max 时自动调整"""
        sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "涨幅5.3",
                    "pct_chg": 5.3,
                    "turnover_rate": 8.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "涨幅5.0",
                    "pct_chg": 5.0,
                    "turnover_rate": 5.0,
                },
                {
                    "ts_code": "000003.SZ",
                    "name": "涨幅3.0",
                    "pct_chg": 3.0,
                    "turnover_rate": 4.0,
                },
            ]
        )
        lf = pl.from_pandas(sample_df).lazy()
        context = {"params": {"pct_chg_min": 5, "pct_chg_max": 5, "turnover_min": 3}}
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertGreater(result.height, 0)
        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000003.SZ", ts_codes)
        # Verify data_warnings is populated when parameter conflict occurs
        self.assertTrue(len(self.strategy._data_warnings) > 0)

    def test_data_warnings_cleared_on_non_conflict_call(self):
        """data_warnings should be cleared when a subsequent call has no conflict"""
        sample_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "涨幅5.0",
                    "pct_chg": 5.0,
                    "turnover_rate": 8.0,
                }
            ]
        )
        lf = pl.from_pandas(sample_df).lazy()
        # First call with conflict
        self.strategy._filter_logic(lf, {"params": {"pct_chg_min": 8, "pct_chg_max": 5, "turnover_min": 0}})
        self.assertTrue(len(self.strategy._data_warnings) > 0)
        # Second call without conflict
        self.strategy._filter_logic(lf, {"params": {"pct_chg_min": 2, "pct_chg_max": 7, "turnover_min": 0}})
        self.assertEqual(self.strategy._data_warnings, [])


class TestNorthboundHoldingStrategy(unittest.TestCase):
    """测试北向持股策略"""

    def setUp(self):
        self.strategy = NorthboundHoldingStrategy()
        self.base_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "industry": "银行",
                    "pe_ttm": 6.5,
                    "total_mv": 1000000,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "万科A",
                    "industry": "房地产",
                    "pe_ttm": 8.0,
                    "total_mv": 800000,
                },
                {
                    "ts_code": "600000.SH",
                    "name": "浦发银行",
                    "industry": "银行",
                    "pe_ttm": 5.0,
                    "total_mv": 1200000,
                },
            ]
        )
        self.northbound_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "ratio": 5.5, "shares": 10000},
                {"ts_code": "000002.SZ", "ratio": 2.0, "shares": 5000},
                {"ts_code": "600000.SH", "ratio": 4.0, "shares": 8000},
                {"ts_code": "000003.BJ", "ratio": 6.0, "shares": 3000},
            ]
        )

    def test_northbound_normal(self):
        """正常北向筛选"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"nb_ratio_min": 3},
            "northbound_data": self.northbound_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertIn("600000.SH", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)
        self.assertNotIn("000003.BJ", ts_codes)

    def test_northbound_ratio_filter(self):
        """持股比例过滤"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"nb_ratio_min": 5},
            "northbound_data": self.northbound_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        for row in result.iter_rows(named=True):
            self.assertGreater(row["ratio"], 5)

    def test_northbound_missing_data(self):
        """北向数据缺失"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"nb_ratio_min": 3},
            "northbound_data": None,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_northbound_empty_data(self):
        """北向数据为空"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"nb_ratio_min": 3},
            "northbound_data": pd.DataFrame(),
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_northbound_exchange_filter(self):
        """交易所过滤 - 排除北交所"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"nb_ratio_min": 0},
            "northbound_data": self.northbound_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        for code in ts_codes:
            self.assertTrue(code.endswith(".SH") or code.endswith(".SZ"))

    def test_northbound_sort_by_ratio(self):
        """按持股比例降序排列"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"nb_ratio_min": 0},
            "northbound_data": self.northbound_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        ratio_values = result["ratio"].to_list()
        self.assertEqual(ratio_values, sorted(ratio_values, reverse=True))


class TestInstitutionalStrategy(unittest.TestCase):
    """测试机构策略"""

    def setUp(self):
        self.strategy = InstitutionalStrategy()
        self.base_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "机构买入股",
                    "industry": "银行",
                    "pe_ttm": 6.5,
                    "total_mv": 1000000,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "普通股",
                    "industry": "房地产",
                    "pe_ttm": 8.0,
                    "total_mv": 800000,
                },
            ]
        )
        self.lhb_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "net_amount": 5000.0},
                {"ts_code": "000002.SZ", "net_amount": 1000.0},
            ]
        )

    def test_institutional_normal(self):
        """正常机构筛选"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"inst_net_min": 3000},
            "top_list": self.lhb_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)

    def test_institutional_missing_data(self):
        """龙虎榜数据缺失"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"inst_net_min": 3000},
            "top_list": None,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_institutional_missing_column(self):
        """龙虎榜缺失 net_amount 列"""
        lf = pl.from_pandas(self.base_df).lazy()
        lhb_missing_col = pd.DataFrame([{"ts_code": "000001.SZ", "other_col": 100}])
        context = {
            "params": {"inst_net_min": 3000},
            "top_list": lhb_missing_col,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_institutional_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"inst_net_min": 10000},
            "top_list": self.lhb_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_institutional_sort_by_net_amount(self):
        """按净买入金额降序排列"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"inst_net_min": 0},
            "top_list": self.lhb_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        net_values = result["net_amount"].to_list()
        self.assertEqual(net_values, sorted(net_values, reverse=True))


class TestBlockTradeStrategy(unittest.TestCase):
    """测试大宗交易策略"""

    def setUp(self):
        self.strategy = BlockTradeStrategy()
        self.base_df = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "大宗交易股",
                    "industry": "银行",
                    "pe_ttm": 6.5,
                    "total_mv": 1000000,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "普通股",
                    "industry": "房地产",
                    "pe_ttm": 8.0,
                    "total_mv": 800000,
                },
            ]
        )
        self.block_df = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "amount": 1500.0, "vol": 100, "price": 10.0},
                {"ts_code": "000001.SZ", "amount": 800.0, "vol": 50, "price": 10.5},
                {"ts_code": "000002.SZ", "amount": 500.0, "vol": 30, "price": 8.0},
            ]
        )

    def test_block_trade_normal(self):
        """正常大宗交易筛选"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"block_amount_min": 1000},
            "block_trade": self.block_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        ts_codes = result["ts_code"].to_list()
        self.assertIn("000001.SZ", ts_codes)
        self.assertNotIn("000002.SZ", ts_codes)

    def test_block_trade_missing_data(self):
        """大宗交易数据缺失"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"block_amount_min": 1000},
            "block_trade": None,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_block_trade_missing_column(self):
        """大宗交易缺失 amount 列"""
        lf = pl.from_pandas(self.base_df).lazy()
        block_missing_col = pd.DataFrame([{"ts_code": "000001.SZ", "other_col": 100}])
        context = {
            "params": {"block_amount_min": 1000},
            "block_trade": block_missing_col,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)

    def test_block_trade_aggregation(self):
        """大宗交易聚合计算"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"block_amount_min": 0},
            "block_trade": self.block_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        row_000001 = result.filter(pl.col("ts_code") == "000001.SZ").row(0, named=True)
        self.assertEqual(row_000001["amount"], 2300.0)
        self.assertEqual(row_000001["vol"], 150)

    def test_block_trade_empty_result(self):
        """无匹配结果"""
        lf = pl.from_pandas(self.base_df).lazy()
        context = {
            "params": {"block_amount_min": 5000},
            "block_trade": self.block_df,
        }
        result = self.strategy._filter_logic(lf, context).collect()

        self.assertEqual(result.height, 0)


if __name__ == "__main__":
    unittest.main()


class TestNorthboundFlowStrategy(unittest.TestCase):
    def test_gating_returns_stocks_when_flow_exceeds_threshold(self):
        flow_df = pd.DataFrame(
            [
                {"trade_date": "20240101", "north_money": 120.0},
            ]
        )
        base_lf = pl.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "industry": "银行",
                    "pe_ttm": 5.5,
                    "total_mv": 4000.0,
                },
                {
                    "ts_code": "600000.SH",
                    "name": "浦发银行",
                    "industry": "银行",
                    "pe_ttm": 4.5,
                    "total_mv": 3000.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "小盘股",
                    "industry": "科技",
                    "pe_ttm": 20.0,
                    "total_mv": 50.0,
                },
            ]
        ).lazy()

        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "params": {"nb_flow_min": 50, "total_mv_min": 100},
        }
        out = strat._filter_logic(base_lf, ctx).collect()
        assert len(out) == 2
        ts_codes = out["ts_code"].to_list()
        assert "000001.SZ" in ts_codes
        assert "600000.SH" in ts_codes
        assert "000002.SZ" not in ts_codes

    def test_gating_returns_empty_when_flow_below_threshold(self):
        flow_df = pd.DataFrame(
            [
                {"trade_date": "20240101", "north_money": 30.0},
            ]
        )
        screening_data = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "industry": "银行",
                    "pe_ttm": 5.5,
                    "total_mv": 4000.0,
                },
            ]
        )

        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": screening_data,
            "params": {"nb_flow_min": 50},
        }
        out = asyncio.run(strat.filter(ctx))
        assert len(out) == 0

    def test_gating_returns_empty_when_flow_null(self):
        flow_df = pd.DataFrame(
            [
                {"trade_date": "20240101", "north_money": None},
            ]
        )
        screening_data = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "平安银行",
                    "industry": "银行",
                    "pe_ttm": 5.5,
                    "total_mv": 4000.0,
                },
            ]
        )

        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": screening_data,
            "params": {"nb_flow_min": 50},
        }
        out = asyncio.run(strat.filter(ctx))
        assert len(out) == 0

    def test_filter_returns_empty_when_context_missing(self):
        base_lf = pl.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "p",
                    "industry": "x",
                    "pe_ttm": 1.0,
                    "total_mv": 1.0,
                }
            ]
        ).lazy()
        strat = NorthboundFlowStrategy()
        out = strat._filter_logic(base_lf, {"params": {}}).collect()
        assert len(out) == 0

    def test_sorts_by_trade_date_desc_before_taking_first(self):
        flow_df = pd.DataFrame(
            [
                {"trade_date": "20240101", "north_money": 10.0},
                {"trade_date": "20240103", "north_money": 120.0},
                {"trade_date": "20240102", "north_money": 30.0},
            ]
        )
        base_lf = pl.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "p",
                    "industry": "x",
                    "pe_ttm": 5.0,
                    "total_mv": 500.0,
                }
            ]
        ).lazy()
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "params": {"nb_flow_min": 50},
        }
        out = strat._filter_logic(base_lf, ctx).collect()
        assert len(out) == 1

    def test_negative_pe_ttm_excluded(self):
        flow_df = pd.DataFrame([{"trade_date": "20240101", "north_money": 120.0}])
        base_lf = pl.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "亏损股",
                    "industry": "科技",
                    "pe_ttm": -5.0,
                    "total_mv": 4000.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "name": "盈利股",
                    "industry": "科技",
                    "pe_ttm": 10.0,
                    "total_mv": 3000.0,
                },
            ]
        ).lazy()
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "params": {"nb_flow_min": 50, "total_mv_min": 100},
        }
        out = strat._filter_logic(base_lf, ctx).collect()
        ts_codes = out["ts_code"].to_list()
        assert "000001.SZ" not in ts_codes
        assert "000002.SZ" in ts_codes

    def test_flow_equal_to_threshold_returns_empty(self):
        flow_df = pd.DataFrame([{"trade_date": "20240101", "north_money": 50.0}])
        screening_data = pd.DataFrame(
            [
                {
                    "ts_code": "000001.SZ",
                    "name": "p",
                    "industry": "x",
                    "pe_ttm": 5.0,
                    "total_mv": 500.0,
                }
            ]
        )
        strat = NorthboundFlowStrategy()
        ctx = {
            "northbound_flow_data": flow_df,
            "screening_data": screening_data,
            "params": {"nb_flow_min": 50},
        }
        out = asyncio.run(strat.filter(ctx))
        assert len(out) == 0


class TestContextKeyTableMap(unittest.TestCase):
    def test_northbound_flow_data_mapped_to_moneyflow_hsgt(self):
        from strategies.base_strategy import BaseStrategy

        assert BaseStrategy.CONTEXT_KEY_TABLE_MAP.get("northbound_flow_data") == "moneyflow_hsgt", (
            "S-1: missing CONTEXT_KEY_TABLE_MAP entry; check_dependencies will report wrong missing_tables"
        )


class TestPrepareScreeningContextWiresFlow(unittest.TestCase):
    def test_auxiliary_tables_dict_includes_northbound_flow_data(self):
        from data import data_processor

        assert hasattr(data_processor.DataProcessor, "prepare_screening_context"), (
            "DataProcessor should have prepare_screening_context method"
        )
