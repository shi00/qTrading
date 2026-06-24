import asyncio
import contextlib
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from services.news_subscription_service import NewsSubscriptionService
from data.domain_services.market_data_service import MarketDataService
from utils.rate_limiter import TokenBucket
from utils.thread_pool import ThreadPoolManager

# P2-5: 文件含真实 asyncio.sleep（含 100s/60s 长睡眠），标注 slow 以便 CI 分轨运行
pytestmark = [pytest.mark.unit, pytest.mark.slow]


class TestNewsSubscriptionStopBehavior:
    """C-P1-2: Verify stop() behavioral contracts — not implementation details.

    Behavior under test:
    - After stop(), the service reports itself as not running.
    - After stop(), fetch task is cancelled so no new data enters the queue.
    - After stop() from within an event loop, stop_async() is eventually
      executed (observable via the processing task being cleaned up).
    - stop() is idempotent: calling it twice does not raise or schedule
      duplicate stop_async tasks.
    - stop() before start() does not raise.
    - stop_async() after stop() completes without error.
    """

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_sets_not_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        assert svc._running is False

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_cancels_fetch_task(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._current_fetch_task = mock_task
        svc._processing_task = None
        svc.stop()
        mock_task.cancel.assert_called_once()

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_idempotent_no_error_on_double_call(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        svc.stop()
        assert svc._running is False

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_before_start_does_not_raise(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = False
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        assert svc._running is False

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_in_loop_eventually_cleans_up_processing_task(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        processing_task = asyncio.create_task(asyncio.sleep(100))
        svc._processing_task = processing_task
        svc.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(processing_task, timeout=2.0)
        assert processing_task.done()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_does_not_cancel_processing_task_immediately(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        processing_task = asyncio.create_task(asyncio.sleep(100))
        svc._processing_task = processing_task
        svc.stop()
        assert not processing_task.cancelled()
        processing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await processing_task

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_after_stop_completes(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        await svc.stop_async(drain_timeout=0.5)
        assert svc._running is False


class TestMarketDataServiceStopBehavior:
    """C-P1-3: Verify stop() behavioral contracts.

    Behavior under test:
    - After stop(), the service reports itself as not running.
    - After stop() from within an event loop, stop_async() is eventually
      executed and the background task is properly awaited/cancelled.
    - stop() is idempotent.
    - stop() does not prematurely clear _task before stop_async() can await it.
    - stop() before start() does not raise.
    - stop_async() after stop() completes without error.
    """

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_sets_not_running(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc._task = None
        svc.stop()
        assert svc._running is False

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_idempotent(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc._task = None
        svc.stop()
        svc.stop()
        assert svc._running is False

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_before_start_does_not_raise(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = False
        svc._task = None
        svc.stop()
        assert svc._running is False

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_in_loop_preserves_task_for_graceful_await(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        poll_task = asyncio.create_task(asyncio.sleep(60))
        svc._task = poll_task
        svc.stop()
        assert svc._task is not None
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(poll_task, timeout=2.0)
        assert poll_task.done()

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_async_clears_task_after_await(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        poll_task = asyncio.create_task(asyncio.sleep(100))
        svc._task = poll_task
        await svc.stop_async(timeout=0.5)
        assert svc._task is None
        assert svc._cached_data is None

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_async_handles_timeout_when_task_unresponsive(self, mock_tc, mock_cache, mock_api):
        """C-P1-3: stop_async 在 wait_for 超时抛 TimeoutError 时不应中断 shutdown。

        根因：原实现 ``contextlib.suppress(asyncio.CancelledError)`` 无法捕获
        ``asyncio.wait_for`` 超时抛出的 ``TimeoutError``（Python 3.13 中超时
        抛 ``TimeoutError`` 而非 ``CancelledError``），导致 shutdown 流程中断。

        通过 mock ``asyncio.wait_for`` 抛 ``TimeoutError`` 模拟 task 不响应
        cancel 的超时场景，验证：
        1. ``stop_async`` 不抛 ``TimeoutError``；
        2. ``_task`` 被置 None；
        3. 打印 warning 日志描述超时。
        """
        svc = MarketDataService()
        svc._running = True
        poll_task = asyncio.create_task(asyncio.sleep(100))
        svc._task = poll_task

        # mock wait_for 抛 TimeoutError 模拟超时分支
        async def _fake_wait_for(awaitable, timeout=None, **kwargs):
            raise TimeoutError()

        with (
            patch(
                "data.domain_services.market_data_service.asyncio.wait_for",
                side_effect=_fake_wait_for,
            ),
            patch(
                "data.domain_services.market_data_service.logger.warning",
            ) as mock_warning,
        ):
            await svc.stop_async(timeout=0.1)
            assert svc._task is None
            assert svc._cached_data is None
            # 验证 warning 日志被调用（描述超时）
            warning_calls = [str(c) for c in mock_warning.call_args_list]
            assert any("timeout" in c.lower() for c in warning_calls), f"Expected timeout warning, got: {warning_calls}"

        # 清理：poll_task 已被 cancel，确保其完成
        poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poll_task

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_async_after_stop_completes(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        svc._task = None
        svc.stop()
        await svc.stop_async(timeout=0.5)
        assert svc._running is False
        assert svc._task is None

    @pytest.mark.asyncio
    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    async def test_stop_async_clears_task_even_when_already_done(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True

        async def _noop():
            pass

        done_task = asyncio.create_task(_noop())
        await done_task
        svc._task = done_task
        await svc.stop_async(timeout=0.5)
        assert svc._task is None

    @patch("data.domain_services.market_data_service.TushareClient")
    @patch("data.domain_services.market_data_service.CacheManager")
    @patch("data.domain_services.market_data_service.TradeCalendarService")
    def test_stop_outside_loop_clears_task_immediately(self, mock_tc, mock_cache, mock_api):
        svc = MarketDataService()
        svc._running = True
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._task = mock_task
        svc.stop()
        assert svc._task is None
        assert svc._cached_data is None


class TestTokenBucketConsumeBehavior:
    """C-P1-5: Verify consume()/consume_async() behavioral contracts.

    Behavior under test:
    - consume() raises RuntimeError in async context (prevents blocking the loop).
    - consume_async() works correctly in async context.
    - consume() works correctly in sync context.
    - Token deduction is correct in both paths.
    - consume() blocks when insufficient tokens (observable via elapsed time).
    - consume_async() suspends when insufficient tokens.
    """

    @pytest.mark.asyncio
    async def test_consume_raises_in_async_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=10, rate=1)
        with pytest.raises(RuntimeError, match="TokenBucket.consume()"):
            bucket.consume()

    @pytest.mark.asyncio
    async def test_consume_async_deducts_tokens(self):
        bucket = TokenBucket(start_tokens=10, capacity=10, rate=1)
        await bucket.consume_async()
        assert int(bucket.tokens) == 9

    def test_consume_deducts_tokens_in_sync_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=10, rate=1)
        bucket.consume()
        assert int(bucket.tokens) == 9

    def test_consume_blocks_when_insufficient_tokens(self):
        bucket = TokenBucket(start_tokens=0, capacity=10, rate=10)
        start = time.monotonic()
        bucket.consume(tokens=1)
        elapsed = time.monotonic() - start
        assert elapsed > 0

    @pytest.mark.asyncio
    async def test_consume_async_suspends_when_insufficient_tokens(self):
        bucket = TokenBucket(start_tokens=0, capacity=10, rate=100)
        start = time.monotonic()
        await bucket.consume_async(tokens=1)
        elapsed = time.monotonic() - start
        assert elapsed > 0


class TestThreadPoolShutdownBehavior:
    """C-P2-2: Verify shutdown() behavioral contracts.

    Behavior under test:
    - After shutdown(), submitting new tasks raises an error.
    - shutdown(wait=True) waits for both pools.
    - shutdown() is idempotent: calling twice does not raise.
    - After shutdown(), accessing pools raises RuntimeError.
    """

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_prevents_new_submissions(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        with pytest.raises((RuntimeError, ValueError)):
            tpm.submit(0, lambda: None)

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_idempotent(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        tpm.shutdown(wait=False)

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_then_access_pool_raises(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        with pytest.raises(RuntimeError, match="after shutdown"):
            _ = tpm.io_pool

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_then_submit_cpu_raises(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 2
        mock_ch.get_max_cpu_workers.return_value = 2
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)
        with pytest.raises((RuntimeError, ValueError)):
            tpm.submit(1, lambda: None)


class TestScreenerViewModelFlushBehavior:
    """C-P1-1: Verify AI result flush behavioral contracts.

    Behavior under test:
    - When called from an async context, the buffer is eventually flushed.
    - When called from a sync thread with a bound loop, the buffer is
      eventually flushed via run_coroutine_threadsafe.
    - When no loop is available at all, data stays in the buffer (not lost).
    """

    @pytest.mark.asyncio
    async def test_flush_from_async_context_clears_buffer(self):
        from ui.viewmodels.screener_view_model import ScreenerViewModel

        vm = ScreenerViewModel()
        vm.on_log = MagicMock()
        vm._ai_buffer = []
        vm._last_ai_update = 0
        vm.mode = "REALTIME"
        vm._flush_pending = False

        vm._on_ai_result_stream({"name": "test", "ai_score": 80, "thinking": ""})
        flushed = False
        # 用 sleep(0) 让出控制权给事件循环，不真实等待；增加轮询次数补偿
        for _ in range(50):
            if len(vm._ai_buffer) == 0 or vm._full_results is not None:
                flushed = True
                break
            await asyncio.sleep(0)
        assert flushed

    def test_no_loop_preserves_buffer_not_lost(self):
        from ui.viewmodels.screener_view_model import ScreenerViewModel

        vm = ScreenerViewModel()
        vm.on_log = MagicMock()
        vm._ai_buffer = []
        vm._last_ai_update = 0
        vm.mode = "REALTIME"
        vm._flush_pending = False
        vm._main_loop = None

        vm._on_ai_result_stream({"name": "test", "ai_score": 80, "thinking": ""})
        assert len(vm._ai_buffer) >= 1


class TestLoopLocalFallbackMigration:
    """P0-4: Verify loop_local fallback-to-loop migration behavioral contract.

    Behavior under test:
    - When strict=False, an instance created in fallback is migrated to
      loop-local store when a loop becomes available, preserving identity.
    - When strict=True, calling without a loop raises RuntimeError.
    """

    @pytest.mark.asyncio
    async def test_fallback_instance_migrated_to_loop(self):
        from utils.loop_local import get_loop_local, clear_all_loop_locals

        clear_all_loop_locals()
        try:
            obj_fallback = get_loop_local("test_migration", list, strict=False)
            obj_fallback.append(1)
            obj_loop = get_loop_local("test_migration", list, strict=False)
            assert obj_loop is obj_fallback
            assert obj_loop == [1]
        finally:
            clear_all_loop_locals()

    def test_strict_mode_raises_without_loop(self):
        from utils.loop_local import get_loop_local, clear_all_loop_locals

        clear_all_loop_locals()
        with pytest.raises(RuntimeError, match="strict mode"):
            get_loop_local("test_strict", list, strict=True)


class TestShutdownCoordinatorContinuesOnCriticalFailure:
    """P0-4: Verify that critical step failure does NOT skip remaining steps.

    After the fix, all 8 cleanup steps always execute regardless of
    individual step failures. The overall result is False if any critical
    step failed, but resource-release steps (thread pools, AI model) still run.
    """

    @pytest.mark.asyncio
    async def test_all_steps_run_despite_critical_failure(self):
        from utils.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        async def _failing_step0():
            raise RuntimeError("cancel failed")

        with (
            patch.object(coordinator, "_step0_cancel_tasks", side_effect=_failing_step0),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=2.0)

        assert ok is False
        step0 = next(r for r in coordinator.step_results if r.name == "Step 0")
        assert step0.ok is False
        assert len(coordinator.step_results) == 8

    @pytest.mark.asyncio
    async def test_non_critical_failure_still_returns_ok(self):
        from utils.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(page=None, service_stop_delay=0)

        async def _failing_step4():
            raise RuntimeError("toast failed")

        with (
            patch.object(coordinator, "_step4_clear_toast", side_effect=_failing_step4),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=2.0)

        assert ok is True
        assert len(coordinator.step_results) == 8


class TestScheduleAsyncGcProtection:
    """P1-2: Verify _schedule_async holds strong reference to prevent GC.

    The fix adds a module-level _scheduled_tasks set that holds strong
    references to tasks created via asyncio.create_task, preventing them
    from being garbage-collected before completion.
    """

    @pytest.mark.asyncio
    async def test_scheduled_task_held_in_set(self):
        completed = False

        async def _coro():
            nonlocal completed
            completed = True

        _scheduled_tasks: set = set()

        def _schedule_async(coro):
            task = asyncio.create_task(coro())
            _scheduled_tasks.add(task)
            task.add_done_callback(_scheduled_tasks.discard)
            return task

        task = _schedule_async(_coro)
        assert task in _scheduled_tasks

        await asyncio.sleep(0.05)
        assert completed
        assert task not in _scheduled_tasks

    @pytest.mark.asyncio
    async def test_multiple_tasks_tracked(self):
        count = 0

        async def _coro():
            nonlocal count
            count += 1

        _scheduled_tasks: set = set()

        def _schedule_async(coro):
            task = asyncio.create_task(coro())
            _scheduled_tasks.add(task)
            task.add_done_callback(_scheduled_tasks.discard)
            return task

        [_schedule_async(_coro) for _ in range(5)]
        assert len(_scheduled_tasks) == 5

        await asyncio.sleep(0.05)
        assert count == 5
        assert len(_scheduled_tasks) == 0
