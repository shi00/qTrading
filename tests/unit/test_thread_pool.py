import inspect

import pytest
from unittest.mock import patch, MagicMock

from utils.thread_pool import ThreadPoolManager, TaskType, get_thread_pool_manager


@pytest.fixture(autouse=True)
def auto_reset_singleton():
    ThreadPoolManager._reset_singleton()
    yield
    ThreadPoolManager._reset_singleton()


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
        assert mgr._io_pool is not None

    def test_cpu_pool_exists(self):
        mgr = ThreadPoolManager()
        assert mgr._cpu_pool is not None

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
        assert pool is not None

    def test_cpu_pool_property_recovery(self):
        mgr = ThreadPoolManager()
        mgr._cpu_pool = None
        pool = mgr.cpu_pool
        assert pool is not None


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


class TestThreadPoolManagerAtexitShutdown:
    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_does_nothing_when_instance_is_none(self, mock_ch):
        ThreadPoolManager._reset_singleton()
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_calls_shutdown_when_instance_exists(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock()
        ThreadPoolManager._atexit_cleanup()
        tpm.shutdown.assert_called_once_with(wait=False, _quiet=True)

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_value_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=ValueError("closed"))
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_runtime_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=RuntimeError("already shut down"))
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_os_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=OSError("broken pipe"))
        ThreadPoolManager._atexit_cleanup()


class TestThreadPoolManagerAtexitCleanup:
    def test_atexit_cleanup_is_classmethod(self):
        assert isinstance(inspect.getattr_static(ThreadPoolManager, "_atexit_cleanup"), classmethod)

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_noop_when_no_instance(self, mock_ch):
        ThreadPoolManager._reset_singleton()
        assert ThreadPoolManager._instance is None
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_calls_shutdown_on_instance(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock()
        ThreadPoolManager._atexit_cleanup()
        tpm.shutdown.assert_called_once_with(wait=False, _quiet=True)

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_value_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=ValueError("bad value"))
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_runtime_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=RuntimeError("already shut down"))
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_handles_os_error(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown = MagicMock(side_effect=OSError("broken pipe"))
        ThreadPoolManager._atexit_cleanup()

    @patch("utils.thread_pool.ConfigHandler")
    def test_atexit_cleanup_does_not_call_shutdown_when_no_instance(self, mock_ch):
        ThreadPoolManager._reset_singleton()
        with patch.object(ThreadPoolManager, "shutdown") as mock_shutdown:
            ThreadPoolManager._atexit_cleanup()
            mock_shutdown.assert_not_called()


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
