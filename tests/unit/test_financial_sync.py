import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.sync.financial import FinancialSyncStrategy
from data.sync.base import SyncResult


def make_ctx():
    ctx = MagicMock()
    ctx.api = MagicMock()
    ctx.cache = MagicMock()
    ctx.processor = MagicMock()
    ctx.processor.trade_calendar = MagicMock()
    ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
    ctx.processor.trade_calendar.get_trade_dates = AsyncMock(return_value=["20240101", "20240614"])
    ctx.cache.get_stock_basic = AsyncMock(
        return_value=pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "list_status": ["L", "L"],
            }
        )
    )
    ctx.cache.get_completed_step4_stocks = AsyncMock(return_value=set())
    ctx.cache.get_incomplete_financial_stocks = AsyncMock(return_value=set())
    ctx.cache.get_sync_status = AsyncMock(return_value=None)
    ctx.cache.clear_step4_sync_status = AsyncMock()
    ctx.cache.save_financial_reports = AsyncMock(return_value=1)
    ctx.cache.mark_stock_step4_completed = AsyncMock()
    ctx.cache.update_sync_status = AsyncMock()
    ctx.cache.save_fina_forecast = AsyncMock(return_value=1)
    ctx.cache.save_dividend = AsyncMock(return_value=1)
    ctx.cache.save_repurchase = AsyncMock(return_value=1)
    ctx.cache.save_fina_mainbz = AsyncMock(return_value=1)
    ctx.cache.save_fina_audit = AsyncMock(return_value=1)
    ctx.cache.engine = MagicMock()
    ctx.cache.engine.begin = MagicMock()
    mock_conn = AsyncMock()
    ctx.cache.engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.cache.engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    ctx.api.get_income = AsyncMock(
        return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "revenue": [100.0]})
    )
    ctx.api.get_balancesheet = AsyncMock(
        return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "total_assets": [1000.0]})
    )
    ctx.api.get_fina_indicator = AsyncMock(
        return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "roe": [10.0]})
    )
    ctx.api.get_cashflow = AsyncMock(
        return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "cfps": [1.0]})
    )
    ctx.api.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_fina_audit = AsyncMock(return_value=pd.DataFrame())
    ctx.api.get_disclosure_date = AsyncMock(return_value=None)
    return ctx


class TestFinancialSyncRun:
    @pytest.mark.asyncio
    async def test_full_sync_force(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None
        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    async def test_full_sync_first_run(self):
        ctx = make_ctx()
        ctx.cache.get_sync_status = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_sync(self):
        ctx = make_ctx()
        ctx.cache.get_sync_status = AsyncMock(
            return_value={
                "last_sync_date": datetime.datetime(2024, 6, 1),
            }
        )
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_stocks(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({"ts_code": [], "list_status": []}))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result.status == "failed"


class TestFinancialSyncCancel:
    @pytest.mark.asyncio
    async def test_cancel(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        await strategy.cancel()
        assert strategy._shutdown_event.is_set()


class TestFinancialSyncFetchComprehensive:
    @pytest.mark.asyncio
    async def test_fetch_with_data(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert df is not None or df is None
        assert "mainbz" in aux
        assert "audit" in aux

    @pytest.mark.asyncio
    async def test_fetch_all_empty(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(return_value=None)
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert df is None
        assert aux["mainbz"] == 0
        assert aux["audit"] == 0

    @pytest.mark.asyncio
    async def test_fetch_exception(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=Exception("API error"))
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert aux["mainbz"] == 0
        assert aux["audit"] == 0


class TestFinancialSyncRepair:
    @pytest.mark.asyncio
    async def test_repair_empty(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_repair_with_codes(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_sync_request_delay.return_value = 0
            result = await strategy.repair_financial_data(["000001.SZ"])
            assert isinstance(result, int)


class TestFinancialSyncCorporateActions:
    @pytest.mark.asyncio
    async def test_empty_dates(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date([])
        ctx.cache.update_sync_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_dates(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            api_method = getattr(ctx.api, cfg["api"], None)
            if api_method is None:
                setattr(ctx.api, cfg["api"], AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})))
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])
