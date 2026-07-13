"""ai_brain_tab — 声明式组件 (Phase E.1).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 ``class AIBrainTab(ft.Container)`` → ``@ft.component def AIBrainTab(show_snack_callback)``
- 3 个子 VM (LLM/failover/local_model) 通过 ``use_viewmodel(factory=)`` 内部模式实例化,
  hook 负责实例化 + dispose on unmount
- 消费已声明式 LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel (函数调用, vm props 推送)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 三阶段保存流程用 ``use_state(save_state)`` 驱动 (idle/saving/success/error)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步保存: ``page.run_task`` 调度; R2 CancelledError 显式 raise
- 移除命令式生命周期回调 / 手动刷新 / page 引用持有 / resize 级联
"""

import asyncio
import logging
import os
from collections.abc import Callable

import flet as ft

from ui.components.config_panels.failover_config_panel import FailoverConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.failover_config_panel_view_model import FailoverConfigPanelViewModel
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel
from ui.viewmodels.local_model_config_panel_view_model import LocalModelConfigPanelViewModel
from utils.config_handler import ConfigHandler
from utils.config_models import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT
from utils.log_decorators import UILogger
from utils.prompt_guard import MAX_PROMPT_LENGTH, validate_prompt
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
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

# 三阶段保存状态机
_SAVE_IDLE = "idle"
_SAVE_SAVING = "saving"
_SAVE_SUCCESS = "success"
_SAVE_ERROR = "error"


# ============================================================================
# Module-level pure helpers
# ============================================================================


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _validate_prompt_or_warn(prompt: str, show_snack: Callable) -> bool:
    """验证 Prompt 安全性，不合法时显示警告并返回 False。"""
    is_valid, warning = validate_prompt(prompt)
    if not is_valid:
        msg = I18n.get(warning, warning)
        if warning == "prompt_err_length":
            msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
        show_snack(f"⚠ {msg}", color=AppColors.WARNING)
        return False
    return True


async def _on_llm_test_connection(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    **kwargs,
) -> dict:
    """LLM 连接测试回调（注入 LLMConfigPanelViewModel/FailoverConfigPanelViewModel）。"""
    from services.ai_service import AIService

    return await AIService.test_connection(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        **kwargs,
    )


async def _on_reload_ai_service() -> None:
    """重载 AIService 配置回调（注入 LLMConfigPanelViewModel）。"""
    from services.ai_service import AIService

    await AIService().reload_config()


async def _on_verify_local_model(model_path: str, config: dict) -> bool:
    """验证本地模型回调（注入 LocalModelConfigPanelViewModel）。"""
    from services.local_model_manager import LocalModelManager

    manager = await LocalModelManager.get_instance()
    return await manager.load_model(model_path, config, is_verification=True)


def _show_saved_snack(show_snack: Callable) -> None:
    """配置保存成功 snack（注入 LLM/failover/local_model VM 的 on_save 回调）。"""
    show_snack(I18n.get("settings_verify_success"), color=AppColors.SUCCESS)


# ============================================================================
# AIBrainTab
# ============================================================================


@ft.component
def AIBrainTab(show_snack_callback: Callable) -> ft.Container:
    """AI Brain 配置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - 3 个子 VM (LLM/failover/local_model) 通过 ``use_viewmodel(factory=)`` 内部模式实例化,
      hook 负责 VM 实例化 + dispose on unmount
    - 消费已声明式 LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel (函数调用, vm props)
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 三阶段保存流程用 ``use_state(save_state)`` 驱动 (idle/saving/success/error)
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, R2 CancelledError 显式 raise

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 3 子 VM (内部模式: hook 实例化 + dispose on unmount) ---
    # VM 工厂仅首次渲染调用一次 (use_viewmodel 内部 use_ref 持久化)
    _llm_state, llm_vm = use_viewmodel(
        factory=lambda: LLMConfigPanelViewModel(
            on_test_connection=_on_llm_test_connection,
            on_reload_service=_on_reload_ai_service,
            on_save=lambda: _show_saved_snack(show_snack_callback),
        )
    )
    _failover_state, failover_vm = use_viewmodel(
        factory=lambda: FailoverConfigPanelViewModel(
            on_test_connection=_on_llm_test_connection,
            on_save=lambda: _show_saved_snack(show_snack_callback),
        )
    )
    _local_state, local_vm = use_viewmodel(
        factory=lambda: LocalModelConfigPanelViewModel(
            on_verify_model=_on_verify_local_model,
            on_save=lambda: _show_saved_snack(show_snack_callback),
        )
    )

    # --- Pure UI state (ConfigHandler 读取初始值, use_state 持久化) ---
    max_candidates_value, set_max_candidates = ft.use_state(str(ConfigHandler.get_ai_max_candidates()))
    min_turnover_value, set_min_turnover = ft.use_state(str(ConfigHandler.get_strategy_min_turnover()))
    ai_concurrency_value, set_ai_concurrency = ft.use_state(
        str(max(_CONCURRENCY_MIN, ConfigHandler.get_ai_max_concurrent_analysis()))
    )
    news_concurrency_value, set_news_concurrency = ft.use_state(str(ConfigHandler.get_ai_news_max_concurrent()))
    ai_prompt_value, set_ai_prompt = ft.use_state(ConfigHandler.get_ai_system_prompt())
    news_prompt_value, set_news_prompt = ft.use_state(ConfigHandler.get_ai_news_prompt())
    save_state, set_save_state = ft.use_state(_SAVE_IDLE)

    # --- 三阶段保存 (state 驱动: idle → saving → success/error) ---
    async def _do_save_ai_settings() -> None:
        """保存 AI 配置 (云端 LLM + 本地模型 + 调优参数).

        采用三阶段模式: 先验证所有输入 → 再统一保存 → 最后统一重载。
        避免部分保存导致磁盘与内存不一致。
        R2: CancelledError 显式 raise。
        """
        from services.ai_service import AIService
        from services.local_model_manager import LocalModelManager

        UILogger.log_action("AIBrainTab", "Click", "btn_save_ai")
        set_save_state(_SAVE_SAVING)
        try:
            # ========== 阶段 1: 验证所有输入（不写入任何配置） ==========
            max_cand_str = (max_candidates_value or "").strip()
            min_turn_str = (min_turnover_value or "").strip()

            if not max_cand_str or not min_turn_str:
                show_snack_callback(I18n.get("ai_snack_fields_empty"), color=AppColors.ERROR)
                set_save_state(_SAVE_ERROR)
                return

            try:
                max_cand = int(max_cand_str)
                min_turn = float(min_turn_str)
                if not (_MAX_CANDIDATES_MIN <= max_cand <= _MAX_CANDIDATES_MAX):
                    show_snack_callback(
                        I18n.get("ai_snack_invalid_range").format(
                            field=I18n.get("settings_max_candidates"),
                            min=_MAX_CANDIDATES_MIN,
                            max=_MAX_CANDIDATES_MAX,
                        ),
                        color=AppColors.ERROR,
                    )
                    set_save_state(_SAVE_ERROR)
                    return
                if not (_MIN_TURNOVER_MIN <= min_turn <= _MIN_TURNOVER_MAX):
                    show_snack_callback(
                        I18n.get("ai_snack_invalid_range").format(
                            field=I18n.get("settings_min_turnover"),
                            min=_MIN_TURNOVER_MIN,
                            max=_MIN_TURNOVER_MAX,
                        ),
                        color=AppColors.ERROR,
                    )
                    set_save_state(_SAVE_ERROR)
                    return
            except ValueError:
                show_snack_callback(I18n.get("ai_snack_param_err"), color=AppColors.ERROR)
                set_save_state(_SAVE_ERROR)
                return

            concurrency_str = (ai_concurrency_value or "").strip()
            try:
                concurrency = int(concurrency_str)
                if not (_CONCURRENCY_MIN <= concurrency <= _CONCURRENCY_MAX):
                    raise ValueError("Range")
            except ValueError:
                show_snack_callback(
                    I18n.get("ai_snack_invalid_range").format(
                        field=I18n.get("settings_ai_concurrency"),
                        min=_CONCURRENCY_MIN,
                        max=_CONCURRENCY_MAX,
                    ),
                    color=AppColors.ERROR,
                )
                set_save_state(_SAVE_ERROR)
                return

            news_concurrency_str = (news_concurrency_value or "").strip()
            try:
                news_concurrency = int(news_concurrency_str)
                if not (_NEWS_CONCURRENCY_MIN <= news_concurrency <= _NEWS_CONCURRENCY_MAX):
                    raise ValueError("Range")
            except ValueError:
                show_snack_callback(
                    I18n.get("ai_snack_invalid_range").format(
                        field=I18n.get("settings_ai_news_concurrency"),
                        min=_NEWS_CONCURRENCY_MIN,
                        max=_NEWS_CONCURRENCY_MAX,
                    ),
                    color=AppColors.ERROR,
                )
                set_save_state(_SAVE_ERROR)
                return

            if not _validate_prompt_or_warn(ai_prompt_value, show_snack_callback):
                set_save_state(_SAVE_ERROR)
                return
            if not _validate_prompt_or_warn(news_prompt_value, show_snack_callback):
                set_save_state(_SAVE_ERROR)
                return

            # ========== 阶段 2: 提取 UI 值 ==========
            local_config = local_vm.get_current_config()
            local_save_kwargs = {
                "model_path": local_config.get("model_path", ""),
                "timeout": local_config.get("timeout", 300),
                "n_threads": local_config.get("n_threads", 4),
                "n_batch": local_config.get("n_batch", 512),
                "n_ctx": local_config.get("n_ctx", 2048),
                "flash_attn": local_config.get("flash_attn", False),
                "n_gpu_layers": local_config.get("n_gpu_layers", 0),
            }

            # ========== 阶段 3: 统一保存所有配置 ==========
            # 先保存 LLM 配置 (VM 收敛 Azure 字段/custom_models 历史/failover 同步/api_key_modified reset)
            llm_saved = await llm_vm.save_config()
            if not llm_saved:
                show_snack_callback(I18n.get("settings_save_failed"), color=AppColors.ERROR)
                set_save_state(_SAVE_ERROR)
                return

            def _save_configs_sync() -> bool:
                """LocalModel + 其他 AI 配置保存操作, 在 IO 线程池执行。"""
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
                if not ConfigHandler.save_ai_system_prompt(ai_prompt_value):
                    return False
                return ConfigHandler.set_ai_news_prompt(news_prompt_value)

            success = await ThreadPoolManager().run_async(TaskType.IO, _save_configs_sync)
            if not success:
                show_snack_callback(I18n.get("settings_save_failed"), color=AppColors.ERROR)
                set_save_state(_SAVE_ERROR)
                return

            # 提交验证模式 (如果活跃) — 验证模型成为正式模型
            LocalModelManager.commit_verification_if_active()

            # ========== 阶段 4: 统一重载 AIService 配置 ==========
            await AIService().reload_config()

            show_snack_callback(I18n.get("settings_verify_success"), color=AppColors.SUCCESS)

            local_path = local_config.get("model_path", "")
            if local_path:
                exists = await ThreadPoolManager().run_async(TaskType.IO, os.path.exists, local_path)
                if not exists:
                    show_snack_callback(
                        I18n.get("ai_model_file_not_found"),
                        color=AppColors.ERROR,
                    )
                    set_save_state(_SAVE_ERROR)
                    return

                show_snack_callback(I18n.get("ai_verifying_model"))
                local_mgr = await LocalModelManager.get_instance()
                loaded_md5 = local_mgr.get_loaded_model_md5()
                new_md5 = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    LocalModelManager.calculate_file_md5,
                    local_path,
                )

                if loaded_md5 and new_md5 and loaded_md5 != new_md5:
                    show_snack_callback(
                        I18n.get("ai_local_model_changed"),
                        color=AppColors.WARNING,
                    )
                else:
                    show_snack_callback(I18n.get("settings_snack_ai_saved"))
            else:
                show_snack_callback(I18n.get("settings_snack_ai_saved"))

            set_save_state(_SAVE_SUCCESS)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            from utils.error_classifier import classify_error, classify_severity

            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                logger.critical("[AIBrainTab] SYSTEM-LEVEL failure saving config: %s", e, exc_info=True)
            else:
                logger.error(
                    "[AIBrainTab] Error saving config (%s): %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            show_snack_callback(
                I18n.get("settings_snack_ai_error").format(
                    error=I18n.get("settings_save_failed"),
                ),
                color=AppColors.ERROR,
            )
            set_save_state(_SAVE_ERROR)

    def _on_save_ai(_e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_ai_settings)

    def _on_reset_ai_prompt(_e: ft.ControlEvent) -> None:
        set_ai_prompt(DEFAULT_AI_PROMPT)
        show_snack_callback(I18n.get("settings_snack_prompt_reset"))

    def _on_reset_news_prompt(_e: ft.ControlEvent) -> None:
        set_news_prompt(DEFAULT_NEWS_PROMPT)
        show_snack_callback(I18n.get("settings_snack_prompt_reset"))

    # --- Build controls (状态驱动: value/disabled/color 从 state 派生) ---
    ai_max_candidates_input = ft.TextField(
        label=I18n.get("settings_max_candidates"),
        value=max_candidates_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=30),
        tooltip=I18n.get("settings_hint_ai_cost"),
        on_change=lambda e: set_max_candidates(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    strategy_min_turnover_input = ft.TextField(
        label=I18n.get("settings_min_turnover"),
        value=min_turnover_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=2.0),
        tooltip=I18n.get("settings_hint_turnover"),
        on_change=lambda e: set_min_turnover(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_concurrency_input = ft.TextField(
        label=I18n.get("settings_ai_concurrency"),
        value=ai_concurrency_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=5),
        tooltip=I18n.get("settings_hint_ai_model"),
        on_change=lambda e: set_ai_concurrency(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_news_concurrency_input = ft.TextField(
        label=I18n.get("settings_ai_news_concurrency"),
        value=news_concurrency_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=1),
        tooltip=I18n.get("settings_hint_ai_news_concurrency"),
        on_change=lambda e: set_news_concurrency(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_prompt_input = ft.TextField(
        label=I18n.get("settings_ai_prompt"),
        value=ai_prompt_value,
        multiline=True,
        min_lines=5,
        max_lines=15,
        text_size=_FONT_SIZE_BODY,
        hint_text=I18n.get("settings_ai_prompt_hint"),
        on_change=lambda e: set_ai_prompt(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_news_prompt_input = ft.TextField(
        label=I18n.get("settings_news_prompt"),
        value=news_prompt_value,
        multiline=True,
        min_lines=3,
        max_lines=10,
        text_size=_FONT_SIZE_BODY,
        hint_text=I18n.get("settings_news_prompt_hint"),
        on_change=lambda e: set_news_prompt(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    btn_reset_prompt = ft.TextButton(
        content=I18n.get("settings_reset_prompt"),
        icon=ft.Icons.RESTORE,
        on_click=_on_reset_ai_prompt,
    )
    btn_reset_news_prompt = ft.TextButton(
        content=I18n.get("settings_reset_prompt"),
        icon=ft.Icons.RESTORE,
        on_click=_on_reset_news_prompt,
    )

    is_saving = save_state == _SAVE_SAVING
    btn_save_ai = ft.Button(
        content=I18n.get("settings_save_ai"),
        icon=ft.Icons.SAVE,
        on_click=_on_save_ai,
        style=AppStyles.primary_button(),
        height=40,
        disabled=is_saving,
    )
    save_progress = ft.ProgressRing(
        visible=is_saving,
        width=20,
        height=20,
        stroke_width=2,
    )

    # --- Sub-panels (函数调用, vm props 推送) ---
    llm_panel = LLMConfigPanel(vm=llm_vm, show_save_button=False)
    failover_panel = FailoverConfigPanel(vm=failover_vm, show_save_button=False)
    local_model_panel = LocalModelConfigPanel(vm=local_vm, show_save_button=False)

    # --- Cards ---
    card_connection = DashboardCard(
        content=ft.Column([llm_panel]),
    )
    card_failover = DashboardCard(
        content=ft.Column([failover_panel]),
    )
    card_local_ai = DashboardCard(
        content=ft.Column([local_model_panel]),
    )

    # --- Tuning card ---
    icon_help_max = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=16,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("ai_hint_cap"),
    )
    icon_help_min = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=16,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("ai_hint_turnover_min"),
    )
    icon_help_conc = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=16,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("settings_hint_ai_model"),
    )

    section_header_tuning = SectionHeader(
        I18n.get("settings_sec_tuning"),
        title_key="settings_sec_tuning",
    )
    card_tuning = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        section_header_tuning,
                        ft.Icon(ft.Icons.TUNE, size=20, color=AppColors.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    I18n.get("ai_tuning_desc"),
                    size=_FONT_SIZE_BODY,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Container(height=10),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [
                                ft.Row(
                                    [ai_max_candidates_input, icon_help_max],
                                    spacing=5,
                                ),
                            ],
                            col={"sm": 12, "md": 4},
                        ),
                        ft.Column(
                            [
                                ft.Row(
                                    [strategy_min_turnover_input, icon_help_min],
                                    spacing=5,
                                ),
                            ],
                            col={"sm": 12, "md": 4},
                        ),
                        ft.Column(
                            [
                                ft.Row(
                                    [ai_concurrency_input, icon_help_conc],
                                    spacing=5,
                                ),
                                ft.Row(
                                    [ai_news_concurrency_input],
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

    # --- Prompt card ---
    section_header_persona = SectionHeader(
        I18n.get("ai_sec_persona"),
        title_key="ai_sec_persona",
    )
    section_header_news_prompt = SectionHeader(
        I18n.get("settings_news_prompt"),
        title_key="settings_news_prompt",
    )
    card_prompt = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [section_header_persona, btn_reset_prompt],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=ai_prompt_input,
                    border=ft.Border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),
                ),
                ft.Text(
                    I18n.get("settings_ai_prompt_hint"),
                    size=_FONT_SIZE_HINT,
                    color=AppColors.TEXT_HINT,
                ),
                ft.Divider(height=20, color=AppColors.BORDER),
                ft.Row(
                    [section_header_news_prompt, btn_reset_news_prompt],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=ai_news_prompt_input,
                    border=ft.Border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),
                ),
                ft.Text(
                    I18n.get("settings_news_prompt_hint"),
                    size=_FONT_SIZE_HINT,
                    color=AppColors.TEXT_HINT,
                ),
            ],
        ),
    )

    return ft.Container(
        content=ft.Column(
            controls=[
                card_connection,
                card_failover,
                card_local_ai,
                card_tuning,
                card_prompt,
                ft.Container(
                    content=ft.Row(
                        [btn_save_ai, save_progress],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=10,
                    ),
                    padding=ft.Padding.only(top=10, bottom=30, right=20),
                ),
            ],
            spacing=15,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
