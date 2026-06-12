"""Unit tests for DataSourceViewModel — MVVM-002 fix.

TDD RED phase: these tests define the expected ViewModel contract.
They will fail until DataSourceViewModel is implemented.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.task_manager import TaskStatus
from ui.viewmodels.data_source_view_model import DataSourceViewModel


# --- Fixtures ---


@pytest.fixture
def mock_processor():
    with patch("ui.viewmodels.data_source_view_model.DataProcessor") as cls:
        instance = MagicMock()
        instance.check_data_health = AsyncMock(
            return_value={
                "status": "green",
                "market": {"latest_local": "2025-01-01", "lag_days": 0},
                "details": {
                    "financial_coverage": 95.0,
                    "missing_critical": 0,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                },
            },
        )
        instance.run_daily_update = AsyncMock()
        instance.run_doubao_tagging = AsyncMock()
        instance.initialize_system = AsyncMock(return_value={"success": True})
        instance.request_cancel = AsyncMock()
        instance.is_cancelled = MagicMock(return_value=False)
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_cache():
    with patch("ui.viewmodels.data_source_view_model.CacheManager") as cls:
        instance = MagicMock()
        instance.clear_all_cache = AsyncMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_task_manager():
    with patch("ui.viewmodels.data_source_view_model.TaskManager") as cls:
        instance = MagicMock()
        instance.submit_task = MagicMock(return_value="task_123")
        instance.get_task = MagicMock()
        instance.get_all_tasks = MagicMock(return_value=[])
        instance.update_progress = MagicMock()
        instance.cancel_task = MagicMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def vm(mock_processor, mock_cache, mock_task_manager):
    return DataSourceViewModel()


@pytest.fixture
def bound_vm(vm):
    """ViewModel with all callbacks bound to MagicMocks."""
    vm.bind(
        on_show_snack=MagicMock(),
        on_sync_busy_changed=MagicMock(),
        on_health_checking=MagicMock(),
        on_health_result=MagicMock(),
        on_health_error=MagicMock(),
        on_health_cancelled=MagicMock(),
        on_health_finished=MagicMock(),
        on_init_sync_started=MagicMock(),
        on_init_sync_reset=MagicMock(),
        on_progress_update=MagicMock(),
        on_cache_cleared=MagicMock(),
    )
    return vm


def _capture_coroutine_factory(mock_submit_task):
    """Extract coroutine_factory from submit_task call."""
    return mock_submit_task.call_args[1]["coroutine_factory"]


# --- Test Classes ---


class TestDataSourceViewModelInit:
    def test_default_dependencies_created(self, vm):
        assert vm._processor is not None
        assert vm._cache is not None
        assert vm._tm is not None

    def test_constructor_injection(self, mock_processor, mock_cache):
        vm = DataSourceViewModel(processor=mock_processor, cache=mock_cache)
        assert vm._processor is mock_processor
        assert vm._cache is mock_cache

    def test_initial_state(self, vm):
        assert vm.is_syncing is False
        assert vm.init_sync_cancellable is False
        assert vm._active_task_ids == {}
        assert vm.on_show_snack is None


class TestDataSourceViewModelBind:
    def test_bind_stores_callbacks(self, vm):
        cb = MagicMock()
        vm.bind(
            on_show_snack=cb,
            on_sync_busy_changed=cb,
            on_health_checking=cb,
            on_health_result=cb,
            on_health_error=cb,
            on_health_cancelled=cb,
            on_health_finished=cb,
            on_init_sync_started=cb,
            on_init_sync_reset=cb,
            on_progress_update=cb,
            on_cache_cleared=cb,
        )
        assert vm.on_show_snack is cb
        assert vm.on_sync_busy_changed is cb
        assert vm.on_health_result is cb
        assert vm.on_cache_cleared is cb

    def test_dispose_clears_callbacks(self, bound_vm):
        bound_vm.dispose()
        assert bound_vm.on_show_snack is None
        assert bound_vm.on_sync_busy_changed is None
        assert bound_vm.on_health_result is None
        assert bound_vm.on_cache_cleared is None
        assert bound_vm.on_init_sync_reset is None


class TestDataSourceViewModelCheckHealth:
    async def test_check_health_success(self, bound_vm, mock_processor, mock_task_manager):
        await bound_vm.check_health()

        bound_vm.on_health_checking.assert_called_once()

        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        bound_vm.on_health_result.assert_called_once()
        result = bound_vm.on_health_result.call_args[0][0]
        assert result["status"] == "green"
        bound_vm.on_health_finished.assert_called()

    async def test_check_health_error(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.check_data_health = AsyncMock(side_effect=RuntimeError("DB down"))

        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with (
            patch("ui.viewmodels.data_source_view_model.classify_error") as mock_classify,
            patch("ui.viewmodels.data_source_view_model.get_error_message") as mock_get_msg,
        ):
            mock_classify.return_value = {"code": "runtime", "message_key": "common_op_fail"}
            mock_get_msg.return_value = "Sanitized error"

            with pytest.raises(RuntimeError, match="DB down"):
                await factory(task_id="task_123")

            mock_classify.assert_called_once()
            mock_get_msg.assert_called_once()
            bound_vm.on_health_error.assert_called_once_with("Sanitized error")

        bound_vm.on_health_finished.assert_called()

    async def test_check_health_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.check_data_health = AsyncMock(side_effect=asyncio.CancelledError())

        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        bound_vm.on_health_cancelled.assert_called_once()
        bound_vm.on_health_finished.assert_called()

    async def test_check_health_task_rejected(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None

        await bound_vm.check_health()
        bound_vm.on_health_finished.assert_called()


class TestDataSourceViewModelFullDailySync:
    def test_execute_sets_sync_busy(self, bound_vm, mock_task_manager):
        bound_vm.execute_full_daily_sync()
        assert bound_vm.is_syncing is True
        bound_vm.on_sync_busy_changed.assert_called_with(True, "daily_sync")

    async def test_daily_sync_success(self, bound_vm, mock_processor, mock_task_manager):
        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        bound_vm.on_show_snack.assert_called()
        assert "daily_sync" in bound_vm._active_task_ids

    async def test_daily_sync_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_daily_update = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        bound_vm.on_show_snack.assert_called()
        assert bound_vm.is_syncing is False
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    async def test_daily_sync_error(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_daily_update = AsyncMock(side_effect=RuntimeError("Network error"))

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        bound_vm.on_show_snack.assert_called()
        assert bound_vm.is_syncing is False
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_task_rejected_resets_busy(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_full_daily_sync()
        assert bound_vm.is_syncing is False
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_tracks_active_task(self, bound_vm, mock_task_manager):
        bound_vm.execute_full_daily_sync()
        assert "daily_sync" in bound_vm._active_task_ids
        assert bound_vm._active_task_ids["daily_sync"] == "task_123"


class TestDataSourceViewModelDoubaoRebuild:
    def test_execute_sets_sync_busy(self, bound_vm):
        bound_vm.execute_doubao_rebuild()
        assert bound_vm.is_syncing is True
        bound_vm.on_sync_busy_changed.assert_called_with(True, "doubao_sync")

    async def test_rebuild_success(self, bound_vm, mock_processor, mock_task_manager):
        bound_vm.execute_doubao_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")
        bound_vm.on_show_snack.assert_called()

    async def test_rebuild_cancelled_propagates(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_doubao_tagging = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_doubao_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        assert bound_vm.is_syncing is False
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_task_rejected_resets_busy(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_doubao_rebuild()
        assert bound_vm.is_syncing is False


class TestDataSourceViewModelClearCache:
    def test_rejects_when_running_tasks(self, bound_vm, mock_task_manager):
        running_task = MagicMock()
        running_task.status = TaskStatus.RUNNING
        running_task.unique_key = "daily_sync"
        mock_task_manager.get_all_tasks.return_value = [running_task]

        bound_vm.execute_clear_cache()

        bound_vm.on_show_snack.assert_called_once()
        assert bound_vm.is_syncing is False

    async def test_clear_cache_success(self, bound_vm, mock_cache, mock_task_manager):
        bound_vm.execute_clear_cache()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        mock_cache.clear_all_cache.assert_awaited_once()
        bound_vm.on_show_snack.assert_called()
        bound_vm.on_cache_cleared.assert_called_once()

    async def test_clear_cache_error(self, bound_vm, mock_cache, mock_task_manager):
        mock_cache.clear_all_cache = AsyncMock(side_effect=RuntimeError("DB error"))

        bound_vm.execute_clear_cache()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        bound_vm.on_show_snack.assert_called()

    def test_clear_cache_resets_busy_on_reject(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_clear_cache()
        assert bound_vm.is_syncing is False


class TestDataSourceViewModelInitHistorical:
    def test_execute_sets_state_and_callbacks(self, bound_vm):
        bound_vm.execute_init_historical_data()
        assert bound_vm.is_syncing is True
        assert bound_vm.init_sync_cancellable is True
        bound_vm.on_init_sync_started.assert_called_once()
        bound_vm.on_sync_busy_changed.assert_called_with(True, "system_init_sync")

    async def test_init_success(self, bound_vm, mock_processor, mock_task_manager):
        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.COMPLETED)
        bound_vm.on_show_snack.assert_called()

    async def test_init_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.CANCELLED)

    async def test_init_failed(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(side_effect=RuntimeError("Sync failed"))

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.FAILED)
        bound_vm.on_show_snack.assert_called()

    async def test_init_none_report_raises(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(return_value=None)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.FAILED)

    async def test_cancel_init_sync(self, bound_vm, mock_processor, mock_task_manager):
        bound_vm._active_task_ids["system_init_sync"] = "task_123"
        await bound_vm.cancel_init_sync()
        mock_processor.request_cancel.assert_awaited_once()
        mock_task_manager.cancel_task.assert_called_with("task_123")

    async def test_init_cancelled_flag_after_success(self, bound_vm, mock_processor, mock_task_manager):
        """initialize_system 正常返回但 is_cancelled 为 True → CancelledError"""
        mock_processor.is_cancelled.return_value = True

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.CANCELLED)

    async def test_init_none_report_raises_init_sync_error(self, bound_vm, mock_processor, mock_task_manager):
        """report=None 时抛 InitSyncError, snack 显示 ds_init_fail_generic 原文"""
        mock_processor.initialize_system = AsyncMock(return_value=None)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.FAILED)
        # InitSyncError 分支: snack 消息是 ds_init_fail_generic 原文, 非 ds_init_fail_fmt 格式
        snack_msg = bound_vm.on_show_snack.call_args[0][0]
        assert "ds_internal_error" not in snack_msg

    def test_task_rejected_resets_state(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_init_historical_data()
        assert bound_vm.is_syncing is False
        assert bound_vm.init_sync_cancellable is False


class TestDataSourceViewModelTaskUpdate:
    def test_noop_when_not_syncing(self, bound_vm):
        bound_vm.is_syncing = False
        bound_vm._active_task_ids = {}
        bound_vm.handle_task_update([])
        bound_vm.on_sync_busy_changed.assert_not_called()

    def test_removes_completed_task(self, bound_vm):
        bound_vm.is_syncing = True
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.COMPLETED

        bound_vm.handle_task_update([task])
        assert "daily_sync" not in bound_vm._active_task_ids
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_handles_failed_task(self, bound_vm):
        bound_vm.is_syncing = True
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.FAILED

        bound_vm.handle_task_update([task])
        assert "daily_sync" not in bound_vm._active_task_ids
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_handles_cancelled_task(self, bound_vm):
        bound_vm.is_syncing = True
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.CANCELLED

        bound_vm.handle_task_update([task])
        assert "daily_sync" not in bound_vm._active_task_ids
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_init_sync_task_terminated_triggers_reset(self, bound_vm):
        bound_vm.is_syncing = True
        bound_vm._active_task_ids = {"system_init_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.COMPLETED

        bound_vm.handle_task_update([task])
        bound_vm.on_init_sync_reset.assert_called_with(TaskStatus.COMPLETED)


class TestDataSourceViewModelRecoverStaleState:
    def test_noop_when_no_active_tasks(self, bound_vm):
        bound_vm.is_syncing = False
        bound_vm._active_task_ids = {}
        bound_vm.recover_stale_state()
        bound_vm.on_sync_busy_changed.assert_not_called()

    def test_cleans_stale_task(self, bound_vm, mock_task_manager):
        bound_vm.is_syncing = True
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.status = TaskStatus.COMPLETED
        mock_task_manager.get_task.return_value = task

        bound_vm.recover_stale_state()
        assert "daily_sync" not in bound_vm._active_task_ids
        bound_vm.on_sync_busy_changed.assert_called_with(False, None)

    def test_cleans_none_task(self, bound_vm, mock_task_manager):
        bound_vm.is_syncing = True
        bound_vm._active_task_ids = {"daily_sync": "task_123"}
        mock_task_manager.get_task.return_value = None

        bound_vm.recover_stale_state()
        assert "daily_sync" not in bound_vm._active_task_ids


class TestDataSourceViewModelSaveToken:
    def test_saves_valid_token(self, bound_vm):
        with patch("ui.viewmodels.data_source_view_model.TushareClient") as mock_tc:
            bound_vm.save_tushare_token("abc123")
            mock_tc.assert_called_once()
            mock_tc.return_value.set_token.assert_called_with("abc123")

    def test_skips_empty_token(self, bound_vm):
        bound_vm.save_tushare_token("  ")
        # Should not raise and should not call ConfigHandler


class TestDataSourceViewModelSetHistoryYears:
    def test_sets_years(self, bound_vm):
        with patch("ui.viewmodels.data_source_view_model.ConfigHandler") as mock_ch:
            bound_vm.set_history_years(3)
            mock_ch.set_init_history_years.assert_called_with(3)


class TestDataSourceViewModelGetHealthReport:
    async def test_returns_report(self, bound_vm, mock_processor):
        result = await bound_vm.get_health_report()
        mock_processor.check_data_health.assert_awaited_once()
        assert result == mock_processor.check_data_health.return_value
