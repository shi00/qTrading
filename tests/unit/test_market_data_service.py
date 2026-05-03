import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from data.domain_services.market_data_service import MarketDataService


class TestMarketDataServiceInit:
    @patch("data.domain_services.market_data_service.CacheManager")
    def test_init(self, mock_cm):
        svc = MarketDataService()
        assert svc is not None


class TestMarketDataServiceFetchMarketData:
    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.CacheManager")
    async def test_fetch_market_data(self, mock_cm):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        svc = MarketDataService()
        svc._fetch_market_data = AsyncMock(return_value={})
        result = await svc._fetch_market_data()
        assert isinstance(result, dict)


class TestMarketDataServiceConfig:
    def test_indices_config(self):
        assert len(MarketDataService.INDICES_CONFIG) == 3
        codes = [c for c, _ in MarketDataService.INDICES_CONFIG]
        assert "000001.SH" in codes
        assert "399001.SZ" in codes
        assert "399006.SZ" in codes

    def test_hot_concepts_limit(self):
        assert MarketDataService.HOT_CONCEPTS_LIMIT == 8


class TestMarketDataServiceListeners:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_add_listener(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        cb = MagicMock()
        svc.add_listener(cb)
        assert cb in svc._listeners

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_remove_listener(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        cb = MagicMock()
        svc.add_listener(cb)
        svc.remove_listener(cb)
        assert cb not in svc._listeners

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_remove_nonexistent_no_error(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        cb = MagicMock()
        svc.remove_listener(cb)


class TestMarketDataServiceStop:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_resets_running(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc.stop()
        assert svc._running is False


class TestMarketDataServiceSafeFloat:
    def test_none(self):
        assert MarketDataService._safe_float(None) == 0.0

    def test_nan(self):
        assert MarketDataService._safe_float(float("nan")) == 0.0

    def test_valid_number(self):
        assert MarketDataService._safe_float(3.14) == 3.14

    def test_string_number(self):
        assert MarketDataService._safe_float("2.5") == 2.5

    def test_invalid_string(self):
        assert MarketDataService._safe_float("abc") == 0.0

    def test_zero(self):
        assert MarketDataService._safe_float(0) == 0.0


class TestMarketDataServiceGetIndicesBatch:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    def _make_svc(self):
        with (
            patch("data.domain_services.market_data_service.TushareClient"),
            patch("data.domain_services.market_data_service.CacheManager") as mock_cm_cls,
            patch("data.domain_services.market_data_service.TradeCalendarService"),
        ):
            mock_cache = MagicMock()
            mock_cm_cls.return_value = mock_cache
            svc = MarketDataService()
            svc.cache = mock_cache
            return svc

    @pytest.mark.asyncio
    async def test_cache_hit_all_indices(self):
        svc = self._make_svc()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SH", "399001.SZ", "399006.SZ"],
                "pct_chg": [1.5, -2.0, 0.0],
                "close": [3000.0, 10000.0, 2000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=df)
        codes = ["000001.SH", "399001.SZ", "399006.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 3
        assert result[0]["color"] == "red"
        assert result[1]["color"] == "green"
        assert result[2]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_cache_miss_falls_back_to_api(self):
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=None)
        api_df = pd.DataFrame(
            {
                "ts_code": ["000001.SH", "399001.SZ"],
                "pct_chg": [0.5, -1.0],
                "close": [3100.0, 9500.0],
            }
        )
        svc.api = MagicMock()
        svc.api.get_index_daily = AsyncMock(return_value=api_df)
        codes = ["000001.SH", "399001.SZ", "399006.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 3
        assert result[0]["color"] == "red"
        assert result[1]["color"] == "green"
        assert result[2]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_both_miss_returns_grey(self):
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=None)
        svc.api = MagicMock()
        svc.api.get_index_daily = AsyncMock(return_value=None)
        codes = ["000001.SH"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 1
        assert result[0]["color"] == "grey"
        assert result[0]["value"] == "-"

    @pytest.mark.asyncio
    async def test_partial_data_fills_missing_with_grey(self):
        svc = self._make_svc()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SH"],
                "pct_chg": [1.0],
                "close": [3000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=df)
        codes = ["000001.SH", "399001.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 2
        assert result[0]["color"] == "red"
        assert result[1]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_empty_cache_df_falls_back(self):
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())
        api_df = pd.DataFrame(
            {
                "ts_code": ["399006.SZ"],
                "pct_chg": [3.0],
                "close": [2100.0],
            }
        )
        svc.api = MagicMock()
        svc.api.get_index_daily = AsyncMock(return_value=api_df)
        codes = ["399006.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 1
        assert result[0]["color"] == "red"

    @pytest.mark.asyncio
    async def test_nan_pct_chg_treated_as_zero(self):
        svc = self._make_svc()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SH"],
                "pct_chg": [float("nan")],
                "close": [3000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=df)
        codes = ["000001.SH"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 1
        assert result[0]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_none_pct_chg_treated_as_zero(self):
        svc = self._make_svc()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SH"],
                "pct_chg": [None],
                "close": [3000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=df)
        codes = ["000001.SH"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 1
        assert result[0]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_empty_codes_returns_empty(self):
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=None)
        result = await svc._get_indices_batch([], "20240614")
        assert result == []

    @pytest.mark.asyncio
    async def test_i18n_name_in_result(self):
        svc = self._make_svc()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SH"],
                "pct_chg": [1.0],
                "close": [3000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=df)
        codes = ["000001.SH"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 1
        assert result[0]["name"] != ""
        assert result[0]["change"] == "+1.00%"

    @pytest.mark.asyncio
    async def test_unknown_code_gets_grey(self):
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=None)
        svc.api = MagicMock()
        svc.api.get_index_daily = AsyncMock(return_value=None)
        codes = ["999999.XX"]
        result = await svc._get_indices_batch(codes, "20240614")
        assert len(result) == 1
        assert result[0]["color"] == "grey"
        assert result[0]["value"] == "-"


class TestMarketDataServiceFetchMarketDataIntegration:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_fetch_builds_cached_data(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        mock_cache = MagicMock()
        mock_cm_cls.return_value = mock_cache
        mock_tc = MagicMock()
        mock_tc_cls.return_value = mock_tc

        svc = MarketDataService()
        svc.cache = mock_cache
        svc.api = mock_tc

        index_df = pd.DataFrame(
            {
                "ts_code": ["000001.SH", "399001.SZ", "399006.SZ"],
                "pct_chg": [1.0, -0.5, 2.0],
                "close": [3000.0, 10000.0, 2000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=index_df)

        hsgt_df = MagicMock()
        hsgt_df.empty = False
        hsgt_df.iloc = [MagicMock()]
        hsgt_df.iloc[0].get = lambda k: 500.0 if k == "north_money" else 0
        svc.cache.get_moneyflow_hsgt = AsyncMock(return_value=hsgt_df)

        mock_news.get_hot_concepts = AsyncMock(return_value=[{"name": "AI", "change": "+3%", "color": "red"}])

        with patch("data.domain_services.market_data_service.TradeCalendarService") as mock_cal_cls:
            mock_cal = MagicMock()
            mock_cal.get_latest_trade_date = AsyncMock(return_value=None)
            mock_cal_cls.return_value = mock_cal
            svc.trade_calendar = mock_cal

            await svc._fetch_market_data()

        assert svc._cached_data is not None
        assert svc._cached_data["date"] is not None
        assert len(svc._cached_data["indices"]) == 3
        assert svc._cached_data["hsgt"] is not None
        assert len(svc._cached_data["hot_concepts"]) == 1

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_fetch_index_exception_fills_grey(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        mock_cache = MagicMock()
        mock_cm_cls.return_value = mock_cache
        mock_tc = MagicMock()
        mock_tc_cls.return_value = mock_tc

        svc = MarketDataService()
        svc.cache = mock_cache
        svc.api = mock_tc

        svc._get_indices_batch = AsyncMock(side_effect=Exception("DB error"))
        svc.cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
        svc.api.get_moneyflow_hsgt = AsyncMock(return_value=None)
        mock_news.get_hot_concepts = AsyncMock(return_value=[])

        with patch("data.domain_services.market_data_service.TradeCalendarService") as mock_cal_cls:
            mock_cal = MagicMock()
            mock_cal.get_latest_trade_date = AsyncMock(return_value=None)
            mock_cal_cls.return_value = mock_cal
            svc.trade_calendar = mock_cal

            await svc._fetch_market_data()

        assert svc._cached_data is not None
        assert len(svc._cached_data["indices"]) == 3
        assert all(idx["color"] == "grey" for idx in svc._cached_data["indices"])

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_fetch_notifies_listeners(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        mock_cache = MagicMock()
        mock_cm_cls.return_value = mock_cache
        mock_tc = MagicMock()
        mock_tc_cls.return_value = mock_tc

        svc = MarketDataService()
        svc.cache = mock_cache
        svc.api = mock_tc

        index_df = pd.DataFrame(
            {
                "ts_code": ["000001.SH"],
                "pct_chg": [1.0],
                "close": [3000.0],
            }
        )
        svc.cache.get_index_daily_range = AsyncMock(return_value=index_df)
        svc.cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
        svc.api.get_moneyflow_hsgt = AsyncMock(return_value=None)
        mock_news.get_hot_concepts = AsyncMock(return_value=[])

        listener = MagicMock()
        svc.add_listener(listener)

        with patch("data.domain_services.market_data_service.TradeCalendarService") as mock_cal_cls:
            mock_cal = MagicMock()
            mock_cal.get_latest_trade_date = AsyncMock(return_value=None)
            mock_cal_cls.return_value = mock_cal
            svc.trade_calendar = mock_cal

            await svc._fetch_market_data()

        listener.assert_called_once()


class TestMarketDataServiceGetHsgt:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_large_inflow(self, mock_tc, mock_cache_cls, mock_api):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        df = MagicMock()
        df.empty = False
        df.iloc = [MagicMock()]
        df.iloc[0].get = lambda k: 500.0 if k == "north_money" else 0
        mock_cache.get_moneyflow_hsgt = AsyncMock(return_value=df)
        svc = MarketDataService()
        result = await svc._get_hsgt("20240614")
        assert result["color"] == "red"

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_small_outflow(self, mock_tc, mock_cache_cls, mock_api):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        df = MagicMock()
        df.empty = False
        df.iloc = [MagicMock()]
        df.iloc[0].get = lambda k: -50.0 if k == "north_money" else 0
        mock_cache.get_moneyflow_hsgt = AsyncMock(return_value=df)
        svc = MarketDataService()
        result = await svc._get_hsgt("20240614")
        assert result["color"] == "green"

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_empty_returns_grey(self, mock_tc, mock_cache_cls, mock_api):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        mock_cache.get_moneyflow_hsgt = AsyncMock(return_value=None)
        mock_api_inst = MagicMock()
        mock_api_inst.get_moneyflow_hsgt = AsyncMock(return_value=None)
        mock_tc.return_value = mock_api_inst
        svc = MarketDataService()
        svc.api = mock_api_inst
        result = await svc._get_hsgt("20240614")
        assert result["color"] == "grey"


class TestMarketDataServiceStartStop:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_start_sets_running(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        with patch("data.domain_services.market_data_service.asyncio.create_task"):
            svc.start()
            assert svc._running is True
            svc.stop()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_start_idempotent(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        with patch("data.domain_services.market_data_service.asyncio.create_task"):
            svc.start()
            assert svc._running is True
            svc.start()
            assert svc._running is True
            svc.stop()

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_get_cached_data_none(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        assert svc.get_cached_data() is None

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_get_cached_data_with_value(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._cached_data = {"date": "20240614"}
        assert svc.get_cached_data() == {"date": "20240614"}


class TestMarketDataServiceSafeFetch:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_safe_fetch_not_running(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = False
        svc._fetch_market_data = AsyncMock()
        await svc._safe_fetch()
        svc._fetch_market_data.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_safe_fetch_exception_handled(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc._fetch_market_data = AsyncMock(side_effect=Exception("network error"))
        await svc._safe_fetch()
