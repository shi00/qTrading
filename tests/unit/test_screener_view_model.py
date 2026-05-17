import asyncio
import datetime
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ui.components.virtual_table import PaginatedTable
from ui.viewmodels.screener_view_model import ScreenerViewModel, TASK_NAME_PREFIX


@pytest.fixture
def vm():
    model = ScreenerViewModel()
    model.data_processor = AsyncMock()
    model.strategy_mgr = MagicMock()
    model.review_mgr = AsyncMock()
    return model


class TestScreenerViewModelConstants:
    def test_task_name_prefix(self):
        assert TASK_NAME_PREFIX == "strategy_screening"


class TestScreenerViewModelInit:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_initial_state(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm._full_results is None
        assert vm.page_no == 1
        assert vm.page_size == 50
        assert vm.total_pages == 0
        assert vm.total_items == 0
        assert vm.sort_column is None
        assert vm.sort_ascending is True

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_initial_mode_realtime(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.mode == "REALTIME"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_ai_buffer_empty(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert len(vm._ai_buffer) == 0

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_callbacks_none(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.on_update is None
        assert vm.on_log is None
        assert vm.on_status is None
        assert vm.on_progress is None


class TestScreenerViewModelBind:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_bind_sets_callbacks(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        cb_update = MagicMock()
        cb_log = MagicMock()
        cb_status = MagicMock()
        cb_progress = MagicMock()
        vm.bind(cb_update, cb_log, cb_status, cb_progress)
        assert vm.on_update is cb_update
        assert vm.on_log is cb_log
        assert vm.on_status is cb_status
        assert vm.on_progress is cb_progress


class TestScreenerViewModelSortState:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_sort_column_default_none(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.sort_column is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_sort_ascending_default_true(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.sort_ascending is True


class TestScreenerViewModelAiUpdateInterval:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_interval_value(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.AI_UPDATE_INTERVAL == 0.5


class TestSortDirectionConsistency:
    @pytest.mark.asyncio
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    async def test_sort_data_accepts_ascending_param(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        vm._full_results = pd.DataFrame({"A": [3, 1, 2], "B": [1, 2, 3]})
        vm.sort_column = "A"
        vm.sort_ascending = True

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(side_effect=lambda t, f, *a, **k: f(*a, **k))
            await vm.sort_data("A", ascending=False)

        assert vm.sort_ascending is False

    @pytest.mark.asyncio
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    async def test_sort_data_ascending_default_toggles(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        vm._full_results = pd.DataFrame({"A": [3, 1, 2], "B": [1, 2, 3]})
        vm.sort_column = "A"
        vm.sort_ascending = True

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(side_effect=lambda t, f, *a, **k: f(*a, **k))
            await vm.sort_data("A")

        assert vm.sort_ascending is False

    @pytest.mark.asyncio
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    async def test_vm_new_column_defaults_ascending(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        vm._full_results = pd.DataFrame({"A": [3, 1, 2], "B": [1, 2, 3]})
        vm.sort_column = "A"
        vm.sort_ascending = False

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(side_effect=lambda t, f, *a, **k: f(*a, **k))
            await vm.sort_data("B")

        assert vm.sort_ascending is True

    def test_paginated_table_new_column_defaults_ascending(self):
        table = PaginatedTable()
        table.sort_col = "A"
        table.sort_asc = False

        table._handle_sort_click("B")

        assert table.sort_asc is True


class TestScreenerViewModelDispose:
    def test_dispose_clears_large_references_and_callbacks(self, vm):
        vm.on_update = MagicMock()
        vm.on_log = MagicMock()
        vm.on_status = MagicMock()
        vm.on_progress = MagicMock()
        vm.on_log_stream_start = MagicMock()
        vm._full_results = pd.DataFrame({"ts_code": ["000001.SZ"]})
        vm._ai_buffer = [{"ts_code": "000001.SZ"}]
        vm._realtime_snapshot = {"ts_code": "000001.SZ"}

        vm.dispose()

        assert vm.on_update is None
        assert vm.on_log is None
        assert vm.on_status is None
        assert vm.on_progress is None
        assert vm.on_log_stream_start is None
        assert vm._full_results is None
        assert vm._ai_buffer == []
        assert vm._realtime_snapshot is None


class TestScreenerViewModelPagination:
    def test_pagination(self, vm):
        df = pd.DataFrame({"A": range(100)})
        vm._full_results = df
        vm._update_pagination()

        assert vm.total_items == 100
        assert vm.total_pages == 2

        assert vm.page_no == 1
        page_data = vm.get_current_page_data()
        assert len(page_data) == 50
        assert page_data.iloc[0]["A"] == 0

        vm.change_page(1)
        assert vm.page_no == 2
        vm._update_pagination()
        page_data = vm.get_current_page_data()
        assert len(page_data) == 50
        assert page_data.iloc[0]["A"] == 50

        vm.change_page(1)
        vm._update_pagination()
        assert vm.page_no == 2


class TestScreenerViewModelSorting:
    @pytest.mark.asyncio
    async def test_sorting(self, vm):
        df = pd.DataFrame({"A": [3, 1, 2], "B": ["c", "a", "b"]})
        vm._full_results = df

        await vm.sort_data("A")
        assert vm.sort_column == "A"
        assert vm.sort_ascending
        assert vm._full_results.iloc[0]["A"] == 1

        await vm.sort_data("A")
        assert not vm.sort_ascending
        assert vm._full_results.iloc[0]["A"] == 3


class TestScreenerViewModelAIStreaming:
    @pytest.mark.asyncio
    async def test_ai_streaming_buffer(self, vm):
        vm.on_update = MagicMock()
        vm.on_log = MagicMock()
        vm._full_results = pd.DataFrame(columns=["name", "ai_score"])
        vm._main_loop = asyncio.get_running_loop()

        row1 = {"name": "S1", "ai_score": 90}
        row2 = {"name": "S2", "ai_score": 80}

        vm._on_ai_result_stream(row1)
        vm._on_ai_result_stream(row2)

        assert len(vm._ai_buffer) == 2
        assert vm.on_log.call_count == 2

        await vm._flush_ai_buffer()

        assert len(vm._ai_buffer) == 0
        assert len(vm._full_results) == 2
        assert vm._full_results.iloc[0]["name"] == "S1"


class TestScreenerViewModelExport:
    def test_get_export_data_none_when_empty(self, vm):
        vm._full_results = None
        assert vm.get_export_data() is None

    def test_get_export_data_none_when_empty_df(self, vm):
        vm._full_results = pd.DataFrame()
        assert vm.get_export_data() is None

    def test_get_export_data_returns_df(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2, 3]})
        result = vm.get_export_data()
        assert result is not None
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_export_results_no_data(self, vm, tmp_path):
        vm._full_results = None
        path, error = await vm.export_results(str(tmp_path / "test.csv"))
        assert path is None
        assert error == "No data to export"

    @pytest.mark.asyncio
    async def test_export_results_success(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2, 3]})
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test_export.csv")
            with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tm:
                mock_tm.return_value.run_async = AsyncMock(
                    side_effect=lambda tt, func, *args, **kwargs: func(*args, **kwargs),
                )
                path, error = await vm.export_results(filepath)
                assert path == filepath
                assert error is None


class TestScreenerViewModelRunStrategy:
    @pytest.mark.asyncio
    async def test_run_strategy_passes_trade_date_to_save_results(self, vm):
        analysis_date = datetime.date(2024, 12, 27)
        result_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [10.5],
                "pct_chg": [2.5],
                "ai_score": [85],
                "ai_reason": ["test"],
                "thinking": ["test"],
            }
        )

        mock_strategy = MagicMock()
        mock_strategy.name = "test_strategy"
        mock_strategy.filter = AsyncMock(return_value=result_df)
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

        test_params = {"rsi_threshold": 30, "volume_ratio": 2.0}
        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": analysis_date,
            }
        )

        submitted_coro = []

        def mock_submit_task(name, task_type, coroutine_factory, cancellable=False, unique_key=None, **kwargs):
            submitted_coro.append(coroutine_factory(task_id="test_task_id"))
            return "test_task_id"

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = mock_submit_task
            await vm.run_strategy("test_strategy", save_results=True, params=test_params)

        for coro in submitted_coro:
            await coro

        vm.review_mgr.save_results.assert_called_once()
        call_kwargs = vm.review_mgr.save_results.call_args
        passed_trade_date = call_kwargs.kwargs.get("trade_date")
        assert passed_trade_date == analysis_date
        passed_run_id = call_kwargs.kwargs.get("run_id")
        assert passed_run_id is not None
        assert len(passed_run_id) == 16
        passed_params = call_kwargs.kwargs.get("params_snapshot")
        assert passed_params == test_params

    @pytest.mark.asyncio
    async def test_run_strategy_raises_when_trade_date_missing_before_save(self, vm):
        result_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [10.5],
                "pct_chg": [2.5],
            }
        )

        mock_strategy = MagicMock()
        mock_strategy.name = "test_strategy"
        mock_strategy.filter = AsyncMock(return_value=result_df)
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)
        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
            }
        )

        submitted_coro = []

        def mock_submit_task(name, task_type, coroutine_factory, cancellable=False, unique_key=None, **kwargs):
            submitted_coro.append(coroutine_factory(task_id="test_task_id"))
            return "test_task_id"

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = mock_submit_task
            await vm.run_strategy("test_strategy", save_results=True)

        assert len(submitted_coro) == 1
        with pytest.raises(RuntimeError):
            await submitted_coro[0]
        vm.review_mgr.save_results.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_strategy_reports_degraded_context_status(self, vm):
        result_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [10.5],
                "pct_chg": [2.5],
            }
        )

        mock_strategy = MagicMock()
        mock_strategy.name = "test_strategy"
        mock_strategy.filter = AsyncMock(return_value=result_df)
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": datetime.date(2024, 12, 31),
                "_diagnostics": {
                    "strategy_ready": False,
                    "table_status": {
                        "northbound_data": {"ready": False, "rows": 0},
                        "moneyflow_data": {"ready": True, "rows": 1},
                    },
                },
            }
        )
        vm.on_status = MagicMock()

        submitted_coro = []

        def mock_submit_task(name, task_type, coroutine_factory, cancellable=False, unique_key=None, **kwargs):
            submitted_coro.append(coroutine_factory(task_id="test_task_id"))
            return "test_task_id"

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = mock_submit_task
            await vm.run_strategy("test_strategy", save_results=False)

        assert len(submitted_coro) == 1
        await submitted_coro[0]
        status_calls = vm.on_status.call_args_list
        assert any(("策略降级运行" in (args[0] if args else "") and args[1] == "orange") for args, _ in status_calls)


class TestSortHelper:
    def test_sorts_ascending(self):
        df = pd.DataFrame({"A": [3, 1, 2]})
        result = ScreenerViewModel._sort_helper(df, "A", True)
        assert list(result["A"]) == [1, 2, 3]

    def test_sorts_descending(self):
        df = pd.DataFrame({"A": [3, 1, 2]})
        result = ScreenerViewModel._sort_helper(df, "A", False)
        assert list(result["A"]) == [3, 2, 1]

    def test_returns_original_on_key_error(self):
        df = pd.DataFrame({"A": [3, 1, 2]})
        result = ScreenerViewModel._sort_helper(df, "Z", True)
        assert result.equals(df)


class TestUpdatePagination:
    def test_calculates_total_items(self, vm):
        vm._full_results = pd.DataFrame({"A": range(75)})
        vm._update_pagination()
        assert vm.total_items == 75

    def test_calculates_total_pages(self, vm):
        vm._full_results = pd.DataFrame({"A": range(75)})
        vm._update_pagination()
        assert vm.total_pages == 2

    def test_exact_page_boundary(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        assert vm.total_pages == 2

    def test_none_results(self, vm):
        vm._full_results = None
        vm._update_pagination()
        assert vm.total_items == 0
        assert vm.total_pages == 0


class TestGetCurrentPageData:
    def test_returns_sliced_dataframe(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm.page_no = 1
        vm.page_size = 50
        vm._update_pagination()
        page = vm.get_current_page_data()
        assert len(page) == 50
        assert page.iloc[0]["A"] == 0

    def test_second_page(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm.page_no = 2
        vm.page_size = 50
        vm._update_pagination()
        page = vm.get_current_page_data()
        assert len(page) == 50
        assert page.iloc[0]["A"] == 50

    def test_none_returns_empty(self, vm):
        vm._full_results = None
        result = vm.get_current_page_data()
        assert result.empty

    def test_empty_df_returns_empty(self, vm):
        vm._full_results = pd.DataFrame()
        result = vm.get_current_page_data()
        assert result.empty


class TestChangePage:
    def test_increment_within_bounds(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.page_no = 1
        vm.change_page(1)
        assert vm.page_no == 2

    def test_decrement_within_bounds(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.page_no = 2
        vm.change_page(-1)
        assert vm.page_no == 1

    def test_does_not_go_below_one(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.page_no = 1
        vm.change_page(-1)
        assert vm.page_no == 1

    def test_does_not_exceed_total_pages(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.page_no = 2
        vm.change_page(1)
        assert vm.page_no == 2

    def test_calls_on_update(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.on_update = MagicMock()
        vm.page_no = 1
        vm.change_page(1)
        vm.on_update.assert_called_once()


class TestChangePageSize:
    def test_updates_page_size(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.change_page_size(25)
        assert vm.page_size == 25

    def test_resets_to_page_one(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.page_no = 2
        vm.change_page_size(25)
        assert vm.page_no == 1

    def test_ignores_non_positive(self, vm):
        vm.page_size = 50
        vm.change_page_size(0)
        assert vm.page_size == 50

    def test_ignores_negative(self, vm):
        vm.page_size = 50
        vm.change_page_size(-10)
        assert vm.page_size == 50

    def test_ignores_same_size(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.page_no = 2
        vm.change_page_size(50)
        assert vm.page_no == 2

    def test_calls_on_update(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.on_update = MagicMock()
        vm.change_page_size(25)
        vm.on_update.assert_called_once()


class TestSwitchToHistory:
    def test_snapshots_state(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2]})
        vm.page_no = 3
        vm.sort_column = "A"
        vm.sort_ascending = False
        vm._ai_buffer = [{"x": 1}]
        vm.switch_to_history()
        assert vm._realtime_snapshot["page_no"] == 3
        assert vm._realtime_snapshot["sort_column"] == "A"
        assert vm._realtime_snapshot["sort_ascending"] is False
        assert vm._realtime_snapshot["ai_buffer"] == [{"x": 1}]

    def test_clears_results(self, vm):
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()
        assert vm._full_results is None

    def test_resets_page_and_sort(self, vm):
        vm.page_no = 5
        vm.sort_column = "B"
        vm.sort_ascending = False
        vm.switch_to_history()
        assert vm.page_no == 1
        assert vm.sort_column is None
        assert vm.sort_ascending is True

    def test_changes_mode(self, vm):
        vm.switch_to_history()
        assert vm.mode == "HISTORY"

    def test_noop_if_already_history(self, vm):
        vm.mode = "HISTORY"
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()
        assert vm._full_results is not None


class TestSwitchToRealtime:
    def test_restores_snapshot(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2]})
        vm.page_no = 3
        vm.sort_column = "A"
        vm.sort_ascending = False
        vm._ai_buffer = [{"x": 1}]
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert vm.page_no == 3
        assert vm.sort_column == "A"
        assert vm.sort_ascending is False

    def test_clears_snapshot(self, vm):
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert vm._realtime_snapshot is None

    def test_changes_mode(self, vm):
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert vm.mode == "REALTIME"

    def test_merges_discarded_buffer(self, vm):
        vm._full_results = pd.DataFrame({"A": [1]})
        vm._ai_buffer = []
        vm.switch_to_history()
        vm._discarded_buffer = [{"y": 2}]
        vm.switch_to_realtime()
        assert {"y": 2} in vm._ai_buffer

    def test_noop_if_already_realtime(self, vm):
        vm.mode = "REALTIME"
        vm.on_update = MagicMock()
        vm.switch_to_realtime()
        vm.on_update.assert_not_called()

    def test_calls_on_update(self, vm):
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()
        vm.on_update = MagicMock()
        vm.switch_to_realtime()
        vm.on_update.assert_called_once()


class TestGetExportData:
    def test_returns_none_for_none_results(self, vm):
        vm._full_results = None
        assert vm.get_export_data() is None

    def test_returns_none_for_empty_df(self, vm):
        vm._full_results = pd.DataFrame()
        assert vm.get_export_data() is None

    def test_returns_dataframe(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2]})
        result = vm.get_export_data()
        assert result is not None
        assert len(result) == 2


class TestExportResultsEmpty:
    @pytest.mark.asyncio
    async def test_returns_none_and_message_for_none(self, vm):
        vm._full_results = None
        path, msg = await vm.export_results("/tmp/test.csv")
        assert path is None
        assert msg == "No data to export"

    @pytest.mark.asyncio
    async def test_returns_none_and_message_for_empty(self, vm):
        vm._full_results = pd.DataFrame()
        path, msg = await vm.export_results("/tmp/test.csv")
        assert path is None
        assert msg == "No data to export"
