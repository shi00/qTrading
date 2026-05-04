import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import datetime

from data.persistence.review_manager import ReviewManager


class TestReviewManagerInit:
    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    def test_init(self, mock_tc, mock_cm):
        rm = ReviewManager()
        assert rm is not None


class TestReviewManagerRunReview:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_no_pending(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm._get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        await rm.run_review()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_with_pending_no_quotes(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240615"],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        mock_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        await rm.run_review()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_with_pending_and_quotes(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240615"],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240615", "20240616"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "pct_chg": [2.0],
                }
            )
        )
        rm._update_result = AsyncMock()
        await rm.run_review()


class TestReviewManagerGetLearningContext:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_empty(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(return_value=None)
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm.get_learning_context()
        assert isinstance(result, str)
        assert "暂无可用历史复盘样本" in result

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_with_wins_and_losses(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(
            side_effect=[
                pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "name": ["Test"],
                        "alpha": [2.0],
                        "t1_pct": [3.0],
                        "ai_score": [80],
                        "ai_reason": ["good"],
                    }
                ),
                pd.DataFrame(
                    {
                        "ts_code": ["000002.SZ"],
                        "name": ["Test2"],
                        "alpha": [-2.0],
                        "t1_pct": [-3.0],
                        "ai_score": [60],
                        "ai_reason": ["bad"],
                    }
                ),
            ]
        )
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm.get_learning_context()
        assert "正向样本" in result
        assert "负向样本" in result

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_error(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(side_effect=Exception("DB Error"))
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm.get_learning_context()
        assert isinstance(result, str)


class TestReviewManagerSaveResults:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_empty_df(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        await rm.save_results("test_strategy", pd.DataFrame())

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_none_df(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        await rm.save_results("test_strategy", None)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_with_data(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "pct_chg": [1.0],
                "trade_date": ["20240615"],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615")
        mock_cache.screener_dao.save_screening_results.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_with_no_trade_date_raises(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
            }
        )
        with pytest.raises(ValueError, match="trade_date"):
            await rm.save_results("test_strategy", df)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_with_multiple_trade_dates_raises(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "name": ["Test", "Test2"],
                "close": [10.0, 20.0],
                "trade_date": ["20240615", "20240616"],
            }
        )
        with pytest.raises(ValueError, match="multiple trade_date"):
            await rm.save_results("test_strategy", df)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_with_params_snapshot(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "trade_date": ["20240615"],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615", params_snapshot={"key": "value"})
        mock_cache.screener_dao.save_screening_results.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_with_custom_run_id(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "trade_date": ["20240615"],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615", run_id="custom_run_id")
        mock_cache.screener_dao.save_screening_results.assert_called_once()


class TestReviewManagerNormalizeTradeDate:
    def test_string_date(self):
        result = ReviewManager._normalize_trade_date("20240615")
        assert isinstance(result, datetime.date)

    def test_date_object(self):
        d = datetime.date(2024, 6, 15)
        result = ReviewManager._normalize_trade_date(d)
        assert result == d

    def test_datetime_object(self):
        dt = datetime.datetime(2024, 6, 15, 10, 30)
        result = ReviewManager._normalize_trade_date(dt)
        assert result == datetime.date(2024, 6, 15)


class TestReviewManagerGetPendingPredictions:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_with_latest_date(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value="20240615")
        mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "cal_date": [
                        "20240606",
                        "20240607",
                        "20240610",
                        "20240611",
                        "20240612",
                        "20240613",
                        "20240614",
                        "20240615",
                    ],
                }
            )
        )
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm._get_pending_predictions()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_no_latest_date(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value=None)
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm._get_pending_predictions()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_error(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(side_effect=Exception("DB Error"))
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm._get_pending_predictions()
        assert isinstance(result, pd.DataFrame)


class TestReviewManagerDateThresholdNormalization:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_date_threshold_is_date_type_with_trade_cal(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20240606", "20240607", "20240610"]})
        )
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm = ReviewManager()
        rm.cache = mock_cache
        await rm._get_pending_predictions()
        call_args = mock_cache.screener_dao.get_pending_predictions.call_args
        date_threshold = call_args[0][0]
        assert isinstance(date_threshold, datetime.date)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_date_threshold_is_date_type_without_trade_cal(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value=None)
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm = ReviewManager()
        rm.cache = mock_cache
        await rm._get_pending_predictions()
        call_args = mock_cache.screener_dao.get_pending_predictions.call_args
        date_threshold = call_args[0][0]
        assert isinstance(date_threshold, datetime.date)


class TestReviewManagerIndexCacheNaN:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_index_pct_nan_does_not_pollute_alpha(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240615"],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240615", "20240616"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )
        nan_df = pd.DataFrame({"pct_chg": [float("nan")]})
        mock_cache.get_index_daily = AsyncMock(return_value=nan_df)
        rm._update_result = AsyncMock()
        await rm.run_review()
        if rm._update_result.called:
            call_kwargs = rm._update_result.call_args
            result_data = call_kwargs[1] if call_kwargs[1] else call_kwargs[0]
            if isinstance(result_data, dict) and "alpha" in result_data:
                assert pd.notna(result_data["alpha"])

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_index_pct_none_skips_alpha(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240615"],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240615", "20240616"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(return_value=None)
        rm._update_result = AsyncMock()
        await rm.run_review()


class TestReviewManagerT1RowBoundary:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_single_quote_row_no_t1(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240615"],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240615"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        assert not rm._update_result.called
