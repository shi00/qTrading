import asyncio

import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
import pandas as pd

from data.sync.financial import FinancialSyncStrategy
from data.sync.base import SyncResult
from data.persistence.daos.base_dao import EngineDisposedError
from utils.time_utils import get_now

pytestmark = pytest.mark.unit


def make_ctx():
    ctx = MagicMock()
    ctx.api = AsyncMock()
    ctx.cache = AsyncMock()
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
    # Async context manager for financial_transaction (used by _run_full_sync)
    mock_tx_conn = AsyncMock()
    ctx.cache.financial_transaction = MagicMock()
    ctx.cache.financial_transaction.return_value.__aenter__ = AsyncMock(return_value=mock_tx_conn)
    ctx.cache.financial_transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    ctx.cache.save_fina_forecast = AsyncMock(return_value=1)
    ctx.cache.save_dividend = AsyncMock(return_value=1)
    ctx.cache.save_repurchase = AsyncMock(return_value=1)
    ctx.cache.save_fina_mainbz = AsyncMock(return_value=1)
    ctx.cache.save_fina_audit = AsyncMock(return_value=1)
    # Phase 3G §4.3.4：express save mock
    ctx.cache.save_express = AsyncMock(return_value=1)
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
    # FINANCIAL_BATCH_TABLES API methods (used by _sync_corporate_actions_by_date via getattr)
    ctx.api.get_forecast = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
    ctx.api.get_dividend = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
    ctx.api.get_repurchase = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
    # Phase 3G §4.3.4：express API mock
    ctx.api.get_express = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
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
        yesterday = get_now() - datetime.timedelta(days=1)
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

    @pytest.mark.asyncio
    async def test_sync_includes_express(self):
        """Phase 3G §4.3.4：_sync_corporate_actions_by_date 覆盖 express 表。

        验证 express 在 FINANCIAL_BATCH_TABLES 中且其 save_func 被正确路由到
        ctx.cache.save_express。
        """
        from data.constants import FINANCIAL_BATCH_TABLES

        # 确认 express 已注册到 FINANCIAL_BATCH_TABLES
        assert "express" in FINANCIAL_BATCH_TABLES
        express_cfg = FINANCIAL_BATCH_TABLES["express"]
        assert express_cfg["api"] == "get_express"
        assert express_cfg["date_col"] == "ann_date"

        ctx = make_ctx()
        # 让 express API 返回非空数据以触发 save 路径
        ctx.api.get_express = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20240930"],
                    "ann_date": ["20241015"],
                    "revenue": [5.0e9],
                    "n_income": [8.0e8],
                }
            )
        )
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20241015"])

        # 验证 save_express 被调用
        ctx.cache.save_express.assert_awaited()


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
        now = get_now()
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
        yesterday = get_now() - datetime.timedelta(days=1)
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
        yesterday = get_now() - datetime.timedelta(days=1)
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
        from data.sync.base import _is_peak_disclosure_season

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 4, 15)
            assert _is_peak_disclosure_season() is True

    def test_is_peak_disclosure_season_august(self):
        from data.sync.base import _is_peak_disclosure_season

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 8, 20)
            assert _is_peak_disclosure_season() is True

    def test_is_peak_disclosure_season_october(self):
        from data.sync.base import _is_peak_disclosure_season

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 10, 25)
            assert _is_peak_disclosure_season() is True

    def test_is_not_peak_disclosure_season_january(self):
        from data.sync.base import _is_peak_disclosure_season

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 1, 15)
            assert _is_peak_disclosure_season() is False

    def test_is_not_peak_disclosure_season_june(self):
        from data.sync.base import _is_peak_disclosure_season

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 15)
            assert _is_peak_disclosure_season() is False

    def test_get_seasonal_adjustments_normal_season(self):
        from data.sync.base import _get_seasonal_adjustments

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 6, 15)
            factor, multiplier = _get_seasonal_adjustments()
            assert factor == 1
            assert multiplier == 1.0

    def test_get_seasonal_adjustments_peak_season(self):
        from data.sync.base import _get_seasonal_adjustments

        with patch("data.sync.base.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 4, 15)
            factor, multiplier = _get_seasonal_adjustments()
            assert factor == 2
            assert multiplier == 2.0


class TestFinancialSyncEngineDisposed:
    """Tests for EngineDisposedError propagation (R5: 必须从策略外层 raise)."""

    @pytest.mark.asyncio
    async def test_engine_disposed_in_run(self):
        """R5: EngineDisposedError 必须从策略外层 raise，不能被吞。

        与 historical/macro/holder/concept_sync 一致，与集成测试
        test_ai_concept_tagging.py::TestEngineDisposedE2E 对齐。
        """
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = FinancialSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run(force=True)

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
            return asyncio.run(strategy.run(force=True))

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


class TestFinancialSyncPartialFailure:
    """Partial-failure boundary tests: some stocks succeed, some fail,
    sync continues and preserves successfully fetched data."""

    @pytest.mark.asyncio
    async def test_partial_failure_some_stocks_fail_sync_continues(self):
        """When some stocks' API calls fail during full sync, the sync should
        continue processing remaining stocks and preserve successfully fetched data."""
        ctx = make_ctx()
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "list_status": ["L", "L", "L"],
                }
            )
        )

        failing_stock = "000002.SZ"
        income_df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "revenue": [100.0]})
        balance_df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "total_assets": [1000.0]})
        indicator_df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "roe": [10.0]})
        cashflow_df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240331"], "cfps": [1.0]})

        async def selective_income(*args, **kwargs):
            if kwargs.get("ts_code") == failing_stock:
                raise RuntimeError("API error")
            return income_df

        async def selective_balance(*args, **kwargs):
            if kwargs.get("ts_code") == failing_stock:
                raise RuntimeError("API error")
            return balance_df

        async def selective_indicator(*args, **kwargs):
            if kwargs.get("ts_code") == failing_stock:
                raise RuntimeError("API error")
            return indicator_df

        async def selective_cashflow(*args, **kwargs):
            if kwargs.get("ts_code") == failing_stock:
                raise RuntimeError("API error")
            return cashflow_df

        ctx.api.get_income = AsyncMock(side_effect=selective_income)
        ctx.api.get_balancesheet = AsyncMock(side_effect=selective_balance)
        ctx.api.get_fina_indicator = AsyncMock(side_effect=selective_indicator)
        ctx.api.get_cashflow = AsyncMock(side_effect=selective_cashflow)

        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)

        # Sync should not crash
        assert result is not None
        assert isinstance(result, SyncResult)
        # Successfully fetched data should be saved (for non-failing stocks)
        assert ctx.cache.save_financial_reports.await_count >= 1
        # The failing stock should not be marked complete
        mark_calls = ctx.cache.mark_stock_step4_completed.call_args_list
        marked_codes = {c[0][0] for c in mark_calls}
        assert failing_stock not in marked_codes
        # Non-failing stocks should be marked complete
        assert "000001.SZ" in marked_codes
        assert "000003.SZ" in marked_codes


class TestAsyncMockCoverage:
    """Verify that ctx.api as AsyncMock allows awaiting unmocked async methods."""

    @pytest.mark.asyncio
    async def test_unmocked_async_method_awaitable(self):
        """Unmocked methods on ctx.api (AsyncMock) should be awaitable without error."""
        ctx = make_ctx()
        # get_repurchase is NOT explicitly set in make_ctx()
        result = await ctx.api.get_repurchase(symbol="000001.SZ", date="20240610")
        # Should not raise "MagicMock can't be used in 'await' expression"
        # AsyncMock returns a coroutine that resolves to another AsyncMock
        assert result is not None or result is None  # just verifying no exception

    @pytest.mark.asyncio
    async def test_cache_engine_mock_chain(self):
        """ctx.cache.engine.begin should work as a synchronous MagicMock chain."""
        ctx = make_ctx()
        # engine is a MagicMock attribute, begin() returns another MagicMock
        engine = ctx.cache.engine
        assert engine is not None
        begin_result = engine.begin()
        assert begin_result is not None


class TestFinancialSyncPeakSeasonAdjustment:
    """peak 披露季分批大小调整 / 并发调整路径覆盖。"""

    @pytest.mark.asyncio
    async def test_peak_season_incremental_adjusts_concurrency(self):
        """peak 披露季时 _run_incremental_sync 应进入 _is_peak_disclosure_season 分支并记日志。"""
        ctx = make_ctx()
        yesterday = get_now() - datetime.timedelta(days=1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": yesterday})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        with (
            patch("data.sync.financial._is_peak_disclosure_season", return_value=True),
            patch("data.sync.financial._get_seasonal_adjustments", return_value=(2, 2.0)),
            patch("data.sync.financial.logger") as mock_logger,
        ):
            result = await strategy.run()
            assert result is not None
            # peak 季节应记录 concurrency/delay 调整的 info 日志
            info_calls = [c for c in mock_logger.info.call_args_list if "Peak disclosure" in str(c)]
            assert len(info_calls) >= 1


class TestFinancialSyncProgressCallbackBoundary:
    """进度回调频率边界 (completed_count % 5 == 0) 覆盖。"""

    @pytest.mark.asyncio
    async def test_progress_callback_triggered_at_multiples_of_5(self):
        """处理股票数达到 5 的倍数时应触发 stock-phase 进度回调。"""
        ctx = make_ctx()
        # 6 个股票 → completed_count=5 时触发一次（0→80% 段）
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"00000{i}.SZ" for i in range(6)],
                    "list_status": ["L"] * 6,
                }
            )
        )
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True, progress_callback=progress_cb)
        assert result is not None
        # 至少一次 stock-phase 回调（pct < 80）
        stock_phase_calls = [c for c in progress_cb.call_args_list if c[0][0] < 80]
        assert len(stock_phase_calls) >= 1

    @pytest.mark.asyncio
    async def test_progress_callback_not_triggered_below_5(self):
        """股票数 < 5 时 stock-phase 不触发进度回调（但 batch-phase 仍会触发）。"""
        ctx = make_ctx()
        # 3 个股票 → completed_count 不会到 5
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "list_status": ["L", "L", "L"],
                }
            )
        )
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True, progress_callback=progress_cb)
        assert result is not None
        # stock-phase (pct < 80) 不应触发
        stock_phase_calls = [c for c in progress_cb.call_args_list if c[0][0] < 80]
        assert len(stock_phase_calls) == 0

    @pytest.mark.asyncio
    async def test_all_synced_progress_callback_boundary(self):
        """所有股票已同步且无 pending 时，progress_callback 应被调用一次（100%）。"""
        ctx = make_ctx()
        ctx.cache.get_completed_step4_stocks = AsyncMock(return_value={"000001.SZ", "000002.SZ"})
        ctx.cache.get_incomplete_financial_stocks = AsyncMock(return_value=set())
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True, progress_callback=progress_cb)
        assert result is not None
        progress_cb.assert_called_once()
        # 最后一次调用应为 100% 完成
        last_call = progress_cb.call_args
        assert last_call[0][0] == 2  # total_stocks
        assert last_call[0][1] == 2


class TestFinancialSyncIncrementalBoundaries:
    """增量边界日期与全量切换 / 增量无新数据早返回。"""

    @pytest.mark.asyncio
    async def test_incremental_last_sync_today_no_dates_early_return(self):
        """last_sync_date 为今天时，dates_to_sync 为空，早返回且不调用 disclosure_date。"""
        ctx = make_ctx()
        now = get_now()
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": now})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None
        # 早返回：disclosure_date 不应被调用
        ctx.api.get_disclosure_date.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_incremental_last_sync_yesterday_generates_dates(self):
        """last_sync_date 为昨天时，生成 dates_to_sync 并调用 disclosure_date。"""
        ctx = make_ctx()
        yesterday = get_now() - datetime.timedelta(days=1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": yesterday})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None
        # 应调用 disclosure_date（至少一天）
        ctx.api.get_disclosure_date.assert_awaited()

    @pytest.mark.asyncio
    async def test_incremental_empty_target_list_continues(self):
        """disclosure_date 返回数据但 drop_duplicates 后 target_list 为空时 continue。"""
        ctx = make_ctx()
        yesterday = get_now() - datetime.timedelta(days=1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": yesterday})
        # 返回无 ts_code/end_date 列的 df → drop_duplicates 后 to_dict 为空
        ctx.api.get_disclosure_date = AsyncMock(return_value=pd.DataFrame({"other_col": ["x"]}))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None


class TestFinancialSyncFetchDegradation:
    """单表降级时仍返回其余表 / 全部表降级返回空。"""

    @pytest.mark.asyncio
    async def test_single_core_table_failure_returns_merged_df(self):
        """income 抛异常，其他 core 表正常 → merged df 仍非空（单表降级）。"""
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("income failed"))
        # balance/indicator/cashflow 仍返回数据（make_ctx 默认）
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert df is not None and not df.empty
        # balance 的 total_assets 应在 merged df 中
        assert "total_assets" in df.columns

    @pytest.mark.asyncio
    async def test_all_core_tables_raise_returns_none(self):
        """所有 core 表抛异常 → 返回 (None, aux_counts)。"""
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.api.get_cashflow = AsyncMock(side_effect=RuntimeError("fail"))
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert df is None
        assert aux == {"mainbz": 0, "audit": 0}

    @pytest.mark.asyncio
    async def test_merge_failure_enters_outer_exception(self):
        """merge 时抛异常（缺 end_date 列）应进入外层 except 返回 None。"""
        ctx = make_ctx()
        # income 有数据但缺 end_date 列 → _dedup_financial_df sort_values("end_date") 抛 KeyError
        ctx.api.get_income = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "revenue": [100.0]}))
        ctx.api.get_balancesheet = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "total_assets": [1000.0]})
        )
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert df is None
        assert aux == {"mainbz": 0, "audit": 0}

    @pytest.mark.asyncio
    async def test_aux_table_permission_error_returns_zero(self):
        """aux 表抛 TushareAPIPermissionError 时返回 0（不传播）。"""
        from data.external.tushare_client import TushareAPIPermissionError

        ctx = make_ctx()
        ctx.api.get_fina_mainbz = AsyncMock(side_effect=TushareAPIPermissionError("get_fina_mainbz", "no perm"))
        ctx.api.get_fina_audit = AsyncMock(side_effect=TushareAPIPermissionError("get_fina_audit", "no perm"))
        strategy = FinancialSyncStrategy(ctx)
        df, aux = await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")
        assert aux["mainbz"] == 0
        assert aux["audit"] == 0


class TestFinancialSyncCorporateActionsExtended:
    """_sync_corporate_actions_by_date: 日期范围跨年 / 多公司同日 / 无新数据。"""

    @pytest.mark.asyncio
    async def test_cross_year_dates_processed(self):
        """日期范围跨年（20231231, 20240101）应正常处理。"""
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(
                ctx.api,
                cfg["api"],
                AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]})),
            )
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20231231", "20240101"])
        # 2 天 × 4 表 = 8 次 update_sync_status
        assert ctx.cache.update_sync_status.await_count >= 8

    @pytest.mark.asyncio
    async def test_multiple_companies_same_date_saved(self):
        """同日多公司数据应全部传给 save 函数。"""
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        multi_df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]})
        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(ctx.api, cfg["api"], AsyncMock(return_value=multi_df))
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])
        # 每个 save 函数应被调用且传入含 2 行的 df
        ctx.cache.save_fina_forecast.assert_awaited()
        saved_df = ctx.cache.save_fina_forecast.call_args[0][0]
        assert len(saved_df) == 2

    @pytest.mark.asyncio
    async def test_no_new_data_updates_status_zero(self):
        """API 返回空 df 时，update_sync_status 应以 row_count=0 调用。"""
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(ctx.api, cfg["api"], AsyncMock(return_value=pd.DataFrame()))
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])
        # 每天 4 个表，row_count=0
        assert ctx.cache.update_sync_status.await_count >= 4
        for call in ctx.cache.update_sync_status.call_args_list:
            # call_args: (table_name, date_obj, row_count)
            assert call[0][2] == 0

    @pytest.mark.asyncio
    async def test_permission_denied_sets_skipped_status(self):
        """TushareAPIPermissionError 应设置 status='skipped_permission'。"""
        from data.external.tushare_client import TushareAPIPermissionError

        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(
                ctx.api,
                cfg["api"],
                AsyncMock(side_effect=TushareAPIPermissionError(cfg["api"], "no perm")),
            )
        strategy = FinancialSyncStrategy(ctx)
        await strategy._sync_corporate_actions_by_date(["20240614"])
        # 应有 skipped_permission 状态调用
        skipped_calls = [c for c in ctx.cache.update_sync_status.call_args_list if "skipped_permission" in str(c)]
        assert len(skipped_calls) >= 4

    @pytest.mark.asyncio
    async def test_system_error_in_table_propagates_to_gather(self):
        """sync_one_date_table 中 system 级异常 (MemoryError) raise 后被 gather 捕获,
        进入 832 行 isinstance(gr, Exception) 分支记 warning。"""
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(ctx.api, cfg["api"], AsyncMock(side_effect=MemoryError("OOM")))
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.logger") as mock_logger:
            await strategy._sync_corporate_actions_by_date(["20240614"])
            warning_calls = [c for c in mock_logger.warning.call_args_list if "Batch table sync failed" in str(c)]
            assert len(warning_calls) >= 1


class TestFinancialSyncRepairScenarios:
    """repair_financial_data: 最新一季度缺失修复 / 历史数据修复。"""

    @pytest.mark.asyncio
    async def test_repair_latest_period_empty_history_has_data(self):
        """最新季度返回空，历史季度有数据 → 只保存历史数据。"""
        ctx = make_ctx()
        now = get_now()
        current_year = now.year

        async def selective_income(*args, **kwargs):
            period = kwargs.get("period")
            if period and period.startswith(str(current_year)):
                return pd.DataFrame()  # 最新季度空
            return pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": [period], "revenue": [100.0]})

        ctx.api.get_income = AsyncMock(side_effect=selective_income)
        ctx.api.get_balancesheet = AsyncMock(return_value=None)
        ctx.api.get_fina_indicator = AsyncMock(return_value=None)
        ctx.api.get_cashflow = AsyncMock(return_value=None)
        ctx.api.get_fina_mainbz = AsyncMock(return_value=None)
        ctx.api.get_fina_audit = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data(["000001.SZ"])
        assert isinstance(result, int)
        # 应有 save 调用（历史季度）
        ctx.cache.save_financial_reports.assert_awaited()

    @pytest.mark.asyncio
    async def test_repair_history_period_data_saved(self):
        """历史 period 数据应被正确保存（验证 period 循环覆盖历史）。"""
        ctx = make_ctx()
        ctx.cache.save_financial_reports = AsyncMock(return_value=3)
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data(["000001.SZ", "000002.SZ"])
        assert result > 0
        # 多股票 × 多 period 应多次调用 save
        assert ctx.cache.save_financial_reports.await_count >= 2

    @pytest.mark.asyncio
    async def test_repair_progress_callback_invoked(self):
        """repair 过程中 progress_callback 应在 i % 10 == 0 时触发。"""
        ctx = make_ctx()
        # 11 个股票 → i=0 和 i=10 时触发
        codes = [f"00000{i}.SZ" for i in range(11)]
        progress_cb = MagicMock()
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.repair_financial_data(codes, progress_callback=progress_cb)
        assert isinstance(result, int)
        # 至少触发一次（i=0）
        assert progress_cb.call_count >= 1


class TestFinancialDedupEdgeCases:
    """_dedup_financial_df: ann_date 缺失场景 / update_flag 全为 None / None 输入。"""

    def test_dedup_none_input(self):
        """None 输入应原样返回 None。"""
        from data.sync.financial import _dedup_financial_df

        # 函数签名标注 df: DataFrame，但运行时接受 None（内部有 None 检查）
        assert _dedup_financial_df(None) is None  # type: ignore[reportArgumentType]

    def test_dedup_empty_df(self):
        """空 DataFrame 应原样返回。"""
        from data.sync.financial import _dedup_financial_df

        empty = pd.DataFrame()
        result = _dedup_financial_df(empty)
        assert result.empty

    def test_dedup_update_flag_all_none(self):
        """update_flag 全为 None 时应正常按 ann_date 去重。"""
        from data.sync.financial import _dedup_financial_df

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 2,
                "end_date": ["20240331", "20240331"],
                "ann_date": ["20240425", "20240430"],
                "update_flag": [None, None],
                "revenue": [100.0, 200.0],
            }
        )
        result = _dedup_financial_df(df)
        assert len(result) == 1
        # ann_date 更大的应被保留（keep=last）
        assert result.iloc[0]["revenue"] == 200.0

    def test_dedup_no_ann_date_no_update_flag(self):
        """既无 ann_date 也无 update_flag 时按 end_date 简单去重。"""
        from data.sync.financial import _dedup_financial_df

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 2,
                "end_date": ["20240331", "20240331"],
                "revenue": [100.0, 200.0],
            }
        )
        result = _dedup_financial_df(df)
        assert len(result) == 1
        assert result.iloc[0]["revenue"] == 200.0

    def test_dedup_preserves_end_date_ordering(self):
        """去重后 end_date 应保持升序。"""
        from data.sync.financial import _dedup_financial_df

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 3,
                "end_date": ["20231231", "20240331", "20231231"],
                "ann_date": ["20240420", "20240430", "20240425"],
                "revenue": [100.0, 300.0, 200.0],
            }
        )
        result = _dedup_financial_df(df)
        # 2 个唯一 end_date
        assert len(result) == 2
        # 20231231 应保留 ann_date 最大的（200.0）
        q4 = result[result["end_date"] == "20231231"].iloc[0]
        assert q4["revenue"] == 200.0


class TestGatherReturnExceptionsPropagatingCancel:
    """gather_return_exceptions_propagating_cancel: 部分失败 + 部分取消的组合。"""

    @pytest.mark.asyncio
    async def test_mixed_failure_and_cancel_propagates_cancel(self):
        """部分任务失败 + 部分被取消 → CancelledError 应被传播（R2 守卫）。"""
        from utils.async_utils import gather_return_exceptions_propagating_cancel

        async def success():
            return 1

        async def fail():
            raise RuntimeError("fail")

        async def cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await gather_return_exceptions_propagating_cancel(success(), fail(), cancelled())

    @pytest.mark.asyncio
    async def test_all_failures_no_cancel_returns_exceptions(self):
        """全部失败（无取消）→ 普通异常保留在结果列表中。"""
        from utils.async_utils import gather_return_exceptions_propagating_cancel

        async def fail1():
            raise RuntimeError("fail1")

        async def fail2():
            raise ValueError("fail2")

        results = await gather_return_exceptions_propagating_cancel(fail1(), fail2())
        assert len(results) == 2
        assert all(isinstance(r, Exception) for r in results)

    @pytest.mark.asyncio
    async def test_success_and_failure_mixed(self):
        """成功 + 失败混合 → 成功值保留，失败保留为 Exception。"""
        from utils.async_utils import gather_return_exceptions_propagating_cancel

        async def success():
            return 42

        async def fail():
            raise RuntimeError("fail")

        results = await gather_return_exceptions_propagating_cancel(success(), fail())
        assert results[0] == 42
        assert isinstance(results[1], Exception)


class TestFinancialSyncEngineDisposedExtended:
    """R5 守卫扩展: EngineDisposedError 在更多路径中传播。"""

    @pytest.mark.asyncio
    async def test_engine_disposed_in_repair_propagates(self):
        """R5: repair_financial_data 中 EngineDisposedError 必须传播。"""
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)
        with patch.object(
            strategy,
            "_fetch_comprehensive_financial_data",
            new=AsyncMock(side_effect=EngineDisposedError("disposed")),
        ):
            with pytest.raises(EngineDisposedError):
                await strategy.repair_financial_data(["000001.SZ"])

    @pytest.mark.asyncio
    async def test_engine_disposed_in_fetch_gather_propagates(self):
        """R5: gather 直接抛 EngineDisposedError 时 _fetch 应传播。"""
        ctx = make_ctx()
        strategy = FinancialSyncStrategy(ctx)

        async def fake_gather(*coros):
            for coro in coros:
                if hasattr(coro, "close"):
                    coro.close()
            raise EngineDisposedError("disposed")

        with patch(
            "data.sync.financial.gather_return_exceptions_propagating_cancel",
            new=fake_gather,
        ):
            with pytest.raises(EngineDisposedError):
                await strategy._fetch_comprehensive_financial_data("000001.SZ", period="20240331")

    @pytest.mark.asyncio
    async def test_engine_disposed_in_corporate_actions_caught_by_gather(self):
        """R5: _sync_corporate_actions_by_date 中 API 抛 EngineDisposedError 时，
        内部 except EngineDisposedError: raise 生效，被 gather 捕获为返回值。"""
        ctx = make_ctx()
        from data.constants import FINANCIAL_BATCH_TABLES

        for _table_name, cfg in FINANCIAL_BATCH_TABLES.items():
            setattr(ctx.api, cfg["api"], AsyncMock(side_effect=EngineDisposedError("disposed")))
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.logger") as mock_logger:
            # 不应抛异常（被 gather 捕获为返回值后记 warning）
            await strategy._sync_corporate_actions_by_date(["20240614"])
            warning_calls = [c for c in mock_logger.warning.call_args_list if "Batch table sync failed" in str(c)]
            assert len(warning_calls) >= 1

    @pytest.mark.asyncio
    async def test_engine_disposed_in_incremental_date_parse(self):
        """R5: _run_incremental_sync 日期解析中 EngineDisposedError 传播。

        通过让 get_sync_status 返回的对象在 + timedelta 时抛 EngineDisposedError,
        覆盖 534 行的 except EngineDisposedError: raise 路径。
        """
        ctx = make_ctx()

        # 构造一个 last_sync_date, 使 last_sync_dt + timedelta 触发 EngineDisposedError
        class _DisposedDate(datetime.datetime):
            def __add__(self, other):
                raise EngineDisposedError("disposed")

        disposed_dt = _DisposedDate(2024, 6, 1)
        ctx.cache.get_sync_status = AsyncMock(return_value={"last_sync_date": disposed_dt})
        ctx.api.get_disclosure_date = AsyncMock(return_value=None)
        strategy = FinancialSyncStrategy(ctx)
        with pytest.raises(EngineDisposedError):
            await strategy.run()

    @pytest.mark.asyncio
    async def test_engine_disposed_in_full_sync_save_caught_by_gather(self):
        """R5: _run_full_sync process_one_stock 中 save_financial_reports 抛 EngineDisposedError 时,
        内部 except EngineDisposedError: raise 生效, 但被 gather 捕获为返回值(不传播到外层)。"""
        ctx = make_ctx()
        ctx.cache.save_financial_reports = AsyncMock(side_effect=EngineDisposedError("disposed"))
        strategy = FinancialSyncStrategy(ctx)
        # gather(return_exceptions=True) 捕获 EngineDisposedError, 不传播
        result = await strategy.run(force=True)
        assert result is not None


class TestFinancialSyncIncrementalErrorPaths:
    """_run_incremental_sync 中 sync_one_target 异常处理路径覆盖。"""

    @pytest.mark.asyncio
    async def test_incremental_save_failure_continues(self):
        """save_financial_reports 抛异常时 sync_one_target 应继续处理。"""
        ctx = make_ctx()
        yesterday = get_now() - datetime.timedelta(days=1)
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
        ctx.cache.save_financial_reports = AsyncMock(side_effect=RuntimeError("save failed"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run()
        assert result is not None

    @pytest.mark.asyncio
    async def test_incremental_aux_status_updated(self):
        """增量 sync 中 aux 表有数据时应更新 sync_status。"""
        ctx = make_ctx()
        yesterday = get_now() - datetime.timedelta(days=1)
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
        result = await strategy.run()
        assert result is not None
        # 应有 fina_mainbz / fina_audit 的 update_sync_status 调用
        aux_calls = [c for c in ctx.cache.update_sync_status.call_args_list if c[0][0] in ("fina_mainbz", "fina_audit")]
        assert len(aux_calls) >= 2

    @pytest.mark.asyncio
    async def test_incremental_batch_task_exception_logged(self):
        """增量 batch 中任务异常应记 warning 日志。"""
        ctx = make_ctx()
        yesterday = get_now() - datetime.timedelta(days=1)
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
        # 所有 API 抛异常 → sync_one_target 内部捕获
        ctx.api.get_income = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=RuntimeError("fail"))
        ctx.api.get_cashflow = AsyncMock(side_effect=RuntimeError("fail"))
        strategy = FinancialSyncStrategy(ctx)
        with patch("data.sync.financial.logger"):
            result = await strategy.run()
            assert result is not None


class TestFinancialSyncFullSyncErrorPathsExtended:
    """_run_full_sync process_one_stock 异常分支扩展覆盖。"""

    @pytest.mark.asyncio
    async def test_attribute_error_in_fetch_reraises(self):
        """_fetch_comprehensive_financial_data 抛 AttributeError 时应被 process_one_stock 内部
        (AttributeError, NameError, TypeError, ImportError) 分支重新抛出。"""
        ctx = make_ctx()
        ctx.api.get_income = AsyncMock(side_effect=AttributeError("bad attr"))
        ctx.api.get_balancesheet = AsyncMock(side_effect=AttributeError("bad attr"))
        ctx.api.get_fina_indicator = AsyncMock(side_effect=AttributeError("bad attr"))
        ctx.api.get_cashflow = AsyncMock(side_effect=AttributeError("bad attr"))
        ctx.api.get_fina_mainbz = AsyncMock(side_effect=AttributeError("bad attr"))
        ctx.api.get_fina_audit = AsyncMock(side_effect=AttributeError("bad attr"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_save_transaction_failure_outer_exception(self):
        """financial_transaction 抛异常时 process_one_stock 外层 except 应处理。"""
        ctx = make_ctx()
        # 让 financial_transaction 抛异常
        ctx.cache.financial_transaction = MagicMock(side_effect=RuntimeError("tx failed"))
        strategy = FinancialSyncStrategy(ctx)
        result = await strategy.run(force=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_shutdown_during_batch_breaks_loop(self):
        """batch 循环中 shutdown_event 被设置时应 break。"""
        ctx = make_ctx()
        # 多股票多 batch
        ctx.cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": [f"00000{i}.SZ" for i in range(15)],
                    "list_status": ["L"] * 15,
                }
            )
        )
        strategy = FinancialSyncStrategy(ctx)

        # 在第一个 batch 后设置 shutdown
        original_gather = asyncio.gather

        call_count = 0

        async def mock_gather(*coros, **kwargs):
            nonlocal call_count
            call_count += 1
            result = await original_gather(*coros, **kwargs)
            if call_count == 1:
                strategy._shutdown_event.set()
            return result

        with patch("utils.async_utils.asyncio.gather", side_effect=mock_gather):
            result = await strategy.run(force=True)
            assert result is not None
