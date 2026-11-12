"""AgentDao C-03-3b 历史搜索集成测试（§4.2 R02 search_history + HistoryPage）。

真实 embedded PG 上验证：

- 三范围扫描：all_saved（全用户域）/current_session（限定主会话）/
  current_child（限定准确叶，parent/node_id 身份错配 INPUT_INVALID）；
- 过滤：query_text 占位 LIKE（C-03-4 全文索引前）、message_role_filter、
  type_filter（main=会话级消息/child=执行内）、round_selection exact、
  agent_filter execution_agent、date 窗口（time_basis 列映射）；
- 排序与游标：sort_moment + 锚 ID keyset（newest_first DESC/oldest_first ASC），
  fixed_upper_bound 首读生成、游标首段续读复用；limit 与游标不入 digest；
- HistoryPage：query_digest/revision_fingerprint（水位变化则指纹变化）/
  无命中空页 / REVISION_CONFLICT（翻页时覆盖域成员消失）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    EventKind,
    HistoryAgentFilter,
    HistoryChildTarget,
    HistoryQuery,
    HistoryRoundSelection,
    MessageRole,
    RecordEventRequest,
    compute_history_query_digest,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentArtifact, AgentExecution, AgentRound, AgentSession
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


async def _seed_session(dao: AgentDao, session_id: str, *, title: str = "历史会话") -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title=title))


async def _seed_execution(
    dao: AgentDao,
    execution_id: str,
    session_id: str,
    *,
    node_id: str = "root",
    parent_execution_id: str | None = None,
) -> None:
    values: dict = {
        "user_scope": USER_SCOPE,
        "execution_id": execution_id,
        "session_id": session_id,
        "execution_kind": "leaf" if parent_execution_id else "root",
        "node_id": node_id,
        "display_state": "queued",
        "goal_coverage": "unknown",
    }
    if parent_execution_id:
        values["parent_execution_id"] = parent_execution_id
    else:
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
        values["intent_digest"] = "a" * 64
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_round(dao: AgentDao, round_id: str, execution_id: str, *, number: int = 1) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentRound).values(
                user_scope=USER_SCOPE,
                round_id=round_id,
                execution_id=execution_id,
                round_number=number,
                workflow_id=f"wf-{round_id}",
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


async def _seed_message(
    dao: AgentDao,
    session_id: str,
    native_event_id: str,
    *,
    execution_id: str | None = None,
    round_id: str | None = None,
    role: MessageRole = MessageRole.ASSISTANT,
    body_text: str = "默认正文",
    occurred_at: str = "2026-06-01T12:00:00.000000Z",
    node_id: str | None = None,
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
        payload_canonical_json=json_doc({"role": role.value}),
        occurred_at=occurred_at,
        execution_id=execution_id,
        round_id=round_id,
        node_id=node_id if node_id is not None else ("root" if execution_id else None),
        role=role,
        body_text=body_text,
        search_text=body_text,
    )
    fact = await dao.record_event(request)
    return fact.session_seq


async def _seed_artifact(
    dao: AgentDao,
    artifact_id: str,
    session_id: str,
    execution_id: str,
    round_id: str,
    *,
    summary: str = "成果摘要",
    created_at: datetime | None = None,
    input_revision: int = 1,
) -> None:
    moment = created_at or datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
    async with dao.engine.begin() as conn:
        await conn.execute(
            insert(AgentArtifact).values(
                user_scope=USER_SCOPE,
                artifact_id=artifact_id,
                artifact_version=1,
                session_id=session_id,
                execution_id=execution_id,
                round_id=round_id,
                artifact_kind="plan",
                input_revision=input_revision,
                actual_input_digest="b" * 64,
                actual_model_ref="model/main",
                result_digest="c" * 64,
                source_available_state="unknown",
                published_at=moment,
                created_at=moment,
                updated_at=moment,
                summary=summary,
                body_text=f"成果正文 {artifact_id}",
            )
        )


def _code_of(excinfo: pytest.ExceptionInfo[AgentServiceError]) -> AgentErrorCode:
    assert isinstance(excinfo.value, AgentServiceError)
    return excinfo.value.code


def _q(**overrides: object) -> HistoryQuery:
    values: dict[str, object] = {
        "scope": "all_saved",
        "round_selection": HistoryRoundSelection(kind="all_rounds"),
        "query_text": "",
        "time_basis": "created",
        "sort": "newest_first",
    }
    values.update(overrides)
    return HistoryQuery(**values)  # pyright: ignore[reportArgumentType]


class TestScopeScanning:
    async def test_all_saved_scans_user_domain(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_message(dao, "s-a", "a1", body_text="会话A消息")
        await _seed_message(dao, "s-b", "b1", body_text="会话B消息")
        await _seed_execution(dao, "exec-b", "s-b")
        await _seed_round(dao, "round-b", "exec-b")
        await _seed_artifact(dao, "art-b", "s-b", "exec-b", "round-b")

        page = await dao.search_history(_q(), user_scope=USER_SCOPE)

        assert len(page.hits) == 3
        assert {hit.session_id for hit in page.hits} == {"s-a", "s-b"}
        # newest_first：sort_moment 单调不增（消息 created_at 为落库时刻，不依赖跨表绝对序）
        moments = [hit.sort_moment for hit in page.hits]
        assert moments == sorted(moments, reverse=True)
        artifact_hit = next(hit for hit in page.hits if hit.hit_kind == "artifact")
        assert artifact_hit.artifact_id == "art-b"
        assert artifact_hit.summary_text == "成果摘要"
        # digest/指纹/上界
        assert page.query_digest == compute_history_query_digest(_q())
        assert len(page.revision_fingerprint) == 64
        assert page.revision_fingerprint != "0" * 64
        assert page.fixed_upper_bound.endswith("Z")
        assert page.next_cursor is None

    async def test_user_scope_isolation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "a1", body_text="会话A消息")
        page = await dao.search_history(_q(), user_scope=OTHER_SCOPE)
        assert page.hits == ()

    async def test_current_session_limits_scope(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_session(dao, "s-b")
        await _seed_message(dao, "s-a", "a1", body_text="A1")
        await _seed_message(dao, "s-b", "b1", body_text="B1")
        page = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-a1"]

    async def test_current_session_missing_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(_q(scope="current_session", session_id="no-such"), user_scope=USER_SCOPE)
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID

    async def test_current_child_limits_and_rejects_mismatch(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-root", "s-a")
        await _seed_execution(dao, "exec-leaf", "s-a", node_id="leaf", parent_execution_id="exec-root")
        await _seed_round(dao, "round-leaf", "exec-leaf")
        await _seed_message(dao, "s-a", "root1", body_text="根执行消息")
        await _seed_message(
            dao, "s-a", "leaf1", execution_id="exec-leaf", round_id="round-leaf", body_text="叶消息", node_id="leaf"
        )
        await _seed_artifact(dao, "art-leaf", "s-a", "exec-leaf", "round-leaf")

        target = HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf")
        page = await dao.search_history(
            _q(scope="current_child", session_id="s-a", child_target=target), user_scope=USER_SCOPE
        )
        assert {hit.message_id or hit.artifact_id for hit in page.hits} == {"m-leaf1", "art-leaf"}

        wrong_target = HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="else")
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_child", session_id="s-a", child_target=wrong_target), user_scope=USER_SCOPE
            )
        assert _code_of(exc) == AgentErrorCode.INPUT_INVALID


class TestFilters:
    async def test_query_text_like_placeholder(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "hit1", body_text="包含回撤关键词的消息")
        await _seed_message(dao, "s-a", "miss1", body_text="无关消息")
        page = await dao.search_history(_q(query_text="回撤"), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-hit1"]
        empty = await dao.search_history(_q(query_text="不存在的词"), user_scope=USER_SCOPE)
        assert empty.hits == ()
        assert empty.next_cursor is None

    async def test_message_role_filter_applies_to_messages_only(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "u1", role=MessageRole.USER, body_text="用户消息")
        await _seed_message(dao, "s-a", "as1", role=MessageRole.ASSISTANT, body_text="助手消息")
        await _seed_artifact(dao, "art-a", "s-a", "exec-a", "round-a")
        page = await dao.search_history(
            _q(scope="current_session", session_id="s-a", message_role_filter="user"), user_scope=USER_SCOPE
        )
        # message_role_filter 是消息维度：消息按 role 过滤，成果不受排除（独立维度，L408）
        message_hits = [hit for hit in page.hits if hit.hit_kind == "message"]
        assert [hit.message_id for hit in message_hits] == ["m-u1"]
        assert any(hit.hit_kind == "artifact" and hit.artifact_id == "art-a" for hit in page.hits)

    async def test_type_filter_main_child(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "main1", body_text="会话级")
        await _seed_message(dao, "s-a", "child1", execution_id="exec-a", round_id="round-a", body_text="执行内")
        page_main = await dao.search_history(
            _q(scope="current_session", session_id="s-a", type_filter="main"), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page_main.hits] == ["m-main1"]
        page_child = await dao.search_history(
            _q(scope="current_session", session_id="s-a", type_filter="child"), user_scope=USER_SCOPE
        )
        assert [hit.message_id for hit in page_child.hits] == ["m-child1"]

    async def test_round_selection_exact(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a", number=1)
        await _seed_message(dao, "s-a", "r1", execution_id="exec-a", round_id="round-a", body_text="第一轮")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                round_selection=HistoryRoundSelection(kind="exact", execution_id="exec-a", round_id="round-a"),
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-r1"]

    async def test_agent_filter_execution_agent(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a", node_id="root")
        await _seed_execution(dao, "exec-b", "s-a", node_id="leaf", parent_execution_id="exec-a")
        await _seed_round(dao, "round-a", "exec-a")
        await _seed_message(dao, "s-a", "ma", execution_id="exec-a", round_id="round-a", body_text="A")
        await _seed_message(dao, "s-a", "mb", body_text="B")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                agent_filter=HistoryAgentFilter(
                    kind="execution_agent", execution_id="exec-a", node_id="root", round_id="round-a"
                ),
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-ma"]

    async def test_date_window_on_time_basis_column(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_message(dao, "s-a", "d1", body_text="六月", occurred_at="2026-06-01T12:00:00.000000Z")
        await _seed_message(dao, "s-a", "d2", body_text="七月", occurred_at="2026-07-01T12:00:00.000000Z")
        page = await dao.search_history(
            _q(
                scope="current_session",
                session_id="s-a",
                time_basis="message",
                date_from="2026-06-01",
                date_to="2026-06-30",
            ),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page.hits] == ["m-d1"]


class TestPagingAndFingerprint:
    async def test_newest_first_cursor_continuation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 4):
            await _seed_message(
                dao,
                "s-a",
                f"p{index}",
                body_text=f"消息{index}",
                occurred_at=f"2026-06-0{index}T12:00:00.000000Z",
            )
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=2), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page1.hits] == ["m-p3", "m-p2"]
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        page2 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", limit=2, cursor=cursor_value),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page2.hits] == ["m-p1"]
        assert page2.next_cursor is None
        # 翻页共享同一 query_digest（limit/游标不入摘要）
        assert page2.query_digest == page1.query_digest
        # 固定上界延续首读值
        assert page2.fixed_upper_bound == page1.fixed_upper_bound

    async def test_oldest_first_cursor_continuation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        for index in range(1, 4):
            await _seed_message(
                dao,
                "s-a",
                f"o{index}",
                body_text=f"消息{index}",
                occurred_at=f"2026-06-0{index}T12:00:00.000000Z",
            )
        page1 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", sort="oldest_first", limit=2),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page1.hits] == ["m-o1", "m-o2"]
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        page2 = await dao.search_history(
            _q(scope="current_session", session_id="s-a", sort="oldest_first", limit=2, cursor=cursor_value),
            user_scope=USER_SCOPE,
        )
        assert [hit.message_id for hit in page2.hits] == ["m-o3"]

    async def test_fingerprint_changes_with_watermark(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        await _seed_message(dao, "s-a", "f1", body_text="新消息")
        page2 = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        assert page1.revision_fingerprint != page2.revision_fingerprint

    async def test_cursor_reuse_conflict_when_target_removed(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        await _seed_execution(dao, "exec-a", "s-a")
        await _seed_round(dao, "round-a", "exec-a")
        for index in range(1, 4):
            await _seed_message(
                dao, "s-a", f"c{index}", execution_id="exec-a", round_id="round-a", body_text=f"消息{index}"
            )
        page1 = await dao.search_history(_q(scope="current_session", session_id="s-a", limit=2), user_scope=USER_SCOPE)
        cursor_value = page1.next_cursor or ""
        assert cursor_value.startswith("h:")
        # 覆盖域成员消失（模拟清理删行，按 FK 依赖序）→ 翻页 REVISION_CONFLICT
        async with dao.engine.begin() as conn:
            await conn.execute(text("DELETE FROM agent_message WHERE user_scope = :u"), {"u": USER_SCOPE})
            await conn.execute(text("DELETE FROM agent_artifact WHERE user_scope = :u"), {"u": USER_SCOPE})
            await conn.execute(text("DELETE FROM agent_round WHERE user_scope = :u"), {"u": USER_SCOPE})
            await conn.execute(text("DELETE FROM agent_execution WHERE user_scope = :u"), {"u": USER_SCOPE})
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(
                _q(scope="current_session", session_id="s-a", limit=2, cursor=cursor_value),
                user_scope=USER_SCOPE,
            )
        assert _code_of(exc) == AgentErrorCode.REVISION_CONFLICT

    async def test_summary_text_truncated_not_full_body(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "s-a")
        long_body = "长" * 500
        await _seed_message(dao, "s-a", "long1", body_text=long_body)
        page = await dao.search_history(_q(scope="current_session", session_id="s-a"), user_scope=USER_SCOPE)
        summary = page.hits[0].summary_text or ""
        assert 0 < len(summary) <= 200
        assert len(summary) < len(long_body)
