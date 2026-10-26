"""新闻情绪倾向柱组件（N5-3，仅展示，ADR-0008：措辞「新闻倾向」，非买卖建议）。

MVVM（CLAUDE.md §3.2 / docs/patterns/mvvm.md）：
- View = 声明式组件，只读不可变 ``SentimentIndexState``（由父组件持有
  SentimentIndexViewModel 并经 props 推送）+ ``on_select_day`` 命令回调；
- 本组件不创建业务 VM、不直接调用打分服务（不 import services 层）；
- 展开态（selected_day）由 VM state 持有（明细为业务数据），组件零业务状态。

红线自检：
- R21：指数 ``index=None`` 不画柱（基线占位，不画 0）；明细 score/conf=None
  显示「-」且不着方向色；
- R23：颜色/字号全部 AppColors/AppStyles token，无裸色值/裸字号；
- 无障碍：强情绪不以颜色为唯一表达（加粗 + 高亮边框 + 数值文本）；
- i18n：VM 只产出 key/数值，本组件渲染期 ``I18n.get`` 翻译。
"""

from __future__ import annotations

import datetime
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_on_click
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.sentiment_index_types import (
    PHASE_IDLE,
    PHASE_LOADING,
    SentimentDayPoint,
    SentimentIndexState,
    SentimentNewsItem,
)

# 强情绪阈值（Plans N5-3：|score| > 0.7 高亮；严格大于，边界值不高亮）
STRONG_SENTIMENT_THRESHOLD = 0.7

# 情绪柱几何（纯展示常量，非字号/色值，不受 R23 约束）
_BAR_MAX_HEIGHT = 40.0
_BAR_MIN_HEIGHT = 2.0
_BAR_WIDTH = 22.0
_BAR_MAX_ABS = 1.0  # 指数理论范围 [-1, +1]


def sentiment_bar_color(index: float | None) -> str | None:
    """指数 → 柱色 token；None → None（不画柱，R21）。"""
    if index is None:
        return None
    if index > 0:
        return AppColors.UP_RED
    if index < 0:
        return AppColors.DOWN_GREEN
    return AppColors.TEXT_HINT  # 恰为 0：合法中性值，非缺失


def is_strong_sentiment(index: float | None) -> bool:
    """强情绪判定：|index| > 0.7（严格大于；None 非强）。"""
    return index is not None and abs(index) > STRONG_SENTIMENT_THRESHOLD


def news_score_color(score: float | None) -> str:
    """明细行分数色：None/弱情绪 → 中性次要色（不着方向色，R21）；强 → 方向色。"""
    if not is_strong_sentiment(score):
        return AppColors.TEXT_SECONDARY
    assert score is not None  # is_strong_sentiment True 蕴含非 None
    return AppColors.UP_RED if score > 0 else AppColors.DOWN_GREEN


def _fmt_index(index: float | None) -> str:
    """指数展示串：None → '-'（R21 不填 0）；否则带符号两位小数。"""
    return f"{index:+.2f}" if index is not None else "-"


def _fmt_score(score: float | None) -> str:
    """明细分数展示串：None → '-'；否则带符号两位小数。"""
    return f"{score:+.2f}" if score is not None else "-"


def _source_label(source_kind: str | None) -> str:
    """来源类型 → i18n 文案（复用新闻洞察面板的 news_insight_src_* key）。"""
    if not source_kind:
        return ""
    return I18n.get(f"news_insight_src_{source_kind}")


def build_day_bar(
    point: SentimentDayPoint,
    selected: bool,
    on_click: Callable[[ft.ControlEvent], None],
) -> ft.Container:
    """单日情绪柱（点击 → on_select_day；缺失值不着色，R21）。"""
    color = sentiment_bar_color(point.index)
    strong = is_strong_sentiment(point.index)
    if color is None:
        bar: ft.Control = ft.Container(height=_BAR_MIN_HEIGHT, bgcolor=AppColors.SURFACE_VARIANT, border_radius=1)
    else:
        bar = ft.Container(
            height=max(_BAR_MIN_HEIGHT, abs(point.index or 0.0) / _BAR_MAX_ABS * _BAR_MAX_HEIGHT),
            width=_BAR_WIDTH,
            bgcolor=color,
            border_radius=2,
        )
    insufficient_tip = (
        f" · {I18n.get('sentiment_index_insufficient', count=point.sample_count)}" if point.sample_insufficient else ""
    )
    tooltip = f"{point.trade_date.isoformat()}  {_fmt_index(point.index)}  ({point.sample_count}){insufficient_tip}"
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    _fmt_index(point.index),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    weight=ft.FontWeight.BOLD if strong else ft.FontWeight.W_400,
                    color=news_score_color(point.index),
                ),
                bar,
                ft.Text(
                    point.trade_date.strftime("%m-%d"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=4, vertical=3),
        border=ft.Border.all(1, AppColors.PRIMARY) if selected else None,
        border_radius=6,
        tooltip=tooltip,
        on_click=safe_on_click(on_click),
    )


def build_news_row(item: SentimentNewsItem) -> ft.Container:
    """当日单条新闻明细行（score/conf=None → '-' 中性色，R21；强分数着方向色 + 加粗）。"""
    head_parts = [p for p in (item.publish_time, _source_label(item.source_kind)) if p]
    score_strong = is_strong_sentiment(item.score)
    conf_text = f"{item.conf:.2f}" if item.conf is not None else "-"
    title_line = ft.Text(
        item.title or item.excerpt or "",
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.TEXT_PRIMARY,
        weight=ft.FontWeight.BOLD if score_strong else ft.FontWeight.W_400,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
        expand=True,
    )
    score_text = ft.Text(
        f"{I18n.get('sentiment_index_score_label')} {_fmt_score(item.score)}",
        size=AppStyles.FONT_SIZE_CAPTION,
        color=news_score_color(item.score),
        weight=ft.FontWeight.BOLD if score_strong else ft.FontWeight.W_400,
    )
    conf_text_control = ft.Text(
        f"{I18n.get('sentiment_index_conf_label')} {conf_text}",
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_SECONDARY,
    )
    return ft.Container(
        content=ft.Column(
            [
                ft.Row([title_line, score_text, conf_text_control], spacing=8),
                ft.Text(
                    " · ".join(head_parts),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                )
                if head_parts
                else ft.Container(),
            ],
            spacing=2,
        ),
        padding=6,
        bgcolor=AppColors.SURFACE_VARIANT,
        border_radius=6,
    )


def build_latest_sentiment_line(state: SentimentIndexState | None) -> ft.Control | None:
    """洞察面板「近 7 日情绪」行（分数与置信度=样本数；无数据/无新闻 → None 或中性提示）。"""
    if state is None or not state.points:
        return None
    latest = state.points[-1]
    if latest.index is None:
        return ft.Text(
            I18n.get("sentiment_index_no_news"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_SECONDARY,
        )
    text = I18n.get("sentiment_index_latest", value=_fmt_index(latest.index), count=latest.sample_count)
    if latest.sample_insufficient:
        text += f" · {I18n.get('sentiment_index_insufficient', count=latest.sample_count)}"
    return ft.Text(
        text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=news_score_color(latest.index),
        weight=ft.FontWeight.BOLD if is_strong_sentiment(latest.index) else ft.FontWeight.W_400,
    )


def build_day_news_section(state: SentimentIndexState) -> ft.Control:
    """展开的当日明细区（loading / 空 / 行列表）。"""
    if state.selected_day is None:
        return ft.Container()
    day: datetime.date = state.selected_day
    header = ft.Text(
        I18n.get("sentiment_index_day_news_title", date=day.isoformat()),
        size=AppStyles.FONT_SIZE_BODY_SM,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )
    if state.day_news_loading:
        body: ft.Control = ft.Row(
            [
                ft.ProgressRing(width=14, height=14),
                ft.Text(I18n.get("sentiment_index_loading"), size=AppStyles.FONT_SIZE_CAPTION),
            ],
            spacing=6,
        )
    elif not state.day_news:
        # 明细加载失败（message 非空）→ 渲染错误提示，不伪装「暂无新闻」
        # （R21 精神：已知失败不得渲染为无信息；message 生命周期由 VM 管理，
        # select_day 展开/收起均重置为 None，无残留误渲染）。
        if state.message is not None:
            body: ft.Control = ft.Text(
                I18n.get(state.message.key, **(state.message.params or {})),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        else:
            body = ft.Text(
                I18n.get("sentiment_index_no_news"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY
            )
    else:
        body = ft.Column([build_news_row(n) for n in state.day_news], spacing=4)
    return ft.Column([header, body], spacing=4)


def build_sentiment_strip(
    state: SentimentIndexState, on_select_day: Callable[[datetime.date], None] | None
) -> ft.Control:
    """情绪区主体（K 线下方）：标题 + 日度柱 + 当日明细 + 免责措辞（ADR-0008）。"""
    if state.phase == PHASE_IDLE:
        return ft.Container()  # 未选股（弹窗打开瞬间）：不渲染占位，避免闪现「暂无数据」
    if state.phase == PHASE_LOADING:
        return ft.Row(
            [
                ft.ProgressRing(width=14, height=14),
                ft.Text(
                    I18n.get("sentiment_index_loading"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            spacing=6,
        )
    if not state.points:
        # ready 但无数据 / error：显示提示（error 时 message 由 VM 产出 key）
        if state.message is not None:
            return ft.Text(
                I18n.get(state.message.key, **(state.message.params or {})),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            )
        return ft.Text(
            I18n.get("sentiment_index_empty"), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY
        )

    def _on_bar_click(day: datetime.date) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            if on_select_day is not None:
                on_select_day(day)

        return _handler

    bars: list[ft.Control] = [
        build_day_bar(
            point,
            selected=state.selected_day == point.trade_date,
            on_click=_on_bar_click(point.trade_date),
        )
        for point in state.points
    ]
    children: list[ft.Control] = [
        ft.Text(
            I18n.get("sentiment_index_title"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        ft.Row(bars, spacing=4, vertical_alignment=ft.CrossAxisAlignment.END),
        build_day_news_section(state),
        ft.Text(
            I18n.get("sentiment_index_disclaimer"),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_SECONDARY,
        ),
    ]
    return ft.Column(children, spacing=6)


@ft.component
def SentimentBar(
    sentiment_state: SentimentIndexState | None = None,
    on_select_day: Callable[[datetime.date], None] | None = None,
) -> ft.Container:  # pragma: no cover  # UI 组件闭包（hooks 订阅）不可无头单测
    """新闻情绪倾向柱（K 线下方；state=None 或无数据时不渲染占位）。"""
    # i18n / 主题变更自动重渲染（组件内禁止手动 I18n.subscribe）
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    if sentiment_state is None:
        return ft.Container()
    return ft.Container(
        content=build_sentiment_strip(sentiment_state, on_select_day),
        margin=ft.Margin.symmetric(vertical=4),
    )
