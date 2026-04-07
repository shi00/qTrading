import asyncio
import logging

import flet as ft

from data.cache.cache_manager import CacheManager
from data.domain_services.market_data_service import MarketDataService
from data.external.news_subscription import NewsSubscriptionService
from ui.components.toast_manager import ToastManager
from ui.i18n import I18n
from ui.theme import apply_page_theme
from ui.views.onboarding_wizard import OnboardingWizard
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
from utils.proxy_manager import ProxyManager
from utils.scheduler_service import scheduler

logger = logging.getLogger(__name__)


async def main(page: ft.Page):
    setup_logging()

    ConfigHandler.ensure_defaults()

    ProxyManager.apply_smart_proxy_policy()

    I18n.initialize()

    cache_manager = CacheManager()

    page.title = I18n.get("app_title")
    page.window_icon = "icon.png"  # type: ignore

    async def cleanup_resources(e):
        """
        Graceful shutdown handler.
        Orchestrate stopping all services and allow natural exit.
        """
        logging.getLogger("asyncio").setLevel(logging.ERROR)

        logger.info("[Main] Cleanup initiated. Stopping services...")

        try:
            logger.info("[Main] Step 0: Cancelling all TaskManager tasks...")
            from services.task_manager import TaskManager

            await TaskManager().cancel_all_running_async()

            logger.info("[Main] Step 1: Stopping Background Services...")

            logger.info("[Main] - Stopping Scheduler...")
            scheduler.stop()

            logger.info("[Main] - Stopping News Service...")
            NewsSubscriptionService().stop()

            logger.info("[Main] - Stopping Market Data Service...")
            MarketDataService().stop()

            await asyncio.sleep(1.5)

            logger.info("[Main] Step 2: Signaling Global Cancellation...")
            from data.data_processor import DataProcessor

            dp = DataProcessor()
            await dp.stop()

            logger.info("[Main] Step 3: Stopping Toast Manager...")
            if hasattr(page, "toast") and page.toast:  # type: ignore
                try:
                    import inspect

                    if hasattr(page.toast, "stop_all"):  # type: ignore
                        res = page.toast.stop_all()  # type: ignore
                        if inspect.isawaitable(res):
                            await res
                except Exception as ex:
                    logger.warning(f"Failed to stop toast manager: {ex}")

            logger.info("[Main] Step 4: Waiting for resources to release...")

            await dp.close()
            logger.info("[Main] DB Writer flushed and closed.")

            logger.info("[Main] Step 4.5: Closing async DB connection pool...")
            try:
                from data.cache.cache_manager import CacheManager

                await CacheManager().close()
            except Exception:
                pass
            logger.info("[Main] Async DB pool closed.")

            logger.info("[Main] Step 5: Shutting down Thread Pools...")
            from utils.thread_pool import ThreadPoolManager

            ThreadPoolManager().shutdown(wait=False)

        except Exception as ex:
            logger.error(f"[Main] Error during cleanup: {ex}", exc_info=True)

        logger.info("[Main] All resources released. Exiting process immediately.")

        import os
        import time

        time.sleep(0.1)
        os._exit(0)

    page.on_disconnect = cleanup_resources

    def on_error(e):
        logger.error(f"[App] Unhandled UI Exception: {e}", exc_info=True)

    page.on_error = on_error

    page.window.min_width = 960
    page.window.min_height = 640
    if not page.window.width or page.window.width < 1200:
        page.window.width = 1280
        page.window.height = 800
    page.window.center()

    page.padding = 0
    apply_page_theme(page)

    page.toast = ToastManager(page)  # type: ignore

    def show_toast(message, type="info"):
        page.toast.show(message, type)  # type: ignore

    page.show_toast = show_toast  # type: ignore

    async def _init_services_and_start_app():
        """Initialize all services and start the app."""
        await cache_manager.init_db()

        from services.task_manager import TaskManager

        await TaskManager().init_db()

        scheduler.start()

        from ui.app_layout import AppLayout

        app_layout = AppLayout(page)

        def on_news_alert(msg):
            if hasattr(page, "toast") and page.toast:  # type: ignore
                page.toast.show(f"📰 {msg}", type="info")  # type: ignore

        NewsSubscriptionService().add_listener(on_news_alert, is_alert=True)

        NewsSubscriptionService().start()
        MarketDataService().start()

        app_layout.show()

    async def on_onboarding_complete():
        """Callback when onboarding wizard completes."""
        await _init_services_and_start_app()
        ConfigHandler.set_onboarding_complete(True)

    db_url = ConfigHandler.get_db_url()
    token = ConfigHandler.get_token()
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")
    onboarding_complete = ConfigHandler.is_onboarding_complete()

    masked_token = f"{token[:4]}****" if token and len(token) > 4 else "None"
    masked_llm_key = f"{llm_api_key[:4]}****" if llm_api_key and len(llm_api_key) > 4 else "None"
    logger.debug(
        f"DB_URL configured: {bool(db_url)}, Token='{masked_token}', API_Key='{masked_llm_key}', Onboarding='{onboarding_complete}'"
    )

    if not db_url or not token or not llm_api_key or not onboarding_complete:
        wizard = OnboardingWizard(page, on_complete=on_onboarding_complete)
        page.add(
            ft.Container(
                content=wizard,
                expand=True,
                padding=40,
            ),
        )
    else:
        await _init_services_and_start_app()


if __name__ == "__main__":
    import os

    # Ensure assets are loaded correctly relative to this script,
    # preventing errors if run from a different working directory.
    assets = os.path.join(os.path.dirname(__file__), "assets")
    ft.app(target=main, assets_dir=assets)
