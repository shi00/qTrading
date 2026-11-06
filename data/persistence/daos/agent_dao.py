"""Agent 应用事务 DAO（设计正本 §8.2/§8.3）。

本模块只负责应用 public 表的事务与查询边界：原生 Harness 运行、原生
store/文件、模型/MCP 出站都在提交之后，不进入 ``_guarded_begin``。
D01 建立共享基础设施（锁序、upsert、公开事件追加）；受理/控制/历史/
效果回执由 D02—D04 在同一 DAO 上补齐，不另建第二入口。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import uuid
from datetime import date
from typing import Any, cast
from uuid import UUID

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.agent_types import (
    AgentRequest,
    AgentValidationError,
    CommandReceipt,
    ContextBinding,
    ControlCommand,
    JsonDoc,
    ModelChoice,
    Mutation,
    MutationReceipt,
    Page,
    Problem,
    QueueItemView,
    ResourceRef,
    RoleBinding,
    SubmissionKey,
    SubmissionReceipt,
    decode_json_doc,
    json_doc,
    request_digest,
    validate_payload,
    validate_request,
)
from data.persistence.models import (
    AgentAdmissionHead,
    AgentCommand,
    AgentConversation,
    AgentDecision,
    AgentEvent,
    AgentExecution,
    AgentInput,
    AgentIntent,
    AgentPath,
    AgentResource,
    AgentResourceVersion,
    AgentRetiredKey,
    AgentStreamHead,
    AgentTask,
    AgentTotalSlot,
    AgentUse,
)

from .base_dao import BaseDao

logger = logging.getLogger(__name__)

# §8.3 固定锁序：同层按 UUID 排序。新增写方法必须沿此顺序取锁，
# 禁止在执行中反向回锁（例如先锁资源再锁 intent）。
LOCK_ORDER: tuple[str, ...] = (
    "submission-identity",
    "admission-head",
    "total-slot",
    "conversation",
    "intent",
    "execution",
    "task",
    "attempt",
    "resource",
    "plan-review",
    "decision",
    "delivery",
    "cleanup",
    "presentation",
    "stream-head",
)

# 公开事件只保存读模型/成果指针：artifact.published 的 payload 仅为
# ArtifactPublication，不得携带 content/markdown 正文副本（§6.7/§10）。
_ARTIFACT_PUBLICATION_KEYS = frozenset({"artifact_id", "revision", "content_digest"})
PUBLIC_STREAM_KEY = "public"


# 设计基线版本（未锁版）；真实原生行为取证由 N01/N04 完成，此处只记录合同。
_FRAMEWORK_VERSION = "pydantic-ai-harness=0.65.0;ai-slim=2.65.0;agent-runtime-contract=1"
_QUEUE_CURSOR_SCHEMA_VERSION = 1


def _digest(doc: str) -> str:
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


def _problem(
    code: str,
    *,
    field: str | None = None,
    expected: object = None,
    actual: object = None,
    related_id: UUID | None = None,
) -> Problem:
    """构造不可变 Problem；params 只装脱敏后的字段/版本信息（§5.4）。"""
    return Problem(
        code=code,
        message_key=f"agent.{code}",
        params=json_doc({"field_path": field, "expected": expected, "actual": actual}),
        related_id=related_id,
    )


def _command_digest(command: ControlCommand) -> str:
    """覆盖完整 ControlCommand（目标/动作/预期版/载荷），不只覆盖 payload。"""
    return _digest(
        json_doc(
            {
                "kind": command.action,
                "target": {
                    "execution_id": command.target.execution_id,
                    "task_id": command.target.task_id,
                    "attempt_id": command.target.attempt_id,
                },
                "expected_input_revision": command.expected_input_revision,
                "expected_plan_revision": command.expected_plan_revision,
                "payload": decode_json_doc(command.payload),
            }
        )
    )


def _mutation_digest(mutation: Mutation) -> str:
    return _digest(
        json_doc(
            {
                "kind": mutation.kind,
                "resource_id": mutation.resource_id,
                "expected_revision": mutation.expected_revision,
                "payload": decode_json_doc(mutation.payload),
            }
        )
    )


def _text_excerpt(request: object) -> str:
    if not isinstance(request, dict):
        return ""
    text = request.get("text")
    return text[:80] if isinstance(text, str) else ""


def _encode_queue_cursor(domain: str, after_admission_seq: int) -> str:
    doc = json_doc(
        {
            "schema_version": _QUEUE_CURSOR_SCHEMA_VERSION,
            "domain": domain,
            "after_admission_seq": after_admission_seq,
        }
    )
    return base64.urlsafe_b64encode(doc.encode("utf-8")).decode("ascii")


def _decode_queue_cursor(domain: str, cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        doc = decode_json_doc(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError, AgentValidationError) as error:
        raise AgentValidationError("invalid_request", "cursor") from error
    if not isinstance(doc, dict):
        raise AgentValidationError("invalid_request", "cursor")
    if doc.get("schema_version") != _QUEUE_CURSOR_SCHEMA_VERSION or doc.get("domain") != domain:
        raise AgentValidationError("invalid_request", "cursor")
    value = doc.get("after_admission_seq")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentValidationError("invalid_request", "cursor")
    return value


def _validate_answer(answer: object, schema: object) -> None:
    """按 §5.10 只接受 text/choice/permission 三种封闭答案。"""
    if isinstance(schema, str):
        schema = decode_json_doc(schema)
    if not isinstance(answer, dict):
        raise AgentValidationError("invalid_request", "answer")
    if not isinstance(schema, dict):
        raise AgentValidationError("unsupported_schema", "answer_schema")
    kind = schema.get("kind")
    if kind == "text":
        if set(answer) != {"kind", "text"} or answer.get("kind") != "text":
            raise AgentValidationError("invalid_request", "answer")
        text = answer.get("text")
        if not isinstance(text, str):
            raise AgentValidationError("invalid_type", "answer.text")
        minimum = schema.get("min_length")
        maximum = schema.get("max_length")
        if isinstance(minimum, int) and len(text) < minimum:
            raise AgentValidationError("invalid_length", "answer.text")
        if isinstance(maximum, int) and len(text) > maximum:
            raise AgentValidationError("invalid_length", "answer.text")
        return
    if kind == "choice":
        if set(answer) != {"kind", "option_ids"} or answer.get("kind") != "choice":
            raise AgentValidationError("invalid_request", "answer")
        selected = answer.get("option_ids")
        allowed = schema.get("option_ids")
        if not isinstance(selected, list) or not isinstance(allowed, list):
            raise AgentValidationError("invalid_type", "answer.option_ids")
        if not all(isinstance(item, str) for item in selected):
            raise AgentValidationError("invalid_type", "answer.option_ids")
        if len(set(cast(list[str], selected))) != len(selected):
            raise AgentValidationError("duplicate_id", "answer.option_ids")
        minimum_items = schema.get("min_items")
        maximum_items = schema.get("max_items")
        if isinstance(minimum_items, int) and len(selected) < minimum_items:
            raise AgentValidationError("invalid_length", "answer.option_ids")
        if isinstance(maximum_items, int) and len(selected) > maximum_items:
            raise AgentValidationError("invalid_length", "answer.option_ids")
        if not set(cast(list[str], selected)) <= set(cast(list[str], allowed)):
            raise AgentValidationError("reference_conflict", "answer.option_ids")
        return
    if kind == "permission":
        if set(answer) != {"kind", "allowed"} or answer.get("kind") != "permission":
            raise AgentValidationError("invalid_request", "answer")
        if not isinstance(answer.get("allowed"), bool):
            raise AgentValidationError("invalid_type", "answer.allowed")
        return
    raise AgentValidationError("unsupported_schema", "answer_schema.kind")


def _answer_is_rejection(answer: object) -> bool:
    return isinstance(answer, dict) and answer.get("kind") == "permission" and answer.get("allowed") is False


def _as_uuid(value: object) -> UUID:
    return uuid.UUID(str(value))


def _as_optional_uuid(value: object) -> UUID | None:
    return None if value is None else uuid.UUID(str(value))


def _ref_from(value: object) -> ResourceRef:
    if not isinstance(value, dict):
        raise AgentValidationError("invalid_type", "request")
    return ResourceRef(_as_uuid(value.get("resource_id")), int(cast(int, value.get("revision"))))


def _refs_from(value: object, field_path: str) -> tuple[ResourceRef, ...]:
    if not isinstance(value, list):
        raise AgentValidationError("invalid_type", field_path)
    return tuple(_ref_from(item) for item in value)


def _model_from(value: object, field_path: str) -> ModelChoice:
    if not isinstance(value, dict):
        raise AgentValidationError("invalid_type", field_path)
    kind = value.get("kind")
    if kind not in ("explicit", "scenario_default", "inherit"):
        raise AgentValidationError("invalid_enum", field_path)
    return ModelChoice(
        kind=cast(Any, kind),
        connection_id=cast(str | None, value.get("connection_id")),
        scenario_id=cast(str | None, value.get("scenario_id")),
    )


def _request_from_doc(doc: JsonDoc) -> AgentRequest:
    """把 revise_queued 的封闭 request 文档还原为唯一本文 DTO（§5.5）。"""
    payload = decode_json_doc(doc)
    if not isinstance(payload, dict):
        raise AgentValidationError("invalid_type", "request")
    key_doc = payload.get("key")
    context_doc = payload.get("context")
    if not isinstance(key_doc, dict) or not isinstance(context_doc, dict):
        raise AgentValidationError("invalid_type", "request")
    roles_doc = payload.get("roles")
    if not isinstance(roles_doc, list):
        raise AgentValidationError("invalid_type", "request.roles")
    roles: list[RoleBinding] = []
    for role in roles_doc:
        if not isinstance(role, dict):
            raise AgentValidationError("invalid_type", "request.roles")
        roles.append(
            RoleBinding(
                role_key=cast(str, role.get("role_key")),
                revision=int(cast(int, role.get("revision"))),
                prompt=_ref_from(role.get("prompt")),
                model=_model_from(role.get("model"), "request.roles.model"),
                default_skills=_refs_from(role.get("default_skills"), "request.roles.default_skills"),
                default_mcp=_refs_from(role.get("default_mcp"), "request.roles.default_mcp"),
                enabled_skills=_refs_from(role.get("enabled_skills"), "request.roles.enabled_skills"),
            )
        )
    predecessors = context_doc.get("predecessor_intents")
    if not isinstance(predecessors, list):
        raise AgentValidationError("invalid_type", "request.context.predecessor_intents")
    as_of = payload.get("as_of")
    reviewed_plan = payload.get("reviewed_plan")
    request = AgentRequest(
        key=SubmissionKey(
            domain=cast(str, key_doc.get("domain")),
            source_module=cast(str, key_doc.get("source_module")),
            request_key=cast(str, key_doc.get("request_key")),
        ),
        conversation_id=_as_optional_uuid(payload.get("conversation_id")),
        intent_kind=cast(Any, payload.get("intent_kind")),
        text=cast(str, payload.get("text")),
        mode=cast(Any, payload.get("mode")),
        as_of=date.fromisoformat(as_of) if isinstance(as_of, str) else None,
        context=ContextBinding(
            path_id=_as_optional_uuid(context_doc.get("path_id")),
            path_revision=cast(int | None, context_doc.get("path_revision")),
            anchor_message_id=_as_optional_uuid(context_doc.get("anchor_message_id")),
            predecessor_intents=tuple(_as_uuid(item) for item in predecessors),
            old_evidence=_refs_from(context_doc.get("old_evidence"), "request.context.old_evidence"),
        ),
        sources=_refs_from(payload.get("sources"), "request.sources"),
        roles=tuple(roles),
        manual_skills=_refs_from(payload.get("manual_skills"), "request.manual_skills"),
        mcp=_refs_from(payload.get("mcp"), "request.mcp"),
        output_contract=cast(str, payload.get("output_contract")),
        business_owner=cast(str | None, payload.get("business_owner")),
        business_run=cast(str | None, payload.get("business_run")),
        business_slot=cast(str | None, payload.get("business_slot")),
        reviewed_plan=_ref_from(reviewed_plan) if reviewed_plan is not None else None,
        plan_review_revision=cast(int | None, payload.get("plan_review_revision")),
        plan_review_digest=cast(str | None, payload.get("plan_review_digest")),
        as_of_basis=cast(Any, payload.get("as_of_basis")),
    )
    validate_request(request)
    return request


class AgentDao(BaseDao):
    """Agent 应用表 DAO（继承 BaseDao，绑定当前可信服务域）。"""

    # ------------------------------------------------------------------
    # 共享写入基础设施（D01）
    # ------------------------------------------------------------------

    async def _upsert_rows(
        self,
        table_name: str,
        columns: list[str],
        pk_columns: list[str],
        rows: list[dict[str, Any]],
        *,
        conn: Any = None,
    ) -> int:
        """批量 upsert，复用 BaseDao._save_upsert 的冲突/类型归一语义。

        空 rows 返回 0；不吞写库异常（调用方事务因此回滚）。
        """
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        return await self._save_upsert(
            df,
            table_name,
            columns,
            pk_columns=pk_columns,
            conn=conn,
        )

    async def append_event(
        self,
        *,
        event_key: str,
        domain: str,
        entity_id: UUID,
        entity_revision: int,
        kind: str,
        payload: str,
        conversation_id: UUID | None = None,
        execution_id: UUID | None = None,
        stream_key: str = PUBLIC_STREAM_KEY,
        conn: Any = None,
    ) -> int:
        """在事务最后追加公开事件并递增 stream tail，返回提交次序 seq。

        事件 payload 必须已是规范 JSON；artifact.published 只接受
        ArtifactPublication 三键指针（正文按准确版本经 read_artifact 读取）。
        ``seq`` 由本事务在锁住 stream head 后递增，不使用 SERIAL 预取号冒充
        提交次序；回滚不推进（与 §8.3 一致）。
        """
        if entity_revision < 1:
            raise ValueError("entity_revision must be >= 1")
        payload_obj = decode_json_doc(payload)
        if kind == "artifact.published":
            if not isinstance(payload_obj, dict):
                raise ValueError("artifact.published payload must be an object")
            keys = frozenset(payload_obj)
            if keys != _ARTIFACT_PUBLICATION_KEYS:
                raise ValueError(
                    "artifact.published payload must contain exactly "
                    f"{sorted(_ARTIFACT_PUBLICATION_KEYS)}, got {sorted(keys)}"
                )

        payload_digest = _digest(payload)

        if conn is None:
            async with self._guarded_begin() as tx:
                return await self._append_event_in_tx(
                    tx,
                    event_key=event_key,
                    domain=domain,
                    entity_id=entity_id,
                    entity_revision=entity_revision,
                    kind=kind,
                    payload_obj=payload_obj,
                    payload_digest=payload_digest,
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    stream_key=stream_key,
                )
        return await self._append_event_in_tx(
            conn,
            event_key=event_key,
            domain=domain,
            entity_id=entity_id,
            entity_revision=entity_revision,
            kind=kind,
            payload_obj=payload_obj,
            payload_digest=payload_digest,
            conversation_id=conversation_id,
            execution_id=execution_id,
            stream_key=stream_key,
        )

    async def _append_event_in_tx(
        self,
        conn: Any,
        *,
        event_key: str,
        domain: str,
        entity_id: UUID,
        entity_revision: int,
        kind: str,
        payload_obj: Any,
        payload_digest: str,
        conversation_id: UUID | None,
        execution_id: UUID | None,
        stream_key: str,
    ) -> int:
        await conn.execute(
            pg_insert(AgentStreamHead)
            .values(stream_key=stream_key, tail=0)
            .on_conflict_do_nothing(index_elements=["stream_key"])
        )
        head = await conn.execute(
            sa.select(AgentStreamHead.tail).where(AgentStreamHead.stream_key == stream_key).with_for_update()
        )
        row = head.first()
        if row is None:
            raise RuntimeError(f"agent_stream_head row missing for {stream_key!r}")
        seq = int(row[0]) + 1
        await conn.execute(
            sa.update(AgentStreamHead)
            .where(AgentStreamHead.stream_key == stream_key)
            .values(tail=seq, updated_at=sa.func.now())
        )
        await conn.execute(
            sa.insert(AgentEvent).values(
                seq=seq,
                event_key=event_key,
                domain=domain,
                conversation_id=conversation_id,
                execution_id=execution_id,
                entity_id=entity_id,
                entity_revision=entity_revision,
                kind=kind,
                payload=payload_obj,
                payload_digest=payload_digest,
            )
        )
        return seq

    # ------------------------------------------------------------------
    # 公开事件指针校验辅助（H01 保存成果时复用）
    # ------------------------------------------------------------------

    @staticmethod
    def artifact_publication_payload(artifact_id: UUID, revision: int, content_digest: str) -> str:
        """构造 ArtifactPublication 规范 JSON（只含身份/版本/摘要）。"""
        return json_doc({"artifact_id": artifact_id, "revision": revision, "content_digest": content_digest})

    # ------------------------------------------------------------------
    # D02：受理、队列与命令
    # ------------------------------------------------------------------

    async def _lock_admission_head(self, conn: Any) -> int:
        """只锁总准入头并返回当前已分配序号；领取 slot 不得消耗序号。"""
        await conn.execute(
            pg_insert(AgentAdmissionHead)
            .values(scope="total", next_seq=0)
            .on_conflict_do_nothing(index_elements=["scope"])
        )
        head = await conn.execute(
            sa.select(AgentAdmissionHead.next_seq).where(AgentAdmissionHead.scope == "total").with_for_update()
        )
        row = head.first()
        if row is None:  # pragma: no cover - 插入后不可能缺失
            raise RuntimeError("agent_admission_head missing total row")
        return int(row[0])

    async def _next_admission_seq(self, conn: Any) -> int:
        seq = await self._lock_admission_head(conn) + 1
        await conn.execute(
            sa.update(AgentAdmissionHead).where(AgentAdmissionHead.scope == "total").values(next_seq=seq)
        )
        return seq

    async def _lock_submission(self, conn: Any, key: SubmissionKey) -> None:
        """按三元键串行化同身份提交，避免重复领取准入序号。"""
        lock_key = json_doc({"domain": key.domain, "source_module": key.source_module, "request_key": key.request_key})
        await conn.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", (lock_key,))

    async def _acquire_uses_in_tx(
        self, conn: Any, refs: tuple[ResourceRef, ...], *, owner_kind: str, owner_id: UUID
    ) -> None:
        unique: dict[tuple[UUID, int], ResourceRef] = {}
        for ref in refs:
            unique.setdefault((ref.resource_id, ref.revision), ref)
        for resource_id, revision in sorted(unique):
            resource = (
                await conn.execute(sa.select(AgentResource).where(AgentResource.id == resource_id).with_for_update())
            ).first()
            if resource is None:
                raise AgentValidationError("not_found", owner_kind)
            if resource.deleting or resource.deleted_at is not None:
                raise AgentValidationError("resource_deleting", owner_kind)
            version = (
                await conn.execute(
                    sa.select(AgentResourceVersion).where(
                        AgentResourceVersion.resource_id == resource_id,
                        AgentResourceVersion.revision == revision,
                    )
                )
            ).first()
            if version is None or version.availability in ("revoked", "deleted"):
                raise AgentValidationError("source_unavailable", owner_kind)
            inserted = await conn.execute(
                pg_insert(AgentUse)
                .values(
                    resource_id=resource_id,
                    resource_revision=revision,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    active=True,
                )
                .on_conflict_do_nothing(index_elements=["resource_id", "resource_revision", "owner_kind", "owner_id"])
            )
            if inserted.rowcount:
                await conn.execute(
                    sa.update(AgentResource)
                    .where(AgentResource.id == resource_id)
                    .values(use_revision=AgentResource.use_revision + 1)
                )

    async def _execution_latest_id(self, conn: Any, intent_id: UUID) -> UUID | None:
        row = await conn.execute(
            sa.select(AgentExecution.id)
            .where(AgentExecution.intent_id == intent_id)
            .order_by(AgentExecution.execution_number.desc())
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def accept_request(self, domain: str, request: AgentRequest, digest: str) -> SubmissionReceipt:
        """原子受理意图；重复 key 查原回执，不调用原生 SDK。"""
        async with self._guarded_begin() as conn:
            await self._lock_submission(conn, request.key)
            key = request.key
            existing = (
                await conn.execute(
                    sa.select(AgentIntent).where(
                        AgentIntent.domain == key.domain,
                        AgentIntent.source_module == key.source_module,
                        AgentIntent.request_key == key.request_key,
                    )
                )
            ).first()
            if existing is not None:
                retired = existing.acceptance_state == "retired"
                if not retired:
                    retired_row = (
                        await conn.execute(
                            sa.select(AgentRetiredKey).where(
                                AgentRetiredKey.domain == key.domain,
                                AgentRetiredKey.source_module == key.source_module,
                                AgentRetiredKey.request_key == key.request_key,
                            )
                        )
                    ).first()
                    retired = retired_row is not None
                if retired:
                    return SubmissionReceipt(
                        status="retired",
                        key=request.key,
                        intent_id=existing.id,
                        execution_id=None,
                        input_revision=None,
                        accepted_seq=None,
                        problem=_problem("retired", related_id=existing.id),
                    )
                if existing.initial_digest != digest:
                    return SubmissionReceipt(
                        status="conflict",
                        key=request.key,
                        intent_id=existing.id,
                        execution_id=None,
                        input_revision=None,
                        accepted_seq=None,
                        problem=_problem(
                            "identity_conflict", field="key", expected=None, actual=None, related_id=existing.id
                        ),
                    )
                return SubmissionReceipt(
                    status="same_request",
                    key=request.key,
                    intent_id=existing.id,
                    execution_id=await self._execution_latest_id(conn, existing.id),
                    input_revision=existing.current_input_revision,
                    accepted_seq=existing.accepted_seq,
                    problem=None,
                )

            if request.key.domain != domain:
                raise AgentValidationError("reference_conflict", "key.domain")

            retired_key = (
                await conn.execute(
                    sa.select(AgentRetiredKey).where(
                        AgentRetiredKey.domain == key.domain,
                        AgentRetiredKey.source_module == key.source_module,
                        AgentRetiredKey.request_key == key.request_key,
                    )
                )
            ).first()
            if retired_key is not None:
                return SubmissionReceipt(
                    status="retired",
                    key=request.key,
                    intent_id=None,
                    execution_id=None,
                    input_revision=None,
                    accepted_seq=None,
                    problem=_problem("retired"),
                )

            admission_seq = await self._next_admission_seq(conn)

            if request.conversation_id is not None:
                conversation = (
                    await conn.execute(
                        sa.select(AgentConversation)
                        .where(
                            AgentConversation.id == request.conversation_id,
                            AgentConversation.domain == domain,
                        )
                        .with_for_update()
                    )
                ).first()
                if conversation is None:
                    return SubmissionReceipt(
                        status="rejected",
                        key=request.key,
                        intent_id=None,
                        execution_id=None,
                        input_revision=None,
                        accepted_seq=None,
                        problem=_problem("not_found", field="conversation_id", related_id=request.conversation_id),
                    )
                if conversation.deleting or conversation.deleted_at is not None:
                    return SubmissionReceipt(
                        status="rejected",
                        key=request.key,
                        intent_id=None,
                        execution_id=None,
                        input_revision=None,
                        accepted_seq=None,
                        problem=_problem("conversation_deleting", related_id=conversation.id),
                    )
                accepted_seq = int(conversation.next_seq) + 1
                await conn.execute(
                    sa.update(AgentConversation)
                    .where(AgentConversation.id == conversation.id)
                    .values(next_seq=accepted_seq, updated_at=sa.func.now())
                )
            else:
                conversation_id = uuid.uuid4()
                path_id = uuid.uuid4()
                accepted_seq = 1
                await conn.execute(
                    sa.insert(AgentConversation).values(
                        id=conversation_id,
                        domain=domain,
                        title=request.text.strip()[:200] or "新会话",
                        pinned=False,
                        archived=False,
                        revision=1,
                        current_path_id=path_id,
                        current_path_revision=1,
                        next_seq=accepted_seq,
                        use_revision=0,
                        deleting=False,
                        updated_at=sa.func.now(),
                    )
                )
                await conn.execute(
                    sa.insert(AgentPath).values(
                        id=path_id,
                        revision=1,
                        conversation_id=conversation_id,
                        inherited_refs=[],
                    )
                )
                conversation = (
                    await conn.execute(
                        sa.select(AgentConversation).where(AgentConversation.id == conversation_id).with_for_update()
                    )
                ).first()

            intent_id = uuid.uuid4()
            await conn.execute(
                sa.insert(AgentIntent).values(
                    id=intent_id,
                    domain=domain,
                    source_module=key.source_module,
                    request_key=key.request_key,
                    initial_digest=digest,
                    conversation_id=conversation.id,
                    accepted_seq=accepted_seq,
                    admission_seq=admission_seq,
                    current_input_revision=1,
                    acceptance_state="queued",
                )
            )

            execution_id = uuid.uuid4()
            task_id = uuid.uuid4()
            command_id = uuid.uuid4()
            command_payload = decode_json_doc(
                json_doc(
                    {
                        "execution_id": execution_id,
                        "intent_id": intent_id,
                        "input_revision": 1,
                        "plan_revision": 1,
                    }
                )
            )
            dispatch_digest = _digest(
                json_doc(
                    {
                        "kind": "dispatch_start",
                        "target": {"execution_id": execution_id, "task_id": task_id, "attempt_id": None},
                        "expected_input_revision": 1,
                        "expected_plan_revision": 1,
                        "payload": command_payload,
                    }
                )
            )
            await conn.execute(
                sa.insert(AgentCommand).values(
                    id=command_id,
                    domain=domain,
                    kind="dispatch_start",
                    target={
                        "execution_id": str(execution_id),
                        "task_id": str(task_id),
                        "attempt_id": None,
                    },
                    payload=command_payload,
                    payload_digest=dispatch_digest,
                    expected_input_revision=1,
                    expected_plan_revision=1,
                    handoff="prepared",
                    receipt={
                        "status": "committed",
                        "intent_id": str(intent_id),
                        "execution_id": str(execution_id),
                        "input_revision": 1,
                        "accepted_seq": accepted_seq,
                    },
                )
            )

            request_doc = json_doc(request)
            context_doc = json_doc(
                {
                    "path_id": request.context.path_id,
                    "path_revision": request.context.path_revision,
                    "anchor_message_id": request.context.anchor_message_id,
                    "predecessor_intents": request.context.predecessor_intents,
                    "old_evidence": request.context.old_evidence,
                }
            )
            await conn.execute(
                sa.insert(AgentInput).values(
                    intent_id=intent_id,
                    revision=1,
                    request=decode_json_doc(request_doc),
                    input_digest=digest,
                    model_snapshot={},
                    context_binding=decode_json_doc(context_doc),
                    resolved_manifest=None,
                    resolved_at=None,
                    dispatch_command_id=command_id,
                )
            )

            plan_ref = request.reviewed_plan
            await conn.execute(
                sa.insert(AgentExecution).values(
                    id=execution_id,
                    intent_id=intent_id,
                    execution_number=1,
                    input_revision=1,
                    plan_revision=1,
                    plan={"nodes": []},
                    plan_resource_id=plan_ref.resource_id if plan_ref else None,
                    plan_resource_revision=plan_ref.revision if plan_ref else None,
                    framework_version=_FRAMEWORK_VERSION,
                    projection={},
                    fence=0,
                )
            )
            await conn.execute(
                sa.insert(AgentTask).values(
                    id=task_id,
                    execution_id=execution_id,
                    plan_node_id=None,
                    role_key=request.roles[0].role_key if request.roles else "main",
                    plan_revision=1,
                    depends_on=[],
                    required=True,
                )
            )

            refs = list(request.sources) + list(request.manual_skills) + list(request.mcp)
            refs += list(request.context.old_evidence)
            for role in request.roles:
                refs += [role.prompt, *role.default_skills, *role.default_mcp, *role.enabled_skills]
            if request.reviewed_plan is not None:
                refs.append(request.reviewed_plan)
            await self._acquire_uses_in_tx(conn, tuple(refs), owner_kind="input", owner_id=intent_id)

            await conn.execute(
                sa.update(AgentConversation)
                .where(AgentConversation.id == conversation.id)
                .values(active_execution_id=execution_id, updated_at=sa.func.now())
            )

            await self.append_event(
                event_key=f"intent.accepted:{intent_id}",
                domain=domain,
                entity_id=intent_id,
                entity_revision=1,
                kind="intent.accepted",
                payload=json_doc(
                    {
                        "intent_id": intent_id,
                        "input_revision": 1,
                        "accepted_seq": accepted_seq,
                        "admission_seq": admission_seq,
                        "acceptance_state": "queued",
                    }
                ),
                conversation_id=conversation.id,
                execution_id=execution_id,
                conn=conn,
            )
            return SubmissionReceipt(
                status="accepted",
                key=request.key,
                intent_id=intent_id,
                execution_id=execution_id,
                input_revision=1,
                accepted_seq=accepted_seq,
                problem=None,
            )

    async def lookup_submission(self, domain: str, key: SubmissionKey) -> SubmissionReceipt:
        async with self._guarded_begin() as conn:
            intent = (
                await conn.execute(
                    sa.select(AgentIntent).where(
                        AgentIntent.domain == domain,
                        AgentIntent.source_module == key.source_module,
                        AgentIntent.request_key == key.request_key,
                    )
                )
            ).first()
            if intent is None:
                retired = (
                    await conn.execute(
                        sa.select(AgentRetiredKey).where(
                            AgentRetiredKey.domain == domain,
                            AgentRetiredKey.source_module == key.source_module,
                            AgentRetiredKey.request_key == key.request_key,
                        )
                    )
                ).first()
                status = "retired" if retired is not None else "rejected"
                return SubmissionReceipt(
                    status=status,
                    key=key,
                    intent_id=None,
                    execution_id=None,
                    input_revision=None,
                    accepted_seq=None,
                    problem=_problem("retired" if retired is not None else "not_found"),
                )
            if intent.acceptance_state == "retired":
                return SubmissionReceipt(
                    status="retired",
                    key=key,
                    intent_id=intent.id,
                    execution_id=None,
                    input_revision=None,
                    accepted_seq=None,
                    problem=_problem("retired", related_id=intent.id),
                )
            return SubmissionReceipt(
                status="same_request",
                key=key,
                intent_id=intent.id,
                execution_id=await self._execution_latest_id(conn, intent.id),
                input_revision=intent.current_input_revision,
                accepted_seq=intent.accepted_seq,
                problem=None,
            )

    async def request_input_revision(
        self, domain: str, command: ControlCommand, request: AgentRequest, input_digest: str
    ) -> CommandReceipt:
        """queued 意图显式修订：追加 input/uses，保初始 key、位置与 initial_digest。"""
        if command.action != "revise_queued":
            raise AgentValidationError("invalid_request", "action")
        validate_payload("revise_queued", command.payload)
        async with self._guarded_begin() as conn:
            return await self._request_input_revision_in_tx(conn, domain, command, request, input_digest)

    async def _request_input_revision_in_tx(
        self,
        conn: Any,
        domain: str,
        command: ControlCommand,
        request: AgentRequest,
        input_digest: str,
    ) -> CommandReceipt:
        await self._lock_submission(conn, request.key)
        intent = (
            await conn.execute(
                sa.select(AgentIntent)
                .where(
                    AgentIntent.domain == domain,
                    AgentIntent.source_module == request.key.source_module,
                    AgentIntent.request_key == request.key.request_key,
                )
                .with_for_update()
            )
        ).first()
        if intent is None:
            return CommandReceipt(
                command_id=command.command_id,
                status="rejected",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem("not_found", field="request_key", related_id=None),
            )
        latest_execution = await self._execution_latest_id(conn, intent.id)
        if latest_execution is None or latest_execution != command.target.execution_id:
            return CommandReceipt(
                command_id=command.command_id,
                status="conflict",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem("reference_conflict", field="execution_id", related_id=intent.id),
            )
        if intent.acceptance_state != "queued":
            return CommandReceipt(
                command_id=command.command_id,
                status="conflict",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem("state_conflict", related_id=intent.id),
            )
        if command.expected_input_revision != intent.current_input_revision:
            return CommandReceipt(
                command_id=command.command_id,
                status="conflict",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem(
                    "revision_conflict",
                    field="expected_input_revision",
                    expected=command.expected_input_revision,
                    actual=intent.current_input_revision,
                    related_id=intent.id,
                ),
            )
        new_revision = int(intent.current_input_revision) + 1
        await conn.execute(
            sa.insert(AgentInput).values(
                intent_id=intent.id,
                revision=new_revision,
                request=decode_json_doc(json_doc(request)),
                input_digest=input_digest,
                model_snapshot={},
                context_binding=decode_json_doc(
                    json_doc(
                        {
                            "path_id": request.context.path_id,
                            "path_revision": request.context.path_revision,
                            "anchor_message_id": request.context.anchor_message_id,
                            "predecessor_intents": request.context.predecessor_intents,
                            "old_evidence": request.context.old_evidence,
                        }
                    )
                ),
                resolved_manifest=None,
                resolved_at=None,
            )
        )
        refs = list(request.sources) + list(request.manual_skills) + list(request.mcp)
        refs += list(request.context.old_evidence)
        for role in request.roles:
            refs += [role.prompt, *role.default_skills, *role.default_mcp, *role.enabled_skills]
        if request.reviewed_plan is not None:
            refs.append(request.reviewed_plan)
        await self._acquire_uses_in_tx(conn, tuple(refs), owner_kind="input", owner_id=intent.id)
        await conn.execute(
            sa.update(AgentIntent).where(AgentIntent.id == intent.id).values(current_input_revision=new_revision)
        )
        await self._save_command_row(conn, domain, command, status="committed")
        await self.append_event(
            event_key=f"intent.input_revised:{intent.id}:{new_revision}",
            domain=domain,
            entity_id=intent.id,
            entity_revision=new_revision,
            kind="intent.input_revised",
            payload=json_doc({"intent_id": intent.id, "input_revision": new_revision, "input_digest": input_digest}),
            conversation_id=intent.conversation_id,
            conn=conn,
        )
        return CommandReceipt(
            command_id=command.command_id,
            status="committed",
            native_request_id=None,
            continuation_ref=None,
            problem=None,
        )

    async def save_command(self, domain: str, command: ControlCommand) -> CommandReceipt:
        validate_payload(command.action, command.payload)
        command_doc = _command_digest(command)
        async with self._guarded_begin() as conn:
            existing = (
                await conn.execute(
                    sa.select(AgentCommand).where(
                        AgentCommand.id == command.command_id,
                        AgentCommand.domain == domain,
                    )
                )
            ).first()
            if existing is not None:
                if existing.retired_at is not None:
                    return CommandReceipt(
                        command_id=command.command_id,
                        status="rejected",
                        native_request_id=None,
                        continuation_ref=None,
                        problem=_problem("retired", related_id=command.command_id),
                    )
                if existing.payload_digest != command_doc:
                    return CommandReceipt(
                        command_id=command.command_id,
                        status="conflict",
                        native_request_id=None,
                        continuation_ref=None,
                        problem=_problem("identity_conflict", related_id=command.command_id),
                    )
                stored = existing.receipt or {}
                return CommandReceipt(
                    command_id=command.command_id,
                    status=stored.get("status", "same_command"),
                    native_request_id=existing.native_request_id,
                    continuation_ref=stored.get("continuation_ref"),
                    problem=None,
                )

            execution = (
                await conn.execute(
                    sa.select(AgentExecution)
                    .join(AgentIntent, AgentIntent.id == AgentExecution.intent_id)
                    .where(
                        AgentExecution.id == command.target.execution_id,
                        AgentIntent.domain == domain,
                    )
                )
            ).first()
            if execution is None:
                return CommandReceipt(
                    command_id=command.command_id,
                    status="rejected",
                    native_request_id=None,
                    continuation_ref=None,
                    problem=_problem("not_found", related_id=command.target.execution_id),
                )

            decoded = decode_json_doc(command.payload)
            payload_obj = decoded if isinstance(decoded, dict) else {}

            if command.action == "revise_queued":
                request_obj = payload_obj.get("request")
                if not isinstance(request_obj, dict):
                    raise AgentValidationError("invalid_request", "payload.request")
                request = _request_from_doc(json_doc(request_obj))
                digest = request_digest(request)
                return await self._request_input_revision_in_tx(conn, domain, command, request, digest)

            if command.action == "answer_decision":
                return await self._save_answer(conn, domain, command, execution, payload_obj)

            if command.action == "withdraw_queued":
                intent = (
                    await conn.execute(
                        sa.select(AgentIntent).where(AgentIntent.id == execution.intent_id).with_for_update()
                    )
                ).first()
                if intent is not None and intent.acceptance_state != "queued":
                    return CommandReceipt(
                        command_id=command.command_id,
                        status="conflict",
                        native_request_id=None,
                        continuation_ref=None,
                        problem=_problem("state_conflict", related_id=intent.id),
                    )
                if intent is not None:
                    await conn.execute(
                        sa.update(AgentIntent).where(AgentIntent.id == intent.id).values(acceptance_state="withdrawn")
                    )

            await self._save_command_row(conn, domain, command, status="committed")
            await self.append_event(
                event_key=f"command.recorded:{command.command_id}",
                domain=domain,
                entity_id=command.command_id,
                entity_revision=1,
                kind="command.recorded",
                payload=json_doc(
                    {
                        "command_id": command.command_id,
                        "action": command.action,
                        "execution_id": command.target.execution_id,
                    }
                ),
                execution_id=command.target.execution_id,
                conn=conn,
            )
            return CommandReceipt(
                command_id=command.command_id,
                status="committed",
                native_request_id=None,
                continuation_ref=None,
                problem=None,
            )

    async def _save_command_row(self, conn: Any, domain: str, command: ControlCommand, *, status: str) -> None:
        await conn.execute(
            sa.insert(AgentCommand).values(
                id=command.command_id,
                domain=domain,
                kind=command.action,
                target={
                    "execution_id": str(command.target.execution_id),
                    "task_id": str(command.target.task_id) if command.target.task_id else None,
                    "attempt_id": str(command.target.attempt_id) if command.target.attempt_id else None,
                },
                payload=decode_json_doc(command.payload),
                payload_digest=_command_digest(command),
                expected_input_revision=command.expected_input_revision,
                expected_plan_revision=command.expected_plan_revision,
                handoff="prepared",
                receipt={"status": status},
            )
        )

    async def _save_answer(
        self,
        conn: Any,
        domain: str,
        command: ControlCommand,
        execution: Any,
        payload: dict[str, Any],
    ) -> CommandReceipt:
        decision_id = uuid.UUID(str(payload["decision_id"]))
        decision_revision = int(payload["decision_revision"])
        decision = (
            await conn.execute(sa.select(AgentDecision).where(AgentDecision.id == decision_id).with_for_update())
        ).first()
        if decision is None:
            return CommandReceipt(
                command_id=command.command_id,
                status="rejected",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem("not_found", related_id=decision_id),
            )
        if decision.execution_id != execution.id:
            return CommandReceipt(
                command_id=command.command_id,
                status="rejected",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem("decision_invalid", related_id=decision_id),
            )
        if (
            decision.status != "pending"
            or decision.revision != decision_revision
            or decision.native_request_id != payload["native_request_id"]
            or decision.scope_digest != payload["scope_digest"]
            or decision.answer_command_id is not None
            or decision.answer_phase != "unanswered"
        ):
            return CommandReceipt(
                command_id=command.command_id,
                status="rejected",
                native_request_id=None,
                continuation_ref=None,
                problem=_problem("decision_invalid", related_id=decision_id),
            )
        answer = payload.get("answer")
        _validate_answer(answer, decision.answer_schema)
        rejected = _answer_is_rejection(answer)
        revision = decision.revision + 1
        answer_doc = json_doc({"decision_id": decision.id, "revision": revision, "answer": answer})
        await self._save_command_row(conn, domain, command, status="committed")
        await conn.execute(
            sa.update(AgentCommand)
            .where(AgentCommand.id == command.command_id)
            .values(
                handoff="acknowledged",
                receipt={
                    "status": "committed",
                    "continuation_ref": answer_doc,
                    "answer": answer,
                },
            )
        )
        await conn.execute(
            sa.update(AgentDecision)
            .where(AgentDecision.id == decision.id)
            .values(
                status="rejected" if rejected else "answered",
                answer_command_id=command.command_id,
                answer_phase="recorded",
                answer_problem=None,
                revision=decision.revision + 1,
            )
        )
        await self.append_event(
            event_key=f"decision.changed:{decision.id}:{revision}",
            domain=domain,
            entity_id=decision.id,
            entity_revision=revision,
            kind="decision.changed",
            payload=json_doc(
                {
                    "decision_id": decision.id,
                    "execution_id": decision.execution_id,
                    "revision": revision,
                    "status": "rejected" if rejected else "answered",
                    "answer_phase": "recorded",
                    "answer_command_id": command.command_id,
                    "answer": answer,
                }
            ),
            execution_id=decision.execution_id,
            conn=conn,
        )
        logger.debug("decision answered: %s", decision.id)
        return CommandReceipt(
            command_id=command.command_id,
            status="committed",
            native_request_id=decision.native_request_id,
            continuation_ref=answer_doc,
            problem=None,
        )

    async def lookup_command(self, domain: str, command_id: UUID) -> CommandReceipt:
        async with self._guarded_begin() as conn:
            command = (
                await conn.execute(
                    sa.select(AgentCommand).where(
                        AgentCommand.id == command_id,
                        AgentCommand.domain == domain,
                    )
                )
            ).first()
            if command is None:
                return CommandReceipt(
                    command_id=command_id,
                    status="rejected",
                    native_request_id=None,
                    continuation_ref=None,
                    problem=_problem("not_found", related_id=command_id),
                )
            if command.retired_at is not None:
                return CommandReceipt(
                    command_id=command_id,
                    status="rejected",
                    native_request_id=None,
                    continuation_ref=None,
                    problem=_problem("retired", related_id=command_id),
                )
            stored = command.receipt or {}
            return CommandReceipt(
                command_id=command_id,
                status=stored.get("status", "committed"),
                native_request_id=command.native_request_id,
                continuation_ref=stored.get("continuation_ref"),
                problem=None,
            )

    async def lookup_mutation(self, domain: str, operation_id: UUID) -> MutationReceipt:
        async with self._guarded_begin() as conn:
            command = (
                await conn.execute(
                    sa.select(AgentCommand).where(
                        AgentCommand.id == operation_id,
                        AgentCommand.domain == domain,
                    )
                )
            ).first()
            if command is None:
                return MutationReceipt(
                    operation_id=operation_id,
                    status="rejected",
                    resource_id=None,
                    revision=None,
                    problem=_problem("not_found", related_id=operation_id),
                )
            stored = command.receipt or {}
            raw_revision = stored.get("revision")
            return MutationReceipt(
                operation_id=operation_id,
                status=stored.get("status", "committed"),
                resource_id=_as_optional_uuid(stored.get("resource_id")),
                revision=int(raw_revision) if isinstance(raw_revision, int) else None,
                problem=None,
            )

    async def apply_mutation(self, domain: str, mutation: Mutation) -> MutationReceipt:
        """D02 只保证管理意图可查回；D04/H02 再补齐资源/草稿/删除落库。"""
        async with self._guarded_begin() as conn:
            existing = (
                await conn.execute(
                    sa.select(AgentCommand).where(
                        AgentCommand.id == mutation.operation_id,
                        AgentCommand.domain == domain,
                    )
                )
            ).first()
            if existing is not None:
                if existing.payload_digest != _mutation_digest(mutation):
                    return MutationReceipt(
                        operation_id=mutation.operation_id,
                        status="conflict",
                        resource_id=None,
                        revision=None,
                        problem=_problem("identity_conflict", related_id=mutation.operation_id),
                    )
                stored = existing.receipt or {}
                raw_revision = stored.get("revision")
                return MutationReceipt(
                    operation_id=mutation.operation_id,
                    status=stored.get("status", "same_operation"),
                    resource_id=_as_optional_uuid(stored.get("resource_id")),
                    revision=int(raw_revision) if isinstance(raw_revision, int) else None,
                    problem=None,
                )
            await conn.execute(
                sa.insert(AgentCommand).values(
                    id=mutation.operation_id,
                    domain=domain,
                    kind=mutation.kind,
                    payload=decode_json_doc(mutation.payload),
                    payload_digest=_mutation_digest(mutation),
                    expected_revision=mutation.expected_revision,
                    handoff="acknowledged",
                    receipt={
                        "status": "committed",
                        "resource_id": str(mutation.resource_id) if mutation.resource_id else None,
                        "revision": mutation.expected_revision,
                    },
                )
            )
            return MutationReceipt(
                operation_id=mutation.operation_id,
                status="committed",
                resource_id=mutation.resource_id,
                revision=mutation.expected_revision,
                problem=None,
            )

    async def claim_total_slot(self, domain: str, execution_id: UUID) -> JsonDoc:
        async with self._guarded_begin() as conn:
            execution = (
                await conn.execute(
                    sa.select(AgentExecution)
                    .join(AgentIntent, AgentIntent.id == AgentExecution.intent_id)
                    .where(
                        AgentExecution.id == execution_id,
                        AgentIntent.domain == domain,
                    )
                )
            ).first()
            if execution is None:
                return json_doc(
                    {
                        "status": "refused",
                        "slot_no": None,
                        "admission_seq": None,
                        "problem": {"code": "not_found"},
                    }
                )
            intent = (await conn.execute(sa.select(AgentIntent).where(AgentIntent.id == execution.intent_id))).first()
            if intent is None:  # pragma: no cover - 复合外键保证存在
                return json_doc(
                    {
                        "status": "refused",
                        "slot_no": None,
                        "admission_seq": None,
                        "problem": {"code": "not_found"},
                    }
                )
            admission_seq = int(intent.admission_seq)
            # 只锁总准入头，不递增：领取 slot 不是一次新受理。
            await self._lock_admission_head(conn)
            slots = (
                await conn.execute(sa.select(AgentTotalSlot).order_by(AgentTotalSlot.slot_no).with_for_update())
            ).all()
            for slot in slots:
                if slot.execution_id == execution_id:
                    return json_doc(
                        {
                            "status": "admitted",
                            "slot_no": slot.slot_no,
                            "admission_seq": admission_seq,
                            "problem": None,
                            "generation": slot.generation,
                        }
                    )
            for slot in slots:
                if slot.execution_id is None:
                    await conn.execute(
                        sa.update(AgentTotalSlot)
                        .where(AgentTotalSlot.slot_no == slot.slot_no)
                        .values(
                            execution_id=execution_id,
                            generation=slot.generation + 1,
                            claimed_at=sa.func.now(),
                        )
                    )
                    return json_doc(
                        {
                            "status": "admitted",
                            "slot_no": slot.slot_no,
                            "admission_seq": admission_seq,
                            "problem": None,
                            "generation": slot.generation + 1,
                        }
                    )
            return json_doc(
                {
                    "status": "queued",
                    "slot_no": None,
                    "admission_seq": admission_seq,
                    "problem": None,
                }
            )

    async def release_total_slot(self, domain: str, execution_id: UUID, expected_generation: int) -> None:
        async with self._guarded_begin() as conn:
            execution = (
                await conn.execute(
                    sa.select(AgentExecution)
                    .join(AgentIntent, AgentIntent.id == AgentExecution.intent_id)
                    .where(
                        AgentExecution.id == execution_id,
                        AgentIntent.domain == domain,
                    )
                )
            ).first()
            if execution is None:
                return
            slots = (
                await conn.execute(sa.select(AgentTotalSlot).order_by(AgentTotalSlot.slot_no).with_for_update())
            ).all()
            for slot in slots:
                if slot.execution_id != execution_id:
                    continue
                if slot.generation != expected_generation:
                    raise AgentValidationError("revision_conflict", "generation")
                await conn.execute(
                    sa.update(AgentTotalSlot)
                    .where(AgentTotalSlot.slot_no == slot.slot_no)
                    .values(
                        execution_id=None,
                        generation=slot.generation + 1,
                        claimed_at=None,
                    )
                )
                return

    async def list_queue(self, domain: str, *, cursor: str | None, limit: int) -> Page[QueueItemView]:
        after = _decode_queue_cursor(domain, cursor) if cursor is not None else 0
        async with self._guarded_begin() as conn:
            latest = sa.select(sa.func.max(AgentIntent.admission_seq).label("read_revision")).where(
                AgentIntent.domain == domain
            )
            read_revision = (await conn.execute(latest)).scalar_one_or_none() or 0
            rows = await conn.execute(
                sa.select(
                    AgentIntent.id,
                    AgentIntent.conversation_id,
                    AgentIntent.accepted_seq,
                    AgentIntent.admission_seq,
                    AgentIntent.acceptance_state,
                    AgentIntent.current_input_revision,
                    sa.select(AgentExecution.id)
                    .where(AgentExecution.intent_id == AgentIntent.id)
                    .order_by(AgentExecution.execution_number.desc())
                    .limit(1)
                    .scalar_subquery()
                    .label("execution_id"),
                    sa.select(AgentInput.request)
                    .where(
                        AgentInput.intent_id == AgentIntent.id,
                        AgentInput.revision == AgentIntent.current_input_revision,
                    )
                    .scalar_subquery()
                    .label("request"),
                )
                .where(AgentIntent.domain == domain, AgentIntent.admission_seq > after)
                .order_by(AgentIntent.admission_seq)
                .limit(limit + 1)
            )
            fetched = rows.all()
            items = [
                QueueItemView(
                    intent_id=row.id,
                    execution_id=row.execution_id,
                    conversation_id=row.conversation_id,
                    input_revision=row.current_input_revision,
                    accepted_seq=row.accepted_seq,
                    admission_seq=row.admission_seq,
                    acceptance_state=row.acceptance_state,
                    text_excerpt=_text_excerpt(row.request),
                    problem=None,
                )
                for row in fetched[:limit]
            ]
            next_cursor = _encode_queue_cursor(domain, int(items[-1].admission_seq)) if len(fetched) > limit else None
            return Page(items=tuple(items), next_cursor=next_cursor, read_revision=int(read_revision))
