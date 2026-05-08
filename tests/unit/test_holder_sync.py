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
        ctx.api.get_pledge_stat = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
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
