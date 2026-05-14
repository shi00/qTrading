import asyncio

import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
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
        strategy.cancel()
        assert strategy._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_cancel_clears_and_sets(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        strategy._shutdown_event.clear()
        assert not strategy._shutdown_event.is_set()
        strategy.cancel()
        assert strategy._shutdown_event.is_set()

    def test_cancel_no_event_loop_no_raise(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        with patch.object(type(strategy), "_shutdown_event", new_callable=PropertyMock) as mock_evt:
            mock_evt.side_effect = RuntimeError("no event loop")
            strategy.cancel()


class TestFinancialSyncGetEffectiveTradeDate:
    @pytest.mark.asyncio
    async def test_with_date_object(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_with_string_date(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value="20240614")
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_with_datetime_object(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(
            return_value=datetime.datetime(2024, 6, 14, 15, 0)
        )
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)
        assert result == datetime.date(2024, 6, 14)

    @pytest.mark.asyncio
    async def test_exception_fallback(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(side_effect=Exception("error"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)

    @pytest.mark.asyncio
    async def test_none_return_falls_back_to_today(self):
        ctx = make_ctx()
        ctx.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy._get_effective_trade_date()
        assert isinstance(result, datetime.date)


class TestFinancialSyncRunModes:
    @pytest.mark.asyncio
    async def test_full_sync_with_periods(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(periods=["20240331", "20231231"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_cancelled_during_run(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        strategy._shutdown_event.set()
        result = await strategy.run(force=True)
        assert result.status in ("cancelled", "success")

    @pytest.mark.asyncio
    async def test_run_exception(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=RuntimeError("unexpected"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_incremental_with_disclosure_dates(self):
        ctx = make_ctx()
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": datetime.datetime(2024, 6, 1)})
        ctx.api.get_disclosure_date = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240331"],
                    "actual_date": ["20240430"],
                }
            )
        )
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None


class TestFinancialSyncFetchComprehensive:
    @pytest.mark.asyncio
    async def test_fetch_with_data(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert isinstance(df, pd.DataFrame)
        assert "ts_code" in df.columns
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

    @pytest.mark.asyncio
    async def test_core_table_exception_logs_warning(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("income API failed"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("balance API failed"))
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.logger") as mock_logger:
            df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
            warning_calls = [c for c in mock_logger.warning.call_args_list if "Core table" in str(c)]
            assert len(warning_calls) >= 2


class TestFinancialSyncRepair:
    @pytest.mark.asyncio
    async def test_repair_empty(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data([])
        assert result == 0

    @pytest.mark.asyncio
    async def test_repair_with_codes_saves_merged_df(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_sync_request_delay.return_value = 0
            result = await strategy.repair_financial_data(["000001.SZ"])
            assert isinstance(result, int)
            ctx.cache.save_financial_reports.assert_awaited()

    @pytest.mark.asyncio
    async def test_repair_returns_actual_saved_count(self):
        ctx = make_ctx()
        ctx.cache.save_financial_reports = AsyncMock(return_value=5)
        ctx.cache.save_fina_mainbz = AsyncMock(return_value=2)
        ctx.cache.save_fina_audit = AsyncMock(return_value=1)
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_sync_request_delay.return_value = 0
            result = await strategy.repair_financial_data(["000001.SZ"])
            assert result > 0

    @pytest.mark.asyncio
    async def test_repair_empty_data_does_not_save(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(return_value=None)
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_sync_request_delay.return_value = 0
            result = await strategy.repair_financial_data(["000001.SZ"])
            ctx.cache.save_financial_reports.assert_not_awaited()
            assert result == 0

    @pytest.mark.asyncio
    async def test_repair_fills_missing_schema_cols_with_none(self):
        from data.constants import FINANCIAL_REPORT_SCHEMA_COLS

        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "revenue": [100.0]})
        )
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_sync_request_delay.return_value = 0
            await strategy.repair_financial_data(["000001.SZ"])
            saved_df = ctx.cache.save_financial_reports.call_args[0][0]
            for col in FINANCIAL_REPORT_SCHEMA_COLS:
                assert col in saved_df.columns, f"Missing column: {col}"

    @pytest.mark.asyncio
    async def test_repair_saves_only_schema_cols(self):
        from data.constants import FINANCIAL_REPORT_SCHEMA_COLS

        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_sync_request_delay.return_value = 0
            await strategy.repair_financial_data(["000001.SZ"])
            saved_df = ctx.cache.save_financial_reports.call_args[0][0]
            assert list(saved_df.columns) == FINANCIAL_REPORT_SCHEMA_COLS


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


class TestFinancialSyncGatherTolerance:
    @pytest.mark.asyncio
    async def test_batch_gather_continues_on_single_failure(self):
        ctx = make_ctx()
        FinancialSyncStrategy(ctx)

        async def success_task():
            return {"saved": 5}

        async def fail_task():
            raise RuntimeError("API timeout")

        results = await asyncio.gather(success_task(), fail_task(), return_exceptions=True)
        saved = 0
        for r in results:
            if isinstance(r, Exception):
                continue
            saved += r["saved"]  # type: ignore[index]
        assert saved == 5

    @pytest.mark.asyncio
    async def test_batch_gather_all_fail_no_crash(self):
        import asyncio

        async def fail_task():
            raise RuntimeError("API error")

        results = await asyncio.gather(fail_task(), fail_task(), return_exceptions=True)
        assert all(isinstance(r, Exception) for r in results)
