import asyncio
import datetime
import logging
import threading
import time as _time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager
from utils.time_utils import CST_TZ, get_now

logger = logging.getLogger(__name__)


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
    _coroutine_gen: Callable = None  # Function that returns a coroutine  # type: ignore
    _asyncio_task: asyncio.Task | None = None
    _cancel_event: asyncio.Event | None = None
    unique_key: str | None = None  # For deduplication


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
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def __init__(self):
        with self._lock:
            if getattr(self, "_initialized", False):
                return

            self._tasks: dict[str, AppTask] = {}
            self._subscribers: list[Callable[[list[AppTask]], None]] = []
            self._background_tasks = set()  # Strong references to prevent GC

            # Semaphore is created lazily inside the event loop to avoid
            # DeprecationWarning on Python 3.10+ when no loop is running.
            self._semaphore_instance: asyncio.Semaphore | None = None

            # Throttle for update_progress notifications (seconds)
            self._last_notify_time: float = 0.0
            self._NOTIFY_THROTTLE_S: float = 0.2  # Max 5 pushes per second

            # History loaded from DB (read-only, separate from active _tasks)
            self._history: list[AppTask] = []
            self._db_ready = False
            self._loop: asyncio.AbstractEventLoop | None = None  # Captured in init_db

            self._initialized = True
            logger.info("[TaskManager] Initialized global task manager.")

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create semaphore bound to the current event loop.
        Concurrency limit follows ThreadPoolManager's CPU pool capacity,
        since most tasks offload heavy work there via run_async."""
        if self._semaphore_instance is None:
            # Priority: explicit config > CPU pool max_workers > fallback 5
            limit = ConfigHandler.get_max_concurrent_tasks()
            if limit <= 0:
                try:
                    limit = ThreadPoolManager().cpu_pool._max_workers
                except Exception as e:
                    logger.debug(f"[TaskManager] Failed to read cpu_pool max_workers, using default: {e}")
                    limit = 5
            self._semaphore_instance = asyncio.Semaphore(limit)
            logger.info(f"[TaskManager] Concurrency semaphore initialized: max={limit}")
        return self._semaphore_instance

    def subscribe(self, callback: Callable[[list[AppTask]], None]):
        """Register a UI callback to be notified when any task updates."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)
            # Instantly push current state
            try:
                callback(self.get_all_tasks())
            except Exception as e:
                logger.error(f"[TaskManager] Error in subscriber initial push: {e}")

    def unsubscribe(self, callback: Callable[[list[AppTask]], None]):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _notify_subscribers(self):
        """Broadcast current tasks snapshot to all listeners. Safe to call from UI tread if using page.run_task."""
        tasks_snapshot = self.get_all_tasks()
        for cb in self._subscribers[:]:  # Iterate copy
            try:
                cb(tasks_snapshot)
            except Exception as e:
                logger.error(f"[TaskManager] Subscriber callback failed: {e}")

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
        unique_key: str = None,  # type: ignore
        **kwargs,
    ) -> str | None:
        """
        Submit a new background task.  Thread-safe: may be called from either
        the event-loop thread or a worker thread (Flet dispatches sync on_click
        handlers to a ThreadPoolExecutor).

        Uses loop.call_soon_threadsafe to guarantee all state mutations
        (dict write, subscriber notification, task launch) happen on the
        event loop thread — no try/except branching needed.
        """
        # Deduplication: reject if a task with same unique_key is already active
        if unique_key:
            for t in self._tasks.values():
                if t.unique_key == unique_key and t.status in (
                    TaskStatus.QUEUED,
                    TaskStatus.RUNNING,
                ):
                    logger.warning(
                        f"[TaskManager] Duplicate task skipped: '{name}' (key={unique_key})",
                    )
                    return None

        task = AppTask(name=name, task_type=task_type, cancellable=cancellable)
        task.unique_key = unique_key
        task._coroutine_gen = lambda t=task: coroutine_factory(task_id=t.id, **kwargs)

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._register_and_run, task)
        else:
            logger.error(
                f"[TaskManager] Cannot submit task '{name}': no event loop captured.",
            )

        return task.id

    def _register_and_run(self, task: AppTask):
        """Register task in dict, persist, notify, and launch runner.
        Always runs on the event loop thread (guaranteed by call_soon_threadsafe)."""
        task._cancel_event = asyncio.Event()
        self._tasks[task.id] = task
        self._persist_task(task)
        self._notify_subscribers()
        logger.info(f"[TaskManager] Queued task: [{task.id}] {task.name}")

        # Keep a strong reference to the task to prevent garbage collection
        coro_task = asyncio.create_task(self._task_runner(task.id))
        self._background_tasks.add(coro_task)
        coro_task.add_done_callback(self._background_tasks.discard)

    def update_progress(self, task_id: str, progress: float, description: str = None):  # type: ignore
        """Allow the executing coroutine to report its progress (0.0 - 1.0).
        Throttled to avoid flooding subscribers with high-frequency updates."""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.RUNNING:
            task.progress = max(0.0, min(1.0, progress))
            if description is not None:
                task.description = description

            # Throttle: only broadcast to subscribers at most every _NOTIFY_THROTTLE_S seconds
            now = _time.monotonic()
            if (now - self._last_notify_time) >= self._NOTIFY_THROTTLE_S or progress >= 1.0:
                self._last_notify_time = now
                self._notify_subscribers()

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
                f"[TaskManager] Attempted to cancel non-cancellable task: {task.id}",
            )
            return

        logger.info(f"[TaskManager] Cancelling task: [{task.id}] {task.name}")
        task.status = TaskStatus.CANCELLED
        task.description = I18n.get("task_cancelled_desc", "用户已中止操作")

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

    async def cancel_all_running_async(self):
        """Async version: cancel all running tasks with guaranteed DB writes.
        Called from main.py cleanup to ensure persistence before loop closes."""
        active_ids = [tid for tid, t in self._tasks.items() if t.status in (TaskStatus.RUNNING, TaskStatus.QUEUED)]
        for tid in active_ids:
            task = self._tasks[tid]
            task.status = TaskStatus.CANCELLED
            task.description = I18n.get("task_cancelled_desc", "用户已中止操作")
            task.completed_at = get_now()
            if task._cancel_event:
                task._cancel_event.set()
            if task._asyncio_task and not task._asyncio_task.done():
                task._asyncio_task.cancel()
            await self._persist_task_async(task)
        if active_ids:
            logger.info(
                f"[TaskManager] Shutdown: cancelled {len(active_ids)} active task(s).",
            )
            self._notify_subscribers()

    _MAX_FINISHED_HISTORY = 200

    def _auto_evict_old(self):
        """Prevent unbounded memory growth by evicting oldest finished tasks when history exceeds limit."""
        finished = [(tid, t) for tid, t in self._tasks.items() if t.status in TERMINAL_STATUSES]
        if len(finished) > self._MAX_FINISHED_HISTORY:
            finished.sort(key=lambda x: x[1].completed_at or datetime.datetime.min)
            to_evict = finished[: len(finished) - self._MAX_FINISHED_HISTORY]
            for tid, _ in to_evict:
                del self._tasks[tid]
            logger.debug(
                f"[TaskManager] Auto-evicted {len(to_evict)} old finished task(s).",
            )

    # --- Internal Runner ---

    async def _task_runner(self, task_id: str):
        """The actual wrapper that executes the user coroutine and handles state transitions."""
        task = self._tasks.get(task_id)
        if not task:
            return

        if task.status == TaskStatus.CANCELLED:
            return

        # Ensure event exists on this loop
        if task._cancel_event is None:
            task._cancel_event = asyncio.Event()

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
                logger.debug(f"[TaskManager] Running: [{task.id}] {task.name}")

                # Execute user logic
                coro = task._coroutine_gen()
                task.result = await coro

                # If we made it here without CancelledError, it's a success
                task.status = TaskStatus.COMPLETED
                task.progress = 1.0
                task.description = str(task.result) if task.result else I18n.get("task_status_completed", "已完成")
                logger.info(f"[TaskManager] Completed: [{task.id}]")

        except asyncio.CancelledError:
            if task.status != TaskStatus.CANCELLED:
                task.status = TaskStatus.CANCELLED
            task.description = I18n.get("task_cancelled_desc", "用户已中止操作")
            logger.info(f"[TaskManager] Cancelled processing for: [{task.id}]")
            raise  # Important to re-raise CancelledError for proper asyncio teardown
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.description = f"Failed: {type(e).__name__}"
            logger.error(
                f"[TaskManager] Task {task.id} Failed: {e}\n{traceback.format_exc()}",
            )
        finally:
            task._asyncio_task = None
            if task.completed_at is None:
                task.completed_at = get_now()
            self._persist_task(task)
            self._notify_subscribers()
            self._auto_evict_old()

    # --- Persistence ---

    @staticmethod
    def _safe_dt(val) -> datetime.datetime | None:
        """
        Safely parse datetime from DB value, handling NaN/None/invalid.

        Returns a timezone-aware datetime in CST (Asia/Shanghai) to ensure
        consistency with get_now() which returns timezone-aware datetimes.
        """
        if val is None:
            return None
        if isinstance(val, float) and pd.isna(val):
            return None
        try:
            dt = datetime.datetime.fromisoformat(str(val))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST_TZ)
            return dt
        except (ValueError, TypeError):
            return None

    async def init_db(self):
        """Initialize persistence layer. Called once from main.py after CacheManager.init_db()."""
        from data.cache.cache_manager import CacheManager

        cache = CacheManager()

        # Capture the running loop so thread-pool callers can schedule back
        self._loop = asyncio.get_running_loop()

        # Wait for database schema to be ready (handled safely by CacheManager)

        # 2. Mark stale RUNNING/QUEUED from last session as INTERRUPTED
        await cache._write_db(
            "UPDATE task_history SET status = $1, description = $2 WHERE status IN ('RUNNING', 'QUEUED')",
            (
                TaskStatus.INTERRUPTED.value,
                I18n.get("task_interrupted_desc", "应用上次异常退出，任务被中断"),
            ),
        )

        # 3. Load recent history (_read_db returns pd.DataFrame)
        df = await cache._read_db(
            "SELECT * FROM task_history ORDER BY created_at DESC LIMIT 200",
        )
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                try:
                    t = AppTask(
                        id=row.get("id", ""),  # type: ignore
                        name=row.get("name", ""),  # type: ignore
                        task_type=row.get("task_type", "System"),  # type: ignore
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
                except Exception as e:
                    logger.warning(f"[TaskManager] Skipping malformed history row: {e}")
            logger.info(
                f"[TaskManager] Loaded {len(self._history)} historical task(s) from DB.",
            )

        # 4. Purge old records (>30 days)
        cutoff_date = (get_now() - datetime.timedelta(days=30)).replace(tzinfo=None)
        await cache._write_db(
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
            str(task.result)[:500] if task.result else None,
            task.created_at.replace(tzinfo=None) if task.created_at else None,
            task.started_at.replace(tzinfo=None) if task.started_at else None,
            task.completed_at.replace(tzinfo=None) if task.completed_at else None,
        )
        self._schedule_coro(self._persist_snapshot(snapshot))

    def _schedule_coro(self, coro):
        """Schedule a coroutine on the main event loop.  Thread-safe."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.create_task, coro)
        else:
            coro.close()
            logger.debug("[TaskManager] No event loop available, coroutine dropped.")

    async def _persist_snapshot(self, params: tuple):
        """Write a pre-captured snapshot tuple to DB."""
        try:
            from data.cache.cache_manager import CacheManager

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
            await CacheManager()._write_db(sql, params)
        except Exception as e:
            logger.debug(f"[TaskManager] Persist failed (non-critical): {e}")

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
            str(task.result)[:500] if task.result else None,
            task.created_at.replace(tzinfo=None) if task.created_at else None,
            task.started_at.replace(tzinfo=None) if task.started_at else None,
            task.completed_at.replace(tzinfo=None) if task.completed_at else None,
        )
        await self._persist_snapshot(params)

    async def _clear_finished_db(self, task_ids: list):
        """Delete specific tasks from DB using SQLAlchemy Core."""
        if not task_ids:
            return
        try:
            from data.cache.cache_manager import CacheManager
            from data.persistence.models import TaskHistory

            cm = CacheManager()
            stmt = TaskHistory.__table__.delete().where(TaskHistory.__table__.c.id.in_(task_ids))
            async with cm.engine.begin() as conn:
                await conn.execute(stmt)
        except Exception as e:
            logger.debug(f"[TaskManager] DB clear failed (non-critical): {e}")
