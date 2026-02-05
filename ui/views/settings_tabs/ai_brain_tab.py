"""
AI Brain Tab - AI 配置与策略调优

重构版本：采用 Builder Pattern + Debounce + 完整 i18n 支持
"""
import asyncio
import logging

import flet as ft

from services.ai_service import AIService
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler, DEFAULT_AI_PROMPT

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
_INPUT_WIDTH_LARGE = 400
_INPUT_WIDTH_MEDIUM = 200
_INPUT_WIDTH_SMALL = 190
_FONT_SIZE_HINT = 11
_FONT_SIZE_BODY = 12
_DEBOUNCE_MS = 500

# Validation bounds
_MAX_CANDIDATES_MIN = 1
_MAX_CANDIDATES_MAX = 500
_MIN_TURNOVER_MIN = 0.0
_MIN_TURNOVER_MAX = 100.0
_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 10


class AIBrainTab(ft.Container):
    """AI Brain 配置标签页"""
    
    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        self._debounce_task = None
        self._locale_subscription_id = None
        
        # Build UI
        self._build_controls()
        self._build_content()
        
    def _build_controls(self):
        """创建所有控件实例（使用当前语言环境）"""
        # Load Config
        ai_cfg = ConfigHandler.get_ai_config()
        current_max_candidates = ConfigHandler.get_ai_max_candidates()
        current_min_turnover = ConfigHandler.get_strategy_min_turnover()
        current_ai_concurrency = ConfigHandler.get_ai_concurrency()
        
        # --- Connection Controls ---
        self.ai_api_key_input = ft.TextField(
            label=I18n.get("settings_ai_api_key_label"),
            password=True,
            can_reveal_password=True,
            value=ai_cfg.get('ai_api_key', ''),
            width=_INPUT_WIDTH_LARGE,
            hint_text="sk-..."
        )
        self.ai_base_url_input = ft.TextField(
            label=I18n.get("settings_ai_base_url_label"),
            value=ai_cfg.get('ai_base_url', 'https://api.deepseek.com'),
            width=_INPUT_WIDTH_LARGE,
            hint_text="https://api.deepseek.com"
        )
        self.ai_model_dropdown = ft.Dropdown(
            label=I18n.get("settings_ai_model"),
            value=ai_cfg.get('ai_model_name', 'deepseek-chat'),
            width=_INPUT_WIDTH_MEDIUM,
            options=[
                ft.dropdown.Option("deepseek-chat", "DeepSeek-V3 (deepseek-chat)"),
                ft.dropdown.Option("deepseek-reasoner", "DeepSeek-R1 (deepseek-reasoner)"),
                ft.dropdown.Option("moonshot-v1-8k", "Moonshot Kimi"),
                ft.dropdown.Option("qwen2.5-max", "Alibaba Qwen"),
                ft.dropdown.Option("gpt-4o", "OpenAI GPT-4o"),
            ]
        )
        
        # Status indicators
        self.ai_status_icon = ft.Icon(ft.Icons.CIRCLE, color=AppColors.TEXT_HINT)
        self.ai_status_text = ft.Text(I18n.get("ai_status_disconnected"), color=AppColors.TEXT_HINT)
        
        self.btn_test_connection = ft.ElevatedButton(
            text=I18n.get("ai_btn_test"),
            icon=ft.Icons.VIBRATION,
            on_click=self._test_ai_connection,
            style=AppStyles.primary_button()
        )

        # --- Tuning Controls ---
        self.ai_max_candidates_input = ft.TextField(
            label=I18n.get("settings_max_candidates"),
            value=str(current_max_candidates),
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text=I18n.get("ai_hint_default").format(val=30),
            tooltip=I18n.get("settings_hint_ai_cost")
        )
        self.strategy_min_turnover_input = ft.TextField(
            label=I18n.get("settings_min_turnover"),
            value=str(current_min_turnover),
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text=I18n.get("ai_hint_default").format(val=2.0),
            tooltip=I18n.get("settings_hint_turnover")
        )
        self.ai_concurrency_label = ft.Text(
            f"{I18n.get('settings_ai_concurrency')}: {current_ai_concurrency}", 
            size=14
        )
        self.ai_concurrency_slider = ft.Slider(
            min=_CONCURRENCY_MIN, max=_CONCURRENCY_MAX, 
            divisions=_CONCURRENCY_MAX - _CONCURRENCY_MIN, 
            value=current_ai_concurrency,
            label="{value}",
            on_change=self._on_ai_concurrency_change
        )

        # --- Prompt Controls ---
        self.ai_prompt_input = ft.TextField(
            label=I18n.get("settings_ai_prompt"),
            value=ConfigHandler.get_ai_system_prompt(),
            multiline=True, min_lines=5, max_lines=15, text_size=12,
            hint_text=I18n.get("settings_ai_prompt_hint")
        )
        self.btn_reset_prompt = ft.TextButton(
            text=I18n.get("settings_reset_prompt"),
            icon=ft.Icons.RESTORE,
            on_click=self._reset_ai_prompt
        )

        # --- Save Button ---
        self.btn_save_ai = ft.ElevatedButton(
            text=I18n.get("settings_save_ai"), 
            icon=ft.Icons.SAVE, 
            on_click=self._save_ai_settings,
            style=AppStyles.primary_button(),
            width=_INPUT_WIDTH_LARGE
        )

    def _build_content(self):
        """组装 UI 布局"""
        # Card 1: Connection & Security
        self.card_connection = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader(I18n.get("settings_sec_ai"))]),
                ft.Text(I18n.get("settings_ai_desc"), size=_FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY),
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

        # Card 2: Strategy Engine
        self.card_tuning = DashboardCard(
            content=ft.Column([
                ft.Row([
                    SectionHeader(I18n.get("settings_sec_tuning")),
                    ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text(I18n.get("ai_tuning_desc"), size=_FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY),
                ft.Container(height=10),
                ft.ResponsiveRow([
                    ft.Column([
                        ft.Row([
                            self.ai_max_candidates_input,
                            ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip=I18n.get("ai_hint_cap"))
                        ]),
                        ft.Container(height=5),
                        ft.Row([
                            self.strategy_min_turnover_input,
                            ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip=I18n.get("ai_hint_turnover_min"))
                        ]),
                    ], col={"sm": 12, "md": 6}),
                    ft.Column([
                        ft.Container(
                            content=ft.Column([
                                self.ai_concurrency_label,
                                self.ai_concurrency_slider,
                                ft.Text(I18n.get("settings_hint_ai_model"), size=_FONT_SIZE_HINT, color=AppColors.TEXT_HINT)
                            ]),
                            padding=10, border=ft.border.all(1, AppColors.BORDER), border_radius=8
                        )
                    ], col={"sm": 12, "md": 6})
                ])
            ])
        )

        # Card 3: System Persona
        self.card_prompt = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader(I18n.get("ai_sec_persona")), self.btn_reset_prompt], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Container(
                    content=self.ai_prompt_input,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER)
                ),
                ft.Text(I18n.get("settings_ai_prompt_hint"), size=_FONT_SIZE_HINT, color=AppColors.TEXT_HINT)
            ])
        )

        # Assembly
        self.content = ft.ListView(controls=[
            self.card_connection,
            self.card_tuning,
            self.card_prompt,
            ft.Container(
                content=ft.Row([self.btn_save_ai], alignment=ft.MainAxisAlignment.END), 
                padding=ft.padding.only(top=10, bottom=30)
            )
        ], spacing=15, padding=ft.padding.only(bottom=50))

    # =========================================================================
    # Lifecycle Hooks
    # =========================================================================
    
    def did_mount(self):
        """组件挂载后订阅语言变更"""
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[AIBrainTab] Subscribed to locale changes")

    def will_unmount(self):
        """组件卸载前取消订阅并清理 debounce 任务"""
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
            logger.debug("[AIBrainTab] Unsubscribed from locale changes")
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _safe_update(self):
        """线程安全的 UI 更新，处理页面未附加的情况"""
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.debug(f"Safe update skipped: {e}")

    def _update_connection_status(self, status_key: str, color: str, icon):
        """更新 AI 连接状态指示器"""
        self.ai_status_text.value = I18n.get(status_key)
        self.ai_status_text.color = color
        self.ai_status_icon.icon = icon
        self.ai_status_icon.color = color

    def _on_locale_change(self, new_locale: str = None):
        """语言变更回调 - 重建整个 UI"""
        try:
            # 保存当前输入值和连接状态
            saved_values = {
                'api_key': self.ai_api_key_input.value,
                'base_url': self.ai_base_url_input.value,
                'model': self.ai_model_dropdown.value,
                'max_cand': self.ai_max_candidates_input.value,
                'min_turn': self.strategy_min_turnover_input.value,
                'concurrency': self.ai_concurrency_slider.value,
                'prompt': self.ai_prompt_input.value,
                'status_text': self.ai_status_text.value,
                'status_color': self.ai_status_text.color,
                'status_icon': self.ai_status_icon.icon,
            }
            
            # 重建控件
            self._build_controls()
            
            # 恢复输入值
            self.ai_api_key_input.value = saved_values['api_key']
            self.ai_base_url_input.value = saved_values['base_url']
            self.ai_model_dropdown.value = saved_values['model']
            self.ai_max_candidates_input.value = saved_values['max_cand']
            self.strategy_min_turnover_input.value = saved_values['min_turn']
            self.ai_concurrency_slider.value = saved_values['concurrency']
            self.ai_concurrency_label.value = f"{I18n.get('settings_ai_concurrency')}: {int(saved_values.get('concurrency', _CONCURRENCY_MIN))}"
            self.ai_prompt_input.value = saved_values['prompt']
            
            # 恢复连接状态
            self.ai_status_text.value = saved_values.get('status_text', I18n.get('ai_status_disconnected'))
            self.ai_status_text.color = saved_values.get('status_color', AppColors.TEXT_HINT)
            self.ai_status_icon.icon = saved_values.get('status_icon', ft.Icons.CIRCLE)
            self.ai_status_icon.color = saved_values.get('status_color', AppColors.TEXT_HINT)
            
            # 重建布局
            self._build_content()
            self._safe_update()
        except Exception as e:
            logger.warning(f"[AIBrainTab] Failed to update locale: {e}")

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _on_ai_concurrency_change(self, e):
        """处理并发数滑块变化 - 使用 debounce 延迟保存"""
        val = int(self.ai_concurrency_slider.value)
        self.ai_concurrency_label.value = f"{I18n.get('settings_ai_concurrency')}: {val}"
        self._safe_update()
        
        # Cancel previous debounce task
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        
        # Start new debounce task
        if self.page:
            self._debounce_task = self.page.run_task(self._debounced_save_concurrency, val)

    async def _debounced_save_concurrency(self, val: int):
        """延迟保存并发数配置"""
        try:
            await asyncio.sleep(_DEBOUNCE_MS / 1000)
            ConfigHandler.set_ai_concurrency(val)
            logger.debug(f"AI concurrency saved: {val}")
        except asyncio.CancelledError:
            pass  # Debounce cancelled, ignore

    async def _save_ai_settings(self, e):
        """保存 AI 配置"""
        try:
            ai_key = self.ai_api_key_input.value.strip()
            ai_base = self.ai_base_url_input.value.strip()
            ai_model = self.ai_model_dropdown.value
            ai_prompt = self.ai_prompt_input.value
            
            # Validate URL format
            if ai_base and not (ai_base.startswith("http://") or ai_base.startswith("https://")):
                self.show_snack(I18n.get("ai_snack_invalid_url"), color=AppColors.ERROR)
                return
            
            # Validate and save numeric parameters
            max_cand_str = self.ai_max_candidates_input.value.strip()
            min_turn_str = self.strategy_min_turnover_input.value.strip()
            
            if not max_cand_str or not min_turn_str:
                self.show_snack(I18n.get("ai_snack_fields_empty"), color=AppColors.ERROR)
                return
            
            try:
                max_cand = int(max_cand_str)
                min_turn = float(min_turn_str)
                
                # Range validation
                if not (_MAX_CANDIDATES_MIN <= max_cand <= _MAX_CANDIDATES_MAX):
                    self.show_snack(
                        I18n.get("ai_snack_invalid_range").format(
                            field=I18n.get("settings_max_candidates"),
                            min=_MAX_CANDIDATES_MIN, max=_MAX_CANDIDATES_MAX
                        ), 
                        color=AppColors.ERROR
                    )
                    return
                
                if not (_MIN_TURNOVER_MIN <= min_turn <= _MIN_TURNOVER_MAX):
                    self.show_snack(
                        I18n.get("ai_snack_invalid_range").format(
                            field=I18n.get("settings_min_turnover"),
                            min=_MIN_TURNOVER_MIN, max=_MIN_TURNOVER_MAX
                        ), 
                        color=AppColors.ERROR
                    )
                    return
                
                ConfigHandler.set_ai_max_candidates(max_cand)
                ConfigHandler.set_strategy_min_turnover(min_turn)
            except ValueError:
                self.show_snack(I18n.get("ai_snack_param_err"), color=AppColors.ERROR)
                return

            # Cancel any pending debounce task and save concurrency
            if self._debounce_task and not self._debounce_task.done():
                self._debounce_task.cancel()
            ConfigHandler.set_ai_concurrency(int(self.ai_concurrency_slider.value))
            
            ConfigHandler.save_ai_config(ai_key, ai_base, ai_model)
            ConfigHandler.save_ai_system_prompt(ai_prompt)
            
            # Update status to verifying
            self._update_connection_status("settings_status_verifying", AppColors.WARNING, ft.Icons.HOURGLASS_EMPTY)
            self._safe_update()
            
            self.ai_client = AIService()
            await self.ai_client.reload_config()
            
            if not ai_key:
                self._update_connection_status("settings_status_no_key", AppColors.TEXT_HINT, ft.Icons.CIRCLE)
                self._safe_update()
                return

            try:
                success = await client.verify_connection()
                if success:
                    self._update_connection_status("settings_status_verify_ok", AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE)
                else:
                    self._update_connection_status("settings_status_verify_err", AppColors.ERROR, ft.Icons.ERROR)
            except Exception as ex:
                logger.error(f"AI connection verification failed: {ex}")
                self._update_connection_status("common_error", AppColors.ERROR, ft.Icons.ERROR)
                
            self._safe_update()
            self.show_snack(I18n.get("settings_snack_ai_saved"))
            
        except Exception as e:
            logger.error(f"Error saving AI settings: {e}")
            self.show_snack(I18n.get("settings_snack_ai_error").format(error=str(e)), color=AppColors.ERROR)

    async def _test_ai_connection(self, e):
        """测试 AI 连接"""
        api_key = self.ai_api_key_input.value.strip()
        base_url = self.ai_base_url_input.value.strip()
        model = self.ai_model_dropdown.value
        
        if not api_key:
            self.show_snack(I18n.get("ai_snack_key_err"), color=AppColors.ERROR)
            return

        self.btn_test_connection.text = I18n.get("ai_btn_testing")
        self.btn_test_connection.disabled = True
        self._safe_update()
        
        try:
            success = await AIService.test_connection(api_key, base_url, model)
            if success:
                self.show_snack(I18n.get("ai_snack_conn_ok"), color=AppColors.SUCCESS)
                self._update_connection_status("ai_status_connected", AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE)
            else:
                self.show_snack(I18n.get("ai_snack_conn_fail"), color=AppColors.ERROR)
                self._update_connection_status("ai_status_disconnected", AppColors.ERROR, ft.Icons.ERROR)
        except Exception as ex:
            self.show_snack(f"{I18n.get('ai_status_disconnected')}: {str(ex)}", color=AppColors.ERROR)
        finally:
            self.btn_test_connection.text = I18n.get("ai_btn_test")
            self.btn_test_connection.disabled = False
            self._safe_update()

    def _reset_ai_prompt(self, e):
        """重置 AI 系统提示词"""
        self.ai_prompt_input.value = DEFAULT_AI_PROMPT
        self._safe_update()
        self.show_snack(I18n.get("settings_snack_prompt_reset"))
