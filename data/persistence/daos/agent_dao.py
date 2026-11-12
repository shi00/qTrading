"""Agent 应用表事务与查询 DAO（Agent 详细设计 §9.13；任务 C-01 注册）。

C-01 只交付注册面（CacheManager 注册，R13）与本契约基线；DAO 事务方法族
（accept_request/revise_queued、bind_actual_input、append_public_fact/save_artifact、
save_answer/mark_answer_delivered/consume_answer、claim_tool_dispatch/revoke_tool_grant、
begin_operation/save_operation_result/mark_unknown、claim_resource/release_resource、
get_session_read/get_execution_snapshot/list_events_after、
get_source_version/get_artifact_version/get_operation_return、search_history、
transfer_draft/read_draft_transfer、stop_source_use/list_source_consumers、
save_draft/change_session/save_read_mark_max、prepare_cleanup/verify_cleanup/
save_delivery_phase）按 §13 任务台账于 C-02（intent/队列/actual_input/命令/
精确许可事务）、C-03（可靠事件/快照/完整正文/来源查询/全文索引）、
C-04（资源/计数/源禁用/fence/阅读批次）分片实现。

契约基线（§9.13，任何新增方法必须遵守）：

- 每个新方法先 ``_check_engine``（继承自 BaseDao）；disposed 传播
  ``EngineDisposedError``（R5）；``asyncio.CancelledError`` 继续 raise（R2）；
- 数据库失败明确分类（classify_error/classify_severity），不返回空成功；
- 受理/派发/决定/序号等多行原子协议用单个显式事务，不得以会分块提交的
  ``_save_upsert`` 批量 helper 代替（§9.13）；
- 水位/计数写入单调（GREATEST / ``*_max`` 语义，R22）；
- 所有读写同时校验 ``user_scope``，不能只核 ID（§9.1.6）。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AnswerReceipt,
    BindActualInputReceipt,
    BindActualInputRequest,
    CommandReceipt,
    CommandRef,
    CommitState,
    ConsumeAnswerRequest,
    ConsumptionFact,
    ConsumptionState,
    DataAttemptAuthorization,
    DataConsentBinding,
    DataConsentClaim,
    DataConsentClaimFact,
    DataConsentCommand,
    DataConsentRevokeCommand,
    DataConsentState,
    DeliveryFact,
    DeliveryState,
    EventPayloadKind,
    GrantRevisionRequest,
    MarkAnswerDeliveredRequest,
    ReviseQueuedRequest,
    RevisionReceipt,
    SaveAnswerRequest,
    SourceRelationKind,
    SubmissionReceipt,
    ToolGrantFact,
    ToolGrantState,
    decode_data_consent_binding,
    decode_command_receipt,
    decode_submission_receipt,
    encode_command_receipt,
    encode_data_consent_binding,
    encode_data_consent_claim,
    encode_submission_receipt,
    derive_accept_identity,
    derive_data_consent_command_identity,
    derive_data_consent_revoke_identity,
    evaluate_data_attempt,
    json_doc,
    sha256_hex,
    stable_id,
    validate_data_attempt_authorization,
    validate_data_consent_claim,
)
from data.persistence.daos.base_dao import BaseDao
from data.persistence.models import (
    AgentAssetVersion,
    AgentCommand,
    AgentDecision,
    AgentExecution,
    AgentMessage,
    AgentOperation,
    AgentRound,
    AgentSession,
    AgentSourceLink,
)


def _utc_now_wire() -> str:
    """UTC 微秒 Z 时刻文本（§3.3.1 T 格式）。"""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _identity_str(request: AcceptRequest) -> str:
    return f"{request.user_scope}/{request.source_module}/{request.business_request_id}"


def _require_text_field(value: object, field: str, identity: str) -> str:
    """事务入口校验：非空文本（§9.13「数据库失败明确分类」，入参失败 INPUT_INVALID）。"""

    if not isinstance(value, str) or value == "":
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{field} must be non-empty text", identity)
    return value


def _require_int_field(value: object, field: str, identity: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{field} must be int >= {minimum}", identity)
    return value


def _wire_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _wire_to_dt(value: str | None) -> datetime | None:
    """UTC 微秒 Z 时刻文本 → 带时区 datetime（TIMESTAMPTZ 列写入；None 透传）。"""

    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class AgentDao(BaseDao):
    """Agent 应用表 DAO（CacheManager.agent_dao）。

    引擎经构造注入（BaseDao.__init__）， disposed 守卫与维护门闩由
    BaseDao 基线统一提供；C-02+ 事务方法按上方契约基线逐步落地。
    """

    async def accept_request(self, request: AcceptRequest) -> SubmissionReceipt:
        """受理事务（SQ-01/§5.3/§3.3.2）：intent 查重/冲突 + execution + command
        + user message + 源资产 + 初始谱系边 + 受理回执，**单个显式事务**。

        幂等与冲突（INV-01/§3.3.2）：

        - 同 intent（user_scope+source_module+business_request_id）同冻结输入
          （intent_digest 相等）→ 重传：查回并返回原回执，零新事实；
        - 同 intent 异输入 → ``AgentServiceError(INPUT_CONFLICT)``，零新事实；
        - 同命令键（target+client_command_id）同输入 → 返回原回执；
        - 同命令键异输入 → ``INPUT_CONFLICT``。

        序号分配持目标 session 行锁（§5.4.4）；session.event_seq 以 GREATEST
        单调推进（R22）。occurred_at/committed_at 用受理时刻（同事务内一致）。

        Raises:
            AgentServiceError: INPUT_INVALID（目标会话不存在）/ INPUT_CONFLICT
                （同身份异输入或同命令键异输入）/ OPERATION_UNKNOWN（原回执
                不可得）。
        """

        self._check_engine()
        identity = derive_accept_identity(request)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. 目标会话存在性 + 行锁（序号分配点，§5.4.4）。
            session_row = (
                await conn.execute(
                    sa.select(AgentSession.session_id)
                    .where(
                        AgentSession.user_scope == request.user_scope,
                        AgentSession.session_id == request.session_id,
                    )
                    .with_for_update()
                )
            ).first()
            if session_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "target session does not exist in user scope",
                    f"{request.user_scope}/{request.session_id}",
                )

            # 2. intent 查重（INV-01：同身份异输入报冲突）。
            existing_execution = (
                await conn.execute(
                    sa.select(AgentExecution.execution_id, AgentExecution.intent_digest).where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.source_module == request.source_module,
                        AgentExecution.business_request_id == request.business_request_id,
                    )
                )
            ).first()
            if existing_execution is not None:
                if existing_execution.intent_digest == identity.intent_digest:
                    return await self._replay_original_receipt(conn, request, existing_execution.execution_id)
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same intent with different frozen input",
                    _identity_str(request),
                )

            # 3. 命令键查重（§3.3.2：相同目标/键重传取原记录；同命令异输入冲突）。
            existing_command = (
                await conn.execute(
                    sa.select(AgentCommand.execution_id, AgentCommand.input_digest).where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.target_id == request.session_id,
                        AgentCommand.client_command_id == request.client_command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if existing_command.input_digest == identity.source_digest:
                    return await self._replay_original_receipt(conn, request, existing_command.execution_id)
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same command key with different input",
                    f"{request.user_scope}/{request.session_id}/{request.client_command_id}",
                )

            # 4. 序号分配（持锁下 max+1；首条消息即首个执行序）。
            session_seq = (
                (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentMessage.session_seq), -1)).where(
                            AgentMessage.user_scope == request.user_scope,
                            AgentMessage.session_id == request.session_id,
                        )
                    )
                ).scalar_one()
            ) + 1
            execution_id = stable_id(
                "execution",
                [request.user_scope, request.session_id, request.source_module, request.business_request_id],
            )
            event_id = stable_id("event", [request.user_scope, request.session_id, identity.message_id])
            occurred_at = datetime.now(UTC)
            occurred_wire = occurred_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            # 5. execution（root；显示态 queued / 目标覆盖 unknown 初始值，§5.1）。
            await conn.execute(
                sa.insert(AgentExecution).values(
                    user_scope=request.user_scope,
                    execution_id=execution_id,
                    session_id=request.session_id,
                    execution_kind="root",
                    source_module=request.source_module,
                    business_request_id=request.business_request_id,
                    node_id=request.node_id,
                    display_state="queued",
                    goal_coverage="unknown",
                    intent_digest=identity.intent_digest,
                )
            )

            # 6. command（保存=committed，待投递=not_delivered，未消费，§9.8）。
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=request.user_scope,
                    command_record_id=identity.command_record_id,
                    client_command_id=request.client_command_id,
                    target_id=request.session_id,
                    command_kind="submit",
                    session_id=request.session_id,
                    execution_id=execution_id,
                    expected_revision=request.expected_revision,
                    input_revision=1,
                    input_digest=identity.source_digest,
                    request_payload=json.loads(request.manifest_payload),
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                )
            )

            # 7. user message M1（§3.3.2：正文即源，不可变 message_version="1"）。
            #    受理先于首次派发、尚无 round：M1 为纯 session 事件（execution/round
            #    为 NULL，§9.6 CHECK），与受理 execution 的关联由第 9 步源谱系边
            #    （consumer_kind=execution, input_revision）承载。
            await conn.execute(
                sa.insert(AgentMessage).values(
                    user_scope=request.user_scope,
                    message_id=identity.message_id,
                    session_id=request.session_id,
                    execution_id=None,
                    round_id=None,
                    node_id=None,
                    event_id=event_id,
                    session_seq=session_seq,
                    execution_seq=None,
                    event_kind="public_message_saved",
                    payload_kind=EventPayloadKind.PUBLIC_MESSAGE.value,
                    subject_id=identity.message_id,
                    subject_revision=1,
                    role="user",
                    body_text=request.text,
                    payload={
                        "schema_version": 1,
                        "message_id": identity.message_id,
                        "role": "user",
                        "source_id": identity.source_id,
                        "source_version": identity.source_version,
                    },
                    occurred_at=occurred_at,
                    committed_at=occurred_at,
                )
            )

            # 8. 源资产版本（§3.3.2：asset_kind=source + user_message_origin 元数据；
            #    正文仍由 message 行拥有，asset 不复制正文）。
            await conn.execute(
                sa.insert(AgentAssetVersion).values(
                    user_scope=request.user_scope,
                    asset_id=identity.source_id,
                    asset_version=identity.source_version,
                    asset_kind="source",
                    display_name=request.source_display_name,
                    digest=identity.source_digest,
                    origin_ref=None,
                    metadata_payload={
                        "schema_version": 1,
                        "user_message_origin": {
                            "session_id": request.session_id,
                            "message_id": identity.message_id,
                            "message_version": "1",
                        },
                    },
                    read_state="ready",
                    verification_state="program_verified",
                    available_at=occurred_at,
                )
            )

            # 9. 初始谱系边（§3.3.2：源→message v1 + 源→受理 execution 输入版）。
            for consumer_kind, consumer_id, consumer_version, relation in (
                ("message", identity.message_id, "1", SourceRelationKind.ORIGIN),
                ("execution", execution_id, "1", SourceRelationKind.ACCEPTED_INPUT),
            ):
                await conn.execute(
                    sa.insert(AgentSourceLink).values(
                        user_scope=request.user_scope,
                        link_id=stable_id(
                            "source_link",
                            [request.user_scope, identity.source_id, "1", consumer_kind, consumer_id, consumer_version],
                        ),
                        source_id=identity.source_id,
                        source_version=identity.source_version,
                        consumer_kind=consumer_kind,
                        consumer_id=consumer_id,
                        consumer_version=consumer_version,
                        message_id=identity.message_id if consumer_kind == "message" else None,
                        execution_id=execution_id if consumer_kind == "execution" else None,
                        relation_kind=relation.value,
                    )
                )

            # 10. 受理回执（同事务写回 command.receipt_payload；重传查回原回执）。
            receipt = SubmissionReceipt(
                command=CommandRef(
                    user_scope=request.user_scope,
                    source_module=request.source_module,
                    business_request_id=request.business_request_id,
                    command_id=request.client_command_id,
                    expected_revision=request.expected_revision,
                ),
                command_record_id=identity.command_record_id,
                session_id=request.session_id,
                execution_id=execution_id,
                workflow_id=request.workflow_id,
                intent_digest=identity.intent_digest,
                commit_state=CommitState.COMMITTED,
                verified_at=occurred_wire,
                next_action="await workflow dispatch; query read_command for delivery facts",
            )
            await conn.execute(
                sa.update(AgentCommand)
                .where(
                    AgentCommand.user_scope == request.user_scope,
                    AgentCommand.command_record_id == identity.command_record_id,
                )
                .values(receipt_payload=encode_submission_receipt(receipt))
            )

            # 11. session 可靠事件水位单调推进（R22）。
            await conn.execute(
                sa.update(AgentSession)
                .where(
                    AgentSession.user_scope == request.user_scope,
                    AgentSession.session_id == request.session_id,
                )
                .values(event_seq=sa.func.greatest(AgentSession.event_seq, session_seq))
            )
            return receipt

    async def _replay_original_receipt(
        self, conn: AsyncConnection, request: AcceptRequest, original_execution_id: str
    ) -> SubmissionReceipt:
        """重传：查回并返回原回执（§3.3.2「重传只查回，不新建研究」）。"""

        row = (
            await conn.execute(
                sa.select(AgentCommand.receipt_payload)
                .where(
                    AgentCommand.user_scope == request.user_scope,
                    AgentCommand.execution_id == original_execution_id,
                    AgentCommand.command_kind == "submit",
                )
                .order_by(AgentCommand.created_at.asc(), AgentCommand.command_record_id.asc())
                .limit(1)
            )
        ).first()
        if row is None or row.receipt_payload is None:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "original submission receipt unavailable",
                _identity_str(request),
            )
        return decode_submission_receipt(row.receipt_payload)

    # ------------------------------------------------------------------
    # C-02-3：首派发绑定 / 答复生命周期 / 精确许可 CAS / 队列修订
    # ------------------------------------------------------------------

    async def bind_actual_input(self, request: BindActualInputRequest) -> BindActualInputReceipt:
        """首派发实际输入绑定（§9.13/SQ-02 W 首步）：建 agent_round +
        execution.current_round_id + input_bound 执行事件，**单个显式事务**。

        幂等（§9.5 workflow_id UNIQUE）：同 workflow_id 同双摘要 → 查回原事实
        零新行；同 workflow_id 异输入 → ``INPUT_CONFLICT``。同 execution 已有
        有效轮（active_round）→ ``INPUT_CONFLICT``（§9.5 部分唯一防双有效轮）。

        锁序（§5.4 第 4 条）：先持 execution 行锁，再锁其 session 分配序号；
        session/execution 事件水位均 GREATEST 单调推进（R22）。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.execution_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.session_id, "session_id", identity)
        _require_text_field(request.execution_id, "execution_id", identity)
        _require_text_field(request.node_id, "node_id", identity)
        _require_text_field(request.workflow_id, "workflow_id", identity)
        _require_text_field(request.workflow_code_version, "workflow_code_version", identity)
        _require_int_field(request.run_epoch, "run_epoch", identity, 0)
        _require_text_field(request.frozen_manifest, "frozen_manifest", identity)
        _require_text_field(request.actual_input, "actual_input", identity)
        frozen_digest = sha256_hex(request.frozen_manifest)
        actual_digest = sha256_hex(request.actual_input)
        round_id = stable_id("round", [request.user_scope, request.execution_id, request.workflow_id])
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. execution 行锁（§5.4 第 4 条：先持目标 execution）。
            exec_row = (
                await conn.execute(
                    sa.select(AgentExecution.session_id)
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id == request.execution_id,
                    )
                    .with_for_update()
                )
            ).first()
            if exec_row is None or exec_row.session_id != request.session_id:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "execution does not exist in user scope/session",
                    identity,
                )
            # 2. session 行锁（序号分配点）。
            await conn.execute(
                sa.select(AgentSession.session_id)
                .where(
                    AgentSession.user_scope == request.user_scope,
                    AgentSession.session_id == request.session_id,
                )
                .with_for_update()
            )
            # 3. workflow 幂等重放（§9.5 UNIQUE(workflow_id)）。
            existing_round = (
                await conn.execute(
                    sa.select(
                        AgentRound.round_id,
                        AgentRound.round_number,
                        AgentRound.input_revision,
                        AgentRound.frozen_digest,
                        AgentRound.actual_digest,
                    ).where(
                        AgentRound.user_scope == request.user_scope,
                        AgentRound.workflow_id == request.workflow_id,
                    )
                )
            ).first()
            if existing_round is not None:
                if existing_round.frozen_digest == frozen_digest and existing_round.actual_digest == actual_digest:
                    event_row = (
                        await conn.execute(
                            sa.select(AgentMessage.execution_seq, AgentMessage.session_seq)
                            .where(
                                AgentMessage.user_scope == request.user_scope,
                                AgentMessage.round_id == existing_round.round_id,
                                AgentMessage.event_kind == "input_bound",
                            )
                            .limit(1)
                        )
                    ).first()
                    if event_row is None or event_row.execution_seq is None:
                        raise AgentServiceError(
                            AgentErrorCode.OPERATION_UNKNOWN,
                            "original input_bound event unavailable",
                            identity,
                        )
                    return BindActualInputReceipt(
                        round_id=existing_round.round_id,
                        round_number=existing_round.round_number,
                        input_revision=existing_round.input_revision,
                        frozen_digest=existing_round.frozen_digest,
                        actual_digest=existing_round.actual_digest,
                        execution_seq=event_row.execution_seq,
                        session_seq=event_row.session_seq,
                    )
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "workflow already bound with different input",
                    identity,
                )
            # 4. 单有效轮（§9.5：同 execution 仅一有效运行轮）。
            active_round = (
                await conn.execute(
                    sa.select(AgentRound.round_id).where(
                        AgentRound.user_scope == request.user_scope,
                        AgentRound.execution_id == request.execution_id,
                        AgentRound.active_round,
                    )
                )
            ).first()
            if active_round is not None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "execution already has an active round",
                    identity,
                )
            # 5. 序号分配（持锁下 max+1；执行事件双 seq 同设，§9.6 CHECK）。
            round_number = (
                (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentRound.round_number), 0)).where(
                            AgentRound.user_scope == request.user_scope,
                            AgentRound.execution_id == request.execution_id,
                        )
                    )
                ).scalar_one()
            ) + 1
            execution_seq = (
                (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentMessage.execution_seq), -1)).where(
                            AgentMessage.user_scope == request.user_scope,
                            AgentMessage.execution_id == request.execution_id,
                        )
                    )
                ).scalar_one()
            ) + 1
            session_seq = (
                (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentMessage.session_seq), -1)).where(
                            AgentMessage.user_scope == request.user_scope,
                            AgentMessage.session_id == request.session_id,
                        )
                    )
                ).scalar_one()
            ) + 1
            occurred = datetime.now(UTC)
            event_id = stable_id(
                "event", [request.user_scope, request.execution_id, round_id, request.node_id, "input_bound"]
            )

            # 6. round 行（首轮：round_number 由既有轮次派生，首派发即 1；§9.5）。
            await conn.execute(
                sa.insert(AgentRound).values(
                    user_scope=request.user_scope,
                    round_id=round_id,
                    execution_id=request.execution_id,
                    round_number=round_number,
                    workflow_id=request.workflow_id,
                    workflow_code_version=request.workflow_code_version,
                    run_epoch=request.run_epoch,
                    input_revision=1,
                    plan_revision=1,
                    frozen_manifest=json.loads(request.frozen_manifest),
                    actual_input=json.loads(request.actual_input),
                    frozen_digest=frozen_digest,
                    actual_digest=actual_digest,
                    active_round=True,
                )
            )
            # 7. execution 更新：current_round_id + 事件水位 GREATEST + revision CAS 位推进。
            await conn.execute(
                sa.update(AgentExecution)
                .where(
                    AgentExecution.user_scope == request.user_scope,
                    AgentExecution.execution_id == request.execution_id,
                )
                .values(
                    current_round_id=round_id,
                    event_seq=sa.func.greatest(AgentExecution.event_seq, execution_seq),
                    revision=AgentExecution.revision + 1,
                    updated_at=occurred,
                )
            )
            # 8. input_bound 执行事件（执行事件 execution/round/execution_seq 同设，§9.6）。
            await conn.execute(
                sa.insert(AgentMessage).values(
                    user_scope=request.user_scope,
                    message_id=event_id,
                    session_id=request.session_id,
                    execution_id=request.execution_id,
                    round_id=round_id,
                    node_id=request.node_id,
                    event_id=event_id,
                    session_seq=session_seq,
                    execution_seq=execution_seq,
                    event_kind="input_bound",
                    payload_kind=EventPayloadKind.INPUT_BINDING.value,
                    subject_id=request.execution_id,
                    subject_revision=1,
                    payload={
                        "schema_version": 1,
                        "execution_id": request.execution_id,
                        "round_id": round_id,
                        "workflow_id": request.workflow_id,
                        "workflow_code_version": request.workflow_code_version,
                        "run_epoch": request.run_epoch,
                        "frozen_digest": frozen_digest,
                        "actual_digest": actual_digest,
                        "input_revision": 1,
                    },
                    input_revision=1,
                    occurred_at=occurred,
                    committed_at=occurred,
                )
            )
            # 9. session 事件水位 GREATEST 推进（R22）。
            await conn.execute(
                sa.update(AgentSession)
                .where(
                    AgentSession.user_scope == request.user_scope,
                    AgentSession.session_id == request.session_id,
                )
                .values(event_seq=sa.func.greatest(AgentSession.event_seq, session_seq))
            )
            return BindActualInputReceipt(
                round_id=round_id,
                round_number=round_number,
                input_revision=1,
                frozen_digest=frozen_digest,
                actual_digest=actual_digest,
                execution_seq=execution_seq,
                session_seq=session_seq,
            )

    async def save_answer(self, request: SaveAnswerRequest) -> AnswerReceipt:
        """答复保存（SQ-03/§5.3 答复线性化点）：decision.answer_command_id CAS
        （NULL→set 单胜者）+ 同事务插 agent_command(kind='answer', 待投递事实)。

        幂等（§9.13）：同命令键同规范输入且决定已指向该命令 → 查回原事实零新行；
        同命令键异输入 → ``INPUT_CONFLICT``；决定已被其他命令应答或预期版不符
        → ``REVISION_CONFLICT``。锁序（§5.4 第 2 条）：decision → command。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.decision_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.decision_id, "decision_id", identity)
        _require_text_field(request.client_command_id, "client_command_id", identity)
        _require_int_field(request.expected_decision_revision, "expected_decision_revision", identity, 1)
        _require_text_field(request.answer_payload, "answer_payload", identity)
        answer_digest = sha256_hex(request.answer_payload)
        command_record_id = stable_id("command", [request.user_scope, request.decision_id, request.client_command_id])
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. decision 行锁（§5.4 第 2 条：execution → decision/command 序）。
            decision_row = (
                await conn.execute(
                    sa.select(
                        AgentDecision.session_id,
                        AgentDecision.execution_id,
                        AgentDecision.round_id,
                        AgentDecision.revision,
                        AgentDecision.answer_command_id,
                    )
                    .where(
                        AgentDecision.user_scope == request.user_scope,
                        AgentDecision.decision_id == request.decision_id,
                    )
                    .with_for_update()
                )
            ).first()
            if decision_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "decision does not exist", identity)
            # 2. 命令键查重（target = decision_id，§3.2 命令身份）。
            existing_command = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest).where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.target_id == request.decision_id,
                        AgentCommand.client_command_id == request.client_command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if existing_command.input_digest == answer_digest:
                    if decision_row.answer_command_id == command_record_id:
                        return await self._replay_answer_facts(
                            conn,
                            request,
                            command_record_id,
                            answer_digest,
                            decision_revision=decision_row.revision,
                        )
                    raise AgentServiceError(
                        AgentErrorCode.REVISION_CONFLICT,
                        "decision already answered by another command",
                        identity,
                    )
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same answer command key with different input",
                    identity,
                )
            # 3. 先插 answer 命令（answer_command_id FK 非延迟，须先建命令行）。
            occurred = datetime.now(UTC)
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=request.user_scope,
                    command_record_id=command_record_id,
                    client_command_id=request.client_command_id,
                    target_id=request.decision_id,
                    command_kind="answer",
                    session_id=decision_row.session_id,
                    execution_id=decision_row.execution_id,
                    round_id=decision_row.round_id,
                    expected_revision=request.expected_decision_revision,
                    input_revision=None,
                    input_digest=answer_digest,
                    request_payload=json.loads(request.answer_payload),
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                    verified_at=occurred,
                )
            )
            # 4. CAS：answer_command_id NULL→set + 预期版（§5.3「核预期版」）。
            cas_result = await conn.execute(
                sa.update(AgentDecision)
                .where(
                    AgentDecision.user_scope == request.user_scope,
                    AgentDecision.decision_id == request.decision_id,
                    AgentDecision.revision == request.expected_decision_revision,
                    AgentDecision.answer_command_id.is_(None),
                )
                .values(
                    answer_command_id=command_record_id,
                    revision=AgentDecision.revision + 1,
                    updated_at=occurred,
                )
            )
            if cas_result.rowcount != 1:
                raise AgentServiceError(
                    AgentErrorCode.REVISION_CONFLICT,
                    "decision revision changed or already answered",
                    identity,
                )
            return AnswerReceipt(
                decision_id=request.decision_id,
                command_record_id=command_record_id,
                decision_revision=request.expected_decision_revision + 1,
                delivery_state=DeliveryState.NOT_DELIVERED,
                consumption_state=ConsumptionState.NOT_CONSUMED,
                answer_digest=answer_digest,
                verified_at=_wire_time(occurred),
            )

    async def _replay_answer_facts(
        self,
        conn: AsyncConnection,
        request: SaveAnswerRequest,
        command_record_id: str,
        answer_digest: str,
        *,
        decision_revision: int,
    ) -> AnswerReceipt:
        """重放：查回原应答事实（§9.13 同命令同输入查回原答案/消费事实）。"""

        cmd_row = (
            await conn.execute(
                sa.select(
                    AgentCommand.delivery_state,
                    AgentCommand.consumption_state,
                    AgentCommand.verified_at,
                ).where(
                    AgentCommand.user_scope == request.user_scope,
                    AgentCommand.command_record_id == command_record_id,
                )
            )
        ).first()
        if cmd_row is None:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "original answer command unavailable",
                f"{request.user_scope}/{request.decision_id}",
            )
        verified_at = cmd_row.verified_at or datetime.now(UTC)
        return AnswerReceipt(
            decision_id=request.decision_id,
            command_record_id=command_record_id,
            decision_revision=decision_revision,
            delivery_state=DeliveryState(cmd_row.delivery_state),
            consumption_state=ConsumptionState(cmd_row.consumption_state),
            answer_digest=answer_digest,
            verified_at=_wire_time(verified_at),
        )

    async def mark_answer_delivered(self, request: MarkAnswerDeliveredRequest) -> DeliveryFact:
        """投递核定（SQ-03「同 command_id 幂等原生 send」）：delivery_state
        not_delivered→delivered CAS。已 delivered → 返回原事实；delivery_unknown
        → ``OPERATION_UNKNOWN``（不可确证已投递，不冒认）。"""

        self._check_engine()
        identity = f"{request.user_scope}/{request.command_record_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.command_record_id, "command_record_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            cmd_row = (
                await conn.execute(
                    sa.select(AgentCommand.delivery_state, AgentCommand.verified_at)
                    .where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.command_record_id == request.command_record_id,
                    )
                    .with_for_update()
                )
            ).first()
            if cmd_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command does not exist", identity)
            occurred = datetime.now(UTC)
            if cmd_row.delivery_state == DeliveryState.DELIVERED.value:
                verified_at = cmd_row.verified_at or occurred
                return DeliveryFact(
                    command_record_id=request.command_record_id,
                    delivery_state=DeliveryState.DELIVERED,
                    verified_at=_wire_time(verified_at),
                )
            if cmd_row.delivery_state != DeliveryState.NOT_DELIVERED.value:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "delivery state cannot be asserted as delivered",
                    identity,
                )
            await conn.execute(
                sa.update(AgentCommand)
                .where(
                    AgentCommand.user_scope == request.user_scope,
                    AgentCommand.command_record_id == request.command_record_id,
                    AgentCommand.delivery_state == DeliveryState.NOT_DELIVERED.value,
                )
                .values(
                    delivery_state=DeliveryState.DELIVERED.value,
                    verified_at=occurred,
                    revision=AgentCommand.revision + 1,
                    updated_at=occurred,
                )
            )
            return DeliveryFact(
                command_record_id=request.command_record_id,
                delivery_state=DeliveryState.DELIVERED,
                verified_at=_wire_time(occurred),
            )

    async def consume_answer(self, request: ConsumeAnswerRequest) -> ConsumptionFact:
        """答复消费（SQ-03/§9.13）：consumption_state not_consumed→consumed CAS +
        decision.answer_consumed_at 同事务落定；已消费返回原事实（W 恢复先查原）。

        锁序（§5.4 第 2 条）：decision（经 answer_command_id 反查）→ command。
        消费与投递是不同事实（INV-06）；消费不核投递状态。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.command_record_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.command_record_id, "command_record_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. decision 行锁（经 answer_command_id 反查，保持 decision → command 序）。
            decision_row = (
                await conn.execute(
                    sa.select(AgentDecision.decision_id, AgentDecision.answer_consumed_at)
                    .where(
                        AgentDecision.user_scope == request.user_scope,
                        AgentDecision.answer_command_id == request.command_record_id,
                    )
                    .with_for_update()
                )
            ).first()
            cmd_row = (
                await conn.execute(
                    sa.select(AgentCommand.consumption_state, AgentCommand.consumed_at)
                    .where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.command_record_id == request.command_record_id,
                    )
                    .with_for_update()
                )
            ).first()
            if decision_row is None or cmd_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "answer command does not exist in user scope",
                    identity,
                )
            occurred = datetime.now(UTC)
            if cmd_row.consumption_state == ConsumptionState.CONSUMED.value:
                # 已消费返回原事实（§9.13：不能因 revision 更新把同输入当新冲突）。
                consumed_at = cmd_row.consumed_at or occurred
                answer_consumed_at = decision_row.answer_consumed_at or consumed_at
                return ConsumptionFact(
                    command_record_id=request.command_record_id,
                    decision_id=decision_row.decision_id,
                    consumption_state=ConsumptionState.CONSUMED,
                    consumed_at=_wire_time(consumed_at),
                    answer_consumed_at=_wire_time(answer_consumed_at),
                )
            if cmd_row.consumption_state != ConsumptionState.NOT_CONSUMED.value:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "consumption state cannot be asserted as consumed",
                    identity,
                )
            await conn.execute(
                sa.update(AgentCommand)
                .where(
                    AgentCommand.user_scope == request.user_scope,
                    AgentCommand.command_record_id == request.command_record_id,
                    AgentCommand.consumption_state == ConsumptionState.NOT_CONSUMED.value,
                )
                .values(
                    consumption_state=ConsumptionState.CONSUMED.value,
                    consumed_at=occurred,
                    revision=AgentCommand.revision + 1,
                    updated_at=occurred,
                )
            )
            await conn.execute(
                sa.update(AgentDecision)
                .where(
                    AgentDecision.user_scope == request.user_scope,
                    AgentDecision.decision_id == decision_row.decision_id,
                    AgentDecision.answer_consumed_at.is_(None),
                )
                .values(answer_consumed_at=occurred, updated_at=occurred)
            )
            return ConsumptionFact(
                command_record_id=request.command_record_id,
                decision_id=decision_row.decision_id,
                consumption_state=ConsumptionState.CONSUMED,
                consumed_at=_wire_time(occurred),
                answer_consumed_at=_wire_time(occurred),
            )

    async def claim_tool_dispatch(self, request: GrantRevisionRequest) -> ToolGrantFact:
        """派发认领（§5.4 第 6 条/SQ-03）：grant_revision CAS 单胜者，
        grant_state granted→claimed。败者 ``REVISION_CONFLICT``；W 崩溃恢复同
        expected 重试且已 claimed（revision=expected+1）→ 返回原事实零新变更。
        """

        return await self._cas_grant_state(
            request,
            allowed_current_states=(ToolGrantState.GRANTED,),
            target_state=ToolGrantState.CLAIMED,
        )

    async def revoke_tool_grant(self, request: GrantRevisionRequest) -> ToolGrantFact:
        """许可撤销（§5.4 第 6 条/SQ-03）：grant_state granted|claimed→revoked
        CAS。撤销先胜则零新派发；派发先胜后撤销仍可 revoked（保真实在途，
        阻止后续 claim），不能倒写「从未发送」。"""

        return await self._cas_grant_state(
            request,
            allowed_current_states=(ToolGrantState.GRANTED, ToolGrantState.CLAIMED),
            target_state=ToolGrantState.REVOKED,
        )

    async def _cas_grant_state(
        self,
        request: GrantRevisionRequest,
        *,
        allowed_current_states: tuple[ToolGrantState, ...],
        target_state: ToolGrantState,
    ) -> ToolGrantFact:
        """grant_revision CAS 公共路径（§9.8：CAS 撤销/派发时锁该行）。"""

        self._check_engine()
        identity = f"{request.user_scope}/{request.decision_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.decision_id, "decision_id", identity)
        _require_int_field(request.expected_grant_revision, "expected_grant_revision", identity, 1)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            decision_row = (
                await conn.execute(
                    sa.select(
                        AgentDecision.grant_id,
                        AgentDecision.grant_revision,
                        AgentDecision.grant_state,
                    )
                    .where(
                        AgentDecision.user_scope == request.user_scope,
                        AgentDecision.decision_id == request.decision_id,
                    )
                    .with_for_update()
                )
            ).first()
            if decision_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "decision does not exist", identity)
            if decision_row.grant_id is None or decision_row.grant_revision is None or decision_row.grant_state is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "decision carries no tool grant",
                    identity,
                )
            occurred = datetime.now(UTC)
            current_states = [state.value for state in allowed_current_states]
            cas_result = await conn.execute(
                sa.update(AgentDecision)
                .where(
                    AgentDecision.user_scope == request.user_scope,
                    AgentDecision.decision_id == request.decision_id,
                    AgentDecision.grant_revision == request.expected_grant_revision,
                    AgentDecision.grant_state.in_(current_states),
                )
                .values(
                    grant_state=target_state.value,
                    grant_revision=AgentDecision.grant_revision + 1,
                    revision=AgentDecision.revision + 1,
                    updated_at=occurred,
                )
            )
            if cas_result.rowcount == 1:
                return ToolGrantFact(
                    decision_id=request.decision_id,
                    grant_id=decision_row.grant_id,
                    grant_revision=request.expected_grant_revision + 1,
                    grant_state=target_state,
                )
            # 重放：同 expected 重试且目标态已达成（revision=expected+1）→ 原事实。
            if (
                decision_row.grant_state == target_state.value
                and decision_row.grant_revision == request.expected_grant_revision + 1
            ):
                return ToolGrantFact(
                    decision_id=request.decision_id,
                    grant_id=decision_row.grant_id,
                    grant_revision=decision_row.grant_revision,
                    grant_state=target_state,
                )
            raise AgentServiceError(
                AgentErrorCode.REVISION_CONFLICT,
                "grant CAS lost: revision changed or state not claimable",
                identity,
            )

    async def revise_queued(self, request: ReviseQueuedRequest) -> RevisionReceipt:
        """队列修订（§9.13 revise_queued/SQ-07 排队期修订）：execution.revision
        CAS（§9.2 通用 revision 列）单胜者后建 M2/S2 新输入版 + 新源资产/两源边
        + revise 命令，保 intent/execution/队列位，**单个显式事务**。

        幂等：同命令键（target=execution_id）同正文 → 查回原事实零新行；异正文
        → ``INPUT_CONFLICT``；revision CAS 败者 → ``REVISION_CONFLICT``。
        M2 与受理消息同为纯 session 事件（先于派发无 round，§9.6 CHECK），执行
        关联由源谱系边（consumer_kind=execution, input_revision=N）承载。
        锁序（§5.4 第 4 条）：先持 execution（CAS 目标）再锁其 session。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.execution_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.session_id, "session_id", identity)
        _require_text_field(request.execution_id, "execution_id", identity)
        _require_text_field(request.client_command_id, "client_command_id", identity)
        _require_int_field(request.expected_execution_revision, "expected_execution_revision", identity, 1)
        _require_text_field(request.node_id, "node_id", identity)
        _require_text_field(request.text, "text", identity)
        _require_text_field(request.manifest_payload, "manifest_payload", identity)
        _require_text_field(request.source_display_name, "source_display_name", identity)
        source_digest = sha256_hex(request.text)
        command_record_id = stable_id("command", [request.user_scope, request.execution_id, request.client_command_id])
        message_id = stable_id("user_message", [request.user_scope, request.session_id, command_record_id])
        source_id = stable_id("message_source", [request.user_scope, request.session_id, message_id])
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. execution 行锁（CAS 目标；§5.4 第 4 条先 execution 后 session）。
            exec_row = (
                await conn.execute(
                    sa.select(AgentExecution.session_id, AgentExecution.revision)
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id == request.execution_id,
                    )
                    .with_for_update()
                )
            ).first()
            if exec_row is None or exec_row.session_id != request.session_id:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "execution does not exist in user scope/session",
                    identity,
                )
            # 2. 命令键查重（同键同正文重放零新事实；异正文冲突，§3.3.2）。
            existing_command = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest, AgentCommand.input_revision).where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.target_id == request.execution_id,
                        AgentCommand.client_command_id == request.client_command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if existing_command.input_digest == source_digest and existing_command.input_revision is not None:
                    event_row = (
                        await conn.execute(
                            sa.select(AgentMessage.session_seq).where(
                                AgentMessage.user_scope == request.user_scope,
                                AgentMessage.message_id == message_id,
                            )
                        )
                    ).first()
                    if event_row is None:
                        raise AgentServiceError(
                            AgentErrorCode.OPERATION_UNKNOWN,
                            "original revision message unavailable",
                            identity,
                        )
                    return RevisionReceipt(
                        execution_id=request.execution_id,
                        command_record_id=command_record_id,
                        message_id=message_id,
                        source_id=source_id,
                        source_version=str(existing_command.input_revision),
                        input_digest=source_digest,
                        input_revision=existing_command.input_revision,
                        execution_revision=exec_row.revision,
                        session_seq=event_row.session_seq,
                    )
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same revise command key with different input",
                    identity,
                )
            # 3. CAS execution.revision（队列修订单胜者，§9.13）。
            occurred = datetime.now(UTC)
            cas_result = await conn.execute(
                sa.update(AgentExecution)
                .where(
                    AgentExecution.user_scope == request.user_scope,
                    AgentExecution.execution_id == request.execution_id,
                    AgentExecution.revision == request.expected_execution_revision,
                )
                .values(revision=AgentExecution.revision + 1, updated_at=occurred)
            )
            if cas_result.rowcount != 1:
                raise AgentServiceError(
                    AgentErrorCode.REVISION_CONFLICT,
                    "execution revision changed concurrently",
                    identity,
                )
            # 4. session 行锁 + 序号分配。
            await conn.execute(
                sa.select(AgentSession.session_id)
                .where(
                    AgentSession.user_scope == request.user_scope,
                    AgentSession.session_id == request.session_id,
                )
                .with_for_update()
            )
            session_seq = (
                (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentMessage.session_seq), -1)).where(
                            AgentMessage.user_scope == request.user_scope,
                            AgentMessage.session_id == request.session_id,
                        )
                    )
                ).scalar_one()
            ) + 1
            # 5. 新输入版：既有命令版 max+1（受理=1，首次修订=2）。
            input_revision = (
                (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentCommand.input_revision), 1)).where(
                            AgentCommand.user_scope == request.user_scope,
                            AgentCommand.execution_id == request.execution_id,
                            AgentCommand.input_revision.is_not(None),
                        )
                    )
                ).scalar_one()
            ) + 1
            source_version = str(input_revision)
            event_id = stable_id("event", [request.user_scope, request.session_id, message_id])

            # 6. M2 修订消息（纯 session 公开消息，role=user，正文即源）。
            await conn.execute(
                sa.insert(AgentMessage).values(
                    user_scope=request.user_scope,
                    message_id=message_id,
                    session_id=request.session_id,
                    execution_id=None,
                    round_id=None,
                    node_id=request.node_id,
                    event_id=event_id,
                    session_seq=session_seq,
                    execution_seq=None,
                    event_kind="public_message_saved",
                    payload_kind=EventPayloadKind.PUBLIC_MESSAGE.value,
                    subject_id=message_id,
                    subject_revision=1,
                    role="user",
                    body_text=request.text,
                    payload={
                        "schema_version": 1,
                        "message_id": message_id,
                        "role": "user",
                        "source_id": source_id,
                        "source_version": source_version,
                    },
                    occurred_at=occurred,
                    committed_at=occurred,
                )
            )
            # 7. S2 源资产版本（正文由 message 行拥有，asset 不复制正文）。
            await conn.execute(
                sa.insert(AgentAssetVersion).values(
                    user_scope=request.user_scope,
                    asset_id=source_id,
                    asset_version=source_version,
                    asset_kind="source",
                    display_name=request.source_display_name,
                    digest=source_digest,
                    origin_ref=None,
                    metadata_payload={
                        "schema_version": 1,
                        "user_message_origin": {
                            "session_id": request.session_id,
                            "message_id": message_id,
                            "message_version": "1",
                        },
                    },
                    read_state="ready",
                    verification_state="program_verified",
                    available_at=occurred,
                )
            )
            # 8. 两条源边：源→message v1（origin）+ 源→执行新输入版（accepted_input）。
            for consumer_kind, consumer_ref_id, consumer_version, relation in (
                ("message", message_id, "1", SourceRelationKind.ORIGIN),
                ("execution", request.execution_id, source_version, SourceRelationKind.ACCEPTED_INPUT),
            ):
                await conn.execute(
                    sa.insert(AgentSourceLink).values(
                        user_scope=request.user_scope,
                        link_id=stable_id(
                            "source_link",
                            [
                                request.user_scope,
                                source_id,
                                source_version,
                                consumer_kind,
                                consumer_ref_id,
                                consumer_version,
                            ],
                        ),
                        source_id=source_id,
                        source_version=source_version,
                        consumer_kind=consumer_kind,
                        consumer_id=consumer_ref_id,
                        consumer_version=consumer_version,
                        message_id=message_id if consumer_kind == "message" else None,
                        execution_id=request.execution_id if consumer_kind == "execution" else None,
                        relation_kind=relation.value,
                    )
                )
            # 9. revise 命令（target=execution_id；保存=committed，待投递事实）。
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=request.user_scope,
                    command_record_id=command_record_id,
                    client_command_id=request.client_command_id,
                    target_id=request.execution_id,
                    command_kind="revise",
                    session_id=request.session_id,
                    execution_id=request.execution_id,
                    round_id=None,
                    expected_revision=request.expected_execution_revision,
                    input_revision=input_revision,
                    input_digest=source_digest,
                    request_payload=json.loads(request.manifest_payload),
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                )
            )
            # 10. session 事件水位 GREATEST 推进（R22）。
            await conn.execute(
                sa.update(AgentSession)
                .where(
                    AgentSession.user_scope == request.user_scope,
                    AgentSession.session_id == request.session_id,
                )
                .values(event_seq=sa.func.greatest(AgentSession.event_seq, session_seq))
            )
            return RevisionReceipt(
                execution_id=request.execution_id,
                command_record_id=command_record_id,
                message_id=message_id,
                source_id=source_id,
                source_version=source_version,
                input_digest=source_digest,
                input_revision=input_revision,
                execution_revision=request.expected_execution_revision + 1,
                session_seq=session_seq,
            )

    # ------------------------------------------------------------------
    # C-02-4a：资料许可（R04 owner 持久事务，§9.8/§10.2）
    # ------------------------------------------------------------------

    async def save_data_consent_decision(self, command: DataConsentCommand) -> DataConsentBinding:
        """资料许可保存事务（§10.2 R04）：decision(kind='data_consent') 建行/CAS
        更新 + save 命令行（同事务 receipt_payload 存结果绑定），**单个显式事务**。

        幂等与冲突（§10.2「同命令同载荷查原，异输入冲突」）：

        - 同命令键（target=consent_id + client_command_id）同完整版本化输入
          → 查回原结果绑定（receipt_payload），零新事实；
        - 同命令键异输入 → ``INPUT_CONFLICT``；
        - 建行（expected_consent_revision=0）遇既有决定行 → ``INPUT_CONFLICT``；
        - 更新（expected≥1）决定行缺失 → ``INPUT_INVALID``，版本不符 →
          ``REVISION_CONFLICT``。

        绑定一致性（§9.8 重复字段同事务核一致）：建行 revision=1，更新后
        revision=expected+1，均与 binding.consent_revision 相等（core 层已校验）；
        decision.expires_at 与 binding.expires_at 同事务写入。锁序：decision →
        command（§5.4 第 2 条）。命令行 delivery_state=not_delivered（不冒认投递，
        INV-06；投递核定由决定面后续任务承接）。
        """

        self._check_engine()
        identity = derive_data_consent_command_identity(command)
        identity_str = f"{command.command.user_scope}/{command.consent_id}"
        binding = command.binding
        payload_wire = encode_data_consent_binding(binding)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. decision 行锁（存在时；建行竞态由 PK 唯一约束兜底）。
            decision_row = (
                await conn.execute(
                    sa.select(AgentDecision.decision_id, AgentDecision.revision)
                    .where(
                        AgentDecision.user_scope == command.command.user_scope,
                        AgentDecision.decision_id == command.consent_id,
                    )
                    .with_for_update()
                )
            ).first()
            # 2. 命令键查重（同载荷查回原结果，异输入冲突）。
            existing_command = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest, AgentCommand.receipt_payload).where(
                        AgentCommand.user_scope == command.command.user_scope,
                        AgentCommand.target_id == command.consent_id,
                        AgentCommand.client_command_id == command.command.command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if existing_command.input_digest == identity.input_digest:
                    if existing_command.receipt_payload is None:
                        raise AgentServiceError(
                            AgentErrorCode.OPERATION_UNKNOWN,
                            "original data consent save receipt unavailable",
                            identity_str,
                        )
                    return decode_data_consent_binding(existing_command.receipt_payload, "$")
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same data consent command key with different input",
                    identity_str,
                )
            occurred = datetime.now(UTC)
            if command.command.expected_revision == 0:
                # 3a. 建行：decision 必须不存在；consent_revision=1（core 已校验）。
                if decision_row is not None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "data consent decision already exists",
                        identity_str,
                    )
                presentation_batch_seq = (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.max(AgentDecision.presentation_batch_seq), 0)).where(
                            AgentDecision.user_scope == command.command.user_scope,
                            AgentDecision.execution_id == command.execution_id,
                        )
                    )
                ).scalar_one() + 1
                try:
                    await conn.execute(
                        sa.insert(AgentDecision).values(
                            user_scope=command.command.user_scope,
                            decision_id=command.consent_id,
                            session_id=command.session_id,
                            execution_id=command.execution_id,
                            round_id=command.round_id,
                            node_id=command.node_id,
                            decision_kind="data_consent",
                            source_scope_digest=binding.source_scope_digest,
                            expires_at=_wire_to_dt(binding.expires_at),
                            presentation_batch_seq=presentation_batch_seq,
                            data_consent_payload=payload_wire,
                            updated_at=occurred,
                        )
                    )
                except IntegrityError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "data consent decision created concurrently",
                        identity_str,
                    ) from exc
            else:
                # 3b. CAS 更新：revision=expected 单胜者；payload 与行版同事务一致。
                if decision_row is None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "data consent decision does not exist",
                        identity_str,
                    )
                cas_result = await conn.execute(
                    sa.update(AgentDecision)
                    .where(
                        AgentDecision.user_scope == command.command.user_scope,
                        AgentDecision.decision_id == command.consent_id,
                        AgentDecision.revision == command.command.expected_revision,
                    )
                    .values(
                        revision=binding.consent_revision,
                        data_consent_payload=payload_wire,
                        source_scope_digest=binding.source_scope_digest,
                        expires_at=_wire_to_dt(binding.expires_at),
                        updated_at=occurred,
                    )
                )
                if cas_result.rowcount != 1:
                    raise AgentServiceError(
                        AgentErrorCode.REVISION_CONFLICT,
                        "data consent revision changed",
                        identity_str,
                    )
            # 4. save 命令行（target=consent_id；receipt_payload 存结果绑定供查回）。
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=command.command.user_scope,
                    command_record_id=identity.command_record_id,
                    client_command_id=command.command.command_id,
                    target_id=command.consent_id,
                    command_kind="data_consent_save",
                    session_id=command.session_id,
                    execution_id=command.execution_id,
                    round_id=command.round_id,
                    expected_revision=command.command.expected_revision,
                    input_revision=None,
                    input_digest=identity.input_digest,
                    request_payload=payload_wire,
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                    receipt_payload=payload_wire,
                )
            )
            return binding

    async def revoke_data_consent_decision(self, command: DataConsentRevokeCommand) -> CommandReceipt:
        """资料许可撤销事务（§10.2 R04）：decision 行锁下 state→revoked CAS +
        revoke 命令行（自足命令：保存/投递/消费同事务落定），**单个显式事务**。

        幂等与冲突：同命令键同输入 → 查回原回执；异输入 → ``INPUT_CONFLICT``；
        决定行缺失 → ``INPUT_INVALID``；版本不符 → ``REVISION_CONFLICT``；已终态
        （revoked/expired）→ ``POLICY_DENIED``。撤销保原 approver_ref/valid_from/
        expires_at（§9.8 状态矩阵：撤销不改写批准主体与期限，revision 单调 +1）。
        """

        self._check_engine()
        identity = derive_data_consent_revoke_identity(command)
        identity_str = f"{command.command.user_scope}/{command.consent_id}"
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. decision 行锁（§5.4 第 2 条：decision → command）。
            decision_row = (
                await conn.execute(
                    sa.select(AgentDecision.revision, AgentDecision.data_consent_payload)
                    .where(
                        AgentDecision.user_scope == command.command.user_scope,
                        AgentDecision.decision_id == command.consent_id,
                    )
                    .with_for_update()
                )
            ).first()
            if decision_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "data consent decision does not exist",
                    identity_str,
                )
            # 2. 命令键查重（同命令同输入查回原回执；先于版本校验——撤销已生效
            #    后 revision 已推进，重放不得被 REVISION_CONFLICT 拦截）。
            existing_command = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest, AgentCommand.receipt_payload).where(
                        AgentCommand.user_scope == command.command.user_scope,
                        AgentCommand.target_id == command.consent_id,
                        AgentCommand.client_command_id == command.command.command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if existing_command.input_digest == identity.input_digest:
                    if existing_command.receipt_payload is None:
                        raise AgentServiceError(
                            AgentErrorCode.OPERATION_UNKNOWN,
                            "original data consent revoke receipt unavailable",
                            identity_str,
                        )
                    return decode_command_receipt(existing_command.receipt_payload, "$")
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same data consent revoke key with different input",
                    identity_str,
                )
            # 3. 版本与状态校验（终态不可再撤销）。
            if decision_row.revision != command.command.expected_revision:
                raise AgentServiceError(
                    AgentErrorCode.REVISION_CONFLICT,
                    "data consent revision changed",
                    identity_str,
                )
            if decision_row.data_consent_payload is None:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "data consent payload cannot be reliably decoded",
                    identity_str,
                )
            current = decode_data_consent_binding(decision_row.data_consent_payload, "$")
            if current.state is not DataConsentState.PENDING and current.state is not DataConsentState.APPROVED:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    "data consent already in terminal state",
                    identity_str,
                )
            new_revision = command.command.expected_revision + 1
            revoked_binding = replace(current, state=DataConsentState.REVOKED, consent_revision=new_revision)
            revoked_wire = encode_data_consent_binding(revoked_binding)
            # 重读核一致（§9.8 重复字段同事务核一致：编码形态必须可严格重读）。
            decode_data_consent_binding(revoked_wire, "$")
            occurred = datetime.now(UTC)
            await conn.execute(
                sa.update(AgentDecision)
                .where(
                    AgentDecision.user_scope == command.command.user_scope,
                    AgentDecision.decision_id == command.consent_id,
                    AgentDecision.revision == command.command.expected_revision,
                )
                .values(
                    revision=new_revision,
                    data_consent_payload=revoked_wire,
                    updated_at=occurred,
                )
            )
            receipt = CommandReceipt(
                command=command.command,
                target_id=command.consent_id,
                current_revision=new_revision,
                commit_state=CommitState.COMMITTED,
                delivery_state=DeliveryState.DELIVERED,
                consumption_state=ConsumptionState.CONSUMED,
                updated_at=_wire_time(occurred),
                error_code=None,
                next_action=None,
            )
            # 4. revoke 命令行（自足 CAS 命令：效果即决定行更新，同事务三态落定）。
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=command.command.user_scope,
                    command_record_id=identity.command_record_id,
                    client_command_id=command.command.command_id,
                    target_id=command.consent_id,
                    command_kind="data_consent_revoke",
                    session_id=None,
                    execution_id=None,
                    round_id=None,
                    expected_revision=command.command.expected_revision,
                    input_revision=None,
                    input_digest=identity.input_digest,
                    request_payload=json.loads(json_doc({"consent_id": command.consent_id})),
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.DELIVERED.value,
                    consumption_state=ConsumptionState.CONSUMED.value,
                    consumed_at=occurred,
                    verified_at=occurred,
                    receipt_payload=encode_command_receipt(receipt),
                )
            )
            return receipt

    async def read_data_consent_decision(self, user_scope: str, consent_id: str) -> DataConsentBinding | None:
        """读取资料许可绑定（决定行缺失 → None；payload 不可靠重读 → fail-closed
        OPERATION_UNKNOWN，R21 不把坏数据伪装为无许可）。"""

        self._check_engine()
        identity_str = f"{user_scope}/{consent_id}"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity_str)
        if not isinstance(consent_id, str) or consent_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "consent_id must be non-empty text", identity_str)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(AgentDecision.data_consent_payload).where(
                        AgentDecision.user_scope == user_scope,
                        AgentDecision.decision_id == consent_id,
                    )
                )
            ).first()
        if row is None or row.data_consent_payload is None:
            return None
        return decode_data_consent_binding(row.data_consent_payload, "$")

    async def claim_data_consent(
        self, authorization: DataAttemptAuthorization, claim: DataConsentClaim
    ) -> DataConsentClaimFact:
        """资料许可认领事务（§10.2/§5.4「来源共同 claim 和资料许可撤销单胜者」）：
        decision 行锁下复核核权判定（撤销先胜 → POLICY_DENIED）+ operation 行锁下
        追加 data_consent_claims，**单个显式事务**。

        - 摘要新鲜重算（不信调用方 digest）+ claim 与请求一致性（core 校验）；
        - 同 (operation_id, attempt_id) 同 claim → 重放返回原事实（W 崩溃恢复）；
          异 claim → ``INPUT_CONFLICT``；
        - 复核不过（撤销先胜 / revision 推进 / 期限届满）→ ``POLICY_DENIED``（脱敏
          原因码）；claim 先胜保真实在途，后续 attempt 必须再核（§5.4 第 6 条）；
        - operation 行缺失 → ``INPUT_INVALID``（调用方违反先备后派发契约）；
        - 锁序：decision → operation（§5.4 仲裁排序）。
        """

        self._check_engine()
        recomputed_digest = validate_data_attempt_authorization(authorization)
        validate_data_consent_claim(claim, authorization, recomputed_digest=recomputed_digest)
        identity_str = f"{authorization.user_scope}/{authorization.consent_id}"
        claim_wire = encode_data_consent_claim(claim)
        claim_canonical = json_doc(claim_wire)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. decision 行锁 + 严格重读（不可靠重读 fail-closed）。
            decision_row = (
                await conn.execute(
                    sa.select(AgentDecision.data_consent_payload)
                    .where(
                        AgentDecision.user_scope == authorization.user_scope,
                        AgentDecision.decision_id == authorization.consent_id,
                    )
                    .with_for_update()
                )
            ).first()
            if decision_row is None or decision_row.data_consent_payload is None:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "data consent payload cannot be reliably decoded",
                    identity_str,
                )
            binding = decode_data_consent_binding(decision_row.data_consent_payload, "$")
            # 2. operation 行锁（claim 追加目标；缺失=先备后派发契约被违反）。
            operation_row = (
                await conn.execute(
                    sa.select(AgentOperation.data_consent_claims)
                    .where(
                        AgentOperation.user_scope == authorization.user_scope,
                        AgentOperation.operation_id == authorization.operation_id,
                    )
                    .with_for_update()
                )
            ).first()
            if operation_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "operation does not exist; prepare before dispatch",
                    identity_str,
                )
            # 3. 同 attempt 重放查回（先于核权判定：已生效 claim 不被后续撤销改写）。
            existing_claims: list[dict] = list(operation_row.data_consent_claims or [])
            for existing in existing_claims:
                if not isinstance(existing, dict):
                    continue
                if existing.get("attempt_id") == authorization.attempt_id:
                    if json_doc(existing) == claim_canonical:
                        return DataConsentClaimFact(claim=claim, replayed=True)
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "same attempt claim with different input",
                        identity_str,
                    )
            # 4. 行锁下单胜者复核（撤销先胜零新 claim）。
            now = datetime.now(UTC)
            deny_reason = evaluate_data_attempt(binding, authorization, now, recomputed_digest=recomputed_digest)
            if deny_reason is not None:
                raise AgentServiceError(AgentErrorCode.POLICY_DENIED, deny_reason, identity_str)
            # 5. 追加 claim（行锁下无丢失更新；operation revision 单调 +1）。
            existing_claims.append(claim_wire)
            await conn.execute(
                sa.update(AgentOperation)
                .where(
                    AgentOperation.user_scope == authorization.user_scope,
                    AgentOperation.operation_id == authorization.operation_id,
                )
                .values(
                    data_consent_claims=existing_claims,
                    revision=AgentOperation.revision + 1,
                    updated_at=now,
                )
            )
            return DataConsentClaimFact(claim=claim, replayed=False)
