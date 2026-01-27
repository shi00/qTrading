import flet as ft
from utils.config_handler import ConfigHandler
from data.tushare_client import TushareClient
from data.data_processor import DataProcessor
import tushare as ts
import asyncio

class OnboardingWizard(ft.Container):
    """
    Step-by-step onboarding wizard for first-time users.
    Steps:
    1. Welcome & Token Configuration
    2. Initial Data Sync
    3. Set up Daily Scheduled Task (optional)
    4. Complete
    """
    
    def __init__(self, page, on_complete=None):
        super().__init__()
        self.app_page = page
        self.on_complete = on_complete  # Callback when wizard completes
        self.current_step = 0
        self.expand = True
        
        # Step 1: Token input
        self.token_input = ft.TextField(
            label="请输入您的 Tushare Pro Token",
            password=True,
            can_reveal_password=True,
            width=400,
            hint_text="可在 tushare.pro 个人中心获取",
            on_submit=self._verify_token
        )
        self.token_status = ft.Text("", size=12)
        
        # Step 2: Sync progress
        self.sync_progress = ft.ProgressBar(width=400, value=0)
        self.sync_status = ft.Text("准备就绪", size=12)
        
        # Step 3: Schedule options
        self.schedule_enabled = ft.Checkbox(
            label="启用每日自动更新 (16:30)",
            value=True
        )
        
        # Build wizard UI
        self.steps_content = [
            self._build_step1(),
            self._build_step2(),
            self._build_step3(),
            self._build_step4(),
        ]
        
        self.step_container = ft.Container(
            content=self.steps_content[0],
            expand=True,
        )
        
        # Progress indicator
        self.step_indicators = ft.Row(
            [self._create_step_indicator(i, ["Token配置", "数据同步", "定时任务", "完成"]) 
             for i in range(4)],
            alignment=ft.MainAxisAlignment.CENTER,
        )
        
        self.content = ft.Column(
            [
                ft.Container(height=20),
                ft.Text("欢迎使用 A股智能选股助手", size=28, weight=ft.FontWeight.BOLD, 
                       text_align=ft.TextAlign.CENTER),
                ft.Text("首次使用需要完成以下配置", size=14, color=ft.Colors.GREY_600,
                       text_align=ft.TextAlign.CENTER),
                ft.Container(height=20),
                self.step_indicators,
                ft.Divider(height=30),
                self.step_container,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

    def _create_step_indicator(self, index, labels):
        """Create step indicator dot"""
        is_active = index == self.current_step
        is_completed = index < self.current_step
        
        if is_completed:
            color = ft.Colors.GREEN
            icon = ft.Icons.CHECK_CIRCLE
        elif is_active:
            color = ft.Colors.BLUE
            icon = ft.Icons.RADIO_BUTTON_CHECKED
        else:
            color = ft.Colors.GREY_400
            icon = ft.Icons.RADIO_BUTTON_UNCHECKED
        
        return ft.Column(
            [
                ft.Icon(icon, color=color, size=24),
                ft.Text(labels[index], size=10, color=color),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            width=80,
        )

    def _update_indicators(self):
        """Update step indicators"""
        labels = ["Token配置", "数据同步", "定时任务", "完成"]
        self.step_indicators.controls = [
            self._create_step_indicator(i, labels) for i in range(4)
        ]
        self.step_container.content = self.steps_content[self.current_step]
        self.update()

    def _build_step1(self):
        """Step 1: Token Configuration"""
        return ft.Column(
            [
                ft.Icon(ft.Icons.KEY, size=48, color=ft.Colors.BLUE),
                ft.Text("步骤 1: 配置 Tushare Token", size=20, weight=ft.FontWeight.W_500),
                ft.Container(height=10),
                ft.Text(
                    "Tushare Pro 是专业的金融数据服务平台，需要注册并获取Token。\n"
                    "注册地址：https://tushare.pro/register\n"
                    "获取Token后粘贴到下方输入框。",
                    size=13, color=ft.Colors.GREY_700,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                self.token_input,
                self.token_status,
                ft.Container(height=20),
                ft.Row([
                    ft.ElevatedButton("验证并继续", icon=ft.Icons.ARROW_FORWARD, 
                                     on_click=self._verify_token),
                ], alignment=ft.MainAxisAlignment.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    async def _handle_quick_sync(self, e):
        await self._start_sync(quick=True)

    async def _handle_full_sync(self, e):
        await self._start_sync(quick=False)

    async def _handle_cancel_sync(self, e):
        """Cancel the running sync task"""
        if hasattr(self, 'cancel_event'):
            self.cancel_event.set()
            self.sync_status.value = "正在取消..."
            self.sync_status.color = ft.Colors.RED
            self.btn_cancel_sync.disabled = True
            self.update()

    def _build_step2(self):
        """Step 2: Data Sync"""
        self.btn_quick_sync = ft.ElevatedButton(
            "仅同步今日 (快)", 
            icon=ft.Icons.FLASH_ON,
            on_click=self._handle_quick_sync
        )
        self.btn_full_sync = ft.ElevatedButton(
            "完整同步 (3年)", 
            icon=ft.Icons.CLOUD_SYNC,
            on_click=self._handle_full_sync
        )
        self.btn_cancel_sync = ft.ElevatedButton(
            "取消", 
            icon=ft.Icons.CANCEL,
            color=ft.Colors.RED,
            visible=False,
            on_click=self._handle_cancel_sync
        )
        
        return ft.Column(
            [
                ft.Icon(ft.Icons.CLOUD_DOWNLOAD, size=48, color=ft.Colors.BLUE),
                ft.Text("步骤 2: 同步历史数据", size=20, weight=ft.FontWeight.W_500),
                ft.Container(height=10),
                ft.Text(
                    "选股策略需要历史数据支持。\n"
                    "完整同步约需3-5分钟 (5倍并发)，也可选择仅同步今日数据。",
                    size=13, color=ft.Colors.GREY_700,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Row(
                    [self.btn_quick_sync, self.btn_full_sync, self.btn_cancel_sync],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Container(height=20),
                self.sync_progress,
                self.sync_status,
                ft.Container(height=10),
                ft.TextButton("跳过此步骤", on_click=self._skip_sync),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_step3(self):
        """Step 3: Scheduled Task"""
        return ft.Column(
            [
                ft.Icon(ft.Icons.SCHEDULE, size=48, color=ft.Colors.BLUE),
                ft.Text("步骤 3: 设置自动更新", size=20, weight=ft.FontWeight.W_500),
                ft.Container(height=10),
                ft.Text(
                    "建议在每个交易日收盘后自动更新数据。\n"
                    "程序将在后台静默更新，不会打扰您的使用。",
                    size=13, color=ft.Colors.GREY_700,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                self.schedule_enabled,
                ft.Text("* 需要程序在后台运行", size=11, color=ft.Colors.GREY_500),
                ft.Container(height=20),
                ft.Row([
                    ft.ElevatedButton("完成配置", icon=ft.Icons.CHECK, 
                                     on_click=self._finish_setup),
                ], alignment=ft.MainAxisAlignment.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_step4(self):
        """Step 4: Complete"""
        return ft.Column(
            [
                ft.Icon(ft.Icons.CELEBRATION, size=64, color=ft.Colors.GREEN),
                ft.Text("🎉 配置完成！", size=24, weight=ft.FontWeight.BOLD),
                ft.Container(height=10),
                ft.Text(
                    "您已完成所有初始配置。\n"
                    "现在可以开始使用智能选股功能了！",
                    size=14, color=ft.Colors.GREY_700,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=30),
                ft.ElevatedButton(
                    "开始使用", 
                    icon=ft.Icons.ROCKET_LAUNCH,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN, color=ft.Colors.WHITE),
                    on_click=self._complete_wizard,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    async def _verify_token(self, e):
        """Verify token and proceed to next step"""
        token = self.token_input.value.strip()
        if not token:
            self.token_status.value = "❌ 请输入Token"
            self.token_status.color = ft.Colors.RED
            self.update()
            return
        
        self.token_status.value = "验证中..."
        self.token_status.color = ft.Colors.ORANGE
        self.update()
        
        try:
            ts.set_token(token)
            pro = ts.pro_api()
            # Simple API call to verify
            pro.trade_cal(exchange='', start_date='20250101', end_date='20250101')
            
            # Save token
            ConfigHandler.save_config({"ts_token": token, "onboarding_complete": False})
            
            self.token_status.value = "✅ Token验证成功"
            self.token_status.color = ft.Colors.GREEN
            self.update()
            
            # Move to next step
            await self._next_step()
            
        except Exception as ex:
            self.token_status.value = f"❌ 验证失败: {str(ex)[:40]}"
            self.token_status.color = ft.Colors.RED
            self.update()

    async def _next_step(self):
        """Move to next step"""
        self.current_step += 1
        self._update_indicators()

    async def _start_sync(self, quick=False):
        """Start data sync"""
        # Disable sync buttons, show cancel button
        self.btn_quick_sync.disabled = True
        self.btn_full_sync.disabled = True
        self.btn_cancel_sync.visible = True
        self.btn_cancel_sync.disabled = False
        
        self.sync_status.value = "正在初始化..."
        self.sync_status.color = ft.Colors.BLUE
        self.sync_progress.value = None  # Indeterminate
        self.update()
        
        # Initialize cancel event
        self.cancel_event = asyncio.Event()
        self.cancel_event.clear()
        
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            processor = DataProcessor()
            await processor.init_data()
            
            if quick:
                # Quick sync
                self.sync_status.value = "同步今日数据..."
                self.update()
                await processor.sync_daily_market_snapshot()
                self.sync_status.value = "✅ 今日数据同步完成"
                self.sync_status.color = ft.Colors.GREEN
            else:
                # Full sync
                self.sync_status.value = "同步股票列表..."
                self.update()
                await processor.sync_stock_basic()
                
                if self.cancel_event.is_set():
                    raise asyncio.CancelledError("User cancelled")

                self.sync_status.value = "同步历史行情 (耗时较长)..."
                self.sync_progress.value = 0
                self.update()
                
                days = 750
                def update_progress(current, total, msg):
                    self.sync_progress.value = current / total
                    self.sync_status.value = f"{msg} ({int(current/total*100)}%)"
                    self.update()
                
                # Pass cancel_event to processor
                await processor.sync_historical_data(
                    days=days, 
                    progress_callback=update_progress,
                    cancel_event=self.cancel_event
                )
                
                if self.cancel_event.is_set():
                     self.sync_status.value = "❌ 同步已取消"
                     self.sync_status.color = ft.Colors.RED
                     self.sync_progress.value = 0
                else:
                    self.sync_status.value = "✅ 历史数据同步完成"
                    self.sync_status.color = ft.Colors.GREEN
                    self.sync_progress.value = 1

            self.update()
            
            if not self.cancel_event.is_set():
                await asyncio.sleep(1)
                await self._next_step()
                
        except Exception as ex:
            import traceback
            logger.error(f"Sync error: {traceback.format_exc()}")
            self.sync_status.value = f"❌ 同步失败: {str(ex)[:40]}"
            self.sync_status.color = ft.Colors.RED
            self.sync_progress.value = 0
            self.update()
            
        finally:
            # Always re-enable buttons and hide cancel
            self.btn_quick_sync.disabled = False
            self.btn_full_sync.disabled = False
            self.btn_cancel_sync.visible = False
            self.update()

    async def _skip_sync(self, e):
        """Skip sync step"""
        await self._next_step()

    async def _finish_setup(self, e):
        """Finish setup and save preferences"""
        # Save schedule preference
        config = {
            "onboarding_complete": True,
            "auto_update_enabled": self.schedule_enabled.value,
            "auto_update_time": "16:30",
        }
        ConfigHandler.save_config(config)
        
        # Move to completion step
        await self._next_step()

    async def _complete_wizard(self, e):
        """Complete wizard and call callback"""
        if self.on_complete:
            self.on_complete()
