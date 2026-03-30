"""
Tests for ReviewManager.

验证复盘管理器功能，包括预测结果复盘、学习上下文获取和结果保存。
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.persistence.review_manager import ReviewManager


class TestReviewManagerInit(unittest.TestCase):
    """测试初始化"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_init(self, mock_config, mock_api, mock_cache):
        """正常初始化"""
        manager = ReviewManager()

        self.assertIsNotNone(manager.cache)
        self.assertIsNotNone(manager.api)
        self.assertIsNotNone(manager.config)


class TestGetPendingPredictions(unittest.TestCase):
    """测试获取待复盘预测"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_pending_success(self, mock_config, mock_api, mock_cache):
        """成功获取待复盘预测"""
        mock_df = pd.DataFrame(
            {
                "id": [1, 2],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20240315", "20240314"],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(return_value=mock_df)

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager._get_pending_predictions()
            self.assertEqual(len(result), 2)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_pending_empty(self, mock_config, mock_api, mock_cache):
        """空结果"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame()
        )

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager._get_pending_predictions()
            self.assertTrue(result.empty)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_pending_error(self, mock_config, mock_api, mock_cache):
        """错误返回空 DataFrame"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            side_effect=Exception("DB error")
        )

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager._get_pending_predictions()
            self.assertTrue(result.empty)

        asyncio.run(run_test())


class TestGetLearningContext(unittest.TestCase):
    """测试获取学习上下文"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_with_data(self, mock_config, mock_api, mock_cache):
        """有历史数据"""
        mock_wins = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "t1_pct": [5.5],
                "ai_score": [85],
                "ai_reason": ["技术突破"],
            }
        )

        mock_losses = pd.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "name": ["万科A"],
                "t1_pct": [-3.2],
                "ai_score": [70],
                "ai_reason": ["市场下跌"],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(
            side_effect=[mock_wins, mock_losses]
        )

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("history_context", result)
            self.assertIn("Success Examples", result)
            self.assertIn("Mistakes to Avoid", result)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_empty(self, mock_config, mock_api, mock_cache):
        """无历史数据"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(return_value=pd.DataFrame())

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("No historical data available", result)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_error(self, mock_config, mock_api, mock_cache):
        """错误返回空上下文"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(
            side_effect=Exception("DB error")
        )

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("history_context", result)

        asyncio.run(run_test())


class TestUpdateResult(unittest.TestCase):
    """测试更新结果"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_update_result_success(self, mock_config, mock_api, mock_cache):
        """成功更新结果"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager._update_result(1, 5.5, "WIN", 1.0)
            mock_screener_dao.update_prediction_result.assert_called_once()

        asyncio.run(run_test())


class TestSaveResults(unittest.TestCase):
    """测试保存结果"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_success(self, mock_config, mock_api, mock_cache):
        """成功保存结果"""
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [10.5],
                "pct_chg": [2.5],
                "industry": ["银行"],
                "vol": [1000000],
                "amount": [10500000],
                "turnover_rate": [1.5],
                "pe_ttm": [6.5],
                "pb": [0.8],
                "ps_ttm": [1.2],
                "dv_ttm": [3.5],
                "total_mv": [1000000],
                "circ_mv": [800000],
                "roe": [12.5],
                "grossprofit_margin": [45.0],
                "debt_to_assets": [60.0],
                "or_yoy": [10.0],
                "netprofit_yoy": [15.0],
                "ai_score": [85],
                "ai_reason": ["技术突破"],
                "thinking": ["看好后市"],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", mock_df)
            mock_screener_dao.save_screening_results.assert_called_once()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_empty(self, mock_config, mock_api, mock_cache):
        """空 DataFrame 不保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", pd.DataFrame())
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_none(self, mock_config, mock_api, mock_cache):
        """None 不保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", None)
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())


class TestSaveResultsEdgeCases(unittest.TestCase):
    """测试保存结果边界条件"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_missing_ts_code(self, mock_config, mock_api, mock_cache):
        """缺少 ts_code 的行被跳过"""
        mock_df = pd.DataFrame(
            {
                "name": ["平安银行"],
                "close": [10.5],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", mock_df)
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_with_nan_values(self, mock_config, mock_api, mock_cache):
        """NaN 值被正确处理"""
        import numpy as np

        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [np.nan],
                "pct_chg": [2.5],
                "ai_score": [np.nan],
                "ai_reason": [np.nan],
                "thinking": [np.nan],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", mock_df)
            mock_screener_dao.save_screening_results.assert_called_once()
            call_args = mock_screener_dao.save_screening_results.call_args[0][0]
            self.assertEqual(len(call_args), 1)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
