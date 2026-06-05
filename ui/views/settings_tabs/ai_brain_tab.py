"""
AI Brain Tab - AI 配置与策略调优

重构版本：采用 Builder Pattern + Debounce + 完整 i18n 支持
"""

import logging
import os

import flet as ft

from services.ai_service import AIService
from services.local_model_manager import LocalModelManager
from ui.components.config_panels.failover_config_panel import FailoverConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from utils.config_models import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT
from utils.log_decorators import UILogger
from utils.prompt_guard import MAX_PROMPT_LENGTH, validate_prompt
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
_NEWS_CONCURRENCY_MIN = 1
_NEWS_CONCURRENCY_MAX = 5


class AIBrainTab(ft.Container):
    """AI Brain 配置标签页"""

    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True
        self._locale_subscription_id = None
        self._mounted = False

        # Build UI
        try:
            self._build_controls()
            self._build_content()
        except Exception as e:
            logger.error("[AIBrainTab] Initialization failed: %s", e, exc_info=True)
            self.content = ft.Text(f"Error loading AI Tab: {e}", color=ft.Colors.RED)

    def _build_controls(self):  # pragma: no cover
        """创建所有控件实例（使用当前语言环境）"""  # pragma: no cover
        current_max_candidates = ConfigHandler.get_ai_max_candidates()  # pragma: no cover
        current_min_turnover = ConfigHandler.get_strategy_min_turnover()  # pragma: no cover
        current_ai_concurrency = ConfigHandler.get_ai_max_concurrent_analysis()  # pragma: no cover

        self.ai_max_candidates_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_max_candidates"),  # pragma: no cover
            value=str(current_max_candidates),  # pragma: no cover
            width=_INPUT_WIDTH_SMALL,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            hint_text=I18n.get("ai_hint_default").format(val=30),  # pragma: no cover
            tooltip=I18n.get("settings_hint_ai_cost"),  # pragma: no cover
        )  # pragma: no cover
        self.strategy_min_turnover_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_min_turnover"),  # pragma: no cover
            value=str(current_min_turnover),  # pragma: no cover
            width=_INPUT_WIDTH_SMALL,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            hint_text=I18n.get("ai_hint_default").format(val=2.0),  # pragma: no cover
            tooltip=I18n.get("settings_hint_turnover"),  # pragma: no cover
        )  # pragma: no cover
        self.ai_concurrency_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_ai_concurrency"),  # pragma: no cover
            value=str(max(_CONCURRENCY_MIN, current_ai_concurrency)),  # pragma: no cover
            width=_INPUT_WIDTH_SMALL,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            hint_text=I18n.get("ai_hint_default").format(val=5),  # pragma: no cover
            tooltip=I18n.get("settings_hint_ai_model"),  # pragma: no cover
        )  # pragma: no cover

        current_news_concurrency = ConfigHandler.get_ai_news_max_concurrent()  # pragma: no cover
        self.ai_news_concurrency_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_ai_news_concurrency"),  # pragma: no cover
            value=str(current_news_concurrency),  # pragma: no cover
            width=_INPUT_WIDTH_SMALL,  # pragma: no cover
            keyboard_type=ft.KeyboardType.NUMBER,  # pragma: no cover
            hint_text=I18n.get("ai_hint_default").format(val=1),  # pragma: no cover
            tooltip=I18n.get("settings_hint_ai_news_concurrency"),  # pragma: no cover
        )  # pragma: no cover

        self.ai_prompt_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_ai_prompt"),  # pragma: no cover
            value=ConfigHandler.get_ai_system_prompt(),  # pragma: no cover
            multiline=True,  # pragma: no cover
            min_lines=5,  # pragma: no cover
            max_lines=15,  # pragma: no cover
            text_size=12,  # pragma: no cover
            hint_text=I18n.get("settings_ai_prompt_hint"),  # pragma: no cover
        )  # pragma: no cover
        self.btn_reset_prompt = ft.TextButton(  # pragma: no cover
            text=I18n.get("settings_reset_prompt"),  # pragma: no cover
            icon=ft.Icons.RESTORE,  # pragma: no cover
            on_click=self._reset_ai_prompt,  # pragma: no cover
        )  # pragma: no cover

        news_prompt_val = ConfigHandler.get_ai_news_prompt()  # pragma: no cover

        self.ai_news_prompt_input = ft.TextField(  # pragma: no cover
            label=I18n.get("settings_news_prompt"),  # pragma: no cover
            value=news_prompt_val,  # pragma: no cover
            multiline=True,  # pragma: no cover
            min_lines=3,  # pragma: no cover
            max_lines=10,  # pragma: no cover
            text_size=12,  # pragma: no cover
            hint_text=I18n.get("settings_news_prompt_hint"),  # pragma: no cover
        )  # pragma: no cover
        self.btn_reset_news_prompt = ft.TextButton(  # pragma: no cover
            text=I18n.get("settings_reset_prompt"),  # pragma: no cover
            icon=ft.Icons.RESTORE,  # pragma: no cover
            on_click=self._reset_news_prompt,  # pragma: no cover
        )  # pragma: no cover

        self.btn_save_ai = ft.ElevatedButton(  # pragma: no cover
            text=I18n.get("settings_save_ai"),  # pragma: no cover
            icon=ft.Icons.SAVE,  # pragma: no cover
            on_click=lambda e: self.page.run_task(self._save_ai_settings, e) if self.page else None,  # pragma: no cover
            style=AppStyles.primary_button(),  # pragma: no cover
            height=40,  # pragma: no cover
        )  # pragma: no cover

    def _build_content(self):  # pragma: no cover
        """组装 UI 布局"""  # pragma: no cover
        self.llm_config_panel = LLMConfigPanel(  # pragma: no cover
            on_save=self._on_llm_config_saved,  # pragma: no cover
            on_test_connection=self._on_llm_test_connection,  # pragma: no cover
            show_save_button=False,  # pragma: no cover
        )  # pragma: no cover

        self.card_connection = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.llm_config_panel,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.failover_panel = FailoverConfigPanel(  # pragma: no cover
            on_save=self._on_llm_config_saved,  # pragma: no cover
        )  # pragma: no cover

        self.card_failover = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.failover_panel,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.local_model_panel = LocalModelConfigPanel(  # pragma: no cover
            on_save=self._on_local_model_saved,  # pragma: no cover
            show_save_button=False,  # pragma: no cover
        )  # pragma: no cover

        self.card_local_ai = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.local_model_panel,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.txt_tuning_desc = ft.Text(  # pragma: no cover
            I18n.get("ai_tuning_desc"),  # pragma: no cover
            size=_FONT_SIZE_BODY,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
        )  # pragma: no cover
        self.icon_help_max = ft.Icon(  # pragma: no cover
            ft.Icons.HELP_OUTLINE,  # pragma: no cover
            size=16,  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
            tooltip=I18n.get("ai_hint_cap"),  # pragma: no cover
        )  # pragma: no cover
        self.icon_help_min = ft.Icon(  # pragma: no cover
            ft.Icons.HELP_OUTLINE,  # pragma: no cover
            size=16,  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
            tooltip=I18n.get("ai_hint_turnover_min"),  # pragma: no cover
        )  # pragma: no cover
        self.icon_help_conc = ft.Icon(  # pragma: no cover
            ft.Icons.HELP_OUTLINE,  # pragma: no cover
            size=16,  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
            tooltip=I18n.get("settings_hint_ai_model"),  # pragma: no cover
        )  # pragma: no cover

        self.card_tuning = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Row(  # pragma: no cover
                        [  # pragma: no cover
                            SectionHeader(I18n.get("settings_sec_tuning")),  # pragma: no cover
                            ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY),  # pragma: no cover
                        ],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,  # pragma: no cover
                    ),  # pragma: no cover
                    self.txt_tuning_desc,  # pragma: no cover
                    ft.Container(height=10),  # pragma: no cover
                    ft.ResponsiveRow(  # pragma: no cover
                        [  # pragma: no cover
                            ft.Column(  # pragma: no cover
                                [  # pragma: no cover
                                    ft.Row(  # pragma: no cover
                                        [  # pragma: no cover
                                            self.ai_max_candidates_input,  # pragma: no cover
                                            self.icon_help_max,  # pragma: no cover
                                        ],  # pragma: no cover
                                        spacing=5,  # pragma: no cover
                                    ),  # pragma: no cover
                                ],  # pragma: no cover
                                col={"sm": 12, "md": 4},  # pragma: no cover
                            ),  # pragma: no cover
                            ft.Column(  # pragma: no cover
                                [  # pragma: no cover
                                    ft.Row(  # pragma: no cover
                                        [  # pragma: no cover
                                            self.strategy_min_turnover_input,  # pragma: no cover
                                            self.icon_help_min,  # pragma: no cover
                                        ],  # pragma: no cover
                                        spacing=5,  # pragma: no cover
                                    ),  # pragma: no cover
                                ],  # pragma: no cover
                                col={"sm": 12, "md": 4},  # pragma: no cover
                            ),  # pragma: no cover
                            ft.Column(  # pragma: no cover
                                [  # pragma: no cover
                                    ft.Row(  # pragma: no cover
                                        [  # pragma: no cover
                                            self.ai_concurrency_input,  # pragma: no cover
                                            self.icon_help_conc,  # pragma: no cover
                                        ],  # pragma: no cover
                                        spacing=5,  # pragma: no cover
                                    ),  # pragma: no cover
                                    ft.Row(  # pragma: no cover
                                        [  # pragma: no cover
                                            self.ai_news_concurrency_input,  # pragma: no cover
                                        ],  # pragma: no cover
                                        spacing=5,  # pragma: no cover
                                    ),  # pragma: no cover
                                ],  # pragma: no cover
                                col={"sm": 12, "md": 4},  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                        run_spacing=10,  # pragma: no cover
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.txt_prompt_hint = ft.Text(  # pragma: no cover
            I18n.get("settings_ai_prompt_hint"),  # pragma: no cover
            size=_FONT_SIZE_HINT,  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
        )  # pragma: no cover
        self.txt_news_prompt_hint = ft.Text(  # pragma: no cover
            I18n.get("settings_news_prompt_hint"),  # pragma: no cover
            size=_FONT_SIZE_HINT,  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
        )  # pragma: no cover

        self.card_prompt = DashboardCard(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Row(  # pragma: no cover
                        [  # pragma: no cover
                            SectionHeader(I18n.get("ai_sec_persona")),  # pragma: no cover
                            self.btn_reset_prompt,  # pragma: no cover
                        ],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Container(  # pragma: no cover
                        content=self.ai_prompt_input,  # pragma: no cover
                        border=ft.border.all(1, AppColors.BORDER),  # pragma: no cover
                        border_radius=8,  # pragma: no cover
                        bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),  # pragma: no cover
                    ),  # pragma: no cover
                    self.txt_prompt_hint,  # pragma: no cover
                    ft.Divider(height=20, color=AppColors.BORDER),  # pragma: no cover
                    ft.Row(  # pragma: no cover
                        [  # pragma: no cover
                            SectionHeader(I18n.get("settings_news_prompt")),  # pragma: no cover
                            self.btn_reset_news_prompt,  # pragma: no cover
                        ],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Container(  # pragma: no cover
                        content=self.ai_news_prompt_input,  # pragma: no cover
                        border=ft.border.all(1, AppColors.BORDER),  # pragma: no cover
                        border_radius=8,  # pragma: no cover
                        bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),  # pragma: no cover
                    ),  # pragma: no cover
                    self.txt_news_prompt_hint,  # pragma: no cover
                ],  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        # Assembly  # pragma: no cover
        self.content = ft.Column(  # pragma: no cover
            controls=[  # pragma: no cover
                self.card_connection,  # pragma: no cover
                self.card_failover,  # pragma: no cover
                self.card_local_ai,  # pragma: no cover
                self.card_tuning,  # pragma: no cover
                self.card_prompt,  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=ft.Row(  # pragma: no cover
                        [self.btn_save_ai],  # pragma: no cover
                        alignment=ft.MainAxisAlignment.END,  # pragma: no cover
                    ),  # pragma: no cover
                    padding=ft.padding.only(top=10, bottom=30, right=20),  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            spacing=15,  # pragma: no cover
            scroll=ft.ScrollMode.AUTO,  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

    def update_theme(self):  # pragma: no cover
        """Update styles on theme change — only Layer 2 custom colors (INPUT_*)."""  # pragma: no cover
        inputs = [  # pragma: no cover
            self.ai_max_candidates_input,  # pragma: no cover
            self.strategy_min_turnover_input,  # pragma: no cover
            self.ai_concurrency_input,  # pragma: no cover
            self.ai_news_concurrency_input,  # pragma: no cover
            self.ai_prompt_input,  # pragma: no cover
            self.ai_news_prompt_input,  # pragma: no cover
        ]  # pragma: no cover
        for ctrl in inputs:  # pragma: no cover
            ctrl.bgcolor = AppColors.INPUT_BG  # pragma: no cover
            if isinstance(ctrl, ft.TextField):  # pragma: no cover
                ctrl.color = AppColors.INPUT_TEXT  # pragma: no cover
            ctrl.border_color = AppColors.INPUT_BORDER  # pragma: no cover

        self._safe_update()  # pragma: no cover

    def did_mount(self):  # pragma: no cover
        """组件挂载后订阅语言变更"""  # pragma: no cover
        if getattr(self, "_mounted", False):  # pragma: no cover
            return  # pragma: no cover
        self._mounted = True  # pragma: no cover
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)  # pragma: no cover
        logger.debug("[AIBrainTab] Subscribed to locale changes")  # pragma: no cover
        self.llm_config_panel.reload_config()  # pragma: no cover
        self.failover_panel.reload_config()  # pragma: no cover
        self.local_model_panel.reload_config()  # pragma: no cover

    def will_unmount(self):  # pragma: no cover
        """组件卸载前取消订阅"""  # pragma: no cover
        self._mounted = False  # pragma: no cover
        if self._locale_subscription_id:  # pragma: no cover
            I18n.unsubscribe(self._locale_subscription_id)  # pragma: no cover
            self._locale_subscription_id = None  # pragma: no cover
            logger.debug("[AIBrainTab] Unsubscribed from locale changes")  # pragma: no cover

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _safe_update(self):  # pragma: no cover
        """线程安全的 UI 更新，处理页面未附加的情况"""  # pragma: no cover
        try:  # pragma: no cover
            if self.page:  # pragma: no cover
                self.update()  # pragma: no cover
        except Exception as e:  # pragma: no cover
            logger.debug("Safe update skipped: %s", e)  # pragma: no cover

    def _on_locale_change(self, new_locale: str = None):  # type: ignore[assignment]  # pragma: no cover
        """语言变更回调 - 重建整个 UI"""  # pragma: no cover
        try:  # pragma: no cover
            saved_values = {  # pragma: no cover
                "max_cand": self.ai_max_candidates_input.value,  # pragma: no cover
                "min_turn": self.strategy_min_turnover_input.value,  # pragma: no cover
                "concurrency": self.ai_concurrency_input.value,  # pragma: no cover
                "news_concurrency": self.ai_news_concurrency_input.value,  # pragma: no cover
                "prompt": self.ai_prompt_input.value,  # pragma: no cover
                "news_prompt": self.ai_news_prompt_input.value,  # pragma: no cover
            }  # pragma: no cover

            self._build_controls()  # pragma: no cover

            self.ai_max_candidates_input.value = saved_values["max_cand"]  # pragma: no cover
            self.strategy_min_turnover_input.value = saved_values["min_turn"]  # pragma: no cover
            self.ai_concurrency_input.value = saved_values["concurrency"]  # pragma: no cover
            self.ai_news_concurrency_input.value = saved_values["news_concurrency"]  # pragma: no cover
            self.ai_prompt_input.value = saved_values["prompt"]  # pragma: no cover
            self.ai_news_prompt_input.value = saved_values["news_prompt"]  # pragma: no cover

            self._build_content()  # pragma: no cover
            self._safe_update()  # pragma: no cover
        except Exception as e:  # pragma: no cover
            logger.warning("[AIBrainTab] Failed to update locale: %s", e)  # pragma: no cover

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

    def _validate_prompt_or_warn(self, prompt: str) -> bool:
        """验证 Prompt 安全性，不合法时显示警告并返回 False"""
        is_valid, warning = validate_prompt(prompt)
        if not is_valid:
            msg = I18n.get(warning, warning)
            if warning == "prompt_err_length":
                msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
            self.show_snack(f"⚠ {msg}", color=AppColors.WARNING)
            return False
        return True

    async def _save_ai_settings(self, e):
        """保存 AI 配置 (云端 LLM + 本地模型 + 调优参数)

        采用三阶段模式：先验证所有输入 → 再统一保存 → 最后统一重载。
        避免部分保存导致磁盘与内存不一致。
        """
        UILogger.log_action("AIBrainTab", "Click", "btn_save_ai")
        try:
            # ========== 阶段 1: 验证所有输入（不写入任何配置） ==========

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
            except ValueError:
                self.show_snack(I18n.get("ai_snack_param_err"), color=AppColors.ERROR)
                return

            concurrency_str = self.ai_concurrency_input.value.strip()
            try:
                concurrency = int(concurrency_str)
                if not (_CONCURRENCY_MIN <= concurrency <= _CONCURRENCY_MAX):
                    raise ValueError("Range")
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

            news_concurrency_str = self.ai_news_concurrency_input.value.strip()
            try:
                news_concurrency = int(news_concurrency_str)
                if not (_NEWS_CONCURRENCY_MIN <= news_concurrency <= _NEWS_CONCURRENCY_MAX):
                    raise ValueError("Range")
            except ValueError:
                self.show_snack(
                    I18n.get("ai_snack_invalid_range").format(
                        field=I18n.get("settings_ai_news_concurrency"),
                        min=_NEWS_CONCURRENCY_MIN,
                        max=_NEWS_CONCURRENCY_MAX,
                    ),
                    color=AppColors.ERROR,
                )
                return

            if not self._validate_prompt_or_warn(ai_prompt):
                return

            # 验证新闻 Prompt
            news_prompt = self.ai_news_prompt_input.value
            if not self._validate_prompt_or_warn(news_prompt):
                return

            # ========== 阶段 2: 提取 UI 值（必须在事件循环中，避免跨线程 UI 访问） ==========

            llm_config = self.llm_config_panel.get_current_config()
            local_config = self.local_model_panel.get_current_config()
            is_azure = self.llm_config_panel._is_azure
            api_key_modified = self.llm_config_panel._api_key_modified

            # 构建 LLM 保存参数
            llm_kwargs: dict = {}
            if is_azure:
                from utils.llm_providers import AZURE_DEFAULT_API_VERSION

                llm_kwargs["api_version"] = llm_config.get("api_version", AZURE_DEFAULT_API_VERSION)
                llm_kwargs["azure_resource_name"] = llm_config.get("azure_resource_name", "")
                llm_kwargs["azure_deployment_name"] = llm_config.get("azure_deployment_name", "")

            custom_models_update = self.llm_config_panel._build_custom_models_update(
                llm_config["provider"], llm_config["model"], is_azure=is_azure
            )
            if custom_models_update is not None:
                llm_kwargs["custom_models"] = custom_models_update

            # 未修改 API Key 时传 None，避免不必要的重加密
            api_key_to_save = llm_config["api_key"] if api_key_modified else None

            # 构建 LocalModel 保存参数
            local_save_kwargs = {
                "model_path": local_config.get("model_path", ""),
                "timeout": local_config.get("timeout", 300),
                "n_threads": local_config.get("n_threads", 4),
                "n_batch": local_config.get("n_batch", 512),
                "n_ctx": local_config.get("n_ctx", 2048),
                "flash_attn": local_config.get("flash_attn", False),
                "n_gpu_layers": local_config.get("n_gpu_layers", 0),
            }

            # ========== 阶段 3: 统一保存所有配置（异步化 IO，纯 ConfigHandler 操作） ==========

            def _save_configs_sync():
                """所有配置保存操作，在 IO 线程池执行（不访问 UI 控件）"""
                if not ConfigHandler.save_llm_config(
                    provider=llm_config["provider"],
                    model=llm_config["model"],
                    base_url=llm_config["base_url"],
                    api_key=api_key_to_save,
                    **llm_kwargs,
                ):
                    return False
                if not ConfigHandler.save_local_ai_config(**local_save_kwargs):
                    return False
                if not ConfigHandler.save_config(
                    {
                        "ai_max_candidates": max_cand,
                        "strategy_min_turnover": min_turn,
                        "ai_max_concurrent_analysis": concurrency,
                        "ai_news_max_concurrent": news_concurrency,
                    }
                ):
                    return False
                if not ConfigHandler.save_ai_system_prompt(ai_prompt):
                    return False
                if not ConfigHandler.set_ai_news_prompt(news_prompt):
                    return False

                # failover 同步逻辑（纯 ConfigHandler IO，在线程池中执行）
                LLMConfigPanel._remove_primary_from_failover(llm_config["provider"])
                custom_models = llm_kwargs.get("custom_models", ConfigHandler.get_llm_config().get("custom_models", {}))
                LLMConfigPanel._sync_provider_credential_to_failover(
                    llm_config["provider"],
                    api_key_to_save,
                    llm_config["base_url"],
                    custom_models.get(llm_config["provider"]),
                )
                return True

            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                _save_configs_sync,
            )
            if not success:
                self.show_snack(I18n.get("settings_save_failed"), color=AppColors.ERROR)
                return

            # 保存成功后更新 panel 状态标志（在事件循环中安全访问 UI 属性）
            self.llm_config_panel._api_key_modified = False

            # ========== 阶段 4: 统一重载 AIService 配置 ==========

            await AIService().reload_config()

            self.show_snack(I18n.get("settings_verify_success"), color=AppColors.SUCCESS)

            local_path = local_config.get("model_path", "")
            if local_path:
                # 文件存在性检查异步化
                exists = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    os.path.exists,
                    local_path,
                )
                if not exists:
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
            from utils.error_classifier import classify_error, classify_severity

            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[AIBrainTab] SYSTEM-LEVEL failure saving config: %s", e, exc_info=True)
            else:
                logger.error("[AIBrainTab] Error saving config (%s): %s", error_info["code"], e, exc_info=True)
            self.show_snack(
                I18n.get("settings_snack_ai_error").format(
                    error=I18n.get("settings_save_failed"),
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
