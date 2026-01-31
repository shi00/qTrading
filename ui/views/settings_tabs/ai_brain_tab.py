import flet as ft
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.components.settings_widgets import DashboardCard, SectionHeader
from utils.config_handler import ConfigHandler
from data.ai_client import AIClient
import logging

logger = logging.getLogger(__name__)

class AIBrainTab(ft.Container):
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        
        # Load Config
        ai_cfg = ConfigHandler.get_ai_config()
        current_max_candidates = ConfigHandler.get_ai_max_candidates()
        current_min_turnover = ConfigHandler.get_strategy_min_turnover()
        current_ai_concurrency = ConfigHandler.get_ai_concurrency()
        
        # --- UI Config ---
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
        
        self.ai_status_icon = ft.Icon(ft.Icons.CIRCLE, color=ft.Colors.GREY)
        self.ai_status_text = ft.Text("未连接", color=ft.Colors.GREY)
        
        self.btn_test_connection = ft.ElevatedButton(
            text="测试连接",
            icon=ft.Icons.VIBRATION,
            on_click=self._test_ai_connection,
            style=AppStyles.primary_button()
        )

        # Card 1: Connection & Security
        self.card_connection = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader(I18n.get("settings_sec_ai"))]),
                ft.Text(I18n.get("settings_ai_desc"), size=12, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=10),
                ft.ResponsiveRow([
                    ft.Column([self.ai_base_url_input], col={"sm": 12, "md": 6}),
                    ft.Column([self.ai_model_dropdown], col={"sm": 12, "md": 6}),
                    ft.Column([self.ai_api_key_input], col={"sm": 12}),
                ], run_spacing=10),
                ft.Container(height=10),
                ft.Row([
                    ft.Container(
                        content=ft.Row([self.ai_status_icon, self.ai_status_text], spacing=5),
                        padding=ft.padding.symmetric(horizontal=10, vertical=5),
                    ),
                    ft.Container(width=10),
                    self.btn_test_connection
                ], alignment=ft.MainAxisAlignment.END, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ])
        )

        # Tuning Controls
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

        # Card 2: Strategy Engine
        self.card_tuning = DashboardCard(
            content=ft.Column([
                ft.Row([
                    SectionHeader(I18n.get("settings_sec_tuning")),
                    ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text("调整AI分析的深度与广度，平衡成本与性能", size=12, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=10),
                ft.ResponsiveRow([
                    ft.Column([
                        ft.Row([
                            self.ai_max_candidates_input,
                            ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip="上限")
                        ]),
                        ft.Container(height=5),
                        ft.Row([
                            self.strategy_min_turnover_input,
                            ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip="换手率下限")
                        ]),
                    ], col={"sm": 12, "md": 6}),
                    ft.Column([
                        ft.Container(
                            content=ft.Column([
                                self.ai_concurrency_label,
                                self.ai_concurrency_slider,
                                ft.Text(I18n.get("settings_hint_ai_model"), size=11, color=AppColors.TEXT_HINT)
                            ]),
                            padding=10, border=ft.border.all(1, AppColors.BORDER), border_radius=8
                        )
                    ], col={"sm": 12, "md": 6})
                ])
            ])
        )

        # Prompt
        self.ai_prompt_input = ft.TextField(
            label=I18n.get("settings_ai_prompt"),
            value=ConfigHandler.get_ai_system_prompt(),
            multiline=True, min_lines=5, max_lines=15, text_size=12,
            hint_text=I18n.get("settings_ai_prompt_hint")
        )
        self.btn_reset_prompt = ft.TextButton(
            text=I18n.get("settings_reset_prompt"),
            icon=ft.Icons.RESTORE,
            on_click=self.reset_ai_prompt
        )

        # Card 3: System Persona
        self.card_prompt = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader("系统人设 (System Prompt)"), self.btn_reset_prompt], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Container(
                    content=self.ai_prompt_input,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.BLACK)
                ),
                ft.Text(I18n.get("settings_ai_prompt_hint"), size=11, color=AppColors.TEXT_HINT)
            ])
        )

        self.btn_save_ai = ft.ElevatedButton(
            text=I18n.get("settings_save_ai"), 
            icon=ft.Icons.SAVE, 
            on_click=self.save_ai_settings,
            style=AppStyles.primary_button(),
            width=400
        )

        # Assembly
        self.content = ft.ListView(controls=[
            self.card_connection,
            self.card_tuning,
            self.card_prompt,
            ft.Container(content=ft.Row([self.btn_save_ai], alignment=ft.MainAxisAlignment.END), padding=ft.padding.only(top=10, bottom=30))
        ], spacing=15, padding=ft.padding.only(bottom=50))
        
        I18n.subscribe(self.refresh_locale)

    def _safe_update(self):
        try:
            if self.page: self.update()
        except: pass

    def refresh_locale(self):
        self.ai_api_key_input.label = I18n.get("settings_ai_api_key_label")
        self.ai_base_url_input.label = I18n.get("settings_ai_base_url_label")
        self.ai_model_dropdown.label = I18n.get("settings_ai_model")
        self.btn_save_ai.text = I18n.get("settings_save_ai")
        self._safe_update()

    async def save_ai_settings(self, e):
        try:
            ai_key = self.ai_api_key_input.value.strip()
            ai_base = self.ai_base_url_input.value.strip()
            ai_model = self.ai_model_dropdown.value
            ai_prompt = self.ai_prompt_input.value
            
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
            
            self.ai_status_text.value = I18n.get("settings_status_verifying")
            self.ai_status_text.color = ft.Colors.ORANGE
            self.ai_status_icon.icon = ft.Icons.HOURGLASS_EMPTY
            self.ai_status_icon.color = ft.Colors.ORANGE
            self.update()
            
            client = AIClient()
            await client.reload_config()
            
            if not ai_key:
                self.ai_status_text.value = I18n.get("settings_status_no_key")
                self.ai_status_text.color = ft.Colors.GREY
                self.ai_status_icon.icon = ft.Icons.CIRCLE
                self.ai_status_icon.color = ft.Colors.GREY
                self._safe_update()
                return

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
                self.title_text = str(ex) # Debug
                self.ai_status_text.value = "Error"
                
            self._safe_update()
            self.show_snack(I18n.get("settings_snack_ai_saved"))
            
        except Exception as e:
            logger.error(f"Error saving AI settings: {e}")
            self.show_snack(I18n.get("settings_snack_ai_error").format(error=str(e)))

    async def _test_ai_connection(self, e):
        api_key = self.ai_api_key_input.value
        base_url = self.ai_base_url_input.value
        model = self.ai_model_dropdown.value
        if not api_key:
            self.show_snack("请输入API Key", color=ft.Colors.ERROR)
            return

        self.btn_test_connection.text = "测试中..."
        self.btn_test_connection.disabled = True
        self.update()
        
        try:
            success = await AIClient.test_connection(api_key, base_url, model)
            if success:
                self.show_snack("连接测试成功！", color=ft.Colors.GREEN)
                self.ai_status_text.value = "已连接"
                self.ai_status_icon.icon = ft.Icons.CHECK_CIRCLE
                self.ai_status_icon.color = ft.Colors.GREEN
            else:
                self.show_snack("连接测试失败", color=ft.Colors.ERROR)
        except Exception as ex:
            self.show_snack(f"连接失败: {str(ex)}", color=ft.Colors.ERROR)
        finally:
            self.btn_test_connection.text = "测试连接"
            self.btn_test_connection.disabled = False
            self._safe_update()

    def reset_ai_prompt(self, e):
        from utils.config_handler import DEFAULT_AI_PROMPT
        self.ai_prompt_input.value = DEFAULT_AI_PROMPT
        self.update()
        self.show_snack(I18n.get("settings_snack_prompt_reset"))

    def on_ai_concurrency_change(self, e):
        val = int(self.ai_concurrency_slider.value)
        self.ai_concurrency_label.value = f"{I18n.get('settings_ai_concurrency')}: {val}"
        ConfigHandler.set_ai_concurrency(val)
        self.update()
