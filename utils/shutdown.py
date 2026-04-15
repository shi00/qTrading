import asyncio
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


class ShutdownCoordinator:
    """
    Centralized shutdown state and cleanup logic.

    Separated from main.py to enable unit testing without Flet dependencies.
    Usage in main.py:
        coordinator = ShutdownCoordinator(page)
        coordinator.start_watchdog(10)
        await coordinator.do_cleanup()
    """

    def __init__(self, page=None):
        self._page = page
        self._cleanup_done = False
        self._watchdog_started = False

    @property
    def cleanup_done(self) -> bool:
        return self._cleanup_done

    @property
    def watchdog_started(self) -> bool:
        return self._watchdog_started

    def start_watchdog(self, timeout_s=10):
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def _force_exit():
            time.sleep(timeout_s)
            logger.warning(f"[Shutdown] Watchdog timeout ({timeout_s}s) — forcing exit.")
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()
        logger.info(f"[Shutdown] Watchdog armed ({timeout_s}s).")

    async def do_cleanup(self):
        """
        Core cleanup coroutine. Stops all background services, flushes DB writes, closes pools.

        Safety rules:
        - Never call singleton factories (e.g. DataProcessor()), only access Class._instance
        - No exit statements (sys.exit / os._exit), caller decides how to exit
        """
        if self._cleanup_done:
            logger.info("[Shutdown] Cleanup already completed, skipping.")
            return
        self._cleanup_done = True

        logging.getLogger("asyncio").setLevel(logging.ERROR)
        logger.info("[Shutdown] ========== Graceful Shutdown Initiated ==========")

        try:
            await self._step0_cancel_tasks()
            await self._step1_stop_services()
            await self._step2_stop_processor()
            await self._step3_flush_db_writes()
            await self._step4_clear_toast()
            await self._step5_dispose_db_engine()
            self._step6_unload_ai_model()
            self._step7_shutdown_thread_pools()
        except Exception as ex:
            logger.error(f"[Shutdown] Error during shutdown: {ex}", exc_info=True)

        logger.info("[Shutdown] ========== Shutdown Sequence Complete ==========")

        for handler in logging.root.handlers:
            try:
                handler.flush()
            except Exception:
                pass

    async def _step0_cancel_tasks(self):
        logger.info("[Shutdown] Step 0: Cancelling managed tasks...")
        from services.task_manager import TaskManager

        if TaskManager._instance is not None:
            await TaskManager._instance.cancel_all_running_async()

    async def _step1_stop_services(self):
        logger.info("[Shutdown] Step 1: Stopping background services...")

        from data.domain_services.market_data_service import MarketDataService
        from data.external.news_subscription import NewsSubscriptionService
        from utils.scheduler_service import scheduler

        if hasattr(scheduler, "scheduler") and scheduler.scheduler.running:
            logger.info("[Shutdown]   - Scheduler")
            scheduler.stop()

        if NewsSubscriptionService._instance is not None:
            logger.info("[Shutdown]   - NewsSubscriptionService")
            NewsSubscriptionService._instance.stop()

        if MarketDataService._instance is not None:
            logger.info("[Shutdown]   - MarketDataService")
            MarketDataService._instance.stop()

        await asyncio.sleep(0.5)

    async def _step2_stop_processor(self):
        logger.info("[Shutdown] Step 2: Stopping DataProcessor...")
        from data.data_processor import DataProcessor

        if DataProcessor._instance is not None:
            await DataProcessor._instance.stop()

    async def _step3_flush_db_writes(self):
        logger.info("[Shutdown] Step 3: Waiting for pending DB writes to flush (1.0s)...")
        await asyncio.sleep(1.0)

    async def _step4_clear_toast(self):
        logger.info("[Shutdown] Step 4: Clearing Toast Manager...")
        page = self._page
        if page is not None and hasattr(page, "toast") and getattr(page, "toast", None):
            try:
                import inspect

                if hasattr(page.toast, "stop_all"):
                    res = page.toast.stop_all()
                    if inspect.isawaitable(res):
                        await res
                    logger.info("[Shutdown]   - Toast Manager stopped.")
            except Exception as e:
                logger.debug(f"[Shutdown]   - Toast Manager cleanup skipped: {e}")
        else:
            logger.info("[Shutdown]   - Toast Manager not initialized, skipping.")

    async def _step5_dispose_db_engine(self):
        logger.info("[Shutdown] Step 5: Disposing async DB engine...")
        from data.cache.cache_manager import CacheManager

        if CacheManager._instance is not None and CacheManager._instance.engine is not None:
            await CacheManager._instance.close()
            logger.info("[Shutdown]   - Async engine disposed.")
        else:
            logger.info("[Shutdown]   - DB engine was never created, skipping.")

    def _step6_unload_ai_model(self):
        logger.info("[Shutdown] Step 6: Unloading AI model...")
        try:
            from services.local_model_manager import LocalModelManager

            if LocalModelManager._instance is not None and LocalModelManager._instance._llm is not None:
                LocalModelManager._instance.unload_model()
                logger.info("[Shutdown]   - Llama.cpp model evicted.")
            else:
                logger.info("[Shutdown]   - AI model not loaded, skipping.")
        except Exception as e:
            logger.debug(f"[Shutdown]   - AI model unload skipped: {e}")

    def _step7_shutdown_thread_pools(self):
        logger.info("[Shutdown] Step 7: Shutting down Thread Pools...")
        from utils.thread_pool import ThreadPoolManager

        if ThreadPoolManager._instance is not None:
            ThreadPoolManager._instance.shutdown(wait=False)
        else:
            logger.info("[Shutdown]   - Thread pools not initialized, skipping.")
