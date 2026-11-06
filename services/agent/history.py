"""Agent 跨会话历史服务（设计正本 §5.7）。

只复用 AgentService 的读取失败转译与参数边界；搜索/定位直接走 DAO，
不建立第二套读模型，也不读取未提交事件边界之外的 message revision。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from core.agent_types import (
    AgentOperationError,
    HistoryHit,
    HistoryQuery,
    MessageWindow,
    Page,
)
from services.agent.service import AgentService, _problem

_MAX_TEXT = 500


class AgentHistoryDaoProtocol(Protocol):
    async def read_messages(
        self,
        domain: str,
        *,
        conversation_id: UUID | None,
        execution_id: UUID | None,
        attempt_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> MessageWindow | None: ...

    async def search(self, domain: str, query: HistoryQuery, *, cursor: str | None, limit: int) -> Page[HistoryHit]: ...

    async def locate(self, domain: str, message_id: UUID, revision: int) -> MessageWindow | None: ...


class AgentHistoryService(AgentService):
    """在可信 domain 内提供 read_messages/search/locate。"""

    def __init__(self, *, domain: str, dao: AgentHistoryDaoProtocol) -> None:  # type: ignore[override]
        super().__init__(domain=domain, dao=dao)  # type: ignore[arg-type]
        self._history_dao = dao

    async def search(self, query: HistoryQuery, *, cursor: str | None = None, limit: int = 50) -> Page[HistoryHit]:
        if not 1 <= limit <= 200:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("limit", "invalid_value"),)))
        if len(query.text) > _MAX_TEXT:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("text", "too_long"),)))
        if query.fields not in ("title", "content", "both"):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("fields", "invalid_value"),)))
        if query.task_scope not in ("all", "main", "child"):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("task_scope", "invalid_value"),)))
        return await self._read(lambda: self._history_dao.search(self._domain, query, cursor=cursor, limit=limit))

    async def locate(self, message_id: UUID, revision: int) -> MessageWindow:
        if revision < 1:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("revision", "invalid_value"),)))
        window = await self._read(lambda: self._history_dao.locate(self._domain, message_id, revision))
        if window is None:
            raise AgentOperationError(_problem("not_found", related_id=message_id))
        return window

    async def find_exact(self, message_id: UUID, revision: int) -> MessageWindow | None:
        """UI 锚点恢复用：允许明确区分 not_found，不伪装成读取失败。"""
        return await self._read(lambda: self._history_dao.locate(self._domain, message_id, revision))
