import flet as ft
from utils.config_handler import ConfigHandler
from data.tushare_client import TushareClient
from data.cache_manager import CacheManager
from data.data_processor import DataProcessor
from ui.theme import AppColors, AppStyles
import tushare as ts
import logging
import asyncio

logger = logging.getLogger(__name__)

class SettingsView(ft.Container):
    def _safe_update(self):
        """Safely update UI only when page and session are valid."""
        try:
            if self.page is not None:
                self.update()
        except RuntimeError as e:
            # Session destroyed, ignore update
            logger.debug(f"Skipped update due to destroyed session: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error during safe update: {e}")

    def _safe_show_snack(self, message, **kwargs):
        """Safely show snackbar only when page and session are valid."""
        try:
            if self.page is not None:
                self.show_snack(message, **kwargs)
        except RuntimeError:
            # Session destroyed, ignore
            pass
        except Exception as e:
            logger.warning(f"Failed to show snack: {e}")

    def __init__(self):
        super().__init__()
        self.expand = True
        
        # Load existing config
        current_token = ConfigHandler.get_token()
        auto_update_enabled = ConfigHandler.is_auto_update_enabled()
        auto_update_time = ConfigHandler.get_auto_update_time()
        db_queue_size = ConfigHandler.get_db_queue_size()
        sync_concurrency = ConfigHandler.get_sync_concurrency()

        # Concurrency Slider
        self.concurrency_label = ft.Text(f"数据同步并发数: {sync_concurrency}", size=14)
        self.concurrency_slider = ft.Slider(
            min=1, max=10, divisions=9, value=sync_concurrency, 
            label="{value}", on_change=self.on_concurrency_change
        )

        self.queue_size_input = ft.TextField(
            value=str(ConfigHandler.get_db_queue_size()),
            label="缓冲大小",
            width=100,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            hint_text="Default: 1024"
        )
        
        # Health Check UI Components
        self.health_status_row = ft.Row([
            ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREY),
            ft.Text("未检测", color=ft.Colors.GREY)
        ])
        self.health_detail_text = ft.Text("点击刷新按钮开始检查...", size=12, color=ft.Colors.GREY_600)
        
        self.token_input = ft.TextField(
            label="Tushare API Token", 
            password=True, 
            can_reveal_password=True,
            value=current_token,
            width=400,
            on_submit=self.save_and_verify
        )
        
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY)
        self.status_text = ft.Text("未验证", color=ft.Colors.GREY)
        
        # Progress bar for historical sync
        self.progress_bar = ft.ProgressBar(width=400, visible=False)
        self.progress_text = ft.Text("", size=12, color=ft.Colors.BLUE)
        self.sync_button = ft.ElevatedButton(
            text="初始化3年数据", 
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
        )
        self.log_level_dropdown = ft.Dropdown(
            label="日志级别",
            value=ConfigHandler.get_log_level(),
            width=120,
            options=[
                ft.dropdown.Option("DEBUG"),
                ft.dropdown.Option("INFO"),
                ft.dropdown.Option("WARNING"),
                ft.dropdown.Option("ERROR"),
            ],
        )
        self.log_level_dropdown.on_change = self.on_log_level_change
        self.schedule_time.on_change = self.on_schedule_time_change
        
        self.schedule_status = ft.Text(
            self._get_schedule_status_text(auto_update_enabled),
            size=12,
            color=ft.Colors.GREEN if auto_update_enabled else ft.Colors.GREY
        )

        # === Layout Organization ===
        
        # Tab 1: Basic Configuration (API)
        # Tab 1: Basic Configuration (API)
        tab_basic = ft.Container(
            content=ft.Column([
                ft.Text("API 连接配置", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text("请输入您的 Tushare Pro Token 以获取数据权限。", size=14, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=10),
                self.token_input,
                ft.Row([
                    ft.ElevatedButton(
                        text="保存并验证", 
                        icon=ft.Icons.SAVE, 
                        on_click=self.save_and_verify,
                        style=AppStyles.primary_button()
                    ),
                    self.status_icon,
                    self.status_text
                ]),
            ], spacing=20),
            **AppStyles.card()
        )

        # Tab 2: Data Management (Sync, Health, Cache)
        # Health Check
        # Health Check
        health_card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.HEALTH_AND_SAFETY, size=24, color=AppColors.PRIMARY),
                    ft.Text("系统状态检查 (近3年)", size=16, weight=ft.FontWeight.BOLD),
                    ft.IconButton(ft.Icons.REFRESH, on_click=self.refresh_health_status, tooltip="立即检查")
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                self.health_status_row,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                self.health_detail_text,
            ]),
            padding=15,
            bgcolor=ft.Colors.BLUE_GREY_50,
            border_radius=8,
        )

        tab_data = ft.Container(
            content=ft.ListView([
                ft.Text("数据健康度", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                health_card,
                ft.Divider(height=20),
                
                ft.Text("手动数据更新", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Row([
                    ft.ElevatedButton(text="更新今日行情", icon=ft.Icons.UPDATE, on_click=self.update_daily_quotes),
                    ft.ElevatedButton(
                        text="完整日更新", 
                        icon=ft.Icons.SYNC, 
                        on_click=self.full_daily_sync,
                        tooltip="同步行情、估值、资金流、北向持股",
                        style=AppStyles.accent_button()
                    ),
                ]),
                ft.Divider(height=10),
                
                ft.Text("历史初始化", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text("首次使用需拉取历史数据", size=12, color=AppColors.TEXT_SECONDARY),
                ft.Row([self.sync_button]),
                # Progress UI
                ft.Container(
                    content=ft.Column([
                        self.progress_bar,
                        self.progress_text,
                    ]),
                    padding=ft.padding.only(top=5, bottom=5),
                ),
                ft.Divider(height=20),

                ft.Text("缓存管理", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text("当数据出现异常时，可尝试清除缓存重新同步。", size=12, color=AppColors.TEXT_SECONDARY),
                ft.Row([
                    ft.ElevatedButton(
                        text="清除所有缓存", 
                        icon=ft.Icons.DELETE_FOREVER, 
                        on_click=self.confirm_clear_cache,
                        color=AppColors.ERROR
                    ),
                ]),
            ], padding=20, spacing=15),
            **AppStyles.card()
        )

        # Tab 3: Scheduled Tasks
        # Tab 3: Scheduled Tasks
        tab_schedule = ft.Container(
            content=ft.Column([
                ft.Text("每日自动更新", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text("设置每日自动同步数据，无需手动操作。", size=14, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=10),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            self.schedule_enabled,
                        ]),
                        ft.Row([
                            ft.Text("同步时间:", size=14),
                            self.schedule_time,
                            ft.Text("(交易日)", size=12, color=AppColors.TEXT_SECONDARY),
                        ]),
                        ft.Row([
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=AppColors.TEXT_SECONDARY),
                            self.schedule_status,
                        ]),
                    ]),
                    padding=15,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=8,
                ),
                ft.Text("* 需要程序在后台持续运行", size=11, color=AppColors.TEXT_HINT),
            ], spacing=20),
            **AppStyles.card()
        )

        # Tab 4: System Optimization
        # Tab 4: System Optimization
        tab_system = ft.Container(
            content=ft.Column([
                ft.Text("性能与日志", size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                
                ft.Text("日志级别", size=14, color=AppColors.TEXT_SECONDARY),
                self.log_level_dropdown,
                ft.Divider(height=10),
                
                ft.Text(self.concurrency_label.value, size=14, color=AppColors.TEXT_SECONDARY),
                self.concurrency_slider,
                ft.Text("根据网络和CPU性能调整，默认5。过高可能导致被封锁。", size=12, color=AppColors.TEXT_HINT),
                ft.Divider(height=10),
                
                ft.Text("数据库写入缓冲", size=14, color=AppColors.TEXT_SECONDARY),
                ft.Text("调整批量写入的缓冲大小 (需重启生效)", size=12, color=AppColors.TEXT_HINT),
                ft.Row([
                    self.queue_size_input,
                    ft.ElevatedButton(
                        text="保存配置", 
                        icon=ft.Icons.SAVE, 
                        on_click=self.save_queue_size,
                        style=AppStyles.primary_button()
                    )
                ]),
            ], spacing=20),
            **AppStyles.card()
        )

        # Custom Tab Implementation (since ft.Tabs is incompatible)
        self.current_tab_index = 0
        self.tab_contents = [tab_basic, tab_data, tab_schedule, tab_system]
        self.tab_buttons = []
        
        def _on_tab_click(e):
            idx = int(e.control.data)
            self.current_tab_index = idx
            
            # Update content
            self.tab_body.content = self.tab_contents[idx]
            
            # Update buttons style
            for i, btn in enumerate(self.tab_buttons):
                is_selected = (i == idx)
                if is_selected:
                    btn.style = ft.ButtonStyle(
                        color=AppColors.TEXT_ON_PRIMARY, 
                        bgcolor=AppColors.PRIMARY,
                        shape=ft.RoundedRectangleBorder(radius=8),
                    )
                else:
                    btn.style = ft.ButtonStyle(
                        color=AppColors.TEXT_SECONDARY,
                        bgcolor=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=8),
                    )
            self.update()

        def _build_tab_button(text, icon, index):
            btn = ft.ElevatedButton(
                content=ft.Row(
                    [ft.Icon(icon, size=18), ft.Text(text)], 
                    spacing=5,
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER
                ),
                data=str(index),
                on_click=_on_tab_click,
                style=ft.ButtonStyle(
                    color=AppColors.TEXT_ON_PRIMARY if index == 0 else AppColors.TEXT_SECONDARY,
                    bgcolor=AppColors.PRIMARY if index == 0 else ft.Colors.TRANSPARENT,
                    elevation=0,
                    shape=ft.RoundedRectangleBorder(radius=8),
                    alignment=ft.alignment.center,
                )
            )
            self.tab_buttons.append(btn)
            return btn

        # Build Tab Bar
        tab_bar = ft.Container(
            content=ft.Row([
                _build_tab_button("基本配置", ft.Icons.SETTINGS, 0),
                _build_tab_button("数据管理", ft.Icons.STORAGE, 1),
                _build_tab_button("定时任务", ft.Icons.SCHEDULE, 2),
                _build_tab_button("系统优化", ft.Icons.TUNE, 3),
            ], alignment=ft.MainAxisAlignment.START, spacing=10),
            padding=ft.padding.only(bottom=10)
        )

        # Tab Body Container
        self.tab_body = ft.Container(
            content=self.tab_contents[0],
            expand=True
        )

        self.content = ft.Column([
            ft.Text("设置", size=24, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
            tab_bar,
            ft.Divider(height=1, thickness=1, color=AppColors.BORDER),
            self.tab_body
        ], expand=True)

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

    def show_snack(self, message, color=None, **kwargs):
        """Show a snackbar message."""
        snack = ft.SnackBar(content=ft.Text(message), open=True, bgcolor=color, **kwargs)
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

    def on_concurrency_change(self, e):
        """Handle concurrency slider change"""
        val = int(self.concurrency_slider.value)
        self.concurrency_label.value = f"数据同步并发数: {val}"
        ConfigHandler.set_sync_concurrency(val)
        self.show_snack(f"并发数已设置为 {val} (下次同步生效)")
        self.update()

    def on_log_level_change(self, e):
        """Handle log level change"""
        level = e.control.value
        if ConfigHandler.set_log_level(level):
            from utils.logger import update_log_level
            update_log_level(level)
            self.show_snack(f"日志级别已更新为 {level}")
            logger.info(f"User changed log level to {level}")
        else:
            self.show_snack("设置保存失败", color=ft.Colors.RED)

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
        
        # Flet 0.27.x: Use overlay instead of page.dialog
        dialog.open = True
        self.page.overlay.append(dialog)
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

    def save_queue_size(self, e):
        """Save the database queue size"""
        try:
            size_str = self.queue_size_input.value.strip()
            if not size_str:
                self.show_snack("Queue size cannot be empty")
                return
            
            size = int(size_str)
            if size < 10:
                self.show_snack("Queue size too small (min 10)")
                return
            
            ConfigHandler.set_db_queue_size(size)
            self.show_snack("配置已保存，请重启程序生效")
            logger.info(f"DB queue size updated to {size}")
        except Exception as ex:
            self.show_snack(f"保存失败: {str(ex)}")
            logger.error(f"Failed to save queue size: {ex}")

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
        self._safe_update()

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
            
            days = 1095 # 3 years
            success_count = await processor.sync_historical_data(
                days=days, 
                progress_callback=lambda c, t, m: self.update_progress(c, t, m),
                cancel_event=None # TODO: Add cancellation support
            )
            
            if success_count < days * 0.95: # If less than 95% success
                 if success_count == 0:
                     self._safe_show_snack("同步未完成，请重试", color=ft.Colors.RED)
                 else:
                     self._safe_show_snack(f"同步完成但有部分失败 ({days - success_count}天)，建议重试", color=ft.Colors.ORANGE)
            
            # Step 3 - Financial Reports (conditional based on last sync)
            should_sync, reason = await processor.should_sync_financials()
            if should_sync:
                self.progress_text.value = "步骤 3/3: 同步财务报表..."
                self.progress_bar.value = None
                self.update()
                await processor.sync_financial_reports()
                logger.info(f"Financial sync executed because: {reason}")
            else:
                logger.info(f"Skipping financial sync: {reason}")
                self.progress_text.value = "步骤 3/3: 财务数据已是最新，跳过..."
                self.update()
                await asyncio.sleep(0.5)  # Brief pause for UI feedback
            
            # Complete
            self.progress_bar.value = 1
            self.progress_text.value = "✅ 历史数据初始化完成！"
            self.progress_text.color = ft.Colors.GREEN
            self.show_snack("历史数据初始化完成！")
            logger.info("Historical data initialization complete")
            
        except asyncio.CancelledError:
            # Task was cancelled (e.g., user closed window), handle gracefully
            logger.info("Historical sync task was cancelled")
            self.progress_text.value = "同步已取消"
            self.progress_text.color = ft.Colors.ORANGE
        except Exception as ex:
            self.progress_text.value = f"❌ 初始化失败: {str(ex)[:50]}"
            self.progress_text.color = ft.Colors.RED
            self._safe_show_snack(f"初始化失败: {str(ex)[:30]}")
            logger.error(f"Historical initialization failed: {ex}")
        finally:
            self.sync_button.disabled = False
            self._safe_update()
    def refresh_health_status(self, e):
        """Trigger health check"""
        self.health_status_row.controls[0].name = ft.Icons.HOURGLASS_TOP
        self.health_status_row.controls[0].color = ft.Colors.BLUE
        self.health_status_row.controls[1].value = "正在分析数据完整性..."
        self.health_status_row.controls[1].color = ft.Colors.BLUE
        self.health_detail_text.value = "正在扫描本地数据库与Tushare日历比对..."
        self.update()
        self.page.run_task(self.check_health_async)

    async def check_health_async(self):
        """Async health check implementation"""
        try:
            processor = DataProcessor()
            # No need to init_data for read-only checks usually, but to be safe
            # check_data_health handles cache access
            
            result = await processor.check_data_health()
            
            status = result.get('status', 'red')
            
            icon_name = ft.Icons.CHECK_CIRCLE
            color = ft.Colors.GREEN
            text = "数据健康"
            
            if status == 'yellow':
                icon_name = ft.Icons.WARNING
                color = ft.Colors.AMBER
                text = "数据滞后"
            elif status == 'red':
                icon_name = ft.Icons.ERROR
                color = ft.Colors.RED
                text = "数据异常/缺失"
                
            self.health_status_row.controls[0].name = icon_name
            self.health_status_row.controls[0].color = color
            self.health_status_row.controls[1].value = text
            self.health_status_row.controls[1].color = color
            
            detail = (
                f"覆盖率: {result.get('coverage', 'N/A')}\n"
                f"最新官方: {result.get('latest_official', '-')}\n"
                f"最新本地: {result.get('latest_local', '-')}\n"
                f"滞后天数: {result.get('lag_days', 0)}\n"
                f"缺失天数: {result.get('missing_count', 0)}"
            )
            self.health_detail_text.value = detail
            self.update()
            
        except Exception as ex:
            self.health_status_row.controls[1].value = "检查失败"
            self.health_status_row.controls[1].color = ft.Colors.RED
            self.health_detail_text.value = str(ex)
            self.update()
            logger.error(f"Health check UI error: {ex}")
