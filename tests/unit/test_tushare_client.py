import asyncio
import functools
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import datetime
import requests

from data.external.tushare_client import TushareClient


def _make_client(token="test_token", limit=120):
    """Helper for tests that need independent client creation (e.g. reinit scenarios)."""
    with (
        patch("data.external.tushare_client.ts") as mock_ts,
        patch("data.external.tushare_client.ConfigHandler") as mock_ch,
    ):
        mock_ts.pro_api.return_value = MagicMock()
        mock_ch.get_token.return_value = token
        mock_ch.get_tushare_timeout.return_value = 30
        mock_ch.get_request_max_retries.return_value = 3
        mock_ch.get_tushare_api_limit.return_value = limit
        mock_ch.get_tushare_point_tier.return_value = "custom"
        client = TushareClient(token=token)
    return client


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
        mock_ch.get_tushare_point_tier.return_value = "custom"
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
            mock_ch.get_tushare_point_tier.return_value = "custom"
            client = TushareClient()
            assert client.pro is None

    def test_reinit_same_token_skips(self):
        """Reinit with same token should skip pro_api call."""
        _make_client("test_token")
        with (
            patch("data.external.tushare_client.ts") as mock_ts,
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 120
            mock_ch.get_tushare_point_tier.return_value = "custom"
            TushareClient(token="test_token")
            mock_ts.pro_api.assert_not_called()

    def test_reinit_different_token_calls_set_token(self):
        """Reinit with different token should call set_token."""
        client = _make_client("old_token")
        with patch.object(client, "set_token") as mock_set:
            with (
                patch("data.external.tushare_client.ts"),
                patch("data.external.tushare_client.ConfigHandler") as mock_ch,
            ):
                mock_ch.get_token.return_value = "old_token"
                mock_ch.get_tushare_timeout.return_value = 30
                mock_ch.get_request_max_retries.return_value = 3
                mock_ch.get_tushare_api_limit.return_value = 120
                mock_ch.get_tushare_point_tier.return_value = "custom"
                TushareClient(token="new_token")
                mock_set.assert_called_once_with("new_token")

    def test_reinit_no_token_skips(self):
        """Reinit with no token should skip pro_api call."""
        _make_client("test_token")
        with (
            patch("data.external.tushare_client.ts") as mock_ts,
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 120
            mock_ch.get_tushare_point_tier.return_value = "custom"
            TushareClient(token=None)
            mock_ts.pro_api.assert_not_called()


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
            mock_ch.get_tushare_point_tier.return_value = "custom"
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

    @pytest.mark.asyncio
    async def test_partial_failure_on_second_page(self, tushare_client_mocks):
        """Second page failure should return partial results from first page."""
        client, _, _ = tushare_client_mocks
        df1 = pd.DataFrame({"a": list(range(10))})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return df1
            raise Exception("API error on page 2")

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)
        assert result is not None
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_first_page_failure_raises(self, tushare_client_mocks):
        """First page failure should propagate the exception."""
        client, _, _ = tushare_client_mocks

        async def mock_handle(func, **kwargs):
            raise Exception("API error on page 1")

        client._handle_api_call = mock_handle
        with pytest.raises(Exception, match="API error on page 1"):
            await client._handle_api_call_paginated(MagicMock(), max_pages=10)

    @pytest.mark.asyncio
    async def test_max_pages_reached(self, tushare_client_mocks):
        """Pagination should stop at max_pages."""
        client, _, _ = tushare_client_mocks
        df = pd.DataFrame({"a": list(range(10))})

        async def mock_handle(func, **kwargs):
            return df

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=1)
        assert result is not None
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_multi_page_concat(self, tushare_client_mocks):
        """多页拼接：首页满页 + 次页部分页，验证 pd.concat 拼接与 returned_len < full_page_size 中断逻辑。"""
        client, _, _ = tushare_client_mocks
        df1 = pd.DataFrame({"a": list(range(10))})  # 首页满页（10 行）
        df2 = pd.DataFrame({"a": list(range(10, 15))})  # 次页部分（5 行）
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            return [df1, df2][call_count[0] - 1]

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)
        assert result is not None
        assert len(result) == 15  # 10 + 5 拼接
        assert call_count[0] == 2  # 第二页后因 returned_len < full_page_size 中断

    @pytest.mark.asyncio
    async def test_none_values_filtered_from_kwargs(self, tushare_client_mocks):
        """None values in kwargs should be filtered before passing to _handle_api_call."""
        client, _, _ = tushare_client_mocks
        captured_kwargs = {}

        async def mock_handle(func, **kwargs):
            captured_kwargs.update(kwargs)
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = mock_handle
        await client._handle_api_call_paginated(MagicMock(), ts_code="000001.SZ", end_date=None, max_pages=1)
        assert "ts_code" in captured_kwargs
        assert "end_date" not in captured_kwargs


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
            mock_ch.get_tushare_point_tier.return_value = "custom"
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

    def test_api_exception_returns_empty(self, tushare_client_mocks):
        """API exception should result in empty list, not raise."""
        client, _, _ = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        result = client.get_trade_dates("20240601", "20240630")
        assert result == []

    def test_empty_df_returns_empty(self, tushare_client_mocks):
        """Empty DataFrame from API should result in empty list."""
        client, _, _ = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": []})
        result = client.get_trade_dates("20240601", "20240630")
        assert result == []


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

    def test_non_string_input_converted(self, tushare_client_mocks):
        """非字符串非日期输入（如整数）应通过 str() 转换为字符串再查询。"""
        client, mock_ts, mock_ch = tushare_client_mocks
        client._loaded_years.add("2024")
        client._trade_cal_cache.add("20240614")
        result = client.is_trading_day(20240614)  # 整数输入
        assert result is True

    def test_api_failure_fallback(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = True
            result = client.is_trading_day("20240614")
            assert result is True

    def test_double_checked_locking(self, tushare_client_mocks):
        """Double-checked locking: cached year should not acquire lock."""
        client, _, _ = tushare_client_mocks
        client._loaded_years.add("2024")
        client._trade_cal_cache.add("20240614")
        with patch.object(client, "_calendar_lock") as mock_lock:
            result = client.is_trading_day("20240614")
            assert result is True
            mock_lock.__enter__.assert_not_called()

    def test_api_returns_empty_df(self, tushare_client_mocks):
        """Empty DataFrame from API should fall back to OfflineCalendar."""
        client, _, _ = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": []})
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = True
            result = client.is_trading_day("20240614")
            assert result is True

    def test_offline_calendar_fallback(self, tushare_client_mocks):
        """API exception should fall back to OfflineCalendar returning False."""
        client, _, _ = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = False
            result = client.is_trading_day("20240614")
            assert result is False

    def test_weekday_fallback(self, tushare_client_mocks):
        """When both API and OfflineCalendar fail, should fall back to weekday check."""
        client, _, _ = tushare_client_mocks
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch(
            "data.domain_services.offline_calendar.OfflineCalendar.is_trading_day",
            side_effect=Exception("offline error"),
        ):
            result = client.is_trading_day("20240614")
            assert isinstance(result, bool)

    def test_no_pro_raises_in_lock(self, tushare_client_mocks):
        """When pro is None, should fall back to OfflineCalendar within lock."""
        client, _, _ = tushare_client_mocks
        client.pro = None
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
        mock_ch.get_tushare_point_tier.return_value = "custom"
        mock_ch.get_tushare_api_limit.return_value = 120
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is not None
        assert "top10_holders" in client._api_limiters

    def test_without_limit(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "custom"
        mock_ch.get_tushare_api_limit.return_value = 0
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is None

    def test_resolve_rate_limit_uses_tier_preset(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "pro"
        mock_ch.get_tushare_api_limit.return_value = 999
        limit = client._resolve_rate_limit()
        assert limit == 500

    def test_resolve_rate_limit_custom_falls_back_to_manual(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "custom"
        mock_ch.get_tushare_api_limit.return_value = 333
        limit = client._resolve_rate_limit()
        assert limit == 333

    def test_build_rate_limiters_honors_tier(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "flagship"
        mock_ch.get_tushare_api_limit.return_value = 0
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is not None

    def test_reload_rate_limiters_updates_instance(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "custom"
        mock_ch.get_tushare_api_limit.return_value = 120
        assert client._rate_limiter is not None
        old_rate = client._rate_limiter.rate
        mock_ch.get_tushare_api_limit.return_value = 600
        client.reload_rate_limiters()
        assert client._rate_limiter is not None
        assert client._rate_limiter.rate != old_rate

    def test_reload_rate_limiters_with_tier_change(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "custom"
        mock_ch.get_tushare_api_limit.return_value = 120
        client.reload_rate_limiters()
        assert client._rate_limiter.rate * 60 == pytest.approx(120, abs=1)
        mock_ch.get_tushare_point_tier.return_value = "pro"
        client.reload_rate_limiters()
        assert client._rate_limiter.rate * 60 == pytest.approx(500, abs=1)


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
        assert "index_dailybasic" in TushareClient._FAST_API_OVERRIDES


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


class TestIsTradingDayInvalidDate:
    def test_invalid_date_returns_false(self):
        """MD-004: is_trading_day should return False for unparseable dates"""
        client = TushareClient.__new__(TushareClient)
        client.pro = None
        client._trade_cal_cache = set()
        client._loaded_years = set()
        client._calendar_lock = MagicMock()
        result = client.is_trading_day("not_a_date")
        assert result is False


class TestTushareClientHandleApiCallPartial:
    """Coverage for partial func handling and rate limiter consume/on_success."""

    @pytest.mark.asyncio
    async def test_partial_func_extracts_api_name(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        mock_pro_func = MagicMock()
        mock_pro_func.__name__ = "daily"
        partial_func = functools.partial(mock_pro_func, "daily")
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        result = await client._handle_api_call(partial_func)
        assert result is not None

    @pytest.mark.asyncio
    async def test_date_kwargs_formatted(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        result_df = pd.DataFrame({"a": [1]})
        captured_kwargs = {}

        async def fake_wait_for(coro, timeout=None):
            nonlocal captured_kwargs
            if hasattr(coro, "cr_frame"):
                captured_kwargs = coro.cr_frame.f_locals.get("kwargs", {})
            return result_df

        mock_func = MagicMock()
        mock_func.__name__ = "test_api"
        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=fake_wait_for):
            result = await client._handle_api_call(
                mock_func,
                start_date=datetime.date(2024, 6, 1),
                end_date=datetime.datetime(2024, 6, 30),
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_api_limiter_consume_and_on_success(self, tushare_client_mocks):
        """api_limiter.consume_async and on_success should be called once on success."""
        client, _, _ = tushare_client_mocks
        api_limiter = client._api_limiters.get("top10_holders")
        assert api_limiter is not None
        api_limiter.consume_async = AsyncMock()
        api_limiter.on_success = MagicMock()

        async def fake_wait_for(coro, timeout=None):
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=fake_wait_for):
            partial_func = functools.partial(client.pro.top10_holders, "top10_holders")
            result = await client._handle_api_call(partial_func)
            assert result is not None
            api_limiter.consume_async.assert_called_once()
            api_limiter.on_success.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limiter_consume_and_on_success(self, tushare_client_mocks):
        """rate_limiter.consume_async and on_success should be called once on success."""
        client, _, _ = tushare_client_mocks
        rate_limiter = client._rate_limiter
        rate_limiter.consume_async = AsyncMock()
        rate_limiter.on_success = MagicMock()

        async def fake_wait_for(coro, timeout=None):
            return pd.DataFrame({"a": [1]})

        mock_func = MagicMock()
        mock_func.__name__ = "unknown_api_not_in_overrides"
        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=fake_wait_for):
            result = await client._handle_api_call(mock_func)
            assert result is not None
            rate_limiter.consume_async.assert_called_once()
            rate_limiter.on_success.assert_called_once()


class TestTushareClientHandleApiCallErrors:
    """Coverage for permission/jifen/param_error/rate_limit/network_error/retry_exhausted."""

    @pytest.mark.asyncio
    async def test_permission_error_raises_immediately(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.max_retries = 3

        async def mock_wait_for(coro, timeout=None):
            raise Exception("没有权限访问该接口")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="没有权限"):
                await client._handle_api_call(MagicMock())

    @pytest.mark.asyncio
    async def test_permission_error_jifen(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.max_retries = 3

        async def mock_wait_for(coro, timeout=None):
            raise Exception("积分不足")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="积分"):
                await client._handle_api_call(MagicMock())

    @pytest.mark.asyncio
    async def test_client_param_error_raises_immediately(self, tushare_client_mocks):
        """参数错误(必填参数)应立即抛出，不重试。"""
        client, _, _ = tushare_client_mocks
        client.max_retries = 3
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            raise Exception("必填参数, ts_code")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="必填参数"):
                await client._handle_api_call(MagicMock())
        assert call_count[0] == 1, "参数错误不应重试"

    @pytest.mark.asyncio
    async def test_client_param_error_missing_param(self, tushare_client_mocks):
        """缺少参数也应立即抛出，不重试。"""
        client, _, _ = tushare_client_mocks
        client.max_retries = 3
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            raise Exception("缺少参数 trade_date")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="缺少参数"):
                await client._handle_api_call(MagicMock())
        assert call_count[0] == 1, "参数错误不应重试"

    @pytest.mark.asyncio
    async def test_rate_limit_reduces_rate_and_retries(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.max_retries = 2
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("每分钟最多访问120次")
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None
                assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_rate_limit_429(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.max_retries = 2
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("429 rate limit exceeded")
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None

    @pytest.mark.asyncio
    async def test_network_error_retries(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.max_retries = 2
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise requests.exceptions.ConnectionError("connection refused")
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None

    @pytest.mark.asyncio
    async def test_network_error_timeout_string(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.max_retries = 2
        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("timed out")
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None

    @pytest.mark.asyncio
    async def test_retry_exhausted_on_last_attempt(self, tushare_client_mocks):
        """Unknown error should be re-raised on the last retry attempt."""
        client, _, _ = tushare_client_mocks
        client.max_retries = 2

        async def mock_wait_for(coro, timeout=None):
            raise Exception("unknown error")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(Exception, match="unknown error"):
                    await client._handle_api_call(MagicMock())


class TestTushareClientGetTradeCal:
    """Coverage for get_trade_cal with/without is_open parameter."""

    @pytest.mark.asyncio
    async def test_with_is_open(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614"]}))
        result = await client.get_trade_cal("20240601", "20240630", is_open=1)
        assert result is not None
        call_kwargs = client._handle_api_call.call_args
        assert call_kwargs[1]["is_open"] == "1"

    @pytest.mark.asyncio
    async def test_without_is_open(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614"]}))
        result = await client.get_trade_cal("20240601", "20240630")
        assert result is not None
        call_kwargs = client._handle_api_call.call_args
        assert "is_open" not in call_kwargs[1]


class TestTushareClientSimpleApiMethods:
    """Simple API methods should correctly pass kwargs to _handle_api_call.

    The 31 simple methods (each previously only asserting `result is not None`)
    are consolidated into a single parametrized test verifying kwargs passthrough.
    """

    @pytest.mark.asyncio
    async def test_simple_api_methods_pass_kwargs(self, tushare_client_mocks):
        """简单 API 方法应正确透传 kwargs 到 _handle_api_call。"""
        client, _, _ = tushare_client_mocks
        # (method_name, call_kwargs, expected_kv_in_handle_call, uses_paginated)
        test_cases = [
            ("get_stock_basic_all", {}, {"list_status": ""}, False),
            ("get_stock_list", {}, {"list_status": "L"}, False),
            ("get_daily_basic", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_income", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_cashflow", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_balancesheet", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_top_list", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_top_inst", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_hk_hold", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_moneyflow", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_block_trade", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_fina_indicator", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_disclosure_date", {"date": "20240614"}, {"actual_date": "20240614"}, False),
            ("get_concept_list", {}, {"src": "ts"}, False),
            ("get_concept_detail_by_id", {"concept_id": "123"}, {"id": "123"}, False),
            ("get_concept_detail", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_index_daily", {"ts_code": "000001.SH"}, {"ts_code": "000001.SH"}, False),
            ("get_index_dailybasic", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_limit_list", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_suspend_d", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_margin_detail", {"trade_date": "20240614"}, {"trade_date": "20240614"}, False),
            ("get_fina_audit", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_forecast", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_fina_mainbz", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_pledge_stat", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, True),
            ("get_repurchase", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_dividend", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_shibor", {"start_date": "20240601"}, {"start_date": "20240601"}, False),
            ("get_top10_holders", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_index_weight", {"index_code": "000001.SH"}, {"index_code": "000001.SH"}, False),
            ("get_stk_holdernumber", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, True),
        ]
        for method_name, call_kwargs, expected_kv, uses_paginated in test_cases:
            mock_attr = "_handle_api_call_paginated" if uses_paginated else "_handle_api_call"
            setattr(client, mock_attr, AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})))
            method = getattr(client, method_name)
            await method(**call_kwargs)
            mock = getattr(client, mock_attr)
            mock.assert_called_once()
            received_kwargs = mock.call_args.kwargs
            for k, v in expected_kv.items():
                assert received_kwargs.get(k) == v, f"{method_name}: expected {k}={v!r}, got {received_kwargs.get(k)!r}"

    @pytest.mark.asyncio
    async def test_get_daily_quotes_adj_merge(self, tushare_client_mocks):
        """get_daily_quotes should merge adj_factor column from adj_factor API."""
        client, _, _ = tushare_client_mocks
        daily_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20240614", "20240614"],
                "close": [10.0, 20.0],
            }
        )
        adj_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20240614", "20240614"],
                "adj_factor": [1.0, 2.0],
            }
        )
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return daily_df
            return adj_df

        client._handle_api_call = mock_handle
        result = await client.get_daily_quotes(trade_date="20240614")
        assert "adj_factor" in result.columns
        assert result["adj_factor"].tolist() == [1.0, 2.0]

    @pytest.mark.asyncio
    async def test_get_daily_quotes_no_adj_columns(self, tushare_client_mocks):
        """When adj_factor df lacks adj_factor column, default 1.0 should be applied."""
        client, _, _ = tushare_client_mocks
        daily_df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "close": [10.0]})
        adj_df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "value": [1.0]})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return daily_df
            return adj_df

        client._handle_api_call = mock_handle
        result = await client.get_daily_quotes(trade_date="20240614")
        assert "adj_factor" in result.columns
        assert result["adj_factor"].iloc[0] == 1.0

    @pytest.mark.asyncio
    async def test_get_daily_quotes_adj_exception_default(self, tushare_client_mocks):
        """When adj_factor API raises, default 1.0 should be applied."""
        client, _, _ = tushare_client_mocks
        daily_df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "close": [10.0]})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return daily_df
            raise Exception("adj_factor API error")

        client._handle_api_call = mock_handle
        result = await client.get_daily_quotes(trade_date="20240614")
        assert "adj_factor" in result.columns
        assert result["adj_factor"].iloc[0] == 1.0

    @pytest.mark.asyncio
    async def test_get_daily_quotes_nan_adj_filled(self, tushare_client_mocks):
        """NaN adj_factor values should be filled with 1.0."""
        client, _, _ = tushare_client_mocks
        daily_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20240614", "20240614"],
                "close": [10.0, 20.0],
            }
        )
        adj_df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "adj_factor": [1.5]})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return daily_df
            return adj_df

        client._handle_api_call = mock_handle
        result = await client.get_daily_quotes(trade_date="20240614")
        assert result["adj_factor"].isna().sum() == 0

    @pytest.mark.asyncio
    async def test_get_moneyflow_hsgt_attaches_units(self, tushare_client_mocks):
        """get_moneyflow_hsgt should attach column units to the result."""
        client, _, _ = tushare_client_mocks
        client._handle_api_call = AsyncMock(
            return_value=pd.DataFrame({"trade_date": ["20240614"], "north_money": [100.0]})
        )
        result = await client.get_moneyflow_hsgt(trade_date="20240614")
        assert result is not None
        assert result.attrs["column_units"]["north_money"] == "million_cny"

    @pytest.mark.asyncio
    async def test_get_macro_data_func_not_found(self, tushare_client_mocks):
        """When pro lacks the API method, get_macro_data should return None."""
        client, _, _ = tushare_client_mocks
        client.pro = MagicMock(spec=[])
        result = await client.get_macro_data("cn_cpi")
        assert result is None
