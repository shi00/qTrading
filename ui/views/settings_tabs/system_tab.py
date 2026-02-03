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
                ft.dropdown.Option("DEBUG", I18n.get("sys_opt_debug")),
                ft.dropdown.Option("INFO", I18n.get("sys_opt_info")),
                ft.dropdown.Option("WARNING", I18n.get("sys_opt_warn")),
                ft.dropdown.Option("ERROR", I18n.get("sys_opt_error")),
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
            suffix_text=I18n.get("common_items"),
            border_radius=8
        )
        
        val = ConfigHandler.get_tushare_api_limit()
        self.rate_limit_input = ft.TextField(
            value=str(val) if val and val > 0 else "",
            width=100,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("common_times_min"),
            border_radius=8
        )
        
        # Layout Construction
        self.content = ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column([
                        SectionHeader(I18n.get("sys_core_config")),
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                        
                        # 1. Log Level Item
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.BUG_REPORT_ROUNDED, color=AppColors.PRIMARY, size=24),
                                padding=10, bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY), border_radius=10
                            ),
                            ft.Column([
                                ft.Text(I18n.get("settings_log_level"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("sys_log_label"), size=12, color=AppColors.TEXT_SECONDARY),
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
                                ft.Text(I18n.get("sys_concurrency"), 
                                       size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("sys_concurrency_hint"), 
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
                                ft.Text(I18n.get("sys_tushare_limit"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("sys_tushare_limit_desc"), size=12, color=AppColors.TEXT_SECONDARY),
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

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 5. Data Maintenance (System Init)
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.CLEANING_SERVICES_ROUNDED, color=ft.Colors.RED_400, size=24),
                                padding=10, bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.RED_400), border_radius=10
                            ),
                            ft.Column([
                                ft.Text(I18n.get("sys_data_maint"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("sys_data_maint_desc"), size=12, color=AppColors.TEXT_SECONDARY),
                            ], expand=True, spacing=2),
                            ft.ElevatedButton(
                                text=I18n.get("sys_btn_init"),
                                icon=ft.Icons.REFRESH,
                                style=ft.ButtonStyle(
                                    color=ft.Colors.WHITE,
                                    bgcolor=ft.Colors.RED_400,
                                ),
                                on_click=self.show_init_dialog
                            ),
                            ft.OutlinedButton(
                                text=I18n.get("sys_btn_health"),
                                icon=ft.Icons.HEALTH_AND_SAFETY,
                                on_click=self.on_health_check_click
                            )
                        ]),
                        
                    ], spacing=10)
                )
            ],
            padding=ft.padding.only(bottom=50)
        )

    def show_init_dialog(self, e):
        # Confirmation Dialog
        def close_dlg(e):
            self.page.close(dlg_modal)

        def start_init(e):
            self.page.close(dlg_modal)
            # We must schedule the async task
            self.page.run_task(self.run_system_initialization)

        dlg_modal = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("sys_init_confirm_title")),
            content=ft.Text(I18n.get("sys_init_confirm_content")),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=close_dlg),
                ft.TextButton(I18n.get("common_start_exec"), on_click=start_init, style=ft.ButtonStyle(color=ft.Colors.RED)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg_modal)

    async def run_system_initialization(self):
        from data.data_processor import DataProcessor
        import asyncio

        # Progress Dialog UI
        pb = ft.ProgressBar(width=400, color=AppColors.PRIMARY, bgcolor=AppColors.SURFACE_VARIANT)
        status_text = ft.Text(I18n.get("common_preparing"), size=12, color=AppColors.TEXT_SECONDARY)
        
        cancel_event = asyncio.Event()

        def cancel_click(e):
            cancel_event.set()
            status_text.value = I18n.get("sys_init_cancel_wait")
            status_text.color = ft.Colors.RED
            status_text.update()

        dlg_progress = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("sys_init_progress_title")),
            content=ft.Column([
                ft.Text(I18n.get("sys_init_wait")),
                ft.Container(height=10),
                pb,
                ft.Container(height=5),
                status_text
            ], height=100, width=400),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=cancel_click, style=ft.ButtonStyle(color=ft.Colors.RED))
            ],
            actions_alignment=ft.MainAxisAlignment.CENTER
        )
        self.page.open(dlg_progress)
        
        try:
            dp = DataProcessor()
            
            def progress_cb(current, total, msg):
                # Update UI elements directly? 
                # In Flet async handlers, we can just update? 
                # But callback comes from DP which might be deep in loop. 
                # We need to ensure we don't block.
                # However, DP calls callback.
                # We can't await inside non-async callback.
                # Trick: Just validation.
                status_text.value = f"{msg} ({int(current/max(1,total)*100)}%)" if total > 0 else msg
                # status_text.update() # Sync update call might fail if loop busy?
                # Better to just set value and trigger update periodically?
                # Or use page.run_thread_safe if from thread?
                # DataProcessor runs in asyncio loop.
                # So we are in the same loop if DP is awaited properly.
                # But callback is sync function.
                # self.page.update() might be too heavy?
                # Let's try direct update() on control.
                try:
                    status_text.update()
                    pb.value = float(current) / max(1.0, float(total))
                    pb.update()
                except:
                    pass

            report = await dp.initialize_system(progress_callback=progress_cb, cancel_event=cancel_event)
            
            self.page.close(dlg_progress)
            self.show_snack(I18n.get("sys_init_success"), color=ft.Colors.GREEN)
            
            # Auto-Show Health Report if available
            if isinstance(report, dict):
                self._show_health_report(report)
            
        except Exception as e:
            self.page.close(dlg_progress)
            logger.error(f"Initialization failed: {e}")
            self.show_snack(I18n.get("sys_init_failed").format(error=e), color=ft.Colors.RED)



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
                # Empty means unlimited (0)
                ConfigHandler.set_tushare_api_limit(0)
                self.show_snack(I18n.get("sys_snack_limit_off"))
                logger.info("Tushare API rate limit disabled (Unlimited)")
                return
            
            limit = int(limit_str)
            if limit <= 0:
                ConfigHandler.set_tushare_api_limit(0)
                self.show_snack("Tushare API 速率限制已解除 (不限速)")
                return
                
            if limit < 10:
                 self.show_snack(I18n.get("sys_snack_limit_min"))
                 return
            
            ConfigHandler.set_tushare_api_limit(limit)
            self.show_snack(I18n.get("sys_snack_limit_set").format(limit=limit))
            logger.info(f"Tushare API rate limit updated to {limit}")
        except ValueError:
            self.show_snack(I18n.get("ai_snack_param_err"), color=ft.Colors.RED)

    def on_health_check_click(self, e):
        """Run standalone health check reusing the core logic"""
        self.page.run_task(self._run_health_check)

    async def _run_health_check(self):
        from data.data_processor import DataProcessor
        import asyncio
        
        # Loading Dialog
        dlg_loading = ft.AlertDialog(
            modal=True,
            content=ft.Row([
                ft.ProgressRing(),
                ft.Text(I18n.get("health_checking"), size=16)
            ], alignment=ft.MainAxisAlignment.CENTER),
            actions=[],
        )
        self.page.open(dlg_loading)
        
        try:
            dp = DataProcessor()
            report = await dp.check_data_health()
            
            self.page.close(dlg_loading)
            self._show_health_report(report)
            
        except Exception as e:
            self.page.close(dlg_loading)
            self.show_snack(I18n.get("common_check_fail").format(error=e), color=ft.Colors.RED)

    def _show_health_report(self, report):
        """Display the health check report dialog"""
        from ui.components.health_report_dialog import HealthReportDialog
        dlg = HealthReportDialog(self.page, report)
        self.page.open(dlg)
