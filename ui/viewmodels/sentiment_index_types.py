"""SentimentIndexViewModel 共享不可变状态类型（N5-3 新闻情绪展示）。

约束（对齐 CLAUDE.md §3.2 / docs/patterns/mvvm.md / news_insight_types.py 范式）：
- 全部 frozen dataclass，禁止 dict/list 可变字段；点/行用 tuple；
- VM 只产出 i18n key（``Message``）与原始数值，不感知 locale；View 渲染期翻译；
- 指数 ``index`` 为 None 表示当日无可用新闻（R21：不画 0）；
- 新闻 ``score``/``conf`` 为 None 表示未打分（R21：展示「-」不着方向色）。

本模块只 import stdlib + ``ui.viewmodels.Message``，不 import flet（VM 层约束）。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from ui.viewmodels import Message

# 状态机阶段：idle（未选择）/ loading（索引加载中）/ ready / error
PHASE_IDLE = "idle"
PHASE_LOADING = "loading"
PHASE_READY = "ready"
PHASE_ERROR = "error"

__all__ = [
    "PHASE_ERROR",
    "PHASE_IDLE",
    "PHASE_LOADING",
    "PHASE_READY",
    "SentimentDayPoint",
    "SentimentIndexState",
    "SentimentNewsItem",
]


@dataclass(frozen=True)
class SentimentDayPoint:
    """单交易日情绪指数点（镜像服务层 DailySentimentPoint；index=None=无新闻，R21）。"""

    trade_date: datetime.date
    index: float | None
    sample_count: int
    sample_insufficient: bool


@dataclass(frozen=True)
class SentimentNewsItem:
    """单条新闻明细（点击某日展开；score/conf=None 表示未打分，R21）。"""

    publish_time: str  # CST 展示串（VM 层格式化，locale 无关）
    title: str | None
    excerpt: str | None  # 无标题时的正文摘录
    source_kind: str | None  # announcement / news / telegraph
    score: float | None
    conf: float | None


@dataclass(frozen=True)
class SentimentIndexState:
    """新闻情绪指数不可变状态快照（N5-3，仅展示，ADR-0008）。"""

    phase: str = PHASE_IDLE
    ts_code: str = ""
    stock_name: str = ""
    days: int = 7
    points: tuple[SentimentDayPoint, ...] = ()
    # 当日明细（select_day 展开态；同日再点收起）
    selected_day: datetime.date | None = None
    day_news: tuple[SentimentNewsItem, ...] = ()
    day_news_loading: bool = False
    # 业务/状态提示（i18n key；error 时非空）
    message: Message | None = None
