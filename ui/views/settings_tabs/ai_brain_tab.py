"""
AI Brain Tab - AI 配置与策略调优

重构版本：采用 Builder Pattern + Debounce + 完整 i18n 支持
"""
import asyncio
import logging
from utils.log_decorators import UILogger
import os

import flet as ft

from services.ai_service import AIService
from services.local_model_manager import LocalModelManager
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler, DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
_INPUT_WIDTH_LARGE = 400
_INPUT_WIDTH_MEDIUM = 200
_INPUT_WIDTH_SMALL = 190
_FONT_SIZE_HINT = 11
_FONT_SIZE_BODY = 12

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
        self._locale_subscription_id = None
        
        # Build UI
        try:
            self._build_controls()
            self._build_content()
        except Exception as e:
            logger.error(f"[AIBrainTab] Initialization failed: {e}", exc_info=True)
            self.content = ft.Text(f"Error loading AI Tab: {e}", color=ft.Colors.RED)
        
    def _build_controls(self):
        """创建所有控件实例（使用当前语言环境）"""
        # Load Config
        ai_cfg = ConfigHandler.get_ai_config()
        current_max_candidates = ConfigHandler.get_ai_max_candidates()
        current_min_turnover = ConfigHandler.get_strategy_min_turnover()
        current_ai_concurrency = ConfigHandler.get_ai_max_concurrent_analysis()
        
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
        
        # --- Local AI Controls (Embedded) ---
        local_cfg = ConfigHandler.get_local_ai_config()
        self.local_model_path_input = ft.TextField(
            label=I18n.get("settings_local_model_path"),
            value=local_cfg.get('local_model_path', ''),
            expand=True,
            hint_text="C:/path/to/model.gguf",
            read_only=False 
        )
        self.btn_select_model = ft.OutlinedButton(
            text=I18n.get("settings_btn_select_file"),
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda _: self.file_picker.pick_files(
                allowed_extensions=["gguf"], 
                dialog_title=I18n.get("settings_btn_select_file")
            )
        )
        
        timeout_val = ConfigHandler.get_local_ai_timeout()
        self.local_timeout_input = ft.TextField(
            label=I18n.get("settings_local_ai_timeout"),
            value=str(timeout_val) if timeout_val is not None else "",
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="30"
        )

        # --- Advanced Local Settings ---
        self.local_threads_input = ft.Slider(
            min=1, max=16, divisions=15, 
            value=local_cfg.get('n_threads', 4),
            label="{value}"
        )
        
        # GPU Layers: Switch for -1 (Auto/All), Slider for partial
        current_gpu_layers = local_cfg.get('n_gpu_layers', 0)
        is_gpu_auto = (current_gpu_layers == -1)
        
        self.local_gpu_auto_switch = ft.Switch(
            label=I18n.get("settings_local_gpu_auto"),
            value=is_gpu_auto,
            on_change=self._on_gpu_auto_change
        )
        
        self.local_gpu_layers_input = ft.Slider(
            min=0, max=100, divisions=100, 
            value=current_gpu_layers if not is_gpu_auto else 0,
            label="{value}",
            disabled=is_gpu_auto
        )
        
        self.local_batch_input = ft.Dropdown(
            label=I18n.get("settings_local_batch"),
            value=str(local_cfg.get('n_batch', 512)),
            options=[ft.dropdown.Option(str(x)) for x in [512, 1024, 2048, 4096]],
            width=_INPUT_WIDTH_SMALL
        )
        self.local_ctx_input = ft.Dropdown(
            label=I18n.get("settings_local_ctx"),
            value=str(local_cfg.get('n_ctx', 4096)),
            options=[ft.dropdown.Option(str(x)) for x in [2048, 4096, 8192, 16384, 32768]],
            width=_INPUT_WIDTH_SMALL
        )
        self.local_flash_attn_switch = ft.Switch(
            label=I18n.get("settings_local_flash_attn"),
            value=local_cfg.get('flash_attn', True)
        )

        
        # File Picker (Must be added to overlay)
        self.file_picker = ft.FilePicker(on_result=self._on_file_picked)

        # Status indicators - Global
        self.ai_status_icon = ft.Icon(ft.Icons.CIRCLE, color=AppColors.TEXT_HINT)
        self.ai_status_text = ft.Text(I18n.get("ai_status_disconnected"), color=AppColors.TEXT_HINT)
        
        self.btn_test_connection = ft.OutlinedButton(
            text=I18n.get("ai_btn_test"),
            icon=ft.Icons.VIBRATION,
            on_click=self._test_ai_connection
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
        self.ai_concurrency_input = ft.TextField(
            label=I18n.get("settings_ai_concurrency"),
            value=str(max(_CONCURRENCY_MIN, current_ai_concurrency)),
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text=I18n.get("ai_hint_default").format(val=5),
            tooltip=I18n.get("settings_hint_ai_model")
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
        
        # New: News Classification Prompt
        news_prompt_val = ConfigHandler.get_ai_news_prompt()
        if not news_prompt_val:
            news_prompt_val = DEFAULT_NEWS_PROMPT
            
        self.ai_news_prompt_input = ft.TextField(
            label=I18n.get("settings_news_prompt"),
            value=news_prompt_val,
            multiline=True, min_lines=3, max_lines=10, text_size=12,
            hint_text=I18n.get("settings_news_prompt_hint")
        )
        self.btn_reset_news_prompt = ft.TextButton(
            text=I18n.get("settings_reset_prompt"),
            icon=ft.Icons.RESTORE,
            on_click=self._reset_news_prompt
        )

        # --- Save Button ---
        self.btn_save_ai = ft.ElevatedButton(
            text=I18n.get("settings_save_ai"), 
            icon=ft.Icons.SAVE, 
            on_click=self._save_ai_settings,
            style=AppStyles.primary_button(),
            width=_INPUT_WIDTH_LARGE
        )

        # ... (Tuning Controls) ...

    def _on_file_picked(self, e: ft.FilePickerResultEvent):
        """Handle file selection"""
        if e.files and len(e.files) > 0:
            file_path = e.files[0].path
            self.local_model_path_input.value = file_path
            self._safe_update()

    def _on_gpu_auto_change(self, e):
        """Toggle slider enablement based on auto switch"""
        self.local_gpu_layers_input.disabled = self.local_gpu_auto_switch.value
        self._safe_update()


    def _build_content(self):
        """组装 UI 布局"""
        # Card 1: Cloud AI Connection (Reasoning)
        self.txt_cloud_desc = ft.Text(I18n.get("settings_ai_desc"), size=_FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY)
        
        self.card_connection = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader(I18n.get("settings_sec_cloud_ai"))]),
                self.txt_cloud_desc,
                ft.Container(height=10),
                ft.ResponsiveRow([
                    ft.Column([self.ai_base_url_input], col={"sm": 12, "md": 6}),
                    ft.Column([self.ai_model_dropdown], col={"sm": 12, "md": 6}),
                    ft.Column([self.ai_api_key_input], col={"sm": 12}),
                ], run_spacing=10),
                ft.Container(height=10),
                ft.Row([
                    self.btn_test_connection,
                    ft.Container(width=15),
                    ft.Container(
                        content=ft.Row([self.ai_status_icon, self.ai_status_text], spacing=5),
                        padding=ft.padding.symmetric(horizontal=10, vertical=5),
                    )
                ], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ])
        )

        # Card 2: Local AI (Embedded Functionality)
        self.txt_local_desc = ft.Text(I18n.get("settings_local_ai_desc"), size=_FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY)
        
        self.card_local_ai = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader(I18n.get("settings_sec_local_ai"))]),
                self.txt_local_desc,
                ft.Container(height=10),
                ft.ResponsiveRow([
                    ft.Column([
                        ft.Row([
                            self.local_model_path_input,
                            self.btn_select_model
                        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.END)
                    ], col={"sm": 12, "md": 7}),
                    
                    ft.Column([], col={"sm": 0, "md": 1}), # Spacer
                    
                    ft.Column([self.local_timeout_input], col={"sm": 12, "md": 4}),
                ], run_spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                
                # Advanced Settings Expansion
                ft.ExpansionTile(
                    title=ft.Text(I18n.get("settings_sec_tuning"), size=_FONT_SIZE_BODY, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(I18n.get("settings_hint_restart"), size=_FONT_SIZE_HINT, color=AppColors.WARNING),
                    controls=[
                        ft.Container(height=10),
                        ft.ResponsiveRow([
                            ft.Column([
                                ft.Text(I18n.get("settings_local_threads"), size=_FONT_SIZE_BODY),
                                self.local_threads_input
                            ], col={"sm": 12, "md": 6}),
                            
                            ft.Column([
                                ft.Text(I18n.get("settings_local_gpu_layers"), size=_FONT_SIZE_BODY),
                                self.local_gpu_auto_switch,
                                self.local_gpu_layers_input
                            ], col={"sm": 12, "md": 6}),
                            
                            ft.Column([self.local_batch_input], col={"sm": 6, "md": 3}),
                            ft.Column([self.local_ctx_input], col={"sm": 6, "md": 3}),
                            
                            ft.Column([self.local_flash_attn_switch], col={"sm": 12, "md": 4}),
                        ], run_spacing=15)
                    ],
                    initially_expanded=False
                )
            ])
        )

        # Card 3: Strategy Engine
        self.txt_tuning_desc = ft.Text(I18n.get("ai_tuning_desc"), size=_FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY)
        self.icon_help_max = ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip=I18n.get("ai_hint_cap"))
        self.icon_help_min = ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip=I18n.get("ai_hint_turnover_min"))
        self.icon_help_conc = ft.Icon(ft.Icons.HELP_OUTLINE, size=16, color=AppColors.TEXT_HINT, tooltip=I18n.get("settings_hint_ai_model"))

        self.card_tuning = DashboardCard(
            content=ft.Column([
                ft.Row([
                    SectionHeader(I18n.get("settings_sec_tuning")),
                    ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                self.txt_tuning_desc,
                ft.Container(height=10),
                ft.ResponsiveRow([
                    ft.Column([
                        ft.Row([
                            self.ai_max_candidates_input,
                            self.icon_help_max
                        ], spacing=5),
                    ], col={"sm": 12, "md": 4}),
                    ft.Column([
                        ft.Row([
                            self.strategy_min_turnover_input,
                            self.icon_help_min
                        ], spacing=5),
                    ], col={"sm": 12, "md": 4}),
                    ft.Column([
                        ft.Row([
                            self.ai_concurrency_input,
                            self.icon_help_conc
                        ], spacing=5),
                    ], col={"sm": 12, "md": 4}),
                ], run_spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ])
        )

        # Card 4: System Persona
        self.txt_prompt_hint = ft.Text(I18n.get("settings_ai_prompt_hint"), size=_FONT_SIZE_HINT, color=AppColors.TEXT_HINT)
        self.txt_news_prompt_hint = ft.Text(I18n.get("settings_news_prompt_hint"), size=_FONT_SIZE_HINT, color=AppColors.TEXT_HINT)

        self.card_prompt = DashboardCard(
            content=ft.Column([
                ft.Row([SectionHeader(I18n.get("ai_sec_persona")), self.btn_reset_prompt], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Container(
                    content=self.ai_prompt_input,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER)
                ),
                self.txt_prompt_hint,
                
                # Divider
                ft.Divider(height=20, color=AppColors.BORDER),
                
                # News Prompt Section
                ft.Row([SectionHeader(I18n.get("settings_news_prompt")), self.btn_reset_news_prompt], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Container(
                    content=self.ai_news_prompt_input,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER)
                ),
                self.txt_news_prompt_hint
            ])
        )

        # Assembly
        self.content = ft.Column(
            controls=[
                self.card_connection,
                self.card_local_ai,
                self.card_tuning,
                self.card_prompt,
                ft.Container(
                    content=ft.Row([self.btn_save_ai], alignment=ft.MainAxisAlignment.END), 
                    padding=ft.padding.only(top=10, bottom=30, right=20)
                )
            ], 
            spacing=15, 
            scroll=ft.ScrollMode.AUTO,
            expand=True
        )
        
    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""
        inputs = [
            self.ai_api_key_input, self.ai_base_url_input, self.ai_model_dropdown,
            self.local_model_path_input, self.local_timeout_input,
            self.local_batch_input, self.local_ctx_input,
            self.ai_max_candidates_input, self.strategy_min_turnover_input, self.ai_concurrency_input,
            self.ai_prompt_input, self.ai_news_prompt_input
        ]
        for ctrl in inputs:
            ctrl.bgcolor = AppColors.INPUT_BG
            if isinstance(ctrl, ft.TextField):
                ctrl.color = AppColors.INPUT_TEXT
            ctrl.border_color = AppColors.INPUT_BORDER

        # Standard colors auto-update via semantic tokens
        self._safe_update()

    # =========================================================================
    # Lifecycle Hooks
    # =========================================================================
    
    def did_mount(self):
        """组件挂载后订阅语言变更"""
        # Add FilePicker to page overlay
        if self.page:
            self.page.overlay.append(self.file_picker)
            self.page.update()
            
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[AIBrainTab] Subscribed to locale changes")

    def will_unmount(self):
        """组件卸载前取消订阅"""
        if self.page and getattr(self, "file_picker", None) in self.page.overlay:
            self.page.overlay.remove(self.file_picker)
            self.page.update()
            
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
            logger.debug("[AIBrainTab] Unsubscribed from locale changes")

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
                
                'local_path': self.local_model_path_input.value,
                'local_timeout': self.local_timeout_input.value,
                
                # New advanced local settings
                'local_threads': self.local_threads_input.value,
                'local_gpu': self.local_gpu_layers_input.value,
                'local_gpu_auto': self.local_gpu_auto_switch.value,
                'local_batch': self.local_batch_input.value,
                'local_ctx': self.local_ctx_input.value,
                'local_flash': self.local_flash_attn_switch.value,

                'max_cand': self.ai_max_candidates_input.value,
                'min_turn': self.strategy_min_turnover_input.value,
                'concurrency': self.ai_concurrency_input.value,
                'prompt': self.ai_prompt_input.value,
                'news_prompt': self.ai_news_prompt_input.value,

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
            
            self.local_model_path_input.value = saved_values['local_path']
            
            timeout_input_val = saved_values.get('local_timeout')
            if timeout_input_val is not None:
                self.local_timeout_input.value = timeout_input_val
                
            # Restore new settings
            self.local_threads_input.value = saved_values.get('local_threads', 4)
            
            # Restore GPU
            # Restore GPU
            
            # Check if we saved the switch state explicitly? No, we saved inputs.
            # Wait, saved_values reads from INPUTS not config. 
            # In _on_locale_change:
            # 'local_gpu': self.local_gpu_layers_input.value
            # We need to capture switch state too.
            
            # Actually, let's fix the saved_values collection first.
            self.local_gpu_auto_switch.value = saved_values.get('local_gpu_auto', False)
            self.local_gpu_layers_input.value = saved_values.get('local_gpu', 0)
            self.local_gpu_layers_input.disabled = self.local_gpu_auto_switch.value

            self.local_batch_input.value = saved_values.get('local_batch', "512")
            self.local_ctx_input.value = saved_values.get('local_ctx', "4096")
            self.local_flash_attn_switch.value = saved_values.get('local_flash', True)

            self.ai_max_candidates_input.value = saved_values['max_cand']
            self.strategy_min_turnover_input.value = saved_values['min_turn']
            self.ai_concurrency_input.value = saved_values['concurrency']
            self.ai_prompt_input.value = saved_values['prompt']
            self.ai_news_prompt_input.value = saved_values['news_prompt']
            
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


    async def _save_ai_settings(self, e):
        """保存 AI 配置"""
        UILogger.log_action("AIBrainTab", "Click", f"btn_save_ai | model={self.ai_model_dropdown.value}")
        try:
            # Cloud AI
            ai_key = self.ai_api_key_input.value.strip()
            ai_base = self.ai_base_url_input.value.strip()
            ai_model = self.ai_model_dropdown.value
            
            # Local AI
            local_path = self.local_model_path_input.value.strip()
            local_timeout_str = self.local_timeout_input.value.strip()
            
            try:
                if not local_timeout_str:
                    # User cleared it? Default to None (Infinite) or force them to set it?
                    # Let's enforce a value for safety.
                    raise ValueError("Empty")
                    
                local_timeout = int(local_timeout_str)
                if not (0 < local_timeout <= 3600):
                     raise ValueError("Must be 1-3600")
            except ValueError:
                 self.show_snack(
                    I18n.get("ai_snack_invalid_range").format(
                        field=I18n.get("settings_local_ai_timeout"),
                        min=1, max=3600
                    ), 
                    color=AppColors.ERROR
                )
                 return

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

            # Save concurrency (validated as integer)
            concurrency_str = self.ai_concurrency_input.value.strip()
            try:
                concurrency = int(concurrency_str)
                if not (_CONCURRENCY_MIN <= concurrency <= _CONCURRENCY_MAX):
                    raise ValueError("Range")
                ConfigHandler.set_ai_max_concurrent_analysis(concurrency)
            except ValueError:
                self.show_snack(
                    I18n.get("ai_snack_invalid_range").format(
                        field=I18n.get("settings_ai_concurrency"),
                        min=_CONCURRENCY_MIN, max=_CONCURRENCY_MAX
                    ),
                    color=AppColors.ERROR
                )
                return
            
            # Save Cloud & Local Configs
            ConfigHandler.save_ai_config(ai_key, ai_base, ai_model)
            
            # Save Local Model Configs
            gpu_layers_to_save = -1 if self.local_gpu_auto_switch.value else int(self.local_gpu_layers_input.value)
            
            ConfigHandler.save_local_ai_config(
                model_path=local_path,
                timeout=local_timeout,
                n_threads=int(self.local_threads_input.value),
                n_batch=int(self.local_batch_input.value),
                n_ctx=int(self.local_ctx_input.value),
                flash_attn=self.local_flash_attn_switch.value,
                n_gpu_layers=gpu_layers_to_save
            )
            
            ConfigHandler.save_ai_system_prompt(ai_prompt)
            ConfigHandler.set_ai_news_prompt(self.ai_news_prompt_input.value)
            
            # Update status to verifying
            self._update_connection_status("settings_status_verifying", AppColors.WARNING, ft.Icons.HOURGLASS_EMPTY)
            self._safe_update()

            # Reload AI Service Config
            self.ai_client = AIService()
            await self.ai_client.reload_config()
            
            if not ai_key:
                self._update_connection_status("settings_status_no_key", AppColors.TEXT_HINT, ft.Icons.CIRCLE)
                self._safe_update()
                return

            try:
                success = await self.ai_client.verify_connection()
                if success:
                    self._update_connection_status("settings_status_verify_ok", AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE)
                else:
                    self._update_connection_status("settings_status_verify_err", AppColors.ERROR, ft.Icons.ERROR)
            except Exception as ex:
                logger.error(f"[AIBrainTab] Verify | ❌ Connection verification failed: {ex}", exc_info=True)
                self._update_connection_status("common_error", AppColors.ERROR, ft.Icons.ERROR)
                
            self._safe_update()
            
            # Check if local model file changed (using MD5) and notify user to restart
            if local_path:
                # Check file exists first
                if not os.path.exists(local_path):
                    self.show_snack(I18n.get("ai_model_file_not_found"), color=AppColors.ERROR)
                    return
                
                self.show_snack(I18n.get("ai_verifying_model"))
                self._safe_update()
                
                # Get loaded model MD5 from singleton
                local_mgr = await LocalModelManager.get_instance()
                loaded_md5 = local_mgr.get_loaded_model_md5()
                
                # Calculate MD5 of configured file (in thread pool)
                new_md5 = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    LocalModelManager.calculate_file_md5,
                    local_path
                )
                
                if loaded_md5 and new_md5 and loaded_md5 != new_md5:
                    self.show_snack(I18n.get("ai_local_model_changed"), color=AppColors.WARNING)
                else:
                    self.show_snack(I18n.get("settings_snack_ai_saved"))
            else:
                self.show_snack(I18n.get("settings_snack_ai_saved"))
            
        except Exception as e:
            logger.error(f"Error saving config: {e}", exc_info=True)
            self.show_snack(I18n.get("settings_snack_ai_error").format(error="配置保存失败，请检查文件权限或日志。"), color=AppColors.ERROR)

    async def _test_ai_connection(self, e):
        """测试 AI 连接"""
        UILogger.log_action("AIBrainTab", "Click", f"btn_test_connection | model={self.ai_model_dropdown.value}")
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
            logger.error(f"AI test connection error: {ex}", exc_info=True)
            self.show_snack(f"{I18n.get('ai_status_disconnected')}: 内部连接错误", color=AppColors.ERROR)
        finally:
            self.btn_test_connection.text = I18n.get("ai_btn_test")
            self.btn_test_connection.disabled = False
            self._safe_update()

    def _reset_news_prompt(self, e):
        """重置新闻分类提示词"""
        self.ai_news_prompt_input.value = DEFAULT_NEWS_PROMPT
        self._safe_update()
        self.show_snack(I18n.get("settings_snack_prompt_reset"))

    def _reset_ai_prompt(self, e):
        """重置 AI 系统提示词"""
        self.ai_prompt_input.value = DEFAULT_AI_PROMPT
        self._safe_update()
        self.show_snack(I18n.get("settings_snack_prompt_reset"))
