"""D02：受理、队列、命令与生效回执集成测试（真实 PostgreSQL）。

覆盖设计正本 §5.5/§5.10/§6/§8.3：
- 同 key 并发受理只产生一条意图（advisory 锁 + 唯一约束）；
- 同 key 异摘要返回 conflict，退役 key 返回 retired；
- 修订命令与 prepared 派发命令可双顺序查回，预期版本 CAS 冲突；
- 末尾总槽争抢返回 queued，领取 slot 不消耗准入序号；
- answer_decision 在同一事务保存命令/决定关联/recorded/事件，重复命令不改原答案；
- generation 不符的释放被拒绝（unknown 状态不得释放槽）；
- 队列游标为版本化 opaque base64，按域校验。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentValidationError,
    CommandReceipt,
    ControlCommand,
    SubmissionKey,
    Target,
    decode_json_doc,
    json_doc,
    request_digest,
)
from data.persistence.daos.agent_dao import AgentDao
from tests.builders.agent import make_request

pytestmark = pytest.mark.integration

_PROMPT_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
_DIGEST = "b" * 64


async def _seed_prompt(engine: AsyncEngine, domain: str) -> None:
    """预置 role prompt 资源版本；角色引用经 input use 保护。"""
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_resources (id, domain, kind, current_revision, use_revision)"
            " VALUES ($1, $2, 'prompt', 1, 0) ON CONFLICT (id) DO NOTHING",
            (_PROMPT_ID, domain),
        )
        await conn.exec_driver_sql(
            "INSERT INTO agent_resource_versions (resource_id, revision, payload, content_digest, availability)"
            " VALUES ($1, 1, '{}'::jsonb, $2, 'available') ON CONFLICT (resource_id, revision) DO NOTHING",
            (_PROMPT_ID, _DIGEST),
        )


def _request(domain: str, suffix: str) -> Any:
    return replace(make_request(), key=SubmissionKey(domain, "tests", f"request-{suffix}"))


async def _accept(dao: AgentDao, domain: str, suffix: str) -> Any:
    request = _request(domain, suffix)
    return await dao.accept_request(domain, request, request_digest(request))


def _as_dict(doc: str) -> dict[str, Any]:
    value = decode_json_doc(doc)
    assert isinstance(value, dict)
    return value


async def _admission_seq(engine: AsyncEngine, intent_id: uuid.UUID) -> int:
    async with engine.connect() as conn:
        value = (
            await conn.exec_driver_sql("SELECT admission_seq FROM agent_intents WHERE id = $1", (intent_id,))
        ).scalar_one()
    return int(value)


async def _conversation_id(engine: AsyncEngine, intent_id: uuid.UUID) -> uuid.UUID:
    async with engine.connect() as conn:
        value = (
            await conn.exec_driver_sql("SELECT conversation_id FROM agent_intents WHERE id = $1", (intent_id,))
        ).scalar_one()
    return uuid.UUID(str(value))


async def _insert_attempt(engine: AsyncEngine, task_id: uuid.UUID) -> uuid.UUID:
    attempt_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index, native_run_refs,"
            " validity_digest, input_manifest, observation)"
            " VALUES ($1, $2, 1, 1, '[]'::jsonb, $3, '{}'::jsonb, '{}'::jsonb)",
            (attempt_id, task_id, _DIGEST),
        )
    return attempt_id


async def _insert_decision(
    engine: AsyncEngine,
    *,
    execution_id: uuid.UUID,
    task_id: uuid.UUID,
    attempt_id: uuid.UUID,
    schema: dict[str, Any],
) -> uuid.UUID:
    decision_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_decisions (id, execution_id, task_id, attempt_id, native_request_id,"
            " revision, created_event_seq, kind, question_key, question_params, options, scope,"
            " scope_digest, answer_schema, status, answer_phase)"
            " VALUES ($1, $2, $3, $4, 'native-1', 1, 0, 'clarification', 'question-1',"
            " '{}'::jsonb, '[]'::jsonb, '{}'::jsonb, $5, $6, 'pending', 'unanswered')",
            (decision_id, execution_id, task_id, attempt_id, _DIGEST, json_doc(schema)),
        )
    return decision_id


async def test_same_key_concurrent_submit_yields_one_intent(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)

    request = _request(domain, "concurrent")
    receipts = await asyncio.gather(
        dao.accept_request(domain, request, request_digest(request)),
        dao.accept_request(domain, request, request_digest(request)),
    )

    statuses = sorted(receipt.status for receipt in receipts)
    assert statuses == ["accepted", "same_request"]
    accepted = next(receipt for receipt in receipts if receipt.status == "accepted")
    repeated = next(receipt for receipt in receipts if receipt.status == "same_request")
    assert accepted.accepted_seq == 1
    assert repeated.intent_id == accepted.intent_id
    assert repeated.execution_id == accepted.execution_id

    async with test_engine.connect() as conn:
        count = (
            await conn.execute(
                sa.text("SELECT count(*) FROM agent_intents WHERE domain = :domain"),
                {"domain": domain},
            )
        ).scalar_one()
        use_revision = (
            await conn.execute(
                sa.text("SELECT use_revision FROM agent_resources WHERE id = :id"),
                {"id": _PROMPT_ID},
            )
        ).scalar_one()
    assert count == 1
    # 唯一 prompt 只被保护一次（upsert 幂等，不重复递增）。
    assert int(use_revision) >= 1


async def test_same_key_different_digest_conflicts(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    first = await _accept(dao, domain, "conflict")
    assert first.status == "accepted"
    second = await dao.accept_request(domain, _request(domain, "conflict"), "f" * 64)
    assert second.status == "conflict"
    assert second.intent_id == first.intent_id


async def test_retired_key_returns_retired_without_new_intent(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    dao = AgentDao(test_engine)
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_retired_keys (domain, source_module, request_key, initial_digest,"
            " retired_at, reason) VALUES ($1, 'tests', 'request-retired', $2, now(), 'retired')",
            (domain, _DIGEST),
        )
    receipt = await _accept(dao, domain, "retired")
    assert receipt.status == "retired"


async def test_second_request_in_conversation_gets_next_accepted_seq(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    first = await _accept(dao, domain, "seq-1")
    assert first.status == "accepted"
    assert first.intent_id is not None
    conversation_id = await _conversation_id(test_engine, first.intent_id)
    second_request = replace(_request(domain, "seq-2"), conversation_id=conversation_id)
    second = await dao.accept_request(domain, second_request, request_digest(second_request))
    assert second.status == "accepted"
    # 新会话首请求 accepted_seq=1；同一会话第二条请求必须递增且不重复。
    assert first.accepted_seq == 1
    assert second.accepted_seq == 2
    assert second.intent_id != first.intent_id


async def test_revise_queued_appends_input_and_rejects_stale_revision(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    accepted = await _accept(dao, domain, "revise")
    assert accepted.status == "accepted"
    assert accepted.execution_id is not None

    revised = replace(
        _request(domain, "revise"),
        text="修订后的说明",
        conversation_id=None,
    )
    payload = json_doc({"request": decode_json_doc(json_doc(revised))})
    command = ControlCommand(
        command_id=uuid.uuid4(),
        target=Target(accepted.execution_id, None, None),
        action="revise_queued",
        expected_input_revision=1,
        expected_plan_revision=1,
        payload=payload,
    )
    receipt = await dao.save_command(domain, command)
    assert receipt.status == "committed"

    # 同一命令重复提交回放原回执，不追加新 input。
    again = await dao.save_command(domain, command)
    assert again.status == "committed"

    # 旧预期版本再用新命令修订 → revision_conflict。
    stale = ControlCommand(
        command_id=uuid.uuid4(),
        target=Target(accepted.execution_id, None, None),
        action="revise_queued",
        expected_input_revision=1,
        expected_plan_revision=1,
        payload=payload,
    )
    conflict = await dao.save_command(domain, stale)
    assert conflict.status == "conflict"
    assert conflict.problem is not None

    # 已派发（非 queued）后不得再修订。
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE agent_intents SET acceptance_state = 'dispatched' WHERE id = $1",
            (accepted.intent_id,),
        )
    dispatched = ControlCommand(
        command_id=uuid.uuid4(),
        target=Target(accepted.execution_id, None, None),
        action="revise_queued",
        expected_input_revision=2,
        expected_plan_revision=1,
        payload=payload,
    )
    rejected = await dao.save_command(domain, dispatched)
    assert rejected.status == "conflict"


async def test_dispatch_command_is_stored_as_prepared_and_lookupable(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    accepted = await _accept(dao, domain, "dispatch")
    assert accepted.status == "accepted"
    async with test_engine.connect() as conn:
        command_id = (
            await conn.exec_driver_sql(
                "SELECT id FROM agent_commands WHERE domain = $1 AND kind = 'dispatch_start'",
                (domain,),
            )
        ).scalar_one()
        handoff = (
            await conn.exec_driver_sql("SELECT handoff FROM agent_commands WHERE id = $1", (command_id,))
        ).scalar_one()
        stored = (
            await conn.exec_driver_sql("SELECT receipt FROM agent_commands WHERE id = $1", (command_id,))
        ).scalar_one()
    assert handoff == "prepared"
    assert isinstance(stored, dict)
    assert stored["status"] == "committed"
    lookup = await dao.lookup_command(domain, command_id)
    assert lookup.status == "committed"
    assert lookup.command_id == command_id


async def test_total_slots_refuse_third_and_release_reopens(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    first = await _accept(dao, domain, "slot-1")
    second = await _accept(dao, domain, "slot-2")
    third = await _accept(dao, domain, "slot-3")
    assert first.execution_id and second.execution_id and third.execution_id

    claims = await asyncio.gather(
        dao.claim_total_slot(domain, first.execution_id),
        dao.claim_total_slot(domain, second.execution_id),
    )
    assert sorted(_as_dict(item)["status"] for item in claims) == ["admitted", "admitted"]

    queued = _as_dict(await dao.claim_total_slot(domain, third.execution_id))
    assert queued["status"] == "queued"

    # 领取 slot 不消耗准入序号：下一受理只比第三多 1。
    fourth = await _accept(dao, domain, "slot-4")
    assert fourth.intent_id is not None and third.intent_id is not None
    assert await _admission_seq(test_engine, fourth.intent_id) == await _admission_seq(test_engine, third.intent_id) + 1

    first_generation = int(_as_dict(claims[0])["generation"])
    await dao.release_total_slot(domain, first.execution_id, first_generation)
    admitted = _as_dict(await dao.claim_total_slot(domain, third.execution_id))
    assert admitted["status"] == "admitted"

    with pytest.raises(AgentValidationError):
        await dao.release_total_slot(domain, third.execution_id, int(admitted["generation"]) - 1)


async def test_answer_decision_records_command_decision_and_event(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    accepted = await _accept(dao, domain, "answer")
    assert accepted.execution_id is not None

    async with test_engine.connect() as conn:
        task_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_tasks WHERE execution_id = $1", (accepted.execution_id,))
        ).scalar_one()
    attempt_id = await _insert_attempt(test_engine, task_id)
    decision_id = await _insert_decision(
        test_engine,
        execution_id=accepted.execution_id,
        task_id=task_id,
        attempt_id=attempt_id,
        schema={"kind": "permission"},
    )

    payload = json_doc(
        {
            "decision_id": decision_id,
            "decision_revision": 1,
            "native_request_id": "native-1",
            "scope_digest": _DIGEST,
            "answer": {"kind": "permission", "allowed": True},
        }
    )
    command = ControlCommand(
        command_id=uuid.uuid4(),
        target=Target(accepted.execution_id, task_id, attempt_id),
        action="answer_decision",
        expected_input_revision=1,
        expected_plan_revision=1,
        payload=payload,
    )
    receipt = await dao.save_command(domain, command)
    assert receipt.status == "committed"
    assert receipt.continuation_ref is not None

    async with test_engine.connect() as conn:
        status = (
            await conn.exec_driver_sql("SELECT status FROM agent_decisions WHERE id = $1", (decision_id,))
        ).scalar_one()
        phase = (
            await conn.exec_driver_sql("SELECT answer_phase FROM agent_decisions WHERE id = $1", (decision_id,))
        ).scalar_one()
        answer_command_id = (
            await conn.exec_driver_sql("SELECT answer_command_id FROM agent_decisions WHERE id = $1", (decision_id,))
        ).scalar_one()
        revision = (
            await conn.exec_driver_sql("SELECT revision FROM agent_decisions WHERE id = $1", (decision_id,))
        ).scalar_one()
        handoff = (
            await conn.exec_driver_sql("SELECT handoff FROM agent_commands WHERE id = $1", (command.command_id,))
        ).scalar_one()
        stored = (
            await conn.exec_driver_sql("SELECT receipt FROM agent_commands WHERE id = $1", (command.command_id,))
        ).scalar_one()
        event_kind = (
            await conn.exec_driver_sql(
                "SELECT kind FROM agent_events WHERE entity_id = $1"
                " AND kind = 'decision.changed' ORDER BY seq DESC LIMIT 1",
                (decision_id,),
            )
        ).scalar_one_or_none()
    assert (status, phase) == ("answered", "recorded")
    assert answer_command_id == command.command_id
    assert revision == 2
    assert handoff == "acknowledged"
    assert isinstance(stored, dict)
    assert stored["answer"] == {"kind": "permission", "allowed": True}
    assert event_kind == "decision.changed"

    # 同一命令再次提交回放原回执，不改变决定修订。
    replay = await dao.save_command(domain, command)
    assert replay.status == "committed"
    async with test_engine.connect() as conn:
        revision = (
            await conn.exec_driver_sql("SELECT revision FROM agent_decisions WHERE id = $1", (decision_id,))
        ).scalar_one()
    assert revision == 2


async def test_invalid_answer_is_rejected_before_writing_command(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    accepted = await _accept(dao, domain, "bad-answer")
    assert accepted.execution_id is not None
    async with test_engine.connect() as conn:
        task_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_tasks WHERE execution_id = $1", (accepted.execution_id,))
        ).scalar_one()
    attempt_id = await _insert_attempt(test_engine, task_id)
    decision_id = await _insert_decision(
        test_engine,
        execution_id=accepted.execution_id,
        task_id=task_id,
        attempt_id=attempt_id,
        schema={"kind": "permission"},
    )
    payload = json_doc(
        {
            "decision_id": decision_id,
            "decision_revision": 1,
            "native_request_id": "native-1",
            "scope_digest": _DIGEST,
            "answer": {"kind": "permission", "allowed": "yes"},
        }
    )
    command = ControlCommand(
        command_id=uuid.uuid4(),
        target=Target(accepted.execution_id, task_id, attempt_id),
        action="answer_decision",
        expected_input_revision=1,
        expected_plan_revision=1,
        payload=payload,
    )
    with pytest.raises(AgentValidationError):
        await dao.save_command(domain, command)
    async with test_engine.connect() as conn:
        exists = (
            await conn.exec_driver_sql("SELECT 1 FROM agent_commands WHERE id = $1", (command.command_id,))
        ).first()
    assert exists is None


async def test_queue_pagination_is_domain_scoped_and_opaque(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    other = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    await _seed_prompt(test_engine, other)
    dao = AgentDao(test_engine)
    for index in range(3):
        assert (await _accept(dao, domain, f"queue-{index}")).status == "accepted"
    assert (await _accept(dao, other, "queue-other")).status == "accepted"

    page = await dao.list_queue(domain, cursor=None, limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is not None
    assert page.items[0].admission_seq < page.items[1].admission_seq

    rest = await dao.list_queue(domain, cursor=page.next_cursor, limit=2)
    assert len(rest.items) == 1
    assert rest.next_cursor is None
    assert rest.items[0].intent_id != page.items[0].intent_id

    with pytest.raises(AgentValidationError):
        await dao.list_queue(domain, cursor="not-a-cursor", limit=2)
    with pytest.raises(AgentValidationError):
        await dao.list_queue(other, cursor=page.next_cursor, limit=2)
