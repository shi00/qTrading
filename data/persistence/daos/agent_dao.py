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

import asyncio
import contextlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from collections.abc import AsyncIterator, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as sa_pg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    AnswerReceipt,
    ArtifactPage,
    ArtifactPageItem,
    ArtifactRead,
    ArtifactReadQuery,
    BindActualInputReceipt,
    BindActualInputRequest,
    BusinessSourceRegistrationReceipt,
    BusinessSourceRegistrationRequest,
    CanonicalJson,
    CommandReceipt,
    CommandRef,
    CommitState,
    ConsumeAnswerRequest,
    ConsumptionFact,
    ConsumptionState,
    CLEANUP_TOMBSTONE,
    CLEANUP_TOMBSTONE_DIGEST,
    CleanupJobRecord,
    CleanupPhase,
    decode_cleanup_identity_set,
    encode_command_ref,
    encode_cleanup_identity_set,
    normalize_cleanup_asset_keys,
    DataAttemptAuthorization,
    DataConsentBinding,
    DataConsentClaim,
    DataConsentClaimFact,
    DataConsentCommand,
    DataConsentRevokeCommand,
    DataConsentState,
    DecisionBatchFact,
    DecisionSummary,
    DeliveryFact,
    DeliveryManifest,
    DeliveryPhaseReceipt,
    DeliveryRead,
    DeliveryState,
    decode_delivery_manifest,
    encode_delivery_manifest,
    validate_delivery_transition,
    DispatchCloseFact,
    DispatchCloseRequest,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferPhase,
    DraftTransferReceipt,
    DraftTransferSourceKind,
    SendableOwner,
    EffectState,
    EventFact,
    EventKind,
    EventPayloadKind,
    EventPage,
    EventReadQuery,
    ExecutionSummary,
    FactValue,
    GrantRevisionRequest,
    HistoryHit,
    HistoryPage,
    HistoryQuery,
    MarkAnswerDeliveredRequest,
    MessagePage,
    MessagePageItem,
    NON_RECOVERABLE_DISPLAY_STATES,
    OPEN_OPERATION_PHASES,
    OPERATION_PHASES,
    Observation,
    OperationRead,
    OperationReadQuery,
    OperationSummary,
    ReadMarkCommand,
    ReadMarkEntry,
    ReadMarkReceipt,
    ReadState,
    RangeLimitation,
    RecordEventRequest,
    RecoveryExecution,
    RecoveryGrantFact,
    RecoveryGrantRequest,
    ResearchEvent,
    ResearchSnapshot,
    ResourceClaimRequest,
    ResourceReleaseReceipt,
    PlanItemSnapshot,
    PlanProposal,
    PLAN_STORE_VERSION,
    ResourceReleaseRequest,
    ResourceSlotKind,
    ResourceTicket,
    ReviseQueuedRequest,
    RevisionReceipt,
    RoleSkillAssignment,
    RoleSkillAssignmentReceipt,
    ROUND_STOP_REASON_USER_TERMINATION,
    RECOVERY_UNVERIFIED_DISPLAY_STATES,
    SKILL_ASSET_METADATA_VERSION,
    SOURCE_COVERAGES,
    SOURCE_REGISTRATION_CONSUMER_KINDS,
    SaveAnswerRequest,
    SelectedRange,
    SessionChangeCommand,
    SessionAction,
    SessionRead,
    SessionReadQuery,
    SessionSummary,
    SkillAssetReceipt,
    SkillAssetRegistration,
    SkillVersionRef,
    SourceClaimBinding,
    SourceClaimFact,
    SourceClaimRequest,
    SourceConsumerRef,
    SourceConsumersPage,
    SourceRead,
    SourceReadQuery,
    SourceReadSummary,
    SourceRelationKind,
    SourceStopCommand,
    SourceStopReceipt,
    SourceVersionRef,
    SourceRef,
    StartupIsolationRecord,
    SubmissionReceipt,
    ToolGrantFact,
    ToolGrantState,
    UsageCounterFact,
    UsageCounterUpdate,
    UsageSummary,
    VerificationState,
    decode_artifact_page_item,
    decode_data_consent_binding,
    decode_command_receipt,
    decode_decision_summary,
    decode_execution_summary,
    decode_history_hit,
    decode_json_doc,
    decode_message_page_item,
    decode_operation_summary,
    decode_plan_proposal_document,
    plan_asset_id,
    plan_proposal_digest,
    normalize_plan_proposal,
    decode_fact_value,
    decode_range_limitation,
    decode_read_mark_entry,
    decode_research_event,
    decode_selected_range,
    decode_session_summary,
    decode_snapshot_identity,
    decode_source_read_summary,
    decode_source_ref,
    decode_submission_receipt,
    decode_draft_payload,
    decode_draft_receipt,
    decode_draft_transfer_receipt,
    draft_save_input_digest,
    draft_transfer_input_digest,
    encode_draft_payload,
    encode_draft_receipt,
    encode_draft_transfer_command,
    encode_draft_transfer_receipt,
    is_empty_draft_payload,
    validate_draft_transfer_command,
    encode_session_change_command,
    session_change_input_digest,
    validate_draft_save_command,
    validate_session_change_command,
    encode_command_receipt,
    encode_data_consent_binding,
    encode_data_consent_claim,
    encode_source_read_query,
    encode_source_ref,
    encode_submission_receipt,
    compute_history_query_digest,
    parse_history_cursor,
    validate_artifact_read_query,
    validate_history_query,
    validate_operation_read_query,
    derive_accept_identity,
    derive_data_consent_command_identity,
    derive_data_consent_revoke_identity,
    derive_event_identity,
    evaluate_data_attempt,
    frozen_command_input_digest,
    json_doc,
    is_digest,
    legacy_command_input_digest,
    normalize_cleanup_ids,
    role_skill_assignment_digest,
    sha256_hex,
    slice_body_page,
    stable_id,
    to_wire_dict,
    validate_data_attempt_authorization,
    validate_data_consent_claim,
    validate_read_mark_command,
    validate_resource_claim_request,
    validate_role_skill_assignment,
    validate_session_read_query,
)
from data.persistence.daos.base_dao import BaseDao

#: observe 监听通道（§9.13「独立 LISTEN ≤1」；pg_notify payload ≤8000B）。
_AGENT_EVENT_CHANNEL: Final = "agent_events"

#: 资源全局仲裁单例行 slot_id（§5.4 锁序第 1 条「全局资源 guard」；C-04 裁决：
#: user_scope='' 哨兵行，claim 事务内 ON CONFLICT DO NOTHING 幂等预置）。
_RESOURCE_GUARD_SLOT_ID: Final = "global_guard"

#: 每总（同 root 树）活跃叶子计数：先向上递归定 root，再向下收集整树开放叶子。
#: 开放谓词 = released_at IS NULL OR release_verified=false（unknown 在途不放空）。
#: 参数化文本 SQL（R4：绑定参数，无拼接）。
_RESOURCE_TREE_OPEN_LEAF_SQL: Final = """
WITH RECURSIVE up(execution_id, parent_execution_id) AS (
    SELECT e.execution_id, e.parent_execution_id
    FROM agent_execution e
    WHERE e.user_scope = :user_scope AND e.execution_id = :execution_id
    UNION ALL
    SELECT e.execution_id, e.parent_execution_id
    FROM agent_execution e
    JOIN up u ON e.user_scope = :user_scope AND e.execution_id = u.parent_execution_id
),
root AS (
    SELECT execution_id FROM up WHERE parent_execution_id IS NULL LIMIT 1
),
down(execution_id) AS (
    SELECT execution_id FROM root
    UNION ALL
    SELECT e.execution_id
    FROM agent_execution e
    JOIN down d ON e.user_scope = :user_scope AND e.parent_execution_id = d.execution_id
)
SELECT count(*)
FROM agent_resource_slot s
JOIN agent_execution x
    ON x.user_scope = s.user_scope AND x.execution_id = s.execution_id
WHERE s.slot_kind = 'leaf'
  AND x.user_scope = :user_scope
  AND x.execution_id IN (SELECT execution_id FROM down)
  AND (s.released_at IS NULL OR s.release_verified = false)
"""


#: 源禁用已知派生闭包标记：递归收集 consumer_kind='asset' 派生链（FK 保证已知
#: 边完整），闭包内全部 source_link 标 disabled_inherited（§9.10；参数化，R4）。
_SOURCE_DISABLE_CLOSURE_SQL: Final = """
WITH RECURSIVE closure(source_id, source_version) AS (
    SELECT CAST(:source_id AS text), CAST(:source_version AS text)
    UNION
    SELECT l.derived_asset_id, l.derived_asset_version
    FROM agent_source_link l
    JOIN closure c
        ON l.user_scope = :user_scope
        AND l.source_id = c.source_id
        AND l.source_version = c.source_version
    WHERE l.consumer_kind = 'asset'
        AND l.derived_asset_id IS NOT NULL
        AND l.derived_asset_version IS NOT NULL
)
UPDATE agent_source_link
SET disabled_inherited = true
WHERE user_scope = :user_scope
    AND (source_id, source_version) IN (SELECT source_id, source_version FROM closure)
"""


# E-06 九类原生目标位于**运行库**（dbos / agent_ds，独立逻辑库），不属业务库；
# 计数/Datasource 删除见 ``data.persistence.agent_runtime_store``，系统表删除走
# DBOS 公开 API（``services.agent_service.cleanup_native``）。本 DAO 只承载应用域
# 的 NOT NULL tombstone（§10.7 字段级最小留存）。


def _budget_blocked(identity: str, reason: str) -> AgentServiceError:
    """资源限额拒绝（§5.5：说明具体受阻原因，不伪失败、不暗放宽）。"""

    return AgentServiceError(AgentErrorCode.BUDGET_BLOCKED, reason, identity)


from data.persistence.models import (
    AgentArtifact,
    AgentAssetVersion,
    AgentBodyPayload,
    AgentCleanupJob,
    AgentCommand,
    AgentDecision,
    AgentExecution,
    AgentMessage,
    AgentOperation,
    AgentReadMark,
    AgentResourceSlot,
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


def _wire_timestamp_or_none(value: str | None, identity: str) -> datetime | None:
    """wire UTC 文本 → 带时区 datetime；NULL 保持 NULL，非法/无时区拒绝。"""

    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "available_at must be UTC text or null", identity)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "available_at is not ISO-8601", identity) from exc
    if parsed.tzinfo is None:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "available_at must carry a timezone", identity)
    return parsed


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


def _event_from_row(row: Any) -> Any:
    """AgentMessage 行 → ResearchEvent 包络（严格结构层解码）。"""

    return decode_research_event(
        {
            "schema_version": 1,
            "event_id": row.event_id,
            "user_scope": row.user_scope,
            "session_id": row.session_id,
            "execution_id": row.execution_id,
            "node_id": row.node_id,
            "round_id": row.round_id,
            "input_revision": row.input_revision,
            "session_seq": row.session_seq,
            "execution_seq": row.execution_seq,
            "occurred_at": _wire_time(row.occurred_at),
            "committed_at": _wire_time(row.committed_at),
            "event_kind": EventKind(row.event_kind).value,
            "subject_id": row.subject_id,
            "subject_revision": row.subject_revision,
            "payload_kind": row.payload_kind,
            "payload_canonical_json": json_doc(row.payload) if row.payload is not None else json_doc({}),
            "native_fact_ref": row.native_fact_ref,
        },
        "$",
    )


def _decode_plan_proposal_row(row: Any, *, execution_id: str, identity: str) -> PlanProposal:
    """计划提案行 → 冻结提案；行内自相矛盾 fail-closed（``OPERATION_UNKNOWN``）。"""

    document = row.body_text
    if not isinstance(document, str) or not document:
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan proposal body missing", identity)
    if row.read_state != "ready":
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan proposal row is not ready", identity)
    try:
        proposal = decode_plan_proposal_document(document, execution_id=execution_id)
        canonical = normalize_plan_proposal(proposal)
        digest = plan_proposal_digest(proposal)
    except AgentValidationError as exc:
        raise AgentServiceError(
            AgentErrorCode.OPERATION_UNKNOWN, f"plan proposal row invalid: {exc.code}", identity
        ) from exc
    if row.asset_version != str(proposal.plan_revision):
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan revision row mismatch", identity)
    if canonical != document:
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan proposal body is not canonical", identity)
    if row.digest != digest:
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan proposal digest mismatch", identity)
    return proposal


def _plan_head_row(rows: Sequence[Any], identity: str) -> tuple[Any | None, int]:
    """计划版本行集 → (head 行, head revision)；非数字/非正版本 fail-closed。"""

    head: Any | None = None
    head_revision = 0
    for row in rows:
        raw_version = row.asset_version
        if not isinstance(raw_version, str):
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan revision row invalid", identity)
        try:
            revision = int(raw_version)
        except ValueError as exc:
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan revision row invalid", identity) from exc
        if revision < 1:
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "plan revision row invalid", identity)
        if revision > head_revision:
            head, head_revision = row, revision
    return head, head_revision


def _command_input_replays(
    existing: Any,
    *,
    text: str,
    manifest_payload: CanonicalJson,
    expected_revision: int,
    command_input_digest: str,
) -> bool:
    """同命令键重放判定（检视 D05）：完整规范输入摘要优先，旧摘要行按存储事实核对。

    旧行 ``input_digest`` 仅覆盖正文（:func:`legacy_command_input_digest`）；命中旧
    摘要时改以 command 行存储的冻结 manifest（``request_payload``）与期望版本
    （``expected_revision``）核对，避免「同键同正文换 manifest/期望版」被误判为
    重放（旧行无 node 列，node 身份仅由新摘要覆盖）。无法证明一致即按异输入处理。
    """

    if existing.input_digest == command_input_digest:
        return True
    if existing.input_digest != legacy_command_input_digest(text):
        return False
    if existing.expected_revision != expected_revision:
        return False
    stored_payload = existing.request_payload
    if stored_payload is None:
        return False
    return json_doc(stored_payload) == json_doc(json.loads(manifest_payload))


def _validate_plan_items_for_save(items: object, *, execution_id: str, identity: str) -> tuple[PlanItemSnapshot, ...]:
    """写入前校验步骤序列（结构、状态值、id 唯一；失败 ``INPUT_INVALID``）。"""

    if isinstance(items, str) or not isinstance(items, Sequence):
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "plan items must be a sequence", identity)
    item_tuple = tuple(items)
    for index, item in enumerate(item_tuple):
        if not isinstance(item, PlanItemSnapshot):
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"plan items[{index}] must be PlanItemSnapshot", identity
            )
    try:
        normalize_plan_proposal(PlanProposal(execution_id=execution_id, plan_revision=1, items=item_tuple))
    except AgentValidationError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"plan items invalid: {exc.code}", identity) from exc
    return item_tuple


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
        - 同命令键（target+client_command_id）同完整输入（正文 + 冻结 manifest +
          期望版/node 身份，``frozen_command_input_digest`` 相等）→ 返回原回执；
        - 同命令键异输入（正文、manifest、期望版或 node 任一不同）→
          ``INPUT_CONFLICT``；旧行摘要仅覆盖正文，按 command 行存储的 manifest 与
          期望版补核（检视 D05）。

        序号分配持目标 session 行锁（§5.4.4）；session.event_seq 以 GREATEST
        单调推进（R22）。occurred_at/committed_at 用受理时刻（同事务内一致）。

        Raises:
            AgentServiceError: INPUT_INVALID（目标会话不存在）/ INPUT_CONFLICT
                （同身份异输入或同命令键异输入）/ OPERATION_UNKNOWN（原回执
                不可得）。
        """

        self._check_engine()
        identity = derive_accept_identity(request)
        command_input_digest = frozen_command_input_digest(
            "accept",
            request.text,
            request.manifest_payload,
            expected_revision=request.expected_revision,
            node_id=request.node_id,
        )
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
                    sa.select(
                        AgentCommand.execution_id,
                        AgentCommand.input_digest,
                        AgentCommand.expected_revision,
                        AgentCommand.request_payload,
                    ).where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.target_id == request.session_id,
                        AgentCommand.client_command_id == request.client_command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if _command_input_replays(
                    existing_command,
                    text=request.text,
                    manifest_payload=request.manifest_payload,
                    expected_revision=request.expected_revision,
                    command_input_digest=command_input_digest,
                ):
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
            # 并发窗口防护：intent 唯一部分索引（跨 session 并发受理同一意图，
            # 各自持不同 session 行锁互不可见）——撞索引即并发首胜者已受理，
            # 按 INV-01 归 INPUT_CONFLICT（败者重传走步骤 2 查回原回执）。
            try:
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
            except IntegrityError as exc:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same intent accepted concurrently under another execution",
                    _identity_str(request),
                ) from exc

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
                    input_digest=command_input_digest,
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
                    search_text=request.text,
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

    async def read_command(self, ref: CommandRef, *, user_scope: str) -> CommandReceipt:
        """查命令原事实（§4.2 ``read_command``）：只读保存/投递/消费，不生成新决定。

        语义（§4.1/§5.1/INV-06）：

        - ``ref.user_scope`` 必须等于调用上下文 ``user_scope``——不因知道命令键而
          放行其他用户域（§4.1「由注入的可信调用上下文提供，不能相信 payload」）；
        - 用户域内按调用方命令键查：0 行 → ``INPUT_INVALID``（不区分「不存在」与
          「属于他人」）；同一键跨 target 复用产生多行 → ``INPUT_INVALID``（引用有
          歧义，不静默择一）；命中的存行须与引用同源：``expected_revision`` 比对
          命令行列，``source_module`` / ``business_request_id`` 在存行链到根/比较路
          execution（意图键非空）时比对 execution 意图键——叶 execution 不存意图键，
          不做无依据比对。不符 → ``INPUT_INVALID``，回执不得回显与存行矛盾的引用；
        - 存储状态/错误码不在冻结枚举内 → ``OPERATION_UNKNOWN``（fail-closed，
          R21：坏事实不得被读成确定的保存/投递/消费）；
        - ``next_action`` 为 None：本方法只回报已存事实，下一步由调用方按命令类型
          决定（不在读取面发明动作）。
        """

        self._check_engine()
        identity = f"{user_scope}/{ref.command_id}"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity)
        if ref.user_scope != user_scope:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command ref outside caller user scope", identity)
        try:
            encode_command_ref(ref)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        AgentCommand.target_id,
                        AgentCommand.expected_revision,
                        AgentCommand.revision,
                        AgentCommand.commit_state,
                        AgentCommand.delivery_state,
                        AgentCommand.consumption_state,
                        AgentCommand.updated_at,
                        AgentCommand.error_code,
                        # 根意图键（source_module/business_request_id）只存于 execution：
                        # 根/比较路非空，叶为 NULL（§9.4）。
                        AgentExecution.source_module.label("intent_source_module"),
                        AgentExecution.business_request_id.label("intent_business_request_id"),
                    )
                    .select_from(AgentCommand)
                    .outerjoin(
                        AgentExecution,
                        sa.and_(
                            AgentExecution.user_scope == AgentCommand.user_scope,
                            AgentExecution.execution_id == AgentCommand.execution_id,
                        ),
                    )
                    .where(
                        AgentCommand.user_scope == user_scope,
                        AgentCommand.client_command_id == ref.command_id,
                    )
                )
            ).all()
        if not rows:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command not found in user scope", identity)
        if len(rows) > 1:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command ref is ambiguous across targets", identity)
        row = rows[0]
        mismatches: list[str] = []
        if ref.expected_revision != row.expected_revision:
            mismatches.append("expected_revision")
        if row.intent_source_module is not None:
            # 存行链到根/比较路 execution 时，意图键是权威事实，必须与引用同源；
            # 叶 execution 不存意图键，不做无依据的比对。
            if ref.source_module != row.intent_source_module:
                mismatches.append("source_module")
            if ref.business_request_id != row.intent_business_request_id:
                mismatches.append("business_request_id")
        if mismatches:
            # 回执必须与传入引用同源：只按 command_id 命中会造成「原命令/准确目标/预期版」自相矛盾。
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"command ref does not match stored command: {','.join(mismatches)}",
                identity,
            )
        try:
            commit_state = CommitState(row.commit_state)
            delivery_state = DeliveryState(row.delivery_state)
            consumption_state = ConsumptionState(row.consumption_state)
            error_code = AgentErrorCode(row.error_code) if row.error_code is not None else None
        except ValueError as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"unreadable command state: {type(exc).__name__}",
                identity,
            ) from exc
        return CommandReceipt(
            command=ref,
            target_id=row.target_id,
            current_revision=row.revision,
            commit_state=commit_state,
            delivery_state=delivery_state,
            consumption_state=consumption_state,
            updated_at=_wire_time(row.updated_at),
            error_code=error_code,
            next_action=None,
        )

    # ------------------------------------------------------------------
    # F-02-1：来源精确读取（§4.2 read_source）
    # ------------------------------------------------------------------

    async def read_source(self, query: SourceReadQuery, *, user_scope: str) -> SourceRead:
        """读**已准备**的来源选定表示（§4.2/§9.10）：只读，零解析、零扩大范围。

        语义：

        - 查询与已存版本三方对齐（source_id/version/digest/locator）：摘要或定位
          不一致 → ``REVISION_CONFLICT``，绝不静默换成最新/相近版本（§3.3）；
        - 已存元数据自相矛盾（坏 ``source_ref``/核验态不一致/坏 coverage/facts）→
          ``OPERATION_UNKNOWN``（fail-closed，R21）；
        - 正文取 ``body_text`` 或 ``body_ref`` 载荷，核 owner 三元组/编码/长度/
          SHA-256 后重建；缺失或损坏 → ``PERSISTENCE_UNAVAILABLE``，不回退摘要
          （§9.10.1）；
        - 未准备表示返回行内 ``read_state`` 与明确原因（不伪造空成功）；禁用/
          清理后 ``available=False``，已存原因优先。
        """

        self._check_engine()
        identity = f"{user_scope}/{query.source_id}@{query.version}"
        _require_text_field(user_scope, "user_scope", identity)
        try:
            encode_source_read_query(query)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.asset_id,
                        AgentAssetVersion.asset_version,
                        AgentAssetVersion.digest,
                        AgentAssetVersion.metadata_payload,
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.verification_state,
                        AgentAssetVersion.body_text,
                        AgentAssetVersion.body_ref,
                        AgentAssetVersion.disabled_at,
                        AgentAssetVersion.cleaned_at,
                        AgentAssetVersion.tombstoned,
                    ).where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == query.source_id,
                        AgentAssetVersion.asset_version == query.version,
                        AgentAssetVersion.asset_kind == "source",
                    )
                )
            ).first()
            if row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "source version not found in user scope", identity
                )
            if row.digest != query.digest:
                raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "source digest mismatch", identity)
            source_ref, verification, metadata = self._decode_source_asset_row(
                row, user_scope=user_scope, identity=identity
            )
            if source_ref.locator != query.locator:
                raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "source locator mismatch", identity)
            try:
                read_state = ReadState(row.read_state)
            except ValueError as exc:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    f"unreadable source read_state: {type(exc).__name__}",
                    identity,
                ) from exc
            await self._assert_source_registered(conn, query=query, user_scope=user_scope, identity=identity)
            body_text: str | None = None
            # 未读/未知态没有可信表示（不外泄矛盾正文）；禁用/不可得≠清理，
            # 已保存正文仍按原样返回并由 available/原因标注当前依据状态（§9.7）。
            if read_state not in (ReadState.UNREAD, ReadState.UNKNOWN):
                body_text = row.body_text
                if body_text is None and row.body_ref is not None:
                    body_text = await self._read_body_text(
                        conn,
                        user_scope=user_scope,
                        body_ref=row.body_ref,
                        identity=identity,
                        owner=("asset", query.source_id, query.version),
                    )
            missing_reason = source_ref.missing_reason
            available = (
                not row.tombstoned
                and row.disabled_at is None
                and row.cleaned_at is None
                and read_state is not ReadState.UNAVAILABLE
            )
            if not available and missing_reason is None:
                missing_reason = "source_unavailable"
            elif body_text is None and missing_reason is None:
                missing_reason = "representation_not_available"
            return SourceRead(
                source=source_ref,
                selected_range=self._source_selected_range(metadata, source_ref, identity),
                read_state=read_state,
                body_text=body_text,
                structured_blocks=self._source_blocks(metadata, identity),
                facts=self._source_facts(metadata, identity),
                coverage=self._source_coverage(metadata, identity),
                verification=verification,
                limitations=self._source_limitations(metadata, identity),
                available=available,
                missing_reason=missing_reason,
            )

    @staticmethod
    async def _assert_source_registered(
        conn: AsyncConnection, *, query: SourceReadQuery, user_scope: str, identity: str
    ) -> None:
        """核对来源已登记到请求上下文（§4.2：草稿 revision 或已受理执行输入版）。

        - 草稿上下文：须存在**会话内**谱系边（``session_id`` 非空），且该会话
          当前 ``agent_session.draft_revision`` 等于请求修订——查询未携会话 id，
          故以「来源所属会话的草稿修订」定位，避免同域不同会话的草稿互认；
        - 执行上下文：须存在 ``consumer_kind='execution'`` 且 ``execution_id``
          一致的谱系边；给出 ``round_id`` 时再核该轮次属于此执行且
          ``input_revision`` 与轮次一致。

        未登记（或轮次/版本不符）→ ``INPUT_INVALID``：不因「同用户域有该来源版本」
        就放行未进入该请求的资料。

        **写入方**：``consumer_kind='draft'`` + ``session_id`` 的谱系边由
        :meth:`save_draft` 在同事务写入（F-03-4），每次保存替换为当前引用集合；
        未保存过草稿的会话无草稿边，草稿上下文读取即未登记拒绝。
        """

        base = (
            AgentSourceLink.user_scope == user_scope,
            AgentSourceLink.source_id == query.source_id,
            AgentSourceLink.source_version == query.version,
        )
        if query.draft_revision is not None:
            link = (
                await conn.execute(
                    sa.select(AgentSourceLink.link_id)
                    .join(
                        AgentSession,
                        sa.and_(
                            AgentSession.user_scope == AgentSourceLink.user_scope,
                            AgentSession.session_id == AgentSourceLink.session_id,
                        ),
                    )
                    .where(
                        *base,
                        AgentSourceLink.session_id.is_not(None),
                        AgentSession.draft_revision == query.draft_revision,
                    )
                    .limit(1)
                )
            ).first()
        else:
            conditions = (*base, AgentSourceLink.consumer_kind == "execution")
            conditions += (
                AgentSourceLink.execution_id == query.execution_id,
                AgentSourceLink.consumer_id == query.execution_id,
            )
            if query.round_id is not None:
                round_row = (
                    await conn.execute(
                        sa.select(AgentRound.round_id, AgentRound.execution_id, AgentRound.input_revision).where(
                            AgentRound.user_scope == user_scope,
                            AgentRound.round_id == query.round_id,
                        )
                    )
                ).first()
                if round_row is None or round_row.execution_id != query.execution_id:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "round does not belong to requested execution",
                        identity,
                    )
                if query.input_revision is not None and round_row.input_revision != query.input_revision:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, "input_revision does not match requested round", identity
                    )
            link = (await conn.execute(sa.select(AgentSourceLink.link_id).where(*conditions).limit(1))).first()
        if link is None:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "source is not registered to requested context", identity
            )

    def _decode_source_asset_row(
        self, row: Any, *, user_scope: str, identity: str
    ) -> tuple[SourceRef, VerificationState, dict[str, Any]]:
        """严格解码来源资产行元数据；任何自相矛盾按 ``OPERATION_UNKNOWN`` 拒绝。"""

        metadata = row.metadata_payload
        schema_version = metadata.get("schema_version") if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "source metadata is unreadable", identity)
        try:
            source_ref = decode_source_ref(metadata.get("source_ref"), "$.metadata.source_ref")
            verification = VerificationState(row.verification_state)
            metadata_verification = VerificationState(metadata.get("verification"))
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, f"source metadata is invalid: {type(exc).__name__}", identity
            ) from exc
        if (
            source_ref.user_scope != user_scope
            or (source_ref.source_id, source_ref.version) != (row.asset_id, row.asset_version)
            or source_ref.digest != row.digest
            or source_ref.locator.origin_ref.source_digest != row.digest
            or source_ref.read_state.value != row.read_state
            or metadata_verification is not verification
        ):
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "source metadata is inconsistent", identity)
        return source_ref, verification, metadata

    @staticmethod
    def _source_selected_range(metadata: dict[str, Any], source_ref: SourceRef, identity: str) -> SelectedRange:
        """已存选定范围；未存时按已选 locator 派生（未读、统计未知，不填 0）。"""

        value = metadata.get("selected_range")
        if value is None:
            return SelectedRange(selection=source_ref.locator.selection)
        try:
            return decode_selected_range(value, "$.metadata.selected_range")
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, f"source selected_range is invalid: {exc.code}", identity
            ) from exc

    @staticmethod
    def _source_facts(metadata: dict[str, Any], identity: str) -> tuple[FactValue, ...]:
        value = metadata.get("facts")
        if value is None:
            return ()
        if not isinstance(value, list):
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "source facts are unreadable", identity)
        try:
            return tuple(decode_fact_value(item, f"$.metadata.facts[{index}]") for index, item in enumerate(value))
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, f"source facts are invalid: {exc.code}", identity
            ) from exc

    @staticmethod
    def _source_blocks(metadata: dict[str, Any], identity: str) -> tuple[CanonicalJson, ...]:
        value = metadata.get("structured_blocks")
        if value is None:
            return ()
        if not isinstance(value, list):
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, "source structured_blocks are unreadable", identity
            )
        blocks: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    f"source structured_blocks[{index}] is not canonical json text",
                    identity,
                )
            try:
                decode_json_doc(item)
            except AgentValidationError as exc:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    f"source structured_blocks[{index}] is invalid: {exc.code}",
                    identity,
                ) from exc
            blocks.append(item)
        return tuple(blocks)

    @staticmethod
    def _source_coverage(metadata: dict[str, Any], identity: str) -> str:
        """未记录覆盖度时返回 ``unknown``（不宣称完整，R21）。"""

        value = metadata.get("coverage")
        if value is None:
            return "unknown"
        if not isinstance(value, str) or value not in SOURCE_COVERAGES:
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "source coverage is invalid", identity)
        return value

    @staticmethod
    def _source_limitations(metadata: dict[str, Any], identity: str) -> tuple[RangeLimitation, ...]:
        value = metadata.get("limitations")
        if value is None:
            return ()
        if not isinstance(value, list):
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "source limitations are unreadable", identity)
        try:
            return tuple(
                decode_range_limitation(item, f"$.metadata.limitations[{index}]") for index, item in enumerate(value)
            )
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, f"source limitations are invalid: {exc.code}", identity
            ) from exc

    @staticmethod
    async def _read_body_text(
        conn: AsyncConnection,
        *,
        user_scope: str,
        body_ref: str,
        identity: str,
        owner: tuple[str, str, str] | None = None,
    ) -> str:
        """重建单块正文（§9.10.1）：核 owner/编码/长度/SHA-256，失败报不可得。

        ``owner=None`` 用于只按引用读取的大结果（工具返回等）：此时核载荷自身
        owner 三元组与 FK 列自洽，不假定引用方类型。
        """

        row = (
            await conn.execute(
                sa.select(
                    AgentBodyPayload.owner_kind,
                    AgentBodyPayload.owner_id,
                    AgentBodyPayload.owner_version,
                    AgentBodyPayload.message_id,
                    AgentBodyPayload.artifact_id,
                    AgentBodyPayload.artifact_version,
                    AgentBodyPayload.asset_id,
                    AgentBodyPayload.asset_version,
                    AgentBodyPayload.encoding,
                    AgentBodyPayload.payload_bytes,
                    AgentBodyPayload.byte_length,
                    AgentBodyPayload.payload_digest,
                ).where(
                    AgentBodyPayload.user_scope == user_scope,
                    AgentBodyPayload.body_ref == body_ref,
                )
            )
        ).first()
        if row is None:
            raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "body payload is missing", identity)
        data = bytes(row.payload_bytes)
        if owner is None:
            consistent = (
                (row.owner_kind == "message" and row.message_id == row.owner_id and row.owner_version == "1")
                or (
                    row.owner_kind == "artifact"
                    and row.artifact_id == row.owner_id
                    and row.artifact_version is not None
                    and row.owner_version == str(row.artifact_version)
                )
                or (
                    row.owner_kind == "asset"
                    and row.asset_id == row.owner_id
                    and row.asset_version == row.owner_version
                )
            )
            if not consistent:
                raise AgentServiceError(
                    AgentErrorCode.PERSISTENCE_UNAVAILABLE, "body payload owner is inconsistent", identity
                )
        elif row.owner_kind != owner[0] or row.owner_id != owner[1] or row.owner_version != owner[2]:
            raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "body payload owner mismatch", identity)
        if row.encoding != "utf-8":
            raise AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "body payload encoding mismatch", identity)
        if row.byte_length != len(data) or sha256_hex(data) != row.payload_digest:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE, "body payload length or digest mismatch", identity
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE, "body payload is not valid utf-8", identity
            ) from exc

    # ------------------------------------------------------------------
    # F-02-2：成果精确读取（§4.2 read_artifact）
    # ------------------------------------------------------------------

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead:
        """读准确成果版本与（分块）完整正文（§4.2/§9.7/§9.10.1）。

        - 行的 execution/round 必须与查询一致（不借 execution_id 放行他人/他轮）；
          不存在/跨用户 → ``INPUT_INVALID``；
        - 行内容严格解码（冻结值域），坏行 → ``OPERATION_UNKNOWN``；
        - 正文取 ``body_text`` 或 ``body_ref`` 载荷（核 owner 三元组/编码/长度/
          SHA-256），按码点分页并给下一游标，不静默截断；失效游标 → ``INPUT_INVALID``；
        - 正文未保存与已清理分开报告，不用空正文冒充成果（R21）。
        """

        self._check_engine()
        identity = f"{user_scope}/{query.artifact_id}@{query.artifact_version}"
        _require_text_field(user_scope, "user_scope", identity)
        try:
            validated = validate_artifact_read_query(query)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentArtifact.artifact_id,
                        AgentArtifact.artifact_version,
                        AgentArtifact.session_id,
                        AgentArtifact.execution_id,
                        AgentArtifact.round_id,
                        AgentArtifact.artifact_kind,
                        AgentArtifact.input_revision,
                        AgentArtifact.actual_input_digest,
                        AgentArtifact.actual_model_ref,
                        AgentArtifact.result_digest,
                        AgentArtifact.source_available_state,
                        AgentArtifact.created_at,
                        AgentArtifact.updated_at,
                        AgentArtifact.revision,
                        AgentArtifact.summary,
                        AgentArtifact.body_text,
                        AgentArtifact.body_ref,
                        AgentArtifact.result_payload,
                        AgentArtifact.published_at,
                        AgentArtifact.cleaned_at,
                    ).where(
                        AgentArtifact.user_scope == user_scope,
                        AgentArtifact.artifact_id == validated.artifact_id,
                        AgentArtifact.artifact_version == validated.artifact_version,
                    )
                )
            ).first()
            if row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "artifact version not found in user scope", identity
                )
            item = self._artifact_item_from_row(row, identity)
            if item.execution_id != validated.execution_id or item.round_id != validated.round_id:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "artifact does not belong to requested execution/round", identity
                )
            body_text: str | None = item.body_text
            if body_text is None and row.body_ref is not None:
                body_text = await self._read_body_text(
                    conn,
                    user_scope=user_scope,
                    body_ref=row.body_ref,
                    identity=identity,
                    owner=("artifact", item.artifact_id, str(item.artifact_version)),
                )
            body_available = body_text is not None
            next_cursor: str | None = None
            unavailable_reason: str | None = None
            if body_text is not None:
                try:
                    body_text, next_cursor = slice_body_page(
                        body_text,
                        cursor=validated.body_cursor,
                        prefix="b",
                        identity=f"{item.artifact_id}:{item.artifact_version}",
                        limit=validated.limit,
                    )
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity
                    ) from exc
            else:
                unavailable_reason = "body_cleaned" if item.cleaned_at is not None else "body_not_saved"
            return ArtifactRead(
                artifact_id=item.artifact_id,
                artifact_version=item.artifact_version,
                session_id=item.session_id,
                execution_id=item.execution_id,
                round_id=item.round_id,
                artifact_kind=item.artifact_kind,
                input_revision=item.input_revision,
                actual_input_digest=item.actual_input_digest,
                actual_model_ref=item.actual_model_ref,
                result_digest=item.result_digest,
                source_available_state=item.source_available_state,
                summary=item.summary,
                result_payload=item.result_payload,
                published_at=item.published_at,
                cleaned_at=item.cleaned_at,
                updated_at=item.updated_at,
                revision=item.revision,
                body_text=body_text,
                next_cursor=next_cursor,
                body_available=body_available,
                unavailable_reason=unavailable_reason,
            )

    # ------------------------------------------------------------------
    # F-02-3：操作精确读取（§4.2 read_operation）
    # ------------------------------------------------------------------

    async def _operation_owner_is_cleaned(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        session_id: str | None,
        execution_id: str | None,
    ) -> bool:
        """所属会话是否已清理（``cleaned_at`` 非空）：区分「结果已清理」与坏行。"""

        if session_id is not None:
            cleaned_at = (
                await conn.execute(
                    sa.select(AgentSession.cleaned_at).where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id == session_id,
                    )
                )
            ).scalar_one_or_none()
            return cleaned_at is not None
        if execution_id is None:
            return False
        cleaned_at = (
            await conn.execute(
                sa.select(AgentSession.cleaned_at)
                .select_from(AgentExecution)
                .join(AgentSession, AgentSession.session_id == AgentExecution.session_id)
                .where(
                    AgentExecution.user_scope == user_scope,
                    AgentExecution.execution_id == execution_id,
                )
            )
        ).scalar_one_or_none()
        return cleaned_at is not None

    async def read_operation(self, query: OperationReadQuery, *, user_scope: str) -> OperationRead:
        """读调用元数据与已保存返回（§4.2/§9.9）：effect/io 事实单列，unknown 不猜。

        - 不存在/跨用户 → ``INPUT_INVALID``；查询给出的执行/轮次/工具调用与行不符
          亦拒绝（不借 operation_id 放行他人他轮事实）；
        - 坏枚举/坏行 → ``OPERATION_UNKNOWN``；``phase='unknown'`` 或
          ``effect_state='unknown'`` 原样返回并置 ``result_available=False``；
        - 返回体优先取 ``result_ref`` 同库正文（核 owner 自洽/编码/长度/SHA-256，
          按码点分页），否则取 ``result_payload`` 原子；``result_saved`` 却两者皆空
          且所属会话已清理（``cleaned_at`` 非空）时按「结果已清理」返回
          （``unavailable_reason='result_cleaned'``），否则按坏行拒绝，不用空结果
          冒充成功（R21）。
        """

        self._check_engine()
        identity = f"{user_scope}/{query.operation_id}"
        _require_text_field(user_scope, "user_scope", identity)
        try:
            validated = validate_operation_read_query(query)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentOperation.session_id,
                        AgentOperation.execution_id,
                        AgentOperation.round_id,
                        AgentOperation.tool_call_id,
                        AgentOperation.workflow_id,
                        AgentOperation.step_id,
                        AgentOperation.call_kind,
                        AgentOperation.ordinal,
                        AgentOperation.phase,
                        AgentOperation.effect_state,
                        AgentOperation.io_state,
                        AgentOperation.owner_epoch,
                        AgentOperation.run_epoch,
                        AgentOperation.grant_id,
                        AgentOperation.grant_revision,
                        AgentOperation.parameter_digest,
                        AgentOperation.request_ref,
                        AgentOperation.result_ref,
                        AgentOperation.result_payload,
                        AgentOperation.worker_refs,
                        AgentOperation.deadline_at,
                        AgentOperation.dispatched_at,
                        AgentOperation.exited_at,
                        AgentOperation.verified_at,
                        AgentOperation.active_ms,
                        AgentOperation.elapsed_ms,
                        AgentOperation.error_code,
                        AgentOperation.error_category,
                        AgentOperation.artifact_id,
                        AgentOperation.artifact_version,
                        AgentOperation.updated_at,
                        AgentOperation.revision,
                    ).where(
                        AgentOperation.user_scope == user_scope,
                        AgentOperation.operation_id == validated.operation_id,
                    )
                )
            ).first()
            if row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "operation not found in user scope", identity)
            if row.phase not in OPERATION_PHASES:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN, f"unreadable operation phase: {row.phase}", identity
                )
            try:
                effect_state = EffectState(row.effect_state)
            except ValueError as exc:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    f"unreadable operation effect_state: {type(exc).__name__}",
                    identity,
                ) from exc
            if (
                (validated.execution_id is not None and row.execution_id != validated.execution_id)
                or (validated.round_id is not None and row.round_id != validated.round_id)
                or (validated.tool_call_id is not None and row.tool_call_id != validated.tool_call_id)
            ):
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "operation does not belong to requested scope", identity
                )
            result_text: str | None = None
            next_cursor: str | None = None
            unavailable_reason: str | None = None
            if row.phase == "unknown" or row.effect_state == "unknown":
                unavailable_reason = "effect_unknown"
            elif row.result_ref is not None:
                body_text = await self._read_body_text(
                    conn, user_scope=user_scope, body_ref=row.result_ref, identity=identity
                )
                try:
                    result_text, next_cursor = slice_body_page(
                        body_text,
                        cursor=validated.body_cursor,
                        prefix="o",
                        identity=validated.operation_id,
                        limit=validated.limit,
                    )
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity
                    ) from exc
            elif row.result_payload is not None:
                if validated.body_cursor is not None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "cursor is not applicable to atomic result payload",
                        identity,
                    )
                result_text = json_doc(row.result_payload)
            elif row.phase == "result_saved":
                if await self._operation_owner_is_cleaned(
                    conn, user_scope=user_scope, session_id=row.session_id, execution_id=row.execution_id
                ):
                    unavailable_reason = "result_cleaned"
                else:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN, "result_saved without saved return", identity
                    )
            elif row.phase == "failed_confirmed":
                unavailable_reason = "failed_confirmed"
            else:
                unavailable_reason = "result_pending"
            return OperationRead(
                operation_id=validated.operation_id,
                session_id=row.session_id,
                execution_id=row.execution_id,
                round_id=row.round_id,
                tool_call_id=row.tool_call_id,
                workflow_id=row.workflow_id,
                step_id=row.step_id,
                call_kind=row.call_kind,
                ordinal=row.ordinal,
                phase=row.phase,
                effect_state=effect_state,
                io_state=row.io_state,
                owner_epoch=row.owner_epoch,
                run_epoch=row.run_epoch,
                grant_id=row.grant_id,
                grant_revision=row.grant_revision,
                parameter_digest=row.parameter_digest,
                request_ref=row.request_ref,
                result_ref=row.result_ref,
                result_payload=json_doc(row.result_payload) if row.result_payload is not None else None,
                worker_refs=json_doc(row.worker_refs) if row.worker_refs is not None else None,
                deadline_at=self._opt_wire_time(row.deadline_at),
                dispatched_at=self._opt_wire_time(row.dispatched_at),
                exited_at=self._opt_wire_time(row.exited_at),
                verified_at=self._opt_wire_time(row.verified_at),
                active_ms=row.active_ms,
                elapsed_ms=row.elapsed_ms,
                error_code=row.error_code,
                error_category=row.error_category,
                artifact_id=row.artifact_id,
                artifact_version=row.artifact_version,
                updated_at=_wire_time(row.updated_at),
                revision=row.revision,
                result_text=result_text,
                next_cursor=next_cursor,
                result_available=result_text is not None,
                unavailable_reason=unavailable_reason,
            )

    async def save_delivery_phase(
        self,
        *,
        user_scope: str,
        operation_id: str,
        expected_revision: int,
        manifest: DeliveryManifest,
    ) -> DeliveryPhaseReceipt:
        """交付阶段 CAS（§4.2/§9.9）：同内容重放幂等；unknown/终态不可绕过。

        - 行不存在或跨用户 → ``INPUT_INVALID``；已存 manifest 属其他 delivery → ``INPUT_CONFLICT``；
        - revision 不符且内容不同 → ``REVISION_CONFLICT``；内容相同返回幂等回执；
        - 非法阶段迁移 → ``INPUT_INVALID``（``unknown``/终态为吸收态）；
        - 首次落库写 artifact_id/version，后续身份不一致 → ``INPUT_CONFLICT``；
        - 坏行（已存 manifest 不可解码）→ ``OPERATION_UNKNOWN``，不静默覆盖。
        """

        self._check_engine()
        identity = f"{user_scope}/{operation_id}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(operation_id, "operation_id", identity)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "expected_revision must be >= 0", identity)
        try:
            wire = encode_delivery_manifest(manifest)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        if manifest.command.user_scope != user_scope:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "manifest user_scope mismatch", identity)

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentOperation.delivery_manifest,
                        AgentOperation.revision,
                        AgentOperation.artifact_id,
                        AgentOperation.artifact_version,
                        AgentOperation.session_id,
                        AgentOperation.execution_id,
                    )
                    .where(
                        AgentOperation.user_scope == user_scope,
                        AgentOperation.operation_id == operation_id,
                    )
                    .with_for_update()
                )
            ).first()
            if row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "operation does not exist", identity)
            if row.delivery_manifest is None and await self._operation_owner_is_cleaned(
                conn, user_scope=user_scope, session_id=row.session_id, execution_id=row.execution_id
            ):
                # 会话已联合清理：交付回执已清，禁止从零重开交付（§10.5 不复活）。
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "session cleaned; delivery cannot be revived", identity
                )
            previous: DeliveryManifest | None = None
            if row.delivery_manifest is not None:
                try:
                    previous = decode_delivery_manifest(row.delivery_manifest)
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        f"stored delivery manifest is invalid: {exc.code}",
                        identity,
                    ) from exc
                if previous.delivery_id != manifest.delivery_id:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "operation already belongs to another delivery",
                        identity,
                    )
            if row.artifact_id is not None and (
                row.artifact_id != manifest.artifact_id or row.artifact_version != manifest.artifact_version
            ):
                raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "delivery artifact identity mismatch", identity)
            if row.revision != expected_revision:
                if row.delivery_manifest is not None and json_doc(row.delivery_manifest) == json_doc(wire):
                    return DeliveryPhaseReceipt(
                        delivery_id=manifest.delivery_id,
                        operation_id=operation_id,
                        phase=manifest.phase,
                        revision=row.revision,
                        artifact_id=manifest.artifact_id,
                        artifact_version=manifest.artifact_version,
                        idempotent=True,
                    )
                raise AgentServiceError(
                    AgentErrorCode.REVISION_CONFLICT, "delivery revision changed concurrently", identity
                )
            if previous is not None:
                try:
                    validate_delivery_transition(previous.phase, manifest.phase)
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity
                    ) from exc
                if previous.phase == manifest.phase and json_doc(row.delivery_manifest) != json_doc(wire):
                    # 同阶段改写（含 unknown/终态、换目标/换摘要）一律冲突：
                    # 阶段只前进不回写，终态与 unknown 必须保持原事实。
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT, "delivery phase already saved with other facts", identity
                    )
            revision = row.revision + 1
            await conn.execute(
                sa.update(AgentOperation)
                .where(
                    AgentOperation.user_scope == user_scope,
                    AgentOperation.operation_id == operation_id,
                    AgentOperation.revision == expected_revision,
                )
                .values(
                    delivery_manifest=wire,
                    artifact_id=manifest.artifact_id,
                    artifact_version=manifest.artifact_version,
                    revision=AgentOperation.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            return DeliveryPhaseReceipt(
                delivery_id=manifest.delivery_id,
                operation_id=operation_id,
                phase=manifest.phase,
                revision=revision,
                artifact_id=manifest.artifact_id,
                artifact_version=manifest.artifact_version,
                idempotent=False,
            )

    async def read_delivery(self, *, user_scope: str, operation_id: str) -> DeliveryRead:
        """查回原交付事实（§10.5）：按原 operation 定位 manifest 与当前修订。

        - 行不存在/跨用户 → ``INPUT_INVALID``；未开始交付返回 ``manifest=None``
          （初始态，不等于失败/unknown）；
        - 已存 manifest 不可解码 → ``OPERATION_UNKNOWN``，不静默当作未开始；
        - 行上 artifact 身份与 manifest 不一致 → ``OPERATION_UNKNOWN``（坏行）；
        - 会话已联合清理（``cleaned_at`` 非空）且该 operation 曾落过交付（artifact
          身份已写）而 manifest 已清 → 返回 ``manifest=None`` + ``manifest_cleaned=True``，
          不得读成「从未交付」（R21）；从未交付者仍报初始态；
        - 只读原事实，不按当前表单重推目标/格式，不改任何状态。
        """

        self._check_engine()
        identity = f"{user_scope}/{operation_id}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(operation_id, "operation_id", identity)

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentOperation.delivery_manifest,
                        AgentOperation.artifact_id,
                        AgentOperation.artifact_version,
                        AgentOperation.revision,
                        AgentOperation.session_id,
                        AgentOperation.execution_id,
                    ).where(
                        AgentOperation.user_scope == user_scope,
                        AgentOperation.operation_id == operation_id,
                    )
                )
            ).first()
            if row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "operation does not exist", identity)
            if row.delivery_manifest is None:
                cleaned = await self._operation_owner_is_cleaned(
                    conn, user_scope=user_scope, session_id=row.session_id, execution_id=row.execution_id
                )
                return DeliveryRead(
                    operation_id=operation_id,
                    revision=row.revision,
                    manifest=None,
                    # 只有曾落过交付（artifact 身份由 save_delivery_phase 写入）
                    # 的 operation 才可报「已清理」，否则会凭空声称有过交付（R21）。
                    manifest_cleaned=cleaned and row.artifact_id is not None,
                )
            try:
                manifest = decode_delivery_manifest(row.delivery_manifest)
            except AgentValidationError as exc:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    f"stored delivery manifest is invalid: {exc.code}",
                    identity,
                ) from exc
        if row.artifact_id is not None and (
            row.artifact_id != manifest.artifact_id or row.artifact_version != manifest.artifact_version
        ):
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "delivery artifact identity mismatch", identity)
        return DeliveryRead(operation_id=operation_id, revision=row.revision, manifest=manifest)

    async def bind_actual_input(self, request: BindActualInputRequest) -> BindActualInputReceipt:
        """首派发实际输入绑定（§9.13/SQ-02 W 首步）：建 agent_round +

        幂等（§9.5 workflow_id UNIQUE）：同 workflow_id **完整身份**（execution /
        node / workflow_code_version / run_epoch / 双摘要）相等 → 查回原事实
        零新行；身份任一不同 → ``INPUT_CONFLICT``（旧实现只比双摘要，跨 execution
        同 workflow 会被误判为重放，检视 D03）。同 execution 已有有效轮
        （active_round）→ ``INPUT_CONFLICT``（§9.5 部分唯一防双有效轮）。

        ``input_revision`` 取自权威有效修订（该 execution 受理/修订单
        ``agent_command.input_revision`` 最大值，受理=1、首次修订=2），不再恒为 1；
        ``run_epoch`` 下限 1，与 ``decode_snapshot_identity`` 快照身份解码一致
        （0 会导致快照身份解码失败，检视 D03）。

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
        _require_int_field(request.run_epoch, "run_epoch", identity, 1)
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
            # 3. workflow 幂等重放（§9.5 UNIQUE(workflow_id)）：完整身份比对。
            existing_round = (
                await conn.execute(
                    sa.select(
                        AgentRound.round_id,
                        AgentRound.round_number,
                        AgentRound.execution_id,
                        AgentRound.input_revision,
                        AgentRound.workflow_code_version,
                        AgentRound.run_epoch,
                        AgentRound.frozen_digest,
                        AgentRound.actual_digest,
                    ).where(
                        AgentRound.user_scope == request.user_scope,
                        AgentRound.workflow_id == request.workflow_id,
                    )
                )
            ).first()
            if existing_round is not None:
                event_row = (
                    await conn.execute(
                        sa.select(
                            AgentMessage.execution_seq,
                            AgentMessage.session_seq,
                            AgentMessage.node_id,
                        ).where(
                            AgentMessage.user_scope == request.user_scope,
                            AgentMessage.round_id == existing_round.round_id,
                            AgentMessage.event_kind == "input_bound",
                        )
                    )
                ).first()
                if event_row is None or event_row.execution_seq is None:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        "original input_bound event unavailable",
                        identity,
                    )
                # 完整身份（execution/node/code/epoch + 双摘要）相等才是重放；
                # 任一不同即同 workflow 异身份 → 冲突，不得跨 execution 冒认原事实。
                if (
                    existing_round.execution_id == request.execution_id
                    and event_row.node_id == request.node_id
                    and existing_round.workflow_code_version == request.workflow_code_version
                    and existing_round.run_epoch == request.run_epoch
                    and existing_round.frozen_digest == frozen_digest
                    and existing_round.actual_digest == actual_digest
                ):
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
                    "workflow already bound with different input identity",
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
            # 5. 权威有效输入版：该 execution 受理/修订单 input_revision 最大值
            #    （受理=1、首次修订=2；队列期多次修订取最新有效版，检视 D03）。
            #    权威版缺失即无可绑定的冻结输入 → 拒绝，不冒充版本 1（R21）。
            accepted_revision = (
                await conn.execute(
                    sa.select(sa.func.max(AgentCommand.input_revision)).where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.execution_id == request.execution_id,
                        AgentCommand.input_revision.is_not(None),
                    )
                )
            ).scalar_one()
            if accepted_revision is None:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    "authoritative accepted input revision unavailable",
                    identity,
                )
            input_revision = _require_int_field(accepted_revision, "input_revision", identity, 1)
            # 6. 序号分配（持锁下 max+1；执行事件双 seq 同设，§9.6 CHECK）。
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

            # 7. round 行（首轮：round_number 由既有轮次派生，首派发即 1；§9.5）。
            await conn.execute(
                sa.insert(AgentRound).values(
                    user_scope=request.user_scope,
                    round_id=round_id,
                    execution_id=request.execution_id,
                    round_number=round_number,
                    workflow_id=request.workflow_id,
                    workflow_code_version=request.workflow_code_version,
                    run_epoch=request.run_epoch,
                    input_revision=input_revision,
                    plan_revision=1,
                    frozen_manifest=json.loads(request.frozen_manifest),
                    actual_input=json.loads(request.actual_input),
                    frozen_digest=frozen_digest,
                    actual_digest=actual_digest,
                    active_round=True,
                )
            )
            # 8. execution 更新：current_round_id + 事件水位 GREATEST + revision CAS 位推进。
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
            # 9. input_bound 执行事件（执行事件 execution/round/execution_seq 同设，§9.6）。
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
                        "input_revision": input_revision,
                    },
                    input_revision=input_revision,
                    occurred_at=occurred,
                    committed_at=occurred,
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
            return BindActualInputReceipt(
                round_id=round_id,
                round_number=round_number,
                input_revision=input_revision,
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

        幂等：同命令键（target=execution_id）同完整输入（正文 + 冻结 manifest +
        期望版/node 身份，``frozen_command_input_digest`` 相等）→ 查回原事实零新行；
        正文、manifest、期望版或 node 任一不同 → ``INPUT_CONFLICT``（旧行摘要仅覆盖
        正文，按存储 manifest 与期望版补核，检视 D05）；revision CAS 败者 →
        ``REVISION_CONFLICT``。
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
        command_input_digest = frozen_command_input_digest(
            "revise",
            request.text,
            request.manifest_payload,
            expected_revision=request.expected_execution_revision,
            node_id=request.node_id,
        )
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
            # 2. 命令键查重（同键同完整输入重放零新事实；异输入冲突，§3.3.2）。
            existing_command = (
                await conn.execute(
                    sa.select(
                        AgentCommand.input_digest,
                        AgentCommand.input_revision,
                        AgentCommand.expected_revision,
                        AgentCommand.request_payload,
                    ).where(
                        AgentCommand.user_scope == request.user_scope,
                        AgentCommand.target_id == request.execution_id,
                        AgentCommand.client_command_id == request.client_command_id,
                    )
                )
            ).first()
            if existing_command is not None:
                if existing_command.input_revision is not None and _command_input_replays(
                    existing_command,
                    text=request.text,
                    manifest_payload=request.manifest_payload,
                    expected_revision=request.expected_execution_revision,
                    command_input_digest=command_input_digest,
                ):
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
                        input_digest=existing_command.input_digest,
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
                    search_text=request.text,
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
                    input_digest=command_input_digest,
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
                input_digest=command_input_digest,
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

    # ------------------------------------------------------------------
    # C-03-1：可靠事件写入与读取（§4.4 包络 + §9.6 统一事件存储）
    # ------------------------------------------------------------------

    async def record_event(self, request: RecordEventRequest) -> EventFact:
        """可靠事件登记事务（§4.4/§9.6）：session/execution 行锁下分配单调水位
        并落 agent_message 行，**单个显式事务**。

        - ``session_seq = session.event_seq + 1``（行锁内分配；写回 GREATEST
          保持单调，R22）；执行事件另分配 ``execution_seq`` 并同步
          ``execution.event_seq``；
        - ``event_id`` 由 (execution/round/node/native_event_id) 规范组合派生；
          同 (session_id, event_id) 同完整输入（message_id、occurred_at、kind/主题/
          载荷/role/正文等全部行字段一致）→ 重放返回原事实（replayed=True），任一
          字段不同 → ``INPUT_CONFLICT``（§9.6 去重身份语义；检视 D05 补齐身份与
          正文比较）。``occurred_at`` 为冻结事件事实时刻，重放须与首次一致；不同
          即异输入冲突，原事实不变，不静默改用请求时刻。
        - 执行事件必须同时有 round/node（§9.6），round 须归属该 execution；
        - ``body_text``/``body_ref`` 互斥（§9.6）；committed_at 取库时钟。

        Raises:
            AgentServiceError: INPUT_INVALID（会话/执行/轮缺失、身份不一致、
                载荷非规范 JSON、正文互斥破坏）/ INPUT_CONFLICT（同事件身份异
                载荷）/ PERSISTENCE_UNAVAILABLE（其余约束失败）。
        """

        self._check_engine()
        identity_str = f"{request.user_scope}/{request.session_id}/{request.native_event_id}"
        # 入口校验（身份派生 + 包络结构 + 行载荷一致性）。
        if not isinstance(request.message_id, str) or request.message_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "message_id must be non-empty text", identity_str)
        if request.execution_id is not None:
            if request.round_id is None or request.node_id is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "execution event requires round and node identity",
                    identity_str,
                )
        if request.body_text is not None and request.body_ref is not None:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "body_text and body_ref are mutually exclusive",
                identity_str,
            )
        try:
            decode_json_doc(request.payload_canonical_json)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, exc.code, identity_str) from exc
        event_id = derive_event_identity(
            execution_id=request.execution_id,
            round_id=request.round_id,
            node_id=request.node_id,
            native_event_id=request.native_event_id,
        )
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. session 行锁（水位单调 + session_seq 分配串行化）。
            session_row = (
                await conn.execute(
                    sa.select(AgentSession.event_seq)
                    .where(
                        AgentSession.user_scope == request.user_scope,
                        AgentSession.session_id == request.session_id,
                    )
                    .with_for_update()
                )
            ).first()
            if session_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session does not exist", identity_str)
            execution_row = None
            if request.execution_id is not None:
                # 2. execution 行锁 + 轮归属核定（§9.6 node 与 round 所属 execution 核定）。
                execution_row = (
                    await conn.execute(
                        sa.select(AgentExecution.event_seq)
                        .where(
                            AgentExecution.user_scope == request.user_scope,
                            AgentExecution.execution_id == request.execution_id,
                        )
                        .with_for_update()
                    )
                ).first()
                if execution_row is None:
                    raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "execution does not exist", identity_str)
                round_row = (
                    await conn.execute(
                        sa.select(AgentRound.execution_id).where(
                            AgentRound.user_scope == request.user_scope,
                            AgentRound.round_id == request.round_id,
                        )
                    )
                ).first()
                if round_row is None or round_row.execution_id != request.execution_id:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "round does not belong to execution",
                        identity_str,
                    )
            # 3. 水位分配（行锁内单调 +1）。
            new_session_seq = session_row.event_seq + 1
            new_execution_seq = execution_row.event_seq + 1 if execution_row is not None else None
            occurred = _wire_to_dt(request.occurred_at)
            if occurred is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "occurred_at must be UTC wire text", identity_str)
            committed = datetime.now(UTC)
            try:
                # 唯一约束冲突分类需要回查原始行：用 SAVEPOINT 包裹插入，
                # 冲突时仅回滚 savepoint，外层事务保持可用（§9.13 原子协议）。
                async with conn.begin_nested():
                    await conn.execute(
                        sa.insert(AgentMessage).values(
                            user_scope=request.user_scope,
                            message_id=request.message_id,
                            session_id=request.session_id,
                            execution_id=request.execution_id,
                            round_id=request.round_id,
                            node_id=request.node_id,
                            event_id=event_id,
                            session_seq=new_session_seq,
                            execution_seq=new_execution_seq,
                            event_kind=request.event_kind.value,
                            payload_kind=request.payload_kind,
                            subject_id=request.subject_id,
                            subject_revision=request.subject_revision,
                            role=request.role.value if request.role is not None else None,
                            role_id=request.role_id,
                            role_version=request.role_version,
                            body_text=request.body_text,
                            body_ref=request.body_ref,
                            payload=json.loads(request.payload_canonical_json)
                            if request.payload_canonical_json != ""
                            else None,
                            input_revision=request.input_revision,
                            occurred_at=occurred,
                            committed_at=committed,
                            native_fact_ref=request.native_fact_ref,
                            tool_call_id=request.tool_call_id,
                            search_text=request.search_text,
                        )
                    )
            except IntegrityError as exc:
                # 4. 去重身份冲突分类（§9.13）：按 SQLSTATE 分——23505 唯一索引
                # （重放/冲突）；23503 FK（INPUT_INVALID）；其余 PERSISTENCE_UNAVAILABLE。
                sqlstate = getattr(exc.orig, "sqlstate", None) or ""
                if sqlstate != "23505":
                    if sqlstate == "23503":
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID,
                            "event references missing session/execution/round",
                            identity_str,
                        ) from exc
                    raise AgentServiceError(
                        AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                        "event persistence failed",
                        identity_str,
                    ) from exc
                existing = (
                    await conn.execute(
                        sa.select(
                            AgentMessage.message_id,
                            AgentMessage.event_id,
                            AgentMessage.session_seq,
                            AgentMessage.execution_seq,
                            AgentMessage.event_kind,
                            AgentMessage.subject_id,
                            AgentMessage.subject_revision,
                            AgentMessage.payload_kind,
                            AgentMessage.payload,
                            AgentMessage.input_revision,
                            AgentMessage.role,
                            AgentMessage.role_id,
                            AgentMessage.role_version,
                            AgentMessage.body_text,
                            AgentMessage.body_ref,
                            AgentMessage.search_text,
                            AgentMessage.native_fact_ref,
                            AgentMessage.tool_call_id,
                            AgentMessage.committed_at,
                        ).where(
                            AgentMessage.user_scope == request.user_scope,
                            AgentMessage.session_id == request.session_id,
                            AgentMessage.event_id == event_id,
                        )
                    )
                ).first()
                if existing is None:
                    raise AgentServiceError(
                        AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                        "event persistence conflict without recoverable original",
                        identity_str,
                    ) from exc
                # 完整输入比较（检视 D05）：同事件身份下 message_id/occurred_at、
                # kind/主题/载荷与全部消息行字段一致才算重放；换正文/role/kind 等
                # 任一字段即异输入冲突。
                same_payload = (
                    existing.message_id == request.message_id
                    and existing.occurred_at == occurred
                    and existing.event_kind == request.event_kind.value
                    and existing.subject_id == request.subject_id
                    and existing.subject_revision == request.subject_revision
                    and existing.payload_kind == request.payload_kind
                    and existing.input_revision == request.input_revision
                    and existing.role == (request.role.value if request.role is not None else None)
                    and existing.role_id == request.role_id
                    and existing.role_version == request.role_version
                    and existing.body_text == request.body_text
                    and existing.body_ref == request.body_ref
                    and existing.search_text == request.search_text
                    and existing.native_fact_ref == request.native_fact_ref
                    and existing.tool_call_id == request.tool_call_id
                    and json_doc(existing.payload) == json_doc(json.loads(request.payload_canonical_json))
                )
                if not same_payload:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "same event identity with different payload",
                        identity_str,
                    ) from exc
                return EventFact(
                    message_id=existing.message_id,
                    event_id=existing.event_id,
                    session_seq=existing.session_seq,
                    execution_seq=existing.execution_seq,
                    session_event_seq=session_row.event_seq,
                    execution_event_seq=execution_row.event_seq if execution_row is not None else None,
                    committed_at=_wire_time(existing.committed_at),
                    replayed=True,
                )
            # 5. 水位写回（GREATEST 单调，R22；行锁下无竞态）。
            await conn.execute(
                sa.update(AgentSession)
                .where(
                    AgentSession.user_scope == request.user_scope,
                    AgentSession.session_id == request.session_id,
                )
                .values(
                    event_seq=sa.func.greatest(AgentSession.event_seq, new_session_seq),
                    updated_at=committed,
                )
            )
            if execution_row is not None and new_execution_seq is not None:
                await conn.execute(
                    sa.update(AgentExecution)
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id == request.execution_id,
                    )
                    .values(
                        event_seq=sa.func.greatest(AgentExecution.event_seq, new_execution_seq),
                        updated_at=committed,
                    )
                )
            # 全文投影同事务落列后发出监听通知（§9.13「通知只唤醒读取，数据库事件才是事实」；
            # observe 迭代器经 LISTEN 唤醒后仍以新事务读库为准）。payload ≤8000B 合同。
            await conn.execute(
                sa.text("SELECT pg_notify(:channel, :payload)").bindparams(
                    sa.bindparam("channel", _AGENT_EVENT_CHANNEL),
                    sa.bindparam(
                        "payload",
                        json_doc({"user_scope": request.user_scope, "session_id": request.session_id}),
                    ),
                )
            )
            return EventFact(
                message_id=request.message_id,
                event_id=event_id,
                session_seq=new_session_seq,
                execution_seq=new_execution_seq,
                session_event_seq=new_session_seq,
                execution_event_seq=new_execution_seq,
                committed_at=_wire_time(committed),
                replayed=False,
            )

    async def read_events(self, query: EventReadQuery) -> EventPage:
        """可靠事件按序读取（§4.5 追平/补齐）：after_seq 起升序分页。

        每行严格解码为 ResearchEvent 包络（坏载荷 fail-closed OPERATION_UNKNOWN，
        R21）；role/body/search 等行级扩展字段不在包络内，由读取面任务承载。
        """

        self._check_engine()
        identity_str = f"{query.user_scope}/{query.session_id}"
        if not isinstance(query.user_scope, str) or query.user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity_str)
        if not isinstance(query.session_id, str) or query.session_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session_id must be non-empty text", identity_str)
        if isinstance(query.after_seq, bool) or not isinstance(query.after_seq, int) or query.after_seq < 0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "after_seq must be >=0 int", identity_str)
        if isinstance(query.limit, bool) or not isinstance(query.limit, int) or not 1 <= query.limit <= 500:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "limit must be within 1..500", identity_str)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        AgentMessage.user_scope,
                        AgentMessage.message_id,
                        AgentMessage.session_id,
                        AgentMessage.execution_id,
                        AgentMessage.node_id,
                        AgentMessage.round_id,
                        AgentMessage.event_id,
                        AgentMessage.session_seq,
                        AgentMessage.execution_seq,
                        AgentMessage.event_kind,
                        AgentMessage.payload_kind,
                        AgentMessage.subject_id,
                        AgentMessage.subject_revision,
                        AgentMessage.input_revision,
                        AgentMessage.occurred_at,
                        AgentMessage.committed_at,
                        AgentMessage.native_fact_ref,
                        AgentMessage.payload,
                    )
                    .where(
                        AgentMessage.user_scope == query.user_scope,
                        AgentMessage.session_id == query.session_id,
                        AgentMessage.session_seq > query.after_seq,
                    )
                    .order_by(AgentMessage.session_seq.asc())
                    .limit(query.limit + 1)
                )
            ).all()
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        events: list[ResearchEvent] = []
        for row in rows:
            try:
                events.append(_event_from_row(row))
            except (AgentValidationError, ValueError) as exc:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN,
                    f"persisted event cannot be reliably decoded: {exc}",
                    identity_str,
                ) from exc
        last_seq = events[-1].session_seq if events else query.after_seq
        return EventPage(events=tuple(events), last_seq=last_seq, has_more=has_more)

    # ------------------------------------------------------------------
    # §4.2/§9.13 会话首读（C-03-2b）
    # ------------------------------------------------------------------

    @staticmethod
    def _opt_wire_time(moment: datetime | None) -> str | None:
        return None if moment is None else _wire_time(moment)

    def _session_summary_from_row(self, row: Any) -> SessionSummary:
        """agent_session 行 → SessionSummary（严格解码，坏行 fail-closed）。"""

        try:
            return decode_session_summary(
                {
                    "user_scope": row.user_scope,
                    "session_id": row.session_id,
                    "title": row.title,
                    "title_user_set": row.title_user_set,
                    "pinned": row.pinned,
                    "archived": row.archived,
                    "draft_revision": row.draft_revision,
                    "event_seq": row.event_seq,
                    "created_at": _wire_time(row.created_at),
                    "updated_at": _wire_time(row.updated_at),
                    "revision": row.revision,
                    "draft_transfer_ref": row.draft_transfer_ref,
                    "causal_head_ref": row.causal_head_ref,
                    "source_module": row.source_module,
                    "origin_ref": row.origin_ref,
                    "parent_session_id": row.parent_session_id,
                    "branch_metadata": json_doc(row.branch_metadata) if row.branch_metadata is not None else None,
                    "cleaned_at": self._opt_wire_time(row.cleaned_at),
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted session row cannot be reliably decoded: {exc}",
                f"{row.user_scope}/{row.session_id}",
            ) from exc

    def _execution_summary_from_row(self, row: Any, identity_str: str) -> ExecutionSummary:
        try:
            return decode_execution_summary(
                {
                    "user_scope": row.user_scope,
                    "session_id": row.session_id,
                    "execution_id": row.execution_id,
                    "execution_kind": row.execution_kind,
                    "node_id": row.node_id,
                    "display_state": row.display_state,
                    "goal_coverage": row.goal_coverage,
                    "owner_epoch": row.owner_epoch,
                    "dispatch_closed": row.dispatch_closed,
                    "deletion_fenced": row.deletion_fenced,
                    "model_attempt_count": row.model_attempt_count,
                    "tool_call_count": row.tool_call_count,
                    "active_ms": row.active_ms,
                    "event_seq": row.event_seq,
                    "created_at": _wire_time(row.created_at),
                    "updated_at": _wire_time(row.updated_at),
                    "revision": row.revision,
                    "source_module": row.source_module,
                    "business_request_id": row.business_request_id,
                    "intent_digest": row.intent_digest,
                    "parent_execution_id": row.parent_execution_id,
                    "comparison_group_ref": row.comparison_group_ref,
                    "comparison_lane_ref": row.comparison_lane_ref,
                    "current_round_id": row.current_round_id,
                    "last_verified_at": self._opt_wire_time(row.last_verified_at),
                    "ended_at": self._opt_wire_time(row.ended_at),
                    "unknown_refs": json_doc(row.unknown_refs) if row.unknown_refs is not None else None,
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted execution row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    def _message_item_from_row(self, row: Any, identity_str: str) -> MessagePageItem:
        try:
            return decode_message_page_item(
                {
                    "message_id": row.message_id,
                    "session_id": row.session_id,
                    "event_id": row.event_id,
                    "session_seq": row.session_seq,
                    "event_kind": row.event_kind,
                    "payload_kind": row.payload_kind,
                    "subject_id": row.subject_id,
                    "subject_revision": row.subject_revision,
                    "occurred_at": _wire_time(row.occurred_at),
                    "committed_at": _wire_time(row.committed_at),
                    "execution_id": row.execution_id,
                    "round_id": row.round_id,
                    "node_id": row.node_id,
                    "execution_seq": row.execution_seq,
                    "input_revision": row.input_revision,
                    "role": row.role,
                    "role_id": row.role_id,
                    "role_version": row.role_version,
                    "body_text": row.body_text,
                    "body_ref": row.body_ref,
                    "payload": json_doc(row.payload) if row.payload is not None else None,
                    "native_fact_ref": row.native_fact_ref,
                    "tool_call_id": row.tool_call_id,
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted message row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    def _artifact_item_from_row(self, row: Any, identity_str: str) -> ArtifactPageItem:
        try:
            return decode_artifact_page_item(
                {
                    "artifact_id": row.artifact_id,
                    "artifact_version": row.artifact_version,
                    "session_id": row.session_id,
                    "execution_id": row.execution_id,
                    "round_id": row.round_id,
                    "artifact_kind": row.artifact_kind,
                    "input_revision": row.input_revision,
                    "actual_input_digest": row.actual_input_digest,
                    "actual_model_ref": row.actual_model_ref,
                    "result_digest": row.result_digest,
                    "source_available_state": row.source_available_state,
                    "created_at": _wire_time(row.created_at),
                    "updated_at": _wire_time(row.updated_at),
                    "revision": row.revision,
                    "summary": row.summary,
                    "body_text": row.body_text,
                    "body_ref": row.body_ref,
                    "result_payload": json_doc(row.result_payload) if row.result_payload is not None else None,
                    "published_at": self._opt_wire_time(row.published_at),
                    "cleaned_at": self._opt_wire_time(row.cleaned_at),
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted artifact row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    def _decision_summary_from_row(self, row: Any, identity_str: str) -> DecisionSummary:
        try:
            return decode_decision_summary(
                {
                    "decision_id": row.decision_id,
                    "session_id": row.session_id,
                    "execution_id": row.execution_id,
                    "round_id": row.round_id,
                    "node_id": row.node_id,
                    "decision_kind": row.decision_kind,
                    "presentation_batch_seq": row.presentation_batch_seq,
                    "created_at": _wire_time(row.created_at),
                    "updated_at": _wire_time(row.updated_at),
                    "revision": row.revision,
                    "tool_call_id": row.tool_call_id,
                    "question_payload": json_doc(row.question_payload) if row.question_payload is not None else None,
                    "answer_schema": json_doc(row.answer_schema) if row.answer_schema is not None else None,
                    "parameter_digest": row.parameter_digest,
                    "source_scope_digest": row.source_scope_digest,
                    "service_ref": row.service_ref,
                    "tool_ref": row.tool_ref,
                    "tool_schema_version": row.tool_schema_version,
                    "expires_at": self._opt_wire_time(row.expires_at),
                    "answer_consumed_at": self._opt_wire_time(row.answer_consumed_at),
                    "answer_command_id": row.answer_command_id,
                    "grant_id": row.grant_id,
                    "grant_revision": row.grant_revision,
                    "grant_state": row.grant_state,
                    "dispatch_operation_id": row.dispatch_operation_id,
                    "data_consent_payload": (
                        json_doc(row.data_consent_payload) if row.data_consent_payload is not None else None
                    ),
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted decision row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    # ------------------------------------------------------------------
    # §4.2/§4.3 首屏分页 helper（get_session_read / get_execution_snapshot 共用）
    # ------------------------------------------------------------------

    async def _load_message_page(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        session_id: str,
        execution_id: str | None,
        round_id: str | None,
        cursor_seq: int | None,
        limit: int,
        identity_str: str,
    ) -> MessagePage:
        """消息首屏页：session_seq DESC keyset（游标 m:{seq}）。"""

        stmt = (
            sa.select(
                AgentMessage.message_id,
                AgentMessage.session_id,
                AgentMessage.event_id,
                AgentMessage.session_seq,
                AgentMessage.event_kind,
                AgentMessage.payload_kind,
                AgentMessage.subject_id,
                AgentMessage.subject_revision,
                AgentMessage.occurred_at,
                AgentMessage.committed_at,
                AgentMessage.execution_id,
                AgentMessage.round_id,
                AgentMessage.node_id,
                AgentMessage.execution_seq,
                AgentMessage.input_revision,
                AgentMessage.role,
                AgentMessage.role_id,
                AgentMessage.role_version,
                AgentMessage.body_text,
                AgentMessage.body_ref,
                AgentMessage.payload,
                AgentMessage.native_fact_ref,
                AgentMessage.tool_call_id,
            )
            .where(
                AgentMessage.user_scope == user_scope,
                AgentMessage.session_id == session_id,
            )
            .order_by(AgentMessage.session_seq.desc())
            .limit(limit + 1)
        )
        if execution_id is not None:
            stmt = stmt.where(AgentMessage.execution_id == execution_id)
        if round_id is not None:
            stmt = stmt.where(AgentMessage.round_id == round_id)
        if cursor_seq is not None:
            stmt = stmt.where(AgentMessage.session_seq < cursor_seq)
        rows = (await conn.execute(stmt)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(self._message_item_from_row(row, identity_str) for row in rows)
        return MessagePage(
            items=items,
            has_more=has_more,
            next_cursor=f"m:{items[-1].session_seq}" if has_more else None,
        )

    async def _load_artifact_page(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        session_id: str,
        execution_id: str | None,
        round_id: str | None,
        cursor_key: tuple[datetime, str, int] | None,
        limit: int,
        identity_str: str,
    ) -> ArtifactPage:
        """成果首屏页：可靠保存时点 DESC（published_at，NULL 以 created_at 承载），
        同点 artifact_id/version DESC tie-break，keyset 游标 a:{ts}:{id}:{ver}。"""

        sort_key = sa.func.coalesce(AgentArtifact.published_at, AgentArtifact.created_at)
        stmt = sa.select(
            AgentArtifact.artifact_id,
            AgentArtifact.artifact_version,
            AgentArtifact.session_id,
            AgentArtifact.execution_id,
            AgentArtifact.round_id,
            AgentArtifact.artifact_kind,
            AgentArtifact.input_revision,
            AgentArtifact.actual_input_digest,
            AgentArtifact.actual_model_ref,
            AgentArtifact.result_digest,
            AgentArtifact.source_available_state,
            AgentArtifact.created_at,
            AgentArtifact.updated_at,
            AgentArtifact.revision,
            AgentArtifact.summary,
            AgentArtifact.body_text,
            AgentArtifact.body_ref,
            AgentArtifact.result_payload,
            AgentArtifact.published_at,
            AgentArtifact.cleaned_at,
            sort_key.label("sort_moment"),
        ).where(
            AgentArtifact.user_scope == user_scope,
            AgentArtifact.session_id == session_id,
        )
        if execution_id is not None:
            stmt = stmt.where(AgentArtifact.execution_id == execution_id)
        if round_id is not None:
            stmt = stmt.where(AgentArtifact.round_id == round_id)
        if cursor_key is not None:
            cursor_moment, cursor_artifact_id, cursor_version = cursor_key
            stmt = stmt.where(
                sa.tuple_(
                    sort_key,
                    AgentArtifact.artifact_id,
                    AgentArtifact.artifact_version,
                )
                < sa.tuple_(
                    sa.bindparam("cursor_moment", cursor_moment),
                    sa.bindparam("cursor_artifact_id", cursor_artifact_id),
                    sa.bindparam("cursor_version", cursor_version),
                )
            )
        stmt = stmt.order_by(
            sort_key.desc(),
            AgentArtifact.artifact_id.desc(),
            AgentArtifact.artifact_version.desc(),
        ).limit(limit + 1)
        rows = (await conn.execute(stmt)).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(self._artifact_item_from_row(row, identity_str) for row in rows)
        next_cursor: str | None = None
        if has_more:
            next_cursor = f"a:{_wire_time(rows[-1].sort_moment)}:{rows[-1].artifact_id}:{rows[-1].artifact_version}"
        return ArtifactPage(items=items, has_more=has_more, next_cursor=next_cursor)

    async def _load_decisions(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        session_id: str,
        execution_id: str | None,
        round_id: str | None,
        identity_str: str,
    ) -> tuple[DecisionSummary, ...]:
        """待决定列表：首屏全量，批次序稳定输出；原回执经 answer_command_id 引用按需查询。"""

        stmt = sa.select(AgentDecision).where(
            AgentDecision.user_scope == user_scope,
            AgentDecision.session_id == session_id,
        )
        if execution_id is not None:
            stmt = stmt.where(AgentDecision.execution_id == execution_id)
        if round_id is not None:
            stmt = stmt.where(AgentDecision.round_id == round_id)
        rows = (
            await conn.execute(
                stmt.order_by(AgentDecision.presentation_batch_seq.asc(), AgentDecision.decision_id.asc())
            )
        ).all()
        return tuple(self._decision_summary_from_row(row, identity_str) for row in rows)

    @staticmethod
    def _parse_session_cursors(
        message_cursor: str | None, artifact_cursor: str | None, identity_str: str
    ) -> tuple[int | None, tuple[datetime, str, int] | None]:
        """游标解析为 keyset 键值（语法已由 validate_session_read_query 冻结）。

        游标 ``a:{ts}:{id}:{ver}`` 中 ts 自身含冒号（YYYY-MM-DDTHH:MM:SS.ffffffZ，
        恰 3 段），id 段冻结不含冒号，ver 为末段。语法冻结不核日期取值
        （正则放过 9999-99-99 等），strptime 失败按 INPUT_INVALID 拒绝
        （检视 m-1：裸 ValueError 不得逃逸受理边界）。
        """

        message_cursor_seq: int | None = None
        if message_cursor is not None:
            message_cursor_seq = int(message_cursor[2:])
        artifact_cursor_key: tuple[datetime, str, int] | None = None
        if artifact_cursor is not None:
            parts = artifact_cursor[2:].split(":")
            wire_ts, artifact_id_part, version_part = ":".join(parts[:3]), parts[3], parts[4]
            try:
                cursor_moment = datetime.strptime(wire_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
            except ValueError as exc:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "artifact cursor contains invalid date value", identity_str
                ) from exc
            artifact_cursor_key = (cursor_moment, artifact_id_part, int(version_part))
        return message_cursor_seq, artifact_cursor_key

    async def get_session_read(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead:
        """会话首读（§4.2 read_session + §9.13）：一致读取截点下的五要素组装。

        - 受理校验：结构校验（validate_session_read_query）+ round_id 必须伴随
          execution_id；session 不在用户域 → INPUT_INVALID；user_scope 为独立
          关键字参数（SessionReadQuery 合同无 user_scope 键，wire 冻结 7 键）；
        - draft_revision 提供时不符当前草稿修订 → REVISION_CONFLICT（乐观读取前提，
          草稿正文不在此读取面返回）；
        - 单事务 REPEATABLE READ 截点读五要素：summary 行投影 / executions 摘要 /
          消息成果首屏分页 / 待决定列表；行级严格解码 fail-closed OPERATION_UNKNOWN（R21）；
        - 分页：消息按 session_seq DESC keyset 游标 ``m:{seq}``；成果按可靠保存时点
          DESC（published_at，NULL 时以 created_at 承载排序——工程裁决，未发布行
          不静默截断）游标 ``a:{ts}:{id}:{ver}``；
        - 目标 execution/round 可选过滤：execution 须属本 session、round 须属该
          execution（精确用户域，§9.13），错配 INPUT_INVALID；指定时五要素聚焦该目标。
        """

        self._check_engine()
        identity_str = f"{user_scope}/{query.session_id}"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity_str)
        if not isinstance(query.session_id, str) or query.session_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session_id must be non-empty text", identity_str)
        try:
            validate_session_read_query(query)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity_str
            ) from exc
        if query.round_id is not None and query.execution_id is None:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "round_id target requires execution_id", identity_str)
        message_cursor_seq, artifact_cursor_key = self._parse_session_cursors(
            query.message_cursor, query.artifact_cursor, identity_str
        )

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                summary_row = (
                    await conn.execute(
                        sa.select(AgentSession).where(
                            AgentSession.user_scope == user_scope,
                            AgentSession.session_id == query.session_id,
                        )
                    )
                ).first()
                if summary_row is None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, "session not found in user scope", identity_str
                    )
                summary = self._session_summary_from_row(summary_row)
                if query.draft_revision is not None and query.draft_revision != summary.draft_revision:
                    raise AgentServiceError(
                        AgentErrorCode.REVISION_CONFLICT,
                        "draft revision does not match current session draft",
                        identity_str,
                    )

                if query.execution_id is not None:
                    execution_exists = (
                        await conn.execute(
                            sa.select(sa.literal(1)).where(
                                AgentExecution.user_scope == user_scope,
                                AgentExecution.execution_id == query.execution_id,
                                AgentExecution.session_id == query.session_id,
                            )
                        )
                    ).first()
                    if execution_exists is None:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID, "target execution not in session", identity_str
                        )
                    if query.round_id is not None:
                        round_exists = (
                            await conn.execute(
                                sa.select(sa.literal(1)).where(
                                    AgentRound.user_scope == user_scope,
                                    AgentRound.round_id == query.round_id,
                                    AgentRound.execution_id == query.execution_id,
                                )
                            )
                        ).first()
                        if round_exists is None:
                            raise AgentServiceError(
                                AgentErrorCode.INPUT_INVALID, "target round not in execution", identity_str
                            )

                # executions 摘要（目标过滤时聚焦该执行；created_at 稳定序）。
                executions_stmt = sa.select(AgentExecution).where(
                    AgentExecution.user_scope == user_scope,
                    AgentExecution.session_id == query.session_id,
                )
                if query.execution_id is not None:
                    executions_stmt = executions_stmt.where(AgentExecution.execution_id == query.execution_id)
                execution_rows = (
                    await conn.execute(
                        executions_stmt.order_by(AgentExecution.created_at.asc(), AgentExecution.execution_id.asc())
                    )
                ).all()
                executions = tuple(self._execution_summary_from_row(row, identity_str) for row in execution_rows)

                # 消息首屏：session_seq DESC keyset（游标 m:{seq}）。
                messages = await self._load_message_page(
                    conn,
                    user_scope=user_scope,
                    session_id=query.session_id,
                    execution_id=query.execution_id,
                    round_id=query.round_id,
                    cursor_seq=message_cursor_seq,
                    limit=query.limit,
                    identity_str=identity_str,
                )
                artifacts = await self._load_artifact_page(
                    conn,
                    user_scope=user_scope,
                    session_id=query.session_id,
                    execution_id=query.execution_id,
                    round_id=query.round_id,
                    cursor_key=artifact_cursor_key,
                    limit=query.limit,
                    identity_str=identity_str,
                )
                decisions = await self._load_decisions(
                    conn,
                    user_scope=user_scope,
                    session_id=query.session_id,
                    execution_id=query.execution_id,
                    round_id=query.round_id,
                    identity_str=identity_str,
                )

        return SessionRead(
            summary=summary,
            executions=executions,
            messages=messages,
            artifacts=artifacts,
            decisions=decisions,
        )

    def _operation_summary_from_row(self, row: Any, identity_str: str) -> OperationSummary:
        try:
            return decode_operation_summary(
                {
                    "operation_id": row.operation_id,
                    "phase": row.phase,
                    "effect_state": row.effect_state,
                    "io_state": row.io_state,
                    "owner_epoch": row.owner_epoch,
                    "run_epoch": row.run_epoch,
                    "active_ms": row.active_ms,
                    "elapsed_ms": row.elapsed_ms,
                    "session_id": row.session_id,
                    "execution_id": row.execution_id,
                    "round_id": row.round_id,
                    "tool_call_id": row.tool_call_id,
                    "grant_id": row.grant_id,
                    "grant_revision": row.grant_revision,
                    "result_ref": row.result_ref,
                    "error_code": row.error_code,
                    "error_category": row.error_category,
                    "deadline_at": self._opt_wire_time(row.deadline_at),
                    "dispatched_at": self._opt_wire_time(row.dispatched_at),
                    "exited_at": self._opt_wire_time(row.exited_at),
                    "verified_at": self._opt_wire_time(row.verified_at),
                    "artifact_id": row.artifact_id,
                    "artifact_version": row.artifact_version,
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted operation row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    def _source_read_summary_from_row(self, row: Any, identity_str: str) -> SourceReadSummary:
        """agent_source_link join agent_asset_version 行 → SourceReadSummary。"""

        try:
            return decode_source_read_summary(
                {
                    "link_id": row.link_id,
                    "source_id": row.source_id,
                    "source_version": row.source_version,
                    "source_kind": row.asset_kind,
                    "display_name": row.display_name,
                    "digest": row.digest,
                    "read_state": row.read_state,
                    "verification_state": row.verification_state,
                    "consumer_kind": row.consumer_kind,
                    "consumer_id": row.consumer_id,
                    "consumer_version": row.consumer_version,
                    "relation_kind": row.relation_kind,
                    "disabled_inherited": row.disabled_inherited,
                    "tombstoned": row.tombstoned,
                    "available_at": self._opt_wire_time(row.available_at),
                    "disabled_at": self._opt_wire_time(row.disabled_at),
                    "locator_payload": json_doc(row.locator_payload) if row.locator_payload is not None else None,
                    "claim_ref": row.claim_ref,
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted source link row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    def _read_mark_entry_from_row(self, row: Any, identity_str: str) -> ReadMarkEntry:
        try:
            return decode_read_mark_entry(
                {
                    "marker_id": row.marker_id,
                    "marker_kind": row.marker_kind,
                    "target_id": row.target_id,
                    "last_seq": row.last_seq,
                    "batch_seq": row.batch_seq,
                    "postponed_through_seq": row.postponed_through_seq,
                    "session_id": row.session_id,
                    "read_targets": list(row.read_targets) if row.read_targets is not None else None,
                }
            )
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted read mark row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    async def get_execution_snapshot(
        self,
        execution_id: str,
        *,
        user_scope: str,
        limit: int = 50,
        round_id: str | None = None,
        message_cursor: str | None = None,
        artifact_cursor: str | None = None,
    ) -> ResearchSnapshot:
        """执行快照（§4.3 完整结构 + §9.13 一致读取截点）：VM 按准确执行身份按需加载。

        - 受理校验：execution_id/user_scope 非空、limit 1..50、游标语法（复用
          validate_session_read_query 冻结合同）；execution 不在用户域 → INPUT_INVALID；
        - round 语义：round_id 指定则须属该 execution（错配 INPUT_INVALID）；
          未指定时取 execution.current_round_id（queued 无 round → identity 全 None 组，
          frozen_scope/actual_input None）；
        - 单事务 REPEATABLE READ 截点：identity（round↔workflow 耦合）/revision/
          双水位（execution.event_seq + session.event_seq）/execution 摘要 /一层
          叶子 nodes（parent_execution_id）/消息成果分页（session 域内按 execution
          [+round] 过滤）/source_reads（source_link join asset_version）/decisions/
          operation_summaries/usage（cost_state 恒 unknown，R21 不填 0）/read_marks/
          observation.reliable=True（一致截点读出，不冒认可靠）；
        - limitations 恒空元组：本读取面未核定到条目 ≠「无限制」承诺（脱敏限制
          记录随来源/清理任务接入后扩展，工程裁决留痕）。
        """

        self._check_engine()
        identity_str = f"{user_scope}/{execution_id}"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity_str)
        if not isinstance(execution_id, str) or execution_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "execution_id must be non-empty text", identity_str)
        try:
            # 游标/limit 语法复用 SessionReadQuery 冻结合同（session_id 占位不影响校验）。
            validate_session_read_query(
                SessionReadQuery(
                    session_id="probe",
                    limit=limit,
                    message_cursor=message_cursor,
                    artifact_cursor=artifact_cursor,
                )
            )
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity_str
            ) from exc
        message_cursor_seq, artifact_cursor_key = self._parse_session_cursors(
            message_cursor, artifact_cursor, identity_str
        )

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                execution_row = (
                    await conn.execute(
                        sa.select(AgentExecution).where(
                            AgentExecution.user_scope == user_scope,
                            AgentExecution.execution_id == execution_id,
                        )
                    )
                ).first()
                if execution_row is None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, "execution not found in user scope", identity_str
                    )
                execution_summary = self._execution_summary_from_row(execution_row, identity_str)
                session_id = execution_row.session_id

                session_row = (
                    await conn.execute(
                        sa.select(AgentSession.event_seq).where(
                            AgentSession.user_scope == user_scope,
                            AgentSession.session_id == session_id,
                        )
                    )
                ).first()
                if session_row is None:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN, "session of execution not found", identity_str
                    )

                effective_round_id = round_id if round_id is not None else execution_row.current_round_id
                round_row = None
                if effective_round_id is not None:
                    round_row = (
                        await conn.execute(
                            sa.select(AgentRound).where(
                                AgentRound.user_scope == user_scope,
                                AgentRound.round_id == effective_round_id,
                                AgentRound.execution_id == execution_id,
                            )
                        )
                    ).first()
                    if round_row is None:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID, "target round not in execution", identity_str
                        )

                try:
                    if round_row is not None:
                        identity = decode_snapshot_identity(
                            {
                                "user_scope": user_scope,
                                "session_id": session_id,
                                "execution_id": execution_id,
                                "node_id": execution_row.node_id,
                                "round_id": round_row.round_id,
                                "input_revision": round_row.input_revision,
                                "workflow_id": round_row.workflow_id,
                                "run_epoch": round_row.run_epoch,
                            }
                        )
                    else:
                        identity = decode_snapshot_identity(
                            {
                                "user_scope": user_scope,
                                "session_id": session_id,
                                "execution_id": execution_id,
                                "node_id": execution_row.node_id,
                                "round_id": None,
                                "input_revision": None,
                                "workflow_id": None,
                                "run_epoch": None,
                            }
                        )
                except (AgentValidationError, ValueError) as exc:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        f"persisted execution/round rows cannot form a reliable identity: {exc}",
                        identity_str,
                    ) from exc

                # 一层叶子 nodes：parent_execution_id = 本执行。
                node_rows = (
                    await conn.execute(
                        sa.select(AgentExecution)
                        .where(
                            AgentExecution.user_scope == user_scope,
                            AgentExecution.parent_execution_id == execution_id,
                        )
                        .order_by(AgentExecution.created_at.asc(), AgentExecution.execution_id.asc())
                    )
                ).all()
                nodes = tuple(self._execution_summary_from_row(row, identity_str) for row in node_rows)

                round_filter = effective_round_id
                messages_page = await self._load_message_page(
                    conn,
                    user_scope=user_scope,
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_filter,
                    cursor_seq=message_cursor_seq,
                    limit=limit,
                    identity_str=identity_str,
                )
                artifacts_page = await self._load_artifact_page(
                    conn,
                    user_scope=user_scope,
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_filter,
                    cursor_key=artifact_cursor_key,
                    limit=limit,
                    identity_str=identity_str,
                )

                # source_reads：本执行消费的来源边 join asset_version 元数据（§4.3）。
                # Core 连接下统一显式列选择（实体选择行为不确定，见 C-03-1 教训）。
                source_rows = (
                    await conn.execute(
                        sa.select(
                            AgentSourceLink.link_id,
                            AgentSourceLink.source_id,
                            AgentSourceLink.source_version,
                            AgentSourceLink.consumer_kind,
                            AgentSourceLink.consumer_id,
                            AgentSourceLink.consumer_version,
                            AgentSourceLink.locator_payload,
                            AgentSourceLink.claim_ref,
                            AgentSourceLink.relation_kind,
                            AgentSourceLink.disabled_inherited,
                            AgentAssetVersion.asset_kind,
                            AgentAssetVersion.display_name,
                            AgentAssetVersion.digest,
                            AgentAssetVersion.read_state,
                            AgentAssetVersion.verification_state,
                            AgentAssetVersion.available_at,
                            AgentAssetVersion.disabled_at,
                            AgentAssetVersion.tombstoned,
                        )
                        .join(
                            AgentAssetVersion,
                            sa.and_(
                                AgentSourceLink.user_scope == AgentAssetVersion.user_scope,
                                AgentSourceLink.source_id == AgentAssetVersion.asset_id,
                                AgentSourceLink.source_version == AgentAssetVersion.asset_version,
                            ),
                        )
                        .where(
                            AgentSourceLink.user_scope == user_scope,
                            AgentSourceLink.consumer_kind == "execution",
                            AgentSourceLink.consumer_id == execution_id,
                        )
                        .order_by(AgentSourceLink.link_id.asc())
                    )
                ).all()
                source_reads = tuple(self._source_read_summary_from_row(row, identity_str) for row in source_rows)

                decisions = await self._load_decisions(
                    conn,
                    user_scope=user_scope,
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_filter,
                    identity_str=identity_str,
                )

                operation_rows = (
                    await conn.execute(
                        sa.select(AgentOperation)
                        .where(
                            AgentOperation.user_scope == user_scope,
                            AgentOperation.execution_id == execution_id,
                        )
                        .order_by(
                            AgentOperation.created_at.asc(),
                            AgentOperation.operation_id.asc(),
                        )
                    )
                ).all()
                operation_summaries = tuple(
                    self._operation_summary_from_row(row, identity_str) for row in operation_rows
                )

                # read_marks：本会话域阅读水位（session marker + 无 session 全局 marker）。
                read_mark_rows = (
                    await conn.execute(
                        sa.select(AgentReadMark)
                        .where(
                            AgentReadMark.user_scope == user_scope,
                            sa.or_(
                                AgentReadMark.session_id == session_id,
                                AgentReadMark.session_id.is_(None),
                            ),
                        )
                        .order_by(AgentReadMark.marker_id.asc())
                    )
                ).all()
                read_marks = tuple(self._read_mark_entry_from_row(row, identity_str) for row in read_mark_rows)

        return ResearchSnapshot(
            identity=identity,
            revision=execution_row.revision,
            execution_watermark=execution_row.event_seq,
            session_watermark=session_row.event_seq,
            updated_at=_wire_time(execution_row.updated_at),
            execution=execution_summary,
            nodes=nodes,
            frozen_scope=json_doc(round_row.frozen_manifest)
            if round_row is not None and round_row.frozen_manifest is not None
            else None,
            actual_input=json_doc(round_row.actual_input)
            if round_row is not None and round_row.actual_input is not None
            else None,
            messages_page=messages_page,
            artifacts_page=artifacts_page,
            source_reads=source_reads,
            decisions=decisions,
            operation_summaries=operation_summaries,
            usage=UsageSummary(
                model_attempt_count=execution_row.model_attempt_count,
                tool_call_count=execution_row.tool_call_count,
                active_ms=execution_row.active_ms,
            ),
            goal_coverage=execution_summary.goal_coverage,
            limitations=(),
            read_marks=read_marks,
            observation=Observation(
                reliable=True, last_verified_at=self._opt_wire_time(execution_row.last_verified_at)
            ),
        )

    # ------------------------------------------------------------------
    # §4.2 R02 历史搜索（C-03-3b）
    # ------------------------------------------------------------------

    _HISTORY_SUMMARY_MAX_CHARS: Final = 200

    async def _history_session_exists(self, conn: AsyncConnection, user_scope: str, session_id: str) -> None:
        row = (
            await conn.execute(
                sa.select(sa.literal(1)).where(
                    AgentSession.user_scope == user_scope,
                    AgentSession.session_id == session_id,
                )
            )
        ).first()
        if row is None:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "session not found in user scope", f"{user_scope}/{session_id}"
            )

    async def _history_fingerprint(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        query: HistoryQuery,
    ) -> str:
        """覆盖域修订指纹（§4.2 L404/L442）：既有会话身份 + revision/事件水位。

        scope 决定覆盖域成员：current_session/current_child 取主 session 行与
        其全部 execution 成员（id/revision/event_seq）；all_saved 取用户域全部
        session 成员（不含 execution 展开——目录级指纹，成员增删/水位变化即漂移）。
        """

        if query.scope == "all_saved":
            # all_saved 成员按 session 粒度：取各行 revision/event_seq
            # （目录级指纹，成员增删/水位变化即漂移）
            detail_rows = (
                await conn.execute(
                    sa.select(
                        AgentSession.session_id,
                        AgentSession.revision,
                        AgentSession.event_seq,
                    ).where(AgentSession.user_scope == user_scope)
                )
            ).all()
            members_wire = [
                {"session_id": row.session_id, "revision": row.revision, "event_seq": row.event_seq}
                for row in sorted(detail_rows, key=lambda r: r.session_id)
            ]
        else:
            await self._history_session_exists(conn, user_scope, query.session_id or "")
            srow = (
                await conn.execute(
                    sa.select(
                        AgentSession.session_id,
                        AgentSession.revision,
                        AgentSession.event_seq,
                    ).where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id == query.session_id,
                    )
                )
            ).first()
            assert srow is not None
            exec_rows = (
                await conn.execute(
                    sa.select(
                        AgentExecution.execution_id,
                        AgentExecution.revision,
                        AgentExecution.event_seq,
                    ).where(
                        AgentExecution.user_scope == user_scope,
                        AgentExecution.session_id == query.session_id,
                    )
                )
            ).all()
            members_wire = [
                {"session_id": srow.session_id, "revision": srow.revision, "event_seq": srow.event_seq},
                *[
                    {"execution_id": row.execution_id, "revision": row.revision, "event_seq": row.event_seq}
                    for row in sorted(exec_rows, key=lambda r: r.execution_id)
                ],
            ]
        return sha256_hex(json_doc({"scope": query.scope, "members": members_wire}))

    def _history_hit_from_row(self, row: Any, identity_str: str) -> HistoryHit:
        """消息/成果命中行 → HistoryHit（严格解码，坏行 fail-closed）。

        UNION 后统一行：summary_source=消息 body_text / summary=成果 summary 列。
        """

        hit_kind = row.hit_kind
        sort_moment = _wire_time(row.sort_moment)
        if hit_kind == "message":
            summary = row.summary_source or ""
            wire = {
                "hit_kind": "message",
                "session_id": row.session_id,
                "sort_moment": sort_moment,
                "created_at": _wire_time(row.created_at),
                "session_seq": row.session_seq,
                "execution_id": row.execution_id,
                "node_id": row.node_id,
                "round_id": row.round_id,
                "message_id": row.anchor_id,
                "artifact_id": None,
                "artifact_version": None,
                "role": row.role,
                "payload_kind": row.payload_kind,
                "artifact_kind": None,
                "summary_text": summary[: self._HISTORY_SUMMARY_MAX_CHARS],
            }
        else:
            summary = row.summary or ""
            wire = {
                "hit_kind": "artifact",
                "session_id": row.session_id,
                "sort_moment": sort_moment,
                "created_at": _wire_time(row.created_at),
                "session_seq": None,
                "execution_id": row.execution_id,
                "node_id": None,
                "round_id": row.round_id,
                "message_id": None,
                "artifact_id": row.anchor_id,
                "artifact_version": row.artifact_version,
                "role": None,
                "payload_kind": None,
                "artifact_kind": row.artifact_kind,
                "summary_text": summary[: self._HISTORY_SUMMARY_MAX_CHARS],
            }
        try:
            return decode_history_hit(wire, "$.hits")
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"persisted history row cannot be reliably decoded: {exc}",
                identity_str,
            ) from exc

    def _history_date_bounds(self, query: HistoryQuery, identity_str: str) -> tuple[datetime | None, datetime | None]:
        """date_from/date_to（含当天，UTC）→ 半开区间界（单边允许）。

        date_from → ``>= from`` 下界；date_to → ``< to+1d`` 上界；单边提供时
        仅该侧生效，两侧都缺则 (None, None)（检视 M-2：此前一侧为 None 即整体
        返回 None，日期条件被静默丢弃、结果集扩大）。正则语法冻结不核日期取值，
        strptime 失败按 INPUT_INVALID 拒绝（检视 m-1）。
        """

        try:
            day_from = (
                datetime.strptime(query.date_from, "%Y-%m-%d").replace(tzinfo=UTC)
                if query.date_from is not None
                else None
            )
            day_to_excl = (
                datetime.strptime(query.date_to, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1)
                if query.date_to is not None
                else None
            )
        except ValueError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "date_from/date_to contain invalid date value", identity_str
            ) from exc
        return day_from, day_to_excl

    async def search_history(self, query: HistoryQuery, *, user_scope: str) -> HistoryPage:
        """历史搜索（§4.2 R02 read_history + §9.13）：固定上界内规范扫描。

        - 受理校验：validate_history_query（R02 三范围/值集/游标语法）→ INPUT_INVALID；
        - 游标翻页：``h:{上界ts}:{排序键ts}:{锚ID}``——上界绑定翻页（续读复用首读
          上界，不因数据增长漏复检）；锚行缺失视为覆盖域已被修订 → REVISION_CONFLICT
          （R02，里程碑检视复核点：fingerprint 端到端比对由服务层承载）；
        - 扫描：消息与成果 UNION 前分别过滤后统一排序（sort_moment + 锚 ID keyset，
          newest_first DESC/oldest_first ASC）；query_text 占位 LIKE（C-03-4 全文
          索引接入前）；type_filter main=会话级（execution_id IS NULL）/child=执行内；
          state_filter 作用于 execution.display_state（值集未冻结，留痕）；date 窗口
          作用于 time_basis 对应列；agent_filter execution_agent 按执行三元组限定
          （role_identity 由消息 role_id 承载，成果不命中——里程碑检视复核点）；
        - 摘要不复制完整正文：summary_text 截断 200 字符（正文经准确地址按需读取）；
        - fingerprint：覆盖域成员/水位 CanonicalJson 后 sha256（每次读取即时计算）。
        """

        self._check_engine()
        identity_str = f"{user_scope}/history"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity_str)
        try:
            validate_history_query(query)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity_str
            ) from exc
        query_digest = compute_history_query_digest(query)

        cursor_bound: datetime | None = None
        cursor_sort_moment: datetime | None = None
        cursor_anchor: str | None = None
        if query.cursor is not None:
            upper_text, sort_text, anchor_id = parse_history_cursor(query.cursor)
            try:
                cursor_bound = datetime.strptime(upper_text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
                cursor_sort_moment = datetime.strptime(sort_text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
            except ValueError as exc:
                # 游标语法正则不核日期取值（放过 9999-99-99 等），strptime 失败按
                # INPUT_INVALID 拒绝（检视 m-1：裸 ValueError 不得逃逸受理边界）
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "history cursor contains invalid date value", identity_str
                ) from exc
            cursor_anchor = anchor_id
        fixed_upper = cursor_bound if cursor_bound is not None else datetime.now(UTC)
        upper_wire = _wire_time(fixed_upper)

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                # ---- 范围受理与覆盖域核对（R02 精确用户域）----
                if query.scope == "current_session":
                    await self._history_session_exists(conn, user_scope, query.session_id or "")
                elif query.scope == "current_child":
                    child_target = query.child_target
                    assert child_target is not None  # validate_history_query R02 已核对（fail-closed）
                    await self._history_session_exists(conn, user_scope, query.session_id or "")
                    child_row = (
                        await conn.execute(
                            sa.select(
                                AgentExecution.parent_execution_id,
                                AgentExecution.node_id,
                                AgentExecution.session_id,
                            ).where(
                                AgentExecution.user_scope == user_scope,
                                AgentExecution.execution_id == child_target.execution_id,
                            )
                        )
                    ).first()
                    if child_row is None or child_row.session_id != query.session_id:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID,
                            "child target execution not in session",
                            identity_str,
                        )
                    if (
                        child_row.parent_execution_id != child_target.parent_execution_id
                        or child_row.node_id != child_target.node_id
                    ):
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID, "child target ancestry mismatch", identity_str
                        )
                if query.round_selection.kind == "exact":
                    round_row = (
                        await conn.execute(
                            sa.select(sa.literal(1)).where(
                                AgentRound.user_scope == user_scope,
                                AgentRound.execution_id == query.round_selection.execution_id,  # pyright: ignore[reportOptionalSubscript]
                                AgentRound.round_id == query.round_selection.round_id,  # pyright: ignore[reportOptionalSubscript]
                            )
                        )
                    ).first()
                    if round_row is None:
                        code = (
                            AgentErrorCode.REVISION_CONFLICT
                            if query.cursor is not None
                            else AgentErrorCode.INPUT_INVALID
                        )
                        raise AgentServiceError(code, "exact round not found in user scope", identity_str)

                # ---- 锚行核对（翻页）----
                # 两表独立 EXISTS（CROSS JOIN 空 artifact 表会使 or_ 组合恒空）
                if cursor_anchor is not None:
                    message_anchor = (
                        await conn.execute(
                            sa.select(sa.literal(1)).where(
                                AgentMessage.user_scope == user_scope,
                                AgentMessage.message_id == cursor_anchor,
                            )
                        )
                    ).first()
                    artifact_anchor = (
                        await conn.execute(
                            sa.select(sa.literal(1)).where(
                                AgentArtifact.user_scope == user_scope,
                                AgentArtifact.artifact_id == cursor_anchor,
                            )
                        )
                    ).first()
                    if message_anchor is None and artifact_anchor is None:
                        raise AgentServiceError(
                            AgentErrorCode.REVISION_CONFLICT,
                            "history cursor anchor no longer exists; coverage was revised",
                            identity_str,
                        )

                # ---- 消息分支 ----
                message_moment = (
                    AgentMessage.occurred_at
                    if query.time_basis == "message"
                    else (AgentMessage.updated_at if query.time_basis == "updated" else AgentMessage.created_at)
                )
                message_cols = [
                    message_moment.label("sort_moment"),
                    AgentMessage.created_at.label("created_at"),
                    AgentMessage.session_id.label("session_id"),
                    AgentMessage.session_seq.label("session_seq"),
                    AgentMessage.execution_id.label("execution_id"),
                    AgentMessage.node_id.label("node_id"),
                    AgentMessage.round_id.label("round_id"),
                    AgentMessage.message_id.label("anchor_id"),
                    sa.literal("message").label("hit_kind"),
                    AgentMessage.role.label("role"),
                    AgentMessage.payload_kind.label("payload_kind"),
                    sa.literal(None).label("artifact_kind"),
                    sa.literal(None).label("artifact_version"),
                    AgentMessage.body_text.label("summary_source"),
                    sa.literal(None).label("summary"),
                ]
                message_stmt = sa.select(*message_cols).where(
                    AgentMessage.user_scope == user_scope,
                    message_moment <= fixed_upper,
                )
                if query.scope in ("current_session", "current_child"):
                    message_stmt = message_stmt.where(AgentMessage.session_id == query.session_id)
                if query.scope == "current_child":
                    child_target_msg = query.child_target
                    assert child_target_msg is not None  # validate_history_query R02 已核对（fail-closed）
                    message_stmt = message_stmt.where(
                        AgentMessage.execution_id == child_target_msg.execution_id,
                        AgentMessage.node_id == child_target_msg.node_id,
                    )
                if query.round_selection.kind == "exact":
                    message_stmt = message_stmt.where(
                        AgentMessage.execution_id == query.round_selection.execution_id,
                        AgentMessage.round_id == query.round_selection.round_id,
                    )
                if query.type_filter == "main":
                    message_stmt = message_stmt.where(AgentMessage.execution_id.is_(None))
                elif query.type_filter == "child":
                    message_stmt = message_stmt.where(AgentMessage.execution_id.is_not(None))
                if query.message_role_filter is not None:
                    message_stmt = message_stmt.where(AgentMessage.role == query.message_role_filter)
                agent_filter = query.agent_filter
                if agent_filter is not None and agent_filter.kind == "role_identity":
                    message_stmt = message_stmt.where(
                        AgentMessage.role_id == agent_filter.role_id,
                    )
                    if agent_filter.version_selection == "exact":
                        message_stmt = message_stmt.where(AgentMessage.role_version == agent_filter.role_version)
                elif agent_filter is not None:
                    message_stmt = message_stmt.where(
                        AgentMessage.execution_id == agent_filter.execution_id,
                        AgentMessage.node_id == agent_filter.node_id,
                        AgentMessage.round_id == agent_filter.round_id,
                    )
                if query.query_text:
                    # 全文搜索（§4.2 L402/§9.6 search_text 投影）：ILIKE 子串（含尾词），
                    # 通配符按字面量转义；简单 ILIKE 语义——tsvector/trgm 查询演化留痕
                    escaped = query.query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    message_stmt = message_stmt.where(AgentMessage.search_text.ilike(f"%{escaped}%", escape="\\"))
                if query.state_filter is not None:
                    message_stmt = message_stmt.where(
                        sa.exists().where(
                            sa.and_(
                                AgentExecution.user_scope == user_scope,
                                AgentExecution.execution_id == AgentMessage.execution_id,
                                AgentExecution.display_state == query.state_filter,
                            )
                        )
                    )
                day_from, day_to_excl = self._history_date_bounds(query, identity_str)
                if day_from is not None:
                    message_stmt = message_stmt.where(message_moment >= day_from)
                if day_to_excl is not None:
                    message_stmt = message_stmt.where(message_moment < day_to_excl)
                if cursor_sort_moment is not None and cursor_anchor is not None:
                    if query.sort == "newest_first":
                        message_stmt = message_stmt.where(
                            sa.tuple_(message_moment, AgentMessage.message_id)
                            < sa.tuple_(
                                sa.bindparam("h_m_moment", cursor_sort_moment),
                                sa.bindparam("h_m_anchor", cursor_anchor),
                            )
                        )
                    else:
                        message_stmt = message_stmt.where(
                            sa.tuple_(message_moment, AgentMessage.message_id)
                            > sa.tuple_(
                                sa.bindparam("h_m_moment", cursor_sort_moment),
                                sa.bindparam("h_m_anchor", cursor_anchor),
                            )
                        )

                # ---- 成果分支 ----
                artifact_moment = (
                    AgentArtifact.created_at
                    if query.time_basis == "message"
                    else (AgentArtifact.updated_at if query.time_basis == "updated" else AgentArtifact.created_at)
                )
                artifact_cols = [
                    artifact_moment.label("sort_moment"),
                    AgentArtifact.created_at.label("created_at"),
                    AgentArtifact.session_id.label("session_id"),
                    sa.literal(None).label("session_seq"),
                    AgentArtifact.execution_id.label("execution_id"),
                    sa.literal(None).label("node_id"),
                    AgentArtifact.round_id.label("round_id"),
                    AgentArtifact.artifact_id.label("anchor_id"),
                    sa.literal("artifact").label("hit_kind"),
                    sa.literal(None).label("role"),
                    sa.literal(None).label("payload_kind"),
                    AgentArtifact.artifact_kind.label("artifact_kind"),
                    AgentArtifact.artifact_version.label("artifact_version"),
                    sa.literal(None).label("summary_source"),
                    AgentArtifact.summary.label("summary"),
                ]
                artifact_stmt = sa.select(*artifact_cols).where(
                    AgentArtifact.user_scope == user_scope,
                    artifact_moment <= fixed_upper,
                )
                if query.scope in ("current_session", "current_child"):
                    artifact_stmt = artifact_stmt.where(AgentArtifact.session_id == query.session_id)
                if query.scope == "current_child":
                    child_target_art = query.child_target
                    assert child_target_art is not None  # validate_history_query R02 已核对（fail-closed）
                    artifact_stmt = artifact_stmt.where(
                        AgentArtifact.execution_id == child_target_art.execution_id,
                    )
                if query.round_selection.kind == "exact":
                    artifact_stmt = artifact_stmt.where(
                        AgentArtifact.execution_id == query.round_selection.execution_id,
                        AgentArtifact.round_id == query.round_selection.round_id,
                    )
                if query.type_filter in ("main", "child"):
                    wanted = query.type_filter == "main"
                    artifact_stmt = artifact_stmt.where(
                        AgentArtifact.execution_id.is_(None) if wanted else AgentArtifact.execution_id.is_not(None)
                    )
                if agent_filter is not None and agent_filter.kind == "role_identity":
                    # role_identity 以消息 role_id 承载，成果不命中（里程碑检视复核点）
                    artifact_stmt = artifact_stmt.where(sa.literal(False))
                elif agent_filter is not None:
                    artifact_stmt = artifact_stmt.where(
                        AgentArtifact.execution_id == agent_filter.execution_id,
                        AgentArtifact.round_id == agent_filter.round_id,
                    )
                if query.query_text:
                    # 成果无 search_text 投影列（§9.7）：摘要/内联正文 ILIKE（同一转义语义）
                    escaped = query.query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    artifact_stmt = artifact_stmt.where(
                        sa.or_(
                            AgentArtifact.summary.ilike(f"%{escaped}%", escape="\\"),
                            AgentArtifact.body_text.ilike(f"%{escaped}%", escape="\\"),
                        )
                    )
                if query.state_filter is not None:
                    artifact_stmt = artifact_stmt.where(
                        sa.exists().where(
                            sa.and_(
                                AgentExecution.user_scope == user_scope,
                                AgentExecution.execution_id == AgentArtifact.execution_id,
                                AgentExecution.display_state == query.state_filter,
                            )
                        )
                    )
                day_from, day_to_excl = self._history_date_bounds(query, identity_str)
                if day_from is not None:
                    artifact_stmt = artifact_stmt.where(artifact_moment >= day_from)
                if day_to_excl is not None:
                    artifact_stmt = artifact_stmt.where(artifact_moment < day_to_excl)
                if cursor_sort_moment is not None and cursor_anchor is not None:
                    if query.sort == "newest_first":
                        artifact_stmt = artifact_stmt.where(
                            sa.tuple_(artifact_moment, AgentArtifact.artifact_id)
                            < sa.tuple_(
                                sa.bindparam("h_a_moment", cursor_sort_moment),
                                sa.bindparam("h_a_anchor", cursor_anchor),
                            )
                        )
                    else:
                        artifact_stmt = artifact_stmt.where(
                            sa.tuple_(artifact_moment, AgentArtifact.artifact_id)
                            > sa.tuple_(
                                sa.bindparam("h_a_moment", cursor_sort_moment),
                                sa.bindparam("h_a_anchor", cursor_anchor),
                            )
                        )

                # ---- 统一排序 ----
                union_sub = sa.union_all(message_stmt.self_group(), artifact_stmt.self_group()).subquery()
                sort_moment_col = union_sub.c.sort_moment
                anchor_col = union_sub.c.anchor_id
                direction = sort_moment_col.desc() if query.sort == "newest_first" else sort_moment_col.asc()
                union_stmt = sa.select(
                    union_sub.c.sort_moment,
                    union_sub.c.created_at,
                    union_sub.c.session_id,
                    union_sub.c.session_seq,
                    union_sub.c.execution_id,
                    union_sub.c.node_id,
                    union_sub.c.round_id,
                    union_sub.c.anchor_id,
                    union_sub.c.hit_kind,
                    union_sub.c.role,
                    union_sub.c.payload_kind,
                    union_sub.c.artifact_kind,
                    union_sub.c.artifact_version,
                    union_sub.c.summary_source,
                    union_sub.c.summary,
                ).order_by(direction, anchor_col.desc() if query.sort == "newest_first" else anchor_col.asc())
                union_rows = (await conn.execute(union_stmt.limit(query.limit + 1))).all()
                has_more = len(union_rows) > query.limit
                union_rows = union_rows[: query.limit]
                hits = tuple(self._history_hit_from_row(row, identity_str) for row in union_rows)
                fingerprint = await self._history_fingerprint(conn, user_scope=user_scope, query=query)

        next_cursor: str | None = None
        if has_more and union_rows:
            last = union_rows[-1]
            next_cursor = f"h:{upper_wire}:{_wire_time(last.sort_moment)}:{last.anchor_id}"
        return HistoryPage(
            query_digest=query_digest,
            fixed_upper_bound=upper_wire,
            revision_fingerprint=fingerprint,
            hits=hits,
            next_cursor=next_cursor,
            read_moment=_utc_now_wire(),
        )

    # ------------------------------------------------------------------
    # §4.2 observe / §9.13 监听协议（C-03-5）
    # ------------------------------------------------------------------

    async def observe_session_events(
        self,
        session_id: str,
        *,
        user_scope: str,
        after_seq: int = 0,
        poll_interval_s: float = 0.25,
    ) -> AsyncIterator[ResearchEvent]:
        """会话持久事件增量迭代器（§4.2 L389 observe_session + §4.2 L531-536 协议）。

        协议顺序（不可颠倒）：

        1. 先建立 PG LISTEN 并确认生效（独立 asyncpg 连接，autocommit 执行即注册）；
        2. 再用新事务按 ``after_seq`` 补 backlog（list_events_after，升序严格解码）；
        3. 之后持续读取：pg_notify 只唤醒读取，数据库事件才是事实——每轮以新
           短事务读增量（``seq > last_seq``）；超时 ``poll_interval_s`` 同样触发
           有界重查（重查节拍 + 读取合计满足「原可靠事件到 UI ≤1 秒」合同，
           不把轮询间隔设为 1 秒）；
        4. 同一 session_seq 只合并一次（``last_seq`` 单调推进）；缺口序号由消费方
           按序号核对（迭代器按库中存在行升序产出，不跳号应用——L533）；
        5. ``CancelledError`` 必须 raise（R2）；退订只关闭本 observer（LISTEN
           连接关闭），不影响其他监听者。

        受理校验（session 存在于用户域）在 LISTEN 生效后、backlog 前执行，
        INPUT_INVALID 以异常终止迭代器（不返回空成功）。
        """

        self._check_engine()
        identity_str = f"{user_scope}/{session_id}"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity_str)
        if not isinstance(session_id, str) or session_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session_id must be non-empty text", identity_str)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "after_seq must be >=0 int", identity_str)
        if poll_interval_s <= 0 or poll_interval_s > 1.0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "poll_interval_s must be in (0, 1.0]", identity_str)

        # 独立 LISTEN 连接（asyncpg 直连；驱动去掉 SQLAlchemy 方言段）。
        import asyncpg

        listen_url = sa.engine.make_url(self.engine.url.render_as_string(hide_password=False)).set(
            drivername="postgresql"
        )
        listen_conn: asyncpg.Connection = await asyncpg.connect(
            host=listen_url.host,
            port=listen_url.port,
            user=listen_url.username,
            password=listen_url.password,
            database=listen_url.database,
        )
        notify_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=16)

        def _on_notify(_conn: asyncpg.Connection, _pid: int, _channel: str, _payload: str | None) -> None:
            # 通知只唤醒读取（payload 内容不承载事实）；队列满时丢弃通知，退化为轮询兜底
            with contextlib.suppress(asyncio.QueueFull):
                notify_queue.put_nowait(None)

        # asyncpg ≥0.29 起 add_listener/remove_listener 为协程，必须 await
        # （否则 LISTEN 从未注册、退订从未执行，监听静默退化为轮询——检视警告暴露）
        await listen_conn.add_listener(_AGENT_EVENT_CHANNEL, _on_notify)
        # 注册与确认阶段任一失败须关闭独立连接（检视 m-3：try/finally 前置窗口泄漏）；
        # BaseException + raise 保证 CancelledError 传播（R2，不吞没）
        try:
            # LISTEN 注册即生效（asyncpg autocommit）；再执行一条语句确认会话可用（L532 确认生效）
            await listen_conn.execute("SELECT 1")
        except BaseException:
            await listen_conn.close()
            raise
        last_seq = after_seq
        try:
            # 受理校验（用户域）：不返回空成功
            async with self.engine.connect() as conn:
                await self._history_session_exists(conn, user_scope, session_id)
            while True:
                # 新短事务追平（增量读取；list_events_after 每次调用独立事务）：
                # backlog 未追平（has_more）时连续翻页读完再进入等待，不逐页插入
                # 等待节拍（检视 M-1：§4.2 L532「先补 backlog 追平，之后持续读取」）
                while True:
                    page = await self.read_events(
                        EventReadQuery(
                            user_scope=user_scope,
                            session_id=session_id,
                            after_seq=last_seq,
                            limit=50,
                        )
                    )
                    for event in page.events:
                        last_seq = event.session_seq
                        yield event
                    if not page.has_more:
                        break
                # 等通知唤醒或超时有界重查（§4.2 L532）
                try:
                    await asyncio.wait_for(notify_queue.get(), timeout=poll_interval_s)
                except TimeoutError:
                    pass
        finally:
            # 退订只关闭本 observer（LISTEN 连接关闭），不影响其他监听者；
            # remove_listener 为协程须 await；连接已失效时退订失败按普通异常
            # 处理不掩盖原异常（Exception 不含 CancelledError，R2 不吞没）
            with contextlib.suppress(Exception):
                await listen_conn.remove_listener(_AGENT_EVENT_CHANNEL, _on_notify)
            await listen_conn.close()

    # ------------------------------------------------------------------
    # C-04-2：资源票据与执行累计计数（§5.4 锁序第 1 条/§5.5/§9.11）
    # ------------------------------------------------------------------

    async def claim_resource(self, request: ResourceClaimRequest) -> ResourceTicket:
        """资源票据认领事务（§5.4 锁序第 1 条/§5.5/§9.11）：global guard 行锁
        下做最严限额核定并落准确票据，**单个显式事务**。

        - 全局仲裁：预置单例 guard 行（user_scope=''、slot_kind=global_guard、
          execution_id NULL）后 ``FOR UPDATE``，所有认领串行化（不靠数量缓存准入）；
        - 最严限额（开放容量谓词 = ``released_at IS NULL OR release_verified=false``，
          unknown 在途不放空）：**事务内检查每会话 1 总占位与每总（同 root 树）
          3 活跃叶子**（§9.11「事务同时检查每会话1/每总3，不靠数量缓存准入」）；
          全局 2 总/4 叶为预分配预算（运行宿主准入承载，D-01/C-05），每计划
          20 叶为计划级验证（D-02 原生 DAG），均不在本票据事务重复检查；
          超限 ``BUDGET_BLOCKED``（不伪失败）；
        - 幂等重放：同 slot_id 同输入（kind/execution/node/round/owner_epoch）且
          票据未确认释放 → 返回原票据零新事实；异输入 ``INPUT_CONFLICT``；
          已确认释放（release_verified=true）的 slot_id 不得复用（零复活）；
        - fence：目标 execution ``deletion_fenced`` → ``POLICY_DENIED``（引用 vs
          清理单胜者，fence 后拒绝新引用）；
        - kind 与执行类别核验：total 限 root/comparison_lane，leaf 限 leaf；
          execution 缺失 ``INPUT_INVALID``。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT / POLICY_DENIED /
                BUDGET_BLOCKED。
        """

        self._check_engine()
        validate_resource_claim_request(request)
        identity = f"{request.user_scope}/{request.slot_id}"
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. 全局 guard：预置单例行并行锁（§5.4 第 1 条；ON CONFLICT DO
            #    NOTHING 幂等预置，无目标子句对任意冲突生效；PG 方言 insert）。
            guard_insert = sa_pg.insert(AgentResourceSlot).values(
                user_scope="",
                slot_id=_RESOURCE_GUARD_SLOT_ID,
                slot_kind=ResourceSlotKind.GLOBAL_GUARD.value,
                owner_epoch=0,
                run_epoch=0,
            )
            await conn.execute(guard_insert.on_conflict_do_nothing())
            guard_row = (
                await conn.execute(
                    sa.select(AgentResourceSlot.slot_id)
                    .where(AgentResourceSlot.slot_id == _RESOURCE_GUARD_SLOT_ID)
                    .with_for_update()
                )
            ).first()
            if guard_row is None:
                raise AgentServiceError(
                    AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                    "global resource guard row unavailable",
                    identity,
                )

            # 2. 目标 execution 行锁 + 严格读取（锁序：guard → execution/节点/票据）。
            execution_row = (
                await conn.execute(
                    sa.select(
                        AgentExecution.execution_kind,
                        AgentExecution.session_id,
                        AgentExecution.parent_execution_id,
                        AgentExecution.deletion_fenced,
                    )
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id == request.execution_id,
                    )
                    .with_for_update()
                )
            ).first()
            if execution_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "target execution does not exist in user scope",
                    identity,
                )
            if execution_row.deletion_fenced:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    "target execution is deletion fenced; new references rejected",
                    identity,
                )
            if request.slot_kind is ResourceSlotKind.TOTAL:
                if execution_row.execution_kind not in ("root", "comparison_lane"):
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "total slot requires root or comparison_lane execution",
                        identity,
                    )
            else:
                if execution_row.execution_kind != "leaf":
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "leaf slot requires leaf execution",
                        identity,
                    )

            # 3. 幂等重放/复用拒绝（同 slot_id 异输入冲突；已确认释放不复用）。
            slot_row = (
                await conn.execute(
                    sa.select(
                        AgentResourceSlot.slot_kind,
                        AgentResourceSlot.execution_id,
                        AgentResourceSlot.node_id,
                        AgentResourceSlot.round_id,
                        AgentResourceSlot.owner_epoch,
                        AgentResourceSlot.run_epoch,
                        AgentResourceSlot.claimed_at,
                        AgentResourceSlot.released_at,
                        AgentResourceSlot.release_verified,
                    )
                    .where(
                        AgentResourceSlot.user_scope == request.user_scope,
                        AgentResourceSlot.slot_id == request.slot_id,
                    )
                    .with_for_update()
                )
            ).first()
            if slot_row is not None:
                same_input = (
                    slot_row.slot_kind == request.slot_kind.value
                    and slot_row.execution_id == request.execution_id
                    and slot_row.node_id == request.node_id
                    and slot_row.round_id == request.round_id
                    and slot_row.owner_epoch == request.owner_epoch
                )
                if not same_input:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "same slot_id with different ticket identity",
                        identity,
                    )
                if slot_row.release_verified:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "slot_id already released and cannot be reused",
                        identity,
                    )
                return ResourceTicket(
                    slot_id=request.slot_id,
                    slot_kind=request.slot_kind,
                    execution_id=request.execution_id,
                    node_id=request.node_id,
                    round_id=request.round_id,
                    owner_epoch=request.owner_epoch,
                    run_epoch=slot_row.run_epoch,
                    claimed_at=_wire_time(slot_row.claimed_at),
                )

            # 4. 最严限额核定（开放容量谓词：unknown 在途不放空；§9.11 事务内
            #    只查每会话 1 总/每总 3 叶，全局与每计划预算由运行宿主/计划验证承载）。
            open_predicate = sa.or_(
                AgentResourceSlot.released_at.is_(None),
                AgentResourceSlot.release_verified.is_(False),
            )
            if request.slot_kind is ResourceSlotKind.TOTAL:
                # 每会话 1 总占位。
                session_open_total = (
                    await conn.execute(
                        sa.select(sa.func.count())
                        .select_from(AgentResourceSlot)
                        .join(
                            AgentExecution,
                            sa.and_(
                                AgentExecution.user_scope == AgentResourceSlot.user_scope,
                                AgentExecution.execution_id == AgentResourceSlot.execution_id,
                            ),
                        )
                        .where(
                            AgentResourceSlot.slot_kind == ResourceSlotKind.TOTAL.value,
                            AgentResourceSlot.user_scope == request.user_scope,
                            AgentExecution.session_id == execution_row.session_id,
                            open_predicate,
                        )
                    )
                ).scalar_one()
                if session_open_total >= 1:
                    raise _budget_blocked(identity, "session total slot limit (1) reached")
            else:
                # 每总（同 root 树）3 实际活跃叶子（递归 CTE 定 root 后计整树）。
                tree_open_leaf = (
                    await conn.execute(
                        sa.text(_RESOURCE_TREE_OPEN_LEAF_SQL).bindparams(
                            user_scope=request.user_scope,
                            execution_id=request.execution_id,
                        )
                    )
                ).scalar_one()
                if tree_open_leaf >= 3:
                    raise _budget_blocked(identity, "per-root leaf slot limit (3) reached")

            # 5. 落票据（guard 行锁下无竞争；owner_epoch 为调用方运行身份）。
            #    跨用户域 slot_id 撞单列全局 PK 时转 INPUT_CONFLICT（检视闭环 m-3：
            #    数据库失败明确分类，不裸 IntegrityError；accept_request 先例）。
            now = datetime.now(UTC)
            try:
                await conn.execute(
                    sa.insert(AgentResourceSlot).values(
                        user_scope=request.user_scope,
                        slot_id=request.slot_id,
                        slot_kind=request.slot_kind.value,
                        execution_id=request.execution_id,
                        node_id=request.node_id,
                        round_id=request.round_id,
                        owner_epoch=request.owner_epoch,
                        run_epoch=0,
                        claimed_at=now,
                        release_verified=False,
                        operation_refs=request.operation_refs,
                    )
                )
            except IntegrityError as exc:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "slot_id already exists (cross-scope collision)",
                    identity,
                ) from exc
            return ResourceTicket(
                slot_id=request.slot_id,
                slot_kind=request.slot_kind,
                execution_id=request.execution_id,
                node_id=request.node_id,
                round_id=request.round_id,
                owner_epoch=request.owner_epoch,
                run_epoch=0,
                claimed_at=_wire_time(now),
            )

    async def release_resource(self, request: ResourceReleaseRequest) -> ResourceReleaseReceipt:
        """资源票据释放事务（§5.5：释放必须完整匹配，不用 TTL/租约/旧 owner）。

        - 完整匹配：slot_kind≠guard、execution_id/node_id/owner_epoch 逐项相等，
          任一不匹配 ``INPUT_CONFLICT``（旧票不释新票）；
        - ``exit_verified=True``：真实退出确认，``release_verified=true`` 落定、
          容量释放；``exit_verified=False``：退出尾段未知，记录 released_at 但
          ``release_verified`` 保持 false、容量不放空（unknown 在途不放空），
          须实际全退或用户保未知结案后以 verified 再确认；
        - 幂等：已确认释放重放返回原回执零新事实；reserved（未确认）重放返回
          released=False 回执；verified 升级（reserved→confirmed）为合法收尾。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.slot_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.slot_id, "slot_id", identity)
        _require_text_field(request.execution_id, "execution_id", identity)
        _require_text_field(request.node_id, "node_id", identity)
        _require_int_field(request.owner_epoch, "owner_epoch", identity, 1)
        if not isinstance(request.exit_verified, bool):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "exit_verified must be bool", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            slot_row = (
                await conn.execute(
                    sa.select(
                        AgentResourceSlot.slot_kind,
                        AgentResourceSlot.execution_id,
                        AgentResourceSlot.node_id,
                        AgentResourceSlot.owner_epoch,
                        AgentResourceSlot.released_at,
                        AgentResourceSlot.release_verified,
                    )
                    .where(
                        AgentResourceSlot.user_scope == request.user_scope,
                        AgentResourceSlot.slot_id == request.slot_id,
                    )
                    .with_for_update()
                )
            ).first()
            if slot_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "slot does not exist", identity)
            if slot_row.slot_kind == ResourceSlotKind.GLOBAL_GUARD.value:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "guard row is not releasable", identity)
            if (
                slot_row.execution_id != request.execution_id
                or slot_row.node_id != request.node_id
                or slot_row.owner_epoch != request.owner_epoch
            ):
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "release identity mismatch: tickets must be fully matched",
                    identity,
                )
            if slot_row.release_verified:
                return ResourceReleaseReceipt(
                    slot_id=request.slot_id,
                    released=True,
                    release_verified=True,
                    released_at=_wire_time(slot_row.released_at) if slot_row.released_at is not None else None,
                    owner_epoch=request.owner_epoch,
                )
            now = datetime.now(UTC)
            released_at = slot_row.released_at if slot_row.released_at is not None else now
            await conn.execute(
                sa.update(AgentResourceSlot)
                .where(
                    AgentResourceSlot.user_scope == request.user_scope,
                    AgentResourceSlot.slot_id == request.slot_id,
                )
                .values(
                    released_at=released_at,
                    release_verified=request.exit_verified,
                    revision=AgentResourceSlot.revision + 1,
                    updated_at=now,
                )
            )
            return ResourceReleaseReceipt(
                slot_id=request.slot_id,
                released=request.exit_verified,
                release_verified=request.exit_verified,
                released_at=_wire_time(released_at),
                owner_epoch=request.owner_epoch,
            )

    async def record_usage_counters(self, request: UsageCounterUpdate) -> UsageCounterFact:
        """执行累计计数 GREATEST 合并事务（§5.5/§9.4：持久累计跨轮/SDK run 不清零）。

        三计数均为**绝对累计值**按列 GREATEST 合并（R22：乱序写入最终取最大值，
        重复 checkpoint 幂等不双记）；``active_ms`` 为已核可靠下界，未关闭区间
        尾段未知不记 0、不伪完整用时。全列无变化时零写回（revision 不动）。

        Raises:
            AgentServiceError: INPUT_INVALID（execution 缺失或负计数）。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.execution_id}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.execution_id, "execution_id", identity)
        for name, value in (
            ("model_attempt_count", request.model_attempt_count),
            ("tool_call_count", request.tool_call_count),
            ("active_ms", request.active_ms),
        ):
            _require_int_field(value, name, identity, 0)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            counter_row = (
                await conn.execute(
                    sa.select(
                        AgentExecution.model_attempt_count,
                        AgentExecution.tool_call_count,
                        AgentExecution.active_ms,
                    )
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id == request.execution_id,
                    )
                    .with_for_update()
                )
            ).first()
            if counter_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "target execution does not exist in user scope",
                    identity,
                )
            landed_attempts = max(counter_row.model_attempt_count, request.model_attempt_count)
            landed_calls = max(counter_row.tool_call_count, request.tool_call_count)
            landed_ms = max(counter_row.active_ms, request.active_ms)
            if (
                landed_attempts == counter_row.model_attempt_count
                and landed_calls == counter_row.tool_call_count
                and landed_ms == counter_row.active_ms
            ):
                return UsageCounterFact(
                    execution_id=request.execution_id,
                    model_attempt_count=landed_attempts,
                    tool_call_count=landed_calls,
                    active_ms=landed_ms,
                )
            await conn.execute(
                sa.update(AgentExecution)
                .where(
                    AgentExecution.user_scope == request.user_scope,
                    AgentExecution.execution_id == request.execution_id,
                )
                .values(
                    model_attempt_count=landed_attempts,
                    tool_call_count=landed_calls,
                    active_ms=landed_ms,
                    revision=AgentExecution.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            return UsageCounterFact(
                execution_id=request.execution_id,
                model_attempt_count=landed_attempts,
                tool_call_count=landed_calls,
                active_ms=landed_ms,
            )

    # ------------------------------------------------------------------
    # C-04-3：源禁用/fence 与派发共同仲裁（§3.3.2/§5.4/§9.10）
    # ------------------------------------------------------------------

    async def stop_source_use(self, request: SourceStopCommand) -> SourceStopReceipt:
        """停止使用来源事务（§3.3.2/§5.4/SQ-07）：source/version 行锁下落
        disabled_revision/fence 与已知派生闭包，**单个显式事务**。

        - 锁序：source/version 行锁（sorted 稳定 ID；不反向持源锁等待 execution 锁）；
        - 禁用落定：``read_state='disabled'``、``disabled_at=now``、
          ``disabled_revision=GREATEST(旧值, revision+1)``、行 revision 单调 +1
          （零复活：阅读/复制/改名不解除禁用，无恢复路径）；
        - 已知派生闭包：按 source/version 递归收集 consumer_kind='asset' 的派生
          链（FK 保证已知边完整），闭包内全部 source_link ``disabled_inherited=true``
          （禁用源及其已知派生；闭包未知不等于空关联——派发方按 claim 共同仲裁
          新鲜重读阻断）；
        - 幂等：已禁用重放返回原事实（already_disabled=True，零新事实）；
          ``command_id`` 仅作非空校验与审计透传——命令键查重/同键异输入冲突由
          服务层 stop_using command 事务（agent_command 唯一约束）承载（§3.3.2），
          本 DAO 层幂等键为源身份（检视闭环 m-1 纠偏：原 docstring 误称本层核命令键）；
        - 来源不存在 ``INPUT_INVALID``（严格读，不空成功）。

        Raises:
            AgentServiceError: INPUT_INVALID。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.source_id}@{request.source_version}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.source_id, "source_id", identity)
        _require_text_field(request.source_version, "source_version", identity)
        _require_text_field(request.command_id, "command_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. source/version 行锁（§5.4 来源仲裁锁序；禁用先胜零受影响新出站）。
            asset_row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.revision,
                        AgentAssetVersion.disabled_revision,
                        AgentAssetVersion.disabled_at,
                    )
                    .where(
                        AgentAssetVersion.user_scope == request.user_scope,
                        AgentAssetVersion.asset_id == request.source_id,
                        AgentAssetVersion.asset_version == request.source_version,
                    )
                    .with_for_update()
                )
            ).first()
            if asset_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "source version does not exist", identity)
            if asset_row.disabled_revision > 0 or asset_row.read_state == "disabled":
                return SourceStopReceipt(
                    source_id=request.source_id,
                    source_version=request.source_version,
                    disabled_revision=asset_row.disabled_revision,
                    already_disabled=True,
                    disabled_at=_wire_time(asset_row.disabled_at)
                    if asset_row.disabled_at is not None
                    else _utc_now_wire(),
                )
            now = datetime.now(UTC)
            new_revision = asset_row.revision + 1
            disabled_revision = max(asset_row.disabled_revision, new_revision)
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(
                    AgentAssetVersion.user_scope == request.user_scope,
                    AgentAssetVersion.asset_id == request.source_id,
                    AgentAssetVersion.asset_version == request.source_version,
                )
                .values(
                    read_state="disabled",
                    disabled_at=now,
                    disabled_revision=disabled_revision,
                    revision=new_revision,
                    updated_at=now,
                )
            )
            # 2. 已知派生闭包：递归收集 consumer_kind='asset' 派生链后整体标记
            #    disabled_inherited（§9.10「禁用源及其已知派生闭包时，按
            #    source/version 排序核边」；参数化 SQL，R4）。
            await conn.execute(
                sa.text(_SOURCE_DISABLE_CLOSURE_SQL).bindparams(
                    user_scope=request.user_scope,
                    source_id=request.source_id,
                    source_version=request.source_version,
                )
            )
            return SourceStopReceipt(
                source_id=request.source_id,
                source_version=request.source_version,
                disabled_revision=disabled_revision,
                already_disabled=False,
                disabled_at=_wire_time(now),
            )

    async def claim_source_versions(self, request: SourceClaimRequest) -> SourceClaimFact:
        """源派发认领事务（§5.4 共同仲裁）：按稳定 ID 排序锁全部 source/version
        并新鲜重读禁用状态，保存绑定 revision 的原 operation 认领，**单个显式事务**。

        - 锁序：source/version（sorted）→ 各源直接入边行（§5.4 L624/L627：与
          stop 闭包 UPDATE 锁集相交形成冲突序列化，禁用先提交则后到 claim 不能
          绕过——检视闭环 M-1）→ operation；
        - 新鲜重读（预检查/旧快照不是派发权）：任一 source 行已禁用，或作为已知
          派生闭包成员被 ``disabled_inherited`` 覆盖 → ``SOURCE_DISABLED``
          （禁用先胜零受影响新出站）；
        - 认领持久化：operation.data_consent_claims 追加 ``kind=source_revision``
          条目绑定各源 disabled_revision（claim 先胜保原 operation 认领与在途）；
        - 幂等：同 claim_ref 同输入返回原事实（replayed=True，claim 已生效不被
          后续禁用改写）；同 claim_ref 异输入 ``INPUT_CONFLICT``；
        - 重复 claim 只返回原事实，不生成第二操作。

        Raises:
            AgentServiceError: INPUT_INVALID / SOURCE_DISABLED。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.claim_ref}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.claim_ref, "claim_ref", identity)
        _require_text_field(request.operation_id, "operation_id", identity)
        if not request.sources:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "sources must not be empty", identity)
        for index, source in enumerate(request.sources):
            _require_text_field(source.source_id, f"sources[{index}].source_id", identity)
            _require_text_field(source.source_version, f"sources[{index}].source_version", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        # 锁序：稳定 ID 排序（source_id, source_version）后逐一持锁。
        ordered = sorted(request.sources, key=lambda s: (s.source_id, s.source_version))
        async with engine.begin() as conn:
            bindings: list[SourceClaimBinding] = []
            disabled_hits: list[str] = []
            for source in ordered:
                asset_row = (
                    await conn.execute(
                        sa.select(
                            AgentAssetVersion.disabled_revision,
                            AgentAssetVersion.read_state,
                        )
                        .where(
                            AgentAssetVersion.user_scope == request.user_scope,
                            AgentAssetVersion.asset_id == source.source_id,
                            AgentAssetVersion.asset_version == source.source_version,
                        )
                        .with_for_update()
                    )
                ).first()
                if asset_row is None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "source version does not exist",
                        f"{identity}/{source.source_id}@{source.source_version}",
                    )
                # 入边行锁（检视闭环 M-1，§5.4 L624/L627）：先对作为派生目标的
                # 引用边 FOR UPDATE，再计禁用状态——stop 的闭包 UPDATE 须更新同
                # 批边行，两者锁集相交形成冲突序列化；否则 claim 判定入边=0 后、
                # 提交前 stop 提交，禁用先提交仍可被后到 claim 绕过。任意祖先
                # stop 都会标记本源直接入边（闭包含全部链上边），故锁直接入边
                # 即覆盖全部祖先禁用路径。
                await conn.execute(
                    sa.select(AgentSourceLink.link_id)
                    .where(
                        AgentSourceLink.user_scope == request.user_scope,
                        AgentSourceLink.derived_asset_id == source.source_id,
                        AgentSourceLink.derived_asset_version == source.source_version,
                    )
                    .order_by(AgentSourceLink.link_id)
                    .with_for_update()
                )
                inherited = (
                    await conn.execute(
                        sa.select(sa.func.count())
                        .select_from(AgentSourceLink)
                        .where(
                            AgentSourceLink.user_scope == request.user_scope,
                            AgentSourceLink.derived_asset_id == source.source_id,
                            AgentSourceLink.derived_asset_version == source.source_version,
                            AgentSourceLink.disabled_inherited.is_(True),
                        )
                    )
                ).scalar_one()
                if asset_row.disabled_revision > 0 or asset_row.read_state == "disabled" or inherited > 0:
                    disabled_hits.append(f"{source.source_id}@{source.source_version}")
                    continue
                bindings.append(
                    SourceClaimBinding(
                        source_id=source.source_id,
                        source_version=source.source_version,
                        disabled_revision=asset_row.disabled_revision,
                    )
                )
            # 2. operation 行锁（认领持久化目标；缺失=先备后派发契约被违反）。
            operation_row = (
                await conn.execute(
                    sa.select(AgentOperation.data_consent_claims)
                    .where(
                        AgentOperation.user_scope == request.user_scope,
                        AgentOperation.operation_id == request.operation_id,
                    )
                    .with_for_update()
                )
            ).first()
            if operation_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "operation does not exist; prepare before dispatch", identity
                )
            # 3. 幂等重放先于禁用判定（§5.4：claim 先提交则保该原 operation 认领，
            #    重放不被后续禁用改写；比对按源身份集合，锚定 revision 取保存原值）。
            existing_claims: list[dict] = list(operation_row.data_consent_claims or [])
            requested_identity = {(s.source_id, s.source_version) for s in request.sources}
            for existing in existing_claims:
                if not isinstance(existing, dict):
                    continue
                if existing.get("claim_ref") != request.claim_ref:
                    continue
                saved_sources = existing.get("sources")
                # 已保存认领条目结构异常 fail-closed：按冲突处理，不重算第二份认领。
                if not isinstance(saved_sources, list):
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "saved claim entry malformed",
                        identity,
                    )
                saved_identity = {(s["source_id"], s["source_version"]) for s in saved_sources if isinstance(s, dict)}
                if saved_identity == requested_identity:
                    saved_bindings = tuple(
                        SourceClaimBinding(
                            source_id=s["source_id"],
                            source_version=s["source_version"],
                            disabled_revision=int(s["disabled_revision"]),
                        )
                        for s in saved_sources
                        if isinstance(s, dict)
                    )
                    return SourceClaimFact(claim_ref=request.claim_ref, bindings=saved_bindings, replayed=True)
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "same claim_ref with different source binding",
                    identity,
                )
            if disabled_hits:
                raise AgentServiceError(
                    AgentErrorCode.SOURCE_DISABLED,
                    "disabled source in fresh re-read: " + ",".join(sorted(disabled_hits)),
                    identity,
                )
            # 4. 追加认领（行锁下无丢失更新；operation revision 单调 +1）。
            claim_entry = {
                "kind": "source_revision",
                "claim_ref": request.claim_ref,
                "operation_id": request.operation_id,
                "sources": [
                    {
                        "source_id": b.source_id,
                        "source_version": b.source_version,
                        "disabled_revision": b.disabled_revision,
                    }
                    for b in bindings
                ],
            }
            existing_claims.append(claim_entry)
            await conn.execute(
                sa.update(AgentOperation)
                .where(
                    AgentOperation.user_scope == request.user_scope,
                    AgentOperation.operation_id == request.operation_id,
                )
                .values(
                    data_consent_claims=existing_claims,
                    revision=AgentOperation.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            return SourceClaimFact(claim_ref=request.claim_ref, bindings=tuple(bindings), replayed=False)

    async def list_source_consumers(
        self, *, user_scope: str, source_id: str, source_version: str
    ) -> SourceConsumersPage:
        """来源消费者谱系列举（§9.10/§9.13）：已知边全量返回（FK 保证边表即已知
        闭包），只读不派发；「unknown 受影响阻断」由派发方按关联不明处理。

        Raises:
            AgentServiceError: INPUT_INVALID（来源不存在；严格读不空成功）。
        """

        self._check_engine()
        identity = f"{user_scope}/{source_id}@{source_version}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(source_id, "source_id", identity)
        _require_text_field(source_version, "source_version", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            exists = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentAssetVersion)
                    .where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == source_id,
                        AgentAssetVersion.asset_version == source_version,
                    )
                )
            ).scalar_one()
            if exists == 0:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "source version does not exist", identity)
            link_rows = (
                await conn.execute(
                    sa.select(
                        AgentSourceLink.link_id,
                        AgentSourceLink.source_id,
                        AgentSourceLink.source_version,
                        AgentSourceLink.consumer_kind,
                        AgentSourceLink.consumer_id,
                        AgentSourceLink.consumer_version,
                        AgentSourceLink.relation_kind,
                        AgentSourceLink.disabled_inherited,
                    )
                    .where(
                        AgentSourceLink.user_scope == user_scope,
                        AgentSourceLink.source_id == source_id,
                        AgentSourceLink.source_version == source_version,
                    )
                    .order_by(AgentSourceLink.link_id)
                )
            ).all()
        consumers = tuple(
            SourceConsumerRef(
                link_id=row.link_id,
                source_id=row.source_id,
                source_version=row.source_version,
                consumer_kind=row.consumer_kind,
                consumer_id=row.consumer_id,
                consumer_version=row.consumer_version,
                relation_kind=row.relation_kind,
                disabled_inherited=row.disabled_inherited,
            )
            for row in link_rows
        )
        return SourceConsumersPage(
            source_id=source_id,
            source_version=source_version,
            consumers=consumers,
            read_moment=_utc_now_wire(),
        )

    # ------------------------------------------------------------------
    # C-04-4：阅读水位与决策批次（§4.2 L436/§9.11/AC33/RR04）
    # ------------------------------------------------------------------

    @staticmethod
    def _read_mark_id(user_scope: str, marker_kind: str, target_id: str) -> str:
        return stable_id("read_mark", [user_scope, marker_kind, target_id])

    async def save_read_mark_max(self, command: ReadMarkCommand) -> ReadMarkReceipt:
        """已读标记事务（§4.2 L436/§9.11）：会话 marker 行锁下 GREATEST 水位与
        精确已读身份并集合并，**单个显式事务**。

        - ``marker_kind='session'``、target_id=session_id：last_seq 仅随
          observed_through_seq（与 observation_scope_digest 成对）GREATEST 推进；
          read_targets 为精确已读消息/成果身份并集（不含正文），不全局清零；
        - merge 天然幂等（GREATEST + 并集）：本次无任何状态变化即视为重放
          （replayed=True）；同项重复标记不产生新事实；
        - 会话不存在 ``INPUT_INVALID``（严格读；FK 同样拦截）。

        Raises:
            AgentServiceError: INPUT_INVALID。
        """

        self._check_engine()
        validate_read_mark_command(command)
        identity = f"{command.user_scope}/{command.session_id}/{command.command_id}"
        if not command.message_ids and not command.artifact_refs and command.observed_through_seq is None:
            # 空标记不承载任何已读事实，落行即造零信息事实（检视闭环 m-4，INV-06）。
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "read mark command carries no exact identity or verified watermark",
                identity,
            )
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. 目标会话存在性（用户域严格校验）。
            session_row = (
                await conn.execute(
                    sa.select(AgentSession.session_id).where(
                        AgentSession.user_scope == command.user_scope,
                        AgentSession.session_id == command.session_id,
                    )
                )
            ).first()
            if session_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "target session does not exist in user scope",
                    identity,
                )
            # 2. session marker 行锁（UNIQUE(user_scope,marker_kind,target_id)）。
            marker_id = self._read_mark_id(command.user_scope, "session", command.session_id)
            marker_row = (
                await conn.execute(
                    sa.select(
                        AgentReadMark.last_seq,
                        AgentReadMark.read_targets,
                    )
                    .where(
                        AgentReadMark.user_scope == command.user_scope,
                        AgentReadMark.marker_id == marker_id,
                    )
                    .with_for_update()
                )
            ).first()
            now = datetime.now(UTC)
            landed_seq = max(
                marker_row.last_seq if marker_row is not None else 0,
                command.observed_through_seq if command.observed_through_seq is not None else 0,
            )
            existing_targets: dict = (
                dict(marker_row.read_targets) if marker_row is not None and marker_row.read_targets else {}
            )
            merged_messages = sorted(set(existing_targets.get("message_ids", [])) | set(command.message_ids))
            merged_artifacts = sorted(set(existing_targets.get("artifact_refs", [])) | set(command.artifact_refs))
            changed = marker_row is None or (
                landed_seq != marker_row.last_seq
                or merged_messages != list(existing_targets.get("message_ids", []))
                or merged_artifacts != list(existing_targets.get("artifact_refs", []))
            )
            if changed:
                merged_targets = {"message_ids": merged_messages, "artifact_refs": merged_artifacts}
                values: dict = {
                    "last_seq": landed_seq,
                    "read_targets": merged_targets,
                    "updated_at": now,
                    "revision": AgentReadMark.revision + 1,
                }
                if marker_row is None:
                    await conn.execute(
                        sa.insert(AgentReadMark).values(
                            user_scope=command.user_scope,
                            marker_id=marker_id,
                            marker_kind="session",
                            target_id=command.session_id,
                            session_id=command.session_id,
                            last_seq=landed_seq,
                            read_targets=merged_targets,
                        )
                    )
                else:
                    await conn.execute(
                        sa.update(AgentReadMark)
                        .where(
                            AgentReadMark.user_scope == command.user_scope,
                            AgentReadMark.marker_id == marker_id,
                        )
                        .values(**values)
                    )
            return ReadMarkReceipt(
                session_id=command.session_id,
                command_id=command.command_id,
                last_seq=landed_seq,
                read_target_count=len(merged_messages) + len(merged_artifacts),
                replayed=not changed,
                marked_at=_wire_time(now),
            )

    async def begin_decision_batch(self, *, user_scope: str) -> DecisionBatchFact:
        """待决定新批次登记（§9.11）：准确用户 global marker 行锁下 batch_seq+1。

        仅在**真正产生新待决定批次**的决策创建事务内调用（同项状态变更不造新
        批次，AC33）；marker_kind='global'、target_id=user_scope、session_id NULL
        （全局决定批次标记）。调用方须自行保证语义（DAO 只提供单调计数）。

        Raises:
            AgentServiceError: INPUT_INVALID。
        """

        return await self._mutate_decision_batch(user_scope=user_scope, postpone_through=None)

    async def postpone_decision_batch(self, *, user_scope: str, through_batch_seq: int) -> DecisionBatchFact:
        """稍后处理（AC33/RR04）：global marker 行锁下 postponed_through_seq=
        GREATEST(旧值, through_batch_seq)；乱序（小于现值）不回退不延期限，
        仅暂缓已有批次；marker 行不存在 ``INPUT_INVALID``（无批次可暂缓）。

        Raises:
            AgentServiceError: INPUT_INVALID。
        """

        identity = f"{user_scope}/global"
        _require_text_field(user_scope, "user_scope", identity)
        if isinstance(through_batch_seq, bool) or not isinstance(through_batch_seq, int) or through_batch_seq < 0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "through_batch_seq must be int >= 0", identity)
        return await self._mutate_decision_batch(user_scope=user_scope, postpone_through=through_batch_seq)

    async def _mutate_decision_batch(self, *, user_scope: str, postpone_through: int | None) -> DecisionBatchFact:
        """global marker 行锁公共路径：postpone_through=None 为新批次递增，
        否则为稍后水位 GREATEST 合并（R22）。"""

        identity = f"{user_scope}/global"
        _require_text_field(user_scope, "user_scope", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        marker_id = self._read_mark_id(user_scope, "global", user_scope)
        async with engine.begin() as conn:
            marker_row = (
                await conn.execute(
                    sa.select(
                        AgentReadMark.batch_seq,
                        AgentReadMark.postponed_through_seq,
                    )
                    .where(
                        AgentReadMark.user_scope == user_scope,
                        AgentReadMark.marker_id == marker_id,
                    )
                    .with_for_update()
                )
            ).first()
            now = datetime.now(UTC)
            if postpone_through is None:
                # 新批次：行锁下单调递增 batch_seq。
                landed_batch = (marker_row.batch_seq if marker_row is not None else 0) + 1
                if marker_row is None:
                    await conn.execute(
                        sa.insert(AgentReadMark).values(
                            user_scope=user_scope,
                            marker_id=marker_id,
                            marker_kind="global",
                            target_id=user_scope,
                            session_id=None,
                            batch_seq=landed_batch,
                        )
                    )
                else:
                    await conn.execute(
                        sa.update(AgentReadMark)
                        .where(
                            AgentReadMark.user_scope == user_scope,
                            AgentReadMark.marker_id == marker_id,
                        )
                        .values(
                            batch_seq=landed_batch,
                            revision=AgentReadMark.revision + 1,
                            updated_at=now,
                        )
                    )
                return DecisionBatchFact(
                    batch_seq=landed_batch,
                    postponed_through_seq=None,
                    replayed=False,
                    recorded_at=_wire_time(now),
                )
            # 稍后：仅暂缓已有批次（marker 行缺失=无批次，INPUT_INVALID；
            # through 超过现有 batch_seq=暂缓尚不存在的批次，同判 INPUT_INVALID——
            # AC33「稍后仅现批次」，检视闭环 m-7）。
            if marker_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "no decision batch exists to postpone", identity)
            if postpone_through > marker_row.batch_seq:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "through_batch_seq exceeds existing decision batch",
                    identity,
                )
            landed = max(marker_row.postponed_through_seq, postpone_through)
            changed = landed != marker_row.postponed_through_seq
            if changed:
                await conn.execute(
                    sa.update(AgentReadMark)
                    .where(
                        AgentReadMark.user_scope == user_scope,
                        AgentReadMark.marker_id == marker_id,
                    )
                    .values(
                        postponed_through_seq=landed,
                        revision=AgentReadMark.revision + 1,
                        updated_at=now,
                    )
                )
            return DecisionBatchFact(
                batch_seq=marker_row.batch_seq,
                postponed_through_seq=landed,
                replayed=not changed,
                recorded_at=_wire_time(now),
            )

    # ------------------------------------------------------------------
    # D-03：启动隔离/派发门控/手动恢复（HANDOFF §8.4，设计 §9.2/§9.5）
    # ------------------------------------------------------------------

    async def list_recovery_executions(self, user_scope: str) -> tuple[RecoveryExecution, ...]:
        """恢复候选执行（非终态投影 + 当前有效轮原生引用，§9.2/§9.5）。

        启动隔离与手动恢复共用本读取：叶执行的原生 workflow 身份不在此登记
        （由原生列举按 ``agent_leaf`` 名称覆盖），调用方须把本集合与原生列举
        取并集后再固定快照（HANDOFF §8.4 第 2 条）。

        Raises:
            AgentServiceError: INPUT_INVALID（user_scope 非法）。
        """

        self._check_engine()
        identity = f"{user_scope}/recovery"
        _require_text_field(user_scope, "user_scope", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        AgentExecution.execution_id,
                        AgentExecution.session_id,
                        AgentExecution.execution_kind,
                        AgentExecution.display_state,
                        AgentExecution.owner_epoch,
                        AgentExecution.dispatch_closed,
                        AgentExecution.ended_at,
                        AgentRound.round_id,
                        AgentRound.round_number,
                        AgentRound.workflow_id,
                        AgentRound.run_epoch,
                        AgentRound.stop_reason,
                    )
                    .outerjoin(
                        AgentRound,
                        sa.and_(
                            AgentRound.user_scope == AgentExecution.user_scope,
                            AgentRound.round_id == AgentExecution.current_round_id,
                        ),
                    )
                    .where(
                        AgentExecution.user_scope == user_scope,
                        AgentExecution.display_state.not_in(sorted(NON_RECOVERABLE_DISPLAY_STATES)),
                    )
                    .order_by(AgentExecution.execution_id)
                )
            ).all()
        return tuple(
            RecoveryExecution(
                execution_id=row.execution_id,
                session_id=row.session_id,
                execution_kind=row.execution_kind,
                display_state=row.display_state,
                owner_epoch=row.owner_epoch,
                dispatch_closed=row.dispatch_closed,
                ended_at=None if row.ended_at is None else _wire_time(row.ended_at),
                round_id=row.round_id,
                round_number=row.round_number,
                workflow_id=row.workflow_id,
                run_epoch=row.run_epoch,
                stop_reason=row.stop_reason,
            )
            for row in rows
        )

    async def mark_dispatch_closed(self, request: DispatchCloseRequest) -> DispatchCloseFact:
        """关闭应用派发门闩（§9.2 ``dispatch_closed``；启动隔离/手动恢复共用）。

        以当前有效轮的 ``workflow_id`` 定位执行行并置 ``dispatch_closed=true``
        （幂等：已关闭行不再写回）。门闩关闭**不等于已停止**，原生取消/退出核定
        仍由隔离与关闭门闩负责。未匹配的 workflow ID 不计失败（原生有而应用
        尚未登记，例如叶执行）。

        Raises:
            AgentServiceError: INPUT_INVALID（user_scope 非法）。
        """

        self._check_engine()
        identity = f"{request.user_scope}/recovery"
        _require_text_field(request.user_scope, "user_scope", identity)
        workflow_ids = tuple(sorted({value for value in request.workflow_ids if isinstance(value, str) and value}))
        if not workflow_ids:
            return DispatchCloseFact(user_scope=request.user_scope, closed_execution_ids=())
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        now = datetime.now(UTC)
        async with engine.begin() as conn:
            matched = (
                await conn.execute(
                    sa.select(AgentExecution.execution_id, AgentExecution.dispatch_closed)
                    .join(
                        AgentRound,
                        sa.and_(
                            AgentRound.user_scope == AgentExecution.user_scope,
                            AgentRound.round_id == AgentExecution.current_round_id,
                        ),
                    )
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentRound.workflow_id.in_(workflow_ids),
                    )
                    .order_by(AgentExecution.execution_id)
                    .with_for_update(of=AgentExecution)
                )
            ).all()
            to_close = [row.execution_id for row in matched if not row.dispatch_closed]
            if to_close:
                await conn.execute(
                    sa.update(AgentExecution)
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id.in_(to_close),
                    )
                    .values(
                        dispatch_closed=True,
                        revision=AgentExecution.revision + 1,
                        updated_at=now,
                    )
                )
        return DispatchCloseFact(
            user_scope=request.user_scope,
            closed_execution_ids=tuple(row.execution_id for row in matched),
        )

    async def save_startup_isolation(self, record: StartupIsolationRecord) -> str:
        """保存启动隔离命令（HANDOFF §8.4 第 2 条：固定快照后、取消之前）。

        命令身份 ``stable_id("command", [user_scope, target_id, client_command_id])``；
        同身份同快照幂等返回原 ID，同身份异快照 ``INPUT_CONFLICT``（不覆盖原
        事实）。命令行记录快照执行 ID 与原状态；取消/复核结果由
        :meth:`finalize_startup_isolation` 回填。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT。
        """

        self._check_engine()
        identity = f"{record.user_scope}/startup-isolation"
        _require_text_field(record.user_scope, "user_scope", identity)
        _require_text_field(record.client_command_id, "client_command_id", identity)
        _require_text_field(record.target_id, "target_id", identity)
        command_record_id = stable_id("command", [record.user_scope, record.target_id, record.client_command_id])
        input_digest = sha256_hex(record.snapshot)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            existing = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest).where(
                        AgentCommand.user_scope == record.user_scope,
                        AgentCommand.command_record_id == command_record_id,
                    )
                )
            ).first()
            if existing is not None:
                if existing.input_digest != input_digest:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "startup isolation command identity already used with a different snapshot",
                        identity,
                    )
                return command_record_id
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=record.user_scope,
                    command_record_id=command_record_id,
                    client_command_id=record.client_command_id,
                    target_id=record.target_id,
                    command_kind="startup_isolation",
                    expected_revision=0,
                    input_digest=input_digest,
                    request_payload=json.loads(record.snapshot),
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.DELIVERED.value,
                    consumption_state=ConsumptionState.CONSUMED.value,
                    receipt_payload=json.loads(record.receipt_payload),
                    verified_at=datetime.now(UTC),
                )
            )
        return command_record_id

    async def finalize_startup_isolation(
        self,
        *,
        user_scope: str,
        isolation_command_id: str,
        receipt_payload: str,
    ) -> None:
        """回填启动隔离复核结果（取消/复查完成或阻塞原因；幂等覆盖本命令回执）。

        Raises:
            AgentServiceError: INPUT_INVALID（命令行不存在）。
        """

        self._check_engine()
        identity = f"{user_scope}/startup-isolation"
        _require_text_field(isolation_command_id, "isolation_command_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.update(AgentCommand)
                .where(
                    AgentCommand.user_scope == user_scope,
                    AgentCommand.command_record_id == isolation_command_id,
                    AgentCommand.command_kind == "startup_isolation",
                )
                .values(
                    receipt_payload=json.loads(receipt_payload),
                    revision=AgentCommand.revision + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount == 0:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "startup isolation command not found in user scope",
                    identity,
                )

    async def grant_recovery_epoch(self, request: RecoveryGrantRequest) -> RecoveryGrantFact:
        """手动恢复准入代次授予（HANDOFF §8.4 第 5 条：仅精确选择集合）。

        单事务行锁下逐项核：存在、非不可恢复终态、非未核状态（unknown/ending）、
        非用户终止、有活动当前轮、轮代码版本与 ``application_version`` 一致
        （兼容矩阵）、旧执行无未收口 operation（§8.5 unknown 先核原 operation）、
        新 epoch 严格大于现值（单调，R22）；合格项写 ``owner_epoch`` 并重开
        ``dispatch_closed``，其有效轮写 ``run_epoch``。不合格项进入 ``blocked``
        （明确拒绝，不静默跳过），对应原生集合不返回，故不会被 resume。

        Raises:
            AgentServiceError: INPUT_INVALID（入参非法/空集合/epoch 非正/版本空）。
        """

        self._check_engine()
        identity = f"{request.user_scope}/recovery"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.application_version, "application_version", identity)
        execution_ids = tuple(sorted(set(request.execution_ids)))
        if not execution_ids:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "execution_ids must be non-empty", identity)
        _require_int_field(request.owner_epoch, "owner_epoch", identity, 1)
        _require_int_field(request.run_epoch, "run_epoch", identity, 1)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        blocked: list[tuple[str, str]] = []
        granted: list[str] = []
        workflow_ids: list[str] = []
        round_ids: list[str] = []
        now = datetime.now(UTC)
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        AgentExecution.execution_id,
                        AgentExecution.display_state,
                        AgentExecution.owner_epoch,
                        AgentRound.round_id,
                        AgentRound.workflow_id,
                        AgentRound.run_epoch,
                        AgentRound.stop_reason,
                        AgentRound.active_round,
                        AgentRound.workflow_code_version,
                    )
                    .outerjoin(
                        AgentRound,
                        sa.and_(
                            AgentRound.user_scope == AgentExecution.user_scope,
                            AgentRound.round_id == AgentExecution.current_round_id,
                        ),
                    )
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id.in_(execution_ids),
                    )
                    .order_by(AgentExecution.execution_id)
                    .with_for_update(of=AgentExecution)
                )
            ).all()
            open_operations = {
                row.execution_id
                for row in (
                    await conn.execute(
                        sa.select(AgentOperation.execution_id).where(
                            AgentOperation.execution_id.in_(execution_ids),
                            AgentOperation.phase.in_(sorted(OPEN_OPERATION_PHASES)),
                        )
                    )
                ).all()
            }
            by_id = {row.execution_id: row for row in rows}
            for execution_id in execution_ids:
                row = by_id.get(execution_id)
                if row is None:
                    blocked.append((execution_id, "not_found"))
                    continue
                if row.display_state in NON_RECOVERABLE_DISPLAY_STATES:
                    blocked.append((execution_id, "already_terminal"))
                    continue
                if row.display_state in RECOVERY_UNVERIFIED_DISPLAY_STATES:
                    blocked.append((execution_id, "state_unverified"))
                    continue
                if row.stop_reason == ROUND_STOP_REASON_USER_TERMINATION:
                    blocked.append((execution_id, "user_termination"))
                    continue
                if row.round_id is None or row.workflow_id is None:
                    blocked.append((execution_id, "no_active_round"))
                    continue
                if not row.active_round:
                    blocked.append((execution_id, "round_not_active"))
                    continue
                if row.workflow_code_version != request.application_version:
                    blocked.append((execution_id, "version_mismatch"))
                    continue
                if execution_id in open_operations:
                    blocked.append((execution_id, "operation_in_flight"))
                    continue
                if request.owner_epoch <= row.owner_epoch:
                    blocked.append((execution_id, "epoch_not_advanced"))
                    continue
                if row.run_epoch is not None and request.run_epoch <= row.run_epoch:
                    blocked.append((execution_id, "epoch_not_advanced"))
                    continue
                granted.append(execution_id)
                workflow_ids.append(row.workflow_id)
                round_ids.append(row.round_id)
            if granted:
                await conn.execute(
                    sa.update(AgentExecution)
                    .where(
                        AgentExecution.user_scope == request.user_scope,
                        AgentExecution.execution_id.in_(granted),
                    )
                    .values(
                        owner_epoch=request.owner_epoch,
                        dispatch_closed=False,
                        revision=AgentExecution.revision + 1,
                        updated_at=now,
                    )
                )
                await conn.execute(
                    sa.update(AgentRound)
                    .where(
                        AgentRound.user_scope == request.user_scope,
                        AgentRound.round_id.in_(round_ids),
                    )
                    .values(
                        run_epoch=request.run_epoch,
                        revision=AgentRound.revision + 1,
                        updated_at=now,
                    )
                )
        return RecoveryGrantFact(
            user_scope=request.user_scope,
            owner_epoch=request.owner_epoch,
            run_epoch=request.run_epoch,
            granted_execution_ids=tuple(granted),
            workflow_ids=tuple(sorted(set(workflow_ids))),
            blocked=tuple(blocked),
        )

    async def register_business_sources(
        self, request: BusinessSourceRegistrationRequest
    ) -> BusinessSourceRegistrationReceipt:
        """业务来源登记（§3.3/§9.10，E-01）：asset 版本 + 消费者入边**单事务**。

        - 仅登记请求中的显式选择集合；未选来源不被本方法接触（D2 零使用）；
        - 同 (source_id, version, consumer) 同规范输入幂等回原回执（replayed=True）；
          摘要/元数据/定位不一致 → ``INPUT_CONFLICT``（不静默覆盖）；
        - ``available_at`` 必须为带时区 UTC 文本或 NULL；时点策略（PIT）由服务层
          在执行本事务前拒绝，DAO 只落已核事实；
        - 来源正文不在此复制（asset 只保存元数据与引用）。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT / SOURCE_DISABLED。
        """

        self._check_engine()
        identity = f"{request.user_scope}/{request.consumer_kind}/{request.consumer_id}@{request.consumer_version}"
        _require_text_field(request.user_scope, "user_scope", identity)
        _require_text_field(request.consumer_kind, "consumer_kind", identity)
        _require_text_field(request.consumer_id, "consumer_id", identity)
        _require_text_field(request.consumer_version, "consumer_version", identity)
        if request.consumer_kind not in SOURCE_REGISTRATION_CONSUMER_KINDS:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "consumer_kind is not registrable", identity)
        if not request.items:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "sources must not be empty", identity)
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(request.items):
            source = item.source
            item_identity = f"{identity}/items[{index}]"
            _require_text_field(source.source_id, "source_id", item_identity)
            _require_text_field(source.version, "version", item_identity)
            _require_text_field(source.digest, "digest", item_identity)
            if source.user_scope != request.user_scope:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "source user_scope mismatch", item_identity)
            if source.read_state is ReadState.DISABLED:
                raise AgentServiceError(AgentErrorCode.SOURCE_DISABLED, "source version is disabled", item_identity)
            key = (source.source_id, source.version)
            if key in seen:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "duplicate source in request", item_identity)
            seen.add(key)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        ordered = sorted(request.items, key=lambda item: (item.source.source_id, item.source.version))
        registered: list[SourceVersionRef] = []
        link_ids: list[str] = []
        replayed = True
        async with engine.begin() as conn:
            for item in ordered:
                source = item.source
                asset_identity = f"{request.user_scope}/{source.source_id}@{source.version}"
                payload = {
                    "schema_version": 1,
                    "source_ref": encode_source_ref(source),
                    "verification": item.verification.value,
                }
                available_at = _wire_timestamp_or_none(source.available_at, asset_identity)
                asset_row = (
                    await conn.execute(
                        sa.select(AgentAssetVersion.digest, AgentAssetVersion.metadata_payload)
                        .where(
                            AgentAssetVersion.user_scope == request.user_scope,
                            AgentAssetVersion.asset_id == source.source_id,
                            AgentAssetVersion.asset_version == source.version,
                        )
                        .with_for_update()
                    )
                ).first()
                if asset_row is None:
                    replayed = False
                    await conn.execute(
                        sa.insert(AgentAssetVersion).values(
                            user_scope=request.user_scope,
                            asset_id=source.source_id,
                            asset_version=source.version,
                            asset_kind="source",
                            display_name=source.display_name,
                            digest=source.digest,
                            origin_ref=(
                                f"{source.locator.origin_ref.source_kind.value}:"
                                f"{source.locator.origin_ref.source_id}@{source.locator.origin_ref.source_version}"
                            ),
                            metadata_payload=payload,
                            read_state=source.read_state.value,
                            verification_state=item.verification.value,
                            available_at=available_at,
                        )
                    )
                elif asset_row.digest != source.digest or json_doc(dict(asset_row.metadata_payload or {})) != json_doc(
                    payload
                ):
                    raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "source version differs", asset_identity)

                link_id = stable_id(
                    "source_link",
                    [
                        request.user_scope,
                        source.source_id,
                        source.version,
                        request.consumer_kind,
                        request.consumer_id,
                        request.consumer_version,
                    ],
                )
                locator_payload = to_wire_dict(source.locator)
                existing_link = (
                    await conn.execute(
                        sa.select(
                            AgentSourceLink.link_id,
                            AgentSourceLink.locator_payload,
                            AgentSourceLink.relation_kind,
                        ).where(
                            AgentSourceLink.source_id == source.source_id,
                            AgentSourceLink.source_version == source.version,
                            AgentSourceLink.consumer_kind == request.consumer_kind,
                            AgentSourceLink.consumer_id == request.consumer_id,
                            AgentSourceLink.consumer_version == request.consumer_version,
                        )
                    )
                ).first()
                if existing_link is None:
                    replayed = False
                    await conn.execute(
                        sa.insert(AgentSourceLink).values(
                            user_scope=request.user_scope,
                            link_id=link_id,
                            source_id=source.source_id,
                            source_version=source.version,
                            consumer_kind=request.consumer_kind,
                            consumer_id=request.consumer_id,
                            consumer_version=request.consumer_version,
                            execution_id=request.consumer_id if request.consumer_kind == "execution" else None,
                            round_id=request.consumer_id if request.consumer_kind == "round" else None,
                            message_id=request.consumer_id if request.consumer_kind == "message" else None,
                            locator_payload=locator_payload,
                            relation_kind=SourceRelationKind.ACCEPTED_INPUT.value,
                        )
                    )
                elif existing_link.link_id != link_id or json_doc(
                    dict(existing_link.locator_payload or {})
                ) != json_doc(locator_payload):
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT, "source locator differs for consumer", asset_identity
                    )
                registered.append(
                    SourceVersionRef(
                        source_kind=source.source_kind,
                        source_id=source.source_id,
                        source_version=source.version,
                        source_digest=source.digest,
                    )
                )
                link_ids.append(link_id)
        return BusinessSourceRegistrationReceipt(
            registered=tuple(registered),
            link_ids=tuple(link_ids),
            replayed=replayed,
        )

    async def tombstone_cleanup_text(
        self,
        user_scope: str,
        *,
        session_ids: Sequence[str] = (),
        asset_keys: Sequence[tuple[str, str]] = (),
    ) -> dict[str, int]:
        """E-06 应用域清理（DD §10.7 字段级最小留存）：NOT NULL 文本/摘要换固定
        tombstone，可 NULL 的私人草稿/来源/元数据与正文引用清除；集合外不变。

        单事务执行；两个域都为空时零写入（仅「未启动计划」允许空原生集合，应用域
        是否可空由服务层准入判定）。返回各域命中行数。

        E-06-5a 起覆盖正文承载域（§10.7 表第 1—3 行「删除正文及受管副本」）：
        ``agent_message``/``agent_artifact``/``agent_round``/``agent_operation`` 的正文
        与载荷列清空，``agent_body_payload`` 对应行准确删除。

        E-06-5b 起按 §10.7 表第 2—4 行处理结构回执与内容摘要：结构载荷/回执列
        （命令请求与回执、决定问题与答案 schema、同意载荷、操作交付清单与 worker
        引用、来源 locator）清空；可 NULL 的内容摘要（执行意图、轮次冻结/实际、参数、
        来源作用域）置 NULL；NOT NULL 的内容摘要（成果 ``actual_input_digest``/
        ``result_digest``、命令 ``input_digest``）换固定 tombstone 摘要。身份、fence、
        unknown/phase、计量与 ``attempt_refs`` 等核定所需非正文引用保原值。

        交付回执清空后由 ``read_delivery`` 报 ``manifest_cleaned=True``（会话
        ``cleaned_at`` 非空且 artifact 身份仍在，即确曾交付），不把「已交付但已清理」
        读成「从未交付」。
        """

        self._check_engine()
        if not user_scope:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope is required", "cleanup")
        sessions = self._normalize_cleanup_values(session_ids)
        assets = self._normalize_asset_keys(asset_keys)
        touched = {
            "agent_session": 0,
            "agent_asset_version": 0,
            "agent_message": 0,
            "agent_artifact": 0,
            "agent_round": 0,
            "agent_operation": 0,
            "agent_body_payload": 0,
            "agent_execution": 0,
            "agent_decision": 0,
            "agent_command": 0,
            "agent_source_link": 0,
        }
        if not sessions and not assets:
            return touched
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        body_refs: set[str] = set()
        async with engine.begin() as conn:
            if sessions:
                session_scope = list(sessions)
                session_result = await conn.execute(
                    sa.update(AgentSession)
                    .where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id.in_(session_scope),
                    )
                    .values(
                        title=CLEANUP_TOMBSTONE,
                        draft_payload=None,
                        origin_ref=None,
                        branch_metadata=None,
                        cleaned_at=sa.func.now(),
                    )
                )
                touched["agent_session"] = int(session_result.rowcount or 0)

                execution_scope = sa.select(AgentExecution.execution_id).where(
                    AgentExecution.user_scope == user_scope,
                    AgentExecution.session_id.in_(session_scope),
                )
                message_refs = (
                    (
                        await conn.execute(
                            sa.select(AgentMessage.body_ref).where(
                                AgentMessage.user_scope == user_scope,
                                AgentMessage.session_id.in_(session_scope),
                                AgentMessage.body_ref.is_not(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                body_refs.update(ref for ref in message_refs if ref)
                artifact_refs = (
                    (
                        await conn.execute(
                            sa.select(AgentArtifact.body_ref).where(
                                AgentArtifact.user_scope == user_scope,
                                AgentArtifact.session_id.in_(session_scope),
                                AgentArtifact.body_ref.is_not(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                body_refs.update(ref for ref in artifact_refs if ref)

                message_result = await conn.execute(
                    sa.update(AgentMessage)
                    .where(
                        AgentMessage.user_scope == user_scope,
                        AgentMessage.session_id.in_(session_scope),
                    )
                    .values(body_text=None, body_ref=None, payload=None, search_text=None)
                )
                touched["agent_message"] = int(message_result.rowcount or 0)

                artifact_result = await conn.execute(
                    sa.update(AgentArtifact)
                    .where(
                        AgentArtifact.user_scope == user_scope,
                        AgentArtifact.session_id.in_(session_scope),
                    )
                    .values(
                        summary=None,
                        body_text=None,
                        body_ref=None,
                        result_payload=None,
                        actual_input_digest=CLEANUP_TOMBSTONE_DIGEST,
                        result_digest=CLEANUP_TOMBSTONE_DIGEST,
                        cleaned_at=sa.func.now(),
                    )
                )
                touched["agent_artifact"] = int(artifact_result.rowcount or 0)

                round_result = await conn.execute(
                    sa.update(AgentRound)
                    .where(
                        AgentRound.user_scope == user_scope,
                        AgentRound.execution_id.in_(execution_scope),
                    )
                    .values(
                        frozen_manifest=None,
                        actual_input=None,
                        model_binding=None,
                        sdk_history_ref=None,
                        frozen_digest=None,
                        actual_digest=None,
                    )
                )
                touched["agent_round"] = int(round_result.rowcount or 0)

                operation_result = await conn.execute(
                    sa.update(AgentOperation)
                    .where(
                        AgentOperation.user_scope == user_scope,
                        sa.or_(
                            AgentOperation.session_id.in_(session_scope),
                            AgentOperation.execution_id.in_(execution_scope),
                        ),
                    )
                    .values(
                        result_payload=None,
                        request_ref=None,
                        result_ref=None,
                        parameter_digest=None,
                        worker_refs=None,
                        delivery_manifest=None,
                        data_consent_claims=None,
                    )
                )
                touched["agent_operation"] = int(operation_result.rowcount or 0)

                execution_result = await conn.execute(
                    sa.update(AgentExecution)
                    .where(
                        AgentExecution.user_scope == user_scope,
                        AgentExecution.session_id.in_(session_scope),
                    )
                    .values(intent_digest=None)
                )
                touched["agent_execution"] = int(execution_result.rowcount or 0)

                decision_result = await conn.execute(
                    sa.update(AgentDecision)
                    .where(
                        AgentDecision.user_scope == user_scope,
                        AgentDecision.session_id.in_(session_scope),
                    )
                    .values(
                        question_payload=None,
                        answer_schema=None,
                        parameter_digest=None,
                        source_scope_digest=None,
                        data_consent_payload=None,
                    )
                )
                touched["agent_decision"] = int(decision_result.rowcount or 0)

                command_result = await conn.execute(
                    sa.update(AgentCommand)
                    .where(
                        AgentCommand.user_scope == user_scope,
                        sa.or_(
                            AgentCommand.session_id.in_(session_scope),
                            AgentCommand.execution_id.in_(execution_scope),
                        ),
                    )
                    .values(
                        request_payload=None,
                        receipt_payload=None,
                        input_digest=CLEANUP_TOMBSTONE_DIGEST,
                    )
                )
                touched["agent_command"] = int(command_result.rowcount or 0)
            if assets:
                asset_scope = list(assets)
                asset_refs = (
                    (
                        await conn.execute(
                            sa.select(AgentAssetVersion.body_ref).where(
                                AgentAssetVersion.user_scope == user_scope,
                                sa.tuple_(AgentAssetVersion.asset_id, AgentAssetVersion.asset_version).in_(asset_scope),
                                AgentAssetVersion.body_ref.is_not(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                body_refs.update(ref for ref in asset_refs if ref)
                asset_result = await conn.execute(
                    sa.update(AgentAssetVersion)
                    .where(
                        AgentAssetVersion.user_scope == user_scope,
                        sa.tuple_(AgentAssetVersion.asset_id, AgentAssetVersion.asset_version).in_(asset_scope),
                    )
                    .values(
                        display_name=CLEANUP_TOMBSTONE,
                        digest=CLEANUP_TOMBSTONE_DIGEST,
                        origin_ref=None,
                        metadata_payload=None,
                        body_text=None,
                        body_ref=None,
                        cleaned_at=sa.func.now(),
                        tombstoned=True,
                    )
                )
                touched["agent_asset_version"] = int(asset_result.rowcount or 0)
            # 来源边的 locator 可能含私人 origin/路径（§10.7 表第 3 行）；按会话边
            # 与资产边合并为单条更新，避免交集行重复计数。其他列（source_id、
            # claim_ref、relation_kind、disabled_inherited）为归属/禁用核定所需。
            link_clauses: list[Any] = []
            if sessions:
                link_clauses.append(AgentSourceLink.session_id.in_(list(sessions)))
            if assets:
                asset_scope = list(assets)
                link_clauses.append(
                    sa.tuple_(AgentSourceLink.source_id, AgentSourceLink.source_version).in_(asset_scope)
                )
                link_clauses.append(
                    sa.tuple_(AgentSourceLink.derived_asset_id, AgentSourceLink.derived_asset_version).in_(asset_scope)
                )
            if link_clauses:
                link_result = await conn.execute(
                    sa.update(AgentSourceLink)
                    .where(AgentSourceLink.user_scope == user_scope, sa.or_(*link_clauses))
                    .values(locator_payload=None)
                )
                touched["agent_source_link"] = int(link_result.rowcount or 0)
            if body_refs:
                payload_result = await conn.execute(
                    sa.delete(AgentBodyPayload).where(
                        AgentBodyPayload.user_scope == user_scope,
                        AgentBodyPayload.body_ref.in_(sorted(body_refs)),
                    )
                )
                touched["agent_body_payload"] = int(payload_result.rowcount or 0)
        return touched

    @staticmethod
    def _normalize_cleanup_values(values: Sequence[str]) -> tuple[str, ...]:
        """非空/去重/排序 + 200 上限；空集合返回 ()，由调用方按准入语义判定。"""

        try:
            return normalize_cleanup_ids(values)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"invalid cleanup ids: {exc.code}", "cleanup"
            ) from exc

    @classmethod
    def _normalize_asset_keys(cls, keys: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        """资产版本键非空/去重/排序 + 200 上限（资产版本是稳定身份，不与正文混算）。"""

        try:
            return normalize_cleanup_asset_keys(keys)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"invalid cleanup asset keys: {exc.code}", "cleanup"
            ) from exc

    # ------------------------------------------------------------------
    # E-06-3 清理作业持久化（§9.11/§10.7）：固定集合 + fence + 阶段推进。
    # 集合一经写入不可改写；fence 只在原预览摘要/修订匹配时提交一次。
    # ------------------------------------------------------------------

    async def save_cleanup_job(
        self, record: CleanupJobRecord, *, content_manifest: dict[str, object] | None = None
    ) -> bool:
        """创建清理作业；同身份重放返回 False，异输入抛 ``INPUT_CONFLICT``。

        身份 = (user_scope, cleanup_job_id, owner_ref, preview_digest, 固定集合)；
        ``content_manifest`` 同属 §9.11 固定集合，同身份重放时须逐值一致，否则
        拒绝（避免首个值被静默保留、后续调用方误以为自己的集合已生效）。
        """

        self._check_engine()
        engine = self._require_async_engine()
        identity = encode_cleanup_identity_set(record)
        label = f"{record.user_scope}/{record.cleanup_job_id}"
        try:
            # 入口即校验规范 JSON：不能入库成功而重放时才因不可承载类型炸掉。
            canonical_manifest = json_doc(content_manifest)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"content_manifest must be canonical JSON: {exc.code}", label
            ) from exc
        # 落库值取规范 JSON 的反解析结果：JSONB 编码器只接受基础类型，Decimal 等
        # 规范 JSON 可承载而 json.dumps 拒绝的类型不得以 StatementError 逃逸。
        stored_manifest = None if content_manifest is None else json.loads(canonical_manifest)
        statement = sa.insert(AgentCleanupJob).values(
            user_scope=record.user_scope,
            cleanup_job_id=record.cleanup_job_id,
            owner_ref=record.owner_ref,
            preview_digest=record.preview_digest,
            expected_revision=record.expected_revision,
            exact_identity_set=identity,
            content_manifest=stored_manifest,
            phase=record.phase.value,
            complete=False,
            fence_committed=record.fence_committed,
        )
        try:
            async with engine.begin() as conn:
                await conn.execute(statement)
        except IntegrityError as exc:
            existing = await self.load_cleanup_job(record.user_scope, record.cleanup_job_id)
            self._require_same_cleanup_identity(existing, record, exc)
            existing_manifest = await self._load_cleanup_content_manifest(record.user_scope, record.cleanup_job_id)
            # 规范 JSON 比较：tuple/list、键序与 JSONB 反序列化差异不得误判为异输入。
            if json_doc(existing_manifest) != canonical_manifest:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "cleanup job already exists with different content manifest",
                    label,
                ) from exc
            return False
        return True

    async def _load_cleanup_content_manifest(self, user_scope: str, cleanup_job_id: str) -> dict[str, object] | None:
        """读取固定集合中的 ``content_manifest``（仅用于同身份重放核对）。"""

        engine = self._require_async_engine()
        async with engine.connect() as conn:
            value = (
                await conn.execute(
                    sa.select(AgentCleanupJob.content_manifest).where(
                        AgentCleanupJob.user_scope == user_scope,
                        AgentCleanupJob.cleanup_job_id == cleanup_job_id,
                    )
                )
            ).scalar_one_or_none()
        return value if isinstance(value, dict) else None

    async def load_cleanup_job(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        """读取作业事实；存储载形不可解释时显式失败（不返回伪造默认）。"""

        self._check_engine()
        engine = self._require_async_engine()
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentCleanupJob.user_scope,
                        AgentCleanupJob.cleanup_job_id,
                        AgentCleanupJob.owner_ref,
                        AgentCleanupJob.preview_digest,
                        AgentCleanupJob.expected_revision,
                        AgentCleanupJob.exact_identity_set,
                        AgentCleanupJob.phase,
                        AgentCleanupJob.fence_committed,
                        AgentCleanupJob.error_category,
                    ).where(
                        AgentCleanupJob.user_scope == user_scope,
                        AgentCleanupJob.cleanup_job_id == cleanup_job_id,
                    )
                )
            ).first()
        return None if row is None else self._cleanup_record_from_row(row)

    async def commit_cleanup_fence(self, record: CleanupJobRecord) -> None:
        """提交删除 fence（只一次）；集合与摘要不匹配则拒绝，不静默改写。

        fence 提交只写 ``fence_committed``，不改写 ``phase``：阶段推进唯一入口是
        ``save_cleanup_phase``（单调约束），否则携带旧 ``prepared`` 记录的并发确认
        会把 ``retry_pending`` 回退成 ``prepared``。已 ``verified`` 的作业拒绝任何
        fence 写入（与 ``save_cleanup_phase`` 同一单调约束，R22 语义）。
        """

        self._check_engine()
        engine = self._require_async_engine()
        label = f"{record.user_scope}/{record.cleanup_job_id}"
        if record.phase is CleanupPhase.VERIFIED:
            # 终态不可由 fence 路径写入（否则会出现 verified 但无 verified_at 的坏态）。
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "verified cleanup job cannot rewrite fence", label)
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.update(AgentCleanupJob)
                .where(
                    AgentCleanupJob.user_scope == record.user_scope,
                    AgentCleanupJob.cleanup_job_id == record.cleanup_job_id,
                    AgentCleanupJob.preview_digest == record.preview_digest,
                    AgentCleanupJob.expected_revision == record.expected_revision,
                    AgentCleanupJob.phase != CleanupPhase.VERIFIED.value,
                )
                .values(fence_committed=True)
            )
            if int(result.rowcount or 0) == 0:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "cleanup fence target missing, drifted, or already verified", label
                )

    async def save_cleanup_phase(
        self,
        user_scope: str,
        cleanup_job_id: str,
        *,
        phase: CleanupPhase,
        verified_counts: dict[str, int] | None = None,
        complete: bool = False,
        error_category: str | None = None,
        sqlstate: str | None = None,
    ) -> None:
        """推进阶段：``verified`` 要求 fence 已提交；不改写固定集合。

        阶段单调（R22 语义）：已 ``verified`` 的作业拒绝任何非 ``verified`` 写入，
        防止 ``retry_pending`` 覆盖完成态、留下不清的 ``verified_at``。
        """

        self._check_engine()
        engine = self._require_async_engine()
        label = f"{user_scope}/{cleanup_job_id}"
        values: dict[str, Any] = {
            "phase": phase.value,
            "verified_counts": verified_counts,
            "complete": complete,
            "error_category": error_category,
            "sqlstate": sqlstate,
        }
        if phase is CleanupPhase.VERIFIED:
            values["verified_at"] = sa.func.now()
        statement = (
            sa.update(AgentCleanupJob)
            .where(AgentCleanupJob.user_scope == user_scope, AgentCleanupJob.cleanup_job_id == cleanup_job_id)
            .values(**values)
        )
        if phase is CleanupPhase.VERIFIED:
            statement = statement.where(AgentCleanupJob.fence_committed.is_(True))
        else:
            statement = statement.where(AgentCleanupJob.phase != CleanupPhase.VERIFIED.value)
        async with engine.begin() as conn:
            result = await conn.execute(statement)
            if int(result.rowcount or 0) == 0:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "cleanup job phase target missing, fence absent, or already verified",
                    label,
                )

    def _require_async_engine(self) -> AsyncEngine:
        engine = self.engine
        if not isinstance(engine, AsyncEngine):
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE, "cleanup requires an async engine", "cleanup"
            )
        return engine

    @staticmethod
    def _require_same_cleanup_identity(
        existing: CleanupJobRecord | None, record: CleanupJobRecord, cause: Exception
    ) -> None:
        label = f"{record.user_scope}/{record.cleanup_job_id}"
        if (
            existing is None
            or existing.owner_ref != record.owner_ref
            or existing.preview_digest != record.preview_digest
            or encode_cleanup_identity_set(existing) != encode_cleanup_identity_set(record)
        ):
            raise AgentServiceError(
                AgentErrorCode.INPUT_CONFLICT, "cleanup job already exists with different identity", label
            ) from cause

    @staticmethod
    def _cleanup_record_from_row(row: Any) -> CleanupJobRecord:
        user_scope, cleanup_job_id, owner_ref, preview_digest, expected_revision, identity_set, phase, fence, error = (
            row
        )
        label = f"{user_scope}/{cleanup_job_id}"
        try:
            identities = decode_cleanup_identity_set(identity_set)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE, f"cleanup identity set invalid: {exc.code}", label
            ) from exc
        try:
            parsed_phase = CleanupPhase(phase)
        except ValueError as exc:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE, f"cleanup phase invalid: {phase}", label
            ) from exc
        return CleanupJobRecord(
            user_scope=str(user_scope),
            cleanup_job_id=str(cleanup_job_id),
            owner_ref=str(owner_ref),
            preview_digest=str(preview_digest),
            expected_revision=int(expected_revision),
            exact_ids=identities.native_ids,
            phase=parsed_phase,
            fence_committed=bool(fence),
            session_ids=identities.session_ids,
            asset_keys=identities.asset_keys,
            prior_error_category=None if error is None else str(error),
        )

    # ------------------------------------------------------------------
    # F-03-1：本地草稿可靠保存（§4.2 save_draft / §9.3 / §9.13）
    # ------------------------------------------------------------------

    async def save_draft(self, command: DraftSaveCommand) -> DraftReceipt:
        """草稿 CAS 保存（§4.2 ``save_draft``）：会话行锁下**单个显式事务**。

        语义：

        - ``expected_draft_revision`` 必须等于会话当前 ``draft_revision``，否则
          ``REVISION_CONFLICT``（零新事实：不改草稿、不写命令）；
        - 同命令键（``user_scope`` + 目标 session + ``client_command_id``）同输入
          （预期版 + 规范载荷摘要）→ 查回原回执，不重复推进版本；异输入 →
          ``INPUT_CONFLICT``；
        - 目标会话不存在或不在调用用户域 → ``INPUT_INVALID``（不静默建会话）；
        - 会话行 ``revision`` 与 ``updated_at`` 同事务推进：目录排序/历史游标
          指纹不因草稿变更静默漏页（§4.2 修订游标失效、§9.3）；
        - 草稿载荷只含引用（凭据/有效批准由 codec 拒绝），清理语义归 E-06；
        - 载荷 ``source_refs`` 在同事务登记为本会话草稿谱系边（当前集合，F-03-4）；
          引用未受管源版本 → ``INPUT_INVALID`` 整体回滚。

        转移 fence（``draft_transfer_ref``，F-03-3）：成功移入会把源草稿清空并
        留下不可发送恢复记录；本方法保存**新草稿**时同事务清空该 fence——新版本
        不是被移入的旧版本，不得因旧引用被发送门误判为不可发送。单事务转移不
        存在「进行中」可见态，故无需额外拒绝分支；并发转移已由 draft_revision
        CAS 串行化。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT / REVISION_CONFLICT。
        """

        self._check_engine()
        try:
            validate_draft_save_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.session_id}/{command.command.command_id}",
            ) from exc
        user_scope = command.command.user_scope
        identity = f"{user_scope}/{command.session_id}/{command.command.command_id}"
        digest = draft_save_input_digest(command)
        payload_object = encode_draft_payload(command.payload)
        command_record_id = stable_id("command", [user_scope, command.session_id, command.command.command_id])
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. 会话行锁（草稿 CAS 与命令幂等的线性化点）。
            session_row = (
                await conn.execute(
                    sa.select(AgentSession.draft_revision)
                    .where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id == command.session_id,
                    )
                    .with_for_update()
                )
            ).first()
            if session_row is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "target session does not exist in user scope",
                    identity,
                )

            # 2. 命令键查重（同键同输入查回原回执；异输入冲突）。
            existing = (
                await conn.execute(
                    sa.select(
                        AgentCommand.input_digest,
                        AgentCommand.receipt_payload,
                    ).where(
                        AgentCommand.user_scope == user_scope,
                        AgentCommand.target_id == command.session_id,
                        AgentCommand.client_command_id == command.command.command_id,
                    )
                )
            ).first()
            if existing is not None:
                if existing.input_digest != digest:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "same draft command key with different input",
                        identity,
                    )
                if existing.receipt_payload is None:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        "original draft receipt unavailable",
                        identity,
                    )
                try:
                    return decode_draft_receipt(existing.receipt_payload)
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        f"stored draft receipt invalid: {exc.code}",
                        identity,
                    ) from exc

            # 3. CAS：预期版必须等于当前草稿版。
            if session_row.draft_revision != command.expected_draft_revision:
                raise AgentServiceError(
                    AgentErrorCode.REVISION_CONFLICT,
                    "draft revision does not match expected",
                    identity,
                )

            now = datetime.now(UTC)
            next_revision = int(session_row.draft_revision) + 1
            await conn.execute(
                sa.update(AgentSession)
                .where(
                    AgentSession.user_scope == user_scope,
                    AgentSession.session_id == command.session_id,
                )
                .values(
                    draft_payload=payload_object,
                    draft_revision=next_revision,
                    draft_transfer_ref=None,
                    revision=AgentSession.revision + 1,
                    updated_at=now,
                )
            )
            await self._sync_draft_source_links(
                conn,
                user_scope=user_scope,
                session_id=command.session_id,
                draft_revision=next_revision,
                source_refs=command.payload.source_refs,
                identity=identity,
            )
            receipt = DraftReceipt(
                session_id=command.session_id,
                command_record_id=command_record_id,
                commit_state=CommitState.COMMITTED,
                draft_revision=next_revision,
                verified_at=_wire_time(now),
                error=None,
            )
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=user_scope,
                    command_record_id=command_record_id,
                    client_command_id=command.command.command_id,
                    target_id=command.session_id,
                    command_kind="save_draft",
                    session_id=command.session_id,
                    expected_revision=command.expected_draft_revision,
                    input_digest=digest,
                    request_payload=payload_object,
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                    receipt_payload=encode_draft_receipt(receipt),
                    verified_at=now,
                )
            )
            return receipt

    async def _sync_draft_source_links(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        session_id: str,
        draft_revision: int,
        source_refs: tuple[SourceVersionRef, ...],
        identity: str,
    ) -> None:
        """草稿来源谱系边（F-03-4；§9.10）：随草稿保存替换为**当前集合**。

        - 只登记引用：目标源版本必须已在同用户域受管资产表中存在，否则
          ``INPUT_INVALID``（不因草稿携带就自动建资产/副本）；
        - 先删本会话既有 ``consumer_kind='draft'`` 边再写当前集合，避免自动保存
          无限累积历史边；``consumer_version`` 为本次草稿修订；
        - 禁用源仍可被草稿引用（禁用拦截在发送/派发门，不在编辑门）。

        调用方须在同一显式事务内调用（失败即整体回滚，不留下半套边）。
        """

        await conn.execute(
            sa.delete(AgentSourceLink).where(
                AgentSourceLink.user_scope == user_scope,
                AgentSourceLink.session_id == session_id,
                AgentSourceLink.consumer_kind == "draft",
            )
        )
        for ref in source_refs:
            asset = (
                await conn.execute(
                    sa.select(AgentAssetVersion.asset_id, AgentAssetVersion.digest).where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == ref.source_id,
                        AgentAssetVersion.asset_version == ref.source_version,
                    )
                )
            ).first()
            if asset is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "draft references a source version not registered in user scope",
                    identity,
                )
            if asset.digest != ref.source_digest:
                # 版本存在但摘要不符：草稿引用的不是该受管版本的事实，fail-closed。
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    "draft source digest does not match stored source version",
                    identity,
                )
            await conn.execute(
                sa.insert(AgentSourceLink).values(
                    user_scope=user_scope,
                    link_id=stable_id(
                        "source_link",
                        [
                            user_scope,
                            ref.source_id,
                            ref.source_version,
                            "draft",
                            session_id,
                            str(draft_revision),
                        ],
                    ),
                    source_id=ref.source_id,
                    source_version=ref.source_version,
                    consumer_kind="draft",
                    consumer_id=session_id,
                    consumer_version=str(draft_revision),
                    session_id=session_id,
                    relation_kind=SourceRelationKind.DRAFT_REFERENCE.value,
                )
            )

    # ------------------------------------------------------------------
    # F-03-2：会话元数据命令（§4.2 change_session / §9.3）
    # ------------------------------------------------------------------
    async def change_session(self, command: SessionChangeCommand) -> CommandReceipt:
        """会话元数据 CAS 命令（§4.2 ``change_session``）：**单个显式事务**。

        语义：

        - ``create`` / ``branch``：预期会话版 0；同 ``session_id`` 已存在 →
          ``INPUT_INVALID``（目标已存在不静默复用，亦不覆盖标题）；成功后
          ``current_revision=1``。``branch`` 先核原消息身份/版本（
          ``subject_revision``），再以 ``parent_session_id`` + 冻结键集
          ``branch_metadata`` + ``causal_head_ref`` 引用原因果前缀，**不复制
          消息/成果正文、不迁 workflow/批准**；
        - ``rename`` / ``pin`` / ``archive`` / ``unarchive``：会话行锁下 CAS
          （``expected_session_revision`` 必须等于行 ``revision``），成功
          ``revision+1``；归档不改执行（§9.3「归档不终止执行」）；
        - ``revise``：核原消息属于本会话且版本匹配，把新因果前缀（源消息之前
          最近一条，或显式截点）写入 ``causal_head_ref``；**不改原消息/成果、
          不建消息、不派发**，用户在修订后的草稿上另行发送；
        - 同命令键同输入 → 查回原回执；异输入 → ``INPUT_CONFLICT``；
        - 会话/原消息不存在或不在调用用户域 → ``INPUT_INVALID``；原消息版本
          不符 → ``REVISION_CONFLICT``（零新事实）；
        - 动作本身不改草稿、不派发、不产生费用（元数据 CAS 与运行分离）。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT / REVISION_CONFLICT /
                OPERATION_UNKNOWN。
        """

        self._check_engine()
        user_scope = command.command.user_scope
        identity = f"{user_scope}/{command.session_id}/{command.command.command_id}"
        try:
            validate_session_change_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        digest = session_change_input_digest(command)
        command_record_id = stable_id("command", [user_scope, command.session_id, command.command.command_id])
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 0. 更新类动作先行锁目标会话行：并发同键请求在此排队，待先提交者
            #    完成后于锁内查重 → 查回原回执（create/branch 无行可锁，见下）。
            locked_row = None
            if command.action not in (SessionAction.CREATE, SessionAction.BRANCH):
                locked_row = (
                    await conn.execute(
                        sa.select(AgentSession.revision)
                        .where(
                            AgentSession.user_scope == user_scope,
                            AgentSession.session_id == command.session_id,
                        )
                        .with_for_update()
                    )
                ).first()

            # 1. 命令键查重（更新类已在行锁内；create/branch 的重放走快路径，
            #    并发同键在插入冲突后复查）。
            existing = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest, AgentCommand.receipt_payload).where(
                        AgentCommand.user_scope == user_scope,
                        AgentCommand.target_id == command.session_id,
                        AgentCommand.client_command_id == command.command.command_id,
                    )
                )
            ).first()
            if existing is not None:
                if existing.input_digest != digest:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT, "same session command key with different input", identity
                    )
                if existing.receipt_payload is None:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN, "original session command receipt unavailable", identity
                    )
                try:
                    return decode_command_receipt(existing.receipt_payload)
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        f"stored session command receipt invalid: {exc.code}",
                        identity,
                    ) from exc

            now = datetime.now(UTC)
            if command.action in (SessionAction.CREATE, SessionAction.BRANCH):
                parent_session_id: str | None = None
                branch_metadata: dict[str, Any] | None = None
                causal_head_ref: str | None = None
                if command.action is SessionAction.BRANCH:
                    source = (
                        await conn.execute(
                            sa.select(
                                AgentMessage.message_id,
                                AgentMessage.session_id,
                                AgentMessage.session_seq,
                                AgentMessage.subject_revision,
                            ).where(
                                AgentMessage.user_scope == user_scope,
                                AgentMessage.message_id == command.source_message_id,
                            )
                        )
                    ).first()
                    if source is None:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID,
                            "source message does not exist in user scope",
                            identity,
                        )
                    if int(source.subject_revision) != command.source_message_revision:
                        raise AgentServiceError(
                            AgentErrorCode.REVISION_CONFLICT,
                            "source message revision does not match expected",
                            identity,
                        )
                    causal_head_ref = await self._resolve_causal_cutoff(
                        conn,
                        user_scope=user_scope,
                        source=source,
                        cutoff_message_id=command.causal_cutoff_message_id,
                        identity=identity,
                    )
                    parent_session_id = source.session_id
                    # 冻结键集：只引用身份/版本/截点，不含正文（清理后不留内容）。
                    branch_metadata = {
                        "schema_version": 1,
                        "source_session_id": source.session_id,
                        "source_message_id": source.message_id,
                        "source_message_version": int(source.subject_revision),
                        "causal_cutoff_message_id": causal_head_ref,
                    }
                # 不做「先查目标是否已存在」的预检查：那会在命令键查重与预检查
                # 之间留出并发窗口（同键命令刚提交 → 后到者被误判为重复目标）。
                # 直接插入，由唯一约束仲裁，冲突后先复查命令键（重放）再判占用。
                try:
                    async with conn.begin_nested():
                        await conn.execute(
                            sa.insert(AgentSession).values(
                                user_scope=user_scope,
                                session_id=command.session_id,
                                title=command.title,
                                source_module=command.command.source_module,
                                parent_session_id=parent_session_id,
                                branch_metadata=branch_metadata,
                                causal_head_ref=causal_head_ref,
                            )
                        )
                except IntegrityError as exc:
                    # 并发同键：先提交者已写入命令行；保存点回滚后在同一事务内
                    # 复查命令键 → 同输入返回原回执，异输入/目标被占用才冲突。
                    replay = (
                        await conn.execute(
                            sa.select(AgentCommand.input_digest, AgentCommand.receipt_payload).where(
                                AgentCommand.user_scope == user_scope,
                                AgentCommand.target_id == command.session_id,
                                AgentCommand.client_command_id == command.command.command_id,
                            )
                        )
                    ).first()
                    if replay is not None:
                        if replay.input_digest != digest:
                            raise AgentServiceError(
                                AgentErrorCode.INPUT_CONFLICT,
                                "same session command key with different input",
                                identity,
                            ) from exc
                        if replay.receipt_payload is None:
                            raise AgentServiceError(
                                AgentErrorCode.OPERATION_UNKNOWN,
                                "original session command receipt unavailable",
                                identity,
                            ) from exc
                        try:
                            return decode_command_receipt(replay.receipt_payload)
                        except AgentValidationError as decode_exc:
                            raise AgentServiceError(
                                AgentErrorCode.OPERATION_UNKNOWN,
                                f"stored session command receipt invalid: {decode_exc.code}",
                                identity,
                            ) from decode_exc
                    # 不依赖约束名：保存点回滚后复查目标是否已被另一命令创建；
                    # 已存在 → 与前置检查同语义的 INPUT_INVALID，不静默复用。
                    occupied = (
                        await conn.execute(
                            sa.select(AgentSession.session_id).where(
                                AgentSession.user_scope == user_scope,
                                AgentSession.session_id == command.session_id,
                            )
                        )
                    ).first()
                    if occupied is not None:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID, "session already exists in user scope", identity
                        ) from exc
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT, "session created concurrently by another command", identity
                    ) from exc
                current_revision = 1
            else:
                if locked_row is None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, "target session does not exist in user scope", identity
                    )
                if int(locked_row.revision) != command.expected_session_revision:
                    raise AgentServiceError(
                        AgentErrorCode.REVISION_CONFLICT, "session revision does not match expected", identity
                    )
                current_revision = int(locked_row.revision) + 1
                values: dict[str, Any] = {"revision": current_revision, "updated_at": now}
                if command.action is SessionAction.RENAME:
                    values["title"] = command.title
                    values["title_user_set"] = True
                elif command.action is SessionAction.PIN:
                    values["pinned"] = True
                elif command.action is SessionAction.ARCHIVE:
                    values["archived"] = True
                elif command.action is SessionAction.UNARCHIVE:
                    values["archived"] = False
                elif command.action is SessionAction.REVISE:
                    source = (
                        await conn.execute(
                            sa.select(
                                AgentMessage.message_id,
                                AgentMessage.session_id,
                                AgentMessage.session_seq,
                                AgentMessage.subject_revision,
                            ).where(
                                AgentMessage.user_scope == user_scope,
                                AgentMessage.message_id == command.source_message_id,
                                AgentMessage.session_id == command.session_id,
                            )
                        )
                    ).first()
                    if source is None:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID,
                            "source message does not exist in target session",
                            identity,
                        )
                    if int(source.subject_revision) != command.source_message_revision:
                        raise AgentServiceError(
                            AgentErrorCode.REVISION_CONFLICT,
                            "source message revision does not match expected",
                            identity,
                        )
                    values["causal_head_ref"] = await self._resolve_causal_cutoff(
                        conn,
                        user_scope=user_scope,
                        source=source,
                        cutoff_message_id=command.causal_cutoff_message_id,
                        identity=identity,
                    )
                await conn.execute(
                    sa.update(AgentSession)
                    .where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id == command.session_id,
                    )
                    .values(**values)
                )

            receipt = CommandReceipt(
                command=command.command,
                target_id=command.session_id,
                current_revision=current_revision,
                commit_state=CommitState.COMMITTED,
                delivery_state=DeliveryState.NOT_DELIVERED,
                consumption_state=ConsumptionState.NOT_CONSUMED,
                updated_at=_wire_time(now),
                error_code=None,
                next_action=None,
            )
            await conn.execute(
                sa.insert(AgentCommand).values(
                    user_scope=user_scope,
                    command_record_id=command_record_id,
                    client_command_id=command.command.command_id,
                    target_id=command.session_id,
                    command_kind="change_session",
                    session_id=command.session_id,
                    expected_revision=command.expected_session_revision,
                    input_digest=digest,
                    request_payload=encode_session_change_command(command),
                    commit_state=CommitState.COMMITTED.value,
                    delivery_state=DeliveryState.NOT_DELIVERED.value,
                    consumption_state=ConsumptionState.NOT_CONSUMED.value,
                    receipt_payload=encode_command_receipt(receipt),
                    verified_at=now,
                )
            )
            return receipt

    async def _resolve_causal_cutoff(
        self,
        conn: AsyncConnection,
        *,
        user_scope: str,
        source: Any,
        cutoff_message_id: str | None,
        identity: str,
    ) -> str | None:
        """解析修订/分支的新因果前缀末条（严格早于源消息）。

        - 未显式给截点：取源消息之前 ``session_seq`` 最大的一条；源为会话首条时
          返回 ``None``（空前缀）；
        - 显式截点必须属于同一会话且严格早于源消息，否则 ``INPUT_INVALID``；
        - 只返回消息身份，不读正文、不改任何消息。
        """

        if cutoff_message_id is None:
            previous = (
                await conn.execute(
                    sa.select(AgentMessage.message_id)
                    .where(
                        AgentMessage.user_scope == user_scope,
                        AgentMessage.session_id == source.session_id,
                        AgentMessage.session_seq < source.session_seq,
                    )
                    .order_by(AgentMessage.session_seq.desc())
                    .limit(1)
                )
            ).first()
            return None if previous is None else previous.message_id
        cutoff = (
            await conn.execute(
                sa.select(AgentMessage.message_id, AgentMessage.session_id, AgentMessage.session_seq).where(
                    AgentMessage.user_scope == user_scope,
                    AgentMessage.message_id == cutoff_message_id,
                )
            )
        ).first()
        if cutoff is None or cutoff.session_id != source.session_id:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "causal cutoff message does not belong to source session",
                identity,
            )
        if int(cutoff.session_seq) >= int(source.session_seq):
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "causal cutoff must precede source message",
                identity,
            )
        return cutoff.message_id

    # ------------------------------------------------------------------
    # F-03-3：草稿移入（§4.2 DraftTransfer 协议 / §9.8 transfer_* 列）
    # ------------------------------------------------------------------
    async def transfer_draft(self, command: DraftTransferCommand) -> DraftTransferReceipt:
        """草稿移入（§4.2 ``transfer_draft``）：**单个显式事务**直达 ``completed``。

        语义（§4.2 L413-419 R03、§9.8 transfer_* 列）：

        - 双方会话行按 ``session_id`` 排序加锁（A→B 与 B→A 并发不死锁），锁内
          先查命令键重放：同键同输入查回原回执（不二次移动），异输入
          ``INPUT_CONFLICT``；
        - 目标 CAS：``expected_target_draft_revision`` 必须等于目标当前草稿版，
          否则 ``REVISION_CONFLICT`` 且零新事实；目标须为无稿或空稿
          （:func:`is_empty_draft_payload`，正文空但有未发送选择不是空位），
          否则 ``INPUT_INVALID``；归档目标先取消归档（``INPUT_INVALID``）；
        - 源 CAS（``source_kind=session_draft``）：源版必须匹配；调用方 payload
          必须与源会话存储的草稿逐字节规范相等（``INPUT_CONFLICT``），空源拒绝；
        - 源 claim：本命令行同事务写 ``transfer_source_*`` + ``claimed=true``，
          源键部分唯一索引拒第二个转移（保护后冲突/unknown 也保留 claim，不因
          只读 phase 判断）。``source_kind=business_preparation`` 时本库无源行，
          claim 即唯一仲裁，源清理由生产者按同一源键自行收尾；
        - 目标安装：草稿载荷 + 版 +1 + 草稿来源边（当前集合）；目标获得可发送
          归属（清其旧 ``draft_transfer_ref``，新版本不是被移入的旧版本）；
        - 源清空：会话稿置 NULL、版 +1、``draft_transfer_ref`` 指向本命令（不可
          发送恢复记录；完整 payload 留在命令 ``request_payload``），并清空其
          草稿来源边；
        - ``commit_state=committed`` 只表示保存事实；移入不发送、不派发研究、
          不产生费用（零自动研究）。

        Raises:
            AgentServiceError: INPUT_INVALID / INPUT_CONFLICT / REVISION_CONFLICT /
                OPERATION_UNKNOWN。
        """

        self._check_engine()
        user_scope = command.command.user_scope
        identity = f"{user_scope}/{command.target_session_id}/{command.command.command_id}"
        try:
            validate_draft_transfer_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        digest = draft_transfer_input_digest(command)
        command_record_id = stable_id("command", [user_scope, command.target_session_id, command.command.command_id])
        payload_object = encode_draft_payload(command.payload)
        is_session_draft = command.source_kind is DraftTransferSourceKind.SESSION_DRAFT
        lock_ids = sorted(
            {command.target_session_id, command.source_id} if is_session_draft else {command.target_session_id}
        )
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            # 1. 双方会话行锁（固定顺序，避免反向并发死锁）。
            rows = (
                await conn.execute(
                    sa.select(
                        AgentSession.session_id,
                        AgentSession.draft_revision,
                        AgentSession.draft_payload,
                        AgentSession.archived,
                    )
                    .where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id.in_(lock_ids),
                    )
                    .order_by(AgentSession.session_id)
                    .with_for_update()
                )
            ).all()
            sessions = {row.session_id: row for row in rows}

            # 2. 命令键查重（锁内：并发同键只有先提交者可见，后到者重放）。
            existing = (
                await conn.execute(
                    sa.select(AgentCommand.input_digest, AgentCommand.receipt_payload).where(
                        AgentCommand.user_scope == user_scope,
                        AgentCommand.target_id == command.target_session_id,
                        AgentCommand.client_command_id == command.command.command_id,
                    )
                )
            ).first()
            if existing is not None:
                if existing.input_digest != digest:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "same draft transfer command key with different input",
                        identity,
                    )
                if existing.receipt_payload is None:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        "original draft transfer receipt unavailable",
                        identity,
                    )
                try:
                    return decode_draft_transfer_receipt(existing.receipt_payload)
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.OPERATION_UNKNOWN,
                        f"stored draft transfer receipt invalid: {exc.code}",
                        identity,
                    ) from exc

            # 3. 目标必须存在、未归档，且为无稿/空稿。
            target = sessions.get(command.target_session_id)
            if target is None:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "target session does not exist in user scope", identity
                )
            if target.archived:
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID, "target session is archived; unarchive before transfer", identity
                )
            if int(target.draft_revision) != command.expected_target_draft_revision:
                raise AgentServiceError(
                    AgentErrorCode.REVISION_CONFLICT, "target draft revision does not match expected", identity
                )
            try:
                target_payload = None if target.draft_payload is None else decode_draft_payload(target.draft_payload)
            except AgentValidationError as exc:
                # 无法证明空稿即按有稿处理（§4.2 L413），不把坏事实读成空位。
                raise AgentServiceError(
                    AgentErrorCode.INPUT_INVALID,
                    f"target draft is not provably empty: {exc.code}",
                    identity,
                ) from exc
            if not is_empty_draft_payload(target_payload):
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "target draft is not empty", identity)

            # 4. 源核验（会话稿）：版本 + 完整载荷一致 + 非空。
            source: Any = None
            if is_session_draft:
                source = sessions.get(command.source_id)
                if source is None:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID, "source session does not exist in user scope", identity
                    )
                if int(source.draft_revision) != command.expected_source_draft_revision:
                    raise AgentServiceError(
                        AgentErrorCode.REVISION_CONFLICT, "source draft revision does not match expected", identity
                    )
                try:
                    source_payload = (
                        None if source.draft_payload is None else decode_draft_payload(source.draft_payload)
                    )
                except AgentValidationError as exc:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        f"source draft is unreadable: {exc.code}",
                        identity,
                    ) from exc
                if source_payload is None or is_empty_draft_payload(source_payload):
                    raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "source draft is empty", identity)
                if encode_draft_payload(source_payload) != payload_object:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_CONFLICT,
                        "transfer payload does not match source draft",
                        identity,
                    )

            now = datetime.now(UTC)
            target_next = int(target.draft_revision) + 1
            source_next: int | None = None
            if source is not None:
                source_next = int(source.draft_revision) + 1
            receipt = DraftTransferReceipt(
                command_record_id=command_record_id,
                transfer_id=command.transfer_id,
                source_kind=command.source_kind,
                source_id=command.source_id,
                source_session_id=command.source_session_id,
                target_session_id=command.target_session_id,
                expected_source_draft_revision=command.expected_source_draft_revision,
                expected_target_draft_revision=command.expected_target_draft_revision,
                payload_digest=command.payload_digest,
                commit_state=CommitState.COMMITTED,
                phase=DraftTransferPhase.COMPLETED,
                sendable_owner=SendableOwner.TARGET,
                # 会话稿：清空后的可靠当前版；业务准备稿：本库无源行，不伪造可靠值。
                source_revision=source_next,
                target_revision=target_next,
                verified_at=_wire_time(now),
                next_action=None,
            )

            # 5. 源 claim 与原阶段同事务保存（唯一约束拒第二个转移）。
            try:
                await conn.execute(
                    sa.insert(AgentCommand).values(
                        user_scope=user_scope,
                        command_record_id=command_record_id,
                        client_command_id=command.command.command_id,
                        target_id=command.target_session_id,
                        command_kind="draft_transfer",
                        session_id=command.target_session_id,
                        expected_revision=command.expected_target_draft_revision,
                        input_revision=command.expected_source_draft_revision,
                        input_digest=digest,
                        request_payload=encode_draft_transfer_command(command),
                        commit_state=CommitState.COMMITTED.value,
                        delivery_state=DeliveryState.NOT_DELIVERED.value,
                        consumption_state=ConsumptionState.NOT_CONSUMED.value,
                        receipt_payload=encode_draft_transfer_receipt(receipt),
                        verified_at=now,
                        transfer_id=command.transfer_id,
                        transfer_phase=DraftTransferPhase.COMPLETED.value,
                        sendable_owner=SendableOwner.TARGET.value,
                        transfer_source_kind=command.source_kind.value,
                        transfer_source_id=command.source_id,
                        transfer_source_revision=command.expected_source_draft_revision,
                        transfer_source_claimed=True,
                    )
                )
            except IntegrityError as exc:
                # 同一源键/版已被另一命令 claim（含跨目标争抢、业务准备稿生产者）。
                raise AgentServiceError(
                    AgentErrorCode.INPUT_CONFLICT,
                    "source draft revision already claimed by another transfer",
                    identity,
                ) from exc

            # 6. 目标安装（可发送归属；清旧 fence）。
            await conn.execute(
                sa.update(AgentSession)
                .where(
                    AgentSession.user_scope == user_scope,
                    AgentSession.session_id == command.target_session_id,
                )
                .values(
                    draft_payload=payload_object,
                    draft_revision=target_next,
                    draft_transfer_ref=None,
                    revision=AgentSession.revision + 1,
                    updated_at=now,
                )
            )
            await self._sync_draft_source_links(
                conn,
                user_scope=user_scope,
                session_id=command.target_session_id,
                draft_revision=target_next,
                source_refs=command.payload.source_refs,
                identity=identity,
            )

            # 7. 源只读收尾（不可发送恢复记录）。
            if source is not None:
                assert source_next is not None
                await conn.execute(
                    sa.update(AgentSession)
                    .where(
                        AgentSession.user_scope == user_scope,
                        AgentSession.session_id == command.source_id,
                    )
                    .values(
                        draft_payload=None,
                        draft_revision=source_next,
                        draft_transfer_ref=command_record_id,
                        revision=AgentSession.revision + 1,
                        updated_at=now,
                    )
                )
                await self._sync_draft_source_links(
                    conn,
                    user_scope=user_scope,
                    session_id=command.source_id,
                    draft_revision=source_next,
                    source_refs=(),
                    identity=identity,
                )
            return receipt

    async def read_draft_transfer(self, ref: CommandRef, *, user_scope: str) -> DraftTransferReceipt:
        """只查原转移回执（§4.2 ``read_draft_transfer``）：零移动、零发送。

        - ``ref.user_scope`` 必须等于可信调用上下文（§4.1）；
        - 用户域内按命令键查：0 行/多行歧义/非 ``draft_transfer`` 命令/预期版
          不符 → ``INPUT_INVALID``（不把别的命令回执当转移）；
        - 存行回执缺失或不在冻结枚举内 → ``OPERATION_UNKNOWN``（fail-closed，
          unknown 不从界面猜归属，R21/INV-07）；
        - 转移命令行不关联 execution，故不比对意图键（与叶 execution 边界一致）。
        """

        self._check_engine()
        identity = f"{user_scope}/{ref.command_id}"
        if not isinstance(user_scope, str) or user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity)
        if ref.user_scope != user_scope:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command ref outside caller user scope", identity)
        try:
            encode_command_ref(ref)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc

        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        AgentCommand.command_kind,
                        AgentCommand.expected_revision,
                        AgentCommand.receipt_payload,
                    ).where(
                        AgentCommand.user_scope == user_scope,
                        AgentCommand.client_command_id == ref.command_id,
                    )
                )
            ).all()
        if not rows:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command not found in user scope", identity)
        if len(rows) > 1:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command ref is ambiguous across targets", identity)
        row = rows[0]
        if row.command_kind != "draft_transfer":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "command is not a draft transfer", identity)
        if ref.expected_revision != row.expected_revision:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "command ref does not match stored transfer: expected_revision",
                identity,
            )
        if row.receipt_payload is None:
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "original transfer receipt unavailable", identity)
        try:
            return decode_draft_transfer_receipt(row.receipt_payload)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"stored draft transfer receipt invalid: {exc.code}",
                identity,
            ) from exc

    # ------------------------------------------------------------------
    # E-03-4 Skill 资产与角色版本引用（§9.10 / F12-F13）
    # ------------------------------------------------------------------

    async def list_skill_versions(self, *, user_scope: str, skill_id: str) -> tuple[tuple[int, str], ...]:
        """同一 Skill 身份的全部已登记版本（版本升序）与内容摘要。"""

        self._check_engine()
        identity = f"{user_scope}/skill/{skill_id}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(skill_id, "skill_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(AgentAssetVersion.asset_version, AgentAssetVersion.digest).where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == skill_id,
                        AgentAssetVersion.asset_kind == "skill",
                    )
                )
            ).all()
        versions: list[tuple[int, str]] = []
        for row in rows:
            try:
                version = int(row.asset_version)
            except (TypeError, ValueError) as exc:
                raise AgentServiceError(
                    AgentErrorCode.OPERATION_UNKNOWN, "skill asset version is not numeric", identity
                ) from exc
            versions.append((version, row.digest))
        return tuple(sorted(versions))

    async def has_skill(self, *, user_scope: str, skill_id: str) -> bool:
        """该 Skill 身份是否已有任一登记版本（依赖存在性核验用）。"""

        self._check_engine()
        identity = f"{user_scope}/skill/{skill_id}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(skill_id, "skill_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(AgentAssetVersion.asset_id)
                    .where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == skill_id,
                        AgentAssetVersion.asset_kind == "skill",
                        AgentAssetVersion.read_state == "ready",
                    )
                    .limit(1)
                )
            ).first()
        return row is not None

    # ------------------------------------------------------------------
    # 计划提案（DD §8 F21 / §10.3；HANDOFF §7.3，E-04）
    # ------------------------------------------------------------------
    #
    # 提案以不可变 asset 版本保存（asset_kind='plan'），绑定 execution 与
    # plan_revision；只存提案：不更新 agent_round、不改已派发图、不是第二
    # 任务状态机。revision 每 execution 单调，0 表示尚无提案。

    async def read_plan_proposal(
        self, *, user_scope: str, execution_id: str, plan_revision: int
    ) -> PlanProposal | None:
        """读取精确版计划提案；无行或跨用户 → ``None``，坏行 → ``OPERATION_UNKNOWN``。"""

        self._check_engine()
        identity = f"{user_scope}/{execution_id}@{plan_revision}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(execution_id, "execution_id", identity)
        if isinstance(plan_revision, bool) or not isinstance(plan_revision, int) or plan_revision < 1:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "plan_revision must be an int >= 1", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.body_text,
                        AgentAssetVersion.digest,
                        AgentAssetVersion.asset_version,
                        AgentAssetVersion.read_state,
                    ).where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == plan_asset_id(execution_id),
                        AgentAssetVersion.asset_version == str(plan_revision),
                        AgentAssetVersion.asset_kind == "plan",
                    )
                )
            ).first()
        if row is None:
            return None
        return _decode_plan_proposal_row(row, execution_id=execution_id, identity=identity)

    async def read_current_plan_proposal(self, *, user_scope: str, execution_id: str) -> PlanProposal | None:
        """读取当前（最大 revision）计划提案；无提案 → ``None``，坏行 → ``OPERATION_UNKNOWN``。"""

        self._check_engine()
        identity = f"{user_scope}/{execution_id}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(execution_id, "execution_id", identity)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.body_text,
                        AgentAssetVersion.digest,
                        AgentAssetVersion.asset_version,
                        AgentAssetVersion.read_state,
                    ).where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == plan_asset_id(execution_id),
                        AgentAssetVersion.asset_kind == "plan",
                    )
                )
            ).all()
        head, _ = _plan_head_row(rows, identity)
        if head is None:
            return None
        return _decode_plan_proposal_row(head, execution_id=execution_id, identity=identity)

    async def save_plan_proposal(
        self,
        *,
        user_scope: str,
        execution_id: str,
        expected_revision: int,
        items: Sequence[PlanItemSnapshot],
    ) -> PlanProposal:
        """以预期版 CAS 追加不可变计划提案版本（单事务，冲突零新事实）。

        同 content 重写不增版（返回当前版）；``expected_revision`` 落后于 head
        → ``REVISION_CONFLICT``；执行行不存在/跨用户 → ``INPUT_INVALID``。
        """

        self._check_engine()
        identity = f"{user_scope}/{execution_id}@{expected_revision}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(execution_id, "execution_id", identity)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "expected_revision must be an int >= 0", identity)
        item_tuple = _validate_plan_items_for_save(items, execution_id=execution_id, identity=identity)
        asset_id = plan_asset_id(execution_id)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            execution_row = (
                await conn.execute(
                    sa.select(AgentExecution.execution_id)
                    .where(
                        AgentExecution.user_scope == user_scope,
                        AgentExecution.execution_id == execution_id,
                    )
                    .with_for_update()
                )
            ).first()
            if execution_row is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "execution not found", identity)
            head_rows = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.body_text,
                        AgentAssetVersion.digest,
                        AgentAssetVersion.asset_version,
                        AgentAssetVersion.read_state,
                    ).where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_id == asset_id,
                        AgentAssetVersion.asset_kind == "plan",
                    )
                )
            ).all()
            head_row, head_revision = _plan_head_row(head_rows, identity)
            if head_revision != expected_revision:
                raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "plan revision conflict", identity)
            if head_row is not None:
                head = _decode_plan_proposal_row(head_row, execution_id=execution_id, identity=identity)
                if head.items == item_tuple:
                    return head
            new_revision = expected_revision + 1
            proposal = PlanProposal(execution_id=execution_id, plan_revision=new_revision, items=item_tuple)
            await conn.execute(
                sa.insert(AgentAssetVersion).values(
                    user_scope=user_scope,
                    asset_id=asset_id,
                    asset_version=str(new_revision),
                    asset_kind="plan",
                    display_name=f"plan r{new_revision}",
                    digest=plan_proposal_digest(proposal),
                    metadata_payload={
                        "plan_store_version": PLAN_STORE_VERSION,
                        "plan_revision": new_revision,
                        "item_count": len(item_tuple),
                    },
                    body_text=normalize_plan_proposal(proposal),
                    read_state="ready",
                    verification_state="unverified",
                )
            )
        return proposal

    async def register_skill_asset(self, request: SkillAssetRegistration) -> SkillAssetReceipt:
        """受管 Skill 版本落库（同身份同内容幂等；异内容冲突；不复制脚本/凭据）。"""

        self._check_engine()
        identity = f"{request.user_scope}/skill/{request.skill_id}@{request.version}"
        _validate_skill_asset_request(request, identity)
        metadata = _skill_asset_metadata(request)
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            row = await _read_asset_row(
                conn,
                user_scope=request.user_scope,
                asset_id=request.skill_id,
                asset_version=str(request.version),
            )
            if row is None:
                try:
                    async with conn.begin_nested():
                        await conn.execute(
                            sa.insert(AgentAssetVersion).values(
                                user_scope=request.user_scope,
                                asset_id=request.skill_id,
                                asset_version=str(request.version),
                                asset_kind="skill",
                                display_name=request.display_name,
                                digest=request.content_digest,
                                origin_ref=None,
                                metadata_payload=metadata,
                                read_state="ready",
                                verification_state="program_verified",
                            )
                        )
                except IntegrityError:
                    row = await _read_asset_row(
                        conn,
                        user_scope=request.user_scope,
                        asset_id=request.skill_id,
                        asset_version=str(request.version),
                    )
                    if row is None:
                        raise AgentServiceError(
                            AgentErrorCode.PERSISTENCE_UNAVAILABLE, "skill asset insert conflicted", identity
                        ) from None
                else:
                    return SkillAssetReceipt(
                        user_scope=request.user_scope,
                        skill_id=request.skill_id,
                        version=request.version,
                        content_digest=request.content_digest,
                        replayed=False,
                    )
            return _compare_skill_asset_row(row, request, metadata, identity)

    async def save_role_skill_assignment(
        self,
        assignment: RoleSkillAssignment,
        *,
        user_scope: str,
        display_name: str,
    ) -> RoleSkillAssignmentReceipt:
        """角色版本对准确 Skill 版本的引用（单事务；引用阻删；默认⊆启用）。"""

        self._check_engine()
        identity = f"{user_scope}/{assignment.role_id}@{assignment.role_version}"
        _require_text_field(user_scope, "user_scope", identity)
        _require_text_field(display_name, "display_name", identity)
        try:
            validate_role_skill_assignment(assignment)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        wire = assignment.wire()
        digest = role_skill_assignment_digest(assignment)
        metadata = {"schema_version": SKILL_ASSET_METADATA_VERSION, "role_skill_assignment": wire}
        refs = tuple(
            SkillVersionRef(skill_id=skill_id, version=version)
            for skill_id, version in sorted(
                {(ref.skill_id, ref.version) for ref in (*assignment.enabled, *assignment.defaults)}
            )
        )
        engine = self.engine
        assert isinstance(engine, AsyncEngine)
        async with engine.begin() as conn:
            row = await _read_asset_row(
                conn,
                user_scope=user_scope,
                asset_id=assignment.role_id,
                asset_version=assignment.role_version,
            )
            if row is not None:
                return _compare_role_assignment_row(row, assignment, digest, metadata, user_scope, refs, identity)
            if refs:
                skill_rows = (
                    await conn.execute(
                        sa.select(
                            AgentAssetVersion.asset_id,
                            AgentAssetVersion.asset_version,
                            AgentAssetVersion.read_state,
                            AgentAssetVersion.metadata_payload,
                        )
                        .where(
                            AgentAssetVersion.user_scope == user_scope,
                            AgentAssetVersion.asset_kind == "skill",
                            sa.tuple_(AgentAssetVersion.asset_id, AgentAssetVersion.asset_version).in_(
                                [(ref.skill_id, str(ref.version)) for ref in refs]
                            ),
                        )
                        .with_for_update()
                    )
                ).all()
                installed = {(item.asset_id, item.asset_version) for item in skill_rows}
                for ref in refs:
                    if (ref.skill_id, str(ref.version)) not in installed:
                        raise AgentServiceError(
                            AgentErrorCode.INPUT_INVALID,
                            f"skill version not installed: {ref.skill_id}@{ref.version}",
                            identity,
                        )
                for item in skill_rows:
                    if item.read_state in {"disabled", "unavailable"}:
                        raise AgentServiceError(
                            AgentErrorCode.POLICY_DENIED,
                            f"skill version not enabled: {item.asset_id}@{item.asset_version}",
                            identity,
                        )
                    if (item.metadata_payload or {}).get("missing_dependencies"):
                        raise AgentServiceError(
                            AgentErrorCode.POLICY_DENIED,
                            f"skill dependency missing: {item.asset_id}@{item.asset_version}",
                            identity,
                        )
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        sa.insert(AgentAssetVersion).values(
                            user_scope=user_scope,
                            asset_id=assignment.role_id,
                            asset_version=assignment.role_version,
                            asset_kind="role",
                            display_name=display_name,
                            digest=digest,
                            origin_ref=None,
                            metadata_payload=metadata,
                            read_state="ready",
                            verification_state="program_verified",
                        )
                    )
            except IntegrityError:
                row = await _read_asset_row(
                    conn,
                    user_scope=user_scope,
                    asset_id=assignment.role_id,
                    asset_version=assignment.role_version,
                )
                if row is None:
                    raise AgentServiceError(
                        AgentErrorCode.PERSISTENCE_UNAVAILABLE, "role asset insert conflicted", identity
                    ) from None
                return _compare_role_assignment_row(row, assignment, digest, metadata, user_scope, refs, identity)
            for ref in refs:
                await conn.execute(
                    sa.insert(AgentSourceLink).values(
                        user_scope=user_scope,
                        link_id=stable_id(
                            "source_link",
                            [
                                user_scope,
                                ref.skill_id,
                                str(ref.version),
                                "asset",
                                assignment.role_id,
                                assignment.role_version,
                            ],
                        ),
                        source_id=ref.skill_id,
                        source_version=str(ref.version),
                        consumer_kind="asset",
                        consumer_id=assignment.role_id,
                        consumer_version=assignment.role_version,
                        derived_asset_id=assignment.role_id,
                        derived_asset_version=assignment.role_version,
                        relation_kind=SourceRelationKind.ROLE_SKILL_BINDING.value,
                    )
                )
        return RoleSkillAssignmentReceipt(
            user_scope=user_scope,
            role_id=assignment.role_id,
            role_version=assignment.role_version,
            digest=digest,
            linked_skill_versions=refs,
            replayed=False,
        )


def _validate_skill_asset_request(request: SkillAssetRegistration, identity: str) -> None:
    for field_name in (
        "user_scope",
        "skill_id",
        "display_name",
        "normalized_name",
        "license_id",
    ):
        _require_text_field(getattr(request, field_name), field_name, identity)
    if not is_digest(request.content_digest):
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "content_digest must be sha256 hex", identity)
    if request.archive_digest is not None and not is_digest(request.archive_digest):
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "archive_digest must be sha256 hex or None", identity)
    if request.source_kind not in {"zip", "directory"}:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "source_kind must be zip or directory", identity)
    if isinstance(request.version, bool) or not isinstance(request.version, int) or request.version < 1:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "version must be a positive integer", identity)
    for field_name in ("file_count", "expanded_bytes"):
        value = getattr(request, field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{field_name} must be non-negative", identity)
    for field_name in ("dependencies", "missing_dependencies", "ignored_behavioral_fields"):
        values = getattr(request, field_name)
        if len(set(values)) != len(values):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{field_name} contains duplicates", identity)


def _skill_asset_metadata(request: SkillAssetRegistration) -> dict[str, Any]:
    return {
        "schema_version": SKILL_ASSET_METADATA_VERSION,
        "skill_version_ref": {"skill_id": request.skill_id, "version": request.version},
        "normalized_name": request.normalized_name,
        "license": request.license_id,
        "archive_digest": request.archive_digest,
        "source_kind": request.source_kind,
        "file_count": request.file_count,
        "expanded_bytes": request.expanded_bytes,
        "dependencies": list(request.dependencies),
        "missing_dependencies": list(request.missing_dependencies),
        "ignored_behavioral_fields": list(request.ignored_behavioral_fields),
    }


async def _read_asset_row(
    conn: AsyncConnection,
    *,
    user_scope: str,
    asset_id: str,
    asset_version: str,
) -> Any:
    return (
        await conn.execute(
            sa.select(
                AgentAssetVersion.asset_kind,
                AgentAssetVersion.digest,
                AgentAssetVersion.metadata_payload,
            )
            .where(
                AgentAssetVersion.user_scope == user_scope,
                AgentAssetVersion.asset_id == asset_id,
                AgentAssetVersion.asset_version == asset_version,
            )
            .with_for_update()
        )
    ).first()


def _compare_skill_asset_row(
    row: Any,
    request: SkillAssetRegistration,
    metadata: dict[str, Any],
    identity: str,
) -> SkillAssetReceipt:
    if row.asset_kind != "skill":
        raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "asset identity belongs to another kind", identity)
    if row.digest != request.content_digest or json_doc(dict(row.metadata_payload or {})) != json_doc(metadata):
        raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "skill version differs from stored facts", identity)
    return SkillAssetReceipt(
        user_scope=request.user_scope,
        skill_id=request.skill_id,
        version=request.version,
        content_digest=request.content_digest,
        replayed=True,
    )


def _compare_role_assignment_row(
    row: Any,
    assignment: RoleSkillAssignment,
    digest: str,
    metadata: dict[str, Any],
    user_scope: str,
    refs: tuple[SkillVersionRef, ...],
    identity: str,
) -> RoleSkillAssignmentReceipt:
    if row.asset_kind != "role":
        raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "asset identity belongs to another kind", identity)
    if row.digest != digest or json_doc(dict(row.metadata_payload or {})) != json_doc(metadata):
        raise AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "role version differs from stored facts", identity)
    return RoleSkillAssignmentReceipt(
        user_scope=user_scope,
        role_id=assignment.role_id,
        role_version=assignment.role_version,
        digest=digest,
        linked_skill_versions=refs,
        replayed=True,
    )  # ------------------------------------------------------------------
