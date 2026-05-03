import pytest
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


class TestMarketDataServiceProcessFetchResults:
    def test_all_success(self):
        indices_config = [("000001.SH", "home_index_sh"), ("399001.SZ", "home_index_sz")]
        results = [
            {"name": "上证", "value": "3000.00", "change": "+1.00%", "color": "red"},
            {"name": "深证", "value": "10000.00", "change": "-0.50%", "color": "green"},
            {"name": "北向", "value": "50亿", "sub": "流入", "color": "red"},
            [{"name": "AI"}],
        ]
        data = MarketDataService._process_fetch_results(results, "20240614", indices_config)
        assert data["date"] == "20240614"
        assert len(data["indices"]) == 2
        assert data["hsgt"]["name"] == "北向"
        assert len(data["hot_concepts"]) == 1

    def test_index_exception(self):
        indices_config = [("000001.SH", "home_index_sh")]
        results = [
            Exception("API error"),
            {"name": "北向", "value": "-", "sub": "-", "color": "grey"},
            [],
        ]
        data = MarketDataService._process_fetch_results(results, "20240614", indices_config)
        assert data["indices"][0]["color"] == "grey"

    def test_hsgt_exception(self):
        indices_config = [("000001.SH", "home_index_sh")]
        results = [
            {"name": "上证", "value": "3000.00", "change": "+1.00%", "color": "red"},
            Exception("API error"),
            [],
        ]
        data = MarketDataService._process_fetch_results(results, "20240614", indices_config)
        assert data["hsgt"]["color"] == "grey"

    def test_hot_concepts_exception(self):
        indices_config = [("000001.SH", "home_index_sh")]
        results = [
            {"name": "上证", "value": "3000.00", "change": "+1.00%", "color": "red"},
            {"name": "北向", "value": "-", "sub": "-", "color": "grey"},
            Exception("API error"),
        ]
        data = MarketDataService._process_fetch_results(results, "20240614", indices_config)
        assert data["hot_concepts"] == []


class TestMarketDataServiceGetIndex:
    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_cache_hit(self, mock_tc, mock_cache_cls, mock_api):
        mock_cache = MagicMock()
        mock_cache.get_index_daily = AsyncMock(
            return_value=MagicMock(
                empty=False,
                iloc=MagicMock(return_value=MagicMock(get=lambda k: {"pct_chg": 1.5, "close": 3000.0}.get(k))),
            )
        )
        mock_cache_cls.return_value = mock_cache
        svc = MarketDataService()
        df = MagicMock()
        df.empty = False
        df.iloc = [MagicMock()]
        df.iloc[0].get = lambda k: {"pct_chg": 1.5, "close": 3000.0}.get(k)
        mock_cache.get_index_daily = AsyncMock(return_value=df)
        result = await svc._get_index("000001.SH", "home_index_sh", "20240614")
        assert result["color"] == "red"
        assert "+" in result["change"]

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_negative_change(self, mock_tc, mock_cache_cls, mock_api):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        df = MagicMock()
        df.empty = False
        df.iloc = [MagicMock()]
        df.iloc[0].get = lambda k: {"pct_chg": -2.0, "close": 2900.0}.get(k)
        mock_cache.get_index_daily = AsyncMock(return_value=df)
        svc = MarketDataService()
        result = await svc._get_index("000001.SH", "home_index_sh", "20240614")
        assert result["color"] == "green"

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_empty_returns_grey(self, mock_tc, mock_cache_cls, mock_api):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        mock_cache.get_index_daily = AsyncMock(return_value=None)
        mock_api_inst = MagicMock()
        mock_api_inst.get_index_daily = AsyncMock(return_value=None)
        mock_tc.return_value = mock_api_inst
        svc = MarketDataService()
        svc.api = mock_api_inst
        result = await svc._get_index("000001.SH", "home_index_sh", "20240614")
        assert result["color"] == "grey"


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
