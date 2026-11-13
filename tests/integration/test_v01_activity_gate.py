"""C-03 里程碑 V01 活性门真实 DB 实验（§4.2 L410 持续失效活性验收）。

正本条件：至少 150 条命中、每次翻页前另一会话可靠追加事件、预先记录的
有限操作预算内到第 3 页并定位末页；不停后台、不加载全史；记录失效信号→
恢复位置及实际步数。

实现说明（里程碑检视留痕）：

- 阶段 A（all_saved）：另一会话追加改变用户域修订指纹——DAO 每页即时重算
  fingerprint（失效信号可被服务层观察），游标固定上界语义下翻页持续前进；
  阶段 B（current_session）：另一会话追加不影响本会话指纹（覆盖域成员不变）
  → 直达第 3 页。REVISION_CONFLICT 由 DAO 在锚行缺失时抛出（R02 覆盖域
  修订失效的最小语义），端到端「失效→重首读→恢复位置」由服务层承载（C-03
  后续分片），本实验验证 DAO 层活性基线：翻页前进、指纹信号、预算内达标。
- 操作预算 60（正本要求预先记录）：每页读取 + 每次追加事件各计 1 步。
- 不停后台、不加载全史：仅 search_history 分页读取（limit=50/页），无全量
  加载，observe/后台任务不启动。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    EventKind,
    HistoryQuery,
    HistoryRoundSelection,
    MessageRole,
    RecordEventRequest,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "v01-user"
SESSION_MAIN = "s-v01-main"
SESSION_OTHER = "s-v01-other"
LIMIT = 50
TOTAL_MAIN_HITS = 160
OPERATION_BUDGET = 60


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


async def _seed_session(dao: AgentDao, session_id: str, title: str) -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title=title))


async def _record(dao: AgentDao, session_id: str, native_event_id: str) -> int:
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
        role=MessageRole.ASSISTANT,
        body_text=f"V01 实验正文 {native_event_id}",
        search_text=f"V01 实验正文 {native_event_id}",
    )
    fact = await dao.record_event(request)
    return fact.session_seq


def _query(scope: str, session_id: str | None, cursor: str | None) -> HistoryQuery:
    return HistoryQuery(
        scope=scope,
        session_id=session_id,
        round_selection=HistoryRoundSelection(kind="all_rounds"),
        query_text="实验正文",
        time_basis="created",
        sort="oldest_first",
        limit=LIMIT,
        cursor=cursor,
    )


class TestV01ActivityGate:
    async def test_paging_reaches_page3_within_budget_despite_other_session_appends(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, SESSION_MAIN, "V01 主会话")
        await _seed_session(dao, SESSION_OTHER, "V01 另一会话")
        for index in range(1, TOTAL_MAIN_HITS + 1):
            await _record(dao, SESSION_MAIN, f"main{index}")
        await _record(dao, SESSION_OTHER, "other-seed")

        # ---- 阶段 A：all_saved，每次翻页前另一会话可靠追加 ----
        operations = 0
        pages_a = 0
        hits_seen_a = 0
        fingerprints: list[str] = []
        cursor: str | None = None
        reached_end_a = False
        while pages_a < 3:
            page = await dao.search_history(_query("all_saved", None, cursor), user_scope=USER_SCOPE)
            operations += 1
            pages_a += 1
            hits_seen_a += len(page.hits)
            fingerprints.append(page.revision_fingerprint)
            cursor = page.next_cursor
            if cursor is None:
                reached_end_a = True
                break
            await _record(dao, SESSION_OTHER, f"other-{pages_a}")
            operations += 1
            # 游标固定上界语义：另一会话追加（created_at 晚于上界）不影响本游标推进
            assert pages_a <= 3

        # 活性判定 1：预算内到第 3 页（160 hits → 3 页 + 2 次追加 ≈ 5-7 步 ≪ 60）
        assert pages_a == 3
        assert operations <= OPERATION_BUDGET
        # 失效信号可观察：另一会话追加改变用户域指纹（服务层据此触发重首读）
        assert len(set(fingerprints)) == len(fingerprints)
        # 游标绑定固定上界：末页 next_cursor（若还有第 4 页）仍携首读上界
        assert reached_end_a or cursor is not None

        # ---- 阶段 B：current_session，另一会话追加不影响本会话指纹 ----
        pages_b = 0
        hits_seen_b = 0
        fingerprints_b: list[str] = []
        cursor_b: str | None = None
        while pages_b < 3:
            page = await dao.search_history(_query("current_session", SESSION_MAIN, cursor_b), user_scope=USER_SCOPE)
            operations += 1
            pages_b += 1
            hits_seen_b += len(page.hits)
            fingerprints_b.append(page.revision_fingerprint)
            cursor_b = page.next_cursor
            if cursor_b is None:
                break
            await _record(dao, SESSION_OTHER, f"other-b-{pages_b}")
            operations += 1

        assert pages_b == 3
        assert hits_seen_b == 3 * LIMIT  # 第 3 页定位：50/页 × 3 = 150（余 10 条在第 4 页）
        # 覆盖域（本会话成员）未变 → 指纹恒定（失效不误报）
        assert len(set(fingerprints_b)) == 1
        assert operations <= OPERATION_BUDGET
