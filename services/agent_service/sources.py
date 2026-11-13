"""业务来源登记服务（E-01）：时点（PIT）与显式选择集合门，不复制 DAO 事务。

- 历史决策/事后解释用途：来源可得时点必须已知且不晚于决策 cutoff，否则
  ``POLICY_DENIED``，且不触达数据库（零登记/零使用，R24）；
- 仅显式选择集合进入登记；未选集合只用于冲突校验，绝不落库（D2）；
- 空选择直接返回空回执（概念问答可无来源）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    BusinessSourceRegistrationReceipt,
    BusinessSourceRegistrationRequest,
    Purpose,
    SourceRegistrationRequest,
)
from data.persistence.daos.agent_dao import AgentDao

#: 需要时点门（cutoff 前可得）的用途。
_PIT_PURPOSES = frozenset({Purpose.HISTORICAL_DECISION, Purpose.POST_HOC_EXPLANATION})


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
        cutoff = _decision_cutoff(request, identity)
        selected_keys = {(item.source.source_id, item.source.version) for item in request.selected}
        if len(selected_keys) != len(request.selected):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "duplicate selected source", identity)
        for item in request.unselected:
            if (item.source.source_id, item.source.version) in selected_keys:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "source cannot be both selected and unselected", identity
                )
        if cutoff is not None:
            for item in request.selected:
                available_at = _parse_timestamp(item.source.available_at, identity)
                if available_at is None or available_at > cutoff:
                    raise AgentServiceError(
                        AgentErrorCode.POLICY_DENIED,
                        "source not available at decision cutoff",
                        f"{identity}/{item.source.source_id}@{item.source.version}",
                    )
        return await self._dao.register_business_sources(
            BusinessSourceRegistrationRequest(
                user_scope=request.user_scope,
                consumer_kind=request.consumer_kind,
                consumer_id=request.consumer_id,
                consumer_version=request.consumer_version,
                items=request.selected,
            )
        )


def _decision_cutoff(request: SourceRegistrationRequest, identity: str) -> datetime | None:
    if request.purpose in _PIT_PURPOSES:
        if request.decision_cutoff is None or request.decision_cutoff == "":
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "decision_cutoff is required for this purpose", identity
            )
        cutoff = _parse_timestamp(request.decision_cutoff, identity)
        assert cutoff is not None
        return cutoff
    # 非时点用途：cutoff 仅记录（由受理 manifest 承载），不参与来源准入。
    _parse_timestamp(request.decision_cutoff, identity)
    return None


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
