import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import datetime

from data.external.tushare_client import TushareClient


@pytest.fixture(autouse=True)
def reset_singleton():
    TushareClient._reset_singleton()
    yield
    TushareClient._reset_singleton()


@pytest.fixture
def tushare_client_mocks():
    with (
        patch("data.external.tushare_client.ts") as mock_ts,
        patch("data.external.tushare_client.ConfigHandler") as mock_ch,
    ):
        mock_ts.pro_api.return_value = MagicMock()
        mock_ch.get_token.return_value = "test_token"
        mock_ch.get_tushare_timeout.return_value = 30
        mock_ch.get_request_max_retries.return_value = 3
        mock_ch.get_tushare_api_limit.return_value = 120
        client = TushareClient(token="test_token")
        yield client, mock_ts, mock_ch


class TestTushareClientInit:
    def test_init_with_token(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        assert client.token == "test_token"
        assert client.timeout == 30

    def test_init_without_token(self):
        with (
            patch("data.external.tushare_client.ts"),
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = ""
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 0
            client = TushareClient()
            assert client.pro is None


class TestTushareClientSetToken:
    def test_set_token(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_api_limit.return_value = 120
        client.set_token("new_token")
        assert client.token == "new_token"
        mock_ts.set_token.assert_called_with("new_token")


class TestTushareClientHandleApiCall:
    @pytest.mark.asyncio
    async def test_no_pro_raises(self):
        with (
            patch("data.external.tushare_client.ts"),
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = ""
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 1
            mock_ch.get_tushare_api_limit.return_value = 0
            client = TushareClient()
            with pytest.raises(Exception, match="Token not set"):
                await client._handle_api_call(lambda: None)

    @pytest.mark.asyncio
    async def test_success(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=pd.DataFrame({"a": [1]}))):
            result = await client._handle_api_call(mock_func)
            assert result is not None

    @pytest.mark.asyncio
    async def test_column_renames(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_func = MagicMock()
        mock_func.__name__ = "cn_cpi"
        df = pd.DataFrame({"month": ["202401"], "nt_val": [100.0]})
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=df)):
            result = await client._handle_api_call(mock_func)
            assert "period" in result.columns
            assert "cpi" in result.columns


class TestTushareClientHandleApiCallPaginated:
    @pytest.mark.asyncio
    async def test_single_page(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        df = pd.DataFrame({"a": list(range(5))})
        client._handle_api_call = AsyncMock(return_value=df)
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=1)
        assert result is not None
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_empty_first_page(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame())
        result = await client._handle_api_call_paginated(MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_none_first_page(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=None)
        result = await client._handle_api_call_paginated(MagicMock())
        assert result is None


class TestTushareClientGetTradeDates:
    def test_no_pro_raises(self):
        with (
            patch("data.external.tushare_client.ts"),
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = ""
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 0
            client = TushareClient()
            with pytest.raises(Exception, match="Tushare Token not set"):
                client.get_trade_dates("20240101", "20240630")

    def test_success(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": ["20240614"]})
        result = client.get_trade_dates("20240601", "20240630")
        assert "20240614" in result

    def test_with_date_objects(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": ["20240614"]})
        result = client.get_trade_dates(datetime.date(2024, 6, 1), datetime.date(2024, 6, 30))
        assert len(result) > 0


class TestTushareClientIsTradingDay:
    def test_with_cached_year(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._loaded_years.add("2024")
        client._trade_cal_cache.add("20240614")
        assert client.is_trading_day("20240614") is True
        assert client.is_trading_day("20240615") is False

    def test_loads_year_from_api(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": ["20240614", "20240617"]})
        result = client.is_trading_day("20240614")
        assert result is True
        assert "2024" in client._loaded_years

    def test_none_uses_today(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._loaded_years.add("2026")
        client._trade_cal_cache.add("20260502")
        result = client.is_trading_day(None)
        assert isinstance(result, bool)

    def test_date_object(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._loaded_years.add("2024")
        client._trade_cal_cache.add("20240614")
        result = client.is_trading_day(datetime.date(2024, 6, 14))
        assert result is True

    def test_api_failure_fallback(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = True
            result = client.is_trading_day("20240614")
            assert result is True


class TestTushareClientApiMethods:
    @pytest.mark.asyncio
    async def test_get_stock_basic(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_stock_basic()
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_daily_quotes(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        daily_df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "close": [10.0]})
        adj_df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "adj_factor": [1.0]})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return daily_df
            return adj_df

        client._handle_api_call = mock_handle
        result = await client.get_daily_quotes(trade_date="20240614")
        assert result is not None
        assert "adj_factor" in result.columns

    @pytest.mark.asyncio
    async def test_get_daily_quotes_empty(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=None)
        result = await client.get_daily_quotes(trade_date="20240614")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_macro_data_rejected(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        result = await client.get_macro_data("invalid_api")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_macro_data_success(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"period": ["202401"]}))
        result = await client.get_macro_data("cn_cpi")
        assert result is not None


class TestTushareClientBuildRateLimiters:
    def test_with_limit(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_api_limit.return_value = 120
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is not None
        assert "top10_holders" in client._api_limiters

    def test_without_limit(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_api_limit.return_value = 0
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is None


class TestTushareClientConstants:
    def test_column_renames_has_cn_cpi(self):
        assert "cn_cpi" in TushareClient._COLUMN_RENAMES

    def test_column_renames_has_cn_ppi(self):
        assert "cn_ppi" in TushareClient._COLUMN_RENAMES

    def test_column_renames_has_cn_m(self):
        assert "cn_m" in TushareClient._COLUMN_RENAMES

    def test_slow_api_overrides(self):
        assert "top10_holders" in TushareClient._SLOW_API_OVERRIDES
        assert "concept_detail" in TushareClient._SLOW_API_OVERRIDES

    def test_fast_api_overrides(self):
        assert "daily" in TushareClient._FAST_API_OVERRIDES
        assert "daily_basic" in TushareClient._FAST_API_OVERRIDES
        assert "trade_cal" in TushareClient._FAST_API_OVERRIDES


class TestTushareClientReset:
    def test_reset_clears_instance(self):
        TushareClient._instance = MagicMock()
        TushareClient._reset_singleton()
        assert TushareClient._instance is None


class TestTushareClientExecutorTimeout:
    @pytest.mark.asyncio
    async def test_asyncio_wait_for_wraps_run_in_executor(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
        with patch(
            "data.external.tushare_client.asyncio.wait_for", new=AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        ) as mock_wait:
            result = await client._handle_api_call(mock_func)
            assert result is not None
            mock_wait.assert_called_once()
            call_args = mock_wait.call_args
            assert call_args[1]["timeout"] == client.timeout * 1.5

    @pytest.mark.asyncio
    async def test_asyncio_timeout_error_triggers_network_retry(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.max_retries = 2
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TimeoutError()
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None
                assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_builtin_timeout_error_triggers_network_retry(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.max_retries = 2
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise TimeoutError("read timeout")
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None
                assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_timeout_exhausts_retries_raises(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.max_retries = 1

        async def mock_wait_for(coro, timeout=None):
            raise TimeoutError()

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(RuntimeError, match="retries exhausted"):
                await client._handle_api_call(MagicMock())
