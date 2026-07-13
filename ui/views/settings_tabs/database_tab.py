"""DatabaseTab — 声明式组件 (Phase 3.3).

从命令式容器子类重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式容器子类 → ``@ft.component def DatabaseTab(show_snack_callback)``
- VM 通过 ``use_viewmodel(factory)`` 内部模式实例化,hook 管理 dispose
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 挂载时的 ``reload_config`` 改用 ``use_effect`` 执行
- 移除命令式生命周期回调 / 手动刷新 / 手动重渲染
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.config_panels import DatabaseConfigPanel
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors
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
    """Database configuration tab for settings page (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 通过 ``use_viewmodel(factory)`` 内部模式实例化,hook 管理 dispose
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
    - ``reload_config`` 通过 ``use_effect`` 挂载时执行(替代命令式挂载回调)
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """

    def _make_vm() -> DatabaseConfigPanelViewModel:
        def _on_save(config: dict) -> None:
            show_snack_callback(I18n.get("settings_db_saved"), "success")

        return DatabaseConfigPanelViewModel(
            on_save_callback=_on_save,
            on_test_success_callback=_on_test_success,
            load_password=True,
        )

    # --- VM: 内部模式,hook 实例化一次,卸载时 dispose ---
    _, vm = use_viewmodel(_make_vm)

    # --- Subscribe to i18n + theme changes (auto-rerender on locale/theme switch) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Mount-time reload (替代命令式挂载回调的 reload_config) ---
    ft.use_effect(vm.reload_config, dependencies=[])

    # --- Build UI ---
    title_text = ft.Text(
        I18n.get("settings_db_title"),
        size=24,
        weight=ft.FontWeight.W_500,
        color=AppColors.TEXT_PRIMARY,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Container(height=20),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.STORAGE, size=32, color=AppColors.PRIMARY),
                        title_text,
                    ],
                ),
                ft.Container(height=20),
                ft.Card(
                    content=ft.Container(
                        content=DatabaseConfigPanel(
                            vm=vm,
                            show_header=True,
                            compact=False,
                            show_save_button=True,
                        ),
                        padding=20,
                        bgcolor=AppColors.SURFACE,
                        border_radius=12,
                    ),
                    elevation=2,
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
