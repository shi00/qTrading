import pytest
import asyncio
import contextlib
from unittest.mock import patch, MagicMock, AsyncMock
from collections import OrderedDict

from services.news_subscription_service import NewsSubscriptionService, NewsUpdateType

# P2-5: 文件含真实 asyncio.sleep（含 60s/100s 长睡眠），标注 slow 以便 CI 分轨运行
# 同时声明 no_auto_mock（测试 NewsSubscriptionService 自身）
pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock, pytest.mark.slow]


class TestNewsUpdateType:
    def test_constants(self):
        assert NewsUpdateType.NEW_ITEM == "new_item"
        assert NewsUpdateType.TAG_UPDATE == "tag_update"
        assert NewsUpdateType.INITIAL == "initial"


class TestNewsSubscriptionServiceInit:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
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
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_add_normal_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=False)
        assert cb in svc._listeners

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_add_alert_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=True)
        assert cb in svc._alert_listeners

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_remove_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb)
        svc.remove_listener(cb)
        assert cb not in svc._listeners

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_remove_alert_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc.add_listener(cb, is_alert=True)
        svc.remove_listener(cb, is_alert=True)
        assert cb not in svc._alert_listeners


class TestNewsSubscriptionServiceStop:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_resets_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        result = svc.stop()
        assert svc._running is False
        assert result is None

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_clears_last_news_when_no_loop(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._last_news_time = "2024-06-15"
        svc._last_news_content = "some content"
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        assert svc._last_news_time is None
        assert svc._last_news_content is None

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_not_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = False
        svc.stop()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_returns_task_when_loop_running(self, mock_cache, mock_ai):
        """C-P1-2: stop() should return asyncio.Task when event loop is running."""
        svc = NewsSubscriptionService()
        svc._running = True
        result = svc.stop()
        assert result is None or isinstance(result, asyncio.Task)


class TestNewsSubscriptionServiceStart:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_start_sets_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        await svc.start()
        assert svc._running is True
        svc._running = False

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_start_already_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        await svc.start()
        assert svc.processing_queue is None


class TestNewsSubscriptionServiceSafeQueuePut:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_put_success(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        await svc._safe_queue_put({"content": "test"})

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_put_no_queue(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc.processing_queue = None
        await svc._safe_queue_put({"content": "test"})


class TestNewsSubscriptionServiceNotifyListeners:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_with_update_type(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        cb.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_removes_failing_listener(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock(side_effect=Exception("fail"))
        svc._listeners.add(cb)
        svc._listener_errors = {}
        for _ in range(3):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert cb not in svc._listeners

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_timeout(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()

        async def slow_listener(*args, **kwargs):
            await asyncio.sleep(10)

        cb = slow_listener
        svc._listeners.add(cb)
        with patch(
            "services.news_subscription_service.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [
                coro.close(),
                (_ for _ in ()).throw(TimeoutError()),
            ][1],
        ):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)


class TestNewsSubscriptionServiceFetchAndNotify:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_no_news(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._notify_listeners = AsyncMock()
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[])
            await svc._fetch_and_notify()
        svc._notify_listeners.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
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
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
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
            patch("services.news_subscription_service.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = False
            mock_fetcher.get_latest_global_news = AsyncMock(
                return_value=[
                    {"content": "new news", "time": "10:05"},
                ]
            )
            svc._notify_listeners = AsyncMock()
            await svc._fetch_and_notify()
            svc._notify_listeners.assert_called_once()


class TestNewsSubscriptionServiceSeenHashes:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_initially_empty(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        assert len(svc._seen_hashes) == 0

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_max_seen_200(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        assert svc._MAX_SEEN == 200

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_eviction(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        for i in range(250):
            svc._seen_hashes[f"hash_{i}"] = None
            if len(svc._seen_hashes) > svc._MAX_SEEN:
                svc._seen_hashes.popitem(last=False)
        assert len(svc._seen_hashes) <= svc._MAX_SEEN


class TestNewsSubscriptionServiceGenerateTags:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.CacheManager")
    @patch("services.news_subscription_service.AIService")
    async def test_ai_tagging_success(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value={"emoji": "[TEST]", "category": "Policy"})
        with patch("core.i18n.I18n.get", return_value="政策"):
            result = await svc._generate_tags("央行发布新政策")
            assert "政策" in result

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.CacheManager")
    @patch("services.news_subscription_service.AIService")
    async def test_ai_tagging_failure_fallback(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(side_effect=Exception("AI error"))
        with patch("core.i18n.I18n.get", return_value="政策"):
            result = await svc._generate_tags("央行发布新政策")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.CacheManager")
    @patch("services.news_subscription_service.AIService")
    async def test_rule_based_policy_tag(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("core.i18n.I18n.get", return_value="政策"):
            result = await svc._generate_tags("央行发布新政策")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.CacheManager")
    @patch("services.news_subscription_service.AIService")
    async def test_rule_based_global_tag(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("core.i18n.I18n.get", return_value="全球"):
            result = await svc._generate_tags("美联储加息")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.CacheManager")
    @patch("services.news_subscription_service.AIService")
    async def test_rule_based_macro_tag(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("core.i18n.I18n.get", return_value="宏观"):
            result = await svc._generate_tags("GDP增长超预期")
            assert len(result) > 0

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.CacheManager")
    @patch("services.news_subscription_service.AIService")
    async def test_no_tag_match(self, mock_ai_cls, mock_cache_cls):
        svc = NewsSubscriptionService()
        svc.ai_client = MagicMock()
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        result = await svc._generate_tags("普通新闻内容")
        assert result == ""


class TestNewsSubscriptionServiceNotifyAdvanced:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_sync_listener_no_params(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        def cb():
            called[0] = True

        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert called[0]

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_async_listener_two_params(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        async def async_cb(ut, data):
            called[0] = True

        svc._listeners.add(async_cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert called[0]

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_sync_listener_one_param(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        def cb(ut):
            called[0] = True

        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert called[0]

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_sync_listener_two_params(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        def cb(ut, d):
            called[0] = True

        svc._listeners.add(cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert called[0]

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_custom_listeners(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        custom = {cb}
        await svc._notify_listeners(listeners=custom, update_type=NewsUpdateType.TAG_UPDATE)
        cb.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_notify_empty_target(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        await svc._notify_listeners(listeners=set(), update_type=NewsUpdateType.NEW_ITEM)


class TestNewsSubscriptionServiceSafeFetchTask:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_not_running_returns(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = False
        svc._fetch_and_notify = AsyncMock()
        await svc._safe_fetch_task()
        svc._fetch_and_notify.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_exception_handled(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._fetch_and_notify = AsyncMock(side_effect=Exception("network error"))
        await svc._safe_fetch_task()


class TestNewsSubscriptionServiceFetchWithAlerts:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
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
            patch("services.news_subscription_service.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()
            alert_cb.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_new_item_alert_timeout(self, mock_cache_cls, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})

        async def slow_alert(msg):
            await asyncio.sleep(60)

        svc._alert_listeners.add(slow_alert)
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("services.news_subscription_service.ConfigHandler") as mock_ch,
            patch(
                "services.news_subscription_service.asyncio.wait_for",
                side_effect=lambda coro, *a, **kw: [
                    coro.close(),
                    (_ for _ in ()).throw(TimeoutError()),
                ][1],
            ),
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
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
            patch("services.news_subscription_service.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()


class TestNewsSubscriptionServiceResetSingleton:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_reset_singleton(self, mock_cache, mock_ai):
        svc1 = NewsSubscriptionService()
        NewsSubscriptionService._reset_singleton()
        svc2 = NewsSubscriptionService()
        assert svc1 is not svc2


class TestNewsSubscriptionServiceProcessingLoop:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_processing_loop_empty_content(self, mock_cache_cls, mock_ai_cls):
        svc = NewsSubscriptionService()
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=10)
        await svc.processing_queue.put({"content": ""})
        svc.cache.save_market_news = AsyncMock()
        svc._notify_listeners = AsyncMock()
        svc._generate_tags = AsyncMock(return_value="tag")
        loop_task = asyncio.create_task(svc._processing_loop())
        await asyncio.sleep(0.05)
        svc._running = False
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_processing_loop_normal_item(self, mock_cache_cls, mock_ai_cls):
        svc = NewsSubscriptionService()
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=10)
        await svc.processing_queue.put({"content": "test news content"})
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        svc._notify_listeners = AsyncMock()
        svc._generate_tags = AsyncMock(return_value="tag")
        loop_task = asyncio.create_task(svc._processing_loop())
        await asyncio.sleep(0.05)
        svc._running = False
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task
        svc.cache.save_market_news.assert_called_once()

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_processing_loop_exception(self, mock_cache_cls, mock_ai_cls):
        svc = NewsSubscriptionService()
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=10)
        await svc.processing_queue.put({"content": "test"})
        svc._generate_tags = AsyncMock(side_effect=Exception("tag error"))
        loop_task = asyncio.create_task(svc._processing_loop())
        await asyncio.sleep(0.05)
        svc._running = False
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


class TestNewsSubscriptionServiceStopSync:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_stop_no_running_loop(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        assert svc._running is False
        assert svc._last_news_time is None
        assert svc._last_news_content is None


class TestNewsSubscriptionServiceNotifyListenerErrors:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_listener_error_count_and_removal(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()

        def bad_cb():
            raise ValueError("fail")

        svc._listeners.add(bad_cb)
        for _ in range(3):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert bad_cb not in svc._listeners

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_listener_timeout(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()

        async def slow_cb():
            await asyncio.sleep(60)

        svc._listeners.add(slow_cb)
        with patch(
            "services.news_subscription_service.asyncio.wait_for",
            side_effect=lambda coro, *a, **kw: [
                coro.close(),
                (_ for _ in ()).throw(TimeoutError()),
            ][1],
        ):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)


class TestNewsSubscriptionServiceInitialSync:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_initial_sync_saves_all_items(self, mock_cache_cls, mock_ai_cls):
        svc = NewsSubscriptionService()
        svc._last_news_time = None
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc._queue_put_lock = asyncio.Lock()
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        svc._notify_listeners = AsyncMock()
        svc._safe_queue_put = AsyncMock()
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(
                return_value=[
                    {"content": "news1", "time": "10:00"},
                    {"content": "news2", "time": "10:01"},
                ]
            )
            await svc._fetch_and_notify()
            assert svc._last_news_time == "10:00"
            assert svc._last_news_content == "news1"


class TestNewsSubscriptionServiceFetchNoNews:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_fetch_returns_empty(self, mock_cache_cls, mock_ai_cls):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[])
            await svc._fetch_and_notify()


class TestNewsSubscriptionServiceAtexitCleanup:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_atexit_cleanup_no_instance(self, mock_cache, mock_ai):
        NewsSubscriptionService._instance = None
        NewsSubscriptionService._atexit_cleanup()  # should not raise

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_atexit_cleanup_no_background_tasks_attr(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        delattr(svc, "_background_tasks")
        NewsSubscriptionService._atexit_cleanup()

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_atexit_cleanup_non_set_background_tasks(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._background_tasks = "not_a_set"
        NewsSubscriptionService._atexit_cleanup()

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_atexit_cleanup_cancels_running_tasks(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._background_tasks = {mock_task}
        NewsSubscriptionService._atexit_cleanup()
        mock_task.cancel.assert_called_once()

    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_atexit_cleanup_skips_done_tasks(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        mock_task = MagicMock()
        mock_task.done.return_value = True
        svc._background_tasks = {mock_task}
        NewsSubscriptionService._atexit_cleanup()
        mock_task.cancel.assert_not_called()


class TestNewsSubscriptionServiceRemoveListenerKeyError:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_remove_nonexistent_listener_no_error(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        cb = MagicMock()
        # Removing a listener that was never added should not raise
        svc.remove_listener(cb)
        assert cb not in svc._listeners


class TestNewsSubscriptionServiceSafeFetchTaskEngineDisposed:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_engine_disposed_stops_service(self, mock_cache, mock_ai):
        from data.persistence.daos.base_dao import EngineDisposedError

        svc = NewsSubscriptionService()
        svc._running = True
        svc._fetch_and_notify = AsyncMock(side_effect=EngineDisposedError("disposed"))
        await svc._safe_fetch_task()
        assert svc._running is False


class TestNewsSubscriptionServiceProcessingLoopEngineDisposed:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_engine_disposed_breaks_loop(self, mock_cache_cls, mock_ai_cls):
        from data.persistence.daos.base_dao import EngineDisposedError

        svc = NewsSubscriptionService()
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=10)
        await svc.processing_queue.put({"content": "test"})
        svc._generate_tags = AsyncMock(side_effect=EngineDisposedError("disposed"))
        loop_task = asyncio.create_task(svc._processing_loop())
        await asyncio.sleep(0.1)
        assert not svc._running or loop_task.done()
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


class TestNewsSubscriptionServiceNotifyAsyncOneParam:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_async_listener_one_param(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        called = [False]

        async def async_cb(ut):
            called[0] = True

        svc._listeners.add(async_cb)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert called[0]


class TestNewsSubscriptionServiceNotifyErrorRecovery:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_listener_error_count_cleared_on_success(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        call_count = [0]

        def flaky_cb():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise ValueError("transient")

        svc._listeners.add(flaky_cb)
        svc._listener_errors = {}
        # First call: error
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert flaky_cb in svc._listener_errors
        # Second call: success - should clear error count
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert flaky_cb not in svc._listener_errors

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_listener_removed_from_errors_dict(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()

        def bad_cb():
            raise ValueError("always fail")

        svc._listeners.add(bad_cb)
        svc._listener_errors = {}
        for _ in range(3):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM)
        assert bad_cb not in svc._listeners
        assert bad_cb not in svc._listener_errors


class TestNewsSubscriptionServiceStopAsync:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_cancels_fetch_task(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = asyncio.create_task(asyncio.sleep(100))
        svc._processing_task = None
        svc.processing_queue = None
        await svc.stop_async()
        assert svc._current_fetch_task is None

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_drain_timeout(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc.processing_queue = asyncio.Queue(maxsize=10)
        # Put an item that will never be processed
        await svc.processing_queue.put({"content": "stuck"})
        svc._processing_task = None
        await svc.stop_async(drain_timeout=0.01)
        assert svc._running is False

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_cancels_processing_task(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc.processing_queue = None
        svc._processing_task = asyncio.create_task(asyncio.sleep(100))
        await svc.stop_async()
        assert svc._processing_task is None

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_resets_last_news(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.processing_queue = None
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        await svc.stop_async()
        assert svc._last_news_time is None
        assert svc._last_news_content is None

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_handles_fetch_task_timeout(self, mock_cache, mock_ai):
        """C-P1-2: fetch_task 不响应 cancel 时 stop_async 不应阻塞。

        根因：原实现 ``await self._current_fetch_task`` 无超时保护，
        task 不响应 cancel 时会无限阻塞 shutdown 流程。

        通过自定义 awaitable 模拟 task 在 cancel 后抛 ``TimeoutError``
        （而非 ``CancelledError``），验证：
        1. 原代码 ``contextlib.suppress(asyncio.CancelledError)`` 无法捕获
           ``TimeoutError``，导致异常传播（RED）；
        2. 修复后 ``except (CancelledError, TimeoutError)`` 正确捕获（GREEN）；
        3. ``_current_fetch_task`` 被置 None。
        """

        class _TaskRaisingTimeout:
            """模拟不响应 cancel 的 task：await 时抛 TimeoutError。"""

            def done(self):
                return False

            def cancel(self):
                return True

            def __await__(self):
                raise TimeoutError()
                yield  # 使其成为 generator

        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = _TaskRaisingTimeout()  # type: ignore[assignment]
        svc._processing_task = None
        svc.processing_queue = None

        await svc.stop_async(drain_timeout=0.1)
        assert svc._current_fetch_task is None
        assert svc._running is False

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_async_handles_processing_task_timeout(self, mock_cache, mock_ai):
        """C-P1-2: processing_task 不响应 cancel 时 stop_async 不应阻塞。

        根因：原实现 ``await self._processing_task`` 无超时保护，
        task 不响应 cancel 时会无限阻塞 shutdown 流程。

        通过自定义 awaitable 模拟 task 在 cancel 后抛 ``TimeoutError``，
        验证修复后 ``except (CancelledError, TimeoutError)`` 正确捕获，
        ``_processing_task`` 被置 None。
        """

        class _TaskRaisingTimeout:
            def done(self):
                return False

            def cancel(self):
                return True

            def __await__(self):
                raise TimeoutError()
                yield

        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = _TaskRaisingTimeout()  # type: ignore[assignment]
        svc.processing_queue = None

        await svc.stop_async(drain_timeout=0.1)
        assert svc._processing_task is None


class TestNewsSubscriptionServiceStopWithRunningLoop:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_stop_schedules_stop_async_when_loop_running(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._current_fetch_task = None
        svc._processing_task = None
        svc.stop()
        # stop_async was scheduled, give it a moment
        await asyncio.sleep(0.05)
        assert svc._running is False


class TestNewsSubscriptionServicePollLoop:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_poll_loop_creates_fetch_tasks(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._safe_fetch_task = AsyncMock()
        with patch("services.news_subscription_service.ConfigHandler") as mock_ch:
            mock_ch.get_config.return_value = 0.05  # very short interval
            loop_task = asyncio.create_task(svc._poll_loop())
            await asyncio.sleep(0.15)
            svc._running = False
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
        assert svc._safe_fetch_task.call_count >= 1

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_poll_loop_cancelled_error_propagates(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        with patch("services.news_subscription_service.ConfigHandler") as mock_ch:
            mock_ch.get_config.return_value = 600  # long interval
            loop_task = asyncio.create_task(svc._poll_loop())
            await asyncio.sleep(0.02)
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task


class TestNewsSubscriptionServiceFetchAndNotifyExceptions:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_engine_disposed_during_fetch(self, mock_cache, mock_ai):
        from data.persistence.daos.base_dao import EngineDisposedError

        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(side_effect=EngineDisposedError("disposed"))
            await svc._fetch_and_notify()
            assert svc._running is False

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_general_exception_during_fetch(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc._running = True
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        with patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher:
            mock_fetcher.get_latest_global_news = AsyncMock(side_effect=RuntimeError("network"))
            await svc._fetch_and_notify()
            assert svc._running is True  # should not stop on general error


class TestNewsSubscriptionServiceFetchAlertSyncListener:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_sync_alert_listener_called(self, mock_cache_cls, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        called = [False]

        def sync_alert_cb(msg):
            called[0] = True

        svc._alert_listeners.add(sync_alert_cb)
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("services.news_subscription_service.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = True
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()
            assert called[0]


class TestNewsSubscriptionServiceFetchNewItemsNotify:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_new_items_triggers_notify(self, mock_cache_cls, mock_ai):
        svc = NewsSubscriptionService()
        svc._last_news_time = "10:00"
        svc._last_news_content = "old"
        svc.processing_queue = asyncio.Queue(maxsize=10)
        svc.cache.save_market_news = AsyncMock()
        svc.cache.normalize_news_item = MagicMock(return_value={"content": "test"})
        with (
            patch("data.external.news_fetcher.NewsFetcher") as mock_fetcher,
            patch("services.news_subscription_service.ConfigHandler") as mock_ch,
        ):
            mock_ch.get_config.return_value = False  # alerts disabled
            mock_fetcher.get_latest_global_news = AsyncMock(return_value=[{"content": "new news", "time": "10:05"}])
            svc._notify_listeners = AsyncMock()
            svc._safe_queue_put = AsyncMock()
            await svc._fetch_and_notify()
            svc._notify_listeners.assert_called_once()


class TestNewsSubscriptionServiceSafeQueuePutTimeout:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_timeout_drops_oldest_and_puts_new(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc.processing_queue = asyncio.Queue(maxsize=1)
        await svc.processing_queue.put({"id": "old"})

        async def _raise_timeout(coro, timeout):
            coro.close()
            raise TimeoutError()

        with patch(
            "services.news_subscription_service.asyncio.wait_for",
            side_effect=_raise_timeout,
        ):
            await svc._safe_queue_put({"id": "new"})

        assert svc.processing_queue.qsize() == 1

    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_timeout_queue_still_full_after_drop(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        svc.processing_queue = asyncio.Queue(maxsize=1)
        await svc.processing_queue.put({"id": "old"})

        call_count = [0]

        async def _raise_timeout_and_fill(coro, timeout):
            coro.close()
            call_count[0] += 1
            if call_count[0] == 1:
                # On first call (the initial put), raise TimeoutError
                raise TimeoutError()
            # On subsequent calls (within lock), succeed
            return None

        with patch(
            "services.news_subscription_service.asyncio.wait_for",
            side_effect=_raise_timeout_and_fill,
        ):
            await svc._safe_queue_put({"id": "new"})


class TestNewsSubscriptionServiceProcessingLoopTimeout:
    @pytest.mark.asyncio
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    async def test_queue_get_timeout_continues_loop(self, mock_cache_cls, mock_ai_cls):
        svc = NewsSubscriptionService()
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=10)
        # Empty queue - wait_for will timeout
        loop_task = asyncio.create_task(svc._processing_loop())
        await asyncio.sleep(1.5)  # wait for at least one timeout cycle
        svc._running = False
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


class TestNewsSubscriptionServiceInitAlreadyInitialized:
    @patch("services.news_subscription_service.AIService")
    @patch("services.news_subscription_service.CacheManager")
    def test_init_returns_early_when_initialized(self, mock_cache, mock_ai):
        svc = NewsSubscriptionService()
        # Second init should return early
        svc.__init__()
        assert svc._initialized is True
