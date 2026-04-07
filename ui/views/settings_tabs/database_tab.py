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

    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self.expand = True

        self.config_panel = DatabaseConfigPanel(
            on_save_callback=self._on_save,
            on_test_success_callback=self._on_test_success,
            show_header=True,
            compact=False,
            load_password=True,
        )

        self.content = self._build_ui()
        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    def _build_ui(self):
        """Build the UI layout."""
        return ft.Column(
            [
                ft.Container(height=20),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.STORAGE, size=32, color=AppColors.PRIMARY),
                        ft.Text(
                            I18n.get("settings_db_title")
                            if I18n.get("settings_db_title")
                            else "Database Configuration",
                            size=24,
                            weight=ft.FontWeight.W_500,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                    ],
                ),
                ft.Container(height=20),
                ft.Card(
                    content=ft.Container(
                        content=self.config_panel,
                        padding=20,
                        bgcolor=AppColors.SURFACE,
                        border_radius=12,
                    ),
                    elevation=2,
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _on_save(self, config: dict):
        """Handle successful save."""
        self.show_snack("Database configuration saved", "success")

    def _on_test_success(self, config: dict):
        """Handle successful connection test."""
        logger.debug(
            f"Database connection test successful: "
            f"{config['host']}:{config['port']}/{config['database']}"
        )

    def _on_mount(self):
        self.config_panel.reload_config()

    def _on_unmount(self):
        pass
