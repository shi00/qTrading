import asyncio
import datetime
import logging
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import pandas as pd

from services.task_manager import TaskManager, AppTask, TaskStatus, TERMINAL_STATUSES
from utils.time_utils import get_now

# P2-5: 文件含真实 asyncio.sleep（含 10s/5s 长睡眠），标注 slow 以便 CI 分轨运行
pytestmark = [pytest.mark.unit, pytest.mark.slow]


@pytest.fixture(autouse=True)
def _cleanup_coroutines():
    yield
    mgr = TaskManager._instance
    if mgr and hasattr(mgr, "_loop") and isinstance(mgr._loop, MagicMock):
        for call in mgr._loop.call_soon_threadsafe.call_args_list:
            if call.args and len(call.args) > 1 and asyncio.iscoroutine(call.args[1]):
                call.args[1].close()


class TestAppTask:
    def test_default_values(self):
        task = AppTask()
        assert task.name == "Unknown Task"
        assert task.task_type == "System"
        assert task.status == TaskStatus.QUEUED
        assert task.progress == 0.0
        assert task.cancellable is False
        assert task.unique_key is None
        assert task.error == ""
        assert task.result is None

    def test_custom_values(self):
        task = AppTask(name="Sync", task_type="Data", cancellable=True, unique_key="sync_1")
        assert task.name == "Sync"
        assert task.task_type == "Data"
        assert task.cancellable is True
        assert task.unique_key == "sync_1"

    def test_has_id(self):
        task = AppTask()
        assert len(task.id) > 0

    def test_unique_ids(self):
        t1 = AppTask()
        t2 = AppTask()
        assert t1.id != t2.id


class TestTaskManagerInit:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_init(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        assert len(mgr._tasks) == 0
        assert len(mgr._subscribers) == 0
        assert mgr._db_ready is False

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_singleton(self, mock_i18n, mock_tp):
        m1 = TaskManager()
        m2 = TaskManager()
        assert m1 is m2


class TestTaskManagerGetSemaphore:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_creates_semaphore(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            sem = mgr._get_semaphore()
            assert sem is not None
            assert sem._value == 3

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_semaphore_cached_per_loop(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            sem1 = mgr._get_semaphore()
            sem2 = mgr._get_semaphore()
            assert sem1 is sem2

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_semaphore_uses_loop_local(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 5
            mgr = TaskManager()
            sem = mgr._get_semaphore()
            from utils.loop_local import get_loop_local

            loop_sem = get_loop_local("task_manager_semaphore", lambda: None)
            assert sem is loop_sem


class TestTaskManagerReloadConfig:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_resets_semaphore(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            sem_before = mgr._get_semaphore()
            mgr.reload_config()
            sem_after = mgr._get_semaphore()
            assert sem_before is not sem_after

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_reload_creates_new_semaphore_with_new_config(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            sem1 = mgr._get_semaphore()
            assert sem1._value == 3
            mgr.reload_config()
            mock_ch.get_max_concurrent_tasks.return_value = 7
            sem2 = mgr._get_semaphore()
            assert sem2._value == 7

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_reload_uses_call_soon_threadsafe_when_loop_running(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            mgr._get_semaphore()
            mgr._loop = MagicMock()
            mgr._loop.is_running.return_value = True
            mgr.reload_config()
            mgr._loop.call_soon_threadsafe.assert_called_once()

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_reload_without_loop_uses_del_loop_local_directly(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            mgr._loop = None
            with patch("services.task_manager.del_loop_local") as mock_del:
                mgr.reload_config()
                mock_del.assert_called_once_with("task_manager_semaphore")


class TestTaskManagerSubscribe:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_subscribe(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        cb = MagicMock()
        mgr.subscribe(cb)
        assert cb in mgr._subscribers

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_subscribe_no_duplicate(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        cb = MagicMock()
        mgr.subscribe(cb)
        mgr.subscribe(cb)
        assert mgr._subscribers.count(cb) == 1

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_unsubscribe(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        cb = MagicMock()
        mgr.subscribe(cb)
        mgr.unsubscribe(cb)
        assert cb not in mgr._subscribers


class TestTaskManagerNotifySubscribers:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_notify_calls_callback(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        cb = MagicMock()
        mgr.subscribe(cb)
        cb.reset_mock()
        mgr._notify_subscribers()
        cb.assert_called_once()

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_notify_removes_failing_subscriber(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        cb = MagicMock(side_effect=Exception("fail"))
        mgr.subscribe(cb)
        for _ in range(3):
            mgr._notify_subscribers()
        assert cb not in mgr._subscribers

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_intermittent_errors_do_not_accumulate(self, mock_i18n, mock_tp):
        """B-P1-4: Intermittent errors should NOT accumulate — success resets consecutive count to 0."""
        mgr = TaskManager()
        call_count = [0]

        def flaky_callback(tasks):
            call_count[0] += 1
            if call_count[0] % 3 != 0:
                raise Exception("intermittent fail")

        cb = MagicMock(side_effect=flaky_callback)
        mgr.subscribe(cb)

        for _ in range(8):
            mgr._notify_subscribers()

        assert cb in mgr._subscribers, (
            "Intermittent-fail subscriber should NOT be removed (success resets consecutive count)"
        )

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_consecutive_failures_remove_subscriber(self, mock_i18n, mock_tp):
        """B-P1-4: Only CONSECUTIVE failures should trigger subscriber removal."""
        mgr = TaskManager()
        call_count = [0]

        def always_failing_after_2(tasks):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise Exception("consecutive fail")

        cb = MagicMock(side_effect=always_failing_after_2)
        mgr.subscribe(cb)

        for _ in range(5):
            mgr._notify_subscribers()

        assert cb not in mgr._subscribers, "Subscriber with consecutive failures should be removed"

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_success_resets_consecutive_error_count_to_zero(self, mock_i18n, mock_tp):
        """B-P1-4: Success should reset consecutive error count to 0 (not decrement by 1)."""
        mgr = TaskManager()
        call_count = [0]

        def alternating_callback(tasks):
            call_count[0] += 1
            if call_count[0] in (3, 5):
                raise Exception("fail")

        cb = MagicMock(side_effect=alternating_callback)
        mgr.subscribe(cb)  # call 1: success (initial push)

        mgr._notify_subscribers()  # call 2: success
        mgr._notify_subscribers()  # call 3: fail → consecutive=1
        mgr._notify_subscribers()  # call 4: success → reset to 0

        error_count = mgr._subscriber_error_counts.get(cb, 0)
        assert error_count == 0, f"Consecutive error count should be 0 after success, got {error_count}"

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_error_count_removed_after_eviction(self, mock_i18n, mock_tp):
        """B-P1-4: Error count entry should be cleaned up when subscriber is evicted."""
        mgr = TaskManager()
        cb = MagicMock(side_effect=Exception("fail"))
        mgr.subscribe(cb)

        for _ in range(3):
            mgr._notify_subscribers()

        assert cb not in mgr._subscribers
        assert cb not in mgr._subscriber_error_counts


class TestTaskManagerGetAllTasks:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_empty(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        assert mgr.get_all_tasks() == []

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_returns_active_tasks(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test")
        mgr._tasks[t.id] = t
        result = mgr.get_all_tasks()
        assert len(result) == 1

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_includes_history(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t1 = AppTask(name="active")
        t2 = AppTask(name="history")
        mgr._tasks[t1.id] = t1
        mgr._history.append(t2)
        result = mgr.get_all_tasks()
        assert len(result) == 2


class TestTaskManagerGetTask:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_found(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test")
        mgr._tasks[t.id] = t
        assert mgr.get_task(t.id) is t

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_not_found(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        assert mgr.get_task("nonexistent") is None


class TestTaskManagerSubmitTask:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_no_event_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = None
        result = mgr.submit_task("test", "System", lambda **kw: None)
        assert result is None

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_duplicate_unique_key(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        existing = AppTask(name="existing", unique_key="sync_1")
        existing.status = TaskStatus.RUNNING
        mgr._tasks[existing.id] = existing
        mgr._active_keys.add("sync_1")
        result = mgr.submit_task("new", "System", lambda **kw: None, unique_key="sync_1")
        assert result is None

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_submit_success(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        result = mgr.submit_task("test", "System", lambda **kw: None)
        assert result is not None


class TestTaskManagerUpdateProgress:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_running_task(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING)
        mgr._tasks[t.id] = t
        mgr._last_notify_time = 0.0
        mgr.update_progress(t.id, 0.5, "Half done")
        assert t.progress == 0.5
        assert t.description == "Half done"

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_clamps_high(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING)
        mgr._tasks[t.id] = t
        mgr._last_notify_time = 0.0
        mgr.update_progress(t.id, 1.5)
        assert t.progress == 1.0

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_clamps_low(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING)
        mgr._tasks[t.id] = t
        mgr.update_progress(t.id, -0.5)
        assert t.progress == 0.0

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_nonexistent_task(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr.update_progress("nonexistent", 0.5)

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_non_running_task_ignored(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.QUEUED)
        mgr._tasks[t.id] = t
        mgr.update_progress(t.id, 0.5)
        assert t.progress == 0.0

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_cancelled_task_returns_false(self, mock_i18n, mock_tp):
        """B-P1-5: update_progress should return False for cancelled tasks."""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.CANCELLED)
        mgr._tasks[t.id] = t
        result = mgr.update_progress(t.id, 0.5)
        assert result is False


class TestTaskManagerIsCancelled:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_cancelled_task(self, mock_i18n, mock_tp):
        """B-P1-5: is_cancelled should return True for cancelled tasks."""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.CANCELLED)
        mgr._tasks[t.id] = t
        assert mgr.is_cancelled(t.id) is True

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_running_task(self, mock_i18n, mock_tp):
        """B-P1-5: is_cancelled should return False for running tasks."""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING)
        mgr._tasks[t.id] = t
        assert mgr.is_cancelled(t.id) is False

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_nonexistent_task(self, mock_i18n, mock_tp):
        """B-P1-5: is_cancelled should return False for nonexistent tasks."""
        mgr = TaskManager()
        assert mgr.is_cancelled("nonexistent") is False


class TestTaskManagerGetCancelEvent:
    """get_cancel_event 访问器测试：task 不存在 / 未启动 / 已启动三态。"""

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_nonexistent_task_returns_none(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        assert mgr.get_cancel_event("nonexistent") is None

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_queued_task_not_started_returns_none(self, mock_i18n, mock_tp):
        """QUEUED 状态的 task _cancel_event 尚未在 _task_runner 中懒初始化，应返回 None。"""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.QUEUED)
        mgr._tasks[t.id] = t
        assert t._cancel_event is None  # 前置条件
        assert mgr.get_cancel_event(t.id) is None

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_running_task_returns_event(self, mock_i18n, mock_tp):
        """RUNNING 状态且 _cancel_event 已设置的 task 应返回该 Event 实例。"""
        mgr = TaskManager()
        evt = asyncio.Event()
        t = AppTask(name="test", status=TaskStatus.RUNNING)
        t._cancel_event = evt
        mgr._tasks[t.id] = t
        assert mgr.get_cancel_event(t.id) is evt

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_completed_task_returns_event_if_set(self, mock_i18n, mock_tp):
        """终态 task 的 _cancel_event 不被清理，仍可返回（供诊断用）。"""
        mgr = TaskManager()
        evt = asyncio.Event()
        t = AppTask(name="test", status=TaskStatus.COMPLETED)
        t._cancel_event = evt
        mgr._tasks[t.id] = t
        assert mgr.get_cancel_event(t.id) is evt


class TestTaskManagerCancelTask:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_cancel_no_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = None
        mgr.cancel_task("nonexistent")

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_cancel_with_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        mgr.cancel_task("some_id")
        mgr._loop.call_soon_threadsafe.assert_called_once()


class TestTaskManagerCancelTaskImpl:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_nonexistent_task(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._cancel_task_impl("nonexistent")

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_already_completed(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.COMPLETED)
        mgr._tasks[t.id] = t
        mgr._cancel_task_impl(t.id)
        assert t.status == TaskStatus.COMPLETED

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_non_cancellable(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING, cancellable=False)
        mgr._tasks[t.id] = t
        mgr._cancel_task_impl(t.id)
        assert t.status == TaskStatus.RUNNING

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_cancellable_task(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING, cancellable=True)
        t._cancel_event = asyncio.Event()
        mgr._tasks[t.id] = t
        mgr._cancel_task_impl(t.id)
        assert t.status == TaskStatus.CANCELLED
        assert t._cancel_event.is_set()


class TestTaskManagerClearFinished:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_no_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = None
        mgr.clear_finished()

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_with_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        mgr.clear_finished()
        mgr._loop.call_soon_threadsafe.assert_called_once()


class TestTaskManagerClearFinishedImpl:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_clears_terminal_tasks(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t1 = AppTask(name="running", status=TaskStatus.RUNNING)
        t2 = AppTask(name="completed", status=TaskStatus.COMPLETED)
        t3 = AppTask(name="failed", status=TaskStatus.FAILED)
        mgr._tasks = {t1.id: t1, t2.id: t2, t3.id: t3}
        mgr._clear_finished_impl()
        assert t1.id in mgr._tasks
        assert t2.id not in mgr._tasks
        assert t3.id not in mgr._tasks

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_clears_history(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        h1 = AppTask(name="hist_completed", status=TaskStatus.COMPLETED)
        h2 = AppTask(name="hist_running", status=TaskStatus.RUNNING)
        mgr._history = [h1, h2]
        mgr._clear_finished_impl()
        assert len(mgr._history) == 1
        assert h2 in mgr._history


class TestTaskManagerAutoEvictOld:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_evicts_when_over_limit(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        for i in range(210):
            t = AppTask(name=f"task_{i}", status=TaskStatus.COMPLETED)
            t.completed_at = datetime.datetime(2024, 1, 1, 0, 0, 0)
            mgr._tasks[t.id] = t
            mgr._finished_order[t.id] = t.completed_at
        last_tid = list(mgr._finished_order.keys())[-1]
        mgr._evict_on_complete(last_tid)
        finished = [t for t in mgr._tasks.values() if t.status in TERMINAL_STATUSES]
        assert len(finished) <= mgr._MAX_FINISHED_HISTORY


class TestTaskManagerSafeDt:
    def test_none(self):
        assert TaskManager._safe_dt(None) is None

    def test_nan_float(self):
        assert TaskManager._safe_dt(float("nan")) is None

    def test_valid_iso_string(self):
        result = TaskManager._safe_dt("2024-06-14T12:00:00")
        assert result is not None

    def test_invalid_string(self):
        result = TaskManager._safe_dt("not_a_date")
        assert result is None


class TestTaskManagerTruncateResult:
    def test_none(self):
        assert TaskManager._truncate_result_for_db(None) is None

    def test_short_string(self):
        assert TaskManager._truncate_result_for_db("short") == "short"

    def test_long_string(self):
        long_str = "a" * 1000
        result = TaskManager._truncate_result_for_db(long_str, max_len=100)
        assert result is not None
        assert len(result) == 100


class TestTaskManagerScheduleCoro:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_no_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = None

        async def dummy():
            pass

        coro = dummy()
        result = mgr._schedule_coro(coro)
        coro.close()
        assert result is False

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_with_running_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True

        async def dummy():
            pass

        coro = dummy()
        result = mgr._schedule_coro(coro)
        coro.close()
        assert result is True


class TestTaskManagerFlushPersistence:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_not_ready(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = False
        await mgr.flush_persistence()

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_no_pending(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._persist_pending_count = 0
        await mgr.flush_persistence(timeout_s=0.5)


class TestTaskManagerCancelAllRunningAsync:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_no_active_tasks(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        await mgr.cancel_all_running_async()

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_cancels_active_tasks(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING, cancellable=True)
        t._cancel_event = asyncio.Event()
        mgr._tasks[t.id] = t
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance.write_db = AsyncMock()
            await mgr.cancel_all_running_async()
            assert t.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_waits_for_active_asyncio_task_to_complete(self, mock_i18n, mock_tp):
        """SHUTDOWN-001: cancel_all_running_async 应等待活跃 asyncio_task 完成后再返回。"""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING, cancellable=True)
        t._cancel_event = asyncio.Event()
        cleanup_done = asyncio.Event()

        async def cooperative_task():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # 注意：不能在 except CancelledError 中 await，因为 _must_cancel 标志
                # 会导致下一个 await 立即再次抛出 CancelledError
                cleanup_done.set()
                raise

        real_task = asyncio.create_task(cooperative_task())
        # 让 task 真正开始运行，进入 await asyncio.sleep(10)
        await asyncio.sleep(0)
        t._asyncio_task = real_task
        mgr._tasks[t.id] = t

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance.write_db = AsyncMock()
            await mgr.cancel_all_running_async(join_timeout=2.0)

        assert cleanup_done.is_set()
        assert real_task.done()
        assert t.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_join_timeout_does_not_raise_only_warns(self, mock_i18n, mock_tp, caplog):
        """SHUTDOWN-001: join_timeout 超时后不抛异常，只记录 warning 日志。"""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING, cancellable=True)
        t._cancel_event = asyncio.Event()

        async def stubborn_task():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # Python 3.9+ _must_cancel 标志会使下一个 await 再次抛出 CancelledError，
                # 需要 uncancel() 才能在 handler 中执行耗时 await（模拟慢清理）
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
                await asyncio.sleep(5)
                raise

        real_task = asyncio.create_task(stubborn_task())
        # 让 task 真正开始运行，进入 await asyncio.sleep(10)
        await asyncio.sleep(0)
        t._asyncio_task = real_task
        mgr._tasks[t.id] = t

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            caplog.at_level(logging.WARNING, logger="services.task_manager"),
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance.write_db = AsyncMock()
            # 不应抛异常，即使 timeout 极短
            await mgr.cancel_all_running_async(join_timeout=0.01)

        # 验证 warning 日志包含超时信息
        assert any("did not exit" in r.message for r in caplog.records)

        # 清理仍在运行的 task
        real_task.cancel()
        try:
            await real_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_default_join_timeout_is_3(self, mock_i18n, mock_tp):
        """SHUTDOWN-001: cancel_all_running_async 默认 join_timeout=3.0。"""
        import inspect

        sig = inspect.signature(TaskManager.cancel_all_running_async)
        assert sig.parameters["join_timeout"].default == 3.0

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_completed_asyncio_task_not_joined(self, mock_i18n, mock_tp):
        """SHUTDOWN-001: 已完成的 _asyncio_task 不应被 cancel 或加入 tasks_to_join。"""
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.RUNNING, cancellable=True)
        t._cancel_event = asyncio.Event()

        mock_asyncio_task = MagicMock()
        mock_asyncio_task.done.return_value = True
        t._asyncio_task = mock_asyncio_task
        mgr._tasks[t.id] = t

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance.write_db = AsyncMock()
            await mgr.cancel_all_running_async()

        assert t.status == TaskStatus.CANCELLED
        # 已完成的 task 不应被 cancel
        mock_asyncio_task.cancel.assert_not_called()


class TestTaskManagerPersistTask:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_not_ready(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = False
        t = AppTask(name="test")
        mgr._persist_task(t)

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_queues_snapshot(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        t = AppTask(name="test")
        mgr._persist_task(t)
        assert mgr._persist_pending_count >= 0


class TestTaskManagerInitDb:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_init_db(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mock_cache = MagicMock()
        mock_cache.write_db = AsyncMock()
        mock_cache.read_db = AsyncMock(return_value=pd.DataFrame())
        with (
            patch("services.task_manager.CacheManager", create=True) as mock_cm_cls,
            patch.dict(
                "sys.modules",
                {"data.cache.cache_manager": MagicMock(CacheManager=mock_cm_cls)},
            ),
        ):
            mock_cm_cls.return_value = mock_cache
            mock_cm_cls._instance = mock_cache
            await mgr.init_db()
            assert mgr._db_ready is True


class TestAppTaskInit:
    def test_default_status(self):
        task = AppTask(name="test")
        assert task.status == TaskStatus.QUEUED
        assert task.name == "test"
        assert task.progress == 0.0
        assert task.cancellable is False


class TestTaskManagerAutoEvict:
    @patch("services.task_manager.ThreadPoolManager")
    def test_evict_old_finished(self, mock_tp):
        mgr = TaskManager()
        for i in range(205):
            t = AppTask(name=f"task_{i}", status=TaskStatus.COMPLETED)
            t.completed_at = get_now()
            mgr._tasks[t.id] = t
            mgr._finished_order[t.id] = t.completed_at
        last_tid = list(mgr._finished_order.keys())[-1]
        mgr._evict_on_complete(last_tid)
        finished = [t for t in mgr._tasks.values() if t.status in TERMINAL_STATUSES]
        assert len(finished) <= mgr._MAX_FINISHED_HISTORY


class TestTaskManagerEvictOrderedDict:
    """C-P1-4: Verify _evict_on_complete uses OrderedDict for O(1) eviction."""

    @patch("services.task_manager.ThreadPoolManager")
    def test_finished_order_tracks_completed_tasks(self, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test_task", status=TaskStatus.COMPLETED)
        t.completed_at = get_now()
        mgr._tasks[t.id] = t
        mgr._evict_on_complete(t.id)
        assert t.id in mgr._finished_order

    @patch("services.task_manager.ThreadPoolManager")
    def test_non_terminal_task_not_tracked(self, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="running_task", status=TaskStatus.RUNNING)
        mgr._tasks[t.id] = t
        mgr._evict_on_complete(t.id)
        assert t.id not in mgr._finished_order

    @patch("services.task_manager.ThreadPoolManager")
    def test_eviction_removes_oldest_first(self, mock_tp):
        mgr = TaskManager()
        old_time = datetime.datetime(2024, 1, 1, 0, 0, 0)
        new_time = datetime.datetime(2024, 6, 1, 0, 0, 0)
        t_old = AppTask(name="old_task", status=TaskStatus.COMPLETED)
        t_old.completed_at = old_time
        t_new = AppTask(name="new_task", status=TaskStatus.COMPLETED)
        t_new.completed_at = new_time
        mgr._tasks[t_old.id] = t_old
        mgr._tasks[t_new.id] = t_new
        mgr._finished_order[t_old.id] = old_time
        mgr._finished_order[t_new.id] = new_time
        for i in range(mgr._MAX_FINISHED_HISTORY - 1):
            t = AppTask(name=f"fill_{i}", status=TaskStatus.COMPLETED)
            t.completed_at = new_time
            mgr._tasks[t.id] = t
            mgr._finished_order[t.id] = new_time
        assert len(mgr._finished_order) == mgr._MAX_FINISHED_HISTORY + 1
        last_tid = list(mgr._finished_order.keys())[-1]
        mgr._evict_on_complete(last_tid)
        assert t_old.id not in mgr._tasks, "Oldest inserted task should be evicted first"
        assert len(mgr._finished_order) <= mgr._MAX_FINISHED_HISTORY


class TestTaskManagerGetTasks:
    @patch("services.task_manager.ThreadPoolManager")
    def test_get_all_tasks_empty(self, mock_tp):
        mgr = TaskManager()
        tasks = mgr.get_all_tasks()
        assert isinstance(tasks, list)

    @patch("services.task_manager.ThreadPoolManager")
    def test_get_task_not_found(self, mock_tp):
        mgr = TaskManager()
        assert mgr.get_task("nonexistent") is None

    @patch("services.task_manager.ThreadPoolManager")
    def test_get_task_found(self, mock_tp):
        mgr = TaskManager()
        task = AppTask(name="Test")
        mgr._tasks[task.id] = task
        assert mgr.get_task(task.id) is task


class TestTaskStatus:
    def test_queued(self):
        assert TaskStatus.QUEUED.value == "QUEUED"

    def test_running(self):
        assert TaskStatus.RUNNING.value == "RUNNING"

    def test_completed(self):
        assert TaskStatus.COMPLETED.value == "COMPLETED"

    def test_failed(self):
        assert TaskStatus.FAILED.value == "FAILED"

    def test_cancelled(self):
        assert TaskStatus.CANCELLED.value == "CANCELLED"

    def test_interrupted(self):
        assert TaskStatus.INTERRUPTED.value == "INTERRUPTED"


class TestTaskStatusEnum:
    def test_all_statuses(self):
        assert TaskStatus.QUEUED.value == "QUEUED"
        assert TaskStatus.RUNNING.value == "RUNNING"
        assert TaskStatus.COMPLETED.value == "COMPLETED"
        assert TaskStatus.FAILED.value == "FAILED"
        assert TaskStatus.CANCELLED.value == "CANCELLED"
        assert TaskStatus.INTERRUPTED.value == "INTERRUPTED"


class TestTerminalStatuses:
    def test_terminal_statuses(self):
        assert TaskStatus.COMPLETED in TERMINAL_STATUSES
        assert TaskStatus.FAILED in TERMINAL_STATUSES
        assert TaskStatus.CANCELLED in TERMINAL_STATUSES
        assert TaskStatus.INTERRUPTED in TERMINAL_STATUSES
        assert TaskStatus.QUEUED not in TERMINAL_STATUSES
        assert TaskStatus.RUNNING not in TERMINAL_STATUSES


class TestTaskManagerRegisterAndRun:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_cancelled_task_skipped(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.CANCELLED)
        mgr._tasks[t.id] = t
        mgr._register_and_run(t)
        assert t._cancel_event is None

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_registers_and_launches(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = False
        t = AppTask(name="test", cancellable=True)
        t._coroutine_gen = lambda t=t: asyncio.sleep(0)
        mgr._tasks[t.id] = t
        with (
            patch.object(mgr, "_notify_subscribers"),
            patch(
                "asyncio.create_task",
                side_effect=lambda c, *args, **kwargs: [c.close(), MagicMock()][1],
            ),
        ):
            mgr._register_and_run(t)
        # _cancel_event is now created lazily in _task_runner, not in _register_and_run
        assert t._cancel_event is None


class TestTaskManagerTaskRunner:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_successful_execution(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        with patch.object(mgr, "_get_semaphore", return_value=asyncio.Semaphore(1)):
            t = AppTask(name="test", cancellable=True)
            t._cancel_event = asyncio.Event()
            t._coroutine_gen = lambda: asyncio.sleep(0)
            mgr._tasks[t.id] = t
            with (
                patch.object(mgr, "_persist_task"),
                patch.object(mgr, "_notify_subscribers"),
                patch.object(mgr, "_evict_on_complete"),
            ):
                await mgr._task_runner(t.id)
            assert t.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_failed_execution(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        with patch.object(mgr, "_get_semaphore", return_value=asyncio.Semaphore(1)):
            t = AppTask(name="test", cancellable=True)
            t._cancel_event = asyncio.Event()
            t._coroutine_gen = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            mgr._tasks[t.id] = t
            with (
                patch.object(mgr, "_persist_task"),
                patch.object(mgr, "_notify_subscribers"),
                patch.object(mgr, "_evict_on_complete"),
            ):
                await mgr._task_runner(t.id)
            assert t.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_cancelled_task_skipped(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", status=TaskStatus.CANCELLED)
        mgr._tasks[t.id] = t
        await mgr._task_runner(t.id)
        assert t.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_nonexistent_task(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        await mgr._task_runner("nonexistent")


class TestTaskManagerPersistSnapshot:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_cache_not_initialized(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            await mgr._persist_snapshot(("id", "name", "type", "QUEUED", 0.0, "", "", None, None, None, None))

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_write_succeeds(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mock_cache = MagicMock()
        mock_cache.write_db = AsyncMock()
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = mock_cache
            await mgr._persist_snapshot(("id", "name", "type", "QUEUED", 0.0, "", "", None, None, None, None))
            mock_cache.write_db.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_write_fails_gracefully(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mock_cache = MagicMock()
        mock_cache.write_db = AsyncMock(side_effect=Exception("DB error"))
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = mock_cache
            await mgr._persist_snapshot(("id", "name", "type", "QUEUED", 0.0, "", "", None, None, None, None))


class TestTaskManagerClearFinishedDb:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_empty_ids(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        await mgr._clear_finished_db([])

    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_clear_succeeds(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_context.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_context)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.TaskHistory") as mock_th,
        ):
            mock_cm.return_value.engine = mock_engine
            mock_th.__table__ = MagicMock()
            mock_th.__table__.delete.return_value.where.return_value = MagicMock()
            await mgr._clear_finished_db(["id1", "id2"])
            mock_conn.execute.assert_awaited_once()


class TestTaskManagerQueuePersistSnapshot:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_no_loop_drops(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = None
        mgr._queue_persist_snapshot(("id", "name", "type", "QUEUED", 0.0, "", "", None, None, None, None))
        assert mgr._persist_pending_count == 0

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_with_loop_schedules(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        mgr._queue_persist_snapshot(("id", "name", "type", "QUEUED", 0.0, "", "", None, None, None, None))
        assert mgr._persist_pending_count == 1


class TestTaskManagerFlushPersistenceTimeout:
    @pytest.mark.asyncio
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    async def test_timeout_raises(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._persist_pending_count = 5
        with pytest.raises(TimeoutError, match="timed out"):
            await mgr.flush_persistence(timeout_s=0.01)


class TestUpdateProgressReturnStatus:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_returns_true_when_running(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", cancellable=True)
        t.status = TaskStatus.RUNNING
        mgr._tasks[t.id] = t
        result = mgr.update_progress(t.id, 0.5)
        assert result is True

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_returns_false_when_cancelled(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", cancellable=True)
        t.status = TaskStatus.CANCELLED
        mgr._tasks[t.id] = t
        result = mgr.update_progress(t.id, 0.5)
        assert result is False

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_returns_false_when_completed(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test")
        t.status = TaskStatus.COMPLETED
        mgr._tasks[t.id] = t
        result = mgr.update_progress(t.id, 0.5)
        assert result is False


class TestNotifyThrottleConstant:
    """Q-P2-7: _NOTIFY_THROTTLE_S should be a module-level constant,
    not a magic number inside the method body."""

    def test_notify_throttle_constant_exists(self):
        import services.task_manager as tm_mod

        assert hasattr(tm_mod, "_NOTIFY_THROTTLE_S")
        assert tm_mod._NOTIFY_THROTTLE_S == 0.2

    def test_notify_uses_constant_not_magic_number(self):
        import services.task_manager as tm_mod
        import inspect

        source = inspect.getsource(tm_mod.TaskManager.update_progress)
        assert "_NOTIFY_THROTTLE_S" in source

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_returns_false_when_task_not_found(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        result = mgr.update_progress("nonexistent", 0.5)
        assert result is False

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_progress_not_updated_when_cancelled(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        t = AppTask(name="test", cancellable=True)
        t.status = TaskStatus.CANCELLED
        t.progress = 0.3
        mgr._tasks[t.id] = t
        mgr.update_progress(t.id, 0.8)
        assert t.progress == 0.3
