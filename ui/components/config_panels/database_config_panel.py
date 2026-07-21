"""DatabaseConfigPanel — 声明式组件 (Phase 3.2.1).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 ``class DatabaseConfigPanel(ft.Container)`` → ``@ft.component def DatabaseConfigPanel(vm, ...)``
- VM 由消费方实例化（OnboardingWizard 需要 ``vm.save_config`` 引用）
- View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- page 访问改用 ``ft.context.page``（try/except 守卫 RuntimeError）
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel

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
    "info": AppColors.TEXT_SECONDARY,
}


def _render_message(msg: Message | None) -> str:
    """Render a Message to localized text via I18n.get."""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


def _on_test_click_factory(vm: DatabaseConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for test button — submits vm.test_connection via page.run_task."""

    def _on_test_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.test_connection)
        except RuntimeError:
            logger.debug("[DatabaseConfigPanel] page not available for test_connection")

    return _on_test_click


def _on_save_click_factory(vm: DatabaseConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for save button — submits vm.save_config via page.run_task."""

    def _on_save_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.save_config)
        except RuntimeError:
            logger.debug("[DatabaseConfigPanel] page not available for save_config")

    return _on_save_click


@ft.component
def DatabaseConfigPanel(
    vm: DatabaseConfigPanelViewModel,
    *,
    show_header: bool = True,
    compact: bool = False,
    show_save_button: bool = True,
) -> ft.Container:
    """Database configuration panel (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（DatabaseTab/OnboardingWizard 直接 new DatabaseConfigPanelViewModel）
    - View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        vm: 由消费方实例化的 DatabaseConfigPanelViewModel
        show_header: 是否显示 section headers（default: True）
        compact: 保留参数兼容消费方调用，不影响布局（原命令式实现亦未使用）
        show_save_button: 是否显示保存按钮（default: True）
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Build form controls (driven by state) ---
    input_width = 280
    port_width = 90
    db_name_width = 380
    user_pass_width = 185

    db_host_input = ft.TextField(
        label=I18n.get("db_host"),
        width=input_width,
        border_color=AppColors.PRIMARY,
        label_style=ft.TextStyle(color=AppColors.PRIMARY),
        hint_text="localhost",
        value=state.host,
        on_change=lambda e: vm.update_host(e.control.value),
    )
    db_port_input = ft.TextField(
        label=I18n.get("db_port"),
        width=port_width,
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=AppColors.PRIMARY,
        label_style=ft.TextStyle(color=AppColors.PRIMARY),
        hint_text="5432",
        value=state.port,
        on_change=lambda e: vm.update_port(e.control.value),
    )
    db_user_input = ft.TextField(
        label=I18n.get("db_user"),
        width=user_pass_width,
        border_color=AppColors.PRIMARY,
        label_style=ft.TextStyle(color=AppColors.PRIMARY),
        hint_text="postgres",
        value=state.user,
        on_change=lambda e: vm.update_user(e.control.value),
    )
    db_password_input = ft.TextField(
        label=I18n.get("db_password"),
        password=True,
        can_reveal_password=True,
        width=user_pass_width,
        border_color=AppColors.PRIMARY,
        label_style=ft.TextStyle(color=AppColors.PRIMARY),
        value=state.password,
        on_change=lambda e: vm.update_password(e.control.value),
    )
    db_name_input = ft.TextField(
        label=I18n.get("db_name"),
        width=db_name_width,
        border_color=AppColors.PRIMARY,
        label_style=ft.TextStyle(color=AppColors.PRIMARY),
        hint_text="astock",
        value=state.database,
        on_change=lambda e: vm.update_database(e.control.value),
    )
    db_create_checkbox = ft.Checkbox(
        label=I18n.get("db_create_if_not_exists"),
        value=state.create_if_not_exists,
        fill_color=AppColors.PRIMARY,
        on_change=lambda e: vm.update_create_if_not_exists(bool(e.control.value)),
    )

    # --- Status display (driven by state.status_message / status_type) ---
    status_text = _render_message(state.status_message)
    status_color = _STATUS_COLOR_MAP.get(state.status_type, AppColors.TEXT_SECONDARY)
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

    # --- DB info display (driven by state.db_info) ---
    db_info_text = _render_message(state.db_info)
    db_info_text_ctrl = ft.Text(
        db_info_text,
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_SECONDARY,
        text_align=ft.TextAlign.CENTER,
    )

    # --- Buttons ---
    btn_test = ft.Button(
        I18n.get("db_test_connection"),
        icon=ft.Icons.POWER,
        on_click=safe_on_click(_on_test_click_factory(vm)),
        style=AppStyles.secondary_button(),
        disabled=state.is_verifying,
    )
    btn_save = ft.Button(
        I18n.get("common_save"),
        icon=ft.Icons.SAVE,
        on_click=safe_on_click(_on_save_click_factory(vm)),
        style=AppStyles.primary_button(),
        visible=show_save_button,
        disabled=state.is_saving,
    )

    # --- Build UI layout ---
    children: list[ft.Control] = []

    if show_header:
        children.append(
            ft.Text(
                I18n.get("db_connection_settings"),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.W_500,
                color=AppColors.TEXT_PRIMARY,
                text_align=ft.TextAlign.CENTER,
            )
        )
        children.append(ft.Container(height=15))

    form_content = ft.Column(
        [
            ft.Row(
                [db_host_input, db_port_input],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
            ),
            ft.Container(height=12),
            ft.Row(
                [db_user_input, db_password_input],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
            ),
            ft.Container(height=12),
            ft.Row(
                [db_name_input],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(height=16),
            ft.Row(
                [db_create_checkbox],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(height=20),
            ft.Row(
                [btn_test, btn_save],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=15,
            ),
            ft.Container(height=12),
            ft.Row(
                [status_icon, status_text_ctrl],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=5,
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )

    children.append(form_content)

    if show_header:
        children.extend(
            [
                ft.Container(height=25),
                ft.Text(
                    I18n.get("db_info"),
                    size=AppStyles.FONT_SIZE_LG,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=10),
                ft.Row(
                    [db_info_text_ctrl],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ]
        )

    return ft.Container(
        content=ft.Column(
            children,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
