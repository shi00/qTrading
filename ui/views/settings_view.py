import logging

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors
from ui.views.settings_tabs.ai_brain_tab import AIBrainTab
from ui.views.settings_tabs.automation_tab import AutomationTab, NotificationsTab
from ui.views.settings_tabs.data_source_tab import DataSourceTab
from ui.views.settings_tabs.database_tab import DatabaseTab
from ui.views.settings_tabs.system_tab import SystemTab

logger = logging.getLogger(__name__)


class SettingsView(ft.Container):
    # Tab configuration: (i18n_key, icon)
    TAB_CONFIG = [
        ("settings_tab_data", ft.Icons.STORAGE),
        ("settings_tab_database", ft.Icons.DNS),
        ("settings_tab_ai", ft.Icons.SMART_TOY),
        ("settings_tab_tasks", ft.Icons.SCHEDULE),
        ("settings_tab_notify", ft.Icons.NOTIFICATIONS),
        ("settings_tab_system", ft.Icons.TUNE),
    ]

    def __init__(self):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.expand = True  # pragma: no cover

        # 1. Header  # pragma: no cover
        self.header_title = ft.Text(  # pragma: no cover
            I18n.get("settings_title"),  # pragma: no cover
            size=24,  # pragma: no cover
            weight=ft.FontWeight.BOLD,  # pragma: no cover
            color=AppColors.TEXT_PRIMARY,  # pragma: no cover
        )  # pragma: no cover

        # 2. Init Tabs (order must match TAB_CONFIG)  # pragma: no cover
        self.tab_contents = [  # pragma: no cover
            DataSourceTab(self.show_snack),  # pragma: no cover
            DatabaseTab(self.show_snack),  # pragma: no cover
            AIBrainTab(self.show_snack),  # pragma: no cover
            AutomationTab(self.show_snack),  # pragma: no cover
            NotificationsTab(self.show_snack, self),  # pragma: no cover
            SystemTab(self.show_snack),  # pragma: no cover
        ]  # pragma: no cover
        assert len(self.TAB_CONFIG) == len(self.tab_contents), (  # pragma: no cover
            f"TAB_CONFIG ({len(self.TAB_CONFIG)}) and tab_contents ({len(self.tab_contents)}) length mismatch!"
        )  # pragma: no cover

        self.tab_buttons = []  # pragma: no cover
        self.current_tab_index = 0  # pragma: no cover

        # 3. Build Tab Bar from config  # pragma: no cover
        tab_bar = ft.Container(  # pragma: no cover
            content=ft.Row(  # pragma: no cover
                [
                    self._build_tab_button(I18n.get(key), icon, i) for i, (key, icon) in enumerate(self.TAB_CONFIG)
                ],  # pragma: no cover
                alignment=ft.MainAxisAlignment.START,  # pragma: no cover
                spacing=10,  # pragma: no cover
                scroll=ft.ScrollMode.HIDDEN,  # pragma: no cover
            ),  # pragma: no cover
            padding=ft.padding.only(bottom=10),  # pragma: no cover
        )  # pragma: no cover

        # 4. Tab Body  # pragma: no cover
        self.tab_body = ft.Container(content=self.tab_contents[0], expand=True)  # pragma: no cover

        # 5. Main Layout  # pragma: no cover
        self.content = ft.Column(  # pragma: no cover
            [  # pragma: no cover
                self.header_title,  # pragma: no cover
                tab_bar,  # pragma: no cover
                ft.Divider(  # pragma: no cover
                    height=1,  # pragma: no cover
                    thickness=1,  # pragma: no cover
                ),  # pragma: no cover
                self.tab_body,  # pragma: no cover
            ],  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

        self.did_mount = self._on_mount  # pragma: no cover
        self.will_unmount = self._on_unmount  # pragma: no cover

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

    def refresh_locale(self):  # pragma: no cover
        self.header_title.value = I18n.get("settings_title")  # pragma: no cover
        # Update tab button labels from config  # pragma: no cover
        for i, (key, _) in enumerate(self.TAB_CONFIG):  # pragma: no cover
            if i < len(self.tab_buttons):  # pragma: no cover
                self.tab_buttons[i].text = I18n.get(key)  # pragma: no cover
        try:  # pragma: no cover
            if self.page:  # pragma: no cover
                self.update()  # pragma: no cover
        except Exception as e:  # pragma: no cover
            logger.debug(f"[SettingsView] Locale refresh update skipped: {e}")  # pragma: no cover

    def _get_tab_button_style(self, is_selected: bool) -> ft.ButtonStyle:  # pragma: no cover
        """Centralized tab button style factory."""  # pragma: no cover
        return ft.ButtonStyle(  # pragma: no cover
            color=AppColors.TEXT_ON_PRIMARY if is_selected else AppColors.TEXT_SECONDARY,  # pragma: no cover
            icon_color=AppColors.TEXT_ON_PRIMARY if is_selected else AppColors.TEXT_SECONDARY,  # pragma: no cover
            bgcolor=AppColors.PRIMARY if is_selected else ft.Colors.TRANSPARENT,  # pragma: no cover
            elevation=0,  # pragma: no cover
            shape=ft.RoundedRectangleBorder(radius=8),  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
        )  # pragma: no cover

    def _build_tab_button(self, text, icon, index):  # pragma: no cover
        btn = ft.ElevatedButton(  # pragma: no cover
            text=text,  # pragma: no cover
            icon=icon,  # pragma: no cover
            tooltip=text,  # 为无障碍语义树提供稳定的 aria-label  # pragma: no cover
            data=str(index),  # pragma: no cover
            on_click=self._on_tab_click,  # pragma: no cover
            style=self._get_tab_button_style(is_selected=(index == 0)),  # pragma: no cover
        )  # pragma: no cover
        self.tab_buttons.append(btn)  # pragma: no cover
        return btn  # pragma: no cover

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
            self.page.show_toast(message, type=msg_type)  # type: ignore[untyped]
        else:
            # Clean up old snackbars to prevent overlay bloat
            self.page.overlay = [  # type: ignore[untyped]
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

    def _safe_update(self):  # pragma: no cover
        try:  # pragma: no cover
            if self.page:  # pragma: no cover
                self.update()  # pragma: no cover
        except Exception as e:  # pragma: no cover
            logger.error(f"[SettingsView] Update failed: {e}")  # pragma: no cover

    def update_theme(self):  # pragma: no cover
        """Propagate custom color updates to child tabs (INPUT_*, UP/DOWN)."""  # pragma: no cover
        for tab in self.tab_contents:  # pragma: no cover
            if hasattr(tab, "update_theme"):  # pragma: no cover
                try:  # pragma: no cover
                    tab.update_theme()  # pragma: no cover
                except Exception as e:  # pragma: no cover
                    logger.warning(  # pragma: no cover
                        f"Failed to update theme for tab {type(tab).__name__}: {e}",  # pragma: no cover
                    )  # pragma: no cover

        self._safe_update()  # pragma: no cover
