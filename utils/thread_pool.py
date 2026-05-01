import asyncio
import atexit
import concurrent.futures
import functools
import logging
import threading
from collections.abc import Callable
from enum import Enum, auto
from typing import Optional, TypeVar

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TaskType(Enum):
    IO = auto()  # For Network, Database, Disk operations (High concurrency allowed)
    CPU = auto()  # For Pandas (releasing GIL), specific Math. PURE PYTHON LOOPS SHOULD USE MULTIPROCESSING!


from utils.singleton_registry import register_singleton


@register_singleton
class ThreadPoolManager:
    """
    Global Thread Pool Manager supporting Split IO/CPU pools.

    Architectural Note:
    - IO Pool: High concurrent thread count for blocking I/O.
    - CPU Pool: Low thread count (cpu cores).
      WARNING: Only effective for CPU tasks that release the GIL (e.g. NumPy, Pandas, C-extensions).
      Pure Python CPU-bound tasks will suffer from GIL contention and should use ProcessPoolExecutor instead.
    """

    _instance: Optional["ThreadPoolManager"] = None
    _lock = threading.Lock()  # Singleton Lock

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.shutdown(wait=False)
                except Exception:
                    pass
            cls._instance = None
            cls._initialized = False

    def __init__(self):
        if self._initialized:
            return

        self._io_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._cpu_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._shutdown_done = False  # S3-1 fix: Idempotent guard

        self._init_pools()
        atexit.register(self._atexit_shutdown)
        self._initialized = True

    def _atexit_shutdown(self):
        """S3-1 fix: atexit handler with wait=False to avoid blocking on exit."""

        for handler in logger.handlers[:]:
            try:
                handler.flush()
                if hasattr(handler, "stream") and hasattr(handler.stream, "closed") and handler.stream.closed:
                    logger.removeHandler(handler)
            except (ValueError, OSError):
                try:
                    logger.removeHandler(handler)
                except Exception:
                    pass
        try:
            self.shutdown(wait=False)
        except ValueError:
            pass

    def _init_pools(self):
        # 1. IO Pool Configuration
        io_workers = ConfigHandler.get_max_io_workers()
        logger.info(f"ThreadPool: IO Pool size: {io_workers}")

        # 2. CPU Pool Configuration
        cpu_workers = ConfigHandler.get_max_cpu_workers()
        logger.info(f"ThreadPool: CPU Pool size: {cpu_workers}")

        self._io_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=io_workers if io_workers > 0 else None,
            thread_name_prefix="IO_Worker",
        )
        self._cpu_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers if cpu_workers > 0 else None,
            thread_name_prefix="CPU_Worker",
        )

    def reload_config(self):
        """Reload pools with new configuration. Swaps pools first to minimize downtime."""
        logger.info("Reloading Thread Pool Configuration...")

        # 1. Create NEW pools first
        io_workers = ConfigHandler.get_max_io_workers()
        cpu_workers = ConfigHandler.get_max_cpu_workers()

        new_io_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=io_workers,
            thread_name_prefix="IO_Worker",
        )
        new_cpu_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="CPU_Worker",
        )

        # 2. Swap references (Atomic in Python GIL)
        old_io_pool = self._io_pool
        old_cpu_pool = self._cpu_pool

        self._io_pool = new_io_pool
        self._cpu_pool = new_cpu_pool

        logger.info(
            f"Thread Pools swapped. New sizes: IO={io_workers}, CPU={cpu_workers}",
        )

        # 3. Graceful shutdown of old pools in background thread to avoid blocking reload
        def shutdown_old_pools():
            if old_io_pool:
                old_io_pool.shutdown(wait=True)
            if old_cpu_pool:
                old_cpu_pool.shutdown(wait=True)
            logger.info("Old Thread Pools shut down completely.")

        threading.Thread(target=shutdown_old_pools, daemon=True).start()

    @property
    def io_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        # Emergency recovery if pool was accidentally shutdown or None
        if self._io_pool is None:
            self._init_pools()
        assert self._io_pool is not None
        return self._io_pool

    @property
    def cpu_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        # Emergency recovery
        if self._cpu_pool is None:
            self._init_pools()
        assert self._cpu_pool is not None
        return self._cpu_pool

    def get_executor(
        self,
        task_type: TaskType,
    ) -> concurrent.futures.ThreadPoolExecutor:
        if task_type == TaskType.IO:
            return self.io_pool
        if task_type == TaskType.CPU:
            return self.cpu_pool
        # Fallback to IO if unsure
        return self.io_pool

    def submit(
        self,
        task_type: TaskType,
        func: Callable[..., T],
        *args,
        **kwargs,
    ) -> concurrent.futures.Future[T]:
        """Submit a sync task to the specific pool"""
        executor = self.get_executor(task_type)
        return executor.submit(func, *args, **kwargs)

    async def run_async(
        self,
        task_type: TaskType,
        func: Callable[..., T],
        *args,
        **kwargs,
    ) -> T:
        """
        Run a sync function in the executor and await it (asyncio bridge).
        Supports kwargs by automatically wrapping in functools.partial.

        Usage: await ThreadPoolManager().run_async(TaskType.IO, my_func, arg1, key=val)
        """
        loop = asyncio.get_running_loop()
        executor = self.get_executor(task_type)

        # run_in_executor does not support kwargs, so wrap with functools.partial when needed.
        # Positional args (*args) are passed through run_in_executor to the target function.
        if kwargs:
            func = functools.partial(func, **kwargs)

        return await loop.run_in_executor(executor, func, *args)

    def shutdown(self, wait=True):
        """
        Gracefully shutdown pools.
        Note: wait=True can block if tasks are stuck.
        For GUI apps on exit, we might want to wait briefly then force kill?
        For now, we trust the executor's shutdown mechanism.
        S3-1 fix: Added idempotent guard to prevent double shutdown.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True

        shutdown_performed = False
        if hasattr(self, "_io_pool") and self._io_pool:
            try:
                logger.info("Shutting down IO Pool...")
            except (ValueError, OSError):
                pass
            self._io_pool.shutdown(wait=wait, cancel_futures=True)
            self._io_pool = None
            shutdown_performed = True

        if hasattr(self, "_cpu_pool") and self._cpu_pool:
            try:
                logger.info("Shutting down CPU Pool...")
            except (ValueError, OSError):
                pass
            self._cpu_pool.shutdown(wait=False, cancel_futures=True)
            self._cpu_pool = None
            shutdown_performed = True

        if shutdown_performed:
            try:
                logger.info("Thread Pools shut down.")
            except (ValueError, OSError):
                pass


# Global Accessor
_manager: ThreadPoolManager | None = None
_manager_lock = threading.Lock()


def get_thread_pool_manager() -> ThreadPoolManager:
    """
    Thread-safe Lazy initialization accessor.
    """
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:  # Double-check locking
                _manager = ThreadPoolManager()

    assert _manager is not None
    return _manager
