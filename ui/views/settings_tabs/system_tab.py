import asyncio
import logging
from typing import TYPE_CHECKING

import flet as ft

from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors
from utils.config_handler import ConfigHandler

if TYPE_CHECKING:
    from data.data_processor import DataProcessor

logger = logging.getLogger(__name__)


class SystemTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback

        sync_concurrency = ConfigHandler.get_sync_concurrency()

        # --- Controls ---

        # Concurrency
        self.concurrency_text = ft.Text(
            f"{sync_concurrency}",
            size=16,
            weight=ft.FontWeight.BOLD,
            color=AppColors.PRIMARY
        )
        self.concurrency_slider = ft.Slider(
            min=1, max=10, divisions=9, value=sync_concurrency,
            label="{value}", on_change=self.on_concurrency_change,
            active_color=AppColors.PRIMARY
        )

        # Log Level
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

        # DB Buffer
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

        # Thread Pool Controls
        self.io_workers_input = ft.TextField(
            value=str(ConfigHandler.get_max_io_workers()),
            width=100,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("sys_suffix_threads"),
            border_radius=8,
            label=I18n.get("sys_pool_io")
        )

        self.cpu_workers_input = ft.TextField(
            value=str(ConfigHandler.get_max_cpu_workers()),
            width=100,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("sys_suffix_threads"),
            border_radius=8,
            label=I18n.get("sys_pool_cpu")
        )

        # Rate Limit Control
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

        # No-Proxy Domains
        domains_list = ConfigHandler.get_no_proxy_domains()
        self.no_proxy_input = ft.TextField(
            value=",".join(domains_list),
            expand=True,
            text_size=14,
            content_padding=10,
            hint_text=I18n.get("settings_no_proxy_hint"),
            border_radius=8,
            multiline=False
        )
        self.content = ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column([
                        SectionHeader(I18n.get("sys_core_config")),
                        ft.Container(height=10),

                        # 1. Log Level Item
                        self._build_setting_row(
                            icon=ft.Icons.BUG_REPORT_ROUNDED,
                            icon_color=AppColors.PRIMARY,
                            title=I18n.get("settings_log_level"),
                            subtitle=I18n.get("sys_log_label"),
                            control=self.log_level_dropdown
                        ),

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 2. Concurrency Item
                        self._build_setting_row(
                            icon=ft.Icons.SPEED_ROUNDED,
                            icon_color=AppColors.ACCENT,
                            title=I18n.get("sys_concurrency"),
                            subtitle=I18n.get("sys_concurrency_hint"),
                            control=ft.Container(
                                content=self.concurrency_text,
                                padding=ft.padding.symmetric(horizontal=12, vertical=6),
                                border=ft.border.all(1, AppColors.BORDER),
                                border_radius=8
                            )
                        ),
                        ft.Container(
                            content=self.concurrency_slider,
                            padding=ft.padding.only(left=54)  # Indent to align with text
                        ),

                        ft.Container(height=10),

                        # 3. Thread Pool Settings (Advanced)
                        self._build_setting_row(
                            icon=ft.Icons.MEMORY_ROUNDED,
                            icon_color=ft.Colors.INDIGO,
                            title=I18n.get("sys_thread_pool_title"),
                            subtitle=I18n.get("sys_thread_pool_desc"),
                            control=ft.Row([
                                self.io_workers_input,
                                self.cpu_workers_input,
                                ft.IconButton(
                                    icon=ft.Icons.SAVE_ROUNDED,
                                    icon_color=AppColors.PRIMARY,
                                    tooltip=I18n.get("settings_save_config"),
                                    on_click=self.save_thread_pool_settings
                                )
                            ], spacing=5)
                        ),

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 4. DB Buffer Item
                        self._build_setting_row(
                            icon=ft.Icons.STORAGE_ROUNDED,
                            icon_color=ft.Colors.ORANGE,
                            title=I18n.get("settings_db_buffer"),
                            subtitle=I18n.get("settings_buffer_desc"),
                            control=ft.Row([
                                self.queue_size_input,
                                ft.IconButton(
                                    icon=ft.Icons.SAVE_ROUNDED,
                                    icon_color=AppColors.PRIMARY,
                                    tooltip=I18n.get("settings_save_config"),
                                    on_click=self.save_queue_size
                                )
                            ], spacing=5)
                        ),

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 5. API Rate Limit Item
                        self._build_setting_row(
                            icon=ft.Icons.SPEED,
                            icon_color=ft.Colors.CYAN,
                            title=I18n.get("sys_tushare_limit"),
                            subtitle=I18n.get("sys_tushare_limit_desc"),
                            control=ft.Row([
                                self.rate_limit_input,
                                ft.IconButton(
                                    icon=ft.Icons.SAVE_ROUNDED,
                                    icon_color=AppColors.PRIMARY,
                                    tooltip=I18n.get("settings_save_config"),
                                    on_click=self.save_rate_limit
                                )
                            ], spacing=5)
                        ),

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 6. No-Proxy Domains
                        self._build_setting_row(
                            icon=ft.Icons.PUBLIC_OFF_ROUNDED,
                            icon_color=ft.Colors.TEAL,
                            title=I18n.get("settings_no_proxy_domains"),
                            subtitle=I18n.get("settings_no_proxy_desc"),
                            control=ft.Row([
                                self.no_proxy_input,
                                ft.IconButton(
                                    icon=ft.Icons.SAVE_ROUNDED,
                                    icon_color=AppColors.PRIMARY,
                                    tooltip=I18n.get("common_save"),
                                    on_click=self.save_no_proxy_domains
                                )
                            ], spacing=5, expand=True) # expand=True for input to take space
                        ),

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 7. Data Maintenance (System Init)
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.CLEANING_SERVICES_ROUNDED, color=AppColors.ERROR, size=24),
                                padding=10,
                                bgcolor=ft.Colors.with_opacity(0.1, AppColors.ERROR),
                                border_radius=10
                            ),
                            ft.Column([
                                ft.Text(I18n.get("sys_data_maint"), size=16, weight=ft.FontWeight.BOLD,
                                        color=AppColors.TEXT_PRIMARY),
                                ft.Text(I18n.get("sys_data_maint_desc"), size=12, color=AppColors.TEXT_SECONDARY),
                            ], expand=True, spacing=2),
                            ft.ElevatedButton(
                                text=I18n.get("sys_btn_init"),
                                icon=ft.Icons.REFRESH,
                                style=ft.ButtonStyle(
                                    color=ft.Colors.WHITE,
                                    bgcolor=AppColors.ERROR,
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

    def _build_setting_row(self, icon, icon_color, title, subtitle, control):
        return ft.Row([
            ft.Container(
                content=ft.Icon(icon, color=icon_color, size=24),
                padding=10,
                bgcolor=ft.Colors.with_opacity(0.1, icon_color),
                border_radius=10
            ),
            ft.Column([
                ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text(subtitle, size=12, color=AppColors.TEXT_SECONDARY),
            ], expand=True, spacing=2),
            control
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    def show_init_dialog(self, e):
        # Confirmation Dialog
        def close_dlg(e):
            self.page.close(dlg_modal)

        def start_init(e):
            self.page.close(dlg_modal)
            self.page.run_task(self.run_system_initialization)

        dlg_modal = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("sys_init_confirm_title")),
            content=ft.Text(I18n.get("sys_init_confirm_content")),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=close_dlg),
                ft.TextButton(
                    I18n.get("common_start_exec"),
                    on_click=start_init,
                    style=ft.ButtonStyle(color=AppColors.ERROR)
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg_modal)

    async def run_system_initialization(self):
        # Local import to avoid circular dependency
        from data.data_processor import DataProcessor

        # Progress Dialog UI
        pb = ft.ProgressBar(width=400, color=AppColors.PRIMARY, bgcolor=AppColors.SURFACE_VARIANT)
        status_text = ft.Text(I18n.get("common_preparing"), size=12, color=AppColors.TEXT_SECONDARY)

        cancel_event = asyncio.Event()

        def cancel_click(e):
            cancel_event.set()
            status_text.value = I18n.get("sys_init_cancel_wait")
            status_text.color = AppColors.ERROR
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
                ft.TextButton(I18n.get("common_cancel"), on_click=cancel_click,
                              style=ft.ButtonStyle(color=AppColors.ERROR))
            ],
            actions_alignment=ft.MainAxisAlignment.CENTER
        )
        self.page.open(dlg_progress)

        try:
            dp = DataProcessor()

            import time
            last_update_time = 0

            def progress_cb(current, total, msg):
                nonlocal last_update_time
                try:
                    current_time = time.time()
                    # Throttle updates to ~10fps or if completed
                    if current_time - last_update_time < 0.1 and current < total:
                        return

                    last_update_time = current_time

                    # Validate inputs
                    safe_total = max(1, total) if total is not None else 1
                    safe_current = current if current is not None else 0

                    status_text.value = f"{msg} ({int(safe_current / safe_total * 100)}%)" if total > 0 else msg
                    pb.value = float(safe_current) / float(safe_total)

                    status_text.update()
                    pb.update()
                except Exception:
                    # Ignore UI update errors if dialog is closed or loop is busy
                    pass

            report = await dp.initialize_system(progress_callback=progress_cb, cancel_event=cancel_event)

            self.page.close(dlg_progress)
            self.show_snack(I18n.get("sys_init_success"), color=AppColors.SUCCESS)

            # Auto-Show Health Report if available
            if isinstance(report, dict):
                self._show_health_report(report)

        except Exception as e:
            self.page.close(dlg_progress)
            logger.error(f"Initialization failed: {e}")
            self.show_snack(I18n.get("sys_init_failed").format(error=str(e)), color=AppColors.ERROR)

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
            self.show_snack(I18n.get("settings_snack_saved_fail"), color=AppColors.ERROR)

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

    async def save_thread_pool_settings(self, e):
        # Async handler to avoid blocking UI during reload
        from utils.thread_pool import ThreadPoolManager
        try:
            io_str = self.io_workers_input.value.strip()
            cpu_str = self.cpu_workers_input.value.strip()

            if not io_str or not cpu_str:
                self.show_snack(I18n.get("sys_snack_threads_empty"), color=AppColors.ERROR)
                return

            io_val = int(io_str)
            cpu_val = int(cpu_str)

            if io_val < 4 or io_val > 512:
                self.show_snack(I18n.get("sys_snack_io_range"), color=AppColors.ERROR)
                return

            if cpu_val < 1 or cpu_val > 64:
                self.show_snack(I18n.get("sys_snack_cpu_range"), color=AppColors.ERROR)
                return

            ConfigHandler.set_max_io_workers(io_val)
            ConfigHandler.set_max_cpu_workers(cpu_val)

            # Show "Processing" state?
            self.show_snack(I18n.get("common_preparing"))  # or "Applying..."

            # Trigger Reload in thread to avoid UI freeze
            # ThreadPoolManager().reload_config() waits for tasks, so it can take time
            await asyncio.to_thread(ThreadPoolManager().reload_config)

            self.show_snack(I18n.get("sys_snack_pool_saved"), color=AppColors.SUCCESS)
            logger.info(f"Updated ThreadPool: IO={io_val}, CPU={cpu_val}")

        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            self.show_snack(I18n.get("sys_snack_save_err").format(error=str(ex)), color=AppColors.ERROR)
            logger.error(f"Failed to save thread pool settings: {ex}")

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
                self.show_snack(I18n.get("sys_snack_limit_off"))
                return

            if limit < 10:
                self.show_snack(I18n.get("sys_snack_limit_min"))
                return

            ConfigHandler.set_tushare_api_limit(limit)
            self.show_snack(I18n.get("sys_snack_limit_set").format(limit=limit))
            logger.info(f"Tushare API rate limit updated to {limit}")
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def save_no_proxy_domains(self, e):
        try:
            raw_text = self.no_proxy_input.value
            if not raw_text:
                domains = []
            else:
                # Split by comma, strip whitespace, remove empty
                domains = [d.strip() for d in raw_text.split(',') if d.strip()]

            ConfigHandler.set_no_proxy_domains(domains)
            # Use generic "Saved" message or construct one
            self.show_snack(I18n.get("settings_snack_no_proxy_saved"), color=AppColors.SUCCESS)
            
            # Trigger ProxyManager reload
            # For now just save.
            logger.info(f"No-Proxy domains updated: {domains}")
            
            # Update ProxyManager runtime? 
            # We can try to re-apply if possible, but restart is safer.
            from utils.proxy_manager import ProxyManager
            from utils.thread_pool import ThreadPoolManager, TaskType
            
            # Use standardized ThreadPoolManager instead of ad-hoc asyncio.to_thread
            ThreadPoolManager().submit(TaskType.IO, ProxyManager.apply_smart_proxy_policy)

        except Exception as ex:
            self.show_snack(f"Save failed: {ex}", color=AppColors.ERROR)

    def on_health_check_click(self, e):
        """Run standalone health check reusing the core logic"""
        self.page.run_task(self._run_health_check)

    async def _run_health_check(self):
        from data.data_processor import DataProcessor

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
            self.show_snack(I18n.get("common_check_fail").format(error=e), color=AppColors.ERROR)

    def _show_health_report(self, report):
        """Display the health check report dialog"""
        from ui.components.health_report_dialog import HealthReportDialog
        dlg = HealthReportDialog(self.page, report)
        self.page.open(dlg)
