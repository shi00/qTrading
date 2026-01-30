import flet as ft
from utils.config_handler import ConfigHandler
from data.tushare_client import TushareClient
from data.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.ai_client import AIClient
from ui.theme import AppColors, AppStyles
from ui.components.settings_widgets import DashboardCard, MetricCard, ActionChip, StatusBadge, SectionHeader
import tushare as ts
import logging
import asyncio
from ui.i18n import I18n

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
        self.is_syncing = False
        self.cancel_event = None
        
        # Load existing config
        current_token = ConfigHandler.get_token()
        auto_update_enabled = ConfigHandler.is_auto_update_enabled()
        auto_update_time = ConfigHandler.get_auto_update_time()
        auto_update_time = ConfigHandler.get_auto_update_time()
        db_queue_size = ConfigHandler.get_db_queue_size()
        sync_concurrency = ConfigHandler.get_sync_concurrency()
        enable_news = ConfigHandler.get_config("enable_news_alerts", True)

        # Concurrency Slider
        self.concurrency_label = ft.Text(f"{I18n.get('settings_sync_concurrency')}: {sync_concurrency}", size=14)
        self.concurrency_slider = ft.Slider(
            min=1, max=10, divisions=9, value=sync_concurrency, 
            label="{value}", on_change=self.on_concurrency_change
        )

        self.queue_size_input = ft.TextField(
            value=str(ConfigHandler.get_db_queue_size()),
            label=I18n.get("settings_db_buffer"),
            width=100,
            keyboard_type=ft.KeyboardType.NUMBER,
            input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
            hint_text="默认: 1024"
        )
        
        # Health Check UI Components
        self.health_status_icon = ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREY)
        self.health_status_text = ft.Text(I18n.get("settings_verify_failed"), color=ft.Colors.GREY)
        self.health_status_row = ft.Row([
            self.health_status_icon,
            self.health_status_text
        ])
        self.health_detail_text = ft.Text(I18n.get("settings_check_health"), size=12, color=ft.Colors.GREY_600)
        
        self.token_input = ft.TextField(
            label=I18n.get("settings_token"), 
            password=True, 
            can_reveal_password=True,
            value=current_token,
            width=400,
            on_submit=self.save_and_verify_tushare
        )
        
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY)
        self.status_text = ft.Text(I18n.get("settings_verify_failed"), color=ft.Colors.GREY)
        
        # Progress bar for historical sync
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
        
        # Scheduled task controls
        self.schedule_enabled = ft.Switch(
            label=I18n.get("settings_auto_update"),
            value=auto_update_enabled,
            on_change=self.on_schedule_toggle
        )
        
        self.news_alerts_enabled = ft.Switch(
            label=I18n.get("settings_news_alerts"),
            value=enable_news,
            on_change=self.on_news_toggle
        )
        
        self.schedule_time = ft.Dropdown(
            label=I18n.get("settings_update_time"),
            width=150,
            value=auto_update_time,
            options=[
                ft.dropdown.Option("15:30", I18n.get("settings_opt_1530")),
                ft.dropdown.Option("16:00", "16:00"),
                ft.dropdown.Option("16:30", "16:30"),
                ft.dropdown.Option("17:00", "17:00"),
                ft.dropdown.Option("18:00", "18:00"),
                ft.dropdown.Option("20:00", I18n.get("settings_opt_2000")),
            ],
        )
        self.log_level_dropdown = ft.Dropdown(
            label=I18n.get("settings_log_level"),
            value=ConfigHandler.get_log_level(),
            width=120,
            options=[
                ft.dropdown.Option("DEBUG", "调试 (DEBUG)"),
                ft.dropdown.Option("INFO", "信息 (INFO)"),
                ft.dropdown.Option("WARNING", "警告 (WARN)"),
                ft.dropdown.Option("ERROR", "错误 (ERROR)"),
            ],
        )
        self.log_level_dropdown.on_change = self.on_log_level_change
        self.schedule_time.on_change = self.on_schedule_time_change
        
        self.schedule_status = ft.Text(
            self._get_schedule_status_text(auto_update_enabled),
            size=12,
            color=ft.Colors.GREEN if auto_update_enabled else ft.Colors.GREY
        )
        
        # Language dropdown removed as per user request to enforce Chinese only
        # self.language_dropdown = ft.Dropdown(...) 
        

        # === AI Configuration ===
        # === AI Configuration ===
        ai_cfg = ConfigHandler.get_ai_config()
        self.ai_api_key_input = ft.TextField(
            label=I18n.get("settings_ai_api_key_label"),
            password=True,
            can_reveal_password=True,
            value=ai_cfg.get('ai_api_key', ''),
            width=400,
            hint_text="sk-..."
        )
        self.ai_base_url_input = ft.TextField(
            label=I18n.get("settings_ai_base_url_label"),
            value=ai_cfg.get('ai_base_url', 'https://api.deepseek.com'),
            width=400,
            hint_text="https://api.deepseek.com"
        )
        self.ai_model_dropdown = ft.Dropdown(
            label=I18n.get("settings_ai_model"),
            value=ai_cfg.get('ai_model_name', 'deepseek-chat'),
            width=200,
            options=[
                ft.dropdown.Option("deepseek-chat", "DeepSeek-V3 (deepseek-chat)"),
                ft.dropdown.Option("deepseek-reasoner", "DeepSeek-R1 (deepseek-reasoner)"),
                ft.dropdown.Option("moonshot-v1-8k", "Moonshot Kimi"),
                ft.dropdown.Option("qwen2.5-max", "Alibaba Qwen"),
                ft.dropdown.Option("gpt-4o", "OpenAI GPT-4o"),
            ]
        )
        
        # === AI Tuning Controls ===
        current_max_candidates = ConfigHandler.get_ai_max_candidates()
        current_min_turnover = ConfigHandler.get_strategy_min_turnover()
        current_ai_concurrency = ConfigHandler.get_ai_concurrency()

        self.ai_max_candidates_input = ft.TextField(
            label=I18n.get("settings_max_candidates"),
            value=str(current_max_candidates),
            width=190,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="默认: 30",
            tooltip=I18n.get("settings_hint_ai_cost")
        )
        
        self.strategy_min_turnover_input = ft.TextField(
            label=I18n.get("settings_min_turnover"),
            value=str(current_min_turnover),
            width=190,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="默认: 2.0",
            tooltip=I18n.get("settings_hint_turnover")
        )

        self.ai_concurrency_label = ft.Text(f"{I18n.get('settings_ai_concurrency')}: {current_ai_concurrency}", size=14)
        self.ai_concurrency_slider = ft.Slider(
            min=1, max=10, divisions=9, value=current_ai_concurrency,
            label="{value}",
            on_change=self.on_ai_concurrency_change
        )
        
        self.ai_status_icon = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY)
        self.ai_status_text = ft.Text("未连接", color=ft.Colors.GREY)

        # === AI Prompt Editor ===
        self.ai_prompt_input = ft.TextField(
            label=I18n.get("settings_ai_prompt"),
            value=ConfigHandler.get_ai_system_prompt(),
            multiline=True,
            min_lines=5,
            max_lines=15,
            text_size=12,
            hint_text=I18n.get("settings_ai_prompt_hint")
        )
        self.btn_reset_prompt = ft.TextButton(
            text=I18n.get("settings_reset_prompt"),
            icon=ft.Icons.RESTORE,
            on_click=self.reset_ai_prompt
        )

        # === Layout Organization ===
        
        # Tab 1: Basic Configuration (API)
        # Store labels to allow updates
        self.txt_sec_api = ft.Text(I18n.get("settings_sec_api"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.txt_token_desc = ft.Text(I18n.get("settings_token_desc"), size=14, color=AppColors.TEXT_SECONDARY)
        
        self.btn_save_token = ft.ElevatedButton(
            text=I18n.get("settings_save_token"), 
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE, 
            on_click=self.save_and_verify_tushare,
            style=AppStyles.primary_button(),
            width=400
        )
        
        self.txt_sec_ai = ft.Text(I18n.get("settings_sec_ai"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.txt_ai_desc = ft.Text(I18n.get("settings_ai_desc"), size=14, color=AppColors.TEXT_SECONDARY)
        self.txt_sec_tuning = ft.Text(I18n.get("settings_sec_tuning"), size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.txt_hint_ai_model = ft.Text(I18n.get("settings_hint_ai_model"), size=12, color=AppColors.TEXT_HINT)
        
        self.btn_save_ai = ft.ElevatedButton(
            text=I18n.get("settings_save_ai"), 
            icon=ft.Icons.SAVE, 
            on_click=self.save_ai_settings,
            style=AppStyles.primary_button(),
            width=400
        )

        # Tab 1: Data Source (API + Data Ops + Health)
        
        # --- Data Source Components Init ---
        # Data Command Center Components
        
        # 1. Health Status Dashboard
        self.metric_sync = MetricCard("最后更新", "今日 15:30", ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
        self.metric_coverage = MetricCard("数据覆盖", "5000+ 股票", ft.Icons.DATA_USAGE, AppColors.INFO)
        self.metric_health = MetricCard("系统健康", "检测中...", ft.Icons.HEALTH_AND_SAFETY, ft.Colors.ORANGE)
        self.metric_storage = MetricCard("存储占用", "计算中...", ft.Icons.STORAGE, ft.Colors.GREY)

        self.health_dashboard = DashboardCard(
            content=ft.Column([
                ft.Row([
                    SectionHeader(I18n.get("settings_sec_health") + " (3Y)"),
                    ft.IconButton(ft.Icons.REFRESH, on_click=self.refresh_health_status, tooltip="刷新状态")
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.ResponsiveRow([
                    ft.Column([self.metric_sync], col={"sm": 6, "md": 3}),
                    ft.Column([self.metric_coverage], col={"sm": 6, "md": 3}),
                    ft.Column([self.metric_health], col={"sm": 6, "md": 3}),
                    ft.Column([self.metric_storage], col={"sm": 6, "md": 3}),
                ]),
                ft.Container(height=10),
                self.health_detail_text
            ])
        )

        # 2. Action Console (Smart Chips)
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
        self.btn_update_today = ft.ElevatedButton(text=I18n.get("settings_update_today"), icon=ft.Icons.UPDATE, on_click=self.update_daily_quotes)
        self.btn_full_sync = ft.ElevatedButton(
            text=I18n.get("settings_full_sync"), 
            icon=ft.Icons.SYNC, 
            on_click=self.full_daily_sync,
            tooltip=I18n.get("settings_hint_sync_full"),
            style=AppStyles.accent_button()
        )
        
        self.txt_init_data = ft.Text(I18n.get("settings_init_data"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.txt_hint_first_run = ft.Text(I18n.get("settings_hint_first_run"), size=12, color=AppColors.TEXT_SECONDARY)
        
        # --- End Data Source Components ---

        # Combine Tushare API and Data Operations into one cohesive tab
        tab_data_source = ft.Container(
            content=ft.ListView(controls=[
                # 1. Health Dashboard (Visual Status)
                self.health_dashboard,
                
                # 2. Action Console (Smart Operations)
                self.action_console,
                
                # 3. Connection Settings (API Config)
                DashboardCard(
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
                ),

                # 4. Historical Data (Initialization)
                DashboardCard(
                    content=ft.Column([
                        # Header Row: Info on Left, Button on Right
                        ft.Row([
                            ft.Column([
                                SectionHeader(I18n.get("settings_init_data")),
                                ft.Text(I18n.get("settings_hint_first_run"), size=12, color=AppColors.TEXT_SECONDARY),
                            ]),
                            self.sync_button
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        
                        # Progress Area
                        ft.Container(
                             content=ft.Column([
                                 self.progress_bar,
                                 self.progress_text,
                             ]),
                             padding=ft.padding.only(top=5),
                         ),
                    ], spacing=5)
                ),
                
            ], spacing=15, padding=ft.padding.only(bottom=50)),
            expand=True
        )



        # Tab 2: AI Brain (LLM + Strategy) - Redesigned
        tab_ai_brain = ft.Container(
            content=self._build_ai_tab_content(),
            expand=True
        )


        # Tab 3: Scheduled Tasks
        self.txt_auto_update = ft.Text(I18n.get("settings_auto_update"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.txt_auto_desc = ft.Text(I18n.get("settings_auto_desc"), size=14, color=AppColors.TEXT_SECONDARY)
        self.txt_update_time_label = ft.Text(f"{I18n.get('settings_update_time')}:", size=14)
        self.txt_trading_days = ft.Text(I18n.get("settings_trading_days"), size=12, color=AppColors.TEXT_SECONDARY)
        self.txt_hint_bg_run = ft.Text(I18n.get("settings_hint_bg_run"), size=11, color=AppColors.TEXT_HINT)

        # Tab 3: Automation (Scheduler)
        tab_automation = ft.Container(
            content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=[
                self.txt_auto_update,
                self.txt_auto_desc,
                ft.Container(height=10),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            self.schedule_enabled,
                        ]),
                        # News Alert moved to Tab 4
                        ft.Row([
                            self.txt_update_time_label,
                            self.schedule_time,
                            self.txt_trading_days,
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
                self.txt_hint_bg_run,
            ], spacing=20),
            **AppStyles.card()
        )

        # Tab 4: Notifications (News)
        self.txt_notify_title = ft.Text(I18n.get("settings_notify_title"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        
        tab_notifications = ft.Container(
            content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=[
                self.txt_notify_title,
                ft.Container(height=10),
                ft.Container(
                    content=ft.Row([
                        self.news_alerts_enabled,
                    ]),
                    padding=15,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=8,
                ),
                ft.Text(I18n.get("settings_notify_desc"), size=14, color=AppColors.TEXT_SECONDARY)
            ], spacing=20),
            **AppStyles.card()
        )

        # Tab 4: System Optimization
        self.txt_general = ft.Text(I18n.get("settings_general"), size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.txt_lang_label = ft.Text(I18n.get("settings_language"), size=14, color=AppColors.TEXT_SECONDARY)
        self.txt_log_label = ft.Text(I18n.get("settings_log_level"), size=14, color=AppColors.TEXT_SECONDARY)
        self.txt_concurrency_intro = ft.Text(self.concurrency_label.value, size=14, color=AppColors.TEXT_SECONDARY) # Dynamic
        self.txt_hint_cpu = ft.Text(I18n.get("settings_hint_cpu"), size=12, color=AppColors.TEXT_HINT)
        
        self.txt_db_buffer_label = ft.Text(I18n.get("settings_db_buffer"), size=14, color=AppColors.TEXT_SECONDARY)
        self.txt_buffer_desc = ft.Text(I18n.get("settings_buffer_desc"), size=12, color=AppColors.TEXT_HINT)
        
        self.btn_save_queue = ft.ElevatedButton(
            text=I18n.get("settings_save_config"), 
            icon=ft.Icons.SAVE, 
            on_click=self.save_queue_size,
            style=AppStyles.primary_button()
        )

        # Tab 5: System (Cache, Logs, Perf)
        tab_system = ft.Container(
            content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=[
                # Cache controls removed as per user request (redundant with Data Tab)
                
                self.txt_general,

                
                self.txt_log_label,
                self.log_level_dropdown,
                ft.Divider(height=10),
                
                self.txt_concurrency_intro,
                self.concurrency_slider,
                self.txt_hint_cpu,
                ft.Divider(height=10),
                
                self.txt_db_buffer_label,
                self.txt_buffer_desc,
                ft.Row([
                    self.queue_size_input,
                    self.btn_save_queue
                ]),
            ], spacing=20),
            **AppStyles.card()
        )

        # Custom Tab Implementation (since ft.Tabs is incompatible)
        self.current_tab_index = 0
        # Reorganized 5 Tabs:
        # 1. Data Source (API + Data Ops)
        # 2. AI Brain (LLM + Strategy)
        # 3. Automation (Scheduler)
        # 4. Notifications (News)
        # 5. System (Cache + Logs)
        self.tab_contents = [tab_data_source, tab_ai_brain, tab_automation, tab_notifications, tab_system]
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
                _build_tab_button(I18n.get("settings_tab_data"), ft.Icons.STORAGE, 0),
                _build_tab_button(I18n.get("settings_tab_ai"), ft.Icons.SMART_TOY, 1),
                _build_tab_button(I18n.get("settings_tab_tasks"), ft.Icons.SCHEDULE, 2),
                _build_tab_button(I18n.get("settings_tab_notify"), ft.Icons.NOTIFICATIONS, 3),
                _build_tab_button(I18n.get("settings_tab_system"), ft.Icons.TUNE, 4),
            ], alignment=ft.MainAxisAlignment.START, spacing=10, scroll=ft.ScrollMode.HIDDEN),
            padding=ft.padding.only(bottom=10)
        )

        # Tab Body Container
        self.tab_body = ft.Container(
            content=self.tab_contents[0],
            expand=True
        )

        self.header_title = ft.Text(I18n.get("settings_title"), size=24, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        self.content = ft.Column([
            self.header_title,
            tab_bar,
            ft.Divider(height=1, thickness=1, color=AppColors.BORDER),
            self.tab_body
        ], expand=True)

        # Init I18n subscription
        self.did_mount = self._on_mount

    def _build_ai_tab_content(self):
        """Build the content for the AI Settings Tab (redesigned)"""
        
        # Test Connection Button
        self.btn_test_connection = ft.OutlinedButton(
            text="测试连接",
            icon=ft.Icons.VIBRATION,
            on_click=self._test_ai_connection,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=15, vertical=10)
            )
        )

        return ft.ListView(controls=[
            # Card 1: Connection & Security
            DashboardCard(
                content=ft.Column([
                    ft.Row([
                        SectionHeader(I18n.get("settings_sec_ai")),
                    ]),
                    
                    ft.Text(I18n.get("settings_ai_desc"), size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Container(height=10),
                    
                    # API Config Grid
                    ft.ResponsiveRow([
                        ft.Column([self.ai_base_url_input], col={"sm": 12, "md": 6}),
                        ft.Column([self.ai_model_dropdown], col={"sm": 12, "md": 6}),
                        ft.Column([self.ai_api_key_input], col={"sm": 12}),
                    ], run_spacing=10),
                    
                    ft.Container(height=10),
                    ft.Row([
                        # Status Indicator (Moved here)
                        ft.Container(
                            content=ft.Row([
                                self.ai_status_icon,
                                self.ai_status_text
                            ], spacing=5),
                            padding=ft.padding.symmetric(horizontal=10, vertical=5),
                            border_radius=12,
                            # Remove background or keep subtle? User wants it "with button". 
                            # Let's keep it subtle but clean.
                        ),
                        ft.Container(width=10),
                        self.btn_test_connection
                    ], alignment=ft.MainAxisAlignment.END, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                ])
            ),

            # Card 2: Strategy Engine (Performance Tuning)
            DashboardCard(
                content=ft.Column([
                    ft.Row([
                        SectionHeader(I18n.get("settings_sec_tuning")),
                        ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    
                    ft.Text("调整AI分析的深度与广度，平衡成本与性能", size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Container(height=10),

                    ft.ResponsiveRow([
                        # Left: Numeric Inputs
                        ft.Column([
                            ft.Row([
                                self.ai_max_candidates_input,
                                ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT,
                                      tooltip="并行分析的候选股票数量上限，数量越多API消耗越大")
                            ]),
                            ft.Container(height=5),
                            ft.Row([
                                self.strategy_min_turnover_input,
                                ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT,
                                      tooltip="剔除流动性过低的股票（换手率低于此百分比）")
                            ]),
                        ], col={"sm": 12, "md": 6}),

                        # Right: Concurrency Slider
                        ft.Column([
                            ft.Container(
                                content=ft.Column([
                                    self.ai_concurrency_label,
                                    self.ai_concurrency_slider,
                                    ft.Text(I18n.get("settings_hint_ai_model"), size=11, color=AppColors.TEXT_HINT)
                                ]),
                                padding=10,
                                border=ft.border.all(1, AppColors.BORDER),
                                border_radius=8
                            )
                        ], col={"sm": 12, "md": 6})
                    ])
                ])
            ),

            # Card 3: System Persona (Prompt)
            DashboardCard(
                content=ft.Column([
                    ft.Row([
                        SectionHeader("系统人设 (System Prompt)"),
                        self.btn_reset_prompt
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    
                    ft.Container(
                        content=self.ai_prompt_input,
                        border=ft.border.all(1, AppColors.BORDER),
                        border_radius=8,
                        bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.BLACK) # Subtle bg for editor feel
                    ),
                    ft.Text(I18n.get("settings_ai_prompt_hint"), size=11, color=AppColors.TEXT_HINT)
                ])
            ),

            # Bottom Action Bar
            ft.Container(
                content=ft.Row([
                    self.btn_save_ai
                ], alignment=ft.MainAxisAlignment.END),
                padding=ft.padding.only(top=10, bottom=30)
            )

        ], spacing=15, padding=ft.padding.only(bottom=50))

    def _on_mount(self):
        I18n.subscribe(self.refresh_locale)
        self.refresh_locale()

    def refresh_locale(self):
        """Update UI strings on locale change"""
        # Header
        self.header_title.value = I18n.get("settings_title")
        
        # Tab Buttons
        if len(self.tab_buttons) >= 5:
            self.tab_buttons[0].content.controls[1].value = I18n.get("settings_tab_data")
            self.tab_buttons[1].content.controls[1].value = I18n.get("settings_tab_ai")
            self.tab_buttons[2].content.controls[1].value = I18n.get("settings_tab_tasks")
            self.tab_buttons[3].content.controls[1].value = I18n.get("settings_tab_notify")
            self.tab_buttons[4].content.controls[1].value = I18n.get("settings_tab_system")

        # Tab 1: Data Source
        self.txt_sec_api.value = I18n.get("settings_sec_api")
        self.txt_token_desc.value = I18n.get("settings_token_desc")
        self.token_input.label = I18n.get("settings_token")
        self.btn_save_token.text = I18n.get("settings_save_token")
        
        # Safe status text update for Tushare
        # Safe status text update for Tushare
        if self.status_text.value in [I18n.STRINGS["zh"]["settings_verify_failed"]]:
             self.status_text.value = I18n.get("settings_verify_failed")
        elif self.status_text.value in [I18n.STRINGS["zh"]["settings_status_verifying"]]:
             self.status_text.value = I18n.get("settings_status_verifying")
        elif self.status_text.value in [I18n.STRINGS["zh"]["settings_snack_token_verified"]]:
             self.status_text.value = I18n.get("settings_snack_token_verified")

        # Tab 1: Dashboard Updates
        # Note: Dashboard components (DashboardCard) text content is usually static or 
        # rebuilt on page load. For now, we only update controls that persist.
        self.sync_button.text = I18n.get("settings_init_data")
        self.sync_button.tooltip = I18n.get("settings_init_desc")

        # Tab 2: AI Brain
        # Tab 2: AI Brain
        # Rebuild content to support new DashboardCard layout
        if len(self.tab_contents) > 1:
            self.tab_contents[1].content = self._build_ai_tab_content()

        # Tab 3: Automation
        self.txt_auto_update.value = I18n.get("settings_auto_update")
        self.txt_auto_desc.value = I18n.get("settings_auto_desc")
        self.schedule_enabled.label = I18n.get("settings_auto_update")
        self.txt_update_time_label.value = f"{I18n.get('settings_update_time')}:"
        self.schedule_time.label = I18n.get("settings_update_time")
        # Update options labels for schedule time if they have translatable text
        self.schedule_time.options[0].text = I18n.get("settings_opt_1530")
        self.schedule_time.options[5].text = I18n.get("settings_opt_2000")
        
        self.txt_trading_days.value = I18n.get("settings_trading_days")
        self.txt_hint_bg_run.value = I18n.get("settings_hint_bg_run")
        self.schedule_status.value = self._get_schedule_status_text(self.schedule_enabled.value)

        # Tab 4: Notifications (New)
        self.txt_notify_title.value = I18n.get("settings_notify_title")
        self.news_alerts_enabled.label = I18n.get("settings_news_alerts")

        # Tab 5: System
        self.txt_general.value = I18n.get("settings_general")
        # Language label removed
        # self.txt_lang_label.value = I18n.get("settings_language")
        # self.language_dropdown.label = I18n.get("settings_language")
        # self.language_dropdown.options[0].text = I18n.get("settings_lang_zh")
        # self.language_dropdown.options[1].text = I18n.get("settings_lang_en")
        
        self.txt_log_label.value = I18n.get("settings_log_level")
        self.log_level_dropdown.label = I18n.get("settings_log_level")
        
        self.concurrency_label.value = f"{I18n.get('settings_sync_concurrency')}: {int(self.concurrency_slider.value)}"
        self.txt_concurrency_intro.value = self.concurrency_label.value
        self.txt_hint_cpu.value = I18n.get("settings_hint_cpu")
        
        self.txt_db_buffer_label.value = I18n.get("settings_db_buffer")
        self.queue_size_input.label = I18n.get("settings_db_buffer")
        self.txt_buffer_desc.value = I18n.get("settings_buffer_desc")
        self.btn_save_queue.text = I18n.get("settings_save_config")
        
        self.btn_save_queue.text = I18n.get("settings_save_config")

        self.update()

    # Language switch disabled
    # def on_language_change(self, e):
    #    lang = self.language_dropdown.value
    #    I18n.set_locale(lang)
    #    self.show_snack(f"Language switched to {lang}")

    def _get_schedule_status_text(self, enabled):
        if enabled:
            return I18n.get("settings_status_auto_on")
        return I18n.get("settings_status_auto_off")

    def on_schedule_toggle(self, e):
        """Handle schedule toggle"""
        enabled = self.schedule_enabled.value
        ConfigHandler.save_config({"auto_update_enabled": enabled})
        
        self.schedule_status.value = self._get_schedule_status_text(enabled)
        self.schedule_status.color = ft.Colors.GREEN if enabled else ft.Colors.GREY
        self.update()
        
        if enabled:
            self.show_snack(I18n.get("settings_snack_auto_on"))
        else:
            self.show_snack(I18n.get("settings_snack_auto_off"))

    def on_news_toggle(self, e):
        """Handle news toggle"""
        enabled = self.news_alerts_enabled.value
        ConfigHandler.save_config({"enable_news_alerts": enabled})
        
        # Dynamically start/stop service if running
        # We need a way to access the service singleton or restart app
        # For now just save config and notify
        from data.news_subscription import NewsSubscriptionService
        service = NewsSubscriptionService()
        if enabled:
            service.start(callback=lambda msg: self.page.open(ft.SnackBar(ft.Text(f"📰 {msg}"), open=True)))
            self.show_snack(I18n.get("settings_snack_news_on"))
        else:
            service.stop()
            self.show_snack(I18n.get("settings_snack_news_off"))

    def on_schedule_time_change(self, e):
        """Handle schedule time change"""
        time = self.schedule_time.value
        ConfigHandler.save_config({"auto_update_time": time})
        self.show_snack(I18n.get("settings_snack_time_set").format(time=time))

    def show_snack(self, message, color=None, **kwargs):
        """Show a toast message (Proposal A)."""
        if hasattr(self.page, "show_toast"):
            # Map color to type
            msg_type = "info"
            if color == ft.Colors.RED:
                msg_type = "error"
            elif color == ft.Colors.GREEN:
                msg_type = "success"
            elif color == ft.Colors.ORANGE or color == ft.Colors.AMBER:
                msg_type = "warning"
            
            self.page.show_toast(message, type=msg_type)
        else:
            # Fallback
            snack = ft.SnackBar(content=ft.Text(message), open=True, bgcolor=color, **kwargs)
            self.page.overlay.append(snack)
            self.page.update()

    def _safe_show_snack(self, message, color=None):
        self.show_snack(message, color)


    async def save_ai_settings(self, e):
        """Save AI settings and verify connection"""
        try:
            ai_key = self.ai_api_key_input.value.strip()
            ai_base = self.ai_base_url_input.value.strip()
            ai_model = self.ai_model_dropdown.value
            ai_prompt = self.ai_prompt_input.value
            
            # Save Tuning Params
            try:
                max_cand = int(self.ai_max_candidates_input.value)
                min_turn = float(self.strategy_min_turnover_input.value)
                ConfigHandler.set_ai_max_candidates(max_cand)
                ConfigHandler.set_strategy_min_turnover(min_turn)
            except ValueError:
                self.show_snack("参数错误：数量必须为整数，换手率必须为数字", color=ft.Colors.RED)
                return

            ConfigHandler.save_ai_config(ai_key, ai_base, ai_model)
            ConfigHandler.save_ai_system_prompt(ai_prompt)
            
            # Reload and Verify
            self.ai_status_text.value = I18n.get("settings_status_verifying")
            self.ai_status_text.color = ft.Colors.ORANGE
            self.ai_status_icon.icon = ft.Icons.HOURGLASS_EMPTY
            self.ai_status_icon.color = ft.Colors.ORANGE
            self.update()
            
            from data.ai_client import AIClient
            client = AIClient()
            await client.reload_config()
            
            if not ai_key:
                 self.ai_status_text.value = I18n.get("settings_status_no_key")
                 self.ai_status_text.color = ft.Colors.GREY
                 self.ai_status_icon.icon = ft.Icons.CIRCLE
                 self.ai_status_icon.color = ft.Colors.GREY
                 self._safe_update()
                 return
            
            # ... verification logic continues ...
            try:
                success = await client.verify_connection()
                if success:
                    self.ai_status_text.value = I18n.get("settings_status_verify_ok")
                    self.ai_status_text.color = ft.Colors.GREEN
                    self.ai_status_icon.icon = ft.Icons.CHECK_CIRCLE
                    self.ai_status_icon.color = ft.Colors.GREEN
                else:
                    self.ai_status_text.value = I18n.get("settings_status_verify_err").format(error="Unknown")
                    self.ai_status_text.color = ft.Colors.RED
                    self.ai_status_icon.icon = ft.Icons.ERROR
                    self.ai_status_icon.color = ft.Colors.RED
            except Exception as ex:
                self.ai_status_text.value = I18n.get("settings_status_verify_err").format(error=str(ex)[:20])
                self.ai_status_text.color = ft.Colors.RED
                self.ai_status_icon.icon = ft.Icons.ERROR
                self.ai_status_icon.color = ft.Colors.RED
            
            self._safe_update()
            self.show_snack(I18n.get("settings_snack_ai_saved"))
            
        except Exception as e:
            logger.error(f"Error saving AI settings: {e}")
            self.show_snack(I18n.get("settings_snack_ai_error").format(error=str(e)))

    def reset_ai_prompt(self, e):
        """Reset AI system prompt to default"""
        from utils.config_handler import DEFAULT_AI_PROMPT
        self.ai_prompt_input.value = DEFAULT_AI_PROMPT
        self.update()
        self.show_snack(I18n.get("settings_snack_prompt_reset"))

    def on_ai_concurrency_change(self, e):
        """Handle AI concurrency slider change"""
        val = int(self.ai_concurrency_slider.value)
        self.ai_concurrency_label.value = f"{I18n.get('settings_ai_concurrency')}: {val}"
        ConfigHandler.set_ai_concurrency(val)
        self.update()

    def save_and_verify_tushare(self, e):
        """Save and verify Tushare token only"""
        token = self.token_input.value.strip()
        if not token:
             self.show_snack(I18n.get("settings_snack_token_empty"))
             return
             
        ConfigHandler.save_token(token)
        
        # Verify token
        self.status_text.value = I18n.get("settings_status_verifying")
        self.status_text.color = ft.Colors.ORANGE
        self.update()
        
        try:
            ts.set_token(token)
            pro = ts.pro_api()
            # Simple API call to verify
            pro.trade_cal(exchange='', start_date='20250101', end_date='20250101')
            
            self.status_text.value = I18n.get("settings_snack_token_verified")
            self.status_text.color = ft.Colors.GREEN
            self.status_icon.icon = ft.Icons.CHECK_CIRCLE
            self.status_icon.color = ft.Colors.GREEN
            logger.info("Token verified successfully")
        except Exception as ex:
            error_msg = I18n.get("settings_snack_token_fail").format(error=str(ex)[:30])
            self.status_text.value = error_msg
            self.status_text.color = ft.Colors.RED
            self.status_icon.icon = ft.Icons.ERROR
            self.status_icon.color = ft.Colors.RED
            logger.error(f"Token verification failed: {ex}")
        
        self.update()

    def on_concurrency_change(self, e):
        """Handle concurrency slider change"""
        val = int(self.concurrency_slider.value)
        self.concurrency_label.value = f"{I18n.get('settings_sync_concurrency')}: {val}"
        self.txt_concurrency_intro.value = self.concurrency_label.value
        ConfigHandler.set_sync_concurrency(val)
        self.show_snack(I18n.get("settings_snack_concurrency_set").format(val=val))
        self.update()

    def on_log_level_change(self, e):
        """Handle log level change"""
        level = e.control.value
        if ConfigHandler.set_log_level(level):
            from utils.logger import update_log_level
            update_log_level(level)
            self.show_snack(I18n.get("settings_snack_log_level").format(level=level))
            logger.info(f"User changed log level to {level}")
        else:
            self.show_snack("Failed to save settings", color=ft.Colors.RED)

    def confirm_clear_cache(self, e):
        """Show confirmation dialog before clearing cache"""
        logger.info("Confirm clear cache clicked")
        try:
            if not self.page:
                logger.error("Page object is None in confirm_clear_cache")
                self._safe_show_snack("页面未初始化", color=ft.Colors.ERROR)
                return

            def close_dialog(e):
                self.page.close(dialog)

            def confirm_clear(e):
                self.page.close(dialog)
                # Run async explicitly
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
            
            self.page.open(dialog)
            
        except Exception as ex:
            logger.error(f"Error opening clear cache dialog: {ex}")
            self._safe_show_snack(f"无法打开弹窗: {ex}", color=ft.Colors.ERROR)

    async def clear_cache_async(self):
        """Actually clear the cache"""
        if self.is_syncing:
             self.show_snack("正在进行数据同步，请稍后再试", color=ft.Colors.ERROR)
             return

        self._set_sync_busy(True, self.action_clear_cache)
        # Update Subtitle to "Cleaning..."
        try:
             self.action_clear_cache.content.controls[1].controls[1].value = "正在清理缓存..."
             self.action_clear_cache.update()
        except: pass

        try:
            cache_mgr = CacheManager()
            await cache_mgr.init_db()
            await cache_mgr.clear_all_cache()
            self.show_snack("缓存已清理完成！")
            logger.info("Cache cleared successfully")
            self.page.pubsub.send_all("cache_cleared")
        except Exception as ex:
            self.show_snack(f"清理失败: {str(ex)[:30]}")
            logger.error(f"Cache clear failed: {ex}")
        finally:
             # Restore Subtitle
             try:
                 self.action_clear_cache.content.controls[1].controls[1].value = "清除缓存并重新校验"
             except: pass
             self._set_sync_busy(False)

    def save_queue_size(self, e):
        """Save the database queue size"""
        try:
            size_str = self.queue_size_input.value.strip()
            if not size_str:
                self.show_snack(I18n.get("snack_queue_empty"))
                return
            
            size = int(size_str)
            if size < 10:
                self.show_snack(I18n.get("snack_queue_min"))
                return
            
            ConfigHandler.set_db_queue_size(size)
            self.show_snack(I18n.get("snack_queue_saved"))
            logger.info(f"DB queue size updated to {size}")
        except Exception as ex:
            self.show_snack(f"{I18n.get('snack_save_fail')}: {str(ex)}")
            logger.error(f"Failed to save queue size: {ex}")

    def _set_sync_busy(self, is_busy: bool, active_btn: ft.Control = None):
        """Unified state management for sync buttons with visual feedback"""
        self.is_syncing = is_busy
        
        # Use simple names for logic
        # action_update_today -> ActionChip (Container)
        # action_full_sync -> ActionChip (Container)
        # action_clear_cache -> ActionChip (Container)
        # sync_button -> ElevatedButton (Standard)
        
        sync_controls = [
            self.action_update_today,
            self.action_full_sync,
            self.action_clear_cache,
            self.sync_button
        ]
        
        for ctrl in sync_controls:
            if is_busy:
                # Disable all
                ctrl.disabled = True
                
                # Visual Feedback
                if isinstance(ctrl, ActionChip):
                    ctrl.opacity = 0.5
                else:
                    # ElevatedButton (Sync Button)
                    ctrl.style = ft.ButtonStyle(
                        color=ft.Colors.GREY_500,
                        bgcolor=ft.Colors.GREY_200,
                    )
                
                # Exception: Active Cancel Button (Only for Historical Sync Button)
                if ctrl == self.sync_button and active_btn == self.sync_button:
                     ctrl.disabled = False 
                     # Style is set in init_historical_data (Red Stop button)
            else:
                # Re-enable all
                ctrl.disabled = False
                
                # Restore Visuals
                if isinstance(ctrl, ActionChip):
                    ctrl.opacity = 1.0
                else:
                    # ElevatedButton (Sync Button)
                    # Restore Sync button style
                    ctrl.style = AppStyles.primary_button()
            
            ctrl.update()
                
        self.update()

    def update_daily_quotes(self, e):
        """更新今日行情数据"""
        if self.is_syncing: return
        self.show_snack(I18n.get("snack_daily_sync_start"))
        self._set_sync_busy(True, self.action_update_today)
        
        # Update ActionChip Subtitle
        # Row -> Column -> Subtitle Text
        try:
            self.action_update_today.content.controls[1].controls[1].value = "正在同步今日数据..."
            self.action_update_today.update()
        except: pass
        
        self.page.run_task(self.sync_daily_async)

    async def sync_daily_async(self):
        """异步同步每日行情"""
        try:
            processor = DataProcessor()
            await processor.init_data()
            df = await processor.sync_daily_market_snapshot()
            if df is not None and not df.empty:
                msg = I18n.get("snack_daily_sync_done").format(count=len(df))
                self.show_snack(msg)
                logger.info(msg)
            else:
                self.show_snack(I18n.get("snack_daily_sync_nodata"))
                logger.warning("Daily sync returned no data")
        except Exception as ex:
            self.show_snack(f"Error: {str(ex)[:30]}")
            logger.error(f"Daily sync failed: {ex}")
        finally:
             # Restore Subtitle
             try:
                 self.action_update_today.content.controls[1].controls[1].value = "快速同步今日行情数据"
             except: pass
             self._set_sync_busy(False)

    def full_daily_sync(self, e):
        """完整日更新：行情+估值+资金流+北向"""
        if self.is_syncing: return
        self.show_snack(I18n.get("snack_full_sync_start"))
        self._set_sync_busy(True, self.action_full_sync)
        
        # Update ActionChip Subtitle
        try:
            self.action_full_sync.content.controls[1].controls[1].value = "正在深度同步所有数据..."
            self.action_full_sync.update()
        except: pass
        
        self.page.run_task(self.full_daily_sync_async)

    async def full_daily_sync_async(self):
        """异步完整日更新"""
        try:
            processor = DataProcessor()
            await processor.init_data()
            results = await processor.sync_all_daily()
            total = sum(results.values())
            msg = I18n.get("snack_full_sync_done").format(total=total)
            self.show_snack(msg)
            logger.info(msg)
        except Exception as ex:
            self.show_snack(f"Error: {str(ex)[:30]}")
            logger.error(f"Full daily sync failed: {ex}")
        finally:
             # Restore Subtitle
             try:
                 self.action_full_sync.content.controls[1].controls[1].value = "完整遍历修复缺失数据"
             except: pass
             self._set_sync_busy(False)

    def init_historical_data(self, e):
        """初始化3年历史数据 (支持取消)"""
        # Toggle Logic: CANCEL
        if self.is_syncing and self.sync_button.text.startswith(I18n.get("common_cancel")):
             # Only handle cancel if it was THIS button that started it
             # But simply checking text/icon state is a proxy
            if self.cancel_event:
                self.cancel_event.set()
                self.sync_button.text = I18n.get("common_cancel") + "..."
                self.sync_button.disabled = True # Prevent double clicking cancel
                self.update()
            return
        
        # Start Sync (Block others)
        if self.is_syncing: return # Blocked by other tasks
        
        self._set_sync_busy(True, self.sync_button)
        
        # Custom state for this button (became critical Cancel button)
        self.sync_button.text = I18n.get("settings_cancel_sync")
        self.sync_button.icon = ft.Icons.STOP_CIRCLE
        self.sync_button.style = ft.ButtonStyle(color=ft.Colors.ERROR)
        
        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.progress_text.value = I18n.get("progress_sync_prepare")
        self.update()
        
        self.page.run_task(self.init_historical_async)

    def update_progress(self, current, total, message):
        """Update progress bar from async task"""
        if not self.page: return 
        progress = current / total if total > 0 else 0
        self.progress_bar.value = progress
        self.progress_text.value = f"{current}/{total} ({progress*100:.1f}%) - {message}"
        self._safe_update()

    async def init_historical_async(self):
        """异步初始化历史数据"""
        self.cancel_event = asyncio.Event()
        
        try:
            processor = DataProcessor()
            await processor.init_data()
            
            # Step 1 - Stock Basic
            self.progress_text.value = "步骤 1/3: 同步股票列表..."
            self.progress_bar.value = None
            self.update()
            
            if self.cancel_event.is_set(): raise asyncio.CancelledError()
            await processor.sync_stock_basic()
            
            # Step 2 - Historical Data
            self.progress_text.value = "步骤 2/3: 同步历史行情 (耗时较长)..."
            self.progress_bar.value = 0
            self.update()
            
            if self.cancel_event.is_set(): raise asyncio.CancelledError()
            
            days = 1095 # 3 years
            success_count = await processor.sync_historical_data(
                days=days, 
                progress_callback=lambda c, t, m: self.update_progress(c, t, m),
                cancel_event=self.cancel_event
            )
            
            if self.cancel_event.is_set(): raise asyncio.CancelledError()
            
            if success_count < days * 0.95: # If less than 95% success
                 if success_count == 0:
                     self._safe_show_snack(I18n.get("snack_sync_not_completed"), color=ft.Colors.RED)
                 else:
                     self._safe_show_snack(f"同步完成但有部分失败 ({days - success_count}天)，建议重试", color=ft.Colors.ORANGE)
            
            # Step 3 - Financial Reports (conditional based on last sync)
            should_sync, reason = await processor.should_sync_financials()
            if should_sync and not self.cancel_event.is_set():
                self.progress_text.value = f"步骤 3/3: 同步财务报表 ({reason})..."
                self.progress_bar.value = None
                self.update()
                await processor.sync_financial_reports(progress_callback=None)
            
            if not self.cancel_event.is_set():
                self.progress_text.value = "✅ 历史数据初始化完成！"
                self.progress_bar.value = 1
                self.progress_text.color = ft.Colors.GREEN
                self._safe_show_snack("✅ 所有数据初始化完成", color=ft.Colors.GREEN)
                logger.info("Historical data initialization complete")
            
        except asyncio.CancelledError:
            logger.info("User cancelled sync.")
            self.progress_text.value = I18n.get("settings_msg_sync_cancelled")
            self.progress_text.color = ft.Colors.RED
            self._safe_show_snack(I18n.get("settings_msg_sync_cancelled"), color=ft.Colors.RED)
            
        except Exception as e:
            logger.error(f"Init historical data failed: {e}")
            self.progress_text.value = f"❌ 初始化失败: {str(e)[:50]}"
            self.progress_text.color = ft.Colors.RED
            self._safe_show_snack(f"初始化失败: {str(e)[:30]}")
            
        finally:
            self.is_syncing = False # Reset flag via helper below
            self.cancel_event = None
            
            # Reset UI
            if self.sync_button:
                self.sync_button.text = I18n.get("settings_sync_btn")
                self.sync_button.icon = ft.Icons.SYNC
                self.sync_button.style = None 
                self.sync_button.disabled = False
            
            self.progress_bar.visible = False
            self.progress_text.value = ""
            
            self._set_sync_busy(False) # Unlock all buttons

    def refresh_health_status(self, e):
        """Trigger health check"""
        # Reset Dashboard State
        self.metric_health.set_value("正在检测...", ft.Icons.HOURGLASS_TOP, ft.Colors.BLUE)
        self.metric_storage.set_value("计算中...", ft.Icons.HOURGLASS_TOP, ft.Colors.GREY)
        self.health_detail_text.value = "正在扫描本地数据库与Tushare日历比对..."
        self.update()
        self.page.run_task(self.check_health_async)

    async def check_health_async(self):
        """Async health check implementation"""
        try:
            processor = DataProcessor()
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
                
            # Update Metrics
            self.metric_health.set_value(text, icon_name, color)
            self.metric_sync.set_value(str(result.get('latest_local', '未知')), ft.Icons.ACCESS_TIME, AppColors.PRIMARY)
            self.metric_coverage.set_value(f"{result.get('coverage', '0%')}", ft.Icons.DATA_USAGE, AppColors.INFO)
            self.metric_storage.set_value("正常", ft.Icons.STORAGE, ft.Colors.GREEN) # Placeholder for now
            
            detail = (
                f"覆盖率: {result.get('coverage', 'N/A')} | "
                f"最新官方: {result.get('latest_official', '-')} | "
                f"滞后天数: {result.get('lag_days', 0)} | "
                f"缺失天数: {result.get('missing_count', 0)}"
            )
            self.health_detail_text.value = detail
            self.update()
            
        except Exception as ex:
            self.metric_health.set_value("检查失败", ft.Icons.ERROR, ft.Colors.RED)
            self.health_detail_text.value = str(ex)
            self.update()
            logger.error(f"Health check UI error: {ex}")

    async def _test_ai_connection(self, e):
        """Test AI Connection with current input values"""
        api_key = self.ai_api_key_input.value
        base_url = self.ai_base_url_input.value
        model = self.ai_model_dropdown.value
        
        if not api_key:
            self._safe_show_snack("请输入API Key", color=ft.Colors.ERROR)
            return

        # Show loading state
        original_text = self.btn_test_connection.text
        self.btn_test_connection.text = "Testing..."
        self.btn_test_connection.disabled = True
        self.btn_test_connection.update()
        
        try:
            success = await AIClient.test_connection(api_key, base_url, model)
            if success:
                self._safe_show_snack("连接测试成功！", color=ft.Colors.GREEN)
                self.ai_status_text.value = "已连接"
                self.ai_status_icon.name = ft.Icons.CHECK_CIRCLE
                self.ai_status_icon.color = ft.Colors.GREEN
                self.ai_status_text.update()
                self.ai_status_icon.update()
            else:
                self._safe_show_snack("连接测试失败", color=ft.Colors.ERROR)
        except Exception as ex:
            self._safe_show_snack(f"连接失败: {str(ex)}", color=ft.Colors.ERROR)
        finally:
            # Restore button state
            self.btn_test_connection.text = original_text
            self.btn_test_connection.disabled = False
            self.btn_test_connection.update()

