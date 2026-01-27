import flet as ft
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView
from ui.views.settings_view import SettingsView
from ui.views.onboarding_wizard import OnboardingWizard
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
from utils.scheduler_service import scheduler

def main(page: ft.Page):
    setup_logging()
    
    # Start background scheduler
    scheduler.start()
    
    page.title = "A股智能选股助手 (Pro)"
    page.padding = 0
    
    # Configure theme with consistent Chinese font
    page.theme = ft.Theme(
        font_family="Microsoft YaHei, PingFang SC, Noto Sans SC, sans-serif",
    )
    page.theme_mode = ft.ThemeMode.LIGHT
    
    # --- State Management ---
    main_layout = None
    
    def show_main_app():
        """Show main application after onboarding"""
        nonlocal main_layout
        
        # --- Views ---
        home_view = HomeView(page)
        screener_view = ScreenerView(page)
        settings_view = SettingsView(page)
        
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

        nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=100,
            min_extended_width=200,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.DASHBOARD_OUTLINED, 
                    selected_icon=ft.Icons.DASHBOARD, 
                    label="市场概览"
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.FILTER_ALT_OUTLINED, 
                    selected_icon=ft.Icons.FILTER_ALT, 
                    label="智能选股"
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SETTINGS_OUTLINED, 
                    selected_icon=ft.Icons.SETTINGS, 
                    label="设置"
                ),
            ],
            on_change=change_tab,
        )

        body = ft.Container(
            content=views[0],
            expand=True,
            padding=20,
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
    ft.run(main)
