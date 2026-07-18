import asyncio
import datetime
import logging
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.i18n import Message
from ui.components.virtual_table import next_sort_state
from ui.viewmodels.screener_view_model import ScreenerViewModel, TASK_NAME_PREFIX
from utils.thread_pool import TaskType

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
        # PaginatedTable 已声明式重写 (Phase B.3), 排序逻辑抽为纯函数 next_sort_state
        new_col, new_asc = next_sort_state("A", False, "B")
        assert new_col == "B"
        assert new_asc is True


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
        # R.2.6.2/R.2.6.3: 新增字段也需重置到默认值
        assert vm.state.strategy_desc == ""
        assert vm.state.strategy_desc_color == "default"
        assert vm.state.status_message is None

    def test_dispose_clears_stream_buffers(self, vm):
        """P1-3: dispose 必须清空 _stream_buffers 防止资源泄漏。"""
        vm.start_stream_card("stock_1")
        assert len(vm._stream_buffers) == 1
        vm.dispose()
        assert len(vm._stream_buffers) == 0


class TestScreenerViewModelDisposeBackgroundTasks:
    """Task 4.2: dispose 后台任务清理 (done_callback + _disposed flag).

    覆盖 5 个 DoD:
    1. dispose 后延迟完成的任务不调用 subscriber
    2. 任务取消后最终从集合移除 (引用保留至 done callback)
    3. 后台任务异常被记录且无 'Task exception was never retrieved'
    4. CancelledError 继续传播 (R2)
    5. 反复 mount/unmount 不增长后台任务集合
    """

    def test_dispose_blocks_set_state_and_subscriber_calls(self, vm):
        """DoD #1: dispose 后 _set_state 不更新 state 也不调用 subscriber."""
        calls: list = []
        vm.subscribe(lambda s: calls.append(s))
        vm.dispose()
        # 模拟 dispose 后仍有 subscriber 被加入 (race / 防御)
        vm._subscribers.append(lambda s: calls.append("post-dispose"))
        original = vm.state
        vm._set_state(loading=True)
        assert vm.state == original  # state 未变
        assert calls == []  # subscriber 未被调用

    @pytest.mark.asyncio
    async def test_dispose_retains_task_reference_until_done_callback(self, vm):
        """DoD #2: dispose 取消任务但保留引用至 done callback 完成 (不立即 clear)."""

        async def long_running():
            await asyncio.sleep(10)

        loop = asyncio.get_running_loop()
        task = loop.create_task(long_running())
        vm._background_tasks.add(task)
        task.add_done_callback(vm._on_background_task_done)
        vm.dispose()
        # 立即检查: 任务已取消但引用仍保留 (未 clear)
        assert task in vm._background_tasks
        await asyncio.sleep(0.05)  # 让 cancel 传播 + done callback 触发
        assert task.cancelled()
        assert task not in vm._background_tasks  # done callback 移除

    @pytest.mark.asyncio
    async def test_background_task_exception_logged_and_retrieved(self, vm, caplog):
        """DoD #3: 后台任务异常被记录且 exception() 已读取 (无 'never retrieved')."""

        async def failing():
            raise RuntimeError("boom")

        loop = asyncio.get_running_loop()
        task = loop.create_task(failing())
        vm._background_tasks.add(task)
        task.add_done_callback(vm._on_background_task_done)
        with caplog.at_level(logging.ERROR):
            await asyncio.sleep(0.05)
        assert task.done()
        assert task not in vm._background_tasks  # done callback 移除
        assert any("boom" in r.message for r in caplog.records)
        # exception 已被读取 (不会触发 'Task exception was never retrieved')
        assert task.exception() is not None

    @pytest.mark.asyncio
    async def test_cancelled_task_not_logged_as_error(self, vm, caplog):
        """DoD #4: CancelledError 继续传播, 不被记录为 error (R2)."""

        async def long_running():
            await asyncio.sleep(10)

        loop = asyncio.get_running_loop()
        task = loop.create_task(long_running())
        vm._background_tasks.add(task)
        task.add_done_callback(vm._on_background_task_done)
        task.cancel()
        with caplog.at_level(logging.ERROR):
            await asyncio.sleep(0.05)
        assert task.cancelled()
        assert task not in vm._background_tasks  # done callback 移除
        # CancelledError 不被记录为 error
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    @pytest.mark.asyncio
    async def test_repeated_mount_unmount_no_task_growth(self, vm):
        """DoD #5: 反复 mount/unmount 不增长后台任务集合."""

        async def long_running():
            await asyncio.sleep(10)

        for _ in range(3):
            loop = asyncio.get_running_loop()
            task = loop.create_task(long_running())
            vm._background_tasks.add(task)
            task.add_done_callback(vm._on_background_task_done)
            vm.dispose()
            await asyncio.sleep(0.05)  # 让 cancel + done callback 完成
            assert len(vm._background_tasks) == 0


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

    @pytest.mark.asyncio
    async def test_export_results_excel_no_data(self, vm, tmp_path):
        vm._full_results = None
        path, error = await vm.export_results_excel(str(tmp_path / "test.xlsx"))
        assert path is None
        assert error == "No data to export"

    @pytest.mark.asyncio
    async def test_export_results_excel_success(self, vm):
        vm._full_results = pd.DataFrame({"A": [1, 2, 3]})
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test_export.xlsx")
            with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as mock_tm:
                mock_tm.return_value.run_async = AsyncMock(
                    side_effect=lambda tt, func, *args, **kwargs: func(*args, **kwargs),
                )
                path, error = await vm.export_results_excel(filepath)
                assert path == filepath
                assert error is None
                # 验证 to_excel 被调用 (通过 ThreadPoolManager.run_async 的调用参数)
                call_args = mock_tm.return_value.run_async.call_args
                assert call_args.args[0] == TaskType.CPU
                assert call_args.args[1] == vm._full_results.to_excel
                assert call_args.args[2] == filepath
                assert call_args.kwargs == {"index": False, "engine": "openpyxl"}


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
    async def test_save_results_stores_i18n_key(self, vm):
        """R.3.1: save_results 应存储 strategy.name_key (i18n key) 而非 I18n.get(name_key) 翻译字符串。

        验证 strategy_name 参数为 i18n key (如 "strategy_value_name")，
        非 locale-dependent 翻译值 (如 "价值投资")。
        """
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
        mock_strategy.name_key = "strategy_value_name"  # i18n key, 非翻译字符串
        mock_strategy.filter = AsyncMock(return_value=result_df)
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)

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
            await vm.run_strategy("test_strategy", save_results=True)

        for coro in submitted_coro:
            await coro

        vm.review_mgr.save_results.assert_called_once()
        call_args = vm.review_mgr.save_results.call_args
        # 第一个位置参数应为 i18n key (strategy.name_key), 非翻译字符串
        stored_strategy_name = call_args.args[0]
        assert stored_strategy_name == "strategy_value_name"
        # 不应等于翻译值 (防御性断言)
        assert stored_strategy_name != "价值投资"

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
        degraded = [s for s in snapshots if s.status_color == "warning" and s.status_message]
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
        assert vm.state.status_color == "error"

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
        assert vm.state.status_color == "warning"


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

    def test_snapshots_stream_cards_and_buffers(self, vm):
        """P1-3: switch_to_history 必须快照 stream_cards 和 stream_buffers。"""
        vm.start_stream_card("stock_1")
        vm.switch_to_history()
        assert vm._realtime_snapshot["stream_cards"] != ()
        assert len(vm._realtime_snapshot["stream_cards"]) == 1
        assert vm._realtime_snapshot["stream_cards"][0].name == "stock_1"
        assert len(vm._realtime_snapshot["stream_buffers"]) == 1

    def test_clears_stream_cards_and_buffers_on_switch(self, vm):
        """P1-3: switch_to_history 后 state.stream_cards 和 _stream_buffers 必须清空。"""
        vm.start_stream_card("stock_1")
        vm.switch_to_history()
        assert vm.state.stream_cards == ()
        assert len(vm._stream_buffers) == 0

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

    def test_restores_stream_cards_and_buffers(self, vm):
        """P1-3: switch_to_realtime 必须恢复 stream_cards 和 _stream_buffers。"""
        vm.start_stream_card("stock_1")
        vm.switch_to_history()
        vm.switch_to_realtime()
        assert len(vm.state.stream_cards) == 1
        assert vm.state.stream_cards[0].name == "stock_1"
        assert len(vm._stream_buffers) == 1

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
        mock_task.task_type = Message("task_type_ai_screening")
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
        mock_task.task_type = Message("task_type_ai_screening")
        mock_task.status.name = "QUEUED"
        vm._on_tasks_updated([mock_task])
        assert vm.state.task_unlocked is False

    def test_on_tasks_updated_unlocks_when_task_completed(self, vm):
        vm._strategy_submitted = True
        mock_task = MagicMock()
        mock_task.task_type = Message("task_type_ai_screening")
        mock_task.status.name = "COMPLETED"
        vm._on_tasks_updated([mock_task])
        assert vm.state.task_unlocked is True
        assert vm._strategy_submitted is False

    def test_on_tasks_updated_ignores_non_strategy_tasks(self, vm):
        vm._strategy_submitted = True
        mock_task = MagicMock()
        mock_task.task_type = Message("task_type_other")
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


# ============================================================================
# R.2.1: select_strategy command + _compute_tier_hint 内聚到 VM
# ============================================================================


class TestScreenerViewModelSelectStrategy:
    """R.2.1: select_strategy command — 选中策略 + 计算 tier_hint 内聚到 VM。"""

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_select_strategy_updates_state(self, mock_dp, mock_sm, mock_rm):
        """select_strategy(key) 更新 state.selected_strategy + state.tier_hint。"""
        vm = ScreenerViewModel()
        with patch.object(ScreenerViewModel, "_compute_tier_hint", return_value=None):
            vm.select_strategy("momentum_breakout")
        assert vm.state.selected_strategy == "momentum_breakout"
        assert vm.state.tier_hint is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_select_strategy_none_clears_state(self, mock_dp, mock_sm, mock_rm):
        """select_strategy(None) 清空 selected_strategy + tier_hint。"""
        vm = ScreenerViewModel()
        vm._set_state(selected_strategy="old_key", tier_hint="sys_strategy_tier_hint")
        with patch.object(ScreenerViewModel, "_compute_tier_hint", return_value=None):
            vm.select_strategy(None)
        assert vm.state.selected_strategy is None
        assert vm.state.tier_hint is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_select_strategy_with_tier_hint(self, mock_dp, mock_sm, mock_rm):
        """select_strategy(key) 档位不足时 tier_hint 为 i18n key（非翻译值，§3.2）。"""
        vm = ScreenerViewModel()
        with patch.object(ScreenerViewModel, "_compute_tier_hint", return_value="sys_strategy_tier_hint"):
            vm.select_strategy("ai_llm_v")
        assert vm.state.selected_strategy == "ai_llm_v"
        assert vm.state.tier_hint == "sys_strategy_tier_hint"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_select_strategy_notifies_subscribers(self, mock_dp, mock_sm, mock_rm):
        """select_strategy 必须通知订阅者（state 变化传播）。"""
        vm = ScreenerViewModel()
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        with patch.object(ScreenerViewModel, "_compute_tier_hint", return_value=None):
            vm.select_strategy("momentum_breakout")
        assert len(snapshots) == 1
        assert snapshots[0].selected_strategy == "momentum_breakout"
        assert snapshots[0].tier_hint is None


class TestScreenerViewModelLoadStrategies:
    """R.2.6.1: load_strategies command — 加载策略列表到 state (业务状态迁入 VM)."""

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_load_strategies_updates_state(self, mock_dp, mock_sm, mock_rm):
        """load_strategies() 成功时更新 state.strategies_with_dep + state.strategies_loaded=True."""
        vm = ScreenerViewModel()
        mock_strategies = {"value": {"name": "价值策略", "missing_apis": []}}
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value=mock_strategies)

        vm.load_strategies()

        assert vm.state.strategies_loaded is True
        assert vm.state.strategies_with_dep == mock_strategies

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_load_strategies_notifies_subscribers(self, mock_dp, mock_sm, mock_rm):
        """load_strategies() 必须通知订阅者 (state 变化传播)."""
        vm = ScreenerViewModel()
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value={})
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

        vm.load_strategies()

        assert len(snapshots) == 1
        assert snapshots[0].strategies_loaded is True

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_load_strategies_failure_sets_error_status(self, mock_dp, mock_sm, mock_rm):
        """load_strategies() 失败时设置 status_message=Message('screener_load_failed') + status_color='error'."""
        vm = ScreenerViewModel()
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(side_effect=RuntimeError("DB error"))

        vm.load_strategies()

        assert vm.state.strategies_loaded is False
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "screener_load_failed"
        assert vm.state.status_color == "error"


class TestScreenerViewModelUpdateStrategyDesc:
    """R.2.6.2: update_strategy_desc command — 更新策略描述+颜色到 state (业务状态迁入 VM).

    VM 不感知 AppColors (§3.2), state.strategy_desc_color 产出语义标识符
    ("default"/"warning"), View 渲染时映射到 AppColors.
    """

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_none_clears_state(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc(None) 清空 desc + color=default."""
        vm = ScreenerViewModel()
        vm.update_strategy_desc(None)
        assert vm.state.strategy_desc == ""
        assert vm.state.strategy_desc_color == "default"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_with_strategy_obj_uses_dynamic_description(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc(key) 当 strategy_obj 存在时调 get_dynamic_description(defaults)."""
        vm = ScreenerViewModel()
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = [{"name": "rsi", "default": 30}]
        mock_strategy.get_dynamic_description.return_value = "RSI<30 选股"
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value={})

        vm.update_strategy_desc("rsi_strategy")

        mock_strategy.get_dynamic_description.assert_called_once_with({"rsi": 30})
        assert vm.state.strategy_desc == "RSI<30 选股"
        assert vm.state.strategy_desc_color == "default"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_with_missing_apis_sets_warning_color(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc(key) 当 dep_info.missing_apis 非空时 color='warning' + desc 追加警告."""
        vm = ScreenerViewModel()
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = []
        mock_strategy.get_dynamic_description.return_value = "策略描述"
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value={"value": {"missing_apis": ["daily_basic"]}})

        vm.update_strategy_desc("value")

        assert "⚠️" in vm.state.strategy_desc
        assert vm.state.strategy_desc_color == "warning"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_with_params_uses_provided_params(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc(key, params=...) 用提供的 params 而非默认参数调 get_dynamic_description."""
        vm = ScreenerViewModel()
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = [{"name": "rsi", "default": 30}]
        mock_strategy.get_dynamic_description.return_value = "RSI<15 选股"
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value={})

        vm.update_strategy_desc("rsi_strategy", params={"rsi": 15})

        mock_strategy.get_dynamic_description.assert_called_once_with({"rsi": 15})
        assert vm.state.strategy_desc == "RSI<15 选股"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_fallback_to_get_strategy_desc_when_no_obj(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc(key) 当 strategy_obj 不存在时回退到 vm.get_strategy_desc(key)."""
        vm = ScreenerViewModel()
        vm.strategy_mgr.get_strategy = MagicMock(return_value=None)
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value={})
        vm.get_strategy_desc = MagicMock(return_value="回退描述")

        vm.update_strategy_desc("unknown")

        vm.get_strategy_desc.assert_called_once_with("unknown")
        assert vm.state.strategy_desc == "回退描述"
        assert vm.state.strategy_desc_color == "default"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_notifies_subscribers(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc() 必须通知订阅者 (state 变化传播)."""
        vm = ScreenerViewModel()
        vm.strategy_mgr.get_all_with_dependencies = MagicMock(return_value={})
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

        vm.update_strategy_desc(None)

        assert len(snapshots) == 1
        assert snapshots[0].strategy_desc == ""

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_update_strategy_desc_exception_resets_to_default(self, mock_dp, mock_sm, mock_rm):
        """update_strategy_desc() 异常时降级为空 desc + default color (G2 M2: 防护 slider 高频场景)."""
        vm = ScreenerViewModel()
        vm.strategy_mgr.get_strategy = MagicMock(side_effect=RuntimeError("DB error"))

        vm.update_strategy_desc("broken_strategy")

        assert vm.state.strategy_desc == ""
        assert vm.state.strategy_desc_color == "default"


class TestScreenerViewModelSetHistoryViewingStatus:
    """R.2.6.3: set_history_viewing_status command — 历史查看状态迁入 VM state.

    VM 接收 View 传入的已格式化 date_str + 已翻译 label, 包装为 Message + params,
    存入 state.status_message/status_color (§3.2 VM 不调 I18n.get).
    """

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_set_history_viewing_status_updates_state(self, mock_dp, mock_sm, mock_rm):
        """set_history_viewing_status() 设置 status_message=Message('screener_history_viewing') + color='info'."""
        vm = ScreenerViewModel()
        vm.set_history_viewing_status("2024-12-27", "#abc12345")
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "screener_history_viewing"
        assert vm.state.status_message.params == {"date": "2024-12-27", "label": "#abc12345"}
        assert vm.state.status_color == "info"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_set_history_viewing_status_with_strategy_label(self, mock_dp, mock_sm, mock_rm):
        """set_history_viewing_status() 接受翻译后的策略名作为 label."""
        vm = ScreenerViewModel()
        vm.set_history_viewing_status("2024-12-27", "价值策略")
        assert vm.state.status_message is not None
        assert vm.state.status_message.params == {"date": "2024-12-27", "label": "价值策略"}

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_set_history_viewing_status_notifies_subscribers(self, mock_dp, mock_sm, mock_rm):
        """set_history_viewing_status() 必须通知订阅者 (state 变化传播)."""
        vm = ScreenerViewModel()
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm.set_history_viewing_status("2024-12-27", "价值策略")
        assert len(snapshots) == 1
        assert snapshots[0].status_message is not None
        assert snapshots[0].status_message.key == "screener_history_viewing"


class TestScreenerViewModelComputeTierHint:
    """R.2.1: _compute_tier_hint 覆盖 None / 已知策略 / 未知策略 3 路径。

    返回 i18n key（非翻译值），符合 §3.2 "VM 只产出 i18n key"。
    """

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_compute_tier_hint_none_strategy(self, mock_dp, mock_sm, mock_rm):
        """路径1: selected_strategy=None → 返回 None。"""
        vm = ScreenerViewModel()
        assert vm._compute_tier_hint(None) is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_compute_tier_hint_tier_sufficient(self, mock_dp, mock_sm, mock_rm):
        """路径2: 已知策略 + 当前档位 >= 最低档位 → 返回 None。"""
        vm = ScreenerViewModel()
        with (
            patch("utils.config_handler.ConfigHandler.get_tushare_point_tier", return_value="points_5000"),
            patch("services.ai_service.get_strategy_min_tier", return_value="points_120"),
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.get_tier_order.side_effect = lambda tier: {"points_120": 0, "points_5000": 2}.get(tier, 0)
            result = vm._compute_tier_hint("momentum_breakout")
        assert result is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_compute_tier_hint_tier_insufficient(self, mock_dp, mock_sm, mock_rm):
        """路径3: 已知策略 + 当前档位 < 最低档位 → 返回 i18n key。"""
        vm = ScreenerViewModel()
        with (
            patch("utils.config_handler.ConfigHandler.get_tushare_point_tier", return_value="points_120"),
            patch("services.ai_service.get_strategy_min_tier", return_value="points_5000"),
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.get_tier_order.side_effect = lambda tier: {"points_120": 0, "points_5000": 2}.get(tier, 0)
            result = vm._compute_tier_hint("ai_llm_v")
        assert result == "sys_strategy_tier_hint"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_compute_tier_hint_unknown_strategy_defaults_points_120(self, mock_dp, mock_sm, mock_rm):
        """未知策略: get_strategy_min_tier 真实默认 points_120, 当前档位 >= points_120 → 返回 None。

        不 mock get_strategy_min_tier，让真实默认回退路径运行（_STRATEGY_MIN_TIER.get(key, "points_120")）。
        """
        vm = ScreenerViewModel()
        with (
            patch("utils.config_handler.ConfigHandler.get_tushare_point_tier", return_value="points_120"),
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
        ):
            mock_client = mock_client_cls.return_value
            mock_client.get_tier_order.side_effect = lambda tier: {"points_120": 0}.get(tier, 0)
            result = vm._compute_tier_hint("unknown_strategy")
        assert result is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_compute_tier_hint_exception_returns_none(self, mock_dp, mock_sm, mock_rm):
        """路径4: 内部异常时安全返回 None (不传播, 安全降级)。

        ConfigHandler.get_tushare_point_tier 抛 RuntimeError 时, _compute_tier_hint
        应捕获异常并返回 None, 而非传播异常 (保持 View 渲染不中断)。
        """
        vm = ScreenerViewModel()
        with patch(
            "utils.config_handler.ConfigHandler.get_tushare_point_tier",
            side_effect=RuntimeError("boom"),
        ):
            result = vm._compute_tier_hint("value")
        assert result is None


# ============================================================================
# R.2.3: Message.params 不含翻译字符串 (§3.2 VM 只产出 i18n key)
# ============================================================================


class TestScreenerViewModelMessageParamsPurity:
    """R.2.3: Message.params 不含翻译字符串 (§3.2 VM 只产出 i18n key).

    VM 不应在 Message.params 中调用 I18n.get 产生翻译字符串;
    应传递 i18n key (如 name_key), 由 View 渲染时翻译为当前 locale.
    避免 VM 持有翻译字符串导致 locale 切换后 state 残留旧 locale 翻译.
    """

    @pytest.mark.asyncio
    async def test_run_strategy_status_message_uses_name_key(self, vm):
        """R.2.3: run_strategy 启动时 status_message.params 应含 name_key (i18n key),
        不含 name (翻译字符串).

        触发 run_strategy 后立即检查 state.status_message:
        - params["name_key"] == strategy.name_key (raw i18n key)
        - "name" not in params (无翻译字符串)

        注意: submit_task 被 mock 故意不执行 coroutine, 仅验证 submit_task 前同步设置的
        status_message (line 460-471), 不进入 _execute_screening 实际执行路径.
        """
        mock_strategy = MagicMock()
        mock_strategy.name = "test_strategy"
        mock_strategy.name_key = "test_strategy_name_key"
        vm.strategy_mgr.get_strategy = MagicMock(return_value=mock_strategy)
        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": datetime.date(2024, 12, 31),
            }
        )

        with patch("ui.viewmodels.screener_view_model.TaskManager") as mock_tm:
            mock_tm.return_value.update_progress = MagicMock()
            mock_tm.return_value.submit_task = MagicMock(return_value="test_task_id")
            await vm.run_strategy("test_strategy", save_results=False)

        msg = vm.state.status_message
        assert msg is not None
        assert msg.key == "screener_running_strategy"
        assert "name_key" in msg.params, "params 必须含 name_key (i18n key, R.2.3)"
        assert msg.params["name_key"] == "test_strategy_name_key"
        assert "name" not in msg.params, "params 不应含 name (翻译字符串, §3.2)"

    def test_no_i18n_get_in_message_params(self):
        """R.2.3 契约守护: VM 源码中 Message(...) 调用不应在 params 中包含 I18n.get(...) 调用.

        Regex 扫描源码确认 §3.2 契约: VM 只产出 (key, params),
        params 中不应有 I18n.get(...) 翻译调用.

        局限性说明: 本 regex 仅检测 ``Message(... I18n.get(...) ...)`` 字面量内联调用模式,
        无法检测变量赋值后传入场景 (如 ``name = I18n.get("x"); Message("k", {"name": name})``).
        完整守护依赖代码评审; 本测试作为快速回归门禁, 覆盖当前 VM 中所有 11 处 Message 调用模式.
        """
        import re
        from pathlib import Path

        from ui.viewmodels import screener_view_model as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        # 多行匹配 Message(... I18n.get(...) ...) 字面量内联调用模式
        pattern = r"Message\([^)]*I18n\.get\([^)]*\)[^)]*\)"
        matches = re.findall(pattern, src, re.DOTALL)
        assert not matches, f"VM 在 Message.params 中调用了 I18n.get (违反 §3.2 VM 只产出 i18n key): {matches}"
