"""
Database Configuration Tab for Settings View

Uses the shared DatabaseConfigPanel component.
"""

import logging

import flet as ft

from ui.components.config_panels import DatabaseConfigPanel
from ui.i18n import I18n
from ui.theme import AppColors
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel

logger = logging.getLogger(__name__)


class DatabaseTab(ft.Container):
    """Database configuration tab for settings page."""

    def __init__(self, show_snack_callback):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.show_snack = show_snack_callback  # pragma: no cover
        self.expand = True  # pragma: no cover
        self._locale_subscription_id: object | None = None  # pragma: no cover

        # NOTE(lazy): VM 由消费方实例化（声明式 DatabaseConfigPanel 接收 vm 参数，经 use_viewmodel(vm=vm) 消费）。
        # ceiling: Phase 3.3 DatabaseTab 声明式重写. upgrade: Task 3.3.3 DatabaseTab 声明式重写.
        self.config_vm = DatabaseConfigPanelViewModel(  # pragma: no cover
            on_save_callback=self._on_save,  # pragma: no cover
            on_test_success_callback=self._on_test_success,  # pragma: no cover
            load_password=True,  # pragma: no cover
        )  # pragma: no cover

        self.title_text = ft.Text(  # pragma: no cover
            I18n.get("settings_db_title"),  # pragma: no cover
            size=24,  # pragma: no cover
            weight=ft.FontWeight.W_500,  # pragma: no cover
            color=AppColors.TEXT_PRIMARY,  # pragma: no cover
        )  # pragma: no cover

        self.content = self._build_ui()  # pragma: no cover
        self.did_mount = self._on_mount  # pragma: no cover
        self.will_unmount = self._on_unmount  # pragma: no cover

    def _build_ui(self):  # pragma: no cover
        """Build the UI layout."""  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        ft.Icon(ft.Icons.STORAGE, size=32, color=AppColors.PRIMARY),  # pragma: no cover
                        self.title_text,  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                ft.Card(  # pragma: no cover
                    content=ft.Container(  # pragma: no cover
                        content=DatabaseConfigPanel(  # pragma: no cover
                            vm=self.config_vm,  # pragma: no cover
                            show_header=True,  # pragma: no cover
                            compact=False,  # pragma: no cover
                            show_save_button=True,  # pragma: no cover
                        ),  # pragma: no cover
                        padding=20,  # pragma: no cover
                        bgcolor=AppColors.SURFACE,  # pragma: no cover
                        border_radius=12,  # pragma: no cover
                    ),  # pragma: no cover
                    elevation=2,  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            scroll=ft.ScrollMode.AUTO,  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

    def _on_save(self, config: dict):
        """Handle successful save."""
        self.show_snack(I18n.get("settings_db_saved"), "success")

    def _on_test_success(self, config: dict):
        """Handle successful connection test."""
        logger.debug(
            "Database connection test successful: %s:%s/%s", config["host"], config["port"], config["database"]
        )

    def _on_mount(self):
        self.config_vm.reload_config()
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def _on_unmount(self):
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
        self.config_vm.dispose()

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作）。

        注：DatabaseConfigPanel 已通过 ft.use_state(I18n.get_observable_state) 自动重渲染。
        """
        try:
            self.title_text.value = I18n.get("settings_db_title")
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[DatabaseTab] refresh_locale error: %s", e, exc_info=True)

    def handle_resize(self, width: float = 0, height: float = 0) -> None:
        """窗口 resize 通知。当前布局自适应，无需响应式调整。"""
        # No responsive adjustment needed
