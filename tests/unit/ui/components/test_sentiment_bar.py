# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件直接断言 flet 控件子属性（content/controls/value/bgcolor），统一局部禁用属性访问告警
# （flet 控件属性在类型存根上宽泛；同组件测试范式，运行时结构已由 isinstance 前置保证）。

"""SentimentBar 组件单测（N5-3）。

覆盖：纯映射函数（颜色/强情绪阈值边界）、柱构建（缺失值不着色，R21）、
明细行（score=None → '-' 中性色）、洞察面板指数行、i18n key 中英对称。

组件本体（@ft.component SentimentBar）含 hooks 闭包不可无头单测，与
test_news_insight_panel.py 范式一致：测纯构建函数 + i18n 对称。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import flet as ft
import pytest

from core.i18n import I18n
from ui.components.sentiment_bar import (
    STRONG_SENTIMENT_THRESHOLD,
    build_day_bar,
    build_latest_sentiment_line,
    build_news_row,
    build_sentiment_strip,
    is_strong_sentiment,
    news_score_color,
    sentiment_bar_color,
)
from ui.theme import AppColors
from ui.viewmodels.sentiment_index_types import (
    PHASE_IDLE,
    PHASE_READY,
    SentimentDayPoint,
    SentimentIndexState,
    SentimentNewsItem,
)

pytestmark = pytest.mark.unit

DAY = datetime.date(2026, 10, 12)


@pytest.fixture(autouse=True)
def _init_i18n():
    I18n.initialize("zh_CN")
    yield


def _point(index: float | None = 0.5, n: int = 4, insufficient: bool = False, trade_date: datetime.date = DAY):
    return SentimentDayPoint(trade_date=trade_date, index=index, sample_count=n, sample_insufficient=insufficient)


def _news(score: float | None = 0.5, conf: float | None = 0.8):
    return SentimentNewsItem(
        publish_time="2026-10-12 10:00", title="利好公告", excerpt=None, source_kind="news", score=score, conf=conf
    )


# --- 纯映射函数 ---


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0.5, AppColors.UP_RED),
        (-0.5, AppColors.DOWN_GREEN),
        (0.0, AppColors.TEXT_HINT),  # 合法中性 0 ≠ 缺失（R21：None 才是缺失）
        (None, None),  # 缺失 → 不画柱
    ],
)
def test_sentiment_bar_color(index, expected):
    color = sentiment_bar_color(index)
    if expected is None:
        assert color is None
    else:
        assert color == expected


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0.71, True), (0.7, False), (-0.71, True), (-0.7, False), (0.5, False), (None, False)],
)
def test_is_strong_sentiment_threshold_strict(index, expected):
    """|score| > 0.7 严格大于：0.7 边界值不高亮。"""
    assert is_strong_sentiment(index) is expected


def test_strong_threshold_constant():
    assert STRONG_SENTIMENT_THRESHOLD == 0.7


@pytest.mark.parametrize(
    ("score", "expected_color"),
    [
        (None, AppColors.TEXT_SECONDARY),  # R21：未打分不着方向色
        (0.5, AppColors.TEXT_SECONDARY),  # 弱情绪中性
        (0.8, AppColors.UP_RED),  # 强正面
        (-0.8, AppColors.DOWN_GREEN),  # 强负面
    ],
)
def test_news_score_color(score, expected_color):
    assert news_score_color(score) == expected_color


# --- 柱构建（缺失值不着色） ---


def test_build_day_bar_missing_index_not_colored():
    """R21：index=None 柱不画方向色（占位灰条）。"""
    control = build_day_bar(_point(index=None), selected=False, on_click=lambda _e: None)
    column = control.content
    assert isinstance(column, ft.Column)
    value_text, bar, _date_text = column.controls
    assert value_text.value == "-"  # 缺失显示 '-' 不填 0
    assert bar.bgcolor == AppColors.SURFACE_VARIANT  # 占位灰，非 UP_RED/DOWN_GREEN


def test_build_day_bar_strong_sentiment_bold_and_colored():
    control = build_day_bar(_point(index=0.85), selected=False, on_click=lambda _e: None)
    column = control.content
    value_text, bar, _ = column.controls
    assert value_text.weight == ft.FontWeight.BOLD  # 强情绪非仅颜色表达
    assert bar.bgcolor == AppColors.UP_RED


def test_build_day_bar_selected_border():
    plain = build_day_bar(_point(index=0.3), selected=False, on_click=lambda _e: None)
    selected = build_day_bar(_point(index=0.3), selected=True, on_click=lambda _e: None)
    assert plain.border is None
    assert selected.border == ft.Border.all(1, AppColors.PRIMARY)  # 选中描边：主色 1px 全边


def test_build_day_bar_insufficient_tooltip_marks_sample():
    """样本不足（n<3）在 tooltip 标注，不隐藏柱。"""
    control = build_day_bar(_point(index=0.5, n=2, insufficient=True), selected=False, on_click=lambda _e: None)
    assert "样本不足" in str(
        control.tooltip
    )  # tooltip 类型 str | TooltipValue，经 str() 归一（同 test_chart_utils.py 先例）


# --- 明细行 ---


def test_build_news_row_missing_score_neutral():
    """R21：score=None → '-' 且中性色。"""
    control = build_news_row(_news(score=None, conf=None))
    row = control.content.controls[0]
    score_text = row.controls[1]
    conf_text = row.controls[2]
    assert "-" in score_text.value
    assert score_text.color == AppColors.TEXT_SECONDARY
    assert conf_text.color == AppColors.TEXT_SECONDARY


def test_build_news_row_strong_score_highlighted():
    control = build_news_row(_news(score=-0.9))
    row = control.content.controls[0]
    title_text, score_text, _ = row.controls
    assert score_text.color == AppColors.DOWN_GREEN
    assert score_text.weight == ft.FontWeight.BOLD
    assert title_text.weight == ft.FontWeight.BOLD  # 标题同步加粗（颜色非唯一表达）


# --- 洞察面板指数行 ---


def test_build_latest_sentiment_line_none_state():
    assert build_latest_sentiment_line(None) is None
    assert build_latest_sentiment_line(SentimentIndexState()) is None


def test_build_latest_sentiment_line_no_news_day():
    """最新交易日无新闻（index=None）→ 中性提示，不填 0。"""
    state = SentimentIndexState(points=(_point(index=None),))
    control = build_latest_sentiment_line(state)
    assert isinstance(control, ft.Text)
    assert control.color == AppColors.TEXT_SECONDARY


def test_build_latest_sentiment_line_with_value():
    state = SentimentIndexState(points=(_point(index=0.85, n=6),))
    control = build_latest_sentiment_line(state)
    assert isinstance(control, ft.Text)
    assert "+0.85" in control.value and "（6" in control.value


# --- 全区构建 ---


def test_build_strip_empty_state_shows_placeholder():
    control = build_sentiment_strip(SentimentIndexState(phase=PHASE_READY), on_select_day=None)
    assert isinstance(control, ft.Text)  # 空数据占位文案


def test_build_strip_idle_renders_no_placeholder():
    """IDLE（弹窗打开瞬间未选股）：不渲染占位，避免闪现「暂无数据」。"""
    control = build_sentiment_strip(SentimentIndexState(phase=PHASE_IDLE), on_select_day=None)
    assert isinstance(control, ft.Container)
    assert control.content is None


def test_build_strip_renders_bars_and_disclaimer():
    points = tuple(_point(index=0.5, trade_date=DAY) for DAY in [DAY, datetime.date(2026, 10, 13)])
    state = SentimentIndexState(phase=PHASE_READY, points=points)
    control = build_sentiment_strip(state, on_select_day=None)
    assert isinstance(control, ft.Column)
    texts = [c for c in control.controls if isinstance(c, ft.Text)]
    # 标题 + 免责措辞到位（ADR-0008）
    assert any("新闻情绪倾向" in (t.value or "") for t in texts)
    assert any("不构成买卖建议" in (t.value or "") for t in texts)
    bars_row = control.controls[1]
    assert len(bars_row.controls) == 2


def test_build_strip_selected_day_shows_detail():
    state = SentimentIndexState(phase=PHASE_READY, points=(_point(index=0.5),), selected_day=DAY, day_news=(_news(),))
    control = build_sentiment_strip(state, on_select_day=None)
    assert isinstance(control, ft.Column)
    detail_section = control.controls[2]  # build_day_news_section
    assert isinstance(detail_section, ft.Column)
    header_text = detail_section.controls[0]
    assert isinstance(header_text, ft.Text)
    assert "2026-10-12" in (header_text.value or "")
    body = detail_section.controls[1]
    assert isinstance(body, ft.Column) and len(body.controls) == 1  # 1 条明细行


# --- i18n key 中英对称 ---


def test_sentiment_index_i18n_keys_symmetric():
    locales_dir = Path(__file__).parents[4] / "locales"
    zh = json.loads((locales_dir / "zh_CN" / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((locales_dir / "en_US" / "strings.json").read_text(encoding="utf-8"))
    zh_keys = {k for k in zh if k.startswith("sentiment_index_")}
    en_keys = {k for k in en if k.startswith("sentiment_index_")}
    assert zh_keys == en_keys
    assert len(zh_keys) >= 12
    # 占位符一致（中英参数对齐）
    for key in zh_keys:
        import re

        assert re.findall(r"\{(\w+)\}", zh[key]) == re.findall(r"\{(\w+)\}", en[key]), key
