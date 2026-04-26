"""
Tests for ReviewManager.

验证复盘管理器功能，包括预测结果复盘、学习上下文获取和结果保存。
"""

import asyncio
import datetime
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
        mock_screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())

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
        mock_screener_dao.get_pending_predictions = AsyncMock(side_effect=Exception("DB error"))

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
        mock_screener_dao.get_learning_context = AsyncMock(side_effect=[mock_wins, mock_losses])

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("history_context", result)
            self.assertIn("复盘参考 - 正向样本", result)
            self.assertIn("复盘参考 - 负向样本", result)
            self.assertNotIn("Learn from these", result)
            self.assertNotIn("Do NOT repeat", result)

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
            self.assertIn("暂无可用历史复盘样本", result)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_error(self, mock_config, mock_api, mock_cache):
        """错误返回空上下文"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(side_effect=Exception("DB error"))

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
        analysis_date = datetime.date(2024, 12, 31)

        async def run_test():
            await manager.save_results("test_strategy", mock_df, trade_date=analysis_date)
            mock_screener_dao.save_screening_results.assert_called_once()
            call_args = mock_screener_dao.save_screening_results.call_args
            records = call_args[0][0]
            saved_date = records[0][0]
            self.assertEqual(saved_date, analysis_date)

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


class TestReviewPredictionsCore(unittest.TestCase):
    """测试 run_review 核心复盘流程（P1 级）"""

    def _make_manager(self, mock_cache_instance, mock_api_instance=None):
        with (
            patch("data.persistence.review_manager.CacheManager", return_value=mock_cache_instance),
            patch("data.persistence.review_manager.TushareClient", return_value=mock_api_instance),
            patch("data.persistence.review_manager.ConfigHandler"),
        ):
            return ReviewManager()

    def test_review_win_when_alpha_positive(self):
        """Alpha > 0.5 时标记为 WIN"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "pct_chg": [1.0],
                }
            )
        )

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_screener_dao.update_prediction_result.call_args[0]
            self.assertEqual(call_args[2], "WIN")

        asyncio.run(run_test())

    def test_review_loss_when_alpha_negative(self):
        """Alpha < -0.5 时标记为 LOSS"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 9.5],
                    "pct_chg": [1.0, -5.0],
                }
            )
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "pct_chg": [2.0],
                }
            )
        )

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_screener_dao.update_prediction_result.call_args[0]
            self.assertEqual(call_args[2], "LOSS")

        asyncio.run(run_test())

    def test_review_draw_when_alpha_near_zero(self):
        """|Alpha| <= 0.5 时标记为 DRAW"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 10.1],
                    "pct_chg": [1.0, 1.0],
                }
            )
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "pct_chg": [0.8],
                }
            )
        )

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_screener_dao.update_prediction_result.call_args[0]
            self.assertEqual(call_args[2], "DRAW")

        asyncio.run(run_test())

    def test_review_no_t1_data(self):
        """T+1 数据缺失时不更新"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240315"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                }
            )
        )

        mock_api_instance = MagicMock()

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_review_no_quotes_at_all(self):
        """无行情数据时跳过"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())

        mock_api_instance = MagicMock()

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_review_index_data_failure_defaults_zero(self):
        """指数数据获取失败时 index_pct 默认 0.0"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 10.3],
                    "pct_chg": [1.0, 3.0],
                }
            )
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(side_effect=Exception("API Error"))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_screener_dao.update_prediction_result.call_args[0]
            self.assertEqual(call_args[2], "WIN")

        asyncio.run(run_test())

    def test_review_empty_pending(self):
        """无待复盘预测时直接返回"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao

        mock_api_instance = MagicMock()

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.get_daily_quotes.assert_not_called()

        asyncio.run(run_test())

    def test_review_t0_not_found_in_quotes(self):
        """预测日期不在行情数据中时跳过"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240310"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20240311", "20240312"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )

        mock_api_instance = MagicMock()

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_review_multiple_predictions(self):
        """多条预测逐一复盘"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1, 2],
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240315", "20240315"],
                }
            )
        )
        mock_screener_dao.update_prediction_result = AsyncMock()

        call_count = 0

        async def mock_get_quotes(**kwargs):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame(
                {
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.get_daily_quotes = AsyncMock(side_effect=mock_get_quotes)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            self.assertEqual(mock_screener_dao.update_prediction_result.call_count, 2)

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
            await manager.save_results(
                "test_strategy",
                mock_df,
                trade_date=datetime.date(2024, 12, 31),
            )
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
            await manager.save_results(
                "test_strategy",
                mock_df,
                trade_date=datetime.date(2024, 12, 31),
            )
            mock_screener_dao.save_screening_results.assert_called_once()
            call_args = mock_screener_dao.save_screening_results.call_args[0][0]
            self.assertEqual(len(call_args), 1)

        asyncio.run(run_test())


class TestSaveResultsTradeDateSemantics(unittest.TestCase):
    """测试 save_results 的 trade_date 语义：必须使用分析交易日而非当前自然日"""

    def _make_mock_df(self):
        return pd.DataFrame(
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

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_explicit_trade_date_used(self, mock_config, mock_api, mock_cache):
        """显式传入 trade_date 时，使用该日期而非当前自然日"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        analysis_date = datetime.date(2024, 12, 31)

        async def run_test():
            await manager.save_results("test_strategy", self._make_mock_df(), trade_date=analysis_date)
            mock_screener_dao.save_screening_results.assert_called_once()
            records = mock_screener_dao.save_screening_results.call_args[0][0]
            saved_date = records[0][0]
            self.assertEqual(saved_date, analysis_date)
            self.assertNotEqual(saved_date, datetime.date.today())

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_missing_trade_date_raises(self, mock_config, mock_api, mock_cache):
        """未传入 trade_date 且结果不带 trade_date 时，应拒绝保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            with self.assertRaises(ValueError):
                await manager.save_results("test_strategy", self._make_mock_df())
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_weekend_analysis_date_preserved(self, mock_config, mock_api, mock_cache):
        """周五盘后分析时，trade_date 应为周五而非周六"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        friday_date = datetime.date(2024, 12, 27)

        async def run_test():
            await manager.save_results("test_strategy", self._make_mock_df(), trade_date=friday_date)
            records = mock_screener_dao.save_screening_results.call_args[0][0]
            saved_date = records[0][0]
            self.assertEqual(saved_date, friday_date)
            self.assertEqual(saved_date.weekday(), 4)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_df_trade_date_used_when_arg_missing(self, mock_config, mock_api, mock_cache):
        """未显式传参时，可从结果集中唯一 trade_date 推导分析日"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        df = self._make_mock_df().copy()
        df["trade_date"] = ["20241231"]

        async def run_test():
            await manager.save_results("test_strategy", df)
            records = mock_screener_dao.save_screening_results.call_args[0][0]
            self.assertEqual(records[0][0], datetime.date(2024, 12, 31))

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_trade_date_mismatch_raises(self, mock_config, mock_api, mock_cache):
        """显式 trade_date 与结果中的 trade_date 冲突时拒绝保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        df = self._make_mock_df().copy()
        df["trade_date"] = ["20241230"]

        async def run_test():
            with self.assertRaises(ValueError):
                await manager.save_results("test_strategy", df, trade_date=datetime.date(2024, 12, 31))
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
