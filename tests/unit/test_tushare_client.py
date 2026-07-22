# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
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

    def test_set_token_clears_capability_cache(self, tushare_client_mocks):
        """T4.7: set_token 后 _capability_cache 应被清空。"""
        client, _, _ = tushare_client_mocks
        client.mark_api_available("api1")
        client.mark_api_available("api2")
        client.mark_api_unavailable("api3")
        assert len(client._capability_cache) == 3

        client.set_token("new_token")

        assert client._capability_cache == {}
        assert len(client._capability_cache) == 0


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
        """多页拼接：首页满页 + 次页部分页（末页）+ 第三页空，验证 pd.concat 拼接与空页中断逻辑。

        B9 修复后：分页终止条件改为空页判断（而非 returned_len < full_page_size），
        故需第三页返回空 DataFrame 触发中断。
        """
        client, _, _ = tushare_client_mocks
        df1 = pd.DataFrame({"a": list(range(10))})  # 首页满页（10 行）
        df2 = pd.DataFrame({"a": list(range(10, 15))})  # 次页部分（5 行，末页）
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return df1
            elif call_count[0] == 2:
                return df2
            return pd.DataFrame()  # 第三页空，触发中断

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)
        assert result is not None
        assert len(result) == 15  # 10 + 5 拼接
        assert call_count[0] == 3  # 第三页空页后中断

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

    @pytest.mark.asyncio
    async def test_max_pages_zero_returns_none(self, tushare_client_mocks):
        """T7: max_pages=0 应返回 None，不抛异常，不调用 _handle_api_call。

        while page < max_pages 循环不执行，df_list 为空，返回 None。
        """
        client, _, _ = tushare_client_mocks
        client._handle_api_call = AsyncMock(return_value=pd.DataFrame({"a": [1]}))

        result = await client._handle_api_call_paginated(MagicMock(), max_pages=0)

        assert result is None
        client._handle_api_call.assert_not_called()


class TestPaginatedPermissionErrorPropagation:
    """B10 修复：分页中途 TushareAPIPermissionError 向上传播（不视为普通分页失败吞掉）。

    验证：
    - 第 1 页成功，第 2 页抛 TushareAPIPermissionError → 向上传播（不返回部分结果）
    - 第 1 页抛 TushareAPIPermissionError → 向上传播
    - 对照：普通 Exception 在第 2 页仍返回部分结果（B10 修复不破坏原有行为）
    """

    @pytest.mark.asyncio
    async def test_permission_error_on_second_page_propagates(self, tushare_client_mocks):
        """第 1 页成功，第 2 页抛 TushareAPIPermissionError，应向上传播而非返回部分结果。"""
        client, _, _ = tushare_client_mocks
        df1 = pd.DataFrame({"a": list(range(10))})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return df1
            raise TushareAPIPermissionError("test_api", "permission denied on page 2")

        client._handle_api_call = mock_handle
        with pytest.raises(TushareAPIPermissionError, match="permission denied on page 2"):
            await client._handle_api_call_paginated(MagicMock(), max_pages=10)

    @pytest.mark.asyncio
    async def test_permission_error_on_first_page_propagates(self, tushare_client_mocks):
        """第 1 页抛 TushareAPIPermissionError，应向上传播。"""
        client, _, _ = tushare_client_mocks

        async def mock_handle(func, **kwargs):
            raise TushareAPIPermissionError("test_api", "permission denied on page 1")

        client._handle_api_call = mock_handle
        with pytest.raises(TushareAPIPermissionError, match="permission denied on page 1"):
            await client._handle_api_call_paginated(MagicMock(), max_pages=10)

    @pytest.mark.asyncio
    async def test_non_permission_error_on_second_page_returns_partial(self, tushare_client_mocks):
        """对照测试：第 2 页普通 Exception 仍返回部分结果（B10 修复不破坏原有行为）。"""
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
    async def test_middle_page_non_permission_error_returns_partial(self, tushare_client_mocks, caplog):
        """T6: 第三页（page=2）失败（非权限错误）应返回部分结果并记 warning。

        覆盖分页中间页失败的边界 case（已有测试仅覆盖第 2 页失败）。
        """
        import logging

        client, _, _ = tushare_client_mocks
        df1 = pd.DataFrame({"a": list(range(10))})
        df2 = pd.DataFrame({"a": list(range(10, 20))})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return df1
            if call_count[0] == 2:
                return df2
            # 第三页（page=2）失败
            raise Exception("API error on page 3")

        client._handle_api_call = mock_handle
        with caplog.at_level(logging.WARNING, logger="data.external.tushare_client"):
            result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)

        assert result is not None
        assert len(result) == 20  # df1 + df2 拼接
        assert call_count[0] == 3  # 第三页失败后中断
        # 验证记 warning 日志（page 索引从 0 开始，第三页对应 page=2）
        assert any(
            "Pagination failed on page 2" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
        )

    @pytest.mark.asyncio
    async def test_middle_page_permission_error_propagates(self, tushare_client_mocks):
        """T6: 第五页（page=4）抛 TushareAPIPermissionError 应向上传播（P1 Task 12 修复）。

        覆盖分页中间页权限错误传播的边界 case（已有测试仅覆盖第 2 页权限错误）。
        """
        client, _, _ = tushare_client_mocks
        df = pd.DataFrame({"a": list(range(10))})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 4:
                return df  # 前 4 页成功
            # 第五页（page=4）抛权限错误
            raise TushareAPIPermissionError("test_api", "permission denied on page 5")

        client._handle_api_call = mock_handle
        with pytest.raises(TushareAPIPermissionError, match="permission denied on page 5"):
            await client._handle_api_call_paginated(MagicMock(), max_pages=10)

        # 验证第 5 页被调用
        assert call_count[0] == 5


class TestTushareClientGetTradeDates:
    def test_no_pro_returns_empty(self):
        """B11 修复：pro is None 时 get_trade_dates 降级返回 []（与 is_trading_day 契约一致）。"""
        with (
            patch("data.external.tushare_client.ts"),
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_token.return_value = ""
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_point_tier.return_value = "points_5000"
            client = TushareClient()
            result = client.get_trade_dates("20240101", "20240630")
            assert result == []

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

    @pytest.mark.asyncio
    async def test_get_cn_gdp_wrapper_returns_none_when_pro_is_none(self, tushare_client_mocks):
        """Phase 2D §3.2.6：pro 为 None 时 get_cn_gdp 返回 None。"""
        client, _, _ = tushare_client_mocks
        client.pro = None
        result = await client.get_cn_gdp(quarter="2024Q4")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cn_gdp_wrapper_delegates_to_handle_api_call(self, tushare_client_mocks):
        """Phase 2D §3.2.6：get_cn_gdp 委托 _handle_api_call，传递 quarter 和显式 fields。"""
        client, _, _ = tushare_client_mocks
        expected_df = pd.DataFrame(
            {
                "quarter": ["2024Q4"],
                "gdp": [35000000.0],
                "gdp_yoy": [5.2],
                "pi": [2500000.0],
                "pi_yoy": [3.1],
                "si": [14000000.0],
                "si_yoy": [5.0],
                "ti": [18500000.0],
                "ti_yoy": [5.8],
            }
        )
        client._handle_api_call = AsyncMock(return_value=expected_df)
        result = await client.get_cn_gdp(quarter="2024Q4")

        assert result is not None
        # 验证 _handle_api_call 被调用，传入 pro.cn_gdp + quarter + 显式 fields
        client._handle_api_call.assert_called_once()
        call_args = client._handle_api_call.call_args
        # 第一个位置参数是 pro.cn_gdp callable
        assert callable(call_args.args[0])
        # kwargs 含 quarter 和 fields
        assert call_args.kwargs["quarter"] == "2024Q4"
        assert "gdp,gdp_yoy,pi,pi_yoy,si,si_yoy,ti,ti_yoy" in call_args.kwargs["fields"]

    @pytest.mark.asyncio
    async def test_get_top_inst_wrapper_delegates_to_handle_api_call(self, tushare_client_mocks):
        """Phase 2E §3.2.7：get_top_inst 委托 _handle_api_call，传递 trade_date 和显式 fields。"""
        client, _, _ = tushare_client_mocks
        expected_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "name": ["平安银行"],
                "close": [10.0],
                "pct_change": [1.0],
                "amount": [1000000.0],
                "net_amount": [500000.0],
                "buy_amount": [800000.0],
                "buy_value": [8000000.0],
                "sell_amount": [300000.0],
                "sell_value": [3000000.0],
            }
        )
        client._handle_api_call = AsyncMock(return_value=expected_df)
        result = await client.get_top_inst(trade_date="20240614")

        assert result is not None
        client._handle_api_call.assert_called_once()
        call_args = client._handle_api_call.call_args
        # 第一个位置参数是 pro.top_inst callable
        assert callable(call_args.args[0])
        # kwargs 含 trade_date 和 fields
        assert call_args.kwargs["trade_date"] == "20240614"
        assert "ts_code,trade_date,name,close,pct_change,amount" in call_args.kwargs["fields"]

    @pytest.mark.asyncio
    async def test_get_stk_limit_wrapper_delegates_to_handle_api_call(self, tushare_client_mocks):
        """Phase 2G §3.2：get_stk_limit 委托 _handle_api_call，传递 trade_date 和显式 fields。"""
        client, _, _ = tushare_client_mocks
        expected_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "pre_close": [9.5],
                "up_limit": [10.45],
                "down_limit": [8.55],
                "limit": ["U"],
            }
        )
        client._handle_api_call = AsyncMock(return_value=expected_df)
        result = await client.get_stk_limit(trade_date="20240614")

        assert result is not None
        client._handle_api_call.assert_called_once()
        call_args = client._handle_api_call.call_args
        # 第一个位置参数是 pro.stk_limit callable
        assert callable(call_args.args[0])
        # kwargs 含 trade_date 和 fields
        assert call_args.kwargs["trade_date"] == "20240614"
        assert "ts_code,trade_date,pre_close,up_limit,down_limit,limit" in call_args.kwargs["fields"]

    @pytest.mark.asyncio
    async def test_get_index_classify_wrapper_delegates_to_handle_api_call(self, tushare_client_mocks):
        """Phase 3F-1 §4.3.2：get_index_classify 委托 _handle_api_call，传递 level/src 和显式 fields。"""
        client, _, _ = tushare_client_mocks
        expected_df = pd.DataFrame(
            {
                "index_code": ["801010.SI"],
                "index_name": ["农林牧渔"],
                "sw_level": ["L1"],
                "industry_code": ["110000"],
                "industry_name": ["农林牧渔"],
                "parent_code": [""],
                "is_sw": ["1"],
            }
        )
        client._handle_api_call = AsyncMock(return_value=expected_df)
        result = await client.get_index_classify(level="L1", src="SW2021")

        assert result is not None
        client._handle_api_call.assert_called_once()
        call_args = client._handle_api_call.call_args
        # 第一个位置参数是 pro.index_classify callable
        assert callable(call_args.args[0])
        # kwargs 含 level/src 和 fields
        assert call_args.kwargs["level"] == "L1"
        assert call_args.kwargs["src"] == "SW2021"
        assert "index_code,index_name,level,industry_code,industry_name,parent_code,is_sw" in call_args.kwargs["fields"]

    @pytest.mark.asyncio
    async def test_get_index_member_all_wrapper_delegates_to_handle_api_call(self, tushare_client_mocks):
        """Phase 3F-1 §4.3.2：get_index_member_all 委托 _handle_api_call，传递 index_code 和显式 fields。"""
        client, _, _ = tushare_client_mocks
        expected_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "index_code": ["801010.SI"],
                "index_name": ["农林牧渔"],
                "sw_l1_code": ["110000"],
                "sw_l1_name": ["农林牧渔"],
                "sw_l2_code": ["110100"],
                "sw_l2_name": ["种植业"],
                "sw_l3_code": ["110101"],
                "sw_l3_name": ["玉米"],
            }
        )
        client._handle_api_call = AsyncMock(return_value=expected_df)
        result = await client.get_index_member_all(index_code="801010.SI")

        assert result is not None
        client._handle_api_call.assert_called_once()
        call_args = client._handle_api_call.call_args
        # 第一个位置参数是 pro.index_member_all callable
        assert callable(call_args.args[0])
        # kwargs 含 index_code 和 fields（含 L1/L2/L3 全字段）
        assert call_args.kwargs["index_code"] == "801010.SI"
        fields_str = call_args.kwargs["fields"]
        assert "ts_code" in fields_str
        assert "sw_l1_code" in fields_str
        assert "sw_l2_code" in fields_str
        assert "sw_l3_code" in fields_str

    @pytest.mark.asyncio
    async def test_get_index_member_all_wrapper_allows_none_index_code(self, tushare_client_mocks):
        """Phase 3F-1 §4.3.2：get_index_member_all 接受 index_code=None（拉取全市场）。"""
        client, _, _ = tushare_client_mocks
        expected_df = pd.DataFrame({"ts_code": ["000001.SZ"], "index_code": ["801010.SI"]})
        client._handle_api_call = AsyncMock(return_value=expected_df)

        result = await client.get_index_member_all(index_code=None)

        assert result is not None
        client._handle_api_call.assert_called_once()
        call_args = client._handle_api_call.call_args
        assert call_args.kwargs["index_code"] is None


class TestTushareClientBuildRateLimiters:
    def test_with_limit(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client._rate_limiter, client._api_limiters, client._probe_rate_limiter = client._build_rate_limiters()
        assert client._rate_limiter is not None
        assert "top10_holders" in client._api_limiters
        assert client._probe_rate_limiter is not None

    def test_without_limit(self, tushare_client_mocks):
        """未知档位应禁用限速器（_POINT_TIER_PRESETS.get 返回 0），但 probe 专用桶仍创建。"""
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "unknown_tier"
        client._rate_limiter, client._api_limiters, client._probe_rate_limiter = client._build_rate_limiters()
        assert client._rate_limiter is None
        assert client._api_limiters == {}
        # probe 专用桶不依赖档位，仍创建
        assert client._probe_rate_limiter is not None

    def test_resolve_rate_limit_uses_tier_preset(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        limit = client._resolve_rate_limit()
        assert limit == 500

    def test_build_rate_limiters_honors_tier(self, tushare_client_mocks):
        client, mock_ts, mock_ch = tushare_client_mocks
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client._rate_limiter, client._api_limiters, client._probe_rate_limiter = client._build_rate_limiters()
        assert client._rate_limiter is not None
        assert pytest.approx(client._rate_limiter.rate * 60, abs=1) == 500

    def test_probe_rate_limiter_is_50_rpm(self, tushare_client_mocks):
        """Phase 2B: probe 专用桶应为 50/min（_PROBE_RATE_LIMIT_RPM）。"""
        client, _, _ = tushare_client_mocks
        client._rate_limiter, client._api_limiters, client._probe_rate_limiter = client._build_rate_limiters()
        assert client._probe_rate_limiter is not None
        assert pytest.approx(client._probe_rate_limiter.rate * 60, abs=1) == 50

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
        # 两段消费契约：全局桶与 per-API 桶各消费 1 个 token
        client._rate_limiter.consume_async.assert_awaited_once_with(1)
        client._api_limiters["top10_holders"].consume_async.assert_awaited_once_with(1)

    def test_fast_api_overrides_removed(self, tushare_client_mocks):
        """_FAST_API_OVERRIDES ClassVar 应已删除（Phase 2A 移除 fast API 循环）。"""
        assert not hasattr(TushareClient, "_FAST_API_OVERRIDES")

    def test_slow_api_limiter_stacks_on_global(self, tushare_client_mocks):
        """slow API limiter 在全局桶基础上额外收紧（factor < 1.0）。"""
        client, _, _ = tushare_client_mocks
        client._rate_limiter, client._api_limiters, _ = client._build_rate_limiters()
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
    """Coverage for bound method api_name extraction and rate limiter consume/on_success.

    B7 修复后 partial 分支已删除，func 统一通过 __name__ 提取 api_name。
    """

    @pytest.mark.asyncio
    async def test_bound_method_extracts_api_name(self, tushare_client_mocks):
        """bound method 的 __name__ 应被正确提取为 api_name（B7：partial 分支已删除）。"""
        client, _, _ = tushare_client_mocks
        mock_func = MagicMock()
        mock_func.__name__ = "daily"
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=pd.DataFrame({"a": [1]}))):
            result = await client._handle_api_call(mock_func)
        assert result is not None
        # 验证 api_name="daily" 被正确标记为 available
        assert client.is_api_available("daily") is True

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
            # B7 修复后 partial 分支已删除，用带 __name__ 的 MagicMock 模拟 bound method
            mock_func = MagicMock()
            mock_func.__name__ = "top10_holders"
            result = await client._handle_api_call(mock_func)
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

    @pytest.mark.asyncio
    async def test_partial_func_extracts_api_name(self, tushare_client_mocks):
        """T1/B7：functools.partial 包装的 func 走 str(func) fallback 路径提取 api_name。

        B7 修复删除了 partial 分支（不可达且语义错误），统一通过
        getattr(func, "__name__", str(func)) 提取。partial 对象无 __name__ 属性，
        走 str(func) fallback，capability cache 以 str(partial) 为 key。
        """
        import functools

        client, _, _ = tushare_client_mocks

        def daily(trade_date: str):
            return pd.DataFrame({"a": [1]})

        partial_func = functools.partial(daily, trade_date="20240101")
        expected_api_name = str(partial_func)

        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=pd.DataFrame({"a": [1]}))):
            result = await client._handle_api_call(partial_func)
        assert result is not None
        # 验证 api_name 走 str(func) fallback 路径，capability cache 以 str(partial) 为 key
        assert client.is_api_available(expected_api_name) is True


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
    """Phase 2A.1/2B：probe 按档位预筛 / 并行化 / 三态分类 / 双层过滤 / 持久化测试。"""

    # Phase 2B: probe_configs 完整 29 项 API 名称
    _ALL_PROBE_APIS = [
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
        "share_float",
        "stk_holdertrade",
        "index_classify",
        "index_member_all",
        "top_inst",
        "stk_factor_pro",
        "top10_floatholders",
        "stk_limit",
        "express",
        "pledge_detail",
        "shibor_lpr",
        "stock_company",
        "stk_managers",
        "stk_surv",
        "cn_gdp",
        "cyq_perf",
        "forecast_eps",
    ]

    def _make_probed_client(self, tier="points_5000"):
        """创建 client 并 mock pro API 方法（设置 __name__ 供 _handle_api_call 推断 api_name）。"""
        client = _make_client(tier=tier)
        # _make_client 的 with 块退出后 ConfigHandler patch 失效，需持续 mock _get_tushare_point_tier
        client._get_tushare_point_tier = lambda: tier
        # 为 probe_configs 中所有 API 在 pro 上挂 MagicMock（带 __name__）
        for api_name in self._ALL_PROBE_APIS:
            mock_func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
            mock_func.__name__ = api_name
            setattr(client.pro, api_name, mock_func)
        return client

    @pytest.mark.asyncio
    async def test_probe_filters_by_tier(self):
        """probe_api_capabilities 应按 is_api_covered_by_tier 预筛 probe_configs。"""
        client = self._make_probed_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        # Phase 2B: probe 走 _handle_probe_call（非 _handle_api_call）
        called_apis: list[str] = []

        async def fake_probe_call(api_name, func, **params):
            called_apis.append(api_name)
            return None

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        # points_120 覆盖 daily + shibor_lpr（probe_configs 中仅这两项在 points_120 档位内）
        assert "daily" in called_apis
        assert "shibor_lpr" in called_apis
        # points_2000+ 档位的 API 不应被 probe
        assert "moneyflow" not in called_apis
        assert "hk_hold" not in called_apis
        assert "top_list" not in called_apis
        assert "fina_indicator" not in called_apis
        assert "top10_holders" not in called_apis
        # results 中包含 daily + shibor_lpr
        assert "daily" in results
        assert "shibor_lpr" in results
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_probe_independent_purchase_log(self):
        """Phase 2B: probe_configs 扩展后含 cyq_perf/forecast_eps（points_10000 覆盖时被 probe）。"""
        client = self._make_probed_client(tier="points_10000")
        # points_10000 档位覆盖 cyq_perf / forecast_eps（即使需独立购买）
        assert client.is_api_covered_by_tier("cyq_perf", "points_10000") is True
        assert client.is_api_covered_by_tier("forecast_eps", "points_10000") is True
        # is_independent_purchase 标记独立存在（不影响 is_api_covered_by_tier）
        assert client.is_independent_purchase("cyq_perf") is True
        assert client.is_independent_purchase("forecast_eps") is True
        # Phase 2B: probe_configs 已含 cyq_perf/forecast_eps，points_10000 覆盖时被 probe
        client.persist_capabilities_to_app_state = AsyncMock()
        client._handle_probe_call = AsyncMock(return_value=None)
        results = await client.probe_api_capabilities()
        assert "cyq_perf" in results
        assert "forecast_eps" in results

    @pytest.mark.asyncio
    async def test_probe_skips_apis_not_in_tier(self):
        """低档位下 probe 应跳过高档位 API。"""
        client = self._make_probed_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        called_apis: list[str] = []

        async def fake_probe_call(api_name, func, **params):
            called_apis.append(api_name)
            return None

        client._handle_probe_call = fake_probe_call
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
        """Phase 2B: _probe_in_progress 互斥——已在 probe 中时返回当前缓存快照，不重复 probe。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        # 预设互斥标志 + 缓存值
        client._probe_in_progress = True
        client.mark_api_available("daily")
        probe_call_count = [0]

        async def fake_probe_call(api_name, func, **params):
            probe_call_count[0] += 1
            return None

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        # 互斥：不应调用 _handle_probe_call
        assert probe_call_count[0] == 0
        # 返回当前缓存快照
        assert results.get("daily") is True

    @pytest.mark.asyncio
    async def test_probe_service_unavailable_detection(self):
        """Phase 2B: None 比例 >80% 时保留旧缓存（服务不可用降级）。"""
        client = self._make_probed_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        # 预设旧缓存（daily=True），验证降级时保留
        client.mark_api_available("daily")

        # points_120 覆盖 daily + shibor_lpr，全部抛网络错误 → None 比例 100% > 80%
        async def fake_probe_call(api_name, func, **params):
            raise requests.exceptions.ConnectionError("connection refused")

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        # 服务不可用 → 保留旧缓存，返回 get_capability_cache()
        assert results.get("daily") is True
        assert client.is_api_available("daily") is True

    @pytest.mark.asyncio
    async def test_probe_token_invalid_detection(self):
        """Phase 2B: TushareAPIPermissionError（含 token_invalid）→ False，写入 _capability_cache。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()

        async def fake_probe_call(api_name, func, **params):
            if api_name == "daily":
                raise TushareAPIPermissionError(api_name, "您的token不对")
            return None

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        # daily 应为 False（权限错误）
        assert results["daily"] is False
        # capability_cache 中 daily 也应为 False
        assert client.is_api_available("daily") is False

    @pytest.mark.asyncio
    async def test_probe_false_over_90_percent_logs_error(self, caplog):
        """T4.6: probe False 比例 >90% 时记 ERROR 告警日志（Token 可能无效）。"""
        import logging

        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()

        async def fake_probe_call(api_name, func, **params):
            raise TushareAPIPermissionError(api_name, "权限不足")

        client._handle_probe_call = fake_probe_call
        with caplog.at_level(logging.ERROR, logger="data.external.tushare_client"):
            results = await client.probe_api_capabilities()

        # 验证所有 results 为 False（points_5000 档位 probe 27 项，全部权限错误）
        assert len(results) == 27
        assert all(v is False for v in results.values())
        # 验证 ERROR 日志被调用，包含 False / permission denied / Token 关键词
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "False" in r.message and "permission denied" in r.message and "Token" in r.message for r in error_records
        )

    @pytest.mark.asyncio
    async def test_probe_progress_callback(self):
        """progress_callback 应被调用 N 次（N = filtered_configs 长度）。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        client._handle_probe_call = AsyncMock(return_value=None)
        progress_calls: list[tuple[int, int]] = []

        def progress_cb(completed: int, total: int) -> None:
            progress_calls.append((completed, total))

        await client.probe_api_capabilities(progress_callback=progress_cb)
        # Phase 2B: points_5000 覆盖 probe_configs 27 项（29 项减去 cyq_perf/forecast_eps 需 points_10000）
        assert len(progress_calls) == 27
        # 第一次调用 completed=1, total=27
        assert progress_calls[0] == (1, 27)
        # 最后一次调用 completed=27, total=27
        assert progress_calls[-1] == (27, 27)

    @pytest.mark.asyncio
    async def test_probe_one_classifies_error(self):
        """Phase 2B: probe 三态分类——True/False/None。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()

        async def fake_probe_call(api_name, func, **params):
            if api_name == "moneyflow_hsgt":
                raise TushareAPIPermissionError(api_name, "权限不足")  # 权限错误 → False
            if api_name == "moneyflow":
                raise Exception("network error")  # 其他错误 → None
            return None  # 成功 → True

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        assert results["daily"] is True
        assert results["moneyflow_hsgt"] is False
        assert results["moneyflow"] is None

    @pytest.mark.asyncio
    async def test_probe_one_distinguishes_429(self):
        """Phase 2B: 429 限流 → None（非 False），不触发 reduce_rate。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()

        async def fake_probe_call(api_name, func, **params):
            if api_name == "daily":
                raise Exception("429 too many requests")
            return None

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        # 429 → None（非 False）
        assert results["daily"] is None
        # None 不写入 cache
        assert client.is_api_available("daily") is None

    @pytest.mark.asyncio
    async def test_probe_configs_has_29_entries(self):
        """Phase 2B: probe_configs 扩展到 29 项（points_10000 覆盖全部）。"""
        client = self._make_probed_client(tier="points_10000")
        client.persist_capabilities_to_app_state = AsyncMock()
        client._handle_probe_call = AsyncMock(return_value=None)
        results = await client.probe_api_capabilities()
        assert len(results) == 29

    @pytest.mark.asyncio
    async def test_handle_probe_call_skips_reduce_rate(self):
        """Phase 2B: _handle_probe_call 两段消费但不调 reduce_rate/on_success（probe 一次性探测）。"""
        from utils.rate_limiter import TokenBucket

        client = self._make_probed_client(tier="points_5000")
        # 用 spec=TokenBucket 的 MagicMock 替换限速器
        client._rate_limiter = MagicMock(spec=TokenBucket)
        client._rate_limiter.consume_async = AsyncMock()
        client._probe_rate_limiter = MagicMock(spec=TokenBucket)
        client._probe_rate_limiter.consume_async = AsyncMock()

        func = MagicMock(return_value=pd.DataFrame({"a": [1]}))
        func.__name__ = "daily"
        loop = asyncio.get_running_loop()
        with patch.object(loop, "run_in_executor", new=AsyncMock(return_value=pd.DataFrame({"a": [1]}))):
            await client._handle_probe_call("daily", func, trade_date="20240101")

        # 两段消费
        client._rate_limiter.consume_async.assert_awaited_once_with(1)
        client._probe_rate_limiter.consume_async.assert_awaited_once_with(1)
        # 不调 reduce_rate / on_success（probe 一次性探测，不永久降速）
        client._rate_limiter.reduce_rate.assert_not_called()
        client._rate_limiter.on_success.assert_not_called()

    @pytest.mark.asyncio
    async def test_probe_parallel_with_semaphore(self):
        """Phase 2B: probe 并行执行（semaphore=4 限制最大并发）。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()

        current_concurrent = [0]
        max_concurrent = [0]

        async def fake_probe_call(api_name, func, **params):
            current_concurrent[0] += 1
            max_concurrent[0] = max(max_concurrent[0], current_concurrent[0])
            await asyncio.sleep(0.01)
            current_concurrent[0] -= 1
            return None

        client._handle_probe_call = fake_probe_call
        await client.probe_api_capabilities()
        # semaphore=4，最大并发应 ≤ 4
        assert max_concurrent[0] <= 4
        # points_5000 有 27 个 API，并行执行应 > 1
        assert max_concurrent[0] > 1

    @pytest.mark.asyncio
    async def test_probe_handles_none_state(self):
        """Phase 2B: probe 三态 None 不写入 _capability_cache（保持原值或不存在）。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        # 预设 daily=True 在 cache 中
        client.mark_api_available("daily")

        async def fake_probe_call(api_name, func, **params):
            if api_name == "daily":
                raise requests.exceptions.ConnectionError("network error")
            return None

        client._handle_probe_call = fake_probe_call
        results = await client.probe_api_capabilities()
        # daily 在 results 中为 None
        assert results["daily"] is None
        # 但 _capability_cache 中 daily 仍为 True（None 不写入）
        assert client.is_api_available("daily") is True

    @pytest.mark.asyncio
    async def test_probe_propagates_cancelled_error(self):
        """Phase 2B: probe 取消时 CancelledError 必须 raise（R2 红线）+ 回滚 cache + 释放互斥。"""
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        client.mark_api_available("daily")  # 预设值，验证回滚

        async def fake_probe_call(api_name, func, **params):
            if api_name == "daily":
                raise asyncio.CancelledError()
            return None

        client._handle_probe_call = fake_probe_call
        with pytest.raises(asyncio.CancelledError):
            await client.probe_api_capabilities()

        # CancelledError 后互斥标志应释放（finally 块）
        assert client._probe_in_progress is False
        # cache 应回滚到入口快照（daily=True 保留）
        assert client.is_api_available("daily") is True

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

    @pytest.mark.asyncio
    async def test_probe_in_progress_concurrent_protection(self):
        """T9: 多协程同时调用 probe_api_capabilities 时的并发保护。

        验证：
        - 第一个协程触发 probe，设置 _probe_in_progress=True
        - 第二个协程在 probe 进行中进入时，立即返回当前缓存快照，不调用 _handle_probe_call
        - probe 完成后 _probe_in_progress 恢复 False
        """
        client = self._make_probed_client(tier="points_5000")
        client.persist_capabilities_to_app_state = AsyncMock()
        # 预设缓存，验证第二个协程返回快照
        client.mark_api_available("daily")

        first_probe_started = asyncio.Event()
        release_first_probe = asyncio.Event()

        async def fake_probe_call(api_name, func, **params):
            # 任何 API 调用都通知第二个协程可以进入，并阻塞等待 release
            first_probe_started.set()
            await release_first_probe.wait()
            return None

        client._handle_probe_call = fake_probe_call

        async def second_probe():
            # 等第一个 probe 进入 _handle_probe_call 后再启动
            await first_probe_started.wait()
            # 此时第一个 probe 正在进行（_probe_in_progress=True）
            assert client._probe_in_progress is True
            # 第二个 probe 应立即返回缓存快照（互斥保护）
            return await client.probe_api_capabilities()

        first_task = asyncio.create_task(client.probe_api_capabilities())
        second_task = asyncio.create_task(second_probe())

        # 第二个 probe 应先于第一个完成（互斥返回缓存）
        second_result = await asyncio.wait_for(second_task, timeout=2.0)
        # 验证第二个 probe 返回缓存快照（daily=True）
        assert second_result.get("daily") is True
        # 验证第一个 probe 仍在进行中（第二个 probe 完成时第一个还未释放）
        assert client._probe_in_progress is True

        # 释放第一个 probe，让它完成
        release_first_probe.set()
        first_result = await asyncio.wait_for(first_task, timeout=2.0)

        # 验证 probe 完成后 _probe_in_progress 已恢复 False
        assert client._probe_in_progress is False
        # 第一个 probe 完成后 daily 仍为 True
        assert first_result.get("daily") is True


class TestRateLimiterConcurrency:
    """T8: 并发 _handle_api_call 验证 rate_limiter 串行化与 B1+B17 局部变量捕获修复。"""

    @pytest.mark.asyncio
    async def test_concurrent_handle_api_call_serializes_consume(self, tushare_client_mocks):
        """通过 asyncio.gather 并发调用 _handle_api_call，验证：
        1. consume_async 被串行调用（用 asyncio.Lock 强制串行化以追踪调用顺序，无交错）
        2. B1+B17 修复：consume/on_success 全部作用于同一捕获的 limiter 实例，
           即使 self._rate_limiter 在网络 await 期间被替换为新实例
        """
        client, _, _ = tushare_client_mocks

        # 准备 original_limiter：consume_async 使用 asyncio.Lock 强制串行化以追踪调用顺序
        serialize_lock = asyncio.Lock()
        call_log: list[str] = []

        original_limiter = MagicMock()
        original_limiter.on_success = MagicMock()
        original_limiter.current_rate_per_min = 500.0

        async def mock_consume_async(*_args, **_kwargs):
            async with serialize_lock:
                call_log.append("start")
                await asyncio.sleep(0)  # yield to event loop，允许其他协程排队
                call_log.append("end")

        original_limiter.consume_async = mock_consume_async

        client._rate_limiter = original_limiter
        client._api_limiters = {}  # 跳过 per-API limiter，避免干扰

        # 准备 new_limiter：模拟 set_token/reload_rate_limiters 在网络 await 期间替换 self._rate_limiter
        new_limiter = MagicMock()
        new_limiter.on_success = MagicMock()

        fixed_df = pd.DataFrame({"a": [1]})

        def replace_limiter_side_effect(*_args, **_kwargs):
            # 模拟网络调用期间 self._rate_limiter 被替换
            client._rate_limiter = new_limiter
            return fixed_df

        # 准备 mock func
        mock_func = MagicMock()
        mock_func.__name__ = "test_api"

        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "run_in_executor",
            new=AsyncMock(side_effect=replace_limiter_side_effect),
        ):
            # 并发调用 3 次 _handle_api_call
            results = await asyncio.gather(
                client._handle_api_call(mock_func),
                client._handle_api_call(mock_func),
                client._handle_api_call(mock_func),
            )

        # 验证所有调用成功返回
        assert len(results) == 3
        for r in results:
            assert r is not None

        # 验证 consume_async 被串行调用：start/end 配对，无交错
        # 期望：["start", "end", "start", "end", "start", "end"]
        assert len(call_log) == 6
        for i in range(0, 6, 2):
            assert call_log[i] == "start"
            assert call_log[i + 1] == "end"

        # 验证 B1+B17 修复：original_limiter 被使用，new_limiter 未被使用
        # on_success 在每次成功后调用，共 3 次
        assert original_limiter.on_success.call_count == 3
        new_limiter.on_success.assert_not_called()


class TestIsRateLimitKeywordPrecision:
    """B8 修复：is_rate_limit 关键字 "抱歉" 过于宽泛，改为 "抱歉，每分钟"/"抱歉，频次"。

    验证：
    - "抱歉，每分钟" 匹配 rate_limit 路径（reduce_rate 被调用）
    - "抱歉"（无后缀）不匹配 rate_limit 路径（走 retry_exhausted）
    - "检测到" 不再匹配 rate_limit 路径
    """

    @pytest.mark.asyncio
    async def test_apology_with_minute_suffix_triggers_rate_limit(self, tushare_client_mocks):
        """抱歉，每分钟... 触发 rate_limit 路径（reduce_rate 被调用）。"""
        client, _, _ = tushare_client_mocks
        client.max_retries = 2
        client._rate_limiter = MagicMock()
        client._rate_limiter.consume_async = AsyncMock()
        client._rate_limiter.reduce_rate = MagicMock()
        client._rate_limiter.on_success = MagicMock()
        client._rate_limiter.current_rate_per_min = 100.0
        client._api_limiters = {}

        call_count = [0]

        async def mock_wait_for(coro, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("抱歉，每分钟最多访问200次")
            return pd.DataFrame({"a": [1]})

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with patch("data.external.tushare_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._handle_api_call(MagicMock())
                assert result is not None
        # reduce_rate 被调用（rate_limit 路径）
        client._rate_limiter.reduce_rate.assert_called_once()

    @pytest.mark.asyncio
    async def test_bare_apology_does_not_trigger_rate_limit(self, tushare_client_mocks):
        """纯 "抱歉" 无后缀不触发 rate_limit 路径（走 retry_exhausted）。"""
        client, _, _ = tushare_client_mocks
        client.max_retries = 1
        client._rate_limiter = MagicMock()
        client._rate_limiter.consume_async = AsyncMock()
        client._rate_limiter.reduce_rate = MagicMock()
        client._api_limiters = {}

        async def mock_wait_for(coro, timeout=None):
            raise Exception("抱歉，系统繁忙")  # "抱歉" 但非限流

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="抱歉"):
                await client._handle_api_call(MagicMock())
        # reduce_rate 不应被调用（非 rate_limit 路径）
        client._rate_limiter.reduce_rate.assert_not_called()

    @pytest.mark.asyncio
    async def test_detected_keyword_no_longer_triggers_rate_limit(self, tushare_client_mocks):
        """B8 修复："检测到" 关键字已移除，不再触发 rate_limit 路径。"""
        client, _, _ = tushare_client_mocks
        client.max_retries = 1
        client._rate_limiter = MagicMock()
        client._rate_limiter.consume_async = AsyncMock()
        client._rate_limiter.reduce_rate = MagicMock()
        client._api_limiters = {}

        async def mock_wait_for(coro, timeout=None):
            raise Exception("检测到异常访问")

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for):
            with pytest.raises(Exception, match="检测到"):
                await client._handle_api_call(MagicMock())
        # reduce_rate 不应被调用（"检测到" 不再匹配 rate_limit）
        client._rate_limiter.reduce_rate.assert_not_called()


class TestPaginatedTruncationFlag:
    """B12 修复：分页达到 max_pages 时在返回的 DataFrame 上设置 df.attrs["truncated"] = True。"""

    @pytest.mark.asyncio
    async def test_truncated_flag_set_when_max_pages_reached(self, tushare_client_mocks):
        """达到 max_pages 时返回的 DataFrame 应设置 attrs["truncated"] = True。"""
        client, _, _ = tushare_client_mocks
        df = pd.DataFrame({"a": list(range(10))})

        async def mock_handle(func, **kwargs):
            return df

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=1)
        assert result is not None
        assert result.attrs.get("truncated") is True

    @pytest.mark.asyncio
    async def test_truncated_flag_not_set_when_normal_completion(self, tushare_client_mocks):
        """正常完成（空页中断）时返回的 DataFrame 不应设置 attrs["truncated"]。"""
        client, _, _ = tushare_client_mocks
        df1 = pd.DataFrame({"a": list(range(10))})
        call_count = [0]

        async def mock_handle(func, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return df1
            return pd.DataFrame()  # 空页中断

        client._handle_api_call = mock_handle
        result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)
        assert result is not None
        assert result.attrs.get("truncated") is not True


class TestProbeClientParamError:
    """B13 修复：_handle_probe_call 检测 client_param_error 分类为 False。

    验证：
    - "必填参数" 错误 → _handle_probe_call 抛 TushareAPIPermissionError
    - _probe_one 分类为 False
    - 记 ERROR 日志
    """

    @pytest.mark.asyncio
    async def test_probe_call_raises_on_client_param_error(self, tushare_client_mocks):
        """_handle_probe_call 遇 client_param_error 抛 TushareAPIPermissionError。"""
        client, _, _ = tushare_client_mocks
        func = MagicMock()
        func.__name__ = "test_api"
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "run_in_executor",
            new=AsyncMock(side_effect=Exception("必填参数 ts_code 未提供")),
        ):
            with pytest.raises(TushareAPIPermissionError, match="client_param_error"):
                await client._handle_probe_call("test_api", func, trade_date="20240101")

    @pytest.mark.asyncio
    async def test_probe_one_classifies_client_param_error_as_false(self, tushare_client_mocks):
        """_probe_one 将 client_param_error 分类为 False。"""
        client, _, _ = tushare_client_mocks
        func = MagicMock()
        func.__name__ = "test_api"
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "run_in_executor",
            new=AsyncMock(side_effect=Exception("缺少参数 trade_date")),
        ):
            semaphore = asyncio.Semaphore(1)
            result = await client._probe_one(semaphore, "test_api", {})
            assert result[0] == "test_api"
            assert result[1] is False

    @pytest.mark.asyncio
    async def test_probe_call_logs_error_on_client_param_error(self, tushare_client_mocks, caplog):
        """_handle_probe_call 遇 client_param_error 记 ERROR 日志。"""
        import logging

        client, _, _ = tushare_client_mocks
        func = MagicMock()
        func.__name__ = "test_api"
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "run_in_executor",
            new=AsyncMock(side_effect=Exception("invalid parameter: ts_code")),
        ):
            with caplog.at_level(logging.ERROR, logger="data.external.tushare_client"):
                with pytest.raises(TushareAPIPermissionError):
                    await client._handle_probe_call("test_api", func, trade_date="20240101")
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("client param error" in r.message for r in error_records)
