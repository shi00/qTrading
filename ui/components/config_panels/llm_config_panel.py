"""LLMConfigPanel — 声明式组件 (Phase 3.2.3).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 ``class LLMConfigPanel(ft.Container)`` → ``@ft.component def LLMConfigPanel(vm, ...)``
- VM 由消费方实例化（AIBrainTab/OnboardingWizard 需要 ``vm.save_config`` / ``vm.verify_connection`` 引用）
- View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(I18n.get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- page 访问改用 ``ft.context.page``（try/except 守卫 RuntimeError）
- provider/model options 由 View 从 LLM_PROVIDERS + 当前 locale 构建（tag 需 i18n）
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.settings_widgets import SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel
from utils.llm_providers import (
    AZURE_API_VERSIONS,
    AZURE_DEFAULT_API_VERSION,
    LLM_PROVIDERS,
    get_display_tag,
)

logger = logging.getLogger(__name__)

MODELS_API_COMPATIBLE = {
    "openai",
    "deepseek",
    "qwen",
    "zhipu",
    "moonshot",
    "mistral",
    "minimax",
    "custom",
}

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
    """Render a Message to localized text via I18n.get."""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


def _get_provider_name(provider: dict, provider_id: str) -> str:
    """获取供应商显示名称（locale 感知）。"""
    if I18n.current_locale() == "zh_CN":
        return provider.get("name", provider_id)
    return provider.get("name_en", provider.get("name", provider_id))


def _build_provider_options() -> list[ft.dropdown.Option]:
    """构建供应商下拉选项（分组：国内/国际/自定义）。"""
    options: list[ft.dropdown.Option] = []

    domestic = ft.dropdown.Option(I18n.get("llm_provider_domestic"))
    domestic.disabled = True
    options.append(domestic)

    for provider_id in ["deepseek", "qwen", "zhipu", "moonshot", "minimax"]:
        provider = LLM_PROVIDERS.get(provider_id)
        if provider:
            options.append(
                ft.dropdown.Option(
                    key=provider_id,
                    text=_get_provider_name(provider, provider_id),
                )
            )

    international = ft.dropdown.Option(I18n.get("llm_provider_international"))
    international.disabled = True
    options.append(international)

    for provider_id in ["openai", "azure", "anthropic", "google", "mistral"]:
        provider = LLM_PROVIDERS.get(provider_id)
        if provider:
            options.append(
                ft.dropdown.Option(
                    key=provider_id,
                    text=_get_provider_name(provider, provider_id),
                )
            )

    custom = ft.dropdown.Option(I18n.get("llm_provider_custom_group"))
    custom.disabled = True
    options.append(custom)

    options.append(
        ft.dropdown.Option(
            key="custom",
            text=I18n.get("llm_provider_custom"),
        )
    )

    return options


def _build_model_options(provider_id: str) -> list[ft.dropdown.Option]:
    """构建指定供应商的模型下拉选项（tag 需 i18n）。"""
    provider = LLM_PROVIDERS.get(provider_id, {})
    models = provider.get("models", [])

    options: list[ft.dropdown.Option] = []
    for model in models:
        text = model.get("name", model.get("id", ""))
        tag = model.get("tag", "")
        display_tag = I18n.get(get_display_tag(tag), default=get_display_tag(tag))
        if display_tag:
            text = f"{text} ({display_tag})"
        options.append(
            ft.dropdown.Option(
                key=model.get("id"),
                text=text,
            )
        )

    return options


def _build_links_row(provider_id: str, compact: bool) -> ft.Row:
    """构建供应商相关链接行（console_url / pricing_url / models_url）。"""
    provider = LLM_PROVIDERS.get(provider_id, {})

    links: list[ft.Control] = []
    compact_btn_style = ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=4)) if compact else None

    console_url = provider.get("console_url")
    if console_url:
        links.append(
            ft.TextButton(
                content=I18n.get("llm_get_api_key"),
                url=console_url,
                icon=ft.Icons.KEY if ft.Icons else None,
                style=compact_btn_style,
            )
        )

    pricing_url = provider.get("pricing_url")
    if pricing_url:
        links.append(
            ft.TextButton(
                content=I18n.get("llm_view_pricing"),
                url=pricing_url,
                icon=ft.Icons.ATTACH_MONEY if ft.Icons else None,
                style=compact_btn_style,
            )
        )

    models_url = provider.get("models_url")
    if models_url:
        links.append(
            ft.TextButton(
                content=I18n.get("llm_view_models"),
                url=models_url,
                icon=ft.Icons.LIST if ft.Icons else None,
                style=compact_btn_style,
            )
        )

    return ft.Row(
        controls=links,
        alignment=ft.MainAxisAlignment.CENTER if compact else ft.MainAxisAlignment.START,
        wrap=not compact,
        spacing=8 if compact else 10,
    )


def _on_test_click_factory(vm: LLMConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for test button — submits vm.verify_connection via page.run_task.

    verify_connection 复用 on_test_connection 回调，同时更新 VM 状态（is_verifying/status）。
    """

    def _on_test_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.verify_connection)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for verify_connection")

    return _on_test_click


def _on_save_click_factory(vm: LLMConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for save button — submits vm.save_config via page.run_task."""

    def _on_save_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.save_config)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for save_config")

    return _on_save_click


def _on_refresh_click_factory(vm: LLMConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for refresh button — submits vm.refresh_models via page.run_task."""

    def _on_refresh_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.refresh_models)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for refresh_models")

    return _on_refresh_click


def _on_provider_change_factory(vm: LLMConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_select handler for provider dropdown — submits vm.update_provider via page.run_task."""

    def _on_provider_change(e: ft.ControlEvent) -> None:
        provider_id = e.control.value
        if not provider_id:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.update_provider, provider_id)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for update_provider")

    return _on_provider_change


@ft.component
def LLMConfigPanel(
    vm: LLMConfigPanelViewModel,
    *,
    show_save_button: bool = True,
    compact: bool = False,
    show_register_link: bool = True,
) -> ft.Control:
    """LLM Configuration panel (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（AIBrainTab/OnboardingWizard 直接 new LLMConfigPanelViewModel）
    - View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(I18n.get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        vm: 由消费方实例化的 LLMConfigPanelViewModel
        show_save_button: 是否显示保存按钮（default: True）
        compact: 是否使用紧凑布局（default: False）
        show_register_link: 是否显示注册链接（default: True）
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(I18n.get_observable_state)

    # --- Build form controls (driven by state) ---
    input_width = 360

    provider_dropdown = ft.Dropdown(
        label=I18n.get("llm_select_provider"),
        options=_build_provider_options(),
        value=state.provider,
        on_select=_on_provider_change_factory(vm),
        width=input_width,
    )

    model_dropdown = ft.Dropdown(
        label=I18n.get("llm_select_model"),
        options=_build_model_options(state.provider),
        value=state.model,
        width=input_width,
        visible=not state.is_azure and not state.show_custom_model_input,
        on_select=lambda e: vm.update_model(e.control.value) if e.control.value else None,
    )

    custom_model_input = ft.Dropdown(
        label=I18n.get("llm_custom_model"),
        value=state.custom_model,
        visible=state.show_custom_model_input and not state.is_azure,
        width=input_width,
        editable=True,
        options=[ft.dropdown.Option(m) for m in state.custom_model_options],
        on_select=lambda e: vm.update_custom_model(e.control.value) if e.control.value else None,
    )

    base_url_input = ft.TextField(
        label=I18n.get("llm_base_url"),
        value=state.base_url,
        width=input_width,
        visible=not state.is_azure,
        read_only=state.base_url_read_only,
        on_change=lambda e: vm.update_base_url(e.control.value),
    )

    api_key_input = ft.TextField(
        label=I18n.get("llm_api_key"),
        password=True,
        can_reveal_password=True,
        value=state.api_key,
        width=input_width,
        on_change=lambda e: vm.update_api_key(e.control.value),
    )

    azure_resource_input = ft.TextField(
        label=I18n.get("llm_azure_resource_name"),
        value=state.azure_resource_name,
        visible=state.is_azure,
        width=input_width,
        on_change=lambda e: vm.update_azure_resource(e.control.value),
    )

    azure_deployment_input = ft.TextField(
        label=I18n.get("llm_azure_deployment_name"),
        value=state.azure_deployment_name,
        visible=state.is_azure,
        width=input_width,
        on_change=lambda e: vm.update_azure_deployment(e.control.value),
    )

    azure_version_input = ft.Dropdown(
        label=I18n.get("llm_azure_api_version"),
        options=[ft.dropdown.Option(v) for v in AZURE_API_VERSIONS],
        value=state.azure_api_version or AZURE_DEFAULT_API_VERSION,
        visible=state.is_azure,
        width=input_width,
        on_select=lambda e: vm.update_azure_version(e.control.value) if e.control.value else None,
    )

    # --- Status display (driven by state.status_message / status_type) ---
    status_text = _render_message(state.status_message)
    status_color = _STATUS_COLOR_MAP.get(state.status_type, AppColors.PRIMARY)
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

    # --- Buttons ---
    test_button = ft.Button(
        content=I18n.get("llm_test_connection"),
        on_click=_on_test_click_factory(vm),
        icon=ft.Icons.CABLE if ft.Icons else None,
        style=AppStyles.secondary_button(),
        disabled=state.is_verifying,
    )

    refresh_models_button = ft.IconButton(
        icon=ft.Icons.REFRESH if ft.Icons else None,
        tooltip=I18n.get("llm_refresh_models"),
        on_click=_on_refresh_click_factory(vm),
        visible=state.show_refresh_button,
        disabled=state.is_refreshing,
    )

    save_button = ft.Button(
        content=I18n.get("settings_save_config"),
        on_click=_on_save_click_factory(vm),
        icon=ft.Icons.SAVE if ft.Icons else None,
        visible=show_save_button,
        style=AppStyles.primary_button(),
        disabled=state.is_saving,
    )

    # --- Build UI layout ---
    model_row = ft.Row(
        controls=[
            model_dropdown,
            custom_model_input,
            refresh_models_button,
        ],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.END,
    )

    azure_row = ft.Column(
        controls=[
            azure_resource_input,
            azure_deployment_input,
            azure_version_input,
        ],
        visible=state.is_azure,
        horizontal_alignment=ft.CrossAxisAlignment.START,
    )

    action_buttons = ft.Row(
        controls=[
            test_button,
            save_button,
        ],
        alignment=ft.MainAxisAlignment.CENTER,
    )

    links_row = _build_links_row(state.provider, compact)
    links_row.visible = show_register_link

    section_header = SectionHeader(I18n.get("settings_sec_ai"), title_key="settings_sec_ai")
    section_header.visible = not compact

    form_content = ft.Column(
        controls=[
            section_header,
            ft.Row(
                [provider_dropdown],
                alignment=ft.MainAxisAlignment.START,
            ),
            model_row,
            base_url_input,
            api_key_input,
            azure_row,
            action_buttons,
            ft.Row(
                [status_icon, status_text_ctrl],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=5,
            ),
            links_row,
        ],
        spacing=10 if not compact else 6,
        horizontal_alignment=ft.CrossAxisAlignment.START,
    )

    if compact:
        container_width = input_width + 60
        return ft.Container(
            content=form_content,
            width=container_width,
            alignment=ft.Alignment.CENTER,
        )

    return form_content
