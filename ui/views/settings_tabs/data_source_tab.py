import asyncio
import contextlib
import inspect
import logging
import time

import flet as ft

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
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
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)


class DataSourceTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        self.is_syncing = False
        self._init_sync_cancellable = False

        self._active_task_ids: dict[str, str] = {}
        self._active_btn_map: dict[str, ft.Control] = {}

        self._tm = TaskManager()

        # Singleton instances (avoid redundant instantiation)
        self._processor = DataProcessor()
        self._cache = CacheManager()

        # --- UI Components ---

        # 1. Health Status Dashboard
        self.metric_sync = MetricCard(
            I18n.get("ds_last_update"),
            f"{I18n.get('time_today')} 15:30",
            ft.Icons.ACCESS_TIME,
            AppColors.PRIMARY,
        )
        self.metric_coverage = MetricCard(
            I18n.get("ds_data_coverage"),
            I18n.get("ds_val_placeholder_count"),
            ft.Icons.DATA_USAGE,
            AppColors.INFO,
        )
        self.metric_health = MetricCard(
            I18n.get("ds_sys_health"),
            I18n.get("ds_status_checking"),
            ft.Icons.HEALTH_AND_SAFETY,
            AppColors.WARNING,
        )
        self.metric_storage = MetricCard(
            I18n.get("ds_storage_usage"),
            I18n.get("ds_status_calc"),
            ft.Icons.STORAGE,
            AppColors.TEXT_HINT,
        )

        self.health_summary_container = ft.Container(
            content=ft.Text(
                I18n.get("settings_check_health"),
                size=12,
                color=AppColors.TEXT_SECONDARY,
            ),
            padding=ft.padding.symmetric(vertical=10, horizontal=15),
            bgcolor=AppColors.SURFACE_VARIANT,
            border_radius=8,
            border=ft.border.all(1, AppColors.DIVIDER),
        )

        style_health = AppStyles.primary_button()
        style_health.padding = ft.padding.symmetric(horizontal=15, vertical=0)

        self.btn_check_health = ft.ElevatedButton(
            text=I18n.get("settings_check_health"),
            icon=ft.Icons.REFRESH,
            on_click=lambda e: self.page.run_task(self.refresh_health_status, e) if self.page else None,
            style=style_health,
            height=40,
            width=AppStyles.CONTROL_WIDTH_MD,
        )

        self.health_dashboard = DashboardCard(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            SectionHeader(I18n.get("settings_sec_health")),
                            ft.Row(
                                [
                                    ft.IconButton(
                                        icon=ft.Icons.INFO_OUTLINE,
                                        tooltip=I18n.get("health_report_title"),
                                        on_click=self.show_health_report_dialog,
                                    ),
                                    self.btn_check_health,
                                ],
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    ft.ResponsiveRow(
                        [
                            ft.Column([self.metric_sync], col={"sm": 6, "md": 3}),
                            ft.Column([self.metric_coverage], col={"sm": 6, "md": 3}),
                            ft.Column([self.metric_health], col={"sm": 6, "md": 3}),
                            ft.Column([self.metric_storage], col={"sm": 6, "md": 3}),
                        ],
                    ),
                    ft.Container(height=10),
                    ft.Container(height=10),
                    ft.Container(height=10),
                    self.health_summary_container,
                ],
            ),
        )

        # 2. Action Console
        self.action_full_sync = ActionChip(
            ft.Icons.SYNC_PROBLEM,
            I18n.get("settings_full_sync"),
            I18n.get("ds_action_full"),
            self.full_daily_sync,
        )

        self.action_clear_cache = ActionChip(
            ft.Icons.CLEANING_SERVICES,
            I18n.get("settings_clear_cache"),
            I18n.get("ds_action_clear"),
            self.confirm_clear_cache,
        )

        self.action_doubao_rebuild = ActionChip(
            ft.Icons.AUTO_FIX_HIGH,
            I18n.get("ds_btn_doubao_rebuild", "AI概念重建"),
            I18n.get(
                "ds_btn_doubao_rebuild_desc",
                "清空所有AI概念并重新生成",
            ),
            self.confirm_doubao_rebuild,
        )

        self.action_console = DashboardCard(
            content=ft.Column(
                [
                    SectionHeader(I18n.get("ds_shortcut_console")),
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    ft.ResponsiveRow(
                        [
                            ft.Column([self.action_full_sync], col={"sm": 12, "md": 4}),
                            ft.Column(
                                [self.action_doubao_rebuild],
                                col={"sm": 12, "md": 4},
                            ),
                            ft.Column(
                                [self.action_clear_cache],
                                col={"sm": 12, "md": 4},
                            ),
                        ],
                        run_spacing=10,
                    ),
                ],
            ),
        )

        # 3. Connection Settings
        self.tushare_panel = TushareConfigPanel(
            compact=False,
            show_save_button=True,
            show_register_link=False,
            show_internal_loading=True,
            on_verify_success=lambda token: self.show_snack(
                I18n.get("settings_snack_token_verified"),
                color=AppColors.SUCCESS,
            ),
            on_save=self._on_tushare_save,
        )

        self.row_token = SettingRow(
            icon=ft.Icons.KEY_ROUNDED,
            title=I18n.get("settings_token"),
            subtitle=I18n.get("settings_token_desc"),
            control=self.tushare_panel,
            icon_color=AppColors.ACCENT,
        )
        self.connection_card = DashboardCard(
            content=ft.Column(
                [
                    SectionHeader(I18n.get("settings_sec_api")),
                    ft.Container(height=10),
                    self.row_token,
                ],
            ),
        )

        # 4. Historical Data
        self.progress_bar = ft.ProgressBar(width=None, visible=False, expand=True)
        self.progress_text = ft.Text("", size=12, color=AppColors.INFO)

        style_init = AppStyles.primary_button()
        style_init.padding = ft.padding.symmetric(horizontal=15, vertical=0)

        self.sync_button = ft.ElevatedButton(
            text=I18n.get("settings_init_data"),
            icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=lambda e: self.page.run_task(self.init_historical_data, e) if self.page else None,
            tooltip=I18n.get("settings_init_desc"),
            style=style_init,
            height=40,
            width=AppStyles.CONTROL_WIDTH_MD,
        )

        # Generate history limit selection dropdown
        years = ConfigHandler.get_init_history_years()
        self.history_years_dropdown = ft.Dropdown(
            label=I18n.get("settings_history_range", "History Range"),
            value=str(years),
            options=[
                ft.dropdown.Option("1", f"1 {I18n.get('unit_year', 'Year')}".strip()),
                ft.dropdown.Option("2", f"2 {I18n.get('unit_years', 'Years')}".strip()),
                ft.dropdown.Option("3", f"3 {I18n.get('unit_years', 'Years')}".strip()),
                ft.dropdown.Option("4", f"4 {I18n.get('unit_years', 'Years')}".strip()),
                ft.dropdown.Option("5", f"5 {I18n.get('unit_years', 'Years')}".strip()),
            ],
            width=150,
            on_change=self.on_history_years_change,
        )

        self.row_init = SettingRow(
            icon=ft.Icons.HISTORY_ROUNDED,
            title=I18n.get("settings_init_data"),
            subtitle=I18n.get("settings_hint_first_run"),
            control=ft.Column(
                [
                    ft.Row(
                        [self.history_years_dropdown, self.sync_button],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=10,
                    ),
                    ft.Row(
                        [
                            ft.Column(
                                [self.progress_bar, self.progress_text],
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    ),  # Container row
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.CENTER,
                expand=True,
            ),
            icon_color=ft.Colors.PURPLE,
        )
        self.historical_card = DashboardCard(
            content=ft.Column(
                [
                    SectionHeader(I18n.get("settings_init_data")),
                    ft.Container(height=10),
                    self.row_init,
                ],
            ),
        )

        # Assemble logic
        self.content = ft.ListView(
            controls=[
                self.health_dashboard,
                self.action_console,
                self.connection_card,
                self.historical_card,
            ],
            spacing=15,
            padding=ft.padding.only(bottom=50),
        )

        # Lifecycle hooks
        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    def _on_mount(self):
        I18n.subscribe(self.refresh_locale)
        self.tushare_panel.reload_config()
        self._tm.subscribe(self._on_task_update)
        self._recover_stale_state()

    def _on_unmount(self):
        I18n.unsubscribe(self.refresh_locale)
        self._tm.unsubscribe(self._on_task_update)

    def _recover_stale_state(self):
        if not self.is_syncing and not self._active_task_ids:
            return
        tm = TaskManager()
        stale_keys = []
        for key, task_id in list(self._active_task_ids.items()):
            task = tm.get_task(task_id)
            if task is None or task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.INTERRUPTED,
            ):
                stale_keys.append(key)
        for key in stale_keys:
            self._active_task_ids.pop(key, None)
            self._active_btn_map.pop(key, None)
        if not self._active_task_ids and self.is_syncing:
            self._set_sync_busy(False)

    def _on_task_update(self, current_tasks: list[AppTask]):
        if not self.page:
            return
        if not self.is_syncing and not self._active_task_ids:
            return
        try:
            self.page.run_task(self._handle_task_state_change, current_tasks)
        except Exception as e:
            logger.debug(f"[DataSourceTab] Task update scheduling failed: {e}")

    async def _handle_task_state_change(self, current_tasks: list[AppTask]):
        active_ids = set(self._active_task_ids.values())
        recovered = False
        for t in current_tasks:
            if t.id in active_ids and t.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.INTERRUPTED,
            ):
                unique_key = next(
                    (k for k, v in self._active_task_ids.items() if v == t.id),
                    None,
                )
                self._active_task_ids = {k: v for k, v in self._active_task_ids.items() if v != t.id}
                if unique_key:
                    self._active_btn_map.pop(unique_key, None)
                if not self._active_task_ids and self.is_syncing and not recovered:
                    self._recover_ui_after_task_terminated(unique_key, t.status)
                    recovered = True

    def _recover_ui_after_task_terminated(
        self,
        unique_key: str | None,
        final_status: TaskStatus,
    ):
        if not self.is_syncing:
            return
        if unique_key == "system_init_sync":
            self._reset_init_sync_ui(final_status)
        else:
            self._set_sync_busy(False)
            if final_status == TaskStatus.CANCELLED:
                self.show_snack(
                    I18n.get("settings_msg_sync_cancelled"),
                    color=AppColors.WARNING,
                )
            elif final_status == TaskStatus.FAILED:
                self.show_snack(
                    I18n.get("ds_init_fail_generic"),
                    color=AppColors.ERROR,
                )

    def _reset_init_sync_ui(self, final_status: TaskStatus = TaskStatus.COMPLETED):
        if not self.is_syncing:
            return
        self._init_sync_cancellable = False
        self.sync_button.text = I18n.get("settings_init_data")
        self.sync_button.icon = ft.Icons.CLOUD_DOWNLOAD
        self.sync_button.style = AppStyles.primary_button()
        self.sync_button.disabled = False
        self.progress_bar.visible = False
        if final_status == TaskStatus.CANCELLED:
            self.progress_text.value = I18n.get(
                "ds_progress_cancelled_fmt",
                msg=I18n.get("settings_msg_sync_cancelled"),
            )
        elif final_status == TaskStatus.FAILED:
            self.progress_text.value = I18n.get("ds_init_fail_generic")
        else:
            self.progress_text.value = ""
        self._set_sync_busy(False)
        self._safe_update()
        if final_status == TaskStatus.CANCELLED:
            self.show_snack(I18n.get("settings_msg_sync_cancelled"), color=AppColors.WARNING)
        elif final_status == TaskStatus.FAILED:
            self.show_snack(I18n.get("ds_init_fail_generic"), color=AppColors.ERROR)

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception:
            pass

    def update_theme(self):
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
        # Historical Data Card
        self.sync_button.text = I18n.get("settings_init_data")
        self.sync_button.tooltip = I18n.get("settings_init_desc")
        self.row_init.title = I18n.get("settings_init_data")  # type: ignore
        self.row_init.subtitle = I18n.get("settings_hint_first_run")  # type: ignore

        self.history_years_dropdown.label = I18n.get(
            "settings_history_range",
            "History Range",
        )
        self.history_years_dropdown.options = [
            ft.dropdown.Option("1", f"1 {I18n.get('unit_year', 'Year')}".strip()),
            ft.dropdown.Option("2", f"2 {I18n.get('unit_years', 'Years')}".strip()),
            ft.dropdown.Option("3", f"3 {I18n.get('unit_years', 'Years')}".strip()),
            ft.dropdown.Option("4", f"4 {I18n.get('unit_years', 'Years')}".strip()),
            ft.dropdown.Option("5", f"5 {I18n.get('unit_years', 'Years')}".strip()),
        ]

        self._safe_update()

    # --- Logic Methods (Migrated from SettingsView) ---

    def _on_tushare_save(self, config: dict):
        token = config.get("token", "").strip()
        if token:
            ConfigHandler.save_token(token)
            from data.external.tushare_client import TushareClient

            client = TushareClient()
            client.set_token(token)
            self.show_snack(
                I18n.get("settings_msg_saved", default="Saved successfully."),
                color=AppColors.SUCCESS,
            )

    async def refresh_health_status(self, e):
        if e is not None:
            UILogger.log_action("DataSourceTab", "Click", "btn_check_health")
        if not self.page:
            return

        if self.btn_check_health.disabled:
            return

        # Disable button to indicate processing
        self.btn_check_health.disabled = True

        self.metric_health.set_value(
            I18n.get("ds_status_checking"),
            ft.Icons.HOURGLASS_TOP,
            AppColors.INFO,
        )
        self.metric_storage.set_value(
            I18n.get("ds_status_calc"),
            ft.Icons.HOURGLASS_TOP,
            AppColors.TEXT_HINT,
        )
        self.health_summary_container.content = ft.Text(
            I18n.get("health_checking"),
            size=12,
            color=AppColors.TEXT_SECONDARY,
        )
        self._safe_update()

        async def _run_health_check(task_id: str, **kwargs):
            try:
                TaskManager().update_progress(
                    task_id,
                    0.2,
                    I18n.get("task_progress_checking"),
                )
                result = await self._processor.check_data_health()

                # Local UI Updates
                status = result.get("status", "red")

                TaskManager().update_progress(
                    task_id,
                    0.9,
                    I18n.get("task_progress_analyzing"),
                )

                if status == "yellow":
                    self.metric_health.set_value(
                        I18n.get("ds_health_lag"),
                        ft.Icons.WARNING,
                        AppColors.WARNING,
                    )
                elif status == "red":
                    self.metric_health.set_value(
                        I18n.get("ds_health_error"),
                        ft.Icons.ERROR,
                        AppColors.ERROR,
                    )
                else:
                    self.metric_health.set_value(
                        I18n.get("ds_health_ok"),
                        ft.Icons.CHECK_CIRCLE,
                        AppColors.SUCCESS,
                    )

                market_info = result.get("market", {})
                details = result.get("details", {})

                latest = market_info.get("latest_local")
                if not latest or str(latest) == "None":
                    display_date = I18n.get("ds_never_sync")
                else:
                    display_date = str(latest)
                self.metric_sync.set_value(
                    display_date,
                    ft.Icons.ACCESS_TIME,
                    AppColors.PRIMARY,
                )

                cov_val = details.get("financial_coverage", 0)
                if isinstance(cov_val, (int, float)):
                    cov_str = f"{cov_val:.1f}%"
                else:
                    cov_str = str(cov_val)

                self.metric_coverage.set_value(
                    cov_str,
                    ft.Icons.DATA_USAGE,
                    AppColors.INFO,
                )
                self.metric_storage.set_value(
                    I18n.get("common_normal"),
                    ft.Icons.STORAGE,
                    AppColors.SUCCESS,
                )

                miss_critical = details.get("missing_critical", 0)
                miss_depth = details.get("missing_depth", 0)
                miss_breadth = details.get("missing_breadth", 0)
                lag = market_info.get("lag_days", 0)

                sys_text = I18n.get("ds_health_summary_sys").format(
                    cov=cov_str,
                    lag=lag,
                )

                if miss_critical > 0:
                    core_text = I18n.get("ds_health_summary_core").format(
                        miss=miss_critical,
                    )
                    core_color = AppColors.ERROR
                    core_icon = ft.Icons.WARNING_AMBER_ROUNDED
                else:
                    core_text = I18n.get("ds_health_summary_core_ok")
                    core_color = AppColors.SUCCESS
                    core_icon = ft.Icons.CHECK_CIRCLE_OUTLINE

                # Build Integrity Row
                integrity_items = [
                    ft.Icon(core_icon, size=14, color=core_color),
                    ft.Text(core_text, size=12, color=core_color),
                ]

                if miss_depth > 0:
                    integrity_items.extend(
                        [
                            ft.Text("|", size=12, color=AppColors.DIVIDER),
                            ft.Text(
                                I18n.get("ds_health_summary_depth").format(
                                    miss=miss_depth,
                                ),
                                size=12,
                                color=AppColors.WARNING,
                            ),
                        ],
                    )
                if miss_breadth > 0:
                    integrity_items.extend(
                        [
                            ft.Text("|", size=12, color=AppColors.DIVIDER),
                            ft.Text(
                                I18n.get("ds_health_summary_breadth").format(
                                    miss=miss_breadth,
                                ),
                                size=12,
                                color=AppColors.WARNING,
                            ),
                        ],
                    )

                self.health_summary_container.content = ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.ANALYTICS,
                                    size=14,
                                    color=AppColors.INFO,
                                ),
                                ft.Text(
                                    sys_text,
                                    size=12,
                                    color=AppColors.TEXT_PRIMARY,
                                ),
                            ],
                            spacing=5,
                            alignment=ft.MainAxisAlignment.START,
                        ),
                        ft.Row(
                            integrity_items,
                            spacing=5,
                            alignment=ft.MainAxisAlignment.START,
                            wrap=True,
                        ),
                    ],
                    spacing=6,
                )

                self._safe_update()

                return I18n.get("task_result_health_done")

            except asyncio.CancelledError:
                self.metric_health.set_value(
                    I18n.get("ds_health_cancelled", "已取消"),
                    ft.Icons.CANCEL_OUTLINED,
                    AppColors.WARNING,
                )
                self.metric_storage.set_value(
                    I18n.get("ds_health_cancelled", "已取消"),
                    ft.Icons.CANCEL_OUTLINED,
                    AppColors.WARNING,
                )
                self.health_summary_container.content = ft.Text(
                    I18n.get("ds_health_cancelled", "已取消"),
                    size=12,
                    color=AppColors.WARNING,
                )
                raise
            except Exception as e:
                logger.error(
                    f"[DataSourceTab] Health | ❌ Check failed: {e}",
                    exc_info=True,
                )
                self.metric_health.set_value(
                    I18n.get("common_check_fail").format(error=""),
                    ft.Icons.ERROR,
                    AppColors.ERROR,
                )
                self.metric_storage.set_value(
                    I18n.get("common_check_fail").format(error=""),
                    ft.Icons.ERROR,
                    AppColors.ERROR,
                )
                self.health_summary_container.content = ft.Text(
                    I18n.get("ds_health_check_error"),
                    size=12,
                    color=AppColors.ERROR,
                )
                raise

            finally:
                try:
                    self.btn_check_health.disabled = False
                    self._safe_update()
                except Exception:
                    pass  # View may have been unmounted

        task_id = TaskManager().submit_task(
            name=I18n.get("task_name_health_check"),
            task_type=I18n.get("task_type_sys_check"),
            coroutine_factory=_run_health_check,
            cancellable=True,
            unique_key="sys_health_check",
        )

        if task_id is None:
            self.btn_check_health.disabled = False
            self._safe_update()

    async def full_daily_sync(self, e):
        UILogger.log_action("DataSourceTab", "Click", "btn_full_sync")
        if self.is_syncing:
            logger.warning("[DataSourceTab] User action intercepted: is_syncing=True")
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        await self._show_confirm_dialog(
            title_key="dialog_confirm_full_sync_title",
            content_key="dialog_confirm_full_sync_content",
            confirm_btn_key="btn_confirm_sync",
            on_confirm_callback=self._do_full_daily_sync,
            is_destructive=False,
        )

    def _do_full_daily_sync(self):
        self._set_sync_busy(True, self.action_full_sync)

        async def _daily_logic(task_id: str, **kwargs):
            tm = TaskManager()

            def _progress(c, t, msg):
                tm.update_progress(task_id, c / t if t else 0, msg)

            try:
                await self._processor.run_daily_update(progress_callback=_progress)
                self.show_snack(
                    I18n.get("snack_full_sync_done", total="全部"),
                    color=AppColors.SUCCESS,
                )
                return I18n.get("ds_daily_update_done")
            except asyncio.CancelledError:
                if self.is_syncing:
                    self.show_snack(
                        I18n.get("settings_msg_sync_cancelled"),
                        color=AppColors.WARNING,
                    )
                raise
            except Exception as ex:
                from utils.error_classifier import classify_error, get_error_message

                error_info = classify_error(ex, context="general")
                self.show_snack(
                    f"{I18n.get('common_op_fail').format(error=get_error_message(error_info))}",
                    color=AppColors.ERROR,
                )
                raise

        task_id = TaskManager().submit_task(
            name=I18n.get("task_name_daily_sync"),
            task_type=I18n.get("sched_task_type_daily"),
            coroutine_factory=_daily_logic,
            cancellable=True,
            unique_key="daily_sync",
        )

        if task_id is None:
            self._set_sync_busy(False)
        else:
            self._active_task_ids["daily_sync"] = task_id
            self._active_btn_map["daily_sync"] = self.action_full_sync

    async def _show_confirm_dialog(
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
                I18n.get("common_op_fail").format(error=ex),
                color=AppColors.ERROR,
            )

    async def confirm_doubao_rebuild(self, e):
        UILogger.log_action("DataSourceTab", "Click", "btn_doubao_rebuild")
        if self.is_syncing:
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        await self._show_confirm_dialog(
            title_key="dialog_doubao_rebuild_title",
            content_key="dialog_doubao_rebuild_content",
            confirm_btn_key="btn_confirm_rebuild",
            on_confirm_callback=self._do_doubao_rebuild,
            is_destructive=True,
        )

    def _do_doubao_rebuild(self):
        self._set_sync_busy(True, self.action_doubao_rebuild)

        async def _doubao_logic(task_id: str, **kwargs):
            tm = TaskManager()
            task = tm.get_task(task_id)
            cancel_event = getattr(task, "_cancel_event", None) if task else None
            try:
                tm.update_progress(task_id, 0.05, I18n.get("ds_doubao_rebuild_start"))
                await self._processor.run_doubao_tagging(
                    task_id=task_id,
                    cancel_event=cancel_event,
                )
                self.show_snack(
                    I18n.get("snack_doubao_done", "AI概念已全部重建！"),
                    color=AppColors.SUCCESS,
                )
                return I18n.get("ds_doubao_rebuild_done")
            except asyncio.CancelledError:
                if self.is_syncing:
                    self.show_snack(
                        I18n.get("settings_msg_sync_cancelled"),
                        color=AppColors.WARNING,
                    )
                raise
            except Exception as ex:
                from utils.error_classifier import classify_error, get_error_message

                error_info = classify_error(ex, context="general")
                self.show_snack(
                    f"{I18n.get('common_op_fail').format(error=get_error_message(error_info))}",
                    color=AppColors.ERROR,
                )
                raise

        task_id = TaskManager().submit_task(
            name=I18n.get("task_name_doubao_rebuild", "AI概念重建"),
            task_type=I18n.get("ds_task_type_ai_tagging"),
            coroutine_factory=_doubao_logic,
            cancellable=True,
            unique_key="doubao_sync",
        )

        if task_id is None:
            self._set_sync_busy(False)
        else:
            self._active_task_ids["doubao_sync"] = task_id
            self._active_btn_map["doubao_sync"] = self.action_doubao_rebuild

    async def confirm_clear_cache(self, e):
        UILogger.log_action("DataSourceTab", "Click", "btn_clear_cache")
        if self.is_syncing:
            logger.warning("[DataSourceTab] User action intercepted: is_syncing=True")
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        await self._show_confirm_dialog(
            title_key="dialog_confirm_clear_title",
            content_key="dialog_confirm_clear_content",
            confirm_btn_key="btn_confirm_clear",
            on_confirm_callback=self._do_clear_cache,
            is_destructive=True,
        )

    def _do_clear_cache(self):
        if self.is_syncing:
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return

        tm = TaskManager()
        running = [t for t in tm.get_all_tasks() if t.status == TaskStatus.RUNNING and t.unique_key != "cache_clear"]
        if running:
            self.show_snack(
                I18n.get(
                    "ds_sync_in_progress",
                    "请先等待执行中任务完成或取消后，再清空缓存",
                ),
                color=AppColors.WARNING,
            )
            return

        self._set_sync_busy(True, self.action_clear_cache)

        async def _clear_logic(task_id: str, **kwargs):
            try:
                await self._cache.clear_all_cache()
                self.show_snack(I18n.get("ds_cache_cleared"))
                if self.page:
                    self.page.pubsub.send_all("cache_cleared")
                return I18n.get("ds_cache_clear_done")
            except Exception as ex:
                from utils.error_classifier import classify_error, get_error_message

                error_info = classify_error(ex, context="general")
                self.show_snack(I18n.get("ds_clean_fail").format(error=get_error_message(error_info)))
                raise
            finally:
                self._set_sync_busy(False)

        task_id = TaskManager().submit_task(
            name=I18n.get("task_name_clear_cache"),
            task_type=I18n.get("ds_task_type_system"),
            coroutine_factory=_clear_logic,
            cancellable=False,
            unique_key="cache_clear",
        )

        if task_id is None:
            self._set_sync_busy(False)
        else:
            self._active_task_ids["cache_clear"] = task_id
            self._active_btn_map["cache_clear"] = self.action_clear_cache

    async def init_historical_data(self, e):
        if self.is_syncing and self._init_sync_cancellable:
            UILogger.log_action("DataSourceTab", "Click", "btn_cancel_sync")
            self.page.run_task(self._processor.request_cancel)  # type: ignore
            task_id = self._active_task_ids.get("system_init_sync")
            if task_id:
                self._tm.cancel_task(task_id)
            self.sync_button.text = I18n.get("sys_init_cancel_wait")
            self.sync_button.disabled = True
            self._safe_update()
            return
        if self.is_syncing:
            logger.warning("[DataSourceTab] User action intercepted: is_syncing=True")
            self.show_snack(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        UILogger.log_action("DataSourceTab", "Click", "btn_init_historical")
        # Prevent accidental trigger, show confirm dialog
        await self._show_confirm_dialog(
            title_key="dialog_confirm_init_title",
            content_key="dialog_confirm_init_content",
            confirm_btn_key="btn_confirm_init",
            on_confirm_callback=self._do_init_historical_data,
            is_destructive=False,
        )

    def _do_init_historical_data(self):
        self._set_sync_busy(True, self.sync_button)
        self._init_sync_cancellable = True

        # Change button to cancel
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

        async def _run_initial_sync(task_id: str, **kwargs):
            try:
                self.progress_text.value = I18n.get("wizard_status_init")
                self.progress_bar.value = 0
                self._safe_update()

                def _combined_progress(c, t, m):
                    self.update_progress(c, t, m)  # UI update
                    TaskManager().update_progress(
                        task_id,
                        c / t if t > 0 else 0,
                        f"[{c:.2f}/{t}] {m}",
                    )

                report = await self._processor.initialize_system(
                    progress_callback=_combined_progress,
                )

                if self._processor.is_cancelled():
                    raise asyncio.CancelledError()

                if report is None:
                    raise Exception(I18n.get("ds_init_fail_generic"))

                self.progress_text.value = f"✅ {I18n.get('sys_init_success')}"
                self.progress_bar.value = 1
                self._reset_init_sync_ui(TaskStatus.COMPLETED)
                self.show_snack(I18n.get("settings_init_done"), color=AppColors.SUCCESS)

                if isinstance(report, dict):
                    await self.refresh_health_status(None)

                return I18n.get("sys_init_success")

            except asyncio.CancelledError:
                self._reset_init_sync_ui(TaskStatus.CANCELLED)
                raise
            except Exception as e:
                error_str = str(e)
                if error_str == I18n.get("ds_init_fail_generic"):
                    msg = error_str
                else:
                    msg = I18n.get(
                        "ds_init_fail_fmt",
                        error=I18n.get("ds_internal_error"),
                    )
                logger.error(
                    f"[DataSourceTab] Sync | ❌ Init sync failed: {e}",
                    exc_info=True,
                )
                self._reset_init_sync_ui(TaskStatus.FAILED)
                self.show_snack(msg, color=AppColors.ERROR)

                raise RuntimeError(msg) from e

        task_id = TaskManager().submit_task(
            name=I18n.get("task_name_init_sync"),
            task_type=I18n.get("task_type_data_sync"),
            coroutine_factory=_run_initial_sync,
            cancellable=True,
            unique_key="system_init_sync",
        )

        if task_id is None:
            self._init_sync_cancellable = False
            self.sync_button.text = I18n.get("settings_init_data")
            self.sync_button.style = AppStyles.primary_button()
            self.sync_button.disabled = False
            self._set_sync_busy(False)
        else:
            self._active_task_ids["system_init_sync"] = task_id
            self._active_btn_map["system_init_sync"] = self.sync_button

    def update_progress(self, current, total, message):
        if not self.page:
            return

        now = time.time()
        should_update = (current == total) or (not hasattr(self, "_last_ui_update") or now - self._last_ui_update > 0.1)

        if should_update:
            progress = current / total if total > 0 else 0
            self.progress_bar.value = progress
            self.progress_text.value = f"{progress * 100:.1f}% - {message}"
            self._safe_update()
            self._last_ui_update = now

    def on_history_years_change(self, e):
        try:
            val = int(e.control.value)
            ConfigHandler.set_init_history_years(val)
            self.history_years_dropdown.value = str(val)
            self.show_snack(I18n.get("common_saved", "Saved"), color=AppColors.SUCCESS)
            self._safe_update()
        except Exception as ex:
            logger.error(
                f"[DataSourceTab] HistoryRange | ❌ Failed to set config: {ex}",
            )

    def _set_sync_busy(self, is_busy: bool, active_btn: ft.Control | None = None):
        self.is_syncing = is_busy

        controls = [
            self.action_full_sync,
            self.action_clear_cache,
            self.action_doubao_rebuild,
            self.sync_button,
        ]

        for ctrl in controls:
            # Case 1: Active Button (The one clicked)
            if active_btn == ctrl:
                if isinstance(ctrl, ActionChip):
                    # ActionChips handle their own disabled state when loading
                    ctrl.set_loading(is_busy)

                elif ctrl == self.sync_button:
                    # Sync button supports 'Cancel' action, so it must remain enabled
                    ctrl.disabled = False

                else:
                    # Other standard buttons (e.g. Repair) that don't support cancellation
                    # should be disabled to prevent double-setup
                    ctrl.disabled = is_busy

            # Case 2: Inactive Buttons (Others)
            elif is_busy:
                ctrl.disabled = True
                if isinstance(ctrl, ActionChip):
                    ctrl.opacity = 0.5  # Strong dim
            else:
                try:
                    ctrl.disabled = False
                    if isinstance(ctrl, ActionChip):
                        ctrl.set_loading(False)  # Reset state
                        ctrl.opacity = 1.0
                except Exception as e:
                    logger.error(
                        f"[DataSourceTab] Sync | ❌ Failed to reset ctrl state ({ctrl}): {e}",
                        exc_info=True,
                    )

        # Batch update via parent container to ensure consistency
        if self.page:
            with contextlib.suppress(Exception):
                self.update()

    async def show_health_report_dialog(self, e):
        """Show full health report dialog"""
        UILogger.log_action("DataSourceTab", "Click", "btn_health_report")
        if not self.page:
            return
        self.page.run_task(self._show_health_report_task)

    async def _show_health_report_task(self):
        try:
            self.show_snack(I18n.get("health_checking"), color=AppColors.INFO)

            report = await self._processor.check_data_health()

            if not self.page:
                return

            from ui.components.health_report_dialog import HealthReportDialog

            dlg = HealthReportDialog(self.page, report)
            self.page.open(dlg)

        except Exception as ex:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(ex, context="general")
            self.show_snack(
                get_error_message(error_info),
                color=AppColors.ERROR,
            )
