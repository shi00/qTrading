"""DatabaseStatusPanel — 声明式数据库状态面板组件 (P3-10).

显示 embedded PostgreSQL 运行状态:
- running/stopped/version/port/data_dir/log_dir
- "打开数据目录" / "打开日志目录" / "刷新状态" 按钮

CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
- 内部 VM 模式 (factory=DatabaseStatusViewModel): hook 实例化 + dispose on unmount
- View 通过 use_viewmodel(factory=...) 订阅 vm.state 变化触发重渲染
- i18n 通过 ft.use_state(get_observable_state) 自动重渲染
- View 不持有业务状态 (state 全部从 VM 读取)
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.database_status_view_model import DatabaseStatusViewModel

logger = logging.getLogger(__name__)

# --- Status display config (与 embedded_status_card 共享同款 mapping) ---

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


def _on_refresh_click_factory(vm: DatabaseStatusViewModel) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for refresh button — submits vm.refresh_status via page.run_task."""

    def _on_refresh_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.refresh_status)
        except RuntimeError:
            logger.debug("[DatabaseStatusPanel] page not available for refresh_status")

    return _on_refresh_click


def _on_open_data_dir_click_factory(
    vm: DatabaseStatusViewModel,
) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for open data dir button — calls vm.open_data_dir (sync)."""

    def _on_open_data_dir_click(e: ft.ControlEvent) -> None:
        vm.open_data_dir()

    return _on_open_data_dir_click


def _on_open_log_dir_click_factory(
    vm: DatabaseStatusViewModel,
) -> Callable[[ft.ControlEvent], None]:
    """Create on_click handler for open log dir button — calls vm.open_log_dir (sync)."""

    def _on_open_log_dir_click(e: ft.ControlEvent) -> None:
        vm.open_log_dir()

    return _on_open_log_dir_click


@ft.component
def DatabaseStatusPanel() -> ft.Container:
    """Embedded PostgreSQL 数据库状态面板 (声明式)。

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - 内部 VM 模式: hook 实例化 DatabaseStatusViewModel + dispose on unmount
    - View 通过 use_viewmodel(factory=...) 订阅 vm.state 变化触发重渲染
    - i18n 通过 ft.use_state(get_observable_state) 自动重渲染
    - View 不持有业务状态, 全部从 VM state 读取

    Returns:
        ft.Container: 含状态显示 + version/port/dir 信息 + 3 个操作按钮的面板
    """
    # --- 内部 VM 模式: hook 实例化 + dispose on unmount ---
    state, vm = use_viewmodel(factory=DatabaseStatusViewModel)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

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

    # --- Info display (version / port / data_dir / log_dir) ---
    info_controls: list[ft.Control] = []

    if state.pg_version is not None:
        info_controls.append(
            ft.Text(
                I18n.get("db_status_version", version=state.pg_version),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )

    if state.port is not None:
        info_controls.append(
            ft.Text(
                I18n.get("db_status_port", port=state.port),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )

    if state.data_dir is not None:
        info_controls.append(
            ft.Text(
                I18n.get("db_status_data_dir", path=state.data_dir),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )

    if state.log_dir is not None:
        info_controls.append(
            ft.Text(
                I18n.get("db_status_log_dir", path=state.log_dir),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )

    # --- Error message display ---
    error_text = _render_message(state.error_message)
    error_text_ctrl = ft.Text(
        error_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.ERROR,
        visible=error_text != "",
    )

    # --- Action buttons ---
    refresh_button = ft.Button(
        content=I18n.get("db_status_refresh"),
        icon=ft.Icons.REFRESH,
        on_click=safe_on_click(_on_refresh_click_factory(vm)),
        style=AppStyles.secondary_button(),
        disabled=state.is_refreshing,
    )

    open_data_dir_button = ft.Button(
        content=I18n.get("db_status_open_data_dir"),
        icon=ft.Icons.FOLDER_OPEN,
        on_click=safe_on_click(_on_open_data_dir_click_factory(vm)),
        style=AppStyles.secondary_button(),
        disabled=state.data_dir is None,
    )

    open_log_dir_button = ft.Button(
        content=I18n.get("db_status_open_log_dir"),
        icon=ft.Icons.FOLDER_OPEN_OUTLINED,
        on_click=safe_on_click(_on_open_log_dir_click_factory(vm)),
        style=AppStyles.secondary_button(),
        disabled=state.log_dir is None,
    )

    # --- Title ---
    title_ctrl = ft.Text(
        I18n.get("db_status_title"),
        size=AppStyles.FONT_SIZE_HEADLINE,
        color=AppColors.TEXT_PRIMARY,
        weight=ft.FontWeight.BOLD,
    )

    # --- Build UI layout ---
    return ft.Container(
        content=ft.Column(
            [
                title_ctrl,
                ft.Container(height=8),
                ft.Row(
                    [status_icon, status_text_ctrl],
                    spacing=5,
                ),
                *info_controls,
                error_text_ctrl,
                ft.Container(height=12),
                ft.Row(
                    [refresh_button, open_data_dir_button, open_log_dir_button],
                    spacing=10,
                    wrap=True,
                ),
            ],
            spacing=5,
        ),
    )
