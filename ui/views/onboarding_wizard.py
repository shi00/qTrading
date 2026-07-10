"""Onboarding Wizard — 声明式组件 (Phase F.1).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式容器子类 → ``@ft.component def OnboardingWizard(on_complete)``
- OnboardingViewModel + 4 个 config panel VM 通过 ``use_viewmodel(factory=)`` 内部模式实例化,
  hook 负责实例化 + dispose on unmount
- 消费已声明式 DatabaseConfigPanel/TushareConfigPanel/LLMConfigPanel/LocalModelConfigPanel (函数调用, vm props)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 8 步状态机用 ``use_state`` + VM state.current_step 驱动条件渲染
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步任务: ``page.run_task`` 调度; R2 CancelledError 不被 ``except Exception`` 捕获
- 移除命令式生命周期回调 / 手动刷新 / page 引用持有 / resize 级联 / 命令式 VM 绑定 / locale 重建

Steps (8 total):
0. Welcome - Configuration overview
1. Database Configuration (Required)
2. Token Configuration (Required)
3. Cloud AI Configuration (Required)
4. Local Model Configuration (Optional)
5. Data Sync (Optional)
6. Schedule Setup (Optional)
7. Complete
"""

import logging
from collections.abc import Awaitable, Callable

import flet as ft

from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel
from ui.hooks import use_viewmodel
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel
from ui.viewmodels.local_model_config_panel_view_model import LocalModelConfigPanelViewModel
from ui.viewmodels.onboarding_view_model import STEP_CONFIGS, OnboardingViewModel
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

DEFAULT_SYNC_YEARS = 3
DEFAULT_SYNC_DAYS = DEFAULT_SYNC_YEARS * 365


# ============================================================================
# Module-level pure helpers
# ============================================================================


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _show_snack(msg: str, color: str) -> None:
    """通过 ``ft.context.page`` 显示 SnackBar。"""
    page = _get_page()
    if page is not None:
        page.show_dialog(ft.SnackBar(ft.Text(msg), bgcolor=color))


async def _default_on_complete() -> None:
    """默认完成回调 (no-op)。"""
    pass


async def _on_llm_test_connection(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    **kwargs,
) -> dict:
    """LLM 连接测试回调 — 委托 OnboardingViewModel 静态方法。"""
    return await OnboardingViewModel.test_llm_connection(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        **kwargs,
    )


async def _on_verify_local_model(model_path: str, config: dict) -> bool:
    """验证本地模型回调 — 委托 OnboardingViewModel 静态方法。"""
    return await OnboardingViewModel.verify_local_model(model_path, config)


async def _validate_cloud_ai(llm_vm: LLMConfigPanelViewModel) -> bool:
    """Cloud AI 步骤验证: 连接测试 + 保存配置。"""
    if await llm_vm.verify_connection():
        if not await llm_vm.save_config():
            logger.error("[OnboardingWizard] Failed to save LLM config")
            _show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)
            return False
        return True
    return False


async def _validate_local_model(local_model_vm: LocalModelConfigPanelViewModel) -> bool:
    """Local Model 步骤验证: 模型验证 + 保存配置 (空路径跳过)。"""
    model_path = local_model_vm.state.model_path.strip()
    if not model_path:
        return True
    if await local_model_vm.verify_model():
        if not await local_model_vm.save_config():
            logger.error("[OnboardingWizard] Failed to save local model config")
            _show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)
            return False
        return True
    return False


def _render_message(msg: Message | None) -> str:
    """渲染 Message 为本地化文本。"""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


def _create_overview_card(
    icon: str,
    color: str,
    title_key: str,
    desc_key: str,
    required: bool,
    is_hovered: bool,
    on_hover: Callable[[ft.ControlEvent], None],
) -> ft.Container:
    """创建概览卡片 (纯函数, hover 状态由参数注入)。"""
    required_text = I18n.get("wizard_required")
    optional_text = I18n.get("wizard_optional")

    if required:
        badge_bgcolor = ft.Colors.with_opacity(0.9, color)
        badge_text_color = AppColors.TEXT_ON_PRIMARY
        dot_color = AppColors.TEXT_ON_PRIMARY
    else:
        badge_bgcolor = ft.Colors.with_opacity(0.6, AppColors.TEXT_SECONDARY)
        badge_text_color = AppColors.TEXT_ON_PRIMARY
        dot_color = AppColors.TEXT_ON_PRIMARY

    badge = ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    width=5,
                    height=5,
                    border_radius=3,
                    bgcolor=dot_color,
                ),
                ft.Text(
                    required_text if required else optional_text,
                    size=9,
                    weight=ft.FontWeight.W_600,
                    color=badge_text_color,
                ),
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        bgcolor=badge_bgcolor,
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
    )

    icon_with_badge = ft.Stack(
        [
            ft.Container(
                content=ft.Icon(icon, size=32, color=color),
                width=52,
                height=52,
                border_radius=14,
                bgcolor=ft.Colors.with_opacity(0.12, color),
                alignment=ft.Alignment.CENTER,
            ),
            ft.Container(
                content=badge,
                top=-4,
                right=-4,
            ),
        ],
        width=64,
        height=60,
        clip_behavior=ft.ClipBehavior.NONE,
    )

    if is_hovered:
        border = ft.Border.all(1.5, ft.Colors.with_opacity(0.5, color))
        shadow = ft.BoxShadow(
            spread_radius=1,
            blur_radius=12,
            color=ft.Colors.with_opacity(0.2, color),
            offset=ft.Offset(0, 3),
        )
    else:
        border = ft.Border.all(1, ft.Colors.with_opacity(0.15, AppColors.PRIMARY))
        shadow = ft.BoxShadow(
            spread_radius=0,
            blur_radius=8,
            color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
            offset=ft.Offset(0, 2),
        )

    card_content = ft.Container(
        padding=20,
        border_radius=16,
        bgcolor=ft.Colors.with_opacity(0.7, AppColors.SURFACE),
        border=border,
        shadow=shadow,
        content=ft.Column(
            [
                icon_with_badge,
                ft.Container(height=16),
                ft.Text(
                    I18n.get(title_key),
                    size=16,
                    weight=ft.FontWeight.W_700,
                    color=AppColors.TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=6),
                ft.Text(
                    I18n.get(desc_key),
                    size=13,
                    color=ft.Colors.with_opacity(0.85, AppColors.TEXT_SECONDARY),
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        ),
        on_hover=on_hover,
        animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
    )

    return ft.Container(
        col={"sm": 6, "md": 4, "lg": 4},
        content=card_content,
    )


# ============================================================================
# OnboardingWizard
# ============================================================================


@ft.component
def OnboardingWizard(
    on_complete: Callable[[], Awaitable[None]] | None = None,
) -> ft.Container:
    """逐步引导配置向导 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - OnboardingViewModel + 4 个 config panel VM 通过 ``use_viewmodel(factory=)`` 内部模式实例化
    - 消费已声明式 DatabaseConfigPanel/TushareConfigPanel/LLMConfigPanel/LocalModelConfigPanel
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 8 步状态机用 VM state.current_step 驱动条件渲染
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步任务: ``page.run_task`` 调度, R2 CancelledError 不被 ``except Exception`` 捕获

    Args:
        on_complete: 完成回调 (异步, 完成步骤触发)
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(I18n.get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- OnboardingViewModel (内部模式: hook 实例化 + dispose on unmount) ---
    state, onboarding_vm = use_viewmodel(factory=lambda: OnboardingViewModel())

    # --- 4 config panel VM (内部模式, hook 负责 dispose) ---
    # on_change 回调捕获 onboarding_vm (已通过 use_viewmodel 持久化, 稳定引用)
    _db_state, database_vm = use_viewmodel(
        factory=lambda: DatabaseConfigPanelViewModel(
            load_password=True,
            on_change=lambda: onboarding_vm.invalidate_step("database"),
        )
    )
    _tushare_state, tushare_vm = use_viewmodel(
        factory=lambda: TushareConfigPanelViewModel(
            show_internal_loading=False,
        )
    )
    _llm_state, llm_vm = use_viewmodel(
        factory=lambda: LLMConfigPanelViewModel(
            on_test_connection=_on_llm_test_connection,
        )
    )
    _local_state, local_model_vm = use_viewmodel(
        factory=lambda: LocalModelConfigPanelViewModel(
            on_verify_model=_on_verify_local_model,
            on_change=lambda: onboarding_vm.invalidate_step("local_model"),
            show_internal_loading=False,
        )
    )

    # --- Pure UI state ---
    schedule_enabled, set_schedule_enabled = ft.use_state(True)
    schedule_time, set_schedule_time = ft.use_state(ConfigHandler.get_auto_update_time())
    language_value, set_language_value = ft.use_state(I18n.current_locale())
    hovered_card, set_hovered_card = ft.use_state(-1)

    # --- Bind panel methods to onboarding VM (每次渲染用最新闭包绑定, idempotent) ---
    onboarding_vm.bind(
        fn_validate_database=database_vm.save_config,
        fn_validate_token=tushare_vm.verify_token,
        fn_validate_cloud_ai=lambda: _validate_cloud_ai(llm_vm),
        fn_validate_local_model=lambda: _validate_local_model(local_model_vm),
        fn_push_schedule_state=lambda: onboarding_vm.set_schedule_state(
            enabled=schedule_enabled,
            time_str=schedule_time,
        ),
        on_complete=on_complete or _default_on_complete,
    )

    # --- Sync normalized schedule time from VM to UI input ---
    def _sync_normalized_time() -> None:
        if state.normalized_schedule_time and state.normalized_schedule_time != schedule_time:
            set_schedule_time(state.normalized_schedule_time)

    ft.use_effect(_sync_normalized_time, dependencies=[state.normalized_schedule_time])

    # --- Cleanup: cancel sync on unmount ---
    def _cleanup_setup() -> None:
        pass

    def _cleanup() -> None:
        page = _get_page()
        if page is None:
            return

        async def _do_cleanup() -> None:
            try:
                if onboarding_vm.sync_in_progress:
                    await onboarding_vm.cancel_sync()
            except Exception as exc:
                logger.debug("[OnboardingWizard] Cleanup cancel sync failed: %s", exc, exc_info=True)
            from services.local_model_manager import LocalModelManager

            LocalModelManager.cancel_verification_if_active()

        page.run_task(_do_cleanup)

    ft.use_effect(_cleanup_setup, dependencies=[], cleanup=_cleanup)

    # --- Async handlers ---
    async def _do_language_change(new_locale: str) -> None:
        """持久化 locale 并触发 I18n observable 重渲染。"""
        try:
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_locale, new_locale)
            if not success:
                set_language_value(I18n.current_locale())
                logger.warning("[OnboardingWizard] Failed to persist locale: %s", new_locale)
                return
            # I18n.set_locale 触发 observable → ft.use_state 自动重渲染
            I18n.set_locale(new_locale)
            page = _get_page()
            if page is not None and getattr(page, "locale_configuration", None):
                try:
                    normalized = I18n.current_locale()
                    parts = normalized.split("_")
                    lang = parts[0]
                    country = parts[1] if len(parts) > 1 else None
                    page.locale_configuration.current_locale = ft.Locale(lang, country)
                except Exception as ex:
                    logger.debug(
                        "[OnboardingWizard] Failed to update page locale configuration: %s",
                        ex,
                        exc_info=True,
                    )
        except Exception as ex:
            logger.error("[OnboardingWizard] Language change failed: %s", DataSanitizer.sanitize_error(ex))
            _show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    async def _next_step() -> None:
        UILogger.log_action("OnboardingWizard", "Click", f"next_step={state.current_step}")
        await onboarding_vm.next_step()

    async def _prev_step() -> None:
        UILogger.log_action("OnboardingWizard", "Click", f"prev_step={state.current_step}")
        await onboarding_vm.prev_step()

    async def _skip_step() -> None:
        UILogger.log_action("OnboardingWizard", "Click", f"skip_step={state.current_step}")
        await onboarding_vm.skip_step()

    async def _on_quick_sync() -> None:
        UILogger.log_action("OnboardingWizard", "Click", "btn_quick_sync")
        await onboarding_vm.start_sync(quick=True)

    async def _on_full_sync() -> None:
        UILogger.log_action("OnboardingWizard", "Click", "btn_full_sync")
        await onboarding_vm.start_sync(quick=False)

    async def _on_cancel_sync() -> None:
        UILogger.log_action("OnboardingWizard", "Click", "btn_cancel_sync")
        await onboarding_vm.cancel_sync()

    # --- Event handlers (sync wrappers → page.run_task) ---
    def _on_next(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_next_step)

    def _on_prev(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_prev_step)

    def _on_skip(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_skip_step)

    def _on_quick_sync_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_quick_sync)

    def _on_full_sync_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_full_sync)

    def _on_cancel_sync_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_cancel_sync)

    def _on_sync_later(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(onboarding_vm.skip_sync)

    def _on_language_select(e: ft.ControlEvent) -> None:
        new_locale = e.control.value
        set_language_value(new_locale)
        page = _get_page()
        if page is not None:
            page.run_task(_do_language_change, new_locale)

    def _on_card_hover(idx: int) -> Callable[[ft.ControlEvent], None]:
        def _hover(e: ft.ControlEvent) -> None:
            set_hovered_card(idx if e.data == "true" else -1)

        return _hover

    # --- Step indicators (1~6 显示) ---
    show_indicators = 1 <= state.current_step <= 6
    step_names = [
        None,
        I18n.get("wizard_step_database"),
        I18n.get("wizard_step_label_token"),
        I18n.get("wizard_step_label_ai"),
        I18n.get("wizard_step_local_model"),
        I18n.get("wizard_step_label_sync"),
        I18n.get("wizard_step_label_schedule"),
        None,
    ]
    current_step_name = step_names[state.current_step] or ""
    progress_percent = state.current_step / 6 if show_indicators else 0

    step_indicators = ft.Row(
        [
            ft.Column(
                [
                    ft.Text(
                        f"{current_step_name}  ({state.current_step}/6)",
                        size=14,
                        weight=ft.FontWeight.W_600,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Container(
                        content=ft.Stack(
                            [
                                ft.Container(
                                    width=200,
                                    height=4,
                                    bgcolor=AppColors.BORDER,
                                    border_radius=2,
                                ),
                                ft.Container(
                                    width=200 * progress_percent,
                                    height=4,
                                    bgcolor=AppColors.PRIMARY,
                                    border_radius=2,
                                ),
                            ],
                        ),
                        padding=ft.Padding.only(top=8),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=0,
            )
        ]
        if show_indicators
        else [],
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.START,
        visible=show_indicators,
    )

    # --- Header (step 0, 7 显示) ---
    show_header = state.current_step in (0, 7)
    header_container = ft.Column(
        [
            ft.Text(
                I18n.get("wizard_welcome_title"),
                size=32,
                weight=ft.FontWeight.BOLD,
                color=AppColors.PRIMARY,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                I18n.get("wizard_welcome_desc_with_time"),
                size=16,
                color=AppColors.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        visible=show_header,
    )

    # --- Step content (条件渲染) ---
    step = state.current_step

    if step == 0:
        # Welcome step
        language_dropdown = ft.Dropdown(
            label=I18n.get_language_label(),
            tooltip=I18n.get_language_label(),
            value=language_value,
            width=200,
            text_size=14,
            border_radius=8,
            content_padding=10,
            options=[ft.dropdown.Option(code, name) for code, name in I18n.get_language_options()],
            on_select=_on_language_select,
        )

        rocket_container = ft.Container(
            content=ft.Icon(ft.Icons.ROCKET_LAUNCH, size=72, color=AppColors.PRIMARY),
            width=120,
            height=120,
            border_radius=60,
            bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY),
            alignment=ft.Alignment.CENTER,
            shadow=ft.BoxShadow(
                spread_radius=2,
                blur_radius=24,
                color=ft.Colors.with_opacity(0.35, AppColors.PRIMARY),
                offset=ft.Offset(0, 4),
            ),
        )

        gradient_guide_text = ft.Text(
            I18n.get("wizard_welcome_guide"),
            size=20,
            weight=ft.FontWeight.W_600,
            text_align=ft.TextAlign.CENTER,
        )
        gradient_title = ft.ShaderMask(
            content=gradient_guide_text,
            shader=ft.LinearGradient(
                begin=ft.Alignment.CENTER_LEFT,
                end=ft.Alignment.CENTER_RIGHT,
                colors=[AppColors.PRIMARY, AppColors.ACCENT],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )

        overview_cards_data = [
            (ft.Icons.STORAGE, AppColors.PRIMARY, "wizard_overview_db_title", "wizard_overview_db_desc", True, 0),
            (ft.Icons.KEY, AppColors.PRIMARY, "wizard_overview_token_title", "wizard_overview_token_desc", True, 1),
            (
                ft.Icons.CLOUD,
                AppColors.ACCENT,
                "wizard_overview_cloud_ai_title",
                "wizard_overview_cloud_ai_desc",
                True,
                2,
            ),
            (
                ft.Icons.PSYCHOLOGY,
                AppColors.ACCENT,
                "wizard_overview_local_model_title",
                "wizard_overview_local_model_desc",
                False,
                3,
            ),
            (
                ft.Icons.CLOUD_SYNC,
                AppColors.PRIMARY,
                "wizard_overview_sync_title",
                "wizard_overview_sync_desc",
                False,
                4,
            ),
            (
                ft.Icons.SCHEDULE,
                AppColors.ACCENT,
                "wizard_overview_schedule_title",
                "wizard_overview_schedule_desc",
                False,
                5,
            ),
        ]

        step_content = ft.Column(
            [
                ft.Container(height=20),
                ft.Container(
                    content=language_dropdown,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Container(height=16),
                rocket_container,
                ft.Container(height=16),
                gradient_title,
                ft.Container(height=20),
                ft.ResponsiveRow(
                    [
                        _create_overview_card(
                            icon=icon,
                            color=color,
                            title_key=title_key,
                            desc_key=desc_key,
                            required=required,
                            is_hovered=(hovered_card == idx),
                            on_hover=_on_card_hover(idx),
                        )
                        for icon, color, title_key, desc_key, required, idx in overview_cards_data
                    ],
                    spacing=20,
                    run_spacing=20,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    elif step == 1:
        # Database step
        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.STORAGE, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_db_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_db_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                DatabaseConfigPanel(
                    vm=database_vm,
                    compact=True,
                    show_save_button=False,
                    show_header=False,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    elif step == 2:
        # Token step
        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.KEY, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step1_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step1_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                TushareConfigPanel(
                    vm=tushare_vm,
                    compact=True,
                    show_save_button=False,
                    show_register_link=True,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    elif step == 3:
        # Cloud AI step
        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.CLOUD, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step_cloud_ai_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step_cloud_ai_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Container(
                    content=LLMConfigPanel(
                        vm=llm_vm,
                        compact=True,
                        show_save_button=False,
                    ),
                    padding=10,
                    border_radius=8,
                    bgcolor=AppColors.SURFACE,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    elif step == 4:
        # Local Model step
        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.PSYCHOLOGY, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step_local_model_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step_local_model_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Container(
                    content=LocalModelConfigPanel(
                        vm=local_model_vm,
                        show_save_button=False,
                        compact=True,
                        show_internal_loading=False,
                    ),
                    padding=10,
                    border_radius=8,
                    bgcolor=AppColors.SURFACE,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    elif step == 5:
        # Data Sync step
        years = (
            ConfigHandler.get_init_history_years()
            if hasattr(ConfigHandler, "get_init_history_years")
            else DEFAULT_SYNC_YEARS
        )
        is_syncing = state.sync_in_progress

        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.CLOUD_DOWNLOAD, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step3_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step3_desc").format(years=years, hours=int(years * 1.5)),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=10),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.Icons.WARNING, color=AppColors.WARNING, size=20),
                            ft.Text(
                                I18n.get("wizard_sync_warning"),
                                size=12,
                                color=AppColors.WARNING,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=10,
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.1, AppColors.WARNING),
                ),
                ft.Container(height=20),
                ft.Row(
                    [
                        ft.Button(
                            content=I18n.get("wizard_sync_quick"),
                            icon=ft.Icons.FLASH_ON,
                            style=AppStyles.accent_button(),
                            on_click=_on_quick_sync_click,
                            disabled=is_syncing,
                        ),
                        ft.Button(
                            content=I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS),
                            icon=ft.Icons.CLOUD_SYNC,
                            style=AppStyles.primary_button(),
                            on_click=_on_full_sync_click,
                            disabled=is_syncing,
                        ),
                        ft.TextButton(
                            content=I18n.get("wizard_btn_sync_later"),
                            icon=ft.Icons.SCHEDULE,
                            on_click=_on_sync_later,
                            disabled=is_syncing,
                        ),
                        ft.Button(
                            content=I18n.get("wizard_btn_cancel"),
                            icon=ft.Icons.CANCEL,
                            color=AppColors.ERROR,
                            on_click=_on_cancel_sync_click,
                            visible=is_syncing,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    wrap=True,
                ),
                ft.Container(height=20),
                ft.ProgressBar(
                    width=AppStyles.CONTROL_WIDTH_LG,
                    value=state.sync_progress,
                    color=AppColors.ACCENT,
                    bgcolor=AppColors.BORDER,
                ),
                ft.Text(
                    _render_message(state.sync_progress_message) or I18n.get("wizard_status_ready"),
                    size=12,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    elif step == 6:
        # Schedule step
        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.SCHEDULE, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step4_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step4_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Row(
                    [
                        ft.Checkbox(
                            label=I18n.get("wizard_schedule_label"),
                            value=schedule_enabled,
                            active_color=AppColors.PRIMARY,
                            on_change=lambda e: set_schedule_enabled(e.control.value),
                        )
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Container(height=15),
                ft.Row(
                    [
                        ft.TextField(
                            label=I18n.get("wizard_schedule_time_label"),
                            value=schedule_time,
                            hint_text="HH:MM",
                            width=150,
                            text_align=ft.TextAlign.CENTER,
                            border_color=AppColors.PRIMARY,
                            label_style=ft.TextStyle(color=AppColors.PRIMARY),
                            on_change=lambda e: set_schedule_time(e.control.value),
                        )
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Text(
                    I18n.get("wizard_schedule_note"),
                    size=12,
                    color=AppColors.TEXT_HINT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    else:
        # Complete step (step == 7)
        step_content = ft.Column(
            [
                ft.Icon(ft.Icons.CELEBRATION, size=80, color=AppColors.SUCCESS),
                ft.Text(
                    I18n.get("wizard_step5_title"),
                    size=32,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step5_desc"),
                    size=16,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # --- Navigation buttons ---
    config = STEP_CONFIGS[state.current_step]
    nav_buttons: list[ft.Control] = []

    if config.show_prev:
        is_sync_step = config.id == "data_sync"
        nav_buttons.append(
            ft.Button(
                content=I18n.get("wizard_btn_prev"),
                icon=ft.Icons.ARROW_BACK,
                on_click=_on_prev,
                style=AppStyles.secondary_button(),
                disabled=(state.sync_in_progress and is_sync_step) or state.validation_in_progress,
            )
        )
    else:
        nav_buttons.append(ft.Container())

    if config.show_skip:
        nav_buttons.append(
            ft.TextButton(
                content=I18n.get(config.skip_text_key),
                on_click=_on_skip,
                disabled=state.validation_in_progress,
            )
        )

    if config.show_next:
        nav_buttons.append(
            ft.Button(
                content=I18n.get(config.next_text_key),
                icon=getattr(ft.Icons, config.next_icon, ft.Icons.ARROW_FORWARD),
                on_click=_on_next,
                style=AppStyles.primary_button(),
                disabled=state.validation_in_progress,
            )
        )

    navigation_bar = ft.Container(
        content=ft.Row(
            nav_buttons,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=20, vertical=10),
        bgcolor=AppColors.SURFACE,
        border=ft.Border.only(top=ft.BorderSide(1, AppColors.BORDER)),
    )

    # --- Loading overlay ---
    show_overlay = state.validation_in_progress
    loading_overlay = ft.Container(
        content=ft.Column(
            [
                ft.ProgressRing(width=40, height=40, stroke_width=3),
                ft.Text(
                    I18n.get("wizard_validating"),
                    size=14,
                    color=AppColors.TEXT_PRIMARY,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        bgcolor=ft.Colors.with_opacity(0.7, AppColors.BACKGROUND),
        visible=show_overlay,
        expand=True,
        alignment=ft.Alignment.CENTER,
        on_click=lambda e: None,
    )

    # --- Layout ---
    return ft.Container(
        expand=True,
        bgcolor=AppColors.BACKGROUND,
        content=ft.Stack(
            controls=[
                ft.Column(
                    controls=[
                        ft.Container(height=5),
                        header_container,
                        step_indicators,
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                        ft.Container(
                            content=ft.Column(
                                [step_content],
                                scroll=ft.ScrollMode.AUTO,
                                expand=True,
                            ),
                            expand=True,
                        ),
                        navigation_bar,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
                loading_overlay,
            ],
            expand=True,
        ),
    )
