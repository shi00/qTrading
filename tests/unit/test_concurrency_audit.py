import asyncio
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from data.external.news_subscription import NewsSubscriptionService
from data.domain_services.market_data_service import MarketDataService
from utils.rate_limiter import TokenBucket
from utils.thread_pool import ThreadPoolManager


@pytest.fixture(autouse=True)
def reset_news_singleton():
    NewsSubscriptionService._instance = None
    NewsSubscriptionService._initialized = False
    yield
    NewsSubscriptionService._instance = None
    NewsSubscriptionService._initialized = False


class TestNewsSubscriptionStopAlwaysCancelsTasks:
    """C-P1-2: stop() must always cancel tasks and reset state,
    regardless of whether called from event loop or outside."""

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_stop_outside_loop_cancels_fetch_task(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._current_fetch_task = mock_task
        svc._processing_task = None
        svc.stop()
        mock_task.cancel.assert_called_once()
        assert svc._current_fetch_task is None
        assert svc._running is False

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_stop_outside_loop_cancels_processing_task(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._current_fetch_task = None
        svc._processing_task = mock_task
        svc.stop()
        mock_task.cancel.assert_called_once()
        assert svc._processing_task is None
        assert svc._running is False

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_stop_outside_loop_clears_state(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._last_news_time = "10:00"
        svc._last_news_content = "old content"
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        assert svc._last_news_time is None
        assert svc._last_news_content is None
        assert svc._running is False

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_stop_inside_loop_cancels_tasks_immediately(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        mock_fetch = MagicMock()
        mock_fetch.done.return_value = False
        svc._current_fetch_task = mock_fetch
        mock_proc = MagicMock()
        mock_proc.done.return_value = False
        svc._processing_task = mock_proc
        svc.stop()
        mock_fetch.cancel.assert_called_once()
        mock_proc.cancel.assert_called_once()
        assert svc._running is False
        assert svc._current_fetch_task is None
        assert svc._processing_task is None

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_stop_inside_loop_schedules_graceful_drain(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        with patch.object(svc, "stop_async", new_callable=AsyncMock) as mock_stop_async:
            svc.stop()
            await asyncio.sleep(0.1)
            mock_stop_async.assert_called()


class TestMarketDataServiceStopAlwaysCancelsTask:
    """C-P1-3: stop() must always cancel the background task,
    regardless of whether called from event loop or outside."""

    def setup_method(self):
        MarketDataService._reset_singleton()

    def teardown_method(self):
        MarketDataService._reset_singleton()

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_outside_loop_cancels_task(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._task = mock_task
        svc.stop()
        mock_task.cancel.assert_called_once()
        assert svc._task is None
        assert svc._running is False
        assert svc._cached_data is None

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_inside_loop_cancels_and_schedules_async(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._task = mock_task
        with patch.object(svc, "stop_async", new_callable=AsyncMock) as mock_stop_async:
            svc.stop()
            mock_task.cancel.assert_called_once()
            assert svc._running is False
            await asyncio.sleep(0.1)
            mock_stop_async.assert_called()

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_already_done_task_no_cancel(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = True
        svc._task = mock_task
        svc.stop()
        mock_task.cancel.assert_not_called()
        assert svc._task is None


class TestTokenBucketConsumeRaisesInAsyncContext:
    """C-P1-5: consume() must raise RuntimeError when called from
    async context, not just warn, to prevent blocking the event loop."""

    @pytest.mark.asyncio
    async def test_consume_raises_in_async_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=10, rate=1)
        with pytest.raises(RuntimeError, match="TokenBucket.consume()"):
            bucket.consume()

    @pytest.mark.asyncio
    async def test_consume_async_works_in_async_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=10, rate=1)
        await bucket.consume_async()
        assert bucket.tokens == 9

    def test_consume_works_in_sync_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=10, rate=1)
        bucket.consume()
        assert bucket.tokens == 9

    def test_consume_blocks_when_insufficient_tokens(self):
        bucket = TokenBucket(start_tokens=0, capacity=10, rate=10)
        start = time.monotonic()
        bucket.consume(tokens=1)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05

    @pytest.mark.asyncio
    async def test_consume_async_suspends_when_insufficient_tokens(self):
        bucket = TokenBucket(start_tokens=0, capacity=10, rate=100)
        start = time.monotonic()
        await bucket.consume_async(tokens=1)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.005


class TestThreadPoolShutdownWaitConsistency:
    """C-P2-2: shutdown() must pass the same wait parameter to both
    IO and CPU pools, not hardcode wait=False for CPU pool."""

    def setup_method(self):
        ThreadPoolManager._reset_singleton()

    def teardown_method(self):
        ThreadPoolManager._reset_singleton()

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_wait_true_passes_to_both_pools(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        io_pool = tpm._io_pool
        cpu_pool = tpm._cpu_pool
        tpm.shutdown(wait=True)
        assert io_pool._shutdown
        assert cpu_pool._shutdown

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_wait_false_passes_to_both_pools(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        io_pool = tpm._io_pool
        cpu_pool = tpm._cpu_pool
        tpm.shutdown(wait=False)
        assert io_pool._shutdown
        assert cpu_pool._shutdown

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_idempotent(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        tpm.shutdown(wait=False)
        assert tpm._shutdown_done is True


class TestScreenerViewModelFlushNoDeadCode:
    """C-P1-1: _on_ai_result_stream() must not contain dead code that tries
    get_running_loop() inside an except RuntimeError block."""

    def test_flush_from_sync_thread_uses_run_coroutine_threadsafe(self):
        from ui.viewmodels.screener_view_model import ScreenerViewModel

        vm = ScreenerViewModel()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        vm._main_loop = mock_loop
        vm._flush_pending = False
        vm._ai_buffer = []
        vm._last_ai_update = 0
        vm.mode = "REALTIME"
        vm.on_log = MagicMock()

        with patch("ui.viewmodels.screener_view_model.asyncio") as mock_aio:
            mock_aio.get_running_loop.side_effect = RuntimeError("no loop")
            mock_aio.run_coroutine_threadsafe = MagicMock()
            vm._on_ai_result_stream({"name": "test", "ai_score": 80, "thinking": ""})
            mock_aio.run_coroutine_threadsafe.assert_called_once()

    def test_flush_from_async_loop_uses_create_task(self):
        from ui.viewmodels.screener_view_model import ScreenerViewModel

        vm = ScreenerViewModel()
        vm._flush_pending = False
        vm._ai_buffer = []
        vm._last_ai_update = 0
        vm.mode = "REALTIME"
        vm.on_log = MagicMock()

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        with patch("ui.viewmodels.screener_view_model.asyncio") as mock_aio:
            mock_aio.get_running_loop.return_value = mock_loop
            vm._on_ai_result_stream({"name": "test", "ai_score": 80, "thinking": ""})
            mock_loop.create_task.assert_called_once()
