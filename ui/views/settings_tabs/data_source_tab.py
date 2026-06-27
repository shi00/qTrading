import inspect
import logging
import time

import flet as ft

from services.task_manager import AppTask, TaskManager, TaskStatus
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel
from ui.components.settings_widgets import (
    ActionChip,
    DashboardCard,
    MetricCard,
    SectionHeader,
    SettingRow,
)
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.data_source_view_model import DataSourceViewModel
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)


class DataSourceTab(ft.Container):
    def __init__(self, show_snack_callback):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.show_snack = show_snack_callback  # pragma: no cover
        self.expand = True  # pragma: no cover

        self._tm = TaskManager()  # pragma: no cover
        self.vm = DataSourceViewModel()  # pragma: no cover

        # Health check i18n state tracking (for refresh_locale)
        self._health_checked: bool = False
        self._health_status_key: str | None = None
        self._storage_status_key: str | None = None
        self._last_health_result: dict | None = None

        # --- UI Components ---

        # 1. Health Status Dashboard
        self.metric_sync = MetricCard(  # pragma: no cover
            I18n.get("ds_last_update"),  # pragma: no cover
            f"{I18n.get('time_today')} 15:30",  # pragma: no cover
            ft.Icons.ACCESS_TIME,  # pragma: no cover
            AppColors.PRIMARY,  # pragma: no cover
        )  # pragma: no cover
        self.metric_coverage = MetricCard(  # pragma: no cover
            I18n.get("ds_data_coverage"),  # pragma: no cover
            I18n.get("ds_val_placeholder_count"),  # pragma: no cover
            ft.Icons.DATA_USAGE,  # pragma: no cover
            AppColors.INFO,  # pragma: no cover
        )  # pragma: no cover
        self.metric_health = MetricCard(  # pragma: no cover
            I18n.get("ds_sys_health"),  # pragma: no cover
            I18n.get("ds_status_checking"),  # pragma: no cover
            ft.Icons.HEALTH_AND_SAFETY,  # pragma: no cover
            AppColors.WARNING,  # pragma: no cover
        )  # pragma: no cover
        self.metric_storage = MetricCard(  # pragma: no cover
            I18n.get("ds_storage_usage"),  # pragma: no cover
            I18n.get("ds_status_calc"),  # pragma: no cover
            ft.Icons.STORAGE,  # pragma: no cover
            AppColors.TEXT_HINT,  # pragma: no cover
        )  # pragma: no cover

        self.health_summary_text = ft.Text(  # pragma: no cover
            I18n.get("settings_check_health"),  # pragma: no cover
            size=12,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
        )  # pragma: no cover
        self.health_summary_container = ft.Container(  # pragma: no cover
            content=self.health_summary_text,  # pragma: no cover
            padding=ft.padding.symmetric(vertical=10, horizontal=15),  # pragma: no cover
            bgcolor=AppColors.SURFACE_VARIANT,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            border=ft.border.all(1, AppColors.DIVIDER),  # pragma: no cover
        )  # pragma: no cover

        style_health = AppStyles.primary_button()  # pragma: no cover
        style_health.padding = ft.padding.symmetric(horizontal=15, vertical=0)  # pragma: no cover

        self.btn_check_health = ft.ElevatedButton(  # pragma: no cover
            text=I18n.get("settings_check_health"),  # pragma: no cover
            icon=ft.Icons.REFRESH,  # pragma: no cover
            on_click=lambda e: self.page.run_task(self.on_check_health, e) if self.page else None,  # pragma: no cover
            style=style_health,  # pragma: no cover
            height=40,  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_MD,  # pragma: no cover
        )  # pragma: no cover
        self.btn_health_report = ft.IconButton(  # pragma: no cover
            icon=ft.Icons.INFO_OUTLINE,  # pragma: no cover
            tooltip=I18n.get("health_report_title"),  # pragma: no cover
            on_click=self.show_health_report_dialog,  # pragma: no cover
        )  # pragma: no cover

        self.header_health = SectionHeader(
            I18n.get("settings_sec_health"), title_key="settings_sec_health"
        )  # pragma: no cover
        self.header_console = SectionHeader(
            I18n.get("ds_shortcut_console"), title_key="ds_shortcut_console"
        )  # pragma: no cover
        self.header_api = SectionHeader(I18n.get("settings_sec_api"), title_key="settings_sec_api")  # pragma: no cover
        self.header_init = SectionHeader(
            I18n.get("settings_init_data"), title_key="settings_init_data"
        )  # pragma: no cover

        self.health_dashboard = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Row(  # pragma: no cover
                        [  # pragma: no cover
                            self.header_health,  # pragma: no cover
                            ft.Row(  # pragma: no cover
                                [  # pragma: no cover
                                    self.btn_health_report,  # pragma: no cover
                                    self.btn_check_health,  # pragma: no cover
                                ],  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),  # pragma: no cover
                    ft.ResponsiveRow(  # pragma: no cover
                        [  # pragma: no cover
                            ft.Column([self.metric_sync], col={"sm": 6, "md": 3}),  # pragma: no cover
                            ft.Column([self.metric_coverage], col={"sm": 6, "md": 3}),  # pragma: no cover
                            ft.Column([self.metric_health], col={"sm": 6, "md": 3}),  # pragma: no cover
                            ft.Column([self.metric_storage], col={"sm": 6, "md": 3}),  # pragma: no cover
                        ],  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Container(height=10),  # pragma: no cover
                    ft.Container(height=10),  # pragma: no cover
                    ft.Container(height=10),  # pragma: no cover
                    self.health_summary_container,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 2. Action Console
        self.action_full_sync = ActionChip(  # pragma: no cover
            ft.Icons.SYNC_PROBLEM,  # pragma: no cover
            I18n.get("settings_full_sync"),  # pragma: no cover
            I18n.get("ds_action_full"),  # pragma: no cover
            self.on_full_sync,  # pragma: no cover
        )  # pragma: no cover

        self.action_clear_cache = ActionChip(  # pragma: no cover
            ft.Icons.CLEANING_SERVICES,  # pragma: no cover
            I18n.get("settings_clear_cache"),  # pragma: no cover
            I18n.get("ds_action_clear"),  # pragma: no cover
            self.on_clear_cache,  # pragma: no cover
        )  # pragma: no cover

        self.action_ai_concept_rebuild = ActionChip(  # pragma: no cover
            ft.Icons.AUTO_FIX_HIGH,  # pragma: no cover
            I18n.get("ds_btn_ai_concept_rebuild"),  # pragma: no cover
            I18n.get("ds_btn_ai_concept_rebuild_desc"),  # pragma: no cover
            self.on_ai_concept_rebuild,  # pragma: no cover
        )  # pragma: no cover

        self.action_console = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.header_console,  # pragma: no cover
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),  # pragma: no cover
                    ft.ResponsiveRow(  # pragma: no cover
                        [  # pragma: no cover
                            ft.Column([self.action_full_sync], col={"sm": 12, "md": 4}),  # pragma: no cover
                            ft.Column(  # pragma: no cover
                                [self.action_ai_concept_rebuild],  # pragma: no cover
                                col={"sm": 12, "md": 4},  # pragma: no cover
                            ),  # pragma: no cover
                            ft.Column(  # pragma: no cover
                                [self.action_clear_cache],  # pragma: no cover
                                col={"sm": 12, "md": 4},  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                        run_spacing=10,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 3. Connection Settings
        self.tushare_panel = TushareConfigPanel(  # pragma: no cover
            compact=False,  # pragma: no cover
            show_save_button=True,  # pragma: no cover
            show_register_link=False,  # pragma: no cover
            show_internal_loading=True,  # pragma: no cover
            on_verify_success=lambda token: self.show_snack(  # pragma: no cover
                I18n.get("settings_snack_token_verified"),  # pragma: no cover
                color=AppColors.SUCCESS,  # pragma: no cover
            ),  # pragma: no cover
            on_save=self._on_tushare_save,  # pragma: no cover
        )  # pragma: no cover

        self.row_token = SettingRow(  # pragma: no cover
            icon=ft.Icons.KEY_ROUNDED,  # pragma: no cover
            title=I18n.get("settings_token"),  # pragma: no cover
            subtitle=I18n.get("settings_token_desc"),  # pragma: no cover
            control=self.tushare_panel,  # pragma: no cover
            icon_color=AppColors.ACCENT,  # pragma: no cover
            title_key="settings_token",  # pragma: no cover
            subtitle_key="settings_token_desc",  # pragma: no cover
            left_col={"xs": 12, "sm": 12, "md": 5, "lg": 4},  # pragma: no cover
            right_col={"xs": 12, "sm": 12, "md": 7, "lg": 8},  # pragma: no cover
        )  # pragma: no cover
        self.connection_card = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.header_api,  # pragma: no cover
                    ft.Container(height=10),  # pragma: no cover
                    self.row_token,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # 4. Historical Data
        self.progress_bar = ft.ProgressBar(width=None, visible=False, expand=True)  # pragma: no cover
        self.progress_text = ft.Text("", size=12, color=AppColors.INFO)  # pragma: no cover

        style_init = AppStyles.primary_button()  # pragma: no cover
        style_init.padding = ft.padding.symmetric(horizontal=15, vertical=0)  # pragma: no cover

        self.sync_button = ft.ElevatedButton(  # pragma: no cover
            text=I18n.get("settings_init_data"),  # pragma: no cover
            icon=ft.Icons.CLOUD_DOWNLOAD,  # pragma: no cover
            on_click=lambda e: (
                self.page.run_task(self.on_init_historical, e) if self.page else None
            ),  # pragma: no cover
            tooltip=I18n.get("settings_init_desc"),  # pragma: no cover
            style=style_init,  # pragma: no cover
            height=40,  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_MD,  # pragma: no cover
        )  # pragma: no cover

        # Generate history limit selection dropdown
        years = ConfigHandler.get_init_history_years()  # pragma: no cover
        self.history_years_dropdown = ft.Dropdown(  # pragma: no cover
            label=I18n.get("settings_history_range"),  # pragma: no cover
            value=str(years),  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option("1", f"1 {I18n.get('unit_year')}".strip()),  # pragma: no cover
                ft.dropdown.Option("2", f"2 {I18n.get('unit_years')}".strip()),  # pragma: no cover
                ft.dropdown.Option("3", f"3 {I18n.get('unit_years')}".strip()),  # pragma: no cover
                ft.dropdown.Option("4", f"4 {I18n.get('unit_years')}".strip()),  # pragma: no cover
                ft.dropdown.Option("5", f"5 {I18n.get('unit_years')}".strip()),  # pragma: no cover
            ],  # pragma: no cover
            width=150,  # pragma: no cover
            on_change=self.on_history_years_change,  # pragma: no cover
        )  # pragma: no cover

        self.row_init = SettingRow(  # pragma: no cover
            icon=ft.Icons.HISTORY_ROUNDED,  # pragma: no cover
            title=I18n.get("settings_init_data"),  # pragma: no cover
            subtitle=I18n.get("settings_hint_first_run"),  # pragma: no cover
            control=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Row(  # pragma: no cover
                        [self.history_years_dropdown, self.sync_button],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.END,  # pragma: no cover
                        spacing=10,  # pragma: no cover
                        wrap=True,  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Row(  # pragma: no cover
                        [  # pragma: no cover
                            ft.Column(  # pragma: no cover
                                [self.progress_bar, self.progress_text],  # pragma: no cover
                                spacing=2,  # pragma: no cover
                                expand=True,  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.END,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=5,  # pragma: no cover
                alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
                expand=True,  # pragma: no cover
            ),  # pragma: no cover
            icon_color=ft.Colors.PURPLE,  # pragma: no cover
            title_key="settings_init_data",  # pragma: no cover
            subtitle_key="settings_hint_first_run",  # pragma: no cover
        )  # pragma: no cover
        self.historical_card = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.header_init,  # pragma: no cover
                    ft.Container(height=10),  # pragma: no cover
                    self.row_init,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # Assemble logic
        self.content = ft.ListView(  # pragma: no cover
            controls=[  # pragma: no cover
                self.health_dashboard,  # pragma: no cover
                self.action_console,  # pragma: no cover
                self.connection_card,  # pragma: no cover
                self.historical_card,  # pragma: no cover
            ],  # pragma: no cover
            spacing=15,  # pragma: no cover
            padding=ft.padding.only(bottom=50),  # pragma: no cover
        )  # pragma: no cover

        # Lifecycle hooks
        self.did_mount = self._on_mount  # pragma: no cover
        self.will_unmount = self._on_unmount  # pragma: no cover

    def _on_mount(self):
        I18n.subscribe(self.refresh_locale)
        self.tushare_panel.reload_config()
        self._tm.subscribe(self._on_task_update)
        self.vm.bind(
            on_show_snack=self._on_vm_show_snack,
            on_sync_busy_changed=self._on_vm_sync_busy_changed,
            on_health_checking=self._on_vm_health_checking,
            on_health_result=self._on_vm_health_result,
            on_health_error=self._on_vm_health_error,
            on_health_cancelled=self._on_vm_health_cancelled,
            on_health_finished=self._on_vm_health_finished,
            on_init_sync_started=self._on_vm_init_sync_started,
            on_init_sync_reset=self._on_vm_init_sync_reset,
            on_progress_update=self._on_vm_progress_update,
            on_cache_cleared=self._on_vm_cache_cleared,
        )
        self.vm.recover_stale_state()

    def _on_unmount(self):
        I18n.unsubscribe(self.refresh_locale)
        self._tm.unsubscribe(self._on_task_update)
        self.vm.dispose()

    def _on_task_update(self, current_tasks: list[AppTask]):
        if not self.page:
            return
        self.vm.handle_task_update(current_tasks)

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug(f"[DataSourceTab] UI update skipped: {exc}")

    def update_theme(self):  # pragma: no cover
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""
        # MetricCards still need UP/DOWN color refresh
        for card in [
            self.metric_sync,
            self.metric_coverage,
            self.metric_health,
            self.metric_storage,
        ]:
            if hasattr(card, "update_theme"):
                card.update_theme()

        # Standard colors auto-update via semantic tokens
        self._safe_update()

    def refresh_locale(self):
        try:
            # Historical Data Card
            self.sync_button.text = I18n.get("settings_init_data")
            self.sync_button.tooltip = I18n.get("settings_init_desc")
            self.row_init.title_view.value = I18n.get("settings_init_data")
            self.row_init.subtitle_view.value = I18n.get("settings_hint_first_run")
            self.row_token.update_locale()
            self.history_years_dropdown.label = I18n.get("settings_history_range")
            saved_history_years = self.history_years_dropdown.value
            self.history_years_dropdown.value = None  # 强制触发 dirty（Flet 对相等值短路，§5.8 规范 4）
            self.history_years_dropdown.options = [
                ft.dropdown.Option("1", f"1 {I18n.get('unit_year')}".strip()),
                ft.dropdown.Option("2", f"2 {I18n.get('unit_years')}".strip()),
                ft.dropdown.Option("3", f"3 {I18n.get('unit_years')}".strip()),
                ft.dropdown.Option("4", f"4 {I18n.get('unit_years')}".strip()),
                ft.dropdown.Option("5", f"5 {I18n.get('unit_years')}".strip()),
            ]
            self.history_years_dropdown.value = saved_history_years

            self.header_health.update_locale()
            self.header_console.update_locale()
            self.header_api.update_locale()
            self.header_init.update_locale()

            # Health dashboard
            self.btn_check_health.text = I18n.get("settings_check_health")
            self.btn_health_report.tooltip = I18n.get("health_report_title")
            self.health_summary_text.value = I18n.get("settings_check_health")

            # MetricCard labels (values are runtime data, refreshed separately)
            self.metric_sync.set_label(I18n.get("ds_last_update"))
            self.metric_coverage.set_label(I18n.get("ds_data_coverage"))
            self.metric_health.set_label(I18n.get("ds_sys_health"))
            self.metric_storage.set_label(I18n.get("ds_storage_usage"))

            # MetricCard values: 刷新 i18n 文本值（初始占位值或健康检查状态文本）
            if not self._health_checked:
                # 未执行健康检查：刷新初始占位值
                self.metric_sync.set_value(f"{I18n.get('time_today')} 15:30", ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
                self.metric_coverage.set_value(
                    I18n.get("ds_val_placeholder_count"), ft.Icons.DATA_USAGE, AppColors.INFO
                )
                self.metric_health.set_value(I18n.get("ds_status_checking"), ft.Icons.HOURGLASS_TOP, AppColors.WARNING)
                self.metric_storage.set_value(I18n.get("ds_status_calc"), ft.Icons.STORAGE, AppColors.TEXT_HINT)
            else:
                # 已执行健康检查：用记录的 key 重新翻译状态文本
                if self._health_status_key:
                    self.metric_health.set_value(I18n.get(self._health_status_key))
                if self._storage_status_key:
                    self.metric_storage.set_value(I18n.get(self._storage_status_key))
                # 有完整结果时重建健康摘要
                if self._last_health_result:
                    self._rebuild_health_summary(self._last_health_result)

            # ActionChip title/subtitle
            self.action_full_sync.set_text(
                I18n.get("settings_full_sync"),
                I18n.get("ds_action_full"),
            )
            self.action_clear_cache.set_text(
                I18n.get("settings_clear_cache"),
                I18n.get("ds_action_clear"),
            )
            self.action_ai_concept_rebuild.set_text(
                I18n.get("ds_btn_ai_concept_rebuild"),
                I18n.get("ds_btn_ai_concept_rebuild_desc"),
            )

            self._safe_update()
        except Exception as e:
            logger.warning(f"[DataSourceTab] refresh_locale failed: {e}")

    # --- Config Handlers ---

    def _on_tushare_save(self, config: dict):
        token = config.get("token", "").strip()
        if token:
            self.vm.save_tushare_token(token)
            self.show_snack(I18n.get("settings_msg_saved"), color=AppColors.SUCCESS)

    def on_history_years_change(self, e):
        try:
            val = int(e.control.value)
            self.vm.set_history_years(val)
            self.history_years_dropdown.value = str(val)
            self.show_snack(I18n.get("common_saved"), color=AppColors.SUCCESS)
            self._safe_update()
        except Exception as ex:
            logger.error(f"[DataSourceTab] HistoryRange | Failed to set config: {ex}")

    # --- View Event Handlers (delegate to ViewModel) ---

    async def on_check_health(self, e):  # pragma: no cover
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        if e is not None:
            UILogger.log_action("DataSourceTab", "Click", "btn_check_health")
        if self.btn_check_health.disabled:
            return
        await self.vm.check_health()

    async def on_full_sync(self, e):  # pragma: no cover
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_full_sync")
        if self.vm.is_syncing:
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        await self._show_confirm_dialog(
            title_key="dialog_confirm_full_sync_title",
            content_key="dialog_confirm_full_sync_content",
            confirm_btn_key="btn_confirm_sync",
            on_confirm_callback=self.vm.execute_full_daily_sync,
            is_destructive=False,
        )

    async def on_ai_concept_rebuild(self, e):  # pragma: no cover
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_ai_concept_rebuild")
        if self.vm.is_syncing:
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        await self._show_confirm_dialog(
            title_key="dialog_ai_concept_rebuild_title",
            content_key="dialog_ai_concept_rebuild_content",
            confirm_btn_key="btn_confirm_rebuild",
            on_confirm_callback=self.vm.execute_ai_concept_rebuild,
            is_destructive=True,
        )

    async def on_clear_cache(self, e):  # pragma: no cover
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_clear_cache")
        if self.vm.is_syncing:
            self.show_snack(I18n.get("ds_clear_cache_syncing"), color=AppColors.WARNING)
            return
        await self._show_confirm_dialog(
            title_key="dialog_confirm_clear_title",
            content_key="dialog_confirm_clear_content",
            confirm_btn_key="btn_confirm_clear",
            on_confirm_callback=self.vm.execute_clear_cache,
            is_destructive=True,
        )

    async def on_init_historical(self, e):  # pragma: no cover
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        if self.vm.is_syncing and self.vm.init_sync_cancellable:
            UILogger.log_action("DataSourceTab", "Click", "btn_cancel_sync")
            self.page.run_task(self.vm.cancel_init_sync)
            self.sync_button.text = I18n.get("sys_init_cancel_wait")
            self.sync_button.disabled = True
            self._safe_update()
            return
        if self.vm.is_syncing:
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        UILogger.log_action("DataSourceTab", "Click", "btn_init_historical")
        await self._show_confirm_dialog(
            title_key="dialog_confirm_init_title",
            content_key="dialog_confirm_init_content",
            confirm_btn_key="btn_confirm_init",
            on_confirm_callback=self.vm.execute_init_historical_data,
            is_destructive=False,
        )

    # --- ViewModel Callback Handlers ---

    def _on_vm_show_snack(self, message: str, color_name: str):
        color_map = {
            "success": AppColors.SUCCESS,
            "warning": AppColors.WARNING,
            "error": AppColors.ERROR,
            "info": AppColors.INFO,
        }
        self.show_snack(message, color=color_map.get(color_name, AppColors.INFO))

    def _on_vm_sync_busy_changed(self, is_busy: bool, active_key: str | None):
        btn_map = {
            "daily_sync": self.action_full_sync,
            "ai_concept_sync": self.action_ai_concept_rebuild,
            "cache_clear": self.action_clear_cache,
            "system_init_sync": self.sync_button,
        }
        active_btn = btn_map.get(active_key) if active_key else None
        self._update_sync_buttons(is_busy, active_btn)

    def _update_sync_buttons(self, is_busy: bool, active_btn: ft.Control | None):
        controls = [
            self.action_full_sync,
            self.action_clear_cache,
            self.action_ai_concept_rebuild,
            self.sync_button,
        ]
        for ctrl in controls:
            if active_btn == ctrl:
                if isinstance(ctrl, ActionChip):
                    ctrl.set_loading(is_busy)
                elif ctrl == self.sync_button:
                    ctrl.disabled = False
                else:
                    ctrl.disabled = is_busy  # pragma: no cover
            elif is_busy:
                ctrl.disabled = True
                if isinstance(ctrl, ActionChip):
                    ctrl.opacity = 0.5
            else:
                try:
                    ctrl.disabled = False
                    if isinstance(ctrl, ActionChip):
                        ctrl.set_loading(False)
                        ctrl.opacity = 1.0
                except Exception as e:
                    logger.error(f"[DataSourceTab] Failed to reset ctrl state ({ctrl}): {e}", exc_info=True)
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug(f"[DataSourceTab] UI update skipped: {exc}")

    def _on_vm_health_checking(self):
        self.btn_check_health.disabled = True
        self.metric_health.set_value(I18n.get("ds_status_checking"), ft.Icons.HOURGLASS_TOP, AppColors.INFO)
        self.metric_storage.set_value(I18n.get("ds_status_calc"), ft.Icons.HOURGLASS_TOP, AppColors.TEXT_HINT)
        self._health_status_key = "ds_status_checking"
        self._storage_status_key = "ds_status_calc"
        self.health_summary_container.content = ft.Text(
            I18n.get("health_checking"), size=12, color=AppColors.TEXT_SECONDARY
        )
        self._safe_update()

    def _on_vm_health_result(self, result: dict):
        status = result.get("status", "red")
        if status == "yellow":
            self._health_status_key = "ds_health_lag"
            self.metric_health.set_value(I18n.get("ds_health_lag"), ft.Icons.WARNING, AppColors.WARNING)
        elif status == "red":
            self._health_status_key = "ds_health_error"
            self.metric_health.set_value(I18n.get("ds_health_error"), ft.Icons.ERROR, AppColors.ERROR)
        else:
            self._health_status_key = "ds_health_ok"
            self.metric_health.set_value(I18n.get("ds_health_ok"), ft.Icons.CHECK_CIRCLE, AppColors.SUCCESS)

        market_info = result.get("market", {})
        details = result.get("details", {})
        latest = market_info.get("latest_local")
        display_date = I18n.get("ds_never_sync") if not latest or str(latest) == "None" else str(latest)
        self.metric_sync.set_value(display_date, ft.Icons.ACCESS_TIME, AppColors.PRIMARY)

        cov_val = details.get("financial_coverage", 0)
        cov_str = f"{cov_val:.1f}%" if isinstance(cov_val, (int, float)) else str(cov_val)
        self.metric_coverage.set_value(cov_str, ft.Icons.DATA_USAGE, AppColors.INFO)
        self._storage_status_key = "common_normal"
        self.metric_storage.set_value(I18n.get("common_normal"), ft.Icons.STORAGE, AppColors.SUCCESS)

        self._last_health_result = result
        self._health_checked = True
        self._rebuild_health_summary(result)
        self._safe_update()

    def _rebuild_health_summary(self, result: dict):
        """从健康检查结果重建 health_summary_container 内容（供 _on_vm_health_result 和 refresh_locale 共用）。"""
        market_info = result.get("market", {})
        details = result.get("details", {})
        cov_val = details.get("financial_coverage", 0)
        cov_str = f"{cov_val:.1f}%" if isinstance(cov_val, (int, float)) else str(cov_val)

        miss_critical = details.get("missing_critical", 0)
        miss_depth = details.get("missing_depth", 0)
        miss_breadth = details.get("missing_breadth", 0)
        lag = market_info.get("lag_days", 0)
        sys_text = I18n.get("ds_health_summary_sys").format(cov=cov_str, lag=lag)

        if miss_critical > 0:
            core_text = I18n.get("ds_health_summary_core").format(miss=miss_critical)
            core_color, core_icon = AppColors.ERROR, ft.Icons.WARNING_AMBER_ROUNDED
        else:
            core_text = I18n.get("ds_health_summary_core_ok")
            core_color, core_icon = AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE_OUTLINE

        integrity_items = [ft.Icon(core_icon, size=14, color=core_color), ft.Text(core_text, size=12, color=core_color)]
        if miss_depth > 0:
            integrity_items.extend(
                [
                    ft.Text("|", size=12, color=AppColors.DIVIDER),
                    ft.Text(
                        I18n.get("ds_health_summary_depth").format(miss=miss_depth), size=12, color=AppColors.WARNING
                    ),
                ]
            )
        if miss_breadth > 0:
            integrity_items.extend(
                [
                    ft.Text("|", size=12, color=AppColors.DIVIDER),
                    ft.Text(
                        I18n.get("ds_health_summary_breadth").format(miss=miss_breadth),
                        size=12,
                        color=AppColors.WARNING,
                    ),
                ]
            )

        self.health_summary_container.content = ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ANALYTICS, size=14, color=AppColors.INFO),
                        ft.Text(sys_text, size=12, color=AppColors.TEXT_PRIMARY),
                    ],
                    spacing=5,
                    alignment=ft.MainAxisAlignment.START,
                ),
                ft.Row(integrity_items, spacing=5, alignment=ft.MainAxisAlignment.START, wrap=True),
            ],
            spacing=6,
        )

    def _on_vm_health_error(self, error_msg: str):
        self._health_status_key = "common_check_fail"
        self._storage_status_key = "common_check_fail"
        self._health_checked = True
        self.metric_health.set_value(I18n.get("common_check_fail"), ft.Icons.ERROR, AppColors.ERROR)
        self.metric_storage.set_value(I18n.get("common_check_fail"), ft.Icons.ERROR, AppColors.ERROR)
        self.health_summary_container.content = ft.Text(
            I18n.get("ds_health_check_error"),
            size=12,
            color=AppColors.ERROR,
        )
        self._safe_update()

    def _on_vm_health_cancelled(self):
        self._health_status_key = "ds_health_cancelled"
        self._storage_status_key = "ds_health_cancelled"
        self._health_checked = True
        self.metric_health.set_value(I18n.get("ds_health_cancelled"), ft.Icons.CANCEL_OUTLINED, AppColors.WARNING)
        self.metric_storage.set_value(I18n.get("ds_health_cancelled"), ft.Icons.CANCEL_OUTLINED, AppColors.WARNING)
        self.health_summary_container.content = ft.Text(
            I18n.get("ds_health_cancelled"), size=12, color=AppColors.WARNING
        )
        self._safe_update()

    def _on_vm_health_finished(self):
        self.btn_check_health.disabled = False
        self._safe_update()

    def _on_vm_init_sync_started(self):
        self.sync_button.text = I18n.get("settings_cancel_sync")
        self.sync_button.icon = ft.Icons.STOP_CIRCLE
        self.sync_button.style = ft.ButtonStyle(
            color=AppColors.TEXT_ON_PRIMARY,
            icon_color=AppColors.TEXT_ON_PRIMARY,
            bgcolor=AppColors.ERROR,
        )
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self._safe_update()

    def _on_vm_init_sync_reset(self, final_status: TaskStatus):
        self.sync_button.text = I18n.get("settings_init_data")
        self.sync_button.icon = ft.Icons.CLOUD_DOWNLOAD
        self.sync_button.style = AppStyles.primary_button()
        self.sync_button.disabled = False
        self.progress_bar.visible = False
        if final_status == TaskStatus.CANCELLED:
            self.progress_text.value = I18n.get(
                "ds_progress_cancelled_fmt", msg=I18n.get("settings_msg_sync_cancelled")
            )
        elif final_status == TaskStatus.FAILED:
            self.progress_text.value = I18n.get("ds_init_fail_generic")
        else:
            self.progress_text.value = ""
        self._safe_update()

    def _on_vm_progress_update(self, progress: float, message: str):
        now = time.time()
        should_update = (progress >= 1.0) or (not hasattr(self, "_last_ui_update") or now - self._last_ui_update > 0.1)
        if should_update:
            self.progress_bar.value = progress
            self.progress_text.value = f"{progress * 100:.1f}% - {message}"
            self._safe_update()
            self._last_ui_update = now

    def _on_vm_cache_cleared(self):
        if self.page:
            self.page.pubsub.send_all("cache_cleared")

    # --- Dialog ---

    async def _show_confirm_dialog(  # pragma: no cover
        self,
        title_key,
        content_key,
        confirm_btn_key,
        on_confirm_callback,
        is_destructive=False,
    ):
        try:
            if not self.page:
                logger.error(
                    "[DataSourceTab] Dialog | ❌ Page not attached, cannot open dialog",
                )
                return

            # Prevent multiple dialogs
            if getattr(self, "_dialog_open", False):
                return
            self._dialog_open = True

            def close_dialog(e):
                self._dialog_open = False
                if self.page:
                    self.page.close(dialog)

            def confirm_action(e):
                self._dialog_open = False
                if not self.page:
                    return
                self.page.close(dialog)
                if inspect.iscoroutinefunction(on_confirm_callback):
                    self.page.run_task(on_confirm_callback)
                else:
                    on_confirm_callback()

            btn_style = (
                ft.ButtonStyle(color=AppColors.ERROR) if is_destructive else ft.ButtonStyle(color=AppColors.PRIMARY)
            )

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(I18n.get(title_key)),
                content=ft.Text(I18n.get(content_key)),
                actions=[
                    ft.TextButton(I18n.get("common_cancel"), on_click=close_dialog),
                    ft.TextButton(
                        I18n.get(confirm_btn_key),
                        on_click=confirm_action,
                        style=btn_style,
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: setattr(self, "_dialog_open", False),
            )
            self.page.open(dialog)
        except Exception as ex:
            self._dialog_open = False
            logger.error(
                f"[DataSourceTab] Dialog | ❌ Failed to open dialog: {ex}",
                exc_info=True,
            )
            self.show_snack(
                I18n.get("common_op_fail"),
                color=AppColors.ERROR,
            )

    # --- Health Report Dialog ---

    async def show_health_report_dialog(self, e):  # pragma: no cover
        UILogger.log_action("DataSourceTab", "Click", "btn_health_report")
        if not self.page:
            return
        self.page.run_task(self._show_health_report_task)

    async def _show_health_report_task(self):  # pragma: no cover
        try:
            self.show_snack(I18n.get("health_checking"), color=AppColors.INFO)
            report = await self.vm.get_health_report()
            if not self.page:
                return
            from ui.components.health_report_dialog import HealthReportDialog

            dlg = HealthReportDialog(self.page, report)
            self.page.open(dlg)
        except Exception as ex:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(ex, context="general")
            self.show_snack(get_error_message(error_info), color=AppColors.ERROR)
