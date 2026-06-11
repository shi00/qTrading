import asyncio
from unittest.mock import patch

import pytest

from services.news_subscription_service import NewsSubscriptionService


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure singleton state is clean before and after each test."""
    NewsSubscriptionService._reset_singleton()
    yield
    NewsSubscriptionService._reset_singleton()


@pytest.mark.asyncio
async def test_safe_queue_put_timeout_path_drops_oldest_and_keeps_newest():
    """N-1: timeout fallback should drop oldest item and keep latest item safely."""
    # Use object.__new__ to create a bare instance without polluting cls._instance
    svc = object.__new__(NewsSubscriptionService)
    svc.processing_queue = asyncio.Queue(maxsize=1)
    svc._queue_put_lock = asyncio.Lock()
    svc._running = True

    await svc.processing_queue.put({"id": "old"})

    async def _raise_timeout(coro, timeout):  # noqa: ANN001
        coro.close()
        raise TimeoutError()

    with patch("services.news_subscription_service.asyncio.wait_for", side_effect=_raise_timeout):
        await asyncio.gather(
            svc._safe_queue_put({"id": "new1"}),
            svc._safe_queue_put({"id": "new2"}),
        )

    assert svc.processing_queue.qsize() == 1
    queued = await svc.processing_queue.get()
    assert queued["id"] in {"new1", "new2"}


@pytest.mark.asyncio
async def test_stop_async_drains_queue_before_cancel_processing():
    """N-6: graceful stop should wait queue drain before cancelling processing task."""
    # Use object.__new__ to create a bare instance without polluting cls._instance
    svc = object.__new__(NewsSubscriptionService)
    svc._running = True
    svc._current_fetch_task = None
    svc._last_news_time = "20260430"
    svc._last_news_content = "x"
    svc.processing_queue = asyncio.Queue(maxsize=10)
    svc._queue_put_lock = asyncio.Lock()

    await svc.processing_queue.put({"id": "item1"})

    async def _fake_processing():
        while svc._running or not svc.processing_queue.empty():
            try:
                item = await asyncio.wait_for(svc.processing_queue.get(), timeout=0.05)
            except TimeoutError:
                continue
            await asyncio.sleep(0.01)
            svc.processing_queue.task_done()
            _ = item

    svc._processing_task = asyncio.create_task(_fake_processing())

    await svc.stop_async(drain_timeout=1.0)

    assert svc._running is False
    assert svc._processing_task is None
    assert svc.processing_queue.qsize() == 0
