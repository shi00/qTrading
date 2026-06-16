"""
Tests for TaskManager service.

验证任务管理器的生命周期管理、并发控制和持久化功能。
"""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.task_manager import (
    TERMINAL_STATUSES,
    AppTask,
    TaskManager,
    TaskStatus,
)
from tests.conftest import singleton_state as _singleton_state_ctx


@pytest.fixture(autouse=True)
def _reset_task_manager_singleton():
    with _singleton_state_ctx(TaskManager):
        yield


class TestTaskStatus:
    def test_status_values(self):
        assert TaskStatus.QUEUED.value == "QUEUED"
        assert TaskStatus.RUNNING.value == "RUNNING"
        assert TaskStatus.COMPLETED.value == "COMPLETED"
        assert TaskStatus.FAILED.value == "FAILED"
        assert TaskStatus.CANCELLED.value == "CANCELLED"
        assert TaskStatus.INTERRUPTED.value == "INTERRUPTED"

    def test_terminal_statuses(self):
        assert TaskStatus.COMPLETED in TERMINAL_STATUSES
        assert TaskStatus.FAILED in TERMINAL_STATUSES
        assert TaskStatus.CANCELLED in TERMINAL_STATUSES
        assert TaskStatus.INTERRUPTED in TERMINAL_STATUSES
        assert TaskStatus.QUEUED not in TERMINAL_STATUSES
        assert TaskStatus.RUNNING not in TERMINAL_STATUSES


class TestAppTask:
    def test_task_creation(self):
        task = AppTask(name="Test Task", task_type="Test")
        assert task.name == "Test Task"
        assert task.task_type == "Test"
        assert task.status == TaskStatus.QUEUED
        assert task.progress == 0.0
        assert task.id is not None
        assert task.created_at is not None

    def test_task_default_values(self):
        task = AppTask()
        assert task.name == "Unknown Task"
        assert task.task_type == "System"
        assert task.description == "Waiting..."
        assert task.cancellable is False
        assert task.started_at is None
        assert task.completed_at is None
        assert task.result is None
        assert task.error == ""

    def test_task_id_uniqueness(self):
        task1 = AppTask()
        task2 = AppTask()
        assert task1.id != task2.id

    def test_task_custom_values(self):
        now = datetime.datetime.now()
        task = AppTask(
            id="custom_id",
            name="Custom",
            task_type="CustomType",
            status=TaskStatus.RUNNING,
            progress=0.5,
            description="In progress",
            cancellable=True,
            created_at=now,
            started_at=now,
        )
        assert task.id == "custom_id"
        assert task.status == TaskStatus.RUNNING
        assert task.progress == 0.5
        assert task.cancellable is True


class TestTaskManagerSingleton:
    def test_singleton(self):
        manager1 = TaskManager()
        manager2 = TaskManager()
        assert manager1 is manager2

    def test_singleton_thread_safety(self):
        import threading

        instances = []

        def create_instance():
            instances.append(TaskManager())

        threads = [threading.Thread(target=create_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(inst is instances[0] for inst in instances)


class TestTaskManagerSubscribe:
    def test_subscribe(self):
        manager = TaskManager()
        callback = MagicMock()
        manager.subscribe(callback)
        assert callback in manager._subscribers

    def test_unsubscribe(self):
        manager = TaskManager()
        callback = MagicMock()
        manager.subscribe(callback)
        manager.unsubscribe(callback)
        assert callback not in manager._subscribers

    def test_notify_subscribers(self):
        manager = TaskManager()
        callback = MagicMock()
        manager.subscribe(callback)
        callback.reset_mock()
        manager._notify_subscribers()
        callback.assert_called_once()

    def test_notify_multiple_subscribers(self):
        manager = TaskManager()
        callbacks = [MagicMock() for _ in range(3)]
        for cb in callbacks:
            manager.subscribe(cb)
            cb.reset_mock()
        manager._notify_subscribers()
        for cb in callbacks:
            assert cb.call_count == 1

    def test_subscriber_disabled_after_repeated_callback_failures(self, caplog):
        import logging

        manager = TaskManager()
        state = {"calls": 0}

        def flaky_callback(_tasks):
            state["calls"] += 1
            if state["calls"] > 1:
                raise RuntimeError("boom")

        manager.subscribe(flaky_callback)

        with caplog.at_level(logging.WARNING, logger="services.task_manager"):
            manager._notify_subscribers()
            manager._notify_subscribers()
            manager._notify_subscribers()

        assert flaky_callback not in manager._subscribers
        assert any("Subscriber disabled" in r.message for r in caplog.records)

    def test_subscriber_error_count_resets_after_success(self):
        manager = TaskManager()
        callback = MagicMock(side_effect=[None, RuntimeError("tmp"), None, None])
        manager.subscribe(callback)

        manager._notify_subscribers()
        assert manager._subscriber_error_counts.get(callback) == 1
        manager._notify_subscribers()
        assert manager._subscriber_error_counts.get(callback) == 0
        assert callback in manager._subscribers


class TestTaskManagerGetTasks:
    def test_get_all_tasks_empty(self):
        manager = TaskManager()
        tasks = manager.get_all_tasks()
        assert len(tasks) == 0

    def test_get_task_not_found(self):
        manager = TaskManager()
        task = manager.get_task("nonexistent")
        assert task is None

    def test_get_all_tasks_with_tasks(self):
        manager = TaskManager()
        task1 = AppTask(id="task1", name="Task 1")
        task2 = AppTask(id="task2", name="Task 2")
        manager._tasks = {"task1": task1, "task2": task2}
        tasks = manager.get_all_tasks()
        assert len(tasks) == 2
        task_ids = [t.id for t in tasks]
        assert "task1" in task_ids
        assert "task2" in task_ids


class TestTaskManagerUpdateProgress:
    def test_update_progress_running_task(self):
        manager = TaskManager()
        task = AppTask(id="task1", name="Test", status=TaskStatus.RUNNING)
        manager._tasks = {"task1": task}
        manager.update_progress("task1", 0.5, "Half done")
        assert task.progress == 0.5
        assert task.description == "Half done"

    def test_update_progress_clamp_values(self):
        manager = TaskManager()
        task = AppTask(id="task1", name="Test", status=TaskStatus.RUNNING)
        manager._tasks = {"task1": task}
        manager.update_progress("task1", 1.5)
        assert task.progress == 1.0
        manager.update_progress("task1", -0.5)
        assert task.progress == 0.0

    def test_update_progress_non_running_task(self):
        manager = TaskManager()
        task = AppTask(id="task1", name="Test", status=TaskStatus.QUEUED)
        manager._tasks = {"task1": task}
        manager.update_progress("task1", 0.5)
        assert task.progress == 0.0


class TestTaskManagerCancel:
    def test_cancel_cancellable_task(self):
        manager = TaskManager()
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = True
        task = AppTask(
            id="task1",
            name="Test",
            status=TaskStatus.RUNNING,
            cancellable=True,
        )
        task._cancel_event = asyncio.Event()
        manager._tasks = {"task1": task}
        manager._cancel_task_impl("task1")
        assert task.status == TaskStatus.CANCELLED

    def test_cancel_non_cancellable_task(self):
        manager = TaskManager()
        task = AppTask(
            id="task1",
            name="Test",
            status=TaskStatus.RUNNING,
            cancellable=False,
        )
        manager._tasks = {"task1": task}
        manager._cancel_task_impl("task1")
        assert task.status == TaskStatus.RUNNING

    def test_cancel_nonexistent_task(self):
        manager = TaskManager()
        manager._cancel_task_impl("nonexistent")

    def test_cancel_finished_task(self):
        manager = TaskManager()
        task = AppTask(
            id="task1",
            name="Test",
            status=TaskStatus.COMPLETED,
            cancellable=True,
        )
        manager._tasks = {"task1": task}
        manager._cancel_task_impl("task1")
        assert task.status == TaskStatus.COMPLETED


class TestTaskManagerClearFinished:
    def test_clear_finished_tasks(self):
        manager = TaskManager()
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = True
        task1 = AppTask(id="task1", name="Running", status=TaskStatus.RUNNING)
        task2 = AppTask(id="task2", name="Completed", status=TaskStatus.COMPLETED)
        task3 = AppTask(id="task3", name="Failed", status=TaskStatus.FAILED)
        manager._tasks = {"task1": task1, "task2": task2, "task3": task3}
        manager._clear_finished_impl()
        assert "task1" in manager._tasks
        assert "task2" not in manager._tasks
        assert "task3" not in manager._tasks


class TestTaskManagerAutoEvict:
    def test_auto_evict_old_tasks(self):
        manager = TaskManager()
        for i in range(250):
            task = AppTask(
                id=f"task{i}",
                name=f"Task {i}",
                status=TaskStatus.COMPLETED,
                completed_at=datetime.datetime.now(),
            )
            manager._tasks[f"task{i}"] = task
            manager._evict_on_complete(f"task{i}")
        assert len(manager._tasks) <= 200


class TestTaskManagerSafeDatetime:
    def test_safe_dt_none(self):
        result = TaskManager._safe_dt(None)
        assert result is None

    def test_safe_dt_nan(self):
        import numpy as np

        result = TaskManager._safe_dt(np.nan)
        assert result is None

    def test_safe_dt_valid_string(self):
        result = TaskManager._safe_dt("2024-01-15 10:30:00")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_safe_dt_invalid_string(self):
        result = TaskManager._safe_dt("invalid date")
        assert result is None

    def test_safe_dt_invalid_string_logs_debug(self, caplog):
        import logging

        with caplog.at_level(logging.DEBUG, logger="services.task_manager"):
            result = TaskManager._safe_dt("invalid date")
        assert result is None
        assert any("_safe_dt parse failed" in r.message for r in caplog.records)


class TestTaskManagerSubmitTask:
    def test_submit_task_returns_id(self):
        manager = TaskManager()
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = True

        async def dummy_coro(task_id):
            return "done"

        task_id = manager.submit_task(
            name="Test Task",
            task_type="Test",
            coroutine_factory=dummy_coro,
        )
        assert task_id is not None

    def test_submit_duplicate_task(self):
        manager = TaskManager()
        manager._loop = MagicMock()
        manager._loop.is_running.return_value = True
        task1 = AppTask(
            id="existing_task",
            name="Existing Task",
            status=TaskStatus.RUNNING,
            unique_key="unique_key_1",
        )
        manager._tasks = {"existing_task": task1}
        manager._active_keys.add("unique_key_1")

        async def dummy_coro(task_id):
            return "done"

        task_id2 = manager.submit_task(
            name="Task 2",
            task_type="Test",
            coroutine_factory=dummy_coro,
            unique_key="unique_key_1",
        )
        assert task_id2 is None


class TestTaskManagerPersistenceFlush:
    def test_flush_persistence_waits_until_no_pending(self):
        manager = TaskManager()
        manager._db_ready = True

        async def run():
            with manager._persist_counter_lock:
                manager._persist_pending_count = 1

            async def complete_later():
                await asyncio.sleep(0.02)
                with manager._persist_counter_lock:
                    manager._persist_pending_count = 0

            task = asyncio.create_task(complete_later())
            try:
                await manager.flush_persistence(timeout_s=0.5)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            with manager._persist_counter_lock:
                assert manager._persist_pending_count == 0

        asyncio.run(run())

    def test_flush_persistence_timeout(self):
        manager = TaskManager()
        manager._db_ready = True

        async def run():
            with manager._persist_counter_lock:
                manager._persist_pending_count = 1
            with pytest.raises(TimeoutError):
                await manager.flush_persistence(timeout_s=0.01)

        asyncio.run(run())


class TestPersistTaskAsyncTimezone:
    def test_persist_converts_aware_datetime_to_utc(self):
        import datetime as _dt

        mgr = TaskManager()

        aware_dt = _dt.datetime(2024, 6, 15, 8, 0, 0, tzinfo=_dt.timezone(_dt.timedelta(hours=8)))
        task = AppTask(id="tz_test", name="tz", created_at=aware_dt, started_at=aware_dt)

        with pytest.MonkeyPatch.context() as m:
            m.setattr(mgr, "_persist_snapshot", AsyncMock())
            asyncio.run(mgr._persist_task_async(task))
            params = mgr._persist_snapshot.call_args[0][0]
            stored_created = params[8]
            stored_started = params[9]
            assert stored_created.tzinfo is None
            assert stored_created == _dt.datetime(2024, 6, 15, 0, 0, 0)
            assert stored_started.tzinfo is None

    def test_persist_handles_naive_datetime(self):
        import datetime as _dt

        mgr = TaskManager()

        naive_dt = _dt.datetime(2024, 6, 15, 8, 0, 0)
        task = AppTask(id="naive_test", name="naive", created_at=naive_dt)

        with pytest.MonkeyPatch.context() as m:
            m.setattr(mgr, "_persist_snapshot", AsyncMock())
            asyncio.run(mgr._persist_task_async(task))
            params = mgr._persist_snapshot.call_args[0][0]
            stored = params[8]
            assert stored == _dt.datetime(2024, 6, 15, 0, 0, 0)

    def test_truncate_result_for_db_limits_length(self):
        text = "A" * 800
        out = TaskManager._truncate_result_for_db(text)
        assert out is not None
        assert len(out) == 500

    def test_persist_uses_truncate_helper(self):
        import datetime as _dt

        mgr = TaskManager()
        task = AppTask(id="truncate_test", name="truncate", created_at=_dt.datetime.now(), result="x" * 900)

        with pytest.MonkeyPatch.context() as m:
            m.setattr(mgr, "_persist_snapshot", AsyncMock())
            asyncio.run(mgr._persist_task_async(task))
            params = mgr._persist_snapshot.call_args[0][0]
            assert params[7] is not None
            assert len(params[7]) == 500
