import asyncio

import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch
import pandas as pd

from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.historical import HistoricalSyncStrategy
from data.sync.base import SyncResult, SyncStatus

pytestmark = pytest.mark.unit


def make_ctx():
    ctx = MagicMock()
    ctx.api = AsyncMock()
    ctx.cache = MagicMock()
    ctx.processor = MagicMock()
    ctx.processor.trade_calendar = MagicMock()
    ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
    ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=["20240614", "20240613"])
    ctx.cache.check_data_exists = AsyncMock(return_value=False)
    ctx.cache.get_cached_dates_for_table = AsyncMock(return_value=set())
    ctx.cache.get_bulk_sync_quality_scores = AsyncMock(return_value={})
    ctx.cache.get_bulk_expected_stock_counts = AsyncMock(return_value={})
    ctx.cache.save_daily_quotes = AsyncMock(return_value=10)
    ctx.cache.save_daily_indicators = AsyncMock(return_value=10)
    ctx.cache.save_limit_list = AsyncMock(return_value=5)
    ctx.cache.save_suspend_d = AsyncMock(return_value=2)
    ctx.cache.save_margin_daily = AsyncMock(return_value=3)
    ctx.cache.save_moneyflow = AsyncMock(return_value=4)
    ctx.cache.save_northbound = AsyncMock(return_value=5)
    ctx.cache.save_moneyflow_hsgt = AsyncMock(return_value=2)
    ctx.cache.save_top_list = AsyncMock(return_value=1)
    ctx.cache.save_top_inst = AsyncMock(return_value=0)
    ctx.cache.save_stk_limit = AsyncMock(return_value=0)
    ctx.cache.save_block_trade = AsyncMock(return_value=1)
    ctx.cache.save_index_daily = AsyncMock(return_value=3)
    ctx.cache.save_index_dailybasic = AsyncMock(return_value=3)
    ctx.cache.update_sync_status = AsyncMock()
    ctx.api.get_daily_quotes = AsyncMock(
        return_value=pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "close": [10.0],
                "pct_chg": [1.0],
                "vol": [1000],
            }
        )
    )
    ctx.api.get_daily_basic = AsyncMock(
        return_value=pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "pe": [10.0],
                "pb": [1.0],
            }
        )
    )
    ctx.api.get_limit_list = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_suspend_d = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_margin_detail = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_hk_hold = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_top_list = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_top_inst = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_stk_limit = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_block_trade = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_index_dailybasic = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_index_daily = AsyncMock(return_value=pd.DataFrame())
    return ctx


class TestHistoricalSyncRun:
    @pytest.mark.asyncio
    async def test_basic_run(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None
        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    async def test_run_with_no_trade_dates(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=[])
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_run_with_progress_callback_and_str_dates(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=["20240614", "20240613"])
        strategy = HistoricalSyncStrategy(ctx)

        callback_called = []

        def progress_callback(current, total, msg):
            callback_called.append(msg)

        result = await strategy.run(days=5, progress_callback=progress_callback)
        assert result.status == "success"
        assert len(callback_called) > 0
        assert any("20240614" in msg for msg in callback_called)


class TestHistoricalSyncDailySnapshot:
    @pytest.mark.asyncio
    async def test_cache_hit_skip(self):
        ctx = make_ctx()
        ctx.cache.check_data_exists = AsyncMock(return_value=True)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14))
        assert result is True

    @pytest.mark.asyncio
    async def test_force_sync(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_quotes_failure_raises(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(side_effect=Exception("API error"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(Exception, match="API error"):
            await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

    @pytest.mark.asyncio
    async def test_basic_failure_raises(self):
        ctx = make_ctx()
        ctx.api.get_daily_basic = AsyncMock(side_effect=Exception("API error"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(Exception, match="API error"):
            await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

    @pytest.mark.asyncio
    async def test_northbound_filter(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "00700.HK"],
                    "trade_date": ["20240614", "20240614"],
                }
            )
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True


class TestHistoricalSyncMoneyflow:
    @pytest.mark.asyncio
    async def test_sync_moneyflow_with_data(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_moneyflow(datetime.date(2024, 6, 14))
        assert result is not None

    @pytest.mark.asyncio
    async def test_sync_moneyflow_empty(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_moneyflow(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_moneyflow_error(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow = AsyncMock(side_effect=Exception("API error"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_moneyflow(datetime.date(2024, 6, 14))
        assert result == 0

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


class TestHistoricalSyncNorthbound:
    @pytest.mark.asyncio
    async def test_sync_northbound_with_data(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                }
            )
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result is not None

    @pytest.mark.asyncio
    async def test_sync_northbound_empty(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_northbound_all_hk(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["00700.HK"],
                    "trade_date": ["20240614"],
                }
            )
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_northbound(datetime.date(2024, 6, 14))
        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_northbound_network_error(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(side_effect=ConnectionError("timeout"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(ConnectionError):
            await strategy.sync_northbound(datetime.date(2024, 6, 14))

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
    async def test_northbound_timeout_error_raises(self):
        ctx = make_ctx()
        ctx.api.get_hk_hold = AsyncMock(side_effect=TimeoutError("timeout"))
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(TimeoutError):
            await strategy.sync_northbound(datetime.date(2024, 6, 14))


class TestHistoricalSyncCancel:
    @pytest.mark.asyncio
    async def test_cancel(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        strategy.cancel()
        assert strategy._shutdown_event.is_set()

    def test_cancel_no_event_loop_no_raise(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        with patch.object(type(strategy), "_shutdown_event", new_callable=PropertyMock) as mock_evt:
            mock_evt.side_effect = RuntimeError("no event loop")
            strategy.cancel()

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


class TestHistoricalSyncGetEffectiveTradeDate:
    @pytest.mark.asyncio
    async def test_with_date(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_exception_fallback(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_returns_none_falls_back_to_today(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestHistoricalSyncRunExtended:
    @pytest.mark.asyncio
    async def test_run_with_quality_scores(self):
        ctx = make_ctx()
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 14): {
                    "score": 90,
                    "expected_base": 5000,
                    "issues": [],
                    "tables": {"daily_quotes": {"count": 5000}},
                }
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_quality_check_exception(self):
        ctx = make_ctx()
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(side_effect=Exception("quality error"))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_cancelled(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        strategy._shutdown_event.set()
        result = await strategy.run(days=5)
        assert result.status in ("cancelled", "success")

    @pytest.mark.asyncio
    async def test_cancelled_error_reraises(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=asyncio.CancelledError())
        strategy = HistoricalSyncStrategy(ctx)
        with pytest.raises(asyncio.CancelledError):
            await strategy.run(days=5)

    @pytest.mark.asyncio
    async def test_run_with_cached_dates(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(return_value={"20240614", "20240613"})
        ctx.cache.check_data_exists = AsyncMock(return_value=True)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_with_low_quality_dates(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(return_value={"20240614", "20240613"})
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 14): {
                    "score": 50,
                    "expected_base": 5000,
                    "issues": ["low count"],
                },
                datetime.date(2024, 6, 13): {
                    "score": 90,
                    "expected_base": 5000,
                    "issues": [],
                },
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_top_level_exception(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        with patch.object(strategy, "_run_historical_sync", side_effect=RuntimeError("boom")):
            result = await strategy.run(days=5)
            assert result.status == "failed"
            assert len(result.errors) > 0

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
                    "tables": {
                        "daily_quotes": {"count": 5000},
                        "daily_indicators": {"count": 4000},
                    },
                }
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.run(days=5)
        assert result is not None


class TestHistoricalSyncDailySnapshotExtended:
    @pytest.mark.asyncio
    async def test_empty_quotes(self):
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_empty_basic(self):
        ctx = make_ctx()
        ctx.api.get_daily_basic = AsyncMock(return_value=None)
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_limit_list_data(self):
        ctx = make_ctx()
        ctx.api.get_limit_list = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_top_list_data(self):
        ctx = make_ctx()
        ctx.api.get_top_list = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_block_trade_data(self):
        ctx = make_ctx()
        ctx.api.get_block_trade = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_margin_data(self):
        ctx = make_ctx()
        ctx.api.get_margin_detail = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_suspend_data(self):
        ctx = make_ctx()
        ctx.api.get_suspend_d = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_moneyflow_hsgt_data(self):
        ctx = make_ctx()
        ctx.api.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame({"ggt_ss": [100], "north_money": [50]}))
        strategy = HistoricalSyncStrategy(ctx)
        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_index_data(self):
        ctx = make_ctx()
        ctx.api.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SH"], "trade_date": ["20240614"]})
        )
        ctx.api.get_index_dailybasic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SH"], "trade_date": ["20240614"]})
        )
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
    async def test_verify_data_integrity_strict_loss_above_threshold_marks_partial(
        self,
    ):
        """strict=True with data loss > 5% must flag status as PARTIAL."""
        ctx = make_ctx()
        # fetched=20 rows, saved=18 rows → 10% loss (> 5%) → PARTIAL
        ctx.api.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"{i:06d}.SZ" for i in range(20)],
                    "trade_date": ["20240614"] * 20,
                    "close": [10.0] * 20,
                    "pct_chg": [1.0] * 20,
                    "vol": [1000] * 20,
                }
            )
        )
        ctx.cache.save_daily_quotes = AsyncMock(return_value=18)
        strategy = HistoricalSyncStrategy(ctx)
        sr = SyncResult()
        await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True, sync_result=sr)
        assert sr.status == SyncStatus.PARTIAL.value

    @pytest.mark.asyncio
    async def test_verify_data_integrity_strict_loss_below_threshold_stays_success(
        self,
    ):
        """strict=True with data loss < 5% must keep status as SUCCESS."""
        ctx = make_ctx()
        # fetched=100 rows, saved=96 rows → 4% loss (< 5%) → SUCCESS
        ctx.api.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"{i:06d}.SZ" for i in range(100)],
                    "trade_date": ["20240614"] * 100,
                    "close": [10.0] * 100,
                    "pct_chg": [1.0] * 100,
                    "vol": [1000] * 100,
                }
            )
        )
        ctx.cache.save_daily_quotes = AsyncMock(return_value=96)
        strategy = HistoricalSyncStrategy(ctx)
        sr = SyncResult()
        await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True, sync_result=sr)
        assert sr.status == SyncStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_verify_data_integrity_strict_boundary_exactly_five_percent_stays_success(
        self,
    ):
        """Boundary case: exactly 5% loss (saved = fetched * 0.95) must stay SUCCESS.

        Uses fetched=20, saved=19 so that 20 * 0.95 == 19.0 exactly (no
        floating-point drift), verifying the strict-less-than comparison.
        """
        ctx = make_ctx()
        ctx.api.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"{i:06d}.SZ" for i in range(20)],
                    "trade_date": ["20240614"] * 20,
                    "close": [10.0] * 20,
                    "pct_chg": [1.0] * 20,
                    "vol": [1000] * 20,
                }
            )
        )
        ctx.cache.save_daily_quotes = AsyncMock(return_value=19)
        strategy = HistoricalSyncStrategy(ctx)
        sr = SyncResult()
        await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True, sync_result=sr)
        assert sr.status == SyncStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_verify_data_integrity_non_strict_loss_above_threshold_stays_success(
        self,
    ):
        """strict=False (non-critical table) with data loss > 5% must stay SUCCESS.

        Backward-compatibility: non-critical tables only warn, never flag PARTIAL.
        Uses moneyflow (strict=False by default) with 10% data loss.
        """
        ctx = make_ctx()
        # daily_quotes: default make_ctx has fetched=1, saved=10 (gain, not loss) → no PARTIAL
        # moneyflow: fetched=20, saved=18 → 10% loss, but strict=False → only warn
        ctx.api.get_moneyflow = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"{i:06d}.SZ" for i in range(20)],
                    "trade_date": ["20240614"] * 20,
                }
            )
        )
        ctx.cache.save_moneyflow = AsyncMock(return_value=18)
        strategy = HistoricalSyncStrategy(ctx)
        sr = SyncResult()
        await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True, sync_result=sr)
        assert sr.status == SyncStatus.SUCCESS.value
        # Warning should still be recorded for the mismatch
        assert any("mf" in w for w in sr.warnings)

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
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                    "vol": [1000],
                }
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


class TestHistoricalSyncConstants:
    def test_synced_tables(self):
        assert "daily_quotes" in HistoricalSyncStrategy.SYNCED_TABLES
        assert "daily_indicators" in HistoricalSyncStrategy.SYNCED_TABLES
        assert len(HistoricalSyncStrategy.SYNCED_TABLES) >= 10

    def test_core_resume_tables(self):
        assert "daily_quotes" in HistoricalSyncStrategy.CORE_RESUME_TABLES
        assert "daily_indicators" in HistoricalSyncStrategy.CORE_RESUME_TABLES
        assert set(HistoricalSyncStrategy.CORE_RESUME_TABLES) == set(HistoricalSyncStrategy.SYNCED_TABLES)

    def test_synced_tables_includes_top_inst(self):
        """Phase 2E：top_inst 加入 SYNCED_TABLES。"""
        assert "top_inst" in HistoricalSyncStrategy.SYNCED_TABLES

    def test_synced_tables_includes_stk_limit(self):
        """Phase 2G：stk_limit 加入 SYNCED_TABLES。"""
        assert "stk_limit" in HistoricalSyncStrategy.SYNCED_TABLES


class TestHistoricalSyncTopInst:
    """Phase 2E：top_inst 同步分支测试。"""

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_includes_top_inst(self):
        """sync_daily_market_snapshot 应调用 get_top_inst + save_top_inst，并更新 sync_status。"""
        ctx = make_ctx()
        ctx.api.get_top_inst = AsyncMock(
            return_value=pd.DataFrame(
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
        )
        ctx.cache.save_top_inst = AsyncMock(return_value=1)
        strategy = HistoricalSyncStrategy(ctx)

        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

        assert result is True
        ctx.api.get_top_inst.assert_awaited_once_with(trade_date=datetime.date(2024, 6, 14))
        ctx.cache.save_top_inst.assert_awaited_once()
        # sync_status 表名应为 "top_inst"
        update_calls = ctx.cache.update_sync_status.await_args_list
        table_names = [call.args[0] for call in update_calls]
        assert "top_inst" in table_names

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_top_inst_permission_denied(self):
        """top_inst 权限不足时应标记 skipped_permission，不阻断同步。"""
        from data.external.tushare_client import TushareAPIPermissionError

        ctx = make_ctx()
        ctx.api.get_top_inst = AsyncMock(side_effect=TushareAPIPermissionError("top_inst", "no permission"))
        strategy = HistoricalSyncStrategy(ctx)

        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

        assert result is True
        ctx.cache.save_top_inst.assert_not_awaited()


class TestHistoricalSyncStkLimit:
    """Phase 2G：stk_limit 同步分支测试（仅数据层，不注入 AI）。"""

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_includes_stk_limit(self):
        """sync_daily_market_snapshot 应调用 get_stk_limit + save_stk_limit，并更新 sync_status。"""
        ctx = make_ctx()
        ctx.api.get_stk_limit = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                    "pre_close": [9.5],
                    "up_limit": [10.45],
                    "down_limit": [8.55],
                    "limit": ["U"],
                }
            )
        )
        ctx.cache.save_stk_limit = AsyncMock(return_value=1)
        strategy = HistoricalSyncStrategy(ctx)

        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

        assert result is True
        ctx.api.get_stk_limit.assert_awaited_once_with(trade_date=datetime.date(2024, 6, 14))
        ctx.cache.save_stk_limit.assert_awaited_once()
        # sync_status 表名应为 "stk_limit"
        update_calls = ctx.cache.update_sync_status.await_args_list
        table_names = [call.args[0] for call in update_calls]
        assert "stk_limit" in table_names

    @pytest.mark.asyncio
    async def test_sync_daily_market_snapshot_stk_limit_permission_denied(self):
        """stk_limit 权限不足时应标记 skipped_permission，不阻断同步。"""
        from data.external.tushare_client import TushareAPIPermissionError

        ctx = make_ctx()
        ctx.api.get_stk_limit = AsyncMock(side_effect=TushareAPIPermissionError("stk_limit", "no permission"))
        strategy = HistoricalSyncStrategy(ctx)

        result = await strategy.sync_daily_market_snapshot(datetime.date(2024, 6, 14), force=True)

        assert result is True
        ctx.cache.save_stk_limit.assert_not_awaited()


class TestHistoricalSyncRunDeepBranches:
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
        with patch(
            "utils.config_handler.ConfigHandler.get_sync_integrity_config",
            side_effect=Exception("cfg err"),
        ):
            strategy = HistoricalSyncStrategy(ctx)
            result = SyncResult()
            await strategy._run_historical_sync(5, None, result)
            assert result is not None

    @pytest.mark.asyncio
    async def test_cached_dates_intersection(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(return_value={"20240614", "20240613"})
        ctx.cache.get_bulk_sync_quality_scores = AsyncMock(
            return_value={
                datetime.date(2024, 6, 14): {
                    "score": 90,
                    "expected_base": 5000,
                    "issues": [],
                },
                datetime.date(2024, 6, 13): {
                    "score": 90,
                    "expected_base": 5000,
                    "issues": [],
                },
            }
        )
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        await strategy._run_historical_sync(5, None, result)
        assert result is not None

    @pytest.mark.asyncio
    async def test_quality_check_exception(self):
        ctx = make_ctx()
        ctx.cache.get_cached_dates_for_table = AsyncMock(return_value={"20240614"})

        async def side_effect(**kwargs):
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
        with patch(
            "utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy",
            return_value=1,
        ):
            await strategy._run_historical_sync(5, None, result)
        assert result.status in ("failed", "partial")
        assert len(result.errors) > 0

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
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240614"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                    "vol": [1000],
                }
            )

        ctx.api.get_daily_quotes = AsyncMock(side_effect=failing_then_success)
        strategy = HistoricalSyncStrategy(ctx)
        result = SyncResult()
        with (
            patch(
                "utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy",
                return_value=1,
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_sync_retry_count",
                return_value=1,
            ),
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
            patch(
                "utils.config_handler.ConfigHandler.get_sync_max_concurrent_heavy",
                return_value=1,
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_sync_retry_count",
                return_value=0,
            ),
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


class TestHistoricalSyncRunEngineDisposedError:
    """R5 举一反三 fix: EngineDisposedError 必须 raise 让调用方感知，不可 swallow"""

    @pytest.mark.asyncio
    async def test_run_reraises_engine_disposed(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = HistoricalSyncStrategy(ctx)

        async def mock_run_hist(days, progress_callback, result):
            raise EngineDisposedError("Engine disposed")

        strategy._run_historical_sync = mock_run_hist
        with pytest.raises(EngineDisposedError):
            await strategy._run_impl()
