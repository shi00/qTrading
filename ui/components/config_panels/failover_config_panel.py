"""FailoverConfigPanel — 声明式组件 (Phase D.1).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 Container 子类 + V0 page-ref mixin → ``@ft.component def FailoverConfigPanel(vm, ...)``
- 旧命令式 AlertDialog 子类 + V0 page-ref mixin → ``@ft.component def ProviderCredentialDialog(vm)``
- VM 由消费方实例化（AIBrainTab 需要 ``vm.reload_config`` / ``vm.save_config`` 引用）
- View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- Dialog 用条件渲染 + ``ft.use_dialog`` hook（Phase 3.0.2 模式）
- 移除命令式生命周期回调、手动 update、手动 locale 刷新、命令式 dialog 挂载/卸载 API
- page 访问改用 ``ft.context.page``（try/except 守卫 RuntimeError）
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.components.settings_widgets import SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.failover_config_panel_view_model import (
    FailoverConfigPanelViewModel,
    FailoverItem,
)
from utils.llm_providers import LLM_PROVIDERS, get_display_tag

logger = logging.getLogger(__name__)

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
    "info": AppColors.PRIMARY,
}


def _render_message(msg: Message | None) -> str:
    """Render a Message to localized text via I18n.get.

    支持 "detail" param（追加为 ": detail"），用于拼接 i18n key + 动态错误信息。
    """
    if msg is None:
        return ""
    detail = msg.params.get("detail", "")
    format_params = {k: v for k, v in msg.params.items() if k != "detail"}
    base = I18n.get(msg.key, **format_params)
    if detail:
        return f"{base}: {detail}"
    return base


# --- Dialog helper functions ---


def _build_provider_options(
    is_edit: bool,
    existing_providers: tuple[str, ...],
) -> list[ft.dropdown.Option]:
    """构建供应商下拉选项（排除 custom + 已存在供应商）。"""
    options: list[ft.dropdown.Option] = []
    for pid, pinfo in LLM_PROVIDERS.items():
        if pid == "custom":
            continue
        if not is_edit and pid in existing_providers:
            continue
        options.append(ft.dropdown.Option(key=pid, text=pinfo.get("name", pid)))
    return options


def _build_model_options(provider: str) -> list[ft.dropdown.Option]:
    """构建指定供应商的模型下拉选项（tag 需 i18n）。"""
    pinfo = LLM_PROVIDERS.get(provider, {})
    models = pinfo.get("models", [])

    options: list[ft.dropdown.Option] = []
    for m in models:
        label = str(m.get("id", ""))
        tag = m.get("tag", "")
        display_tag = get_display_tag(tag)
        if display_tag:
            label += f" ({display_tag})"
        options.append(ft.dropdown.Option(key=m.get("id"), text=label))
    return options


def _build_links_row(provider: str) -> ft.Row:
    """构建供应商相关链接行（console_url / pricing_url / models_url）。"""
    pinfo = LLM_PROVIDERS.get(provider, {})

    links: list[ft.Control] = []
    console_url = pinfo.get("console_url")
    if console_url:
        links.append(
            ft.TextButton(
                content=I18n.get("llm_get_api_key"),
                url=console_url,
            )
        )
    pricing_url = pinfo.get("pricing_url")
    if pricing_url:
        links.append(
            ft.TextButton(
                content=I18n.get("llm_view_pricing"),
                url=pricing_url,
            )
        )
    models_url = pinfo.get("models_url")
    if models_url:
        links.append(
            ft.TextButton(
                content=I18n.get("llm_view_models"),
                url=models_url,
            )
        )

    return ft.Row(controls=links, spacing=10, wrap=True)


# --- Event handler factories (submit VM commands via page.run_task) ---


def _run_task_factory(coro_func: Callable, *args) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler that submits a VM async command via page.run_task.

    R16: Flet 事件处理器中不能直接 await，必须通过 page.run_task 提交。
    """

    def _on_click(_e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(coro_func, *args)
        except RuntimeError:
            logger.debug("[FailoverConfigPanel] page not available for %s", coro_func.__name__)

    return _on_click


def _run_task_no_args(coro_func: Callable) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler that submits a VM async command (no args) via page.run_task."""
    return _run_task_factory(coro_func)


# --- ProviderCredentialDialog (declarative) ---


@ft.component
def ProviderCredentialDialog(vm: FailoverConfigPanelViewModel) -> ft.Control:
    """Provider credential dialog (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 声明式范式 + Phase 3.0.2 spike 模式:
    - ``use_viewmodel(vm=vm)`` 订阅 VM state 变化
    - ``ft.use_state(get_observable_state)`` 自动重渲染 on locale switch
    - Dialog 用条件渲染 + ``ft.use_dialog`` hook（state.dialog_open 驱动）
    - 无 did_mount/will_unmount/refresh_locale/show_dialog/pop_dialog/.update()

    Args:
        vm: 由消费方实例化的 FailoverConfigPanelViewModel（外部 VM 模式）
    """
    # --- Subscribe to VM state changes (外部 VM 模式) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Dialog form controls (driven by state) ---
    provider_options = _build_provider_options(state.dialog_is_edit, state.dialog_existing_providers)
    model_options = _build_model_options(state.dialog_provider)
    links_row = _build_links_row(state.dialog_provider)

    provider_dropdown = ft.Dropdown(
        label=I18n.get("failover_select_provider"),
        options=provider_options,
        value=state.dialog_provider or None,
        width=400,
        on_select=lambda e: vm.update_dialog_provider(e.control.value) if e.control.value else None,
        disabled=state.dialog_is_edit,
    )

    model_dropdown = ft.Dropdown(
        label=I18n.get("failover_select_model"),
        options=model_options,
        value=state.dialog_model or None,
        width=400,
        on_select=lambda e: vm.update_dialog_model(e.control.value) if e.control.value else None,
        visible=not state.dialog_custom_model,
    )

    custom_model_input = ft.TextField(
        label=I18n.get("llm_custom_model"),
        value=state.dialog_custom_model,
        width=400,
        hint_text=I18n.get("failover_custom_model_hint"),
        on_change=lambda e: vm.update_dialog_custom_model(e.control.value),
    )

    base_url_input = ft.TextField(
        label=I18n.get("failover_base_url_optional"),
        value=state.dialog_base_url,
        width=400,
        hint_text=I18n.get("failover_base_url_hint"),
        on_change=lambda e: vm.update_dialog_base_url(e.control.value),
    )

    api_key_input = ft.TextField(
        label=I18n.get("llm_api_key"),
        value=state.dialog_api_key,
        width=400,
        password=True,
        can_reveal_password=True,
        on_change=lambda e: vm.update_dialog_api_key(e.control.value),
    )

    # --- Dialog status display ---
    status_text = _render_message(state.dialog_status_message)
    status_color = _STATUS_COLOR_MAP.get(state.dialog_status_type, AppColors.PRIMARY)
    status_icon_name = _STATUS_ICON_MAP.get(state.dialog_status_type, ft.Icons.INFO)

    status_row = (
        ft.Row(
            [
                ft.Icon(status_icon_name, visible=status_text != "", size=16, color=status_color),
                ft.Text(status_text, size=12, color=status_color),
            ],
            spacing=5,
        )
        if status_text
        else ft.Container()
    )

    # --- Dialog actions ---
    cancel_btn = ft.TextButton(
        content=I18n.get("common_cancel"),
        on_click=lambda _e: vm.close_dialog(),
    )

    test_btn = ft.TextButton(
        content=I18n.get("failover_test_connection"),
        on_click=safe_on_click(_run_task_no_args(vm.test_credential)),
        disabled=state.dialog_is_testing,
    )

    confirm_btn = ft.Button(
        content=I18n.get("common_confirm"),
        on_click=safe_on_click(_run_task_no_args(vm.confirm_credential)),
        style=AppStyles.primary_button(),
        disabled=state.dialog_is_saving,
    )

    # --- Conditional render dialog via ft.use_dialog ---
    dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("failover_dialog_title")),
            content=ft.Column(
                [
                    provider_dropdown,
                    model_dropdown,
                    custom_model_input,
                    base_url_input,
                    api_key_input,
                    links_row,
                    status_row,
                ],
                tight=True,
                spacing=12,
                width=440,
            ),
            actions=[cancel_btn, test_btn, confirm_btn],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        if state.dialog_open
        else None
    )
    ft.use_dialog(dialog)

    # 宿主容器（不可见，仅承载 use_dialog hook）
    return ft.Container(width=0, height=0)


# --- FailoverConfigPanel (declarative) ---


def _build_list_item(
    index: int,
    item: FailoverItem,
    total: int,
    vm: FailoverConfigPanelViewModel,
) -> ft.Container:
    """构建单个 failover 列表项（纯函数，由 state.failover_items 驱动）。"""
    status_icon = ft.Icon(
        ft.Icons.CHECK_CIRCLE,
        size=16,
        color=AppColors.SUCCESS if item.has_credential else AppColors.WARNING,
    )
    status_text = ft.Text(
        I18n.get("failover_credential_ok") if item.has_credential else I18n.get("failover_credential_missing"),
        size=11,
        color=AppColors.SUCCESS if item.has_credential else AppColors.WARNING,
    )

    btn_up = ft.IconButton(
        ft.Icons.ARROW_UPWARD,
        icon_size=16,
        on_click=safe_on_click(_run_task_factory(vm.move_item, index, -1)),
        disabled=index == 0,
        tooltip=I18n.get("failover_move_up"),
    )
    btn_down = ft.IconButton(
        ft.Icons.ARROW_DOWNWARD,
        icon_size=16,
        on_click=safe_on_click(_run_task_factory(vm.move_item, index, 1)),
        disabled=index == total - 1,
        tooltip=I18n.get("failover_move_down"),
    )
    btn_edit = ft.IconButton(
        ft.Icons.EDIT,
        icon_size=16,
        on_click=safe_on_click(_run_task_factory(vm.open_edit_dialog, index)),
        tooltip=I18n.get("failover_edit"),
    )
    btn_delete = ft.IconButton(
        ft.Icons.DELETE_OUTLINE,
        icon_size=16,
        on_click=safe_on_click(_run_task_factory(vm.delete_item, index)),
        tooltip=I18n.get("failover_delete"),
    )

    left_section = ft.Row(
        [
            ft.Text(f"{index + 1}.", size=13, weight=ft.FontWeight.BOLD, width=24),
            status_icon,
            ft.Text(
                f"{item.display_name} / {item.model}",
                size=13,
                weight=ft.FontWeight.W_500,
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    right_section = ft.Row(
        [btn_up, btn_down, btn_edit, btn_delete],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [left_section, ft.Container(expand=True), right_section],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row([status_text], spacing=4),
            ],
            spacing=2,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border.all(1, ft.Colors.OUTLINE),
        border_radius=6,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
    )


@ft.component
def FailoverConfigPanel(
    vm: FailoverConfigPanelViewModel,
    *,
    show_save_button: bool = True,
) -> ft.Control:
    """Failover configuration panel (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（AIBrainTab 直接 new FailoverConfigPanelViewModel）
    - View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - Dialog 通过内嵌 ProviderCredentialDialog(vm=vm) 条件渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        vm: 由消费方实例化的 FailoverConfigPanelViewModel
        show_save_button: 是否显示保存按钮（default: True）
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Build list items (driven by state.failover_items) ---
    if not state.failover_items:
        list_controls: list[ft.Control] = [
            ft.Container(
                content=ft.Text(
                    I18n.get("failover_empty_hint"),
                    size=12,
                    color=AppColors.TEXT_HINT,
                    italic=True,
                ),
                padding=20,
                alignment=ft.Alignment.CENTER,
            )
        ]
    else:
        total = len(state.failover_items)
        list_controls = [_build_list_item(i, item, total, vm) for i, item in enumerate(state.failover_items)]

    # --- Panel status display ---
    status_text = _render_message(state.status_message)
    status_color = _STATUS_COLOR_MAP.get(state.status_type, AppColors.PRIMARY)
    status_icon_name = _STATUS_ICON_MAP.get(state.status_type, ft.Icons.INFO)

    status_row = (
        ft.Row(
            [
                ft.Icon(status_icon_name, visible=status_text != "", size=16, color=status_color),
                ft.Text(status_text, size=12, color=status_color),
            ],
            spacing=5,
        )
        if status_text
        else ft.Container()
    )

    # --- Buttons ---
    btn_add = ft.OutlinedButton(
        content=I18n.get("failover_add_provider"),
        icon=ft.Icons.ADD,
        on_click=safe_on_click(_run_task_no_args(vm.open_add_dialog)),
        style=AppStyles.secondary_button(),
        height=36,
    )

    btn_validate = ft.OutlinedButton(
        content=I18n.get("failover_validate_all"),
        icon=ft.Icons.VERIFIED_USER,
        on_click=safe_on_click(_run_task_no_args(vm.validate_all)),
        style=AppStyles.secondary_button(),
        height=36,
    )

    btn_save = ft.Button(
        content=I18n.get("settings_save_ai"),
        icon=ft.Icons.SAVE,
        on_click=lambda _e: vm.save_config(),
        style=AppStyles.primary_button(),
        height=36,
        visible=show_save_button,
    )

    # --- Build UI layout ---
    return ft.Column(
        [
            ft.Row(
                [
                    SectionHeader(I18n.get("failover_title")),
                    ft.Icon(ft.Icons.BOLT, size=20, color=AppColors.PRIMARY),
                    ft.Container(expand=True),
                    btn_add,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Text(
                I18n.get("failover_hint"),
                size=11,
                color=AppColors.TEXT_HINT,
            ),
            ft.Container(height=8),
            ft.Column(list_controls, spacing=8),
            ft.Container(height=8),
            ft.Row(
                [btn_validate, ft.Container(expand=True), btn_save],
                alignment=ft.MainAxisAlignment.START,
            ),
            status_row,
            # 内嵌 Dialog 组件（条件渲染，dialog_open=False 时仅渲染不可见宿主容器）
            ProviderCredentialDialog(vm=vm),
        ],
        spacing=4,
    )
