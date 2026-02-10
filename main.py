import asyncio
import logging

import flet as ft

from data.cache_manager import CacheManager
from data.market_data_service import MarketDataService
from data.news_subscription import NewsSubscriptionService
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

    # Ensure config file has all defaults populated
    ConfigHandler.ensure_defaults()

    # --- Network Optimization    # [CRITICAL] Initialize Proxy Manager FIRST
    # 必须在所有网络请求库（如 TushareClient, Requests）初始化之前设置好 Proxy
    # 此方法是同步阻塞的，确保环境变量在后续组件加载前就绪
    ProxyManager.apply_smart_proxy_policy()

    I18n.initialize()  # Initialize Locale


    # Start Cache Manager explicitly on Main Loop
    await CacheManager().init_db()

    # Start background scheduler
    scheduler.start()

    page.title = I18n.get("app_title")
    page.window_icon = "icon.png"

    # ... (Cleanup resources code) ...

    async def cleanup_resources(e):
        """
        Graceful shutdown handler.
        Orchestrate stopping all services and allow natural exit.
        """
        # Silence low-level asyncio network warnings during shutdown
        logging.getLogger("asyncio").setLevel(logging.ERROR)

        logger.info("[Main] Cleanup initiated. Stopping services...")

        try:
            logger.info("[Main] Step 1: Stopping Background Services...")
            
            logger.info("[Main] - Stopping Scheduler...")
            scheduler.stop()

            logger.info("[Main] - Stopping News Service...")
            NewsSubscriptionService().stop()

            logger.info("[Main] - Stopping Market Data Service...")
            MarketDataService().stop()
            
            # Give services a moment to stop internal loops
            await asyncio.sleep(0.5)

            logger.info("[Main] Step 2: Signaling Global Cancellation...")
            from data.data_processor import DataProcessor
            dp = DataProcessor()
            await dp.stop()

            # Stop Toasts (Cancel pending timers)
            logger.info("[Main] Step 3: Stopping Toast Manager...")
            if hasattr(page, "toast") and page.toast:
                try:
                    import inspect
                    # Robust shutdown: handle sync or async stop_all
                    if hasattr(page.toast, "stop_all"):
                        res = page.toast.stop_all()
                        if inspect.isawaitable(res):
                            await res
                except Exception as ex:
                    logger.warning(f"Failed to stop toast manager: {ex}")

            logger.info("[Main] Step 4: Waiting for resources to release...")

            # Flush and Close Database
            await dp.close()
            logger.info("[Main] DB Writer flushed and closed.")

        except Exception as ex:
            logger.error(f"[Main] Error during cleanup: {ex}", exc_info=True)

        logger.info("[Main] Resources released. Bye.")
        # No os._exit(0) here. Flet window dispatch will close process naturally 
        # now that threads are joined and loops cancelled.

    page.on_disconnect = cleanup_resources

    def on_error(e):
        logger.error(f"[App] Unhandled UI Exception: {e}", exc_info=True)
        # Optional: Show toast to user if critical?
        # if hasattr(page, "toast"): page.toast.show(f"Error: {e}", "error")

    page.on_error = on_error

    page.padding = 0
    apply_page_theme(page)


    # --- Toast Manager (Proposal A) ---
    page.toast = ToastManager(page)

    def show_toast(message, type="info"):
        page.toast.show(message, type)

    page.show_toast = show_toast  # Helper for views

    # --- Initialize App Layout ---
    from ui.app_layout import AppLayout

    app_layout = AppLayout(page)

    async def start_app():
        """Start the main app layout"""
        # Register Global News Alert (Decoupled from AppLayout)
        def on_news_alert(msg):
            if hasattr(page, "toast") and page.toast:
                page.toast.show(f"📰 {msg}", type="info")

        NewsSubscriptionService().add_listener(on_news_alert, is_alert=True)
        
        # Start Background Services (Moved from AppLayout)
        NewsSubscriptionService().start()
        MarketDataService().start()
        
        # Show UI
        app_layout.show()

    async def on_onboarding_complete():
        """Callback when onboarding wizard completes"""
        ConfigHandler.set_onboarding_complete(True)
        await start_app()

    # --- Check if onboarding is needed ---
    token = ConfigHandler.get_token()
    onboarding_complete = ConfigHandler.is_onboarding_complete()
    masked_token = f"{token[:4]}****" if token and len(token) > 4 else "None"
    logger.debug(f"Token='{masked_token}', Onboarding='{onboarding_complete}'")

    if not token or not onboarding_complete:
        # Show onboarding wizard
        wizard = OnboardingWizard(page, on_complete=on_onboarding_complete)
        page.add(
            ft.Container(
                content=wizard,
                expand=True,
                padding=40,
            )
        )
    else:
        # Show main app directly
        await start_app()


if __name__ == "__main__":
    import os

    # Ensure assets are loaded correctly relative to this script,
    # preventing errors if run from a different working directory.
    assets = os.path.join(os.path.dirname(__file__), "assets")
    ft.app(target=main, assets_dir=assets)
