import flet as ft
from ui.theme import AppColors, AppStyles

class DashboardCard(ft.Container):
    """
    Base card component for the dashboard.
    White background, rounded corners, subtle shadow.
    """
    def __init__(self, content, padding=20):
        super().__init__(
            content=content,
            padding=padding,
            bgcolor=AppColors.SURFACE,
            border_radius=16,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=10,
                color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
                offset=ft.Offset(0, 4),
            ),
            border=ft.border.all(1, ft.Colors.with_opacity(0.5, AppColors.BORDER))
        )

class MetricCard(ft.Container):
    """
    Display a single key metric with label, value, and status icon.
    """
    def __init__(self, label, value, icon=None, status_color=None, trend=None, trend_up=True):
        super().__init__()
        self.expand = True
        
        status_row = []
        if icon:
            status_row.append(ft.Icon(icon, size=14, color=status_color or AppColors.PRIMARY))
        
        if trend:
            trend_color = AppColors.UP if trend_up else AppColors.DOWN
            status_row.append(ft.Text(trend, size=11, color=trend_color, weight=ft.FontWeight.BOLD))
            
        self.content = ft.Column([
            ft.Text(label.upper(), size=11, color=AppColors.TEXT_HINT, weight=ft.FontWeight.BOLD),
            ft.Text(value, size=22, weight=ft.FontWeight.BOLD, color=AppColors.PRIMARY),
            ft.Row(status_row, spacing=4, alignment=ft.MainAxisAlignment.START) if status_row else ft.Container()
        ], spacing=4)
        
        self.padding = 15
        self.bgcolor = ft.Colors.with_opacity(0.02, AppColors.PRIMARY)
        self.border_radius = 12
        self.border = ft.border.all(1, ft.Colors.with_opacity(0.1, AppColors.PRIMARY))

    def set_value(self, value, icon=None, status_color=None):
        """Update the value and optional status icon of the card"""
        # Update Value Text
        self.content.controls[1].value = value
        
        # Update Status Row
        if icon:
            new_status_row = [ft.Icon(icon, size=14, color=status_color or AppColors.PRIMARY)]
            # We replace the 3rd element (Status Container/Row)
            self.content.controls[2] = ft.Row(new_status_row, spacing=4, alignment=ft.MainAxisAlignment.START)
        
        self.update()

class ActionChip(ft.Container):
    """
    Interactive chip for quick actions.
    """
    def __init__(self, icon, title, subtitle, on_click, is_primary=False):
        super().__init__()
        self.on_click = on_click
        self.ink = True
        self.border_radius = 12
        self.padding = 15
        
        base_color = AppColors.PRIMARY if is_primary else AppColors.TEXT_PRIMARY
        bg_color = AppColors.PRIMARY if is_primary else AppColors.SURFACE
        text_color = AppColors.TEXT_ON_PRIMARY if is_primary else AppColors.TEXT_PRIMARY
        sub_color = ft.Colors.with_opacity(0.8, text_color)
        
        if not is_primary:
            self.border = ft.border.all(1, AppColors.BORDER)
            self.bgcolor = AppColors.SURFACE
        else:
            self.bgcolor = AppColors.PRIMARY
            self.shadow = ft.BoxShadow(
                blur_radius=8,
                color=ft.Colors.with_opacity(0.3, AppColors.PRIMARY),
                offset=ft.Offset(0, 4)
            )

        self.content = ft.Row([
            ft.Container(
                content=ft.Icon(icon, color=text_color, size=24),
                padding=10,
                bgcolor=ft.Colors.with_opacity(0.1 if is_primary else 0.05, text_color if is_primary else ft.Colors.BLACK),
                border_radius=10,
            ),
            ft.Column([
                ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=text_color),
                ft.Text(subtitle, size=11, color=sub_color),
            ], spacing=2, expand=True),
            ft.Icon(ft.Icons.CHEVRON_RIGHT, color=sub_color, size=16)
        ], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.CENTER)

class StatusBadge(ft.Container):
    """
    Small pill-shaped badge for status (Connected, Syncing, etc).
    """
    def __init__(self, text, color, icon=None):
        super().__init__()
        content_row = [ft.Text(text, size=10, color=color, weight=ft.FontWeight.BOLD)]
        if icon:
            content_row.insert(0, ft.Icon(icon, size=10, color=color))
            
        self.content = ft.Row(content_row, spacing=4, alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.padding = ft.padding.symmetric(horizontal=8, vertical=4)
        self.bgcolor = ft.Colors.with_opacity(0.1, color)
        self.border_radius = 20
        self.border = ft.border.all(1, ft.Colors.with_opacity(0.2, color))

class SectionHeader(ft.Row):
    """
    Professional section header with left border accent.
    """
    def __init__(self, title, action=None):
        super().__init__()
        self.alignment = ft.MainAxisAlignment.SPACE_BETWEEN
        self.vertical_alignment = ft.CrossAxisAlignment.CENTER
        
        self.controls = [
            ft.Row([
                ft.Container(width=4, height=18, bgcolor=AppColors.ACCENT, border_radius=2),
                ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY)
            ], spacing=10),
        ]
        if action:
            self.controls.append(action)
