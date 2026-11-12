"""AgentDao C-03-2c 执行快照集成测试（§4.3 ResearchSnapshot 19 字段组装）。

真实 embedded PG 上一致读取截点语义验证：

- get_execution_snapshot：identity（round↔workflow 耦合）/revision/双水位/
  execution 摘要 / 一层叶子 nodes / frozen_scope+actual_input（round JSONB
  规范化 CanonicalJson）/ 消息成果分页 / source_reads（source_link join
  asset_version 元数据）/ decisions / operation_summaries / usage（cost_state
  恒 unknown）/ goal_coverage / read_marks / observation.reliable；
- 行级严格解码 fail-closed OPERATION_UNKNOWN（R21）；
- queued 执行无 round：identity 全 None 组，frozen_scope/actual_input None；
- 目标 round 过滤：round 不属该 execution → INPUT_INVALID。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    EventKind,
    MessageRole,
    RecordEventRequest,
    decode_research_snapshot,
    json_doc,
    to_wire_dict,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentArtifact,
    AgentAssetVersion,
    AgentDecision,
    AgentExecution,
    AgentMessage,
    AgentOperation,
    AgentReadMark,
    AgentRound,
    AgentSession,
    AgentSourceLink,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "synthetic-user"
OTHER_SCOPE = "other-user"


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


async def _seed_chain(
    dao: AgentDao,
    session_id: str = "s-snap",
    execution_id: str = "exec-snap",
    round_id: str = "round-snap",
    *,
    with_round: bool = True,
    with_leaf: bool = False,
) -> tuple[str, str, str | None]:
    """session + execution(+round)(+leaf 子执行) 最小合法行。"""

    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="快照会话"))
        execution_values: dict = {
            "user_scope": USER_SCOPE,
            "execution_id": execution_id,
            "session_id": session_id,
            "execution_kind": "root",
            "source_module": "ui",
            "business_request_id": "req-snap",
            "node_id": "root",
            "display_state": "running" if with_round else "queued",
            "goal_coverage": "unknown",
            "intent_digest": "a" * 64,
            "model_attempt_count": 2,
            "tool_call_count": 1,
            "active_ms": 1500,
        }
        await conn.execute(insert(AgentExecution).values(**execution_values))
        if with_round:
            # current_round_id FK 即时核验：先建 execution/round，再回填当前轮
            await conn.execute(
                insert(AgentRound).values(
                    user_scope=USER_SCOPE,
                    round_id=round_id,
                    execution_id=execution_id,
                    round_number=1,
                    workflow_id="wf-snap-1",
                    workflow_code_version="wcv-1",
                    run_epoch=1,
                    input_revision=1,
                    plan_revision=1,
                    goal_coverage="unknown",
                    final_saved=False,
                    convergence_confirmed=False,
                    active_round=True,
                    frozen_manifest={"scope": "daily", "universe": "hs300"},
                    actual_input={"messages": 3, "sources": 2},
                )
            )
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == USER_SCOPE, AgentExecution.execution_id == execution_id)
                .values(current_round_id=round_id)
            )
        if with_leaf:
            await conn.execute(
                insert(AgentExecution).values(
                    user_scope=USER_SCOPE,
                    execution_id=f"{execution_id}-leaf",
                    session_id=session_id,
                    execution_kind="leaf",
                    parent_execution_id=execution_id,
                    node_id="leaf-node",
                    display_state="queued",
                    goal_coverage="unknown",
                )
            )
    return session_id, execution_id, round_id if with_round else None


async def _seed_message(
    dao: AgentDao,
    session_id: str,
    execution_id: str,
    native_event_id: str,
    *,
    round_id: str | None,
    body_text: str | None = None,
) -> int:
    request = RecordEventRequest(
        user_scope=USER_SCOPE,
        session_id=session_id,
        message_id=f"m-{native_event_id}",
        native_event_id=native_event_id,
        event_kind=EventKind.PUBLIC_MESSAGE_SAVED,
        subject_id=f"m-{native_event_id}",
        subject_revision=1,
        payload_kind="public_message_saved.v1",
        payload_canonical_json=json_doc({"role": "assistant"}),
        occurred_at="2026-06-01T12:00:00.000000Z",
        execution_id=execution_id,
        round_id=round_id,
        node_id="root",
        role=MessageRole.ASSISTANT,
        body_text=body_text,
    )
    fact = await dao.record_event(request)
    return fact.session_seq


async def _seed_artifact(dao: AgentDao, artifact_id: str, execution_id: str, round_id: str) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentArtifact).values(
                user_scope=USER_SCOPE,
                artifact_id=artifact_id,
                artifact_version=1,
                session_id="s-snap",
                execution_id=execution_id,
                round_id=round_id,
                artifact_kind="plan",
                input_revision=1,
                actual_input_digest="b" * 64,
                actual_model_ref="model/main",
                result_digest="c" * 64,
                source_available_state="unknown",
                published_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
                body_text=f"成果正文 {artifact_id}",
            )
        )


async def _seed_snapshot_extras(dao: AgentDao, execution_id: str, round_id: str) -> None:
    """decision/operation/asset_version+source_link/read_mark 各一行。"""

    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentDecision).values(
                user_scope=USER_SCOPE,
                decision_id="dec-snap",
                session_id="s-snap",
                execution_id=execution_id,
                round_id=round_id,
                node_id="root",
                decision_kind="user_choice",
                presentation_batch_seq=1,
            )
        )
        await conn.execute(
            insert(AgentOperation).values(
                user_scope=USER_SCOPE,
                operation_id="op-snap",
                session_id="s-snap",
                execution_id=execution_id,
                round_id=round_id,
                tool_call_id="tc-1",
                owner_epoch=1,
                run_epoch=1,
                phase="dispatched",
                effect_state="dispatched",
                io_state="open",
                grant_id="grant-1",
                grant_revision=1,
                active_ms=300,
                elapsed_ms=900,
            )
        )
        await conn.execute(
            insert(AgentAssetVersion).values(
                user_scope=USER_SCOPE,
                asset_id="asset-snap",
                asset_version="v1",
                asset_kind="source",
                display_name="快照来源",
                digest="d" * 64,
                read_state="ready",
                verification_state="unverified",
            )
        )
        await conn.execute(
            insert(AgentSourceLink).values(
                user_scope=USER_SCOPE,
                link_id="link-snap",
                source_id="asset-snap",
                source_version="v1",
                consumer_kind="execution",
                consumer_id=execution_id,
                consumer_version="1",
                execution_id=execution_id,
                locator_payload={"locator": "rows[0:10]"},
                relation_kind="input",
                disabled_inherited=False,
            )
        )
        await conn.execute(
            insert(AgentReadMark).values(
                user_scope=USER_SCOPE,
                marker_id="mark-snap",
                marker_kind="session",
                target_id="s-snap",
                session_id="s-snap",
                last_seq=5,
                batch_seq=1,
                postponed_through_seq=5,
            )
        )


def _code_of(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> AgentErrorCode:
    assert isinstance(excinfo.value, AgentServiceError)
    return excinfo.value.code


class TestGetExecutionSnapshotBasics:
    async def test_assembles_snapshot_and_strict_roundtrip(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao, with_leaf=True)
        await _seed_message(dao, session_id, execution_id, "m1", round_id=round_id, body_text="执行消息")
        await _seed_artifact(dao, "art-snap", execution_id, round_id)
        await _seed_snapshot_extras(dao, execution_id, round_id)

        snap = await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE)

        # identity：round↔workflow 耦合（§4.3）
        assert snap.identity.user_scope == USER_SCOPE
        assert snap.identity.session_id == session_id
        assert snap.identity.execution_id == execution_id
        assert snap.identity.node_id == "root"
        assert snap.identity.round_id == round_id
        assert snap.identity.input_revision == 1
        assert snap.identity.workflow_id == "wf-snap-1"
        assert snap.identity.run_epoch == 1
        # 同一截点水位：execution 双计数 + session 水位
        assert snap.revision == 1
        assert snap.execution_watermark == 1
        assert snap.session_watermark == 1
        assert snap.updated_at.endswith("Z")
        # execution / 一层叶子
        assert snap.execution.execution_id == execution_id
        assert snap.execution.display_state == "running"
        assert [n.execution_id for n in snap.nodes] == [f"{execution_id}-leaf"]
        assert snap.nodes[0].parent_execution_id == execution_id
        # frozen_scope / actual_input：round JSONB 规范化 CanonicalJson 文本
        assert snap.frozen_scope == json_doc({"scope": "daily", "universe": "hs300"})
        assert snap.actual_input == json_doc({"messages": 3, "sources": 2})
        # 消息/成果首屏
        assert [m.message_id for m in snap.messages_page.items] == ["m-m1"]
        assert snap.messages_page.has_more is False
        assert [a.artifact_id for a in snap.artifacts_page.items] == ["art-snap"]
        # source_reads：join 元数据
        assert len(snap.source_reads) == 1
        src = snap.source_reads[0]
        assert src.link_id == "link-snap"
        assert src.source_id == "asset-snap"
        assert src.source_kind == "source"
        assert src.display_name == "快照来源"
        assert src.read_state.value == "ready"
        assert src.verification_state.value == "unverified"
        assert src.consumer_kind == "execution"
        assert src.relation_kind == "input"
        assert src.disabled_inherited is False
        assert src.tombstoned is False
        assert src.locator_payload == json_doc({"locator": "rows[0:10]"})
        assert src.claim_ref is None
        # decisions / operations
        assert [d.decision_id for d in snap.decisions] == ["dec-snap"]
        assert len(snap.operation_summaries) == 1
        op = snap.operation_summaries[0]
        assert op.operation_id == "op-snap"
        assert op.phase == "dispatched"
        assert op.effect_state.value == "dispatched"
        assert op.io_state == "open"
        assert op.tool_call_id == "tc-1"
        assert op.grant_id == "grant-1"
        assert op.active_ms == 300
        assert op.elapsed_ms == 900
        # usage：实际累计 + cost_state 恒 unknown（R21 不填 0）
        assert snap.usage.model_attempt_count == 2
        assert snap.usage.tool_call_count == 1
        assert snap.usage.active_ms == 1500
        assert snap.usage.cost_state == "unknown"
        assert snap.goal_coverage == "unknown"
        # limitations 空元组=未核定到条目；read_marks / observation
        assert snap.limitations == ()
        assert len(snap.read_marks) == 1
        assert snap.read_marks[0].marker_id == "mark-snap"
        assert snap.read_marks[0].last_seq == 5
        assert snap.observation.reliable is True
        # 严格解码往返
        wire = to_wire_dict(snap)
        assert json_doc(wire) == json_doc(to_wire_dict(decode_research_snapshot(wire)))

    async def test_queued_execution_without_round(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, _ = await _seed_chain(dao, with_round=False)
        snap = await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE)
        assert snap.identity.round_id is None
        assert snap.identity.input_revision is None
        assert snap.identity.workflow_id is None
        assert snap.identity.run_epoch is None
        assert snap.frozen_scope is None
        assert snap.actual_input is None
        assert snap.execution.display_state == "queued"
        assert snap.messages_page.items == ()
        assert snap.artifacts_page.items == ()
        assert snap.decisions == ()
        assert snap.operation_summaries == ()

    async def test_missing_execution_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_execution_snapshot("no-such-exec", user_scope=USER_SCOPE)
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_user_scope_isolation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        _, execution_id, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_execution_snapshot(execution_id, user_scope=OTHER_SCOPE)
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestRoundTarget:
    async def test_round_not_in_execution_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        _, execution_id, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE, round_id="round-elsewhere")
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_round_target_filters_messages(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        # 同 execution 另建 round-2，消息只挂 round-2；目标 round-snap 过滤后应无消息
        async with dao.engine.begin() as conn:
            await conn.execute(
                insert(AgentRound).values(
                    user_scope=USER_SCOPE,
                    round_id="round-2",
                    execution_id=execution_id,
                    round_number=2,
                    workflow_id="wf-snap-2",
                    workflow_code_version="wcv-1",
                    run_epoch=1,
                    input_revision=2,
                    plan_revision=1,
                    goal_coverage="unknown",
                    final_saved=False,
                    convergence_confirmed=False,
                    active_round=False,
                )
            )
        await _seed_message(dao, session_id, execution_id, "m2", round_id="round-2", body_text="第二轮消息")
        snap = await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE, round_id="round-2")
        assert snap.identity.round_id == "round-2"
        assert snap.identity.input_revision == 2
        assert [m.message_id for m in snap.messages_page.items] == ["m-m2"]

    async def test_limit_bounds_and_bad_cursor(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        _, execution_id, _ = await _seed_chain(dao)
        for bad_limit in (0, 51):
            with pytest.raises(AgentServiceError) as exc:
                await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE, limit=bad_limit)
            assert _code_of(exc) == AgentErrorCode.INPUT_INVALID
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE, message_cursor="not-a-cursor")
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestPagingAndFailClosed:
    async def test_message_cursor_continuation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        for index in range(1, 4):
            await _seed_message(dao, session_id, execution_id, f"p{index}", round_id=round_id, body_text=f"消息{index}")
        page1 = await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE, limit=2)
        assert [m.session_seq for m in page1.messages_page.items] == [3, 2]
        assert page1.messages_page.has_more is True
        assert page1.messages_page.next_cursor == "m:2"
        page2 = await dao.get_execution_snapshot(
            execution_id, user_scope=USER_SCOPE, limit=2, message_cursor=page1.messages_page.next_cursor
        )
        assert [m.session_seq for m in page2.messages_page.items] == [1]
        assert page2.messages_page.has_more is False

    async def test_corrupt_execution_row_fail_closed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        _, execution_id, _ = await _seed_chain(dao)
        # 空串身份列（无值域 CHECK）→ 严格解码 fail-closed（R21）
        async with dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_execution SET node_id = '' WHERE user_scope = :u AND execution_id = :e"),
                {"u": USER_SCOPE, "e": execution_id},
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE)
        assert _code_of(exc) == AgentErrorCode.OPERATION_UNKNOWN

    async def test_corrupt_operation_row_fail_closed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        await _seed_snapshot_extras(dao, execution_id, round_id)
        async with dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_operation SET io_state = '' WHERE user_scope = :u AND operation_id = :o"),
                {"u": USER_SCOPE, "o": "op-snap"},
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.get_execution_snapshot(execution_id, user_scope=USER_SCOPE)
        assert _code_of(exc) == AgentErrorCode.OPERATION_UNKNOWN
