"""ai_brain_tab — 声明式组件 (Phase E.1 + Task 5.2 + Phase 3.2 P1-1).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 ``class AIBrainTab(ft.Container)`` → ``@ft.component def AIBrainTab(show_snack_callback)``
- 3 个子 VM (LLM/failover/local_model) 通过 ``use_viewmodel(factory=)`` 内部模式实例化,
  hook 负责实例化 + dispose on unmount
- AIBrainSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2),
  构造注入 3 个子 VM (复用现有 config panel VM, 不再包一层薄 VM, §1.3 拒绝过度抽象),
  收敛 ConfigHandler/ThreadPoolManager 业务编排 (AI 调优参数 + 三阶段保存状态机)
- Phase 3.2 P1-1: AIService/LocalModelManager/ThreadPoolManager 业务编排全部下沉到 VM
  (test_connection/reload_service/verify_local_model 静态 command + MD5 检查整合进
  save_ai_settings); View 不再 lazy import 任何业务对象, 仅调 save_ai_settings() 并据
  state.save_state/state.warning_message 展示反馈
- 消费已声明式 LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel (函数调用, vm props 推送)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- save_state 从 ai_settings_state.save_state 派生 (声明式自动重渲染)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步保存: ``page.run_task`` 调度; R2 CancelledError 显式 raise
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from ui.components.config_panels.failover_config_panel import FailoverConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.confirm_dialog import ConfirmDialog
from ui.components.flet_type_helpers import safe_on_click
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.ai_brain_settings_view_model import (
    SAVE_ERROR,
    SAVE_SAVING,
    SAVE_SUCCESS,
    AIBrainSettingsViewModel,
)
from ui.viewmodels.failover_config_panel_view_model import FailoverConfigPanelViewModel
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel
from ui.viewmodels.local_model_config_panel_view_model import LocalModelConfigPanelViewModel
from utils.config_models import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT
from utils.log_decorators import UILogger
from utils.prompt_guard import MAX_PROMPT_LENGTH, validate_prompt

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
_INPUT_WIDTH_SMALL = 190


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
    - AIBrainSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2),
      构造注入 3 个子 VM (复用 save_config/get_current_config API), 收敛
      ConfigHandler/ThreadPoolManager 业务编排 (验证/保存/重载状态机)
    - Phase 3.2 P1-1: 子 VM 业务回调 (test_connection/reload_service/verify_local_model)
      注入 AIBrainSettingsViewModel 静态 command; View 不再 lazy import 业务对象
    - 消费已声明式 LLMConfigPanel/LocalModelConfigPanel/FailoverConfigPanel (函数调用, vm props)
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - save_state 从 ai_settings_state.save_state 派生 (声明式自动重渲染)
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, R2 CancelledError 显式 raise
    - View 仅调 ai_settings_vm.save_ai_settings() 并据 state.save_state/
      state.warning_message 展示反馈 (业务编排全部下沉到 VM)

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 3 子 VM (内部模式: hook 实例化 + dispose on unmount) ---
    # VM 工厂仅首次渲染调用一次 (use_viewmodel 内部 use_ref 持久化)
    # Phase 3.2 P1-1: 业务回调注入 AIBrainSettingsViewModel 静态 command (View 不再 lazy import)
    _llm_state, llm_vm = use_viewmodel(
        factory=lambda: LLMConfigPanelViewModel(
            on_test_connection=AIBrainSettingsViewModel.test_connection,
            on_reload_service=AIBrainSettingsViewModel.reload_service,
            on_save=lambda: _show_saved_snack(show_snack_callback),
        )
    )
    _failover_state, failover_vm = use_viewmodel(
        factory=lambda: FailoverConfigPanelViewModel(
            on_test_connection=AIBrainSettingsViewModel.test_connection,
            on_save=lambda: _show_saved_snack(show_snack_callback),
        )
    )
    _local_state, local_vm = use_viewmodel(
        factory=lambda: LocalModelConfigPanelViewModel(
            on_verify_model=AIBrainSettingsViewModel.verify_local_model,
            on_save=lambda: _show_saved_snack(show_snack_callback),
        )
    )

    # --- AIBrainSettingsViewModel (Task 5.2): 构造注入 3 个子 VM, 复用 save_config/get_current_config ---
    ai_settings_state, ai_settings_vm = use_viewmodel(
        factory=lambda: AIBrainSettingsViewModel(
            llm_vm=llm_vm,
            failover_vm=failover_vm,
            local_vm=local_vm,
        )
    )

    # --- Pure UI state (VM state 是唯一真值源, 无 use_state 本地副本) ---
    # P1-4 批次 2: ConfirmDialog open_state (消费方驱动, 无 dirty state 检测 §0.5.11.1 #78)
    reset_ai_dialog_open, set_reset_ai_dialog_open = ft.use_state(False)
    reset_news_dialog_open, set_reset_news_dialog_open = ft.use_state(False)

    # --- 异步保存 (R2: CancelledError 显式 raise; 调用 VM commands) ---
    async def _do_save_ai_settings() -> None:
        """保存 AI 配置 (云端 LLM + 本地模型 + 调优参数).

        Phase 3.2 P1-1: View 仅调 ai_settings_vm.save_ai_settings() 并据
        ai_settings_vm.state.save_state / state.warning_message 展示反馈;
        业务编排 (含 MD5 检查) 全部下沉到 VM。R2: CancelledError 显式 raise。
        """
        UILogger.log_action("AIBrainTab", "Click", "btn_save_ai")

        # Prompt 验证 (UI 反馈逻辑, 保留在 View)
        if not _validate_prompt_or_warn(ai_settings_state.ai_prompt_value, show_snack_callback):
            return
        if not _validate_prompt_or_warn(ai_settings_state.news_prompt_value, show_snack_callback):
            return

        try:
            await ai_settings_vm.save_ai_settings()
            # VM 内部已完成三阶段保存 + MD5 检查, 据 state 反馈 UI
            # (异步流程中读 ai_settings_vm.state 拿最新值, 非 use_viewmodel 快照)
            current_state = ai_settings_vm.state
            if current_state.save_state == SAVE_ERROR:
                show_snack_callback(I18n.get("settings_save_failed"), color=AppColors.ERROR)
            elif current_state.save_state == SAVE_SUCCESS:
                # warning_message 非空表示 MD5 检查发现模型文件变化 (VM 已写入 i18n key)
                if current_state.warning_message:
                    show_snack_callback(
                        I18n.get(current_state.warning_message),
                        color=AppColors.WARNING,
                    )
                else:
                    show_snack_callback(I18n.get("settings_snack_ai_saved"))
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

    def _on_save_ai(_e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_ai_settings)

    def _on_reset_ai_prompt(_e: ft.ControlEvent) -> None:
        # P1-4 批次 2: 打开 ConfirmDialog 而非直接重置 (统一提示, 无 dirty state)
        set_reset_ai_dialog_open(True)

    def _on_reset_news_prompt(_e: ft.ControlEvent) -> None:
        set_reset_news_dialog_open(True)

    def _do_confirm_reset_ai_prompt() -> None:
        """ConfirmDialog on_confirm: 实际执行 AI prompt 重置 (P1-4 批次 2)."""
        ai_settings_vm.set_ai_prompt_value(DEFAULT_AI_PROMPT)
        show_snack_callback(I18n.get("settings_snack_prompt_reset"))
        set_reset_ai_dialog_open(False)

    def _do_cancel_reset_ai_prompt() -> None:
        """ConfirmDialog on_cancel: 关闭对话框, 不执行重置。"""
        set_reset_ai_dialog_open(False)

    def _do_confirm_reset_news_prompt() -> None:
        """ConfirmDialog on_confirm: 实际执行 news prompt 重置 (P1-4 批次 2)."""
        ai_settings_vm.set_news_prompt_value(DEFAULT_NEWS_PROMPT)
        show_snack_callback(I18n.get("settings_snack_prompt_reset"))
        set_reset_news_dialog_open(False)

    def _do_cancel_reset_news_prompt() -> None:
        """ConfirmDialog on_cancel: 关闭对话框, 不执行重置。"""
        set_reset_news_dialog_open(False)

    # --- Build controls (状态驱动: value/disabled/color 从 state 派生) ---
    ai_max_candidates_input = ft.TextField(
        label=I18n.get("settings_max_candidates"),
        value=ai_settings_state.max_candidates_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=30),
        tooltip=I18n.get("settings_hint_ai_cost"),
        on_change=lambda e: ai_settings_vm.set_max_candidates_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    strategy_min_turnover_input = ft.TextField(
        label=I18n.get("settings_min_turnover"),
        value=ai_settings_state.min_turnover_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=2.0),
        tooltip=I18n.get("settings_hint_turnover"),
        on_change=lambda e: ai_settings_vm.set_min_turnover_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_concurrency_input = ft.TextField(
        label=I18n.get("settings_ai_concurrency"),
        value=ai_settings_state.ai_concurrency_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=5),
        tooltip=I18n.get("settings_hint_ai_model"),
        on_change=lambda e: ai_settings_vm.set_ai_concurrency_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_news_concurrency_input = ft.TextField(
        label=I18n.get("settings_ai_news_concurrency"),
        value=ai_settings_state.news_concurrency_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=1),
        tooltip=I18n.get("settings_hint_ai_news_concurrency"),
        on_change=lambda e: ai_settings_vm.set_news_concurrency_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_prompt_input = ft.TextField(
        label=I18n.get("settings_ai_prompt"),
        value=ai_settings_state.ai_prompt_value,
        multiline=True,
        min_lines=5,
        max_lines=15,
        text_size=AppStyles.FONT_SIZE_BODY_SM,
        hint_text=I18n.get("settings_ai_prompt_hint"),
        on_change=lambda e: ai_settings_vm.set_ai_prompt_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_news_prompt_input = ft.TextField(
        label=I18n.get("settings_news_prompt"),
        value=ai_settings_state.news_prompt_value,
        multiline=True,
        min_lines=3,
        max_lines=10,
        text_size=AppStyles.FONT_SIZE_BODY_SM,
        hint_text=I18n.get("settings_news_prompt_hint"),
        on_change=lambda e: ai_settings_vm.set_news_prompt_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    btn_reset_prompt = ft.TextButton(
        content=I18n.get("settings_reset_prompt"),
        icon=ft.Icons.RESTORE,
        on_click=safe_on_click(_on_reset_ai_prompt),
    )
    btn_reset_news_prompt = ft.TextButton(
        content=I18n.get("settings_reset_prompt"),
        icon=ft.Icons.RESTORE,
        on_click=safe_on_click(_on_reset_news_prompt),
    )

    # save_state 从 VM state 派生 (声明式自动重渲染)
    is_saving = ai_settings_state.save_state == SAVE_SAVING
    is_error = ai_settings_state.save_state == SAVE_ERROR
    is_success = ai_settings_state.save_state == SAVE_SUCCESS
    btn_save_ai = ft.Button(
        content=I18n.get("settings_save_ai"),
        icon=ft.Icons.SAVE,
        on_click=safe_on_click(_on_save_ai),
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
        size=AppStyles.FONT_SIZE_TITLE,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("ai_hint_cap"),
    )
    icon_help_min = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=AppStyles.FONT_SIZE_TITLE,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("ai_hint_turnover_min"),
    )
    icon_help_conc = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=AppStyles.FONT_SIZE_TITLE,
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
                        ft.Icon(ft.Icons.TUNE, size=AppStyles.FONT_SIZE_HEADLINE, color=AppColors.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    I18n.get("ai_tuning_desc"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
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
                    size=AppStyles.FONT_SIZE_CAPTION,
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
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
            ],
        ),
    )

    # 状态指示器 (从 VM state 派生)
    status_color = AppColors.SUCCESS if is_success else AppColors.ERROR if is_error else AppColors.TEXT_HINT
    status_text = I18n.get("common_saved") if is_success else I18n.get("sys_snack_save_err") if is_error else ""
    status_row = (
        ft.Row(
            [
                ft.Icon(
                    ft.Icons.INFO_OUTLINE,
                    size=AppStyles.FONT_SIZE_TITLE,
                    color=status_color,
                ),
                ft.Text(status_text, size=AppStyles.FONT_SIZE_CAPTION, color=status_color),
            ],
            spacing=5,
        )
        if status_text
        else ft.Container()
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
                        [btn_save_ai, save_progress, status_row],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=10,
                    ),
                    padding=ft.Padding.only(top=10, bottom=30, right=20),
                ),
                # P1-4 批次 2: 追加 2 个 ConfirmDialog (width=0/height=0 不影响布局,
                # use_dialog 挂载到 page overlay)
                ConfirmDialog(
                    open_state=reset_ai_dialog_open,
                    title=I18n.get("confirm_reset_title"),
                    body=I18n.get("confirm_reset_body"),
                    on_confirm=_do_confirm_reset_ai_prompt,
                    on_cancel=_do_cancel_reset_ai_prompt,
                    confirm_text=I18n.get("common_confirm"),
                    cancel_text=I18n.get("common_cancel"),
                ),
                ConfirmDialog(
                    open_state=reset_news_dialog_open,
                    title=I18n.get("confirm_reset_title"),
                    body=I18n.get("confirm_reset_body"),
                    on_confirm=_do_confirm_reset_news_prompt,
                    on_cancel=_do_cancel_reset_news_prompt,
                    confirm_text=I18n.get("common_confirm"),
                    cancel_text=I18n.get("common_cancel"),
                ),
            ],
            spacing=15,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
