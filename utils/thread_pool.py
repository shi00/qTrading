import asyncio
import atexit
import concurrent.futures
import functools
import logging
import os
import threading
from enum import Enum, auto
from typing import Optional, Callable, TypeVar

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TaskType(Enum):
    IO = auto()  # For Network, Database, Disk operations (High concurrency allowed)
    CPU = auto()  # For Pandas (releasing GIL), specific Math. PURE PYTHON LOOPS SHOULD USE MULTIPROCESSING!


class ThreadPoolManager:
    """
    Global Thread Pool Manager supporting Split IO/CPU pools.

    Architectural Note:
    - IO Pool: High concurrent thread count for blocking I/O.
    - CPU Pool: Low thread count (cpu cores).
      WARNING: Only effective for CPU tasks that release the GIL (e.g. NumPy, Pandas, C-extensions).
      Pure Python CPU-bound tasks will suffer from GIL contention and should use ProcessPoolExecutor instead.
    """
    _instance: Optional['ThreadPoolManager'] = None
    _lock = threading.Lock()  # Singleton Lock

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ThreadPoolManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        # Double-check locking optimization not needed for init if __new__ handles instance creation safely, 
        # but standard singleton pattern usually locks on creation.

        if self._initialized:
            return

        self._io_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self._cpu_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None

        self._init_pools()
        atexit.register(self.shutdown)
        self._initialized = True

    def _init_pools(self):
        # 1. IO Pool Configuration
        io_workers = ConfigHandler.get_max_io_workers()
        if io_workers is None:
            io_workers = 32
            logger.info(f"ThreadPool: IO Pool using default size: {io_workers}")
        else:
            logger.info(f"ThreadPool: IO Pool using config size: {io_workers}")

        # 2. CPU Pool Configuration
        cpu_workers = ConfigHandler.get_max_cpu_workers()
        if cpu_workers is None:
            cpu_workers = os.cpu_count() or 1
            logger.info(f"ThreadPool: CPU Pool using default size (Core Count): {cpu_workers}")
        else:
            logger.info(f"ThreadPool: CPU Pool using config size: {cpu_workers}")

        self._io_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=io_workers,
            thread_name_prefix="IO_Worker"
        )
        self._cpu_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=cpu_workers,
            thread_name_prefix="CPU_Worker"
        )

    def reload_config(self):
        """Reload pools with new configuration. Not thread-safe if tasks are running, use with caution."""
        logger.info("Reloading Thread Pool Configuration...")
        self.shutdown(wait=True)  # Wait for current tasks to finish
        self._init_pools()
        logger.info("Thread Pools reloaded.")

    @property
    def io_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        # Emergency recovery if pool was accidentally shutdown or None
        if self._io_pool is None:
            self._init_pools()
        return self._io_pool

    @property
    def cpu_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        # Emergency recovery
        if self._cpu_pool is None:
            self._init_pools()
        return self._cpu_pool

    def get_executor(self, task_type: TaskType) -> concurrent.futures.ThreadPoolExecutor:
        if task_type == TaskType.IO:
            return self.io_pool
        elif task_type == TaskType.CPU:
            return self.cpu_pool
        else:
            # Fallback to IO if unsure
            return self.io_pool

    def submit(self, task_type: TaskType, func: Callable[..., T], *args, **kwargs) -> concurrent.futures.Future[T]:
        """Submit a sync task to the specific pool"""
        executor = self.get_executor(task_type)
        return executor.submit(func, *args, **kwargs)

    async def run_async(self, task_type: TaskType, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Run a sync function in the executor and await it (asyncio bridge).
        Supports kwargs by automatically wrapping in functools.partial.

        Usage: await ThreadPoolManager().run_async(TaskType.IO, my_func, arg1, key=val)
        """
        loop = asyncio.get_running_loop()
        executor = self.get_executor(task_type)

        # run_in_executor does not support kwargs, so use lambda or functools.partial if needed by caller
        if kwargs:
            func = functools.partial(func, **kwargs)

        return await loop.run_in_executor(executor, func, *args)

    def shutdown(self, wait=True):
        """
        Gracefully shutdown pools. 
        Note: wait=True can block if tasks are stuck. 
        For GUI apps on exit, we might want to wait briefly then force kill?
        For now, we trust the executor's shutdown mechanism.
        """
        if hasattr(self, '_io_pool') and self._io_pool:
            logger.info("Shutting down IO Pool...")
            self._io_pool.shutdown(wait=wait)
            self._io_pool = None

        if hasattr(self, '_cpu_pool') and self._cpu_pool:
            logger.info("Shutting down CPU Pool...")
            self._cpu_pool.shutdown(wait=wait)
            self._cpu_pool = None
        logger.info("Thread Pools shut down.")


# Global Accessor
_manager: Optional[ThreadPoolManager] = None
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
    return _manager
