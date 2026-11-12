"""AgentDao.accept_request 受理事务集成测试（任务 C-02-2，SQ-01/§5.3/§3.3.2）。

真实 embedded PG 上的单事务语义验证：

- 受理在**单个显式事务**内落 execution/command/message/asset_version/两源边/
  回执六类事实（§9.13「多行原子协议用单个显式事务」）；
- 同 intent 同冻结输入重传 → 查回原回执，零新事实（INV-01/§3.3.2）；
- 同 intent 异输入 → INPUT_CONFLICT 且零新事实；
- 同命令键异输入 → INPUT_CONFLICT 且零新事实；
- 不同目标复用同 client_command_id → 不同命令记录/消息/源（§3.3.2）；
- session_seq 单调分配 + session.event_seq GREATEST 推进（R22）；
- command 保存/投递/消费三态初值（committed/not_delivered/not_consumed，INV-06）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    Mode,
    Purpose,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import (
    AgentAssetVersion,
    AgentCommand,
    AgentExecution,
    AgentMessage,
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
    # FrozenManifest 覆盖冻结输入（§3.4：请求规范字节含正文/模式/用途）——
    # intent_digest 随 text 变化，重传同文/同 manifest 才幂等。
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


_COUNT_TABLES: dict[str, object] = {
    "agent_execution": AgentExecution,
    "agent_command": AgentCommand,
    "agent_message": AgentMessage,
    "agent_asset_version": AgentAssetVersion,
    "agent_source_link": AgentSourceLink,
}


async def _count(engine, table: str) -> int:
    model = _COUNT_TABLES[table]
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(sa.func.count()).select_from(model))
        return int(result.scalar_one())


class TestAcceptRequestTransaction:
    """受理单事务语义（§9.13 多行原子协议）。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_accept_creates_all_facts_in_one_transaction(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        engine = create_test_engine(real_embedded_pg.url)

        receipt = await dao.accept_request(_request())

        assert receipt.commit_state.value == "committed"
        assert receipt.session_id == "s1"
        assert receipt.execution_id.startswith("execution_")
        assert receipt.workflow_id == "wf-1"
        assert receipt.command.command_id == "client-1"
        assert len(receipt.intent_digest) == 64
        # 六类事实同事务落地
        assert await _count(engine, "agent_execution") == 1
        assert await _count(engine, "agent_command") == 1
        assert await _count(engine, "agent_message") == 1
        assert await _count(engine, "agent_asset_version") == 1
        assert await _count(engine, "agent_source_link") == 2
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_command_initial_states_separated(self, real_embedded_pg: ConnectionInfo) -> None:
        """保存/投递/消费三态分列（INV-06）：committed/not_delivered/not_consumed。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT commit_state, delivery_state, consumption_state FROM agent_command"))
            ).one_or_none()
        assert row is not None and tuple(row) == ("committed", "not_delivered", "not_consumed")
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_replay_same_intent_returns_original_receipt_zero_new_facts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """同 intent 同冻结输入重传：查回原回执，零新事实（§3.3.2）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        first = await dao.accept_request(_request())
        engine = create_test_engine(real_embedded_pg.url)
        before = {
            t: await _count(engine, t)
            for t in (
                "agent_execution",
                "agent_command",
                "agent_message",
                "agent_asset_version",
                "agent_source_link",
            )
        }

        replay = await dao.accept_request(_request(client_command_id="client-2"))

        assert replay == first
        after = {t: await _count(engine, t) for t in before}
        assert after == before, f"replay must not create new facts: {before} -> {after}"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_intent_different_input_conflicts_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        """同 intent 异输入 INPUT_CONFLICT，零新事实（INV-01）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())
        engine = create_test_engine(real_embedded_pg.url)
        before = {
            t: await _count(engine, t)
            for t in (
                "agent_execution",
                "agent_command",
                "agent_message",
                "agent_asset_version",
                "agent_source_link",
            )
        }

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(text="换一个问题"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before, f"conflict must not create facts: {before} -> {after}"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_command_key_same_input_returns_original_receipt(self, real_embedded_pg: ConnectionInfo) -> None:
        """同命令键同输入（不同 intent id）→ 取原已分配消息/源（§3.3.2 重传规则）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        first = await dao.accept_request(_request())
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        # 同命令键 + 同正文 + 不同 business_request_id：命令身份相同（target+键），
        # 同输入取原记录，不新建第二 intent。
        replay = await dao.accept_request(_request(business_request_id="req-2"))

        assert replay == first
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_command_key_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """同命令键异输入 INPUT_CONFLICT 且零新事实（§3.3.2）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(business_request_id="req-2", text="不同的正文"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_client_key_across_targets_gets_distinct_records(self, real_embedded_pg: ConnectionInfo) -> None:
        """同用户/会话下两个目标复用同客户端键 → 不同命令记录/消息/源（§3.3.2）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_session(dao, "u1", "s2")
        r1 = await dao.accept_request(_request(session_id="s1", business_request_id="req-1"))
        r2 = await dao.accept_request(_request(session_id="s2", business_request_id="req-2", text="另一个会话的问题"))
        assert r1.command_record_id != r2.command_record_id
        assert r1.execution_id != r2.execution_id
        engine = create_test_engine(real_embedded_pg.url)
        assert await _count(engine, "agent_command") == 2
        assert await _count(engine, "agent_message") == 2
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_session_seq_monotonic_and_watermark_greatest(self, real_embedded_pg: ConnectionInfo) -> None:
        """session_seq 单调分配；session.event_seq GREATEST 推进（R22）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        engine = create_test_engine(real_embedded_pg.url)
        await dao.accept_request(_request(business_request_id="req-1", client_command_id="c1", text="问一"))
        async with engine.connect() as conn:
            seq1 = (await conn.execute(text("SELECT session_seq FROM agent_message"))).scalar_one()
            wm1 = (await conn.execute(text("SELECT event_seq FROM agent_session"))).scalar_one()
        await dao.accept_request(_request(business_request_id="req-2", client_command_id="c2", text="问二"))
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT session_seq FROM agent_message ORDER BY session_seq"))).fetchall()
            wm2 = (await conn.execute(text("SELECT event_seq FROM agent_session"))).scalar_one()
        assert seq1 == 0
        assert [r[0] for r in rows] == [0, 1]
        assert wm1 == 0 and wm2 == 1
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_session_rejected_without_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        """目标会话不存在 → INPUT_INVALID，零新事实。"""
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request())
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        engine = create_test_engine(real_embedded_pg.url)
        assert await _count(engine, "agent_command") == 0
        await engine.dispose()
