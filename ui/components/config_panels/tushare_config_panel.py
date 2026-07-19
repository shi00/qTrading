"""TushareConfigPanel — 声明式组件 (Phase 3.2.2).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 ``class TushareConfigPanel(ft.Container)`` → ``@ft.component def TushareConfigPanel(vm, ...)``
- VM 由消费方实例化（OnboardingWizard 需要 ``vm.verify_token`` 引用）
- View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- page 访问改用 ``ft.context.page``（try/except 守卫 RuntimeError）
"""

import logging
import webbrowser
from collections.abc import Callable

import flet as ft

from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel

logger = logging.getLogger(__name__)

_TUSHARE_REGISTER_URL = "https://tushare.pro/register?reg=728426"

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


def _build_tier_options(tier_options: tuple[str, ...]) -> list[ft.dropdown.Option]:
    """构建档位下拉选项 (P1-1: tier_options 由 VM state 产出, View 不再直接 import TUSHARE_POINT_TIERS).

    与 TierApiPanel 同源 i18n key，不抽象共享（YAGNI，5 行重复成本 < 抽象成本）。
    """
    return [ft.dropdown.Option(key=tier, text=I18n.get(f"sys_tier_{tier}_label")) for tier in tier_options]


def _on_verify_click_factory(vm: TushareConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for verify button — submits vm.verify_token via page.run_task."""

    def _on_verify_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.verify_token)
        except RuntimeError:
            logger.debug("[TushareConfigPanel] page not available for verify_token")

    return _on_verify_click


def _on_save_click_factory(vm: TushareConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for save button — calls vm.save (sync, triggers on_save callback)."""

    def _on_save_click(e: ft.ControlEvent) -> None:
        vm.save()

    return _on_save_click


def _on_tier_change_factory(vm: TushareConfigPanelViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_select handler for tier dropdown — submits vm.update_tier via page.run_task."""

    def _on_tier_change(e: ft.ControlEvent) -> None:
        new_tier = e.control.value
        if not new_tier:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.update_tier, new_tier)
        except RuntimeError:
            logger.debug("[TushareConfigPanel] page not available for update_tier")

    return _on_tier_change


def _on_register_click(e: ft.ControlEvent) -> None:
    """Open Tushare register page in browser."""
    webbrowser.open_new_tab(_TUSHARE_REGISTER_URL)


@ft.component
def TushareConfigPanel(
    vm: TushareConfigPanelViewModel,
    *,
    show_save_button: bool = True,
    compact: bool = False,
    show_register_link: bool = True,
) -> ft.Control:
    """Tushare Token configuration panel (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（DataSourceTab/OnboardingWizard 直接 new TushareConfigPanelViewModel）
    - View 通过 ``use_viewmodel(vm=vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        vm: 由消费方实例化的 TushareConfigPanelViewModel
        show_save_button: 是否显示保存按钮（default: True）
        compact: 是否使用紧凑布局（default: False）
        show_register_link: 是否显示注册链接（default: True）
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Build form controls (driven by state) ---
    token_input = ft.TextField(
        label=I18n.get("tushare_token_label"),
        password=True,
        can_reveal_password=True,
        value=state.token,
        on_change=lambda e: vm.update_token(e.control.value),
        border_color=AppColors.PRIMARY,
        label_style=ft.TextStyle(color=AppColors.PRIMARY),
    )

    if compact:
        token_input.width = AppStyles.CONTROL_WIDTH_LG
        token_input.hint_text = I18n.get("tushare_token_hint")

    tier_dropdown = ft.Dropdown(
        label=I18n.get("sys_tier_label_in_token_panel"),
        value=state.tier,
        width=AppStyles.CONTROL_WIDTH_MD,
        options=_build_tier_options(state.tier_options),
        on_select=_on_tier_change_factory(vm),
        hint_text=I18n.get("sys_tier_hint_in_token_panel"),
        disabled=state.is_verifying,
    )

    verify_button = ft.Button(
        content=I18n.get("tushare_verify"),
        icon=ft.Icons.VERIFIED_USER_OUTLINED,
        on_click=_on_verify_click_factory(vm),
        style=AppStyles.secondary_button(),
        disabled=state.is_verifying,
    )

    save_button = ft.Button(
        content=I18n.get("tushare_save"),
        icon=ft.Icons.SAVE_OUTLINED,
        on_click=_on_save_click_factory(vm),
        style=AppStyles.secondary_button(),
        visible=show_save_button,
        disabled=state.is_verifying,
    )

    # --- Status display (driven by state.status_message / status_type) ---
    status_text = _render_message(state.status_message)
    status_color = _STATUS_COLOR_MAP.get(state.status_type, AppColors.TEXT_SECONDARY)
    status_icon_name = _STATUS_ICON_MAP.get(state.status_type, ft.Icons.INFO)

    status_icon = ft.Icon(
        status_icon_name,
        visible=status_text != "",
        size=12,
        color=status_color,
    )
    status_text_ctrl = ft.Text(
        status_text,
        size=12,
        color=status_color,
    )

    register_link = ft.TextButton(
        content=I18n.get("tushare_register"),
        icon=ft.Icons.OPEN_IN_NEW,
        on_click=_on_register_click,
        style=ft.ButtonStyle(
            color=AppColors.PRIMARY,
        ),
    )

    no_token_text = ft.Text(
        I18n.get("tushare_no_token"),
        size=12,
        color=AppColors.TEXT_SECONDARY,
    )

    # --- Build UI layout ---
    if compact:
        controls: list[ft.Control] = [
            token_input,
            ft.Container(height=10),
            tier_dropdown,
            ft.Container(height=10),
            ft.Row(
                [verify_button],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(height=5),
            ft.Row(
                [status_icon, status_text_ctrl],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=5,
            ),
        ]

        if show_register_link:
            controls.extend(
                [
                    ft.Container(height=15),
                    ft.Row(
                        [
                            no_token_text,
                            register_link,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=5,
                    ),
                ]
            )

        return ft.Column(
            controls,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # standard layout
    buttons: list[ft.Control] = [verify_button]
    if show_save_button:
        buttons.append(save_button)

    return ft.Row(
        [
            ft.Column(
                [
                    ft.Row(
                        [token_input] + buttons,
                        alignment=ft.MainAxisAlignment.START,
                        spacing=10,
                        wrap=True,
                    ),
                    tier_dropdown,
                    ft.Row(
                        [status_icon, status_text_ctrl],
                        spacing=5,
                    ),
                ],
                spacing=5,
                expand=True,
            ),
        ],
        alignment=ft.MainAxisAlignment.START,
    )
