"""Tests for queue guard, thread pool, and rate limiter (project implementations).

TP08-T02: 队列守护测试必须针对项目实现（NewsSubscriptionService 的
``_queue_factory`` / loop-local ``processing_queue`` / ``_safe_queue_put``），
而非标准库 ``asyncio.Queue`` 自身语义；生产封装守护失效时测试必须失败。

S1-2: processing_queue has maxsize and overflow handling.
S3-1: ThreadPoolManager shutdown is idempotent and rejects post-shutdown access.
S3-2: TokenBucket warns when used in async context, and enforces capacity /
min-rate boundaries.
"""

import asyncio
from unittest.mock import patch

import pytest

from services import news_subscription_service as news_service_module
from services.news_subscription_service import NewsSubscriptionService
from utils.rate_limiter import TokenBucket
from utils.thread_pool import TaskType, ThreadPoolManager

pytestmark = pytest.mark.unit


@pytest.fixture
def svc():
    """真实 NewsSubscriptionService，仅隔离外部依赖（CacheManager / AIService）。"""
    with (
        patch("services.news_subscription_service.CacheManager"),
        patch("services.news_subscription_service.AIService"),
    ):
        service = NewsSubscriptionService()
        yield service


class TestProcessingQueueGuard:
    """S1-2: 有界 processing_queue 的守护语义（项目封装，非 stdlib 默认行为）"""

    def test_queue_factory_matches_design_maxsize(self):
        queue = NewsSubscriptionService._queue_factory()
        assert isinstance(queue, asyncio.Queue)
        assert queue.maxsize == 500

    def test_inactive_processing_queue_returns_none(self, svc):
        svc.processing_queue = None
        assert svc.processing_queue is None

    def test_processing_queue_is_isolated_per_event_loop(self, svc):
        """CON-07: 未显式注入队列时，processing_queue 按当前事件循环隔离。"""
        svc._custom_queue = None
        svc._queue_active = True

        async def _resolve_queue():
            return svc.processing_queue

        q1 = asyncio.run(_resolve_queue())
        q2 = asyncio.run(_resolve_queue())

        assert q1 is not None
        assert q2 is not None
        assert q1 is not q2
        assert q1.maxsize == 500
        assert q2.maxsize == 500

    def test_project_queue_factory_is_bounded(self):
        queue = NewsSubscriptionService._queue_factory()
        for i in range(queue.maxsize):
            queue.put_nowait(f"item_{i}")
        assert queue.qsize() == 500

        with pytest.raises(asyncio.QueueFull) as exc_info:
            queue.put_nowait("overflow")
        assert exc_info.value.args == ()
        # 溢出被拒绝后队列仍保持设计上限，未发生替换/扩容。
        assert queue.qsize() == 500

    @pytest.mark.asyncio
    async def test_safe_queue_put_inactive_queue_is_noop(self, svc):
        svc.processing_queue = None
        await svc._safe_queue_put({"seq": 0})
        assert svc.processing_queue is None

    @pytest.mark.asyncio
    async def test_safe_queue_put_non_full_queue_preserves_item(self, svc):
        svc.processing_queue = asyncio.Queue(maxsize=2)
        queue = svc.processing_queue
        assert queue is not None

        await svc._safe_queue_put({"seq": 0})

        assert queue.qsize() == 1
        assert queue.get_nowait() == {"seq": 0}
        queue.task_done()

    @pytest.mark.asyncio
    async def test_safe_queue_put_drops_oldest_and_balances_unfinished_tasks(self, svc):
        """队列满时丢弃最旧条目并写入新条目，且 drop 必须配对 task_done。"""
        svc.processing_queue = asyncio.Queue(maxsize=2)
        queue = svc.processing_queue
        assert queue is not None
        queue.put_nowait({"seq": 0})
        queue.put_nowait({"seq": 1})

        # 缩短真实 wait_for 超时，避免单测固定耗时 1s；仍走 _safe_queue_put 真实路径。
        with patch.object(news_service_module, "_QUEUE_PUT_TIMEOUT_S", 0.05):
            await svc._safe_queue_put({"seq": 2})

        assert queue.qsize() == 2
        assert queue.get_nowait() == {"seq": 1}
        assert queue.get_nowait() == {"seq": 2}
        queue.task_done()
        queue.task_done()
        # 若被丢弃的最旧条目未 task_done，unfinished_tasks 残留会使 join 超时。
        await asyncio.wait_for(queue.join(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_safe_queue_put_serializes_concurrent_overflow(self, svc):
        """并发生产者在队列满时不得突破 maxsize，也不得向上抛 QueueFull。"""
        maxsize = 2
        svc.processing_queue = asyncio.Queue(maxsize=maxsize)
        queue = svc.processing_queue
        assert queue is not None

        async def _put(seq):
            await svc._safe_queue_put({"seq": seq})

        with patch.object(news_service_module, "_QUEUE_PUT_TIMEOUT_S", 0.02):
            await asyncio.gather(*(_put(i) for i in range(6)))

        assert queue.maxsize == maxsize
        assert queue.qsize() == maxsize
        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())
            queue.task_done()
        assert len(drained) == maxsize
        assert all(item["seq"] in range(6) for item in drained)
        await asyncio.wait_for(queue.join(), timeout=0.1)


class TestThreadPoolManagerIdempotentShutdown:
    """S3-1: ThreadPoolManager shutdown should be idempotent"""

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_is_idempotent(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        ThreadPoolManager._reset_singleton()
        tpm = ThreadPoolManager()

        tpm.shutdown(wait=False)
        assert tpm._shutdown_event.is_set()

        tpm.shutdown(wait=False)
        assert tpm._shutdown_event.is_set()

        ThreadPoolManager._reset_singleton()

    @patch("utils.thread_pool.ConfigHandler")
    def test_shutdown_guard_prevents_double_shutdown(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        ThreadPoolManager._reset_singleton()
        tpm = ThreadPoolManager()

        tpm.shutdown(wait=False)

        tpm.shutdown(wait=False)
        assert tpm._io_pool is None

        ThreadPoolManager._reset_singleton()


class TestThreadPoolManagerBoundaries:
    """线程池停机后的边界语义（项目守护，非仅通知标志）"""

    @patch("utils.thread_pool.ConfigHandler")
    def test_pool_access_after_shutdown_raises(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        ThreadPoolManager._reset_singleton()
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)

        with pytest.raises(RuntimeError, match="Cannot access io_pool after shutdown"):
            _ = tpm.io_pool
        with pytest.raises(RuntimeError, match="Cannot access cpu_pool after shutdown"):
            _ = tpm.cpu_pool

        ThreadPoolManager._reset_singleton()

    @patch("utils.thread_pool.ConfigHandler")
    def test_reload_config_after_shutdown_is_noop(self, mock_ch):
        mock_ch.get_max_io_workers.return_value = 4
        mock_ch.get_max_cpu_workers.return_value = 2
        ThreadPoolManager._reset_singleton()
        tpm = ThreadPoolManager()
        tpm.shutdown(wait=False)

        tpm.reload_config()

        assert tpm._io_pool is None
        assert tpm._cpu_pool is None
        assert tpm._shutdown_event.is_set()

        ThreadPoolManager._reset_singleton()

    @pytest.mark.asyncio
    async def test_run_async_rejects_coroutine_function(self):
        with patch("utils.thread_pool.ConfigHandler") as mock_ch:
            mock_ch.get_max_io_workers.return_value = 4
            mock_ch.get_max_cpu_workers.return_value = 2
            ThreadPoolManager._reset_singleton()
            tpm = ThreadPoolManager()

            async def _coro():
                return "unexpected"

            with pytest.raises(ValueError, match="Cannot run coroutine function"):
                await tpm.run_async(TaskType.IO, _coro)

            ThreadPoolManager._reset_singleton()


class TestTokenBucketAsyncWarning:
    """S3-2 / C-P1-5: TokenBucket must raise RuntimeError when consume()
    is called from async context (use consume_async instead)."""

    @pytest.mark.asyncio
    async def test_consume_raises_in_async_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)
        with pytest.raises(RuntimeError, match="TokenBucket.consume\\(\\)"):
            bucket.consume(1)

    @pytest.mark.asyncio
    async def test_consume_async_no_error(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=1)
        await bucket.consume_async(1)
        assert int(bucket.tokens) == 9

    def test_consume_no_error_in_sync_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=1)
        bucket.consume(1)
        assert int(bucket.tokens) == 9


class TestTokenBucketBoundaries:
    """限流边界语义（项目实现）：容量钳制、余额不透支、最低速率。"""

    def test_start_tokens_clamped_to_capacity(self):
        bucket = TokenBucket(start_tokens=100, capacity=5, rate=1)
        assert bucket.tokens == 5.0

    def test_tokens_exceeding_capacity_raises_value_error(self):
        bucket = TokenBucket(start_tokens=1, capacity=5, rate=1)
        with pytest.raises(ValueError, match="exceed bucket capacity"):
            bucket.consume(6)
        # 超容量请求在扣减前即拒绝，余额保持不变。
        assert bucket.tokens == 1.0

    def test_insufficient_tokens_are_not_deducted(self):
        bucket = TokenBucket(start_tokens=1, capacity=10, rate=1)
        wait_time = bucket._consume_reserve(5)
        assert wait_time > 0
        # 不足时不得把余额扣成负数（D1-5：要么全额成功要么失败）。
        assert 1.0 <= bucket.tokens < 5.0

    def test_reduce_rate_respects_min_rate(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=10, min_rate=1.0)
        for _ in range(10):
            bucket.reduce_rate(0.5)
        assert bucket.rate == 1.0

    def test_reconfigure_non_positive_values_are_normalized(self):
        bucket = TokenBucket(start_tokens=5, capacity=5, rate=5)
        bucket.reconfigure(rate=0, capacity=0)
        assert bucket.rate == 1.0
        assert bucket.capacity == 1.0
        assert bucket.tokens == 1.0
