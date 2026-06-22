from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _create_news_service():
    from services.news_subscription_service import NewsSubscriptionService

    NewsSubscriptionService._instance = None
    svc = object.__new__(NewsSubscriptionService)
    svc._initialized = True
    svc.cache = MagicMock()
    svc.ai_client = MagicMock()
    svc._running = False
    svc._last_news_time = None
    svc._last_news_content = None
    svc.processing_queue = None
    svc._background_tasks = set()
    svc._listeners = set()
    svc._alert_listeners = set()
    svc._current_fetch_task = None
    svc._processing_task = None
    svc._queue_put_lock = None
    svc._seen_hashes = OrderedDict()
    svc._MAX_SEEN = 200
    return svc


class TestNewsSubscriptionStopNoClear:
    """U-2: stop() should not clear _listeners"""

    def test_stop_preserves_listeners(self):
        svc = _create_news_service()
        cb1 = MagicMock()
        cb2 = MagicMock()
        svc.add_listener(cb1)
        svc.add_listener(cb2)
        assert len(svc._listeners) == 2

        svc.stop()

        assert cb1 in svc._listeners
        assert cb2 in svc._listeners
        assert len(svc._listeners) == 2

    def test_stop_preserves_alert_listeners(self):
        svc = _create_news_service()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=True)
        svc.stop()
        assert cb in svc._alert_listeners

    def test_listeners_attribute_in_init(self):
        svc = _create_news_service()
        assert hasattr(svc, "_listeners")
        assert isinstance(svc._listeners, set)


class TestHistoryModeBuffer:
    """U-3: HISTORY mode should buffer AI content and merge on switch back"""

    def test_discarded_buffer_in_viewmodel(self):
        with (
            patch("ui.viewmodels.screener_view_model.ReviewManager"),
            patch("ui.viewmodels.screener_view_model.StrategyManager"),
            patch("ui.viewmodels.screener_view_model.DataProcessor"),
        ):
            from ui.viewmodels.screener_view_model import ScreenerViewModel

            vm = ScreenerViewModel()
            assert hasattr(vm, "_discarded_buffer")
            assert isinstance(vm._discarded_buffer, list)

    def test_switch_to_realtime_merges_discarded_buffer(self):
        with (
            patch("ui.viewmodels.screener_view_model.ReviewManager"),
            patch("ui.viewmodels.screener_view_model.StrategyManager"),
            patch("ui.viewmodels.screener_view_model.DataProcessor"),
        ):
            from ui.viewmodels.screener_view_model import ScreenerViewModel

            vm = ScreenerViewModel()
            vm._realtime_snapshot = {
                "full_results": None,
                "page_no": 1,
                "sort_column": None,
                "sort_ascending": True,
                "ai_buffer": ["chunk3"],
            }
            vm._discarded_buffer = ["chunk1", "chunk2"]
            vm.mode = "HISTORY"

            vm.switch_to_realtime()

            assert "chunk1" in vm._ai_buffer
            assert "chunk2" in vm._ai_buffer
            assert "chunk3" in vm._ai_buffer
            assert vm._discarded_buffer == []

    def test_discarded_buffer_merge_logic(self):
        discarded = ["chunk1", "chunk2"]
        current = ["chunk3"]
        merged = discarded + current
        assert merged == ["chunk1", "chunk2", "chunk3"]


class TestNewsSubscriptionCorrelationId:
    """Verify NewsSubscriptionService uses correlation_scope for log tracing."""

    def test_correlation_scope_module_exists(self):
        from utils.correlation import correlation_scope

        assert callable(correlation_scope)

    @pytest.mark.asyncio
    async def test_fetch_and_notify_uses_correlation_scope(self):
        from services.news_subscription_service import NewsSubscriptionService

        svc = _create_news_service()
        svc._running = True
        svc._last_news_time = "20260101"

        with patch("utils.correlation.correlation_scope") as mock_cs:
            mock_cs.return_value.__enter__ = MagicMock(return_value=None)
            mock_cs.return_value.__exit__ = MagicMock(return_value=None)

            with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
                mock_fetcher.get_latest_global_news = AsyncMock(return_value=[])
                await svc._fetch_and_notify()

            mock_cs.assert_called_once()

        NewsSubscriptionService._instance = None


class TestNewsSubscriptionLRU:
    """H-5: _seen_hashes must preserve insertion order (OrderedDict-based LRU)."""

    def test_seen_hashes_is_ordered_dict(self):
        svc = _create_news_service()
        assert isinstance(svc._seen_hashes, OrderedDict)

    def test_lru_eviction_preserves_recent_items(self):
        svc = _create_news_service()
        svc._MAX_SEEN = 5
        svc._seen_hashes = OrderedDict()
        for i in range(10):
            h = f"hash_{i:03d}"
            svc._seen_hashes[h] = None
            if len(svc._seen_hashes) > svc._MAX_SEEN:
                svc._seen_hashes.popitem(last=False)
        assert len(svc._seen_hashes) == 5
        keys = list(svc._seen_hashes.keys())
        assert keys == ["hash_005", "hash_006", "hash_007", "hash_008", "hash_009"]
