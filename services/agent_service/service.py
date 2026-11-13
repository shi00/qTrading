"""Agent 统一服务入口（DD §4.1/§4.2）。

本模块只做**事实读写的服务边界**：所有持久事实由 ``AgentDao`` 在显式事务内读写，
服务层不另建执行推进器、不缓存可写状态。

当前分片交付只读查询面与 ``aclose``（F-01 前置）：

- ``read_command`` 查命令保存/投递/消费原事实（§4.2，不生成新决定）；
- ``read_session`` / ``get_snapshot`` / ``read_events`` / ``observe_session`` 为
  §4.1/§4.2 读取入口，直接复用 C-03 一致读取截点与观察实现；
- ``submit`` / ``control`` / ``answer`` / ``search`` / ``deliver_artifact`` 等写路径
  依赖原生 root workflow 派发（E-04 计划 + DBOS launch，当前受 Q5 阻塞），
  未就绪前不提供占位实现，避免把「未派发」伪装成「已受理」。

``aclose`` 只标记服务关闭并拒绝后续读取；DAO/Engine 生命周期归应用组合根
（DD §10.6：宿主关闭由 Runtime 前置门闩编排，服务不越权释放共享引擎）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CommandReceipt,
    CommandRef,
    EventPage,
    EventReadQuery,
    ResearchEvent,
    ResearchSnapshot,
    SessionRead,
    SessionReadQuery,
)
from data.persistence.daos.agent_dao import AgentDao


class AgentService:
    """统一受理/查询 facade（DD §4.1）。构造后由应用组合根持有同一实例。"""

    def __init__(self, dao: AgentDao) -> None:
        self._dao = dao
        self._closed = False

    # ------------------------------------------------------------------
    # 读取面（§4.1 get_snapshot / §4.2 read_* / observe）
    # ------------------------------------------------------------------

    async def read_command(self, ref: CommandRef, *, user_scope: str) -> CommandReceipt:
        """查命令原回执（保存/投递/消费分列）；未知时不猜、不重发。"""

        self._require_open("read_command")
        return await self._dao.read_command(ref, user_scope=user_scope)

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead:
        """主会话/目录首屏（§4.2）：多执行摘要与首屏分页，不复制成果正文。"""

        self._require_open("read_session")
        return await self._dao.get_session_read(query, user_scope=user_scope)

    async def get_snapshot(
        self,
        execution_id: str,
        *,
        user_scope: str,
        limit: int = 50,
        round_id: str | None = None,
        message_cursor: str | None = None,
        artifact_cursor: str | None = None,
    ) -> ResearchSnapshot:
        """执行快照（§4.3）：同一可靠截点的水位/投影/分页。"""

        self._require_open("get_snapshot")
        return await self._dao.get_execution_snapshot(
            execution_id,
            user_scope=user_scope,
            limit=limit,
            round_id=round_id,
            message_cursor=message_cursor,
            artifact_cursor=artifact_cursor,
        )

    async def read_events(self, query: EventReadQuery) -> EventPage:
        """可靠事件增量（§4.4）：用户域与水位由 query 承载。"""

        self._require_open("read_events")
        return await self._dao.read_events(query)

    def observe_session(
        self,
        session_id: str,
        *,
        user_scope: str,
        after_seq: int = 0,
        poll_interval_s: float = 0.25,
    ) -> AsyncIterator[ResearchEvent]:
        """会话可靠事件订阅（§4.2 observe_session）：退订只关本观察者，不控制执行。"""

        self._require_open("observe_session")
        return self._dao.observe_session_events(
            session_id,
            user_scope=user_scope,
            after_seq=after_seq,
            poll_interval_s=poll_interval_s,
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """关闭服务入口（幂等）：只停止本 facade，不释放共享 DAO/Engine。"""

        self._closed = True

    def _require_open(self, operation: str) -> None:
        if self._closed:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                f"agent service is closed ({operation})",
                "agent_service",
            )
