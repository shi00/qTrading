"""settings_widgets — 声明式组件 (Phase A.1).

从命令式 class 子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 6 个命令式 class (ft.Container/ft.Row/ft.ResponsiveRow) → ``@ft.component`` 函数组件
- 移除所有命令式 API: set_value/set_label/update_theme/set_loading/set_text/update_locale/.update()
- i18n 自动重渲染: SectionHeader/SettingRow 通过 ``ft.use_state(I18n.get_observable_state)`` 订阅
- theme 自动重渲染: MetricCard 通过 ``ft.use_state(AppColors.get_observable_state)`` 订阅
  (trend 用的 UP/DOWN 为 Layer 2 自定义色，需随主题刷新)
- 状态驱动渲染: MetricCard 的 value/icon/status_color、ActionChip 的 is_loading/title/subtitle
  由消费方通过 props 推送触发重渲染（替代旧 set_value/set_loading/set_text）
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


@ft.component
def DashboardCard(
    content: ft.Control,
    padding: int = 20,
    expand: bool = False,
) -> ft.Container:
    """Base card component for the dashboard (declarative).

    Uses semantic tokens via ``AppStyles.card()`` — auto-resolves with theme.
    """
    style = AppStyles.card()
    return ft.Container(
        content=content,
        padding=padding,
        expand=expand,
        border_radius=style.get("border_radius"),
        bgcolor=style.get("bgcolor"),
        border=style.get("border"),
        shadow=style.get("shadow"),
    )


@ft.component
def MetricCard(
    label: str,
    value: str | None,
    icon: str | None = None,
    status_color: str | None = None,
    trend: str | None = None,
    trend_up: bool = True,
) -> ft.Container:
    """Display a single key metric with label, value, and status icon (declarative).

    Layer 2 custom colors (UP/DOWN) for trend display require theme subscription
    via ``ft.use_state(AppColors.get_observable_state)`` for auto-rerender.
    Dynamic value/icon/status_color are pushed by the consumer via props.
    """
    # Subscribe to theme changes (Layer 2 UP/DOWN colors auto-refresh)
    ft.use_state(AppColors.get_observable_state)

    # --- Status row (icon + trend) ---
    resolved_color = status_color if status_color else ft.Colors.PRIMARY
    status_controls: list[ft.Control] = []
    if icon:
        status_controls.append(ft.Icon(icon, size=14, color=resolved_color))
    if trend:
        trend_color = AppColors.UP if trend_up else AppColors.DOWN
        status_controls.append(
            ft.Text(trend, size=11, color=trend_color, weight=ft.FontWeight.BOLD),
        )
    if not status_controls:
        status_controls.append(ft.Container())

    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    label.upper() if label else "",
                    size=11,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Text(
                    value,
                    size=22,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.PRIMARY,
                ),
                ft.Row(status_controls, spacing=4, alignment=ft.MainAxisAlignment.START),
            ],
            spacing=4,
        ),
        expand=True,
        padding=15,
        border_radius=12,
        bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.PRIMARY),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.1, ft.Colors.PRIMARY)),
    )


@ft.component
def ActionChip(
    icon: str,
    title: str,
    subtitle: str,
    on_click: Callable[[ft.ControlEvent], None],
    is_primary: bool = False,
    is_loading: bool = False,
) -> ft.Container:
    """Interactive chip for quick actions (declarative).

    Uses semantic tokens — auto-resolves with theme. ``is_loading`` is a prop
    (pushed by consumer) replacing the old ``set_loading`` imperative API.
    """
    if is_primary:
        text_color = ft.Colors.ON_PRIMARY
        bgcolor = ft.Colors.PRIMARY
        icon_bg_opacity = 0.1
        icon_bg_base = text_color
    else:
        text_color = ft.Colors.ON_SURFACE
        bgcolor = ft.Colors.SURFACE
        icon_bg_opacity = 0.05
        icon_bg_base = ft.Colors.SHADOW

    sub_color = ft.Colors.with_opacity(0.8, text_color)

    # Trailing control: ProgressRing when loading, chevron icon otherwise
    if is_loading:
        trailing = ft.ProgressRing(
            width=16,
            height=16,
            stroke_width=2,
            color=ft.Colors.ON_PRIMARY if is_primary else ft.Colors.PRIMARY,
        )
        disabled = True
        opacity = 0.8
    else:
        trailing = ft.Icon(ft.Icons.CHEVRON_RIGHT, color=sub_color, size=16)
        disabled = False
        opacity = 1.0

    return ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.Icon(icon, color=text_color, size=24),
                    padding=10,
                    bgcolor=ft.Colors.with_opacity(icon_bg_opacity, icon_bg_base),
                    border_radius=10,
                ),
                ft.Column(
                    [
                        ft.Text(
                            title,
                            size=14,
                            weight=ft.FontWeight.BOLD,
                            color=text_color,
                        ),
                        ft.Text(subtitle, size=11, color=sub_color),
                    ],
                    spacing=2,
                    expand=True,
                ),
                trailing,
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        on_click=on_click,
        ink=True,
        border_radius=12,
        padding=15,
        bgcolor=bgcolor,
        disabled=disabled,
        opacity=opacity,
    )


@ft.component
def StatusBadge(
    text: str,
    color: str,
    icon: str | None = None,
) -> ft.Container:
    """Small pill-shaped badge for status (Connected, Syncing, etc).

    Color is a prop — consumer resolves and pushes new text/color via props
    (replacing the old ``set_text`` imperative API).
    """
    content_row: list[ft.Control] = [ft.Text(text, size=10, color=color, weight=ft.FontWeight.BOLD)]
    if icon:
        content_row.insert(0, ft.Icon(icon, size=10, color=color))
    return ft.Container(
        content=ft.Row(
            content_row,
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        bgcolor=ft.Colors.with_opacity(0.1, color),
        border_radius=20,
        border=ft.Border.all(1, ft.Colors.with_opacity(0.2, color)),
    )


@ft.component
def SectionHeader(
    title: str,
    action: ft.Control | None = None,
    title_key: str | None = None,
) -> ft.Row:
    """Professional section header with left border accent (declarative).

    When ``title_key`` is set, the title is re-resolved via ``I18n.get(title_key)``
    on each render; locale changes auto-trigger rerender via
    ``ft.use_state(I18n.get_observable_state)``.
    """
    ft.use_state(I18n.get_observable_state)

    display_title = I18n.get(title_key) if title_key else title
    controls: list[ft.Control] = [
        ft.Row(
            [
                ft.Container(
                    width=4,
                    height=18,
                    bgcolor=ft.Colors.SECONDARY,
                    border_radius=2,
                ),
                ft.Text(
                    display_title,
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.ON_SURFACE,
                ),
            ],
            spacing=10,
        ),
    ]
    if action:
        controls.append(action)
    return ft.Row(
        controls,
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


@ft.component
def SettingRow(
    icon: str,
    title: str,
    subtitle: str,
    control: ft.Control,
    icon_color: str | None = None,
    title_key: str | None = None,
    subtitle_key: str | None = None,
    left_col: dict | None = None,
    right_col: dict | None = None,
) -> ft.ResponsiveRow:
    """Standard setting row with icon, title, subtitle, and control (declarative).

    Responsive layout: aligns strictly on desktop via grids, wraps on mobile.
    When ``title_key``/``subtitle_key`` are set, text is re-resolved via
    ``I18n.get``; locale changes auto-trigger rerender via
    ``ft.use_state(I18n.get_observable_state)``.
    """
    ft.use_state(I18n.get_observable_state)

    display_title = I18n.get(title_key) if title_key else title
    display_subtitle = I18n.get(subtitle_key) if subtitle_key else subtitle
    color = icon_color if icon_color else ft.Colors.PRIMARY

    icon_container = ft.Container(
        content=ft.Icon(icon, size=24, color=color),
        padding=10,
        border_radius=10,
        bgcolor=ft.Colors.with_opacity(0.1, color),
    )
    left_side = ft.Row(
        [
            icon_container,
            ft.Container(width=10),
            ft.Column(
                [
                    ft.Text(
                        display_title,
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.ON_SURFACE,
                    ),
                    ft.Text(
                        display_subtitle,
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=2,
                expand=True,
            ),
        ],
        alignment=ft.MainAxisAlignment.START,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    right_side = ft.Row(
        [control],
        alignment=ft.MainAxisAlignment.END,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    return ft.ResponsiveRow(
        [
            ft.Container(
                content=left_side,
                col=left_col if left_col is not None else {"xs": 12, "sm": 7, "md": 7},
            ),
            ft.Container(
                content=right_side,
                col=right_col if right_col is not None else {"xs": 12, "sm": 5, "md": 5},
            ),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
