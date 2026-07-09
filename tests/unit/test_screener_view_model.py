import asyncio
import datetime
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ui.components.virtual_table import PaginatedTable
from ui.viewmodels.screener_view_model import ScreenerViewModel, TASK_NAME_PREFIX

pytestmark = pytest.mark.unit


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
        assert vm.state.page_no == 1
        assert vm.state.page_size == 50
        assert vm.state.total_pages == 0
        assert vm.state.total_items == 0
        assert vm.state.sort_column is None
        assert vm.state.sort_ascending is True

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_initial_mode_realtime(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.state.mode == "REALTIME"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_ai_buffer_empty(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert len(vm._ai_buffer) == 0

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_state_defaults(self, mock_dp, mock_sm, mock_rm):
        """VM 不再有回调属性；state 字段覆盖原回调承载的 UI 状态。"""
        vm = ScreenerViewModel()
        assert vm.state.loading is False
        assert vm.state.status_message is None
        assert vm.state.status_color == ""
        assert vm.state.logs == ()
        assert vm.state.task_unlocked is False
        assert vm.state.data_version == 0


class TestScreenerViewModelSortState:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_sort_column_default_none(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.state.sort_column is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_sort_ascending_default_true(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.state.sort_ascending is True


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
        vm._set_state(sort_column="A", sort_ascending=True)

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(side_effect=lambda t, f, *a, **k: f(*a, **k))
            await vm.sort_data("A", ascending=False)

        assert vm.state.sort_ascending is False

    @pytest.mark.asyncio
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    async def test_sort_data_ascending_default_toggles(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        vm._full_results = pd.DataFrame({"A": [3, 1, 2], "B": [1, 2, 3]})
        vm._set_state(sort_column="A", sort_ascending=True)

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(side_effect=lambda t, f, *a, **k: f(*a, **k))
            await vm.sort_data("A")

        assert vm.state.sort_ascending is False

    @pytest.mark.asyncio
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    async def test_vm_new_column_defaults_ascending(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        vm._full_results = pd.DataFrame({"A": [3, 1, 2], "B": [1, 2, 3]})
        vm._set_state(sort_column="A", sort_ascending=False)

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(side_effect=lambda t, f, *a, **k: f(*a, **k))
            await vm.sort_data("B")

        assert vm.state.sort_ascending is True

    def test_paginated_table_new_column_defaults_ascending(self):
        table = PaginatedTable()
        table.sort_col = "A"
        table.sort_asc = False

        table._handle_sort_click("B")

        assert table.sort_asc is True


class TestScreenerViewModelDispose:
    def test_dispose_clears_large_references_and_state(self, vm):
        vm._full_results = pd.DataFrame({"ts_code": ["000001.SZ"]})
        vm._ai_buffer = [{"ts_code": "000001.SZ"}]
        vm._realtime_snapshot = {"ts_code": "000001.SZ"}

        vm.dispose()

        assert vm._full_results is None
        assert vm._ai_buffer == []
        assert vm._realtime_snapshot is None
        # State reset to defaults
        assert vm.state.loading is False
        assert vm.state.page_no == 1
        assert vm.state.mode == "REALTIME"
        assert vm.state.logs == ()


class TestScreenerViewModelPagination:
    def test_pagination(self, vm):
        df = pd.DataFrame({"A": range(100)})
        vm._full_results = df
        vm._update_pagination()

        assert vm.state.total_items == 100
        assert vm.state.total_pages == 2

        assert vm.state.page_no == 1
        page_data = vm.get_current_page_data()
        assert len(page_data) == 50
        assert page_data.iloc[0]["A"] == 0

        vm.change_page(1)
        assert vm.state.page_no == 2
        page_data = vm.get_current_page_data()
        assert len(page_data) == 50
        assert page_data.iloc[0]["A"] == 50

        vm.change_page(1)
        assert vm.state.page_no == 2  # already at last page


class TestScreenerViewModelSorting:
    @pytest.mark.asyncio
    async def test_sorting(self, vm):
        df = pd.DataFrame({"A": [3, 1, 2], "B": ["c", "a", "b"]})
        vm._full_results = df

        await vm.sort_data("A")
        assert vm.state.sort_column == "A"
        assert vm.state.sort_ascending
        assert vm._full_results.iloc[0]["A"] == 1

        await vm.sort_data("A")
        assert not vm.state.sort_ascending
        assert vm._full_results.iloc[0]["A"] == 3


class TestScreenerViewModelAIStreaming:
    @pytest.mark.asyncio
    async def test_ai_streaming_buffer(self, vm):
        vm._full_results = pd.DataFrame(columns=["name", "ai_score"])
        vm._main_loop = asyncio.get_running_loop()

        row1 = {"name": "S1", "ai_score": 90}
        row2 = {"name": "S2", "ai_score": 80}

        vm._on_ai_result_stream(row1)
        vm._on_ai_result_stream(row2)

        assert len(vm._ai_buffer) == 2
        # Logs appended to state (replaces on_log callback)
        assert len(vm.state.logs) == 2
        assert vm.state.logs[0].name == "S1"
        assert vm.state.logs[1].name == "S2"

        await vm._flush_ai_buffer()

        assert len(vm._ai_buffer) == 0
        assert len(vm._full_results) == 2
        assert vm._full_results.iloc[0]["name"] == "S1"

    @pytest.mark.asyncio
    async def test_flush_ai_buffer_missing_ai_reason_column(self, vm):
        """AI 结果只返回 ai_score 而无 ai_reason 时，flush 不应抛 KeyError，并补齐空 ai_reason 列。"""
        vm._full_results = pd.DataFrame(columns=["name", "ai_score"])
        vm._main_loop = asyncio.get_running_loop()

        vm._on_ai_result_stream({"name": "S1", "ai_score": 90})

        await vm._flush_ai_buffer()

        assert "ai_reason" in vm._full_results.columns
        assert len(vm._full_results) == 1
        assert vm._full_results.iloc[0]["ai_reason"] == ""
        assert vm._full_results.iloc[0]["ai_score"] == 90

    @pytest.mark.asyncio
    async def test_flush_ai_buffer_with_both_ai_columns(self, vm):
        """ai_score 与 ai_reason 同时存在时，flush 正常工作且保留 ai_reason 原值。"""
        vm._full_results = pd.DataFrame(columns=["name", "ai_score", "ai_reason"])
        vm._main_loop = asyncio.get_running_loop()

        vm._on_ai_result_stream({"name": "S1", "ai_score": 90, "ai_reason": "good"})
        vm._on_ai_result_stream({"name": "S2", "ai_score": 80, "ai_reason": "ok"})

        await vm._flush_ai_buffer()

        assert len(vm._full_results) == 2
        assert vm._full_results.iloc[0]["name"] == "S1"
        assert vm._full_results.iloc[0]["ai_reason"] == "good"
        assert vm._full_results.iloc[1]["ai_reason"] == "ok"


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

        def mock_submit_task(
            name,
            task_type,
            coroutine_factory,
            cancellable=False,
            unique_key=None,
            **kwargs,
        ):
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

        def mock_submit_task(
            name,
            task_type,
            coroutine_factory,
            cancellable=False,
            unique_key=None,
            **kwargs,
        ):
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
        """策略降级运行时，state 中间快照应包含 orange 降级状态。"""
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

        # Subscribe to capture intermediate state snapshots (degraded status is
        # overwritten by success status after strategy completes).
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

        submitted_coro = []

        def mock_submit_task(
            name,
            task_type,
            coroutine_factory,
            cancellable=False,
            unique_key=None,
            **kwargs,
        ):
            submitted_coro.append(coroutine_factory(task_id="test_task_id"))
            return "test_task_id"

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = mock_submit_task
            await vm.run_strategy("test_strategy", save_results=False)

        assert len(submitted_coro) == 1
        await submitted_coro[0]

        # At some point, degraded status (orange) was set
        degraded = [s for s in snapshots if s.status_color == "orange" and s.status_message]
        assert len(degraded) >= 1

    @pytest.mark.asyncio
    async def test_run_strategy_failure_reverts_loading_and_shows_error(self, vm):
        """策略执行失败时，state.loading 恢复为 False 且 status_color 为 red。"""
        mock_strategy = MagicMock()
        mock_strategy.name = "test_strategy"
        mock_strategy.name_key = "test_strategy_name"
        mock_strategy.filter = AsyncMock(side_effect=RuntimeError("strategy crashed"))
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": datetime.date(2024, 12, 31),
            }
        )

        submitted_coro = []

        def mock_submit_task(
            name,
            task_type,
            coroutine_factory,
            cancellable=False,
            unique_key=None,
            **kwargs,
        ):
            submitted_coro.append(coroutine_factory(task_id="test_task_id"))
            return "test_task_id"

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = mock_submit_task
            await vm.run_strategy("test_strategy")

        assert len(submitted_coro) == 1
        with pytest.raises(RuntimeError, match="Strategy execution crashed"):
            await submitted_coro[0]

        # Final state: loading reverted, error status set
        assert vm.state.loading is False
        assert vm.state.status_color == "red"

    @pytest.mark.asyncio
    async def test_run_strategy_cancellation_cleans_up_state(self, vm):
        """策略执行取消时，state.loading 恢复为 False 且 status_color 为 orange。"""
        mock_strategy = MagicMock()
        mock_strategy.name = "test_strategy"
        mock_strategy.name_key = "test_strategy_name"
        mock_strategy.filter = AsyncMock(side_effect=asyncio.CancelledError())
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": datetime.date(2024, 12, 31),
            }
        )

        submitted_coro = []

        def mock_submit_task(
            name,
            task_type,
            coroutine_factory,
            cancellable=False,
            unique_key=None,
            **kwargs,
        ):
            submitted_coro.append(coroutine_factory(task_id="test_task_id"))
            return "test_task_id"

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = mock_submit_task
            await vm.run_strategy("test_strategy")

        assert len(submitted_coro) == 1
        with pytest.raises(asyncio.CancelledError):
            await submitted_coro[0]

        # Final state: loading reverted, cancellation status set
        assert vm.state.loading is False
        assert vm.state.status_color == "orange"


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
        assert vm.state.total_items == 75

    def test_calculates_total_pages(self, vm):
        vm._full_results = pd.DataFrame({"A": range(75)})
        vm._update_pagination()
        assert vm.state.total_pages == 2

    def test_exact_page_boundary(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        assert vm.state.total_pages == 2

    def test_none_results(self, vm):
        vm._full_results = None
        vm._update_pagination()
        assert vm.state.total_items == 0
        assert vm.state.total_pages == 0


class TestGetCurrentPageData:
    def test_returns_sliced_dataframe(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._set_state(page_no=1, page_size=50)
        vm._update_pagination()
        page = vm.get_current_page_data()
        assert len(page) == 50
        assert page.iloc[0]["A"] == 0

    def test_second_page(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._set_state(page_no=2, page_size=50)
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
        vm._set_state(page_no=1)
        vm.change_page(1)
        assert vm.state.page_no == 2

    def test_decrement_within_bounds(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm._set_state(page_no=2)
        vm.change_page(-1)
        assert vm.state.page_no == 1

    def test_does_not_go_below_one(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm._set_state(page_no=1)
        vm.change_page(-1)
        assert vm.state.page_no == 1

    def test_does_not_exceed_total_pages(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm._set_state(page_no=2)
        vm.change_page(1)
        assert vm.state.page_no == 2

    def test_notifies_subscribers(self, vm):
        """change_page 通过 _set_state 通知订阅者（替代原 on_update 回调）。"""
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm._set_state(page_no=1)

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm.change_page(1)

        assert len(snapshots) >= 1
        assert snapshots[-1].page_no == 2


class TestChangePageSize:
    def test_updates_page_size(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm.change_page_size(25)
        assert vm.state.page_size == 25

    def test_resets_to_page_one(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm._set_state(page_no=2)
        vm.change_page_size(25)
        assert vm.state.page_no == 1

    def test_ignores_non_positive(self, vm):
        vm._set_state(page_size=50)
        vm.change_page_size(0)
        assert vm.state.page_size == 50

    def test_ignores_negative(self, vm):
        vm._set_state(page_size=50)
        vm.change_page_size(-10)
        assert vm.state.page_size == 50

    def test_ignores_same_size(self, vm):
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()
        vm._set_state(page_no=2)
        vm.change_page_size(50)
        assert vm.state.page_no == 2

    def test_notifies_subscribers(self, vm):
        """change_page_size 通过 _notify 通知订阅者（替代原 on_update 回调）。"""
        vm._full_results = pd.DataFrame({"A": range(100)})
        vm._update_pagination()

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm.change_page_size(25)

        assert len(snapshots) >= 1
        assert snapshots[-1].page_size == 25


class TestSwitchToHistory:
    def test_snapshots_state(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2]})
        vm._set_state(page_no=3, sort_column="A", sort_ascending=False)
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
        vm._set_state(page_no=5, sort_column="B", sort_ascending=False)
        vm.switch_to_history()
        assert vm.state.page_no == 1
        assert vm.state.sort_column is None
        assert vm.state.sort_ascending is True

    def test_changes_mode(self, vm):
        vm.switch_to_history()
        assert vm.state.mode == "HISTORY"

    def test_noop_if_already_history(self, vm):
        vm._set_state(mode="HISTORY")
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()
        assert vm._full_results is not None


class TestSwitchToRealtime:
    def test_restores_snapshot(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2]})
        vm._set_state(page_no=3, sort_column="A", sort_ascending=False)
        vm._ai_buffer = [{"x": 1}]
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert vm.state.page_no == 3
        assert vm.state.sort_column == "A"
        assert vm.state.sort_ascending is False

    def test_clears_snapshot(self, vm):
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert vm._realtime_snapshot is None

    def test_changes_mode(self, vm):
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert vm.state.mode == "REALTIME"

    def test_merges_discarded_buffer(self, vm):
        vm._full_results = pd.DataFrame({"A": [1]})
        vm._ai_buffer = []
        vm.switch_to_history()
        vm._discarded_buffer = [{"y": 2}]
        vm.switch_to_realtime()
        assert {"y": 2} in vm._ai_buffer

    def test_noop_if_already_realtime(self, vm):
        """REALTIME 模式下 switch_to_realtime 不通知订阅者。"""
        vm._set_state(mode="REALTIME")
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm.switch_to_realtime()
        assert len(snapshots) == 0

    def test_notifies_subscribers(self, vm):
        """从 HISTORY 切回 REALTIME 时通知订阅者（替代原 on_update 回调）。"""
        vm._full_results = pd.DataFrame({"A": [1]})
        vm.switch_to_history()

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm.switch_to_realtime()

        assert len(snapshots) >= 1
        assert snapshots[-1].mode == "REALTIME"


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


class TestTaskManagerSubscription:
    """Tests for TaskManager subscribe/unsubscribe moved from View to ViewModel."""

    @patch("ui.viewmodels.screener_view_model.TaskManager")
    def test_subscribe_task_manager(self, mock_tm_cls, vm):
        vm.subscribe_task_manager()
        mock_tm_cls.return_value.subscribe.assert_called_once_with(vm._on_tasks_updated)

    @patch("ui.viewmodels.screener_view_model.TaskManager")
    def test_unsubscribe_task_manager(self, mock_tm_cls, vm):
        vm.unsubscribe_task_manager()
        mock_tm_cls.return_value.unsubscribe.assert_called_once_with(vm._on_tasks_updated)

    def test_on_tasks_updated_triggers_unlock_when_no_running_tasks(self, vm):
        """策略任务完成时，state.task_unlocked 设为 True（替代原 on_task_unlock 回调）。"""
        vm._strategy_submitted = True
        vm._on_tasks_updated([])
        assert vm.state.task_unlocked is True
        assert vm._strategy_submitted is False

    def test_on_tasks_updated_no_unlock_when_tasks_running(self, vm):
        vm._strategy_submitted = True
        mock_task = MagicMock()
        mock_task.name = f"{TASK_NAME_PREFIX}: test"
        mock_task.status.name = "RUNNING"
        vm._on_tasks_updated([mock_task])
        assert vm.state.task_unlocked is False
        assert vm._strategy_submitted is True

    def test_on_tasks_updated_no_unlock_without_submission(self, vm):
        vm._strategy_submitted = False
        vm._on_tasks_updated([])
        assert vm.state.task_unlocked is False

    def test_on_tasks_updated_no_unlock_when_queued(self, vm):
        vm._strategy_submitted = True
        mock_task = MagicMock()
        mock_task.name = f"{TASK_NAME_PREFIX}: test"
        mock_task.status.name = "QUEUED"
        vm._on_tasks_updated([mock_task])
        assert vm.state.task_unlocked is False

    def test_on_tasks_updated_unlocks_when_task_completed(self, vm):
        vm._strategy_submitted = True
        mock_task = MagicMock()
        mock_task.name = f"{TASK_NAME_PREFIX}: test"
        mock_task.status.name = "COMPLETED"
        vm._on_tasks_updated([mock_task])
        assert vm.state.task_unlocked is True
        assert vm._strategy_submitted is False

    def test_on_tasks_updated_ignores_non_strategy_tasks(self, vm):
        vm._strategy_submitted = True
        mock_task = MagicMock()
        mock_task.name = "other_task"
        mock_task.status.name = "RUNNING"
        vm._on_tasks_updated([mock_task])
        # Non-strategy task doesn't block unlock
        assert vm.state.task_unlocked is True

    @patch("ui.viewmodels.screener_view_model.TaskManager")
    def test_dispose_unsubscribes(self, mock_tm_cls, vm):
        vm.dispose()
        mock_tm_cls.return_value.unsubscribe.assert_called_once_with(vm._on_tasks_updated)

    def test_strategy_submitted_flag_initially_false(self, vm):
        assert vm._strategy_submitted is False

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_task_unlocked_initially_false(self, mock_dp, mock_sm, mock_rm):
        """VM 不再有 on_task_unlock 回调；state.task_unlocked 默认为 False。"""
        vm = ScreenerViewModel()
        assert vm.state.task_unlocked is False
