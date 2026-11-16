"""AgentDao C-03-1 可靠事件写入/读取集成测试（§4.4 包络 + §9.6 统一事件存储）。

真实 embedded PG 上的单事务语义验证：

- ``record_event``：session/execution 行锁下单调水位分配（session_seq/
  execution_seq，R22 GREATEST 写回）+ agent_message 行落定；
- 同 (session_id, event_id) 同载荷重放查回原事实（replayed=True，零新行）；
  异载荷 INPUT_CONFLICT；执行事件身份一致性（round 归属核定）；正文互斥；
- ``read_events``：after_seq 升序分页 + has_more + user_scope 隔离 + 严格
  解码（坏载荷 fail-closed OPERATION_UNKNOWN）。

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
    EventReadQuery,
    MessageRole,
    RecordEventRequest,
    decode_research_event,
    derive_event_identity,
    encode_research_event,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentExecution, AgentMessage, AgentRound, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "synthetic-user"


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


async def _seed_session(dao: AgentDao, session_id: str = "s-evt") -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="evt session")
        )


async def _seed_execution_chain(dao: AgentDao) -> tuple[str, str, str]:
    """session + execution + round（§9.4/§9.5 最小合法行）→ (session_id, execution_id, round_id)。"""

    session_id, execution_id, round_id = "s-evt-exec", "exec-evt", "round-evt"
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="evt exec session")
        )
        await conn.execute(
            insert(AgentExecution).values(
                user_scope=USER_SCOPE,
                execution_id=execution_id,
                session_id=session_id,
                execution_kind="root",
                source_module="ui",
                business_request_id="req-evt",
                node_id="root",
                display_state="queued",
                goal_coverage="unknown",
                intent_digest="a" * 64,
            )
        )
        await conn.execute(
            insert(AgentRound).values(
                user_scope=USER_SCOPE,
                round_id=round_id,
                execution_id=execution_id,
                round_number=1,
                workflow_id="wf-evt-1",
                workflow_code_version="wcv-1",
                run_epoch=1,
                input_revision=1,
                plan_revision=1,
                goal_coverage="unknown",
                final_saved=False,
                convergence_confirmed=False,
                active_round=True,
            )
        )
    return session_id, execution_id, round_id


def _session_request(session_id: str = "s-evt", native_event_id: str = "native-1", **overrides) -> RecordEventRequest:
    values: dict = {
        "user_scope": USER_SCOPE,
        "session_id": session_id,
        "message_id": f"m-{native_event_id}",
        "native_event_id": native_event_id,
        "event_kind": EventKind.SESSION_CHANGED,
        "subject_id": session_id,
        "subject_revision": 1,
        "payload_kind": "session_changed.v1",
        "payload_canonical_json": json_doc({"revision": 1}),
        "occurred_at": "2026-06-01T12:00:00.000000Z",
    }
    values.update(overrides)
    return RecordEventRequest(**values)


def _execution_request(
    session_id: str, execution_id: str, native_event_id: str = "native-exec-1", **overrides
) -> RecordEventRequest:
    values: dict = {
        "user_scope": USER_SCOPE,
        "session_id": session_id,
        "message_id": f"m-{native_event_id}",
        "native_event_id": native_event_id,
        "event_kind": EventKind.PUBLIC_MESSAGE_SAVED,
        "subject_id": f"m-{native_event_id}",
        "subject_revision": 1,
        "payload_kind": "public_message_saved.v1",
        "payload_canonical_json": json_doc({"role": "assistant"}),
        "occurred_at": "2026-06-01T12:00:00.000000Z",
        "execution_id": execution_id,
        "node_id": "root",
        "round_id": None,
        "role": MessageRole.ASSISTANT,
        "body_text": "公开正文",
        "search_text": "公开正文",
    }
    values.update(overrides)
    return RecordEventRequest(**values)


def _code_of(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> AgentErrorCode:
    assert isinstance(excinfo.value, AgentServiceError)
    return excinfo.value.code


class TestRecordEvent:
    async def test_session_event_appends_and_monotonic_watermark(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        fact = await dao.record_event(_session_request())
        assert fact.replayed is False
        assert fact.session_seq == 1
        assert fact.event_id == derive_event_identity(
            execution_id=None, round_id=None, node_id=None, native_event_id="native-1"
        )
        fact2 = await dao.record_event(_session_request(native_event_id="native-2"))
        assert fact2.session_seq == 2
        async with dao.engine.connect() as conn:
            watermark = (
                await conn.execute(
                    sa.select(AgentSession.event_seq).where(
                        AgentSession.user_scope == USER_SCOPE, AgentSession.session_id == "s-evt"
                    )
                )
            ).scalar_one()
            assert watermark == 2
            count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentMessage)
                    .where(AgentMessage.user_scope == USER_SCOPE, AgentMessage.session_id == "s-evt")
                )
            ).scalar_one()
            assert count == 2


class TestExecutionEvents:
    async def test_execution_event_allocates_both_watermarks(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_execution_chain(dao)
        fact = await dao.record_event(_execution_request(session_id, execution_id, round_id=round_id))
        assert fact.replayed is False
        assert fact.execution_seq == 1
        assert fact.execution_event_seq == 1
        async with dao.engine.connect() as conn:
            watermark = (
                await conn.execute(
                    sa.select(AgentExecution.event_seq).where(
                        AgentExecution.user_scope == USER_SCOPE, AgentExecution.execution_id == execution_id
                    )
                )
            ).scalar_one()
            assert watermark == 1
            row = (
                await conn.execute(
                    sa.select(AgentMessage).where(
                        AgentMessage.user_scope == USER_SCOPE, AgentMessage.message_id == "m-native-exec-1"
                    )
                )
            ).first()
            assert row is not None
            assert row.role == "assistant"
            assert row.body_text == "公开正文"
            assert row.search_text == "公开正文"
            assert row.round_id == round_id

    async def test_execution_event_requires_round(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, _ = await _seed_execution_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_execution_request(session_id, execution_id, round_id=None))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_round_must_belong_to_execution(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, _ = await _seed_execution_chain(dao)
        # 异执行轮：另建 execution + round，事件声称归属 exec-evt 但 round 属 exec-other
        async with dao.engine.begin() as conn:
            await conn.execute(
                insert(AgentExecution).values(
                    user_scope=USER_SCOPE,
                    execution_id="exec-other",
                    session_id=session_id,
                    execution_kind="root",
                    source_module="ui",
                    business_request_id="req-evt-2",
                    node_id="root",
                    display_state="queued",
                    goal_coverage="unknown",
                    intent_digest="b" * 64,
                )
            )
            await conn.execute(
                insert(AgentRound).values(
                    user_scope=USER_SCOPE,
                    round_id="round-other",
                    execution_id="exec-other",
                    round_number=1,
                    workflow_id="wf-evt-2",
                    workflow_code_version="wcv-1",
                    run_epoch=1,
                    input_revision=1,
                    plan_revision=1,
                    goal_coverage="unknown",
                    final_saved=False,
                    convergence_confirmed=False,
                    active_round=True,
                )
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_execution_request(session_id, execution_id, round_id="round-other"))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_replay_same_payload_returns_original(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        request = _session_request()
        first = await dao.record_event(request)
        second = await dao.record_event(request)
        assert second.replayed is True
        assert second.session_seq == first.session_seq
        assert second.event_id == first.event_id
        async with dao.engine.connect() as conn:
            count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentMessage)
                    .where(AgentMessage.user_scope == USER_SCOPE, AgentMessage.session_id == "s-evt")
                )
            ).scalar_one()
            assert count == 1

    async def test_same_identity_different_payload_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.record_event(_session_request())
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(subject_revision=2))
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT

    async def test_same_payload_different_role_or_body_conflicts(self, real_embedded_pg) -> None:
        """同事件身份同 payload 换 role/正文 → INPUT_CONFLICT（检视 D05），零新行。"""
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_execution_chain(dao)
        await dao.record_event(_execution_request(session_id, execution_id, round_id=round_id))
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(
                _execution_request(session_id, execution_id, round_id=round_id, role=MessageRole.USER)
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(
                _execution_request(session_id, execution_id, round_id=round_id, body_text="换了正文")
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT
        async with dao.engine.connect() as conn:
            count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentMessage)
                    .where(AgentMessage.user_scope == USER_SCOPE, AgentMessage.session_id == session_id)
                )
            ).scalar_one()
            assert count == 1

    async def test_same_identity_different_event_kind_conflicts(self, real_embedded_pg) -> None:
        """同事件身份同 payload 换 event_kind → INPUT_CONFLICT（检视 D05）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.record_event(_session_request())
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(event_kind=EventKind.INPUT_BOUND))
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT

    async def test_same_identity_different_message_id_conflicts(self, real_embedded_pg) -> None:
        """同事件身份换 message_id → INPUT_CONFLICT，不把请求 message_id 静默丢弃（检视 D05）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        first = await dao.record_event(_session_request())
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(message_id="m-other"))
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT
        # 原事实不变：既有 message_id 未被覆盖。
        async with dao.engine.connect() as conn:
            stored = (
                await conn.execute(sa.select(AgentMessage.message_id).where(AgentMessage.event_id == first.event_id))
            ).scalar_one()
            assert stored == "m-native-1"

    async def test_same_identity_different_occurred_at_conflicts(self, real_embedded_pg) -> None:
        """occurred_at 属冻结事件事实：同身份换时刻 → INPUT_CONFLICT，原事实不变（检视 D05）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        first = await dao.record_event(_session_request())
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(occurred_at="2026-06-01T12:00:01.000000Z"))
        assert _code_of(exc) == AgentErrorCode.INPUT_CONFLICT
        async with dao.engine.connect() as conn:
            stored = (
                await conn.execute(
                    sa.select(AgentMessage.occurred_at).where(AgentMessage.message_id == first.message_id)
                )
            ).scalar_one()
            assert stored == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    async def test_missing_session_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(session_id="no-such"))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_body_text_and_ref_mutually_exclusive(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(body_text="正文", body_ref="ref-1"))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_invalid_payload_json_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.record_event(_session_request(payload_canonical_json="{broken"))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestReadEvents:
    async def test_paging_and_monotonic_order(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        for index in range(1, 4):
            await dao.record_event(_session_request(native_event_id=f"n-{index}"))
        page1 = await dao.read_events(EventReadQuery(user_scope=USER_SCOPE, session_id="s-evt", after_seq=0, limit=2))
        assert len(page1.events) == 2
        assert page1.has_more is True
        assert page1.last_seq == 2
        assert [e.session_seq for e in page1.events] == [1, 2]
        page2 = await dao.read_events(
            EventReadQuery(user_scope=USER_SCOPE, session_id="s-evt", after_seq=page1.last_seq, limit=2)
        )
        assert len(page2.events) == 1
        assert page2.has_more is False
        assert page2.last_seq == 3
        # 包络往返一致
        assert json_doc(encode_research_event(page2.events[0])) == json_doc(
            encode_research_event(decode_research_event(encode_research_event(page2.events[0]), "$"))
        )

    async def test_user_scope_isolation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.record_event(_session_request())
        page = await dao.read_events(EventReadQuery(user_scope="other-scope", session_id="s-evt", after_seq=0))
        assert page.events == ()
        assert page.last_seq == 0

    async def test_limit_bounds(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.read_events(EventReadQuery(user_scope=USER_SCOPE, session_id="s-evt", after_seq=0, limit=501))
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_persisted_corrupt_event_kind_fail_closed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await dao.record_event(_session_request())
        # 直接破坏已保存 payload_kind（无 CHECK 的包络字段，模拟坏写入）→
        # 读取严格解码 fail-closed OPERATION_UNKNOWN（R21）
        async with dao.engine.begin() as conn:
            await conn.execute(
                sa.update(AgentMessage)
                .where(AgentMessage.user_scope == USER_SCOPE, AgentMessage.session_id == "s-evt")
                .values(payload_kind="")
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.read_events(EventReadQuery(user_scope=USER_SCOPE, session_id="s-evt", after_seq=0))
        assert _code_of(exc) == AgentErrorCode.OPERATION_UNKNOWN
