import asyncio
import hashlib
import inspect
import logging
import threading
import typing

from data.cache.cache_manager import CacheManager
from services.ai_service import AIService
from ui.i18n import I18n
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

        # P2-R4: Content hash dedup (LRU-style) to catch duplicates across restarts
        self._seen_hashes = set()
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

    def start(self):
        """
        Start the subscription service.
        """
        if self._running:
            return

        # 始终启动服务进行数据同步（enable_news_alerts 只控制弹窗推送）
        self._running = True

        # BUG-05 fix: Create Queue lazily here (within the running event loop)
        # to avoid binding to the wrong loop when singleton is created before loop starts.
        # S1-2 fix: Add maxsize to prevent unbounded memory growth
        self.processing_queue = asyncio.Queue(maxsize=500)

        # Keep strong references to background tasks to prevent GC
        poll_task = asyncio.create_task(self._poll_loop())
        self._background_tasks.add(poll_task)
        poll_task.add_done_callback(self._background_tasks.discard)

        # Start background processing loop
        self._processing_task = asyncio.create_task(self._processing_loop())
        self._background_tasks.add(self._processing_task)
        self._processing_task.add_done_callback(self._background_tasks.discard)

        logger.info("[NewsService] Started news polling service [STARTED]")

    def stop(self):
        """Stop the service and reset state"""
        self._running = False

        # Also cancel the detached fetch task if running
        if self._current_fetch_task and not self._current_fetch_task.done():
            self._current_fetch_task.cancel()
            self._current_fetch_task = None

        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
            self._processing_task = None

        # 清理状态，确保下次 start() 时能正确执行首次同步
        self._last_news_time = None
        self._last_news_content = None

        # U-2 fix: Do NOT clear listeners on stop() - they should persist across restarts
        # Listeners are only removed via explicit remove_listener() call
        # self._listeners.clear()
        # self._alert_listeners.clear()

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
                await asyncio.sleep(base_interval)  # type: ignore
            except asyncio.CancelledError:
                break

    async def _safe_fetch_task(self):
        """Wrapper to handle errors within the independent task"""
        if not self._running:
            return

        try:
            await self._fetch_and_notify()
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
                # AI Success
                emoji = ai_result.get("emoji", "[NEWS]")
                category = ai_result.get("category", "News")

                # Map AI category to I18n key if possible
                i18n_key = f"tag_{category.lower()}"
                localized_category = I18n.get(i18n_key)

                # If key missing or matches fallback (English), use it, otherwise use original if not found
                if localized_category == i18n_key:
                    localized_category = category  # Fallback to original if I18n key missing

                tag = f"【{emoji} {localized_category}】"
                return tag
        except Exception as e:
            logger.warning(f"[NewsService] AI Tagging failed: {e}")

        # Fallback to Rule-based
        if any(k in clean_content for k in ["央行", "证监会", "国务院", "财政部", "政策", "立案", "违规"]):
            tag = f"【🏛️ {I18n.get('tag_policy')}】"
        elif any(k in clean_content for k in ["美联储", "欧佩克", "纳斯达克", "汇率", "外盘", "美元"]):
            tag = f"【🌍 {I18n.get('tag_global')}】"
        elif any(k in clean_content for k in ["GDP", "CPI", "PPI", "PMI", "社融", "通胀"]):
            tag = f"【📈 {I18n.get('tag_macro')}】"

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
                        self.processing_queue.get(),  # type: ignore
                        timeout=1.0,
                    )
                except TimeoutError:
                    continue

                # Process Item
                content = item.get("content", "")
                if not content:
                    self.processing_queue.task_done()  # type: ignore
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

                    self._notify_listeners(
                        update_type=NewsUpdateType.TAG_UPDATE,
                        data={"content": content, "tags": tags},
                    )

                self.processing_queue.task_done()  # type: ignore

                # 4. Cooperative yield: let event loop handle pending UI
                # events (Flet WebSocket dispatch) before next item.
                await asyncio.sleep(0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[NewsService] Error in processing loop: {e}",
                    exc_info=True,
                )
                # Prevent tight error loop logging
                await asyncio.sleep(5.0)

    def _notify_listeners(
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
                if param_count >= 2:
                    listener(update_type, data)
                elif param_count == 1:
                    listener(update_type)
                else:
                    listener()
                if listener in self._listener_errors:
                    del self._listener_errors[listener]
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
                            self._seen_hashes.add(h)

                            normalized = CacheManager.normalize_news_item(
                                item,
                                default_source="CLS",
                            )
                            await self.cache.save_market_news(normalized)

                            await self._safe_queue_put(item)  # type: ignore

                    if len(self._seen_hashes) > self._MAX_SEEN:
                        self._seen_hashes = set(list(self._seen_hashes)[-self._MAX_SEEN :])

                    latest_item = news_list[0]
                    self._last_news_time = latest_item.get("time", "")
                    self._last_news_content = latest_item.get("content", "")

                    logger.info(
                        "[NewsService] Initial sync complete, queued for AI processing...",
                    )

                    self._notify_listeners(update_type=NewsUpdateType.INITIAL)
                    return

                new_items_found = False
                new_items = []
                for item in reversed(news_list):
                    h = get_hash(item)
                    if h not in self._seen_hashes:
                        self._seen_hashes.add(h)
                        new_items_found = True
                        new_items.append(item)

                        if len(self._seen_hashes) > self._MAX_SEEN:
                            self._seen_hashes = set(
                                list(self._seen_hashes)[-self._MAX_SEEN :],
                            )

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

                        await self._safe_queue_put(item)  # type: ignore

                        display_msg = clean_content
                        enable_alerts = ConfigHandler.get_config("enable_news_alerts", True)
                        if enable_alerts:
                            for listener in list(self._alert_listeners):
                                try:
                                    listener(display_msg)
                                except Exception as e:
                                    logger.error(f"[NewsService] Alert listener error: {e}")

                if new_items_found:
                    self._notify_listeners(
                        update_type=NewsUpdateType.NEW_ITEM,
                        data=new_items,
                    )

        except Exception as e:
            logger.warning(f"[NewsService] Poll failed: {e}", exc_info=True)
