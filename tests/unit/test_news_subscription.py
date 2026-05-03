import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from collections import OrderedDict

from data.external.news_subscription import NewsSubscriptionService, NewsUpdateType


@pytest.fixture(autouse=True)
def reset_singleton():
    NewsSubscriptionService._instance = None
    NewsSubscriptionService._initialized = False
    yield
    NewsSubscriptionService._instance = None
    NewsSubscriptionService._initialized = False


class TestNewsUpdateType:
    def test_constants(self):
        assert NewsUpdateType.NEW_ITEM == "new_item"
        assert NewsUpdateType.TAG_UPDATE == "tag_update"
        assert NewsUpdateType.INITIAL == "initial"


class TestNewsSubscriptionServiceInit:
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_init(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        assert svc._running is False
        assert svc._last_news_time is None
        assert svc._last_news_content is None
        assert isinstance(svc._seen_hashes, OrderedDict)
        assert svc._MAX_SEEN == 200
        assert len(svc._listeners) == 0
        assert len(svc._alert_listeners) == 0


class TestNewsSubscriptionServiceListeners:
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_add_normal_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=False)
        assert cb in svc._listeners

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_add_alert_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=True)
        assert cb in svc._alert_listeners

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_remove_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb)
        svc.remove_listener(cb)
        assert cb not in svc._listeners

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_remove_alert_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=True)
        svc.remove_listener(cb, is_alert=True)
        assert cb not in svc._alert_listeners


class TestNewsSubscriptionServiceStop:
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_stop_resets_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc.stop()
        assert svc._running is False

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_stop_clears_last_news(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "2024-06-15"
        svc._last_news_content = "some content"
        svc.stop()
        assert svc._last_news_time is None
        assert svc._last_news_content is None

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_stop_not_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = False
        svc.stop()


class TestNewsSubscriptionServiceStart:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_start_sets_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        with patch("data.external.news_subscription.asyncio") as mock_aio:
            mock_aio.Queue = asyncio.Queue
            mock_aio.Lock = asyncio.Lock
            mock_aio.create_task = MagicMock()
            svc.start()
            assert svc._running is True
            mock_aio.create_task.assert_called()
        svc._running = False

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_start_already_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc.start()
        assert svc.processing_queue is None


class TestNewsSubscriptionServiceSafeQueuePut:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_put_success(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        await svc._safe_queue_put({"content": "test"})

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_put_no_queue(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc.processing_queue = None
        await svc._safe_queue_put({"content": "test"})


class TestNewsSubscriptionServiceNotifyListeners:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_with_update_type(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        cb.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_removes_failing_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock(side_effect=Exception("fail"))
        svc._listeners.add(cb)
        svc._listener_errors = {}
        for _ in range(3):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert cb not in svc._listeners

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_timeout(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()

        async def slow_listener(*args, **kwargs):
            await asyncio.sleep(10)

        cb = slow_listener
        svc._listeners.add(cb)
        with patch("data.external.news_subscription.asyncio.wait_for", side_effect=TimeoutError):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)


class TestNewsSubscriptionServiceFetchAndNotify:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_no_news(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[])
            await svc._fetch_and_notify()

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_initial_sync(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = None
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        mock_cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        svc.cache.save_market_news = AsyncMock()
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(
                return_value=[
                    {"content": "news1", "time": "10:00"},
                    {"content": "news2", "time": "10:01"},
                ]
            )
            svc._notify_listeners = AsyncMock()
            await svc._fetch_and_notify()
            assert svc._last_news_time is not None
            assert svc._last_news_content is not None

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_new_items_found(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old content"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        mock_cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        svc.cache.save_market_news = AsyncMock()
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("data.external.news_subscription.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = False
            mock_fetcher.get_latest_global_news = AsyncMock(
                return_value=[
                    {"content": "new news", "time": "10:05"},
                ]
            )
            svc._notify_listeners = AsyncMock()
            await svc._fetch_and_notify()
            svc._notify_listeners.assert_called()


class TestNewsSubscriptionServiceSeenHashes:
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_initially_empty(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        assert len(svc._seen_hashes) == 0

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_max_seen_200(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        assert svc._MAX_SEEN == 200

    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    def test_eviction(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        for i in range(250):
            svc._seen_hashes[f"hash_{i}"] = None
            if len(svc._seen_hashes) > svc._MAX_SEEN:
                svc._seen_hashes.popitem(last=False)
        assert len(svc._seen_hashes) <= svc._MAX_SEEN


class TestNewsSubscriptionServiceGenerateTags:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.CacheManager")
    @patch("data.external.news_subscription.AIService")
    async def test_ai_tagging_success(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value={"emoji": "[TEST]", "category": "Policy"})
        with patch("ui.i18n.I18n.get", return_value="政策"):
            result = await svc._generate_tags("央行发布新政策")
            assert "政策" in result

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.CacheManager")
    @patch("data.external.news_subscription.AIService")
    async def test_ai_tagging_failure_fallback(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(side_effect=Exception("AI error"))
        with patch("ui.i18n.I18n.get", return_value="政策"):
            result = await svc._generate_tags("央行发布新政策")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.CacheManager")
    @patch("data.external.news_subscription.AIService")
    async def test_rule_based_policy_tag(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("ui.i18n.I18n.get", return_value="政策"):
            result = await svc._generate_tags("央行发布新政策")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.CacheManager")
    @patch("data.external.news_subscription.AIService")
    async def test_rule_based_global_tag(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("ui.i18n.I18n.get", return_value="全球"):
            result = await svc._generate_tags("美联储加息")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.CacheManager")
    @patch("data.external.news_subscription.AIService")
    async def test_rule_based_macro_tag(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("ui.i18n.I18n.get", return_value="宏观"):
            result = await svc._generate_tags("GDP增长超预期")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.CacheManager")
    @patch("data.external.news_subscription.AIService")
    async def test_no_tag_match(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        result = await svc._generate_tags("普通新闻内容")
        assert result == ""


class TestNewsSubscriptionServiceNotifyAdvanced:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_sync_listener_no_params(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        def cb():
            called[0] = True

        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert called[0]

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_async_listener_two_params(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        async def async_cb(ut, data):
            called[0] = True

        svc._listeners.add(async_cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert called[0]

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_sync_listener_one_param(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        def cb(ut):
            called[0] = True

        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert called[0]

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_sync_listener_two_params(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        def cb(ut, d):
            called[0] = True

        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert called[0]

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_custom_listeners(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        custom = {cb}
        await svc._notify_listeners(listeners=custom, update_type=NewsUpdateType.TAG_UPDATE)
        cb.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_notify_empty_target(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        await svc._notify_listeners(listeners=set(), update_type=NewsUpdateType.NEW_ITEM)


class TestNewsSubscriptionServiceSafeFetchTask:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_not_running_returns(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = False
        svc._fetch_and_notify = AsyncMock()
        await svc._safe_fetch_task()
        svc._fetch_and_notify.assert_not_called()

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_exception_handled(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._fetch_and_notify = AsyncMock(side_effect=Exception("network error"))
        await svc._safe_fetch_task()


class TestNewsSubscriptionServiceFetchWithAlerts:
    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_new_item_with_alerts_enabled(self, mock_cache_cls, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        alert_cb = MagicMock()
        svc._alert_listeners.add(alert_cb)
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("data.external.news_subscription.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()
            alert_cb.assert_called()

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_new_item_alert_timeout(self, mock_cache_cls, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})

        async def slow_alert(msg):
            await asyncio.sleep(10)

        svc._alert_listeners.add(slow_alert)
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("data.external.news_subscription.ConfigHandler") as mock_ch,
            patch("data.external.news_subscription.asyncio.wait_for", side_effect=TimeoutError),
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()

    @pytest.mark.asyncio
    @patch("data.external.news_subscription.AIService")
    @patch("data.external.news_subscription.CacheManager")
    async def test_new_item_alert_error(self, mock_cache_cls, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        alert_cb = MagicMock(side_effect=Exception("alert error"))
        svc._alert_listeners.add(alert_cb)
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("data.external.news_subscription.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()
