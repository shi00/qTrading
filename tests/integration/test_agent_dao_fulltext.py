"""AgentDao C-03-4 全文搜索投影集成测试（§9.6 search_text + §9.13 全文索引）。

真实 embedded PG 上验证：

- record_event 将内联公开正文（body_text）同事务投影到 search_text（§9.6
  「仅可读公开文本；清理同时删除」；§9.13「全文投影同事务」）；
- search_history 的 query_text 经 search_text ILIKE 子串匹配（含尾词），用户
  输入中的 LIKE 通配符（%/_）按字面量匹配（转义，不误匹配）；
- GIN tsvector 索引（idx_agent_message_search，C-01 0046 建立）随库存在；
  工程裁决：不用 to_tsvector 查询（simple 分词无法支撑中文），保持 ILIKE
  子串语义，索引/查询演化留痕（pg_trgm 备选未启用）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    EventKind,
    HistoryQuery,
    HistoryRoundSelection,
    MessageRole,
    RecordEventRequest,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
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


async def _seed_session(dao: AgentDao, session_id: str = "s-ft") -> None:
    from sqlalchemy import insert

    from data.persistence.models import AgentSession

    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="全文会话"))


async def _record_message(
    dao: AgentDao,
    native_event_id: str,
    *,
    body_text: str | None = None,
    body_ref: str | None = None,
    search_text: str | None = None,
) -> int:
    request = RecordEventRequest(
        user_scope=USER_SCOPE,
        session_id="s-ft",
        message_id=f"m-{native_event_id}",
        native_event_id=native_event_id,
        event_kind=EventKind.PUBLIC_MESSAGE_SAVED,
        subject_id=f"m-{native_event_id}",
        subject_revision=1,
        payload_kind="public_message_saved.v1",
        payload_canonical_json=json_doc({"role": "assistant"}),
        occurred_at="2026-06-01T12:00:00.000000Z",
        role=MessageRole.ASSISTANT,
        body_text=body_text,
        body_ref=body_ref,
        search_text=search_text,
    )
    fact = await dao.record_event(request)
    return fact.session_seq


async def _search_text_of(dao: AgentDao, message_id: str) -> str | None:
    from sqlalchemy import select

    from data.persistence.models import AgentMessage

    async with dao.engine.begin() as conn:
        row = (
            await conn.execute(
                select(AgentMessage.search_text).where(
                    AgentMessage.user_scope == USER_SCOPE, AgentMessage.message_id == message_id
                )
            )
        ).first()
    return None if row is None else row.search_text


def _q(query_text: str) -> HistoryQuery:
    return HistoryQuery(
        scope="current_session",
        session_id="s-ft",
        round_selection=HistoryRoundSelection(kind="all_rounds"),
        query_text=query_text,
        time_basis="created",
        sort="newest_first",
    )


class TestSearchTextProjection:
    async def test_record_event_projects_declared_search_text(self, real_embedded_pg) -> None:
        # 投影合同（§9.6）：search_text 由调用方按公开性判定传入，DAO 同事务落列
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record_message(dao, "p1", body_text="包含回撤分析的公开正文", search_text="包含回撤分析的公开正文")
        assert await _search_text_of(dao, "m-p1") == "包含回撤分析的公开正文"

    async def test_record_event_without_declared_projection_stays_null(self, real_embedded_pg) -> None:
        # 未声明投影（search_text=None）→ 不自动以 body_text 兜底（公开性由调用方判定，留痕）
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record_message(dao, "r1", body_text="未声明投影的正文")
        assert await _search_text_of(dao, "m-r1") is None

    async def test_gin_index_present_after_upgrade(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        async with dao.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE indexname = 'idx_agent_message_search' "
                        "AND tablename = 'agent_message'"
                    )
                )
            ).first()
        assert row is not None
        assert "to_tsvector" in row.indexdef
        assert "gin" in row.indexdef.lower()


class TestSearchQuerySemantics:
    async def test_query_text_matches_tail_of_body(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record_message(
            dao, "t1", body_text="正文开头，尾部是阶段性结论词", search_text="正文开头，尾部是阶段性结论词"
        )
        await _record_message(dao, "t2", body_text="无关消息", search_text="无关消息")
        page = await dao.search_history(_q("阶段性结论"), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-t1"]

    async def test_query_text_wildcards_matched_literally(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        # 正文字面含 % 与 _；通配符不展开为任意匹配
        await _record_message(dao, "w1", body_text="涨幅100%确定_度", search_text="涨幅100%确定_度")
        await _record_message(dao, "w2", body_text="涨幅任意值任意字符X度", search_text="涨幅任意值任意字符X度")
        page = await dao.search_history(_q("100%确定_度"), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-w1"]

    async def test_query_text_empty_means_no_keyword_filter(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record_message(dao, "e1", body_text="任意正文", search_text="任意正文")
        page = await dao.search_history(_q(""), user_scope=USER_SCOPE)
        assert [hit.message_id for hit in page.hits] == ["m-e1"]

    async def test_no_hits_is_explicit_empty_page(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record_message(dao, "n1", body_text="正文", search_text="正文")
        page = await dao.search_history(_q("不存在的词"), user_scope=USER_SCOPE)
        assert page.hits == ()
        assert page.next_cursor is None

    async def test_corrupt_event_row_fail_closed_on_search(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record_message(dao, "c1", body_text="正文", search_text="正文")
        # 破坏无值域 CHECK 的 payload_kind 列（空串合法入库但违反读取合同）→ fail-closed
        async with dao.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_message SET payload_kind = '' WHERE user_scope = :u AND message_id = 'm-c1'"),
                {"u": USER_SCOPE},
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.search_history(_q("正文"), user_scope=USER_SCOPE)
        assert exc.value.code == AgentErrorCode.OPERATION_UNKNOWN
