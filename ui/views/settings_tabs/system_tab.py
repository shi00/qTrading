import asyncio
import logging
from typing import TYPE_CHECKING

import flet as ft

from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
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

        # Theme Selector
        self.theme_dropdown = ft.Dropdown(
            value=ConfigHandler.get_theme_name(),
            width=180,
            text_size=14,
            border_radius=8,
            content_padding=10,
            options=[
                ft.dropdown.Option("dark", I18n.get("theme_dark")),
                ft.dropdown.Option("light", I18n.get("theme_light")),
                ft.dropdown.Option("navy", I18n.get("theme_navy")),
            ],
            on_change=self.on_theme_change
        )

        # Concurrency
        self.concurrency_input = ft.TextField(
            value=str(sync_concurrency),
            width=100,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("sys_suffix_threads"),
            border_radius=8
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
        # --- Instantiate Rows (for theme updates) ---
        
        # 1. Theme Selector
        self.row_theme = SettingRow(
            icon=ft.Icons.COLOR_LENS_ROUNDED,
            icon_color=ft.Colors.PURPLE,
            title=I18n.get("settings_theme"),
            subtitle=I18n.get("settings_snack_theme_updated"),
            control=self.theme_dropdown
        )

        # 2. Log Level Item
        self.row_log = SettingRow(
            icon=ft.Icons.BUG_REPORT_ROUNDED,
            icon_color=AppColors.PRIMARY,
            title=I18n.get("settings_log_level"),
            subtitle=I18n.get("sys_log_label"),
            control=self.log_level_dropdown
        )

        # 3. Concurrency Item
        self.row_concurrency = SettingRow(
            icon=ft.Icons.SPEED_ROUNDED,
            icon_color=AppColors.ACCENT,
            title=I18n.get("sys_concurrency"),
            subtitle=I18n.get("sys_concurrency_hint"),
            control=ft.Row([
                self.concurrency_input,
                ft.IconButton(
                    icon=ft.Icons.SAVE_ROUNDED,
                    icon_color=AppColors.PRIMARY,
                    tooltip=I18n.get("settings_save_config"),
                    on_click=self.save_concurrency
                )
            ], spacing=5)
        )

        # 4. Thread Pool Settings (Advanced)
        self.row_pool = SettingRow(
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
        )

        # 5. DB Buffer Item
        self.row_buffer = SettingRow(
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
        )

        # 6. API Rate Limit Item
        self.row_limit = SettingRow(
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
        )

        # 7. No-Proxy Domains
        self.row_proxy = SettingRow(
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
        )

        self.content = ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column([
                        SectionHeader(I18n.get("sys_core_config")),
                        ft.Container(height=10),

                        self.row_theme,
                        
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),

                        self.row_log,

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        self.row_concurrency,

                        ft.Container(height=10),

                        self.row_pool,

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        self.row_buffer,

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        self.row_limit,

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        self.row_proxy,

                        ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),

                        # 8. Data Maintenance (System Init)
                        ft.Row([
                            ft.Container(
                                content=ft.Icon(ft.Icons.CLEANING_SERVICES_ROUNDED, color=AppColors.ERROR, size=24),
                                padding=10,
                                bgcolor=ft.Colors.with_opacity(0.1, AppColors.ERROR),
                                border_radius=10,
                                data="maint_icon" # Mark for update
                            ),
                            ft.Column([
                                ft.Text(I18n.get("sys_data_maint"), size=16, weight=ft.FontWeight.BOLD,
                                        color=AppColors.TEXT_PRIMARY, data="maint_title"),
                                ft.Text(I18n.get("sys_data_maint_desc"), size=12, color=AppColors.TEXT_SECONDARY, data="maint_desc"),
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
        self.card_main = self.content.controls[0]

    def on_theme_change(self, e):
        """Handle theme change"""
        try:
            theme_name = self.theme_dropdown.value
            ConfigHandler.set_theme_name(theme_name)
            
            from ui.theme import apply_page_theme
            if self.page:
                apply_page_theme(self.page, theme_name)
                self.page.update()
            
            self.show_snack(I18n.get("settings_snack_theme_updated"))
        except Exception as ex:
            logger.error(f"Theme change failed: {ex}")
            self.show_snack(f"Theme Error: {ex}", color=AppColors.ERROR)

    def on_log_level_change(self, e):
        """Handle log level change"""
        level = self.log_level_dropdown.value
        ConfigHandler.set_log_level(level)
        self.show_snack(I18n.get("sys_log_label") + ": " + level)

    def save_concurrency(self, e):
        """Save concurrency setting"""
        try:
            val = int(self.concurrency_input.value)
            if val < 1 or val > 32:
                self.show_snack(I18n.get("sys_snack_io_range"), color=AppColors.ERROR)
                return
            ConfigHandler.set_sync_concurrency(val)
            self.show_snack(I18n.get("sys_concurrency") + " " + I18n.get("common_saved"), color=AppColors.SUCCESS)
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def save_queue_size(self, e):
        """Save DB buffer size"""
        try:
            val = int(self.queue_size_input.value)
            if val < 100 or val > 10000:
                self.show_snack(I18n.get("sys_snack_io_range"), color=AppColors.ERROR)
                return
            ConfigHandler.set_db_queue_size(val)
            self.show_snack(I18n.get("settings_db_buffer") + " " + I18n.get("common_saved"), color=AppColors.SUCCESS)
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def show_init_dialog(self, e):
        """Show system initialization confirmation"""
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("sys_btn_init")),
            content=ft.Text(I18n.get("sys_data_maint_desc")),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=lambda e: self.page.close(dlg)),
                ft.TextButton(I18n.get("common_confirm"), 
                              style=ft.ButtonStyle(color=AppColors.ERROR),
                              on_click=lambda e: self._on_init_confirm(dlg)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.open(dlg)

    def _on_init_confirm(self, dlg):
        self.page.close(dlg)
        self.page.run_task(self._run_system_init)

    async def _run_system_init(self):
        """Run system initialization logic"""
        from data.data_processor import DataProcessor
        
        p_bar = ft.ProgressBar(width=300)
        p_text = ft.Text(I18n.get("common_preparing"))
        dlg_progress = ft.AlertDialog(
            modal=True,
            content=ft.Column([p_text, p_bar], height=70, alignment=ft.MainAxisAlignment.CENTER),
            actions=[],
        )
        self.page.open(dlg_progress)

        async def callback(pct, msg):
            p_bar.value = pct / 100.0
            p_text.value = f"{msg} ({pct:.1f}%)"
            dlg_progress.update()

        try:
            # Step 1: Initialize System Data via DataProcessor
            res = await DataProcessor().initialize_system(progress_callback=callback)
            self.page.close(dlg_progress)
            
            if res:
                self.show_snack(I18n.get("sys_data_maint") + " " + I18n.get("common_completed"), color=AppColors.SUCCESS)
                await self._run_health_check()
            else:
                self.show_snack(I18n.get("common_check_fail"), color=AppColors.ERROR)

        except Exception as ex:
            self.page.close(dlg_progress)
            self.show_snack(str(ex), color=AppColors.ERROR)
            logger.error(f"System init failed: {ex}")

    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""
        inputs = [
            self.theme_dropdown, self.concurrency_input, self.log_level_dropdown, 
            self.queue_size_input, self.io_workers_input, self.cpu_workers_input,
            self.rate_limit_input, self.no_proxy_input
        ]
        for ctrl in inputs:
            if isinstance(ctrl, (ft.TextField, ft.Dropdown)):
                ctrl.bgcolor = AppColors.INPUT_BG
                ctrl.color = AppColors.INPUT_TEXT
                ctrl.border_color = AppColors.INPUT_BORDER

        # Standard colors (text, bg, borders) auto-update via semantic tokens
        if self.page:
            self.update()

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
