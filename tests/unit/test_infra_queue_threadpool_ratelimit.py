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
        assert tpm._shutdown_done is True

        tpm.shutdown(wait=False)
        assert tpm._shutdown_done is True

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
    """S3-2: TokenBucket should warn when used in async context"""

    @pytest.mark.asyncio
    async def test_consume_warns_in_async_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

        with patch("utils.rate_limiter.logger") as mock_logger:
            bucket.consume(1)
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0][0]
            assert "async context" in call_args.lower() or "consume_async" in call_args

    @pytest.mark.asyncio
    async def test_consume_async_no_warning(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

        with patch("utils.rate_limiter.logger") as mock_logger:
            await bucket.consume_async(1)
            warning_calls = [c for c in mock_logger.warning.call_args_list if "async context" in str(c).lower()]
            assert len(warning_calls) == 0

    def test_consume_no_warning_in_sync_context(self):
        bucket = TokenBucket(start_tokens=10, capacity=20, rate=5.0)

        with patch("utils.rate_limiter.logger") as mock_logger:
            bucket.consume(1)
            warning_calls = [c for c in mock_logger.warning.call_args_list if "async context" in str(c).lower()]
            assert len(warning_calls) == 0
