import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from unittest.mock import patch, MagicMock

from utils.thread_pool import ThreadPoolManager, TaskType, get_thread_pool_manager

pytestmark = pytest.mark.unit


class TestThreadPoolManagerInit:
    @patch("utils.thread_pool.ConfigHandler")
    def test_init(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        assert tpm._initialized is True


class TestThreadPoolManagerRunAsync:
    @pytest.mark.asyncio
    @patch("utils.thread_pool.ConfigHandler")
    async def test_run_async_io(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        result = await tpm.run_async(TaskType.IO, lambda: 42)
        assert result == 42

    @pytest.mark.asyncio
    @patch("utils.thread_pool.ConfigHandler")
    async def test_run_async_cpu(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        result = await tpm.run_async(TaskType.CPU, lambda: 99)
        assert result == 99


class TestThreadPoolManagerReloadConfig:
    @patch("utils.thread_pool.ConfigHandler")
    def test_reload_config(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        mock_ch.get_max_io_workers.return_value = 8
        mock_ch.get_max_cpu_workers.return_value = 4
        tpm.reload_config()
        assert isinstance(tpm._io_pool, ThreadPoolExecutor)
        assert isinstance(tpm._cpu_pool, ThreadPoolExecutor)

    @patch("utils.thread_pool.ConfigHandler")
    def test_reload_config_after_shutdown_noop(self, mock_ch):
        """CON-05: 停机后 reload_config 幂等，不重建线程池（防泄漏）。

        旧代码无 _shutdown_event 检查：shutdown() 后 reload_config() 会重建两个
        ThreadPoolExecutor，但 io_pool/cpu_pool property 因 _shutdown_event.is_set()
        抛 RuntimeError → 新池永远拿不到、永远不被 shutdown() → 纯泄漏。
        """
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        tpm.reload_config()
        assert tpm._shutdown_event.is_set()
        assert tpm._io_pool is None
        assert tpm._cpu_pool is None

    @patch("utils.thread_pool.ConfigHandler")
    def test_reload_config_pools_accessible(self, mock_ch):
        """CON-05: 热重载后 io_pool/cpu_pool property 可访问，且反映新配置。"""
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        mock_ch.get_max_io_workers.return_value = 8
        mock_ch.get_max_cpu_workers.return_value = 4
        tpm.reload_config()
        assert isinstance(tpm.io_pool, ThreadPoolExecutor)
        assert isinstance(tpm.cpu_pool, ThreadPoolExecutor)
        assert tpm.io_pool_max_workers == 8
        assert tpm.cpu_pool_max_workers == 4

    @patch("utils.thread_pool.ConfigHandler")
    def test_reload_config_inflight_task_completes(self, mock_ch):
        """CON-05 行为守护：reload_config 不中断/孤立运行中任务。

        旧代码（wait=False）下运行中任务同样自然完成——本测试守护的是
        「reload 不孤立在途任务」不变量，防止未来把 cancel_futures 语义扩展到
        运行中任务、或把旧池 shutdown 内联同步执行（join 自身 worker 死锁）。

        确定性同步：任务体内先 started.set() 再 release.wait()，主线程
        started.wait() 后才 reload，保证任务已进入运行态而非仍在排队
        （排队任务会被 cancel_futures=True 取消，会误触发本测试）。
        """
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()

        started = threading.Event()
        release = threading.Event()

        def blocking_task():
            started.set()
            release.wait(timeout=10)
            return "inflight-done"

        future = tpm.submit(TaskType.IO, blocking_task)
        assert started.wait(timeout=10), "任务未在旧池启动"
        try:
            mock_ch.get_max_io_workers.return_value = 8
            mock_ch.get_max_cpu_workers.return_value = 4
            tpm.reload_config()  # 热重载不应中断在途任务
            release.set()
            assert future.result(timeout=10) == "inflight-done"
        finally:
            release.set()  # 防异常路径任务挂起

    @patch("utils.thread_pool.ConfigHandler")
    def test_reload_config_shutdown_during_creation_noop(self, mock_ch):
        """CON-05: 创建新池过程中并发停机（TOCTOU 防御分支）——释放新池并早退。

        编排：仅对 reload 创建的新池生效——第二次构造（CPU 池）完成后置位
        _shutdown_event，命中 reload_config 内「swap 前二次检查」分支，
        断言新建 io/cpu 池被释放（_shutdown=True）、旧池未被 swap。
        """
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()

        original_init = ThreadPoolExecutor.__init__
        created_pools: list[ThreadPoolExecutor] = []
        counter = {"n": 0}

        def patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            created_pools.append(self)
            counter["n"] += 1
            if counter["n"] == 2:
                tpm._shutdown_event.set()

        with patch.object(ThreadPoolExecutor, "__init__", patched_init):
            tpm.reload_config()

        assert tpm._shutdown_event.is_set()
        assert len(created_pools) == 2
        assert all(pool._shutdown for pool in created_pools), "新建池应被释放，避免泄漏"
        # 未执行 swap：旧池保留原引用
        assert tpm._io_pool is not None
        assert tpm._cpu_pool is not None


class TestThreadPoolManagerShutdown:
    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        assert tpm._io_pool is None or tpm._io_pool._shutdown


class TestGetThreadPoolManager:
    def test_returns_instance(self):
        mgr = get_thread_pool_manager()
        assert isinstance(mgr, ThreadPoolManager)

    def test_returns_same_instance(self):
        mgr1 = get_thread_pool_manager()
        mgr2 = get_thread_pool_manager()
        assert mgr1 is mgr2


class TestThreadPoolManagerPools:
    def test_io_pool_exists(self):
        mgr = ThreadPoolManager()
        assert isinstance(mgr._io_pool, ThreadPoolExecutor)

    def test_cpu_pool_exists(self):
        mgr = ThreadPoolManager()
        assert isinstance(mgr._cpu_pool, ThreadPoolExecutor)

    def test_get_executor_io(self):
        mgr = ThreadPoolManager()
        executor = mgr.get_executor(TaskType.IO)
        assert executor is mgr._io_pool

    def test_get_executor_cpu(self):
        mgr = ThreadPoolManager()
        executor = mgr.get_executor(TaskType.CPU)
        assert executor is mgr._cpu_pool

    def test_io_pool_property_recovery(self):
        mgr = ThreadPoolManager()
        mgr._io_pool = None
        pool = mgr.io_pool
        assert isinstance(pool, ThreadPoolExecutor)

    def test_cpu_pool_property_recovery(self):
        mgr = ThreadPoolManager()
        mgr._cpu_pool = None
        pool = mgr.cpu_pool
        assert isinstance(pool, ThreadPoolExecutor)


class TestThreadPoolManagerSingleton:
    def test_singleton_creation(self):
        mgr1 = ThreadPoolManager()
        mgr2 = ThreadPoolManager()
        assert mgr1 is mgr2

    def test_reset_singleton(self):
        mgr1 = ThreadPoolManager()
        ThreadPoolManager._reset_singleton()
        mgr2 = ThreadPoolManager()
        assert mgr1 is not mgr2


class TestThreadPoolManagerConcurrentInit:
    """CON-01: 并发首次访问时 _init_pools 只执行一次（double-checked locking）。

    旧模式"锁在 __new__、初始化在 __init__ 锁外"存在竞态：两线程同时首次访问时，
    后到线程在首个线程完成初始化前也看到 _initialized=False，重复创建线程池（前一个泄漏）。
    本测试用事件闩锁强制两线程交错进入初始化段，断言 _init_pools 恰好执行一次。
    """

    def test_concurrent_init_inits_pools_once(self):
        ThreadPoolManager._reset_singleton()

        start = threading.Barrier(3)  # 2 workers + main，保证两线程同时开始构造
        entered = threading.Event()  # 首个线程已进入初始化段
        release = threading.Event()  # 主线程放行首个线程
        gate_lock = threading.Lock()
        init_count = {"n": 0}

        real_io = ThreadPoolExecutor(max_workers=2)
        real_cpu = ThreadPoolExecutor(max_workers=2)

        def slow_init(self):
            with gate_lock:
                init_count["n"] += 1
                if init_count["n"] == 1:
                    entered.set()
                    release.wait()  # 首个线程在此停车，确保第二个线程也进入 __init__
            self._io_pool = real_io
            self._cpu_pool = real_cpu

        instances = []
        errors = []

        def worker():
            try:
                start.wait()
                instances.append(ThreadPoolManager())
            except Exception as e:  # pragma: no cover - 仅测试防御
                errors.append(e)

        with patch.object(ThreadPoolManager, "_init_pools", slow_init):
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            start.wait()
            entered.wait()
            # 在首个线程完成初始化（_initialized=True）前，给第二个线程足够的调度窗口
            # 到达 _initialized 检查：DCL 下第二线程被锁阻塞（无副作用），旧模式下则进入
            # 初始化段使计数=2 → 断言失败，从而把回归捕获从概率性（~76%）提升到近乎确定。
            time.sleep(0.1)
            release.set()
            t1.join()
            t2.join()

        assert not errors, errors
        assert instances[0] is instances[1]
        assert init_count["n"] == 1, "并发首次访问时 _init_pools 应只执行一次"

        ThreadPoolManager._reset_singleton()
        real_io.shutdown(wait=False, cancel_futures=True)
        real_cpu.shutdown(wait=False, cancel_futures=True)


class TestThreadPoolManagerSubmit:
    def test_submit_sync_task(self):
        mgr = ThreadPoolManager()
        result = mgr.submit(TaskType.IO, lambda: 42)
        assert result.result(timeout=5) == 42

    def test_submit_with_args(self):
        mgr = ThreadPoolManager()
        result = mgr.submit(TaskType.IO, lambda x, y: x + y, 3, 4)
        assert result.result(timeout=5) == 7

    @pytest.mark.asyncio
    async def test_run_async(self):
        mgr = ThreadPoolManager()
        result = await mgr.run_async(TaskType.IO, lambda: "hello")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_run_async_with_kwargs(self):
        mgr = ThreadPoolManager()

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}"

        result = await mgr.run_async(TaskType.IO, greet, "World", greeting="Hi")
        assert result == "Hi, World"


class TestThreadPoolManagerResetSingletonErrorHandling:
    @patch("utils.thread_pool.ConfigHandler")
    def test_reset_singleton_handles_shutdown_runtime_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=RuntimeError("already shut down"))
        ThreadPoolManager._reset_singleton()
        assert ThreadPoolManager._instance is None

    @patch("utils.thread_pool.ConfigHandler")
    def test_reset_singleton_handles_shutdown_value_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=ValueError("bad value"))
        ThreadPoolManager._reset_singleton()
        assert ThreadPoolManager._instance is None


class TestThreadPoolManagerAtexitCleanup:
    """Tests for ThreadPoolManager._atexit_cleanup classmethod."""

    def test_atexit_cleanup_is_classmethod(self):
        assert isinstance(inspect.getattr_static(ThreadPoolManager, "_atexit_cleanup"), classmethod)

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_noop_when_no_instance(self, mock_ch):
        ThreadPoolManager._reset_singleton()
        assert ThreadPoolManager._instance is None
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_does_not_call_shutdown_when_no_instance(self, mock_ch):
        ThreadPoolManager._reset_singleton()
        with patch.object(ThreadPoolManager, "shutdown") as mock_shutdown:
            ThreadPoolManager._atexit_cleanup()
            mock_shutdown.assert_not_called()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_calls_shutdown_on_instance(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock()
        ThreadPoolManager._atexit_cleanup()
        tpm.shutdown.assert_called_once_with(wait=False, _quiet=True)

    @pytest.mark.parametrize(
        "error_class,error_msg",
        [
            (ValueError, "bad value"),
            (RuntimeError, "already shut down"),
            (OSError, "broken pipe"),
        ],
    )
    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_shutdown_exception(self, mock_ch, error_class, error_msg):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=error_class(error_msg))
        ThreadPoolManager._atexit_cleanup()


class TestThreadPoolManagerShutdownPoolAccess:
    @patch("utils.thread_pool.ConfigHandler")
    def test_io_pool_raises_after_shutdown(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        with pytest.raises(RuntimeError, match="Cannot access io_pool"):
            _ = tpm.io_pool

    @patch("utils.thread_pool.ConfigHandler")
    def test_cpu_pool_raises_after_shutdown(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        with pytest.raises(RuntimeError, match="Cannot access cpu_pool"):
            _ = tpm.cpu_pool


class TestThreadPoolManagerShutdownLoggingErrors:
    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_handles_logger_info_value_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        with patch("utils.thread_pool.logger") as mock_logger:
            mock_logger.info = MagicMock(side_effect=ValueError("handler closed"))
            mock_logger.handlers = []
            tpm.shutdown(wait=False)
        assert tpm._io_pool is None
        assert tpm._cpu_pool is None

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_handles_logger_info_os_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        with patch("utils.thread_pool.logger") as mock_logger:
            mock_logger.info = MagicMock(side_effect=OSError("broken pipe"))
            mock_logger.handlers = []
            tpm.shutdown(wait=False)
        assert tpm._io_pool is None
        assert tpm._cpu_pool is None


class TestThreadPoolManagerContextVarsPropagation:
    """OBS-010: Verify contextvars (e.g. correlation_id) propagate to worker threads."""

    @pytest.mark.asyncio
    async def test_correlation_id_propagates_to_worker(self):
        from utils.correlation import (
            set_correlation_id,
            get_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        set_correlation_id("test-ctx-01")
        try:
            mgr = ThreadPoolManager()
            result = await mgr.run_async(TaskType.IO, get_correlation_id)
            assert result == "test-ctx-01"
        finally:
            clear_correlation_id()

    @pytest.mark.asyncio
    async def test_correlation_id_propagates_with_kwargs(self):
        from utils.correlation import (
            set_correlation_id,
            get_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        set_correlation_id("test-ctx-02")
        try:
            mgr = ThreadPoolManager()

            def func(x, y=0):
                return get_correlation_id(), x + y

            cid, val = await mgr.run_async(TaskType.IO, func, 1, y=2)
            assert cid == "test-ctx-02"
            assert val == 3
        finally:
            clear_correlation_id()

    @pytest.mark.asyncio
    async def test_no_correlation_id_in_worker_when_none_set(self):
        from utils.correlation import get_correlation_id, clear_correlation_id

        clear_correlation_id()
        mgr = ThreadPoolManager()
        result = await mgr.run_async(TaskType.IO, get_correlation_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_run_async_raises_on_coroutine_function(self):
        mgr = ThreadPoolManager()

        async def dummy_coro():
            pass

        with pytest.raises(ValueError) as excinfo:
            await mgr.run_async(TaskType.IO, dummy_coro)
        assert "Cannot run coroutine function" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_run_async_raises_on_partial_coroutine_function(self):
        import functools

        mgr = ThreadPoolManager()

        async def dummy_coro(a, b=2):
            pass

        partial_coro = functools.partial(dummy_coro, 1, b=3)
        with pytest.raises(ValueError) as excinfo:
            await mgr.run_async(TaskType.IO, partial_coro)
        assert "Cannot run coroutine function" in str(excinfo.value)
        assert "dummy_coro" in str(excinfo.value)
