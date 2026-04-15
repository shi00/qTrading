"""Tests for ShutdownCoordinator — the core shutdown logic extracted from main.py.

Tests directly import and exercise ShutdownCoordinator instead of maintaining
a parallel simplified copy of the cleanup code.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.shutdown import ShutdownCoordinator


@pytest.fixture
def mock_singletons():
    """Mock all Singletons to prevent real execution by setting their _instances"""
    from services.task_manager import TaskManager
    from data.external.news_subscription import NewsSubscriptionService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager
    from utils.scheduler_service import scheduler

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    orig_scheduler_running = getattr(scheduler.scheduler, "running", None) if hasattr(scheduler, "scheduler") else None
    orig_scheduler_stop = getattr(scheduler, "stop", None)

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
    if orig_scheduler_running is not None:
        scheduler.scheduler.running = orig_scheduler_running
    if orig_scheduler_stop is not None:
        scheduler.stop = orig_scheduler_stop


@pytest.mark.asyncio
async def test_full_cleanup_all_steps(mock_singletons):
    """Verify all 7 cleanup steps are executed in order with all singletons present."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator.do_cleanup()

    mock_singletons["TaskManager"]._instance.cancel_all_running_async.assert_awaited_once()
    mock_singletons["scheduler"].stop.assert_called_once()
    mock_singletons["NewsSubscriptionService"]._instance.stop.assert_called_once()
    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()
    mock_singletons["LocalModelManager"]._instance.unload_model.assert_called_once()
    mock_singletons["ThreadPoolManager"]._instance.shutdown.assert_called_once_with(wait=False)

    assert coordinator.cleanup_done is True


@pytest.mark.asyncio
async def test_cleanup_idempotent(mock_singletons):
    """Verify that calling do_cleanup twice only executes cleanup once."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator.do_cleanup()
        await coordinator.do_cleanup()

    mock_singletons["TaskManager"]._instance.cancel_all_running_async.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()
    assert coordinator.cleanup_done is True


@pytest.mark.asyncio
async def test_safe_skip_empty_singletons():
    """Ensure missing singletons (Onboarding phase) don't crash the shutdown."""
    from services.task_manager import TaskManager
    from data.external.news_subscription import NewsSubscriptionService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager
    from utils.scheduler_service import scheduler

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    orig_scheduler_running = getattr(scheduler.scheduler, "running", None) if hasattr(scheduler, "scheduler") else None

    TaskManager._instance = None
    NewsSubscriptionService._instance = None
    DataProcessor._instance = None
    CacheManager._instance = None
    LocalModelManager._instance = None
    ThreadPoolManager._instance = None
    scheduler.scheduler = MagicMock()
    scheduler.scheduler.running = False

    try:
        coordinator = ShutdownCoordinator(page=None)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await coordinator.do_cleanup()
        assert coordinator.cleanup_done is True
    finally:
        TaskManager._instance = orig_tm
        NewsSubscriptionService._instance = orig_news
        DataProcessor._instance = orig_dp
        CacheManager._instance = orig_cache
        LocalModelManager._instance = orig_llm
        ThreadPoolManager._instance = orig_tp
        if orig_scheduler_running is not None:
            scheduler.scheduler.running = orig_scheduler_running


@pytest.mark.asyncio
async def test_toast_manager_cleanup_with_page():
    """Verify Toast Manager is cleaned up when page has toast attribute."""
    mock_page = MagicMock()
    mock_toast = MagicMock()
    mock_toast.stop_all = MagicMock(return_value=None)
    mock_page.toast = mock_toast

    coordinator = ShutdownCoordinator(page=mock_page)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator._step4_clear_toast()

    mock_toast.stop_all.assert_called_once()


@pytest.mark.asyncio
async def test_toast_manager_skipped_without_page():
    """Verify Toast Manager step is skipped when page is None."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator._step4_clear_toast()


@pytest.mark.asyncio
async def test_ai_model_unloaded_when_present():
    """Verify AI model is unloaded when LocalModelManager has an LLM instance."""
    from services.local_model_manager import LocalModelManager

    orig = LocalModelManager._instance
    LocalModelManager._instance = MagicMock()
    LocalModelManager._instance._llm = MagicMock()

    try:
        coordinator = ShutdownCoordinator(page=None)
        coordinator._step6_unload_ai_model()
        LocalModelManager._instance.unload_model.assert_called_once()
    finally:
        LocalModelManager._instance = orig


@pytest.mark.asyncio
async def test_ai_model_skipped_when_absent():
    """Verify AI model step is skipped when no LLM is loaded."""
    from services.local_model_manager import LocalModelManager

    orig = LocalModelManager._instance
    LocalModelManager._instance = MagicMock()
    LocalModelManager._instance._llm = None

    try:
        coordinator = ShutdownCoordinator(page=None)
        coordinator._step6_unload_ai_model()
        LocalModelManager._instance.unload_model.assert_not_called()
    finally:
        LocalModelManager._instance = orig


@pytest.mark.asyncio
async def test_db_engine_disposed_when_present():
    """Verify DB engine is disposed when CacheManager has an engine."""
    from data.cache.cache_manager import CacheManager

    orig = CacheManager._instance
    CacheManager._instance = AsyncMock()
    CacheManager._instance.engine = AsyncMock()
    CacheManager._instance.close = AsyncMock()

    try:
        coordinator = ShutdownCoordinator(page=None)
        await coordinator._step5_dispose_db_engine()
        CacheManager._instance.close.assert_awaited_once()
    finally:
        CacheManager._instance = orig


@pytest.mark.asyncio
async def test_db_engine_skipped_when_absent():
    """Verify DB engine step is skipped when no engine exists."""
    from data.cache.cache_manager import CacheManager

    orig = CacheManager._instance
    CacheManager._instance = None

    try:
        coordinator = ShutdownCoordinator(page=None)
        await coordinator._step5_dispose_db_engine()
    finally:
        CacheManager._instance = orig


def test_watchdog_started_once():
    """Verify watchdog can only be started once."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("threading.Thread") as mock_thread:
        coordinator.start_watchdog(10)
        assert coordinator.watchdog_started is True
        mock_thread.assert_called_once()

        coordinator.start_watchdog(10)
        mock_thread.assert_called_once()


def test_watchdog_daemon_thread():
    """Verify watchdog creates a daemon thread."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("threading.Thread") as mock_thread:
        coordinator.start_watchdog(10)
        _, kwargs = mock_thread.call_args
        assert kwargs.get("daemon") is True


@pytest.mark.asyncio
async def test_disconnect_after_cleanup_skips_exit():
    """Verify that disconnect after window close sees cleanup_done=True and skips exit."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator.do_cleanup()

    was_window_path = coordinator.cleanup_done
    assert was_window_path is True


@pytest.mark.asyncio
async def test_step_exception_does_not_crash(mock_singletons):
    """Verify that an exception in one step doesn't prevent other steps from running."""
    mock_singletons["TaskManager"]._instance.cancel_all_running_async.side_effect = RuntimeError("boom")

    coordinator = ShutdownCoordinator(page=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator.do_cleanup()

    assert coordinator.cleanup_done is True
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()
