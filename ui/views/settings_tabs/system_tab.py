import asyncio
import logging

import flet as ft

from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
from ui.i18n import I18n, refresh_dropdown_options
from ui.theme import AppColors, AppStyles, ThemeName
from ui.viewmodels.system_viewmodel import SystemViewModel
from ui.views.settings_tabs.tier_api_panel import TierApiPanel
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class SystemTab(ft.Container):
    def __init__(self, show_snack_callback):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.show_snack = show_snack_callback  # pragma: no cover

        sync_concurrency = ConfigHandler.get_sync_max_concurrent_heavy()  # pragma: no cover

        # --- Controls ---  # pragma: no cover

        self.section_header = SectionHeader(
            I18n.get("sys_core_config"), title_key="sys_core_config"
        )  # pragma: no cover

        # Language Selector  # pragma: no cover
        self.language_dropdown = ft.Dropdown(  # pragma: no cover
            label=I18n.get_language_label(),  # pragma: no cover
            value=ConfigHandler.get_locale(),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_MD,  # pragma: no cover
            text_size=14,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option(code, name)  # pragma: no cover
                for code, name in I18n.get_language_options()  # pragma: no cover
            ],  # pragma: no cover
            on_select=self.on_language_change,  # pragma: no cover
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
            on_select=self.on_theme_change,  # pragma: no cover
        )  # pragma: no cover

        # Concurrency  # pragma: no cover
        self.concurrency_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_concurrency"),  # pragma: no cover
            value=str(sync_concurrency),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix=I18n.get("sys_suffix_threads"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
        )  # pragma: no cover

        # Log Level  # pragma: no cover
        self.log_level_dropdown = ft.Dropdown(  # pragma: no cover
            label=I18n.get("settings_log_level"),  # pragma: no cover
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
            on_select=self.on_log_level_change,  # pragma: no cover
        )  # pragma: no cover

        # DB Connection Pool Size  # pragma: no cover
        self.pool_size_input = ft.TextField(  # pragma: no cover
            value=str(ConfigHandler.get_db_connection_pool_size()),  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_SM,  # pragma: no cover
            text_size=14,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),  # pragma: no cover
            suffix=I18n.get("common_items"),  # pragma: no cover
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
            suffix=I18n.get("common_items"),  # pragma: no cover
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
            suffix=I18n.get("common_seconds"),  # pragma: no cover
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
            suffix=I18n.get("sys_suffix_threads"),  # pragma: no cover
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
            suffix=I18n.get("sys_suffix_threads"),  # pragma: no cover
            border_radius=8,  # pragma: no cover
            label=I18n.get("sys_pool_cpu"),  # pragma: no cover
        )  # pragma: no cover

        # Phase 2A.1 §3.2.10: SystemViewModel 单一实例 + TierApiPanel 注入（v1.6.0 P1-10）
        # 半迁移：档位/probe 走 VM（含异步 probe 长任务 + UI 状态管理），其他配置仍直调 ConfigHandler
        # 档位下拉框由 TierApiPanel 自身承担（避免 UI 重复），system_tab 不再独立维护 point_tier_dropdown
        self.system_vm = SystemViewModel()  # pragma: no cover
        self.tier_api_panel = TierApiPanel(viewmodel=self.system_vm)  # pragma: no cover

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

        # System Diagnostics Button  # pragma: no cover
        self.diagnostics_button = ft.Button(  # pragma: no cover
            content=I18n.get("settings_diagnostics_btn"),  # pragma: no cover
            icon=ft.Icons.DOWNLOAD_ROUNDED,  # pragma: no cover
            on_click=lambda e: (
                self.page.run_task(self.on_export_diagnostics) if self.page else None
            ),  # pragma: no cover
            style=ft.ButtonStyle(  # pragma: no cover
                shape=ft.RoundedRectangleBorder(radius=8),  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # --- Instantiate Rows (for theme updates) ---  # pragma: no cover

        # 0. Language Selector  # pragma: no cover
        self.row_language = SettingRow(  # pragma: no cover
            icon=ft.Icons.LANGUAGE_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.BLUE,  # pragma: no cover
            title=I18n.get("settings_language"),  # pragma: no cover
            subtitle=I18n.get("settings_language_desc"),  # pragma: no cover
            control=self.language_dropdown,  # pragma: no cover
            title_key="settings_language",  # pragma: no cover
            subtitle_key="settings_language_desc",  # pragma: no cover
        )  # pragma: no cover

        # 1. Theme Selector  # pragma: no cover
        self.row_theme = SettingRow(  # pragma: no cover
            icon=ft.Icons.COLOR_LENS_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.PURPLE,  # pragma: no cover
            title=I18n.get("settings_theme"),  # pragma: no cover
            subtitle=I18n.get("settings_snack_theme_updated"),  # pragma: no cover
            control=self.theme_dropdown,  # pragma: no cover
            title_key="settings_theme",  # pragma: no cover
            subtitle_key="settings_snack_theme_updated",  # pragma: no cover
        )  # pragma: no cover

        # 2. Log Level Item  # pragma: no cover
        self.row_log = SettingRow(  # pragma: no cover
            icon=ft.Icons.BUG_REPORT_ROUNDED,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            title=I18n.get("settings_log_level"),  # pragma: no cover
            subtitle=I18n.get("sys_log_label"),  # pragma: no cover
            control=self.log_level_dropdown,  # pragma: no cover
            title_key="settings_log_level",  # pragma: no cover
            subtitle_key="sys_log_label",  # pragma: no cover
        )  # pragma: no cover

        # 3. Concurrency Item  # pragma: no cover
        self.save_concurrency_btn = ft.IconButton(  # pragma: no cover
            icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            tooltip=I18n.get("settings_save_config"),  # pragma: no cover
            on_click=self.save_concurrency,  # pragma: no cover
        )  # pragma: no cover
        self.row_concurrency = SettingRow(  # pragma: no cover
            icon=ft.Icons.SPEED_ROUNDED,  # pragma: no cover
            icon_color=AppColors.ACCENT,  # pragma: no cover
            title=I18n.get("sys_sync_heavy"),  # pragma: no cover
            subtitle=I18n.get("sys_sync_heavy_hint"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.concurrency_input,  # pragma: no cover
                    self.save_concurrency_btn,  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
            ),  # pragma: no cover
            title_key="sys_sync_heavy",  # pragma: no cover
            subtitle_key="sys_sync_heavy_hint",  # pragma: no cover
        )  # pragma: no cover

        # 4. Thread Pool Settings (Advanced)  # pragma: no cover
        self.save_thread_pool_btn = ft.IconButton(  # pragma: no cover
            icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            tooltip=I18n.get("settings_save_config"),  # pragma: no cover
            on_click=lambda e: (
                self.page.run_task(self.save_thread_pool_settings, e) if self.page else None
            ),  # pragma: no cover
        )  # pragma: no cover
        self.row_thread_pool = SettingRow(  # pragma: no cover
            icon=ft.Icons.MEMORY_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.INDIGO,  # pragma: no cover
            title=I18n.get("sys_thread_pool_title"),  # pragma: no cover
            subtitle=I18n.get("sys_thread_pool_desc"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.io_workers_input,  # pragma: no cover
                    self.cpu_workers_input,  # pragma: no cover
                    self.save_thread_pool_btn,  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
                wrap=True,  # pragma: no cover
            ),  # pragma: no cover
            title_key="sys_thread_pool_title",  # pragma: no cover
            subtitle_key="sys_thread_pool_desc",  # pragma: no cover
        )  # pragma: no cover

        # 5. DB Connection Pool  # pragma: no cover
        self.save_db_pool_btn = ft.IconButton(  # pragma: no cover
            icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            tooltip=I18n.get("settings_save_config"),  # pragma: no cover
            on_click=self.save_db_pool_settings,  # pragma: no cover
        )  # pragma: no cover
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
                    self.save_db_pool_btn,  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
                wrap=True,  # pragma: no cover
            ),  # pragma: no cover
            title_key="settings_db_pool",  # pragma: no cover
            subtitle_key="settings_pool_desc",  # pragma: no cover
        )  # pragma: no cover

        # 6. API Rate Limit Item — 由 TierApiPanel 承担（含档位下拉框 + probe 状态 + 触发按钮）
        # Phase 2A.1 §3.2.10：取代旧 row_limit（独立 point_tier_dropdown 已移除，避免 UI 重复）
        # TierApiPanel 自身订阅 I18n 变更，SystemTab._on_locale_change 不级联刷新它

        # 7. No-Proxy Domains  # pragma: no cover
        self.save_no_proxy_btn = ft.IconButton(  # pragma: no cover
            icon=ft.Icons.SAVE_ROUNDED,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            tooltip=I18n.get("common_save"),  # pragma: no cover
            on_click=self.save_no_proxy_domains,  # pragma: no cover
        )  # pragma: no cover
        self.row_proxy = SettingRow(  # pragma: no cover
            icon=ft.Icons.PUBLIC_OFF_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.TEAL,  # pragma: no cover
            title=I18n.get("settings_no_proxy_domains"),  # pragma: no cover
            subtitle=I18n.get("settings_no_proxy_desc"),  # pragma: no cover
            control=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.no_proxy_input,  # pragma: no cover
                    self.save_no_proxy_btn,  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
                expand=True,  # pragma: no cover
            ),  # pragma: no cover
            title_key="settings_no_proxy_domains",  # pragma: no cover
            subtitle_key="settings_no_proxy_desc",  # pragma: no cover
        )  # pragma: no cover

        # 8. System Diagnostics  # pragma: no cover
        self.row_diagnostics = SettingRow(  # pragma: no cover
            icon=ft.Icons.ANALYTICS_ROUNDED,  # pragma: no cover
            icon_color=ft.Colors.RED,  # pragma: no cover
            title=I18n.get("settings_diagnostics"),  # pragma: no cover
            subtitle=I18n.get("settings_diagnostics_desc"),  # pragma: no cover
            control=self.diagnostics_button,  # pragma: no cover
            title_key="settings_diagnostics",  # pragma: no cover
            subtitle_key="settings_diagnostics_desc",  # pragma: no cover
        )  # pragma: no cover

        self.content = ft.ListView(  # pragma: no cover
            controls=[  # pragma: no cover
                DashboardCard(  # pragma: no cover
                    content=ft.Column(  # pragma: no cover
                        [  # pragma: no cover
                            self.section_header,  # pragma: no cover
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
                            self.tier_api_panel,  # pragma: no cover
                            ft.Divider(  # pragma: no cover
                                height=20,  # pragma: no cover
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),  # pragma: no cover
                            ),  # pragma: no cover
                            self.row_proxy,  # pragma: no cover
                            ft.Divider(  # pragma: no cover
                                height=20,  # pragma: no cover
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),  # pragma: no cover
                            ),  # pragma: no cover
                            self.row_diagnostics,  # pragma: no cover
                        ],  # pragma: no cover
                        spacing=10,  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            padding=ft.Padding.only(bottom=50),  # pragma: no cover
        )  # pragma: no cover
        self.card_main = self.content.controls[0]  # pragma: no cover
        self._locale_subscription_id = None  # pragma: no cover

    def on_language_change(self, e):  # pragma: no cover
        """Handle language change"""
        UILogger.log_action("SystemTab", "Select", f"language={self.language_dropdown.value}")
        if self.page:  # pragma: no cover
            self.page.run_task(self._do_language_change_async)

    async def _do_language_change_async(self):
        try:
            new_locale = self.language_dropdown.value
            if new_locale:
                success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_locale, new_locale)
                if not success:
                    self.language_dropdown.value = I18n.current_locale()
                    self.show_snack(I18n.get("settings_language_save_failed"), color=AppColors.ERROR)
                    self._safe_update()
                    return
                I18n.set_locale(new_locale)
                if self.page and getattr(self.page, "locale_configuration", None):
                    try:
                        normalized = I18n.current_locale()
                        parts = normalized.split("_")
                        lang = parts[0]
                        country = parts[1] if len(parts) > 1 else None
                        self.page.locale_configuration.current_locale = ft.Locale(lang, country)
                        self.page.update()
                    except Exception as ex:
                        logger.debug("[SystemTab] Failed to update page locale configuration: %s", ex, exc_info=True)
                self.show_snack(I18n.get("settings_language_changed"))
        except Exception as ex:
            logger.error("[SystemTab] Language | Change failed: %s", DataSanitizer.sanitize_error(ex))
            logger.debug("[SystemTab] Language | Change failed traceback", exc_info=True)
            self.show_snack(DataSanitizer.sanitize_error(ex), color=AppColors.ERROR)

    def _on_locale_change(self, new_locale: str | None = None):
        """语言变更回调 - 更新 Settings UI 文本

        Args:
            new_locale: 可选 locale 代码（用于测试显式注入）；I18n.subscribe
                调用时不会传参，依赖默认值 None。
        """
        try:
            self.language_dropdown.label = I18n.get_language_label()
            self.language_dropdown.tooltip = I18n.get_language_label()
            refresh_dropdown_options(
                self.language_dropdown,
                [ft.dropdown.Option(code, name) for code, name in I18n.get_language_options()],
            )
            self.theme_dropdown.label = I18n.get("settings_theme")
            self.log_level_dropdown.label = I18n.get("settings_log_level")

            refresh_dropdown_options(
                self.theme_dropdown,
                [
                    ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),
                    ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),
                    ft.dropdown.Option(ThemeName.NAVY, I18n.get("theme_navy")),
                    ft.dropdown.Option(ThemeName.DRACULA, I18n.get("theme_dracula")),
                ],
            )

            refresh_dropdown_options(
                self.log_level_dropdown,
                [
                    ft.dropdown.Option("DEBUG", I18n.get("sys_opt_debug")),
                    ft.dropdown.Option("INFO", I18n.get("sys_opt_info")),
                    ft.dropdown.Option("WARNING", I18n.get("sys_opt_warn")),
                    ft.dropdown.Option("ERROR", I18n.get("sys_opt_error")),
                ],
            )

            self.concurrency_input.label = I18n.get("settings_concurrency")
            self.concurrency_input.suffix = I18n.get("sys_suffix_threads")
            self.pool_size_input.label = I18n.get("settings_db_pool")
            self.pool_size_input.suffix = I18n.get("common_items")
            self.db_overflow_input.label = I18n.get("settings_db_overflow")
            self.db_overflow_input.suffix = I18n.get("common_items")
            self.db_timeout_input.label = I18n.get("settings_db_timeout")
            self.db_timeout_input.suffix = I18n.get("common_seconds")
            self.io_workers_input.label = I18n.get("sys_pool_io")
            self.io_workers_input.suffix = I18n.get("sys_suffix_threads")
            self.cpu_workers_input.label = I18n.get("sys_pool_cpu")
            self.cpu_workers_input.suffix = I18n.get("sys_suffix_threads")
            self.no_proxy_input.hint_text = I18n.get("settings_no_proxy_hint")
            self.diagnostics_button.content = I18n.get("settings_diagnostics_btn")

            # 刷新保存按钮的 tooltip
            save_config_tip = I18n.get("settings_save_config")
            self.save_concurrency_btn.tooltip = save_config_tip
            self.save_thread_pool_btn.tooltip = save_config_tip
            self.save_db_pool_btn.tooltip = save_config_tip
            self.save_no_proxy_btn.tooltip = I18n.get("common_save")

            for row in [
                self.row_language,
                self.row_theme,
                self.row_log,
                self.row_concurrency,
                self.row_thread_pool,
                self.row_db_pool,
                self.row_proxy,
                self.row_diagnostics,
            ]:
                row.update_locale()

            self.section_header.update_locale()

            self._safe_update()
        except Exception as e:
            logger.warning("[SystemTab] Locale update failed: %s", e, exc_info=True)

    def _safe_update(self):  # pragma: no cover
        """线程安全的 UI 更新"""
        try:
            if self.page:
                self.page.update()
        except Exception as e:
            logger.debug("[SystemTab] Update skipped: %s", e, exc_info=True)

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
        if self.page:
            self.page.run_task(self._do_theme_change_async)

    async def _do_theme_change_async(self):
        try:
            theme_name = self.theme_dropdown.value
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_theme_name, theme_name)

            from ui.theme import apply_page_theme

            if self.page:
                apply_page_theme(self.page, theme_name)  # type: ignore[untyped]
                self.page.update()

            self.show_snack(I18n.get("settings_snack_theme_updated"))
        except Exception as ex:
            logger.error("[SystemTab] Theme | ❌ Change failed: %s", ex, exc_info=True)
            self.show_snack(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    def on_log_level_change(self, e):
        """Handle log level change"""
        UILogger.log_action(
            "SystemTab",
            "Select",
            f"log_level={self.log_level_dropdown.value}",
        )
        if self.page:
            self.page.run_task(self._do_log_level_change_async)

    async def _do_log_level_change_async(self):
        try:
            level = self.log_level_dropdown.value
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_log_level, level)
            from utils.logger import update_log_level

            update_log_level(level)
            self.show_snack(I18n.get("sys_log_label") + ": " + level)  # type: ignore[untyped]
        except Exception as ex:
            logger.error("[SystemTab] LogLevel | ❌ Change failed: %s", ex, exc_info=True)
            self.show_snack(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    def save_concurrency(self, e):
        """Save concurrency setting"""
        if self.page:
            self.page.run_task(self._do_save_concurrency_async)

    async def _do_save_concurrency_async(self):
        try:
            val = int(self.concurrency_input.value)  # type: ignore[untyped]
            if val < 1 or val > 32:
                self.show_snack(
                    I18n.get("sys_snack_concurrency_range"),
                    color=AppColors.ERROR,
                )
                return
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_sync_max_concurrent_heavy, val)
            self.show_snack(
                I18n.get("sys_sync_heavy") + " " + I18n.get("common_saved"),
                color=AppColors.SUCCESS,
            )
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            logger.error("[SystemTab] Concurrency | ❌ Save failed: %s", ex, exc_info=True)
            self.show_snack(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    def save_db_pool_settings(self, e):
        """Save DB connection pool settings (pool_size, max_overflow, timeout)"""
        if self.page:
            self.page.run_task(self._do_save_db_pool_settings_async)

    async def _do_save_db_pool_settings_async(self):
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

            def _save_db_pool_sync():
                ConfigHandler.set_db_connection_pool_size(pool_size)
                ConfigHandler.set_db_max_overflow(max_overflow)
                ConfigHandler.set_db_pool_timeout(timeout)

            await ThreadPoolManager().run_async(TaskType.IO, _save_db_pool_sync)

            self.show_snack(
                I18n.get("settings_db_pool_saved"),
                color=AppColors.SUCCESS,
            )
        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            logger.error("[SystemTab] DBPool | ❌ Save failed: %s", ex, exc_info=True)
            self.show_snack(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

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

            def _save_thread_pool_sync():
                ConfigHandler.set_max_io_workers(io_val)
                ConfigHandler.set_max_cpu_workers(cpu_val)

            await ThreadPoolManager().run_async(TaskType.IO, _save_thread_pool_sync)

            self.show_snack(I18n.get("common_preparing"))

            # Trigger Reload in thread to avoid UI freeze
            await asyncio.to_thread(ThreadPoolManager().reload_config)

            self.show_snack(I18n.get("sys_snack_pool_saved"), color=AppColors.SUCCESS)
            logger.info("Updated ThreadPool: IO=%s, CPU=%s", io_val, cpu_val)

        except ValueError:
            self.show_snack(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            self.show_snack(
                I18n.get("sys_snack_save_err"),
                color=AppColors.ERROR,
            )
            logger.error(
                "[SystemTab] ThreadPool | ❌ Save failed: %s",
                ex,
                exc_info=True,
            )

    def handle_resize(self, width: float = 0, height: float = 0) -> None:  # pragma: no cover - UI 事件
        """窗口 resize 通知 — 级联调用 TierApiPanel.handle_resize 触发响应式布局。

        Phase 2A.1 §3.2.10：TierApiPanel 自身实现 3/2/1 列响应式断点，
        SystemTab 在此方法中级联调用，确保子组件布局同步刷新。
        """
        try:
            if hasattr(self.tier_api_panel, "handle_resize"):
                self.tier_api_panel.handle_resize(width, height)
        except Exception as exc:
            logger.debug("[SystemTab] tier_api_panel handle_resize skipped: %s", exc, exc_info=True)

    def save_no_proxy_domains(self, e):
        """Save no-proxy domain list."""
        if self.page:
            self.page.run_task(self._do_save_no_proxy_domains_async)

    async def _do_save_no_proxy_domains_async(self):
        try:
            raw_text = self.no_proxy_input.value
            if not raw_text:
                domains = []
            else:
                domains = [d.strip() for d in raw_text.split(",") if d.strip()]

            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_no_proxy_domains, domains)
            self.show_snack(
                I18n.get("settings_snack_no_proxy_saved"),
                color=AppColors.SUCCESS,
            )
            logger.info("No-Proxy domains updated: %s", domains)

            from utils.proxy_manager import ProxyManager

            ThreadPoolManager().submit(
                TaskType.IO,
                ProxyManager.reapply_proxy_policy,
            )

        except Exception as ex:
            logger.error("[SystemTab] No-proxy domains save failed: %s", ex, exc_info=True)
            self.show_snack(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def on_export_diagnostics(self, e=None):
        """异步导出系统诊断包"""
        UILogger.log_action("SystemTab", "Click", "export_diagnostics")
        self.diagnostics_button.disabled = True
        self.diagnostics_button.content = I18n.get("settings_diagnostics_exporting")
        self._safe_update()

        try:
            from utils.diagnostics import SystemDiagnosticsCollector

            # 运行导出
            zip_path = await SystemDiagnosticsCollector.export()

            self.show_snack(
                I18n.get("settings_diagnostics_success").format(path=zip_path),
                color=AppColors.SUCCESS,
            )
        except Exception as ex:
            logger.error("[SystemTab] Diagnostics | Export failed: %s", DataSanitizer.sanitize_error(ex))
            logger.debug("[SystemTab] Diagnostics | Export failed traceback", exc_info=True)
            self.show_snack(
                I18n.get("settings_diagnostics_failed"),
                color=AppColors.ERROR,
            )
        finally:
            self.diagnostics_button.disabled = False
            self.diagnostics_button.content = I18n.get("settings_diagnostics_btn")
            self._safe_update()
