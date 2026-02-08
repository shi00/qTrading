import asyncio
import logging

import flet as ft

from data.cache_manager import CacheManager
from data.market_data_service import MarketDataService
from data.news_subscription import NewsSubscriptionService
from ui.components.toast_manager import ToastManager
from ui.i18n import I18n
from ui.theme import AppColors, apply_page_theme
from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.onboarding_wizard import OnboardingWizard
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
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

    # Silence asyncio 'ConnectionResetError' on Windows exit

    def silence_event_loop_closed(loop, context):
        if "Event loop is closed" not in str(context.get("message")):
            loop.default_exception_handler(context)

    # Start Cache Manager explicitly on Main Loop
    await CacheManager().start()

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
            logger.info("[Main] Step 1: Signaling Global Cancellation...")
            from data.data_processor import DataProcessor
            dp = DataProcessor()
            await dp.stop()

            logger.info("[Main] Step 2: Stopping Scheduler...")
            scheduler.stop()

            logger.info("[Main] Step 3: Stopping News Service...")
            NewsSubscriptionService().stop()

            logger.info("[Main] Step 3b: Stopping Market Data Service...")
            MarketDataService().stop()

            # Stop Toasts (Cancel pending timers)
            logger.info("[Main] Step 4: Stopping Toast Manager...")
            if hasattr(page, "toast") and page.toast:
                try:
                    # Robust shutdown: handle if stop_all is async or somehow mis-assigned
                    if asyncio.iscoroutinefunction(page.toast.stop_all):
                        await page.toast.stop_all()
                    else:
                        # Fallback if it's sync (unlikely but safe)
                        page.toast.stop_all()
                except Exception as ex:
                    logger.warning(f"Failed to stop toast manager: {ex}")

            logger.info("[Main] Step 5: Waiting for resources to release...")

            # Flush and Close Database
            await dp.close()
            logger.info("[Main] DB Writer flushed and closed.")

        except Exception as ex:
            logger.error(f"[Main] Error during cleanup: {ex}", exc_info=True)

        logger.info("[Main] Resources released. Bye.")
        # No os._exit(0) here. Flet window dispatch will close process naturally 
        # now that threads are joined and loops cancelled.

    page.on_disconnect = cleanup_resources

    page.padding = 0
    apply_page_theme(page)
    page.theme_mode = ft.ThemeMode.LIGHT

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
