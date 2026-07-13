"""market_dashboard — 声明式组件 (Phase B.1).

从命令式 class 子类重写为 @ft.component 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式容器子类 → ``@ft.component def MarketDashboard(data)``
- 移除所有命令式 API（数据推送/主题刷新/语言刷新/手动刷新/数据缓存/概念卡回收池）
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 状态驱动渲染: data 由消费方通过 props 推送触发重渲染
"""

import flet as ft

from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles


def _resolve_color(color_str: str | None) -> str:
    """Resolve RED/GREEN/GREY color name to AppColors token."""
    name = (color_str or "").upper()
    if name == "RED":
        return AppColors.UP
    if name == "GREEN":
        return AppColors.DOWN
    return AppColors.TEXT_SECONDARY


def _build_index_card(title_key: str, info: dict) -> ft.Container:
    """Build a single index card (SH/SZ/CYB) — pure function."""
    style = AppStyles.dashboard_card()
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    I18n.get(title_key),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    no_wrap=True,
                ),
                ft.Text(
                    str(info.get("value", "--")),
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(
                    str(info.get("change", "--")),
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=_resolve_color(info.get("color")),
                ),
            ],
            spacing=5,
        ),
        padding=style["padding"],
        bgcolor=style["bgcolor"],
        border_radius=style["border_radius"],
        border=style["border"],
        shadow=style["shadow"],
        col={"xs": 6, "sm": 6, "md": 3, "lg": 3},
    )


def _build_hsgt_card(info: dict) -> ft.Container:
    """Build the northbound funds (HSGT) card — pure function."""
    style = AppStyles.dashboard_card()
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    I18n.get("home_northbound"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    no_wrap=True,
                ),
                ft.Text(
                    str(info.get("value", "--")),
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=_resolve_color(info.get("color")),
                ),
                ft.Text(
                    str(info.get("sub", "--")),
                    size=12,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            spacing=5,
        ),
        padding=style["padding"],
        bgcolor=style["bgcolor"],
        border_radius=style["border_radius"],
        border=style["border"],
        shadow=style["shadow"],
        col={"xs": 6, "sm": 6, "md": 3, "lg": 3},
    )


def _build_concept_card(item: dict) -> ft.Container:
    """Build a single hot concept card — pure function."""
    color_str = str(item.get("color", ""))
    is_up = "red" in color_str
    color = AppColors.UP if is_up else AppColors.DOWN
    icon = ft.Icons.TRENDING_UP if is_up else ft.Icons.TRENDING_DOWN
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    item.get("name", "--"),
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                    no_wrap=True,
                ),
                ft.Row(
                    [
                        ft.Icon(icon, size=16, color=color),
                        ft.Text(
                            item.get("change", "0.00%"),
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            color=color,
                        ),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=5,
        ),
        padding=15,
        bgcolor=AppColors.SURFACE,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
        col={"xs": 6, "sm": 4, "md": 3, "lg": 2},
    )


@ft.component
def MarketDashboard(data: dict | None = None) -> ft.Column:
    """Market Dashboard Component (declarative).

    Displays market indices (SH/SZ/CYB), northbound funds (HSGT),
    and hot concepts. Data is pushed via the ``data`` prop (replaces
    the old imperative data-push API). i18n/theme auto-rerender via
    ``ft.use_state`` subscription.
    """
    # Subscribe to i18n + theme changes (auto-rerender on locale/theme switch)
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    if data is None:
        data = {}
    indices = data.get("indices") or []
    hsgt = data.get("hsgt") or {}
    hot_concepts = data.get("hot_concepts") or []

    # --- Indices row (3 indices + 1 HSGT, always 4 cards) ---
    index_titles = ["home_index_sh", "home_index_sz", "home_index_cyb"]
    index_cards: list[ft.Control] = []
    for i in range(3):
        info = indices[i] if i < len(indices) and isinstance(indices[i], dict) else {}
        index_cards.append(_build_index_card(index_titles[i], info))
    index_cards.append(_build_hsgt_card(hsgt if isinstance(hsgt, dict) else {}))

    indices_row = ft.ResponsiveRow(index_cards, run_spacing=10)

    # --- Hot concepts section ---
    concepts_title = ft.Text(
        I18n.get("home_hot_concepts"),
        size=16,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    concept_cards: list[ft.Control] = []
    if hot_concepts:
        for item in hot_concepts:
            concept_cards.append(_build_concept_card(item))
    else:
        concept_cards.append(
            ft.Container(
                col=AppStyles.COL_FULL,
                content=ft.Text(
                    I18n.get("home_hot_concepts_empty"),
                    size=12,
                    color=AppColors.TEXT_HINT,
                ),
            )
        )

    concepts_row = ft.ResponsiveRow(concept_cards, run_spacing=10)
    concepts_section = ft.Column([concepts_title, concepts_row], spacing=10)

    return ft.Column(
        [
            indices_row,
            ft.Container(height=10),
            concepts_section,
            ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
        ],
        spacing=10,
    )
