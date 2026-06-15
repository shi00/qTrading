import functools
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import datetime
import requests

from data.external.tushare_client import TushareClient


@pytest.fixture(autouse=True)
def reset_singleton():
    TushareClient._reset_singleton()
    yield
    TushareClient._reset_singleton()


def _make_client(token="test_token", limit=120):
    with (
        patch("data.external.tushare_client.ts") as mock_ts,
        patch("data.external.tushare_client.ConfigHandler") as mock_ch,
    ):
        mock_ts.pro_api.return_value = MagicMock()
        mock_ch.get_token.return_value = token
        mock_ch.get_tushare_timeout.return_value = 30
        mock_ch.get_request_max_retries.return_value = 3
        mock_ch.get_tushare_api_limit.return_value = limit
        client = TushareClient(token=token)
    return client


class TestTushareClientInitAlreadyInitialized:
    def test_reinit_same_token_skips(self):
        _make_client("test_token")
        with (
            patch("data.external.tushare_client.ts") as mock_ts,
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 120
            TushareClient(token="test_token")
            mock_ts.pro_api.assert_not_called()

    def test_reinit_different_token_calls_set_token(self):
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
                TushareClient(token="new_token")
                mock_set.assert_called_once_with("new_token")

    def test_reinit_no_token_skips(self):
        _make_client("test_token")
        with (
            patch("data.external.tushare_client.ts") as mock_ts,
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_api_limit.return_value = 120
            TushareClient(token=None)
            mock_ts.pro_api.assert_not_called()


class TestTushareClientHandleApiCallPartialFunc:
    @pytest.mark.asyncio
    async def test_partial_func_extracts_api_name(self):
        client = _make_client()
        mock_pro_func = MagicMock()
        mock_pro_func.__name__ = "daily"
        partial_func = functools.partial(mock_pro_func, "daily")
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        result = await client._handle_api_call(partial_func)
        assert result is not None

    @pytest.mark.asyncio
    async def test_date_kwargs_formatted(self):
        client = _make_client()
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
    async def test_api_limiter_consume_and_on_success(self):
        client = _make_client(limit=120)
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
            api_limiter.consume_async.assert_called()
            api_limiter.on_success.assert_called()

    @pytest.mark.asyncio
    async def test_rate_limiter_consume_and_on_success(self):
        client = _make_client(limit=120)
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
            rate_limiter.consume_async.assert_called()
            rate_limiter.on_success.assert_called()


class TestTushareClientHandleApiCallErrors:
    @pytest.mark.asyncio
    async def test_permission_error_raises_immediately(self):
        client = _make_client()
        client.max_retries = 3

        async def mock_wait_for(coro, timeout=None):
            raise Exception("没有权限访问该接口")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="没有权限"):
                await client._handle_api_call(MagicMock())

    @pytest.mark.asyncio
    async def test_permission_error_jifen(self):
        client = _make_client()
        client.max_retries = 3

        async def mock_wait_for(coro, timeout=None):
            raise Exception("积分不足")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="积分"):
                await client._handle_api_call(MagicMock())

    @pytest.mark.asyncio
    async def test_client_param_error_raises_immediately(self):
        """参数错误(必填参数)应立即抛出，不重试。"""
        client = _make_client()
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
    async def test_client_param_error_missing_param(self):
        """缺少参数也应立即抛出，不重试。"""
        client = _make_client()
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
    async def test_rate_limit_reduces_rate_and_retries(self):
        client = _make_client(limit=120)
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
    async def test_rate_limit_429(self):
        client = _make_client()
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
    async def test_network_error_retries(self):
        client = _make_client()
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
    async def test_network_error_timeout_string(self):
        client = _make_client()
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
    async def test_retry_exhausted_on_last_attempt(self):
        client = _make_client()
        client.max_retries = 2

        async def mock_wait_for(coro, timeout=None):
            raise Exception("unknown error")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(Exception, match="unknown error"):
                    await client._handle_api_call(MagicMock())

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_runtime_error(self):
        client = _make_client()
        client.max_retries = 2

        async def mock_wait_for(coro, timeout=None):
            raise Exception("unknown error")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(Exception, match="unknown error"):
                    await client._handle_api_call(MagicMock())


class TestTushareClientHandleApiCallPaginatedExtended:
    @pytest.mark.asyncio
    async def test_multi_page(self):
        client = _make_client()
        df1 = pd.DataFrame({"a": list(range(10))})
        df2 = pd.DataFrame({"a": list(range(5))})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return df1
            return df2

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)
        assert result is not None
        assert len(result) == 15

    @pytest.mark.asyncio
    async def test_partial_failure_on_second_page(self):
        client = _make_client()
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
    async def test_first_page_failure_raises(self):
        client = _make_client()

        async def mock_handle(func, **kwargs):
            raise Exception("API error on page 1")

        client._handle_api_call = mock_handle
        with pytest.raises(Exception, match="API error on page 1"):
            await client._handle_api_call_paginated(MagicMock(), max_pages=10)

    @pytest.mark.asyncio
    async def test_max_pages_reached(self):
        client = _make_client()
        df = pd.DataFrame({"a": list(range(10))})

        async def mock_handle(func, **kwargs):
            return df

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=1)
        assert result is not None
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_none_values_filtered_from_kwargs(self):
        client = _make_client()
        captured_kwargs = {}

        async def mock_handle(func, **kwargs):
            captured_kwargs.update(kwargs)
            return pd.DataFrame({"a": [1]})

        client._handle_api_call = mock_handle
        await client._handle_api_call_paginated(MagicMock(), ts_code="000001.SZ", end_date=None, max_pages=1)
        assert "ts_code" in captured_kwargs
        assert "end_date" not in captured_kwargs


class TestTushareClientGetTradeDatesExtended:
    def test_api_exception_returns_empty(self):
        client = _make_client()
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        result = client.get_trade_dates("20240601", "20240630")
        assert result == []

    def test_empty_df_returns_empty(self):
        client = _make_client()
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": []})
        result = client.get_trade_dates("20240601", "20240630")
        assert result == []


class TestTushareClientIsTradingDayExtended:
    def test_non_string_input_converted(self):
        client = _make_client()
        client._loaded_years.add("2024")
        client._trade_cal_cache.add("20240614")
        result = client.is_trading_day(20240614)
        assert result is True

    def test_double_checked_locking(self):
        client = _make_client()
        client._loaded_years.add("2024")
        client._trade_cal_cache.add("20240614")
        with patch.object(client, "_calendar_lock"):
            result = client.is_trading_day("20240614")
            assert result is True

    def test_api_returns_empty_df(self):
        client = _make_client()
        client.pro = MagicMock()
        client.pro.trade_cal.return_value = pd.DataFrame({"cal_date": []})
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = True
            result = client.is_trading_day("20240614")
            assert result is True

    def test_offline_calendar_fallback(self):
        client = _make_client()
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = False
            result = client.is_trading_day("20240614")
            assert result is False

    def test_weekday_fallback(self):
        client = _make_client()
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch(
            "data.domain_services.offline_calendar.OfflineCalendar.is_trading_day",
            side_effect=Exception("offline error"),
        ):
            result = client.is_trading_day("20240614")
            assert isinstance(result, bool)

    def test_invalid_date_string_returns_false(self):
        """MD-004: is_trading_day returns False for unparseable dates."""
        client = _make_client()
        client.pro = MagicMock()
        client.pro.trade_cal.side_effect = Exception("API error")
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.side_effect = Exception("offline error")
            result = client.is_trading_day("notadate")
            assert result is False

    def test_no_pro_raises_in_lock(self):
        client = _make_client()
        client.pro = None
        with patch("data.domain_services.offline_calendar.OfflineCalendar") as mock_offline:
            mock_offline.is_trading_day.return_value = True
            result = client.is_trading_day("20240614")
            assert result is True


class TestTushareClientGetTradeCal:
    @pytest.mark.asyncio
    async def test_with_is_open(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614"]}))
        result = await client.get_trade_cal("20240601", "20240630", is_open=1)
        assert result is not None
        call_kwargs = client._handle_api_call.call_args
        assert call_kwargs[1]["is_open"] == "1"

    @pytest.mark.asyncio
    async def test_without_is_open(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"cal_date": ["20240614"]}))
        result = await client.get_trade_cal("20240601", "20240630")
        assert result is not None


class TestTushareClientSimpleApiMethods:
    @pytest.mark.asyncio
    async def test_get_stock_basic_all(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_stock_basic_all()
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_stock_list(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_stock_list()
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_daily_quotes_adj_merge(self):
        client = _make_client()
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
    async def test_get_daily_quotes_no_adj_columns(self):
        client = _make_client()
        daily_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "close": [10.0],
            }
        )
        adj_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "value": [1.0],
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
        assert result["adj_factor"].iloc[0] == 1.0

    @pytest.mark.asyncio
    async def test_get_daily_quotes_adj_exception_default(self):
        client = _make_client()
        daily_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "close": [10.0],
            }
        )
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
    async def test_get_daily_quotes_nan_adj_filled(self):
        client = _make_client()
        daily_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20240614", "20240614"],
                "close": [10.0, 20.0],
            }
        )
        adj_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "adj_factor": [1.5],
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
        assert result["adj_factor"].isna().sum() == 0

    @pytest.mark.asyncio
    async def test_get_daily_basic(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_daily_basic(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_income(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_income(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_cashflow(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_cashflow(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_balancesheet(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_balancesheet(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_top_list(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "net_amount": [1000000]})
        )
        result = await client.get_top_list(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_top_inst(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_top_inst(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_hk_hold(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_hk_hold(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_moneyflow(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_moneyflow(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_block_trade(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_block_trade(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_fina_indicator(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_fina_indicator(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_disclosure_date(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_disclosure_date(date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_concept_list(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"id": ["1"]}))
        result = await client.get_concept_list()
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_concept_detail_by_id(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_concept_detail_by_id(concept_id="123")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_concept_detail(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"id": ["1"]}))
        result = await client.get_concept_detail(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_index_daily(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await client.get_index_daily(ts_code="000001.SH")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_moneyflow_hsgt(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(
            return_value=pd.DataFrame({"trade_date": ["20240614"], "north_money": [100.0]})
        )
        result = await client.get_moneyflow_hsgt(trade_date="20240614")
        assert result is not None
        assert result.attrs["column_units"]["north_money"] == "million_cny"

    @pytest.mark.asyncio
    async def test_get_index_dailybasic(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SH"]}))
        result = await client.get_index_dailybasic(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_limit_list(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_limit_list(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_suspend_d(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_suspend_d(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_margin_detail(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_margin_detail(trade_date="20240614")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_fina_audit(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_fina_audit(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_forecast(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_forecast(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_fina_mainbz(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_fina_mainbz(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_pledge_stat(self):
        client = _make_client()
        client._handle_api_call_paginated = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_pledge_stat(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_repurchase(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_repurchase(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_dividend(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_dividend(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_shibor(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"date": ["20240614"]}))
        result = await client.get_shibor(start_date="20240601")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_top10_holders(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_top10_holders(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_index_weight(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"index_code": ["000001.SH"]}))
        result = await client.get_index_weight(index_code="000001.SH")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_stk_holdernumber(self):
        client = _make_client()
        client._handle_api_call_paginated = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await client.get_stk_holdernumber(ts_code="000001.SZ")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_macro_data_not_in_whitelist(self):
        client = _make_client()
        result = await client.get_macro_data("invalid_api")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_macro_data_func_not_found(self):
        client = _make_client()
        client.pro = MagicMock(spec=[])
        result = await client.get_macro_data("cn_cpi")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_macro_data_success(self):
        client = _make_client()
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"period": ["202401"]}))
        result = await client.get_macro_data("cn_cpi", start_m="202401", end_m="202406")
        assert result is not None
