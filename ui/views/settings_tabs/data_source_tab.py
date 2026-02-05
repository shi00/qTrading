import asyncio
import logging

import flet as ft

from data.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.tushare_client import TushareClient
from ui.components.settings_widgets import DashboardCard, MetricCard, ActionChip, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)


class DataSourceTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        self.is_syncing = False
        self.cancel_event = None

        # Singleton instances (avoid redundant instantiation)
        self._processor = DataProcessor()
        self._cache = CacheManager()

        # State flags
        self._is_verifying = False

        # Load config
        current_token = ConfigHandler.get_token()

        # --- UI Components ---

        # 1. Health Status Dashboard
        self.metric_sync = MetricCard(I18n.get("ds_last_update"), f"{I18n.get('time_today')} 15:30",
                                      ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
        self.metric_coverage = MetricCard(I18n.get("ds_data_coverage"), I18n.get("ds_val_placeholder_count"),
                                          ft.Icons.DATA_USAGE, AppColors.INFO)
        self.metric_health = MetricCard(I18n.get("ds_sys_health"), I18n.get("ds_status_checking"),
                                        ft.Icons.HEALTH_AND_SAFETY, AppColors.WARNING)
        self.metric_storage = MetricCard(I18n.get("ds_storage_usage"), I18n.get("ds_status_calc"), ft.Icons.STORAGE,
                                         AppColors.TEXT_HINT)

        self.health_detail_text = ft.Text(I18n.get("settings_check_health"), size=12, color=AppColors.TEXT_SECONDARY)

        # Repair UI
        self.missing_fin_codes = []
        self.btn_repair = ft.ElevatedButton(
            I18n.get("ds_btn_repair"),
            icon=ft.Icons.BUILD_CIRCLE,
            style=ft.ButtonStyle(color=AppColors.TEXT_ON_PRIMARY, icon_color=AppColors.TEXT_ON_PRIMARY,
                                 bgcolor=AppColors.ERROR),
            visible=False,
            on_click=self.repair_data,
            height=36
        )

        self.health_dashboard = DashboardCard(
            content=ft.Column([
                ft.Row([
                    SectionHeader(I18n.get("settings_sec_health") + " (3Y)"),
                    ft.Row([
                        ft.IconButton(
                            icon=ft.Icons.INFO_OUTLINE,
                            tooltip=I18n.get("health_report_title"),
                            on_click=self.show_health_report_dialog
                        ),
                        ft.ElevatedButton(
                            text=I18n.get("settings_check_health"),
                            icon=ft.Icons.REFRESH,
                            on_click=self.refresh_health_status,
                            style=AppStyles.primary_button(),
                        )
                    ])
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.ResponsiveRow([
                    ft.Column([self.metric_sync], col={"sm": 6, "md": 3}),
                    ft.Column([self.metric_coverage], col={"sm": 6, "md": 3}),
                    ft.Column([self.metric_health], col={"sm": 6, "md": 3}),
                    ft.Column([self.metric_storage], col={"sm": 6, "md": 3}),
                ]),
                ft.Container(height=10),
                ft.Container(height=10),
                self.health_detail_text,
                ft.Container(height=5),
                self.btn_repair
            ])
        )

        # 2. Action Console
        self.action_update_today = ActionChip(
            ft.Icons.UPDATE,
            I18n.get("settings_update_today"),
            I18n.get("ds_action_today"),
            self.update_daily_quotes,
            is_primary=False
        )

        self.action_full_sync = ActionChip(
            ft.Icons.SYNC_PROBLEM,
            I18n.get("settings_full_sync"),
            I18n.get("ds_action_full"),
            self.full_daily_sync
        )

        self.action_clear_cache = ActionChip(
            ft.Icons.CLEANING_SERVICES,
            I18n.get("settings_clear_cache"),
            I18n.get("ds_action_clear"),
            self.confirm_clear_cache
        )

        self.action_console = DashboardCard(
            content=ft.Column([
                SectionHeader(I18n.get("ds_shortcut_console")),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.ResponsiveRow([
                    ft.Column([self.action_update_today], col={"sm": 12, "md": 4}),
                    ft.Column([self.action_full_sync], col={"sm": 12, "md": 4}),
                    ft.Column([self.action_clear_cache], col={"sm": 12, "md": 4}),
                ], run_spacing=10)
            ])
        )

        # 3. Connection Settings
        self.token_input = ft.TextField(
            label=I18n.get("settings_token"),
            password=True,
            can_reveal_password=True,
            value=current_token,
            width=400,
            on_submit=self.save_and_verify_tushare
        )
        self.btn_save_token = ft.ElevatedButton(
            text=I18n.get("settings_save_token"),
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            on_click=self.save_and_verify_tushare,
            style=AppStyles.primary_button(),
            width=400
        )
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, color=AppColors.TEXT_HINT)
        self.status_text = ft.Text(I18n.get("settings_verify_failed"), color=AppColors.TEXT_HINT)

        self.connection_card = DashboardCard(
            content=ft.Column([
                SectionHeader(I18n.get("settings_sec_api")),
                ft.Text(I18n.get("settings_token_desc"), size=12, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=5),
                ft.Row([
                    self.token_input,
                    self.btn_save_token
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row([
                    self.status_icon,
                    self.status_text
                ])
            ])
        )

        # 4. Historical Data
        self.progress_bar = ft.ProgressBar(width=400, visible=False)
        self.progress_text = ft.Text("", size=12, color=AppColors.INFO)
        self.sync_button = ft.ElevatedButton(
            text=I18n.get("settings_init_data"),
            icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=self.init_historical_data,
            tooltip=I18n.get("settings_init_desc"),
            style=AppStyles.primary_button(),
            width=400
        )

        self.historical_card = DashboardCard(
            content=ft.Column([
                ft.Row([
                    ft.Column([
                        SectionHeader(I18n.get("settings_init_data")),
                        ft.Text(I18n.get("settings_hint_first_run"), size=12, color=AppColors.TEXT_SECONDARY),
                    ]),
                    self.sync_button
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),

                ft.Container(
                    content=ft.Column([
                        self.progress_bar,
                        self.progress_text,
                    ]),
                    padding=ft.padding.only(top=5),
                ),
            ], spacing=5)
        )

        # Assemble logic
        self.content = ft.ListView(controls=[
            self.health_dashboard,
            self.action_console,
            self.connection_card,
            self.historical_card,
        ], spacing=15, padding=ft.padding.only(bottom=50))

        # Lifecycle hooks
        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    def _on_mount(self):
        I18n.subscribe(self.refresh_locale)

    def _on_unmount(self):
        I18n.unsubscribe(self.refresh_locale)

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception:
            pass

    def refresh_locale(self):
        # Update text labels here... simplified for brevity, in real impl should match SettingsView
        # We can implement a minimal set for now
        self.token_input.label = I18n.get("settings_token")
        self.btn_save_token.text = I18n.get("settings_save_token")
        self._safe_update()

    # --- Logic Methods (Migrated from SettingsView) ---

    def refresh_health_status(self, e):
        if not self.page:
            return
        self.metric_health.set_value(I18n.get("ds_status_checking"), ft.Icons.HOURGLASS_TOP, AppColors.INFO)
        self.metric_storage.set_value(I18n.get("ds_status_calc"), ft.Icons.HOURGLASS_TOP, AppColors.TEXT_HINT)
        self.health_detail_text.value = I18n.get("health_checking")
        self.update()
        self.page.run_task(self.check_health_async)

    async def check_health_async(self):
        try:
            result = await self._processor.check_data_health()
            status = result.get('status', 'red')

            if status == 'yellow':
                self.metric_health.set_value(I18n.get("ds_health_lag"), ft.Icons.WARNING, AppColors.WARNING)
            elif status == 'red':
                self.metric_health.set_value(I18n.get("ds_health_error"), ft.Icons.ERROR, AppColors.ERROR)
            else:
                self.metric_health.set_value(I18n.get("ds_health_ok"), ft.Icons.CHECK_CIRCLE, AppColors.SUCCESS)

            latest = result.get('latest_local')
            if not latest or str(latest) == 'None':
                display_date = I18n.get("ds_never_sync")
            else:
                display_date = str(latest)
            self.metric_sync.set_value(display_date, ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
            self.metric_coverage.set_value(f"{result.get('coverage', '0%')}", ft.Icons.DATA_USAGE, AppColors.INFO)
            self.metric_storage.set_value(I18n.get("common_normal"), ft.Icons.STORAGE, AppColors.SUCCESS)

            fin_cov = result.get('financial_coverage', 'N/A')
            recent_cov = result.get('financial_recent_coverage', fin_cov)
            stale_count = result.get('financial_stale_count', 0)
            self.health_detail_text.value = I18n.get("ds_text_cov_detail").format(cov=result.get('coverage', 'N/A'),
                                                                                  fin_cov=fin_cov, recent=recent_cov,
                                                                                  lag=result.get('lag_days', 0))

            # Show Repair Button if needed (now includes stale data)
            missing_fin = result.get('financial_missing_count', 0)
            total_need_repair = missing_fin + stale_count
            if total_need_repair > 0:
                self.missing_fin_codes = result.get('financial_missing_codes', [])
                if stale_count > 0 and missing_fin > 0:
                    self.btn_repair.text = I18n.get("ds_btn_repair_fmt").format(missing=missing_fin, stale=stale_count)
                elif stale_count > 0:
                    self.btn_repair.text = I18n.get("ds_btn_repair_fmt").format(missing=0, stale=stale_count)
                else:
                    self.btn_repair.text = I18n.get("ds_btn_repair_fmt").format(missing=missing_fin, stale=0)
                self.btn_repair.visible = True
            else:
                self.btn_repair.visible = False
            self.btn_repair.update()

            self._safe_update()
        except Exception as e:
            self.metric_health.set_value(I18n.get("common_check_fail").format(error=""), ft.Icons.ERROR,
                                         AppColors.ERROR)
            self.health_detail_text.value = str(e)
            self._safe_update()

    def repair_data(self, e):
        if self.is_syncing: return
        self.show_snack(I18n.get("ds_repair_start_snack"))
        self._set_sync_busy(True, self.btn_repair)
        self.page.run_task(self.repair_data_async)

    async def repair_data_async(self):
        try:
            await self._processor.init_data()  # ensure init

            self.show_snack(I18n.get("ds_repair_progress"), color=AppColors.INFO)

            count = await self._processor.repair_financial_data(
                self.missing_fin_codes,
                progress_callback=lambda c, t, m: self.update_progress(c, t, m)
            )

            self.show_snack(I18n.get("ds_repair_done").format(count=count), color=AppColors.SUCCESS)
            # Auto refresh health
            self.refresh_health_status(None)
        except Exception as e:
            self.show_snack(I18n.get("ds_repair_fail").format(error=e), color=AppColors.ERROR)
        finally:
            self._set_sync_busy(False)

    def update_daily_quotes(self, e):
        if self.is_syncing: return
        self.show_snack(I18n.get("snack_daily_sync_start"))
        self._set_sync_busy(True, self.action_update_today)
        self.page.run_task(self.sync_daily_async)

    async def sync_daily_async(self):
        try:
            await self._processor.init_data()
            df = await self._processor.sync_daily_market_snapshot()
            if df is not None:
                self.show_snack(I18n.get("snack_daily_sync_done").format(count=len(df)))
            else:
                self.show_snack(I18n.get("snack_daily_sync_nodata"))
        except Exception as ex:
            self.show_snack(f"Error: {str(ex)[:30]}", color=AppColors.ERROR)
        finally:
            self._set_sync_busy(False)

    def full_daily_sync(self, e):
        if self.is_syncing: return
        self.show_snack(I18n.get("snack_full_sync_start"))
        self._set_sync_busy(True, self.action_full_sync)
        self.page.run_task(self.full_daily_sync_async)

    async def full_daily_sync_async(self):
        try:
            await self._processor.init_data()
            # Use the robust sync_daily_market_snapshot instead of deprecated sync_all_daily
            df = await self._processor.sync_daily_market_snapshot()

            count = len(df) if df is not None else 0
            self.show_snack(I18n.get("snack_full_sync_done").format(total=f"{count} ({I18n.get('common_quotes')})"))
        except Exception as ex:
            self.show_snack(f"{I18n.get('common_error')}: {str(ex)[:30]}", color=AppColors.ERROR)
        finally:
            self._set_sync_busy(False)

    def confirm_clear_cache(self, e):
        try:
            if not self.page:
                logger.error("Page is not attached")
                return

            # Prevent multiple dialogs
            if getattr(self, '_dialog_open', False):
                return
            self._dialog_open = True

            def close_dialog(e):
                self._dialog_open = False
                self.page.close(dialog)

            def confirm_clear(e):
                self._dialog_open = False
                self.page.close(dialog)
                self.page.run_task(self.clear_cache_async)

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(I18n.get("dialog_confirm_clear_title")),
                content=ft.Text(I18n.get("dialog_confirm_clear_content")),
                actions=[
                    ft.TextButton(I18n.get("common_cancel"), on_click=close_dialog),
                    ft.TextButton(I18n.get("btn_confirm_clear"), on_click=confirm_clear,
                                  style=ft.ButtonStyle(color=AppColors.ERROR)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: setattr(self, '_dialog_open', False)
                # Handle click outside if not modal (though it is modal)
            )
            self.page.open(dialog)
            logger.info("Confirmation dialog opened")
        except Exception as ex:
            self._dialog_open = False
            logger.error(f"Error opening dialog: {ex}")
            self.show_snack(I18n.get("common_op_fail").format(error=ex), color=AppColors.ERROR)

    async def clear_cache_async(self):
        if self.is_syncing: return
        self._set_sync_busy(True, self.action_clear_cache)
        try:
            # init_db is handled inside hard_reset.
            await self._cache.hard_reset()
            self.show_snack(I18n.get("ds_cache_cleared"))
            self.page.pubsub.send_all("cache_cleared")
        except Exception as ex:
            self.show_snack(I18n.get("ds_clean_fail").format(error=str(ex)[:30]))
        finally:
            logger.info("[clear_cache_async] Releasing sync lock...")
            self._set_sync_busy(False)

    def save_and_verify_tushare(self, e):
        """Initiate async token verification to avoid blocking UI"""
        # Prevent double-click during verification
        if self._is_verifying:
            return

        token = self.token_input.value.strip()
        if not token:
            self.show_snack(I18n.get("settings_snack_token_empty"))
            return

        self._is_verifying = True
        self.status_text.value = I18n.get("settings_status_verifying")
        self.status_text.color = AppColors.WARNING
        self.status_icon.color = AppColors.WARNING
        self.status_icon.icon = ft.Icons.HOURGLASS_TOP
        self.update()

        # Run verification in background to avoid blocking UI
        self.page.run_task(self._verify_token_async, token)

    async def _verify_token_async(self, token: str):
        """Verify Tushare token in IO thread pool to prevent UI blocking"""

        def _verify_sync(token_to_verify: str) -> bool:
            """Synchronous verification logic - runs in thread pool"""
            import tushare as ts
            ts.set_token(token_to_verify)
            temp_pro = ts.pro_api()
            # Simple API call to verify token validity
            temp_pro.trade_cal(exchange='', start_date='20250101', end_date='20250101')
            return True

        try:
            # Use project's unified IO thread pool
            await ThreadPoolManager().run_async(TaskType.IO, _verify_sync, token)

            # Verification passed - Save token and update singleton
            ConfigHandler.save_token(token)

            # Update singleton with verified token
            client = TushareClient()
            client.set_token(token)

            self.status_text.value = I18n.get("settings_snack_token_verified")
            self.status_text.color = AppColors.SUCCESS
            self.status_icon.color = AppColors.SUCCESS
            self.status_icon.icon = ft.Icons.CHECK_CIRCLE
            self.show_snack(I18n.get("settings_snack_token_verified"), color=AppColors.SUCCESS)
        except Exception as ex:
            # Verification failed - Don't save token, don't update singleton
            logger.warning(f"Token verification failed: {ex}")
            self.status_text.value = I18n.get("ds_verify_fail_fmt").format(error=str(ex)[:20])
            self.status_text.color = AppColors.ERROR
            self.status_icon.color = AppColors.ERROR
            self.status_icon.icon = ft.Icons.ERROR
        finally:
            self._is_verifying = False
            self._safe_update()

    def init_historical_data(self, e):
        if self.is_syncing and self.sync_button.text.startswith(I18n.get("common_cancel")):
            if self.cancel_event:
                self.cancel_event.set()
                self.sync_button.text = I18n.get("sys_init_cancel_wait")
                self.sync_button.disabled = True
                self.update()
            return

        if self.is_syncing: return
        self._set_sync_busy(True, self.sync_button)

        # Change button to cancel
        self.sync_button.text = I18n.get("settings_cancel_sync")
        self.sync_button.icon = ft.Icons.STOP_CIRCLE
        self.sync_button.style = ft.ButtonStyle(color=AppColors.TEXT_ON_PRIMARY, icon_color=AppColors.TEXT_ON_PRIMARY,
                                                bgcolor=AppColors.ERROR)

        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.update()

        self.page.run_task(self.init_historical_async)

    def update_progress(self, current, total, message):
        if not self.page: return

        # Throttle updates to prevent freezing UI
        # Only update every 0.1s or if complete
        import time
        now = time.time()
        should_update = (current == total) or (not hasattr(self, '_last_ui_update') or now - self._last_ui_update > 0.1)

        if should_update:
            progress = current / total if total > 0 else 0
            self.progress_bar.value = progress
            self.progress_text.value = f"{int(current)}/{int(total)} ({int(progress * 100)}%) - {message}"
            self._safe_update()
            self._last_ui_update = now

    async def init_historical_async(self):
        self.cancel_event = asyncio.Event()
        try:

            # Unified Initialization Logic (v2.0)
            # This handles all 5 steps: List, Calendar, Quotes, Financials, Health Check
            # and provides weighted progress reporting.

            self.progress_text.value = I18n.get("wizard_status_init")
            self.progress_bar.value = 0
            self._safe_update()

            report = await self._processor.initialize_system(
                progress_callback=lambda c, t, m: self.update_progress(c, t, m),
                cancel_event=self.cancel_event
            )

            if self.cancel_event.is_set(): raise asyncio.CancelledError()

            self.progress_text.value = f"✅ {I18n.get('sys_init_success')}"
            self.progress_bar.value = 1
            self.show_snack(I18n.get("settings_init_done"), color=AppColors.SUCCESS)

            # Auto refresh health dashboard if report available
            if isinstance(report, dict):
                self.refresh_health_status(None)

        except asyncio.CancelledError:
            self.show_snack(I18n.get("settings_msg_sync_cancelled"), color=AppColors.WARNING)
        except Exception as e:
            self.show_snack(I18n.get("ds_init_fail_fmt").format(error=str(e)[:30]), color=AppColors.ERROR)
            logger.error(f"Sync error: {e}")
        finally:
            self.is_syncing = False
            self.cancel_event = None
            self.sync_button.text = I18n.get("settings_init_data")
            self.sync_button.icon = ft.Icons.CLOUD_DOWNLOAD
            self.sync_button.style = AppStyles.primary_button()
            self.sync_button.disabled = False
            self.progress_bar.visible = False
            self._set_sync_busy(False)
            self._safe_update()

    def _set_sync_busy(self, is_busy: bool, active_btn: ft.Control = None):
        self.is_syncing = is_busy
        if not self.page: return

        controls = [self.action_update_today, self.action_full_sync, self.action_clear_cache, self.sync_button]
        for ctrl in controls:
            if is_busy:
                ctrl.disabled = True
                if isinstance(ctrl, ActionChip): ctrl.opacity = 0.5

                if ctrl == self.sync_button and active_btn == self.sync_button:
                    ctrl.disabled = False
            else:
                ctrl.disabled = False
                if isinstance(ctrl, ActionChip): ctrl.opacity = 1.0

        # Batch update via parent container to ensure consistency
        self.update()

    def show_health_report_dialog(self, e):
        """Show full health report dialog"""
        if not self.page: return
        self.page.run_task(self._show_health_report_task)

    async def _show_health_report_task(self):
        try:
            self.show_snack(I18n.get("health_checking"), color=AppColors.INFO)

            report = await self._processor.check_data_health()

            from ui.components.health_report_dialog import HealthReportDialog
            dlg = HealthReportDialog(self.page, report)
            self.page.open(dlg)

        except Exception as ex:
            self.show_snack(I18n.get("common_op_fail").format(error=ex), color=AppColors.ERROR)
