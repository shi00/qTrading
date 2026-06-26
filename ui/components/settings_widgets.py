import logging

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


class DashboardCard(ft.Container):
    """
    Base card component for the dashboard.
    Uses semantic tokens — auto-updates with theme.
    """

    def __init__(self, content, padding=20, expand=False):
        style = AppStyles.card()
        super().__init__(
            content=content,
            padding=padding,
            expand=expand,
            border_radius=style["border_radius"],  # type: ignore[untyped]
            bgcolor=style["bgcolor"],  # ft.Colors.SURFACE — auto-resolves  # type: ignore[untyped]
            border=style.get("border"),
            shadow=style.get("shadow"),
        )


class MetricCard(ft.Container):
    """
    Display a single key metric with label, value, and status icon.
    Standard colors use semantic tokens. UP/DOWN colors are custom (Layer 2).
    """

    def __init__(
        self,
        label,
        value,
        icon=None,
        status_color=None,
        trend=None,
        trend_up=True,
    ):
        super().__init__()
        self.expand = True

        self.label_text = label
        self.value_text = value
        self.icon_name = icon
        self.status_color_val = status_color
        self.trend_text = trend
        self.trend_up_val = trend_up

        self.label_view = ft.Text(
            self.label_text.upper(),
            size=11,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.value_view = ft.Text(
            self.value_text,
            size=22,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.PRIMARY,
        )
        self.status_row_view = ft.Row(
            [],
            spacing=4,
            alignment=ft.MainAxisAlignment.START,
        )

        self._build_status_row()

        self.content = ft.Column(
            [self.label_view, self.value_view, self.status_row_view],
            spacing=4,
        )

        self.padding = 15
        self.border_radius = 12
        self.bgcolor = ft.Colors.with_opacity(0.02, ft.Colors.PRIMARY)
        self.border = ft.border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.PRIMARY))

    def _build_status_row(self):
        """Build status row controls (uses custom colors for UP/DOWN)."""
        controls = []
        resolved_color = self.status_color_val if self.status_color_val else ft.Colors.PRIMARY
        if self.icon_name:
            controls.append(ft.Icon(self.icon_name, size=14, color=resolved_color))

        if self.trend_text:
            # UP/DOWN are Layer 2 custom colors
            trend_color = AppColors.UP if self.trend_up_val else AppColors.DOWN
            controls.append(
                ft.Text(
                    self.trend_text,
                    size=11,
                    color=trend_color,
                    weight=ft.FontWeight.BOLD,
                ),
            )

        self.status_row_view.controls = controls if controls else [ft.Container()]

    def set_value(self, value, icon=None, status_color=None):
        """Update the value and optional status icon of the card."""
        self.value_text = value
        self.value_view.value = value
        self.icon_name = icon
        self.status_color_val = status_color
        self._build_status_row()
        if self.page:
            self.update()

    def set_label(self, label: str) -> None:
        """Update the label text (used for i18n hot reload)."""
        self.label_text = label
        self.label_view.value = label.upper() if label else ""
        if self.page:
            self.update()

    def update_theme(self):
        """Only needed for custom UP/DOWN colors in trend display."""
        self._build_status_row()
        if self.page:
            self.update()


class ActionChip(ft.Container):
    """
    Interactive chip for quick actions.
    Uses semantic tokens — auto-updates with theme.
    """

    def __init__(self, icon, title, subtitle, on_click, is_primary=False):
        super().__init__(on_click=on_click, ink=True, border_radius=12, padding=15)

        self.icon_name = icon
        self.title_text = title
        self.subtitle_text = subtitle
        self.is_primary = is_primary

        # Colors resolve automatically via tokens
        self.content = self._build_content()

    def _build_content(self):
        # Colors resolve automatically via tokens
        if self.is_primary:
            text_color = ft.Colors.ON_PRIMARY
            self.bgcolor = ft.Colors.PRIMARY
            # Shadow already set in init but can be dynamic
        else:
            text_color = ft.Colors.ON_SURFACE
            self.bgcolor = ft.Colors.SURFACE

        sub_color = ft.Colors.with_opacity(0.8, text_color)

        return ft.Row(
            [
                ft.Container(
                    content=ft.Icon(self.icon_name, color=text_color, size=24),
                    padding=10,
                    bgcolor=ft.Colors.with_opacity(
                        0.1 if self.is_primary else 0.05,
                        text_color if self.is_primary else ft.Colors.SHADOW,
                    ),
                    border_radius=10,
                ),
                ft.Column(
                    [
                        ft.Text(
                            self.title_text,
                            size=14,
                            weight=ft.FontWeight.BOLD,
                            color=text_color,
                        ),
                        ft.Text(self.subtitle_text, size=11, color=sub_color),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Icon(ft.Icons.CHEVRON_RIGHT, color=sub_color, size=16),
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def set_loading(self, is_loading: bool):
        """Visual update for loading state"""
        if is_loading:
            # Show Spinner
            color = ft.Colors.ON_PRIMARY if self.is_primary else ft.Colors.PRIMARY
            self.content.controls[-1] = ft.ProgressRing(  # type: ignore[untyped]
                width=16,
                height=16,
                stroke_width=2,
                color=color,
            )
            self.disabled = True
            self.opacity = 0.8  # Slight dim but clearer than disabled
        else:
            # Restore Icon
            sub_color = ft.Colors.with_opacity(
                0.8,
                ft.Colors.ON_PRIMARY if self.is_primary else ft.Colors.ON_SURFACE,
            )
            self.content.controls[-1] = ft.Icon(  # type: ignore[untyped]
                ft.Icons.CHEVRON_RIGHT,
                color=sub_color,
                size=16,
            )
            self.disabled = False
            self.opacity = 1.0

        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug(f"[SettingsWidget] UI update skipped: {exc}")

    def set_text(self, title: str, subtitle: str | None = None) -> None:
        """Update title (and optional subtitle) — used for i18n hot reload."""
        self.title_text = title
        # Rebuild content to refresh text since title/subtitle are inlined
        if subtitle is not None:
            self.subtitle_text = subtitle
        self.content = self._build_content()
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug(f"[SettingsWidget] UI update skipped: {exc}")


class StatusBadge(ft.Container):
    """
    Small pill-shaped badge for status (Connected, Syncing, etc).
    """

    def __init__(self, text, color, icon=None):
        super().__init__()
        self.badge_text = text
        self.badge_color = color
        self.badge_icon = icon
        self.content = self._build_content()
        self.padding = ft.padding.symmetric(horizontal=8, vertical=4)
        self.bgcolor = ft.Colors.with_opacity(0.1, color)
        self.border_radius = 20
        self.border = ft.border.all(1, ft.Colors.with_opacity(0.2, color))

    def _build_content(self):
        content_row: list[ft.Control] = [
            ft.Text(self.badge_text, size=10, color=self.badge_color, weight=ft.FontWeight.BOLD)
        ]
        if self.badge_icon:
            content_row.insert(0, ft.Icon(self.badge_icon, size=10, color=self.badge_color))
        return ft.Row(
            content_row,
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def set_text(self, text: str) -> None:
        """Update badge text — used for i18n hot reload."""
        self.badge_text = text
        self.content = self._build_content()
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug(f"[StatusBadge] UI update skipped: {exc}")


class SectionHeader(ft.Row):
    """
    Professional section header with left border accent.
    Uses semantic tokens — auto-updates with theme.
    """

    def __init__(self, title, action=None, title_key=None):
        self.title_key = title_key
        self.title_view = ft.Text(
            title,
            size=16,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )
        controls = [
            ft.Row(
                [
                    ft.Container(
                        width=4,
                        height=18,
                        bgcolor=ft.Colors.SECONDARY,
                        border_radius=2,
                    ),
                    self.title_view,
                ],
                spacing=10,
            ),
        ]
        if action:
            controls.append(action)

        super().__init__(
            controls=controls,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def update_locale(self):
        if self.title_key:
            self.title_view.value = I18n.get(self.title_key)


class SettingRow(ft.ResponsiveRow):
    """
    Standard setting row with icon, title, subtitle, and control.
    Uses responsive layout: aligns strictly on desktop via grids,
    and gracefully wraps to next line on mobile.
    """

    def __init__(
        self,
        icon,
        title,
        subtitle,
        control,
        icon_color=None,
        title_key=None,
        subtitle_key=None,
        left_col: dict | None = None,
        right_col: dict | None = None,
    ):
        super().__init__()
        self.vertical_alignment = ft.CrossAxisAlignment.CENTER
        self.title_key = title_key
        self.subtitle_key = subtitle_key

        # Default icon color is a semantic token
        color = icon_color if icon_color else ft.Colors.PRIMARY

        self.icon_view = ft.Icon(icon, size=24, color=color)
        self.icon_container = ft.Container(
            content=self.icon_view,
            padding=10,
            border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.1, color),
        )
        self.title_view = ft.Text(
            title,
            size=16,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )
        self.subtitle_view = ft.Text(
            subtitle,
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )

        # --- Left Side (Icon + Text) ---
        left_side = ft.Row(
            [
                self.icon_container,
                ft.Container(width=10),
                ft.Column(
                    [self.title_view, self.subtitle_view],
                    spacing=2,
                    expand=True,
                ),  # Text wraps or expands
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Right Side (Control) ---
        # On mobile (xs): wrap to next line, take full width.
        # On desktop (sm/md): stay on same line, align right, take strict grid width.
        right_side = ft.Row(
            [control],
            alignment=ft.MainAxisAlignment.END,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.controls = [
            ft.Container(content=left_side, col=left_col if left_col is not None else {"xs": 12, "sm": 7, "md": 7}),
            ft.Container(content=right_side, col=right_col if right_col is not None else {"xs": 12, "sm": 5, "md": 5}),
        ]

    def update_locale(self):
        if self.title_key:
            self.title_view.value = I18n.get(self.title_key)
        if self.subtitle_key:
            self.subtitle_view.value = I18n.get(self.subtitle_key)
