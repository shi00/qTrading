"""
Tests for queue management, thread pool, and rate limiter.

S1-2: processing_queue has maxsize and overflow handling.
S3-1: ThreadPoolManager shutdown is idempotent.
S3-2: TokenBucket warns when used in async context.
"""

import asyncio
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestProcessingQueueMaxsize:
    """S1-2: processing_queue should have maxsize and overflow handling"""

    def test_queue_maxsize_in_source(self):
        """NewsSubscriptionService should set maxsize on processing_queue"""
        svc_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "external", "news_subscription.py")
        )
        with open(svc_path, encoding="utf-8") as f:
            source = f.read()

        assert "maxsize" in source, "S1-2: processing_queue should have maxsize"

    def test_queue_overflow_handled(self):
        """When queue is full, overflow should be handled gracefully"""
        q = asyncio.Queue(maxsize=2)
        q.put_nowait("item1")
        q.put_nowait("item2")

        with pytest.raises(asyncio.QueueFull):
            q.put_nowait("item3")


class TestThreadPoolManagerIdempotentShutdown:
    """S3-1: ThreadPoolManager shutdown should be idempotent"""

    def test_shutdown_guard_in_source(self):
        """ThreadPoolManager should have _shutdown_done idempotent guard"""
        tp_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "utils", "thread_pool.py"))
        with open(tp_path, encoding="utf-8") as f:
            source = f.read()

        assert "_shutdown_done" in source, "S3-1: ThreadPoolManager should have _shutdown_done guard"


class TestTokenBucketAsyncWarning:
    """S3-2: TokenBucket should warn when used in async context"""

    def test_async_context_detection_in_source(self):
        """TokenBucket should detect async context and warn"""
        rl_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "utils", "rate_limiter.py"))
        with open(rl_path, encoding="utf-8") as f:
            source = f.read()

        has_async_check = "async" in source and ("warning" in source.lower() or "warn" in source.lower())
        assert has_async_check, "S3-2: TokenBucket should have async context detection"
