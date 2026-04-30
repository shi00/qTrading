import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
    @patch("strategies.ai_mixin.I18n")
    def test_capital_data_uses_context_trade_date(self, mock_i18n, mock_news, mock_ai):
        from strategies.ai_mixin import AIStrategyMixin

        mock_ai_inst = MagicMock()
        mock_ai_inst.is_cloud_available.return_value = True
        mock_ai.return_value = mock_ai_inst
        mock_news.get_stock_news = AsyncMock(return_value=[])

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"
        mixin._history_cache = {}
        mixin.should_include_learning_context = MagicMock(return_value=False)
        mixin.should_include_global_context = MagicMock(return_value=False)
        mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
        mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

        cache = MagicMock()
        cache.get_concepts = AsyncMock(return_value={})
        cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

        dp = MagicMock()
        dp.cache = cache
        dp.is_cancelled.return_value = False
        dp.get_latest_trade_date = AsyncMock(return_value="20240430")

        context = {"trade_date": "20240315", "data_processor": dp}
        candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

        asyncio.run(mixin.run_ai_analysis(candidates_df, context))

        cache.get_moneyflow.assert_awaited_once_with(trade_date="20240315")
        cache.get_top_list.assert_awaited_once_with(trade_date="20240315")
        cache.get_northbound.assert_awaited_once_with(trade_date="20240315")
        dp.get_latest_trade_date.assert_not_awaited()

    @patch("strategies.ai_mixin.AIService")
    @patch("strategies.ai_mixin.NewsFetcher")
    @patch("strategies.ai_mixin.I18n")
    def test_capital_data_falls_back_to_latest(self, mock_i18n, mock_news, mock_ai):
        from strategies.ai_mixin import AIStrategyMixin

        mock_ai_inst = MagicMock()
        mock_ai_inst.is_cloud_available.return_value = True
        mock_ai.return_value = mock_ai_inst
        mock_news.get_stock_news = AsyncMock(return_value=[])

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"
        mixin._history_cache = {}
        mixin.should_include_learning_context = MagicMock(return_value=False)
        mixin.should_include_global_context = MagicMock(return_value=False)
        mixin._prefetch_strategy_specific = AsyncMock(side_effect=lambda _df, _ctx, prefetched: prefetched)
        mixin._mixin_analyze_single = AsyncMock(return_value={"score": 0})

        cache = MagicMock()
        cache.get_concepts = AsyncMock(return_value={})
        cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

        dp = MagicMock()
        dp.cache = cache
        dp.is_cancelled.return_value = False
        dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 4, 30))

        context = {"data_processor": dp}
        candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

        asyncio.run(mixin.run_ai_analysis(candidates_df, context))

        dp.get_latest_trade_date.assert_awaited_once()
        cache.get_moneyflow.assert_awaited_once_with(trade_date="20240430")
        cache.get_top_list.assert_awaited_once_with(trade_date="20240430")
        cache.get_northbound.assert_awaited_once_with(trade_date="20240430")

    def test_normalize_trade_date_handles_various_types(self):
        from strategies.ai_mixin import AIStrategyMixin

        self.assertEqual(AIStrategyMixin._normalize_trade_date_for_cache("20240315"), "20240315")
        self.assertEqual(AIStrategyMixin._normalize_trade_date_for_cache(datetime.date(2024, 3, 15)), "20240315")
        self.assertEqual(AIStrategyMixin._normalize_trade_date_for_cache(pd.Timestamp("2024-03-15")), "20240315")
        self.assertIsNone(AIStrategyMixin._normalize_trade_date_for_cache(None))
        self.assertIsNone(AIStrategyMixin._normalize_trade_date_for_cache(""))


if __name__ == "__main__":
    unittest.main()
