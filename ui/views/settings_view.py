import flet as ft
from utils.config_handler import ConfigHandler
from data.tushare_client import TushareClient
from data.cache_manager import CacheManager
from data.data_processor import DataProcessor
import tushare as ts
import logging

logger = logging.getLogger(__name__)

class SettingsView(ft.Container):
    def __init__(self, page):
        super().__init__()
        self.expand = True
        
        # Load existing config
        current_token = ConfigHandler.get_token()
        auto_update_enabled = ConfigHandler.is_auto_update_enabled()
        auto_update_time = ConfigHandler.get_auto_update_time()
        
        self.token_input = ft.TextField(
            label="Tushare API Token", 
            password=True, 
            can_reveal_password=True,
            value=current_token,
            width=400
        )
        
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY)
        self.status_text = ft.Text("未验证", color=ft.Colors.GREY)
        
        # Progress bar for historical sync
        self.progress_bar = ft.ProgressBar(width=400, visible=False)
        self.progress_text = ft.Text("", size=12, color=ft.Colors.BLUE)
        self.sync_button = ft.ElevatedButton(
            "初始化3年数据", 
            icon=ft.Icons.CLOUD_DOWNLOAD, 
            on_click=self.init_historical_data,
            tooltip="同步3年历史行情和财务数据，约需10-15分钟"
        )
        
        # Scheduled task controls
        self.schedule_enabled = ft.Switch(
            label="启用每日自动更新",
            value=auto_update_enabled,
            on_change=self.on_schedule_toggle
        )
        
        self.schedule_time = ft.Dropdown(
            label="更新时间",
            width=150,
            value=auto_update_time,
            options=[
                ft.dropdown.Option("15:30", "15:30 (收盘后)"),
                ft.dropdown.Option("16:00", "16:00"),
                ft.dropdown.Option("16:30", "16:30"),
                ft.dropdown.Option("17:00", "17:00"),
                ft.dropdown.Option("18:00", "18:00"),
                ft.dropdown.Option("20:00", "20:00 (晚间)"),
            ],
            on_change=self.on_schedule_time_change
        )
        
        self.schedule_status = ft.Text(
            self._get_schedule_status_text(auto_update_enabled),
            size=12,
            color=ft.Colors.GREEN if auto_update_enabled else ft.Colors.GREY
        )

        self.content = ft.Column(
            [
                ft.Text("设置", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                
                # === API Configuration ===
                ft.Text("API 配置", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("请输入您的 Tushare Pro Token 以获取数据权限。", size=14, color=ft.Colors.GREY),
                ft.Row([self.token_input]),
                ft.Row([
                    ft.ElevatedButton("保存并验证", icon=ft.Icons.SAVE, on_click=self.save_and_verify),
                    self.status_icon,
                    self.status_text
                ]),
                
                ft.Divider(height=30),
                
                # === Scheduled Task ===
                ft.Text("定时任务", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("设置每日自动同步数据，无需手动操作。", size=14, color=ft.Colors.GREY),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            self.schedule_enabled,
                        ]),
                        ft.Row([
                            ft.Text("同步时间:", size=14),
                            self.schedule_time,
                            ft.Text("(交易日)", size=12, color=ft.Colors.GREY),
                        ]),
                        ft.Row([
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=ft.Colors.GREY),
                            self.schedule_status,
                        ]),
                    ]),
                    padding=ft.padding.all(10),
                    border=ft.border.all(1, ft.Colors.GREY_300),
                    border_radius=8,
                ),
                ft.Text("* 需要程序在后台持续运行", size=11, color=ft.Colors.GREY_500),
                
                ft.Divider(height=30),
                
                # === Manual Data Management ===
                ft.Text("手动数据管理", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("每日更新", size=14, color=ft.Colors.GREY),
                ft.Row([
                    ft.ElevatedButton("更新今日行情", icon=ft.Icons.UPDATE, on_click=self.update_daily_quotes),
                    ft.ElevatedButton("完整日更新", icon=ft.Icons.SYNC, on_click=self.full_daily_sync,
                                      tooltip="同步行情、估值、资金流、北向持股"),
                ]),
                ft.Text("历史数据初始化 (首次使用)", size=14, color=ft.Colors.GREY),
                ft.Row([self.sync_button]),
                # Progress UI
                ft.Container(
                    content=ft.Column([
                        self.progress_bar,
                        self.progress_text,
                    ]),
                    padding=ft.padding.only(top=10, bottom=10),
                ),
                ft.Text("缓存管理", size=14, color=ft.Colors.GREY),
                ft.Row([
                    ft.ElevatedButton("清理过往缓存", icon=ft.Icons.DELETE_OUTLINE, 
                                      color=ft.Colors.RED, on_click=self.confirm_clear_cache),
                ]),
            ],
            scroll=ft.ScrollMode.AUTO,
        )

    def _get_schedule_status_text(self, enabled):
        if enabled:
            return "✅ 自动更新已启用，将在每个交易日指定时间自动同步数据"
        return "自动更新已关闭"

    def on_schedule_toggle(self, e):
        """Handle schedule toggle"""
        enabled = self.schedule_enabled.value
        ConfigHandler.save_config({"auto_update_enabled": enabled})
        
        self.schedule_status.value = self._get_schedule_status_text(enabled)
        self.schedule_status.color = ft.Colors.GREEN if enabled else ft.Colors.GREY
        self.update()
        
        if enabled:
            self.show_snack("自动更新已启用")
        else:
            self.show_snack("自动更新已关闭")

    def on_schedule_time_change(self, e):
        """Handle schedule time change"""
        time = self.schedule_time.value
        ConfigHandler.save_config({"auto_update_time": time})
        self.show_snack(f"更新时间已设置为 {time}")

    def show_snack(self, message):
        snack = ft.SnackBar(ft.Text(message), open=True)
        self.page.overlay.append(snack)
        self.page.update()

    def save_and_verify(self, e):
        token = self.token_input.value.strip()
        if not token:
            self.show_snack("Token 不能为空")
            return
        
        ConfigHandler.save_token(token)
        
        # Verify token
        self.status_text.value = "验证中..."
        self.status_text.color = ft.Colors.ORANGE
        self.update()
        
        try:
            ts.set_token(token)
            pro = ts.pro_api()
            # Simple API call to verify
            pro.trade_cal(exchange='', start_date='20250101', end_date='20250101')
            
            self.status_text.value = "Token 验证成功 ✓"
            self.status_text.color = ft.Colors.GREEN
            self.status_icon.icon = ft.Icons.CHECK_CIRCLE
            self.status_icon.color = ft.Colors.GREEN
            logger.info("Token verified successfully")
        except Exception as ex:
            error_msg = f"验证失败: {str(ex)[:30]}"
            self.status_text.value = error_msg
            self.status_text.color = ft.Colors.RED
            self.status_icon.icon = ft.Icons.ERROR
            self.status_icon.color = ft.Colors.RED
            logger.error(f"Token verification failed: {ex}")
        
        self.update()

    def confirm_clear_cache(self, e):
        """Show confirmation dialog before clearing cache"""
        def close_dialog(e):
            dialog.open = False
            self.page.update()

        def confirm_clear(e):
            dialog.open = False
            self.page.update()
            self.page.run_task(self.clear_cache_async)

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认清理缓存"),
            content=ft.Text("这将删除所有已缓存的历史数据。\n清理后需要重新同步数据。\n\n确定要继续吗？"),
            actions=[
                ft.TextButton("取消", on_click=close_dialog),
                ft.TextButton("确认清理", on_click=confirm_clear, 
                            style=ft.ButtonStyle(color=ft.Colors.RED)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    async def clear_cache_async(self):
        """Actually clear the cache"""
        try:
            cache_mgr = CacheManager()
            await cache_mgr.init_db()
            await cache_mgr.clear_all_cache()
            self.show_snack("缓存已清理完成！")
            logger.info("Cache cleared successfully")
        except Exception as ex:
            self.show_snack(f"清理失败: {str(ex)[:30]}")
            logger.error(f"Cache clear failed: {ex}")

    def update_daily_quotes(self, e):
        """更新今日行情数据"""
        self.show_snack("正在同步今日行情数据...")
        self.page.run_task(self.sync_daily_async)

    async def sync_daily_async(self):
        """异步同步每日行情"""
        try:
            processor = DataProcessor()
            await processor.init_data()
            df = await processor.sync_daily_market_snapshot()
            if df is not None and not df.empty:
                msg = f"行情更新完成！共 {len(df)} 只股票"
                self.show_snack(msg)
                logger.info(msg)
            else:
                self.show_snack("未获取到数据，请检查Token或网络")
                logger.warning("Daily sync returned no data")
        except Exception as ex:
            self.show_snack(f"同步失败: {str(ex)[:30]}")
            logger.error(f"Daily sync failed: {ex}")

    def full_daily_sync(self, e):
        """完整日更新：行情+估值+资金流+北向"""
        self.show_snack("正在执行完整日更新...")
        self.page.run_task(self.full_daily_sync_async)

    async def full_daily_sync_async(self):
        """异步完整日更新"""
        try:
            processor = DataProcessor()
            await processor.init_data()
            results = await processor.sync_all_daily()
            total = sum(results.values())
            msg = f"完整日更新完成！共同步 {total} 条记录"
            self.show_snack(msg)
            logger.info(msg)
        except Exception as ex:
            self.show_snack(f"同步失败: {str(ex)[:30]}")
            logger.error(f"Full daily sync failed: {ex}")

    def init_historical_data(self, e):
        """初始化3年历史数据"""
        self.sync_button.disabled = True
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.progress_text.value = "准备同步..."
        self.update()
        
        self.page.run_task(self.init_historical_async)

    def update_progress(self, current, total, message):
        """Update progress bar from async task"""
        progress = current / total if total > 0 else 0
        self.progress_bar.value = progress
        self.progress_text.value = f"进度: {current}/{total} ({progress*100:.1f}%) - {message}"
        self.update()

    async def init_historical_async(self):
        """异步初始化历史数据"""
        try:
            processor = DataProcessor()
            await processor.init_data()
            
            # Step 1 - Stock Basic
            self.progress_text.value = "步骤 1/3: 同步股票列表..."
            self.progress_bar.value = None
            self.update()
            await processor.sync_stock_basic()
            
            # Step 2 - Historical Data
            self.progress_text.value = "步骤 2/3: 同步历史行情 (耗时较长)..."
            self.progress_bar.value = 0
            self.update()
            
            days = 750
            await processor.sync_historical_data(
                days=days, 
                progress_callback=lambda c, t, m: self.update_progress(c, t, m)
            )
            
            # Step 3 - Financial Reports
            self.progress_text.value = "步骤 3/3: 同步财务报表..."
            self.progress_bar.value = None
            self.update()
            await processor.sync_financial_reports()
            
            # Complete
            self.progress_bar.value = 1
            self.progress_text.value = "✅ 历史数据初始化完成！"
            self.progress_text.color = ft.Colors.GREEN
            self.show_snack("历史数据初始化完成！")
            logger.info("Historical data initialization complete")
            
        except Exception as ex:
            self.progress_text.value = f"❌ 初始化失败: {str(ex)[:50]}"
            self.progress_text.color = ft.Colors.RED
            self.show_snack(f"初始化失败: {str(ex)[:30]}")
            logger.error(f"Historical initialization failed: {ex}")
        finally:
            self.sync_button.disabled = False
            self.update()
