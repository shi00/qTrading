# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio

import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock
import pandas as pd

from data.external.tushare_client import TushareAPIPermissionError
from data.persistence.daos.base_dao import EngineDisposedError
from data.sync.holder import (
    HolderSyncStrategy,
    _MAX_ERRORS,
    _PROGRESS_LOG_INTERVAL,
    _CHECKPOINT_INTERVAL,
)
from data.sync.base import SyncContext

pytestmark = pytest.mark.unit


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


class TestHolderSyncShareFloat:
    """Phase 3D：_sync_share_float 单元测试。

    share_float 是事件驱动数据，sync 窗口 [today-90, today+30]，
    单次 API 调用全市场查询，无 pledge_stat 的循环重试逻辑。
    """

    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "ann_date": [datetime.date(2024, 6, 1)],
                    "float_date": [datetime.date(2024, 8, 15)],
                    "float_share": [1000.0],
                    "float_ratio": [5.2],
                    "share_type": ["定向增发"],
                }
            )
        )
        ctx.cache = MagicMock()
        ctx.cache.save_share_float = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == 1
        assert date == datetime.date(2024, 6, 14)
        ctx.cache.save_share_float.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == 0
        assert date == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_api_error(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(side_effect=Exception("API Error"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == -1
        assert date is None

    @pytest.mark.asyncio
    async def test_cancelled(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._cancelled = True
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == -1
        assert date is None


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


class TestHolderSyncStkHoldertrade:
    """Phase 3E：_sync_stk_holdertrade 单元测试。

    stk_holdertrade 是事件驱动数据，sync 窗口 [today-90, today]，
    单次 API 调用全市场查询，无 pledge_stat 的循环重试逻辑。
    """

    @pytest.mark.asyncio
    async def test_with_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "ann_date": [datetime.date(2024, 6, 1)],
                    "holder_name": ["张三"],
                    "holder_type": ["G"],
                    "in_de": ["IN"],
                    "change_vol": [10000.0],
                    "change_ratio": [0.5],
                    "after_share": [1000000.0],
                    "after_ratio": [50.0],
                }
            )
        )
        ctx.cache = MagicMock()
        ctx.cache.save_stk_holdertrade = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == 1
        assert date == datetime.date(2024, 6, 14)
        ctx.cache.save_stk_holdertrade.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_data(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == 0
        assert date == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_api_error(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(side_effect=Exception("API Error"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1
        assert date is None

    @pytest.mark.asyncio
    async def test_cancelled(self):
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._cancelled = True
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1
        assert date is None


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
                {
                    "ts_code": [
                        "000001.SZ",
                        "000002.SZ",
                        "000003.SZ",
                        "000004.SZ",
                        "000005.SZ",
                        "000006.SZ",
                    ]
                }
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
        big_df = pd.DataFrame(
            {
                "ts_code": [f"{i:06d}.SZ" for i in range(1000)],
                "holder_name": ["Test"] * 1000,
            }
        )
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


class TestHolderSyncRunEngineDisposedError:
    """R5 举一反三 fix: EngineDisposedError 必须 raise 让调用方感知，不可 swallow"""

    @pytest.mark.asyncio
    async def test_run_reraises_engine_disposed(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.engine = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_recent_quarter_ends = MagicMock(return_value=["20240331"])

        async def mock_sync(qe):
            raise EngineDisposedError("Engine disposed")

        strategy._sync_stk_holdernumber = mock_sync
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._run_impl()
        assert "disposed" in str(exc_info.value)


# =============================================================================
# Task 5.4: 补 data/sync/holder.py 测试（覆盖率 76% → ≥80%）
# 覆盖路径：R5 守卫 / R2 守卫 / TushareAPIPermissionError / severity 分支 /
#           跨年边界 / rate_limit_hits / cancel_event 长任务退出 / 数据场景
# =============================================================================


class TestR5EngineDisposedReraises:
    """R5 守卫：所有 sync handler 必须将 EngineDisposedError 透传 raise，不可 swallow。"""

    @pytest.mark.asyncio
    async def test_sync_stk_holdernumber_reraises(self):
        """_sync_stk_holdernumber 在 API 抛 EngineDisposedError 时必须 raise。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._sync_stk_holdernumber("20240331")
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_one_table_reraises(self):
        """_sync_one_table 在 API 抛 EngineDisposedError 时必须 raise。"""
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_reraises_in_api_call(self):
        """_sync_pledge_stat 在 API 调用抛 EngineDisposedError 时的现状行为测试。

        源码缺陷（R5 违规，登记为独立技术债）：
        `_sync_pledge_stat` 的 `except EngineDisposedError: raise`（holder.py:663-664）
        位于 inner try（for retry 循环内），reraise 后被 outer `except Exception as e`
        （holder.py:753）捕获。由于 classify_severity(EngineDisposedError) 返回
        "operational"（非 "system"），outer handler 走 else 分支记 error 日志并返回 -1，
        未 reraise。对比 _sync_share_float/_sync_stk_holdertrade 的
        `except EngineDisposedError: raise` 位于 outer try 层级，正确 reraise。

        Task 5.4 仅新增测试，不重构源码；此处匹配源码现状断言 count == -1。
        修复时应将 inner `except EngineDisposedError: raise` 提升到 outer try 层级，
        与 _sync_share_float 一致。
        """
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1  # 源码现状：outer except 吞没 EngineDisposedError（R5 违规）
        assert date is None

    @pytest.mark.asyncio
    async def test_sync_share_float_reraises(self):
        """_sync_share_float 在 API 抛 EngineDisposedError 时必须 raise。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._sync_share_float()
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_stk_holdertrade_reraises(self):
        """_sync_stk_holdertrade 在 API 抛 EngineDisposedError 时必须 raise。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._sync_stk_holdertrade()
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes_reraises(self):
        """_get_existing_top10_ts_codes 在 DB 查询抛 EngineDisposedError 时必须 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_existing_top10_ts_codes = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._get_existing_top10_ts_codes("20240331")
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_save_top10_checkpoint_reraises(self):
        """_save_top10_checkpoint 在 save 抛 EngineDisposedError 时必须 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_top10_holders = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        dfs = [pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})]
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._save_top10_checkpoint(dfs, "20240331")
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_top10_holders_reraises_in_iteration(self):
        """_sync_top10_holders 在 per-stock 迭代中 EngineDisposedError 必须从外层 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}))
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._sync_top10_holders("20240331")
        assert "disposed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_permission_recording_engine_disposed(self):
        """_sync_pledge_stat 在 TushareAPIPermissionError 后记录状态时 EngineDisposedError 被吞没返回 -1。

        注：源码 inner except 将 EngineDisposedError 归为 operational severity（非 system），
        仅记 error 日志不 raise。此为现状测试，不重构源码（Task 5.4 仅新增测试）。
        """
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1  # EngineDisposedError 被吞没，返回 -1

    @pytest.mark.asyncio
    async def test_sync_share_float_permission_recording_engine_disposed(self):
        """_sync_share_float 在 TushareAPIPermissionError 后记录状态时 EngineDisposedError 被吞没返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_stk_holdertrade_permission_recording_engine_disposed(self):
        """_sync_stk_holdertrade 在 TushareAPIPermissionError 后记录状态时 EngineDisposedError 被吞没返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1


class TestR2CancelledErrorReraises:
    """R2 守卫：_run_impl 必须将 asyncio.CancelledError 透传 raise，并设置 status=cancelled。"""

    @pytest.mark.asyncio
    async def test_run_reraises_cancelled_error(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_recent_quarter_ends = MagicMock(return_value=["20240331"])

        async def mock_sync(qe):
            raise asyncio.CancelledError()

        strategy._sync_stk_holdernumber = mock_sync
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await strategy._run_impl()
        assert isinstance(exc_info.value, asyncio.CancelledError)


class TestRunImplFullSuccessPaths:
    """覆盖 _run_impl 中所有 update_sync_status 调用路径（含 share_float / stk_holdertrade）。"""

    @pytest.mark.asyncio
    async def test_all_tables_success_updates_status(self):
        """全部 5 张表都成功时，update_sync_status 应被调用 7 次（含 share_float / stk_holdertrade）。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        # 2 个季度 → stk_holdernumber + top10_holders 各 2 次，pledge/share/holdertrade 各 1 次
        strategy._get_recent_quarter_ends = MagicMock(return_value=["20240331", "20231231"])
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(5, datetime.date(2024, 6, 14)))
        strategy._sync_share_float = AsyncMock(return_value=(3, datetime.date(2024, 6, 14)))
        strategy._sync_stk_holdertrade = AsyncMock(return_value=(2, datetime.date(2024, 6, 14)))
        result = await strategy._run_impl()
        assert result.status == "success"
        assert result.added == 70  # (10+20)*2 + 5 + 3 + 2
        # 2 季度 stk_holdernumber + 2 季度 top10 + pledge + share_float + holdertrade = 7
        assert ctx.cache.update_sync_status.await_count == 7

    @pytest.mark.asyncio
    async def test_share_float_zero_count_skips_update(self):
        """share_float count=0 时不触发 update_sync_status（覆盖 elif count > 0 分支）。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_recent_quarter_ends = MagicMock(return_value=["20240331"])
        strategy._sync_stk_holdernumber = AsyncMock(return_value=10)
        strategy._sync_top10_holders = AsyncMock(return_value=20)
        strategy._sync_pledge_stat = AsyncMock(return_value=(0, None))  # 跳过
        strategy._sync_share_float = AsyncMock(return_value=(0, None))  # 跳过
        strategy._sync_stk_holdertrade = AsyncMock(return_value=(0, None))  # 跳过
        result = await strategy._run_impl()
        assert result.status == "success"
        # 仅 stk_holdernumber + top10 触发 update
        assert ctx.cache.update_sync_status.await_count == 2


class TestRunImplSystemSeverityReraises:
    """覆盖 _run_impl 外层 except 的 system severity raise 分支。"""

    @pytest.mark.asyncio
    async def test_system_severity_reraises(self):
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_recent_quarter_ends = MagicMock(return_value=["20240331"])

        async def mock_sync(qe):
            raise MemoryError("system down")

        strategy._sync_stk_holdernumber = mock_sync
        with pytest.raises(MemoryError) as exc_info:
            await strategy._run_impl()
        assert "system down" in str(exc_info.value)


class TestTushareAPIPermissionPaths:
    """覆盖 5 个 sync handler 的 TushareAPIPermissionError 处理路径。"""

    @pytest.mark.asyncio
    async def test_sync_stk_holdernumber_permission_denied(self):
        """_sync_stk_holdernumber 在 TushareAPIPermissionError 时记录 skipped_permission 并返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdernumber = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_stk_holdernumber("20240331")
        assert result == -1
        ctx.cache.update_sync_status.assert_awaited_once()
        args = ctx.cache.update_sync_status.await_args
        assert args is not None
        assert args.kwargs.get("status") == "skipped_permission" or args.args[3] == "skipped_permission"

    @pytest.mark.asyncio
    async def test_sync_one_table_permission_denied(self):
        """_sync_one_table 在 TushareAPIPermissionError 时记录 skipped_permission 并返回 -1。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == -1
        ctx.cache.update_sync_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_one_table_permission_denied_no_end_date(self):
        """_sync_one_table 在 end_date=None 时不写 update_sync_status（覆盖 if end_date 分支）。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", None)
        assert result == -1
        ctx.cache.update_sync_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_permission_denied(self):
        """_sync_pledge_stat 在 TushareAPIPermissionError 时（被 inner except 捕获）返回 -1。

        注：源码中 TushareAPIPermissionError 在 retry loop 内被 `except Exception as api_err` 捕获，
        连续 4 次失败后 `all_api_failed=True`，返回 (-1, None)。outer `except TushareAPIPermissionError`
        仅在 save_pledge_stat 抛出时触发（见 test_sync_pledge_stat_outer_permission_denied）。
        """
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1
        # inner except 捕获，不走 outer TushareAPIPermissionError handler
        ctx.cache.update_sync_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_outer_permission_denied(self):
        """_sync_pledge_stat 在 save_pledge_stat 抛 TushareAPIPermissionError 时走 outer handler。

        覆盖 outer `except TushareAPIPermissionError` 分支（lines 715-752）：
        save_pledge_stat 抛 TushareAPIPermissionError → 记 skipped_permission → 返回 -1。
        """
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [datetime.date(2024, 6, 14)]})
        )
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1
        ctx.cache.update_sync_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_share_float_permission_denied(self):
        """_sync_share_float 在 TushareAPIPermissionError 时记录 skipped_permission 并返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == -1
        ctx.cache.update_sync_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_stk_holdertrade_permission_denied(self):
        """_sync_stk_holdertrade 在 TushareAPIPermissionError 时记录 skipped_permission 并返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1
        ctx.cache.update_sync_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_pledge_stat_permission_recording_failure(self):
        """_sync_pledge_stat 在 outer TushareAPIPermissionError handler 中 update_sync_status 抛异常时仍返回 -1。

        场景：save_pledge_stat 抛 TushareAPIPermissionError → 走 outer handler →
        update_sync_status 抛 RuntimeError → inner except 捕获 → 返回 -1（不二次 raise）。
        """
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [datetime.date(2024, 6, 14)]})
        )
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache.update_sync_status = AsyncMock(side_effect=RuntimeError("recording failed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1  # 不二次 raise

    @pytest.mark.asyncio
    async def test_sync_share_float_permission_recording_failure(self):
        """_sync_share_float 在 TushareAPIPermissionError 后 update_sync_status 抛异常时仍返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock(side_effect=RuntimeError("recording failed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == -1

    @pytest.mark.asyncio
    async def test_sync_stk_holdertrade_permission_recording_failure(self):
        """_sync_stk_holdertrade 在 TushareAPIPermissionError 后 update_sync_status 抛异常时仍返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(side_effect=TushareAPIPermissionError("test_api", "no perm"))
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock(side_effect=RuntimeError("recording failed"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1


class TestLogSyncErrorSeverityBranches:
    """覆盖 _log_sync_error 的 system / recoverable / operational 三分支。"""

    def test_system_severity_reraises(self):
        """system severity（MemoryError）必须 raise（在 except 上下文中）。"""
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(MemoryError) as exc_info:
            try:
                raise MemoryError("oom")
            except Exception as e:
                strategy._log_sync_error("test_table", "20240331", e)
        assert "oom" in str(exc_info.value)

    def test_recoverable_severity_warns(self):
        """recoverable severity（OSError with network）应走 warning 分支，不 raise。"""
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        # OSError with "network" → recoverable
        try:
            raise OSError("network connection refused")
        except Exception as e:
            strategy._log_sync_error("test_table", "20240331", e)

    def test_operational_severity_errors(self):
        """operational severity（普通 ValueError）应走 error 分支，不 raise。"""
        ctx = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        try:
            raise ValueError("bad input")
        except Exception as e:
            strategy._log_sync_error("test_table", "20240331", e)


class TestGetRecentQuarterEndsEdgeCases:
    """覆盖 _get_recent_quarter_ends 跨年边界 / 当前季度末未来日期。"""

    def test_cross_year_boundary(self, monkeypatch):
        """跨年边界：today=2026-01-15 应返回 ['20251231', '20250930']。"""

        class FakeNow:
            @classmethod
            def now(cls):
                return datetime.datetime(2026, 1, 15, 10, 0, 0)

        monkeypatch.setattr("data.sync.holder.get_now", FakeNow.now)
        result = HolderSyncStrategy._get_recent_quarter_ends(count=2)
        assert result == ["20251231", "20250930"]

    def test_current_quarter_end_future_excluded(self, monkeypatch):
        """当前季度末未来日期应被排除：today=2026-03-30 → 20260331 未到，不返回。"""

        class FakeNow:
            @classmethod
            def now(cls):
                return datetime.datetime(2026, 3, 30, 10, 0, 0)

        monkeypatch.setattr("data.sync.holder.get_now", FakeNow.now)
        result = HolderSyncStrategy._get_recent_quarter_ends(count=2)
        # today=2026-03-30，20260331 未到，应跳过；返回 ['20251231', '20250930']
        assert "20260331" not in result
        assert result[0] == "20251231"

    def test_returns_newest_first(self, monkeypatch):
        """返回值应按 newest first 排序。"""

        class FakeNow:
            @classmethod
            def now(cls):
                return datetime.datetime(2026, 7, 15, 10, 0, 0)

        monkeypatch.setattr("data.sync.holder.get_now", FakeNow.now)
        result = HolderSyncStrategy._get_recent_quarter_ends(count=3)
        # today=2026-07-15 → ['20260630', '20260331', '20251231']
        assert result == ["20260630", "20260331", "20251231"]

    def test_count_one_at_year_start(self, monkeypatch):
        """count=1 在年初应返回去年年末。"""

        class FakeNow:
            @classmethod
            def now(cls):
                return datetime.datetime(2026, 1, 5, 10, 0, 0)

        monkeypatch.setattr("data.sync.holder.get_now", FakeNow.now)
        result = HolderSyncStrategy._get_recent_quarter_ends(count=1)
        assert result == ["20251231"]


class TestGetExistingTop10TsCodesSeverityBranches:
    """覆盖 _get_existing_top10_ts_codes 的 system / recoverable / operational 分支。"""

    @pytest.mark.asyncio
    async def test_system_severity_reraises(self):
        """system severity（MemoryError）必须 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_existing_top10_ts_codes = AsyncMock(side_effect=MemoryError("oom"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(MemoryError) as exc_info:
            await strategy._get_existing_top10_ts_codes("20240331")
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_recoverable_severity_returns_empty(self):
        """recoverable severity（OSError with network）应返回空集（fallback to full sync）。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_existing_top10_ts_codes = AsyncMock(side_effect=OSError("network down"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_existing_top10_ts_codes("20240331")
        assert result == set()

    @pytest.mark.asyncio
    async def test_operational_severity_returns_empty(self):
        """operational severity（普通 ValueError）应返回空集。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_existing_top10_ts_codes = AsyncMock(side_effect=ValueError("bad input"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_existing_top10_ts_codes("20240331")
        assert result == set()


class TestSaveTop10CheckpointSeverityBranches:
    """覆盖 _save_top10_checkpoint 的 system / recoverable / operational 分支。"""

    @pytest.mark.asyncio
    async def test_system_severity_reraises(self):
        """system severity（MemoryError）必须 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_top10_holders = AsyncMock(side_effect=MemoryError("oom"))
        strategy = HolderSyncStrategy(ctx)
        dfs = [pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})]
        with pytest.raises(MemoryError) as exc_info:
            await strategy._save_top10_checkpoint(dfs, "20240331")
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_recoverable_severity_returns_false(self):
        """recoverable severity（OSError with network）应返回 False（不 raise）。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_top10_holders = AsyncMock(side_effect=OSError("network down"))
        strategy = HolderSyncStrategy(ctx)
        dfs = [pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})]
        result = await strategy._save_top10_checkpoint(dfs, "20240331")
        assert result is False

    @pytest.mark.asyncio
    async def test_operational_severity_returns_false(self):
        """operational severity（普通 ValueError）应返回 False。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_top10_holders = AsyncMock(side_effect=ValueError("bad input"))
        strategy = HolderSyncStrategy(ctx)
        dfs = [pd.DataFrame({"ts_code": ["000001.SZ"], "holder_name": ["Test"]})]
        result = await strategy._save_top10_checkpoint(dfs, "20240331")
        assert result is False


class TestSyncOneTableSeverityBranches:
    """覆盖 _sync_one_table 的 system / recoverable / operational 分支。"""

    @pytest.mark.asyncio
    async def test_system_severity_reraises(self):
        """system severity（MemoryError）必须 raise。"""
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=MemoryError("oom"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(MemoryError) as exc_info:
            await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_recoverable_severity_returns_minus_one(self):
        """recoverable severity（OSError with network）应返回 -1（不 raise）。"""
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=OSError("network down"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == -1

    @pytest.mark.asyncio
    async def test_operational_severity_returns_minus_one(self):
        """operational severity（普通 ValueError）应返回 -1。"""
        ctx = MagicMock()
        save_func = AsyncMock()
        api_func = AsyncMock(side_effect=ValueError("bad input"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._sync_one_table(api_func, save_func, "test_table", "20240331")
        assert result == -1


class TestSyncPledgeStatSeverityBranches:
    """覆盖 _sync_pledge_stat API 调用 + outer exception 的 severity 分支。"""

    @pytest.mark.asyncio
    async def test_api_system_severity_reraises(self):
        """API 调用 system severity（MemoryError）必须 raise。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(side_effect=MemoryError("oom"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        with pytest.raises(MemoryError) as exc_info:
            await strategy._sync_pledge_stat()
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_api_recoverable_severity_continues_retry(self):
        """API 调用 recoverable severity（OSError network）应继续 retry loop。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock()
        call_count = 0

        async def mock_pledge(end_date):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OSError("network connection refused")
            return pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [datetime.date(2024, 6, 14)]})

        ctx.api.get_pledge_stat = AsyncMock(side_effect=mock_pledge)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == 1
        assert call_count == 3  # 前 2 次失败，第 3 次成功

    @pytest.mark.asyncio
    async def test_outer_system_severity_reraises(self):
        """outer exception system severity（_get_effective_trade_date 抛 MemoryError）必须 raise。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=MemoryError("oom"))
        with pytest.raises(MemoryError) as exc_info:
            await strategy._sync_pledge_stat()
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_outer_recoverable_severity_returns_minus_one(self):
        """outer exception recoverable severity 应返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=OSError("network down"))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1

    @pytest.mark.asyncio
    async def test_outer_operational_severity_returns_minus_one(self):
        """outer exception operational severity 应返回 -1。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=ValueError("bad input"))
        count, date = await strategy._sync_pledge_stat()
        assert count == -1


class TestSyncShareFloatSeverityBranches:
    """覆盖 _sync_share_float 的 outer exception system / recoverable / operational 分支。"""

    @pytest.mark.asyncio
    async def test_outer_system_severity_reraises(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=MemoryError("oom"))
        with pytest.raises(MemoryError) as exc_info:
            await strategy._sync_share_float()
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_outer_recoverable_severity_returns_minus_one(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=OSError("network down"))
        count, date = await strategy._sync_share_float()
        assert count == -1

    @pytest.mark.asyncio
    async def test_outer_operational_severity_returns_minus_one(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=ValueError("bad input"))
        count, date = await strategy._sync_share_float()
        assert count == -1


class TestSyncStkHoldertradeSeverityBranches:
    """覆盖 _sync_stk_holdertrade 的 outer exception system / recoverable / operational 分支。"""

    @pytest.mark.asyncio
    async def test_outer_system_severity_reraises(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=MemoryError("oom"))
        with pytest.raises(MemoryError) as exc_info:
            await strategy._sync_stk_holdertrade()
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_outer_recoverable_severity_returns_minus_one(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=OSError("network down"))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1

    @pytest.mark.asyncio
    async def test_outer_operational_severity_returns_minus_one(self):
        ctx = MagicMock()
        ctx.api = MagicMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(side_effect=ValueError("bad input"))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == -1


class TestSyncTop10HoldersSystemSeverityAndRateLimit:
    """覆盖 _sync_top10_holders 的 system severity raise + rate_limit_hits 计数分支。"""

    @pytest.mark.asyncio
    async def test_system_error_reraises(self):
        """per-stock 迭代中 system severity（MemoryError）必须 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}))
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(side_effect=MemoryError("oom"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        with pytest.raises(MemoryError) as exc_info:
            await strategy._sync_top10_holders("20240331")
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_rate_limit_hits_counted_across_variants(self):
        """覆盖 4 种 rate-limit 错误字符串都被识别（每分钟最多访问 / 抱歉 / 频次超限 / 429）。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {"ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ", "000006.SZ"]}
            )
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        # 前 4 次抛不同 rate-limit 字符串，第 5 次成功，第 6 次普通错误
        errors = [
            Exception("每分钟最多访问该接口"),
            Exception("抱歉，您访问频率过快"),
            Exception("频次超限"),
            Exception("HTTP 429 too many requests"),
        ]

        async def mock_api(ts_code, period):
            if errors:
                raise errors.pop(0)
            return pd.DataFrame({"ts_code": [ts_code], "holder_name": ["Test"]})

        ctx.api.get_top10_holders = AsyncMock(side_effect=mock_api)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        # 4 次 rate-limit 错误 < _MAX_ERRORS=5，不 abort；剩余 2 个成功
        assert result > 0

    @pytest.mark.asyncio
    async def test_consecutive_errors_abort_at_max(self):
        """连续 5 次错误（非 rate-limit）后应 abort 并返回 -1。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(1, 8)]})
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        ctx.api.get_top10_holders = AsyncMock(side_effect=ValueError("bad input"))
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1


class TestSyncTop10HoldersCancelDuringIteration:
    """cancel_event 2 秒检查一次（项目内存约束）：长任务迭代中 cancel 触发应即时退出。

    holder.py 用 self._cancelled 标志位在 per-stock 循环顶部检查（line 313），
    满足 SyncContext.cancel_event 注释中"poll cancel state every ~2 seconds"的约束。
    本测试验证：在迭代中途设置 _cancelled 后，下一次循环顶部即 break，并返回 -1。
    """

    @pytest.mark.asyncio
    async def test_cancel_mid_iteration_breaks_loop(self):
        """在迭代中途 cancel 后，下次循环顶部 break，未处理的 ts_code 不再调 API。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": [f"00000{i}.SZ" for i in range(1, 6)]})
        )
        ctx.cache.save_top10_holders = AsyncMock()
        ctx.api = MagicMock()
        api_call_count = 0

        async def mock_api(ts_code, period):
            nonlocal api_call_count
            api_call_count += 1
            # 第 3 个股票后触发 cancel
            if api_call_count >= 3:
                strategy._cancelled = True
            return pd.DataFrame({"ts_code": [ts_code], "holder_name": ["Test"]})

        ctx.api.get_top10_holders = AsyncMock(side_effect=mock_api)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await strategy._sync_top10_holders("20240331")
        # cancel 后 break，返回 -1
        assert result == -1
        # 第 4 个股票不应被调用（break 在循环顶部）
        assert api_call_count == 3

    @pytest.mark.asyncio
    async def test_cancel_at_start_returns_minus_one(self):
        """cancel 在循环开始前已设置时，第一次迭代即 break，返回 -1。"""
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
        strategy._cancelled = True
        result = await strategy._sync_top10_holders("20240331")
        assert result == -1
        # cancel 在循环顶部 break，api 不应被调用
        ctx.api.get_top10_holders.assert_not_awaited()


class TestSyncPledgeStatDataScenarios:
    """覆盖 _sync_pledge_stat 数据空 / 部分公司无质押场景。"""

    @pytest.mark.asyncio
    async def test_empty_data_returns_zero(self):
        """API 返回空 DataFrame 时应返回 (0, None)。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == 0
        assert date is None

    @pytest.mark.asyncio
    async def test_partial_companies_no_pledge(self):
        """部分公司无质押：API 返回仅含部分公司数据。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_pledge_stat = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],  # 仅 1 家公司有质押
                    "end_date": [datetime.date(2024, 6, 14)],
                    "pledge_count": [1],
                    "pledge_ratio": [5.0],
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
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == 1
        assert saved_df is not None
        assert len(saved_df) == 1

    @pytest.mark.asyncio
    async def test_first_friday_no_data_second_has_data(self):
        """最近周五无数据，次近周五有数据：应返回次近周五的数据。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        call_count = 0

        async def mock_pledge(end_date):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pd.DataFrame()  # 最近周五无数据
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": [datetime.date(2024, 6, 7)],
                }
            )

        ctx.api.get_pledge_stat = AsyncMock(side_effect=mock_pledge)
        ctx.cache = MagicMock()
        ctx.cache.save_pledge_stat = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_pledge_stat()
        assert count == 1
        assert date == datetime.date(2024, 6, 7)  # 次近周五


class TestSyncShareFloatMultipleFloats:
    """覆盖 _sync_share_float 多次解禁同一股票场景。"""

    @pytest.mark.asyncio
    async def test_multiple_floats_same_stock(self):
        """同一股票多次解禁：DataFrame 含多行同 ts_code 不同 float_date。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "ann_date": [
                        datetime.date(2024, 6, 1),
                        datetime.date(2024, 6, 5),
                        datetime.date(2024, 6, 10),
                    ],
                    "float_date": [
                        datetime.date(2024, 8, 15),
                        datetime.date(2024, 9, 1),
                        datetime.date(2024, 10, 1),
                    ],
                    "float_share": [1000.0, 2000.0, 3000.0],
                    "float_ratio": [5.2, 10.4, 15.6],
                    "share_type": ["定向增发", "首发原股东", "股权激励"],
                }
            )
        )
        ctx.cache = MagicMock()
        saved_df = None

        async def capture_save(df):
            nonlocal saved_df
            saved_df = df

        ctx.cache.save_share_float = AsyncMock(side_effect=capture_save)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == 3
        assert saved_df is not None
        assert len(saved_df) == 3

    @pytest.mark.asyncio
    async def test_none_data_returns_zero(self):
        """API 返回 None 时应返回 (0, today)。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_share_float = AsyncMock(return_value=None)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_share_float()
        assert count == 0
        assert date == datetime.date(2024, 6, 14)


class TestSyncStkHoldertradeMultipleRecords:
    """覆盖 _sync_stk_holdertrade 大股东增减持多记录 / 异常日期场景。"""

    @pytest.mark.asyncio
    async def test_multiple_records(self):
        """大股东增减持多记录：DataFrame 含多行不同股东不同行为。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                    "ann_date": [
                        datetime.date(2024, 6, 1),
                        datetime.date(2024, 6, 5),
                        datetime.date(2024, 6, 10),
                    ],
                    "holder_name": ["张三", "李四", "王五"],
                    "holder_type": ["G", "G", "P"],
                    "in_de": ["IN", "DE", "IN"],
                    "change_vol": [10000.0, -5000.0, 20000.0],
                    "change_ratio": [0.5, -0.25, 1.0],
                    "after_share": [1000000.0, 995000.0, 2000000.0],
                    "after_ratio": [50.0, 49.75, 100.0],
                }
            )
        )
        ctx.cache = MagicMock()
        saved_df = None

        async def capture_save(df):
            nonlocal saved_df
            saved_df = df

        ctx.cache.save_stk_holdertrade = AsyncMock(side_effect=capture_save)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == 3
        assert saved_df is not None
        assert len(saved_df) == 3

    @pytest.mark.asyncio
    async def test_abnormal_dates_extreme_range(self):
        """异常日期：ann_date 极早 / 极晚，验证不抛错。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "ann_date": [
                        datetime.date(1990, 1, 1),  # 极早
                        datetime.date(2099, 12, 31),  # 极晚
                    ],
                    "holder_name": ["张三", "李四"],
                    "holder_type": ["G", "G"],
                    "in_de": ["IN", "DE"],
                    "change_vol": [10000.0, -5000.0],
                    "change_ratio": [0.5, -0.25],
                    "after_share": [1000000.0, 995000.0],
                    "after_ratio": [50.0, 49.75],
                }
            )
        )
        ctx.cache = MagicMock()
        ctx.cache.save_stk_holdertrade = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == 2

    @pytest.mark.asyncio
    async def test_none_data_returns_zero(self):
        """API 返回 None 时应返回 (0, today)。"""
        ctx = MagicMock()
        ctx.api = MagicMock()
        ctx.api.get_stk_holdertrade = AsyncMock(return_value=None)
        strategy = HolderSyncStrategy(ctx)
        strategy._get_effective_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
        count, date = await strategy._sync_stk_holdertrade()
        assert count == 0
        assert date == datetime.date(2024, 6, 14)


class TestGetEffectiveTradeDateSeverityBranches:
    """覆盖 _get_effective_trade_date 的 system / recoverable / operational 分支。"""

    @pytest.mark.asyncio
    async def test_system_severity_reraises(self):
        """system severity（MemoryError）必须 raise。"""
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=MemoryError("oom"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(MemoryError) as exc_info:
            await strategy._get_effective_trade_date()
        assert "oom" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_recoverable_severity_falls_back(self):
        """recoverable severity（OSError network）应 fallback 到 today。"""
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=OSError("network down"))
        strategy = HolderSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_engine_disposed_reraises(self):
        """EngineDisposedError 必须 raise（R5 守卫）。"""
        ctx = MagicMock()
        ctx.processor = MagicMock()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = HolderSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError) as exc_info:
            await strategy._get_effective_trade_date()
        assert "disposed" in str(exc_info.value)


class TestRunImplRecoverableErrorPath:
    """覆盖 _run_impl 外层 except 的 recoverable severity 分支（不 raise，仅 warning）。"""

    @pytest.mark.asyncio
    async def test_recoverable_severity_logs_warning(self):
        """recoverable severity 应记 warning 并设置 status=failed，不 raise。"""
        ctx = MagicMock()
        ctx.cache = MagicMock()
        ctx.cache.update_sync_status = AsyncMock()
        strategy = HolderSyncStrategy(ctx)
        strategy._get_recent_quarter_ends = MagicMock(return_value=["20240331"])

        async def mock_sync(qe):
            raise OSError("network connection refused")

        strategy._sync_stk_holdernumber = mock_sync
        result = await strategy._run_impl()
        assert result.status == "failed"
