import pytest
import datetime
from unittest.mock import MagicMock, AsyncMock
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


class TestHistoricalSyncCancel:
    @pytest.mark.asyncio
    async def test_cancel(self):
        ctx = make_ctx()
        strategy = HistoricalSyncStrategy(ctx)
        await strategy.cancel()
        assert strategy._shutdown_event.is_set()


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
        assert result.status in ("cancelled", "failed", "success")

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
                datetime.date(2024, 6, 14): {"score": 50, "expected_base": 5000, "issues": ["low count"]},
                datetime.date(2024, 6, 13): {"score": 90, "expected_base": 5000, "issues": []},
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


class TestHistoricalSyncConstants:
    def test_synced_tables(self):
        assert "daily_quotes" in HistoricalSyncStrategy.SYNCED_TABLES
        assert "daily_indicators" in HistoricalSyncStrategy.SYNCED_TABLES
        assert len(HistoricalSyncStrategy.SYNCED_TABLES) >= 10

    def test_core_resume_tables(self):
        assert "daily_quotes" in HistoricalSyncStrategy.CORE_RESUME_TABLES
        assert "daily_indicators" in HistoricalSyncStrategy.CORE_RESUME_TABLES
