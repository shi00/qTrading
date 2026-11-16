"""AgentDao C-02-3 命令/许可/修订事务集成测试（SQ-03/§5.3/§5.4/§9.13）。

真实 embedded PG 上的单事务语义验证：

- ``bind_actual_input``：首派发建 agent_round（round_number=1、active_round、
  双摘要）+ execution.current_round_id + input_bound 执行事件（execution/round/
  execution_seq 同设）；输入版取自权威受理/修订单（检视 D03）；workflow_id
  唯一幂等重放须 execution/node/代码版/epoch 完整身份相等（§9.5，检视 D03）；
- ``save_answer``/``mark_answer_delivered``/``consume_answer``：answer_command_id
  CAS 单胜者、投递/消费事实分列（INV-06）、已消费返回原事实（SQ-03）；
- ``claim_tool_dispatch``/``revoke_tool_grant``：grant_revision CAS 单胜者，
  撤销先胜零新派发（§5.4 第 6 条）；
- ``revise_queued``：execution.revision CAS 单胜者后建 M2/S2 新输入版（§9.13）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    BindActualInputReceipt,
    BindActualInputRequest,
    ConsumeAnswerRequest,
    ConsumptionState,
    DeliveryState,
    GrantRevisionRequest,
    MarkAnswerDeliveredRequest,
    Mode,
    Purpose,
    SubmissionReceipt,
    ReviseQueuedRequest,
    SaveAnswerRequest,
    ToolGrantState,
    frozen_command_input_digest,
    json_doc,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import (
    AgentAssetVersion,
    AgentCommand,
    AgentDecision,
    AgentExecution,
    AgentMessage,
    AgentRound,
    AgentSession,
    AgentSourceLink,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


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


async def _seed_session(dao: AgentDao, user_scope: str, session_id: str) -> None:
    from sqlalchemy import insert

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造受理前置会话
        await conn.execute(
            insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title=f"session {session_id}",
            )
        )


def _request(user_scope: str = "u1", session_id: str = "s1", text: str = "研究三家公司", **overrides) -> AcceptRequest:
    manifest = json_doc(
        {
            "schema_version": 1,
            "source_module": "ui",
            "business_request_id": overrides.get("business_request_id", "req-1"),
            "mode": "discuss",
            "purpose": "concept",
            "text": text,
        }
    )
    values: dict = {
        "user_scope": user_scope,
        "session_id": session_id,
        "source_module": "ui",
        "business_request_id": "req-1",
        "client_command_id": "client-1",
        "expected_revision": 0,
        "node_id": "root",
        "workflow_id": "wf-1",
        "mode": Mode.DISCUSS,
        "purpose": Purpose.CONCEPT,
        "text": text,
        "manifest_payload": manifest,
        "source_display_name": "用户消息",
    }
    values.update(overrides)
    return AcceptRequest(**values)


def _bind_request(
    receipt: SubmissionReceipt, user_scope: str = "u1", session_id: str = "s1", **overrides
) -> BindActualInputRequest:
    values: dict = {
        "user_scope": user_scope,
        "session_id": session_id,
        "execution_id": receipt.execution_id,
        "node_id": "root",
        "workflow_id": receipt.workflow_id,
        "workflow_code_version": "wcv-1",
        "run_epoch": 1,
        "frozen_manifest": json_doc({"schema_version": 1, "kind": "frozen_manifest"}),
        "actual_input": json_doc({"schema_version": 1, "kind": "actual_input"}),
    }
    values.update(overrides)
    return BindActualInputRequest(**values)


def _answer_request(decision_id: str = "d-1", client_command_id: str = "ans-1", **overrides) -> SaveAnswerRequest:
    values: dict = {
        "user_scope": "u1",
        "decision_id": decision_id,
        "client_command_id": client_command_id,
        "expected_decision_revision": 1,
        "answer_payload": json_doc({"schema_version": 1, "approved": True}),
    }
    values.update(overrides)
    return SaveAnswerRequest(**values)


def _grant_request(decision_id: str = "d-1", expected: int = 1, user_scope: str = "u1") -> GrantRevisionRequest:
    return GrantRevisionRequest(user_scope=user_scope, decision_id=decision_id, expected_grant_revision=expected)


def _revise_request(
    receipt: SubmissionReceipt, client_command_id: str = "rev-1", expected: int = 1, **overrides
) -> ReviseQueuedRequest:
    manifest = json_doc(
        {
            "schema_version": 1,
            "source_module": "ui",
            "business_request_id": "req-1",
            "mode": "discuss",
            "purpose": "concept",
            "text": overrides.get("text", "改为研究两家公司"),
        }
    )
    values: dict = {
        "user_scope": "u1",
        "session_id": "s1",
        "execution_id": receipt.execution_id,
        "client_command_id": client_command_id,
        "expected_execution_revision": expected,
        "node_id": "root",
        "text": "改为研究两家公司",
        "manifest_payload": manifest,
        "source_display_name": "用户消息",
    }
    values.update(overrides)
    return ReviseQueuedRequest(**values)


async def _accept_only(
    real_embedded_pg: ConnectionInfo, user_scope: str = "u1", session_id: str = "s1", **accept_overrides
) -> tuple[AgentDao, SubmissionReceipt]:
    """受理即止（不派发绑定）：队列修订发生在此阶段（execution.revision=1）。"""

    dao = await _setup(real_embedded_pg.url)
    await _seed_session(dao, user_scope, session_id)
    receipt = await dao.accept_request(_request(user_scope=user_scope, session_id=session_id, **accept_overrides))
    return dao, receipt


async def _seed_decision(dao: AgentDao, receipt: SubmissionReceipt, round_id: str, **overrides) -> str:
    """受理 + 绑定后落决定行（§9.8：decision 依赖 round 存在）。"""

    from sqlalchemy import insert

    values: dict = {
        "user_scope": "u1",
        "decision_id": "d-1",
        "session_id": receipt.session_id,
        "execution_id": receipt.execution_id,
        "round_id": round_id,
        "node_id": "root",
        "decision_kind": "tool_grant",
        "presentation_batch_seq": 1,
    }
    values.update(overrides)
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(insert(AgentDecision).values(**values))
    return values["decision_id"]


async def _full_setup(
    real_embedded_pg: ConnectionInfo, user_scope: str = "u1", session_id: str = "s1", **accept_overrides
) -> tuple[AgentDao, SubmissionReceipt, str]:
    """受理 → 首派发绑定 → 返回 (dao, 受理回执, round_id)。"""

    dao = await _setup(real_embedded_pg.url)
    await _seed_session(dao, user_scope, session_id)
    receipt = await dao.accept_request(_request(user_scope=user_scope, session_id=session_id, **accept_overrides))
    bind_receipt: BindActualInputReceipt = await dao.bind_actual_input(
        _bind_request(receipt, user_scope=user_scope, session_id=session_id)
    )
    return dao, receipt, bind_receipt.round_id


_COUNT_TABLES: dict[str, object] = {
    "agent_round": AgentRound,
    "agent_command": AgentCommand,
    "agent_message": AgentMessage,
    "agent_asset_version": AgentAssetVersion,
    "agent_source_link": AgentSourceLink,
    "agent_decision": AgentDecision,
}


async def _count(engine, table: str) -> int:
    model: Any = _COUNT_TABLES[table]
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(sa.func.count()).select_from(model))
        return int(result.scalar_one())


async def _count_for(engine, model, *conditions) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(sa.func.count()).select_from(model).where(*conditions))
        return int(result.scalar_one())


class TestBindActualInput:
    """首派发实际输入绑定（§9.5/§9.6/§9.13 bind_actual_input）。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_creates_round_updates_execution_and_event(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, _ = await _full_setup(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        bind = await dao.bind_actual_input(_bind_request(receipt))

        assert bind.round_id.startswith("round_")
        assert bind.round_number == 1
        assert bind.input_revision == 1
        assert bind.frozen_digest == sha256_hex(_bind_request(receipt).frozen_manifest)
        assert bind.actual_digest == sha256_hex(_bind_request(receipt).actual_input)

        async with engine.connect() as conn:
            round_row = (
                await conn.execute(
                    sa.select(
                        AgentRound.round_number,
                        AgentRound.active_round,
                        AgentRound.workflow_id,
                        AgentRound.frozen_digest,
                        AgentRound.actual_digest,
                        AgentRound.input_revision,
                    ).where(AgentRound.round_id == bind.round_id)
                )
            ).one()
            current = (
                await conn.execute(
                    sa.select(AgentExecution.current_round_id).where(
                        AgentExecution.execution_id == receipt.execution_id
                    )
                )
            ).scalar_one()
            event = (
                await conn.execute(
                    sa.select(
                        AgentMessage.event_kind,
                        AgentMessage.execution_id,
                        AgentMessage.round_id,
                        AgentMessage.execution_seq,
                        AgentMessage.session_seq,
                        AgentMessage.role,
                    ).where(
                        AgentMessage.round_id == bind.round_id,
                        AgentMessage.event_kind == "input_bound",
                    )
                )
            ).one_or_none()

        assert round_row.round_number == 1 and round_row.active_round is True
        assert round_row.workflow_id == "wf-1"
        assert round_row.frozen_digest == bind.frozen_digest
        assert round_row.actual_digest == bind.actual_digest
        assert round_row.input_revision == 1
        assert current == bind.round_id
        # input_bound 执行事件：execution/round/execution_seq 同设（§9.6 CHECK），
        # M1 是 session_seq=1 的受理消息（水位起点 1，检视 D01），故本事件 session_seq=2。
        assert event is not None
        assert event.event_kind == "input_bound"
        assert event.execution_id == receipt.execution_id and event.round_id == bind.round_id
        assert event.execution_seq == 1 and event.session_seq == 2
        assert event.role is None
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_replay_same_workflow_returns_original_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        """同 workflow_id 同输入重放 → 原事实，零新行（§9.5 workflow UNIQUE）。"""
        dao, receipt, _ = await _full_setup(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        first = await dao.bind_actual_input(_bind_request(receipt))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        replay = await dao.bind_actual_input(_bind_request(receipt))

        assert replay == first
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before, f"replay must not create facts: {before} -> {after}"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_same_workflow_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, _ = await _full_setup(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        await dao.bind_actual_input(_bind_request(receipt))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.bind_actual_input(
                _bind_request(receipt, actual_input=json_doc({"schema_version": 1, "kind": "different"}))
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_unknown_execution_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        receipt = await dao.accept_request(_request())

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.bind_actual_input(_bind_request(receipt, execution_id="execution_missing"))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_second_active_round_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """同 execution 已有有效轮 → 第二 workflow 绑定冲突（§9.5 部分唯一）。"""
        dao, receipt, _ = await _full_setup(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        await dao.bind_actual_input(_bind_request(receipt))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.bind_actual_input(_bind_request(receipt, workflow_id="wf-2"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_watermarks_monotonic(self, real_embedded_pg: ConnectionInfo) -> None:
        """session/execution 事件水位 GREATEST 推进（R22）。"""
        dao, receipt, _ = await _full_setup(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        bind = await dao.bind_actual_input(_bind_request(receipt))
        async with engine.connect() as conn:
            wm = (
                await conn.execute(
                    sa.select(AgentSession.event_seq, AgentExecution.event_seq).join(
                        AgentExecution,
                        (AgentExecution.user_scope == AgentSession.user_scope)
                        & (AgentExecution.session_id == AgentSession.session_id),
                    )
                )
            ).one()
        assert tuple(wm) == (bind.session_seq, bind.execution_seq)
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_uses_authoritative_input_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        """输入版取自权威修订单（受理=1、首次修订=2），不恒为 1（检视 D03）。"""
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        rev = await dao.revise_queued(_revise_request(receipt, expected=1))

        bind = await dao.bind_actual_input(_bind_request(receipt))

        assert rev.input_revision == 2
        assert bind.input_revision == 2
        async with engine.connect() as conn:
            round_revision = (
                await conn.execute(sa.select(AgentRound.input_revision).where(AgentRound.round_id == bind.round_id))
            ).scalar_one()
            event = (
                await conn.execute(
                    sa.select(AgentMessage.payload, AgentMessage.input_revision).where(
                        AgentMessage.round_id == bind.round_id,
                        AgentMessage.event_kind == "input_bound",
                    )
                )
            ).one()
        assert round_revision == 2
        assert event.input_revision == 2
        assert event.payload["input_revision"] == 2
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_same_workflow_other_execution_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """同 workflow 双摘要相同但 execution 不同 → 冲突，不得跨执行冒认重放（检视 D03）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        first = await dao.accept_request(_request(business_request_id="req-1", client_command_id="client-1"))
        second = await dao.accept_request(_request(business_request_id="req-2", client_command_id="client-2"))
        engine = create_test_engine(real_embedded_pg.url)
        await dao.bind_actual_input(_bind_request(first, workflow_id="wf-shared"))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.bind_actual_input(_bind_request(second, workflow_id="wf-shared"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_replay_requires_matching_code_version_and_node(self, real_embedded_pg: ConnectionInfo) -> None:
        """同 workflow 双摘要相同但代码版/node 不同 → 冲突（检视 D03）。"""
        dao, receipt, _ = await _full_setup(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)

        with pytest.raises(AgentServiceError) as exc_code:
            await dao.bind_actual_input(_bind_request(receipt, workflow_code_version="wcv-2"))
        assert exc_code.value.code is AgentErrorCode.INPUT_CONFLICT
        with pytest.raises(AgentServiceError) as exc_node:
            await dao.bind_actual_input(_bind_request(receipt, node_id="node-2"))
        assert exc_node.value.code is AgentErrorCode.INPUT_CONFLICT

        replay = await dao.bind_actual_input(_bind_request(receipt))
        assert replay.execution_seq == 1  # 水位起点 1（检视 D01）
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_bind_run_epoch_zero_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        """run_epoch=0 拒绝：与快照身份解码下限 1 对齐（检视 D03）。"""
        dao, receipt = await _accept_only(real_embedded_pg)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.bind_actual_input(_bind_request(receipt, run_epoch=0))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID


class TestAnswerLifecycle:
    """答复保存/投递/消费（SQ-03/§5.3/§9.13；INV-06 三态分列）。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_save_answer_cas_and_pending_command(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        decision_id = await _seed_decision(dao, receipt, round_id)
        engine = create_test_engine(real_embedded_pg.url)

        ans = await dao.save_answer(_answer_request(decision_id))

        assert ans.decision_id == decision_id
        assert ans.command_record_id.startswith("command_")
        assert ans.decision_revision == 2
        assert ans.delivery_state is DeliveryState.NOT_DELIVERED
        assert ans.consumption_state is ConsumptionState.NOT_CONSUMED
        assert ans.answer_digest == sha256_hex(_answer_request(decision_id).answer_payload)
        async with engine.connect() as conn:
            cmd = (
                await conn.execute(
                    sa.select(AgentCommand.command_kind, AgentCommand.delivery_state, AgentCommand.target_id).where(
                        AgentCommand.command_record_id == ans.command_record_id
                    )
                )
            ).one()
            linked = (
                await conn.execute(
                    sa.select(AgentDecision.answer_command_id, AgentDecision.revision).where(
                        AgentDecision.decision_id == decision_id
                    )
                )
            ).one()
        assert cmd.command_kind == "answer" and cmd.delivery_state == "not_delivered"
        assert cmd.target_id == decision_id
        assert linked.answer_command_id == ans.command_record_id and linked.revision == 2
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_save_answer_replay_returns_original(self, real_embedded_pg: ConnectionInfo) -> None:
        """同命令同规范输入重放 → 原事实零新行（§9.13 save_answer 幂等）。"""
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        decision_id = await _seed_decision(dao, receipt, round_id)
        engine = create_test_engine(real_embedded_pg.url)
        first = await dao.save_answer(_answer_request(decision_id))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        replay = await dao.save_answer(_answer_request(decision_id))

        assert replay == first
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_save_answer_second_answerer_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """决定已被应答 → 第二应答者 REVISION_CONFLICT（answer_command_id CAS 单胜者）。"""
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        decision_id = await _seed_decision(dao, receipt, round_id)
        engine = create_test_engine(real_embedded_pg.url)
        await dao.save_answer(_answer_request(decision_id))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_answer(_answer_request(decision_id, client_command_id="ans-2"))

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_save_answer_same_key_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        decision_id = await _seed_decision(dao, receipt, round_id)
        engine = create_test_engine(real_embedded_pg.url)
        await dao.save_answer(_answer_request(decision_id))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_answer(
                _answer_request(decision_id, answer_payload=json_doc({"schema_version": 1, "approved": False}))
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_save_answer_unknown_decision_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_answer(_answer_request("d-missing"))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio(loop_scope="session")
    async def test_mark_delivered_then_consume_separate_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        """投递与消费分列（INV-06）：mark → delivered（重放零变化）；consume →
        consumed + decision.answer_consumed_at（重放返回原时刻）。"""
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        decision_id = await _seed_decision(dao, receipt, round_id)
        engine = create_test_engine(real_embedded_pg.url)
        ans = await dao.save_answer(_answer_request(decision_id))

        delivered = await dao.mark_answer_delivered(
            MarkAnswerDeliveredRequest(user_scope="u1", command_record_id=ans.command_record_id)
        )
        replay_delivered = await dao.mark_answer_delivered(
            MarkAnswerDeliveredRequest(user_scope="u1", command_record_id=ans.command_record_id)
        )
        consumed = await dao.consume_answer(
            ConsumeAnswerRequest(user_scope="u1", command_record_id=ans.command_record_id)
        )
        replay_consumed = await dao.consume_answer(
            ConsumeAnswerRequest(user_scope="u1", command_record_id=ans.command_record_id)
        )

        assert delivered.delivery_state is DeliveryState.DELIVERED
        assert replay_delivered == delivered
        assert consumed.consumption_state is ConsumptionState.CONSUMED
        assert consumed.decision_id == decision_id
        assert consumed.consumed_at == replay_consumed.consumed_at
        assert consumed.answer_consumed_at == replay_consumed.answer_consumed_at
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(
                        AgentCommand.delivery_state,
                        AgentCommand.consumption_state,
                        AgentCommand.consumed_at,
                        AgentDecision.answer_consumed_at,
                    )
                    .join(
                        AgentDecision,
                        (AgentDecision.user_scope == AgentCommand.user_scope)
                        & (AgentDecision.answer_command_id == AgentCommand.command_record_id),
                    )
                    .where(AgentCommand.command_record_id == ans.command_record_id)
                )
            ).one()
        assert row.delivery_state == "delivered" and row.consumption_state == "consumed"
        assert row.consumed_at is not None and row.answer_consumed_at is not None
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_consume_unknown_command_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.consume_answer(ConsumeAnswerRequest(user_scope="u1", command_record_id="command_missing"))
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID


class TestGrantCas:
    """精确许可 CAS（§5.4 第 6 条/§9.13 claim_tool_dispatch/revoke_tool_grant）。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_claim_cas_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        await _seed_decision(
            dao,
            receipt,
            round_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state="granted",
            parameter_digest="a" * 64,
        )
        engine = create_test_engine(real_embedded_pg.url)

        fact = await dao.claim_tool_dispatch(_grant_request("d-1", expected=1))

        assert fact.grant_id == "grant-1"
        assert fact.grant_revision == 2
        assert fact.grant_state is ToolGrantState.CLAIMED
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(AgentDecision.grant_state, AgentDecision.grant_revision).where(
                        AgentDecision.decision_id == "d-1"
                    )
                )
            ).one()
        assert row.grant_state == "claimed" and row.grant_revision == 2
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_claim_replay_returns_fact_zero_new(self, real_embedded_pg: ConnectionInfo) -> None:
        """W 崩溃恢复同 expected 重试 → 返回原事实，零新变更（§9.13 重放规则）。"""
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        await _seed_decision(
            dao,
            receipt,
            round_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state="granted",
            parameter_digest="a" * 64,
        )
        engine = create_test_engine(real_embedded_pg.url)
        first = await dao.claim_tool_dispatch(_grant_request("d-1", expected=1))
        rev_before = await _scalar(engine, sa.select(AgentDecision.revision).where(AgentDecision.decision_id == "d-1"))

        replay = await dao.claim_tool_dispatch(_grant_request("d-1", expected=1))

        assert replay == first
        rev_after = await _scalar(engine, sa.select(AgentDecision.revision).where(AgentDecision.decision_id == "d-1"))
        assert rev_after == rev_before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_claim_stale_revision_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        await _seed_decision(
            dao,
            receipt,
            round_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state="granted",
            parameter_digest="a" * 64,
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_tool_dispatch(_grant_request("d-1", expected=2))
        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revoke_before_claim_blocks_dispatch(self, real_embedded_pg: ConnectionInfo) -> None:
        """撤销先胜 → claim 失败零新派发（§5.4 第 6 条/SQ-03 alt 分支）。"""
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        await _seed_decision(
            dao,
            receipt,
            round_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state="granted",
            parameter_digest="a" * 64,
        )
        engine = create_test_engine(real_embedded_pg.url)
        revoked = await dao.revoke_tool_grant(_grant_request("d-1", expected=1))
        assert revoked.grant_state is ToolGrantState.REVOKED
        assert revoked.grant_revision == 2

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_tool_dispatch(_grant_request("d-1", expected=1))

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        async with engine.connect() as conn:
            state = (
                await conn.execute(sa.select(AgentDecision.grant_state).where(AgentDecision.decision_id == "d-1"))
            ).scalar_one()
        assert state == "revoked"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revoke_after_claim_succeeds(self, real_embedded_pg: ConnectionInfo) -> None:
        """派发先胜后撤销仍可 revoked（阻止后续，保真实在途，§5.4 第 6 条）。"""
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        await _seed_decision(
            dao,
            receipt,
            round_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state="granted",
            parameter_digest="a" * 64,
        )
        await dao.claim_tool_dispatch(_grant_request("d-1", expected=1))
        engine = create_test_engine(real_embedded_pg.url)

        fact = await dao.revoke_tool_grant(_grant_request("d-1", expected=2))

        assert fact.grant_state is ToolGrantState.REVOKED
        assert fact.grant_revision == 3
        async with engine.connect() as conn:
            state = (
                await conn.execute(sa.select(AgentDecision.grant_state).where(AgentDecision.decision_id == "d-1"))
            ).scalar_one()
        assert state == "revoked"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revoke_replay_returns_fact(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt, round_id = await _full_setup(real_embedded_pg)
        await _seed_decision(
            dao,
            receipt,
            round_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state="granted",
            parameter_digest="a" * 64,
        )
        first = await dao.revoke_tool_grant(_grant_request("d-1", expected=1))
        replay = await dao.revoke_tool_grant(_grant_request("d-1", expected=1))
        assert replay == first


class TestReviseQueued:
    """队列修订（§9.13 revise_queued：revision CAS 单胜者 + M2/S2 新输入版）。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_creates_m2_s2_and_bumps_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt = await _accept_only(real_embedded_pg, session_id="s1")
        engine = create_test_engine(real_embedded_pg.url)
        # 修订前事实基线（M1/1 源/2 边/1 命令）
        assert await _count(engine, "agent_message") == 1

        revise = _revise_request(receipt)
        rev = await dao.revise_queued(revise)

        assert rev.execution_id == receipt.execution_id
        assert rev.command_record_id.startswith("command_")
        assert rev.input_revision == 2
        assert rev.execution_revision == 2
        # 源自身不可变版恒为 "1"（AR-07），输入版 2 只由源边 consumer_version 承载（检视 D13）。
        assert rev.source_version == "1"
        # 命令 input_digest 为完整规范输入摘要（正文 + manifest + 期望版/node，检视 D05）。
        assert rev.input_digest == frozen_command_input_digest(
            "revise",
            revise.text,
            revise.manifest_payload,
            expected_revision=revise.expected_execution_revision,
            node_id=revise.node_id,
        )
        assert rev.session_seq == 2  # M1=1 之后的第二条 session 事件（水位起点 1，检视 D01）
        async with engine.connect() as conn:
            m2 = (
                await conn.execute(
                    sa.select(
                        AgentMessage.session_seq,
                        AgentMessage.role,
                        AgentMessage.body_text,
                        AgentMessage.execution_id,
                    ).where(AgentMessage.message_id == rev.message_id)
                )
            ).one()
            cmd = (
                await conn.execute(
                    sa.select(
                        AgentCommand.command_kind,
                        AgentCommand.input_revision,
                        AgentCommand.target_id,
                        AgentCommand.expected_revision,
                    ).where(AgentCommand.command_record_id == rev.command_record_id)
                )
            ).one()
            exec_revision = (
                await conn.execute(
                    sa.select(AgentExecution.revision).where(AgentExecution.execution_id == receipt.execution_id)
                )
            ).scalar_one()
            execution_edges = await _count_for(
                engine,
                AgentSourceLink,
                AgentSourceLink.consumer_kind == "execution",
                AgentSourceLink.execution_id == receipt.execution_id,
                AgentSourceLink.consumer_version == "2",
            )
            # 新源资产自身不可变版恒为 "1"；两源边的 consumer_version 分列
            # （message 版 "1" / execution 输入版 "2"，检视 D13）。
            s2 = (
                await conn.execute(
                    sa.select(AgentAssetVersion.asset_version).where(AgentAssetVersion.asset_id == rev.source_id)
                )
            ).scalar_one()
            edge_versions = {
                (row.consumer_kind, row.consumer_version)
                for row in (
                    await conn.execute(
                        sa.select(AgentSourceLink.consumer_kind, AgentSourceLink.consumer_version).where(
                            AgentSourceLink.source_id == rev.source_id
                        )
                    )
                ).all()
            }
        assert m2.session_seq == 2 and m2.role == "user"
        assert m2.body_text == "改为研究两家公司"
        assert m2.execution_id is None  # 修订消息纯 session 事件（无 round，§9.6）
        assert cmd.command_kind == "revise" and cmd.input_revision == 2
        assert cmd.target_id == receipt.execution_id and cmd.expected_revision == 1
        assert exec_revision == 2
        assert execution_edges == 1
        assert s2 == "1"
        assert edge_versions == {("message", "1"), ("execution", "2")}
        # 新源资产 S2 落地
        assert await _count(engine, "agent_asset_version") == 2
        assert await _count(engine, "agent_message") == 2
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_stale_revision_conflicts_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, expected=99))

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_replay_same_key_same_input_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        first = await dao.revise_queued(_revise_request(receipt))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        replay = await dao.revise_queued(_revise_request(receipt))

        assert replay == first
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_same_key_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        await dao.revise_queued(_revise_request(receipt))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, text="又改成别的"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_same_key_same_text_different_manifest_conflicts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """同修订键同正文换 manifest → INPUT_CONFLICT（检视 D05），零新事实。"""
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        frozen_a = json_doc({"schema_version": 1, "text": "改为研究两家公司", "purpose": "concept"})
        frozen_b = json_doc({"schema_version": 1, "text": "改为研究两家公司", "purpose": "decide"})
        await dao.revise_queued(_revise_request(receipt, manifest_payload=frozen_a))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, manifest_payload=frozen_b))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_same_key_different_expected_revision_or_node_conflicts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """同修订键同正文同 manifest 换期望版/node → INPUT_CONFLICT（检视 D05），零新事实。"""
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        frozen = json_doc({"schema_version": 1, "text": "改为研究两家公司"})
        await dao.revise_queued(_revise_request(receipt, manifest_payload=frozen))
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, expected=2, manifest_payload=frozen))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, node_id="node-2", manifest_payload=frozen))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_revise_legacy_digest_row_replays_and_manifest_change_conflicts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """旧摘要行（仅正文）同输入重放返回原事实；同正文换 manifest 仍冲突（D05 兼容路径）。"""
        dao, receipt = await _accept_only(real_embedded_pg)
        engine = create_test_engine(real_embedded_pg.url)
        frozen_a = json_doc({"schema_version": 1, "text": "改为研究两家公司", "purpose": "concept"})
        frozen_b = json_doc({"schema_version": 1, "text": "改为研究两家公司", "purpose": "decide"})
        first = await dao.revise_queued(_revise_request(receipt, manifest_payload=frozen_a))
        # 模拟 D05 修复前的历史行：命令 input_digest 仅覆盖正文。
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentCommand)
                .where(AgentCommand.user_scope == "u1", AgentCommand.command_kind == "revise")
                .values(input_digest=sha256_hex("改为研究两家公司"))
            )
        before = {t: await _count(engine, t) for t in _COUNT_TABLES}

        replay = await dao.revise_queued(_revise_request(receipt, manifest_payload=frozen_a))
        assert replay.input_revision == first.input_revision
        assert replay.input_digest == sha256_hex("改为研究两家公司")
        assert replay.session_seq == first.session_seq

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, expected=2, manifest_payload=frozen_a))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.revise_queued(_revise_request(receipt, manifest_payload=frozen_b))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in _COUNT_TABLES}
        assert after == before
        await engine.dispose()


async def _scalar(engine, query) -> object:
    async with engine.connect() as conn:
        return (await conn.execute(query)).scalar_one()
