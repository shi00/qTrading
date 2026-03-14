import logging

import flet as ft

from services.ai_service import AIService
from ui.i18n import I18n
from ui.theme import AppColors
from utils.config_handler import DEFAULT_AI_PROMPT, ConfigHandler

logger = logging.getLogger(__name__)


class AISettingsDialog(ft.AlertDialog):
    def __init__(self, page: ft.Page):
        self.page_ref = page
        self.config = ConfigHandler()

        # Load current settings
        ai_cfg = self.config.get_ai_config()

        self.api_key_field = ft.TextField(
            label=I18n.get("settings_ai_api_key_label"),
            password=True,
            can_reveal_password=True,
            value=ai_cfg.get("ai_api_key", ""),
            hint_text="sk-...",
            border_color=AppColors.BORDER,
        )

        self.base_url_field = ft.TextField(
            label=I18n.get("settings_ai_base_url_label"),
            value=ai_cfg.get("ai_base_url", "https://api.deepseek.com"),
            hint_text="https://api.deepseek.com",
            border_color=AppColors.BORDER,
        )

        self.model_field = ft.Dropdown(
            label=I18n.get("settings_ai_model"),
            value=ai_cfg.get("ai_model_name", "deepseek-chat"),
            options=[
                ft.dropdown.Option("deepseek-chat", "DeepSeek-V3 (deepseek-chat)"),
                ft.dropdown.Option(
                    "deepseek-reasoner", "DeepSeek-R1 (deepseek-reasoner)",
                ),
                ft.dropdown.Option("moonshot-v1-8k", "Moonshot Kimi"),
                ft.dropdown.Option("qwen2.5-max", "Alibaba Qwen"),
                ft.dropdown.Option("gpt-4o", "OpenAI GPT-4o"),
            ],
            border_color=AppColors.BORDER,
        )

        self.prompt_field = ft.TextField(
            label=I18n.get("ai_system_prompt"),
            value=self.config.get_ai_system_prompt(),
            multiline=True,
            min_lines=5,
            max_lines=10,
            text_size=12,
            border_color=AppColors.BORDER,
        )

        super().__init__(
            modal=True,
            title=ft.Text(I18n.get("ai_settings_title"), color=AppColors.TEXT_PRIMARY),
            content=ft.Column(
                [
                    ft.Text(
                        I18n.get("ai_settings_desc"),
                        size=12,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    self.base_url_field,
                    self.api_key_field,
                    self.model_field,
                    ft.Container(height=10),
                    ft.Row(
                        [
                            ft.Text(
                                I18n.get("ai_prompt_label"),
                                size=14,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.TEXT_PRIMARY,
                            ),
                            ft.TextButton(
                                I18n.get("ai_reset_default"),
                                on_click=self.reset_prompt,
                                style=ft.ButtonStyle(padding=0),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self.prompt_field,
                ],
                width=500,
                height=500,
            ),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=self.close),
                ft.ElevatedButton(I18n.get("common_save"), on_click=self.save_settings),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def close(self, e):
        self.open = False
        self.page_ref.update()

    def reset_prompt(self, e):
        """Reset prompt to default"""
        self.prompt_field.value = DEFAULT_AI_PROMPT
        self.prompt_field.update()

    async def save_settings(self, e):
        api_key = self.api_key_field.value.strip()
        base_url = self.base_url_field.value.strip()
        model_name = self.model_field.value

        if not api_key:
            self.api_key_field.error_text = I18n.get("ai_key_required")
            self.api_key_field.update()
            return

        # Save to config
        self.config.save_ai_config(api_key, base_url, model_name)
        self.config.save_ai_system_prompt(self.prompt_field.value)

        # Reload AI Client
        await AIService().reload_config()

        # Show success
        self.page_ref.snack_bar = ft.SnackBar(ft.Text(I18n.get("ai_settings_saved")))
        self.page_ref.snack_bar.open = True
        self.close(e)
