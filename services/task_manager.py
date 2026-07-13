import asyncio
import contextlib
import datetime
import logging
import threading
import time as _time
import uuid
from collections.abc import Callable
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, cast


from core.i18n import I18n
from utils.async_utils import gather_for_shutdown_cleanup
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.config_handler import ConfigHandler
from utils.loop_local import del_loop_local, get_loop_local
from utils.singleton_registry import register_singleton
from utils.thread_pool import ThreadPoolManager
from utils.time_utils import from_utc_to_cst, get_now, to_utc_for_db

logger = logging.getLogger(__name__)

_NOTIFY_THROTTLE_S = 0.2


class TaskStatus(Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"  # Was running when the app closed unexpectedly


# Terminal statuses — used for clear/evict/UI filtering
TERMINAL_STATUSES = (
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.INTERRUPTED,
)


@dataclass
class AppTask:
    """Represents a long-running asynchronous operation in the application."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = "Unknown Task"
    task_type: str = "System"
    description: str = "Waiting..."
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0  # 0.0 to 1.0
    cancellable: bool = False

    created_at: datetime.datetime = field(default_factory=get_now)
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None

    result: Any = None
    error: str = ""

    # Internal fields for execution
    _coroutine_gen: Callable = None  # Function that returns a coroutine  # type: ignore[assignment]
    _asyncio_task: asyncio.Task | None = None
    _cancel_event: threading.Event | None = None
    unique_key: str | None = None  # For deduplication
    correlation_id: str | None = None  # Inherited from caller context for full-chain tracing


@register_singleton
class TaskManager:
    """
    Singleton service to manage, track, and execute all long-running asynchronous tasks.
    Provides a reactive pub/sub architecture for UI updates.

    Architecture:
        TaskManager is an orchestrator that tracks task lifecycle (state, progress, UI).
        Actual heavy work is executed in ThreadPoolManager's thread pools.
        The concurrency semaphore here is coordinated with ThreadPoolManager's CPU pool
        capacity to avoid over-subscription.
    """

    _instance = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

        del_loop_local("task_manager_semaphore")

    @classmethod
    def _atexit_cleanup(cls):
        """Cancel active tasks on process exit. Called by singleton_registry."""
        inst = cls._instance
        if inst is not None:
            for task in list(inst._tasks.values()):
                if task._asyncio_task and not task._asyncio_task.done():
                    task._asyncio_task.cancel()

    def __init__(self):
        with self._lock:
            if self.__class__._initialized:
                return

            self._tasks: dict[str, AppTask] = {}
            self._active_keys: set[str] = set()  # Thread-safe dedup for unique_key
            self._active_keys_lock = threading.Lock()
            self._finished_order: OrderedDict[str, datetime.datetime] = OrderedDict()
            self._subscribers: list[Callable[[list[AppTask]], None]] = []
            self._subscriber_error_counts: dict[Callable[[list[AppTask]], None], int] = {}
            self._MAX_SUBSCRIBER_ERRORS: int = 3
            self._background_tasks = set()  # Strong references to prevent GC

            # Throttle for update_progress notifications (seconds)
            self._last_notify_time: float = 0.0
            self._NOTIFY_THROTTLE_S: float = _NOTIFY_THROTTLE_S

            # History loaded from DB (read-only, separate from active _tasks)
            self._history: list[AppTask] = []
            self._db_ready = False
            self._loop: asyncio.AbstractEventLoop | None = None  # Captured in init_db
            self._persist_pending_count = 0
            self._persist_counter_lock = threading.Lock()

            self.__class__._initialized = True
            logger.info("[TaskManager] Initialized global task manager.")

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create semaphore bound to the current event loop.
        Concurrency limit follows ThreadPoolManager's CPU pool capacity,
        since most tasks offload heavy work there via run_async."""
        limit = ConfigHandler.get_max_concurrent_tasks()
        if limit <= 0:
            try:
                # ASYNC-010: use the public property instead of the private _max_workers.
                limit = ThreadPoolManager().cpu_pool_max_workers
                if limit <= 0:
                    limit = 5
            except Exception as e:
                logger.debug("[TaskManager] Failed to read cpu_pool max_workers, using default: %s", e, exc_info=True)
                limit = 5

        def _factory():
            sem = asyncio.Semaphore(limit)
            logger.info("[TaskManager] Concurrency semaphore initialized: max=%s", limit)
            return sem

        return get_loop_local("task_manager_semaphore", _factory)

    def subscribe(self, callback: Callable[[list[AppTask]], None]):
        """Register a UI callback to be notified when any task updates."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)
            self._subscriber_error_counts[callback] = 0
            # Instantly push current state
            try:
                callback(self.get_all_tasks())
            except Exception as e:
                logger.error("[TaskManager] Error in subscriber initial push: %s", e, exc_info=True)

    def unsubscribe(self, callback: Callable[[list[AppTask]], None]):
        if callback in self._subscribers:
            self._subscribers.remove(callback)
        self._subscriber_error_counts.pop(callback, None)

    def reload_config(self):
        """S1-1 fix: Reset semaphore so new concurrency limit takes effect on next _get_semaphore call."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: del_loop_local("task_manager_semaphore"))
        else:
            del_loop_local("task_manager_semaphore")
        logger.info("[TaskManager] Semaphore reset, will reinitialize with new config on next task")

    def _notify_subscribers(self):
        """Broadcast current tasks snapshot to all listeners. Safe to call from UI tread if using page.run_task."""
        tasks_snapshot = self.get_all_tasks()
        for cb in self._subscribers[:]:
            try:
                cb(tasks_snapshot)
                if cb in self._subscriber_error_counts:
                    self._subscriber_error_counts[cb] = 0
            except Exception as e:
                consecutive_errors = self._subscriber_error_counts.get(cb, 0) + 1
                self._subscriber_error_counts[cb] = consecutive_errors
                logger.error(
                    "[TaskManager] Subscriber callback failed (consecutive: %s/%s): %s",
                    consecutive_errors,
                    self._MAX_SUBSCRIBER_ERRORS,
                    e,
                    exc_info=True,
                )
                if consecutive_errors >= self._MAX_SUBSCRIBER_ERRORS:
                    with contextlib.suppress(ValueError):
                        self._subscribers.remove(cb)
                    self._subscriber_error_counts.pop(cb, None)
                    logger.warning("[TaskManager] Subscriber disabled after consecutive callback failures")

    def get_all_tasks(self) -> list[AppTask]:
        """Return a snapshot of all tracked tasks + loaded history, ordered by creation."""
        active = list(self._tasks.values())
        active_ids = {t.id for t in active}
        # Append history items not already in active set
        combined = active + [h for h in self._history if h.id not in active_ids]
        return sorted(combined, key=lambda t: t.created_at, reverse=True)

    def get_task(self, task_id: str) -> AppTask | None:
        return self._tasks.get(task_id)

    def submit_task(
        self,
        name: str,
        task_type: str,
        coroutine_factory: Callable,
        cancellable: bool = False,
        unique_key: str = None,  # type: ignore[assignment]
        **kwargs,
    ) -> str | None:
        """
        Submit a new background task.  Thread-safe: may be called from either
        the event-loop thread or a worker thread (Flet dispatches sync on_click
        handlers to a ThreadPoolExecutor).

        Deduplication via unique_key is checked synchronously using _active_keys
        (thread-safe, O(1)), so callers still receive None on duplicate hits.
        All _tasks mutations are deferred to the event loop thread via
        call_soon_threadsafe, eliminating cross-thread dict access.
        """
        # Synchronous dedup via _active_keys (thread-safe, O(1))
        if unique_key:
            with self._active_keys_lock:
                if unique_key in self._active_keys:
                    logger.warning(
                        "[TaskManager] Duplicate task skipped: '%s' (key=%s)",
                        name,
                        unique_key,
                    )
                    return None
                self._active_keys.add(unique_key)

        task = AppTask(name=name, task_type=task_type, cancellable=cancellable)
        task.unique_key = unique_key
        task._coroutine_gen = lambda t=task: coroutine_factory(task_id=t.id, **kwargs)

        from utils.correlation import get_correlation_id as _get_cid

        task.correlation_id = _get_cid()

        if not (self._loop and self._loop.is_running()):
            # Rollback dedup key
            if unique_key:
                with self._active_keys_lock:
                    self._active_keys.discard(unique_key)
            logger.error(
                "[TaskManager] Cannot submit task '%s': no event loop captured.",
                name,
            )
            return None

        # All _tasks mutations (register + launch) on loop thread only
        def _enqueue():
            self._tasks[task.id] = task
            self._register_and_run(task)

        self._loop.call_soon_threadsafe(_enqueue)
        return task.id

    def _register_and_run(self, task: AppTask):
        """Finalize task registration and launch runner.
        Task is already in self._tasks (set by _enqueue on loop thread).
        Always runs on the event loop thread (guaranteed by call_soon_threadsafe)."""
        if task.status == TaskStatus.CANCELLED:
            logger.debug("[TaskManager] Task [%s] already cancelled, skipping launch", task.id)
            # Release dedup key since _task_runner won't run for this task
            if task.unique_key:
                with self._active_keys_lock:
                    self._active_keys.discard(task.unique_key)
            return

        # Note: _cancel_event will be created lazily in _task_runner.
        # Using threading.Event (not asyncio.Event) per project memory hard
        # constraint to avoid loop binding issues (R11).
        self._persist_task(task)
        self._notify_subscribers()
        logger.info("[TaskManager] Queued task: [%s] %s", task.id, task.name)

        # Keep a strong reference to the task to prevent garbage collection
        coro_task = asyncio.create_task(self._task_runner(task.id))
        self._background_tasks.add(coro_task)
        coro_task.add_done_callback(self._background_tasks.discard)

    def update_progress(self, task_id: str, progress: float, description: str = None) -> bool:  # type: ignore[assignment]
        """Allow the executing coroutine to report its progress (0.0 - 1.0).
        Throttled to avoid flooding subscribers with high-frequency updates.

        Returns True if the progress was accepted (task is RUNNING),
        False if the task is not RUNNING (e.g. CANCELLED, COMPLETED)
        or does not exist. Workers should check the return value and
        exit early when False to avoid wasting resources on a cancelled task.
        """
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            if task and task.status == TaskStatus.CANCELLED:
                logger.debug(
                    "[TaskManager] update_progress ignored for cancelled task %s. "
                    "Worker should check is_cancelled() or update_progress return value.",
                    task_id[:8],
                )
            return False
        task.progress = max(0.0, min(1.0, progress))
        if description is not None:
            task.description = description

        now = _time.monotonic()
        if (now - self._last_notify_time) >= self._NOTIFY_THROTTLE_S or progress >= 1.0:
            self._last_notify_time = now
            self._notify_subscribers()
        return True

    def is_cancelled(self, task_id: str) -> bool:
        """B-P1-5: Check if a task has been cancelled. Workers should call this
        periodically to detect cancellation and exit early."""
        task = self._tasks.get(task_id)
        return task is not None and task.status == TaskStatus.CANCELLED

    def get_cancel_event(self, task_id: str) -> threading.Event | None:
        """返回任务的取消信号 threading.Event，task 不存在或未启动时返回 None。

        使用 threading.Event 而非 asyncio.Event 以避免跨循环绑定问题（R11，
        项目内存硬约束）。Event 在 _task_runner 中懒初始化。
        调用方应优先使用此访问器，而非穿透 task._cancel_event 私有字段。
        """
        task = self._tasks.get(task_id)
        return task._cancel_event if task else None

    def cancel_task(self, task_id: str):
        """User requested cancellation.  Thread-safe."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._cancel_task_impl, task_id)

    def _cancel_task_impl(self, task_id: str):
        """Actual cancellation logic. Runs on event loop thread."""
        task = self._tasks.get(task_id)
        if not task:
            return

        if task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
            return  # Already finished

        if not task.cancellable:
            logger.warning(
                "[TaskManager] Attempted to cancel non-cancellable task: %s",
                task.id,
            )
            return

        logger.info("[TaskManager] Cancelling task: [%s] %s", task.id, task.name)
        task.status = TaskStatus.CANCELLED
        task.description = I18n.get("task_cancelled_desc")

        # Release dedup key so same unique_key can be resubmitted
        if task.unique_key:
            with self._active_keys_lock:
                self._active_keys.discard(task.unique_key)

        if task._cancel_event:
            task._cancel_event.set()

        if task._asyncio_task and not task._asyncio_task.done():
            task._asyncio_task.cancel()

        task.completed_at = get_now()
        self._persist_task(task)
        self._notify_subscribers()

    def clear_finished(self):
        """Remove completed, failed, or cancelled tasks from the queue and DB.  Thread-safe."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._clear_finished_impl)

    def _clear_finished_impl(self):
        """Actual clearing logic. Runs on event loop thread."""
        to_delete = [tid for tid, t in self._tasks.items() if t.status in TERMINAL_STATUSES]
        for tid in to_delete:
            task = self._tasks[tid]
            # Safety-net: release dedup key if not already discarded
            if task.unique_key:
                with self._active_keys_lock:
                    self._active_keys.discard(task.unique_key)
            del self._tasks[tid]
        # Also clear matching items from history
        delete_set = set(to_delete)
        history_to_clear = [h.id for h in self._history if h.status in TERMINAL_STATUSES]
        self._history = [h for h in self._history if h.status not in TERMINAL_STATUSES]
        all_clear_ids = list(delete_set | set(history_to_clear))
        # DB cleanup
        if all_clear_ids:
            self._schedule_coro(self._clear_finished_db(all_clear_ids))
        self._notify_subscribers()

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def cancel_all_running_async(self, join_timeout: float = 3.0):
        """Async version: cancel all running tasks with guaranteed DB writes.
        Called from main.py cleanup to ensure persistence before loop closes.

        Args:
            join_timeout: Max seconds to wait for cancelled tasks to finish
                their finally blocks. Prevents Step 3 (DB close) from
                racing against still-running task runners.
        """
        active_ids = [tid for tid, t in self._tasks.items() if t.status in (TaskStatus.RUNNING, TaskStatus.QUEUED)]
        persist_coros = []
        tasks_to_join: list[asyncio.Task] = []
        for tid in active_ids:
            task = self._tasks[tid]
            task.status = TaskStatus.CANCELLED
            task.description = I18n.get("task_cancelled_desc")
            task.completed_at = get_now()
            # Release dedup key so same unique_key can be resubmitted
            if task.unique_key:
                with self._active_keys_lock:
                    self._active_keys.discard(task.unique_key)
            if task._cancel_event:
                task._cancel_event.set()
            if task._asyncio_task and not task._asyncio_task.done():
                task._asyncio_task.cancel()
                tasks_to_join.append(task._asyncio_task)
            persist_coros.append(self._persist_task_async(task))
        if persist_coros:
            await gather_for_shutdown_cleanup(*persist_coros)
        # Wait for cancelled task runners to exit their finally blocks,
        # ensuring they don't access DAOs after DB engine is disposed.
        if tasks_to_join:
            try:
                await asyncio.wait_for(
                    gather_for_shutdown_cleanup(*tasks_to_join),
                    timeout=join_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "[TaskManager] %s task(s) did not exit within %ss after cancellation",
                    len(tasks_to_join),
                    join_timeout,
                )
        if active_ids:
            logger.info(
                "[TaskManager] Shutdown: cancelled %s active task(s).",
                len(active_ids),
            )
            self._notify_subscribers()

        # Clear any keys from tasks submitted via call_soon_threadsafe but not
        # yet enqueued (shutdown race window). Safe since no new submissions
        # can arrive after this point.
        with self._active_keys_lock:
            self._active_keys.clear()

    _MAX_FINISHED_HISTORY = 200

    def _evict_on_complete(self, task_id: str):
        """Evict oldest finished task if history exceeds limit, O(1) amortized.

        Uses _finished_order OrderedDict to track completion order,
        avoiding O(n) scan of all tasks on each completion.
        """
        task = self._tasks.get(task_id)
        if task is None or task.status not in TERMINAL_STATUSES:
            return
        completed_time = task.completed_at or datetime.datetime.min
        self._finished_order[task_id] = completed_time
        self._finished_order.move_to_end(task_id)
        while len(self._finished_order) > self._MAX_FINISHED_HISTORY:
            oldest_tid, _ = self._finished_order.popitem(last=False)
            if oldest_tid in self._tasks:
                del self._tasks[oldest_tid]

    # --- Internal Runner ---

    async def _task_runner(self, task_id: str):
        """The actual wrapper that executes the user coroutine and handles state transitions."""
        task = self._tasks.get(task_id)
        if not task:
            return

        if task.status == TaskStatus.CANCELLED:
            return

        # S5-3 fix: Set correlation_id for cross-module log tracing
        from utils.correlation import set_correlation_id, clear_correlation_id

        cid = task.correlation_id or task.id[:8]
        set_correlation_id(cid)

        # Ensure cancel event exists (threading.Event avoids loop binding, R11)
        if task._cancel_event is None:
            task._cancel_event = threading.Event()

        task.status = TaskStatus.RUNNING
        task.started_at = get_now()
        task.description = "Starting..."
        self._persist_task(task)
        self._notify_subscribers()

        try:
            # Rehydrate the coroutine inside the semaphore
            async with self._get_semaphore():
                # Capture the current asyncio task to allow forceful cancellation
                task._asyncio_task = asyncio.current_task()
                logger.info("[TaskManager] Running: [%s] %s", task.id, task.name)

                # Execute user logic
                coro = task._coroutine_gen()
                task.result = await coro

                # If we made it here without CancelledError, it's a success.
                # T1 fix: 守卫已被 _cancel_task_impl / cancel_all_running_async 设为 CANCELLED 的状态，
                # 避免 coro 返回后被覆盖为 COMPLETED（与下面 CancelledError 分支的守卫对称）。
                if task.status != TaskStatus.CANCELLED:
                    task.status = TaskStatus.COMPLETED
                    task.progress = 1.0
                    task.description = str(task.result) if task.result else I18n.get("task_status_completed")
                    logger.info("[TaskManager] Completed: [%s]", task.id)
                else:
                    logger.info("[TaskManager] Skipping COMPLETED: [%s] already CANCELLED", task.id)

        except asyncio.CancelledError:
            if task.status != TaskStatus.CANCELLED:
                task.status = TaskStatus.CANCELLED
            task.description = I18n.get("task_cancelled_desc")
            logger.info("[TaskManager] Cancelled processing for: [%s]", task.id)
            raise  # Important to re-raise CancelledError for proper asyncio teardown
        except Exception as e:
            # T3 fix: 守卫已被取消的状态，避免用户协程在 except CancelledError 内抛非取消异常时覆盖 CANCELLED。
            if task.status == TaskStatus.CANCELLED:
                # M1 fix: 保留 traceback 便于诊断取消过程中伴随的异常（如 DB 断连）
                # L1 fix: 重置 description 与 CancelledError 分支保持一致
                task.description = I18n.get("task_cancelled_desc")
                logger.info(
                    "[TaskManager] Suppressed FAILED (already CANCELLED): [%s] %s",
                    task.id,
                    e,
                    exc_info=True,
                )
            else:
                task.status = TaskStatus.FAILED
                error_info = classify_error(e, context="general")
                severity = classify_severity(e, context="general")
                task.error = error_info["message_key"]
                task.description = I18n.get("task_failed_desc")
                if severity == "system":
                    logger.critical(
                        "[TaskManager] Task %s SYSTEM-LEVEL failure: %s",
                        task.id,
                        e,
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "[TaskManager] Task %s Failed (%s): %s",
                        task.id,
                        severity,
                        e,
                        exc_info=True,
                    )
        finally:
            # T2 fix: 此处 status 已被 T1/T3 守卫或 CancelledError 分支正确设置为终态，
            # 即使 cancel_all_running_async 已先行写入 CANCELLED 也不会被覆盖。
            task._asyncio_task = None
            if task.completed_at is None:
                task.completed_at = get_now()
            # Release dedup key so same unique_key can be resubmitted
            if task.unique_key:
                with self._active_keys_lock:
                    self._active_keys.discard(task.unique_key)
            self._persist_task(task)
            self._notify_subscribers()
            self._evict_on_complete(task.id)
            clear_correlation_id()

    # --- Persistence ---

    @staticmethod
    def _safe_dt(val) -> datetime.datetime | None:
        """
        Parse a datetime value from DB. S1-6 fix: Assumes UTC storage, converts to CST.
        consistency with get_now() which returns timezone-aware datetimes.
        """
        if val is None:
            return None
        if isinstance(val, (float, Decimal)) and val != val:  # NaN check (faster than pd.isna)
            return None
        try:
            dt = datetime.datetime.fromisoformat(str(val))
            return from_utc_to_cst(dt)
        except (ValueError, TypeError) as e:
            logger.debug("[TaskManager] _safe_dt parse failed for value=%r: %s", val, e, exc_info=True)
            return None

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def init_db(self):
        """Initialize persistence layer. Called once from main.py after CacheManager.init_db()."""
        from data.cache.cache_manager import CacheManager

        cache = CacheManager()

        # Capture the running loop so thread-pool callers can schedule back
        self._loop = asyncio.get_running_loop()

        # Wait for database schema to be ready (handled safely by CacheManager)

        # 2. Mark stale RUNNING/QUEUED from last session as INTERRUPTED
        await cache.write_db(
            "UPDATE task_history SET status = $1, description = $2 WHERE status IN ('RUNNING', 'QUEUED')",
            (
                TaskStatus.INTERRUPTED.value,
                I18n.get("task_interrupted_desc"),
            ),
        )

        # 3. Load recent history (read_db returns pd.DataFrame)
        df = await cache.read_db(
            "SELECT * FROM task_history ORDER BY created_at DESC LIMIT 200",
        )
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                try:
                    t = AppTask(
                        id=row.get("id", ""),  # type: ignore[union-attr]
                        name=row.get("name", ""),  # type: ignore[union-attr]
                        task_type=row.get("task_type", "System"),  # type: ignore[union-attr]
                        status=TaskStatus(row.get("status", "COMPLETED")),
                        progress=float(row.get("progress", 0) or 0),
                        description=str(row.get("description", "") or ""),
                        error=str(row.get("error", "") or ""),
                        result=row.get("result"),
                        created_at=self._safe_dt(row.get("created_at")) or get_now(),
                        started_at=self._safe_dt(row.get("started_at")),
                        completed_at=self._safe_dt(row.get("completed_at")),
                    )
                    self._history.append(t)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("[TaskManager] Skipping malformed history row: %s", str(e), exc_info=True)
            logger.info(
                "[TaskManager] Loaded %s historical task(s) from DB.",
                len(self._history),
            )

        # 4. Purge old records (>30 days)
        # H1 举一反三 fix: 与 completed_at (UTC tz-naive) 时区一致，避免 8 小时偏差
        cutoff_date = cast(datetime.datetime, to_utc_for_db(get_now() - datetime.timedelta(days=30)))
        await cache.write_db(
            "DELETE FROM task_history WHERE completed_at < $1",
            (cutoff_date,),
        )

        self._db_ready = True
        logger.info("[TaskManager] Persistence layer initialized.")

    def _persist_task(self, task: AppTask):
        """Fire-and-forget async write to DB. Snapshots values at call time
        to prevent race conditions with later state mutations."""
        if not self._db_ready:
            return
        snapshot = (
            task.id,
            task.name,
            task.task_type,
            task.status.value,
            task.progress,
            task.description,
            task.error,
            self._truncate_result_for_db(task.result),
            to_utc_for_db(task.created_at),
            to_utc_for_db(task.started_at),
            to_utc_for_db(task.completed_at),
        )
        self._queue_persist_snapshot(snapshot)

    @staticmethod
    def _truncate_result_for_db(result: Any, max_len: int = 500) -> str | None:
        """Truncate task.result safely for DB storage."""
        if result is None:
            return None
        text = str(result)
        if len(text) <= max_len:
            clipped = text
        else:
            clipped = text[:max_len]
        try:
            return clipped.encode("utf-8", "replace").decode("utf-8", "ignore")
        except (UnicodeDecodeError, UnicodeEncodeError) as e:
            logger.debug("[TaskManager] UTF-8 sanitize failed, using raw clipped: %s", e, exc_info=True)
            return clipped

    def _queue_persist_snapshot(self, snapshot: tuple):
        """Schedule a tracked persistence write so shutdown can flush deterministically."""
        with self._persist_counter_lock:
            self._persist_pending_count += 1

        async def _tracked_persist():
            try:
                await self._persist_snapshot(snapshot)
            finally:
                with self._persist_counter_lock:
                    self._persist_pending_count = max(0, self._persist_pending_count - 1)

        scheduled = self._schedule_coro(_tracked_persist())
        if not scheduled:
            with self._persist_counter_lock:
                self._persist_pending_count = max(0, self._persist_pending_count - 1)

    def _schedule_coro(self, coro):
        """Schedule a coroutine on the main event loop.  Thread-safe."""
        if self._loop and self._loop.is_running():

            def _launch():
                try:
                    task = self._loop.create_task(coro)
                except RuntimeError:
                    coro.close()
                    logger.debug("[TaskManager] Loop closed before _launch, coroutine dropped.")
                    return
                # Keep a strong reference to the task to prevent garbage collection
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            try:
                self._loop.call_soon_threadsafe(_launch)
            except RuntimeError:
                # Loop closed between is_running() check and call_soon_threadsafe
                coro.close()
                logger.debug("[TaskManager] Loop closed before call_soon_threadsafe, coroutine dropped.")
                return False
            return True
        coro.close()
        logger.debug("[TaskManager] No event loop available, coroutine dropped.")
        return False

    async def flush_persistence(self, timeout_s: float = 1.5):
        """Wait until all queued persistence writes are completed."""
        if not self._db_ready:
            return

        deadline = _time.monotonic() + timeout_s
        while True:
            with self._persist_counter_lock:
                pending = self._persist_pending_count
            if pending <= 0:
                return
            if _time.monotonic() >= deadline:
                raise TimeoutError(f"Task persistence flush timed out with {pending} pending write(s)")
            await asyncio.sleep(0.02)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _persist_snapshot(self, params: tuple):
        """Write a pre-captured snapshot tuple to DB."""
        try:
            from data.cache.cache_manager import CacheManager

            cache = CacheManager._instance
            if cache is None:
                logger.debug("[TaskManager] Persist skipped: CacheManager not initialized.")
                return
            sql = (
                "INSERT INTO task_history "
                "(id, name, task_type, status, progress, description, error, result, "
                "created_at, started_at, completed_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (id) DO UPDATE SET "
                "name=EXCLUDED.name, task_type=EXCLUDED.task_type, status=EXCLUDED.status, "
                "progress=EXCLUDED.progress, description=EXCLUDED.description, error=EXCLUDED.error, "
                "result=EXCLUDED.result, started_at=EXCLUDED.started_at, completed_at=EXCLUDED.completed_at"
            )
            await cache.write_db(sql, params)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[TaskManager] Persist failed (non-critical): %s", e, exc_info=True)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _persist_task_async(self, task: AppTask):
        """Upsert task record (reads current state — use for await-based callers only)."""
        params = (
            task.id,
            task.name,
            task.task_type,
            task.status.value,
            task.progress,
            task.description,
            task.error,
            self._truncate_result_for_db(task.result),
            to_utc_for_db(task.created_at),
            to_utc_for_db(task.started_at),
            to_utc_for_db(task.completed_at),
        )
        await self._persist_snapshot(params)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _clear_finished_db(self, task_ids: list):
        """Delete specific tasks from DB using SQLAlchemy Core."""
        if not task_ids:
            return
        try:
            from data.cache.cache_manager import CacheManager
            from data.persistence.models import TaskHistory

            cm = CacheManager()
            stmt = TaskHistory.__table__.delete().where(TaskHistory.__table__.c.id.in_(task_ids))
            async with cm.engine.begin() as conn:  # type: ignore[union-attr]
                await conn.execute(stmt)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[TaskManager] DB clear failed (non-critical): %s", e, exc_info=True)
