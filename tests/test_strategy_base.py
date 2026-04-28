import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.persistence.quality_gate import QualityTier
from strategies.ai_mixin import AIStrategyMixin
from strategies.all_strategies import StrategyManager
from strategies.fundamental import (
    CashFlowStrategy,
    DividendStrategy,
    GrowthStrategy,
    LargePEStrategy,
    ValueStrategy,
)
from strategies.market import (
    BlockTradeStrategy,
    InstitutionalStrategy,
    NorthboundStrategy,
    TechnicalBreakoutStrategy,
)
from strategies.oversold_strategy import OversoldStrategy
from strategies.utils import fmt_val, safe_float


class TestStrategies(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mgr = StrategyManager()

        self.base_data = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "name": ["Stock A", "Stock B", "Stock C"],
                "industry": ["Bank", "Real Estate", "Tech"],
                "pe_ttm": [10.0, 50.0, 5.0],
                "pb": [1.0, 5.0, 0.8],
                "dv_ttm": [3.0, 0.5, 4.5],
                "pct_chg": [3.0, 8.0, -4.0],
                "turnover_rate": [5.0, 1.0, 2.0],
                "total_mv": [6000000, 100000, 200000],
                "or_yoy": [25.0, 10.0, 5.0],
                "netprofit_yoy": [30.0, 5.0, -10.0],
                "roe": [16.0, 8.0, 5.0],
                "debt_to_assets": [40.0, 80.0, 60.0],
            },
        )

        self.context = {"screening_data": self.base_data, "fundamental_screening_data": self.base_data}

    def test_manager(self):
        s = self.mgr.get_strategy("value")
        self.assertIsInstance(s, ValueStrategy)
        self.assertIsNotNone(self.mgr.get_strategy("growth"))
        self.assertTrue(len(self.mgr.get_all_names()) > 0)

    async def test_value_strategy(self):
        s = ValueStrategy()
        res = await s.filter(self.context)
        self.assertIn("000001.SZ", res["ts_code"].values)
        self.assertNotIn("000002.SZ", res["ts_code"].values)

    async def test_growth_strategy(self):
        s = GrowthStrategy()
        res = await s.filter(self.context)
        self.assertIn("000001.SZ", res["ts_code"].values)
        self.assertEqual(len(res), 1)

    async def test_dividend_strategy(self):
        s = DividendStrategy()
        res = await s.filter(self.context)
        self.assertIn("000003.SZ", res["ts_code"].values)
        self.assertNotIn("000001.SZ", res["ts_code"].values)

    async def test_technical_breakout(self):
        s = TechnicalBreakoutStrategy()
        res = await s.filter(self.context)
        self.assertIn("000001.SZ", res["ts_code"].values)
        self.assertNotIn("000002.SZ", res["ts_code"].values)

    async def test_northbound(self):
        nb_data = pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ"], "ratio": [6.0, 1.0]},
        )
        ctx = {"northbound_data": nb_data, "screening_data": self.base_data}
        s = NorthboundStrategy()
        res = await s.filter(ctx)
        self.assertIn("000001.SZ", res["ts_code"].values)
        self.assertNotIn("000002.SZ", res["ts_code"].values)
        self.assertTrue((await s.filter({})).empty)

    async def test_oversold(self):
        s = OversoldStrategy()
        dp_mock = MagicMock()
        dp_mock._quality_tier = 2
        trade_calendar_mock = MagicMock()
        trade_calendar_mock.get_latest_trade_date = AsyncMock(return_value=datetime.date(2023, 1, 1))
        trade_calendar_mock.get_start_date_by_trade_days = AsyncMock(return_value=datetime.date(2022, 8, 1))
        dp_mock.trade_calendar = trade_calendar_mock
        cache_mock = MagicMock()
        cache_mock.get_latest_trade_date = AsyncMock(return_value="20230101")
        dates = pd.date_range(end="20230101", periods=30).strftime("%Y%m%d").tolist()
        c_prices = list(range(35, 5, -1))
        history_data = []
        for d, p in zip(dates, c_prices, strict=True):
            history_data.append({"ts_code": "000003.SZ", "trade_date": d, "close": p, "adj_factor": 1.0})
        history_df = pd.DataFrame(history_data)
        cache_mock.get_daily_quotes = AsyncMock(return_value=history_df)
        dp_mock.cache = cache_mock
        ctx = self.context.copy()
        ctx["data_processor"] = dp_mock
        res = await s.filter(ctx)
        self.assertIn("000003.SZ", res["ts_code"].values)

    async def test_oversold_volume_threshold_filters_candidates(self):
        s = OversoldStrategy()
        dp_mock = MagicMock()
        dp_mock._quality_tier = 2
        trade_calendar_mock = MagicMock()
        trade_calendar_mock.get_latest_trade_date = AsyncMock(return_value=datetime.date(2023, 1, 30))
        trade_calendar_mock.get_start_date_by_trade_days = AsyncMock(return_value=datetime.date(2022, 8, 1))
        dp_mock.trade_calendar = trade_calendar_mock

        dates = pd.date_range(end="2023-01-30", periods=30).strftime("%Y%m%d").tolist()
        history_data = []
        for d, p, vol in zip(dates, range(40, 10, -1), [100] * 29 + [160], strict=True):
            history_data.append({"ts_code": "000003.SZ", "trade_date": d, "close": p, "adj_factor": 1.0, "vol": vol})
        history_df = pd.DataFrame(history_data)

        cache_mock = MagicMock()
        cache_mock.get_daily_quotes = AsyncMock(return_value=history_df)
        dp_mock.cache = cache_mock

        ctx = self.context.copy()
        ctx["data_processor"] = dp_mock
        ctx["params"] = {"vol_ratio_threshold": 1.5}
        res_lo = await s.filter(ctx)
        self.assertIn("000003.SZ", res_lo["ts_code"].values)

        ctx["params"] = {"vol_ratio_threshold": 1.7}
        res_hi = await s.filter(ctx)
        self.assertTrue(res_hi.empty)

    async def test_institutional(self):
        lhb_data = pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ"], "net_amount": [3500.0, 100.0]},
        )
        ctx = {"top_list": lhb_data, "screening_data": self.base_data}
        s = InstitutionalStrategy()
        res = await s.filter(ctx)
        self.assertIn("000001.SZ", res["ts_code"].values)
        self.assertNotIn("000002.SZ", res["ts_code"].values)

    async def test_block_trade(self):
        block_data_pass = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "amount": [1200], "vol": [10], "price": [10]},
        )
        ctx = {"block_trade": block_data_pass, "screening_data": self.base_data}
        s = BlockTradeStrategy()
        res = await s.filter(ctx)
        self.assertIn("000001.SZ", res["ts_code"].values)

    async def test_cashflow(self):
        s = CashFlowStrategy()
        res = await s.filter(self.context)
        self.assertIn("000001.SZ", res["ts_code"].values)

    async def test_large_pe(self):
        s = LargePEStrategy()
        res = await s.filter(self.context)
        self.assertIn("000001.SZ", res["ts_code"].values)

    async def test_empty_input(self):
        s = ValueStrategy()
        self.assertTrue((await s.filter({})).empty)
        self.assertTrue((await s.filter({"screening_data": None})).empty)
        self.assertTrue((await s.filter({"screening_data": pd.DataFrame()})).empty)


class TestAIIntegration(unittest.TestCase):
    STRATEGY_CLASSES = [
        ValueStrategy,
        GrowthStrategy,
        DividendStrategy,
        CashFlowStrategy,
        LargePEStrategy,
        TechnicalBreakoutStrategy,
        NorthboundStrategy,
        InstitutionalStrategy,
        BlockTradeStrategy,
    ]

    MARKET_STRATEGY_CLASSES = [
        TechnicalBreakoutStrategy,
        NorthboundStrategy,
        InstitutionalStrategy,
        BlockTradeStrategy,
    ]

    FUNDAMENTAL_STRATEGY_CLASSES = [
        ValueStrategy,
        GrowthStrategy,
        DividendStrategy,
        CashFlowStrategy,
        LargePEStrategy,
    ]

    def test_all_strategies_inherit_ai_mixin(self):
        for cls in self.STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                instance = cls()
                self.assertIsInstance(instance, AIStrategyMixin, f"{cls.__name__} should inherit AIStrategyMixin")

    def test_fundamental_strategies_have_get_ai_context(self):
        for cls in self.FUNDAMENTAL_STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                instance = cls()
                self.assertTrue(
                    hasattr(instance, "get_ai_context"),
                    f"{cls.__name__} should have get_ai_context()",
                )
                ctx = instance.get_ai_context({"pe_ttm": 10, "pb": 1.5})
                self.assertIsInstance(ctx, str)
                self.assertTrue(len(ctx) > 0, f"{cls.__name__}.get_ai_context() should return non-empty string")

    def test_market_strategies_use_default_get_ai_context(self):
        for cls in self.MARKET_STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                instance = cls()
                ctx = instance.get_ai_context({"pe_ttm": 10})
                self.assertEqual(ctx, "", f"{cls.__name__} should use default empty get_ai_context()")

    def test_oversold_inherits_ai_mixin(self):
        instance = OversoldStrategy()
        self.assertIsInstance(instance, AIStrategyMixin)
        self.assertTrue(hasattr(instance, "enable_ai_analysis"))
        self.assertTrue(instance.enable_ai_analysis)
        self.assertTrue(hasattr(instance, "_sort_for_ai"))

    def test_mro_includes_ai_mixin(self):
        for cls in self.STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                mro_names = [c.__name__ for c in cls.__mro__]
                self.assertIn("AIStrategyMixin", mro_names, f"{cls.__name__} MRO missing AIStrategyMixin")
                self.assertIn("PolarsBaseStrategy", mro_names, f"{cls.__name__} MRO missing PolarsBaseStrategy")

    def test_quality_tier_defaults(self):
        for cls in self.STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                instance = cls()
                tier = instance.required_quality_tier
                self.assertIsInstance(tier, QualityTier)
                if cls in (
                    TechnicalBreakoutStrategy,
                    ValueStrategy,
                    GrowthStrategy,
                    DividendStrategy,
                    CashFlowStrategy,
                    LargePEStrategy,
                ):
                    self.assertEqual(tier, QualityTier.SILVER)
                else:
                    self.assertEqual(tier, QualityTier.BRONZE)

    def test_market_strategies_disable_ai(self):
        for cls in self.MARKET_STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                instance = cls()
                self.assertFalse(instance.enable_ai_analysis, f"{cls.__name__} should have enable_ai_analysis=False")

    def test_fundamental_strategies_enable_ai(self):
        for cls in self.FUNDAMENTAL_STRATEGY_CLASSES:
            with self.subTest(strategy=cls.__name__):
                instance = cls()
                self.assertTrue(instance.enable_ai_analysis, f"{cls.__name__} should have enable_ai_analysis=True")

    def test_enable_ai_analysis_source_is_mixin(self):
        self.assertTrue(hasattr(AIStrategyMixin, "enable_ai_analysis"))
        self.assertTrue(AIStrategyMixin.enable_ai_analysis)
        self.assertTrue(hasattr(AIStrategyMixin, "_sort_for_ai"))

    def test_sort_for_ai_default(self):
        s = ValueStrategy()
        df = pd.DataFrame({"ts_code": ["C", "A", "B"], "pe_ttm": [10, 5, 8]})
        result = s._sort_for_ai(df)
        self.assertEqual(list(result["ts_code"]), ["C", "A", "B"])

    def test_get_ai_context_nan_handling(self):
        s = ValueStrategy()
        row_with_nan = {"pe_ttm": float("nan"), "pb": None, "dv_ttm": 3.0, "roe": float("nan"), "debt_to_assets": 40.0}
        ctx = s.get_ai_context(row_with_nan)
        self.assertNotIn("nan", ctx.lower())
        self.assertIn("N/A", ctx)


class TestPhase2Trigger(unittest.IsolatedAsyncioTestCase):
    async def test_phase2_bypassed_when_ai_not_configured(self):
        s = ValueStrategy()
        candidates = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["Stock A"], "pe_ttm": [10.0], "pb": [1.0], "dv_ttm": [3.0]},
        )
        context = {"params": {}}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_instance = MagicMock()
            mock_instance.is_cloud_available.return_value = False
            mock_ai.return_value = mock_instance
            result = await s.run_ai_analysis(candidates, context)
            self.assertEqual(len(result), 1)
            self.assertIn("ts_code", result.columns)
            self.assertNotIn("ai_score", result.columns)

    async def test_phase2_triggered_when_ai_available(self):
        s = ValueStrategy()
        candidates = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["Stock A"], "pe_ttm": [10.0], "pb": [1.0], "dv_ttm": [3.0]},
        )
        dp_mock = MagicMock()
        dp_mock.is_cancelled.return_value = False
        dp_mock.cache = MagicMock()
        dp_mock.get_latest_trade_date = AsyncMock(return_value=None)
        context = {"data_processor": dp_mock, "params": {}}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_instance = MagicMock()
            mock_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_instance
            with patch("strategies.ai_mixin.NewsFetcher") as mock_news:
                mock_news.get_us_major_moves = AsyncMock(return_value="")
                mock_news.get_stock_news = AsyncMock(return_value=[])
            with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                mock_rm_instance = MagicMock()
                mock_rm_instance.get_learning_context = AsyncMock(return_value="")
                mock_rm.return_value = mock_rm_instance
            with patch("strategies.ai_mixin.ConfigHandler.get_ai_max_candidates", return_value=30):
                with patch.object(s, "_mixin_analyze_single", new_callable=AsyncMock) as mock_analyze:
                    mock_analyze.return_value = {
                        "score": 75,
                        "summary": "Good",
                        "thinking": "",
                        "confidence": 80,
                        "uncertainty_factors": [],
                    }
                    result = await s.run_ai_analysis(candidates, context)
                    self.assertIsNotNone(result)
                    mock_analyze.assert_called_once()
                    self.assertIn("ai_score", result.columns)

    async def test_market_strategy_skips_ai_analysis(self):
        s = TechnicalBreakoutStrategy()
        data = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["Stock A"], "pct_chg": [5.0], "turnover_rate": [6.0]},
        )
        context = {"screening_data": data, "params": {}}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            _ = await s.filter(context)
            mock_ai.assert_not_called()

    async def test_market_strategy_no_progress_callback(self):
        s = TechnicalBreakoutStrategy()
        data = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["Stock A"], "pct_chg": [5.0], "turnover_rate": [6.0]},
        )
        progress_mock = MagicMock()
        context = {"screening_data": data, "params": {}, "on_progress": progress_mock}
        await s.filter(context)
        progress_mock.assert_not_called()

    async def test_phase2_bypassed_when_dp_missing(self):
        s = ValueStrategy()
        candidates = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "name": ["Stock A"], "pe_ttm": [10.0], "pb": [1.0], "dv_ttm": [3.0]},
        )
        context = {"params": {}}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_instance = MagicMock()
            mock_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_instance
            result = await s.run_ai_analysis(candidates, context)
            self.assertEqual(len(result), 1)
            self.assertNotIn("ai_score", result.columns)


class TestUtils(unittest.TestCase):
    def test_safe_float_normal(self):
        self.assertEqual(safe_float(3.14), 3.14)

    def test_safe_float_none(self):
        self.assertEqual(safe_float(None), 0.0)

    def test_safe_float_nan(self):
        self.assertEqual(safe_float(float("nan")), 0.0)

    def test_safe_float_string(self):
        self.assertEqual(safe_float("abc"), 0.0)

    def test_safe_float_custom_default(self):
        self.assertEqual(safe_float(None, -1.0), -1.0)

    def test_fmt_val_normal(self):
        self.assertEqual(fmt_val(3.14), "3.14")

    def test_fmt_val_integer(self):
        self.assertEqual(fmt_val(16.0), "16")

    def test_fmt_val_none(self):
        self.assertEqual(fmt_val(None), "N/A")

    def test_fmt_val_nan(self):
        self.assertEqual(fmt_val(float("nan")), "N/A")

    def test_fmt_val_string(self):
        self.assertEqual(fmt_val("abc"), "N/A")

    def test_fmt_val_custom_spec(self):
        self.assertEqual(fmt_val(3.14159, ".4f"), "3.1416")

    def test_fmt_val_negative_integer(self):
        self.assertEqual(fmt_val(-5.0), "-5")

    def test_fmt_val_zero(self):
        self.assertEqual(fmt_val(0.0), "0")


class TestDependencyCheck(unittest.TestCase):
    def test_northbound_declares_dependencies(self):
        s = NorthboundStrategy()
        self.assertIn("northbound_data", s.required_context_keys)
        self.assertIn("northbound_holding", s.required_tables)

    def test_institutional_declares_dependencies(self):
        s = InstitutionalStrategy()
        self.assertIn("top_list", s.required_context_keys)
        self.assertIn("top_list", s.required_tables)

    def test_block_trade_declares_dependencies(self):
        s = BlockTradeStrategy()
        self.assertIn("block_trade", s.required_context_keys)
        self.assertIn("block_trade", s.required_tables)

    def test_oversold_declares_dependencies(self):
        s = OversoldStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("daily_quotes", s.required_tables)
        self.assertNotIn("data_processor", s.required_context_keys)

    def test_check_dependencies_missing_key(self):
        s = NorthboundStrategy()
        result = s.check_dependencies({})
        self.assertEqual(result["status"], "unready")
        self.assertIn("northbound_data", result["missing_keys"])

    def test_check_dependencies_empty_key(self):
        s = NorthboundStrategy()
        result = s.check_dependencies({"northbound_data": pd.DataFrame()})
        self.assertEqual(result["status"], "degraded")
        self.assertIn("northbound_data", result["empty_keys"])

    def test_check_dependencies_ok(self):
        s = NorthboundStrategy()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "ratio": [5.0]})
        result = s.check_dependencies({"northbound_data": df})
        self.assertEqual(result["status"], "ready")

    def test_value_strategy_declares_fundamental_deps(self):
        s = ValueStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("fundamental_screening_data", s.required_context_keys)
        self.assertIn("daily_quotes", s.required_tables)
        self.assertIn("daily_indicators", s.required_tables)

    def test_polars_base_default_deps(self):
        s = TechnicalBreakoutStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("daily_quotes", s.required_tables)

    def test_check_dependencies_table_missing(self):
        s = ValueStrategy()
        result = s.check_dependencies({"screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]})})
        self.assertEqual(result["status"], "unready")
        self.assertIn("fundamental_screening_data", result["missing_keys"])

    def test_ai_selection_declares_dependencies(self):
        from strategies.ai_strategy import AISelectionStrategy

        s = AISelectionStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("daily_quotes", s.required_tables)
        self.assertIn("daily_indicators", s.required_tables)

    def test_growth_strategy_declares_deps(self):
        s = GrowthStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("fundamental_screening_data", s.required_context_keys)

    def test_dividend_strategy_declares_deps(self):
        s = DividendStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("fundamental_screening_data", s.required_context_keys)

    def test_cashflow_strategy_declares_deps(self):
        s = CashFlowStrategy()
        self.assertIn("screening_data", s.required_context_keys)
        self.assertIn("fundamental_screening_data", s.required_context_keys)

    def test_check_dependencies_returns_missing_tables(self):
        s = NorthboundStrategy()
        result = s.check_dependencies({})
        self.assertIn("northbound_holding", result["missing_tables"])


if __name__ == "__main__":
    unittest.main()
