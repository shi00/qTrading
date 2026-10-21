"""新闻风险解读共享 DTO / 枚举（services 层内部使用，不导入 ui）。

供 ``NewsInsightService``（C1）与 ``services.ai_service.news_risk``（C2）共同消费的
数据类型与枚举正本。枚举集合对应设计方案 §7.2（事件枚举、严重程度、公司角色、状态、影响期
限）。

约束：
- 本模块只依赖 stdlib，位于 services 层；数据层/UI 如需读取结构应经服务层结果对象，不反向依赖。
"""

from __future__ import annotations

import datetime
import typing
from dataclasses import dataclass, field

# Prompt/schema 版本：参与 input_hash (§11)。变更事件 schema / 首版 Prompt 时递增。
NEWS_RISK_PROMPT_VERSION = "news-risk-v1"

# 事件类型枚举（§7.2）
EVENT_TYPES = {
    "regulatory",
    "litigation",
    "financial_reporting",
    "liquidity_debt",
    "shareholder_governance",
    "operation_safety",
    "corporate_action",
    "reputation",
    "macro_industry",
    "other",
}
# 事件级严重程度（§9.3 有序，用于服务端确定性聚合）
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
# 公司角色
COMPANY_ROLES = {"direct", "indirect", "supply_chain"}
# 事件状态
EVENT_STATUS = {"proposed", "ongoing", "resolved"}
# 影响期限
IMPACT_HORIZONS = {"short_term", "medium_term", "long_term"}

# 仅成功分析（含/不含事件）的快照可作缓存复用（§11）
SUCCESS_STATUS = frozenset({"analyzed_with_events", "analyzed_no_event"})
# 合法 analysis_status（§7.2）
ANALYSIS_STATUS = SUCCESS_STATUS | frozenset({"evidence_only", "no_evidence", "failed"})


@dataclass
class EvidenceDocument:
    """单条风险分析证据（一条 market_news 文档 + 供 LLM 展示的中性化摘录）。

    ``quoteable_text`` 为服务层经 ``neutralize_external_text`` 生成并通过证据校验的
    摘录；AIService 分析子模块据此构建 Prompt 与证据引用校验（§9.4）。
    """

    news_id: int
    source_kind: str | None = None
    source: str | None = None
    title: str | None = None
    content: str | None = None
    publish_time: datetime.datetime | None = None
    url: str | None = None
    content_hash: str | None = None
    quoteable_text: str = ""
    category_l1: str | None = None
    sentiment: str | None = None


@dataclass
class NewsInsightRequest:
    """进入 ``AIService.analyze_news_risk`` 的分析请求。

    ``evidence`` 已由服务层完成：来源落库取 id、确定性去重、公告优先截取 top-12、逐条
    neutralize 形成 ``quoteable_text``。``coverage`` / ``analysis_profile`` 亦由服务层
    计算后传入，分析子模块不感知来源获取细节。
    """

    ts_code: str
    stock_name: str
    window_start: datetime.datetime
    window_end: datetime.datetime
    evidence: list[EvidenceDocument] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    analysis_profile: str = ""
    prompt_version: str = NEWS_RISK_PROMPT_VERSION
    # F39 冻结分派载荷：``dispatch_frozen=True`` 时 ``frozen_dispatch`` 为编排层
    # 冻结的分派结果（``ScenarioDispatch`` 或 None=新链未命中/旧链域），分析子模块
    # 不再自行分派读盘——保证缓存指纹（``analysis_profile``）与实际推理同版；
    # 默认 False = 未冻结（既有自行分派语义，E2E fake / 旧调用零破坏）。
    # 类型说明：``ScenarioDispatch | None``（scenario_dispatch 位于同层 ai_service，
    # 此处仅 typing.Any 承载，避免 models 反向感知执行域细节）。
    dispatch_frozen: bool = False
    frozen_dispatch: typing.Any = None


@dataclass
class NewsInsightResult:
    """结构化风险分析结果（由服务端校验 & 确定性聚合后产出）。

    ``risk_level`` 为已校验有效事件的最高严重程度；无有效事件或未成功分析时为 None（R21，
    不伪装低风险）。``analysis_status`` 区分成功/降级/失败（§9.2）。
    """

    analysis_status: str
    risk_level: str | None
    confidence: int | None
    summary: str | None
    events: list[dict]
    evidence_news_ids: list[int]
    coverage: dict
    model_id: str | None
    analysis_profile: str
    prompt_version: str
    # S4 统一场景链（s4_plan §4.1）：程序构造的执行元数据（8 键），旧链为 None。
    execution_metadata: dict | None = None
