"""派发前澄清决定（decision_kind=user_choice）的写入与消费集成测试。

真实 embedded PG 上验证：

- ``save_clarification_decision``：无轮决定可落库（``round_id`` 为空），
  同问题幂等查回、批次序号递增、``answer_schema`` 与 UI 文本答复对齐；
- 无轮决定进入「待答复」阶段；答复后不进入交付/消费队列（其消费由下一轮受理
  完成）；
- ``consume_clarification``：只消费真实答复一次，``answer_consumed_at`` 只落一次；
- 有界有效期：过期问题拒绝答复（``POLICY_DENIED``），``expires_at`` 为空拒绝
  创建（``INPUT_INVALID``）；执行已绑定轮次后无轮问题不再可答。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text, update

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    BindActualInputRequest,
    ClarificationFact,
    Mode,
    Purpose,
    SaveAnswerRequest,
    SaveClarificationDecisionRequest,
    derive_round_id,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentCommand, AgentDecision, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER_SCOPE = "u1"


def _wire(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> AgentDao:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


async def _seed_execution(
    dao: AgentDao,
    *,
    session_id: str,
    business_request_id: str,
    workflow_id: str,
) -> Any:
    """受理但未绑定轮次：执行 ``current_round_id`` 为空（派发前时点）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造受理前置会话
        await conn.execute(
            insert(AgentSession).values(user_scope=_USER_SCOPE, session_id=session_id, title=f"session {session_id}")
        )
    manifest = json_doc(
        {
            "schema_version": 1,
            "source_module": "ui",
            "business_request_id": business_request_id,
            "mode": "discuss",
            "purpose": "concept",
            "text": "研究三家公司",
        }
    )
    return await dao.accept_request(
        AcceptRequest(
            user_scope=_USER_SCOPE,
            session_id=session_id,
            source_module="ui",
            business_request_id=business_request_id,
            client_command_id=f"client-{business_request_id}",
            expected_revision=0,
            node_id="root",
            workflow_id=workflow_id,
            mode=Mode.DISCUSS,
            purpose=Purpose.CONCEPT,
            text="研究三家公司",
            manifest_payload=manifest,
            source_display_name="用户消息",
        )
    )


def _clarification_request(
    receipt: Any,
    *,
    question: str = "你关注行业景气还是个股估值？",
    options: tuple[str, ...] = ("行业景气", "个股估值"),
    impact: str = "决定后续分析口径",
    expires_at: datetime | None = None,
) -> SaveClarificationDecisionRequest:
    return SaveClarificationDecisionRequest(
        user_scope=_USER_SCOPE,
        session_id=receipt.session_id,
        execution_id=receipt.execution_id,
        node_id="root",
        question_text=question,
        options=options,
        impact=impact,
        expires_at=_wire(expires_at or datetime.now(UTC) + timedelta(days=1)),
    )


async def _bind_round(dao: AgentDao, receipt: Any, *, workflow_id: str = "wf-1") -> str:
    """把执行绑定到真实轮次（``current_round_id`` 非空，派发前时点已结束）。"""

    bound = await dao.bind_actual_input(
        BindActualInputRequest(
            user_scope=_USER_SCOPE,
            session_id=receipt.session_id,
            execution_id=receipt.execution_id,
            node_id="root",
            workflow_id=receipt.workflow_id,
            workflow_code_version="wcv-1",
            run_epoch=1,
            expected_input_revision=1,
            frozen_manifest=json_doc(
                {
                    "payload_version": 2,
                    "execution_id": receipt.execution_id,
                    "round_id": derive_round_id(
                        user_scope=_USER_SCOPE,
                        execution_id=receipt.execution_id,
                        workflow_id=receipt.workflow_id,
                    ),
                    "binding_identity": {
                        "user_scope": _USER_SCOPE,
                        "session_id": receipt.session_id,
                        "workflow_code_version": "wcv-1",
                        "run_epoch": 1,
                        "expected_input_revision": 1,
                    },
                }
            ),
            actual_input=json_doc({"schema_version": 1, "kind": "actual_input"}),
        )
    )
    return bound.round_id


class TestSaveClarificationDecision:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_creates_round_less_decision_with_text_answer_schema(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")

        fact = await dao.save_clarification_decision(_clarification_request(receipt))

        assert isinstance(fact, ClarificationFact)
        assert fact.revision == 1
        assert fact.presentation_batch_seq == 1
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            row = (
                await conn.execute(
                    sa.select(
                        AgentDecision.round_id,
                        AgentDecision.node_id,
                        AgentDecision.decision_kind,
                        AgentDecision.question_payload,
                        AgentDecision.answer_schema,
                    ).where(AgentDecision.decision_id == fact.decision_id)
                )
            ).one()
        assert row.round_id is None
        assert row.node_id == "root"
        assert row.decision_kind == "user_choice"
        assert row.question_payload["question"] == "你关注行业景气还是个股估值？"
        assert row.question_payload["options"] == ["行业景气", "个股估值"]
        assert row.answer_schema["required"] == ["text"]
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_replay_returns_same_fact_and_new_question_bumps_batch(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        request = _clarification_request(receipt)

        first = await dao.save_clarification_decision(request)
        replay = await dao.save_clarification_decision(request)
        second = await dao.save_clarification_decision(_clarification_request(receipt, question="研究哪段时间？"))

        assert replay == first
        assert second.decision_id != first.decision_id
        assert (first.presentation_batch_seq, second.presentation_batch_seq) == (1, 2)
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            rows = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentDecision)
                    .where(AgentDecision.execution_id == receipt.execution_id)
                )
            ).scalar_one()
        assert rows == 2
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unbounded_expiry_is_input_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        request = _clarification_request(receipt)
        request = replace(request, expires_at="")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_clarification_decision(request)

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_dispatched_execution_is_policy_denied(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        bound_round = await _bind_round(dao, receipt)
        assert bound_round

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_clarification_decision(_clarification_request(receipt))

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        await dao.engine.dispose()  # type: ignore[union-attr]


class TestPendingAndConsumeClarification:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_round_less_answer_is_pending_then_consumed_once(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        fact = await dao.save_clarification_decision(_clarification_request(receipt))

        pending = await dao.read_pending_answers(user_scope=_USER_SCOPE)
        assert [(item.decision_id, item.stage, item.round_id) for item in pending.items] == [
            (fact.decision_id, "answer", None)
        ]

        answer = await dao.save_answer(
            SaveAnswerRequest(
                user_scope=_USER_SCOPE,
                decision_id=fact.decision_id,
                client_command_id="ans-clarify",
                expected_decision_revision=1,
                expected_execution_id=receipt.execution_id,
                expected_round_id=None,
                answer_payload=json_doc({"text": "行业景气"}),
            )
        )

        # 已答复的无轮决定不进入交付/消费队列（避免永久「待交付」假象）。
        after_answer = await dao.read_pending_answers(user_scope=_USER_SCOPE)
        assert after_answer.items == ()

        consumed = await dao.consume_clarification(user_scope=_USER_SCOPE, session_id=receipt.session_id)
        assert [item.decision_id for item in consumed] == [fact.decision_id]
        assert consumed[0].question_text == "你关注行业景气还是个股估值？"
        assert consumed[0].answer_text == "行业景气"
        replay = await dao.consume_clarification(user_scope=_USER_SCOPE, session_id=receipt.session_id)
        assert replay == ()
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            decision_row = (
                await conn.execute(
                    sa.select(AgentDecision.answer_consumed_at).where(AgentDecision.decision_id == fact.decision_id)
                )
            ).one()
            command_row = (
                await conn.execute(
                    sa.select(AgentCommand.consumption_state).where(
                        AgentCommand.command_record_id == answer.command_record_id
                    )
                )
            ).one()
        assert decision_row.answer_consumed_at is not None
        assert command_row.consumption_state == "consumed"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_expired_question_rejects_answer(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        fact = await dao.save_clarification_decision(_clarification_request(receipt))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                update(AgentDecision)
                .where(AgentDecision.decision_id == fact.decision_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_answer(
                SaveAnswerRequest(
                    user_scope=_USER_SCOPE,
                    decision_id=fact.decision_id,
                    client_command_id="ans-expired",
                    expected_decision_revision=1,
                    expected_execution_id=receipt.execution_id,
                    expected_round_id=None,
                    answer_payload=json_doc({"text": "行业景气"}),
                )
            )

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert await dao.consume_clarification(user_scope=_USER_SCOPE, session_id=receipt.session_id) == ()
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unanswered_clarification_is_not_consumed(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        await dao.save_clarification_decision(_clarification_request(receipt))

        consumed = await dao.consume_clarification(user_scope=_USER_SCOPE, session_id=receipt.session_id)

        assert consumed == ()
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_restore_returns_answer_to_unconsumed_once_and_is_idempotent(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await _seed_execution(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        fact = await dao.save_clarification_decision(_clarification_request(receipt))
        answer = await dao.save_answer(
            SaveAnswerRequest(
                user_scope=_USER_SCOPE,
                decision_id=fact.decision_id,
                client_command_id="ans-restore",
                expected_decision_revision=1,
                expected_execution_id=receipt.execution_id,
                expected_round_id=None,
                answer_payload=json_doc({"text": "行业景气"}),
            )
        )
        consumed = await dao.consume_clarification(user_scope=_USER_SCOPE, session_id=receipt.session_id)
        assert [item.decision_id for item in consumed] == [fact.decision_id]

        restored = await dao.restore_clarification_consumption(
            user_scope=_USER_SCOPE,
            session_id=receipt.session_id,
            decision_ids=(fact.decision_id,),
        )
        assert restored == 1
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            decision_row = (
                await conn.execute(
                    sa.select(AgentDecision.answer_consumed_at).where(AgentDecision.decision_id == fact.decision_id)
                )
            ).one()
            command_row = (
                await conn.execute(
                    sa.select(AgentCommand.consumption_state, AgentCommand.consumed_at).where(
                        AgentCommand.command_record_id == answer.command_record_id
                    )
                )
            ).one()
        assert decision_row.answer_consumed_at is None
        assert command_row.consumption_state == "not_consumed"
        assert command_row.consumed_at is None

        assert (
            await dao.restore_clarification_consumption(
                user_scope=_USER_SCOPE,
                session_id=receipt.session_id,
                decision_ids=(fact.decision_id,),
            )
            == 0
        )
        assert (
            await dao.restore_clarification_consumption(
                user_scope=_USER_SCOPE,
                session_id=receipt.session_id,
                decision_ids=(fact.decision_id, "decision-unknown"),
            )
            == 0
        )

        reconsume = await dao.consume_clarification(user_scope=_USER_SCOPE, session_id=receipt.session_id)
        assert [item.decision_id for item in reconsume] == [fact.decision_id]
        assert reconsume[0].answer_text == "行业景气"
        await dao.engine.dispose()  # type: ignore[union-attr]
