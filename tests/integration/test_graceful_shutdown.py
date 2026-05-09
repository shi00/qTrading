"""Tests for ShutdownCoordinator — the core shutdown logic extracted from main.py.

Tests directly import and exercise ShutdownCoordinator instead of maintaining
a parallel simplified copy of the cleanup code.
"""

import asyncio
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
    from data.domain_services.market_data_service import MarketDataService
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager
    from utils.scheduler_service import SchedulerService

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_mds = MarketDataService._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    svc = SchedulerService()
    orig_scheduler_running = getattr(svc.scheduler, "running", None) if hasattr(svc, "scheduler") else None
    orig_scheduler_stop = getattr(svc, "stop", None)

    TaskManager._instance = AsyncMock()
    NewsSubscriptionService._instance = AsyncMock()
    DataProcessor._instance = AsyncMock()
    CacheManager._instance = AsyncMock()
    CacheManager._instance.engine = AsyncMock()
    MarketDataService._instance = AsyncMock()
    LocalModelManager._instance = MagicMock()
    LocalModelManager._instance._llm = MagicMock()
    ThreadPoolManager._instance = MagicMock()
    svc.scheduler = MagicMock()
    svc.scheduler.running = True
    svc.stop = MagicMock()

    yield {
        "TaskManager": TaskManager,
        "scheduler": svc,
        "NewsSubscriptionService": NewsSubscriptionService,
        "DataProcessor": DataProcessor,
        "CacheManager": CacheManager,
        "MarketDataService": MarketDataService,
        "LocalModelManager": LocalModelManager,
        "ThreadPoolManager": ThreadPoolManager,
    }

    TaskManager._instance = orig_tm
    NewsSubscriptionService._instance = orig_news
    DataProcessor._instance = orig_dp
    CacheManager._instance = orig_cache
    MarketDataService._instance = orig_mds
    LocalModelManager._instance = orig_llm
    ThreadPoolManager._instance = orig_tp
    if orig_scheduler_running is not None:
        svc.scheduler.running = orig_scheduler_running
    if orig_scheduler_stop is not None:
        svc.stop = orig_scheduler_stop


@pytest.mark.asyncio
async def test_full_cleanup_all_steps(mock_singletons):
    """Verify all 7 cleanup steps are executed in order with all singletons present."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        ok = await coordinator.do_cleanup()

    mock_singletons["TaskManager"]._instance.cancel_all_running_async.assert_awaited_once()
    mock_singletons["scheduler"].stop.assert_called_once()
    mock_singletons["NewsSubscriptionService"]._instance.stop_async.assert_awaited_once()
    mock_singletons["MarketDataService"]._instance.stop_async.assert_awaited_once()
    mock_singletons["DataProcessor"]._instance.close.assert_awaited_once()
    mock_singletons["LocalModelManager"]._instance.unload_model.assert_called_once()
    mock_singletons["ThreadPoolManager"]._instance.shutdown.assert_called_once_with(wait=False)

    assert ok is True
    assert coordinator.cleanup_done is True
    assert coordinator.cleanup_success is True


@pytest.mark.asyncio
async def test_cleanup_idempotent(mock_singletons):
    """Verify that calling do_cleanup twice only executes cleanup once."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        ok1 = await coordinator.do_cleanup()
        ok2 = await coordinator.do_cleanup()

    mock_singletons["TaskManager"]._instance.cancel_all_running_async.assert_awaited_once()
    mock_singletons["DataProcessor"]._instance.close.assert_awaited_once()
    assert ok1 is True
    assert ok2 is True
    assert coordinator.cleanup_done is True


@pytest.mark.asyncio
async def test_safe_skip_empty_singletons():
    """Ensure missing singletons (Onboarding phase) don't crash the shutdown."""
    from services.task_manager import TaskManager
    from data.external.news_subscription import NewsSubscriptionService
    from data.data_processor import DataProcessor
    from data.cache.cache_manager import CacheManager
    from data.domain_services.market_data_service import MarketDataService
    from services.local_model_manager import LocalModelManager
    from utils.thread_pool import ThreadPoolManager
    from utils.scheduler_service import SchedulerService

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_mds = MarketDataService._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    svc = SchedulerService()
    orig_scheduler_running = getattr(svc.scheduler, "running", None) if hasattr(svc, "scheduler") else None

    TaskManager._instance = None
    NewsSubscriptionService._instance = None
    DataProcessor._instance = None
    CacheManager._instance = None
    MarketDataService._instance = None
    LocalModelManager._instance = None
    ThreadPoolManager._instance = None
    svc.scheduler = MagicMock()
    svc.scheduler.running = False

    try:
        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            ok = await coordinator.do_cleanup()
        assert ok is True
        assert coordinator.cleanup_done is True
    finally:
        TaskManager._instance = orig_tm
        NewsSubscriptionService._instance = orig_news
        DataProcessor._instance = orig_dp
        CacheManager._instance = orig_cache
        MarketDataService._instance = orig_mds
        LocalModelManager._instance = orig_llm
        ThreadPoolManager._instance = orig_tp
        if orig_scheduler_running is not None:
            svc.scheduler.running = orig_scheduler_running


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
        await coordinator._step5_unload_ai_model()
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
        await coordinator._step5_unload_ai_model()
        LocalModelManager._instance.unload_model.assert_not_called()
    finally:
        LocalModelManager._instance = orig


@pytest.mark.asyncio
async def test_processor_close_called():
    """Verify DataProcessor.close() is called (which internally handles DB engine disposal)."""
    from data.data_processor import DataProcessor

    orig = DataProcessor._instance
    DataProcessor._instance = AsyncMock()

    try:
        coordinator = ShutdownCoordinator(page=None)
        await coordinator._step2_close_processor()
        DataProcessor._instance.close.assert_awaited_once()
    finally:
        DataProcessor._instance = orig


@pytest.mark.asyncio
async def test_processor_close_skipped_when_absent():
    """Verify processor close step is skipped when no processor exists."""
    from data.data_processor import DataProcessor

    orig = DataProcessor._instance
    DataProcessor._instance = None

    try:
        coordinator = ShutdownCoordinator(page=None)
        await coordinator._step2_close_processor()
    finally:
        DataProcessor._instance = orig


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


def test_watchdog_can_restart_after_cancel():
    """Verify watchdog can be restarted after cancel_watchdog is called."""
    coordinator = ShutdownCoordinator(page=None)

    with patch("threading.Thread") as mock_thread:
        coordinator.start_watchdog(10)
        assert coordinator.watchdog_started is True
        assert mock_thread.call_count == 1

        coordinator.cancel_watchdog()
        assert coordinator.watchdog_started is False

        coordinator.start_watchdog(10)
        assert coordinator.watchdog_started is True
        assert mock_thread.call_count == 2


def test_watchdog_uses_force_exit_callback():
    """Verify watchdog uses the injected force_exit_callback instead of os._exit."""
    exit_calls = []
    coordinator = ShutdownCoordinator(page=None, force_exit_callback=lambda code: exit_calls.append(code))

    with patch("threading.Thread") as mock_thread:
        coordinator.start_watchdog(10)
        call_args = mock_thread.call_args
        assert call_args is not None
        thread_target = call_args.kwargs.get("target") or (call_args.args[0] if call_args.args else None)
        assert thread_target is not None

        thread_target()

    assert exit_calls == [1]


def test_watchdog_cancel_prevents_force_exit():
    """Verify canceling watchdog prevents force exit callback from being called."""
    exit_calls = []
    coordinator = ShutdownCoordinator(page=None, force_exit_callback=lambda code: exit_calls.append(code))

    with patch("threading.Thread") as mock_thread:
        coordinator.start_watchdog(0.01)
        call_args = mock_thread.call_args
        assert call_args is not None
        thread_target = call_args.kwargs.get("target") or (call_args.args[0] if call_args.args else None)
        assert thread_target is not None

        coordinator.cancel_watchdog()
        thread_target()

    assert exit_calls == []


@pytest.mark.asyncio
async def test_disconnect_after_cleanup_skips_exit():
    """Verify that disconnect after window close sees cleanup_done=True and skips exit."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await coordinator.do_cleanup()

    was_window_path = coordinator.cleanup_done
    assert was_window_path is True


@pytest.mark.asyncio
async def test_step_exception_does_not_crash(mock_singletons):
    """Verify that an exception in one step doesn't prevent other steps from running."""
    mock_singletons["TaskManager"]._instance.cancel_all_running_async.side_effect = RuntimeError("boom")

    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        ok = await coordinator.do_cleanup()

    assert ok is False
    assert coordinator.cleanup_done is True
    assert any(result.name == "Step 0" and result.ok is False for result in coordinator.step_results)


@pytest.mark.asyncio
async def test_cleanup_timeout_returns_false(mock_singletons):
    """Verify cleanup returns False when total timeout is exceeded."""
    coordinator = ShutdownCoordinator(page=None)

    async def _slow_steps(*_args, **_kwargs):
        await asyncio.sleep(1.0)

    with patch.object(
        coordinator,
        "_run_cleanup_steps",
        new=AsyncMock(side_effect=_slow_steps),
    ):
        ok = await coordinator.do_cleanup(timeout_s=0.01, step_timeout_s=0.01)

    assert ok is False
    assert coordinator.cleanup_done is True
    assert coordinator.cleanup_success is False


@pytest.mark.asyncio
async def test_cleanup_single_flight_concurrent_calls():
    """Verify concurrent do_cleanup calls join the same cleanup task."""
    coordinator = ShutdownCoordinator(page=None)
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def _fake_execute(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        return True

    with patch.object(coordinator, "_execute_cleanup", new=AsyncMock(side_effect=_fake_execute)):
        task1 = asyncio.create_task(coordinator.do_cleanup())
        await started.wait()
        task2 = asyncio.create_task(coordinator.do_cleanup())
        await asyncio.sleep(0)
        release.set()
        res1, res2 = await asyncio.gather(task1, task2)

    assert call_count == 1
    assert res1 is True
    assert res2 is True


@pytest.mark.asyncio
async def test_cleanup_caller_cancel_does_not_cancel_single_flight():
    """Verify caller cancellation does not cancel the shared cleanup task."""
    coordinator = ShutdownCoordinator(page=None)
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def _fake_execute(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        return True

    with patch.object(coordinator, "_execute_cleanup", new=AsyncMock(side_effect=_fake_execute)):
        first = asyncio.create_task(coordinator.do_cleanup())
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        release.set()
        result = await coordinator.do_cleanup()

    assert call_count == 1
    assert result is True


@pytest.mark.asyncio
async def test_step5_timeout_marks_cleanup_failed(mock_singletons):
    """Verify blocking Step 5 (AI model unload) is constrained by step timeout and marks cleanup failed."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

    async def _noop():
        pass

    async def _blocking_unload():
        await asyncio.sleep(5)

    with (
        patch.object(coordinator, "_step0_cancel_tasks", side_effect=_noop),
        patch.object(coordinator, "_step1_stop_services", side_effect=_noop),
        patch.object(coordinator, "_step2_close_processor", side_effect=_noop),
        patch.object(coordinator, "_step3_flush_db_writes", side_effect=_noop),
        patch.object(coordinator, "_step4_clear_toast", side_effect=_noop),
        patch.object(coordinator, "_step5_unload_ai_model", side_effect=_blocking_unload),
    ):
        ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=0.5)

    assert ok is False
    step5 = next(result for result in coordinator.step_results if result.name == "Step 5")
    assert step5.ok is False
    assert step5.timed_out is True


@pytest.mark.asyncio
async def test_step3_flush_exception_marks_cleanup_failed(mock_singletons):
    """Verify Step 3 flush errors are propagated as cleanup failure."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)
    mock_singletons["TaskManager"]._instance.flush_persistence = AsyncMock(side_effect=RuntimeError("flush failed"))

    with patch("asyncio.sleep", new_callable=AsyncMock):
        ok = await coordinator.do_cleanup()

    assert ok is False
    step3 = next(result for result in coordinator.step_results if result.name == "Step 3")
    assert step3.ok is False
    assert step3.timed_out is False


@pytest.mark.asyncio
async def test_step5_exception_marks_cleanup_failed(mock_singletons):
    """Verify Step 5 (AI model) runtime errors bubble up and fail cleanup."""
    coordinator = ShutdownCoordinator(page=None)

    with patch.object(coordinator, "_step5_unload_ai_model", side_effect=RuntimeError("llm unload failed")):
        ok = await coordinator.do_cleanup()

    assert ok is False
    step5 = next(result for result in coordinator.step_results if result.name == "Step 5")
    assert step5.ok is False
    assert step5.timed_out is False


@pytest.mark.asyncio
async def test_service_stop_delay_configurable(mock_singletons):
    """Verify service_stop_delay parameter controls the sleep after stopping services."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0.3)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await coordinator._step1_stop_services()

        mock_sleep.assert_called_once_with(0.3)


@pytest.mark.asyncio
async def test_service_stop_delay_zero_skips_sleep(mock_singletons):
    """Verify service_stop_delay=0 skips the sleep after stopping services."""
    coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await coordinator._step1_stop_services()

        mock_sleep.assert_not_called()
