import flet as ft
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from ui.components.settings_widgets import DashboardCard, SectionHeader
import logging

logger = logging.getLogger(__name__)

class SystemTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        
        sync_concurrency = ConfigHandler.get_sync_concurrency()
        
        # Controls
        self.concurrency_text = ft.Text(f"{sync_concurrency}", size=16, weight=ft.FontWeight.BOLD, color=AppColors.PRIMARY)
        self.concurrency_slider = ft.Slider(
            min=1, max=10, divisions=9, value=sync_concurrency, 
            label="{value}", on_change=self.on_concurrency_change,
            active_color=AppColors.PRIMARY
        )
        
        self.log_level_dropdown = ft.Dropdown(
            value=ConfigHandler.get_log_level(),
            width=180,
            text_size=14,
            border_radius=8,
            content_padding=10,
            options=[
                ft.dropdown.Option("DEBUG", "调试 (DEBUG)"),
                ft.dropdown.Option("INFO", "信息 (INFO)"),
                ft.dropdown.Option("WARNING", "警告 (WARN)"),
                ft.dropdown.Option("ERROR", "错误 (ERROR)"),
            ],
            on_change=self.on_log_level_change
        )
        
        self.queue_size_input = ft.TextField(
            value=str(ConfigHandler.get_db_queue_size()),
            width=100,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text="条",
            border_radius=8
        )
        
        self.rate_limit_input = ft.TextField(
            value=str(ConfigHandler.get_api_rate_limit()),
            width=100,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text="次/分",
            border_radius=8
        )
        
        # Layout Construction
        self.content = ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column([
                        SectionHeader(I18n.get("settings_general") if I18n.get("settings_general") != "settings_general" else "核心配置"),
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                        
                        # 1. Log Level Item
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.BUG_REPORT_ROUNDED, color=AppColors.PRIMARY, size=24),
                                padding=10, bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY), border_radius=10
                            ),
                            ft.Column([
                                ft.Text(I18n.get("settings_log_level"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text("控制系统日志详细程度 (Info/Debug)", size=12, color=AppColors.TEXT_SECONDARY),
                            ], expand=True, spacing=2),
                            self.log_level_dropdown
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        
                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),
                        
                        # 2. Concurrency Item
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.SPEED_ROUNDED, color=AppColors.ACCENT, size=24),
                                padding=10, bgcolor=ft.Colors.with_opacity(0.1, AppColors.ACCENT), border_radius=10
                            ),
                            ft.Column([
                                ft.Text(I18n.get("settings_sync_concurrency") if I18n.get("settings_sync_concurrency") != "settings_sync_concurrency" else "同步并发数", 
                                       size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("settings_hint_cpu") if I18n.get("settings_hint_cpu") != "settings_hint_cpu" else "多线程请求数量，建议 3-5", 
                                       size=12, color=AppColors.TEXT_SECONDARY),
                            ], expand=True, spacing=2),
                            ft.Container(
                                content=self.concurrency_text,
                                padding=ft.padding.symmetric(horizontal=12, vertical=6),
                                border=ft.border.all(1, AppColors.BORDER),
                                border_radius=8
                            )
                        ]),
                        # Slider row below description
                        ft.Container(
                            content=self.concurrency_slider,
                            padding=ft.padding.only(left=54) # Indent to align with text
                        ),
                        
                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),
                        
                        # 3. DB Buffer Item
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.STORAGE_ROUNDED, color=ft.Colors.ORANGE, size=24),
                                padding=10, bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.ORANGE), border_radius=10
                            ),
                            ft.Column([
                                ft.Text(I18n.get("settings_db_buffer"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("settings_buffer_desc"), size=12, color=AppColors.TEXT_SECONDARY),
                            ], expand=True, spacing=2),
                            ft.Row([
                                self.queue_size_input,
                                ft.IconButton(
                                    icon=ft.Icons.SAVE_ROUNDED, 
                                    icon_color=AppColors.PRIMARY,
                                    tooltip=I18n.get("settings_save_config"),
                                    on_click=self.save_queue_size
                                )
                            ], spacing=5)
                        ]),
                        
                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),
                        
                        # 4. API Rate Limit Item
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.SPEED, color=ft.Colors.CYAN, size=24),
                                padding=10, bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.CYAN), border_radius=10
                            ),
                            ft.Column([
                                ft.Text("API 速率限制", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text("Tushare 接口每分钟最大请求次数 (默认200)", size=12, color=AppColors.TEXT_SECONDARY),
                            ], expand=True, spacing=2),
                            ft.Row([
                                self.rate_limit_input,
                                ft.IconButton(
                                    icon=ft.Icons.SAVE_ROUNDED, 
                                    icon_color=AppColors.PRIMARY,
                                    tooltip=I18n.get("settings_save_config"),
                                    on_click=self.save_rate_limit
                                )
                            ], spacing=5)
                        ]),
                        
                    ], spacing=10)
                )
            ],
            padding=ft.padding.only(bottom=50)
        )

    def on_concurrency_change(self, e):
        val = int(self.concurrency_slider.value)
        self.concurrency_text.value = f"{val}"
        ConfigHandler.set_sync_concurrency(val)
        self.show_snack(I18n.get("settings_snack_concurrency_set").format(val=val))
        self.update()

    def on_log_level_change(self, e):
        level = e.control.value
        if ConfigHandler.set_log_level(level):
            from utils.logger import update_log_level
            update_log_level(level)
            self.show_snack(I18n.get("settings_snack_log_level").format(level=level))
            logger.info(f"User changed log level to {level}")
        else:
            self.show_snack("Failed to save settings", color=ft.Colors.RED)

    def save_queue_size(self, e):
        try:
            size_str = self.queue_size_input.value.strip()
            if not size_str:
                self.show_snack(I18n.get("snack_queue_empty"))
                return
            
            size = int(size_str)
            if size < 10:
                self.show_snack(I18n.get("snack_queue_min"))
                return
            
            ConfigHandler.set_db_queue_size(size)
            self.show_snack(I18n.get("snack_queue_saved"))
            logger.info(f"DB queue size updated to {size}")
        except Exception as ex:
            self.show_snack(f"{I18n.get('snack_save_fail')}: {str(ex)}")
            logger.error(f"Failed to save queue size: {ex}")

    def save_rate_limit(self, e):
        try:
            limit_str = self.rate_limit_input.value.strip()
            if not limit_str:
                self.show_snack("请输入速率限制")
                return
            
            limit = int(limit_str)
            if limit < 10:
                self.show_snack("速率限制至少为 10 次/分钟")
                return
            
            ConfigHandler.set_api_rate_limit(limit)
            self.show_snack(f"API 速率限制已更新为 {limit} 次/分钟")
            logger.info(f"API rate limit updated to {limit}")
        except Exception as ex:
            self.show_snack(f"{I18n.get('snack_save_fail')}: {str(ex)}")
            logger.error(f"Failed to save rate limit: {ex}")
