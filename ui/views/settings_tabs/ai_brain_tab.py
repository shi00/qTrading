"""
AI Brain Tab - AI 配置与策略调优

重构版本：采用 Builder Pattern + Debounce + 完整 i18n 支持
"""

import logging
import os

import flet as ft

from services.ai_service import AIService
from services.local_model_manager import LocalModelManager
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT, ConfigHandler
from utils.log_decorators import UILogger
from utils.thread_pool import TaskType, ThreadPoolManager

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
        current_max_candidates = ConfigHandler.get_ai_max_candidates()
        current_min_turnover = ConfigHandler.get_strategy_min_turnover()
        current_ai_concurrency = ConfigHandler.get_ai_max_concurrent_analysis()

        self.ai_max_candidates_input = ft.TextField(
            label=I18n.get("settings_max_candidates"),
            value=str(current_max_candidates),
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text=I18n.get("ai_hint_default").format(val=30),
            tooltip=I18n.get("settings_hint_ai_cost"),
        )
        self.strategy_min_turnover_input = ft.TextField(
            label=I18n.get("settings_min_turnover"),
            value=str(current_min_turnover),
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text=I18n.get("ai_hint_default").format(val=2.0),
            tooltip=I18n.get("settings_hint_turnover"),
        )
        self.ai_concurrency_input = ft.TextField(
            label=I18n.get("settings_ai_concurrency"),
            value=str(max(_CONCURRENCY_MIN, current_ai_concurrency)),
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text=I18n.get("ai_hint_default").format(val=5),
            tooltip=I18n.get("settings_hint_ai_model"),
        )

        self.ai_prompt_input = ft.TextField(
            label=I18n.get("settings_ai_prompt"),
            value=ConfigHandler.get_ai_system_prompt(),
            multiline=True,
            min_lines=5,
            max_lines=15,
            text_size=12,
            hint_text=I18n.get("settings_ai_prompt_hint"),
        )
        self.btn_reset_prompt = ft.TextButton(
            text=I18n.get("settings_reset_prompt"),
            icon=ft.Icons.RESTORE,
            on_click=self._reset_ai_prompt,
        )

        news_prompt_val = ConfigHandler.get_ai_news_prompt()
        if not news_prompt_val:
            news_prompt_val = DEFAULT_NEWS_PROMPT

        self.ai_news_prompt_input = ft.TextField(
            label=I18n.get("settings_news_prompt"),
            value=news_prompt_val,
            multiline=True,
            min_lines=3,
            max_lines=10,
            text_size=12,
            hint_text=I18n.get("settings_news_prompt_hint"),
        )
        self.btn_reset_news_prompt = ft.TextButton(
            text=I18n.get("settings_reset_prompt"),
            icon=ft.Icons.RESTORE,
            on_click=self._reset_news_prompt,
        )

        self.btn_save_ai = ft.ElevatedButton(
            text=I18n.get("settings_save_ai"),
            icon=ft.Icons.SAVE,
            on_click=lambda e: self.page.run_task(self._save_ai_settings, e) if self.page else None,
            style=AppStyles.primary_button(),
            height=40,
        )

    def _build_content(self):
        """组装 UI 布局"""
        self.llm_config_panel = LLMConfigPanel(
            on_save=self._on_llm_config_saved,
            on_test_connection=self._on_llm_test_connection,
            show_save_button=False,
        )

        self.card_connection = DashboardCard(
            content=ft.Column(
                [
                    self.llm_config_panel,
                ],
            ),
        )

        self.local_model_panel = LocalModelConfigPanel(
            on_save=self._on_local_model_saved,
            show_save_button=False,
        )

        self.card_local_ai = DashboardCard(
            content=ft.Column(
                [
                    self.local_model_panel,
                ],
            ),
        )

        self.txt_tuning_desc = ft.Text(
            I18n.get("ai_tuning_desc"),
            size=_FONT_SIZE_BODY,
            color=AppColors.TEXT_SECONDARY,
        )
        self.icon_help_max = ft.Icon(
            ft.Icons.HELP_OUTLINE,
            size=16,
            color=AppColors.TEXT_HINT,
            tooltip=I18n.get("ai_hint_cap"),
        )
        self.icon_help_min = ft.Icon(
            ft.Icons.HELP_OUTLINE,
            size=16,
            color=AppColors.TEXT_HINT,
            tooltip=I18n.get("ai_hint_turnover_min"),
        )
        self.icon_help_conc = ft.Icon(
            ft.Icons.HELP_OUTLINE,
            size=16,
            color=AppColors.TEXT_HINT,
            tooltip=I18n.get("settings_hint_ai_model"),
        )

        self.card_tuning = DashboardCard(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            SectionHeader(I18n.get("settings_sec_tuning")),
                            ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    self.txt_tuning_desc,
                    ft.Container(height=10),
                    ft.ResponsiveRow(
                        [
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            self.ai_max_candidates_input,
                                            self.icon_help_max,
                                        ],
                                        spacing=5,
                                    ),
                                ],
                                col={"sm": 12, "md": 4},
                            ),
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            self.strategy_min_turnover_input,
                                            self.icon_help_min,
                                        ],
                                        spacing=5,
                                    ),
                                ],
                                col={"sm": 12, "md": 4},
                            ),
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            self.ai_concurrency_input,
                                            self.icon_help_conc,
                                        ],
                                        spacing=5,
                                    ),
                                ],
                                col={"sm": 12, "md": 4},
                            ),
                        ],
                        run_spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
            ),
        )

        self.txt_prompt_hint = ft.Text(
            I18n.get("settings_ai_prompt_hint"),
            size=_FONT_SIZE_HINT,
            color=AppColors.TEXT_HINT,
        )
        self.txt_news_prompt_hint = ft.Text(
            I18n.get("settings_news_prompt_hint"),
            size=_FONT_SIZE_HINT,
            color=AppColors.TEXT_HINT,
        )

        self.card_prompt = DashboardCard(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            SectionHeader(I18n.get("ai_sec_persona")),
                            self.btn_reset_prompt,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(
                        content=self.ai_prompt_input,
                        border=ft.border.all(1, AppColors.BORDER),
                        border_radius=8,
                        bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),
                    ),
                    self.txt_prompt_hint,
                    # Divider
                    ft.Divider(height=20, color=AppColors.BORDER),
                    # News Prompt Section
                    ft.Row(
                        [
                            SectionHeader(I18n.get("settings_news_prompt")),
                            self.btn_reset_news_prompt,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(
                        content=self.ai_news_prompt_input,
                        border=ft.border.all(1, AppColors.BORDER),
                        border_radius=8,
                        bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),
                    ),
                    self.txt_news_prompt_hint,
                ],
            ),
        )

        # Assembly
        self.content = ft.Column(
            controls=[
                self.card_connection,
                self.card_local_ai,
                self.card_tuning,
                self.card_prompt,
                ft.Container(
                    content=ft.Row(
                        [self.btn_save_ai],
                        alignment=ft.MainAxisAlignment.END,
                    ),
                    padding=ft.padding.only(top=10, bottom=30, right=20),
                ),
            ],
            spacing=15,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""
        inputs = [
            self.ai_max_candidates_input,
            self.strategy_min_turnover_input,
            self.ai_concurrency_input,
            self.ai_prompt_input,
            self.ai_news_prompt_input,
        ]
        for ctrl in inputs:
            ctrl.bgcolor = AppColors.INPUT_BG
            if isinstance(ctrl, ft.TextField):
                ctrl.color = AppColors.INPUT_TEXT
            ctrl.border_color = AppColors.INPUT_BORDER

        self._safe_update()

    def did_mount(self):
        """组件挂载后订阅语言变更"""
        if getattr(self, "_mounted", False):
            return
        self._mounted = True
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[AIBrainTab] Subscribed to locale changes")
        self.llm_config_panel.reload_config()
        self.local_model_panel.reload_config()

    def will_unmount(self):
        """组件卸载前取消订阅"""
        self._mounted = False
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

    def _on_locale_change(self, new_locale: str = None):
        """语言变更回调 - 重建整个 UI"""
        try:
            saved_values = {
                "max_cand": self.ai_max_candidates_input.value,
                "min_turn": self.strategy_min_turnover_input.value,
                "concurrency": self.ai_concurrency_input.value,
                "prompt": self.ai_prompt_input.value,
                "news_prompt": self.ai_news_prompt_input.value,
            }

            self._build_controls()

            self.ai_max_candidates_input.value = saved_values["max_cand"]
            self.strategy_min_turnover_input.value = saved_values["min_turn"]
            self.ai_concurrency_input.value = saved_values["concurrency"]
            self.ai_prompt_input.value = saved_values["prompt"]
            self.ai_news_prompt_input.value = saved_values["news_prompt"]

            self._build_content()
            self._safe_update()
        except Exception as e:
            logger.warning(f"[AIBrainTab] Failed to update locale: {e}")

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _on_llm_config_saved(self):
        """LLM 配置保存回调"""
        self.show_snack(I18n.get("settings_verify_success"), color=AppColors.SUCCESS)

    async def _on_llm_test_connection(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        **kwargs,
    ) -> dict:
        """LLM 连接测试回调"""
        return await AIService.test_connection(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    def _on_local_model_saved(self):
        """本地模型配置保存回调"""
        self.show_snack(I18n.get("settings_verify_success"), color=AppColors.SUCCESS)

    async def _save_ai_settings(self, e):
        """保存 AI 配置 (云端 LLM + 本地模型 + 调优参数)"""
        UILogger.log_action("AIBrainTab", "Click", "btn_save_ai")
        try:
            llm_config = self.llm_config_panel.get_current_config()
            if llm_config.get("api_key"):
                self.llm_config_panel.save_current_config()

            self.local_model_panel.save_config()

            ai_prompt = self.ai_prompt_input.value

            max_cand_str = self.ai_max_candidates_input.value.strip()
            min_turn_str = self.strategy_min_turnover_input.value.strip()

            if not max_cand_str or not min_turn_str:
                self.show_snack(
                    I18n.get("ai_snack_fields_empty"),
                    color=AppColors.ERROR,
                )
                return

            try:
                max_cand = int(max_cand_str)
                min_turn = float(min_turn_str)

                if not (_MAX_CANDIDATES_MIN <= max_cand <= _MAX_CANDIDATES_MAX):
                    self.show_snack(
                        I18n.get("ai_snack_invalid_range").format(
                            field=I18n.get("settings_max_candidates"),
                            min=_MAX_CANDIDATES_MIN,
                            max=_MAX_CANDIDATES_MAX,
                        ),
                        color=AppColors.ERROR,
                    )
                    return

                if not (_MIN_TURNOVER_MIN <= min_turn <= _MIN_TURNOVER_MAX):
                    self.show_snack(
                        I18n.get("ai_snack_invalid_range").format(
                            field=I18n.get("settings_min_turnover"),
                            min=_MIN_TURNOVER_MIN,
                            max=_MIN_TURNOVER_MAX,
                        ),
                        color=AppColors.ERROR,
                    )
                    return

                ConfigHandler.set_ai_max_candidates(max_cand)
                ConfigHandler.set_strategy_min_turnover(min_turn)
            except ValueError:
                self.show_snack(I18n.get("ai_snack_param_err"), color=AppColors.ERROR)
                return

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
                        min=_CONCURRENCY_MIN,
                        max=_CONCURRENCY_MAX,
                    ),
                    color=AppColors.ERROR,
                )
                return

            ConfigHandler.save_ai_system_prompt(ai_prompt)
            ConfigHandler.set_ai_news_prompt(self.ai_news_prompt_input.value)

            self.ai_client = AIService()
            await self.ai_client.reload_config()

            self.show_snack(I18n.get("settings_verify_success"), color=AppColors.SUCCESS)

            local_config = self.local_model_panel.get_current_config()
            local_path = local_config.get("model_path", "")
            if local_path:
                if not os.path.exists(local_path):
                    self.show_snack(
                        I18n.get("ai_model_file_not_found"),
                        color=AppColors.ERROR,
                    )
                    return

                self.show_snack(I18n.get("ai_verifying_model"))
                self._safe_update()

                local_mgr = await LocalModelManager.get_instance()
                loaded_md5 = local_mgr.get_loaded_model_md5()

                new_md5 = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    LocalModelManager.calculate_file_md5,
                    local_path,
                )

                if loaded_md5 and new_md5 and loaded_md5 != new_md5:
                    self.show_snack(
                        I18n.get("ai_local_model_changed"),
                        color=AppColors.WARNING,
                    )
                else:
                    self.show_snack(I18n.get("settings_snack_ai_saved"))
            else:
                self.show_snack(I18n.get("settings_snack_ai_saved"))

        except Exception as e:
            logger.error(f"Error saving config: {e}", exc_info=True)
            self.show_snack(
                I18n.get("settings_snack_ai_error").format(
                    error="配置保存失败，请检查文件权限或日志。",
                ),
                color=AppColors.ERROR,
            )

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
