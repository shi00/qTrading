"""AgentDao C-03-2b 会话首读集成测试（§4.2 read_session + §9.13 get_session_read）。

真实 embedded PG 上一致读取截点语义验证：

- 五要素组装：summary（agent_session 行投影）/ executions 摘要 / 消息成果首屏
  分页（keyset 游标续读）/ 待决定列表 + 严格解码（坏行 fail-closed
  OPERATION_UNKNOWN，R21）；
- 消息按 session_seq DESC 游标 ``m:{seq}`` 续读；成果按可靠保存时点 DESC
  （published_at 为 NULL 时以 created_at 承载排序，工程裁决）游标
  ``a:{ts}:{id}:{ver}`` 续读；has_more=True 必携下一游标，不静默截断；
- 目标 execution/round 可选过滤：round 归属错配拒绝（精确用户域，§9.13）；
- draft_revision 提供时不符 → REVISION_CONFLICT；session 不存在 → INPUT_INVALID。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    EventKind,
    MessageRole,
    RecordEventRequest,
    SessionReadQuery,
    decode_session_read,
    json_doc,
    to_wire_dict,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentArtifact,
    AgentBodyPayload,
    AgentDecision,
    AgentExecution,
    AgentMessage,
    AgentRound,
    AgentSession,
)
from core.agent_types import sha256_hex
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
    session_id: str = "s-read",
    execution_id: str = "exec-read",
    round_id: str = "round-read",
) -> tuple[str, str, str]:
    """session + execution + round 最小合法行 → (session_id, execution_id, round_id)。"""

    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="读取会话"))
        await conn.execute(
            insert(AgentExecution).values(
                user_scope=USER_SCOPE,
                execution_id=execution_id,
                session_id=session_id,
                execution_kind="root",
                source_module="ui",
                business_request_id="req-read",
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
                workflow_id="wf-read",
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


async def _seed_message(
    dao: AgentDao,
    session_id: str,
    native_event_id: str,
    *,
    execution_id: str | None = None,
    round_id: str | None = None,
    body_text: str | None = None,
    body_ref: str | None = None,
) -> int:
    """经 record_event 真实路径落一条消息，返回 session_seq。"""

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
        node_id="root" if execution_id else None,
        role=MessageRole.ASSISTANT,
        body_text=body_text,
        body_ref=body_ref,
    )
    fact = await dao.record_event(request)
    return fact.session_seq


async def _seed_artifact(
    dao: AgentDao,
    artifact_id: str,
    execution_id: str,
    round_id: str,
    *,
    published_at: datetime | None,
    created_at: datetime,
) -> None:
    values: dict = {
        "user_scope": USER_SCOPE,
        "artifact_id": artifact_id,
        "artifact_version": 1,
        "session_id": "s-read",
        "execution_id": execution_id,
        "round_id": round_id,
        "artifact_kind": "plan",
        "input_revision": 1,
        "actual_input_digest": "b" * 64,
        "actual_model_ref": "model/main",
        "result_digest": "c" * 64,
        "source_available_state": "unknown",
        "published_at": published_at,
        "created_at": created_at,
        "updated_at": created_at,
        "summary": f"摘要 {artifact_id}",
        "body_text": f"成果正文 {artifact_id}",
    }
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentArtifact).values(**values))


async def _seed_decision(dao: AgentDao, decision_id: str, execution_id: str, round_id: str) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentDecision).values(
                user_scope=USER_SCOPE,
                decision_id=decision_id,
                session_id="s-read",
                execution_id=execution_id,
                round_id=round_id,
                node_id="root",
                decision_kind="user_choice",
                presentation_batch_seq=1,
                question_payload={"question": "选择方案", "options": ["a", "b"]},
            )
        )


async def _read(
    dao: AgentDao,
    session_id: str = "s-read",
    *,
    user_scope: str = USER_SCOPE,
    **overrides: object,
):
    """get_session_read 便捷调用（user_scope 为 DAO 独立关键字参数）。"""

    return await dao.get_session_read(SessionReadQuery(session_id=session_id, **overrides), user_scope=user_scope)


def _code_of(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> AgentErrorCode:
    assert isinstance(excinfo.value, AgentServiceError)
    return excinfo.value.code


class TestGetSessionReadBasics:
    async def test_assembles_five_elements_and_strict_roundtrip(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        await _seed_message(dao, session_id, "m1", body_text="会话级消息")
        await _seed_message(dao, session_id, "m2", execution_id=execution_id, round_id=round_id, body_text="执行消息")
        await _seed_artifact(
            dao,
            "art-1",
            execution_id,
            round_id,
            published_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
        )
        await _seed_decision(dao, "dec-1", execution_id, round_id)

        read = await _read(dao, session_id=session_id)

        # summary 投影
        assert read.summary.user_scope == USER_SCOPE
        assert read.summary.session_id == session_id
        assert read.summary.title == "读取会话"
        assert read.summary.draft_revision == 1
        assert read.summary.event_seq == 2
        assert read.summary.revision == 1
        assert read.summary.created_at.endswith("Z")
        # executions 摘要
        assert [e.execution_id for e in read.executions] == [execution_id]
        assert read.executions[0].display_state == "queued"
        assert read.executions[0].execution_kind == "root"
        assert read.executions[0].event_seq == 1
        # 消息首屏（DESC）
        assert [m.message_id for m in read.messages.items] == ["m-m2", "m-m1"]
        assert read.messages.has_more is False
        assert read.messages.next_cursor is None
        assert read.messages.items[0].body_text == "执行消息"
        assert read.messages.items[0].execution_id == execution_id
        assert read.messages.items[1].execution_id is None
        # 成果首屏
        assert [a.artifact_id for a in read.artifacts.items] == ["art-1"]
        assert read.artifacts.items[0].published_at == "2026-06-02T08:00:00.000000Z"
        assert read.artifacts.has_more is False
        # 决定
        assert [d.decision_id for d in read.decisions] == ["dec-1"]
        assert read.decisions[0].decision_kind == "user_choice"
        assert read.decisions[0].question_payload == json_doc({"question": "选择方案", "options": ["a", "b"]})
        # 严格解码往返（wire 合同一致）
        wire = to_wire_dict(read)
        assert json_doc(wire) == json_doc(to_wire_dict(decode_session_read(wire)))

    async def test_missing_session_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id="no-such")
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_user_scope_isolation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, user_scope=OTHER_SCOPE, session_id=session_id)
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_summary_branch_metadata_carried_as_canonical_json(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        async with dao.engine.begin() as conn:
            await conn.execute(
                insert(AgentSession).values(
                    user_scope=USER_SCOPE,
                    session_id="s-meta",
                    title="分支会话",
                    branch_metadata={"from_session": "s-read", "cause": "revise"},
                )
            )
        read = await _read(dao, session_id="s-meta")
        assert read.summary.parent_session_id is None
        assert read.summary.branch_metadata == json_doc({"from_session": "s-read", "cause": "revise"})


class TestMessagePaging:
    async def test_desc_pages_with_cursor_continuation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        seqs = [await _seed_message(dao, session_id, f"p{i}", body_text=f"消息{i}") for i in range(1, 5)]
        assert seqs == [1, 2, 3, 4]

        page1 = await _read(dao, session_id=session_id, limit=2)
        assert [m.session_seq for m in page1.messages.items] == [4, 3]
        assert page1.messages.has_more is True
        target = AgentDao._session_cursor_target(
            user_scope=USER_SCOPE, session_id=session_id, execution_id=None, round_id=None
        )
        assert page1.messages.next_cursor == f"m:{target}:3"

        page2 = await _read(
            dao,
            session_id=session_id,
            limit=2,
            message_cursor=page1.messages.next_cursor,
        )
        assert [m.session_seq for m in page2.messages.items] == [2, 1]
        assert page2.messages.has_more is False
        assert page2.messages.next_cursor is None

        # 游标到底后空页：has_more=False、items 空，不伪成功
        page3 = await _read(
            dao,
            session_id=session_id,
            limit=2,
            message_cursor=f"m:{target}:1",
        )
        assert page3.messages.items == ()
        assert page3.messages.has_more is False

    async def test_cursor_syntax_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        for bad_cursor in ("m:x", "3", "m:", "a:bad"):
            with pytest.raises(AgentServiceError) as exc:
                await _read(dao, session_id=session_id, message_cursor=bad_cursor)
            assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_cursor_from_other_target_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_a, _, _ = await _seed_chain(dao, session_id="s-a", execution_id="exec-a", round_id="round-a")
        session_b, _, _ = await _seed_chain(dao, session_id="s-b", execution_id="exec-b", round_id="round-b")
        for index in range(1, 4):
            await _seed_message(dao, session_a, f"a{index}", body_text=f"甲{index}")
            await _seed_message(dao, session_b, f"b{index}", body_text=f"乙{index}")
        page1 = await _read(dao, session_id=session_a, limit=2)
        cursor = page1.messages.next_cursor
        assert cursor is not None
        # 跨会话复用同一游标 → 显式失效（不静默套用他会话的翻页位置）
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id=session_b, limit=2, message_cursor=cursor)
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT
        # 同会话但目标过滤改变（限定执行）→ 目标摘要不同，同样失效
        with pytest.raises(AgentServiceError) as exc2:
            await _read(
                dao,
                session_id=session_a,
                limit=2,
                execution_id="exec-a",
                message_cursor=cursor,
            )
        assert _code_of(exc2) == AgentErrorCode.REVISION_CONFLICT

    async def test_limit_bounds(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        for bad_limit in (0, 51):
            with pytest.raises(AgentServiceError) as exc:
                await _read(dao, session_id=session_id, limit=bad_limit)
            assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestArtifactPaging:
    async def test_desc_pages_with_cursor_and_unpublished_sorted_last(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        # published DESC：art-late > art-mid > art-early > 未发布（NULL，按 created_at 排最后）
        await _seed_artifact(
            dao,
            "art-early",
            execution_id,
            round_id,
            published_at=datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC),
        )
        await _seed_artifact(
            dao,
            "art-mid",
            execution_id,
            round_id,
            published_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
        )
        await _seed_artifact(
            dao,
            "art-late",
            execution_id,
            round_id,
            published_at=datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 6, 3, 8, 0, 0, tzinfo=UTC),
        )
        await _seed_artifact(
            dao,
            "art-unpub",
            execution_id,
            round_id,
            published_at=None,
            created_at=datetime(2026, 5, 30, 8, 0, 0, tzinfo=UTC),
        )

        page1 = await _read(dao, session_id=session_id, limit=2)
        assert [a.artifact_id for a in page1.artifacts.items] == ["art-late", "art-mid"]
        assert page1.artifacts.has_more is True
        target = AgentDao._session_cursor_target(
            user_scope=USER_SCOPE, session_id=session_id, execution_id=None, round_id=None
        )
        assert page1.artifacts.next_cursor == f"a:{target}:2026-06-02T08:00:00.000000Z:art-mid:1"

        page2 = await _read(
            dao,
            session_id=session_id,
            limit=2,
            artifact_cursor=page1.artifacts.next_cursor,
        )
        # 未发布行不静默截断：以 created_at 承载排序续读覆盖
        assert [a.artifact_id for a in page2.artifacts.items] == ["art-early", "art-unpub"]
        assert page2.artifacts.has_more is False
        assert page2.artifacts.next_cursor is None

    async def test_artifact_cursor_syntax_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id=session_id, artifact_cursor="a:99:art:1")
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestTargetFilters:
    async def test_execution_target_filters_lists(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        await _seed_message(dao, session_id, "s1", body_text="会话级")
        await _seed_message(dao, session_id, "e1", execution_id=execution_id, round_id=round_id, body_text="执行级")
        await _seed_artifact(
            dao,
            "art-e",
            execution_id,
            round_id,
            published_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
            created_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
        )
        await _seed_decision(dao, "dec-e", execution_id, round_id)

        read = await _read(dao, session_id=session_id, execution_id=execution_id)
        assert [e.execution_id for e in read.executions] == [execution_id]
        assert [m.message_id for m in read.messages.items] == ["m-e1"]
        assert [a.artifact_id for a in read.artifacts.items] == ["art-e"]
        assert [d.decision_id for d in read.decisions] == ["dec-e"]

    async def test_round_target_requires_execution(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, round_id = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id=session_id, round_id=round_id)
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_round_not_in_execution_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await _read(
                dao,
                session_id=session_id,
                execution_id="exec-read",
                round_id="round-elsewhere",
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_execution_not_in_session_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id=session_id, execution_id="exec-elsewhere")
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestRevisionAndFailClosed:
    async def test_draft_revision_mismatch_conflict(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id=session_id, draft_revision=99)
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT

    async def test_draft_revision_match_ok(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        read = await _read(dao, session_id=session_id, draft_revision=1)
        assert read.summary.draft_revision == 1

    async def test_corrupt_execution_row_fail_closed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, _, _ = await _seed_chain(dao)
        # 破坏无值域 CHECK 守护的身份列（模拟坏写入；空串合法入库但违反读取合同）→
        # 严格解码 fail-closed OPERATION_UNKNOWN（R21）
        async with dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_execution SET node_id = '' WHERE user_scope = :u AND session_id = :s"),
                {"u": USER_SCOPE, "s": session_id},
            )
        with pytest.raises(AgentServiceError) as exc:
            await _read(dao, session_id=session_id)
        assert _code_of(exc) == AgentErrorCode.OPERATION_UNKNOWN

    async def test_message_body_ref_item_kept_without_body(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        session_id, execution_id, round_id = await _seed_chain(dao)
        body_ref = "bs-read-ref1"
        # body_ref 环引用双方均 DEFERRABLE：payload（message_id FK）与 message（body_ref FK）
        # 须同事务提交（§9.10.1 owner 环核定），不能用 record_event 独立事务拆开
        payload_bytes = f"正文块 {body_ref}".encode()
        async with dao.engine.begin() as conn:
            await conn.execute(
                insert(AgentBodyPayload).values(
                    user_scope=USER_SCOPE,
                    body_ref=body_ref,
                    owner_kind="message",
                    owner_id="m-ref1",
                    owner_version="1",
                    message_id="m-ref1",
                    encoding="utf-8",
                    payload_bytes=payload_bytes,
                    byte_length=len(payload_bytes),
                    payload_digest=sha256_hex(payload_bytes),
                )
            )
            await conn.execute(
                insert(AgentMessage).values(
                    user_scope=USER_SCOPE,
                    message_id="m-ref1",
                    session_id=session_id,
                    execution_id=execution_id,
                    round_id=round_id,
                    node_id="root",
                    event_id="evt-ref1",
                    session_seq=1,
                    execution_seq=1,
                    event_kind="public_message_saved",
                    payload_kind="public_message_saved.v1",
                    subject_id="m-ref1",
                    subject_revision=1,
                    role="assistant",
                    body_ref=body_ref,
                    occurred_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
                    committed_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
                )
            )
        read = await _read(dao, session_id=session_id)
        item = read.messages.items[0]
        assert item.body_text is None
        assert item.body_ref == body_ref
        # wire 往返仍保 body_ref（不吞正文引用，R21）
        wire = json.loads(json_doc(to_wire_dict(item)))
        assert wire["body_ref"] == body_ref
