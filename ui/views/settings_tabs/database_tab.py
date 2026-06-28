"""
Database Configuration Tab for Settings View

Uses the shared DatabaseConfigPanel component.
"""

import logging

import flet as ft

from ui.components.config_panels import DatabaseConfigPanel
from ui.i18n import I18n
from ui.theme import AppColors

logger = logging.getLogger(__name__)


class DatabaseTab(ft.Container):
    """Database configuration tab for settings page."""

    def __init__(self, show_snack_callback):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.show_snack = show_snack_callback  # pragma: no cover
        self.expand = True  # pragma: no cover
        self._locale_subscription_id: object | None = None  # pragma: no cover

        self.config_panel = DatabaseConfigPanel(  # pragma: no cover
            on_save_callback=self._on_save,  # pragma: no cover
            on_test_success_callback=self._on_test_success,  # pragma: no cover
            show_header=True,  # pragma: no cover
            compact=False,  # pragma: no cover
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
                        content=self.config_panel,  # pragma: no cover
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
        logger.debug("Database connection test successful: %s:%s/%s", config['host'], config['port'], config['database'])

    def _on_mount(self):
        self.config_panel.reload_config()
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def _on_unmount(self):
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作）。

        注：DatabaseConfigPanel 已自行订阅 I18n，由 I18n.set_locale 自动触发。
        """
        try:
            self.title_text.value = I18n.get("settings_db_title")
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[DatabaseTab] refresh_locale error: %s", e, exc_info=True)
