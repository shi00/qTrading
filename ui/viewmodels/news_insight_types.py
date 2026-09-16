"""NewsInsightViewModel 共享不可变状态类型（新闻风险解读第一期 Phase D1）。

设计依据：``reviews/新闻风险解读方案.md`` §12（UI 状态机）+ §3.2 MVVM 状态契约。

约束（对齐 CLAUDE.md §3.2 / mvvm.md）：
- 全部 frozen dataclass，禁止 dict/list 可变字段；行/事件/证据用 tuple。
- VM 只产出 i18n key（``Message``）与原始枚举值，不感知 locale；View 渲染期翻译。
- 本模块只 import stdlib + ``ui.viewmodels.Message``，不 import flet（VM 层 R16/宪法）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ui.viewmodels import Message

# 状态机阶段（§12）：idle / loading_evidence / evidence_ready / analyzing / ready / degraded / error
PHASE_IDLE = "idle"
PHASE_LOADING_EVIDENCE = "loading_evidence"
PHASE_EVIDENCE_READY = "evidence_ready"
PHASE_ANALYZING = "analyzing"
PHASE_READY = "ready"
PHASE_DEGRADED = "degraded"
PHASE_ERROR = "error"

__all__ = [
    "PHASE_IDLE",
    "PHASE_LOADING_EVIDENCE",
    "PHASE_EVIDENCE_READY",
    "PHASE_ANALYZING",
    "PHASE_READY",
    "PHASE_DEGRADED",
    "PHASE_ERROR",
    "RiskQuote",
    "RiskEvent",
    "SourceCoverage",
    "EvidenceItem",
    "NewsInsightState",
]


@dataclass(frozen=True)
class RiskQuote:
    """事件的一条原文摘录引用（仅指向本次输入证据的 ``news_id``，§9.4）。"""

    news_id: int
    quote: str


@dataclass(frozen=True)
class RiskEvent:
    """单个风险事件（不可变；供 View 渲染事件卡片）。"""

    event_type: str
    severity: str
    company_role: str
    event_status: str
    impact_horizon: str
    fact: str
    impact_reasoning: str
    uncertainty: str
    confidence: int
    evidence_quotes: tuple[RiskQuote, ...] = ()


@dataclass(frozen=True)
class SourceCoverage:
    """单个来源覆盖状态（§8.1：announcement / news / telegraph）。"""

    source: str
    status: str  # "ok" / "fail"
    fetched: int = 0
    adopted: int = 0
    earliest: str | None = None
    latest: str | None = None


@dataclass(frozen=True)
class EvidenceItem:
    """单条证据的展示视图（来源名/标题/摘录，供 View 展示原始来源，§12 必须项）。"""

    news_id: int
    source_kind: str | None = None
    source: str | None = None
    title: str | None = None
    quoteable_text: str = ""
    url: str | None = None
    publish_time: str | None = None


@dataclass(frozen=True)
class NewsInsightState:
    """新闻风险解读不可变状态快照（§12 状态机）。"""

    phase: str = PHASE_IDLE
    ts_code: str = ""
    stock_name: str = ""
    # 分析结果（ready 时非空）
    risk_level: str | None = None  # "low"/"medium"/"high"/"critical"/None（None=未知，R21）
    risk_level_text: str | None = None  # 由 View 映射后的文字说明（运行期翻译填充，非 i18n key）
    confidence: int | None = None  # AI 置信度（0-100），None=未知
    summary: str | None = None
    events: tuple[RiskEvent, ...] = ()
    # 复用历史快照标注（§11 cache_hit/subset）
    reused: bool = False
    reuse_type: str = "none"
    # 窗口与覆盖（§12 必须显示）
    window_label: str = "unknown"
    analysis_time: str | None = None
    coverage: tuple[SourceCoverage, ...] = ()
    # 证据列表（evidence_ready/degraded 时展示；无证据为空 tuple）
    evidence: tuple[EvidenceItem, ...] = ()
    # 业务/状态提示（i18n key；error/degraded 时非空）
    message: Message | None = None
