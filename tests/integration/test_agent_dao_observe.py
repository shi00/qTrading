"""AgentDao C-03-5 observe 集成测试（§4.2 observe_session + §9.13 监听协议）。

真实 embedded PG 上验证（§4.2 L531-536 协议）：

1. 先建立监听并确认生效，再用新事务按 after_seq 补 backlog（顺序产出）；
2. 之后持续读取（pg_notify 唤醒 + 有界重查 poll 0.25s），同一 session_seq
   只合并一次（seq 单调推进）；通知只唤醒读取，数据库事件才是事实；
3. CancelledError 必须 raise（R2），退订只关闭本 observer（LISTEN 连接关闭）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    EventKind,
    MessageRole,
    RecordEventRequest,
    ResearchEvent,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "synthetic-user"
OTHER_SCOPE = "other-user"

POLL_INTERVAL_S = 0.25
SETTLE_S = 1.0  # 有界重查节拍 + 读取合计须 ≤1s 合同（§4.2 L532）


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


async def _seed_session(dao: AgentDao, session_id: str = "s-obs") -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id=session_id, title="监听会话"))


async def _record(dao: AgentDao, session_id: str, native_event_id: str, *, body_text: str = "事件正文") -> int:
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
        body_text=body_text,
        search_text=body_text,
    )
    fact = await dao.record_event(request)
    return fact.session_seq


async def _collect(
    iterator: AsyncIterator[ResearchEvent], count: int, timeout_s: float = SETTLE_S
) -> list[ResearchEvent]:
    """在超时内收集 count 个事件（不足即失败，不静默截断）。"""

    events: list[ResearchEvent] = []
    for _ in range(count):
        events.append(await asyncio.wait_for(iterator.__anext__(), timeout=timeout_s))
    with contextlib.suppress(asyncio.CancelledError, RuntimeError):
        await iterator.aclose()  # type: ignore[attr-defined]  # 异步生成器 aclose 为标准库 API
    return events


class TestObserveBacklog:
    async def test_backlog_emitted_in_order_then_settles(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        seqs = [await _record(dao, "s-obs", f"b{i}") for i in range(1, 4)]

        observer = dao.observe_session_events("s-obs", user_scope=USER_SCOPE, after_seq=0)
        events = await _collect(observer, 3)
        assert [e.session_seq for e in events] == seqs
        assert all(e.session_id == "s-obs" for e in events)

    async def test_after_seq_skips_older_backlog(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        seqs = [await _record(dao, "s-obs", f"s{i}") for i in range(1, 4)]
        observer = dao.observe_session_events("s-obs", user_scope=USER_SCOPE, after_seq=seqs[1])
        events = await _collect(observer, 1)
        assert [e.session_seq for e in events] == [seqs[2]]

    async def test_large_backlog_catchup_without_per_page_throttle(self, real_embedded_pg) -> None:
        """追平阶段连续翻页读至 has_more=False，不逐页插入 poll 节拍（检视 M-1）。

        120 条 backlog = 3 页（limit=50）；poll_interval_s 取上限 0.9s——若每页
        强制等待一次节拍，第 120 条投递前至少 ≈1.8s；修复后追平背靠背读取，
        整体耗时远低于该阈值（同时验证顺序与不重不漏）。
        """

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        count = 120
        seqs = []
        for i in range(1, count + 1):
            seqs.append(await _record(dao, "s-obs", f"bulk{i}"))

        started = asyncio.get_running_loop().time()
        observer = dao.observe_session_events("s-obs", user_scope=USER_SCOPE, after_seq=0, poll_interval_s=0.9)
        events = await _collect(observer, count, timeout_s=2.0)
        elapsed = asyncio.get_running_loop().time() - started
        assert [e.session_seq for e in events] == seqs
        assert len({e.session_seq for e in events}) == count
        assert elapsed < 1.5  # 逐页节拍的旧行为 ≈2.7s，追平直读应远低于此

    async def test_missing_session_invalid(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        observer = dao.observe_session_events("no-such", user_scope=USER_SCOPE, after_seq=0)
        with pytest.raises(AgentServiceError) as exc:
            await observer.__anext__()
        assert exc.value.code == AgentErrorCode.INPUT_INVALID

    async def test_user_scope_isolation(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        observer = dao.observe_session_events("s-obs", user_scope=OTHER_SCOPE, after_seq=0)
        with pytest.raises(AgentServiceError) as exc:
            await observer.__anext__()
        assert exc.value.code == AgentErrorCode.INPUT_INVALID


class TestObserveLive:
    async def test_new_events_wake_within_deadline_after_backlog(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        baseline = await _record(dao, "s-obs", "base")

        # poll_interval_s 取上限 1.0s：若 LISTEN 未真正注册（asyncpg ≥0.29 协程
        # 未 await 即静默退化），唤醒只能靠轮询超时 ≥1.0s；真 LISTEN 唤醒即时。
        observer = dao.observe_session_events("s-obs", user_scope=USER_SCOPE, after_seq=baseline, poll_interval_s=1.0)
        # 先确认 backlog 已追平（首 anext 会等待第一事件；先写入一个后置事件再收集）
        started = asyncio.get_running_loop().time()
        live_seq = await _record(dao, "s-obs", "live1")
        event = await asyncio.wait_for(observer.__anext__(), timeout=3.0)
        assert event.session_seq == live_seq
        wake_elapsed = asyncio.get_running_loop().time() - started
        assert wake_elapsed < 0.8  # LISTEN 唤醒即时；退化到 1.0s 轮询即超阈值

        # 第二个事件经 notify 唤醒（不依赖轮询超时也须到）
        second_seq = await _record(dao, "s-obs", "live2")
        event2 = await asyncio.wait_for(observer.__anext__(), timeout=3.0)
        assert event2.session_seq == second_seq

        await observer.aclose()  # type: ignore[attr-defined]  # 异步生成器 aclose 为标准库 API

    async def test_no_duplicate_delivery_for_same_seq(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        first = await _record(dao, "s-obs", "d1")
        observer = dao.observe_session_events("s-obs", user_scope=USER_SCOPE, after_seq=0)
        event = await asyncio.wait_for(observer.__anext__(), timeout=SETTLE_S)
        assert event.session_seq == first
        # 无新事件时继续等待会超时（不重复投递同一 seq）；
        # asyncio.TimeoutError 无诊断载荷，超时语义即断言本身（白名单豁免）
        with pytest.raises(asyncio.TimeoutError):  # noqa: weak-assertion 超时类异常无参数可断言
            await asyncio.wait_for(observer.__anext__(), timeout=SETTLE_S)
        await observer.aclose()  # type: ignore[attr-defined]

    async def test_cancellation_propagates_and_closes(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao)
        await _record(dao, "s-obs", "c1")

        observer = dao.observe_session_events("s-obs", user_scope=USER_SCOPE, after_seq=0)
        task = asyncio.ensure_future(observer.__anext__())
        event = await asyncio.wait_for(task, timeout=SETTLE_S)
        assert event.session_seq == 1

        # 消费任务停在等待下一事件时被取消 → CancelledError 传播（R2）
        wait_task = asyncio.ensure_future(observer.__anext__())
        await asyncio.sleep(0.1)
        wait_task.cancel()
        # CancelledError 无诊断载荷，取消传播即断言本身（白名单豁免）
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion 取消类异常无参数可断言
            await wait_task
        await observer.aclose()  # type: ignore[attr-defined]

        # 退订后 LISTEN 连接关闭：再写事件不产生泄漏通知（无异常即通过）
        await _record(dao, "s-obs", "after-close")
        await asyncio.sleep(0.2)
