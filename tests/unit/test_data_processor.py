import asyncio
import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.data_processor import DataProcessor


def _make_dp():
    DataProcessor._instance = None
    DataProcessor._initialized = False
    with (
        patch("data.data_processor.CacheManager"),
        patch("data.data_processor.TushareClient"),
        patch("data.data_processor.TradeCalendarService"),
        patch("data.data_processor.ConfigHandler") as mock_ch,
    ):
        mock_ch.get_token.return_value = "test_token"
        dp = DataProcessor()
    return dp


def _cleanup(dp):
    DataProcessor._instance = None
    DataProcessor._initialized = False


class TestDataProcessorRefreshToken:
    def test_refresh_with_new_token(self):
        dp = _make_dp()
        try:
            with patch("data.data_processor.TushareClient"):
                dp.refresh_token("new_token")
                assert dp._current_token == "new_token"
        finally:
            _cleanup(dp)

    def test_refresh_auto_detect(self):
        dp = _make_dp()
        try:
            with (
                patch("data.data_processor.ConfigHandler") as mock_ch,
                patch("data.data_processor.TushareClient"),
            ):
                mock_ch.get_token.return_value = "auto_token"
                dp.refresh_token()
                assert dp._current_token == "auto_token"
        finally:
            _cleanup(dp)


class TestDataProcessorCancel:
    @pytest.mark.asyncio
    async def test_get_cancel_event(self):
        dp = _make_dp()
        try:
            evt = dp._get_cancel_event()
            assert evt is not None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_is_cancelled(self):
        dp = _make_dp()
        try:
            dp.clear_cancel()
            assert not dp.is_cancelled()
            dp._get_cancel_event().set()
            assert dp.is_cancelled()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_clear_cancel(self):
        dp = _make_dp()
        try:
            dp._get_cancel_event().set()
            dp.clear_cancel()
            assert not dp.is_cancelled()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_request_cancel(self):
        dp = _make_dp()
        try:
            for s in dp.strategies.values():
                s.cancel = AsyncMock()
            await dp.request_cancel()
            assert dp.is_cancelled()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_stop(self):
        dp = _make_dp()
        try:
            for s in dp.strategies.values():
                s.cancel = AsyncMock()
            await dp.stop()
            assert dp.is_cancelled()
        finally:
            _cleanup(dp)


class TestDataProcessorClose:
    @pytest.mark.asyncio
    async def test_close_with_cache(self):
        dp = _make_dp()
        try:
            dp.cache = MagicMock()
            dp.cache.close = AsyncMock()
            for s in dp.strategies.values():
                s.cancel = AsyncMock()
            await dp.close()
            dp.cache.close.assert_called_once()
        finally:
            _cleanup(dp)


class TestDataProcessorSyncHistorical:
    @pytest.mark.asyncio
    async def test_sync_historical_data(self):
        dp = _make_dp()
        try:
            mock_result = MagicMock()
            mock_result.status = "completed"
            dp.strategies["historical"].run = AsyncMock(return_value=mock_result)
            result = await dp.sync_historical_data(days=100)
            assert result.status == "completed"
        finally:
            _cleanup(dp)


class TestDataProcessorSyncFinancial:
    @pytest.mark.asyncio
    async def test_sync_financial_reports(self):
        dp = _make_dp()
        try:
            mock_result = MagicMock()
            mock_result.added = 50
            dp.strategies["financial"].run = AsyncMock(return_value=mock_result)
            result = await dp.sync_financial_reports()
            assert result == 50
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_sync_comprehensive_fundamentals(self):
        dp = _make_dp()
        try:
            mock_result = MagicMock()
            mock_result.status = "completed"
            dp.strategies["financial"].run = AsyncMock(return_value=mock_result)
            result = await dp.sync_comprehensive_fundamentals()
            assert result.status == "completed"
        finally:
            _cleanup(dp)


class TestDataProcessorSyncDailyMarket:
    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_with_date(self):
        dp = _make_dp()
        try:
            dp.strategies["historical"].sync_daily_market_snapshot = AsyncMock()
            dp.get_screening_data = AsyncMock(return_value=pd.DataFrame())
            await dp.sync_daily_market_snapshot(trade_date="20240614")
            dp.strategies["historical"].sync_daily_market_snapshot.assert_called_once()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_no_date(self):
        dp = _make_dp()
        try:
            dp.get_latest_trade_date = AsyncMock(return_value="20240614")
            dp.strategies["historical"].sync_daily_market_snapshot = AsyncMock()
            dp.get_screening_data = AsyncMock(return_value=pd.DataFrame())
            await dp.sync_daily_market_snapshot()
            dp.strategies["historical"].sync_daily_market_snapshot.assert_called_once_with("20240614", force=False)
        finally:
            _cleanup(dp)


class TestDataProcessorShouldSyncFinancials:
    @pytest.mark.asyncio
    async def test_force(self):
        dp = _make_dp()
        try:
            result, reason = await dp.should_sync_financials(force=True)
            assert result is True
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_never_synced(self):
        dp = _make_dp()
        try:
            dp.cache.get_sync_status = AsyncMock(return_value=None)
            result, reason = await dp.should_sync_financials()
            assert result is True
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_no_last_sync_date(self):
        dp = _make_dp()
        try:
            dp.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": None})
            result, reason = await dp.should_sync_financials()
            assert result is True
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_recent_sync(self):
        dp = _make_dp()
        try:
            recent = datetime.datetime.now() - datetime.timedelta(days=5)
            dp.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": recent.strftime("%Y-%m-%d")})
            with patch("data.data_processor.get_now", return_value=datetime.datetime.now()):
                result, reason = await dp.should_sync_financials()
                assert result is False
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_old_sync(self):
        dp = _make_dp()
        try:
            old = datetime.datetime.now() - datetime.timedelta(days=35)
            dp.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": old.strftime("%Y-%m-%d")})
            with patch("data.data_processor.get_now", return_value=datetime.datetime.now()):
                result, reason = await dp.should_sync_financials()
                assert result is True
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_exception_returns_true(self):
        dp = _make_dp()
        try:
            dp.cache.get_sync_status = AsyncMock(side_effect=Exception("db error"))
            result, reason = await dp.should_sync_financials()
            assert result is True
        finally:
            _cleanup(dp)


class TestDataProcessorSyncStockBasic:
    @pytest.mark.asyncio
    async def test_cancelled(self):
        dp = _make_dp()
        try:
            dp._get_cancel_event().set()
            result = await dp.sync_stock_basic()
            assert result == 0
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_already_syncing(self):
        dp = _make_dp()
        try:
            dp._is_syncing_basic = True
            result = await dp.sync_stock_basic()
            assert result == 0
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_empty_api_result(self):
        dp = _make_dp()
        try:
            dp.api.get_stock_basic_all = AsyncMock(return_value=None)
            dp.clear_cancel()
            result = await dp.sync_stock_basic()
            assert result == 0
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_successful_sync(self):
        dp = _make_dp()
        try:
            df = pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "list_status": ["L", "D"],
                }
            )
            dp.api.get_stock_basic_all = AsyncMock(return_value=df)
            dp.cache.save_stock_basic = AsyncMock(return_value=2)
            dp.cache.update_sync_status = AsyncMock()
            dp.clear_cancel()
            with patch("data.data_processor.get_now", return_value=datetime.datetime.now()):
                result = await dp.sync_stock_basic()
                assert result == 2
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_no_stocks_saved(self):
        dp = _make_dp()
        try:
            df = pd.DataFrame({"ts_code": ["000001.SZ"], "list_status": ["L"]})
            dp.api.get_stock_basic_all = AsyncMock(return_value=df)
            dp.cache.save_stock_basic = AsyncMock(return_value=0)
            dp.clear_cancel()
            with patch("data.data_processor.get_now", return_value=datetime.datetime.now()):
                result = await dp.sync_stock_basic()
                assert result == 0
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_exception(self):
        dp = _make_dp()
        try:
            dp.api.get_stock_basic_all = AsyncMock(side_effect=Exception("api error"))
            dp.clear_cancel()
            result = await dp.sync_stock_basic()
            assert result == 0
        finally:
            _cleanup(dp)


class TestDataProcessorSyncConcepts:
    @pytest.mark.asyncio
    async def test_cancelled(self):
        dp = _make_dp()
        try:
            dp._get_cancel_event().set()
            result = await dp.sync_concepts()
            assert result == 0
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_empty_concept_list(self):
        dp = _make_dp()
        try:
            dp.api.get_concept_list = AsyncMock(return_value=None)
            dp.clear_cancel()
            result = await dp.sync_concepts()
            assert result == 0
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_successful_sync(self):
        dp = _make_dp()
        try:
            df_c = pd.DataFrame({"code": ["TS1"]})
            dp.api.get_concept_list = AsyncMock(return_value=df_c)
            detail_df = pd.DataFrame(
                {
                    "id": ["TS1"],
                    "concept_name": ["Concept1"],
                    "ts_code": ["000001.SZ"],
                    "name": ["Stock1"],
                }
            )
            dp.api.get_concept_detail_by_id = AsyncMock(return_value=detail_df)
            dp.cache.overwrite_concepts = AsyncMock(return_value=1)
            dp.clear_cancel()
            with patch("data.data_processor.asyncio") as mock_aio:
                mock_aio.Semaphore = asyncio.Semaphore
                mock_aio.create_task = asyncio.create_task
                mock_aio.gather = AsyncMock(return_value=[detail_df])
                mock_aio.sleep = AsyncMock()
                mock_aio.CancelledError = asyncio.CancelledError
                result = await dp.sync_concepts()
                assert result == 1
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_exception(self):
        dp = _make_dp()
        try:
            dp.api.get_concept_list = AsyncMock(side_effect=Exception("api error"))
            dp.clear_cancel()
            result = await dp.sync_concepts()
            assert result == 0
        finally:
            _cleanup(dp)


class TestDataProcessorInitData:
    @pytest.mark.asyncio
    async def test_init_data(self):
        dp = _make_dp()
        try:
            dp.cache.init_db = AsyncMock()
            dp.sync_stock_basic = AsyncMock(return_value=5)
            await dp.init_data()
            dp.cache.init_db.assert_called_once()
        finally:
            _cleanup(dp)


class TestDataProcessorNormalizeContextTradeDate:
    def test_none(self):
        assert DataProcessor._normalize_context_trade_date(None) is None

    def test_nan(self):
        assert DataProcessor._normalize_context_trade_date(float("nan")) is None

    def test_string(self):
        assert DataProcessor._normalize_context_trade_date("20240614") == "20240614"

    def test_datetime(self):
        dt = datetime.datetime(2024, 6, 14)
        assert DataProcessor._normalize_context_trade_date(dt) == "20240614"

    def test_date(self):
        d = datetime.date(2024, 6, 14)
        assert DataProcessor._normalize_context_trade_date(d) == "20240614"


class TestDataProcessorResolveScreeningTradeDate:
    def test_explicit_only(self):
        result = DataProcessor._resolve_screening_trade_date("20240614", None)
        assert result == "20240614"

    def test_from_data(self):
        df = pd.DataFrame({"trade_date": ["20240614"]})
        result = DataProcessor._resolve_screening_trade_date(None, df)
        assert result == "20240614"

    def test_both_match(self):
        df = pd.DataFrame({"trade_date": ["20240614"]})
        result = DataProcessor._resolve_screening_trade_date("20240614", df)
        assert result == "20240614"

    def test_mismatch(self):
        df = pd.DataFrame({"trade_date": ["20240615"]})
        with pytest.raises(RuntimeError, match="mismatch"):
            DataProcessor._resolve_screening_trade_date("20240614", df)

    def test_multiple_dates(self):
        df = pd.DataFrame({"trade_date": ["20240614", "20240615"]})
        with pytest.raises(RuntimeError, match="multiple"):
            DataProcessor._resolve_screening_trade_date(None, df)

    def test_no_date_available(self):
        with pytest.raises(RuntimeError, match="No analysis"):
            DataProcessor._resolve_screening_trade_date(None, None)


class TestDataProcessorPrepareMarketData:
    @pytest.mark.asyncio
    async def test_latest_not_today(self):
        dp = _make_dp()
        try:
            dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 13))
            with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                result = await dp.prepare_market_data()
                assert result == datetime.date(2024, 6, 13)
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_latest_is_today_cached(self):
        dp = _make_dp()
        try:
            today = datetime.date(2024, 6, 14)
            dp.get_latest_trade_date = AsyncMock(return_value=today)
            dp.cache.get_latest_trade_date = AsyncMock(return_value=today)
            with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                result = await dp.prepare_market_data()
                assert result == today
        finally:
            _cleanup(dp)


class TestDataProcessorGetMarketOverview:
    @pytest.mark.asyncio
    async def test_exception(self):
        dp = _make_dp()
        try:
            dp.trade_calendar = MagicMock()
            dp.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
            with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                result = await dp.get_market_overview()
                assert result is None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_batch_query_success(self):
        dp = _make_dp()
        try:
            dp.trade_calendar = MagicMock()
            dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
            index_df = pd.DataFrame(
                {
                    "ts_code": ["000001.SH", "399001.SZ", "399006.SZ"],
                    "pct_chg": [1.5, -2.0, 0.0],
                    "close": [3000.0, 10000.0, 2000.0],
                }
            )
            dp.cache.get_index_daily_range = AsyncMock(return_value=index_df)
            dp.cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
            dp.api.get_moneyflow_hsgt = AsyncMock(return_value=None)
            with patch("data.data_processor.NewsFetcher.get_hot_concepts", new_callable=AsyncMock, return_value=[]):
                with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                    result = await dp.get_market_overview()
            assert result is not None
            assert len(result["indices"]) == 3
            assert result["indices"][0]["color"] == "red"
            assert result["indices"][1]["color"] == "green"
            assert result["indices"][2]["color"] == "grey"
            dp.cache.get_index_daily_range.assert_called_once()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_cache_miss_falls_back_to_api(self):
        dp = _make_dp()
        try:
            dp.trade_calendar = MagicMock()
            dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
            dp.cache.get_index_daily_range = AsyncMock(return_value=None)
            api_df = pd.DataFrame(
                {
                    "ts_code": ["000001.SH"],
                    "pct_chg": [0.5],
                    "close": [3100.0],
                }
            )
            dp.api.get_index_daily = AsyncMock(return_value=api_df)
            dp.cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
            dp.api.get_moneyflow_hsgt = AsyncMock(return_value=None)
            with patch("data.data_processor.NewsFetcher.get_hot_concepts", new_callable=AsyncMock, return_value=[]):
                with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                    result = await dp.get_market_overview()
            assert result is not None
            assert len(result["indices"]) == 3
            assert result["indices"][0]["color"] == "red"
            assert result["indices"][1]["color"] == "grey"
            assert result["indices"][2]["color"] == "grey"
            dp.api.get_index_daily.assert_called_once()
        finally:
            _cleanup(dp)


class TestDataProcessorGetStockHistory:
    @pytest.mark.asyncio
    async def test_with_end_date_string(self):
        dp = _make_dp()
        try:
            dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
            dp.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)])
            dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            await dp.get_stock_history("000001.SZ", days=365, end_date="20240614")
            dp.cache.get_daily_quotes.assert_called_once()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_with_end_date_date(self):
        dp = _make_dp()
        try:
            dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
            dp.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)])
            dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            await dp.get_stock_history("000001.SZ", end_date=datetime.date(2024, 6, 14))
            dp.cache.get_daily_quotes.assert_called_once()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_no_end_date(self):
        dp = _make_dp()
        try:
            dp.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
            dp.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)])
            dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                await dp.get_stock_history("000001.SZ")
                dp.cache.get_daily_quotes.assert_called_once()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_end_date_exception_fallback(self):
        dp = _make_dp()
        try:
            dp.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
            dp.get_trade_dates = AsyncMock(return_value=[datetime.date(2024, 1, 2), datetime.date(2024, 6, 14)])
            dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
            with patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)):
                await dp.get_stock_history("000001.SZ")
                dp.cache.get_daily_quotes.assert_called_once()
        finally:
            _cleanup(dp)


class TestDataProcessorRunDailyUpdate:
    @pytest.mark.asyncio
    async def test_run_daily_update(self):
        dp = _make_dp()
        try:
            dp.init_data = AsyncMock()
            dp.sync_daily_market_snapshot = AsyncMock(return_value=pd.DataFrame())
            dp.sync_financial_reports = AsyncMock()
            with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                mock_instance = MagicMock()
                mock_instance.run_review = AsyncMock()
                mock_rm.return_value = mock_instance
                await dp.run_daily_update()
                dp.init_data.assert_called_once()
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_with_callback(self):
        dp = _make_dp()
        try:
            dp.init_data = AsyncMock()
            dp.sync_daily_market_snapshot = AsyncMock(return_value=pd.DataFrame())
            dp.sync_financial_reports = AsyncMock()
            callback = MagicMock()
            with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                mock_instance = MagicMock()
                mock_instance.run_review = AsyncMock()
                mock_rm.return_value = mock_instance
                await dp.run_daily_update(progress_callback=callback)
                assert callback.call_count >= 4
        finally:
            _cleanup(dp)


class TestDataProcessorPrepareScreeningContext:
    @pytest.mark.asyncio
    async def test_basic_context(self):
        dp = _make_dp()
        try:
            dp._quality_tier = 3
            dp.cache.get_screening_data = AsyncMock(
                return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "is_tradable": [True]})
            )
            dp.cache.get_fundamental_screening_data = AsyncMock(
                return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "is_tradable": [True]})
            )
            dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
            dp.cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
            dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
            dp.cache.get_top_list = AsyncMock(return_value=None)
            dp.cache.get_block_trade = AsyncMock(return_value=None)
            result = await dp.prepare_screening_context(trade_date="20240614")
            assert "screening_data" in result
            assert "_diagnostics" in result
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_with_suspended_stocks(self):
        dp = _make_dp()
        try:
            dp._quality_tier = 3
            dp.cache.get_screening_data = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ"],
                        "trade_date": ["20240614", "20240614"],
                        "is_tradable": [True, False],
                    }
                )
            )
            dp.cache.get_fundamental_screening_data = AsyncMock(return_value=None)
            dp.cache.get_northbound = AsyncMock(return_value=None)
            dp.cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
            dp.cache.get_moneyflow = AsyncMock(return_value=None)
            dp.cache.get_top_list = AsyncMock(return_value=None)
            dp.cache.get_block_trade = AsyncMock(return_value=None)
            result = await dp.prepare_screening_context(trade_date="20240614")
            assert len(result["screening_data"]) == 1
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_no_quality_tier(self):
        dp = _make_dp()
        try:
            dp._quality_tier = None
            dp._assign_basic_tier = AsyncMock()
            dp.cache.get_screening_data = AsyncMock(
                return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "is_tradable": [True]})
            )
            dp.cache.get_fundamental_screening_data = AsyncMock(return_value=None)
            dp.cache.get_northbound = AsyncMock(return_value=None)
            dp.cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
            dp.cache.get_moneyflow = AsyncMock(return_value=None)
            dp.cache.get_top_list = AsyncMock(return_value=None)
            dp.cache.get_block_trade = AsyncMock(return_value=None)
            await dp.prepare_screening_context(trade_date="20240614")
            dp._assign_basic_tier.assert_called_once()
        finally:
            _cleanup(dp)


class TestDataProcessorInitializeSystem:
    @pytest.mark.asyncio
    async def test_quick_mode(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=5)
            dp.sync_concepts = AsyncMock(return_value=3)
            dp.ensure_trade_cal = AsyncMock(return_value=True)
            dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="completed"))
            dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="completed"))
            dp.check_data_health = AsyncMock(return_value={"tier": 3})
            dp.clear_cancel()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
                patch("data.data_processor.ConfigHandler") as mock_ch,
                patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                mock_ch.get_init_history_years.return_value = 1
                result = await dp.initialize_system(quick=True)
                assert result is not None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_stock_basic_returns_zero(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=0)
            dp.clear_cancel()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                result = await dp.initialize_system()
                assert result is None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_calendar_fails(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=5)
            dp.sync_concepts = AsyncMock(return_value=3)
            dp.ensure_trade_cal = AsyncMock(return_value=False)
            dp.clear_cancel()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
                patch("data.data_processor.ConfigHandler") as mock_ch,
                patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                mock_ch.get_init_history_years.return_value = 1
                result = await dp.initialize_system()
                assert result is None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_full_mode(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=5)
            dp.sync_concepts = AsyncMock(return_value=3)
            dp.ensure_trade_cal = AsyncMock(return_value=True)
            dp.strategies["historical"].run = AsyncMock(return_value=MagicMock(status="completed"))
            dp.strategies["financial"].run = AsyncMock(return_value=MagicMock(status="completed", added=10))
            dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="completed"))
            dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="completed"))
            dp.check_data_health = AsyncMock(return_value={"tier": 3})
            dp.clear_cancel()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
                patch("data.data_processor.ConfigHandler") as mock_ch,
                patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                mock_ch.get_init_history_years.return_value = 1
                result = await dp.initialize_system()
                assert result is not None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_historical_failed(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=5)
            dp.sync_concepts = AsyncMock(return_value=3)
            dp.ensure_trade_cal = AsyncMock(return_value=True)
            dp.strategies["historical"].run = AsyncMock(return_value=MagicMock(status="failed", errors=["err"]))
            dp.clear_cancel()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
                patch("data.data_processor.ConfigHandler") as mock_ch,
                patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                mock_ch.get_init_history_years.return_value = 1
                result = await dp.initialize_system()
                assert result is None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_financial_failed(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=5)
            dp.sync_concepts = AsyncMock(return_value=3)
            dp.ensure_trade_cal = AsyncMock(return_value=True)
            dp.strategies["historical"].run = AsyncMock(return_value=MagicMock(status="completed"))
            dp.strategies["financial"].run = AsyncMock(return_value=MagicMock(status="failed", errors=["err"]))
            dp.clear_cancel()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
                patch("data.data_processor.ConfigHandler") as mock_ch,
                patch("data.data_processor.get_now", return_value=datetime.datetime(2024, 6, 14)),
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                mock_ch.get_init_history_years.return_value = 1
                result = await dp.initialize_system()
                assert result is None
        finally:
            _cleanup(dp)

    @pytest.mark.asyncio
    async def test_cancelled_after_step1(self):
        dp = _make_dp()
        try:
            dp.sync_stock_basic = AsyncMock(return_value=5)
            dp.sync_concepts = AsyncMock(return_value=3)
            dp.ensure_trade_cal = AsyncMock(return_value=True)
            dp.clear_cancel()
            dp._get_cancel_event().set()
            with (
                patch("data.data_dictionary.validate_schema_definitions"),
                patch("data.data_processor.I18n") as mock_i18n,
            ):
                mock_i18n.get.side_effect = lambda k, **kw: k
                result = await dp.initialize_system()
                assert result is None
        finally:
            _cleanup(dp)


class TestDataProcessorCancelControl:
    def test_is_cancelled_default(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc._cancel_event = None
        with patch("data.data_processor.get_loop_local") as mock_gll:
            mock_evt = MagicMock()
            mock_evt.is_set.return_value = False
            mock_gll.return_value = mock_evt
            assert proc.is_cancelled() is False

    def test_clear_cancel(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc._cancel_event = None
        with patch("data.data_processor.get_loop_local") as mock_gll:
            mock_evt = MagicMock()
            mock_gll.return_value = mock_evt
            proc.clear_cancel()
            mock_evt.clear.assert_called_once()


class TestDataProcessorCancelEvent:
    def setup_method(self):
        DataProcessor._reset_singleton()

    def teardown_method(self):
        DataProcessor._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_get_cancel_event(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        evt = dp._get_cancel_event()
        assert evt is not None

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_is_cancelled_initially_false(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        assert dp.is_cancelled() is False


class TestDataProcessorClearCancel:
    def setup_method(self):
        DataProcessor._reset_singleton()

    def teardown_method(self):
        DataProcessor._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_clear_cancel(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        dp._get_cancel_event().set()
        dp.clear_cancel()
        assert dp.is_cancelled() is False


class TestDataProcessorGetFundamentalScreeningData:
    @pytest.mark.asyncio
    async def test_delegates_to_cache(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc.cache = MagicMock()
        proc.cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        await proc.get_fundamental_screening_data("20240614")
        proc.cache.get_fundamental_screening_data.assert_called_once_with("20240614")


class TestDataProcessorGetScreeningData:
    @pytest.mark.asyncio
    async def test_delegates_to_cache(self):
        proc = DataProcessor.__new__(DataProcessor)
        proc.cache = MagicMock()
        proc.cache.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        await proc.get_screening_data("20240614")
        proc.cache.get_screening_data.assert_called_once_with("20240614")


class TestDataProcessorInit:
    def setup_method(self):
        DataProcessor._reset_singleton()

    def teardown_method(self):
        DataProcessor._reset_singleton()

    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.ConfigHandler")
    def test_init_sets_token(self, mock_ch, mock_tc, mock_cm):
        mock_ch.get_token.return_value = "test_token"
        dp = DataProcessor()
        assert dp._current_token == "test_token"

    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.ConfigHandler")
    def test_init_creates_api(self, mock_ch, mock_tc, mock_cm):
        mock_ch.get_token.return_value = "test_token"
        DataProcessor()
        mock_tc.assert_called_once_with(token="test_token")


class TestDataProcessorRequestCancel:
    def setup_method(self):
        DataProcessor._reset_singleton()

    def teardown_method(self):
        DataProcessor._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_request_cancel_sets_event(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        for _name, strategy in dp.strategies.items():
            strategy.cancel = AsyncMock()
        await dp.request_cancel()
        assert dp.is_cancelled() is True


class TestDataProcessorResetSingleton:
    def test_reset(self):
        DataProcessor._reset_singleton()
        assert DataProcessor._instance is None


class TestDataProcessorSingleton:
    def setup_method(self):
        DataProcessor._reset_singleton()

    def teardown_method(self):
        DataProcessor._reset_singleton()

    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.ConfigHandler")
    def test_singleton_same_instance(self, mock_ch, mock_tc, mock_cm):
        mock_ch.get_token.return_value = "test_token"
        dp1 = DataProcessor()
        dp2 = DataProcessor()
        assert dp1 is dp2


class TestDataProcessorStop:
    def setup_method(self):
        DataProcessor._reset_singleton()

    def teardown_method(self):
        DataProcessor._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.data_processor.TushareClient")
    @patch("data.data_processor.CacheManager")
    @patch("data.data_processor.TradeCalendarService")
    @patch("data.data_processor.ConfigHandler")
    async def test_stop_sets_cancel(self, mock_ch, mock_tc, mock_cache, mock_api):
        mock_ch.get_token.return_value = "test-token"
        dp = DataProcessor()
        for _name, strategy in dp.strategies.items():
            strategy.cancel = AsyncMock()
        await dp.stop()
        assert dp.is_cancelled() is True
