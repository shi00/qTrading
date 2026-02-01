import flet as ft
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.components.settings_widgets import DashboardCard, MetricCard, ActionChip, StatusBadge, SectionHeader
from utils.config_handler import ConfigHandler
from data.data_processor import DataProcessor
from data.cache_manager import CacheManager
import tushare as ts
import logging
import asyncio

logger = logging.getLogger(__name__)

class DataSourceTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        self.is_syncing = False
        self.cancel_event = None
        
        # Load config
        current_token = ConfigHandler.get_token()
        
        # --- UI Components ---
        
        # 1. Health Status Dashboard
        self.metric_sync = MetricCard("最后更新", "今日 15:30", ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
        self.metric_coverage = MetricCard("数据覆盖", "5000+ 股票", ft.Icons.DATA_USAGE, AppColors.INFO)
        self.metric_health = MetricCard("系统健康", "检测中...", ft.Icons.HEALTH_AND_SAFETY, ft.Colors.ORANGE)
        self.metric_storage = MetricCard("存储占用", "计算中...", ft.Icons.STORAGE, ft.Colors.GREY)
        
        self.health_detail_text = ft.Text(I18n.get("settings_check_health"), size=12, color=ft.Colors.GREY_600)

        # Repair UI
        self.missing_fin_codes = []
        self.btn_repair = ft.ElevatedButton(
            "一键修复缺失数据", 
            icon=ft.Icons.BUILD_CIRCLE, 
            style=ft.ButtonStyle(color=ft.Colors.WHITE, icon_color=ft.Colors.WHITE, bgcolor=ft.Colors.ERROR),
            visible=False,
            on_click=self.repair_data,
            height=36
        )

        self.health_dashboard = DashboardCard(
            content=ft.Column([
                ft.Row([
                    SectionHeader(I18n.get("settings_sec_health") + " (3Y)"),
                    ft.ElevatedButton(
                        text=I18n.get("settings_check_health"),
                        icon=ft.Icons.REFRESH,
                        on_click=self.refresh_health_status,
                        style=AppStyles.primary_button(),
                    )
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
            "快速同步今日行情数据", 
            self.update_daily_quotes,
            is_primary=False
        )
        
        self.action_full_sync = ActionChip(
            ft.Icons.SYNC_PROBLEM, 
            I18n.get("settings_full_sync"), 
            "完整遍历修复缺失数据", 
            self.full_daily_sync
        )

        self.action_clear_cache = ActionChip(
            ft.Icons.CLEANING_SERVICES, 
            I18n.get("settings_clear_cache"), 
            "清除缓存并重新校验", 
            self.confirm_clear_cache
        )

        self.action_console = DashboardCard(
            content=ft.Column([
                SectionHeader("快捷指令台"),
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
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY)
        self.status_text = ft.Text(I18n.get("settings_verify_failed"), color=ft.Colors.GREY)

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
        self.progress_text = ft.Text("", size=12, color=ft.Colors.BLUE)
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
        
        # Subscribe to I18n
        I18n.subscribe(self.refresh_locale)

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except: pass

    def refresh_locale(self):
        # Update text labels here... simplified for brevity, in real impl should match SettingsView
        # We can implement a minimal set for now
        self.token_input.label = I18n.get("settings_token")
        self.btn_save_token.text = I18n.get("settings_save_token")
        self._safe_update()

    # --- Logic Methods (Migrated from SettingsView) ---

    def refresh_health_status(self, e):
        self.metric_health.set_value("正在检测...", ft.Icons.HOURGLASS_TOP, ft.Colors.BLUE)
        self.metric_storage.set_value("计算中...", ft.Icons.HOURGLASS_TOP, ft.Colors.GREY)
        self.health_detail_text.value = "正在扫描本地数据库与Tushare日历比对..."
        self.update()
        self.page.run_task(self.check_health_async)

    async def check_health_async(self):
        try:
            processor = DataProcessor()
            result = await processor.check_data_health()
            status = result.get('status', 'red')
            
            if status == 'yellow':
                self.metric_health.set_value("数据滞后", ft.Icons.WARNING, ft.Colors.AMBER)
            elif status == 'red':
                self.metric_health.set_value("数据异常/缺失", ft.Icons.ERROR, ft.Colors.RED)
            else:
                self.metric_health.set_value("数据健康", ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN)
                
            latest = result.get('latest_local')
            if not latest or str(latest) == 'None':
                display_date = "从未同步"
            else:
                display_date = str(latest)
            self.metric_sync.set_value(display_date, ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
            self.metric_coverage.set_value(f"{result.get('coverage', '0%')}", ft.Icons.DATA_USAGE, AppColors.INFO)
            self.metric_storage.set_value("正常", ft.Icons.STORAGE, ft.Colors.GREEN)
            
            fin_cov = result.get('financial_coverage', 'N/A')
            recent_cov = result.get('financial_recent_coverage', fin_cov)
            stale_count = result.get('financial_stale_count', 0)
            self.health_detail_text.value = f"覆盖率: {result.get('coverage', 'N/A')} | 财报覆盖: {fin_cov} (近期: {recent_cov}) | 滞后: {result.get('lag_days', 0)}天"
            
            # Show Repair Button if needed (now includes stale data)
            missing_fin = result.get('financial_missing_count', 0)
            total_need_repair = missing_fin + stale_count
            if total_need_repair > 0:
                self.missing_fin_codes = result.get('financial_missing_codes', [])
                if stale_count > 0 and missing_fin > 0:
                    self.btn_repair.text = f"修复 {missing_fin} 只缺失 + {stale_count} 只过期数据的股票"
                elif stale_count > 0:
                    self.btn_repair.text = f"修复 {stale_count} 只过期数据的股票"
                else:
                    self.btn_repair.text = f"修复 {missing_fin} 只缺失基本面数据的股票"
                self.btn_repair.visible = True
            else:
                self.btn_repair.visible = False
            self.btn_repair.update()
            
            self._safe_update()
        except Exception as e:
            self.metric_health.set_value("检查失败", ft.Icons.ERROR, ft.Colors.RED)
            self.health_detail_text.value = str(e)
            self._safe_update()

    def repair_data(self, e):
        if self.is_syncing: return
        self.show_snack("开始针对性修复...")
        self._set_sync_busy(True, self.btn_repair)
        self.page.run_task(self.repair_data_async)

    async def repair_data_async(self):
        try:
             processor = DataProcessor()
             await processor.init_data() # ensure init
             
             self.show_snack("正在修复... 请勿关闭", color=ft.Colors.BLUE)
             
             count = await processor.repair_financial_data(
                self.missing_fin_codes,
                progress_callback=lambda c, t, m: self.update_progress(c, t, m)
             )
             
             self.show_snack(f"✅ 修复完成！已补充 {count} 条记录", color=ft.Colors.GREEN)
             # Auto refresh health
             self.refresh_health_status(None)
        except Exception as e:
             self.show_snack(f"修复失败: {e}", color=ft.Colors.RED)
        finally:
             self._set_sync_busy(False)

    def update_daily_quotes(self, e):
        if self.is_syncing: return
        self.show_snack(I18n.get("snack_daily_sync_start"))
        self._set_sync_busy(True, self.action_update_today)
        self.page.run_task(self.sync_daily_async)

    async def sync_daily_async(self):
        try:
            processor = DataProcessor()
            await processor.init_data()
            df = await processor.sync_daily_market_snapshot()
            if df is not None:
                self.show_snack(I18n.get("snack_daily_sync_done").format(count=len(df)))
            else:
                self.show_snack(I18n.get("snack_daily_sync_nodata"))
        except Exception as ex:
            self.show_snack(f"Error: {str(ex)[:30]}", color=ft.Colors.RED)
        finally:
            self._set_sync_busy(False)

    def full_daily_sync(self, e):
        if self.is_syncing: return
        self.show_snack(I18n.get("snack_full_sync_start"))
        self._set_sync_busy(True, self.action_full_sync)
        self.page.run_task(self.full_daily_sync_async)

    async def full_daily_sync_async(self):
        try:
            processor = DataProcessor()
            await processor.init_data()
            results = await processor.sync_all_daily()
            self.show_snack(I18n.get("snack_full_sync_done").format(total=sum(results.values())))
        except Exception as ex:
             self.show_snack(f"Error: {str(ex)[:30]}", color=ft.Colors.RED)
        finally:
             self._set_sync_busy(False)

    def confirm_clear_cache(self, e):
        try:
            if not self.page:
                logger.error("Page is not attached")
                return

            def close_dialog(e):
                self.page.close(dialog)
            def confirm_clear(e):
                self.page.close(dialog)
                self.page.run_task(self.clear_cache_async)
                
            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("确认清理缓存"),
                content=ft.Text("这将删除所有已缓存的历史数据。\n清理后需要重新同步数据。\n\n确定要继续吗？"),
                actions=[
                    ft.TextButton("取消", on_click=close_dialog),
                    ft.TextButton("确认清理", on_click=confirm_clear, style=ft.ButtonStyle(color=ft.Colors.RED)),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.open(dialog)
            logger.info("Confirmation dialog opened")
        except Exception as ex:
            logger.error(f"Error opening dialog: {ex}")
            self.show_snack(f"操作失败: {ex}", color=ft.Colors.RED)

    async def clear_cache_async(self):
        if self.is_syncing: return
        self._set_sync_busy(True, self.action_clear_cache)
        try:
            cache_mgr = CacheManager()
            await cache_mgr.init_db()
            await cache_mgr.clear_all_cache()
            self.show_snack("缓存已清理完成！")
            self.page.pubsub.send_all("cache_cleared")
        except Exception as ex:
            self.show_snack(f"清理失败: {str(ex)[:30]}")
        finally:
            logger.info("[clear_cache_async] Releasing sync lock...")
            self._set_sync_busy(False)

    def save_and_verify_tushare(self, e):
        token = self.token_input.value.strip()
        if not token:
             self.show_snack(I18n.get("settings_snack_token_empty"))
             return
        ConfigHandler.save_token(token)
        
        self.status_text.value = I18n.get("settings_status_verifying")
        self.status_text.color = ft.Colors.ORANGE
        self.update()
        
        try:
            ts.set_token(token)
            pro = ts.pro_api()
            pro.trade_cal(exchange='', start_date='20250101', end_date='20250101')
            self.status_text.value = I18n.get("settings_snack_token_verified")
            self.status_text.color = ft.Colors.GREEN
            self.status_icon.color = ft.Colors.GREEN
            self.status_icon.icon = ft.Icons.CHECK_CIRCLE
        except Exception as ex:
            self.status_text.value = f"验证失败: {str(ex)[:20]}"
            self.status_text.color = ft.Colors.RED
            self.status_icon.color = ft.Colors.RED
            self.status_icon.icon = ft.Icons.ERROR
        self.update()

    def init_historical_data(self, e):
        if self.is_syncing and self.sync_button.text.startswith(I18n.get("common_cancel")):
             if self.cancel_event:
                 self.cancel_event.set()
                 self.sync_button.text = "取消中..."
                 self.sync_button.disabled = True
                 self.update()
             return

        if self.is_syncing: return
        self._set_sync_busy(True, self.sync_button)
        
        # Change button to cancel
        self.sync_button.text = I18n.get("settings_cancel_sync")
        self.sync_button.icon = ft.Icons.STOP_CIRCLE
        self.sync_button.style = ft.ButtonStyle(color=ft.Colors.WHITE, icon_color=ft.Colors.WHITE, bgcolor=ft.Colors.ERROR)
        
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
            self.progress_text.value = f"{current}/{total} ({progress*100:.1f}%) - {message}"
            self._safe_update()
            self._last_ui_update = now

    async def init_historical_async(self):
        self.cancel_event = asyncio.Event()
        try:
            processor = DataProcessor()
            await processor.init_data()
            
            # Step 1
            self.progress_text.value = "步骤 1/3: 同步股票列表..."
            self._safe_update()
            if self.cancel_event.is_set(): raise asyncio.CancelledError()
            await processor.sync_stock_basic()

            # Step 2
            self.progress_text.value = "步骤 2/3: 同步历史行情..."
            self.progress_bar.value = 0
            self._safe_update()
            if self.cancel_event.is_set(): raise asyncio.CancelledError()
            
            days = 1095
            success = await processor.sync_historical_data(
                days=days, 
                progress_callback=lambda c, t, m: self.update_progress(c, t, m),
                cancel_event=self.cancel_event
            )
            
            if self.cancel_event.is_set(): raise asyncio.CancelledError()
            
            # Step 3
            should_sync, _ = await processor.should_sync_financials()
            if should_sync and not self.cancel_event.is_set():
                self.progress_text.value = "步骤 3/3: 同步财务报表..."
                self.progress_bar.value = None
                self._safe_update()
                await processor.sync_financial_reports(progress_callback=None)

            if not self.cancel_event.is_set():
                self.progress_text.value = "✅ 完成！"
                self.progress_bar.value = 1
                self.show_snack("✅ 初始化完成", color=ft.Colors.GREEN)

        except asyncio.CancelledError:
            self.show_snack("同步已取消", color=ft.Colors.ORANGE)
        except Exception as e:
            self.show_snack(f"初始化失败: {str(e)[:30]}", color=ft.Colors.RED)
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
