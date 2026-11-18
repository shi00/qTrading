"""业务来源登记服务（E-01）：时点（PIT）与显式选择集合门，不复制 DAO 事务。

- 历史决策/日终研究用途：来源可得时点必须已知且早于决策 cutoff，否则
  ``POLICY_DENIED``，且不触达数据库（零登记/零使用，R24）；
- 事后解释用途：事件决策界（``decision_cutoff``）与解释资料可得界
  （``explanation_cutoff``）分离——决策界之后、解释界之前披露的资料可登记，
  但在回执 ``retrospective`` 中标注为事后补充，不得伪当时已知、不得入历史评分；
- 日终研究用途：冻结日终界同样参与准入——披露于日终 17:00 之后的资料不得按
  当日可见登记（顺延到下一可用交易日后再登记），故此处直接拒绝（R24）；
- 截止界为**排他**边界：恰在截止时刻（含过去日的午夜界）的披露归入下一区间，
  不可见；可得时点未知不按按时披露处理，单独给出可审计拒绝（R21）；
- 仅显式选择集合进入登记；未选集合只用于冲突校验，绝不落库（D2）；
- 空选择直接返回空回执（概念问答可无来源）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceItem,
    BusinessSourceRegistrationReceipt,
    BusinessSourceRegistrationRequest,
    Purpose,
    SourceRegistrationRequest,
    SourceVersionRef,
)
from data.persistence.daos.agent_dao import AgentDao

#: 需在准入时核截止界的用途：历史/事后解释按决策 cutoff，日终研究按日终冻结界
#: （当日日终前披露才可见）；两种截止界的语义分离另由裁决项处理，此处统一按
#: 「早于截止才可见」的排他边界判定。
_CUTOFF_PURPOSES = frozenset({Purpose.HISTORICAL_DECISION, Purpose.POST_HOC_EXPLANATION, Purpose.END_OF_DAY_RESEARCH})


class BusinessSourceRegistrar(Protocol):
    """登记持久化端口（生产为 ``AgentDao``；测试可注入替身）。"""

    async def register_business_sources(
        self, request: BusinessSourceRegistrationRequest
    ) -> BusinessSourceRegistrationReceipt: ...


class BusinessSourceService:
    """来源登记应用服务（由 app 组装注入 DAO；不做第二套持久化）。"""

    def __init__(self, dao: AgentDao | BusinessSourceRegistrar) -> None:
        self._dao = dao

    async def register(self, request: SourceRegistrationRequest) -> BusinessSourceRegistrationReceipt:
        """登记显式选择来源；失败不留下部分登记（DAO 单事务）。"""

        identity = f"{request.user_scope}/{request.consumer_kind}/{request.consumer_id}@{request.consumer_version}"
        if not request.selected:
            return BusinessSourceRegistrationReceipt(registered=(), link_ids=(), replayed=False)
        decision_cutoff, explanation_cutoff = _cutoff_bounds(request, identity)
        selected_keys = {(item.source.source_id, item.source.version) for item in request.selected}
        if len(selected_keys) != len(request.selected):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "duplicate selected source", identity)
        for item in request.unselected:
            if (item.source.source_id, item.source.version) in selected_keys:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "source cannot be both selected and unselected", identity
                )
        admission_cutoff = explanation_cutoff or decision_cutoff
        retrospective: list[SourceVersionRef] = []
        if admission_cutoff is not None:
            for item in request.selected:
                available_at = _ensure_visible_at(item, admission_cutoff, identity)
                if decision_cutoff is not None and explanation_cutoff is not None and available_at >= decision_cutoff:
                    # I46：决策界之后才披露 → 可作事后解释资料，但必须显式标注。
                    retrospective.append(
                        SourceVersionRef(
                            source_kind=item.source.source_kind,
                            source_id=item.source.source_id,
                            source_version=item.source.version,
                            source_digest=item.source.digest,
                        )
                    )
        receipt = await self._dao.register_business_sources(
            BusinessSourceRegistrationRequest(
                user_scope=request.user_scope,
                consumer_kind=request.consumer_kind,
                consumer_id=request.consumer_id,
                consumer_version=request.consumer_version,
                items=request.selected,
            )
        )
        if not retrospective:
            return receipt
        return replace(receipt, retrospective=tuple(retrospective))


def _ensure_visible_at(item: BusinessSourceItem, cutoff: datetime, identity: str) -> datetime:
    """截止界准入：可得时点未知或恰在/晚于截止（排他）时拒绝；返回可得时点。"""

    available_at = _parse_timestamp(item.source.available_at, identity)
    if available_at is None:
        # 时点未知不能按「按时披露」放行（R21），单独给出可审计原因。
        raise AgentServiceError(
            AgentErrorCode.POLICY_DENIED,
            "source availability time unknown at decision cutoff",
            f"{identity}/{item.source.source_id}@{item.source.version}",
        )
    if available_at >= cutoff:
        raise AgentServiceError(
            AgentErrorCode.POLICY_DENIED,
            "source disclosed at or after the decision cutoff",
            f"{identity}/{item.source.source_id}@{item.source.version}",
        )
    return available_at


def _cutoff_bounds(request: SourceRegistrationRequest, identity: str) -> tuple[datetime | None, datetime | None]:
    """返回 (事件决策界, 解释资料可得界)；解释界仅事后解释用途启用（I46）。"""

    if request.purpose not in _CUTOFF_PURPOSES:
        # 无截止界的用途（概念问答）：时点仅记录（由受理 manifest 承载），不参与来源准入。
        _parse_timestamp(request.decision_cutoff, identity)
        _parse_timestamp(request.explanation_cutoff, identity)
        return None, None
    if request.decision_cutoff is None or request.decision_cutoff == "":
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "decision_cutoff is required for this purpose", identity)
    decision_cutoff = _parse_timestamp(request.decision_cutoff, identity)
    assert decision_cutoff is not None
    if request.purpose is not Purpose.POST_HOC_EXPLANATION:
        if request.explanation_cutoff not in (None, ""):
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "explanation_cutoff only applies to post_hoc_explanation",
                identity,
            )
        return decision_cutoff, None
    explanation_cutoff = (
        _parse_timestamp(request.explanation_cutoff, identity)
        if request.explanation_cutoff not in (None, "")
        else decision_cutoff
    )
    assert explanation_cutoff is not None
    if explanation_cutoff < decision_cutoff:
        raise AgentServiceError(
            AgentErrorCode.INPUT_INVALID, "explanation_cutoff must not precede decision_cutoff", identity
        )
    return decision_cutoff, explanation_cutoff


def _parse_timestamp(value: str | None, identity: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp must be non-empty text", identity)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp is not ISO-8601", identity) from exc
    if parsed.tzinfo is None:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "timestamp must carry a timezone", identity)
    return parsed
