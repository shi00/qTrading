import asyncio
import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.task_manager import TaskManager, AppTask, TaskStatus, TERMINAL_STATUSES
from utils.time_utils import get_now


@pytest.fixture(autouse=True)
def reset_singleton():
    TaskManager._instance = None
    TaskManager._initialized = False
    yield
    TaskManager._instance = None
    TaskManager._initialized = False


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
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_creates_semaphore(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            sem = mgr._get_semaphore()
            assert sem is not None
            assert sem._value == 3

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_semaphore_cached(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            sem1 = mgr._get_semaphore()
            sem2 = mgr._get_semaphore()
            assert sem1 is sem2


class TestTaskManagerReloadConfig:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_resets_semaphore(self, mock_i18n, mock_tp):
        with patch("services.task_manager.ConfigHandler") as mock_ch:
            mock_ch.get_max_concurrent_tasks.return_value = 3
            mgr = TaskManager()
            mgr._get_semaphore()
            mgr.reload_config()
            assert mgr._semaphore_instance is None


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
        mgr._auto_evict_old()
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
        assert len(result) == 100


class TestTaskManagerScheduleCoro:
    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_no_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = None
        result = mgr._schedule_coro(AsyncMock())
        assert result is False

    @patch("services.task_manager.ThreadPoolManager")
    @patch("services.task_manager.I18n")
    def test_with_running_loop(self, mock_i18n, mock_tp):
        mgr = TaskManager()
        mgr._loop = MagicMock()
        mgr._loop.is_running.return_value = True
        result = mgr._schedule_coro(AsyncMock())
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
            mock_cm._instance._write_db = AsyncMock()
            await mgr.cancel_all_running_async()
            assert t.status == TaskStatus.CANCELLED


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
        mock_cache._write_db = AsyncMock()
        mock_cache._read_db = AsyncMock(return_value=pd.DataFrame())
        with (
            patch("services.task_manager.CacheManager", create=True) as mock_cm_cls,
            patch.dict("sys.modules", {"data.cache.cache_manager": MagicMock(CacheManager=mock_cm_cls)}),
        ):
            mock_cm_cls.return_value = mock_cache
            mock_cm_cls._instance = mock_cache
            await mgr.init_db()
            assert mgr._db_ready is True


import pandas as pd


class TestAppTaskInit:
    def test_default_status(self):
        task = AppTask(name="test")
        assert task.status == TaskStatus.QUEUED
        assert task.name == "test"
        assert task.progress == 0.0
        assert task.cancellable is False


class TestTaskManagerAutoEvict:
    def setup_method(self):
        TaskManager._reset_singleton()

    def teardown_method(self):
        TaskManager._reset_singleton()

    @patch("services.task_manager.ThreadPoolManager")
    def test_evict_old_finished(self, mock_tp):
        mgr = TaskManager()
        for i in range(205):
            t = AppTask(name=f"task_{i}", status=TaskStatus.COMPLETED)
            t.completed_at = get_now()
            mgr._tasks[t.id] = t
        mgr._auto_evict_old()
        finished = [t for t in mgr._tasks.values() if t.status in TERMINAL_STATUSES]
        assert len(finished) <= mgr._MAX_FINISHED_HISTORY


class TestTaskManagerGetTasks:
    def setup_method(self):
        TaskManager._reset_singleton()

    def teardown_method(self):
        TaskManager._reset_singleton()

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
