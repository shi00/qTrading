"""LLMConfigPanel — 声明式组件 (Phase 3.2.3).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 ``class LLMConfigPanel(ft.Container)`` → ``@ft.component def LLMConfigPanel(vm, ...)``
- VM 由消费方实例化（AIBrainTab/OnboardingWizard 需要 ``vm.save_config`` / ``vm.verify_connection`` 引用）
- View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- page 访问改用 ``ft.context.page``（try/except 守卫 RuntimeError）
- provider/model options 由 View 从 LLM_PROVIDERS + 当前 locale 构建（tag 需 i18n）
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.model_picker import ModelPicker
from ui.components.settings_widgets import SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel
from ui.viewmodels.model_picker_view_model import ModelPickerViewModel
from utils.llm_providers import (
    AZURE_API_VERSIONS,
    AZURE_DEFAULT_API_VERSION,
    LLM_PROVIDERS,
)

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

    D8: 翻译 ``*_key`` 后缀 params 为当前 locale (R.2.3) — VM 只产出 i18n key
    (如 ``provider_key``), View 渲染时翻译为显示名并替换回同名去后缀字段,
    与 screener_view._render_status_message 行为一致.
    """
    if msg is None:
        return ""
    params = dict(msg.params)
    for k in list(params):
        if k.endswith("_key") and isinstance(params[k], str):
            params[k[:-4]] = I18n.get(params[k])
            del params[k]
    return I18n.get(msg.key, **params)


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
                    # D8: 显示名走 i18n key（llm_provider_{id}），View 按当前 locale 渲染
                    text=I18n.get(f"llm_provider_{provider_id}"),
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
                    # D8: 显示名走 i18n key（llm_provider_{id}），View 按当前 locale 渲染
                    text=I18n.get(f"llm_provider_{provider_id}"),
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


def _on_provider_change_factory(vm: LLMConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_select handler for provider dropdown — submits vm.update_provider via page.run_task."""

    def _on_provider_change(e: ft.ControlEvent) -> None:
        provider_id = get_control_value(e.control, ft.Dropdown)
        if not provider_id:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.update_provider, provider_id)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for update_provider")

    return _on_provider_change


def _on_acknowledgment_change_factory(vm: LLMConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Task 2.2: Create on_change handler for AI external acknowledgment checkbox.

    Submits vm.update_ai_external_acknowledged via page.run_task (R16: persists via ThreadPoolManager).
    """

    def _on_acknowledgment_change(e: ft.ControlEvent) -> None:
        checked = bool(e.control.value) if e.control else False
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.update_ai_external_acknowledged, checked)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for update_ai_external_acknowledged")

    return _on_acknowledgment_change


@ft.component
def LLMConfigPanel(
    vm: LLMConfigPanelViewModel,
    *,
    show_save_button: bool = True,
    compact: bool = False,
    enable_enter_submit: bool = True,
) -> ft.Control:
    """LLM Configuration panel (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（AIBrainTab/OnboardingWizard 直接 new LLMConfigPanelViewModel）
    - View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    §3.1c：模型选择经 ModelPicker（litellm 目录搜索/浏览/回记），selected 回写 vm.model。
    移除注册/定价/模型链接行与 refresh 按钮（目录即权威源，见 §3.1c-review #4）。

    Args:
        vm: 由消费方实例化的 LLMConfigPanelViewModel
        show_save_button: 是否显示保存按钮（default: True）
        compact: 是否使用紧凑布局（default: False）
        enable_enter_submit: 单行主表单 Enter 提交开关（UX-09 P2-04；default: True；
            设置页默认开启，wizard 传 False 避免 Enter 触发网络验证卡流程）
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Build form controls (driven by state) ---
    input_width = 360

    provider_dropdown = ft.Dropdown(
        label=I18n.get("llm_select_provider"),
        options=_build_provider_options(),
        value=state.provider,
        on_select=safe_on_select(_on_provider_change_factory(vm)),
        width=input_width,
    )

    # ModelPicker 内部 VM（内部模式，卸载自动 dispose）。选中模型 → 回写 vm.model。
    _pick_state, picker_vm = use_viewmodel(factory=ModelPickerViewModel)

    def _on_model_selected(provider_id: str, model_id: str) -> None:
        if provider_id != state.provider:
            # 跨供应商命中：先切 provider（异步重置 model 等派生状态），完成后再写回
            # model。与 ProviderCredentialDialog 的 dialog 处理同构（§3.1c-review #5），
            # 避免保存时 provider/model 错配（如 openai 前缀发往 kimi 端点）。
            async def _switch_and_set() -> None:
                await vm.update_provider(provider_id)
                vm.update_model(model_id)

            try:
                page = ft.context.page
                if page is not None:
                    page.run_task(_switch_and_set)
            except RuntimeError:
                logger.debug("[LLMConfigPanel] page not available for provider switch on model select")
        else:
            vm.update_model(model_id)

    # provider/model 变化 → 同步 ModelPicker 已选态（回显 + 判定 custom 历史）
    def _sync_picker_selection() -> None:
        picker_vm.set_selection(state.provider, state.model or "")

    ft.use_effect(_sync_picker_selection, dependencies=[state.provider, state.model])

    model_picker = ft.Column(
        [
            ModelPicker(
                picker_vm,
                on_select=_on_model_selected,
                text_field_width=input_width,
            ),
        ],
        visible=not state.is_azure and not state.show_custom_model_input,
    )

    custom_model_input = ft.Dropdown(
        label=I18n.get("llm_custom_model"),
        value=state.custom_model,
        visible=state.show_custom_model_input and not state.is_azure,
        width=input_width,
        editable=True,
        options=[ft.dropdown.Option(m) for m in state.custom_model_options],
        on_select=lambda e: vm.update_custom_model(e.control.value) if e.control.value else None,
        on_text_change=lambda e: vm.update_custom_model(e.control.value) if e.control.value else None,
    )

    base_url_input = ft.TextField(
        label=I18n.get("llm_base_url"),
        value=state.base_url,
        width=input_width,
        visible=not state.is_azure,
        read_only=state.base_url_read_only,
        on_change=lambda e: vm.update_base_url(e.control.value),
        # UX-09 (P2-04): Enter 提交 = 测试连接主动作 (wizard 经 enable_enter_submit=False 关闭)
        on_submit=safe_on_change(_on_test_click_factory(vm)) if enable_enter_submit else None,
    )

    api_key_input = ft.TextField(
        label=I18n.get("llm_api_key"),
        password=True,
        can_reveal_password=True,
        value=state.api_key,
        width=input_width,
        on_change=lambda e: vm.update_api_key(e.control.value),
        on_submit=safe_on_change(_on_test_click_factory(vm)) if enable_enter_submit else None,
    )

    azure_resource_input = ft.TextField(
        label=I18n.get("llm_azure_resource_name"),
        value=state.azure_resource_name,
        visible=state.is_azure,
        width=input_width,
        on_change=lambda e: vm.update_azure_resource(e.control.value),
        on_submit=safe_on_change(_on_test_click_factory(vm)) if enable_enter_submit else None,
    )

    azure_deployment_input = ft.TextField(
        label=I18n.get("llm_azure_deployment_name"),
        value=state.azure_deployment_name,
        visible=state.is_azure,
        width=input_width,
        on_change=lambda e: vm.update_azure_deployment(e.control.value),
        on_submit=safe_on_change(_on_test_click_factory(vm)) if enable_enter_submit else None,
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
        size=AppStyles.FONT_SIZE_TITLE,
        color=status_color,
    )
    status_text_ctrl = ft.Text(
        status_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=status_color,
    )

    # --- Buttons ---
    test_button = ft.Button(
        content=I18n.get("llm_test_connection"),
        on_click=safe_on_click(_on_test_click_factory(vm)),
        icon=ft.Icons.CABLE if ft.Icons else None,
        style=AppStyles.secondary_button(),
        disabled=state.is_verifying,
    )

    save_button = ft.Button(
        content=I18n.get("settings_save_config"),
        on_click=safe_on_click(_on_save_click_factory(vm)),
        icon=ft.Icons.SAVE if ft.Icons else None,
        visible=show_save_button,
        style=AppStyles.primary_button(),
        disabled=state.is_saving,
    )

    # --- Build UI layout ---
    model_row = ft.Column(
        controls=[
            model_picker,
            custom_model_input,
        ],
        spacing=6,
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

    # SectionHeader 是 @ft.component，返回的 Component 为 frozen，不能直接改 .visible，
    # 因此在 compact 模式下通过条件渲染（不加入 form_content）实现隐藏。
    section_header = SectionHeader(I18n.get("settings_sec_ai"), title_key="settings_sec_ai")

    # Task 2.2: 确认 checkbox。长 label 在窄容器内无法换行会被截断，
    # 故用无 label 的 Checkbox + 可 expand 的独立 Text 组合展示。
    def _on_acknowledgment_label_click(e: ft.ControlEvent) -> None:
        # 点击 label 等价于切换 checkbox（取反当前确认状态）
        checked = not state.ai_external_acknowledged
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.update_ai_external_acknowledged, checked)
        except RuntimeError:
            logger.debug("[LLMConfigPanel] page not available for update_ai_external_acknowledged")

    ai_acknowledgment_checkbox = ft.Checkbox(
        value=state.ai_external_acknowledged,
        on_change=safe_on_change(_on_acknowledgment_change_factory(vm)),
    )
    ai_acknowledgment_label_clickable = ft.GestureDetector(
        content=ft.Text(
            I18n.get("ai_external_acknowledgment_checkbox"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_PRIMARY,
        ),
        on_tap=safe_on_click(_on_acknowledgment_label_click),
        expand=True,
    )

    # compact 模式下隐藏区块标题：SectionHeader 为 frozen Component，无法在构建后改 visible，
    # 故通过条件渲染决定是否加入 controls。
    content_controls: list[ft.Control] = []
    if not compact:
        content_controls.append(section_header)
    content_controls.extend(
        [
            ft.Row(
                [provider_dropdown],
                alignment=ft.MainAxisAlignment.START,
            ),
            model_row,
            base_url_input,
            api_key_input,
            azure_row,
            ft.Row(
                controls=[
                    ai_acknowledgment_checkbox,
                    ai_acknowledgment_label_clickable,
                ],
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            action_buttons,
            ft.Row(
                [status_icon, status_text_ctrl],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=5,
            ),
        ]
    )

    form_content = ft.Column(
        controls=content_controls,
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
