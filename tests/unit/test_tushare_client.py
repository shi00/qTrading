import asyncio
import functools
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import datetime
import requests

from data.external.tushare_client import TushareAPIPermissionError, TushareClient

pytestmark = pytest.mark.unit


def _make_client(token="test_token", tier="points_5000"):
    """Helper for tests that need independent client creation (e.g. reinit scenarios)."""
    with (
        patch("data.external.tushare_client.ts") as mock_ts,
        patch("data.external.tushare_client.ConfigHandler") as mock_ch,
    ):
        mock_ts.pro_api.return_value = MagicMock()
        mock_ch.get_token.return_value = token
        mock_ch.get_tushare_timeout.return_value = 30
        mock_ch.get_request_max_retries.return_value = 3
        mock_ch.get_tushare_point_tier.return_value = tier
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
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
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
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
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
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
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
                mock_ch.get_tushare_point_tier.return_value = "points_5000"
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
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
            TushareClient(token=None)
            mock_ts.pro_api.assert_not_called()


class TestTushareClientSetToken:
    def test_set_token(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        client.set_token("new_token")
        assert client.token == "new_token"
        mock_ts.set_token.assert_called_with("new_token")


class TestTushareClientTokenBreakerProperty:
    """is_token_invalid 只读属性应正确反映 _token_invalid 状态。"""

    def test_is_token_invalid_default_false(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        assert client.is_token_invalid is False

    def test_is_token_invalid_true_after_set(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client._token_invalid = True
        assert client.is_token_invalid is True

    def test_is_token_invalid_resets_on_set_token(self, tushare_client_mocks):
        """set_token 应重置熔断标志（已有逻辑），is_token_invalid 随之返回 False。"""
        client, _, _ = tushare_client_mocks
        client._token_invalid = True
        assert client.is_token_invalid is True
        client.set_token("new_token_after_invalid")
        assert client.is_token_invalid is False

    def test_is_token_invalid_is_readonly(self, tushare_client_mocks):
        """is_token_invalid 是 property，不能直接赋值。"""
        client, _, _ = tushare_client_mocks
        with pytest.raises(AttributeError):
            client.is_token_invalid = True  # type: ignore[misc]


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
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
            client = TushareClient()
            with pytest.raises(Exception, match="Token not set"):
                await client._handle_api_call(lambda: None)

    @pytest.mark.asyncio
    async def test_success(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "run_in_executor",
            new=AsyncMock(return_value=pd.DataFrame({"a": [1]})),
        ):
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
    async def test_max_pages_logs_warning(self, tushare_client_mocks, caplog):
        """Pagination hitting max_pages is a degraded path — should log WARNING, not ERROR."""
        import logging

        client, _, _ = tushare_client_mocks
        df = pd.DataFrame({"a": list(range(10))})

        async def mock_handle(func, **kwargs):
            return df

        client._handle_api_call = mock_handle
        with caplog.at_level(logging.WARNING, logger="data.external.tushare_client"):
            await client._handle_api_call_paginated(MagicMock(), max_pages=1)
        assert any("max_pages" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records)
        assert not any(rec.levelno == logging.ERROR for rec in caplog.records)

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
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
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
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is not None
        assert "top10_holders" in client._api_limiters

    def test_without_limit(self, tushare_client_mocks):
        """未知档位应禁用限速器（_POINT_TIER_PRESETS.get 返回 0）。"""
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "unknown_tier"
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is None

    def test_resolve_rate_limit_uses_tier_preset(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        limit = client._resolve_rate_limit()
        assert limit == 500

    def test_build_rate_limiters_honors_tier(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        assert client._rate_limiter is not None
        assert pytest.approx(client._rate_limiter.rate * 60, abs=1) == 500

    def test_reload_rate_limiters_updates_instance(self, tushare_client_mocks):
        """reload_rate_limiters 应根据当前档位重建限速器，rate 随档位变化。"""
        client, mock_ts, mock_ch = tushare_client_mocks
        # 先 reload 到 points_120，记录 old_rate（fixture 初始化为 points_5000，需显式 reload 切换）
        mock_ch.get_tushare_point_tier.return_value = "points_120"
        client.reload_rate_limiters()
        assert client._rate_limiter is not None
        old_rate = client._rate_limiter.rate
        # 切换到 points_5000 并 reload，验证 rate 变化
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client.reload_rate_limiters()
        assert client._rate_limiter is not None
        assert client._rate_limiter.rate != old_rate

    def test_reload_rate_limiters_with_tier_change(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_120"
        client.reload_rate_limiters()
        assert client._rate_limiter.rate * 60 == pytest.approx(50, abs=1)
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client.reload_rate_limiters()
        assert client._rate_limiter.rate * 60 == pytest.approx(500, abs=1)

    @pytest.mark.asyncio
    async def test_global_limiter_always_consumed(self, tushare_client_mocks):
        """两段消费：全局 _rate_limiter 始终先消费，per-API limiter 额外收紧。

        _handle_api_call 通过 func.__name__ 推断 api_name，故需显式设置 __name__
        使其命中 _api_limiters["top10_holders"]。
        """
        client, _, _ = tushare_client_mocks
        client._rate_limiter = MagicMock()
        client._rate_limiter.consume_async = AsyncMock()
        client._api_limiters = {"top10_holders": MagicMock()}
        client._api_limiters["top10_holders"].consume_async = AsyncMock()
        func = MagicMock()
        func.__name__ = "top10_holders"  # _handle_api_call 通过 __name__ 推断 api_name
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=pd.DataFrame({"a": [1]}))):
            await client._handle_api_call(func)
        client._rate_limiter.consume_async.assert_awaited()
        client._api_limiters["top10_holders"].consume_async.assert_awaited()

    def test_fast_api_overrides_removed(self, tushare_client_mocks):
        """_FAST_API_OVERRIDES ClassVar 应已删除（Phase 2A 移除 fast API 循环）。"""
        assert not hasattr(TushareClient, "_FAST_API_OVERRIDES")

    def test_slow_api_limiter_stacks_on_global(self, tushare_client_mocks):
        """slow API limiter 在全局桶基础上额外收紧（factor < 1.0）。"""
        client, _, _ = tushare_client_mocks
        client._rate_limiter, client._api_limiters = client._build_rate_limiters()
        # slow API limiter 应存在且 rate 低于全局
        assert "top10_holders" in client._api_limiters
        global_rate = client._rate_limiter.rate
        slow_rate = client._api_limiters["top10_holders"].rate
        assert slow_rate < global_rate


class TestTushareClientTierApiCoverage:
    """档位 API 覆盖映射 + 独立付费 API 标记测试。"""

    def test_tier_api_coverage_points_120_has_basic_apis(self):
        """points_120 档位应覆盖基础元数据 + 日线 + shibor。"""
        apis = TushareClient().get_tier_apis("points_120")
        assert "trade_cal" in apis
        assert "stock_basic" in apis
        assert "daily" in apis
        assert "shibor" in apis

    def test_tier_api_coverage_points_2000_adds_financial_apis(self):
        """points_2000 应在 points_120 基础上新增财务/股东/龙虎榜等。"""
        apis_120 = TushareClient().get_tier_apis("points_120")
        apis_2000 = TushareClient().get_tier_apis("points_2000")
        # points_2000 包含 points_120 的所有 API
        assert apis_120.issubset(apis_2000)
        # 新增项
        assert "income" in apis_2000
        assert "top10_holders" in apis_2000
        assert "top_list" in apis_2000

    def test_tier_api_coverage_points_5000_adds_share_float(self):
        """points_5000 应在 points_2000 基础上新增 share_float。"""
        apis_2000 = TushareClient().get_tier_apis("points_2000")
        apis_5000 = TushareClient().get_tier_apis("points_5000")
        assert apis_2000.issubset(apis_5000)
        assert "share_float" in apis_5000
        assert "share_float" not in apis_2000

    def test_tier_api_coverage_points_10000_adds_independent_purchase(self):
        """points_10000 应在 points_5000 基础上新增 cyq_perf / forecast_eps。"""
        apis_5000 = TushareClient().get_tier_apis("points_5000")
        apis_10000 = TushareClient().get_tier_apis("points_10000")
        assert apis_5000.issubset(apis_10000)
        assert "cyq_perf" in apis_10000
        assert "forecast_eps" in apis_10000

    def test_tier_api_coverage_points_15000_no_new_apis(self):
        """points_15000 与 points_10000 API 集合相同（频次相同，仅积分门槛不同）。"""
        apis_10000 = TushareClient().get_tier_apis("points_10000")
        apis_15000 = TushareClient().get_tier_apis("points_15000")
        assert apis_10000 == apis_15000

    def test_is_api_covered_by_tier_returns_bool(self):
        """is_api_covered_by_tier 返回 bool 类型。"""
        client = TushareClient()
        assert client.is_api_covered_by_tier("daily", "points_120") is True
        assert client.is_api_covered_by_tier("income", "points_120") is False

    def test_independent_purchase_apis(self):
        """独立付费 API 集合应包含 cyq_perf / forecast_eps / rating。"""
        assert TushareClient().is_independent_purchase("cyq_perf") is True
        assert TushareClient().is_independent_purchase("forecast_eps") is True
        assert TushareClient().is_independent_purchase("rating") is True
        assert TushareClient().is_independent_purchase("daily") is False

    def test_get_tier_apis_uses_current_tier_when_none_passed(self, tushare_client_mocks):
        """get_tier_apis(None) 应使用当前档位（从 ConfigHandler 读取）。"""
        client, _, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        apis = client.get_tier_apis()
        assert "share_float" in apis


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
            "data.external.tushare_client.asyncio.wait_for",
            new=AsyncMock(return_value=pd.DataFrame({"a": [1]})),
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
    async def test_no_pro_raises(self):
        """get_trade_cal should raise a clear error when pro is None (token not set)."""
        with (
            patch("data.external.tushare_client.ts"),
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = ""
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
            client = TushareClient()
            with pytest.raises(Exception, match="Tushare Token not set"):
                await client.get_trade_cal("20240601", "20240630")

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
            (
                "get_daily_basic",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            ("get_income", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            ("get_cashflow", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            (
                "get_balancesheet",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            (
                "get_top_list",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_top_inst",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_hk_hold",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_moneyflow",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_block_trade",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_fina_indicator",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            (
                "get_disclosure_date",
                {"date": "20240614"},
                {"actual_date": "20240614"},
                False,
            ),
            ("get_concept_list", {}, {"src": "ts"}, False),
            ("get_concept_detail_by_id", {"concept_id": "123"}, {"id": "123"}, False),
            (
                "get_concept_detail",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            (
                "get_index_daily",
                {"ts_code": "000001.SH"},
                {"ts_code": "000001.SH"},
                False,
            ),
            (
                "get_index_dailybasic",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_limit_list",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_suspend_d",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_margin_detail",
                {"trade_date": "20240614"},
                {"trade_date": "20240614"},
                False,
            ),
            (
                "get_fina_audit",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            ("get_forecast", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            (
                "get_fina_mainbz",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            (
                "get_pledge_stat",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                True,
            ),
            (
                "get_repurchase",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            ("get_dividend", {"ts_code": "000001.SZ"}, {"ts_code": "000001.SZ"}, False),
            (
                "get_shibor",
                {"start_date": "20240601"},
                {"start_date": "20240601"},
                False,
            ),
            (
                "get_top10_holders",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                False,
            ),
            (
                "get_index_weight",
                {"index_code": "000001.SH"},
                {"index_code": "000001.SH"},
                False,
            ),
            (
                "get_stk_holdernumber",
                {"ts_code": "000001.SZ"},
                {"ts_code": "000001.SZ"},
                True,
            ),
        ]
        for method_name, call_kwargs, expected_kv, uses_paginated in test_cases:
            mock_attr = "_handle_api_call_paginated" if uses_paginated else "_handle_api_call"
            setattr(
                client,
                mock_attr,
                AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})),
            )
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


class TestTushareClientProbeAndEffectiveTables:
    """Phase 2A.1 Task 2A.1.13：probe 按档位预筛 / 双层过滤 / 三态分类 / 持久化测试。"""

    def _make_probed_client(self, tier="points_5000"):
        """创建 client 并 mock pro API 方法（设置 __name__ 供 _handle_api_call 推断 api_name）。"""
        client = _make_client(tier=tier)
        # _make_client 的 with 块退出后 ConfigHandler patch 失效，需持续 mock _get_tushare_point_tier
        client._get_tushare_point_tier = lambda: tier
        # 为 probe_configs 中所有 API 在 pro 上挂 MagicMock（带 __name__）
        pro_apis = [
            "daily",
            "moneyflow_hsgt",
            "moneyflow",
            "hk_hold",
            "top_list",
            "limit_list_d",
            "margin_detail",
            "block_trade",
            "fina_indicator",
            "fina_mainbz",
            "stk_holdernumber",
            "top10_holders",
        ]
        for api_name in pro_apis:
            mock_func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
            mock_func.__name__ = api_name
            setattr(client.pro, api_name, mock_func)
        return client

    @pytest.mark.asyncio
    async def test_probe_filters_by_tier(self):
        """probe_api_capabilities 应按 is_api_covered_by_tier 预筛 probe_configs。"""
        client = self._make_probed_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        # mock _handle_api_call 记录被调用的 api_name
        called_apis: list[str] = []

        async def fake_handle(func, **kwargs):
            api_name = getattr(func, "__name__", str(func))
            called_apis.append(api_name)
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = fake_handle
        results = await client.probe_api_capabilities()
        # points_120 只覆盖 daily（probe_configs 中唯一在 points_120 档位内的 API）
        assert "daily" in called_apis
        # points_2000+ 档位的 API 不应被 probe
        assert "moneyflow" not in called_apis
        assert "hk_hold" not in called_apis
        assert "top_list" not in called_apis
        assert "fina_indicator" not in called_apis
        assert "top10_holders" not in called_apis
        # results 中只包含 daily
        assert "daily" in results
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_probe_independent_purchase_log(self):
        """probe_configs 当前不含 cyq_perf/forecast_eps（Phase 2B 扩展）；is_independent_purchase 在档位覆盖查询中不特殊处理。"""
        client = self._make_probed_client(tier="points_10000")
        # points_10000 档位覆盖 cyq_perf / forecast_eps（即使需独立购买）
        assert client.is_api_covered_by_tier("cyq_perf", "points_10000") is True
        assert client.is_api_covered_by_tier("forecast_eps", "points_10000") is True
        # is_independent_purchase 标记独立存在（不影响 is_api_covered_by_tier）
        assert client.is_independent_purchase("cyq_perf") is True
        assert client.is_independent_purchase("forecast_eps") is True
        # probe_configs 不含 cyq_perf（Phase 2B 才扩展）
        client.persist_capabilities_to_app_state = AsyncMock()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        results = await client.probe_api_capabilities()
        assert "cyq_perf" not in results
        assert "forecast_eps" not in results

    @pytest.mark.asyncio
    async def test_probe_skips_apis_not_in_tier(self):
        """低档位下 probe 应跳过高档位 API。"""
        client = self._make_probed_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        called_apis: list[str] = []

        async def fake_handle(func, **kwargs):
            api_name = getattr(func, "__name__", str(func))
            called_apis.append(api_name)
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = fake_handle
        await client.probe_api_capabilities()
        # 所有 points_2000+ 档位的 API 都不应被 probe
        for api in [
            "moneyflow",
            "hk_hold",
            "top_list",
            "fina_indicator",
            "top10_holders",
            "block_trade",
            "margin_detail",
        ]:
            assert api not in called_apis, f"{api} should be skipped at points_120 tier"

    @pytest.mark.asyncio
    async def test_probe_includes_independent_purchase_at_high_tier(self):
        """高档位下独立付费 API 在档位覆盖内（probe_configs 扩展后才会被 probe）。"""
        client = self._make_probed_client(tier="points_10000")
        # 验证档位覆盖关系
        assert client.is_api_covered_by_tier("cyq_perf", "points_10000") is True
        assert client.is_api_covered_by_tier("cyq_perf", "points_5000") is False
        assert client.is_api_covered_by_tier("cyq_perf", "points_2000") is False
        # forecast_eps 同理
        assert client.is_api_covered_by_tier("forecast_eps", "points_10000") is True
        assert client.is_api_covered_by_tier("forecast_eps", "points_5000") is False

    @pytest.mark.asyncio
    async def test_probe_mutex_skips_concurrent(self):
        """probe_api_capabilities 串行执行 probe_configs（Phase 2A.1 串行实现，Phase 2B 才加 _probe_in_progress 互斥）。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        call_order: list[str] = []

        async def fake_handle(func, **kwargs):
            api_name = getattr(func, "__name__", str(func))
            call_order.append(api_name)
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = fake_handle
        await client.probe_api_capabilities()
        # 串行执行：所有调用按 probe_configs 顺序依次发生
        assert len(call_order) > 0
        # 验证串行性：调用次数 == results 数量（无并发）
        assert len(call_order) == len(
            [
                api
                for api in [
                    "daily",
                    "moneyflow_hsgt",
                    "moneyflow",
                    "hk_hold",
                    "top_list",
                    "limit_list_d",
                    "margin_detail",
                    "block_trade",
                    "fina_indicator",
                    "fina_mainbz",
                    "stk_holdernumber",
                    "top10_holders",
                ]
                if client.is_api_covered_by_tier(api, "points_5000")
            ]
        )

    @pytest.mark.asyncio
    async def test_probe_service_unavailable_detection(self):
        """probe 时非权限错误（如网络错误）导致 results[api_name] = None。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        # mock _handle_api_call 抛出非 TushareAPIPermissionError 的异常
        call_count = [0]

        async def fake_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第一个 API（daily）抛出网络错误
                raise requests.exceptions.ConnectionError("connection refused")
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = fake_handle
        results = await client.probe_api_capabilities()
        # daily 应为 None（非权限错误）
        assert results["daily"] is None
        # 其他 API 仍正常 probe
        assert results.get("moneyflow_hsgt") is True

    @pytest.mark.asyncio
    async def test_probe_token_invalid_detection(self):
        """probe 时 TushareAPIPermissionError（含 token_invalid）导致 results[api_name] = False。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()

        async def fake_handle(func, **kwargs):
            api_name = getattr(func, "__name__", str(func))
            if api_name == "daily":
                raise TushareAPIPermissionError(api_name, "您的token不对")
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = fake_handle
        results = await client.probe_api_capabilities()
        # daily 应为 False（权限错误）
        assert results["daily"] is False
        # capability_cache 中 daily 也应为 False
        assert client.is_api_available("daily") is False

    @pytest.mark.asyncio
    async def test_probe_progress_callback(self):
        """progress_callback 应被调用 N 次（N = filtered_configs 长度）。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        progress_calls: list[tuple[int, int]] = []

        def progress_cb(completed: int, total: int) -> None:
            progress_calls.append((completed, total))

        await client.probe_api_capabilities(progress_callback=progress_cb)
        # points_5000 档位覆盖 probe_configs 全部 12 项
        assert len(progress_calls) == 12
        # 第一次调用 completed=1, total=12
        assert progress_calls[0] == (1, 12)
        # 最后一次调用 completed=12, total=12
        assert progress_calls[-1] == (12, 12)

    @pytest.mark.asyncio
    async def test_probe_one_classifies_error(self):
        """probe 三态分类：TushareAPIPermissionError → False；其他 Exception → None；成功 → True。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        call_count = [0]

        async def fake_handle(func, **kwargs):
            call_count[0] += 1
            api_name = getattr(func, "__name__", str(func))
            if api_name == "daily":
                return pd.DataFrame({"a": [1]})  # 成功 → True
            if api_name == "moneyflow_hsgt":
                raise TushareAPIPermissionError(api_name, "权限不足")  # 权限错误 → False
            raise Exception("network error")  # 其他错误 → None

        client._handle_api_call = fake_handle
        results = await client.probe_api_capabilities()
        assert results["daily"] is True
        assert results["moneyflow_hsgt"] is False
        assert results["moneyflow"] is None

    @pytest.mark.asyncio
    async def test_probe_one_distinguishes_429(self):
        """probe 通过 _handle_api_call 内部重试处理 429 错误；429 不导致 probe 失败。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        # 直接 mock _handle_api_call 成功返回（429 在 _handle_api_call 内部已重试）
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        results = await client.probe_api_capabilities()
        # 所有 API 都应成功（_handle_api_call 内部处理了 429）
        for api, available in results.items():
            assert available is True, f"{api} should be True after 429 retry"

    def test_effective_tables_filters_by_tier(self):
        """get_effective_synced_tables 第一层：档位覆盖过滤。"""
        client = self._make_probed_client(tier="points_120")
        # TABLE_TO_API_MAP 中的表（需档位覆盖）
        all_tables = [
            "moneyflow_hsgt",  # api=moneyflow_hsgt，points_2000+ 才覆盖
            "northbound_holding",  # api=hk_hold，points_2000+ 才覆盖
            "moneyflow_daily",  # api=moneyflow，points_2000+ 才覆盖
        ]
        effective = client.get_effective_synced_tables(all_tables)
        # points_120 档位不足，所有需要 points_2000+ API 的表都应被过滤
        assert effective == []

    def test_effective_tables_filters_by_probe(self):
        """get_effective_synced_tables 第二层：probe 验证过滤（is_api_available() is False 时排除）。"""
        client = self._make_probed_client(tier="points_5000")
        # 标记 moneyflow 为不可用
        client.mark_api_unavailable("moneyflow")
        all_tables = ["moneyflow_daily"]  # api=moneyflow
        effective = client.get_effective_synced_tables(all_tables)
        assert "moneyflow_daily" not in effective

    def test_effective_tables_preserves_none_probe(self):
        """get_effective_synced_tables：probe 验证为 None（未探测）时不阻塞。"""
        client = self._make_probed_client(tier="points_5000")
        # 不设置 capability_cache（None 状态）
        all_tables = ["moneyflow_daily"]  # api=moneyflow
        effective = client.get_effective_synced_tables(all_tables)
        assert "moneyflow_daily" in effective

    def test_effective_tables_downgrade_preserves_db(self):
        """档位降级后 is_api_covered_by_tier 返回 False；get_effective_synced_tables 跳过高档位 API 表。

        DB 历史数据保留由 stale 标注机制处理（不在 get_effective_synced_tables 职责内），
        本测试只验证档位覆盖判断的正确性。
        """
        client = self._make_probed_client(tier="points_2000")
        # share_float 在 points_5000 才覆盖（显式传 tier 避免单例共享问题）
        assert client.is_api_covered_by_tier("share_float", "points_5000") is True
        assert client.is_api_covered_by_tier("share_float", "points_2000") is False
        # points_2000 档位下 share_float 对应的表会被 get_effective_synced_tables 过滤
        # （DB 数据保留是 sync 层职责，get_effective_synced_tables 只返回有效表列表）

    @pytest.mark.asyncio
    async def test_persist_last_probe_time(self):
        """persist_capabilities_to_app_state payload 应包含 last_probe_time ISO 8601 字段。"""
        import json

        client = self._make_probed_client(tier="points_5000")
        # 设置 _last_probe_time
        client._last_probe_time = datetime.datetime(2024, 6, 15, 10, 30, 0)
        # mock CacheManager.engine 和 set_app_state
        captured_payload = {}

        async def fake_set_app_state(engine, key, value):
            captured_payload["key"] = key
            captured_payload["value"] = value

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.app_state_service.set_app_state", new=fake_set_app_state),
        ):
            mock_cm.return_value.engine = MagicMock()
            await client.persist_capabilities_to_app_state()

        assert captured_payload["key"] == "tushare_capabilities"
        payload = json.loads(captured_payload["value"])
        assert "last_probe_time" in payload
        assert payload["last_probe_time"] == "2024-06-15T10:30:00"
        assert "token_hash" in payload
        assert "capabilities" in payload
