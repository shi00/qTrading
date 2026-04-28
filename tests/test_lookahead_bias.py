import asyncio
import datetime
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd


class TestLookaheadBias(unittest.TestCase):
    def test_ai_mixin_uses_context_trade_date_first(self):
        from strategies.ai_mixin import AIStrategyMixin

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"

        normalized = mixin._normalize_trade_date_for_cache("20240315")
        self.assertEqual(normalized, "20240315")

    def test_ai_mixin_falls_back_to_latest_when_context_none(self):
        from strategies.ai_mixin import AIStrategyMixin

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"

        result = mixin._normalize_trade_date_for_cache(None)
        self.assertIsNone(result)

    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_mixin.NewsFetcher")
    @patch("strategies.ai_mixin.ConfigHandler")
    @patch("strategies.ai_mixin.I18n")
    def test_capital_data_uses_context_trade_date(self, mock_i18n, mock_config, mock_news, mock_ai):
        from strategies.ai_mixin import AIStrategyMixin

        mock_ai_inst = MagicMock()
        mock_ai_inst.is_cloud_available.return_value = False
        mock_ai.return_value = mock_ai_inst

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"

        context = {"trade_date": "20240315", "data_processor": MagicMock()}
        candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"]})

        asyncio.run(mixin.run_ai_analysis(candidates_df, context))

        self.assertEqual(mixin._normalize_trade_date_for_cache("20240315"), "20240315")

    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_mixin.NewsFetcher")
    @patch("strategies.ai_mixin.ConfigHandler")
    @patch("strategies.ai_mixin.I18n")
    def test_capital_data_falls_back_to_latest(self, mock_i18n, mock_config, mock_news, mock_ai):
        from strategies.ai_mixin import AIStrategyMixin

        mock_ai_inst = MagicMock()
        mock_ai_inst.is_cloud_available.return_value = False
        mock_ai.return_value = mock_ai_inst

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"

        context = {"data_processor": MagicMock()}
        candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"]})

        asyncio.run(mixin.run_ai_analysis(candidates_df, context))

        self.assertIsNone(mixin._normalize_trade_date_for_cache(None))

    def test_normalize_trade_date_handles_various_types(self):
        from strategies.ai_mixin import AIStrategyMixin

        self.assertEqual(AIStrategyMixin._normalize_trade_date_for_cache("20240315"), "20240315")
        self.assertEqual(AIStrategyMixin._normalize_trade_date_for_cache(datetime.date(2024, 3, 15)), "20240315")
        self.assertEqual(AIStrategyMixin._normalize_trade_date_for_cache(pd.Timestamp("2024-03-15")), "20240315")
        self.assertIsNone(AIStrategyMixin._normalize_trade_date_for_cache(None))
        self.assertIsNone(AIStrategyMixin._normalize_trade_date_for_cache(""))


if __name__ == "__main__":
    unittest.main()
