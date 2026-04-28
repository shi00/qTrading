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

    @patch("strategies.ai_mixin.AIStrategyMixin._prefetch_strategy_specific", new_callable=AsyncMock)
    @patch("strategies.ai_mixin.AIStrategyMixin._analyze_stock_single", new_callable=AsyncMock)
    def test_capital_data_uses_context_trade_date(self, mock_analyze, mock_prefetch):
        from strategies.ai_mixin import AIStrategyMixin

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"
        mixin._normalize_trade_date_for_cache = AIStrategyMixin._normalize_trade_date_for_cache

        mock_dp = MagicMock()
        mock_dp.get_latest_trade_date = AsyncMock(return_value="20240320")
        mock_dp.cache = MagicMock()
        mock_dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        mock_dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        mock_dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        mock_dp.cache.prefetch_history_data = AsyncMock(return_value={})
        mock_dp.cache.prefetch_auxiliary_data = AsyncMock(return_value={})
        mock_dp.cache.get_concept_map = AsyncMock(return_value={})

        context = {"trade_date": "20240315", "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]})}

        with (
            patch.object(mixin, "_prefetch_strategy_specific", mock_prefetch),
            patch.object(mixin, "_analyze_stock_single", mock_analyze),
            patch("strategies.ai_mixin.NewsFetcher"),
        ):
            import asyncio

            async def _run():
                candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"]})
                prefetched = await mixin._prefetch_data(candidates_df, context, mock_dp)
                return prefetched

            asyncio.get_event_loop().run_until_complete(_run())

            mock_dp.cache.get_moneyflow.assert_awaited_once_with(trade_date="20240315")
            mock_dp.cache.get_top_list.assert_awaited_once_with(trade_date="20240315")
            mock_dp.cache.get_northbound.assert_awaited_once_with(trade_date="20240315")

    @patch("strategies.ai_mixin.AIStrategyMixin._prefetch_strategy_specific", new_callable=AsyncMock)
    @patch("strategies.ai_mixin.AIStrategyMixin._analyze_stock_single", new_callable=AsyncMock)
    def test_capital_data_falls_back_to_latest(self, mock_analyze, mock_prefetch):
        from strategies.ai_mixin import AIStrategyMixin

        mixin = AIStrategyMixin.__new__(AIStrategyMixin)
        mixin.strategy_name = "test"
        mixin._normalize_trade_date_for_cache = AIStrategyMixin._normalize_trade_date_for_cache

        mock_dp = MagicMock()
        mock_dp.get_latest_trade_date = AsyncMock(return_value="20240320")
        mock_dp.cache = MagicMock()
        mock_dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        mock_dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        mock_dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        mock_dp.cache.prefetch_history_data = AsyncMock(return_value={})
        mock_dp.cache.prefetch_auxiliary_data = AsyncMock(return_value={})
        mock_dp.cache.get_concept_map = AsyncMock(return_value={})

        context = {"screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]})}

        with (
            patch.object(mixin, "_prefetch_strategy_specific", mock_prefetch),
            patch.object(mixin, "_analyze_stock_single", mock_analyze),
            patch("strategies.ai_mixin.NewsFetcher"),
        ):
            import asyncio

            async def _run():
                candidates_df = pd.DataFrame({"ts_code": ["000001.SZ"]})
                prefetched = await mixin._prefetch_data(candidates_df, context, mock_dp)
                return prefetched

            asyncio.get_event_loop().run_until_complete(_run())

            mock_dp.cache.get_moneyflow.assert_awaited_once_with(trade_date="20240320")


if __name__ == "__main__":
    unittest.main()
