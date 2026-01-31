import flet as ft
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from data.news_subscription import NewsSubscriptionService
import logging

logger = logging.getLogger(__name__)

class AutomationTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        
        auto_update_enabled = ConfigHandler.is_auto_update_enabled()
        auto_update_time = ConfigHandler.get_auto_update_time()
        
        self.schedule_enabled = ft.Switch(
            label=I18n.get("settings_auto_update"),
            value=auto_update_enabled,
            on_change=self.on_schedule_toggle
        )
        self.schedule_time = ft.Dropdown(
            label=I18n.get("settings_update_time"),
            width=150,
            value=auto_update_time,
            options=[
                ft.dropdown.Option("15:30", I18n.get("settings_opt_1530")),
                ft.dropdown.Option("16:00", "16:00"),
                ft.dropdown.Option("16:30", "16:30"),
                ft.dropdown.Option("17:00", "17:00"),
                ft.dropdown.Option("18:00", "18:00"),
                ft.dropdown.Option("20:00", I18n.get("settings_opt_2000")),
            ],
            on_change=self.on_schedule_time_change
        )
        
        self.schedule_status = ft.Text(
            self._get_schedule_status_text(auto_update_enabled),
            size=12,
            color=ft.Colors.GREEN if auto_update_enabled else ft.Colors.GREY
        )
        
        self.content = ft.Container(
            content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=[
                ft.Text(I18n.get("settings_auto_update"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text(I18n.get("settings_auto_desc"), size=14, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=10),
                ft.Container(
                    content=ft.Column([
                        ft.Row([self.schedule_enabled]),
                        ft.Row([
                            ft.Text(f"{I18n.get('settings_update_time')}:", size=14),
                            self.schedule_time,
                            ft.Text(I18n.get("settings_trading_days"), size=12, color=AppColors.TEXT_SECONDARY),
                        ]),
                        ft.Row([
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=AppColors.TEXT_SECONDARY),
                            self.schedule_status,
                        ]),
                    ]),
                    padding=15, border=ft.border.all(1, AppColors.BORDER), border_radius=8,
                ),
                ft.Text(I18n.get("settings_hint_bg_run"), size=11, color=AppColors.TEXT_HINT),
            ], spacing=20),
            **AppStyles.card()
        )

    def _get_schedule_status_text(self, enabled):
        return I18n.get("settings_status_auto_on") if enabled else I18n.get("settings_status_auto_off")

    def on_schedule_toggle(self, e):
        enabled = self.schedule_enabled.value
        ConfigHandler.save_config({"auto_update_enabled": enabled})
        self.schedule_status.value = self._get_schedule_status_text(enabled)
        self.schedule_status.color = ft.Colors.GREEN if enabled else ft.Colors.GREY
        self.update()
        self.show_snack(I18n.get("settings_snack_auto_on") if enabled else I18n.get("settings_snack_auto_off"))

    def on_schedule_time_change(self, e):
        time = self.schedule_time.value
        ConfigHandler.save_config({"auto_update_time": time})
        self.show_snack(I18n.get("settings_snack_time_set").format(time=time))


class NotificationsTab(ft.Container):
    def __init__(self, show_snack_callback, page_ref):
        super().__init__()
        self.show_snack = show_snack_callback
        self.page_ref = page_ref # Need access to page for snackbar callback in service
        
        enable_news = ConfigHandler.get_config("enable_news_alerts", True)
        
        self.news_alerts_enabled = ft.Switch(
            label=I18n.get("settings_news_alerts"),
            value=enable_news,
            on_change=self.on_news_toggle
        )
        
        self.content = ft.Container(
            content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=[
                ft.Text(I18n.get("settings_notify_title"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Container(height=10),
                ft.Container(
                    content=ft.Row([self.news_alerts_enabled]),
                    padding=15, border=ft.border.all(1, AppColors.BORDER), border_radius=8,
                ),
                ft.Text(I18n.get("settings_notify_desc"), size=14, color=AppColors.TEXT_SECONDARY)
            ], spacing=20),
            **AppStyles.card()
        )

    def on_news_toggle(self, e):
        enabled = self.news_alerts_enabled.value
        ConfigHandler.save_config({"enable_news_alerts": enabled})
        
        service = NewsSubscriptionService()
        if enabled:
            # We need a proper callback that uses 'self.page_ref' which might be None initially?
            # Ideally the page is attached.
            if self.page_ref:
                service.start(callback=lambda msg: self.page_ref.open(ft.SnackBar(ft.Text(f"📰 {msg}"), open=True)))
            self.show_snack(I18n.get("settings_snack_news_on"))
        else:
            service.stop()
            self.show_snack(I18n.get("settings_snack_news_off"))
