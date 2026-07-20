import asyncio
import hashlib
import html
import inspect
import logging
import re
import threading
import typing
from collections import OrderedDict

from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.loop_local import del_loop_local, get_loop_local
from utils.sanitizers import DataSanitizer
from utils.singleton_registry import register_singleton
from utils.thread_pool import TaskType, ThreadPoolManager
from data.cache.cache_manager import CacheManager
from data.persistence.daos.base_dao import EngineDisposedError
from services.ai_service import AIService

logger = logging.getLogger(__name__)


class NewsUpdateType:
    NEW_ITEM = "new_item"
    TAG_UPDATE = "tag_update"
    INITIAL = "initial"


@register_singleton
class NewsSubscriptionService:
    """
    Background service to poll real-time news.
    """

    _instance = None
    _lock = threading.Lock()  # Thread-safe singleton

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            inst = cls._instance
            cls._instance = None
            cls._initialized = False
        del_loop_local("news_processing_queue")
        del_loop_local("news_queue_put_lock")
        if inst is not None:
            inst._listeners.clear()
            inst._alert_listeners.clear()

    @classmethod
    def _atexit_cleanup(cls):
        """Cleanup background tasks on process exit."""
        if cls._instance is None:
            return
        if not hasattr(cls._instance, "_background_tasks"):
            return
        tasks = cls._instance._background_tasks
        if not isinstance(tasks, set):
            return
        for task in list(tasks):
            if not task.done():
                task.cancel()

    def __init__(self):
        if self._initialized:
            return

        self.cache = CacheManager()
        self.ai_client = AIService()
        self._running = False
        self._last_news_time = None
        self._last_news_content = None

        # Async Queue
        self.processing_queue = None

        # Strong references to prevent GC from killing background tasks
        self._background_tasks = set()

        # Observer Pattern: List of callbacks
        # Format: set of callables
        self._listeners = set()
        self._alert_listeners = set()  # Special listeners for popups (controlled by config)

        self._current_fetch_task = None
        self._processing_task = None

        # P2-R4: Content hash dedup (LRU-style) to catch duplicates across restarts
        self._seen_hashes: OrderedDict[str, None] = OrderedDict()
        self._MAX_SEEN = 200

        self._initialized = True

    def add_listener(self, callback: typing.Callable | None, is_alert: typing.Any = False):
        """
        Add a listener for news updates.
        Args:
            callback: Callable to be executed on update.
            Signature: () for normal, (msg) for alert.
            is_alert: If True, this is an alert listener (e.g. snackbar)
        """
        if is_alert:
            self._alert_listeners.add(callback)
            logger.debug("[NewsService] Added alert listener: %s", callback)
        else:
            self._listeners.add(callback)
            logger.info("[NewsService] Added news listener: %s", callback)

    def remove_listener(self, callback: typing.Callable | None, is_alert: typing.Any = False):
        """Remove a listener."""
        if is_alert:
            self._alert_listeners.discard(callback)
        else:
            try:
                self._listeners.remove(callback)
                logger.info("[NewsService] Removed news listener: %s", callback)
            except KeyError:
                pass

    async def _safe_queue_put(self, item: dict):
        """
        Safely put item to processing_queue with timeout and overflow handling.
        S1-2 fix: Prevent unbounded queue growth.
        """
        if self.processing_queue is None:
            return
        try:
            await asyncio.wait_for(self.processing_queue.put(item), timeout=1.0)
        except TimeoutError:
            # Serialize drop-then-put to avoid race window between multiple producers.
            def _lock_factory():
                return asyncio.Lock()

            lock = get_loop_local("news_queue_put_lock", _lock_factory)

            async with lock:
                if self.processing_queue.full():
                    try:
                        self.processing_queue.get_nowait()
                        logger.warning("[NewsService] Queue full, dropped oldest item")
                    except asyncio.QueueEmpty:
                        pass
                try:
                    self.processing_queue.put_nowait(item)
                except asyncio.QueueFull:
                    logger.warning("[NewsService] Queue still full after drop, skipping item")

    async def stop_async(self, drain_timeout: float = 3.0):
        """Stop service gracefully and drain processing queue before cancellation."""
        self._running = False

        if self._current_fetch_task and not self._current_fetch_task.done():
            self._current_fetch_task.cancel()
            try:
                await asyncio.wait_for(self._current_fetch_task, timeout=drain_timeout)
            except asyncio.CancelledError:
                # R2: stop_async 被外部取消时必须传播；_task 被主动取消时吞没合理
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    raise
            except TimeoutError:
                pass
            self._current_fetch_task = None

        if self.processing_queue is not None:
            try:
                await asyncio.wait_for(self.processing_queue.join(), timeout=drain_timeout)
            except TimeoutError:
                qsize = self.processing_queue.qsize()
                logger.warning("[NewsService] stop_async drain timeout, remaining queue size=%s", qsize)

        if self._processing_task:
            if not self._processing_task.done():
                self._processing_task.cancel()
                try:
                    await asyncio.wait_for(self._processing_task, timeout=drain_timeout)
                except asyncio.CancelledError:
                    # R2: stop_async 被外部取消时必须传播；_task 被主动取消时吞没合理
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
                        raise
                except TimeoutError:
                    pass
            self._processing_task = None

        # 清理状态，确保下次 start() 时能正确执行首次同步
        self._last_news_time = None
        self._last_news_content = None

        logger.info("[NewsService] Stopped news polling service (async graceful)")

    async def start(self):
        """
        Start the subscription service.

        Must be called within a running event loop (e.g. from async context).
        """
        if self._running:
            return

        self._running = True

        # Use loop-local Queue and Lock to avoid cross-loop reuse issues
        def _queue_factory():
            return asyncio.Queue(maxsize=500)

        self.processing_queue = get_loop_local("news_processing_queue", _queue_factory)

        poll_task = asyncio.create_task(self._poll_loop())
        self._background_tasks.add(poll_task)
        poll_task.add_done_callback(self._background_tasks.discard)

        self._processing_task = asyncio.create_task(self._processing_loop())
        self._background_tasks.add(self._processing_task)
        self._processing_task.add_done_callback(self._background_tasks.discard)

        logger.info("[NewsService] Started news polling service [STARTED]")

    def stop(self):
        """Stop the news subscription service.

        Schedules stop_async() and returns immediately. The scheduled task
        is tracked in _background_tasks to prevent GC.

        Note: For guaranteed cleanup (e.g. during shutdown), use
        ``await stop_async()`` instead.
        """
        if not self._running:
            return
        self._running = False

        if self._current_fetch_task and not self._current_fetch_task.done():
            self._current_fetch_task.cancel()

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                task = loop.create_task(self.stop_async())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
                logger.debug("[NewsService] stop() scheduled stop_async for graceful drain")
        except RuntimeError:
            if self._processing_task and not self._processing_task.done():
                self._processing_task.cancel()
            self._processing_task = None
            self._last_news_time = None
            self._last_news_content = None

        logger.info("[NewsService] Stopped news polling service")

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _poll_loop(self):
        """Main polling loop"""

        while self._running:
            # Read config dynamically
            base_interval = ConfigHandler.get_config("news_poll_interval", 60)

            # Fire and forget but track for cleanup and GC prevention.
            self._current_fetch_task = asyncio.create_task(self._safe_fetch_task())
            self._background_tasks.add(self._current_fetch_task)
            self._current_fetch_task.add_done_callback(self._background_tasks.discard)

            # Simple error handling for the loop itself (unlikely to fail here)
            try:
                await asyncio.sleep(base_interval)  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _safe_fetch_task(self):
        """Wrapper to handle errors within the independent task"""
        if not self._running:
            return

        try:
            await self._fetch_and_notify()
        except EngineDisposedError:
            logger.warning("[NewsService] Engine disposed during fetch, stopping.")
            self._running = False
        except Exception as e:
            severity = classify_severity(e)
            if severity == "system":
                logger.error(
                    "[NewsService] System-level error in background fetch task: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                raise
            logger.error(
                "[NewsService] Error in background fetch task: %s",
                DataSanitizer.sanitize_error(e),
            )
            logger.debug("[NewsService] Error in background fetch task traceback:", exc_info=True)
            # Optional: Implement retry logic here if needed, but for periodic polling,
            # just failing and waiting for next interval is often cleaner.

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def _generate_tags(self, content: typing.Any):
        """Generate tags for news content using AI or Rule-based fallback"""
        clean_content = content.strip()
        tag = ""

        # Try AI Classification first
        try:
            ai_result = await self.ai_client.classify_news(clean_content)
            if ai_result:
                emoji = ai_result.get("emoji", "[NEWS]")
                category = ai_result.get("category", "News")

                tag = I18n.get("news_tag_format", emoji=emoji, category=category)
                return tag
        except Exception as e:
            severity = classify_severity(e)
            if severity == "system":
                logger.error(
                    "[NewsService] System-level error in AI Tagging: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                raise
            logger.warning("[NewsService] AI Tagging failed: %s", DataSanitizer.sanitize_error(e))

        if any(k in clean_content for k in ["央行", "证监会", "国务院", "财政部", "政策", "立案", "违规"]):
            tag = I18n.get("news_tag_format", emoji="🏛️", category=I18n.get("tag_policy"))
        elif any(k in clean_content for k in ["美联储", "欧佩克", "纳斯达克", "汇率", "外盘", "美元"]):
            tag = I18n.get("news_tag_format", emoji="🌍", category=I18n.get("tag_global"))
        elif any(k in clean_content for k in ["GDP", "CPI", "PPI", "PMI", "社融", "通胀"]):
            tag = I18n.get("news_tag_format", emoji="📈", category=I18n.get("tag_macro"))

        return tag

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def _processing_loop(self):
        """
        Background loop to process news items from queue.
        Consumes items one by one, applies AI tagging, and updates DB.
        """
        from utils.correlation import correlation_scope

        logger.info("[NewsService] Background processing queue started.")
        while self._running:
            try:
                # Wait for item with timeout to allow checking self._running
                try:
                    item = await asyncio.wait_for(
                        self.processing_queue.get(),  # type: ignore[union-attr]
                        timeout=1.0,
                    )
                except TimeoutError:
                    continue

                # Process Item
                content = item.get("content", "")
                if not content:
                    self.processing_queue.task_done()  # type: ignore[union-attr]
                    continue

                content_hash = item.get("content_hash", hashlib.md5(content.encode()).hexdigest()[:12])
                with correlation_scope(f"news-{content_hash}"):
                    tags = await self._generate_tags(content)
                    item["tags"] = tags

                    normalized = CacheManager.normalize_news_item(
                        item,
                        default_source="CLS",
                    )
                    await self.cache.save_market_news(normalized, wait=True)

                    await self._notify_listeners(
                        update_type=NewsUpdateType.TAG_UPDATE,
                        data={"content": content, "tags": tags},
                    )

                self.processing_queue.task_done()  # type: ignore[union-attr]

                # 4. Cooperative yield: let event loop handle pending UI
                # events (Flet WebSocket dispatch) before next item.
                await asyncio.sleep(0)

            except asyncio.CancelledError:
                raise
            except EngineDisposedError:
                logger.warning("[NewsService] Engine disposed during processing, stopping loop.")
                break
            except Exception as e:
                severity = classify_severity(e)
                if severity == "system":
                    logger.error(
                        "[NewsService] System-level error in processing loop: %s",
                        DataSanitizer.sanitize_error(e),
                        exc_info=True,
                    )
                    raise
                logger.error(
                    "[NewsService] Error in processing loop: %s",
                    DataSanitizer.sanitize_error(e),
                )
                logger.debug("[NewsService] Error in processing loop traceback:", exc_info=True)
                # Prevent tight error loop logging
                await asyncio.sleep(5.0)

    async def _notify_listeners(
        self,
        listeners: typing.Any = None,
        update_type: typing.Any = None,
        data: typing.Any = None,
    ):
        target = listeners if listeners else self._listeners
        if not target:
            return

        if not hasattr(self, "_listener_errors"):
            self._listener_errors = {}

        for listener in list(target):
            try:
                sig = inspect.signature(listener)
                param_count = len(sig.parameters)
                if inspect.iscoroutinefunction(listener):
                    if param_count >= 2:
                        await asyncio.wait_for(listener(update_type, data), timeout=5.0)
                    elif param_count == 1:
                        await asyncio.wait_for(listener(update_type), timeout=5.0)
                    else:
                        await asyncio.wait_for(listener(), timeout=5.0)
                else:
                    if param_count >= 2:
                        _l, _ut, _d = listener, update_type, data
                        await asyncio.wait_for(
                            ThreadPoolManager().run_async(TaskType.IO, _l, _ut, _d),
                            timeout=5.0,
                        )
                    elif param_count == 1:
                        _l, _ut = listener, update_type
                        await asyncio.wait_for(
                            ThreadPoolManager().run_async(TaskType.IO, _l, _ut),
                            timeout=5.0,
                        )
                    else:
                        _l = listener
                        await asyncio.wait_for(
                            ThreadPoolManager().run_async(TaskType.IO, _l),
                            timeout=5.0,
                        )
                if listener in self._listener_errors:
                    del self._listener_errors[listener]
            except TimeoutError:
                logger.warning("[NewsService] Listener %s timed out (5s)", listener)
            except Exception as e:
                severity = classify_severity(e)
                if severity == "system":
                    logger.error(
                        "[NewsService] System-level error in listener %s: %s",
                        listener,
                        DataSanitizer.sanitize_error(e),
                        exc_info=True,
                    )
                    raise
                count = self._listener_errors.get(listener, 0) + 1
                self._listener_errors[listener] = count

                if count >= 3:
                    logger.error(
                        "[NewsService] Listener %s failed %s times. Removing. Last error: %s",
                        listener,
                        count,
                        DataSanitizer.sanitize_error(e),
                    )
                    if listener in self._listeners:
                        self._listeners.remove(listener)
                    if listener in self._listener_errors:
                        del self._listener_errors[listener]
                else:
                    logger.warning("[NewsService] Listener error (%s/3): %s", count, DataSanitizer.sanitize_error(e))

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def _fetch_and_notify(self):
        """Fetch latest news and trigger alert if new"""
        from utils.correlation import correlation_scope

        logger.debug("[NewsService] Polling for latest news...")
        try:
            with correlation_scope("news-fetch"):
                from data.external.news_fetcher import NewsFetcher

                is_initial_sync = self._last_news_time is None
                fetch_limit = 20 if is_initial_sync else 1

                news_list = await NewsFetcher.get_latest_global_news(limit=fetch_limit)

                def get_hash(item: dict):
                    content = item.get("content", "").strip()
                    # 归一化：去除 URL、折叠连续空白、HTML 实体解码（复用标准库，幂等且覆盖全部实体）
                    content = re.sub(r"https?://\S+", "", content)
                    content = re.sub(r"\s+", " ", content)
                    content = html.unescape(content)
                    # 边界保护：归一化后 content 为空（如纯链接新闻）回退原始 content，避免误判重复
                    if not content:
                        content = item.get("content", "").strip()
                    time_str = item.get("time", "")
                    return hashlib.sha256(f"{time_str}_{content}".encode()).hexdigest()

                if not news_list:
                    return

                if is_initial_sync:
                    logger.info(
                        "[NewsService] Initial sync: saving %s news items",
                        len(news_list),
                    )
                    for item in reversed(news_list):
                        current_time = item.get("time", "")
                        if self._last_news_time and current_time <= self._last_news_time:
                            continue
                        h = get_hash(item)
                        if h not in self._seen_hashes:
                            self._seen_hashes[h] = None
                            if len(self._seen_hashes) > self._MAX_SEEN:
                                self._seen_hashes.popitem(last=False)

                            normalized = CacheManager.normalize_news_item(
                                item,
                                default_source="CLS",
                            )
                            await self.cache.save_market_news(normalized)

                            await self._safe_queue_put(item)  # type: ignore[misc]

                    latest_item = news_list[0]
                    new_time = latest_item.get("time", self._last_news_time)
                    # 水位线单调性保护：CLS 偶发返回旧数据时不应倒退水位线
                    if not self._last_news_time or (new_time and new_time > self._last_news_time):
                        self._last_news_time = new_time
                    self._last_news_content = latest_item.get("content", "")

                    logger.info(
                        "[NewsService] Initial sync complete, queued for AI processing...",
                    )

                    await self._notify_listeners(update_type=NewsUpdateType.INITIAL)
                    return

                new_items_found = False
                new_items = []
                for item in reversed(news_list):
                    current_time = item.get("time", "")
                    if self._last_news_time and current_time <= self._last_news_time:
                        continue
                    h = get_hash(item)
                    if h not in self._seen_hashes:
                        self._seen_hashes[h] = None
                        if len(self._seen_hashes) > self._MAX_SEEN:
                            self._seen_hashes.popitem(last=False)
                        new_items_found = True
                        new_items.append(item)

                        current_news_content = item.get("content", "")
                        current_news_time = item.get("time", "")
                        logger.info(
                            "[NewsService] Found NEW update! Time: %s",
                            current_news_time,
                        )

                        clean_content = current_news_content.strip()
                        normalized = CacheManager.normalize_news_item(
                            {
                                "content": clean_content,
                                "tags": "",
                                "publish_time": current_news_time,
                                "source": "CLS",
                            },
                        )
                        await self.cache.save_market_news(normalized, wait=True)

                        await self._safe_queue_put(item)  # type: ignore[misc]

                        display_msg = clean_content
                        enable_alerts = ConfigHandler.get_config("enable_news_alerts", True)
                        if enable_alerts:
                            for listener in list(self._alert_listeners):
                                try:
                                    if inspect.iscoroutinefunction(listener) or getattr(listener, "is_async", False):
                                        await asyncio.wait_for(listener(display_msg), timeout=3.0)
                                    else:
                                        _l, _msg = listener, display_msg
                                        await asyncio.wait_for(
                                            ThreadPoolManager().run_async(TaskType.IO, _l, _msg),
                                            timeout=3.0,
                                        )
                                except TimeoutError:
                                    logger.warning("[NewsService] Alert listener %s timed out (3s)", listener)
                                except Exception as e:
                                    severity = classify_severity(e)
                                    if severity == "system":
                                        logger.error(
                                            "[NewsService] System-level error in alert listener: %s",
                                            DataSanitizer.sanitize_error(e),
                                            exc_info=True,
                                        )
                                        raise
                                    logger.error(
                                        "[NewsService] Alert listener error: %s", DataSanitizer.sanitize_error(e)
                                    )

                # 水位线单调性保护：CLS 偶发返回旧数据时不应倒退水位线
                new_time = news_list[0].get("time", self._last_news_time)
                if not self._last_news_time or (new_time and new_time > self._last_news_time):
                    self._last_news_time = new_time

                if new_items_found:
                    await self._notify_listeners(
                        update_type=NewsUpdateType.NEW_ITEM,
                        data=new_items,
                    )

        except EngineDisposedError:
            logger.warning("[NewsService] Engine disposed during poll, stopping.")
            self._running = False
        except Exception as e:
            severity = classify_severity(e)
            if severity == "system":
                logger.error(
                    "[NewsService] System-level error in poll: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                raise
            logger.warning("[NewsService] Poll failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[NewsService] Poll failed traceback:", exc_info=True)
