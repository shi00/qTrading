import asyncio
import logging
from utils.log_decorators import UILogger

import flet as ft

from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles, ThemeName
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class SystemTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback

        sync_concurrency = ConfigHandler.get_sync_max_concurrent_heavy()

        # --- Controls ---

        # Theme Selector
        self.theme_dropdown = ft.Dropdown(
            label=I18n.get("settings_theme"),
            value=ConfigHandler.get_theme_name(),
            width=AppStyles.CONTROL_WIDTH_MD,
            text_size=14,
            border_radius=8,
            content_padding=10,
            options=[
                ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),
                ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),
                ft.dropdown.Option(ThemeName.NAVY, I18n.get("theme_navy")),
                ft.dropdown.Option(ThemeName.DRACULA, I18n.get("theme_dracula")),
            ],
            on_change=self.on_theme_change,
        )

        # Concurrency
        self.concurrency_input = ft.TextField(
            value=str(sync_concurrency),
            width=AppStyles.CONTROL_WIDTH_SM,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("sys_suffix_threads"),
            border_radius=8,
        )

        # Log Level
        self.log_level_dropdown = ft.Dropdown(
            value=ConfigHandler.get_log_level(),
            width=AppStyles.CONTROL_WIDTH_MD,
            text_size=14,
            border_radius=8,
            content_padding=10,
            options=[
                ft.dropdown.Option("DEBUG", I18n.get("sys_opt_debug")),
                ft.dropdown.Option("INFO", I18n.get("sys_opt_info")),
                ft.dropdown.Option("WARNING", I18n.get("sys_opt_warn")),
                ft.dropdown.Option("ERROR", I18n.get("sys_opt_error")),
            ],
            on_change=self.on_log_level_change,
        )

        # DB Connection Pool Size
        self.pool_size_input = ft.TextField(
            value=str(ConfigHandler.get_db_connection_pool_size()),
            width=AppStyles.CONTROL_WIDTH_SM,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("common_items"),
            border_radius=8,
        )

        # Thread Pool Controls
        self.io_workers_input = ft.TextField(
            value=str(ConfigHandler.get_max_io_workers()),
            width=AppStyles.CONTROL_WIDTH_SM,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("sys_suffix_threads"),
            border_radius=8,
            label=I18n.get("sys_pool_io"),
        )

        self.cpu_workers_input = ft.TextField(
            value=str(ConfigHandler.get_max_cpu_workers()),
            width=AppStyles.CONTROL_WIDTH_SM,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("sys_suffix_threads"),
            border_radius=8,
            label=I18n.get("sys_pool_cpu"),
        )

        # Rate Limit Control
        val = ConfigHandler.get_tushare_api_limit()
        self.rate_limit_input = ft.TextField(
            value=str(val) if val and val > 0 else "",
            width=AppStyles.CONTROL_WIDTH_SM,
            text_size=14,
            content_padding=10,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            suffix_text=I18n.get("common_times_min"),
            border_radius=8,
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
            multiline=False,
        )
        # --- Instantiate Rows (for theme updates) ---

        # 1. Theme Selector
        self.row_theme = SettingRow(
            icon=ft.Icons.COLOR_LENS_ROUNDED,
            icon_color=ft.Colors.PURPLE,
            title=I18n.get("settings_theme"),
            subtitle=I18n.get("settings_snack_theme_updated"),
            control=self.theme_dropdown,
        )

        # 2. Log Level Item
        self.row_log = SettingRow(
            icon=ft.Icons.BUG_REPORT_ROUNDED,
            icon_color=AppColors.PRIMARY,
            title=I18n.get("settings_log_level"),
            subtitle=I18n.get("sys_log_label"),
            control=self.log_level_dropdown,
        )

        # 3. Concurrency Item
        self.row_concurrency = SettingRow(
            icon=ft.Icons.SPEED_ROUNDED,
            icon_color=AppColors.ACCENT,
            title=I18n.get("sys_sync_heavy"),
            subtitle=I18n.get("sys_sync_heavy_hint"),
            control=ft.Row(
                [
                    self.concurrency_input,
                    ft.IconButton(
                        icon=ft.Icons.SAVE_ROUNDED,
                        icon_color=AppColors.PRIMARY,
                        tooltip=I18n.get("settings_save_config"),
                        on_click=self.save_concurrency,
                    ),
                ],
                spacing=5,
            ),
        )

        # 4. Thread Pool Settings (Advanced)
        self.row_thread_pool = SettingRow(
            icon=ft.Icons.MEMORY_ROUNDED,
            icon_color=ft.Colors.INDIGO,
            title=I18n.get("sys_thread_pool_title"),
            subtitle=I18n.get("sys_thread_pool_desc"),
            control=ft.Row(
                [
                    self.io_workers_input,
                    self.cpu_workers_input,
                    ft.IconButton(
                        icon=ft.Icons.SAVE_ROUNDED,
                        icon_color=AppColors.PRIMARY,
                        tooltip=I18n.get("settings_save_config"),
                        on_click=self.save_thread_pool_settings,
                    ),
                ],
                spacing=5,
            ),
        )

        # 5. DB Connection Pool
        self.row_db_pool = SettingRow(
            icon=ft.Icons.STORAGE_ROUNDED,
            icon_color=ft.Colors.ORANGE,
            title=I18n.get("settings_db_pool"),
            subtitle=I18n.get("settings_pool_desc"),
            control=ft.Row(
                [
                    self.pool_size_input,
                    ft.IconButton(
                        icon=ft.Icons.SAVE_ROUNDED,
                        icon_color=AppColors.PRIMARY,
                        tooltip=I18n.get("settings_save_config"),
                        on_click=self.save_pool_size,
                    ),
                ],
                spacing=5,
            ),
        )

        # 6. API Rate Limit Item
        self.row_limit = SettingRow(
            icon=ft.Icons.SPEED,
            icon_color=ft.Colors.CYAN,
            title=I18n.get("sys_tushare_limit"),
            subtitle=I18n.get("sys_tushare_limit_desc"),
            control=ft.Row(
                [
                    self.rate_limit_input,
                    ft.IconButton(
                        icon=ft.Icons.SAVE_ROUNDED,
                        icon_color=AppColors.PRIMARY,
                        tooltip=I18n.get("settings_save_config"),
                        on_click=self.save_rate_limit,
                    ),
                ],
                spacing=5,
            ),
        )

        # 7. No-Proxy Domains
        self.row_proxy = SettingRow(
            icon=ft.Icons.PUBLIC_OFF_ROUNDED,
            icon_color=ft.Colors.TEAL,
            title=I18n.get("settings_no_proxy_domains"),
            subtitle=I18n.get("settings_no_proxy_desc"),
            control=ft.Row(
                [
                    self.no_proxy_input,
                    ft.IconButton(
                        icon=ft.Icons.SAVE_ROUNDED,
                        icon_color=AppColors.PRIMARY,
                        tooltip=I18n.get("common_save"),
                        on_click=self.save_no_proxy_domains,
                    ),
                ],
                spacing=5,
                expand=True,
            ),  # expand=True for input to take space
        )

        self.content = ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column(
                        [
                            SectionHeader(I18n.get("sys_core_config")),
                            ft.Container(height=10),
                            self.row_theme,
                            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                            self.row_log,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            self.row_concurrency,
                            ft.Container(height=10),
                            self.row_thread_pool,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            self.row_db_pool,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            self.row_limit,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            self.row_proxy,
                        ],
                        spacing=10,
                    )
                )
            ],
            padding=ft.padding.only(bottom=50),
        )
        self.card_main = self.content.controls[0]

    def on_theme_change(self, e):
        """Handle theme change"""
        UILogger.log_action("SystemTab", "Select", f"theme={self.theme_dropdown.value}")
        try:
            theme_name = self.theme_dropdown.value
            ConfigHandler.set_theme_name(theme_name)

            from ui.theme import apply_page_theme

            if self.page:
                apply_page_theme(self.page, theme_name)
                self.page.update()

            self.show_snack(I18n.get("settings_snack_theme_updated"))
        except Exception as ex:
            logger.error(f"[SystemTab] Theme | ❌ Change failed: {ex}", exc_info=True)
            self.show_snack(f"Theme Error: {ex}", color=AppColors.ERROR)

    def on_log_level_change(self, e):
        """Handle log level change"""
        UILogger.log_action(
            "SystemTab", "Select", f"log_level={self.log_level_dropdown.value}"
        )
        level = self.log_level_dropdown.value
        ConfigHandler.set_log_level(level)
        from utils.logger import update_log_level
        update_log_level(level)
        self.show_snack(I18n.get("sys_log_label") + ": " + level)

    def save_concurrency(self, e):
        """Save concurrency setting"""
        try:
            val = int(self.concurrency_input.value)
            if val < 1 or val > 32:
                self.show_snack(
                    I18n.get("sys_snack_concurrency_range"), color=AppColors.ERROR
                )
                return
            ConfigHandler.set_sync_max_concurrent_heavy(val)
            self.show_snack(
                I18n.get("sys_sync_heavy") + " " + I18n.get("common_saved"),
                color=AppColors.SUCCESS,
            )
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def save_pool_size(self, e):
        """Save DB connection pool size"""
        try:
            val = int(self.pool_size_input.value)
            if val < 1 or val > 50:
                self.show_snack(I18n.get("sys_snack_pool_range"), color=AppColors.ERROR)
                return
            ConfigHandler.set_db_connection_pool_size(val)
            self.show_snack(
                I18n.get("settings_db_pool") + " " + I18n.get("common_saved"),
                color=AppColors.SUCCESS,
            )
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""
        inputs = [
            self.theme_dropdown,
            self.concurrency_input,
            self.log_level_dropdown,
            self.pool_size_input,
            self.io_workers_input,
            self.cpu_workers_input,
            self.rate_limit_input,
            self.no_proxy_input,
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
        """Async handler to avoid blocking UI during reload."""
        from utils.thread_pool import ThreadPoolManager

        try:
            io_str = self.io_workers_input.value.strip()
            cpu_str = self.cpu_workers_input.value.strip()

            if not io_str or not cpu_str:
                self.show_snack(
                    I18n.get("sys_snack_threads_empty"), color=AppColors.ERROR
                )
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

            self.show_snack(I18n.get("common_preparing"))

            # Trigger Reload in thread to avoid UI freeze
            await asyncio.to_thread(ThreadPoolManager().reload_config)

            self.show_snack(I18n.get("sys_snack_pool_saved"), color=AppColors.SUCCESS)
            logger.info(f"Updated ThreadPool: IO={io_val}, CPU={cpu_val}")

        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            self.show_snack(
                I18n.get("sys_snack_save_err").format(error=str(ex)),
                color=AppColors.ERROR,
            )
            logger.error(
                f"[SystemTab] ThreadPool | ❌ Save failed: {ex}", exc_info=True
            )

    def save_rate_limit(self, e):
        """Save API rate limit setting."""
        try:
            limit_str = self.rate_limit_input.value.strip()

            if not limit_str:
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
        """Save no-proxy domain list."""
        try:
            raw_text = self.no_proxy_input.value
            if not raw_text:
                domains = []
            else:
                domains = [d.strip() for d in raw_text.split(",") if d.strip()]

            ConfigHandler.set_no_proxy_domains(domains)
            self.show_snack(
                I18n.get("settings_snack_no_proxy_saved"), color=AppColors.SUCCESS
            )
            logger.info(f"No-Proxy domains updated: {domains}")

            from utils.proxy_manager import ProxyManager
            from utils.thread_pool import ThreadPoolManager, TaskType

            ThreadPoolManager().submit(
                TaskType.IO, ProxyManager.apply_smart_proxy_policy
            )

        except Exception as ex:
            self.show_snack(f"Save failed: {ex}", color=AppColors.ERROR)
