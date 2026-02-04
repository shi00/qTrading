import logging

import flet as ft

from data.cache_manager import CacheManager
from data.news_fetcher import NewsFetcher
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

    # --- Network Optimization (Smart Proxy) ---
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
            dp.stop()

            logger.info("[Main] Step 2: Stopping Scheduler...")
            scheduler.stop()

            logger.info("[Main] Step 3: Stopping News Service...")
            NewsSubscriptionService().stop()

            # Stop Toasts (Cancel pending timers)
            logger.info("[Main] Step 4: Stopping Toast Manager...")
            if hasattr(page, "toast") and page.toast:
                try:
                    await page.toast.stop_all()
                except Exception as ex:
                    logger.warning(f"Failed to stop toast manager: {ex}")

            logger.info("[Main] Step 5: Waiting for resources to release...")

            # Shutdown IO Thread Pools
            NewsFetcher.shutdown()

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

    # --- State Management ---
    main_layout = None
    nav_rail = None

    def on_locale_change():
        """Refresh UI on locale change"""
        page.title = I18n.get("app_title")
        if nav_rail:
            # Update Nav Rail Labels
            # format: (index, i18n_key)
            nav_items = [
                (0, "nav_market"),
                (1, "nav_screener"),
                (2, "nav_data"),
                (3, "nav_settings"),
            ]

            for index, key in nav_items:
                if index < len(nav_rail.destinations):
                    text = I18n.get(key)
                    nav_rail.destinations[index].label = text
                    nav_rail.destinations[index].label_content.value = text

            nav_rail.update()
        page.update()

    I18n.subscribe(on_locale_change)

    async def show_main_app():
        """Show main application after onboarding"""
        nonlocal main_layout, nav_rail

        # --- Navigation ---
        def change_tab(e):
            index = e.control.selected_index
            body.content = views[index]
            body.update()

        async def run_strategy_from_home(strategy_key):
            # Switch to Screener tab (Index 1)
            nav_rail.selected_index = 1
            body.content = screener_view
            nav_rail.update()
            body.update()
            await screener_view.select_and_run_strategy(strategy_key)

        # --- Views ---
        home_view = HomeView(on_run_strategy=run_strategy_from_home)
        screener_view = ScreenerView()
        data_view = DataExplorerView()
        settings_view = SettingsView()

        views = [
            home_view,
            screener_view,
            data_view,
            settings_view
        ]

        # Brand header for navigation
        brand_header = ft.Container(
            content=ft.Column([
                ft.Image(src="/icon.png", width=48, height=48, fit=ft.ImageFit.CONTAIN),
                ft.Text(I18n.get("app_brand"), size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_ON_PRIMARY),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=5),
            padding=ft.padding.only(top=20, bottom=10),
        )

        nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            min_extended_width=200,
            bgcolor=AppColors.PRIMARY_DARK,
            indicator_color=AppColors.ACCENT,
            leading=brand_header,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.DASHBOARD_OUTLINED,
                    selected_icon=ft.Icons.DASHBOARD,
                    label=I18n.get("nav_market"),
                    label_content=ft.Text(I18n.get("nav_market"), color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FILTER_ALT_OUTLINED,
                    selected_icon=ft.Icons.FILTER_ALT,
                    label=I18n.get("nav_screener"),
                    label_content=ft.Text(I18n.get("nav_screener"), color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.STORAGE_OUTLINED,
                    selected_icon=ft.Icons.STORAGE_ROUNDED,
                    label=I18n.get("nav_data"),
                    label_content=ft.Text(I18n.get("nav_data"), color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    selected_icon=ft.Icons.SETTINGS,
                    label=I18n.get("nav_settings"),
                    label_content=ft.Text(I18n.get("nav_settings"), color=AppColors.TEXT_ON_PRIMARY),
                ),
            ],
            on_change=change_tab,
        )

        body = ft.Container(
            content=views[0],
            expand=True,
            padding=20,
            bgcolor=AppColors.BACKGROUND,
        )

        main_layout = ft.Row(
            [
                nav_rail,
                ft.VerticalDivider(width=1),
                body
            ],
            expand=True,
        )

        page.clean()
        page.add(main_layout)
        page.update()

        # Start News Service
        def on_news_alert(msg):
            # Show snackbar on main page (thread safe)
            page.open(ft.SnackBar(ft.Text(f"📰 {msg}"), open=True))

        NewsSubscriptionService().start(callback=on_news_alert)

    async def on_onboarding_complete():
        """Callback when onboarding wizard completes"""
        ConfigHandler.set_onboarding_complete(True)
        await show_main_app()

    # --- Check if onboarding is needed ---
    token = ConfigHandler.get_token()
    onboarding_complete = ConfigHandler.is_onboarding_complete()
    masked_token = f"{token[:4]}****" if token and len(token) > 4 else "None"
    logger.info(f"DEBUG: Token='{masked_token}', Onboarding='{onboarding_complete}'")

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
        await show_main_app()


if __name__ == "__main__":
    import os

    # Ensure assets are loaded correctly relative to this script,
    # preventing errors if run from a different working directory.
    assets = os.path.join(os.path.dirname(__file__), "assets")
    ft.app(target=main, assets_dir=assets)
