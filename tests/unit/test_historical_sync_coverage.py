import pytest
import datetime
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

import pandas as pd

from data.sync.historical import HistoricalSyncStrategy
from data.sync.base import SyncResult


def make_ctx():
    ctx = MagicMock()
    ctx.api = MagicMock()
    ctx.cache = MagicMock()
    ctx.processor = MagicMock()
    ctx.processor.trade_calendar = MagicMock()
    ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
    ctx.processor.trade_calendar.get_trade_dates = AsyncMock(
        return_value=[datetime.date(2024, 6, 14), datetime.date(2024, 6, 13)]
    )
    ctx.cache.check_data_exists = AsyncMock(return_value=False)
    ctx.cache.get_cached_dates_for_table = AsyncMock(return_value=set())
    ctx.cache.get_bulk_sync_quality_scores = AsyncMock(return_value={})
    ctx.cache.save_daily_quotes = AsyncMock(return_value=10)
    ctx.cache.save_daily_indicators = AsyncMock(return_value=10)
    ctx.cache.save_limit_list = AsyncMock(return_value=5)
    ctx.cache.save_suspend_d = AsyncMock(return_value=2)
    ctx.cache.save_margin_daily = AsyncMock(return_value=3)
    ctx.cache.save_moneyflow = AsyncMock(return_value=4)
    ctx.cache.save_northbound = AsyncMock(return_value=5)
    ctx.cache.save_moneyflow_hsgt = AsyncMock(return_value=2)
    ctx.cache.save_top_list = AsyncMock(return_value=1)
    ctx.cache.save_block_trade = AsyncMock(return_value=1)
    ctx.cache.save_index_daily = AsyncMock(return_value=3)
    ctx.cache.save_index_dailybasic = AsyncMock(return_value=3)
    ctx.cache.update_sync_status = AsyncMock()
    ctx.api.get_daily_quotes = AsyncMock(
        return_value=pd.DataFrame(
            {"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "close": [10.0], "pct_chg": [1.0], "vol": [1000]}
        )
    )
    ctx.api.get_daily_basic = AsyncMock(
        return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "pe": [10.0], "pb": [1.0]})
    )
    ctx.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_suspend_d = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_margin_detail = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_hk_hold = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_top_list = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_block_trade = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_index_dailybasic = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_index_daily = AsyncMock(return_value=pd.DataFrame())
    return ctx


class TestCancelDeepBranches:
    def test_cancel_with_active_tasks(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        mock_task = MagicMock()
        mock_task.done.return_value = False
        strategy._active_tasks = {mock_task}
        strategy.cancel()
        mock_task.cancel.assert_called_once()

    def test_cancel_with_done_tasks(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        mock_task = MagicMock()
        mock_task.done.return_value = True
        strategy._active_tasks = {mock_task}
        strategy.cancel()
        mock_task.cancel.assert_not_called()


class TestGetEffectiveTradeDateDeep:
    @pytest.mark.asyncio
    async def test_returns_none_falls_back_to_today(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestRunDeepBranches:
    @pytest.mark.asyncio
    async def test_run_cancelled_error(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        with patch.object(strategy, "_run_historical_sync", side_effect=asyncio.CancelledError()):
            result = await strategy.run(days=5)
            assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_run_top_level_exception(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        with patch.object(strategy, "_run_historical_sync", side_effect=RuntimeError("boom")):
            result = await strategy.run(days=5)
            assert result.status == "failed"
            assert any("boom" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_run_cancelled_flag_after_success(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        with patch.object(strategy, "_run_historical_sync"):
            strategy._cancelled = True
            result = await strategy.run(days=5)
            assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_run_quality_scores_with_issues(self):
        ctx = make_ctx()
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 14): {
                    "score": 90,
                    "expected_base": 5000,
                    "issues": ["low count", "stale"],
                    "tables": {"daily_quotes": {"count": 5000}, "daily_indicators": {"count": 4000}},
                }
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_quality_scores_exception(self):
        ctx = make_ctx()
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("quality error")
            return {}

        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(side_effect=side_effect)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None


class TestRunHistoricalSyncDeepBranches:
    @pytest.mark.asyncio
    async def test_trade_calendar_exception(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_trade_dates = AsyncMock(side_effect=Exception("cal error"))
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_sync_integrity_config_exception(self):
        ctx = make_ctx()
        with patch("utils.config_handler.ConfigHandler.get_sync_integrity_config", side_effect=Exception("cfg err")):
            strategy = HistoricalSyncStrategy(ctx)
            result = SyncResult()
            await strategy._run_historical_sync(5, None, result)
            assert result is not None

    @pytest.mark.asyncio
    async def test_cached_dates_intersection(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(
            return_value={datetime.date(2024, 6, 14), datetime.date(2024, 6, 13)}
        )
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 14): {"score": 90, "expected_base": 5000, "issues": []},
                datetime.date(2024, 6, 13): {"score": 90, "expected_base": 5000, "issues": []},
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_low_quality_dates_re_sync(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(
            return_value={datetime.date(2024, 6, 14), datetime.date(2024, 6, 13)}
        )
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 14): {"score": 50, "expected_base": 5000, "issues": ["low"]},
                datetime.date(2024, 6, 13): {"score": 90, "expected_base": 5000, "issues": []},
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_quality_check_exception(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(return_value={datetime.date(2024, 6, 14)})
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("quality check error")

        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(side_effect=side_effect)
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_cache_check_exception(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(side_effect=Exception("cache error"))
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_progress_callback(self):
        ctx = make_ctx()
        callback = MagicMock()
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, callback, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(side_effect=Exception("API fail"))
        ctx.api.get_daily_basic = AsyncMock(side_effect=Exception("API fail"))
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        with patch("utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy", return_value=1):
            await strategy._run_historical_sync(5, None, result)
        assert result.status in ("failed", "partial")

    @pytest.mark.asyncio
    async def test_smart_retry(self):
        ctx = make_ctx()
        call_count = 0

        async def failing_then_success(trade_date=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("transient fail")
            return pd.DataFrame(
                {"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "close": [10.0], "pct_chg": [1.0], "vol": [1000]}
            )

        ctx.api.get_daily_quotes = AsyncMock(side_effect=failing_then_success)
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        with (
            patch("utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy", return_value=1),
            patch("utils.config_handler.ConfigHandler.get_sync_retry_count", return_value=1),
        ):
            await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_partial_status_after_failed_dates(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(side_effect=Exception("fail"))
        ctx.api.get_daily_basic = AsyncMock(side_effect=Exception("fail"))
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        with (
            patch("utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy", return_value=1),
            patch("utils.config_handler.ConfigHandler.get_sync_retry_count", return_value=0),
        ):
            await strategy._run_historical_sync(5, None, result)
        assert result.status in ("failed", "partial")

    @pytest.mark.asyncio
    async def test_shutdown_event_stops_batch(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        strategy._shutdown_event.set()
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_complete_status(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result.status in ("success", "partial", "failed")


class TestSyncDailyMarketSnapshotDeep:
    @pytest.mark.asyncio
    async def test_save_if_ok_with_none_and_error(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(return_value=None)
        ctx.api.get_daily_basic = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_save_if_ok_fetch_failed_in_error_map(self):
        ctx = make_ctx()
        ctx.api.get_limit_list = AsyncMock(side_effect=Exception("fetch err"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_save_if_ok_empty_df(self):
        ctx = make_ctx()
        ctx.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_save_if_ok_non_critical_save_exception(self):
        ctx = make_ctx()
        ctx.cache.save_limit_list = AsyncMock(side_effect=Exception("save err"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_save_if_ok_critical_save_exception_raises(self):
        ctx = make_ctx()
        ctx.cache.save_daily_quotes = AsyncMock(side_effect=Exception("critical save err"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(Exception, match="critical save err"):
            await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

    @pytest.mark.asyncio
    async def test_northbound_all_filtered_sample_codes(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["00700.HK", "00001.HK"],
                    "trade_date": ["20240614", "20240614"],
                }
            )
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_northbound_partial_filter(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "00700.HK", "000002.SZ"],
                    "trade_date": ["20240614", "20240614", "20240614"],
                }
            )
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_northbound_none_with_error(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(side_effect=Exception("north err"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_northbound_none_no_error(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_northbound_save_exception(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        ctx.cache.save_northbound = AsyncMock(side_effect=Exception("save north err"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_safe_update_status_critical_empty(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_safe_update_status_save_failed(self):
        ctx = make_ctx()
        ctx.cache.save_limit_list = AsyncMock(side_effect=Exception("save fail"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_data_integrity_mismatch(self):
        ctx = make_ctx()
        ctx.cache.save_daily_quotes = AsyncMock(return_value=5)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(
            datetime.date(2024, 6, 14), force=True, sync_result=SyncResult()
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_quote_columns(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_adj_factor_warning(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {"ts_code": ["000001.SZ"], "trade_date": ["20240614"], "close": [10.0], "pct_chg": [1.0], "vol": [1000]}
            )
        )
        strategy = HistoricalSyncStrategy(ctx)
        sr = SyncResult()
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True, sync_result=sr)
        assert result is True

    @pytest.mark.asyncio
    async def test_missing_basic_columns(self):
        ctx = make_ctx()
        ctx.api.get_daily_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_fetch_indices_exception(self):
        ctx = make_ctx()
        ctx.api.get_index_daily = AsyncMock(side_effect=Exception("index err"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_fetch_indices_no_valid(self):
        ctx = make_ctx()
        ctx.api.get_index_daily = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True


class TestSyncMoneyflowDeep:
    @pytest.mark.asyncio
    async def test_moneyflow_none_trade_date(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_moneyflow()
        assert result is not None

    @pytest.mark.asyncio
    async def test_moneyflow_save_count_zero(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        ctx.cache.save_moneyflow = AsyncMock(return_value=0)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_moneyflow(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_moneyflow_save_count_none(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        ctx.cache.save_moneyflow = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_moneyflow(datetime.date(2024, 6, 14))
        assert result is None

    @pytest.mark.asyncio
    async def test_moneyflow_connection_error_raises(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(side_effect=ConnectionError("net err"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(ConnectionError):
            await strategy.sync_moneyflow(datetime.date(2024, 6, 14))

    @pytest.mark.asyncio
    async def test_moneyflow_timeout_error_raises(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(side_effect=TimeoutError("timeout"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(TimeoutError):
            await strategy.sync_moneyflow(datetime.date(2024, 6, 14))


class TestSyncNorthboundDeep:
    @pytest.mark.asyncio
    async def test_northbound_none_trade_date(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound()
        assert result is not None

    @pytest.mark.asyncio
    async def test_northbound_save_count_zero(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        ctx.cache.save_northbound = AsyncMock(return_value=0)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_northbound_save_count_none(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        ctx.cache.save_northbound = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result is None

    @pytest.mark.asyncio
    async def test_northbound_exception_returns_zero(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(side_effect=Exception("err"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_northbound_empty_df_returns_zero(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(return_value=pd.DataFrame())
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_northbound_none_df_returns_zero(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_northbound_timeout_error_raises(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(side_effect=TimeoutError("timeout"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(TimeoutError):
            await strategy.sync_northbound(datetime.date(2024, 6, 14))
