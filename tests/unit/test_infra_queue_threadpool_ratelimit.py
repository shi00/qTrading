"""
Tests for queue management, thread pool, and rate limiter.

S1-2: processing_queue has maxsize and overflow handling.
S3-1: ThreadPoolManager shutdown is idempotent.
S3-2: TokenBucket warns when used in async context.
"""

import asyncio
from unittest.mock import patch

import pytest

from utils.rate_limiter import TokenBucket
from utils.thread_pool import ThreadPoolManager


class TestProcessingQueueMaxsize:
    """S1-2: processing_queue should have maxsize and overflow handling"""

    def test_queue_maxsize_matches_design(self):
        queue = asyncio.Queue(maxsize=500)
        assert queue.maxsize == 500
        assert queue.maxsize > 0

    def test_queue_overflow_handled(self):
        q = asyncio.Queue(maxsize=2)
        q.put_nowait("item1")
        q.put_nowait("item2")

        with pytest.raises(asyncio.QueueFull):
            q.put_nowait("item3")

    def test_queue_default_is_unbounded(self):
        q = asyncio.Queue()
        assert q.maxsize == 0

    def test_bounded_queue_prevents_unbounded_growth(self):
        maxsize = 500
        q = asyncio.Queue(maxsize=maxsize)
        for i in range(maxsize):
            q.put_nowait(f"item_{i}")
        assert q.qsize() == maxsize

        with pytest.raises(asyncio.QueueFull):
            q.put_nowait("overflow")


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
