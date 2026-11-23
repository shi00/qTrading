"""跨会话待处理聚合与答复命令读取集成测试（P1-05，P1-05 残余②）。

真实 embedded PG 上验证 ``read_pending_answers`` / ``read_answer_command`` 的 SQL 语义：

- 三态聚合：待答复（未绑定答复命令）/ 待交付（命令非 delivered）/ 待消费
  （已 delivered 未 consumed），跨会话合并，只取仍可操作事实；
- 单类 ``limit`` + 1 判截断（``truncated`` 不把上限当全量，R21）；
- 坏枚举 → ``OPERATION_UNKNOWN``（不读成确定状态）；
- 重复 ``mark_answer_delivered`` 幂等，且聚合随之从交付阶段移入消费阶段。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text, update

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    BindActualInputReceipt,
    BindActualInputRequest,
    ConsumptionState,
    DeliveryState,
    MarkAnswerDeliveredRequest,
    Mode,
    Purpose,
    SaveAnswerRequest,
    SubmissionReceipt,
    derive_round_id,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentCommand, AgentDecision, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER_SCOPE = "u1"


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


async def _seed_scope(
    dao: AgentDao,
    *,
    session_id: str,
    business_request_id: str,
    workflow_id: str,
) -> tuple[SubmissionReceipt, str]:
    """受理 + 首派发绑定：真实建 execution/round（``current_round_id`` 指向该轮）。"""

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
    receipt = await dao.accept_request(
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
    bound: BindActualInputReceipt = await dao.bind_actual_input(
        BindActualInputRequest(
            user_scope=_USER_SCOPE,
            session_id=session_id,
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
                        "session_id": session_id,
                        "workflow_code_version": "wcv-1",
                        "run_epoch": 1,
                        "expected_input_revision": 1,
                    },
                }
            ),
            actual_input=json_doc({"schema_version": 1, "kind": "actual_input"}),
        )
    )
    return receipt, bound.round_id


async def _seed_decision(
    dao: AgentDao,
    receipt: SubmissionReceipt,
    round_id: str,
    decision_id: str,
    *,
    decision_kind: str = "user_choice",
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            insert(AgentDecision).values(
                user_scope=_USER_SCOPE,
                decision_id=decision_id,
                session_id=receipt.session_id,
                execution_id=receipt.execution_id,
                round_id=round_id,
                node_id="root",
                decision_kind=decision_kind,
                presentation_batch_seq=1,
            )
        )


def _answer_request(decision_id: str, execution_id: str, round_id: str, *, key: str) -> SaveAnswerRequest:
    return SaveAnswerRequest(
        user_scope=_USER_SCOPE,
        decision_id=decision_id,
        client_command_id=key,
        expected_decision_revision=1,
        expected_execution_id=execution_id,
        expected_round_id=round_id,
        answer_payload=json_doc({"schema_version": 1, "text": "选择第一家"}),
    )


async def _save_answer(dao: AgentDao, decision_id: str, execution_id: str, round_id: str) -> str:
    receipt = await dao.save_answer(_answer_request(decision_id, execution_id, round_id, key=f"ans-{decision_id}"))
    return receipt.command_record_id


class TestReadPendingAnswers:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_aggregates_three_stages_across_sessions(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt_a, round_a = await _seed_scope(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        receipt_b, round_b = await _seed_scope(dao, session_id="s2", business_request_id="req-2", workflow_id="wf-2")
        await _seed_decision(dao, receipt_a, round_a, "d-answer")
        await _seed_decision(dao, receipt_a, round_a, "d-delivery")
        await _seed_decision(dao, receipt_a, round_a, "d-consumption")
        await _seed_decision(dao, receipt_b, round_b, "d-other")
        delivery_command = await _save_answer(dao, "d-delivery", receipt_a.execution_id, round_a)
        consumption_command = await _save_answer(dao, "d-consumption", receipt_a.execution_id, round_a)
        await dao.mark_answer_delivered(
            MarkAnswerDeliveredRequest(user_scope=_USER_SCOPE, command_record_id=consumption_command)
        )

        page = await dao.read_pending_answers(user_scope=_USER_SCOPE)

        assert (page.answer_count, page.delivery_count, page.consumption_count) == (2, 1, 1)
        assert page.truncated is False
        by_stage: dict[str, list[Any]] = {}
        for item in page.items:
            by_stage.setdefault(item.stage, []).append(item)
        assert {item.decision_id for item in by_stage["answer"]} == {"d-answer", "d-other"}
        assert {item.session_id for item in by_stage["answer"]} == {"s1", "s2"}
        assert by_stage["delivery"][0].decision_id == "d-delivery"
        assert by_stage["delivery"][0].command_record_id == delivery_command
        assert by_stage["delivery"][0].delivery_state is DeliveryState.NOT_DELIVERED
        assert by_stage["consumption"][0].decision_id == "d-consumption"
        assert by_stage["consumption"][0].consumption_state is ConsumptionState.NOT_CONSUMED
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_limit_plus_one_marks_truncation(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt, round_id = await _seed_scope(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        await _seed_decision(dao, receipt, round_id, "d-1")
        await _seed_decision(dao, receipt, round_id, "d-2")

        page = await dao.read_pending_answers(user_scope=_USER_SCOPE, limit=1)

        assert page.truncated is True
        assert page.answer_count == 1
        assert len(page.items) == 1
        await dao.engine.dispose()  # type: ignore[union-attr]


class TestReadAnswerCommand:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_maps_round_workflow_and_payload(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt, round_id = await _seed_scope(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        await _seed_decision(dao, receipt, round_id, "d-1")
        command_record_id = await _save_answer(dao, "d-1", receipt.execution_id, round_id)

        fact = await dao.read_answer_command(user_scope=_USER_SCOPE, command_record_id=command_record_id)

        assert fact.decision_id == "d-1"
        assert fact.workflow_id == "wf-1"
        assert fact.client_command_id == "ans-d-1"
        assert fact.delivery_state is DeliveryState.NOT_DELIVERED
        assert fact.consumption_state is ConsumptionState.NOT_CONSUMED
        assert '"选择第一家"' in fact.request_payload
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bad_enum_state_is_operation_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt, round_id = await _seed_scope(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        await _seed_decision(dao, receipt, round_id, "d-1")
        command_record_id = await _save_answer(dao, "d-1", receipt.execution_id, round_id)
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                update(AgentCommand)
                .where(AgentCommand.command_record_id == command_record_id)
                .values(delivery_state="bogus")
            )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_answer_command(user_scope=_USER_SCOPE, command_record_id=command_record_id)

        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_non_answer_command_is_input_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt, round_id = await _seed_scope(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        await _seed_decision(dao, receipt, round_id, "d-1")
        command_record_id = await _save_answer(dao, "d-1", receipt.execution_id, round_id)
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                update(AgentCommand)
                .where(AgentCommand.command_record_id == command_record_id)
                .values(command_kind="submit")
            )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_answer_command(user_scope=_USER_SCOPE, command_record_id=command_record_id)

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]


class TestMarkDeliveredIdempotency:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_repeat_mark_is_idempotent_and_moves_to_consumption(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt, round_id = await _seed_scope(dao, session_id="s1", business_request_id="req-1", workflow_id="wf-1")
        await _seed_decision(dao, receipt, round_id, "d-1")
        command_record_id = await _save_answer(dao, "d-1", receipt.execution_id, round_id)
        request = MarkAnswerDeliveredRequest(user_scope=_USER_SCOPE, command_record_id=command_record_id)

        first = await dao.mark_answer_delivered(request)
        replay = await dao.mark_answer_delivered(request)
        page = await dao.read_pending_answers(user_scope=_USER_SCOPE)

        assert first.delivery_state is DeliveryState.DELIVERED
        assert replay == first
        assert (page.answer_count, page.delivery_count, page.consumption_count) == (0, 0, 1)
        assert page.items[0].delivery_state is DeliveryState.DELIVERED
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            command_rows = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentCommand)
                    .where(AgentCommand.command_record_id == command_record_id)
                )
            ).scalar_one()
        assert command_rows == 1
        await dao.engine.dispose()  # type: ignore[union-attr]
