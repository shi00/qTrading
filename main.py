import flet as ft
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.onboarding_wizard import OnboardingWizard
from ui.theme import AppColors, AppStyles, apply_page_theme
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
from utils.scheduler_service import scheduler

def main(page: ft.Page):
    setup_logging()
    
    # Silence asyncio 'ConnectionResetError' on Windows exit
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    def silence_event_loop_closed(loop, context):
        msg = context.get("message", "")
        # Filter out the benign Windows exit error
        if "WinError 10054" in str(context.get("exception", "")) or \
           "ConnectionResetError" in str(context.get("exception", "")):
            return
        # Handle other default exceptions
        if "exception" in context:
            # Call default handler for others? 
            # Better to just print if not the ignored one, or let default handler do it 
            # if we didn't override. But since we override, we must print others.
             import traceback
             exc = context.get("exception")
             print(f"Asyncio Error: {msg}")
             if exc:
                 traceback.print_exception(type(exc), exc, exc.__traceback__)
        else:
            print(f"Asyncio Error: {msg}")

    loop.set_exception_handler(silence_event_loop_closed)

    # Start background scheduler
    scheduler.start()
    
    page.title = "A股智能选股助手 (Pro)"
    def cleanup_resources(e):
        """Cleanup resources on exit"""
        logger.info("Cleaning up resources...")
        scheduler.stop()
        
        # Determine if we should force kill
        # For a desktop app, closing the main window should kill everything.
        import os
        import sys
        
        # Give a brief moment for logs to flush
        import time
        time.sleep(0.1)
        
        logger.info("Force exiting process...")
        os._exit(0)

    page.on_disconnect = cleanup_resources
    
    page.padding = 0
    # Apply professional theme
    apply_page_theme(page)
    page.theme_mode = ft.ThemeMode.LIGHT
    
    # --- State Management ---
    main_layout = None
    
    def show_main_app():
        """Show main application after onboarding"""
        nonlocal main_layout
        
        # --- Views ---
        home_view = HomeView()
        screener_view = ScreenerView()
        settings_view = SettingsView()
        
        views = [
            home_view,
            screener_view,
            settings_view
        ]

        # --- Navigation ---
        def change_tab(e):
            index = e.control.selected_index
            body.content = views[index]
            body.update()

        # Brand header for navigation
        brand_header = ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.SHOW_CHART, size=32, color=AppColors.ACCENT),
                ft.Text("量化选股", size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_ON_PRIMARY),
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
                    label="市场概览",
                    label_content=ft.Text("市场概览", color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FILTER_ALT_OUTLINED, 
                    selected_icon=ft.Icons.FILTER_ALT, 
                    label="智能选股",
                    label_content=ft.Text("智能选股", color=AppColors.TEXT_ON_PRIMARY),
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED, 
                    selected_icon=ft.Icons.SETTINGS, 
                    label="设置",
                    label_content=ft.Text("设置", color=AppColors.TEXT_ON_PRIMARY),
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
        
        # Clear and add main layout
        page.clean()
        page.add(main_layout)
        page.update()

    def on_onboarding_complete():
        """Callback when onboarding wizard completes"""
        ConfigHandler.set_onboarding_complete(True)
        show_main_app()

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
        show_main_app()

if __name__ == "__main__":
    ft.app(target=main)
