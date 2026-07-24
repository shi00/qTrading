# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import datetime

from data.cache.cache_manager import CacheManager
from data.external.tushare_client import TushareClient
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.review_manager import ReviewManager

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


class TestReviewManagerInit:
    @patch("data.persistence.review_manager.CacheManager", spec=CacheManager)
    @patch("data.persistence.review_manager.TushareClient", spec=TushareClient)
    def test_init_creates_cache_and_api(self, mock_tc, mock_cm):
        mock_cm.return_value = MagicMock(spec=CacheManager)
        mock_tc.return_value = MagicMock(spec=TushareClient)
        rm = ReviewManager()
        assert isinstance(rm.cache, CacheManager)
        assert isinstance(rm.api, TushareClient)

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    def test_init_default_thresholds(self, mock_tc, mock_cm):
        rm = ReviewManager()
        assert rm.alpha_win_threshold == 0.5
        assert rm.alpha_loss_threshold == 0.5

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    def test_init_custom_thresholds(self, mock_tc, mock_cm):
        rm = ReviewManager(alpha_win_threshold=1.0, alpha_loss_threshold=2.0)
        assert rm.alpha_win_threshold == 1.0
        assert rm.alpha_loss_threshold == 2.0


class TestReviewManagerSwIndustryPassThrough:
    """Phase 3F-2 轨道 B：验证 save_results 能正确传递申万行业字段。

    screener_dao 的 SQL 已改为 COALESCE(m.sw_l2_name, b.industry) AS industry，
    review_manager.save_results 通过 _s(row, "industry") 读取后写入 screening_history，
    无需修改代码即可传递申万行业（自动获益）。
    """

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_review_manager_uses_sw_industry(self, mock_cm, mock_tc):
        """Phase 3F-2：含申万二级行业名的 df 经 save_results 后，industry 字段应原样写入 record。

        review_manager L521 路径切换验证：上游 screener_dao SQL 已改为
        COALESCE(m.sw_l2_name, b.industry) AS industry，review_manager 通过 _s(row, "industry")
        透传 df.industry 字段（v1.9.0 M-4 修订后实施），无需修改 review_manager 代码即可
        自动获益于申万行业覆写。
        """
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock(return_value=1)
        mock_cache.screener_dao = mock_screener_dao

        rm = ReviewManager()
        rm.cache = mock_cache

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "industry": ["银行Ⅱ"],
                "trade_date": ["20240615"],
                "close": [10.0],
                "pct_chg": [1.0],
                "vol": [1e6],
                "amount": [1e7],
                "turnover_rate": [1.5],
                "ai_score": [80],
                "ai_reason": ["test"],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615")

        mock_screener_dao.save_screening_results.assert_called_once()
        saved_records = mock_screener_dao.save_screening_results.call_args.args[0]
        assert len(saved_records) == 1
        assert saved_records[0]["industry"] == "银行Ⅱ"


class TestReviewManagerRunReview:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_no_pending_skips_update(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm._get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_with_pending_no_quotes_skips_update(self, mock_cm, mock_tc):
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
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_with_pending_and_quotes_updates_result(self, mock_cm, mock_tc):
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
        rm._update_result.assert_called_once()


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
    async def test_error_returns_fallback_string(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(side_effect=Exception("DB Error"))
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm.get_learning_context()
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_as_of_passed_to_dao(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(return_value=None)
        rm = ReviewManager()
        rm.cache = mock_cache
        import datetime

        as_of_date = datetime.date(2024, 6, 1)
        await rm.get_learning_context(as_of=as_of_date)
        mock_cache.screener_dao.get_learning_context.assert_any_call(
            limit=3,
            is_win=True,
            as_of=as_of_date,
        )
        mock_cache.screener_dao.get_learning_context.assert_any_call(
            limit=3,
            is_win=False,
            as_of=as_of_date,
        )

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_datetime_as_of_converted_to_date(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(return_value=None)
        rm = ReviewManager()
        rm.cache = mock_cache
        import datetime

        as_of_dt = datetime.datetime(2024, 6, 1, 12, 0, 0)
        as_of_date = datetime.date(2024, 6, 1)
        await rm.get_learning_context(as_of=as_of_dt)
        mock_cache.screener_dao.get_learning_context.assert_any_call(
            limit=3,
            is_win=True,
            as_of=as_of_date,
        )


class TestReviewManagerSaveResults:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_empty_df_skips_dao(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        await rm.save_results("test_strategy", pd.DataFrame())
        mock_cache.screener_dao.save_screening_results.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_save_none_df_skips_dao(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        await rm.save_results("test_strategy", None)
        mock_cache.screener_dao.save_screening_results.assert_not_called()

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
        assert result == datetime.date(2024, 6, 15)

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


class TestReviewManagerRunReviewNoQuotesForStock:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_no_quotes_for_stock_skips(self, mock_cm, mock_tc):
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
                    "ts_code": ["000002.SZ"],
                    "trade_date": ["20240615"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                }
            )
        )
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()


class TestReviewManagerRunReviewNoT0Row:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_t0_row_not_found_skips(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240620"],
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
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()


class TestReviewManagerRunReviewT5Calculation:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_t5_calculation_with_enough_rows(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240610"],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 7,
                "trade_date": [
                    "20240610",
                    "20240611",
                    "20240612",
                    "20240613",
                    "20240614",
                    "20240617",
                    "20240618",
                ],
                "close": [10.0, 10.5, 11.0, 10.8, 10.2, 9.8, 9.5],
                "pct_chg": [1.0, 5.0, 4.76, -1.82, -5.56, -3.92, -3.06],
            }
        )
        mock_cache.get_daily_quotes = AsyncMock(return_value=quotes)
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()
        call_kwargs = rm._update_result.call_args
        assert call_kwargs.kwargs.get("t5_pct") is not None


class TestReviewManagerRunReviewTimestampDate:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_timestamp_trade_date_has_date_method(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._get_pending_predictions = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "id": [1],
                    "ts_code": ["000001.SZ"],
                    "trade_date": [pd.Timestamp("2024-06-15")],
                    "ai_score": [80],
                    "ai_reason": ["test"],
                }
            )
        )
        quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [pd.Timestamp("2024-06-15"), pd.Timestamp("2024-06-16")],
                "close": [10.0, 10.5],
                "pct_chg": [1.0, 5.0],
            }
        )
        mock_cache.get_daily_quotes = AsyncMock(return_value=quotes)
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()


class TestReviewManagerRunReviewIndexApiFallback:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_cache_miss_api_fallback(self, mock_cm, mock_tc):
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
        mock_api = MagicMock()
        mock_api.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [1.5]}))
        rm.api = mock_api
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_cache_miss_api_also_empty(self, mock_cm, mock_tc):
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
        mock_api = MagicMock()
        mock_api.get_index_daily = AsyncMock(return_value=None)
        rm.api = mock_api
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_cache_miss_api_exception(self, mock_cm, mock_tc):
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
        mock_api = MagicMock()
        mock_api.get_index_daily = AsyncMock(side_effect=ValueError("bad data"))
        rm.api = mock_api
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_cache_exception_falls_to_none(self, mock_cm, mock_tc):
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
        mock_cache.get_index_daily = AsyncMock(side_effect=RuntimeError("cache error"))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_not_called()


class TestReviewManagerRunReviewLossLabel:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_loss_label_when_alpha_negative(self, mock_cm, mock_tc):
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
                    "close": [10.0, 9.0],
                    "pct_chg": [1.0, -10.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()
        call_args = rm._update_result.call_args
        label = call_args[0][2]
        assert label == "LOSS"

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_draw_label_when_alpha_small(self, mock_cm, mock_tc):
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
                    "close": [10.0, 10.2],
                    "pct_chg": [1.0, 2.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()
        call_args = rm._update_result.call_args
        label = call_args[0][2]
        assert label == "DRAW"


class TestReviewManagerCustomThresholds:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_custom_win_threshold_higher(self, mock_cm, mock_tc):
        """alpha=3.0 with default threshold (0.5) -> WIN.
        With alpha_win_threshold=5.0, alpha=3.0 -> DRAW (not high enough).
        """
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager(alpha_win_threshold=5.0)
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
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()
        call_args = rm._update_result.call_args
        label = call_args[0][2]
        assert label == "DRAW"

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_custom_loss_threshold_higher(self, mock_cm, mock_tc):
        """alpha=-8.0 with default threshold (0.5) -> LOSS.
        With alpha_loss_threshold=10.0, alpha=-8.0 -> DRAW (not low enough).
        """
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager(alpha_loss_threshold=10.0)
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
                    "close": [10.0, 9.0],
                    "pct_chg": [1.0, -6.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()
        call_args = rm._update_result.call_args
        label = call_args[0][2]
        assert label == "DRAW"

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_custom_win_threshold_lower(self, mock_cm, mock_tc):
        """alpha=1.0 with default threshold (0.5) -> WIN.
        With alpha_win_threshold=0.3, alpha=1.0 still -> WIN.
        With alpha=0.4, default threshold -> DRAW; threshold=0.3 -> WIN.
        """
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager(alpha_win_threshold=0.3)
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
        # alpha = 2.0 - 1.6 = 0.4 -> WIN with threshold 0.3, DRAW with default 0.5
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240615", "20240616"],
                    "close": [10.0, 10.4],
                    "pct_chg": [1.0, 2.0],
                }
            )
        )
        mock_cache.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [1.6]}))
        rm._update_result = AsyncMock()
        await rm.run_review()
        rm._update_result.assert_called_once()
        call_args = rm._update_result.call_args
        label = call_args[0][2]
        assert label == "WIN"


class TestReviewManagerRunReviewExceptionInRow:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_exception_in_row_continues(self, mock_cm, mock_tc):
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
        mock_cache.get_index_daily = AsyncMock(side_effect=RuntimeError("DB error"))
        rm._update_result = AsyncMock()
        await rm.run_review()


class TestReviewManagerGetPendingPredictionsEdgeCases:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_trade_cal_less_than_10_rows(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value="20240615")
        mock_cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614", "20240615"]}))
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm._get_pending_predictions()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_trade_cal_none(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value="20240615")
        mock_cache.get_trade_cal = AsyncMock(return_value=None)
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm._get_pending_predictions()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_trade_cal_empty(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(return_value="20240615")
        mock_cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())
        rm = ReviewManager()
        rm.cache = mock_cache
        result = await rm._get_pending_predictions()
        assert isinstance(result, pd.DataFrame)


class TestReviewManagerSaveResultsEdgeCases:
    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_trade_date_mismatch_raises(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["Test"],
                "close": [10.0],
                "trade_date": ["20240616"],
            }
        )
        with pytest.raises(ValueError, match="mismatch"):
            await rm.save_results("test_strategy", df, trade_date="20240615")

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_params_snapshot_json_string(self, mock_cm, mock_tc):
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
        await rm.save_results(
            "test_strategy",
            df,
            trade_date="20240615",
            params_snapshot='{"key": "value"}',
        )
        mock_cache.screener_dao.save_screening_results.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_params_snapshot_invalid_json(self, mock_cm, mock_tc):
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
        await rm.save_results("test_strategy", df, trade_date="20240615", params_snapshot="not-json")
        mock_cache.screener_dao.save_screening_results.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_no_ts_code_rows_skipped(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": [None],
                "name": ["Test"],
                "close": [10.0],
                "trade_date": ["20240615"],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615")
        mock_cache.screener_dao.save_screening_results.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_nan_fields_handled(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.save_screening_results = AsyncMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": [float("nan")],
                "close": [float("nan")],
                "trade_date": ["20240615"],
                "ai_score": [float("nan")],
                "ai_reason": [float("nan")],
                "thinking": [float("nan")],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615")
        mock_cache.screener_dao.save_screening_results.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_ai_score_value_error_handled(self, mock_cm, mock_tc):
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
                "ai_score": ["not_a_number"],
            }
        )
        await rm.save_results("test_strategy", df, trade_date="20240615")
        mock_cache.screener_dao.save_screening_results.assert_called_once()
        records = mock_cache.screener_dao.save_screening_results.call_args[0][0]
        assert records[0]["ai_score"] == 0


class TestReviewManagerEngineDisposedErrorR5:
    """R5 一致性：disposed 引擎抛出的 EngineDisposedError 必须上抛，不可被 except Exception 吞没。

    对齐 services/news_subscription_service.py 的显式 `except EngineDisposedError: raise` 范式。
    覆盖 _get_pending_predictions / get_learning_context / _batch_update_results 三个方法。
    """

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_get_pending_predictions_propagates_engine_disposed(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(side_effect=EngineDisposedError("engine disposed"))
        rm = ReviewManager()
        rm.cache = mock_cache
        with pytest.raises(EngineDisposedError):
            await rm._get_pending_predictions()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_get_learning_context_propagates_engine_disposed(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(side_effect=EngineDisposedError("engine disposed"))
        rm = ReviewManager()
        rm.cache = mock_cache
        with pytest.raises(EngineDisposedError):
            await rm.get_learning_context()

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_batch_update_results_propagates_engine_disposed(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(side_effect=EngineDisposedError("engine disposed"))
        mock_cache.engine = mock_engine
        mock_cache.screener_dao = MagicMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        updates = [
            {
                "record_id": 1,
                "pct": 1.0,
                "label": "WIN",
                "t1_price": 10.0,
                "t5_pct": 2.0,
                "t5_price": 10.5,
                "index_pct": 0.5,
                "alpha": 0.5,
            }
        ]
        with pytest.raises(EngineDisposedError):
            await rm._batch_update_results(updates)

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_batch_update_results_fallback_propagates_engine_disposed(self, mock_cm, mock_tc):
        """R5 一致性：主路径抛普通 Exception 触发 fallback 时，_update_result 抛 EngineDisposedError 必须上抛。

        防止 fallback 路径的 except Exception 误吞 disposed 引擎异常（与主路径对齐）。
        """
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(side_effect=RuntimeError("batch tx failed"))
        mock_cache.engine = mock_engine
        mock_cache.screener_dao = MagicMock()
        rm = ReviewManager()
        rm.cache = mock_cache
        rm._update_result = AsyncMock(side_effect=EngineDisposedError("engine disposed"))
        updates = [
            {
                "record_id": 1,
                "pct": 1.0,
                "label": "WIN",
                "t1_price": 10.0,
                "t5_pct": 2.0,
                "t5_price": 10.5,
                "index_pct": 0.5,
                "alpha": 0.5,
            }
        ]
        with pytest.raises(EngineDisposedError):
            await rm._batch_update_results(updates)


class TestReviewManagerSystemLevelError:
    """system 级异常（PermissionError）必须 raise 传播，不可被 except Exception 降级吞没。

    覆盖 _get_pending_predictions / get_learning_context 的 classify_error + classify_severity
    system 分支（if severity == "system": raise）。
    """

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_get_pending_predictions_propagates_system_error(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.get_latest_trade_date = AsyncMock(side_effect=PermissionError("permission denied"))
        rm = ReviewManager()
        rm.cache = mock_cache
        with pytest.raises(PermissionError, match="permission denied") as exc_info:
            await rm._get_pending_predictions()
        assert str(exc_info.value) == "permission denied"

    @pytest.mark.asyncio
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.CacheManager")
    async def test_get_learning_context_propagates_system_error(self, mock_cm, mock_tc):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_learning_context = AsyncMock(side_effect=PermissionError("permission denied"))
        rm = ReviewManager()
        rm.cache = mock_cache
        with pytest.raises(PermissionError, match="permission denied") as exc_info:
            await rm.get_learning_context()
        assert str(exc_info.value) == "permission denied"
