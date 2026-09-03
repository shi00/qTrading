"""NewsSubscriptionService 单元测试。

覆盖范围:
- 单例生命周期 (__new__ / __init__ / _reset_singleton / _atexit_cleanup)
- 监听器管理 (add_listener / remove_listener，含 alert 通道)
- 队列写入 (_safe_queue_put：正常 / 超时 / 队列满丢弃)
- 优雅停机 (stop_async / stop)
- 启动轮询 (start)
- 轮询循环 (_poll_loop：取消传播)
- 错误包装 (_safe_fetch_task：EngineDisposedError / 通用异常)
- AI 标签 (_generate_tags：AI 成功 / AI 失败回退规则)
- 处理队列 (_processing_loop：空内容 / 正常 / 取消 / EngineDisposed / 通用异常)
- 通知 (_notify_listeners：async/sync/0-2 参数 / 超时 / 连续失败移除)
- 抓取通知 (_fetch_and_notify：空新闻 / 初始同步 / 新条目 / 告警 / 水位线保护)
"""

import asyncio
import functools
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.news_subscription_service import (
    NewsSubscriptionService,
    NewsUpdateType,
)
from data.persistence.daos.base_dao import EngineDisposedError

pytestmark = pytest.mark.unit


@pytest.fixture
def svc():
    """返回一个初始化好的 NewsSubscriptionService 单例（依赖被 mock）。"""
    with (
        patch("services.news_subscription_service.CacheManager") as mock_cm,
        patch("services.news_subscription_service.AIService") as mock_ai,
    ):
        mock_cm.return_value = MagicMock()
        mock_ai.return_value = MagicMock()
        service = NewsSubscriptionService()
        yield service


# ---------------------------------------------------------------------------
# 单例生命周期
# ---------------------------------------------------------------------------


class TestSingletonLifecycle:
    def test_singleton_identity(self):
        with (
            patch("services.news_subscription_service.CacheManager"),
            patch("services.news_subscription_service.AIService"),
        ):
            s1 = NewsSubscriptionService()
            s2 = NewsSubscriptionService()
            assert s1 is s2

    def test_concurrent_init_runs_once(self):
        """CON-01: 并发首次访问时 __init__ 只执行一次初始化（double-checked locking）。

        旧模式"锁在 __new__、初始化在 __init__ 锁外"存在竞态：两线程同时首次访问时，
        后到线程在首个线程完成初始化前也看到 _initialized=False，导致 CacheManager/
        AIService 被重复构造。本测试用事件闩锁强制两线程交错进入初始化段，
        断言依赖构造恰好各一次。
        """
        import threading
        import time

        NewsSubscriptionService._reset_singleton()

        start = threading.Barrier(3)  # 2 workers + main，保证两线程同时开始构造
        entered = threading.Event()  # 首个线程已进入 CacheManager 构造
        release = threading.Event()  # 主线程放行首个线程
        gate_lock = threading.Lock()
        cm_count = {"n": 0}

        real_cm = MagicMock()
        real_ai = MagicMock()

        def slow_cm():
            with gate_lock:
                cm_count["n"] += 1
                if cm_count["n"] == 1:
                    entered.set()
                    release.wait()  # 首个线程在此停车，确保第二个线程也进入 __init__
            return real_cm

        instances = []
        errors = []

        def worker():
            try:
                start.wait()
                instances.append(NewsSubscriptionService())
            except Exception as e:  # pragma: no cover - 仅测试防御
                errors.append(e)

        with (
            patch("services.news_subscription_service.CacheManager", side_effect=slow_cm),
            patch("services.news_subscription_service.AIService", return_value=real_ai),
        ):
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            start.wait()
            entered.wait()
            # 在首个线程完成初始化（_initialized=True）前，给第二个线程足够的调度窗口
            # 到达 _initialized 检查：DCL 下第二线程被锁阻塞（无副作用），旧模式下则进入
            # 初始化段使计数=2 → 断言失败，从而把回归捕获从概率性（~76%）提升到近乎确定。
            time.sleep(0.1)
            release.set()
            t1.join()
            t2.join()

        assert not errors, errors
        assert instances[0] is instances[1]
        assert cm_count["n"] == 1, "并发首次访问时 CacheManager 应只构造一次"

        NewsSubscriptionService._reset_singleton()

    def test_reset_singleton_clears_state(self):
        with (
            patch("services.news_subscription_service.CacheManager"),
            patch("services.news_subscription_service.AIService"),
        ):
            s = NewsSubscriptionService()
            s._listeners.add(lambda: None)
            s._alert_listeners.add(lambda: None)
            s._running = True
            NewsSubscriptionService._reset_singleton()
            assert NewsSubscriptionService._instance is None

    def test_atexit_cleanup_no_instance(self):
        """无实例时 _atexit_cleanup 应安全返回。"""
        NewsSubscriptionService._instance = None
        NewsSubscriptionService._atexit_cleanup()

    def test_atexit_cleanup_cancels_running_tasks(self):
        """有未完成 background task 时应调用 cancel。"""
        with (
            patch("services.news_subscription_service.CacheManager"),
            patch("services.news_subscription_service.AIService"),
        ):
            s = NewsSubscriptionService()
            mock_task = MagicMock()
            mock_task.done.return_value = False
            s._background_tasks = {mock_task}
            NewsSubscriptionService._atexit_cleanup()
            mock_task.cancel.assert_called_once()

    def test_atexit_cleanup_skips_done_tasks(self):
        """已完成 task 不应被 cancel。"""
        with (
            patch("services.news_subscription_service.CacheManager"),
            patch("services.news_subscription_service.AIService"),
        ):
            s = NewsSubscriptionService()
            mock_task = MagicMock()
            mock_task.done.return_value = True
            s._background_tasks = {mock_task}
            NewsSubscriptionService._atexit_cleanup()
            mock_task.cancel.assert_not_called()

    def test_atexit_cleanup_no_background_tasks_attr(self):
        """无 _background_tasks 属性时安全返回。"""
        with (
            patch("services.news_subscription_service.CacheManager"),
            patch("services.news_subscription_service.AIService"),
        ):
            s = NewsSubscriptionService()
            del s._background_tasks
            NewsSubscriptionService._atexit_cleanup()  # 不应抛异常


# ---------------------------------------------------------------------------
# 监听器管理
# ---------------------------------------------------------------------------


def _noop() -> None:
    """No-op callback for listener tests (avoids E731 lambda assignment)."""
    return None


class TestListenerManagement:
    def test_add_normal_listener(self, svc):
        cb = _noop
        svc.add_listener(cb, is_alert=False)
        assert cb in svc._listeners

    def test_add_alert_listener(self, svc):
        cb = _noop
        svc.add_listener(cb, is_alert=True)
        assert cb in svc._alert_listeners
        assert cb not in svc._listeners

    def test_add_none_listener_ignored(self, svc):
        """callback=None 不应崩溃（set.add(None) 可行但无意义）。"""
        svc.add_listener(None, is_alert=False)
        assert None in svc._listeners

    def test_remove_normal_listener(self, svc):
        cb = _noop
        svc.add_listener(cb)
        svc.remove_listener(cb)
        assert cb not in svc._listeners

    def test_remove_alert_listener(self, svc):
        cb = _noop
        svc.add_listener(cb, is_alert=True)
        svc.remove_listener(cb, is_alert=True)
        assert cb not in svc._alert_listeners

    def test_remove_nonexistent_listener_no_error(self, svc):
        """移除不存在的 listener 不应抛 KeyError。"""
        svc.remove_listener(_noop)


# ---------------------------------------------------------------------------
# _safe_queue_put
# ---------------------------------------------------------------------------


class TestSafeQueuePut:
    @pytest.mark.asyncio
    async def test_put_when_queue_none(self, svc):
        """processing_queue 为 None 时安全返回。"""
        svc.processing_queue = None
        await svc._safe_queue_put({"content": "test"})

    @pytest.mark.asyncio
    async def test_put_success(self, svc):
        svc.processing_queue = asyncio.Queue(maxsize=10)
        item = {"content": "test"}
        await svc._safe_queue_put(item)
        assert svc.processing_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_put_timeout_drops_oldest(self, svc):
        """队列满时超时应丢弃最旧条目再写入。"""
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"old": True})

        async def fake_wait_for(coro, timeout):
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError()

        with patch("services.news_subscription_service.asyncio.wait_for", side_effect=fake_wait_for):
            await svc._safe_queue_put({"new": True})

        assert svc.processing_queue.qsize() == 1
        item = svc.processing_queue.get_nowait()
        assert item == {"new": True}

    @pytest.mark.asyncio
    async def test_put_timeout_queue_still_full(self, svc):
        """丢弃后队列仍满（竞态）应跳过。"""
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"old": True})

        async def fake_wait_for(coro, timeout):
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError()

        with (
            patch("services.news_subscription_service.asyncio.wait_for", side_effect=fake_wait_for),
            patch.object(svc.processing_queue, "full", return_value=True),
            patch.object(svc.processing_queue, "get_nowait", side_effect=asyncio.QueueEmpty),
        ):
            await svc._safe_queue_put({"new": True})


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_tasks(self, svc):
        await svc.start()
        assert svc._running is True
        assert svc.processing_queue is not None
        await svc.stop_async()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, svc):
        svc._running = True
        await svc.start()
        # 不应重复创建 processing_task
        assert svc._processing_task is None

    @pytest.mark.asyncio
    async def test_stop_async_clears_state(self, svc):
        await svc.start()
        await svc.stop_async()
        assert svc._running is False
        assert svc._last_news_time is None
        assert svc._last_news_content is None

    @pytest.mark.asyncio
    async def test_stop_async_drain_timeout(self, svc, caplog):
        """processing_queue.join 超时时应记录 warning 而非崩溃。"""
        import logging

        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        # 放入一个永远不会 task_done 的 item
        svc.processing_queue.put_nowait({"content": "stuck"})

        with caplog.at_level(logging.WARNING, logger="services.news_subscription_service"):
            await svc.stop_async(drain_timeout=0.1)
        assert any("drain timeout" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_stop_async_with_running_fetch_task(self, svc):
        """有活跃 fetch task 时应取消并等待。"""
        svc._running = True

        async def slow_fetch():
            await asyncio.sleep(10)

        svc._current_fetch_task = asyncio.create_task(slow_fetch())
        await svc.stop_async(drain_timeout=0.5)
        assert svc._current_fetch_task is None

    @pytest.mark.asyncio
    async def test_stop_async_with_running_processing_task(self, svc):
        """有活跃 processing task 时应取消并等待。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)

        async def slow_loop():
            await asyncio.sleep(10)

        svc._processing_task = asyncio.create_task(slow_loop())
        await svc.stop_async(drain_timeout=0.5)
        assert svc._processing_task is None

    @pytest.mark.asyncio
    async def test_stop_async_external_cancel_propagates(self, svc):
        """R2: stop_async 被外部取消时 CancelledError 必须传播。"""
        svc._running = True
        # 用 stuck 队列让 stop_async 阻塞在 processing_queue.join()
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"content": "stuck"})

        async def _do_stop():
            await svc.stop_async(drain_timeout=5.0)

        task = asyncio.create_task(_do_stop())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    def test_stop_not_running(self, svc):
        svc._running = False
        svc.stop()

    @pytest.mark.asyncio
    async def test_stop_with_running_loop(self, svc):
        """有运行中 loop 时 stop 应调度 stop_async。"""
        svc._running = True
        svc.stop()
        # 让 scheduled task 有机会执行
        await asyncio.sleep(0.1)
        assert svc._running is False

    @pytest.mark.asyncio
    async def test_stop_no_running_loop(self, svc):
        """无运行中 loop 时 stop 应走 except RuntimeError 分支。"""
        svc._running = True
        svc._processing_task = MagicMock()
        svc._processing_task.done.return_value = False

        with patch("services.news_subscription_service.asyncio.get_running_loop", side_effect=RuntimeError):
            svc.stop()

        assert svc._processing_task is None
        assert svc._last_news_time is None


# ---------------------------------------------------------------------------
# _poll_loop / _safe_fetch_task
# ---------------------------------------------------------------------------


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_poll_loop_cancelled(self, svc):
        """R2: _poll_loop 被取消时 CancelledError 必须传播。"""
        with patch("services.news_subscription_service.ConfigHandler.get_config", return_value=0.01):
            svc._running = True
            task = asyncio.create_task(svc._poll_loop())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_poll_loop_not_running_returns(self, svc):
        """_running=False 时 _poll_loop 应立即返回。"""
        svc._running = False
        await svc._poll_loop()


class TestSafeFetchTask:
    @pytest.mark.asyncio
    async def test_not_running_returns(self, svc):
        svc._running = False
        await svc._safe_fetch_task()

    @pytest.mark.asyncio
    async def test_engine_disposed_stops_service(self, svc):
        """EngineDisposedError 应设置 _running=False 停止服务。"""
        svc._running = True
        with patch.object(svc, "_fetch_and_notify", side_effect=EngineDisposedError()):
            await svc._safe_fetch_task()
        assert svc._running is False

    @pytest.mark.asyncio
    async def test_general_exception_does_not_stop(self, svc):
        """通用异常应被捕获但不停止服务。"""
        svc._running = True
        with patch.object(svc, "_fetch_and_notify", side_effect=RuntimeError("boom")):
            await svc._safe_fetch_task()
        assert svc._running is True


# ---------------------------------------------------------------------------
# _generate_tags
# ---------------------------------------------------------------------------


class TestGenerateTags:
    @pytest.mark.asyncio
    async def test_ai_success(self, svc):
        svc.ai_client.classify_news = AsyncMock(return_value={"emoji": "🚀", "category": "Tech"})
        with patch("services.news_subscription_service.I18n.get", return_value="🚀 Tech"):
            tag = await svc._generate_tags("some content")
        assert tag == "🚀 Tech"

    @pytest.mark.asyncio
    async def test_ai_returns_none_falls_back_to_rules(self, svc):
        svc.ai_client.classify_news = AsyncMock(return_value=None)
        with patch("services.news_subscription_service.I18n.get", return_value="🏛️ Policy"):
            tag = await svc._generate_tags("央行发布新政策")
        assert tag == "🏛️ Policy"

    @pytest.mark.asyncio
    async def test_ai_exception_falls_back_to_rules(self, svc):
        svc.ai_client.classify_news = AsyncMock(side_effect=RuntimeError("AI down"))
        with patch("services.news_subscription_service.I18n.get", return_value="🌍 Global"):
            tag = await svc._generate_tags("美联储加息")
        assert tag == "🌍 Global"

    @pytest.mark.asyncio
    async def test_no_keyword_match_returns_empty(self, svc):
        svc.ai_client.classify_news = AsyncMock(side_effect=RuntimeError("AI down"))
        tag = await svc._generate_tags("普通新闻无关键词")
        assert tag == ""

    @pytest.mark.asyncio
    async def test_macro_keyword(self, svc):
        svc.ai_client.classify_news = AsyncMock(side_effect=RuntimeError("AI down"))
        with patch("services.news_subscription_service.I18n.get", return_value="📈 Macro"):
            tag = await svc._generate_tags("GDP增长超预期")
        assert tag == "📈 Macro"

    @pytest.mark.asyncio
    async def test_strips_content(self, svc):
        """content 应被 strip() 后传给 AI。"""
        svc.ai_client.classify_news = AsyncMock(return_value={"emoji": "📊", "category": "Data"})
        with patch("services.news_subscription_service.I18n.get", return_value="📊 Data"):
            await svc._generate_tags("  spaced content  ")
        svc.ai_client.classify_news.assert_called_once_with("spaced content")

    @pytest.mark.asyncio
    async def test_ai_exception_logs_classify_error_code(self, svc, caplog):
        """M9: AI 失败日志应含 classify_error 的结构化 code 字段。"""
        svc.ai_client.classify_news = AsyncMock(side_effect=RuntimeError("connection timeout"))
        with caplog.at_level(logging.WARNING, logger="services.news_subscription_service"):
            await svc._generate_tags("普通新闻无关键词")
        assert any("code=timeout" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _processing_loop
# ---------------------------------------------------------------------------


class TestProcessingLoop:
    @pytest.mark.asyncio
    async def test_empty_content_skipped(self, svc):
        """content 为空时应 task_done 并 continue。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"content": ""})

        with patch.object(svc, "_generate_tags", AsyncMock(return_value="tag")):
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert svc.processing_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_normal_item_processed(self, svc):
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        item = {"content": "test news", "time": "2024-01-01"}
        svc.processing_queue.put_nowait(item.copy())

        with (
            patch.object(svc, "_generate_tags", AsyncMock(return_value="tag")),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
        ):
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert svc.processing_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_engine_disposed_breaks_loop(self, svc):
        """EngineDisposedError 应 break 循环。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"content": "test"})

        with (
            patch.object(svc, "_generate_tags", AsyncMock(side_effect=EngineDisposedError())),
        ):
            await svc._processing_loop()
        assert svc._running is True  # EngineDisposed 只 break，不改 _running

    @pytest.mark.asyncio
    async def test_general_exception_continues_loop(self, svc):
        """通用异常应被捕获，循环继续。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"content": "test"})

        call_count = 0

        async def mock_gen_tags(content):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("processing error")

        with patch.object(svc, "_generate_tags", mock_gen_tags):
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.sleep(0.2)
            svc._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert call_count >= 1

    @pytest.mark.asyncio
    async def test_cancelled_propagates(self, svc):
        """R2: CancelledError 在 _processing_loop 中必须传播。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)

        task = asyncio.create_task(svc._processing_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_content_hash_md5_marks_usedforsecurity_false(self, svc):
        """DAT-07: item 无 content_hash 时，md5 摘要必须带 usedforsecurity=False（防回退，消除弱哈希告警噪声）。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait({"content": "test news"})

        processed = asyncio.Event()
        md5_mock = MagicMock()
        md5_mock.hexdigest.return_value = "a" * 12

        async def fake_generate_tags(content):
            processed.set()
            return "tag"

        with (
            patch("services.news_subscription_service.hashlib.md5", return_value=md5_mock) as md5_spy,
            patch.object(svc, "_generate_tags", fake_generate_tags),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
        ):
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.wait_for(processed.wait(), timeout=2)
            svc._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        md5_spy.assert_any_call(b"test news", usedforsecurity=False)


# ---------------------------------------------------------------------------
# _notify_listeners
# ---------------------------------------------------------------------------


class TestNotifyListeners:
    @pytest.mark.asyncio
    async def test_no_listeners_returns(self, svc):
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={})

    @pytest.mark.asyncio
    async def test_async_listener_no_params(self, svc):
        called = asyncio.Event()

        async def listener():
            called.set()

        svc._listeners.add(listener)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data=None)
        assert called.is_set()

    @pytest.mark.asyncio
    async def test_async_listener_one_param(self, svc):
        received = []

        async def listener(update_type):
            received.append(update_type)

        svc._listeners.add(listener)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data=None)
        assert received == [NewsUpdateType.NEW_ITEM]

    @pytest.mark.asyncio
    async def test_async_listener_two_params(self, svc):
        received = []

        async def listener(update_type, data):
            received.append((update_type, data))

        svc._listeners.add(listener)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert received == [(NewsUpdateType.NEW_ITEM, {"key": "val"})]

    @pytest.mark.asyncio
    async def test_async_listener_awaited_on_event_loop_not_io_thread(self, svc):
        """async 监听必须在事件循环线程被 await，禁止经 ThreadPoolManager 分发到 IO 线程。

        R16 跨线程修复: 若 async 监听被误经 run_async 分发, 变更 Flet observable 状态会在
        IO 线程执行, 触发跨线程 asyncio TypeError。本断言锁定服务端对 async 监听走 await 分支。
        """
        called_on_loop_thread = asyncio.Event()

        async def listener(update_type, data):
            # 记录是否在事件循环线程执行 (asyncio.Event.set 要求运行中的事件循环)
            called_on_loop_thread.set()

        svc._listeners.add(listener)
        with patch("services.news_subscription_service.ThreadPoolManager") as mock_tpm:
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert called_on_loop_thread.is_set(), "async 监听应在事件循环线程被 await"
        assert mock_tpm.mock_calls == [], "async 监听不应经 ThreadPoolManager 分发到 IO 线程"

    @pytest.mark.asyncio
    async def test_sync_listener_two_params(self, svc):
        """同步 listener 应通过 ThreadPoolManager.run_async 提交执行。"""
        called_with = []

        def listener(update_type, data):
            called_with.append((update_type, data))

        svc._listeners.add(listener)
        with patch("services.news_subscription_service.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value=None)
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        # 验证 run_async 被调用且传入了 listener 和参数
        mock_tpm.return_value.run_async.assert_called_once()
        call_args = mock_tpm.return_value.run_async.call_args
        assert call_args.args[1] is listener  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_listener_timeout_logs_warning(self, svc, caplog):
        """超时的 listener 应记录 warning。"""
        import logging

        async def slow_listener():
            await asyncio.sleep(10)

        svc._listeners.add(slow_listener)
        with caplog.at_level(logging.WARNING, logger="services.news_subscription_service"):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data=None)
        assert any("timed out" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_listener_error_count_tracks(self, svc):
        """连续失败 3 次应移除 listener。"""
        counter = 0

        async def failing_listener():
            nonlocal counter
            counter += 1
            raise RuntimeError("fail")

        svc._listeners.add(failing_listener)
        for _ in range(3):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data=None)
        assert failing_listener not in svc._listeners

    @pytest.mark.asyncio
    async def test_listener_error_resets_on_success(self, svc):
        """成功后错误计数应重置。"""
        call_count = 0

        async def flaky_listener():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("fail")

        svc._listeners.add(flaky_listener)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data=None)  # fail
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data=None)  # success
        assert svc._listener_errors.get(flaky_listener, 0) == 0

    @pytest.mark.asyncio
    async def test_partial_wrapped_async_listener(self, svc):
        """partial 包装的 async listener 应被正确 await。"""
        received = []

        async def listener(update_type, data):
            received.append((update_type, data))

        partial_listener = functools.partial(listener)
        svc._listeners.add(partial_listener)
        await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        assert received == [(NewsUpdateType.NEW_ITEM, {"key": "val"})]

    @pytest.mark.asyncio
    async def test_partial_wrapped_sync_listener_thread_pool(self, svc):
        """partial 包装的 sync listener 应走线程池，返回 None 不再抛 TypeError。"""

        def listener(update_type, data):
            return None

        partial_listener = functools.partial(listener)
        svc._listeners.add(partial_listener)
        with patch("services.news_subscription_service.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = AsyncMock(return_value=None)
            # 不应抛 TypeError
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        mock_tpm.return_value.run_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_mislabeled_async_listener_skipped_no_typeerror(self, svc, caplog):
        """sync listener 被误标 is_async=True 时，返回非 awaitable 应记录并跳过，不抛 TypeError 也不重跑。"""
        import logging

        calls = []

        class _SyncListener:
            is_async = True

            def __init__(self, sink):
                self.sink = sink

            def __call__(self, update_type, data):
                self.sink.append((update_type, data))
                return None

        listener = _SyncListener(calls)
        svc._listeners.add(listener)
        with caplog.at_level(logging.ERROR, logger="services.news_subscription_service"):
            await svc._notify_listeners(update_type=NewsUpdateType.NEW_ITEM, data={"key": "val"})
        # 只在 async 分支被调用一次，未重跑
        assert calls == [(NewsUpdateType.NEW_ITEM, {"key": "val"})]
        assert any("non-awaitable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _fetch_and_notify
# ---------------------------------------------------------------------------


class TestFetchAndNotify:
    @pytest.mark.asyncio
    async def test_empty_news_list_returns(self, svc):
        """NewsFetcher 返回空列表时应直接返回。"""
        with patch(
            "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
            AsyncMock(return_value=[]),
        ):
            await svc._fetch_and_notify()
        assert svc._last_news_time is None

    @pytest.mark.asyncio
    async def test_initial_sync_saves_and_notifies(self, svc):
        """初始同步应保存新闻、入队、通知 INIT。"""
        svc._last_news_time = None
        # news_list[0] 为最新（API 返回最新在前）
        news_items = [
            {"content": "news2", "time": "2024-01-01T11:00:00"},
            {"content": "news1", "time": "2024-01-01T10:00:00"},
        ]

        svc.processing_queue = asyncio.Queue(maxsize=10)
        notify_called = asyncio.Event()

        async def mock_notify(*args, **kwargs):
            notify_called.set()

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", mock_notify),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
        ):
            await svc._fetch_and_notify()

        assert notify_called.is_set()
        assert svc._last_news_time == "2024-01-01T11:00:00"

    @pytest.mark.asyncio
    async def test_initial_sync_dedup_by_hash(self, svc):
        """相同 content+time 的新闻不应重复保存。"""
        svc._last_news_time = None
        news_items = [
            {"content": "dup", "time": "2024-01-01T10:00:00"},
            {"content": "dup", "time": "2024-01-01T10:00:00"},
        ]

        svc.processing_queue = asyncio.Queue(maxsize=10)
        save_count = 0

        async def mock_save(*args, **kwargs):
            nonlocal save_count
            save_count += 1

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", mock_save),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
        ):
            await svc._fetch_and_notify()
        assert save_count == 1  # 去重后只保存一次

    @pytest.mark.asyncio
    async def test_initial_sync_water_level_protection(self, svc):
        """初始同步水位线不应倒退。"""
        svc._last_news_time = None
        # news_list[0] 为最新
        news_items = [
            {"content": "new", "time": "2024-01-01T10:00:00"},
            {"content": "old", "time": "2024-01-01T09:00:00"},
        ]

        svc.processing_queue = asyncio.Queue(maxsize=10)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
        ):
            await svc._fetch_and_notify()
        # latest_item = news_items[0]，其 time 不应倒退（若 new_time > last_time 才更新）
        assert svc._last_news_time == "2024-01-01T10:00:00"

    @pytest.mark.asyncio
    async def test_new_items_found_notifies(self, svc):
        """非初始同步发现新条目应通知 NEW_ITEM。"""
        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [
            {"content": "fresh news", "time": "2024-01-01T10:00:00"},
        ]

        svc.processing_queue = asyncio.Queue(maxsize=10)
        notify_args = []

        async def mock_notify(update_type=None, data=None, **kwargs):
            notify_args.append((update_type, data))

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", mock_notify),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=True),
        ):
            await svc._fetch_and_notify()

        assert any(ut == NewsUpdateType.NEW_ITEM for ut, _ in notify_args)

    @pytest.mark.asyncio
    async def test_alert_listener_called(self, svc):
        """enable_news_alerts=True 时应调用 alert listener。"""
        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "alert news", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        alert_called = asyncio.Event()

        async def alert_listener(msg):
            alert_called.set()

        svc._alert_listeners.add(alert_listener)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=True),
        ):
            await svc._fetch_and_notify()
        assert alert_called.is_set()

    @pytest.mark.asyncio
    async def test_alert_async_listener_awaited_on_event_loop_not_io_thread(self, svc):
        """async alert 监听必须在事件循环线程被 await，禁止经 ThreadPoolManager 分发到 IO 线程。

        R16 跨线程修复 (on_news_alert): startup_views.on_news_alert 注册为 is_alert=True 的
        async 监听, 在 _fetch_and_notify 的 alert 分发循环中被 await。若被误经 run_async 分发,
        变更 Flet observable 状态会在 IO 线程执行, 触发跨线程 asyncio TypeError。本断言锁定
        alert 分发路径对 async 监听走 await 分支且不触碰 ThreadPoolManager。
        """
        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "async alert", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        called_on_loop_thread = asyncio.Event()

        async def alert_listener(msg):
            # 记录是否在事件循环线程执行 (asyncio.Event.set 要求运行中的事件循环)
            called_on_loop_thread.set()

        svc._alert_listeners.add(alert_listener)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=True),
            patch("services.news_subscription_service.ThreadPoolManager") as mock_tpm,
        ):
            await svc._fetch_and_notify()
        assert called_on_loop_thread.is_set(), "async alert 监听应在事件循环线程被 await"
        assert mock_tpm.mock_calls == [], "async alert 监听不应经 ThreadPoolManager 分发到 IO 线程"

    @pytest.mark.asyncio
    async def test_alert_disabled(self, svc):
        """enable_news_alerts=False 时不应调用 alert listener。"""
        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "no alert", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        alert_called = []

        async def alert_listener(msg):
            alert_called.append(msg)

        svc._alert_listeners.add(alert_listener)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=False),
        ):
            await svc._fetch_and_notify()
        assert len(alert_called) == 0

    @pytest.mark.asyncio
    async def test_sync_alert_listener(self, svc):
        """同步 alert listener 应通过 ThreadPoolManager 调用。"""
        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "sync alert", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        def sync_alert(msg):
            pass

        svc._alert_listeners.add(sync_alert)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=True),
            patch("services.news_subscription_service.ThreadPoolManager") as mock_tpm,
        ):
            mock_tpm.return_value.run_async = AsyncMock(return_value=None)
            await svc._fetch_and_notify()
        mock_tpm.return_value.run_async.assert_called()

    @pytest.mark.asyncio
    async def test_alert_listener_timeout(self, svc, caplog):
        """alert listener 超时应记录 warning。"""
        import logging

        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "slow alert", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        async def slow_alert(msg):
            await asyncio.sleep(10)

        svc._alert_listeners.add(slow_alert)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=True),
            caplog.at_level(logging.WARNING, logger="services.news_subscription_service"),
        ):
            await svc._fetch_and_notify()
        assert any("timed out" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_alert_listener_error(self, svc, caplog):
        """alert listener 抛异常应记录 error 但不崩溃。"""
        import logging

        svc._last_news_time = "2024-01-01T09:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "err alert", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        async def err_alert(msg):
            raise RuntimeError("alert fail")

        svc._alert_listeners.add(err_alert)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
            patch("services.news_subscription_service.ConfigHandler.get_config", return_value=True),
            caplog.at_level(logging.ERROR, logger="services.news_subscription_service"),
        ):
            await svc._fetch_and_notify()
        assert any("Alert listener error" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_engine_disposed_stops_service(self, svc):
        """EngineDisposedError 应设置 _running=False。"""
        svc._running = True
        with patch(
            "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
            AsyncMock(side_effect=EngineDisposedError()),
        ):
            await svc._fetch_and_notify()
        assert svc._running is False

    @pytest.mark.asyncio
    async def test_general_exception_logged(self, svc, caplog):
        """通用异常应记录 warning。"""
        import logging

        svc._running = True
        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(side_effect=RuntimeError("fetch fail")),
            ),
            caplog.at_level(logging.WARNING, logger="services.news_subscription_service"),
        ):
            await svc._fetch_and_notify()
        assert any("Poll failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_new_items_no_notify(self, svc):
        """无新条目（时间 <= 水位线）时不应通知 NEW_ITEM。"""
        svc._last_news_time = "2024-01-01T10:00:00"
        svc._last_news_content = "old"
        news_items = [{"content": "old news", "time": "2024-01-01T09:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        notify_calls = []

        async def mock_notify(*args, **kwargs):
            notify_calls.append(kwargs.get("update_type"))

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", mock_notify),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
        ):
            await svc._fetch_and_notify()
        assert NewsUpdateType.NEW_ITEM not in notify_calls

    @pytest.mark.asyncio
    async def test_seen_hashes_lru_eviction(self, svc):
        """_seen_hashes 超过 _MAX_SEEN 时应淘汰最旧。"""
        svc._last_news_time = None
        # 预填充至上限
        for i in range(svc._MAX_SEEN):
            svc._seen_hashes[f"hash_{i}"] = None
        assert len(svc._seen_hashes) == svc._MAX_SEEN

        news_items = [{"content": "new", "time": "2024-01-01T10:00:00"}]
        svc.processing_queue = asyncio.Queue(maxsize=10)

        with (
            patch(
                "data.external.news_fetcher.NewsFetcher.get_latest_global_news",
                AsyncMock(return_value=news_items),
            ),
            patch("services.news_subscription_service.CacheManager.normalize_news_item", return_value={}),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()),
            patch.object(svc, "_notify_listeners", AsyncMock()),
            patch.object(svc, "_safe_queue_put", AsyncMock()),
        ):
            await svc._fetch_and_notify()
        assert len(svc._seen_hashes) <= svc._MAX_SEEN
