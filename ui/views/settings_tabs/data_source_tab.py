import logging
import asyncio
import flet as ft
from ui.components.settings_widgets import DashboardCard, MetricCard, ActionChip, SectionHeader, SettingRow
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from data.data_processor import DataProcessor
from data.cache_manager import CacheManager
from services.task_manager import TaskManager
from utils.thread_pool import ThreadPoolManager, TaskType
from data.tushare_client import TushareClient

logger = logging.getLogger(__name__)


class DataSourceTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        self.is_syncing = False

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

        self.health_summary_container = ft.Container(
            content=ft.Text(I18n.get("settings_check_health"), size=12, color=AppColors.TEXT_SECONDARY),
            padding=ft.padding.symmetric(vertical=10, horizontal=15),
            bgcolor=AppColors.SURFACE_VARIANT,
            border_radius=8,
            border=ft.border.all(1, AppColors.DIVIDER)
        )
        # Repair UI
        self.missing_fin_codes = []
        self.btn_repair = ft.ElevatedButton(
            I18n.get("ds_btn_repair"),
            icon=ft.Icons.BUILD_CIRCLE,
            style=ft.ButtonStyle(color=AppColors.TEXT_ON_PRIMARY, icon_color=AppColors.TEXT_ON_PRIMARY,
                                 bgcolor=AppColors.ERROR),
            visible=False,
            on_click=self.repair_data,
            height=40,
            width=AppStyles.CONTROL_WIDTH_MD
        )

        style_health = AppStyles.primary_button()
        style_health.padding = ft.padding.symmetric(horizontal=15, vertical=0)

        self.btn_check_health = ft.ElevatedButton(
            text=I18n.get("settings_check_health"),
            icon=ft.Icons.REFRESH,
            on_click=self.refresh_health_status,
            style=style_health,
            height=40,
            width=AppStyles.CONTROL_WIDTH_MD
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
                        self.btn_check_health
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
                ft.Container(height=10),
                self.health_summary_container,
                ft.Container(height=5),
                ft.Row([self.btn_repair], alignment=ft.MainAxisAlignment.END)
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
            expand=True,
            height=40,
            content_padding=10,
            text_size=14,
            on_submit=self.save_and_verify_tushare
        )
        style_save = AppStyles.primary_button()
        style_save.padding = ft.padding.symmetric(horizontal=15, vertical=0)
        
        self.btn_save_token = ft.ElevatedButton(
            text=I18n.get("common_save"),
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            on_click=self.save_and_verify_tushare,
            style=style_save,
            height=40,
            width=AppStyles.CONTROL_WIDTH_MD, # Match width of sync_button
        )
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, color=AppColors.TEXT_HINT, size=12)
        self.status_text = ft.Text(I18n.get("settings_verify_failed"), color=AppColors.TEXT_HINT, size=12)

        self.row_token = SettingRow(
            icon=ft.Icons.KEY_ROUNDED,
            title=I18n.get("settings_token"),
            subtitle=I18n.get("settings_token_desc"),
            control=ft.Column([
                ft.Row([self.token_input, self.btn_save_token], alignment=ft.MainAxisAlignment.END, spacing=10),
                ft.Row([self.status_icon, self.status_text], spacing=5, alignment=ft.MainAxisAlignment.END)
            ], spacing=5, alignment=ft.MainAxisAlignment.CENTER, expand=True), 
            icon_color=AppColors.ACCENT
        )
        self.connection_card = DashboardCard(
            content=ft.Column([
                SectionHeader(I18n.get("settings_sec_api")),
                ft.Container(height=10),
                self.row_token
            ])
        )

        # 4. Historical Data
        self.progress_bar = ft.ProgressBar(width=None, visible=False, expand=True) 
        self.progress_text = ft.Text("", size=12, color=AppColors.INFO)

        style_init = AppStyles.primary_button()
        style_init.padding = ft.padding.symmetric(horizontal=15, vertical=0)

        self.sync_button = ft.ElevatedButton(
            text=I18n.get("settings_init_data"),
            icon=ft.Icons.CLOUD_DOWNLOAD,
            on_click=self.init_historical_data,
            tooltip=I18n.get("settings_init_desc"),
            style=style_init,
            height=40,
            width=AppStyles.CONTROL_WIDTH_MD, 
        )

        # Refactored Historical Card using SettingRow
        # Uses identical structure to row_token for consistent alignment
        self.row_init = SettingRow(
            icon=ft.Icons.HISTORY_ROUNDED,
            title=I18n.get("settings_init_data"),
            subtitle=I18n.get("settings_hint_first_run"),
            control=ft.Column([
                ft.Row([self.sync_button], alignment=ft.MainAxisAlignment.END),
                ft.Row([
                    ft.Column([self.progress_bar, self.progress_text], spacing=2, expand=True)
                ], alignment=ft.MainAxisAlignment.END) # Container row
            ], spacing=5, alignment=ft.MainAxisAlignment.CENTER, expand=True),
            icon_color=ft.Colors.PURPLE
        )
        self.historical_card = DashboardCard(
            content=ft.Column([
                SectionHeader(I18n.get("settings_init_data")),
                ft.Container(height=10),
                self.row_init
            ])
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
            
    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""
        # Input fields use custom colors
        self.token_input.bgcolor = AppColors.INPUT_BG
        self.token_input.color = AppColors.INPUT_TEXT
        self.token_input.border_color = AppColors.INPUT_BORDER

        # MetricCards still need UP/DOWN color refresh
        for card in [self.metric_sync, self.metric_coverage, self.metric_health, self.metric_storage]:
            if hasattr(card, 'update_theme'): card.update_theme()

        # Standard colors auto-update via semantic tokens
        self._safe_update()

    def refresh_locale(self):
        # Update text labels here... simplified for brevity, in real impl should match SettingsView
        # We can implement a minimal set for now
        self.token_input.label = I18n.get("settings_token")
        self.btn_save_token.text = I18n.get("common_save")
        self._safe_update()

    # --- Logic Methods (Migrated from SettingsView) ---

    def refresh_health_status(self, e):
        if not self.page:
            return
        
        # Disable button to indicate processing
        self.btn_check_health.disabled = True
        
        self.metric_health.set_value(I18n.get("ds_status_checking"), ft.Icons.HOURGLASS_TOP, AppColors.INFO)
        self.metric_storage.set_value(I18n.get("ds_status_calc"), ft.Icons.HOURGLASS_TOP, AppColors.TEXT_HINT)
        self.health_summary_container.content = ft.Text(I18n.get("health_checking"), size=12, color=AppColors.TEXT_SECONDARY)
        self.update()
        
        async def _run_health_check(task_id: str, **kwargs):
             try:
                 TaskManager().update_progress(task_id, 0.2, I18n.get("task_progress_checking"))
                 result = await self._processor.check_data_health()
                 
                 # Local UI Updates
                 status = result.get('status', 'red')
                 
                 TaskManager().update_progress(task_id, 0.9, I18n.get("task_progress_analyzing"))

                 if status == 'yellow':
                     self.metric_health.set_value(I18n.get("ds_health_lag"), ft.Icons.WARNING, AppColors.WARNING)
                 elif status == 'red':
                     self.metric_health.set_value(I18n.get("ds_health_error"), ft.Icons.ERROR, AppColors.ERROR)
                 else:
                     self.metric_health.set_value(I18n.get("ds_health_ok"), ft.Icons.CHECK_CIRCLE, AppColors.SUCCESS)

                 market_info = result.get('market', {})
                 details = result.get('details', {})
                 
                 latest = market_info.get('latest_local')
                 if not latest or str(latest) == 'None':
                     display_date = I18n.get("ds_never_sync")
                 else:
                     display_date = str(latest)
                 self.metric_sync.set_value(display_date, ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
                 
                 cov_val = details.get('financial_coverage', 0)
                 if isinstance(cov_val, (int, float)):
                     cov_str = f"{cov_val:.1f}%"
                 else:
                     cov_str = str(cov_val)
                     
                 self.metric_coverage.set_value(cov_str, ft.Icons.DATA_USAGE, AppColors.INFO)
                 self.metric_storage.set_value(I18n.get("common_normal"), ft.Icons.STORAGE, AppColors.SUCCESS)

                 miss_critical = details.get('missing_critical', 0)
                 miss_depth = details.get('missing_depth', 0)
                 miss_breadth = details.get('missing_breadth', 0)
                 lag = market_info.get('lag_days', 0)

                 sys_text = I18n.get("ds_health_summary_sys").format(cov=cov_str, lag=lag)
                 
                 if miss_critical > 0:
                     core_text = I18n.get("ds_health_summary_core").format(miss=miss_critical)
                     core_color = AppColors.ERROR
                     core_icon = ft.Icons.WARNING_AMBER_ROUNDED
                 else:
                     core_text = I18n.get("ds_health_summary_core_ok")
                     core_color = AppColors.SUCCESS
                     core_icon = ft.Icons.CHECK_CIRCLE_OUTLINE

                 # Build Integrity Row
                 integrity_items = [
                     ft.Icon(core_icon, size=14, color=core_color),
                     ft.Text(core_text, size=12, color=core_color)
                 ]
                 
                 if miss_depth > 0:
                     integrity_items.extend([
                         ft.Text("|", size=12, color=AppColors.DIVIDER),
                         ft.Text(I18n.get("ds_health_summary_depth").format(miss=miss_depth), 
                                 size=12, color=AppColors.WARNING)
                     ])
                 if miss_breadth > 0:
                     integrity_items.extend([
                         ft.Text("|", size=12, color=AppColors.DIVIDER),
                         ft.Text(I18n.get("ds_health_summary_breadth").format(miss=miss_breadth), 
                                 size=12, color=AppColors.WARNING)
                     ])

                 self.health_summary_container.content = ft.Column([
                     ft.Row([
                         ft.Icon(ft.Icons.ANALYTICS, size=14, color=AppColors.INFO),
                         ft.Text(sys_text, size=12, color=AppColors.TEXT_PRIMARY)
                     ], spacing=5, alignment=ft.MainAxisAlignment.START),
                     ft.Row(integrity_items, spacing=5, alignment=ft.MainAxisAlignment.START, wrap=True)
                 ], spacing=6)

                 stale_count = 0
                 missing_fin = 0 
                 total_need_repair = missing_fin + stale_count
                 if total_need_repair > 0:
                     self.missing_fin_codes = []
                     self.btn_repair.visible = True
                 else:
                     self.btn_repair.visible = False
                 self._safe_update()
                 
                 return I18n.get("task_result_health_done")

             except Exception as e:
                 logger.error(f"Health check error: {e}", exc_info=True)
                 self.metric_health.set_value(I18n.get("common_check_fail").format(error=""), ft.Icons.ERROR,
                                              AppColors.ERROR)
                 self.health_summary_container.content = ft.Text(I18n.get("ds_health_check_error"), size=12, color=AppColors.ERROR)
                 raise
                 
             finally:
                 try:
                     self.btn_check_health.disabled = False
                     self._safe_update()
                 except Exception:
                     pass  # View may have been unmounted
                 
        TaskManager().submit_task(
             name=I18n.get("task_name_health_check"),
             task_type=I18n.get("task_type_sys_check"),
             coroutine_factory=_run_health_check,
             cancellable=True
        )

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
        self._show_confirm_dialog(
            title_key="dialog_confirm_full_sync_title",
            content_key="dialog_confirm_full_sync_content",
            confirm_btn_key="btn_confirm_sync",
            on_confirm_callback=self._do_full_daily_sync,
            is_destructive=False
        )

    def _do_full_daily_sync(self):
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

    def _show_confirm_dialog(self, title_key, content_key, confirm_btn_key, on_confirm_callback, is_destructive=False):
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

            def confirm_action(e):
                self._dialog_open = False
                self.page.close(dialog)
                if asyncio.iscoroutinefunction(on_confirm_callback):
                    self.page.run_task(on_confirm_callback)
                else:
                    on_confirm_callback()

            btn_style = ft.ButtonStyle(color=AppColors.ERROR) if is_destructive else ft.ButtonStyle(color=AppColors.PRIMARY)

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(I18n.get(title_key)),
                content=ft.Text(I18n.get(content_key)),
                actions=[
                    ft.TextButton(I18n.get("common_cancel"), on_click=close_dialog),
                    ft.TextButton(I18n.get(confirm_btn_key), on_click=confirm_action, style=btn_style),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: setattr(self, '_dialog_open', False)
            )
            self.page.open(dialog)
        except Exception as ex:
            self._dialog_open = False
            logger.error(f"Error opening dialog: {ex}")
            self.show_snack(I18n.get("common_op_fail").format(error=ex), color=AppColors.ERROR)

    def confirm_clear_cache(self, e):
        if self.is_syncing: return
        self._show_confirm_dialog(
            title_key="dialog_confirm_clear_title",
            content_key="dialog_confirm_clear_content",
            confirm_btn_key="btn_confirm_clear",
            on_confirm_callback=self.clear_cache_async,
            is_destructive=True
        )

    async def clear_cache_async(self):
        if self.is_syncing: return
        self._set_sync_busy(True, self.action_clear_cache)
        try:
            # init_db is handled inside clear_all_cache.
            await self._cache.clear_all_cache()
            self.show_snack(I18n.get("ds_cache_cleared"))
            self.page.pubsub.send_all("cache_cleared")
        except Exception as ex:
            self.show_snack(I18n.get("ds_clean_fail").format(error=str(ex)[:100]))
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
        if self.is_syncing and getattr(self.sync_button, "text", "").startswith(I18n.get("common_cancel")):
            # Request cancellation via DataProcessor
            self.page.run_task(self._processor.request_cancel)
            self.sync_button.text = I18n.get("sys_init_cancel_wait")
            self.sync_button.disabled = True
            self.update()
            return

        if self.is_syncing: return
        
        # Prevent accidental trigger, show confirm dialog
        self._show_confirm_dialog(
            title_key="dialog_confirm_init_title",
            content_key="dialog_confirm_init_content",
            confirm_btn_key="btn_confirm_init",
            on_confirm_callback=self._do_init_historical_data,
            is_destructive=False
        )

    def _do_init_historical_data(self):
        self._set_sync_busy(True, self.sync_button)

        # Change button to cancel
        self.sync_button.text = I18n.get("settings_cancel_sync")
        self.sync_button.icon = ft.Icons.STOP_CIRCLE
        self.sync_button.style = ft.ButtonStyle(color=AppColors.TEXT_ON_PRIMARY, icon_color=AppColors.TEXT_ON_PRIMARY,
                                                bgcolor=AppColors.ERROR)

        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.update()

        async def _run_initial_sync(task_id: str, **kwargs):
            try:
                self.progress_text.value = I18n.get("wizard_status_init")
                self.progress_bar.value = 0
                self._safe_update()
                
                def _combined_progress(c, t, m):
                     self.update_progress(c, t, m) # UI update
                     TaskManager().update_progress(task_id, c / t if t > 0 else 0, f"[{c}/{t}] {m}")

                report = await self._processor.initialize_system(
                    progress_callback=_combined_progress
                )

                if self._processor.is_cancelled(): raise asyncio.CancelledError()

                if report is None:
                    raise Exception(I18n.get("ds_init_fail_generic"))

                self.progress_text.value = f"✅ {I18n.get('sys_init_success')}"
                self.progress_bar.value = 1
                self.show_snack(I18n.get("settings_init_done"), color=AppColors.SUCCESS)
                
                # Back to original button state
                self.sync_button.text = I18n.get("settings_init_data")
                self.sync_button.icon = ft.Icons.CLOUD_DOWNLOAD
                self.sync_button.style = AppStyles.primary_button()
                self._set_sync_busy(False)
                self._safe_update()
                
                if isinstance(report, dict):
                    self.refresh_health_status(None)
                    
                return I18n.get("sys_init_success")

            except asyncio.CancelledError:
                msg = I18n.get("settings_msg_sync_cancelled")
                self.show_snack(msg, color=AppColors.WARNING)
                self.progress_text.value = I18n.get("ds_progress_cancelled_fmt", msg=msg)
                
                # Revert UI on cancel
                self.sync_button.text = I18n.get("settings_init_data")
                self.sync_button.style = AppStyles.primary_button()
                self.sync_button.disabled = False
                
                # Use call_soon_threadsafe to ensure UI resets happen safely
                def _safe_revert():
                    try:
                        self._set_sync_busy(False)
                        self._safe_update()
                    except Exception as ex:
                        logger.error(f"Error reverting UI on cancel: {ex}")
                
                if self.page and self.page.loop:
                    self.page.loop.call_soon_threadsafe(_safe_revert)
                else:
                    _safe_revert()
                    
                raise
            except Exception as e:
                error_str = str(e)
                if error_str == I18n.get("ds_init_fail_generic"):
                    msg = error_str
                else:
                    msg = I18n.get("ds_init_fail_fmt", error="内部系统错误，请检查系统日志。")
                
                self.show_snack(msg, color=AppColors.ERROR)
                self.progress_text.value = I18n.get("ds_progress_failed_fmt", msg=msg)
                logger.error(f"Sync error: {e}", exc_info=True)
                
                # Revert UI
                self.sync_button.text = I18n.get("settings_init_data")
                self.sync_button.style = AppStyles.primary_button()
                self.sync_button.disabled = False
                
                def _safe_revert_err():
                    try:
                        self._set_sync_busy(False)
                        self._safe_update()
                    except Exception as ex:
                        logger.error(f"Error reverting UI on exception: {ex}")
                
                if self.page and self.page.loop:
                    self.page.loop.call_soon_threadsafe(_safe_revert_err)
                else:
                    _safe_revert_err()
                    
                raise RuntimeError(msg)

        TaskManager().submit_task(
            name=I18n.get("task_name_init_sync"),
            task_type=I18n.get("task_type_data_sync"),
            coroutine_factory=_run_initial_sync,
            cancellable=True,
            unique_key="system_init_sync"
        )

    def update_progress(self, current, total, message):
        if not self.page: return

        # Throttle updates to prevent freezing UI
        import time
        now = time.time()
        should_update = (current == total) or (not hasattr(self, '_last_ui_update') or now - self._last_ui_update > 0.1)

        if should_update:
            progress = current / total if total > 0 else 0
            self.progress_bar.value = progress
            self.progress_text.value = f"{progress * 100:.1f}% - {message}"
            self._safe_update()
            self._last_ui_update = now

    def _set_sync_busy(self, is_busy: bool, active_btn: ft.Control = None):
        self.is_syncing = is_busy
        if not self.page: return

        # Include btn_repair in the managed controls
        controls = [self.action_update_today, self.action_full_sync, self.action_clear_cache, self.sync_button, self.btn_repair]
        
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
            else:
                if is_busy:
                    ctrl.disabled = True
                    if isinstance(ctrl, ActionChip): 
                        ctrl.opacity = 0.5 # Strong dim
                else:
                    try:
                        ctrl.disabled = False
                        if isinstance(ctrl, ActionChip): 
                            ctrl.set_loading(False) # Reset state
                            ctrl.opacity = 1.0
                    except Exception as e:
                        logger.error(f"Failed to reset ctrl state ({ctrl}): {e}")

        # Batch update via parent container to ensure consistency
        try:
            self.update()
        except:
            pass

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
