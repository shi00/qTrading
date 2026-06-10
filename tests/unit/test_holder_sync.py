import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock
import pandas as pd

from data.sync.holder import HolderSyncStrategy, _MAX_ERRORS, _PROGRESS_LOG_INTERVAL, _CHECKPOINT_INTERVAL
from data.sync.base import SyncContext


class TestHolderSyncCancel:
    @pytest.mark.asyncio
    async def test_cancel(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        assert not strategy._cancelled
        strategy.cancel()
        assert strategy._cancelled


class TestHolderSyncGetEffectiveTradeDate:
    @pytest.mark.asyncio
    async def test_with_processor(self):
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.datetime(2024, 6, 14))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_with_date_return(self):
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_fallback_on_error(self):
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("test error"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestHolderSyncStkHoldernumber:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder_num": [100]})
        )
        ctx.cache = MagicMock()
        ctx.cache.save_holder_number = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == 1

    @pytest.mark.asyncio
    async def test_empty_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_none_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(return_value=None)
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(side_effect=Exception("API error"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == -1


class TestHolderSyncTop10Holders:
    @pytest.mark.asyncio
    async def test_no_stock_list(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=None)
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_empty_stock_list(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_all_already_synced(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value={"000001.SZ"})
        result = await strategy._sync_top10_holders("20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}))
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})
        )
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result >= 0


class TestHolderSyncCancelReturnsMinusOne:
    """B-P1-7: Verify that cancelled sync operations return -1."""

    @pytest.mark.asyncio
    async def test_top10_holders_cancelled_returns_minus_one(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"]})
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})
        )
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        strategy.cancel()
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1, "B-P1-7: Cancelled _sync_top10_holders should return -1"

    @pytest.mark.asyncio
    async def test_pledge_stat_cancelled_returns_minus_one(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        ctx.cache.save_pledge_stat = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy.cancel()
        result = await strategy._sync_pledge_stat()
        assert result[0] == -1, "B-P1-7: Cancelled _sync_pledge_stat should return -1"


class TestHolderSyncGetExistingTop10TsCodes:
    @pytest.mark.asyncio
    async def test_success(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_existing_top10_ts_codes = AsyncMock(return_value={"000001.SZ"})
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_existing_top10_ts_codes("20240331")
        assert "000001.SZ" in result

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_existing_top10_ts_codes = AsyncMock(side_effect=Exception("DB error"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_existing_top10_ts_codes("20240331")
        assert result == set()


class TestHolderSyncSaveTop10Checkpoint:
    @pytest.mark.asyncio
    async def test_success(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_top10_holders = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        dfs = [pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})]
        result = await strategy._save_top10_checkpoint(dfs, "20240331")
        assert result is True

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_top10_holders = AsyncMock(side_effect=Exception("DB error"))
        strategy = HolderSyncStrategy(ctx)
        dfs = [pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})]
        result = await strategy._save_top10_checkpoint(dfs, "20240331")
        assert result is False


class TestHolderSyncOneTable:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == 1

    @pytest.mark.asyncio
    async def test_empty_data(self):
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=Exception("API error"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == -1


class TestHolderSyncPledgeStat:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [datetime.date(2024, 6, 14)]})
        )
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = await strategy._sync_pledge_stat()
        assert result[0] >= 0

    @pytest.mark.asyncio
    async def test_no_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = await strategy._sync_pledge_stat()
        assert result[0] == 0

    @pytest.mark.asyncio
    async def test_all_api_failed(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=Exception("API error"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = await strategy._sync_pledge_stat()
        assert result[0] == -1

    @pytest.mark.asyncio
    async def test_cancelled(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._cancelled = True
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        result = await strategy._sync_pledge_stat()
        assert result[0] == -1


class TestHolderSyncGetRecentQuarterEnds:
    def test_returns_list(self):
        result = HolderSyncStrategy._get_recent_quarter_ends(count=2)
        assert isinstance(result, list)
        assert len(result) <= 2

    def test_returns_strings(self):
        result = HolderSyncStrategy._get_recent_quarter_ends(count=2)
        for item in result:
            assert isinstance(item, str)
            assert len(item) == 8


class TestHolderSyncLogSyncError:
    def test_permission_error(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._log_sync_error("test_table", "20240331", Exception("permission denied"))

    def test_regular_error(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._log_sync_error("test_table", "20240331", Exception("network error"))


class TestHolderSyncRun:
    @pytest.mark.asyncio
    async def test_basic_run(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(5, datetime.date(2024, 6, 14)))
        result = await strategy.run()
        assert result is not None
        assert result.added == 65

    @pytest.mark.asyncio
    async def test_cancelled_run(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(5, datetime.date(2024, 6, 14)))
        strategy._cancelled = True
        result = await strategy.run()
        assert result is not None


class TestHolderSyncInit:
    def test_init(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        assert strategy.context is ctx
        assert strategy._cancelled is False


class TestHolderSyncStrategyCancel:
    @pytest.mark.asyncio
    async def test_cancel_sets_flag(self):
        ctx = MagicMock(spec=SyncContext)
        strategy = HolderSyncStrategy(ctx)
        strategy.cancel()
        assert strategy._cancelled is True


class TestHolderSyncStrategyConstants:
    def test_max_errors(self):
        assert _MAX_ERRORS == 5

    def test_progress_log_interval(self):
        assert _PROGRESS_LOG_INTERVAL == 200

    def test_checkpoint_interval(self):
        assert _CHECKPOINT_INTERVAL == 5000


class TestHolderSyncStrategyGetEffectiveTradeDate:
    @pytest.mark.asyncio
    async def test_no_processor(self):
        ctx = MagicMock(spec=SyncContext)
        ctx.processor = None
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result is not None

    @pytest.mark.asyncio
    async def test_with_processor_date(self):
        import datetime

        ctx = MagicMock(spec=SyncContext)
        mock_processor = MagicMock()
        mock_processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 15))
        ctx.processor = mock_processor
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result == datetime.date(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_with_processor_datetime(self):
        import datetime

        ctx = MagicMock(spec=SyncContext)
        mock_processor = MagicMock()
        mock_processor.trade_calendar.get_latest_trade_date = AsyncMock(
            return_value=datetime.datetime(2024, 6, 15, 15, 0)
        )
        ctx.processor = mock_processor
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result == datetime.date(2024, 6, 15)

    @pytest.mark.asyncio
    async def test_processor_exception_fallback(self):
        ctx = MagicMock(spec=SyncContext)
        mock_processor = MagicMock()
        mock_processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
        ctx.processor = mock_processor
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert result is not None


class TestHolderSyncStrategyInit:
    def test_init(self):
        ctx = MagicMock(spec=SyncContext)
        strategy = HolderSyncStrategy(ctx)
        assert strategy.context is ctx
        assert strategy._cancelled is False


class TestHolderSyncSyncPledgeStat:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": [datetime.date(2024, 6, 14)],
                }
            )
        )
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        count, date = await strategy._sync_pledge_stat()
        assert count >= 0

    @pytest.mark.asyncio
    async def test_no_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        count, date = await strategy._sync_pledge_stat()
        assert count == 0

    @pytest.mark.asyncio
    async def test_api_error(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=Exception("API Error"))
        strategy = HolderSyncStrategy(ctx)
        count, date = await strategy._sync_pledge_stat()
        assert count == -1

    @pytest.mark.asyncio
    async def test_cancelled(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._cancelled = True
        count, date = await strategy._sync_pledge_stat()
        assert count == -1

    @pytest.mark.asyncio
    async def test_no_synthetic_ann_date(self):
        """MD-001: pledge_stat API does not return ann_date; we must NOT synthesize it."""
        import datetime as _dt

        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "end_date": [_dt.date(2024, 6, 14), _dt.date(2024, 6, 7)],
                    "pledge_ratio": [10.0, 20.0],
                }
            )
        )
        ctx.cache = MagicMock()
        saved_df = None

        async def capture_save(df):
            nonlocal saved_df
            saved_df = df

        ctx.cache.save_pledge_stat = AsyncMock(side_effect=capture_save)
        strategy = HolderSyncStrategy(ctx)
        count, date = await strategy._sync_pledge_stat()
        assert count == 2
        assert saved_df is not None
        # ann_date should NOT be synthesized from end_date
        assert "ann_date" not in saved_df.columns

    @pytest.mark.asyncio
    async def test_preserves_existing_ann_date(self):
        """If Tushare ever returns ann_date, the sync layer preserves it in the DataFrame.

        Note: The DAO layer (save_pledge_stat) currently excludes ann_date from upsert
        columns (see MD-001). If Tushare starts returning ann_date, the DAO exclude
        must be removed for the data to persist. This test verifies sync-layer behavior only.
        """
        import datetime as _dt

        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": [_dt.date(2024, 6, 14)],
                    "ann_date": [_dt.date(2024, 6, 16)],
                    "pledge_ratio": [10.0],
                }
            )
        )
        ctx.cache = MagicMock()
        saved_df = None

        async def capture_save(df):
            nonlocal saved_df
            saved_df = df

        ctx.cache.save_pledge_stat = AsyncMock(side_effect=capture_save)
        strategy = HolderSyncStrategy(ctx)
        count, date = await strategy._sync_pledge_stat()
        assert count == 1
        assert saved_df is not None
        assert saved_df.loc[0, "ann_date"] == _dt.date(2024, 6, 16)


class TestHolderSyncSyncStkHoldernumber:
    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240331"],
                }
            )
        )
        ctx.cache = MagicMock()
        ctx.cache.save_holder_number = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == 1

    @pytest.mark.asyncio
    async def test_empty(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_none(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(return_value=None)
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_error(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(side_effect=Exception("API Error"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == -1


class TestHolderSyncSyncTop10Holders:
    @pytest.mark.asyncio
    async def test_no_stocks(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_all_already_synced(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value={"000001.SZ"})
        result = await strategy._sync_top10_holders("20240331")
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                }
            )
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "holder_name": ["Test"],
                }
            )
        )
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result >= 0


class TestHolderSyncGetEffectiveTradeDateNone:
    @pytest.mark.asyncio
    async def test_none_return_falls_back(self):
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestHolderSyncRunErrorPaths:
    @pytest.mark.asyncio
    async def test_stk_holdernumber_error_accumulates(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=-1)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(5, datetime.date(2024, 6, 14)))
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_top10_holders_error_accumulates(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=-1)
        strategy._sync_pledge_stat = AsyncMock(return_value=(5, datetime.date(2024, 6, 14)))
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_pledge_stat_error_accumulates(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(-1, None))
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_max_errors_sets_partial(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=-1)
        strategy._sync_top10_holders = AsyncMock(return_value=-1)
        strategy._sync_pledge_stat = AsyncMock(return_value=(-1, None))
        result = await strategy.run()
        assert result.status == "partial"

    @pytest.mark.asyncio
    async def test_cancelled_during_run(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(5, datetime.date(2024, 6, 14)))
        check_count = 0

        def fake_check(result):
            nonlocal check_count
            check_count += 1
            if check_count >= 2:
                strategy._cancelled = True
                return True
            return False

        strategy._check_cancelled = fake_check
        result = await strategy.run()
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_top_level_exception(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(side_effect=RuntimeError("unexpected"))
        result = await strategy.run()
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_pledge_stat_zero_count_no_update(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(0, None))
        result = await strategy.run()
        assert result is not None


class TestHolderSyncTop10ErrorPaths:
    @pytest.mark.asyncio
    async def test_consecutive_errors_abort(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(10)]})
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(side_effect=Exception("API error"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_rate_limit_error_counted(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ", "000006.SZ"]}
            )
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(side_effect=Exception("每分钟最多访问"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_outer_exception_returns_minus_one(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=RuntimeError("DB error"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1


class TestHolderSyncTop10ProgressAndCheckpoint:
    @pytest.mark.asyncio
    async def test_progress_logging_at_interval(self):
        codes = [f"{i:06d}.SZ" for i in range(1, 250)]
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": codes}))
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})
        )
        ctx.api._api_limiters = {}
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result >= 0

    @pytest.mark.asyncio
    async def test_checkpoint_save_triggered(self):
        codes = [f"{i:06d}.SZ" for i in range(1, 10)]
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": codes}))
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        big_df = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(1000)], "holder_name": ["Test"] * 1000})
        ctx.api.get_top10_holders = AsyncMock(return_value=big_df)
        ctx.api._api_limiters = {}
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result >= 0

    @pytest.mark.asyncio
    async def test_progress_with_rate_limiter(self):
        codes = [f"{i:06d}.SZ" for i in range(1, 250)]
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": codes}))
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})
        )
        mock_limiter = MagicMock()
        mock_limiter.current_rate_per_min = 120.0
        ctx.api._api_limiters = {"top10_holders": mock_limiter}
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result >= 0


class TestHolderSyncOneTablePermission:
    @pytest.mark.asyncio
    async def test_permission_error(self):
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=Exception("permission denied"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_jifen_error(self):
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=Exception("积分不足"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == -1


class TestHolderSyncPledgeStatErrorPaths:
    @pytest.mark.asyncio
    async def test_permission_error(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=Exception("permission denied"))
        ctx.cache = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1

    @pytest.mark.asyncio
    async def test_outer_exception(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.cache = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=RuntimeError("unexpected"))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1

    @pytest.mark.asyncio
    async def test_api_error_continues_retry(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        call_count = 0

        async def mock_pledge(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("temporary error")
            return pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [datetime.date(2024, 6, 14)]})

        ctx.api.get_pledge_stat = AsyncMock(side_effect=mock_pledge)
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count >= 0


class TestHolderSyncGetRecentQuarterEndsEdge:
    def test_count_one(self):
        result = HolderSyncStrategy._get_recent_quarter_ends(count=1)
        assert len(result) <= 1

    def test_count_large(self):
        result = HolderSyncStrategy._get_recent_quarter_ends(count=10)
        assert len(result) <= 10
