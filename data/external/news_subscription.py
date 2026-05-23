import asyncio
import contextlib
import hashlib
import inspect
import logging
import threading
import typing
from collections import OrderedDict

from data.cache.cache_manager import CacheManager
from data.persistence.daos.base_dao import EngineDisposedError
from services.ai_service import AIService
from core.i18n import I18n
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class NewsUpdateType:
    NEW_ITEM = "new_item"
    TAG_UPDATE = "tag_update"
    INITIAL = "initial"


from utils.singleton_registry import register_singleton


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
            cls._instance = None
            cls._initialized = False

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
        self._queue_put_lock = None

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
            logger.debug(f"[NewsService] Added alert listener: {callback}")
        else:
            self._listeners.add(callback)
            logger.info(f"[NewsService] Added news listener: {callback}")

    def remove_listener(self, callback: typing.Callable | None, is_alert: typing.Any = False):
        """Remove a listener."""
        if is_alert:
            self._alert_listeners.discard(callback)
        else:
            try:
                self._listeners.remove(callback)
                logger.info(f"[NewsService] Removed news listener: {callback}")
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
            lock = self._queue_put_lock
            if lock is None:
                lock = asyncio.Lock()
                self._queue_put_lock = lock

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
            with contextlib.suppress(asyncio.CancelledError):
                await self._current_fetch_task
            self._current_fetch_task = None

        if self.processing_queue is not None:
            try:
                await asyncio.wait_for(self.processing_queue.join(), timeout=drain_timeout)
            except TimeoutError:
                qsize = self.processing_queue.qsize()
                logger.warning(f"[NewsService] stop_async drain timeout, remaining queue size={qsize}")

        if self._processing_task:
            if not self._processing_task.done():
                self._processing_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._processing_task
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

        self.processing_queue = asyncio.Queue(maxsize=500)
        self._queue_put_lock = asyncio.Lock()

        poll_task = asyncio.create_task(self._poll_loop())
        self._background_tasks.add(poll_task)
        poll_task.add_done_callback(self._background_tasks.discard)

        self._processing_task = asyncio.create_task(self._processing_loop())
        self._background_tasks.add(self._processing_task)
        self._processing_task.add_done_callback(self._background_tasks.discard)

        logger.info("[NewsService] Started news polling service [STARTED]")

    def stop(self):
        """Stop the service and reset state.

        Cancels the fetch task immediately so no new items enter the queue.
        If called from a running event loop, schedules stop_async() which
        will drain the processing queue and then cancel the processing task.
        The processing task is NOT cancelled here so stop_async() can still
        drain queued items.
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
                break

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
            logger.error(
                f"[NewsService] Error in background fetch task: {e}",
                exc_info=True,
            )
            # Optional: Implement retry logic here if needed, but for periodic polling,
            # just failing and waiting for next interval is often cleaner.

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
            logger.warning(f"[NewsService] AI Tagging failed: {e}")

        if any(k in clean_content for k in ["央行", "证监会", "国务院", "财政部", "政策", "立案", "违规"]):
            tag = I18n.get("news_tag_format", emoji="🏛️", category=I18n.get("tag_policy"))
        elif any(k in clean_content for k in ["美联储", "欧佩克", "纳斯达克", "汇率", "外盘", "美元"]):
            tag = I18n.get("news_tag_format", emoji="🌍", category=I18n.get("tag_global"))
        elif any(k in clean_content for k in ["GDP", "CPI", "PPI", "PMI", "社融", "通胀"]):
            tag = I18n.get("news_tag_format", emoji="📈", category=I18n.get("tag_macro"))

        return tag

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
                break
            except EngineDisposedError:
                logger.warning("[NewsService] Engine disposed during processing, stopping loop.")
                break
            except Exception as e:
                logger.error(
                    f"[NewsService] Error in processing loop: {e}",
                    exc_info=True,
                )
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

        loop = asyncio.get_running_loop()
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
                            loop.run_in_executor(None, lambda _l=_l, _ut=_ut, _d=_d: _l(_ut, _d)),
                            timeout=5.0,
                        )
                    elif param_count == 1:
                        _l, _ut = listener, update_type
                        await asyncio.wait_for(
                            loop.run_in_executor(None, lambda _l=_l, _ut=_ut: _l(_ut)),
                            timeout=5.0,
                        )
                    else:
                        _l = listener
                        await asyncio.wait_for(
                            loop.run_in_executor(None, lambda _l=_l: _l()),
                            timeout=5.0,
                        )
                if listener in self._listener_errors:
                    del self._listener_errors[listener]
            except TimeoutError:
                logger.warning(f"[NewsService] Listener {listener} timed out (5s)")
            except Exception as e:
                count = self._listener_errors.get(listener, 0) + 1
                self._listener_errors[listener] = count

                if count >= 3:
                    logger.error(
                        f"[NewsService] Listener {listener} failed {count} times. Removing. Last error: {e}",
                    )
                    if listener in self._listeners:
                        self._listeners.remove(listener)
                    if listener in self._listener_errors:
                        del self._listener_errors[listener]
                else:
                    logger.warning(f"[NewsService] Listener error ({count}/3): {e}")

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
                    time_str = item.get("time", "")
                    return hashlib.sha256(f"{time_str}_{content}".encode()).hexdigest()

                if not news_list:
                    return

                if is_initial_sync:
                    logger.info(
                        f"[NewsService] Initial sync: saving {len(news_list)} news items",
                    )
                    for item in reversed(news_list):
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
                    self._last_news_time = latest_item.get("time", "")
                    self._last_news_content = latest_item.get("content", "")

                    logger.info(
                        "[NewsService] Initial sync complete, queued for AI processing...",
                    )

                    await self._notify_listeners(update_type=NewsUpdateType.INITIAL)
                    return

                new_items_found = False
                new_items = []
                for item in reversed(news_list):
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
                            f"[NewsService] Found NEW update! Time: {current_news_time}",
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
                            loop = asyncio.get_running_loop()
                            for listener in list(self._alert_listeners):
                                try:
                                    if inspect.iscoroutinefunction(listener) or getattr(listener, "is_async", False):
                                        await asyncio.wait_for(listener(display_msg), timeout=3.0)
                                    else:
                                        _l, _msg = listener, display_msg
                                        await asyncio.wait_for(
                                            loop.run_in_executor(None, lambda _l=_l, _msg=_msg: _l(_msg)),
                                            timeout=3.0,
                                        )
                                except TimeoutError:
                                    logger.warning(f"[NewsService] Alert listener {listener} timed out (3s)")
                                except Exception as e:
                                    logger.error(f"[NewsService] Alert listener error: {e}")

                if new_items_found:
                    await self._notify_listeners(
                        update_type=NewsUpdateType.NEW_ITEM,
                        data=new_items,
                    )

        except EngineDisposedError:
            logger.warning("[NewsService] Engine disposed during poll, stopping.")
            self._running = False
        except Exception as e:
            logger.warning(f"[NewsService] Poll failed: {e}", exc_info=True)
