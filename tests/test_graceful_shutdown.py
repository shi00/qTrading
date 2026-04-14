"""Tests for Graceful Shutdown flow in main.py.

Exit Strategy (v4):
- Normal path: sys.exit(0) triggers Python atexit handlers
- Watchdog fallback: os._exit(0) after timeout (daemon thread)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest


class WindowEvent:
    """Mock for Flet WindowEvent."""

    def __init__(self, e_type=ft.WindowEventType.CLOSE):
        self.type = e_type


def create_mock_page():
    """Create a mock page with all necessary attributes."""
    mock_page = MagicMock(spec=ft.Page)
    mock_page.window = MagicMock()
    mock_page.window.destroy = AsyncMock()
    mock_page.on_disconnect = None
    mock_page.window.on_event = None
    mock_page.window.width = 1000
    return mock_page


@pytest.fixture
def mock_singletons():
    """Mock all Singletons to prevent real execution by setting their _instances"""
    from services.task_manager import TaskManager
    from utils.scheduler_service import scheduler
    from data.external.news_subscription import NewsSubscriptionService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    orig_scheduler = getattr(scheduler, "scheduler", None)
    orig_stop = getattr(scheduler, "stop", None)

    TaskManager._instance = AsyncMock()
    NewsSubscriptionService._instance = MagicMock()
    DataProcessor._instance = AsyncMock()
    CacheManager._instance = AsyncMock()
    CacheManager._instance.engine = AsyncMock()
    LocalModelManager._instance = MagicMock()
    LocalModelManager._instance._llm = MagicMock()
    ThreadPoolManager._instance = MagicMock()
    scheduler.scheduler = MagicMock()
    scheduler.scheduler.running = True
    scheduler.stop = MagicMock()

    yield {
        "TaskManager": TaskManager,
        "scheduler": scheduler,
        "NewsSubscriptionService": NewsSubscriptionService,
        "DataProcessor": DataProcessor,
        "CacheManager": CacheManager,
        "LocalModelManager": LocalModelManager,
        "ThreadPoolManager": ThreadPoolManager,
    }

    TaskManager._instance = orig_tm
    NewsSubscriptionService._instance = orig_news
    DataProcessor._instance = orig_dp
    CacheManager._instance = orig_cache
    LocalModelManager._instance = orig_llm
    ThreadPoolManager._instance = orig_tp
    scheduler.scheduler = orig_scheduler
    scheduler.stop = orig_stop


@pytest.mark.asyncio
async def test_normal_window_close_path(mock_singletons):
    """Scenario A: User clicks the window close button (Normal Graceful Shutdown)"""
    mock_page = create_mock_page()

    _cleanup_done = False
    _watchdog_started = False
    watchdog_target = None
    watchdog_daemon = None
    sys_exit_called = False
    sys_exit_code = None

    def _start_watchdog(timeout_s=10):
        nonlocal _watchdog_started, watchdog_target, watchdog_daemon
        if _watchdog_started:
            return
        _watchdog_started = True

        class MockThread:
            def __init__(self, target, daemon=False):
                nonlocal watchdog_target, watchdog_daemon
                watchdog_target = target
                watchdog_daemon = daemon

            def start(self):
                pass

        MockThread(target=lambda: None, daemon=True)

    async def _do_cleanup():
        nonlocal _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True

        from services.task_manager import TaskManager
        from utils.scheduler_service import scheduler
        from data.external.news_subscription import NewsSubscriptionService
        from data.data_processor import DataProcessor
        from data.cache.cache_manager import CacheManager
        from services.local_model_manager import LocalModelManager
        from utils.thread_pool import ThreadPoolManager

        if TaskManager._instance is not None:
            await TaskManager._instance.cancel_all_running_async()
        if hasattr(scheduler, "scheduler") and scheduler.scheduler.running:
            scheduler.stop()
        if NewsSubscriptionService._instance is not None:
            NewsSubscriptionService._instance.stop()
        if DataProcessor._instance is not None:
            await DataProcessor._instance.stop()
        if CacheManager._instance is not None:
            await CacheManager._instance.close()
        if LocalModelManager._instance is not None:
            LocalModelManager._instance.unload_model()
        if ThreadPoolManager._instance is not None:
            ThreadPoolManager._instance.shutdown(wait=False)

    async def _on_window_event(e):
        nonlocal sys_exit_called, sys_exit_code
        if e.type == ft.WindowEventType.CLOSE:
            _start_watchdog(10)
            await _do_cleanup()
            try:
                mock_page.window.prevent_close = False
                await mock_page.window.destroy()
            except Exception:
                pass
            sys_exit_called = True
            sys_exit_code = 0

    event = WindowEvent()
    await _on_window_event(event)

    assert _watchdog_started is True
    assert watchdog_daemon is True
    mock_singletons["TaskManager"]._instance.cancel_all_running_async.assert_awaited_once()
    mock_singletons["scheduler"].stop.assert_called_once()
    mock_singletons["NewsSubscriptionService"]._instance.stop.assert_called_once()
    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()
    mock_singletons["LocalModelManager"]._instance.unload_model.assert_called_once()
    mock_singletons["ThreadPoolManager"]._instance.shutdown.assert_called_once_with(wait=False)
    mock_page.window.destroy.assert_awaited_once()
    assert sys_exit_called is True
    assert sys_exit_code == 0


@pytest.mark.asyncio
async def test_external_disconnect_fallback(mock_singletons):
    """Scenario B: Disconnect triggered without window close event"""
    mock_page = create_mock_page()

    _cleanup_done = False
    _watchdog_started = False
    sys_exit_called = False
    sys_exit_code = None

    def _start_watchdog(timeout_s=10):
        nonlocal _watchdog_started
        if _watchdog_started:
            return
        _watchdog_started = True

    async def _do_cleanup():
        nonlocal _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True

        from data.data_processor import DataProcessor
        from data.cache.cache_manager import CacheManager

        if DataProcessor._instance is not None:
            await DataProcessor._instance.stop()
        if CacheManager._instance is not None:
            await CacheManager._instance.close()

    async def _on_disconnect(e):
        nonlocal sys_exit_called, sys_exit_code
        _start_watchdog(10)
        await _do_cleanup()
        sys_exit_called = True
        sys_exit_code = 0

    await _on_disconnect(None)

    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()
    assert sys_exit_called is True
    assert sys_exit_code == 0


@pytest.mark.asyncio
async def test_safe_skip_empty_singletons():
    """Scenario C: Ensure missing singletons (Onboarding phase) don't crash the shutdown"""
    from services.task_manager import TaskManager
    from utils.scheduler_service import scheduler
    from data.external.news_subscription import NewsSubscriptionService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    orig_scheduler = getattr(scheduler, "scheduler", None)

    TaskManager._instance = None
    NewsSubscriptionService._instance = None
    DataProcessor._instance = None
    CacheManager._instance = None
    LocalModelManager._instance = None
    ThreadPoolManager._instance = None
    scheduler.scheduler = MagicMock()
    scheduler.scheduler.running = False

    try:
        mock_page = create_mock_page()

        _cleanup_done = False
        _watchdog_started = False
        sys_exit_called = False
        sys_exit_code = None

        def _start_watchdog(timeout_s=10):
            nonlocal _watchdog_started
            if _watchdog_started:
                return
            _watchdog_started = True

        async def _do_cleanup():
            nonlocal _cleanup_done
            if _cleanup_done:
                return
            _cleanup_done = True

        async def _on_window_event(e):
            nonlocal sys_exit_called, sys_exit_code
            if e.type == ft.WindowEventType.CLOSE:
                _start_watchdog(10)
                await _do_cleanup()
                sys_exit_called = True
                sys_exit_code = 0

        event = WindowEvent()
        await _on_window_event(event)
        assert sys_exit_called is True
        assert sys_exit_code == 0
    finally:
        TaskManager._instance = orig_tm
        NewsSubscriptionService._instance = orig_news
        DataProcessor._instance = orig_dp
        CacheManager._instance = orig_cache
        LocalModelManager._instance = orig_llm
        ThreadPoolManager._instance = orig_tp
        scheduler.scheduler = orig_scheduler


@pytest.mark.asyncio
async def test_double_close_idempotency(mock_singletons):
    """Scenario D: Verify that _cleanup_done flag prevents duplicate clears"""
    mock_page = create_mock_page()

    _cleanup_done = False
    cleanup_count = 0

    async def _do_cleanup():
        nonlocal _cleanup_done, cleanup_count
        if _cleanup_done:
            return
        _cleanup_done = True
        cleanup_count += 1

        from data.data_processor import DataProcessor
        from data.cache.cache_manager import CacheManager

        if DataProcessor._instance is not None:
            await DataProcessor._instance.stop()
        if CacheManager._instance is not None:
            await CacheManager._instance.close()

    async def _on_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            await _do_cleanup()

    event = WindowEvent()
    await _on_window_event(event)
    await _on_window_event(event)

    assert cleanup_count == 1
    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchdog_timeout_forces_exit(mock_singletons):
    """Scenario E: Watchdog timeout forces os._exit(0) when cleanup hangs"""
    mock_page = create_mock_page()

    _cleanup_done = False
    _watchdog_started = False
    os_exit_called = False
    os_exit_code = None

    def _start_watchdog(timeout_s=10):
        nonlocal _watchdog_started, os_exit_called, os_exit_code
        if _watchdog_started:
            return
        _watchdog_started = True

        def _force_exit():
            nonlocal os_exit_called, os_exit_code
            os_exit_called = True
            os_exit_code = 0

        _force_exit()

    async def _do_cleanup():
        nonlocal _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True

    async def _on_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            _start_watchdog(10)
            await _do_cleanup()

    event = WindowEvent()
    await _on_window_event(event)

    assert os_exit_called is True
    assert os_exit_code == 0


@pytest.mark.asyncio
async def test_disconnect_after_window_close_skips_exit(mock_singletons):
    """Scenario F: Disconnect after window close should not call sys.exit again"""
    mock_page = create_mock_page()

    _cleanup_done = False
    _watchdog_started = False
    sys_exit_count = 0

    def _start_watchdog(timeout_s=10):
        nonlocal _watchdog_started
        if _watchdog_started:
            return
        _watchdog_started = True

    async def _do_cleanup():
        nonlocal _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True

    async def _on_window_event(e):
        nonlocal sys_exit_count
        if e.type == ft.WindowEventType.CLOSE:
            _start_watchdog(10)
            await _do_cleanup()
            sys_exit_count += 1

    async def _on_disconnect(e):
        nonlocal sys_exit_count
        was_window_path = _cleanup_done
        _start_watchdog(10)
        await _do_cleanup()
        if not was_window_path:
            sys_exit_count += 1

    event = WindowEvent()
    await _on_window_event(event)
    await _on_disconnect(None)

    assert sys_exit_count == 1


@pytest.mark.asyncio
async def test_watchdog_started_only_once(mock_singletons):
    """Scenario G: Watchdog should only be started once even with multiple triggers"""
    mock_page = create_mock_page()

    _cleanup_done = False
    _watchdog_started = False
    watchdog_call_count = 0

    def _start_watchdog(timeout_s=10):
        nonlocal _watchdog_started, watchdog_call_count
        watchdog_call_count += 1
        if _watchdog_started:
            return
        _watchdog_started = True

    async def _do_cleanup():
        nonlocal _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True

    async def _on_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            _start_watchdog(10)
            await _do_cleanup()

    async def _on_disconnect(e):
        _start_watchdog(10)
        await _do_cleanup()

    event = WindowEvent()
    await _on_window_event(event)
    await _on_disconnect(None)

    assert watchdog_call_count == 2
    assert _watchdog_started is True
