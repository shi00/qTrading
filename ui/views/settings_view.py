import logging

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors
from ui.views.settings_tabs.ai_brain_tab import AIBrainTab
from ui.views.settings_tabs.automation_tab import AutomationTab, NotificationsTab
from ui.views.settings_tabs.data_source_tab import DataSourceTab
from ui.views.settings_tabs.system_tab import SystemTab

logger = logging.getLogger(__name__)


class SettingsView(ft.Container):
    # Tab configuration: (i18n_key, icon)
    TAB_CONFIG = [
        ("settings_tab_data", ft.Icons.STORAGE),
        ("settings_tab_ai", ft.Icons.SMART_TOY),
        ("settings_tab_tasks", ft.Icons.SCHEDULE),
        ("settings_tab_notify", ft.Icons.NOTIFICATIONS),
        ("settings_tab_system", ft.Icons.TUNE),
    ]

    def __init__(self):
        super().__init__()
        self.expand = True

        # 1. Header
        self.header_title = ft.Text(
            I18n.get("settings_title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )

        # 2. Init Tabs (order must match TAB_CONFIG)
        self.tab_contents = [
            DataSourceTab(self.show_snack),
            AIBrainTab(self.show_snack),
            AutomationTab(self.show_snack),
            NotificationsTab(self.show_snack, self),  # Pass self as page_ref holder
            SystemTab(self.show_snack),
        ]
        assert len(self.TAB_CONFIG) == len(self.tab_contents), (
            f"TAB_CONFIG ({len(self.TAB_CONFIG)}) and tab_contents ({len(self.tab_contents)}) length mismatch!"
        )

        self.tab_buttons = []
        self.current_tab_index = 0

        # 3. Build Tab Bar from config
        tab_bar = ft.Container(
            content=ft.Row(
                [
                    self._build_tab_button(I18n.get(key), icon, i)
                    for i, (key, icon) in enumerate(self.TAB_CONFIG)
                ],
                alignment=ft.MainAxisAlignment.START,
                spacing=10,
                scroll=ft.ScrollMode.HIDDEN,
            ),
            padding=ft.padding.only(bottom=10),
        )

        # 4. Tab Body
        self.tab_body = ft.Container(content=self.tab_contents[0], expand=True)

        # 5. Main Layout
        self.content = ft.Column(
            [
                self.header_title,
                tab_bar,
                ft.Divider(
                    height=1,
                    thickness=1,
                ),  # Color defaults to DIVIDER (Outline Variant) which is correct
                self.tab_body,
            ],
            expand=True,
        )

        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    @property
    def page_ref(self):
        # Helper for NotificationsTab to access page
        return self.page

    def _on_mount(self):
        I18n.subscribe(self.refresh_locale)

    def _on_unmount(self):
        I18n.unsubscribe(self.refresh_locale)
        # Cascade cleanup to child tabs
        for tab in self.tab_contents:
            if hasattr(tab, "_on_unmount"):
                try:
                    tab._on_unmount()
                except Exception as e:
                    logger.warning(f"Tab {type(tab).__name__} cleanup error: {e}")

    def refresh_locale(self):
        self.header_title.value = I18n.get("settings_title")
        # Update tab button labels from config
        for i, (key, _) in enumerate(self.TAB_CONFIG):
            if i < len(self.tab_buttons):
                self.tab_buttons[i].text = I18n.get(key)
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.debug(f"[SettingsView] Locale refresh update skipped: {e}")

    def _get_tab_button_style(self, is_selected: bool) -> ft.ButtonStyle:
        """Centralized tab button style factory."""
        return ft.ButtonStyle(
            color=AppColors.TEXT_ON_PRIMARY
            if is_selected
            else AppColors.TEXT_SECONDARY,
            icon_color=AppColors.TEXT_ON_PRIMARY
            if is_selected
            else AppColors.TEXT_SECONDARY,
            bgcolor=AppColors.PRIMARY if is_selected else ft.Colors.TRANSPARENT,
            elevation=0,
            shape=ft.RoundedRectangleBorder(radius=8),
            alignment=ft.alignment.center,
        )

    def _build_tab_button(self, text, icon, index):
        btn = ft.ElevatedButton(
            text=text,
            icon=icon,
            data=str(index),
            on_click=self._on_tab_click,
            style=self._get_tab_button_style(is_selected=(index == 0)),
        )
        self.tab_buttons.append(btn)
        return btn

    def _on_tab_click(self, e):
        try:
            idx = int(e.control.data)
        except (ValueError, TypeError):
            logger.warning(f"Invalid tab index data: {e.control.data}")
            return

        if not (0 <= idx < len(self.tab_contents)):
            logger.warning(f"Tab index out of range: {idx}")
            return

        logger.debug(f"[SettingsView] Switching to tab index: {idx}")
        self.current_tab_index = idx
        self.tab_body.content = self.tab_contents[idx]

        for i, btn in enumerate(self.tab_buttons):
            btn.style = self._get_tab_button_style(is_selected=(i == idx))
        self._safe_update()

    def show_snack(self, message, color=None, **kwargs):
        """Centralized snackbar handler"""
        if not self.page:
            return

        if hasattr(self.page, "show_toast"):
            msg_type = "info"
            if color == ft.Colors.RED:
                msg_type = "error"
            elif color == ft.Colors.GREEN:
                msg_type = "success"
            elif color == ft.Colors.ORANGE or color == ft.Colors.AMBER:
                msg_type = "warning"
            self.page.show_toast(message, type=msg_type)  # type: ignore
        else:
            # Clean up old snackbars to prevent overlay bloat
            self.page.overlay = [  # type: ignore
                o for o in self.page.overlay if not isinstance(o, ft.SnackBar)
            ]
            snack = ft.SnackBar(
                content=ft.Text(message),
                open=True,
                bgcolor=color,
                **kwargs,
            )
            self.page.overlay.append(snack)
            self.page.update()

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.error(f"[SettingsView] Update failed: {e}")

    def update_theme(self):
        """Propagate custom color updates to child tabs (INPUT_*, UP/DOWN)."""
        for tab in self.tab_contents:
            if hasattr(tab, "update_theme"):
                try:
                    tab.update_theme()
                except Exception as e:
                    logger.warning(
                        f"Failed to update theme for tab {type(tab).__name__}: {e}",
                    )

        self._safe_update()
