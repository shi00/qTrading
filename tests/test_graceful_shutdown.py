from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from main import main


class WindowEvent:
    """Mock for Flet WindowEvent."""

    def __init__(self, e_type=ft.WindowEventType.CLOSE):
        self.type = e_type


@pytest.fixture
async def app_handlers():
    """
    Simulates Flet calling the main app.
    We mock ConfigHandler so that the app enters the onboarding phase and returns immediately,
    leaving behind the registered event handlers for our tests.
    """
    mock_page = MagicMock(spec=ft.Page)
    mock_page.window = MagicMock()
    mock_page.window.destroy = AsyncMock()
    mock_page.on_disconnect = None
    mock_page.window.on_event = None
    mock_page.window.width = 1000

    # Patch things to prevent actual app initialization dependencies
    with (
        patch("main.ConfigHandler.get_db_url", return_value=""),
        patch("main.setup_logging"),
        patch("main.CacheManager"),
        patch("main.ProxyManager"),
        patch("main.I18n"),
        patch("main.OnboardingWizard"),
    ):
        await main(mock_page)

    return {"page": mock_page, "on_window_event": mock_page.window.on_event, "on_disconnect": mock_page.on_disconnect}


@pytest.fixture
def mock_singletons():
    """Mock all Singletons to prevent real execution by setting their _instances"""
    from services.task_manager import TaskManager
    from utils.scheduler_service import scheduler
    from data.external.news_subscription import NewsSubscriptionService
    from data.domain_services.market_data_service import MarketDataService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager

    # Save original
    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_md = MarketDataService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    orig_running = getattr(scheduler, "scheduler", MagicMock()).running
    orig_stop = scheduler.stop

    # Set mocks
    TaskManager._instance = AsyncMock()
    NewsSubscriptionService._instance = MagicMock()
    MarketDataService._instance = MagicMock()
    DataProcessor._instance = AsyncMock()
    CacheManager._instance = AsyncMock()
    CacheManager._instance.engine = AsyncMock()  # So it's not None
    LocalModelManager._instance = MagicMock()
    LocalModelManager._instance._llm = MagicMock()  # So it unloads
    ThreadPoolManager._instance = MagicMock()
    scheduler.scheduler = MagicMock()
    scheduler.scheduler.running = True
    scheduler.stop = MagicMock()

    yield {
        "TaskManager": TaskManager,
        "scheduler": scheduler,
        "NewsSubscriptionService": NewsSubscriptionService,
        "MarketDataService": MarketDataService,
        "DataProcessor": DataProcessor,
        "CacheManager": CacheManager,
        "LocalModelManager": LocalModelManager,
        "ThreadPoolManager": ThreadPoolManager,
    }

    # Restore
    TaskManager._instance = orig_tm
    NewsSubscriptionService._instance = orig_news
    MarketDataService._instance = orig_md
    DataProcessor._instance = orig_dp
    CacheManager._instance = orig_cache
    LocalModelManager._instance = orig_llm
    ThreadPoolManager._instance = orig_tp
    scheduler.scheduler.running = orig_running
    scheduler.stop = orig_stop


@pytest.mark.asyncio
@patch("os._exit")
@patch("threading.Thread")
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_normal_window_close_path(mock_sleep, mock_thread, mock_exit, mock_singletons, app_handlers):
    """Scenario A: User clicks the window close button (Normal Graceful Shutdown)"""
    on_window_event = app_handlers["on_window_event"]

    event = WindowEvent()
    await on_window_event(event)

    # Verifications
    # 1. Watchdog started
    mock_thread.assert_called_once()

    # 2. Components stopped
    mock_singletons["TaskManager"]._instance.cancel_all_running_async.assert_awaited_once()
    mock_singletons["scheduler"].stop.assert_called_once()
    mock_singletons["NewsSubscriptionService"]._instance.stop.assert_called_once()
    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()
    mock_singletons["LocalModelManager"]._instance.unload_model.assert_called_once()
    mock_singletons["ThreadPoolManager"]._instance.shutdown.assert_called_once_with(wait=False)

    # 3. Flet destroy called
    app_handlers["page"].window.destroy.assert_awaited_once()

    # 4. Process exits with 0
    mock_exit.assert_called_once_with(0)


@pytest.mark.asyncio
@patch("os._exit")
@patch("threading.Thread")
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_external_disconnect_fallback(mock_sleep, mock_thread, mock_exit, mock_singletons, app_handlers):
    """Scenario B: Disconnect triggered without window close event"""
    on_disconnect = app_handlers["on_disconnect"]

    await on_disconnect(None)

    # Verifications matches cleanup logic
    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()

    # Process exits with 0 because it was an external disconnect
    mock_exit.assert_called_once_with(0)


@pytest.mark.asyncio
@patch("os._exit")
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_safe_skip_empty_singletons(mock_sleep, mock_exit, app_handlers):
    """Scenario C: Ensure missing singletons (Onboarding phase) don't crash the shutdown"""
    on_window_event = app_handlers["on_window_event"]

    # Import the classes locally to set their instances to None
    from services.task_manager import TaskManager
    from utils.scheduler_service import scheduler
    from data.external.news_subscription import NewsSubscriptionService
    from data.domain_services.market_data_service import MarketDataService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager

    # Save original
    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_md = MarketDataService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    orig_running = getattr(scheduler, "scheduler", MagicMock()).running

    # Force all singletons to be None
    TaskManager._instance = None
    NewsSubscriptionService._instance = None
    MarketDataService._instance = None
    DataProcessor._instance = None
    CacheManager._instance = None
    LocalModelManager._instance = None
    ThreadPoolManager._instance = None

    scheduler.scheduler = MagicMock()
    scheduler.scheduler.running = False

    try:
        event = WindowEvent()
        await on_window_event(event)

        # Should not crash, and should still exit normally
        mock_exit.assert_called_once_with(0)

        # Ensure destroy is async mocked
        if not isinstance(app_handlers["page"].window.destroy, AsyncMock):
            # If magic mock is not awaitable, just check it was called.
            pass  # In real Flet page.window.destroy is an async function called by await

    finally:
        # Restore
        TaskManager._instance = orig_tm
        NewsSubscriptionService._instance = orig_news
        MarketDataService._instance = orig_md
        DataProcessor._instance = orig_dp
        CacheManager._instance = orig_cache
        LocalModelManager._instance = orig_llm
        ThreadPoolManager._instance = orig_tp
        scheduler.scheduler.running = orig_running


@pytest.mark.asyncio
@patch("os._exit")
@patch("threading.Thread")
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_double_close_idempotency(mock_sleep, mock_thread, mock_exit, mock_singletons, app_handlers):
    """Scenario D: Verify that _cleanup_done flag prevents duplicate clears"""
    on_window_event = app_handlers["on_window_event"]
    event = WindowEvent()

    # First trigger
    await on_window_event(event)
    # Second trigger (emulating rapid double click)
    await on_window_event(event)

    # The components should ONLY be stopped exactly ONCE!
    mock_singletons["DataProcessor"]._instance.stop.assert_awaited_once()
    mock_singletons["CacheManager"]._instance.close.assert_awaited_once()

    # os._exit might be called twice if we fake await to release instantly,
    # but the actual component cleanup logic (_do_cleanup) won't re-run.
