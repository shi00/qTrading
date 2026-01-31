import flet as ft
from ui.theme import AppColors
from ui.i18n import I18n
from ui.views.settings_tabs.data_source_tab import DataSourceTab
from ui.views.settings_tabs.ai_brain_tab import AIBrainTab
from ui.views.settings_tabs.automation_tab import AutomationTab, NotificationsTab
from ui.views.settings_tabs.system_tab import SystemTab
import logging

logger = logging.getLogger(__name__)

class SettingsView(ft.Container):
    def __init__(self):
        super().__init__()
        self.expand = True
        
        # 1. Header
        self.header_title = ft.Text(I18n.get("settings_title"), size=24, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
        
        # 2. Init Tabs
        # We pass self.show_snack as a callback to all tabs
        # For NotificationsTab, we also pass `self.page` (via property or lazily) 
        # but since page is not available in init, we might need a workaround or pass 'self' and let them access self.page
        
        self.tab_data = DataSourceTab(self.show_snack)
        self.tab_ai = AIBrainTab(self.show_snack)
        self.tab_auto = AutomationTab(self.show_snack)
        self.tab_notify = NotificationsTab(self.show_snack, self) # Pass self as page_ref holder
        self.tab_system = SystemTab(self.show_snack)
        
        self.tab_contents = [
            self.tab_data, 
            self.tab_ai, 
            self.tab_auto, 
            self.tab_notify, 
            self.tab_system
        ]
        
        self.tab_buttons = []
        self.current_tab_index = 0
        
        # 3. Build Tab Bar
        tab_bar = ft.Container(
            content=ft.Row([
                self._build_tab_button(I18n.get("settings_tab_data"), ft.Icons.STORAGE, 0),
                self._build_tab_button(I18n.get("settings_tab_ai"), ft.Icons.SMART_TOY, 1),
                self._build_tab_button(I18n.get("settings_tab_tasks"), ft.Icons.SCHEDULE, 2),
                self._build_tab_button(I18n.get("settings_tab_notify"), ft.Icons.NOTIFICATIONS, 3),
                self._build_tab_button(I18n.get("settings_tab_system"), ft.Icons.TUNE, 4),
            ], alignment=ft.MainAxisAlignment.START, spacing=10, scroll=ft.ScrollMode.HIDDEN),
            padding=ft.padding.only(bottom=10)
        )

        # 4. Tab Body
        self.tab_body = ft.Container(
            content=self.tab_contents[0],
            expand=True
        )

        # 5. Main Layout
        self.content = ft.Column([
            self.header_title,
            tab_bar,
            ft.Divider(height=1, thickness=1, color=AppColors.BORDER),
            self.tab_body
        ], expand=True)

        self.did_mount = self._on_mount

    @property
    def page_ref(self):
        # Helper for NotificationsTab to access page
        return self.page

    def _on_mount(self):
        I18n.subscribe(self.refresh_locale)

    def refresh_locale(self):
        self.header_title.value = I18n.get("settings_title")
        if len(self.tab_buttons) >= 5:
            self.tab_buttons[0].text = I18n.get("settings_tab_data")
            self.tab_buttons[1].text = I18n.get("settings_tab_ai")
            self.tab_buttons[2].text = I18n.get("settings_tab_tasks")
            self.tab_buttons[3].text = I18n.get("settings_tab_notify")
            self.tab_buttons[4].text = I18n.get("settings_tab_system")
        try:
            if self.page: self.update()
        except: pass

    def _build_tab_button(self, text, icon, index):
        btn = ft.ElevatedButton(
            text=text,
            icon=icon,
            data=str(index),
            on_click=self._on_tab_click,
            style=ft.ButtonStyle(
                color=AppColors.TEXT_ON_PRIMARY if index == 0 else AppColors.TEXT_SECONDARY,
                icon_color=AppColors.TEXT_ON_PRIMARY if index == 0 else AppColors.TEXT_SECONDARY,
                bgcolor=AppColors.PRIMARY if index == 0 else ft.Colors.TRANSPARENT,
                elevation=0,
                shape=ft.RoundedRectangleBorder(radius=8),
                alignment=ft.alignment.center,
            )
        )
        self.tab_buttons.append(btn)
        return btn

    def _on_tab_click(self, e):
        idx = int(e.control.data)
        self.current_tab_index = idx
        
        self.tab_body.content = self.tab_contents[idx]
        
        for i, btn in enumerate(self.tab_buttons):
            is_selected = (i == idx)
            btn.style = ft.ButtonStyle(
                color=AppColors.TEXT_ON_PRIMARY if is_selected else AppColors.TEXT_SECONDARY,
                icon_color=AppColors.TEXT_ON_PRIMARY if is_selected else AppColors.TEXT_SECONDARY,
                bgcolor=AppColors.PRIMARY if is_selected else ft.Colors.TRANSPARENT,
                shape=ft.RoundedRectangleBorder(radius=8),
            )
        self._safe_update()

    def show_snack(self, message, color=None, **kwargs):
        """Centralized snackbar handler"""
        if not self.page: return

        if hasattr(self.page, "show_toast"):
            msg_type = "info"
            if color == ft.Colors.RED: msg_type = "error"
            elif color == ft.Colors.GREEN: msg_type = "success"
            elif color == ft.Colors.ORANGE or color == ft.Colors.AMBER: msg_type = "warning"
            self.page.show_toast(message, type=msg_type)
        else:
            snack = ft.SnackBar(content=ft.Text(message), open=True, bgcolor=color, **kwargs)
            self.page.overlay.append(snack)
            self.page.update()

    def _safe_update(self):
        try:
            if self.page: self.update()
        except: pass
