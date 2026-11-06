"""D01：Agent 应用表结构、约束与 DAO 基础设施集成测试（真实 PostgreSQL）。

覆盖设计正本 §8.2/§8.2.1 的负面证据：复合外键、同域键、角色启用引用、
成果墓碑双向 CHECK、交付快照联合约束、input/execution/attempt 退役
双向 CHECK（拒绝 JSON `null` 与空计划墓碑）、决定答复阶段联合约束，
以及事件只存成果指针与引擎释放守卫。
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import json_doc
from data.cache.cache_manager import CacheManager
from data.data_dictionary import TABLE_DEFINITIONS
from data.persistence import engine_provider
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.models import Base

pytestmark = pytest.mark.integration

DIGEST = "a" * 64

EXPECTED_AGENT_TABLES = {
    "agent_admission_head",
    "agent_total_slots",
    "agent_conversations",
    "agent_paths",
    "agent_drafts",
    "agent_intents",
    "agent_inputs",
    "agent_executions",
    "agent_tasks",
    "agent_attempts",
    "agent_model_turns",
    "agent_tool_calls",
    "agent_commands",
    "agent_decisions",
    "agent_messages",
    "agent_resources",
    "agent_resource_versions",
    "agent_plan_reviews",
    "agent_uses",
    "agent_artifacts",
    "agent_deliveries",
    "agent_cleanups",
    "agent_presentation_state",
    "agent_limits",
    "agent_stream_head",
    "agent_events",
    "agent_retired_keys",
}


async def _insert(conn: Any, sql: str, params: tuple[Any, ...]) -> None:
    await conn.exec_driver_sql(sql, params)


async def _seed(conn: Any) -> dict[str, uuid.UUID]:
    """建立一条最小合法链路：会话→意图→输入→资源→执行→任务→尝试→命令。"""
    ids = {name: uuid.uuid4() for name in ("conversation", "intent", "resource", "execution", "task", "attempt")}
    ids["command"] = uuid.uuid4()

    await _insert(
        conn,
        "INSERT INTO agent_conversations (id, domain, title, revision, next_seq, use_revision)"
        " VALUES ($1, 'test', 'seed', 1, 0, 0)",
        (ids["conversation"],),
    )
    await _insert(
        conn,
        "INSERT INTO agent_intents (id, domain, source_module, request_key, initial_digest,"
        " conversation_id, accepted_seq, admission_seq, current_input_revision)"
        " VALUES ($1, 'test', 'seed', $2, $3, $4, 1, 1, 1)",
        (ids["intent"], f"key-{uuid.uuid4()}", DIGEST, ids["conversation"]),
    )
    await _insert(
        conn,
        "INSERT INTO agent_inputs (intent_id, revision, request, input_digest, model_snapshot,"
        " context_binding, resolved_manifest, resolved_at)"
        " VALUES ($1, 1, '{}'::jsonb, $2, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, now())",
        (ids["intent"], DIGEST),
    )
    await _insert(
        conn,
        "INSERT INTO agent_resources (id, domain, kind, current_revision, use_revision)"
        " VALUES ($1, 'test', 'artifact', 1, 0)",
        (ids["resource"],),
    )
    await _insert(
        conn,
        "INSERT INTO agent_resource_versions (resource_id, revision, payload, content_digest, availability)"
        " VALUES ($1, 1, '{}'::jsonb, $2, 'available')",
        (ids["resource"], DIGEST),
    )
    await _insert(
        conn,
        "INSERT INTO agent_executions (id, intent_id, execution_number, input_revision, plan_revision,"
        " plan, framework_version, projection, fence)"
        " VALUES ($1, $2, 1, 1, 1, '{\"nodes\": []}'::jsonb, 'test', '{}'::jsonb, 0)",
        (ids["execution"], ids["intent"]),
    )
    await _insert(
        conn,
        "INSERT INTO agent_tasks (id, execution_id, plan_node_id, role_key, plan_revision, depends_on, required)"
        " VALUES ($1, $2, NULL, 'main', 1, '[]'::jsonb, true)",
        (ids["task"], ids["execution"]),
    )
    await _insert(
        conn,
        "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index, native_run_refs,"
        " validity_digest, input_manifest, observation)"
        " VALUES ($1, $2, 1, 1, '[]'::jsonb, $3, '{}'::jsonb, '{}'::jsonb)",
        (ids["attempt"], ids["task"], DIGEST),
    )
    await _insert(
        conn,
        "INSERT INTO agent_commands (id, domain, kind, payload, payload_digest, handoff)"
        " VALUES ($1, 'test', 'answer_decision', '{}'::jsonb, $2, 'prepared')",
        (ids["command"], DIGEST),
    )
    return ids


async def _assert_rejected(conn: Any, sql: str, params: tuple[Any, ...]) -> None:
    """在 savepoint 内执行并断言数据库约束拒绝，不污染外层事务。"""
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await _insert(conn, sql, params)


async def test_agent_tables_registered_and_dao_wired() -> None:
    tables = {name for name in Base.metadata.tables if name.startswith("agent_")}
    assert tables == EXPECTED_AGENT_TABLES
    assert set(TABLE_DEFINITIONS) >= EXPECTED_AGENT_TABLES

    source = inspect.getsource(CacheManager.__init__)
    assert "self.agent_dao = AgentDao(self.engine)" in source


async def test_composite_fk_rejects_unknown_parent_revision(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            missing_parent = uuid.uuid4()
            await _assert_rejected(
                conn,
                "INSERT INTO agent_paths (id, revision, conversation_id, parent_path_id,"
                " parent_path_revision, inherited_refs)"
                " VALUES ($1, 1, $2, $3, 9, '[]'::jsonb)",
                (uuid.uuid4(), ids["conversation"], missing_parent),
            )
            await _insert(
                conn,
                "INSERT INTO agent_paths (id, revision, conversation_id, inherited_refs)"
                " VALUES ($1, 1, $2, '[]'::jsonb)",
                (uuid.uuid4(), ids["conversation"]),
            )
        finally:
            await tx.rollback()


async def test_same_key_is_scoped_by_domain(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            key = f"key-{uuid.uuid4()}"
            await _insert(
                conn,
                "INSERT INTO agent_retired_keys (domain, source_module, request_key, initial_digest,"
                " retired_at, reason)"
                " VALUES ('a', 'seed', $1, $2, now(), 'retired')",
                (key, DIGEST),
            )
            await _insert(
                conn,
                "INSERT INTO agent_retired_keys (domain, source_module, request_key, initial_digest,"
                " retired_at, reason)"
                " VALUES ('b', 'seed', $1, $2, now(), 'retired')",
                (key, DIGEST),
            )
            await _assert_rejected(
                conn,
                "INSERT INTO agent_retired_keys (domain, source_module, request_key, initial_digest,"
                " retired_at, reason)"
                " VALUES ('a', 'seed', $1, $2, now(), 'retired')",
                (key, DIGEST),
            )
        finally:
            await tx.rollback()


async def test_role_enabled_reference_requires_existing_version(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            await _insert(
                conn,
                "INSERT INTO agent_uses (resource_id, resource_revision, owner_kind, owner_id, active)"
                " VALUES ($1, 1, 'role_enabled', $2, true)",
                (ids["resource"], uuid.uuid4()),
            )
            await _assert_rejected(
                conn,
                "INSERT INTO agent_uses (resource_id, resource_revision, owner_kind, owner_id, active)"
                " VALUES ($1, 2, 'role_enabled', $2, true)",
                (ids["resource"], uuid.uuid4()),
            )
        finally:
            await tx.rollback()


async def test_artifact_tombstone_is_bidirectional(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            artifact_id = ids["resource"]  # 与资源版本 identity 一致（复合外键）
            live = (
                "INSERT INTO agent_artifacts (id, revision, execution_id, attempt_id,"
                " output_contract, content, markdown, source_refs, limitations, content_digest)"
                " VALUES ($1, 1, $2, $3, 'answer/1', $4::jsonb, $5, $6::jsonb, '[]'::jsonb, $7)"
            )
            await _assert_rejected(
                conn,
                "INSERT INTO agent_artifacts (id, revision, execution_id, attempt_id, output_contract,"
                " content, markdown, source_refs, limitations, content_digest)"
                " VALUES ($1, 1, $2, $3, 'answer/1', NULL, NULL, NULL, NULL, $4)",
                (artifact_id, ids["execution"], ids["attempt"], DIGEST),
            )
            await _assert_rejected(
                conn,
                "INSERT INTO agent_artifacts (id, revision, execution_id, attempt_id, output_contract,"
                " content, markdown, source_refs, limitations, content_digest, deleted_at)"
                " VALUES ($1, 2, $2, $3, 'answer/1', '{}'::jsonb, 'md', '[]'::jsonb, '[]'::jsonb, $4, now())",
                (artifact_id, ids["execution"], ids["attempt"], DIGEST),
            )
            await _insert(
                conn,
                live,
                (artifact_id, ids["execution"], ids["attempt"], "{}", "md", "[]", DIGEST),
            )
        finally:
            await tx.rollback()


async def test_delivery_snapshot_joint_constraint(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            artifact_id = ids["resource"]
            await _insert(
                conn,
                "INSERT INTO agent_artifacts (id, revision, execution_id, attempt_id,"
                " output_contract, content, markdown, source_refs, limitations, content_digest)"
                " VALUES ($1, 1, $2, $3, 'answer/1', '{}'::jsonb, 'md', '[]'::jsonb, '[]'::jsonb, $4)",
                (artifact_id, ids["execution"], ids["attempt"], DIGEST),
            )
            await _insert(
                conn,
                "INSERT INTO agent_commands (id, domain, kind, payload, payload_digest, handoff)"
                " VALUES ($1, 'test', 'export', '{}'::jsonb, $2, 'prepared')",
                (uuid.uuid4(), DIGEST),
            )
            delivery_sql = (
                "INSERT INTO agent_deliveries (operation_id, artifact_id, artifact_revision, format,"
                " destination, content_digest, source_availability, delivery_digest, revision, phase, status)"
                " VALUES ($1, $2, 1, 'md', '/tmp/out.md', $3, $4::jsonb, $5, 1, $6, $7)"
            )
            await _assert_rejected(
                conn,
                delivery_sql,
                (ids["command"], artifact_id, DIGEST, "[]", None, "settled", "published"),
            )
            await _assert_rejected(
                conn,
                delivery_sql,
                (ids["command"], artifact_id, DIGEST, "[]", DIGEST, "settled", "preparing"),
            )
            await _insert(
                conn,
                delivery_sql,
                (ids["command"], artifact_id, DIGEST, "[]", DIGEST, "settled", "published"),
            )
        finally:
            await tx.rollback()


async def test_input_retirement_rejects_json_null(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            await _assert_rejected(
                conn,
                "INSERT INTO agent_inputs (intent_id, revision, request, input_digest, retired_at)"
                " VALUES ($1, 2, 'null'::jsonb, $2, now())",
                (ids["intent"], DIGEST),
            )
            await _insert(
                conn,
                "INSERT INTO agent_inputs (intent_id, revision, request, input_digest, retired_at)"
                " VALUES ($1, 2, NULL, $2, now())",
                (ids["intent"], DIGEST),
            )
        finally:
            await tx.rollback()


async def test_execution_retirement_rejects_empty_plan(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            await _assert_rejected(
                conn,
                "INSERT INTO agent_executions (id, intent_id, execution_number, input_revision,"
                " plan_revision, plan, framework_version, fence, retired_at)"
                " VALUES ($1, $2, 2, 1, 1, '{\"nodes\": []}'::jsonb, 'test', 0, now())",
                (uuid.uuid4(), ids["intent"]),
            )
            await _insert(
                conn,
                "INSERT INTO agent_executions (id, intent_id, execution_number, input_revision,"
                " plan_revision, plan, framework_version, fence, retired_at)"
                " VALUES ($1, $2, 2, 1, 1, NULL, 'test', 0, now())",
                (uuid.uuid4(), ids["intent"]),
            )
        finally:
            await tx.rollback()


async def test_attempt_retirement_rejects_json_null(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            await _assert_rejected(
                conn,
                "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index,"
                " native_run_refs, validity_digest, input_manifest, retired_at)"
                " VALUES ($1, $2, 2, 1, '[]'::jsonb, $3, 'null'::jsonb, now())",
                (uuid.uuid4(), ids["task"], DIGEST),
            )
            await _insert(
                conn,
                "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index,"
                " native_run_refs, validity_digest, input_manifest, retired_at)"
                " VALUES ($1, $2, 2, 1, '[]'::jsonb, $3, NULL, now())",
                (uuid.uuid4(), ids["task"], DIGEST),
            )
        finally:
            await tx.rollback()


async def test_decision_answer_phase_command_joint(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            ids = await _seed(conn)
            base = (
                "INSERT INTO agent_decisions (id, execution_id, task_id, attempt_id, native_request_id,"
                " revision, created_event_seq, kind, question_key, question_params, options, scope,"
                " scope_digest, answer_schema, status, answer_command_id, answer_phase)"
                " VALUES ($1, $2, $3, $4, $5, 1, 0, 'tool_permission', 'q', '{}'::jsonb, '[]'::jsonb,"
                " '{}'::jsonb, $6, '{}'::jsonb, $7, $8, $9)"
            )
            await _assert_rejected(
                conn,
                base,
                (
                    uuid.uuid4(),
                    ids["execution"],
                    ids["task"],
                    ids["attempt"],
                    f"req-{uuid.uuid4()}",
                    DIGEST,
                    "answered",
                    None,
                    "recorded",
                ),
            )
            await _assert_rejected(
                conn,
                base,
                (
                    uuid.uuid4(),
                    ids["execution"],
                    ids["task"],
                    ids["attempt"],
                    f"req-{uuid.uuid4()}",
                    DIGEST,
                    "pending",
                    ids["command"],
                    "unanswered",
                ),
            )
            await _insert(
                conn,
                base,
                (
                    uuid.uuid4(),
                    ids["execution"],
                    ids["task"],
                    ids["attempt"],
                    f"req-{uuid.uuid4()}",
                    DIGEST,
                    "answered",
                    ids["command"],
                    "recorded",
                ),
            )
        finally:
            await tx.rollback()


async def test_stream_event_increments_tail_and_rejects_body_copy(test_engine: AsyncEngine) -> None:
    dao = AgentDao(test_engine)
    stream_key = f"stream-{uuid.uuid4()}"
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            first = await dao.append_event(
                event_key=f"event-{uuid.uuid4()}",
                domain="test",
                entity_id=uuid.uuid4(),
                entity_revision=1,
                kind="message.appended",
                payload=json_doc({"message_id": str(uuid.uuid4())}),
                stream_key=stream_key,
                conn=conn,
            )
            second = await dao.append_event(
                event_key=f"event-{uuid.uuid4()}",
                domain="test",
                entity_id=uuid.uuid4(),
                entity_revision=1,
                kind="message.appended",
                payload=json_doc({"message_id": str(uuid.uuid4())}),
                stream_key=stream_key,
                conn=conn,
            )
            assert (first, second) == (1, 2)

            with pytest.raises(ValueError, match="artifact.published"):
                async with conn.begin_nested():
                    await dao.append_event(
                        event_key=f"event-{uuid.uuid4()}",
                        domain="test",
                        entity_id=uuid.uuid4(),
                        entity_revision=1,
                        kind="artifact.published",
                        payload=json_doc(
                            {
                                "artifact_id": str(uuid.uuid4()),
                                "revision": 1,
                                "content_digest": DIGEST,
                                "content": {},
                            }
                        ),
                        stream_key=stream_key,
                        conn=conn,
                    )
        finally:
            await tx.rollback()


async def test_agent_dao_rejects_disposed_engine(test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    dao = AgentDao(test_engine)
    monkeypatch.setattr(engine_provider, "is_disposed", lambda _engine: True)
    with pytest.raises(EngineDisposedError):
        await dao.append_event(
            event_key=f"event-{uuid.uuid4()}",
            domain="test",
            entity_id=uuid.uuid4(),
            entity_revision=1,
            kind="message.appended",
            payload=json_doc({"message_id": str(uuid.uuid4())}),
        )


async def test_upsert_helper_uses_conflict_key(test_engine: AsyncEngine) -> None:
    dao = AgentDao(test_engine)
    key = f"stream-{uuid.uuid4()}"
    async with test_engine.connect() as conn:
        tx = await conn.begin()
        try:
            written = await dao._upsert_rows(
                "agent_stream_head",
                ["stream_key", "tail"],
                ["stream_key"],
                [{"stream_key": key, "tail": 0}, {"stream_key": key, "tail": 5}],
                conn=conn,
            )
            assert written == 1
            row = await conn.execute(
                sa.text("SELECT tail FROM agent_stream_head WHERE stream_key = :key"), {"key": key}
            )
            assert row.scalar_one() == 5
        finally:
            await tx.rollback()
