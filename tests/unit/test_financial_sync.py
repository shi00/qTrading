import asyncio

import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
import pandas as pd

from data.sync.financial import FinancialSyncStrategy
from data.sync.base import SyncResult
from data.persistence.daos.base_dao import EngineDisposedError

pytestmark = pytest.mark.unit


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
        return_value=pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20240331"],
                "total_assets": [1000.0],
            }
        )
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
    # Inject zero delay to avoid asyncio.sleep blocking in tests
    ctx.request_delay_provider = lambda is_heavy: 0.0
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
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": yesterday})
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
        await strategy.repair_financial_data(["000001.SZ"])
        saved_df = ctx.cache.save_financial_reports.call_args[0][0]
        for col in FINANCIAL_REPORT_SCHEMA_COLS:
            assert col in saved_df.columns, f"Missing column: {col}"

    @pytest.mark.asyncio
    async def test_repair_saves_only_schema_cols(self):
        from data.constants import FINANCIAL_REPORT_SCHEMA_COLS

        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
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
                setattr(
                    ctx.api,
                    cfg["api"],
                    AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})),
                )
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


class TestFinancialSyncCancelActiveTasks:
    @pytest.mark.asyncio
    async def test_cancel_cancels_active_tasks(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        mock_task = MagicMock()
        mock_task.done.return_value = False
        strategy._active_tasks = {mock_task}
        strategy.cancel()
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_skips_done_tasks(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        mock_task = MagicMock()
        mock_task.done.return_value = True
        strategy._active_tasks = {mock_task}
        strategy.cancel()
        mock_task.cancel.assert_not_called()


class TestFinancialSyncCancelledError:
    @pytest.mark.asyncio
    async def test_cancelled_error_reraises(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=asyncio.CancelledError())
        strategy = FinancialSyncStrategy(ctx)
        with pytest.raises(asyncio.CancelledError):
            await strategy.run(force=True)


class TestFinancialSyncIncompleteStocks:
    @pytest.mark.asyncio
    async def test_incomplete_stocks_removed_from_synced(self):
        ctx = make_ctx()
        ctx.cache.get_completed_step4_stocks = AsyncMock(return_value={"000001.SZ"})
        ctx.cache.get_incomplete_financial_stocks = AsyncMock(return_value={"000001.SZ"})
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None


class TestFinancialSyncAllStocksSynced:
    @pytest.mark.asyncio
    async def test_all_synced_with_progress_callback(self):
        ctx = make_ctx()
        ctx.cache.get_completed_step4_stocks = AsyncMock(return_value={"000001.SZ", "000002.SZ"})
        ctx.cache.get_incomplete_financial_stocks = AsyncMock(return_value=set())
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True, progress_callback=progress_cb)
        assert result is not None
        progress_cb.assert_called_once()


class TestFinancialSyncFullSyncErrorPaths:
    @pytest.mark.asyncio
    async def test_fetch_runtime_error_marks_incomplete(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("API failed"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("API failed"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=RuntimeError("API failed"))
        ctx.api.get_cashflow = AsyncMock(side_effect=RuntimeError("API failed"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_attribute_error_caught_by_outer(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=AttributeError("bad attr"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_empty_data_not_marked_complete_allows_retry(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(return_value=None)
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None
        ctx.cache.mark_stock_step4_completed.assert_not_awaited()


class TestFinancialSyncFullSyncProgress:
    @pytest.mark.asyncio
    async def test_progress_callback_with_enough_stocks(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"00000{i}.SZ" for i in range(10)],
                    "list_status": ["L"] * 10,
                }
            )
        )
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True, progress_callback=progress_cb)
        assert result is not None

    @pytest.mark.asyncio
    async def test_aux_table_status_updated(self):
        ctx = make_ctx()
        ctx.api.get_fina_mainbz = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"]})
        )
        ctx.api.get_fina_audit = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"]})
        )
        ctx.cache.save_fina_mainbz = AsyncMock(return_value=5)
        ctx.cache.save_fina_audit = AsyncMock(return_value=3)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None


class TestFinancialSyncIncrementalPaths:
    @pytest.mark.asyncio
    async def test_incremental_shutdown_event_set(self):
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)

        async def set_shutdown_and_return(*args, **kwargs):
            strategy._shutdown_event.set()
            return {"last_sync_date": datetime.datetime(2024, 6, 1)}

        ctx.cache.get_sync_status = AsyncMock(side_effect=set_shutdown_and_return)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_date_parse_string(self):
        ctx = make_ctx()
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": "2024-06-01 00:00:00"})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_date_parse_fallback(self):
        ctx = make_ctx()
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": "invalid-date"})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_no_dates_to_sync(self):
        ctx = make_ctx()
        now = datetime.datetime.now()
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": now})
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_with_progress_callback(self):
        ctx = make_ctx()
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": datetime.datetime(2024, 6, 1)})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(progress_callback=progress_cb)
        assert result is not None


class TestFinancialSyncIncrementalWithDisclosure:
    @pytest.mark.asyncio
    async def test_incremental_with_aux_updates(self):
        ctx = make_ctx()
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": yesterday})
        ctx.api.get_disclosure_date = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240331"],
                    "actual_date": ["20240430"],
                }
            )
        )
        ctx.api.get_fina_mainbz = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"]})
        )
        ctx.api.get_fina_audit = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"]})
        )
        ctx.cache.save_fina_mainbz = AsyncMock(return_value=5)
        ctx.cache.save_fina_audit = AsyncMock(return_value=3)
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_max_batch_rows.return_value = 100
            mock_cfg.get_sync_max_concurrent_heavy.return_value = 5
            result = await strategy.run()
            assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_fetch_error_continues(self):
        ctx = make_ctx()
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": yesterday})
        ctx.api.get_disclosure_date = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240331"],
                    "actual_date": ["20240430"],
                }
            )
        )
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("API error"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("API error"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=RuntimeError("API error"))
        ctx.api.get_cashflow = AsyncMock(side_effect=RuntimeError("API error"))
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.ConfigHandler") as mock_cfg:
            mock_cfg.get_max_batch_rows.return_value = 100
            mock_cfg.get_sync_max_concurrent_heavy.return_value = 5
            result = await strategy.run()
            assert result is not None


class TestFinancialSyncCorporateActionsErrorPaths:
    @pytest.mark.asyncio
    async def test_permission_denied_handling(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(
                ctx.api,
                cfg["api"],
                AsyncMock(side_effect=Exception("permission denied")),
            )
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])

    @pytest.mark.asyncio
    async def test_jifen_denied_handling(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(ctx.api, cfg["api"], AsyncMock(side_effect=Exception("积分不足")))
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])

    @pytest.mark.asyncio
    async def test_general_error_handling(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(ctx.api, cfg["api"], AsyncMock(side_effect=Exception("network error")))
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])

    @pytest.mark.asyncio
    async def test_shutdown_during_corporate_actions(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(
                ctx.api,
                cfg["api"],
                AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})),
            )
        strategy = FinancialSyncStrategy(ctx)
        strategy._shutdown_event.set()
        await strategy._sync_corporate_actions_by_date(["20240614"])

    @pytest.mark.asyncio
    async def test_progress_callback_every_10_days(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(
                ctx.api,
                cfg["api"],
                AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})),
            )
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        strategy._shutdown_event.clear()
        dates = [f"202406{d:02d}" for d in range(1, 12)]
        await strategy._sync_corporate_actions_by_date(dates, progress_callback=progress_cb)
        assert progress_cb.call_count >= 2

    @pytest.mark.asyncio
    async def test_no_save_func_for_unknown_table(self):
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(
                ctx.api,
                cfg["api"],
                AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})),
            )
        ctx.cache.save_fina_forecast = AsyncMock(return_value=None)
        ctx.cache.save_dividend = AsyncMock(return_value=None)
        ctx.cache.save_repurchase = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])


class TestFinancialSyncFetchAuxPaths:
    @pytest.mark.asyncio
    async def test_fetch_aux_save_returns_none_uses_len(self):
        ctx = make_ctx()
        ctx.cache.save_fina_mainbz = AsyncMock(return_value=None)
        ctx.cache.save_fina_audit = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"]})
        )
        ctx.api.get_fina_audit = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"]})
        )
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert aux["mainbz"] > 0
        assert aux["audit"] > 0

    @pytest.mark.asyncio
    async def test_fetch_aux_permission_error(self):
        ctx = make_ctx()
        ctx.api.get_fina_mainbz = AsyncMock(side_effect=Exception("permission denied"))
        ctx.api.get_fina_audit = AsyncMock(side_effect=Exception("积分不足"))
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert aux["mainbz"] == 0
        assert aux["audit"] == 0

    @pytest.mark.asyncio
    async def test_aux_exception_returns_zero(self):
        ctx = make_ctx()
        ctx.api.get_fina_mainbz = AsyncMock(side_effect=RuntimeError("mainbz failed"))
        ctx.api.get_fina_audit = AsyncMock(side_effect=RuntimeError("audit failed"))
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert aux["mainbz"] == 0
        assert aux["audit"] == 0

    @pytest.mark.asyncio
    async def test_fetch_outer_exception_returns_zero_aux(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("outer fail"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("outer fail"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=RuntimeError("outer fail"))
        ctx.api.get_cashflow = AsyncMock(side_effect=RuntimeError("outer fail"))
        ctx.api.get_fina_mainbz = AsyncMock(side_effect=RuntimeError("outer fail"))
        ctx.api.get_fina_audit = AsyncMock(side_effect=RuntimeError("outer fail"))
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert df is None
        assert aux["mainbz"] == 0
        assert aux["audit"] == 0


class TestFinancialSyncRepairPaths:
    @pytest.mark.asyncio
    async def test_repair_with_progress_callback(self):
        ctx = make_ctx()
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data(["000001.SZ"], progress_callback=progress_cb)
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_repair_exception_continues(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("API error"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("API error"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=RuntimeError("API error"))
        ctx.api.get_cashflow = AsyncMock(side_effect=RuntimeError("API error"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data(["000001.SZ"])
        assert isinstance(result, int)


class TestFinancialDedupWithAnnDate:
    @pytest.mark.asyncio
    async def test_dedup_prefers_later_ann_date(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 3,
                    "end_date": ["20240331", "20240331", "20240331"],
                    "ann_date": ["20240425", "20240430", "20240428"],
                    "revenue": [100.0, 300.0, 200.0],
                }
            )
        )
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        df, _aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert len(df) == 1
        assert df.iloc[0]["revenue"] == 300.0

    @pytest.mark.asyncio
    async def test_dedup_without_ann_date_fallback(self):
        ctx = make_ctx()
        ctx.api.get_balancesheet = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 2,
                    "end_date": ["20240331", "20240331"],
                    "total_assets": [1000.0, 2000.0],
                }
            )
        )
        ctx.api.get_income = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        df, _aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert len(df) == 1
        assert df.iloc[0]["total_assets"] == 2000.0

    @pytest.mark.asyncio
    async def test_dedup_multiple_end_dates(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 4,
                    "end_date": ["20231231", "20231231", "20240331", "20240331"],
                    "ann_date": ["20240420", "20240425", "20240428", "20240430"],
                    "revenue": [100.0, 150.0, 200.0, 250.0],
                }
            )
        )
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        df, _aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert len(df) == 2
        q1_row = df[df["end_date"] == "20240331"].iloc[0]
        assert q1_row["revenue"] == 250.0
        q4_row = df[df["end_date"] == "20231231"].iloc[0]
        assert q4_row["revenue"] == 150.0

    @pytest.mark.asyncio
    async def test_dedup_with_update_flag(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 3,
                    "end_date": ["20240331", "20240331", "20240331"],
                    "ann_date": ["20240428", "20240428", "20240428"],
                    "update_flag": ["0", "1", "0"],
                    "revenue": [100.0, 150.0, 200.0],
                }
            )
        )
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        df, _aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert len(df) == 1
        assert df.iloc[0]["revenue"] == 150.0

    @pytest.mark.asyncio
    async def test_dedup_ann_date_and_update_flag_combined(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 4,
                    "end_date": ["20240331", "20240331", "20240331", "20240331"],
                    "ann_date": ["20240428", "20240428", "20240428", "20240430"],
                    "update_flag": ["0", "1", "0", "1"],
                    "revenue": [100.0, 150.0, 200.0, 250.0],
                }
            )
        )
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        df, _aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert len(df) == 1
        assert df.iloc[0]["revenue"] == 250.0


class TestPeakDisclosureSeason:
    """Tests for peak disclosure season detection and adjustments."""

    def test_is_peak_disclosure_season_april(self):
        from data.sync.financial import _is_peak_disclosure_season

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 4, 15)
            assert _is_peak_disclosure_season() is True

    def test_is_peak_disclosure_season_august(self):
        from data.sync.financial import _is_peak_disclosure_season

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 8, 20)
            assert _is_peak_disclosure_season() is True

    def test_is_peak_disclosure_season_october(self):
        from data.sync.financial import _is_peak_disclosure_season

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 10, 25)
            assert _is_peak_disclosure_season() is True

    def test_is_not_peak_disclosure_season_january(self):
        from data.sync.financial import _is_peak_disclosure_season

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 1, 15)
            assert _is_peak_disclosure_season() is False

    def test_is_not_peak_disclosure_season_june(self):
        from data.sync.financial import _is_peak_disclosure_season

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 15)
            assert _is_peak_disclosure_season() is False

    def test_get_seasonal_adjustments_normal_season(self):
        from data.sync.financial import _get_seasonal_adjustments

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 15)
            factor, multiplier = _get_seasonal_adjustments()
            assert factor == 1
            assert multiplier == 1.0

    def test_get_seasonal_adjustments_peak_season(self):
        from data.sync.financial import _get_seasonal_adjustments

        with patch("data.sync.financial.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 4, 15)
            factor, multiplier = _get_seasonal_adjustments()
            assert factor == 2
            assert multiplier == 2.0


class TestFinancialSyncEngineDisposed:
    """Tests for EngineDisposedError graceful degradation."""

    @pytest.mark.asyncio
    async def test_engine_disposed_in_run(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result.status == "failed"
        assert any("Engine disposed" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_engine_disposed_in_fetch_comprehensive(self):
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = FinancialSyncStrategy(ctx)
        # EngineDisposedError is caught by gather_return_exceptions_propagating_cancel
        # and returned as an exception result; other API calls may still succeed
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        # The income table failed but other tables may still produce data
        assert isinstance(df, (type(None), pd.DataFrame))


class TestFinancialSyncClassifyError:
    """Tests for classify_error / classify_severity integration in run()."""

    @pytest.mark.asyncio
    async def test_system_error_reraises(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=MemoryError("OOM"))
        strategy = FinancialSyncStrategy(ctx)
        with pytest.raises(MemoryError):
            await strategy.run(force=True)

    @pytest.mark.asyncio
    async def test_recoverable_error_status_failed(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=ConnectionError("network reset"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_operational_error_status_failed(self):
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=ValueError("bad value"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result.status == "failed"
        assert len(result.errors) > 0


class TestFinancialSyncCounterLockLoopLocal:
    """Verify _counter_lock uses get_loop_local (R11 red line) so the sync
    works across different event loops without raising
    'RuntimeError: ... bound to a different event loop'."""

    def test_counter_lock_works_across_event_loops(self):
        def _run_sync_in_fresh_loop():
            ctx = make_ctx()
            strategy = FinancialSyncStrategy(ctx)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(strategy.run(force=True))
            finally:
                loop.close()
                asyncio.set_event_loop(None)

        # First run in a fresh loop - binds a lock instance to that loop
        # via get_loop_local("financial_counter_lock", ...).
        result1 = _run_sync_in_fresh_loop()
        assert result1 is not None

        # Second run in a brand-new event loop. A direct asyncio.Lock() bound
        # to the first loop would raise RuntimeError here; get_loop_local
        # gives each loop its own instance.
        try:
            result2 = _run_sync_in_fresh_loop()
        except RuntimeError as exc:
            if "bound" in str(exc) or "event loop" in str(exc):
                pytest.fail(f"_counter_lock not loop-local: {exc}")
            raise

        assert result2 is not None
        assert isinstance(result2, SyncResult)
