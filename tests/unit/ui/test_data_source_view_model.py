"""Unit tests for DataSourceViewModel — MVVM-002 fix.

H2-2 改造: 移除 dual-track, state 字段全部用 frozen dataclass / Message (L771 合规).
- 状态型字段直接放入 frozen DataSourceState (is_syncing/health_checking/init_sync_running 等)
- 业务数据 (health_result/snack/health_error) 直接放入 state (frozen dataclass / Message),
  无 dual-track (version + last_* property) 间接暴露
- 测试用 snapshots list + state 字段断言 (替代 last_* property 断言)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from services.ai_service import AIService
from services.task_manager import TaskManager, TaskStatus
from ui.viewmodels import Message
from ui.viewmodels.data_source_view_model import DataSourceViewModel, HealthResultRow

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
        instance = MagicMock(spec=DataProcessor)
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
        instance = MagicMock(spec=CacheManager)
        instance.clear_all_cache = AsyncMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_ai_service():
    # T6 fix: AIService 通过构造注入到 ViewModel，需 mock 避免真实单例初始化
    with patch("ui.viewmodels.data_source_view_model.AIService") as cls:
        instance = MagicMock(spec=AIService)
        instance.is_cloud_available = MagicMock(return_value=True)
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_task_manager():
    with patch("ui.viewmodels.data_source_view_model.TaskManager") as cls:
        instance = MagicMock(spec=TaskManager)
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


def _assert_snack(bound_vm, snapshots, message_key, color_name):
    """断言 snack 已发射 (L771 合规: 直接从 state.snack 读取, 无 last_* property)."""
    assert bound_vm.state.snack is not None
    assert bound_vm.state.snack.message == Message(message_key)
    assert bound_vm.state.snack.color_name == color_name
    assert any(s.snack is not None for s in snapshots)


# --- Test Classes ---


class TestDataSourceViewModelInit:
    def test_default_dependencies_created(self, vm):
        assert isinstance(vm._processor, DataProcessor)
        assert isinstance(vm._cache, CacheManager)
        assert isinstance(vm._tm, TaskManager)
        assert isinstance(vm._ai_service, AIService)  # T6 fix: 验证 AIService 注入

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
        assert vm.state.snack is None
        assert vm.state.health_result is None
        assert vm.state.health_error is None


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
        bound_vm._emit_snack(Message("msg"), "success")
        assert len(snapshots) > 0

        bound_vm.dispose()

        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None
        assert bound_vm.state.snack is None
        assert bound_vm.state.health_result is None
        assert bound_vm.state.health_error is None
        assert bound_vm._subscribers == []

        # dispose 后 _notify 无订阅者,snapshots 不再增长
        prev_count = len(snapshots)
        bound_vm._set_state(is_syncing=True)
        assert len(snapshots) == prev_count


class TestDataSourceViewModelDisposeCancelsTasks:
    """R.1.2: dispose() 必须先取消所有活跃任务再清引用，防止孤儿任务。"""

    def test_dispose_cancels_all_active_tasks(self, vm, mock_task_manager):
        """dispose() 遍历 _active_task_ids 逐一调 cancel_task（R.1.2）。"""
        vm._active_task_ids = {
            "daily_sync": "task_001",
            "ai_concept_sync": "task_002",
        }
        vm._set_state(
            is_syncing=True,
            active_key="daily_sync",
            health_checking=True,
            init_sync_running=True,
            progress=0.5,
            progress_message=Message("msg"),
        )
        vm._emit_snack(Message("snack"), "success")
        vm._emit_health_result(HealthResultRow(status="green"))
        vm._emit_health_error(Message("err"))

        vm.dispose()

        assert mock_task_manager.cancel_task.call_count == 2
        mock_task_manager.cancel_task.assert_any_call("task_001")
        mock_task_manager.cancel_task.assert_any_call("task_002")
        assert vm._active_task_ids == {}
        assert vm._subscribers == []
        # 完整 state 重置断言（与 R.1.1 对齐）
        assert vm.state.is_syncing is False
        assert vm.state.active_key is None
        assert vm.state.health_checking is False
        assert vm.state.init_sync_running is False
        assert vm.state.init_sync_cancellable is False
        assert vm.state.init_sync_final_status is None
        assert vm.state.progress == 0.0
        assert vm.state.progress_message is None
        assert vm.state.cache_cleared_version == 0
        # L771 合规: 业务数据字段 (frozen dataclass / Message) 重置为 None
        assert vm.state.snack is None
        assert vm.state.health_result is None
        assert vm.state.health_error is None

    def test_dispose_cancels_non_cancellable_task(self, vm, mock_task_manager):
        """dispose() 对 cancellable=False 任务仍调 cancel_task（TaskManager 内部 no-op，R.1.2）。"""
        vm._active_task_ids = {"cache_clear": "task_003"}

        vm.dispose()

        mock_task_manager.cancel_task.assert_called_once_with("task_003")
        assert vm._active_task_ids == {}

    def test_dispose_no_active_tasks_is_noop(self, vm, mock_task_manager):
        """dispose() 在无活跃任务时不应调用 cancel_task（幂等性，R.1.2）。"""
        vm.dispose()

        mock_task_manager.cancel_task.assert_not_called()
        assert vm._active_task_ids == {}

    def test_dispose_is_idempotent(self, vm, mock_task_manager):
        """dispose() 连续调用两次：第二次不应重复调 cancel_task（R.1.2 幂等性）。"""
        vm._active_task_ids = {"daily_sync": "task_001"}

        vm.dispose()
        vm.dispose()

        mock_task_manager.cancel_task.assert_called_once_with("task_001")
        assert vm._active_task_ids == {}


class TestDataSourceViewModelCheckHealth:
    async def test_check_health_success(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        await bound_vm.check_health()

        # health_checking started (False → True)
        assert any(s.health_checking is True for s in snapshots)

        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        # health_result emitted (L771 合规: 直接放入 state, 无 dual-track)
        assert any(s.health_result is not None for s in snapshots)
        assert bound_vm.state.health_result is not None
        assert bound_vm.state.health_result.status == "green"

        # health_finished: health_checking back to False (False → True → False)
        assert snapshots[-1].health_checking is False
        assert _count_transitions(snapshots, lambda s: s.health_checking, initial=False) >= 2

    async def test_check_health_error(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        db_down_error = RuntimeError("DB down")
        mock_processor.check_data_health = AsyncMock(side_effect=db_down_error)

        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with patch("ui.viewmodels.data_source_view_model.classify_error") as mock_classify:
            mock_classify.return_value = {
                "code": "runtime",
                "message_key": "common_op_fail",
            }

            with pytest.raises(RuntimeError, match="DB down"):
                await factory(task_id="task_123")

            mock_classify.assert_called_once_with(db_down_error, context="general")
            # health_error emitted as Message (VM 不感知 locale, 透传 i18n key + params)
            assert bound_vm.state.health_error is not None
            assert bound_vm.state.health_error.key == "common_op_fail"
            assert any(s.health_error is not None for s in snapshots)

        # health_finished: health_checking back to False
        assert snapshots[-1].health_checking is False

    async def test_check_health_cancelled(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.check_data_health = AsyncMock(side_effect=asyncio.CancelledError())

        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await factory(task_id="task_123")
        assert isinstance(exc_info.value, asyncio.CancelledError)

        # cancelled: health_checking transitioned to False, no result/error emitted
        assert snapshots[-1].health_checking is False
        assert not any(s.health_result is not None for s in snapshots)
        assert not any(s.health_error is not None for s in snapshots)

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

        _assert_snack(bound_vm, snapshots, "snack_full_sync_done_simple", "success")
        assert "daily_sync" in bound_vm._active_task_ids

    async def test_daily_sync_cancelled(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.run_daily_update = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await factory(task_id="task_123")
        assert isinstance(exc_info.value, asyncio.CancelledError)

        _assert_snack(bound_vm, snapshots, "settings_msg_sync_cancelled", "warning")
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None

    async def test_daily_sync_error(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.run_daily_update = AsyncMock(side_effect=RuntimeError("Network error"))

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError, match="Network error"):
            await factory(task_id="task_123")

        _assert_snack(bound_vm, snapshots, "common_op_fail", "error")
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

    async def test_t8_progress_false_raises_cancelled(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        """O1 fix: _daily_logic._progress 回调在 update_progress 返回 False 时应抛 CancelledError 早退。"""
        mock_task_manager.update_progress = MagicMock(return_value=False)

        async def _call_progress(*args, progress_callback=None, **kwargs):
            assert progress_callback is not None
            progress_callback(1, 10, "step1")

        mock_processor.run_daily_update = AsyncMock(side_effect=_call_progress)

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(asyncio.CancelledError, match="task cancelled by user"):
            await factory(task_id="task_123")
        # 副作用验证：CancelledError 被外层 except 捕获后应触发 snack warning
        _assert_snack(bound_vm, snapshots, "settings_msg_sync_cancelled", "warning")
        # finally 应重置 sync busy 状态
        assert bound_vm.state.is_syncing is False


class TestDataSourceViewModelAiConceptRebuild:
    def test_execute_sets_sync_busy(self, bound_vm):
        bound_vm.execute_ai_concept_rebuild()
        assert bound_vm.state.is_syncing is True
        assert bound_vm.state.active_key == "ai_concept_sync"

    async def test_rebuild_success(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        bound_vm.execute_ai_concept_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")
        _assert_snack(bound_vm, snapshots, "snack_ai_concept_done", "success")
        # 验证通过 get_cancel_event 访问器获取取消事件（P0-2 取消链路）
        mock_task_manager.get_cancel_event.assert_called_once_with("task_123")
        # 验证 manual_trigger=True + cancel_event + ai_service 参数正确传递
        mock_processor.run_ai_concept_tagging.assert_awaited_once_with(
            task_id="task_123",
            cancel_event=mock_task_manager.get_cancel_event.return_value,
            manual_trigger=True,
            ai_service=bound_vm._ai_service,
        )
        kwargs = mock_processor.run_ai_concept_tagging.call_args.kwargs
        assert kwargs.get("manual_trigger") is True
        assert kwargs.get("cancel_event") is mock_task_manager.get_cancel_event.return_value
        assert "ai_service" in kwargs

    async def test_rebuild_cancelled_propagates(self, bound_vm, mock_processor, mock_task_manager):
        mock_processor.run_ai_concept_tagging = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_ai_concept_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await factory(task_id="task_123")
        assert isinstance(exc_info.value, asyncio.CancelledError)

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
        with pytest.raises(asyncio.CancelledError, match="task cancelled by user"):
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
        with pytest.raises(asyncio.CancelledError, match="task cancelled by user"):
            await factory(task_id="task_123")
        # 验证后续 processor.check_data_health 未执行
        mock_processor.check_data_health.assert_not_called()
        # cancelled: health_checking back to False, no result/error
        assert snapshots[-1].health_checking is False
        assert not any(s.health_result is not None for s in snapshots)


class TestDataSourceViewModelClearCache:
    def test_rejects_when_running_tasks(self, bound_vm, snapshots, mock_task_manager):
        running_task = MagicMock()
        running_task.status = TaskStatus.RUNNING
        running_task.unique_key = "daily_sync"
        mock_task_manager.get_all_tasks.return_value = [running_task]

        bound_vm.execute_clear_cache()

        # snack emitted (warning)
        _assert_snack(bound_vm, snapshots, "ds_clear_cache_syncing", "warning")
        assert bound_vm.state.is_syncing is False

    async def test_clear_cache_success(self, bound_vm, snapshots, mock_cache, mock_task_manager):
        bound_vm.execute_clear_cache()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        mock_cache.clear_all_cache.assert_awaited_once()
        _assert_snack(bound_vm, snapshots, "ds_cache_cleared", "success")
        # cache_cleared emitted (瞬态信号, 非 dual-track)
        assert any(s.cache_cleared_version > 0 for s in snapshots)

    async def test_clear_cache_error(self, bound_vm, snapshots, mock_cache, mock_task_manager):
        mock_cache.clear_all_cache = AsyncMock(side_effect=RuntimeError("DB error"))

        bound_vm.execute_clear_cache()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError, match="DB error"):
            await factory(task_id="task_123")

        _assert_snack(bound_vm, snapshots, "ds_clean_fail", "error")

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
        _assert_snack(bound_vm, snapshots, "settings_init_done", "success")

    async def test_init_cancelled(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(side_effect=asyncio.CancelledError())

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await factory(task_id="task_123")
        assert isinstance(exc_info.value, asyncio.CancelledError)

        assert any(s.init_sync_final_status == TaskStatus.CANCELLED for s in snapshots)

    async def test_init_failed(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(side_effect=RuntimeError("Sync failed"))

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError, match="ds_init_fail_fmt"):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.FAILED for s in snapshots)
        _assert_snack(bound_vm, snapshots, "ds_init_fail_fmt", "error")

    async def test_init_none_report_raises(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        mock_processor.initialize_system = AsyncMock(return_value=None)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError, match="ds_init_fail_generic"):
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

        with pytest.raises(asyncio.CancelledError, match="task cancelled by user"):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.CANCELLED for s in snapshots)

    async def test_init_none_report_raises_init_sync_error(
        self, bound_vm, snapshots, mock_processor, mock_task_manager
    ):
        """report=None 时抛 InitSyncError, snack 显示 ds_init_fail_generic 原文"""
        mock_processor.initialize_system = AsyncMock(return_value=None)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)

        with pytest.raises(RuntimeError, match="ds_init_fail_generic"):
            await factory(task_id="task_123")

        assert any(s.init_sync_final_status == TaskStatus.FAILED for s in snapshots)
        # InitSyncError 分支: snack 消息是 ds_init_fail_generic key, 非 ds_init_fail_fmt key
        _assert_snack(bound_vm, snapshots, "ds_init_fail_generic", "error")

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
        with pytest.raises(asyncio.CancelledError, match="task cancelled by user"):
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
    async def test_saves_valid_token(self, bound_vm):
        with (
            patch("ui.viewmodels.data_source_view_model.TushareClient") as mock_tc,
            patch("ui.viewmodels.data_source_view_model.ThreadPoolManager") as mock_tpm,
        ):
            # ThreadPoolManager.run_async 直接调用 func (不经线程池, 便于同步验证)
            async def _fake_run_async(task_type, func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_tpm.return_value.run_async = _fake_run_async
            await bound_vm.save_tushare_token("abc123")
            mock_tc.assert_called_once_with()
            mock_tc.return_value.set_token.assert_called_with("abc123")

    async def test_skips_empty_token(self, bound_vm):
        await bound_vm.save_tushare_token("  ")
        # Should not raise and should not call ConfigHandler


class TestDataSourceViewModelSetHistoryYears:
    async def test_sets_years(self, bound_vm):
        with (
            patch("ui.viewmodels.data_source_view_model.ConfigHandler") as mock_ch,
            patch("ui.viewmodels.data_source_view_model.ThreadPoolManager") as mock_tpm,
        ):
            # ThreadPoolManager.run_async 直接调用 func (不经线程池, 便于同步验证)
            async def _fake_run_async(task_type, func, *args, **kwargs):
                return func(*args, **kwargs)

            mock_tpm.return_value.run_async = _fake_run_async
            await bound_vm.set_history_years(3)
            mock_ch.set_init_history_years.assert_called_with(3)


class TestDataSourceViewModelGetHealthReport:
    async def test_returns_report(self, bound_vm, mock_processor):
        result = await bound_vm.get_health_report()
        mock_processor.check_data_health.assert_awaited_once()
        assert result == mock_processor.check_data_health.return_value


class TestDataSourceViewModelCoverageFill:
    """补充覆盖:unsubscribe 幂等性、私有 helper 早退、command 异常路径与边界分支。

    覆盖原 0% → 94% 报告中剩余的 missing 行:
    - 131->exit: unsubscribe 在 callback 已移除时的 no-op 分支
    - 202: _reset_init_sync 在 is_syncing=False 时早退
    - 218: _recover_after_task_terminated 在 is_syncing=False 时早退
    - 238: check_health 第二次 update_progress(0.9) 返回 False 时早退
    - 278->exit / 429->exit: progress 回调 t=0 时不抛 ZeroDivisionError
    - 348-354: ai_concept_rebuild Exception 分支
    - 485: cancel_init_sync 在无 system_init_sync 任务时跳过 cancel_task
    - 535->533 / 544->exit: recover_stale_state 保留 RUNNING 任务
    """

    def test_unsubscribe_twice_is_noop(self, vm):
        """重复 unsubscribe 已被移除的 callback 不抛异常（幂等性，131->exit）。"""
        cb = MagicMock()
        unsub = vm.subscribe(cb)
        unsub()
        # 第二次调用: callback 已不在 _subscribers, if 分支为 False, no-op
        unsub()
        assert cb not in vm._subscribers

    def test_reset_init_sync_noop_when_not_syncing(self, vm, snapshots):
        """is_syncing=False 时 _reset_init_sync 早退,不触发 _set_state（202）。"""
        assert vm.state.is_syncing is False
        prev_count = len(snapshots)
        vm._reset_init_sync(TaskStatus.COMPLETED)
        assert len(snapshots) == prev_count
        assert vm.state.init_sync_final_status is None

    def test_recover_after_task_terminated_noop_when_not_syncing(self, vm, snapshots):
        """is_syncing=False 时 _recover_after_task_terminated 早退,不触发 _set_state（218）。"""
        assert vm.state.is_syncing is False
        prev_count = len(snapshots)
        vm._recover_after_task_terminated("daily_sync", TaskStatus.COMPLETED)
        assert len(snapshots) == prev_count

    async def test_check_health_second_update_progress_false_raises_cancelled(
        self, bound_vm, snapshots, mock_processor, mock_task_manager
    ):
        """check_health 第二次 update_progress(0.9) 返回 False 时抛 CancelledError 早退（238）。

        场景: 第一次 update_progress(0.2) 返回 True 通过, check_data_health 执行成功,
        但第二次 update_progress(0.9) 返回 False（任务被取消/不再 RUNNING）, 立即早退。
        """
        # 第一次(0.2) True, 第二次(0.9) False
        mock_task_manager.update_progress = MagicMock(side_effect=[True, False])
        await bound_vm.check_health()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(asyncio.CancelledError, match="task cancelled by user"):
            await factory(task_id="task_123")
        # 第二次早退, _emit_health_result 未调用
        assert bound_vm.state.health_result is None
        assert not any(s.health_result is not None for s in snapshots)

    async def test_ai_concept_rebuild_error_emits_snack(self, bound_vm, snapshots, mock_processor, mock_task_manager):
        """AI concept rebuild 抛 Exception 时 emit error snack 并 re-raise（348-354）。"""
        mock_processor.run_ai_concept_tagging = AsyncMock(side_effect=RuntimeError("LLM down"))
        bound_vm.execute_ai_concept_rebuild()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        with pytest.raises(RuntimeError, match="LLM down"):
            await factory(task_id="task_123")
        # error snack emitted
        _assert_snack(bound_vm, snapshots, "common_op_fail", "error")
        # finally 重置 sync busy
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None

    async def test_cancel_init_sync_no_active_task(self, bound_vm, mock_processor, mock_task_manager):
        """cancel_init_sync 在 _active_task_ids 无 system_init_sync 时仅 await request_cancel（485）。"""
        assert "system_init_sync" not in bound_vm._active_task_ids
        await bound_vm.cancel_init_sync()
        mock_processor.request_cancel.assert_awaited_once()
        # 无 task_id, 跳过 cancel_task 调用
        mock_task_manager.cancel_task.assert_not_called()

    def test_recover_stale_state_keeps_running_task(self, bound_vm, mock_task_manager):
        """recover_stale_state 保留 RUNNING 状态的活跃任务,仅清理终态任务（535->533, 544->exit）。"""
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        bound_vm._active_task_ids = {
            "daily_sync": "task_running",  # 仍 RUNNING, 保留
            "ai_concept_sync": "task_done",  # COMPLETED, 清理
        }
        running_task = MagicMock()
        running_task.status = TaskStatus.RUNNING
        done_task = MagicMock()
        done_task.status = TaskStatus.COMPLETED
        mock_task_manager.get_task.side_effect = [running_task, done_task]

        bound_vm.recover_stale_state()

        assert "daily_sync" in bound_vm._active_task_ids
        assert "ai_concept_sync" not in bound_vm._active_task_ids
        # 仍有活跃任务, is_syncing 保持 True, 不调 _set_sync_busy(False)
        assert bound_vm.state.is_syncing is True

    async def test_daily_sync_progress_with_zero_total(self, bound_vm, mock_processor, mock_task_manager):
        """_daily_logic._progress 回调在 t=0 时使用 0 进度,不抛 ZeroDivisionError（278->exit）。"""

        async def _call_progress(*args, progress_callback=None, **kwargs):
            assert progress_callback is not None
            # t=0 触发 `c / t if t else 0` False 分支, 应返回 0 而非抛异常
            progress_callback(1, 0, "zero-total-step")

        mock_processor.run_daily_update = AsyncMock(side_effect=_call_progress)
        # update_progress 返回 True, 不触发 CancelledError 早退
        mock_task_manager.update_progress = MagicMock(return_value=True)

        bound_vm.execute_full_daily_sync()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        # update_progress 被以 progress=0 调用, 未抛 ZeroDivisionError
        mock_task_manager.update_progress.assert_called_once_with("task_123", 0, "zero-total-step")
        progress_arg = mock_task_manager.update_progress.call_args.args[1]
        assert progress_arg == 0

    async def test_init_sync_progress_with_zero_total(self, bound_vm, mock_processor, mock_task_manager):
        """_combined_progress 在 t=0 时使用 0 进度,不抛 ZeroDivisionError（429->exit）。"""
        # update_progress 返回 True, 不触发 CancelledError 早退
        mock_task_manager.update_progress = MagicMock(return_value=True)

        async def _fake_initialize(*args, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                # t=0 触发 `c / t if t > 0 else 0` False 分支
                cb(1, 0, "zero-total-step")
            return {"success": True}

        mock_processor.initialize_system = AsyncMock(side_effect=_fake_initialize)

        bound_vm.execute_init_historical_data()
        factory = _capture_coroutine_factory(mock_task_manager.submit_task)
        await factory(task_id="task_123")

        # 验证 _combined_progress 用 progress=0 调用 update_progress
        first_call_args = mock_task_manager.update_progress.call_args_list[0].args
        assert first_call_args[1] == 0
        # 正常完成, init_sync_final_status=COMPLETED
        assert bound_vm.state.init_sync_final_status == TaskStatus.COMPLETED


class TestDataSourceViewModelCancelActiveTask:
    """P1-5: cancel_active_task — daily_sync / ai_concept_sync 取消按钮入口。

    取消语义:
    - active_key=None: 无活跃任务, 不动作
    - active_key="system_init_sync": 走专门 cancel_init_sync 通道, 此处不动作
    - active_key="daily_sync"/"ai_concept_sync"/"cache_clear" 且 task_id 存在:
      委托 TaskManager.cancel_task(task_id)
    - active_key 有值但 _active_task_ids 无记录: 不动作 (防御)
    """

    def test_cancel_active_task_no_active_key_noop(self, bound_vm, mock_task_manager):
        """active_key=None 时不调 cancel_task。"""
        assert bound_vm.state.active_key is None
        bound_vm.cancel_active_task()
        mock_task_manager.cancel_task.assert_not_called()

    def test_cancel_active_task_system_init_sync_noop(self, bound_vm, mock_task_manager):
        """active_key="system_init_sync" 走 cancel_init_sync 专用通道, 此方法不处理。"""
        bound_vm._set_state(is_syncing=True, active_key="system_init_sync")
        bound_vm._active_task_ids["system_init_sync"] = "task_init_1"
        bound_vm.cancel_active_task()
        mock_task_manager.cancel_task.assert_not_called()

    def test_cancel_active_task_daily_sync_cancels(self, bound_vm, mock_task_manager):
        """active_key="daily_sync" + task_id 存在 → cancel_task("task_123")。"""
        bound_vm.execute_full_daily_sync()
        assert bound_vm.state.active_key == "daily_sync"
        bound_vm.cancel_active_task()
        mock_task_manager.cancel_task.assert_called_once_with("task_123")

    def test_cancel_active_task_ai_concept_sync_cancels(self, bound_vm, mock_task_manager):
        """active_key="ai_concept_sync" + task_id 存在 → cancel_task("task_123")。"""
        bound_vm.execute_ai_concept_rebuild()
        assert bound_vm.state.active_key == "ai_concept_sync"
        bound_vm.cancel_active_task()
        mock_task_manager.cancel_task.assert_called_once_with("task_123")

    def test_cancel_active_task_missing_task_id_noop(self, bound_vm, mock_task_manager):
        """active_key 有值但 _active_task_ids 无记录 (状态不一致防御) → 不动作。"""
        bound_vm._set_state(is_syncing=True, active_key="daily_sync")
        # 手动清空 task_id 记录, 模拟状态不一致
        bound_vm._active_task_ids.clear()
        bound_vm.cancel_active_task()
        mock_task_manager.cancel_task.assert_not_called()


class TestDataSourceViewModelSyncBusyProgressReset:
    """P1-5: _set_sync_busy 启动/结束时重置 progress 与 progress_message。

    避免 init_sync 与 secondary sync (daily/ai_concept/cache_clear) 的
    进度条状态互相污染 (state 字段共用, 启动新任务必须清零旧值)。
    """

    def test_set_sync_busy_true_resets_progress(self, bound_vm):
        """启动任务: progress=0.0, progress_message=None。"""
        # 先模拟残留进度
        bound_vm._set_state(progress=0.7, progress_message=Message("task_progress_checking"))
        bound_vm._set_sync_busy(True, active_key="daily_sync")
        assert bound_vm.state.is_syncing is True
        assert bound_vm.state.active_key == "daily_sync"
        assert bound_vm.state.progress == 0.0
        assert bound_vm.state.progress_message is None

    def test_set_sync_busy_false_resets_progress(self, bound_vm):
        """结束任务: progress=0.0, progress_message=None (下次启动从干净状态开始)。"""
        bound_vm._set_state(
            is_syncing=True,
            active_key="daily_sync",
            progress=0.9,
            progress_message=Message("task_progress_analyzing"),
        )
        bound_vm._set_sync_busy(False)
        assert bound_vm.state.is_syncing is False
        assert bound_vm.state.active_key is None
        assert bound_vm.state.progress == 0.0
        assert bound_vm.state.progress_message is None

    def test_set_sync_busy_true_without_active_key(self, bound_vm):
        """is_busy=True 不传 active_key 时 active_key=None (向后兼容)。"""
        bound_vm._set_sync_busy(True)
        assert bound_vm.state.is_syncing is True
        assert bound_vm.state.active_key is None
        assert bound_vm.state.progress == 0.0
