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
- ``save_draft`` 只做本地草稿 CAS 持久（不产生派发/费用），随 F-03 交付；
- ``change_session`` 只做会话元数据 CAS（create/rename/pin/archive/unarchive）与
  修订/分支的因果前缀保存（revise 改 ``causal_head_ref``；branch 新建会话并引用
  原消息身份/版本/截点，不复制正文、不派发）；
- ``transfer_draft`` / ``read_draft_transfer`` 只做草稿跨会话移入与原阶段查回
  （不发送、不派发研究、零费用）。

``aclose`` 只标记服务关闭并拒绝后续读取；DAO/Engine 生命周期归应用组合根
（DD §10.6：宿主关闭由 Runtime 前置门闩编排，服务不越权释放共享引擎）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ArtifactRead,
    ArtifactReadQuery,
    CommandReceipt,
    CommandRef,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferReceipt,
    EventPage,
    EventReadQuery,
    OperationRead,
    OperationReadQuery,
    ResearchEvent,
    ResearchSnapshot,
    SessionRead,
    SessionReadQuery,
    SessionChangeCommand,
    SourceRead,
    SourceReadQuery,
    SubmissionReceipt,
    derive_accept_identity,
    validate_draft_save_command,
    validate_draft_transfer_command,
    validate_session_change_command,
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

    async def read_source(self, query: SourceReadQuery, *, user_scope: str) -> SourceRead:
        """精确资料卡/依据详情（§4.2）：只读已准备好的选定表示。

        未准备表示由 DAO 返回 ``read_state`` 与原因（不现场解析、不扩大范围）；
        版本/摘要/定位不符为 ``REVISION_CONFLICT``，坏事实为 ``OPERATION_UNKNOWN``。
        """

        self._require_open("read_source")
        return await self._dao.read_source(query, user_scope=user_scope)

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead:
        """完整成果/历史/多模型单路（§4.2）：准确版本，正文按游标分块传输。

        正文未保存与已清理分别给出明确原因（不空正文冒充成果）；身份不符为
        ``INPUT_INVALID``，坏行/坏载荷为 ``OPERATION_UNKNOWN``/``PERSISTENCE_UNAVAILABLE``。
        """

        self._require_open("read_artifact")
        return await self._dao.read_artifact(query, user_scope=user_scope)

    async def read_operation(self, query: OperationReadQuery, *, user_scope: str) -> OperationRead:
        """公开过程/完整工具返回（§4.2）：调用元数据与 effect/io 事实单列。

        ``unknown`` 原样返回且不给结果（不猜成功/失败）；大结果按 ``result_ref``
        正文游标分页；身份不符为 ``INPUT_INVALID``。
        """

        self._require_open("read_operation")
        return await self._dao.read_operation(query, user_scope=user_scope)

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
    # 写入面（受理）
    # ------------------------------------------------------------------

    async def submit(self, request: AcceptRequest, *, command: CommandRef) -> SubmissionReceipt:
        """受理入口（§4.1/§5.1/INV-01）：可信命令引用必须与受理输入逐字段一致。

        本方法只提交**受理事务**并返回原回执：``commit_state`` 描述保存事实，
        不代表已投递或已消费（INV-06）。原生 root workflow 派发由 Runtime 按回执
        的 ``workflow_id``/``execution_id`` 执行（launcher 依赖 E-04 计划），回执
        ``next_action`` 已给出「待派发后按 read_command 查投递事实」的下一步。

        拒绝语义：命令引用与受理输入任一字段不一致 → ``INPUT_INVALID``（不以
        调用方声称的幂等键覆盖真实身份）；受理输入本身不合法 → 由
        ``derive_accept_identity`` 的校验转为 ``INPUT_INVALID``（类型错不吞）。
        """

        self._require_open("submit")
        identity = f"{request.user_scope}/{request.session_id}"
        try:
            # 只借校验与稳定身份派生（§3.3.2）；持久 PK 仍由 DAO 事务内再派生一次。
            derive_accept_identity(request)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        mismatches = [
            name
            for name, ref_value, request_value in (
                ("user_scope", command.user_scope, request.user_scope),
                ("source_module", command.source_module, request.source_module),
                ("business_request_id", command.business_request_id, request.business_request_id),
                ("command_id", command.command_id, request.client_command_id),
                ("expected_revision", command.expected_revision, request.expected_revision),
            )
            if ref_value != request_value
        ]
        if mismatches:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"command ref does not match request: {','.join(mismatches)}",
                identity,
            )
        return await self._dao.accept_request(request)

    async def save_draft(self, command: DraftSaveCommand) -> DraftReceipt:
        """本地自动草稿保存（§4.2 ``save_draft``）：可靠 draft revision，不发送。

        命令在边界处整份校验（全键/标签/集合/基线摘要/禁凭据），失败为
        ``INPUT_INVALID``，不触达 DAO；CAS 冲突、同键异输入与重放语义由 DAO
        事务裁决（``REVISION_CONFLICT`` / ``INPUT_CONFLICT`` / 查回原回执）。
        """

        self._require_open("save_draft")
        try:
            validate_draft_save_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.session_id}",
            ) from exc
        return await self._dao.save_draft(command)

    async def change_session(self, command: SessionChangeCommand) -> CommandReceipt:
        """会话元数据命令（§4.2 ``change_session``）：元数据五动作 + 修订/分支。

        边界整份校验（动作自身字段、预期版与命令引用一致、源消息身份/版本）；
        ``revise`` 只保存新因果前缀，``branch`` 只新建会话并引用原前缀，均不
        复制正文、不派发；CAS/重放/冲突由 DAO 事务裁决。
        """

        self._require_open("change_session")
        try:
            validate_session_change_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.session_id}",
            ) from exc
        return await self._dao.change_session(command)

    async def transfer_draft(self, command: DraftTransferCommand) -> DraftTransferReceipt:
        """草稿移入（§4.2 ``transfer_draft``）：唯一可发送归属，不发送。

        边界整份校验（双方身份/预期版、源类别约束、payload 摘要、确认位），失败
        为 ``INPUT_INVALID`` 且不触达 DAO；双方 CAS、源 claim 仲裁与源清空由 DAO
        单事务裁决（``REVISION_CONFLICT`` / ``INPUT_CONFLICT``）。
        """

        self._require_open("transfer_draft")
        try:
            validate_draft_transfer_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.target_session_id}",
            ) from exc
        return await self._dao.transfer_draft(command)

    async def read_draft_transfer(self, ref: CommandRef, *, user_scope: str) -> DraftTransferReceipt:
        """只查原转移回执（§4.2 ``read_draft_transfer``）：零移动、零发送。

        读取失败由 DAO 抛类型化错误（不存在/歧义/非转移命令 → ``INPUT_INVALID``；
        坏回执 → ``OPERATION_UNKNOWN``），服务不把失败读成「未转移」。
        """

        self._require_open("read_draft_transfer")
        return await self._dao.read_draft_transfer(ref, user_scope=user_scope)

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
