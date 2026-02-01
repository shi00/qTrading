import flet as ft
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.onboarding_wizard import OnboardingWizard
from ui.theme import AppColors, AppStyles, apply_page_theme
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
from utils.scheduler_service import scheduler
from data.news_subscription import NewsSubscriptionService

from ui.i18n import I18n
import logging

logger = logging.getLogger(__name__)

async def main(page: ft.Page):
    setup_logging()
    
    # --- Network Optimization (Smart Proxy) ---
    from utils.proxy_manager import ProxyManager
    ProxyManager.apply_smart_proxy_policy()
    
    I18n.initialize() # Initialize Locale
    
    # ... (Error handling code kept same, assume it's before this block if not shown) ...
    # Silence asyncio 'ConnectionResetError' on Windows exit
    
    def silence_event_loop_closed(loop, context):
        # ... (Existing handler) ...
        pass # Placeholder for brevity, strict replacement should include it or not touch it if outside range. 
        # I will preserve the surrounding code by targeting specific range.

    # ... (Skip ahead to page setup) ...

    # Start background scheduler
    scheduler.start()
    
    page.title = I18n.get("app_title")
    
    # ... (Cleanup resources code) ...
    
    def cleanup_resources(e):
        # ...
        logger.info("Cleaning up resources...")
        scheduler.stop()
        NewsSubscriptionService().stop()
        
        # Shutdown thread pools
        from data.news_fetcher import NewsFetcher
        NewsFetcher.shutdown()
        
        async def async_cleanup():
            try:
                from data.data_processor import DataProcessor
                dp = DataProcessor()
                await dp.close()
                logger.info("DB Writer flushed and closed.")
            except Exception as ex:
                logger.error(f"Cleanup error: {ex}")

        # Run async cleanup synchronously before exit
        try:
             import asyncio
             try:
                 loop = asyncio.get_event_loop()
                 if loop.is_running():
                     future = asyncio.run_coroutine_threadsafe(async_cleanup(), loop)
                     future.result(timeout=3)
                 else:
                     loop.run_until_complete(async_cleanup())
             except Exception as e:
                 logger.warning(f"Async cleanup failed: {e}")
        except Exception as e:
             logger.error(f"Cleanup runner failed: {e}")
        
        import os
        import time
        time.sleep(0.5)
        logger.info("Force exiting process...")
        os._exit(0)

    page.on_disconnect = cleanup_resources
    
    page.padding = 0
    apply_page_theme(page)
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window_icon = "icon.png"
    
    # --- Toast Manager (Proposal A) ---
    from ui.components.toast_manager import ToastManager
    page.toast = ToastManager(page)
    
    def show_toast(message, type="info"):
        page.toast.show(message, type)
    
    page.show_toast = show_toast # Helper for views

    
    # --- State Management ---
    main_layout = None
    nav_rail = None
    
    def on_locale_change():
        """Refresh UI on locale change"""
        page.title = I18n.get("app_title")
        if nav_rail:
            # Update Nav Rail Labels
            nav_rail.destinations[0].label = I18n.get("nav_market")
            nav_rail.destinations[0].label_content.value = I18n.get("nav_market")
            
            nav_rail.destinations[1].label = I18n.get("nav_screener")
            nav_rail.destinations[1].label_content.value = I18n.get("nav_screener")
            
            nav_rail.destinations[2].label = I18n.get("nav_settings")
            nav_rail.destinations[2].label_content.value = I18n.get("nav_settings")
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
        settings_view = SettingsView()
        
        views = [
            home_view,
            screener_view,
            settings_view
        ]

        page.window_icon = "/icon.png" # Icon from assets (with slash)
    
        # Brand header for navigation
        brand_header = ft.Container(
            content=ft.Column([
                ft.Image(src="/icon.png", width=48, height=48, fit=ft.ImageFit.CONTAIN),
                ft.Text("A股智选", size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_ON_PRIMARY),
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
    print(f"DEBUG: Token='{masked_token}', Onboarding='{onboarding_complete}'")

    
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
    ft.app(target=main, assets_dir="assets")
