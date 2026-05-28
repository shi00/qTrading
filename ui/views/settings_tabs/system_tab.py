import asyncio
import logging

import flet as ft

from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles, ThemeName
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)


class SystemTab(ft.Container):
    def __init__(self, show_snack_callback):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.show_snack = show_snack_callback  # pragma: no cover

        sync_concurrency = ConfigHandler.get_sync_max_concurrent_heavy()  # pragma: no cover

        # --- Controls ---  # pragma: no cover

        # Language Selector  # pragma: no cover
        self.language_dropdown = ft.Dropdown(  # pragma: no cover
            label=I18n.get("settings_language"),  # pragma: no cover
            value=ConfigHandler.get_locale(),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_MD,  # pragma: no cover
            text_size=14,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option("zh_CN", "简体中文"),  # pragma: no cover
                ft.dropdown.Option("en_US", "English"),  # pragma: no cover
            ],  # pragma: no cover
            on_change=self.on_language_change,  # pragma: no cover
        )  # pragma: no cover

        # Theme Selector  # pragma: no cover
        self.theme_dropdown = ft.Dropdown(  # pragma: no cover
            label=I18n.get("settings_theme"),  # pragma: no cover
            value=ConfigHandler.get_theme_name(),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_MD,  # pragma: no cover
            text_size=14,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),  # pragma: no cover
                ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),  # pragma: no cover
                ft.dropdown.Option(ThemeName.NAVY, I18n.get("theme_navy")),  # pragma: no cover
                ft.dropdown.Option(ThemeName.DRACULA, I18n.get("theme_dracula")),  # pragma: no cover
            ],  # pragma: no cover
            on_change=self.on_theme_change,  # pragma: no cover
        )  # pragma: no cover

        # Concurrency  # pragma: no cover
        self.concurrency_input = ft.TextField(  # pragma: no cover
            value=str(sync_concurrency),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("sys_suffix_threads"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
        )  # pragma: no cover

        # Log Level  # pragma: no cover
        self.log_level_dropdown = ft.Dropdown(  # pragma: no cover
            value=ConfigHandler.get_log_level(),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_MD,  # pragma: no cover
            text_size=14,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option("DEBUG", I18n.get("sys_opt_debug")),  # pragma: no cover
                ft.dropdown.Option("INFO", I18n.get("sys_opt_info")),  # pragma: no cover
                ft.dropdown.Option("WARNING", I18n.get("sys_opt_warn")),  # pragma: no cover
                ft.dropdown.Option("ERROR", I18n.get("sys_opt_error")),  # pragma: no cover
            ],  # pragma: no cover
            on_change=self.on_log_level_change,  # pragma: no cover
        )  # pragma: no cover

        # DB Connection Pool Size  # pragma: no cover
        self.pool_size_input = ft.TextField(  # pragma: no cover
            value=str(ConfigHandler.get_db_connection_pool_size()),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("common_items"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            label=I18n.get("settings_db_pool"),  # pragma: no cover
        )  # pragma: no cover

        # DB Max Overflow  # pragma: no cover
        self.db_overflow_input = ft.TextField(  # pragma: no cover
            value=str(ConfigHandler.get_db_max_overflow()),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("common_items"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            label=I18n.get("settings_db_overflow"),  # pragma: no cover
        )  # pragma: no cover

        # DB Pool Timeout  # pragma: no cover
        self.db_timeout_input = ft.TextField(  # pragma: no cover
            value=str(ConfigHandler.get_db_pool_timeout()),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("common_seconds"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            label=I18n.get("settings_db_timeout"),  # pragma: no cover
        )  # pragma: no cover

        # Thread Pool Controls  # pragma: no cover
        self.io_workers_input = ft.TextField(  # pragma: no cover
            value=str(ConfigHandler.get_max_io_workers()),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("sys_suffix_threads"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            label=I18n.get("sys_pool_io"),  # pragma: no cover
        )  # pragma: no cover

        self.cpu_workers_input = ft.TextField(  # pragma: no cover
            value=str(ConfigHandler.get_max_cpu_workers()),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("sys_suffix_threads"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            label=I18n.get("sys_pool_cpu"),  # pragma: no cover
        )  # pragma: no cover

        # Rate Limit Control
        val = ConfigHandler.get_tushare_api_limit()  # pragma: no cover
        self.rate_limit_input = ft.TextField(  # pragma: no cover
            value=str(val) if val and val > 0 else "",  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix_text=I18n.get("common_times_min"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
        )  # pragma: no cover

        # No-Proxy Domains
        domains_list = ConfigHandler.get_no_proxy_domains()  # pragma: no cover
        self.no_proxy_input = ft.TextField(  # pragma: no cover
            value=",".join(domains_list),  # pragma: no cover
            expand=True,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            hint_text=I18n.get("settings_no_proxy_hint"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            multiline=False,  # pragma: no cover
        )  # pragma: no cover
        # --- Instantiate Rows (for theme updates) ---  # pragma: no cover

        # 0. Language Selector  # pragma: no cover
        self.row_language = SettingRow(  # pragma: no cover
            icon=ft.Icons.LANGUAGE_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.BLUE,  # pragma: no cover
            title=I18n.get("settings_language"),  # pragma: no cover
            subtitle=I18n.get("settings_language_desc"),  # pragma: no cover
            control=self.language_dropdown,  # pragma: no cover
        )  # pragma: no cover

        # 1. Theme Selector  # pragma: no cover
        self.row_theme = SettingRow(  # pragma: no cover
            icon=ft.Icons.COLOR_LENS_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.PURPLE,  # pragma: no cover
            title=I18n.get("settings_theme"),  # pragma: no cover
            subtitle=I18n.get("settings_snack_theme_updated"),  # pragma: no cover
            control=self.theme_dropdown,  # pragma: no cover
        )  # pragma: no cover

        # 2. Log Level Item  # pragma: no cover
        self.row_log = SettingRow(  # pragma: no cover
            icon=ft.Icons.BUG_REPORT_ROUNDED,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            title=I18n.get("settings_log_level"),  # pragma: no cover
            subtitle=I18n.get("sys_log_label"),  # pragma: no cover
            control=self.log_level_dropdown,  # pragma: no cover
        )  # pragma: no cover

        # 3. Concurrency Item  # pragma: no cover
        self.row_concurrency = SettingRow(  # pragma: no cover
            icon=ft.Icons.SPEED_ROUNDED,  # pragma: no cover
            icon_color=AppColors.ACCENT,  # pragma: no cover
            title=I18n.get("sys_sync_heavy"),  # pragma: no cover
            subtitle=I18n.get("sys_sync_heavy_hint"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.concurrency_input,  # pragma: no cover
                    ft.IconButton(  # pragma: no cover
                        icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
                        icon_color=AppColors.PRIMARY,  # pragma: no cover
                        tooltip=I18n.get("settings_save_config"),  # pragma: no cover
                        on_click=self.save_concurrency,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 4. Thread Pool Settings (Advanced)  # pragma: no cover
        self.row_thread_pool = SettingRow(  # pragma: no cover
            icon=ft.Icons.MEMORY_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.INDIGO,  # pragma: no cover
            title=I18n.get("sys_thread_pool_title"),  # pragma: no cover
            subtitle=I18n.get("sys_thread_pool_desc"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.io_workers_input,  # pragma: no cover
                    self.cpu_workers_input,  # pragma: no cover
                    ft.IconButton(  # pragma: no cover
                        icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
                        icon_color=AppColors.PRIMARY,  # pragma: no cover
                        tooltip=I18n.get("settings_save_config"),  # pragma: no cover
                        on_click=lambda e: (
                            self.page.run_task(self.save_thread_pool_settings, e) if self.page else None
                        ),  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 5. DB Connection Pool  # pragma: no cover
        self.row_db_pool = SettingRow(  # pragma: no cover
            icon=ft.Icons.STORAGE_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.ORANGE,  # pragma: no cover
            title=I18n.get("settings_db_pool"),  # pragma: no cover
            subtitle=I18n.get("settings_pool_desc"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.pool_size_input,  # pragma: no cover
                    self.db_overflow_input,  # pragma: no cover
                    self.db_timeout_input,  # pragma: no cover
                    ft.IconButton(  # pragma: no cover
                        icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
                        icon_color=AppColors.PRIMARY,  # pragma: no cover
                        tooltip=I18n.get("settings_save_config"),  # pragma: no cover
                        on_click=self.save_db_pool_settings,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 6. API Rate Limit Item  # pragma: no cover
        self.row_limit = SettingRow(  # pragma: no cover
            icon=ft.Icons.SPEED,  # pragma: no cover
            icon_color=ft.Colors.CYAN,  # pragma: no cover
            title=I18n.get("sys_tushare_limit"),  # pragma: no cover
            subtitle=I18n.get("sys_tushare_limit_desc"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.rate_limit_input,  # pragma: no cover
                    ft.IconButton(  # pragma: no cover
                        icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
                        icon_color=AppColors.PRIMARY,  # pragma: no cover
                        tooltip=I18n.get("settings_save_config"),  # pragma: no cover
                        on_click=self.save_rate_limit,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 7. No-Proxy Domains  # pragma: no cover
        self.row_proxy = SettingRow(  # pragma: no cover
            icon=ft.Icons.PUBLIC_OFF_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.TEAL,  # pragma: no cover
            title=I18n.get("settings_no_proxy_domains"),  # pragma: no cover
            subtitle=I18n.get("settings_no_proxy_desc"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.no_proxy_input,  # pragma: no cover
                    ft.IconButton(  # pragma: no cover
                        icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
                        icon_color=AppColors.PRIMARY,  # pragma: no cover
                        tooltip=I18n.get("common_save"),  # pragma: no cover
                        on_click=self.save_no_proxy_domains,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
                expand=True,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.content = ft.ListView(  # pragma: no cover
            controls=[  # pragma: no cover
                DashboardCard(  # pragma: no cover
                    content=ft.Column(  # pragma: no cover
                        [  # pragma: no cover
                            SectionHeader(I18n.get("sys_core_config")),  # pragma: no cover
                            ft.Container(height=10),  # pragma: no cover
                            self.row_language,  # pragma: no cover
                            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),  # pragma: no cover
                            self.row_theme,  # pragma: no cover
                            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),  # pragma: no cover
                            self.row_log,  # pragma: no cover
                            ft.Divider(  # pragma: no cover
                                height=20,  # pragma: no cover
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),  # pragma: no cover
                            ),  # pragma: no cover
                            self.row_concurrency,  # pragma: no cover
                            ft.Container(height=10),  # pragma: no cover
                            self.row_thread_pool,  # pragma: no cover
                            ft.Divider(  # pragma: no cover
                                height=20,  # pragma: no cover
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),  # pragma: no cover
                            ),  # pragma: no cover
                            self.row_db_pool,  # pragma: no cover
                            ft.Divider(  # pragma: no cover
                                height=20,  # pragma: no cover
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),  # pragma: no cover
                            ),  # pragma: no cover
                            self.row_limit,  # pragma: no cover
                            ft.Divider(  # pragma: no cover
                                height=20,  # pragma: no cover
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),  # pragma: no cover
                            ),  # pragma: no cover
                            self.row_proxy,  # pragma: no cover
                        ],  # pragma: no cover
                        spacing=10,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            padding=ft.padding.only(bottom=50),  # pragma: no cover
        )  # pragma: no cover
        self.card_main = self.content.controls[0]  # pragma: no cover
        self._locale_subscription_id = None  # pragma: no cover

    def on_language_change(self, e):  # pragma: no cover
        """Handle language change"""
        UILogger.log_action("SystemTab", "Select", f"language={self.language_dropdown.value}")
        try:
            new_locale = self.language_dropdown.value
            I18n.set_locale(new_locale)
            self.show_snack(I18n.get("settings_language_changed"))
        except Exception as ex:
            logger.error(f"[SystemTab] Language | ❌ Change failed: {ex}", exc_info=True)
            self.show_snack(f"Error: {ex}", color=AppColors.ERROR)

    def _on_locale_change(self, new_locale: str = None):  # pragma: no cover
        """语言变更回调 - 更新 Settings UI 文本"""
        try:
            self.language_dropdown.label = I18n.get("settings_language")
            self.theme_dropdown.label = I18n.get("settings_theme")
            self.log_level_dropdown.label = I18n.get("settings_log_level")

            self.theme_dropdown.options = [
                ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),
                ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),
                ft.dropdown.Option(ThemeName.NAVY, I18n.get("theme_navy")),
                ft.dropdown.Option(ThemeName.DRACULA, I18n.get("theme_dracula")),
            ]

            self.log_level_dropdown.options = [
                ft.dropdown.Option("DEBUG", I18n.get("sys_opt_debug")),
                ft.dropdown.Option("INFO", I18n.get("sys_opt_info")),
                ft.dropdown.Option("WARNING", I18n.get("sys_opt_warn")),
                ft.dropdown.Option("ERROR", I18n.get("sys_opt_error")),
            ]

            self._safe_update()
        except Exception as e:
            logger.warning(f"[SystemTab] Locale update failed: {e}")

    def _safe_update(self):  # pragma: no cover
        """线程安全的 UI 更新"""
        try:
            if self.page:
                self.page.update()
        except Exception as e:
            logger.debug(f"[SystemTab] Update skipped: {e}")

    def did_mount(self):  # pragma: no cover
        """挂载时订阅语言变更"""
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)

    def will_unmount(self):  # pragma: no cover
        """卸载时取消订阅"""
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def on_theme_change(self, e):
        """Handle theme change"""
        UILogger.log_action("SystemTab", "Select", f"theme={self.theme_dropdown.value}")
        try:
            theme_name = self.theme_dropdown.value
            ConfigHandler.set_theme_name(theme_name)

            from ui.theme import apply_page_theme

            if self.page:
                apply_page_theme(self.page, theme_name)  # type: ignore[untyped]
                self.page.update()

            self.show_snack(I18n.get("settings_snack_theme_updated"))
        except Exception as ex:
            logger.error(f"[SystemTab] Theme | ❌ Change failed: {ex}", exc_info=True)
            self.show_snack(f"Theme Error: {ex}", color=AppColors.ERROR)

    def on_log_level_change(self, e):
        """Handle log level change"""
        UILogger.log_action(
            "SystemTab",
            "Select",
            f"log_level={self.log_level_dropdown.value}",
        )
        level = self.log_level_dropdown.value
        ConfigHandler.set_log_level(level)
        from utils.logger import update_log_level

        update_log_level(level)
        self.show_snack(I18n.get("sys_log_label") + ": " + level)  # type: ignore[untyped]

    def save_concurrency(self, e):
        """Save concurrency setting"""
        try:
            val = int(self.concurrency_input.value)  # type: ignore[untyped]
            if val < 1 or val > 32:
                self.show_snack(
                    I18n.get("sys_snack_concurrency_range"),
                    color=AppColors.ERROR,
                )
                return
            ConfigHandler.set_sync_max_concurrent_heavy(val)
            self.show_snack(
                I18n.get("sys_sync_heavy") + " " + I18n.get("common_saved"),
                color=AppColors.SUCCESS,
            )
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def save_db_pool_settings(self, e):
        """Save DB connection pool settings (pool_size, max_overflow, timeout)"""
        try:
            pool_size = int(self.pool_size_input.value)  # type: ignore[untyped]
            max_overflow = int(self.db_overflow_input.value)  # type: ignore[untyped]
            timeout = int(self.db_timeout_input.value)  # type: ignore[untyped]
            if pool_size < 1 or pool_size > 50:
                self.show_snack(I18n.get("sys_snack_pool_range"), color=AppColors.ERROR)
                return
            if max_overflow < 0 or max_overflow > 50:
                self.show_snack(
                    I18n.get("settings_db_overflow") + ": 0-50",
                    color=AppColors.ERROR,
                )
                return
            if timeout < 5 or timeout > 300:
                self.show_snack(
                    I18n.get("settings_db_timeout") + ": 5-300",
                    color=AppColors.ERROR,
                )
                return

            ConfigHandler.set_db_connection_pool_size(pool_size)
            ConfigHandler.set_db_max_overflow(max_overflow)
            ConfigHandler.set_db_pool_timeout(timeout)

            self.show_snack(
                I18n.get("settings_db_pool_saved"),
                color=AppColors.SUCCESS,
            )
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)

    def update_theme(self):  # pragma: no cover
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""  # pragma: no cover
        inputs = [  # pragma: no cover
            self.theme_dropdown,  # pragma: no cover
            self.concurrency_input,  # pragma: no cover
            self.log_level_dropdown,  # pragma: no cover
            self.pool_size_input,  # pragma: no cover
            self.db_overflow_input,  # pragma: no cover
            self.db_timeout_input,  # pragma: no cover
            self.io_workers_input,  # pragma: no cover
            self.cpu_workers_input,  # pragma: no cover
            self.rate_limit_input,  # pragma: no cover
            self.no_proxy_input,  # pragma: no cover
        ]  # pragma: no cover
        for ctrl in inputs:  # pragma: no cover
            if isinstance(ctrl, (ft.TextField, ft.Dropdown)):  # pragma: no cover
                ctrl.bgcolor = AppColors.INPUT_BG  # pragma: no cover
                ctrl.color = AppColors.INPUT_TEXT  # pragma: no cover
                ctrl.border_color = AppColors.INPUT_BORDER  # pragma: no cover

        # Standard colors (text, bg, borders) auto-update via semantic tokens  # pragma: no cover
        if self.page:  # pragma: no cover
            self.update()  # pragma: no cover

    async def save_thread_pool_settings(self, e):
        """Async handler to avoid blocking UI during reload."""
        from utils.thread_pool import ThreadPoolManager

        try:
            io_str = self.io_workers_input.value.strip()  # type: ignore[untyped]
            cpu_str = self.cpu_workers_input.value.strip()  # type: ignore[untyped]
            if not io_str or not cpu_str:
                self.show_snack(
                    I18n.get("sys_snack_threads_empty"),
                    color=AppColors.ERROR,
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
                f"[SystemTab] ThreadPool | ❌ Save failed: {ex}",
                exc_info=True,
            )

    def save_rate_limit(self, e):
        """Save API rate limit setting."""
        try:
            limit_str = self.rate_limit_input.value.strip()  # type: ignore[untyped]
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
                I18n.get("settings_snack_no_proxy_saved"),
                color=AppColors.SUCCESS,
            )
            logger.info(f"No-Proxy domains updated: {domains}")

            from utils.proxy_manager import ProxyManager
            from utils.thread_pool import TaskType, ThreadPoolManager

            ThreadPoolManager().submit(
                TaskType.IO,
                ProxyManager.reapply_proxy_policy,
            )

        except Exception as ex:
            self.show_snack(f"Save failed: {ex}", color=AppColors.ERROR)
