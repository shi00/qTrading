"""LocalModelConfigPanel — 声明式组件 (Phase 3.2.4).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 ``class LocalModelConfigPanel(ft.Container)`` → ``@ft.component def LocalModelConfigPanel(vm, ...)``
- VM 由消费方实例化（AIBrainTab/OnboardingWizard 直接 new LocalModelConfigPanelViewModel）
- View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- page 访问改用 ``ft.context.page``（try/except 守卫 RuntimeError）
- FilePicker 通过 ``use_effect`` 注册到 ``page.services``，cleanup 时移除
- P1-1: cleanup 时调 ``vm.cancel_verification()`` (VM 命令) 而非直接 import LocalModelManager
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.settings_widgets import SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.local_model_config_panel_view_model import (
    LocalModelConfigPanelViewModel,
    LocalModelConfigState,
)

logger = logging.getLogger(__name__)

_INPUT_WIDTH_SMALL = 190

# --- Status display config ---

_STATUS_ICON_MAP = {
    "success": ft.Icons.CHECK_CIRCLE,
    "error": ft.Icons.ERROR,
    "warning": ft.Icons.WARNING,
    "info": ft.Icons.INFO,
}

_STATUS_COLOR_MAP = {
    "success": AppColors.SUCCESS,
    "error": AppColors.ERROR,
    "warning": AppColors.WARNING,
    "info": AppColors.TEXT_SECONDARY,
}


def _render_message(msg: Message | None) -> str:
    """Render a Message to localized text via I18n.get."""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


def _on_verify_click_factory(vm: LocalModelConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for verify button — submits vm.verify_model via page.run_task."""

    def _on_verify_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.verify_model)
        except RuntimeError:
            logger.debug("[LocalModelConfigPanel] page not available for verify_model")

    return _on_verify_click


def _on_save_click_factory(vm: LocalModelConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for save button — submits vm.save_config via page.run_task."""

    def _on_save_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.save_config)
        except RuntimeError:
            logger.debug("[LocalModelConfigPanel] page not available for save_config")

    return _on_save_click


async def _select_file(vm: LocalModelConfigPanelViewModel, file_picker: ft.FilePicker) -> None:
    """Open file picker and update vm.model_path on selection."""
    try:
        result = await file_picker.pick_files(
            allowed_extensions=["gguf"],
            dialog_title=I18n.get("settings_btn_select_file"),
        )
        if result and result.files and len(result.files) > 0:
            vm.update_model_path(result.files[0].path or "")
    except Exception as e:
        logger.error("[LocalModelConfigPanel] File pick failed: %s", e, exc_info=True)


def _on_select_file_click_factory(
    vm: LocalModelConfigPanelViewModel,
    file_picker: ft.FilePicker,
) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for file select button."""

    def _on_select_file_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_select_file, vm, file_picker)
        except RuntimeError:
            logger.debug("[LocalModelConfigPanel] page not available for file pick")

    return _on_select_file_click


@ft.component
def LocalModelConfigPanel(
    vm: LocalModelConfigPanelViewModel,
    *,
    show_save_button: bool = False,
    compact: bool = False,
    show_internal_loading: bool = True,
) -> ft.Container:
    """Local model configuration panel (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（AIBrainTab/OnboardingWizard 直接 new LocalModelConfigPanelViewModel）
    - View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        vm: 由消费方实例化的 LocalModelConfigPanelViewModel
        show_save_button: 是否显示保存按钮（default: False）
        compact: 是否使用紧凑布局（wizard 用，default: False）
        show_internal_loading: 是否显示内部 loading 指示器（default: True）
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- FilePicker lifecycle (register on page.services + cleanup) ---
    file_picker = ft.use_ref(lambda: ft.FilePicker()).current

    def _setup_file_picker() -> None:
        try:
            page = ft.context.page
            if page is not None and file_picker not in page.services:
                page.services.append(file_picker)
        except RuntimeError:
            logger.debug("[LocalModelConfigPanel] page not available for FilePicker setup")

    def _cleanup_file_picker() -> None:
        try:
            page = ft.context.page
            if page is not None and file_picker in page.services:
                page.services.remove(file_picker)
        except RuntimeError:
            pass
        # 清理未提交的验证状态 (P1-1: 经 VM 命令转发, 避免View 直接 import LocalModelManager)
        vm.cancel_verification()

    ft.use_effect(_setup_file_picker, dependencies=[], cleanup=_cleanup_file_picker)

    # --- Build form controls (driven by state) ---
    is_gpu_auto = state.n_gpu_layers == -1
    gpu_layers_display = 0 if is_gpu_auto else state.n_gpu_layers

    model_path_input = ft.TextField(
        label=I18n.get("settings_local_model_path"),
        value=state.model_path,
        expand=True,
        hint_text="C:/path/to/model.gguf",
        on_change=lambda e: vm.update_model_path(e.control.value),
    )

    btn_select_file = ft.OutlinedButton(
        content=I18n.get("settings_btn_select_file"),
        icon=ft.Icons.FOLDER_OPEN,
        on_click=_on_select_file_click_factory(vm, file_picker),
        disabled=state.is_verifying if show_internal_loading else False,
    )

    timeout_input = ft.TextField(
        label=I18n.get("settings_local_ai_timeout"),
        value=state.timeout,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text="300",
        on_change=lambda e: vm.update_timeout(e.control.value),
    )

    threads_input = ft.Slider(
        min=1,
        max=16,
        divisions=15,
        value=float(state.n_threads),
        label="{value}",
        tooltip=str(state.n_threads),
        on_change=lambda e: vm.update_threads(e.control.value),
    )

    gpu_auto_switch = ft.Switch(
        label=I18n.get("settings_local_gpu_auto"),
        value=is_gpu_auto,
        on_change=lambda e: vm.update_gpu_auto(e.control.value),
    )

    gpu_layers_input = ft.Slider(
        min=0,
        max=100,
        divisions=100,
        value=float(gpu_layers_display),
        label="{value}",
        tooltip=str(gpu_layers_display),
        visible=not is_gpu_auto,
        on_change=lambda e: vm.update_gpu_layers(e.control.value),
    )

    batch_input = ft.Dropdown(
        label=I18n.get("settings_local_batch"),
        value=str(state.n_batch),
        options=[ft.dropdown.Option(str(x)) for x in [512, 1024, 2048, 4096]],
        width=_INPUT_WIDTH_SMALL,
        on_select=lambda e: vm.update_batch(e.control.value or "512"),
    )

    ctx_input = ft.Dropdown(
        label=I18n.get("settings_local_ctx"),
        value=str(state.n_ctx),
        options=[ft.dropdown.Option(str(x)) for x in [2048, 4096, 8192, 16384, 32768]],
        width=_INPUT_WIDTH_SMALL,
        on_select=lambda e: vm.update_ctx(e.control.value or "4096"),
    )

    flash_attn_switch = ft.Switch(
        label=I18n.get("settings_local_flash_attn"),
        value=state.flash_attn,
        on_change=lambda e: vm.update_flash_attn(e.control.value),
    )

    # --- Status display (driven by state.status_message / status_type) ---
    status_text = _render_message(state.status_message)
    status_color = _STATUS_COLOR_MAP.get(state.status_type, AppColors.TEXT_SECONDARY)
    status_icon_name = _STATUS_ICON_MAP.get(state.status_type, ft.Icons.INFO)

    status_icon = ft.Icon(
        status_icon_name,
        visible=status_text != "",
        size=16,
        color=status_color,
    )
    status_text_ctrl = ft.Text(
        status_text,
        size=12,
        color=status_color,
    )

    # --- Loading indicator ---
    progress_indicator = ft.ProgressRing(
        visible=state.is_verifying if show_internal_loading else False,
        width=20,
        height=20,
        stroke_width=2,
    )

    # --- Buttons ---
    verify_button = ft.Button(
        content=I18n.get("wizard_btn_verify_model"),
        on_click=_on_verify_click_factory(vm),
        icon=ft.Icons.CHECK_CIRCLE,
        style=AppStyles.secondary_button(),
        disabled=state.is_verifying if show_internal_loading else False,
    )

    save_button = ft.Button(
        content=I18n.get("settings_save_config"),
        on_click=_on_save_click_factory(vm),
        icon=ft.Icons.SAVE,
        visible=show_save_button,
        style=AppStyles.primary_button(),
        disabled=state.is_saving if show_internal_loading else False,
    )

    # --- Advanced tile ---
    advanced_title = ft.Text(
        I18n.get("ai_advanced_settings"),
        size=14 if not compact else 12,
        weight=ft.FontWeight.BOLD,
    )
    advanced_subtitle = ft.Text(
        I18n.get("settings_hint_restart"),
        size=11,
        color=AppColors.WARNING,
    )
    advanced_tile = ft.ExpansionTile(
        title=advanced_title,
        subtitle=advanced_subtitle,
        controls=[
            ft.Container(height=10),
            ft.ResponsiveRow(
                [
                    ft.Column(
                        [
                            ft.Text(I18n.get("settings_local_threads"), size=12),
                            threads_input,
                        ],
                        col={"sm": 12, "md": 6},
                    ),
                    ft.Column(
                        [
                            ft.Text(I18n.get("settings_local_gpu_layers"), size=12),
                            gpu_auto_switch,
                            gpu_layers_input,
                        ],
                        col={"sm": 12, "md": 6},
                    ),
                    ft.Column([batch_input], col={"sm": 6, "md": 4}),
                    ft.Column([ctx_input], col={"sm": 6, "md": 4}),
                    ft.Column([timeout_input], col={"sm": 12, "md": 4}),
                    ft.Column([flash_attn_switch], col={"sm": 12, "md": 12}),
                ],
                run_spacing=15,
            ),
        ],
        expanded=False,
    )

    # --- Header / desc ---
    header_text = SectionHeader(I18n.get("settings_sec_local_ai"), title_key="settings_sec_local_ai")
    header_text.visible = not compact

    desc_text = ft.Text(
        value=I18n.get("settings_local_ai_desc"),
        size=12,
        color=AppColors.TEXT_SECONDARY,
        visible=not compact,
    )

    # --- Layout ---
    compact_main_align = ft.MainAxisAlignment.CENTER if compact else ft.MainAxisAlignment.START
    compact_cross_align = ft.CrossAxisAlignment.CENTER if compact else ft.CrossAxisAlignment.START

    action_buttons = ft.Row(
        controls=[verify_button, save_button, progress_indicator],
        alignment=compact_main_align,
    )

    form_content = ft.Column(
        controls=[
            header_text,
            desc_text,
            ft.Container(height=10) if not compact else ft.Container(height=5),
            ft.Row(
                [model_path_input, btn_select_file],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.END,
                alignment=compact_main_align,
            ),
            ft.Container(height=10) if not compact else ft.Container(height=5),
            advanced_tile,
            ft.Container(height=10) if not compact else ft.Container(height=5),
            action_buttons,
            ft.Row(
                [status_icon, status_text_ctrl],
                alignment=compact_main_align,
                spacing=5,
            ),
        ],
        spacing=10 if not compact else 6,
        horizontal_alignment=compact_cross_align,
    )

    if compact:
        return ft.Container(
            content=form_content,
            width=550,
            alignment=ft.Alignment.CENTER,
        )
    return ft.Container(content=form_content)


# --- Backward-compat: state type re-export for type checks ---
__all__ = ["LocalModelConfigPanel", "LocalModelConfigPanelViewModel", "LocalModelConfigState"]
