"""D04：模型回合与工具效果回执（真实 PostgreSQL）。

覆盖 §7.3/§4.5-D 的 CAS 回执：prepared→in_flight→committed/unknown/failed、
同 invocation 同输入只复用、异输入/异身份冲突、unknown 不得退回未执行、
committed 响应可重放、retired 不得复活且不返回正文。
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentValidationError, JsonDoc, SubmissionKey, json_doc, request_digest, validate_request
from data.persistence.daos.agent_dao import AgentDao
from tests.builders.agent import make_request

pytestmark = pytest.mark.integration

_PROMPT_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
_DIGEST = "c" * 64
_OTHER_DIGEST = "d" * 64


async def _seed_prompt(engine: AsyncEngine, domain: str) -> None:
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


async def _accept(engine: AsyncEngine, dao: AgentDao, domain: str) -> tuple[uuid.UUID, uuid.UUID]:
    """受理一个请求并补一枚 attempt；返回 (execution_id, attempt_id)。"""
    request = replace(make_request(), key=SubmissionKey(domain, "tests", f"receipt-{uuid.uuid4()}"))
    validate_request(request)
    receipt = await dao.accept_request(domain, request, request_digest(request))
    assert receipt.status == "accepted"
    async with engine.connect() as conn:
        execution_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_executions WHERE intent_id = $1", (receipt.intent_id,))
        ).scalar_one()
        task_id = (
            await conn.exec_driver_sql("SELECT id FROM agent_tasks WHERE execution_id = $1", (execution_id,))
        ).scalar_one()
    attempt_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO agent_attempts (id, task_id, attempt_number, segment_index, native_run_refs,"
            " validity_digest, input_manifest, observation)"
            " VALUES ($1, $2, 1, 1, '[]'::jsonb, $3, '{}'::jsonb, '{}'::jsonb)",
            (attempt_id, task_id, _DIGEST),
        )
    return uuid.UUID(str(execution_id)), attempt_id


def _model_payload(
    execution_id: uuid.UUID,
    attempt_id: uuid.UUID,
    state: str,
    *,
    invocation: str = "invocation-1",
    input_digest: str = _DIGEST,
    response: dict[str, object] | None = None,
    ledger_refs: list[object] | None = None,
    **overrides: object,
) -> JsonDoc:
    payload: dict[str, object] = {
        "state": state,
        "execution_id": execution_id,
        "attempt_id": attempt_id,
        "turn_number": 1,
        "native_owner_id": "owner-1",
        "native_task_id": "native-task-1",
        "native_run_id": f"native-run-{execution_id}",
        "native_event_ref": f"event-{invocation}",
        "request_position": 1,
        "purpose": "main",
        "input_digest": input_digest,
        "ledger_refs": ledger_refs or [],
    }
    if response is not None:
        payload["response"] = response
    payload.update(overrides)
    return json_doc(payload)


def _tool_payload(
    execution_id: uuid.UUID,
    attempt_id: uuid.UUID,
    state: str,
    *,
    effect_key: str = "effect-1",
    input_digest: str = _DIGEST,
    response: dict[str, object] | None = None,
    **overrides: object,
) -> JsonDoc:
    payload: dict[str, object] = {
        "state": state,
        "execution_id": execution_id,
        "attempt_id": attempt_id,
        "native_owner_id": "owner-1",
        "native_task_id": "native-task-1",
        "native_run_id": "native-run-1",
        "native_event_ref": f"event-{effect_key}",
        "tool_call_id": "call-1",
        "tool_ref": {"name": "search_news", "schema_revision": "1"},
        "input_digest": input_digest,
    }
    if response is not None:
        payload["response"] = response
    payload.update(overrides)
    return json_doc(payload)


_COMMITTED_RESPONSE: dict[str, object] = {
    "message": {"role": "assistant", "content": "已整理资料缺口", "tool_calls": []},
    "finish_reason": "final",
}
_TOOL_RESPONSE: dict[str, object] = {"items": [{"title": "样本新闻"}], "status": "ok"}
_LEDGER_REFS: list[object] = [{"kind": "model", "attempt_id": "ledger-attempt-1", "amount": "0.01"}]


async def test_model_turn_persists_committed_and_replays_without_reoutbound(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    execution_id, attempt_id = await _accept(test_engine, dao, domain)
    invocation = "invocation-1"

    await dao.save_model_turn(invocation, "prepared", _model_payload(execution_id, attempt_id, "prepared"))
    prepared = await dao.read_model_turn(invocation)
    assert prepared is not None
    assert '"state":"prepared"' in prepared
    assert '"response":null' in prepared

    await dao.save_model_turn(invocation, "prepared", _model_payload(execution_id, attempt_id, "in_flight"))
    await dao.save_model_turn(
        invocation,
        "in_flight",
        _model_payload(
            execution_id,
            attempt_id,
            "committed",
            response=_COMMITTED_RESPONSE,
            ledger_refs=_LEDGER_REFS,
        ),
    )

    committed = await dao.read_model_turn(invocation)
    assert committed is not None
    assert '"state":"committed"' in committed
    assert '"content":"已整理资料缺口"' in committed
    assert '"attempt_id":"ledger-attempt-1"' in committed

    # committed 同输入同结果重放不产生第二次出站；读回内容不变。
    await dao.save_model_turn(
        invocation,
        "committed",
        _model_payload(
            execution_id,
            attempt_id,
            "committed",
            response=_COMMITTED_RESPONSE,
            ledger_refs=_LEDGER_REFS,
        ),
    )
    assert await dao.read_model_turn(invocation) == committed

    # 同 invocation 异输入摘要冲突，不覆盖已保存响应。
    with pytest.raises(AgentValidationError):
        await dao.save_model_turn(
            invocation,
            "committed",
            _model_payload(execution_id, attempt_id, "committed", input_digest=_OTHER_DIGEST),
        )
    assert await dao.read_model_turn(invocation) == committed
    assert await dao.read_model_turn("missing-invocation") is None


async def test_model_turn_rejects_identity_and_position_conflicts(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    execution_id, attempt_id = await _accept(test_engine, dao, domain)

    await dao.save_model_turn("invocation-a", "prepared", _model_payload(execution_id, attempt_id, "prepared"))
    with pytest.raises(AgentValidationError):
        await dao.save_model_turn(
            "invocation-b",
            "prepared",
            _model_payload(execution_id, attempt_id, "prepared"),
        )
    with pytest.raises(AgentValidationError):
        await dao.save_model_turn(
            "invocation-a",
            "prepared",
            _model_payload(execution_id, attempt_id, "prepared", native_run_id="native-run-3"),
        )

    # 已进入 in_flight 后不得回退 prepared。
    await dao.save_model_turn("invocation-a", "prepared", _model_payload(execution_id, attempt_id, "in_flight"))
    with pytest.raises(AgentValidationError):
        await dao.save_model_turn("invocation-a", "in_flight", _model_payload(execution_id, attempt_id, "prepared"))

    # committed 缺完整响应不得成功。
    with pytest.raises(AgentValidationError):
        await dao.save_model_turn("invocation-a", "in_flight", _model_payload(execution_id, attempt_id, "committed"))


async def test_model_turn_unknown_stays_unknown_until_verified(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    execution_id, attempt_id = await _accept(test_engine, dao, domain)

    await dao.save_model_turn("invocation-u", "prepared", _model_payload(execution_id, attempt_id, "prepared"))
    await dao.save_model_turn("invocation-u", "prepared", _model_payload(execution_id, attempt_id, "in_flight"))
    await dao.save_model_turn("invocation-u", "in_flight", _model_payload(execution_id, attempt_id, "unknown"))
    unknown = await dao.read_model_turn("invocation-u")
    assert unknown is not None
    assert '"state":"unknown"' in unknown
    assert '"response":null' in unknown

    for target in ("prepared", "in_flight"):
        with pytest.raises(AgentValidationError):
            await dao.save_model_turn("invocation-u", "unknown", _model_payload(execution_id, attempt_id, target))

    # 只有拿到确切响应后才允许从 unknown 收敛为 committed。
    await dao.save_model_turn(
        "invocation-u",
        "unknown",
        _model_payload(execution_id, attempt_id, "committed", response=_COMMITTED_RESPONSE),
    )
    verified = await dao.read_model_turn("invocation-u")
    assert verified is not None
    assert '"state":"committed"' in verified

    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE agent_model_turns SET retired_at = now(), response = NULL WHERE invocation_id = $1",
            ("invocation-u",),
        )
    retired = await dao.read_model_turn("invocation-u")
    assert retired is not None
    assert '"state":"retired"' in retired
    assert '"response":null' in retired
    with pytest.raises(AgentValidationError):
        await dao.save_model_turn(
            "invocation-u",
            "committed",
            _model_payload(execution_id, attempt_id, "committed", response=_COMMITTED_RESPONSE),
        )


async def test_tool_call_persists_committed_and_rejects_different_input(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    execution_id, attempt_id = await _accept(test_engine, dao, domain)
    effect_key = "effect-1"

    await dao.save_tool_call(effect_key, "prepared", _tool_payload(execution_id, attempt_id, "prepared"))
    await dao.save_tool_call(effect_key, "prepared", _tool_payload(execution_id, attempt_id, "in_flight"))
    await dao.save_tool_call(
        effect_key, "in_flight", _tool_payload(execution_id, attempt_id, "committed", response=_TOOL_RESPONSE)
    )

    committed = await dao.read_tool_call(effect_key)
    assert committed is not None
    assert '"state":"committed"' in committed
    assert '"status":"ok"' in committed

    # committed 响应可重放；同 key 异输入拒绝；异 tool_call_id 拒绝。
    await dao.save_tool_call(
        effect_key, "committed", _tool_payload(execution_id, attempt_id, "committed", response=_TOOL_RESPONSE)
    )
    assert await dao.read_tool_call(effect_key) == committed
    with pytest.raises(AgentValidationError):
        await dao.save_tool_call(
            effect_key,
            "committed",
            _tool_payload(execution_id, attempt_id, "committed", input_digest=_OTHER_DIGEST, response=_TOOL_RESPONSE),
        )
    with pytest.raises(AgentValidationError):
        await dao.save_tool_call(
            effect_key,
            "committed",
            _tool_payload(execution_id, attempt_id, "committed", tool_call_id="call-2", response=_TOOL_RESPONSE),
        )
    assert await dao.read_tool_call("missing-effect") is None


async def test_tool_call_unknown_cannot_become_unexecuted_and_retired_is_terminal(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    execution_id, attempt_id = await _accept(test_engine, dao, domain)
    effect_key = "effect-u"

    await dao.save_tool_call(
        effect_key, "prepared", _tool_payload(execution_id, attempt_id, "prepared", effect_key=effect_key)
    )
    await dao.save_tool_call(
        effect_key, "prepared", _tool_payload(execution_id, attempt_id, "in_flight", effect_key=effect_key)
    )
    await dao.save_tool_call(
        effect_key, "in_flight", _tool_payload(execution_id, attempt_id, "unknown", effect_key=effect_key)
    )
    unknown = await dao.read_tool_call(effect_key)
    assert unknown is not None
    assert '"state":"unknown"' in unknown

    for target in ("prepared", "in_flight"):
        with pytest.raises(AgentValidationError):
            await dao.save_tool_call(
                effect_key,
                "unknown",
                _tool_payload(execution_id, attempt_id, target, effect_key=effect_key),
            )

    await dao.save_tool_call(
        effect_key,
        "unknown",
        _tool_payload(execution_id, attempt_id, "committed", effect_key=effect_key, response=_TOOL_RESPONSE),
    )
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE agent_tool_calls SET retired_at = now(), response = NULL WHERE effect_key = $1",
            (effect_key,),
        )
    retired = await dao.read_tool_call(effect_key)
    assert retired is not None
    assert '"state":"retired"' in retired
    assert '"response":null' in retired
    with pytest.raises(AgentValidationError):
        await dao.save_tool_call(
            effect_key,
            "committed",
            _tool_payload(execution_id, attempt_id, "committed", effect_key=effect_key, response=_TOOL_RESPONSE),
        )


async def test_receipt_payloads_are_closed_and_typed(test_engine: AsyncEngine) -> None:
    domain = f"domain-{uuid.uuid4()}"
    await _seed_prompt(test_engine, domain)
    dao = AgentDao(test_engine)
    execution_id, attempt_id = await _accept(test_engine, dao, domain)

    invalid_payloads = (
        _model_payload(execution_id, attempt_id, "bogus"),
        _model_payload(execution_id, attempt_id, "prepared", input_digest="not-a-digest"),
        _model_payload(execution_id, attempt_id, "prepared", request_position=0),
        _tool_payload(execution_id, attempt_id, "prepared", tool_ref={}),
        _tool_payload(execution_id, attempt_id, "prepared", input_digest="not-a-digest"),
    )
    for index, payload in enumerate(invalid_payloads):
        with pytest.raises(AgentValidationError):
            if index < 3:
                await dao.save_model_turn(f"invalid-{index}", "prepared", payload)
            else:
                await dao.save_tool_call(f"invalid-{index}", "prepared", payload)

    with pytest.raises(AgentValidationError):
        await dao.save_model_turn(
            "invalid-expected", "speculative", _model_payload(execution_id, attempt_id, "prepared")
        )
