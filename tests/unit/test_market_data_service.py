import asyncio
import contextlib

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, AsyncMock

from data.cache.cache_manager import CacheManager
from data.domain_services.market_data_service import MarketDataService

pytestmark = pytest.mark.unit


class TestMarketDataServiceInit:
    @patch("data.domain_services.market_data_service.CacheManager")
    def test_init(self, mock_cm):
        mock_cm.return_value = MagicMock(spec=CacheManager)
        svc = MarketDataService()
        assert isinstance(svc.cache, CacheManager)
        assert isinstance(svc._listeners, set)


class TestMarketDataServiceFetchMarketData:
    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.CacheManager")
    async def test_fetch_market_data(self, mock_cm):
        mock_cache = MagicMock()
        mock_cm.return_value = mock_cache
        svc = MarketDataService()
        expected = {"key": "value"}
        svc._fetch_market_data = AsyncMock(return_value=expected)
        result = await svc._fetch_market_data()
        assert result == expected


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
        assert cb not in svc._listeners


class TestMarketDataServiceStop:
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_resets_running(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc.stop()
        assert svc._running is False


class TestMarketDataServiceStopAsyncR2Propagation:
    """R2: CancelledError 传播规范验证。

    场景A：stop_async 正常调用，主动 cancel _task，task 响应 cancel 完成
            → stop_async 正常返回，不抛 CancelledError（cancelling()==0）
    场景B：stop_async 本身被外部 cancel（应用关闭/超时）
            → CancelledError 必须传播给调用者（cancelling()>0）
    """

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_async_normal_cancel_does_not_raise(self, mock_tc, mock_cache, mock_api):
        """场景A：主动取消 _task 后 stop_async 应正常完成（cancelling()==0）。"""
        svc = MarketDataService()
        svc._running = True
        # _task 响应 cancel 后完成
        svc._task = asyncio.create_task(asyncio.sleep(100))
        await svc.stop_async(timeout=2.0)
        assert svc._task is None
        assert svc._running is False

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_async_propagates_external_cancel(self, mock_tc, mock_cache, mock_api):
        """场景B：stop_async 被外部取消时，CancelledError 必须传播（R2）。

        模拟方式：
        1. _task 使用首次捕获 CancelledError 不传播的协程，使 task 不立即完成
        2. stop_async 主动 cancel _task 后，wait_for 阻塞（_task 不完成）
        3. 外部 cancel stop_task，wait_for 抛 CancelledError
        4. 此时 stop_async 的 cancelling() > 0，必须 raise 传播
        """
        svc = MarketDataService()
        svc._running = True

        async def _uncancellable_coro():
            # 首次 CancelledError 被捕获不传播，第二次 await 可被 cancel
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(100)

        real_task = asyncio.create_task(_uncancellable_coro())
        svc._task = real_task

        stop_task = asyncio.create_task(svc.stop_async(timeout=10.0))
        await asyncio.sleep(0.1)  # 让 stop_async 进入 wait_for

        stop_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await stop_task

        # 清理 real_task
        real_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await real_task


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

    def test_inf(self):
        assert MarketDataService._safe_float(float("inf")) == 0.0

    def test_neg_inf(self):
        assert MarketDataService._safe_float(float("-inf")) == 0.0


class TestMarketDataServiceGetIndicesBatch:
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

    @pytest.mark.asyncio
    async def test_cache_miss_api_called_with_ts_code_per_index(self):
        """缓存未命中时，API 必须按每个 ts_code 分别调用，不能只传 trade_date。"""
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=None)
        svc.api = MagicMock()

        async def fake_get_index_daily(**kwargs):
            ts_code = kwargs.get("ts_code")
            if ts_code == "000001.SH":
                return pd.DataFrame({"ts_code": ["000001.SH"], "pct_chg": [1.0], "close": [3100.0]})
            if ts_code == "399001.SZ":
                return pd.DataFrame({"ts_code": ["399001.SZ"], "pct_chg": [-0.5], "close": [9500.0]})
            return None

        svc.api.get_index_daily = AsyncMock(side_effect=fake_get_index_daily)
        codes = ["000001.SH", "399001.SZ", "399006.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")

        assert svc.api.get_index_daily.call_count == 3
        call_kwargs_list = [call.kwargs for call in svc.api.get_index_daily.call_args_list]
        for call_kwargs in call_kwargs_list:
            assert "ts_code" in call_kwargs, f"API 调用缺少 ts_code 参数: {call_kwargs}"
            assert call_kwargs["ts_code"] is not None

        assert len(result) == 3
        assert result[0]["color"] == "red"
        assert result[1]["color"] == "green"
        assert result[2]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_cache_miss_partial_api_failure_fills_grey(self):
        """部分指数 API 失败时，成功的正常渲染，失败的灰色占位。"""
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=None)
        svc.api = MagicMock()

        async def fake_get_index_daily(**kwargs):
            ts_code = kwargs.get("ts_code")
            if ts_code == "000001.SH":
                return pd.DataFrame({"ts_code": ["000001.SH"], "pct_chg": [1.0], "close": [3100.0]})
            raise Exception("API error")

        svc.api.get_index_daily = AsyncMock(side_effect=fake_get_index_daily)
        codes = ["000001.SH", "399001.SZ", "399006.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")

        assert len(result) == 3
        assert result[0]["color"] == "red"
        assert result[1]["color"] == "grey"
        assert result[2]["color"] == "grey"

    @pytest.mark.asyncio
    async def test_cache_miss_single_index_passes_ts_code(self):
        """单指数缓存未命中时，API 调用必须包含 ts_code。"""
        svc = self._make_svc()
        svc.cache.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())
        svc.api = MagicMock()
        svc.api.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["399006.SZ"], "pct_chg": [3.0], "close": [2100.0]})
        )
        codes = ["399006.SZ"]
        result = await svc._get_indices_batch(codes, "20240614")

        svc.api.get_index_daily.assert_called_once_with(ts_code="399006.SZ", trade_date="20240614")
        assert len(result) == 1
        assert result[0]["color"] == "red"


class TestMarketDataServiceFetchMarketDataIntegration:
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

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_hot_concepts_failure_preserves_previous(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        """When hot concepts fetch fails, previous cached hot_concepts should be preserved."""
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

        # First call: hot concepts succeeds
        previous_concepts = [{"name": "AI", "change": "+3%", "color": "red"}]
        mock_news.get_hot_concepts = AsyncMock(return_value=previous_concepts)

        with patch("data.domain_services.market_data_service.TradeCalendarService") as mock_cal_cls:
            mock_cal = MagicMock()
            mock_cal.get_latest_trade_date = AsyncMock(return_value=None)
            mock_cal_cls.return_value = mock_cal
            svc.trade_calendar = mock_cal

            await svc._fetch_market_data()

        assert svc._cached_data["hot_concepts"] == previous_concepts

        # Second call: hot concepts fails — should preserve previous
        mock_news.get_hot_concepts = AsyncMock(side_effect=TimeoutError("timeout"))

        with patch("data.domain_services.market_data_service.TradeCalendarService") as mock_cal_cls:
            mock_cal = MagicMock()
            mock_cal.get_latest_trade_date = AsyncMock(return_value=None)
            mock_cal_cls.return_value = mock_cal
            svc.trade_calendar = mock_cal

            await svc._fetch_market_data()

        assert svc._cached_data["hot_concepts"] == previous_concepts

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_fetch_independent_fallback(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        import datetime

        mock_cache = MagicMock()
        mock_cm_cls.return_value = mock_cache
        mock_tc = MagicMock()
        mock_tc_cls.return_value = mock_tc

        svc = MarketDataService()
        svc.cache = mock_cache
        svc.api = mock_api_cls.return_value

        today = datetime.date(2026, 6, 15)
        yesterday = datetime.date(2026, 6, 14)

        # Mock trade_calendar.get_latest_trade_date to return today
        mock_cal = MagicMock()
        mock_cal.get_latest_trade_date = AsyncMock(return_value=today)
        mock_cal.get_prev_trade_date = AsyncMock(return_value=yesterday)
        svc.trade_calendar = mock_cal

        # Mock _get_indices_batch: succeed for today
        indices_today = [{"name": "SH", "value": "3000.00", "change": "+1.00%", "color": "red"}]

        async def fake_get_indices_batch(codes, date_str):
            if date_str == "20260615":
                return indices_today
            return []

        svc._get_indices_batch = AsyncMock(side_effect=fake_get_indices_batch)

        # Mock _get_hsgt: return empty for today, valid for yesterday
        hsgt_yesterday = {
            "name": "HSGT",
            "value": "1.50亿",
            "sub": "inflow",
            "color": "red",
        }
        hsgt_today = {"name": "HSGT", "value": "-", "sub": "-", "color": "grey"}

        async def fake_get_hsgt(date_str):
            if date_str == "20260615":
                return hsgt_today
            elif date_str == "20260614":
                return hsgt_yesterday
            return MarketDataService._get_empty_hsgt_data_static()

        svc._get_hsgt = AsyncMock(side_effect=fake_get_hsgt)

        mock_news.get_hot_concepts = AsyncMock(return_value=[])

        # Run _fetch_market_data
        await svc._fetch_market_data()

        cached_data = svc.get_cached_data()
        assert cached_data is not None
        # Verify indices are today's data
        assert cached_data["indices"] == indices_today
        # Verify HSGT fell back to yesterday's data
        assert cached_data["hsgt"] == hsgt_yesterday
        # Verify "stale" is True
        assert cached_data["stale"] is True

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_fetch_full_fallback_updates_date(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        """全部回退时 date 应更新为 prev_str"""
        import datetime

        mock_cache = MagicMock()
        mock_cm_cls.return_value = mock_cache
        mock_tc = MagicMock()
        mock_tc_cls.return_value = mock_tc

        svc = MarketDataService()
        svc.cache = mock_cache
        svc.api = mock_api_cls.return_value

        today = datetime.date(2026, 6, 15)
        yesterday = datetime.date(2026, 6, 13)

        mock_cal = MagicMock()
        mock_cal.get_latest_trade_date = AsyncMock(return_value=today)
        mock_cal.get_prev_trade_date = AsyncMock(return_value=yesterday)
        svc.trade_calendar = mock_cal

        indices_yesterday = [{"name": "SH", "value": "3000.00", "change": "+1.00%", "color": "red"}]
        hsgt_yesterday = {
            "name": "HSGT",
            "value": "1.50亿",
            "sub": "inflow",
            "color": "red",
        }

        async def fake_get_indices_batch(codes, date_str):
            if date_str == "20260613":
                return indices_yesterday
            return [{"name": "SH", "value": "-", "change": "-", "color": "grey"}]

        async def fake_get_hsgt(date_str):
            if date_str == "20260613":
                return hsgt_yesterday
            return MarketDataService._get_empty_hsgt_data_static()

        svc._get_indices_batch = AsyncMock(side_effect=fake_get_indices_batch)
        svc._get_hsgt = AsyncMock(side_effect=fake_get_hsgt)
        mock_news.get_hot_concepts = AsyncMock(return_value=[])

        await svc._fetch_market_data()

        cached_data = svc.get_cached_data()
        assert cached_data is not None
        assert cached_data["date"] == "20260613"
        assert cached_data["stale"] is True
        assert cached_data["indices"] == indices_yesterday
        assert cached_data["hsgt"] == hsgt_yesterday

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.NewsFetcher")
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_fetch_fallback_prev_date_none(self, mock_tc_cls, mock_cm_cls, mock_api_cls, mock_news):
        """prev_date 为 None 时不崩溃，date 保持当日"""
        import datetime

        mock_cache = MagicMock()
        mock_cm_cls.return_value = mock_cache
        mock_tc = MagicMock()
        mock_tc_cls.return_value = mock_tc

        svc = MarketDataService()
        svc.cache = mock_cache
        svc.api = mock_api_cls.return_value

        today = datetime.date(2026, 6, 15)

        mock_cal = MagicMock()
        mock_cal.get_latest_trade_date = AsyncMock(return_value=today)
        mock_cal.get_prev_trade_date = MagicMock(return_value=None)
        svc.trade_calendar = mock_cal

        # 今日数据为空
        svc._get_indices_batch = AsyncMock(return_value=[{"name": "SH", "value": "-", "change": "-", "color": "grey"}])
        svc._get_hsgt = AsyncMock(return_value=MarketDataService._get_empty_hsgt_data_static())
        mock_news.get_hot_concepts = AsyncMock(return_value=[])

        await svc._fetch_market_data()

        cached_data = svc.get_cached_data()
        assert cached_data is not None
        assert cached_data["date"] == "20260615"
        assert cached_data["stale"] is False


class TestMarketDataServiceGetHsgt:
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

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_unit_mismatch_warning(self, mock_tc, mock_cache_cls, mock_api):
        """单位不匹配时应触发 warning"""
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        df = MagicMock()
        df.empty = False
        df.iloc = [MagicMock()]
        df.iloc[0].get = lambda k: 500.0 if k == "north_money" else 0
        mock_cache.get_moneyflow_hsgt = AsyncMock(return_value=df)

        with patch(
            "data.domain_services.market_data_service.get_column_unit",
            return_value="yuan",
        ):
            svc = MarketDataService()
            with patch("data.domain_services.market_data_service.logger") as mock_logger:
                result = await svc._get_hsgt("20240614")
                mock_logger.warning.assert_any_call(
                    "[MarketDataService] north_money unit changed: expected='million_cny', got='%s'. "
                    "Display conversion may be incorrect.",
                    "yuan",
                )
                assert result["color"] == "red"


class TestMarketDataServiceStartStop:
    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_start_sets_running(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        with patch("data.domain_services.market_data_service.asyncio.create_task") as mock_ct:
            await svc.start()
            assert svc._running is True
            svc.stop()
            for call in mock_ct.call_args_list:
                call.args[0].close()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_start_idempotent(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        with patch("data.domain_services.market_data_service.asyncio.create_task") as mock_ct:
            await svc.start()
            assert svc._running is True
            await svc.start()
            assert svc._running is True
            svc.stop()
            for call in mock_ct.call_args_list:
                call.args[0].close()

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


class TestMarketDataServicePollLoopTokenBreaker:
    """token 熔断时 _poll_loop 应延长 sleep 间隔，避免 fast-fail 每 30s 刷屏 WARNING。"""

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_token_invalid_extends_poll_interval(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc._safe_fetch = AsyncMock()
        # 模拟 token 熔断
        svc.api = MagicMock()
        svc.api.is_token_invalid = True

        sleep_calls = []

        async def fake_sleep(interval):
            sleep_calls.append(interval)
            # 第一次 sleep 后停止循环
            svc._running = False

        with (
            patch(
                "data.domain_services.market_data_service.ConfigHandler.get_market_data_poll_interval", return_value=30
            ),
            patch("data.domain_services.market_data_service.asyncio.sleep", side_effect=fake_sleep),
        ):
            await svc._poll_loop()

        # token 熔断时应延长到 300s
        assert sleep_calls == [300], f"预期 sleep 300s，实际 {sleep_calls}"

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_token_valid_uses_config_interval(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc._safe_fetch = AsyncMock()
        svc.api = MagicMock()
        svc.api.is_token_invalid = False

        sleep_calls = []

        async def fake_sleep(interval):
            sleep_calls.append(interval)
            svc._running = False

        with (
            patch(
                "data.domain_services.market_data_service.ConfigHandler.get_market_data_poll_interval", return_value=45
            ),
            patch("data.domain_services.market_data_service.asyncio.sleep", side_effect=fake_sleep),
        ):
            await svc._poll_loop()

        # token 正常时使用配置值
        assert sleep_calls == [45], f"预期 sleep 45s，实际 {sleep_calls}"

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_token_invalid_uses_max_when_config_higher(self, mock_tc, mock_cache, mock_api):
        """配置间隔高于 300s 时，token 熔断不应缩短间隔（max 语义）。"""
        svc = MarketDataService()
        svc._running = True
        svc._safe_fetch = AsyncMock()
        svc.api = MagicMock()
        svc.api.is_token_invalid = True

        sleep_calls = []

        async def fake_sleep(interval):
            sleep_calls.append(interval)
            svc._running = False

        with (
            patch(
                "data.domain_services.market_data_service.ConfigHandler.get_market_data_poll_interval", return_value=600
            ),
            patch("data.domain_services.market_data_service.asyncio.sleep", side_effect=fake_sleep),
        ):
            await svc._poll_loop()

        # 配置 600s > 300s，应取 600s
        assert sleep_calls == [600], f"预期 sleep 600s（取最大值），实际 {sleep_calls}"
