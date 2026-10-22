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
- 情感打分 (_score_sentiment：label 映射三例 / 服务不可用 / NaN / 未知 label → None，R21)
- 处理队列 (_processing_loop：空内容 / 正常 / 取消 / EngineDisposed / 通用异常 / sentiment 落库贯通)
- 通知 (_notify_listeners：async/sync/0-2 参数 / 超时 / 连续失败移除)
- 抓取通知 (_fetch_and_notify：空新闻 / 初始同步 / 新条目 / 告警 / 水位线保护)
"""

import asyncio
import functools
import hashlib
import logging
import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from services.news_subscription_service import (
    NewsSubscriptionService,
    NewsUpdateType,
    _build_analysis_metadata,
    _coerce_llm_sentiment,
)
from data.cache.cache_manager import CacheManager as RealCacheManager
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

    @pytest.mark.asyncio
    async def test_explicit_none_skips_ai_call(self, svc):
        """S4（s4_plan §2.2）：显式 ai_result=None =「已调用无结果」→ 不再内部调用，规则回退。"""
        svc.ai_client.classify_news = AsyncMock(return_value={"emoji": "🚀", "category": "Tech"})
        with patch("services.news_subscription_service.I18n.get", return_value="🏛️ Policy"):
            tag = await svc._generate_tags("央行发布新政策", ai_result=None)
        svc.ai_client.classify_news.assert_not_called()
        assert tag == "🏛️ Policy"

    @pytest.mark.asyncio
    async def test_unset_fetches_result_once(self, svc):
        """S4：不传 ai_result（哨兵）→ 内部单次调用 classify_news（尚未调用语义保留）。"""
        svc.ai_client.classify_news = AsyncMock(return_value={"emoji": "🚀", "category": "Tech"})
        with patch("services.news_subscription_service.I18n.get", return_value="🚀 Tech"):
            tag = await svc._generate_tags("some content")
        svc.ai_client.classify_news.assert_called_once_with("some content")
        assert tag == "🚀 Tech"


# ---------------------------------------------------------------------------
# _coerce_llm_sentiment (N3-6)
# ---------------------------------------------------------------------------


class TestCoerceLlmSentiment:
    """LLM sentiment 白名单过滤：严格大小写 + 失败标记拒绝（提案 §9.3 / Q7）。"""

    def test_valid_codes_pass_through(self):
        assert _coerce_llm_sentiment({"sentiment": "Positive"}) == "Positive"
        assert _coerce_llm_sentiment({"sentiment": "Neutral"}) == "Neutral"
        assert _coerce_llm_sentiment({"sentiment": "Negative"}) == "Negative"

    def test_lowercase_neutral_rejected(self):
        """classify_news 全供应商失败输出小写 "neutral" —— 存量伪装路径不得沿用。"""
        assert _coerce_llm_sentiment({"category": "unknown", "sentiment": "neutral", "error": "x"}) is None

    def test_error_key_rejected(self):
        assert _coerce_llm_sentiment({"sentiment": "Positive", "error": "x"}) is None

    def test_category_unknown_rejected(self):
        assert _coerce_llm_sentiment({"category": "unknown", "sentiment": "Positive"}) is None

    def test_none_and_empty_rejected(self):
        assert _coerce_llm_sentiment(None) is None
        assert _coerce_llm_sentiment({}) is None

    def test_unknown_label_rejected(self):
        assert _coerce_llm_sentiment({"sentiment": "看涨"}) is None
        assert _coerce_llm_sentiment({"sentiment": "positive"}) is None

    def test_missing_sentiment_key_rejected(self):
        assert _coerce_llm_sentiment({"emoji": "🚀", "category": "Tech"}) is None


# ---------------------------------------------------------------------------
# _score_sentiment (N3-5)
# ---------------------------------------------------------------------------


class TestScoreSentiment:
    """打分器 label → 入库 code 映射与 R21 缺失语义。"""

    @staticmethod
    @contextmanager
    def _patched_scorer(results):
        """patch 单例 get_instance + score_texts 返回固定 results。"""
        with patch("services.news_subscription_service.NewsSentimentScorer") as mock_cls:
            mock_cls.get_instance = AsyncMock(return_value=MagicMock(score_texts=AsyncMock(return_value=results)))
            yield

    @pytest.mark.asyncio
    async def test_bullish_maps_positive(self, svc):
        with self._patched_scorer([{"label": "利好", "confidence": 0.9, "score": 0.8}]):
            assert await svc._score_sentiment("央行降准") == "Positive"

    @pytest.mark.asyncio
    async def test_neutral_maps_neutral(self, svc):
        with self._patched_scorer([{"label": "中性", "confidence": 0.6, "score": 0.0}]):
            assert await svc._score_sentiment("常规消息") == "Neutral"

    @pytest.mark.asyncio
    async def test_bearish_maps_negative(self, svc):
        with self._patched_scorer([{"label": "利空", "confidence": 0.85, "score": -0.7}]):
            assert await svc._score_sentiment("立案调查") == "Negative"

    @pytest.mark.asyncio
    async def test_service_unavailable_returns_none(self, svc):
        """R21：打分服务整体不可用（未启用/加载失败等）→ None，不回填中性。"""
        with self._patched_scorer(None):
            assert await svc._score_sentiment("内容") is None

    @pytest.mark.asyncio
    async def test_nan_entry_returns_none(self, svc):
        """R21：条目非有限（NaN/Inf）→ None，不回填中性。"""
        with self._patched_scorer([None]):
            assert await svc._score_sentiment("内容") is None

    @pytest.mark.asyncio
    async def test_empty_results_returns_none(self, svc):
        with self._patched_scorer([]):
            assert await svc._score_sentiment("内容") is None

    @pytest.mark.asyncio
    async def test_unknown_label_returns_none(self, svc):
        """契约外 label 按缺失处理（None），不伪装为 Neutral 合法值。"""
        with self._patched_scorer([{"label": "无法判断", "confidence": 0.5, "score": 0.0}]):
            assert await svc._score_sentiment("内容") is None

    # --- N3-6: 服务不可用 → LLM 降级回退（提案 §9.2）---

    @pytest.mark.asyncio
    async def test_service_unavailable_falls_back_to_llm(self, svc):
        """打分服务整体不可用 → 回退 llm_fallback（经白名单过滤后的 LLM sentiment）。"""
        with self._patched_scorer(None):
            assert await svc._score_sentiment("内容", llm_fallback="Positive") == "Positive"

    @pytest.mark.asyncio
    async def test_service_unavailable_no_fallback_returns_none(self, svc):
        """无 LLM 结果（fallback=None）→ 维持 N3-5 语义缺失即 None。"""
        with self._patched_scorer(None):
            assert await svc._score_sentiment("内容", llm_fallback=None) is None

    @pytest.mark.asyncio
    async def test_nan_entry_ignores_fallback(self, svc):
        """模型可用但条目 NaN → 不回退（输出无效 ≠ 服务不可用，R21）。"""
        with self._patched_scorer([None]):
            assert await svc._score_sentiment("内容", llm_fallback="Positive") is None

    @pytest.mark.asyncio
    async def test_unknown_label_ignores_fallback(self, svc):
        """模型可用但 label 未知 → 不回退。"""
        with self._patched_scorer([{"label": "无法判断", "confidence": 0.5, "score": 0.0}]):
            assert await svc._score_sentiment("内容", llm_fallback="Positive") is None

    @pytest.mark.asyncio
    async def test_scored_result_wins_over_fallback(self, svc):
        """打分成功时优先打分结果，fallback 不生效。"""
        with self._patched_scorer([{"label": "利空", "confidence": 0.8, "score": -0.5}]):
            assert await svc._score_sentiment("内容", llm_fallback="Positive") == "Negative"


# ---------------------------------------------------------------------------
# _score_sentiment_result (N5-2：完整落库载荷)
# ---------------------------------------------------------------------------


class TestScoreSentimentResult:
    """打分 → 完整载荷（sentiment + score/conf/model 三数值列）的降级语义。"""

    @staticmethod
    @contextmanager
    def _patched_scorer(results, model_tag=None):
        with patch("services.news_subscription_service.NewsSentimentScorer") as mock_cls:
            mock_cls.get_instance = AsyncMock(
                return_value=MagicMock(score_texts=AsyncMock(return_value=results), model_tag=model_tag)
            )
            yield

    @pytest.mark.asyncio
    async def test_full_payload_on_success(self, svc):
        with self._patched_scorer(
            [{"label": "利好", "confidence": 0.9, "score": 0.8}],
            model_tag="finbert2-news-q8_0",
        ):
            payload = await svc._score_sentiment_result("央行降准")
        assert payload == {
            "sentiment": "Positive",
            "sentiment_score": 0.8,
            "sentiment_conf": 0.9,
            "sentiment_model": "finbert2-news-q8_0",
        }

    @pytest.mark.asyncio
    async def test_unavailable_falls_back_llm_only(self, svc):
        """服务整体不可用 → sentiment 取 LLM 白名单结果，三数值列 None（R21 不填 0）。"""
        with self._patched_scorer(None):
            payload = await svc._score_sentiment_result("内容", llm_fallback="Positive")
        assert payload == {
            "sentiment": "Positive",
            "sentiment_score": None,
            "sentiment_conf": None,
            "sentiment_model": None,
        }

    @pytest.mark.asyncio
    async def test_invalid_entry_all_none(self, svc):
        """条目 NaN → 四键全 None（不回退，R21）。"""
        with self._patched_scorer([None]):
            payload = await svc._score_sentiment_result("内容", llm_fallback="Positive")
        assert payload == {
            "sentiment": None,
            "sentiment_score": None,
            "sentiment_conf": None,
            "sentiment_model": None,
        }

    @pytest.mark.asyncio
    async def test_unknown_label_keeps_numeric_outputs(self, svc):
        """label 未知 → sentiment None；score/conf 为有效数值输出仍保留（语义独立）。"""
        with self._patched_scorer([{"label": "无法判断", "confidence": 0.5, "score": 0.1}]):
            payload = await svc._score_sentiment_result("内容")
        assert payload["sentiment"] is None
        assert payload["sentiment_score"] == 0.1
        assert payload["sentiment_conf"] == 0.5

    @pytest.mark.asyncio
    async def test_model_tag_none_when_unloaded(self, svc):
        """打分器未加载模型 → sentiment_model None（R21 缺失不伪装）。"""
        with self._patched_scorer([{"label": "中性", "confidence": 0.6, "score": 0.0}], model_tag=None):
            payload = await svc._score_sentiment_result("常规消息")
        assert payload["sentiment_model"] is None

    @pytest.mark.asyncio
    async def test_score_sentiment_reads_payload_sentiment(self, svc):
        """_score_sentiment（N3-5 契约）= 载荷的 sentiment 键。"""
        with self._patched_scorer([{"label": "利空", "confidence": 0.8, "score": -0.5}]):
            assert await svc._score_sentiment("立案调查") == "Negative"


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

    @staticmethod
    async def _run_one_item(svc, item, sentiment, payload=None):
        """处理单条 item（真实 normalize 链），返回 save_market_news 收到的 dict。

        svc fixture 已把 CacheManager 整类 patch 为 MagicMock，
        此处用 side_effect 接回真实静态方法以验证 sentiment 与 N5-2 三打分列透传。
        """
        if payload is None:
            payload = {
                "sentiment": sentiment,
                "sentiment_score": None,
                "sentiment_conf": None,
                "sentiment_model": None,
            }
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait(item)
        with (
            patch.object(svc, "_generate_tags", AsyncMock(return_value="tag")),
            patch.object(svc, "_score_sentiment_result", AsyncMock(return_value=payload)),
            patch(
                "services.news_subscription_service.CacheManager.normalize_news_item",
                side_effect=RealCacheManager.normalize_news_item,
            ),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()) as mock_save,
            patch.object(svc, "_notify_listeners", AsyncMock()),
        ):
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return mock_save.call_args[0][0]

    @pytest.mark.asyncio
    async def test_sentiment_unavailable_persists_none(self, svc):
        """R21：打分服务不可用（None）→ 落库 sentiment=None，不伪装为中性。"""
        saved = await self._run_one_item(svc, {"content": "央行发布新政策", "time": "2024-01-01"}, None)
        assert saved["sentiment"] is None
        assert saved["tags"] == "tag"

    @pytest.mark.asyncio
    async def test_sentiment_scored_persists_code(self, svc):
        """N3-5 全链：打分利好 → normalize 透传 → 落库 sentiment="Positive"。"""
        saved = await self._run_one_item(svc, {"content": "紫金矿业发现金矿", "time": "2024-01-01"}, "Positive")
        assert saved["sentiment"] == "Positive"

    @pytest.mark.asyncio
    async def test_scored_payload_persists_full(self, svc):
        """N5-2 全链：完整载荷（sentiment + 三打分列）→ normalize 透传 → 落库。"""
        saved = await self._run_one_item(
            svc,
            {"content": "紫金矿业发现金矿", "time": "2024-01-01"},
            "Positive",
            payload={
                "sentiment": "Positive",
                "sentiment_score": 0.8,
                "sentiment_conf": 0.9,
                "sentiment_model": "finbert2-news-q8_0",
            },
        )
        assert saved["sentiment"] == "Positive"
        assert saved["sentiment_score"] == 0.8
        assert saved["sentiment_conf"] == 0.9
        assert saved["sentiment_model"] == "finbert2-news-q8_0"

    @pytest.mark.asyncio
    async def test_unscored_path_persists_none_columns(self, svc):
        """N5-2/R21：未打分路径三数值列为 None，不伪装（R21 不填 0）。"""
        saved = await self._run_one_item(svc, {"content": "普通新闻", "time": "2024-01-01"}, None)
        assert saved["sentiment_score"] is None
        assert saved["sentiment_conf"] is None
        assert saved["sentiment_model"] is None

    @staticmethod
    async def _run_one_item_llm_fallback(svc, item, scorer_results, classify_result):
        """N3-6 全链：真实 _generate_tags/_score_sentiment（scorer/classify mock），返回落库 dict。

        与 _run_one_item 区别：不 mock 服务方法本身，验证单次 classify_news 共用 +
        白名单过滤 + 降级回退贯通。scorer_results=None 模拟打分服务整体不可用。
        """
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait(item)
        svc.ai_client.classify_news = AsyncMock(return_value=classify_result)
        with (
            patch("services.news_subscription_service.NewsSentimentScorer") as mock_scorer_cls,
            patch(
                "services.news_subscription_service.CacheManager.normalize_news_item",
                side_effect=RealCacheManager.normalize_news_item,
            ),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()) as mock_save,
            patch.object(svc, "_notify_listeners", AsyncMock()),
        ):
            mock_scorer_cls.get_instance = AsyncMock(
                return_value=MagicMock(score_texts=AsyncMock(return_value=scorer_results))
            )
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return mock_save.call_args[0][0], svc.ai_client.classify_news

    @pytest.mark.asyncio
    async def test_llm_fallback_flows_when_scorer_unavailable(self, svc):
        """N3-6 全链：打分服务不可用 + LLM 判利好 → 落库 sentiment="Positive"；单次 classify。"""
        saved, classify = await self._run_one_item_llm_fallback(
            svc,
            {"content": "紫金矿业发现金矿", "time": "2024-01-01"},
            None,
            {"emoji": "🚀", "category": "Tech", "sentiment": "Positive"},
        )
        assert saved["sentiment"] == "Positive"
        classify.assert_called_once_with("紫金矿业发现金矿")  # N3-6: 单次 classify_news 且入参为原始 content

    @pytest.mark.asyncio
    async def test_llm_lowercase_neutral_never_persists(self, svc):
        """Q7 全链：classify 全供应商失败（小写 neutral + error）→ 落库 None 不伪装。"""
        saved, _ = await self._run_one_item_llm_fallback(
            svc,
            {"content": "紫金矿业发现金矿", "time": "2024-01-01"},
            None,
            {"category": "unknown", "sentiment": "neutral", "error": "all providers failed"},
        )
        assert saved["sentiment"] is None

    @pytest.mark.asyncio
    async def test_both_unavailable_no_keyword_guess(self, svc):
        """R21 负向：打分与 LLM 均不可用 → 落库 None（不做关键词猜测、不回填中性）。"""
        saved, _ = await self._run_one_item_llm_fallback(
            svc,
            {"content": "普通新闻无关键词", "time": "2024-01-01"},
            None,
            None,
        )
        assert saved["sentiment"] is None

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

        async def mock_gen_tags(content, ai_result=None):
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

        async def fake_generate_tags(content, ai_result=None):
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
# S4 统一场景链接线（s4_plan §2.2：新链一次统一请求直取结果 / 旧链逐字节保留）
# ---------------------------------------------------------------------------


class TestScenarioChainWiring:
    """订阅服务新链接线：classify_news 返回含 execution_metadata → 新链；否则旧链。

    订阅服务层判定键为 execution_metadata（NewsClassifier 构造的程序状态），
    不真走 Executor——分派/校验/计量由 NewsClassifier 层测试承载（批次 1）。
    """

    # 新链 bundled 成功形态（NewsClassifier._classify_bundled 输出契约）
    BUNDLED_OK = {
        "category_L1": "macro_policy",
        "category_L2": "",
        "category": "政策",
        "emoji": "🏛️",
        "sentiment": "Positive",
        "sentiment_status": "success",
        "sentiment_source": "bundled",
        "execution_metadata": {
            "invocation_id": "inv-ok",
            "route_revision": 1,
            "connection_id": "conn-1",
            "connection_revision": 3,
            "actual_model": "gpt-x",
            "usage_status": "reported",
        },
    }
    # 新链全失败形态（_scenario_failure_result 契约）
    BUNDLED_FAILED = {
        "category": "unknown",
        "sentiment": None,
        "sentiment_status": "unavailable",
        "sentiment_source": "bundled",
        "error": "all candidates failed",
        "execution_metadata": {
            "invocation_id": "inv-fail",
            "route_revision": 1,
            "connection_id": None,
            "connection_revision": None,
            "actual_model": None,
            "usage_status": "unavailable",
        },
    }

    @staticmethod
    async def _run_with_classify(svc, item, classify_result):
        """单条 item 走真实 _processing_loop，返回 (save 载荷, classify mock, scorer mock)。"""
        svc._running = True
        svc.processing_queue = asyncio.Queue(maxsize=1)
        svc.processing_queue.put_nowait(item)
        svc.ai_client.classify_news = AsyncMock(return_value=classify_result)
        with (
            patch.object(
                svc,
                "_score_sentiment_result",
                AsyncMock(
                    return_value={
                        "sentiment": "Negative",
                        "sentiment_score": 0.1,
                        "sentiment_conf": 0.2,
                        "sentiment_model": "finbert2-news-q8_0",
                    }
                ),
            ) as mock_score,
            patch(
                "services.news_subscription_service.CacheManager.normalize_news_item",
                side_effect=RealCacheManager.normalize_news_item,
            ),
            patch.object(svc.cache.market_dao, "save_market_news", AsyncMock()) as mock_save,
            patch.object(svc, "_notify_listeners", AsyncMock()),
        ):
            task = asyncio.create_task(svc._processing_loop())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return mock_save.call_args[0][0], svc.ai_client.classify_news, mock_score

    @pytest.mark.asyncio
    async def test_new_chain_direct_take_single_request(self, svc):
        """新链：sentiment 直取统一结果 + 三数值列 None（不再打分）+ metadata 完整；scorer 零调用。"""
        saved, classify, mock_score = await self._run_with_classify(
            svc,
            {"content": "央行发布新政策", "time": "2024-01-01"},
            dict(self.BUNDLED_OK),
        )
        classify.assert_called_once_with("央行发布新政策")  # 一次统一请求
        mock_score.assert_not_awaited()  # 执行器之后不再打分（NewsSentimentScorer 直调移除）
        assert saved["sentiment"] == "Positive"
        assert saved["sentiment_score"] is None
        assert saved["sentiment_conf"] is None
        assert saved["sentiment_model"] is None
        assert saved["category_l1"] == "macro_policy"
        assert saved["category_l2"] is None  # 空串归一化为缺失（COALESCE 不清空既有分类）
        am = saved["analysis_metadata"]
        assert am["sentiment_applied"] is True
        assert am["sentiment_status"] == "success"
        assert am["sentiment_source"] == "bundled"
        assert am["invocation_id"] == "inv-ok"
        assert am["route_revision"] == 1
        assert am["connection_id"] == "conn-1"
        assert am["connection_revision"] == 3
        assert am["actual_model"] == "gpt-x"
        assert am["usage_status"] == "reported"
        assert am["topic_status"] == "succeeded"
        assert am["schema_version"] == 1
        assert am["analyzed_at"]
        assert "error" not in am

    @pytest.mark.asyncio
    async def test_new_chain_gguf_numbers_passthrough(self, svc):
        """F24 断裂 4：GGUF dedicated 成功链数值三列透传落库，不再无条件清空。

        ai_result 携带编码器真实数值与产物身份（NewsClassifier 校验器产出），
        订阅层原样透传 → normalize → save 载荷；analysis_metadata 数值状态与
        DAO sentiment_applied=True 原子分支语义保持一致。
        """
        saved, classify, mock_score = await self._run_with_classify(
            svc,
            {"content": "央行发布新政策", "time": "2024-01-01"},
            {
                "category_L1": "macro_policy",
                "category_L2": "",
                "category": "政策",
                "emoji": "🏛️",
                "sentiment": "Positive",
                "sentiment_score": 0.6,
                "sentiment_conf": 0.7,
                "sentiment_model": "finbert2-base",
                "sentiment_status": "success",
                "sentiment_source": "dedicated:gguf",
                "execution_metadata": {
                    "invocation_id": "inv-gguf",
                    "route_revision": 2,
                    "connection_id": "conn-9",
                    "connection_revision": 1,
                    "actual_model": "finbert2-base",
                    "usage_status": "reported",
                },
            },
        )
        classify.assert_called_once_with("央行发布新政策")
        mock_score.assert_not_awaited()  # 执行器之后不再打分（数值来自编码器校验器）
        assert saved["sentiment"] == "Positive"
        assert saved["sentiment_score"] == 0.6
        assert saved["sentiment_conf"] == 0.7
        assert saved["sentiment_model"] == "finbert2-base"
        am = saved["analysis_metadata"]
        assert am["sentiment_applied"] is True
        assert am["sentiment_status"] == "success"
        assert am["sentiment_source"] == "dedicated:gguf"
        assert am["connection_id"] == "conn-9"
        assert am["actual_model"] == "finbert2-base"

    @pytest.mark.asyncio
    async def test_new_chain_gguf_partial_numbers_kept(self, svc):
        """F24：GGUF partial 链有效条目数值照常落库，状态透传不伪装 success（R21）。"""
        saved, _, mock_score = await self._run_with_classify(
            svc,
            {"content": "震荡整理", "time": "2024-01-01"},
            {
                "category": "unknown",
                "sentiment": "Neutral",
                "sentiment_score": None,
                "sentiment_conf": None,
                "sentiment_model": "finbert2-base",
                "sentiment_status": "partial",
                "sentiment_source": "dedicated:gguf",
                "execution_metadata": {
                    "invocation_id": "inv-gguf-partial",
                    "route_revision": 2,
                    "connection_id": "conn-9",
                    "connection_revision": 1,
                    "actual_model": "finbert2-base",
                    "usage_status": "reported",
                },
            },
        )
        mock_score.assert_not_awaited()
        assert saved["sentiment"] == "Neutral"
        assert saved["sentiment_score"] is None
        assert saved["sentiment_conf"] is None
        assert saved["sentiment_model"] == "finbert2-base"
        am = saved["analysis_metadata"]
        assert am["sentiment_applied"] is True
        assert am["sentiment_status"] == "partial"

    @pytest.mark.asyncio
    async def test_new_chain_failure_group_kept_via_dao_branch(self, svc):
        """新链全失败：sentiment_applied=False（旧成功组由 DAO 条件分支保留）+ error 通道。"""
        saved, _, mock_score = await self._run_with_classify(
            svc,
            {"content": "普通新闻", "time": "2024-01-01"},
            dict(self.BUNDLED_FAILED),
        )
        mock_score.assert_not_awaited()
        assert saved["sentiment"] is None  # R21：缺失不伪装
        assert saved["sentiment_score"] is None
        assert saved["sentiment_conf"] is None
        assert saved["sentiment_model"] is None
        am = saved["analysis_metadata"]
        assert am["sentiment_applied"] is False
        assert am["sentiment_status"] == "unavailable"
        assert am["topic_status"] == "failed"
        assert am["error"] == "all candidates failed"
        assert "category_l1" not in saved or saved["category_l1"] is None

    @pytest.mark.asyncio
    async def test_new_chain_topic_ok_sentiment_missing(self, svc):
        """新链主题成功但情绪缺失：applied=False（不覆盖旧成功组），topic_status=succeeded。"""
        result = dict(self.BUNDLED_OK)
        result["sentiment"] = None
        result["sentiment_status"] = "unavailable"
        saved, _, _ = await self._run_with_classify(
            svc,
            {"content": "普通新闻", "time": "2024-01-01"},
            result,
        )
        am = saved["analysis_metadata"]
        assert am["sentiment_applied"] is False
        assert am["sentiment_status"] == "unavailable"
        assert am["topic_status"] == "succeeded"
        assert saved["sentiment"] is None

    @pytest.mark.asyncio
    async def test_legacy_chain_rescores_and_no_metadata(self, svc):
        """旧链（pending 逐字节保留）：scorer 照常打分、载荷无 analysis_metadata（DAO 旧 SQL 分支）。"""
        legacy = {"emoji": "🚀", "category": "Tech", "sentiment": "Positive"}
        saved, _, mock_score = await self._run_with_classify(
            svc,
            {"content": "紫金矿业发现金矿", "time": "2024-01-01"},
            legacy,
        )
        mock_score.assert_awaited_once_with("紫金矿业发现金矿", llm_fallback="Positive")
        assert saved["sentiment"] == "Negative"  # scorer 载荷透传（mock 返回值）
        assert "analysis_metadata" not in saved
        assert "category_l1" not in saved
        assert "category_l2" not in saved

    @pytest.mark.asyncio
    async def test_legacy_chain_classify_none_still_rescores(self, svc):
        """旧链 classify 异常降级（None）：照常打分、无 metadata（行为冻结）。"""
        saved, _, mock_score = await self._run_with_classify(
            svc,
            {"content": "普通新闻", "time": "2024-01-01"},
            None,
        )
        mock_score.assert_awaited_once_with("普通新闻", llm_fallback=None)
        assert "analysis_metadata" not in saved

    @pytest.mark.asyncio
    async def test_dedicated_gguf_success_direct_take(self, svc):
        """dedicated:gguf 情绪成功：同样直取（source 进 metadata），三数值列 None。"""
        result = dict(self.BUNDLED_OK)
        result["sentiment"] = "Negative"
        result["sentiment_source"] = "dedicated:gguf"
        result["category_L1"] = "company_ops"
        result["category_L2"] = "earnings"
        saved, _, _ = await self._run_with_classify(
            svc,
            {"content": "某公司业绩暴雷", "time": "2024-01-01"},
            result,
        )
        assert saved["sentiment"] == "Negative"
        assert saved["category_l1"] == "company_ops"
        assert saved["category_l2"] == "earnings"
        am = saved["analysis_metadata"]
        assert am["sentiment_applied"] is True
        assert am["sentiment_source"] == "dedicated:gguf"


class TestAnalysisMetadataBuilder:
    """_build_analysis_metadata 纯函数契约（s4_plan §2.3：程序状态，不存 prompt/完整响应）。"""

    EXEC_META = {
        "invocation_id": "inv-1",
        "route_revision": 2,
        "connection_id": "conn-9",
        "connection_revision": 5,
        "actual_model": "m-1",
        "usage_status": "reported",
    }

    def test_content_hash_matches_dao_calculation(self):
        """输入摘要与 save_market_news 旧链 content_hash 同口径（sha256 全文 hex）。"""
        content = "某条新闻全文"
        am = _build_analysis_metadata(content, {"sentiment": None}, dict(self.EXEC_META))
        assert am["content_hash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert am["content_length"] == len(content)

    def test_success_fields(self):
        am = _build_analysis_metadata(
            "内容",
            {
                "sentiment": "Positive",
                "sentiment_status": "success",
                "sentiment_source": "bundled",
            },
            dict(self.EXEC_META),
        )
        assert am["sentiment_applied"] is True
        assert am["topic_status"] == "succeeded"
        assert am["schema_version"] == 1
        assert am["invocation_id"] == "inv-1"
        assert "error" not in am

    def test_failure_fields_and_error_channel(self):
        am = _build_analysis_metadata(
            "内容",
            {"category": "unknown", "sentiment": None, "error": "boom"},
            dict(self.EXEC_META),
        )
        assert am["sentiment_applied"] is False
        assert am["topic_status"] == "failed"
        assert am["error"] == "boom"

    def test_no_prompt_or_response_stored(self):
        """R9/§2.3 边界：metadata 只含程序状态摘要，不存 prompt/完整响应。"""
        am = _build_analysis_metadata("内容", {"sentiment": None}, dict(self.EXEC_META))
        for key in ("prompt", "response", "raw_output", "system_prompt"):
            assert key not in am


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


class TestProcessingQueueLoopLocal:
    """CON-07: processing_queue property resolves dynamically to current loop."""

    def test_queue_is_none_before_start(self):
        svc = NewsSubscriptionService()
        assert svc.processing_queue is None

    @pytest.mark.asyncio
    async def test_queue_resolves_dynamically_to_current_loop(self):
        svc = NewsSubscriptionService()
        svc._queue_active = True
        q1 = svc.processing_queue
        assert isinstance(q1, asyncio.Queue)
        # Same loop gets identical instance
        assert svc.processing_queue is q1

        # Across a different loop in another thread, it gets a separate loop-local instance
        def _in_other_thread():
            other_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(other_loop)

                async def _get_queue():
                    return svc.processing_queue

                return other_loop.run_until_complete(_get_queue())
            finally:
                other_loop.close()
                asyncio.set_event_loop(None)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            q2 = executor.submit(_in_other_thread).result()

        assert isinstance(q2, asyncio.Queue)
        assert q1 is not q2

    def test_queue_setter_override_and_clear(self):
        svc = NewsSubscriptionService()
        mock_q = MagicMock()
        svc.processing_queue = mock_q
        assert svc.processing_queue is mock_q
        svc.processing_queue = None
        assert svc.processing_queue is None
