"""Unit tests for DataSourceViewModel — MVVM-002 fix.

Phase 2 改造: 11 个 on_* 回调移除,改用 state snapshot + subscribe/_notify。
- 状态型字段直接放入 frozen DataSourceState (is_syncing/health_checking/init_sync_running 等)
- 瞬态事件/大体积数据用 dual-track (§3.0.4): version 递增 + last_* property
- 测试用 snapshots list + state 字段断言 + last_* property 断言替代 on_* mock 断言
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.i18n import I18n
from services.task_manager import TaskStatus
from ui.viewmodels.data_source_view_model import DataSourceViewModel

pytestmark = pytest.mark.unit

# --- Helpers ---


def _count_transitions(snapshots, field_getter, initial) -> int:
    """Count state transitions in snapshots for a given field.

    A transition occurs when consecutive snapshots (including the initial state)
    have different values for the field returned by field_getter(snapshots[i]).
    The `initial` argument represents the state value before any snapshots.
    """
    transitions = 0
    prev = initial
    for s in snapshots:
        value = field_getter(s)
        if value != prev:
            transitions += 1
            prev = value
    return transitions


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
        instance.run_ai_concept_tagging = AsyncMock()
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
def mock_ai_service():
    # T6 fix: AIService 通过构造注入到 ViewModel，需 mock 避免真实单例初始化
    with patch("ui.viewmodels.data_source_view_model.AIService") as cls:
        instance = MagicMock()
        instance.is_cloud_available = MagicMock(return_value=True)
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
def vm(mock_processor, mock_cache, mock_ai_service, mock_task_manager):
    return DataSourceViewModel()


@pytest.fixture
def snapshots():
    """List to collect DataSourceState snapshots from bound_vm."""
    return []


@pytest.fixture
def bound_vm(vm, snapshots):
    """ViewModel subscribed to snapshot collector.

    Phase 2 改造: 11 个 on_* 回调移除,改用 state snapshot + subscribe。
    snapshots fixture 自动订阅 bound_vm 的 state 变化。
    """
    vm.subscribe(lambda s: snapshots.append(s))
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
        assert vm._ai_service is not None  # T6 fix: 验证 AIService 注入

    def test_constructor_injection(self, mock_processor, mock_cache, mock_ai_service):
        # T6 fix: ai_service 也支持构造注入，与 _processor / _cache 一致
        vm = DataSourceViewModel(processor=mock_processor, cache=mock_cache, ai_service=mock_ai_service)
        assert vm._processor is mock_processor
        assert vm._cache is mock_cache
        assert vm._ai_service is mock_ai_service

    def test_initial_state(self, vm):
        assert vm.state.is_syncing is False
        assert vm.state.init_sync_cancellable is False
        assert vm._active_task_ids == {}
        assert vm.last_snack is None
        assert vm.last_health_result is None
        assert vm.last_health_error is None


class TestDataSourceViewModelSubscribe:
    """Phase 2 改造: subscribe / dispose 契约 (替代 bind/on_* 回调)。"""

    def test_subscribe_stores_callback(self, vm):
        cb = MagicMock()
        vm.subscribe(cb)
        assert cb in vm._subscribers

    def test_subscribe_returns_unsubscribe_and_removes_callback(self, vm):
        received: list = []
        unsub = vm.subscribe(lambda s: received.append(s))

        vm._set_state(is_syncing=True)
        assert len(received) == 1

        unsub()
        vm._set_state(is_syncing=False)
        assert len(received) == 1  # unsubscribe 后不再接收

    def test_dispose_clears_state_and_subscribers(self, bound_vm, snapshots):
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        bound_vm._emit_snack("msg", "success")
        assert len(snapshots) > 0

        bound_vm.dispose()

        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None
        assert bound_vm.state.snack_version == 0
        assert bound_vm.last_snack is None
        assert bound_vm.last_health_result is None
        assert bound_vm.last_health_error is None
        assert bound_vm._subscribers == []

        # dispose 后 _notify 无订阅者,snapshots 不再增长
        prev_count = len(snapshots)
        bound_vm._set_state(is_syncing=True)
        assert len(snapshots) == prev_count


class TestDataSourceViewModelCheckHealth:
    async def test_check_health_success(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        await bound_vm.check_health()

        # health_checking started (False → True)
        assert any(s.health_checking is True for s in snapshots)

        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        # health_result emitted (dual-track)
        assert any(s.health_result_version > 0 for s in snapshots)
        assert bound_vm.last_health_result is not None
        assert bound_vm.last_health_result["status"] == "green"

        # health_finished: health_checking back to False (False → True → False)
        assert snapshots[-1].health_checking is False
        assert _count_transitions(snapshots, lambda s: s.health_checking, initial=False) >= 2

    async def test_check_health_error(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.check_data_health = AsyncMock(side_effect=RuntimeError("DB down"))

        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with (
            patch("ui.viewmodels.data_source_view_model.classify_error") as mock_classify,
            patch("ui.viewmodels.data_source_view_model.get_error_message") as mock_get_msg,
        ):
            mock_classify.return_value = {
                "code": "runtime",
                "message_key": "common_op_fail",
            }
            mock_get_msg.return_value = "Sanitized error"

            with pytest.raises(RuntimeError, match="DB down"):
                await factory(task_id="task_123")

            mock_classify.assert_called_once()
            mock_get_msg.assert_called_once()
            # health_error emitted (dual-track)
            assert bound_vm.last_health_error == "Sanitized error"
            assert any(s.health_error_version > 0 for s in snapshots)

        # health_finished: health_checking back to False
        assert snapshots[-1].health_checking is False

    async def test_check_health_cancelled(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.check_data_health = AsyncMock(side_effect=asyncio.CancelledError())

        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        # cancelled: health_checking transitioned to False, no result/error emitted
        assert snapshots[-1].health_checking is False
        assert not any(s.health_result_version > 0 for s in snapshots)
        assert not any(s.health_error_version > 0 for s in snapshots)

    async def test_check_health_task_rejected(self, bound_vm, snapshots, mock_task_manager):
        mock_task_manager.submit_task.return_value = None

        await bound_vm.check_health()
        # rejected: health_checking set back to False
        assert snapshots[-1].health_checking is False


class TestDataSourceViewModelFullDailySync:
    def test_execute_sets_sync_busy(self, bound_vm):
        bound_vm.execute_full_daily_sync()
        assert bound_vm.state.is_syncing is True
        assert bound_vm.state.active_key == "daily_sync"

    async def test_daily_sync_success(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        assert bound_vm.last_snack == (I18n.get("snack_full_sync_done_simple"), "success")
        assert any(s.snack_version > 0 for s in snapshots)
        assert "daily_sync" in bound_vm._active_task_ids

    async def test_daily_sync_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_daily_update = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        assert bound_vm.last_snack == (I18n.get("settings_msg_sync_cancelled"), "warning")
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None

    async def test_daily_sync_error(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_daily_update = AsyncMock(side_effect=RuntimeError("Network error"))

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        assert bound_vm.last_snack == (I18n.get("common_op_fail"), "error")
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None

    def test_task_rejected_resets_busy(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_full_daily_sync()
        assert bound_vm.state.is_syncing is False

    def test_tracks_active_task(self, bound_vm, mock_task_manager):
        bound_vm.execute_full_daily_sync()
        assert "daily_sync" in bound_vm._active_task_ids
        assert bound_vm._active_task_ids["daily_sync"] == "task_123"

    async def test_t8_progress_false_raises_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        """O1 fix: _daily_logic._progress 回调在 update_progress 返回 False 时应抛 CancelledError 早退。"""
        mock_task_manager.update_progress = MagicMock(return_value=False)

        async def _call_progress(*args, progress_callback=None, **kwargs):
            assert progress_callback is not None
            progress_callback(1, 10, "step1")

        mock_processor.run_daily_update = AsyncMock(side_effect=_call_progress)

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")
        # 副作用验证：CancelledError 被外层 except 捕获后应触发 snack warning
        assert bound_vm.last_snack == (I18n.get("settings_msg_sync_cancelled"), "warning")
        # finally 应重置 sync busy 状态
        assert bound_vm.state.is_syncing is False


class TestDataSourceViewModelAiConceptRebuild:
    def test_execute_sets_sync_busy(self, bound_vm):
        bound_vm.execute_ai_concept_rebuild()
        assert bound_vm.state.is_syncing is True
        assert bound_vm.state.active_key == "ai_concept_sync"

    async def test_rebuild_success(self, bound_vm, mock_processor, mock_task_manager):
        bound_vm.execute_ai_concept_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")
        assert bound_vm.last_snack == (I18n.get("snack_ai_concept_done"), "success")
        # 验证通过 get_cancel_event 访问器获取取消事件（P0-2 取消链路）
        mock_task_manager.get_cancel_event.assert_called_once_with("task_123")
        # 验证 manual_trigger=True + cancel_event + ai_service 参数正确传递
        mock_processor.run_ai_concept_tagging.assert_called_once()
        kwargs = mock_processor.run_ai_concept_tagging.call_args.kwargs
        assert kwargs.get("manual_trigger") is True
        assert kwargs.get("cancel_event") is mock_task_manager.get_cancel_event.return_value
        assert "ai_service" in kwargs

    async def test_rebuild_cancelled_propagates(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_ai_concept_tagging = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_ai_concept_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None

    def test_task_rejected_resets_busy(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_ai_concept_rebuild()
        assert bound_vm.state.is_syncing is False

    async def test_t8_update_progress_false_raises_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        """T8 fix: update_progress 返回 False（任务已取消/不再 RUNNING）时，应立即 raise CancelledError 早退。"""
        mock_task_manager.update_progress = MagicMock(return_value=False)
        bound_vm.execute_ai_concept_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")
        # 验证后续的 processor 调用未执行（早退生效）
        mock_processor.run_ai_concept_tagging.assert_not_called()


class TestDataSourceViewModelHealthCheckT8:
    """T8 fix: health check 任务（cancellable=True）的 update_progress 早退验证。"""

    async def test_t8_update_progress_false_raises_cancelled(
        self, bound_vm, snapshots, mock_processor, mock_task_manager
    ):
        """health check 第一次 update_progress 返回 False 时立即 raise CancelledError。"""
        mock_task_manager.update_progress = MagicMock(return_value=False)
        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")
        # 验证后续 processor.check_data_health 未执行
        mock_processor.check_data_health.assert_not_called()
        # cancelled: health_checking back to False, no result/error
        assert snapshots[-1].health_checking is False
        assert not any(s.health_result_version > 0 for s in snapshots)


class TestDataSourceViewModelClearCache:
    def test_rejects_when_running_tasks(self, bound_vm, snapshots, mock_task_manager):
        running_task = MagicMock()
        running_task.status = TaskStatus.RUNNING
        running_task.unique_key = "daily_sync"
        mock_task_manager.get_all_tasks.return_value = [running_task]

        bound_vm.execute_clear_cache()

        # snack emitted (warning)
        assert bound_vm.last_snack == (I18n.get("ds_clear_cache_syncing"), "warning")
        assert any(s.snack_version > 0 for s in snapshots)
        assert bound_vm.state.is_syncing is False

    async def test_clear_cache_success(self, bound_vm, snapshots, mock_cache, mock_task_manager):
        bound_vm.execute_clear_cache()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        mock_cache.clear_all_cache.assert_awaited_once()
        assert bound_vm.last_snack == (I18n.get("ds_cache_cleared"), "success")
        # cache_cleared emitted (dual-track)
        assert any(s.cache_cleared_version > 0 for s in snapshots)

    async def test_clear_cache_error(self, bound_vm, mock_cache, mock_task_manager):
        mock_cache.clear_all_cache = AsyncMock(side_effect=RuntimeError("DB error"))

        bound_vm.execute_clear_cache()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        assert bound_vm.last_snack == (I18n.get("ds_clean_fail"), "error")

    def test_clear_cache_resets_busy_on_reject(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_clear_cache()
        assert bound_vm.state.is_syncing is False


class TestDataSourceViewModelInitHistorical:
    def test_execute_sets_state_and_callbacks(self, bound_vm, snapshots):
        bound_vm.execute_init_historical_data()
        assert bound_vm.state.is_syncing is True
        assert bound_vm.state.init_sync_cancellable is True
        # init_sync_started: init_sync_running transitioned to True
        assert any(s.init_sync_running is True for s in snapshots)
        # sync_busy: is_syncing=True, active_key="system_init_sync"
        assert any(s.is_syncing is True and s.active_key == "system_init_sync" for s in snapshots)

    async def test_init_success(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        # init_sync_reset with COMPLETED
        assert any(s.init_sync_final_status == TaskStatus.COMPLETED for s in snapshots)
        assert bound_vm.last_snack == (I18n.get("settings_init_done"), "success")

    async def test_init_cancelled(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.CANCELLED for s in snapshots)

    async def test_init_failed(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(side_effect=RuntimeError("Sync failed"))

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.FAILED for s in snapshots)
        assert bound_vm.last_snack == (I18n.get("ds_init_fail_fmt"), "error")

    async def test_init_none_report_raises(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(return_value=None)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.FAILED for s in snapshots)

    async def test_cancel_init_sync(self, bound_vm, mock_processor, mock_task_manager):
        bound_vm._active_task_ids["system_init_sync"] = "task_123"
        await bound_vm.cancel_init_sync()
        mock_processor.request_cancel.assert_awaited_once()
        mock_task_manager.cancel_task.assert_called_with("task_123")

    async def test_init_cancelled_flag_after_success(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        """initialize_system 正常返回但 is_cancelled 为 True → CancelledError"""
        mock_processor.is_cancelled.return_value = True

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.CANCELLED for s in snapshots)

    async def test_init_none_report_raises_init_sync_error(
        self, bound_vm, snapshots, mock_processor, mock_task_manager
    ):
        """report=None 时抛 InitSyncError, snack 显示 ds_init_fail_generic 原文"""
        mock_processor.initialize_system = AsyncMock(return_value=None)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.FAILED for s in snapshots)
        # InitSyncError 分支: snack 消息是 ds_init_fail_generic 原文, 非 ds_init_fail_fmt 格式
        snack_msg = bound_vm.last_snack[0]
        assert "ds_internal_error" not in snack_msg

    def test_task_rejected_resets_state(self, bound_vm, mock_task_manager):
        mock_task_manager.submit_task.return_value = None
        bound_vm.execute_init_historical_data()
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.init_sync_cancellable is False

    async def test_t8_init_sync_progress_false_raises_cancelled(self, bound_vm, mock_processor, mock_task_manager):
        """H3 fix: init_sync _combined_progress 在 update_progress 返回 False 时应早退。

        场景：cancellable=True 任务被用户取消，progress_callback 第一次调用时 update_progress
        返回 False，应立即抛 CancelledError 中断 initialize_system。
        """
        mock_task_manager.update_progress = MagicMock(return_value=False)

        # 让 initialize_system 调用 progress_callback 以触发 T8 早退
        async def _fake_initialize(*args, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                cb(1, 10, "step1")  # 触发 _combined_progress → update_progress 返回 False → raise CancelledError
            return None

        mock_processor.initialize_system = AsyncMock(side_effect=_fake_initialize)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(asyncio.CancelledError):
            await factory(task_id="task_123")
        # 验证 initialize_system 被调用但内部未完成（CancelledError 中断）
        mock_processor.initialize_system.assert_awaited_once()


class TestDataSourceViewModelTaskUpdate:
    def test_noop_when_not_syncing(self, bound_vm, snapshots):
        bound_vm.handle_task_update([])
        # no state changes (early return)
        assert not snapshots

    def test_removes_completed_task(self, bound_vm, snapshots):
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.COMPLETED

        bound_vm.handle_task_update([task])
        assert "daily_sync" not in bound_vm._active_task_ids
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None

    def test_handles_failed_task(self, bound_vm):
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.FAILED

        bound_vm.handle_task_update([task])
        assert "daily_sync" not in bound_vm._active_task_ids
        assert bound_vm.state.is_syncing is False

    def test_handles_cancelled_task(self, bound_vm):
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.CANCELLED

        bound_vm.handle_task_update([task])
        assert "daily_sync" not in bound_vm._active_task_ids
        assert bound_vm.state.is_syncing is False

    def test_init_sync_task_terminated_triggers_reset(self, bound_vm, snapshots):
        bound_vm._set_state(is_syncing=True, active_key="system_init_sync")
        bound_vm._active_task_ids = {"system_init_sync": "task_123"}

        task = MagicMock()
        task.id = "task_123"
        task.status = TaskStatus.COMPLETED

        bound_vm.handle_task_update([task])
        assert any(s.init_sync_final_status == TaskStatus.COMPLETED for s in snapshots)


class TestDataSourceViewModelRecoverStaleState:
    def test_noop_when_no_active_tasks(self, bound_vm, snapshots):
        bound_vm.recover_stale_state()
        # no state changes (early return)
        assert not snapshots

    def test_cleans_stale_task(self, bound_vm, mock_task_manager):
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        bound_vm._active_task_ids = {"daily_sync": "task_123"}

        task = MagicMock()
        task.status = TaskStatus.COMPLETED
        mock_task_manager.get_task.return_value = task

        bound_vm.recover_stale_state()
        assert "daily_sync" not in bound_vm._active_task_ids
        assert bound_vm.state.is_syncing is False

    def test_cleans_none_task(self, bound_vm, mock_task_manager):
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
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
