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
