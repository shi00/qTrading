"""DatabaseTab — 声明式组件 (Phase 3 P3-13).

P3-13 改造为 3 面板默认显示 + 高级模式开关:
- 默认渲染: EmbeddedStatusCard + DatabaseStatusPanel + BackupRestorePanel
- 高级模式开关 (ft.Switch, 持久化到 AppConfig.db_show_advanced)
- 高级模式开启时追加 ExternalPgForm (DatabaseConfigPanelViewModel 外部 VM 模式)
- 底部追加"离线维护工具"说明 Card

CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
- 内部 VM 模式面板 (EmbeddedStatusCard / DatabaseStatusPanel / BackupRestorePanel)
  自身通过 use_viewmodel(factory=...) 实例化 VM, DatabaseTab 直接调用即可
- 高级模式的 ExternalPgForm 通过 use_viewmodel(factory=...) 在 DatabaseTab 顶层
  实例化 DatabaseConfigPanelViewModel (hook 顺序稳定, 不受 show_advanced 影响)
- i18n/theme 通过 ft.use_state(*.get_observable_state) 订阅自动重渲染
- 高级模式状态通过 ft.use_state + use_effect 持久化到 AppConfig
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.config_panels.backup_restore_panel import BackupRestorePanel
from ui.components.config_panels.database_status_panel import DatabaseStatusPanel
from ui.components.config_panels.embedded_status_card import EmbeddedStatusCard
from ui.components.config_panels.external_pg_form import ExternalPgForm
from ui.components.flet_type_helpers import get_control_value, safe_on_change
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel

logger = logging.getLogger(__name__)


def _on_test_success(config: dict) -> None:
    """Log successful connection test (module-level pure function)."""
    logger.debug(
        "Database connection test successful: %s:%s/%s",
        config["host"],
        config["port"],
        config["database"],
    )


@ft.component
def DatabaseTab(show_snack_callback: Callable) -> ft.Container:
    """Database configuration tab for settings page (declarative, P3-13 3-panel).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - 默认渲染 3 个内部 VM 模式面板 (各自 hook 实例化 + dispose)
    - 高级模式开启时追加 ExternalPgForm (DatabaseConfigPanelViewModel 外部 VM 模式,
      通过 use_viewmodel(factory=...) 在顶层实例化以保持 hook 顺序稳定)
    - i18n/theme 通过 ft.use_state(*.get_observable_state) 订阅自动重渲染
    - 高级模式状态通过 ft.use_state + use_effect 持久化到 AppConfig.db_show_advanced
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """

    def _make_external_vm() -> DatabaseConfigPanelViewModel:
        def _on_save(config: dict) -> None:
            show_snack_callback(I18n.get("settings_db_saved"), "success")

        return DatabaseConfigPanelViewModel(
            on_save_callback=_on_save,
            on_test_success_callback=_on_test_success,
            load_password=True,
        )

    # --- ExternalPgForm VM: 顶层实例化, hook 顺序稳定 (不受 show_advanced 影响) ---
    _, database_config_vm = use_viewmodel(_make_external_vm)

    # --- Subscribe to i18n + theme changes (auto-rerender on locale/theme switch) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 高级模式开关状态 (默认 False) ---
    show_advanced, set_show_advanced = ft.use_state(False)

    def _load_advanced_state() -> None:
        """挂载时从 AppConfig 读取高级模式开关初始值。"""
        try:
            saved = database_config_vm.load_show_advanced()
            set_show_advanced(bool(saved))
        except Exception:  # noqa: BLE001  # NOTE(lazy): 配置读取失败降级为默认 False. ceiling: 配置文件不可读. upgrade: 引入配置可读性预检.
            logger.debug("[DatabaseTab] Failed to load db_show_advanced, using default False")

    ft.use_effect(_load_advanced_state, dependencies=[])

    def _on_advanced_toggle(e: ft.ControlEvent) -> None:
        """高级模式开关切换: 更新 state + 持久化到 AppConfig。"""
        value = bool(get_control_value(e.control, ft.Switch))
        set_show_advanced(value)
        try:
            database_config_vm.save_show_advanced(value)
        except Exception:  # noqa: BLE001  # NOTE(lazy): 配置写入失败降级为内存态. ceiling: 配置文件不可写. upgrade: 引入配置可写性预检或重试.
            logger.debug("[DatabaseTab] Failed to persist db_show_advanced=%s", value)

    # --- Build UI ---
    title_text = ft.Text(
        I18n.get("settings_db_title"),
        size=AppStyles.FONT_SIZE_XL,
        weight=ft.FontWeight.W_500,
        color=AppColors.TEXT_PRIMARY,
    )

    advanced_switch = ft.Switch(
        label=I18n.get("settings_db_advanced_mode"),
        value=show_advanced,
        on_change=safe_on_change(_on_advanced_toggle),
        active_color=AppColors.PRIMARY,
    )

    # --- 组装面板列表 (默认 3 面板 + 高级模式追加 ExternalPgForm) ---
    panels: list[ft.Control] = [
        EmbeddedStatusCard(),
        ft.Container(height=12),
        DatabaseStatusPanel(),
        ft.Container(height=12),
        BackupRestorePanel(),
    ]

    if show_advanced:
        panels.append(ft.Container(height=12))
        panels.append(
            ExternalPgForm(
                vm=database_config_vm,
                show_header=True,
                compact=False,
                show_save_button=True,
            )
        )

    # --- 离线维护工具说明 Card (DoD 8) ---
    offline_maintenance_card = ft.Card(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        I18n.get("settings_db_offline_maintenance_title"),
                        size=AppStyles.FONT_SIZE_TITLE,
                        weight=ft.FontWeight.W_500,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Container(height=6),
                    ft.Text(
                        I18n.get("settings_db_offline_maintenance_desc"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
            ),
            padding=16,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
        ),
        elevation=1,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Container(height=20),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.STORAGE, size=AppStyles.ICON_SIZE_LG, color=AppColors.PRIMARY),
                        title_text,
                    ],
                ),
                ft.Container(height=12),
                advanced_switch,
                ft.Container(height=20),
                *panels,
                ft.Container(height=20),
                offline_maintenance_card,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
