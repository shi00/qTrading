import asyncio
import logging

import flet as ft

from data.cache_manager import CacheManager
from data.news_fetcher import NewsFetcher
from data.news_subscription import NewsSubscriptionService
from services.local_model_manager import LocalModelManager
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
        import time as _time
        VIEW_NAMES = ['HomeView', 'ScreenerView', 'DataExplorerView', 'SettingsView']

        # --- Debounce state for tab switching ---
        _pending_tab_index = [None]  # Use list to allow closure mutation
        _debounce_task = [None]
        _current_tab_index = [0]  # Track current tab to avoid redundant switches
        DEBOUNCE_MS = 50  # Debounce window in milliseconds

        def change_tab(e):
            """Handle tab change with debounce to prevent rapid-click freezing"""
            index = e.control.selected_index

            # Skip if already on this tab
            if index == _current_tab_index[0]:
                return

            _pending_tab_index[0] = index

            # Cancel previous pending switch if any
            if _debounce_task[0]:
                _debounce_task[0].cancel()

            # Schedule debounced switch
            _debounce_task[0] = page.run_task(_execute_tab_switch)

        async def _execute_tab_switch():
            """Execute tab switch after debounce delay"""
            try:
                await asyncio.sleep(DEBOUNCE_MS / 1000)  # Wait for debounce window
            except asyncio.CancelledError:
                return  # Task was cancelled by a newer click, exit gracefully

            index = _pending_tab_index[0]
            if index is None or index == _current_tab_index[0]:
                return

            _t0 = _time.perf_counter()
            logger.debug(f"[PERF] >>> change_tab START: switching to {VIEW_NAMES[index]} (index={index})")

            # Notify HomeView of visibility change (for auto-refresh optimization)
            home_view.set_visible(index == 0)

            body.content = views[index]
            _current_tab_index[0] = index  # Update current tab
            _t1 = _time.perf_counter()
            logger.debug(f"[PERF] change_tab: content assignment took {(_t1 - _t0) * 1000:.1f}ms")

            body.update()
            _t2 = _time.perf_counter()
            logger.debug(
                f"[PERF] <<< change_tab END: body.update() took {(_t2 - _t1) * 1000:.1f}ms, TOTAL={(_t2 - _t0) * 1000:.1f}ms")

        async def run_strategy_from_home(strategy_key):
            # Switch to Screener tab (Index 1)
            home_view.set_visible(False)  # Notify HomeView it's no longer visible
            _current_tab_index[0] = 1  # Keep debounce state in sync
            nav_rail.selected_index = 1
            body.content = screener_view
            nav_rail.update()
            body.update()
            await screener_view.select_and_run_strategy(strategy_key)

        # --- Views ---
        logger.debug("[PERF] >>> Creating views START")
        _t_views_start = _time.perf_counter()

        _t0 = _time.perf_counter()
        home_view = HomeView(on_run_strategy=run_strategy_from_home)
        logger.debug(f"[PERF] HomeView.__init__ took {(_time.perf_counter() - _t0) * 1000:.1f}ms")

        _t0 = _time.perf_counter()
        screener_view = ScreenerView()
        logger.debug(f"[PERF] ScreenerView.__init__ took {(_time.perf_counter() - _t0) * 1000:.1f}ms")

        _t0 = _time.perf_counter()
        data_view = DataExplorerView()
        logger.debug(f"[PERF] DataExplorerView.__init__ took {(_time.perf_counter() - _t0) * 1000:.1f}ms")

        _t0 = _time.perf_counter()
        settings_view = SettingsView()
        logger.debug(f"[PERF] SettingsView.__init__ took {(_time.perf_counter() - _t0) * 1000:.1f}ms")

        logger.debug(f"[PERF] <<< Creating views END, TOTAL={(_time.perf_counter() - _t_views_start) * 1000:.1f}ms")

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
            # Show snackbar on main page
            try:
                snackbar = ft.SnackBar(
                    content=ft.Text(f"📰 {msg}"),
                    duration=5000,  # Keep 5 seconds visibility for better UX
                    open=True
                )
                page.open(snackbar)
                page.update()  # Critical: force UI refresh
            except Exception as e:
                logger.error(f"[NewsAlert] Failed to open snackbar: {e}")

        NewsSubscriptionService().start(callback=on_news_alert)



    async def on_onboarding_complete():
        """Callback when onboarding wizard completes"""
        ConfigHandler.set_onboarding_complete(True)
        await show_main_app()

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
        await show_main_app()


if __name__ == "__main__":
    import os

    # Ensure assets are loaded correctly relative to this script,
    # preventing errors if run from a different working directory.
    assets = os.path.join(os.path.dirname(__file__), "assets")
    ft.app(target=main, assets_dir=assets)
