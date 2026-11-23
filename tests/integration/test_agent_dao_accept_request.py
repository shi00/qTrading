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
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    EventKind,
    EventReadQuery,
    Mode,
    Purpose,
    json_doc,
    sha256_hex,
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


async def _seed_session(dao: AgentDao, user_scope: str, session_id: str, *, archived: bool = False) -> None:
    from sqlalchemy import insert

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造受理前置会话
        await conn.execute(
            insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title=f"session {session_id}",
                archived=archived,
            )
        )


async def _set_archived(dao: AgentDao, *, archived: bool) -> None:
    """直接置会话归档位（模拟归档/解档命令已提交的会话状态）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造归档前置
        await conn.execute(
            sa.update(AgentSession)
            .where(AgentSession.user_scope == "u1", AgentSession.session_id == "s1")
            .values(archived=archived)
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
    async def test_same_command_key_with_new_intent_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """P1-03：同命令键 + 新意图/同正文同 manifest → INPUT_CONFLICT，不得返回别的研究。

        命令身份是「目标 + 客户端键」，同一键不能同时属于两个意图；旧实现按命令摘要
        重放会返回别的研究的 COMMITTED 身份（并按本次模式派发）。
        """
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        await dao.accept_request(_request(manifest_payload=frozen))
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(business_request_id="req-2", manifest_payload=frozen))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_command_key_new_intent_changed_mode_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """P1-03：同命令键 + 新意图 + 仅改模式/用途 → INPUT_CONFLICT，零新事实。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        first = await dao.accept_request(_request(manifest_payload=frozen))
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        for index, overrides in enumerate(
            (
                {"mode": Mode.EXECUTE},
                {"purpose": Purpose.END_OF_DAY_RESEARCH},
                {"workflow_id": "wf-2"},
            ),
            start=1,
        ):
            with pytest.raises(AgentServiceError) as excinfo:
                await dao.accept_request(
                    _request(business_request_id=f"req-new-{index}", manifest_payload=frozen, **overrides)
                )
            assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        after = {t: await _count(engine, t) for t in before}
        assert after == before
        assert first.mode is Mode.DISCUSS
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_replay_intent_with_client_key_bound_to_other_intent_conflicts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """P1-03：两个既有意图各有客户端键时，用别家的键重传原意图 → INPUT_CONFLICT。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        first = await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c1"))
        second = await dao.accept_request(
            _request(business_request_id="req-2", manifest_payload=frozen, client_command_id="c2")
        )
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c2"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        assert first.execution_id != second.execution_id
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_command_key_same_text_different_manifest_conflicts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """同命令键同正文换 manifest → INPUT_CONFLICT（检视 D05），零新事实。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen_a = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司", "purpose": "concept"})
        frozen_b = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司", "purpose": "decide"})
        await dao.accept_request(_request(manifest_payload=frozen_a))
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(business_request_id="req-2", manifest_payload=frozen_b))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_command_key_different_expected_revision_or_node_conflicts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """同命令键同正文同 manifest 换期望版/node → INPUT_CONFLICT（检视 D05），零新事实。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        await dao.accept_request(_request(manifest_payload=frozen))
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(
                _request(business_request_id="req-2", manifest_payload=frozen, expected_revision=3)
            )
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(business_request_id="req-3", manifest_payload=frozen, node_id="node-2"))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_legacy_command_digest_row_replays_by_full_intent_digest(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """旧命令摘要行（仅正文）：同 intent 完整摘要相同仍重放，换 manifest 冲突（D05）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen_a = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司", "purpose": "concept"})
        frozen_b = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司", "purpose": "decide"})
        first = await dao.accept_request(_request(manifest_payload=frozen_a))
        engine = create_test_engine(real_embedded_pg.url)
        # 模拟 D05 修复前的历史行：命令 input_digest 仅覆盖正文。
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentCommand)
                .where(AgentCommand.user_scope == "u1", AgentCommand.command_kind == "submit")
                .values(input_digest=sha256_hex("研究三家公司"))
            )
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        replay = await dao.accept_request(_request(manifest_payload=frozen_a, client_command_id="c2"))
        assert replay == first
        assert replay.mode is Mode.DISCUSS

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(manifest_payload=frozen_a, client_command_id="c3", expected_revision=5))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(manifest_payload=frozen_b, client_command_id="c4"))
        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_intent_same_manifest_changed_text_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """I14：同 intent + 同 manifest 只换正文 → INPUT_CONFLICT（旧实现误判重放）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        first = await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c1"))
        engine = create_test_engine(real_embedded_pg.url)
        before = {
            t: await _count(engine, t)
            for t in ("agent_execution", "agent_command", "agent_message", "agent_asset_version", "agent_source_link")
        }

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c2", text="换一个问题"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before, f"conflict must not create facts: {before} -> {after}"
        assert first.execution_id  # 原受理未被改写
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_intent_other_input_change_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """I14：同 intent 换模式/用途/工作流/来源显示名/期望版/node/目标会话/比较引用均冲突。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_session(dao, "u1", "s2")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        first = await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c1"))
        engine = create_test_engine(real_embedded_pg.url)
        before = {
            t: await _count(engine, t)
            for t in ("agent_execution", "agent_command", "agent_message", "agent_asset_version", "agent_source_link")
        }
        variants = {
            "mode": {"mode": Mode.EXECUTE},
            "purpose": {"purpose": Purpose.END_OF_DAY_RESEARCH},
            "workflow_id": {"workflow_id": "wf-2"},
            "source_display_name": {"source_display_name": "另一显示名"},
            "node_id": {"node_id": "node-2"},
            "expected_revision": {"expected_revision": 3},
            "session_id": {"session_id": "s2"},
            "comparison_refs": {"comparison_group_ref": "group-1", "comparison_lane_ref": "lane-1"},
        }
        for index, (name, overrides) in enumerate(variants.items(), start=2):
            with pytest.raises(AgentServiceError) as excinfo:
                await dao.accept_request(_request(manifest_payload=frozen, client_command_id=f"c{index}", **overrides))
            assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT, name

        after = {t: await _count(engine, t) for t in before}
        assert after == before, f"conflict must not create facts: {before} -> {after}"
        assert first.execution_id
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_legacy_intent_row_replays_same_input_and_conflicts_on_change(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """I14 旧行兼容：仅当存储事实能证明完整输入一致才重放，其余显式冲突。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        # 旧行需由冻结 manifest 声明模式/用途，才能证明受理时冻结值与本次一致（P1-03）。
        frozen = json_doc(
            {
                "schema_version": 1,
                "source_module": "ui",
                "text": "研究三家公司",
                "mode": "discuss",
                "purpose": "concept",
            }
        )
        first = await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c1"))
        engine = create_test_engine(real_embedded_pg.url)
        # 模拟 I14 修复前的历史行：意图摘要只覆盖 manifest，命令摘要只覆盖正文。
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == "u1", AgentExecution.business_request_id == "req-1")
                .values(intent_digest=sha256_hex(frozen))
            )
            await conn.execute(
                sa.update(AgentCommand)
                .where(AgentCommand.user_scope == "u1", AgentCommand.command_kind == "submit")
                .values(input_digest=sha256_hex("研究三家公司"))
            )
        before = {
            t: await _count(engine, t)
            for t in ("agent_execution", "agent_command", "agent_message", "agent_asset_version", "agent_source_link")
        }

        replay = await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c2"))
        assert replay == first

        variants: tuple[dict[str, Any], ...] = (
            {"text": "换一个问题"},
            {"workflow_id": "wf-2"},
            {"source_display_name": "另一显示名"},
            {"expected_revision": 2},
        )
        for index, overrides in enumerate(variants, start=3):
            with pytest.raises(AgentServiceError) as excinfo:
                await dao.accept_request(_request(manifest_payload=frozen, client_command_id=f"c{index}", **overrides))
            assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        after = {t: await _count(engine, t) for t in before}
        assert after == before, f"legacy replay must not create facts: {before} -> {after}"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_legacy_intent_row_mode_or_purpose_change_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        """P1-03：旧行重放必须证明 mode/purpose；改模式/用途 → INPUT_CONFLICT。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc(
            {
                "schema_version": 1,
                "source_module": "ui",
                "text": "研究三家公司",
                "mode": "discuss",
                "purpose": "concept",
            }
        )
        await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c1"))
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == "u1", AgentExecution.business_request_id == "req-1")
                .values(intent_digest=sha256_hex(frozen))
            )
            await conn.execute(
                sa.update(AgentCommand)
                .where(AgentCommand.user_scope == "u1", AgentCommand.command_kind == "submit")
                .values(input_digest=sha256_hex("研究三家公司"))
            )
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        for index, overrides in enumerate(
            ({"mode": Mode.EXECUTE}, {"purpose": Purpose.END_OF_DAY_RESEARCH}),
            start=2,
        ):
            with pytest.raises(AgentServiceError) as excinfo:
                await dao.accept_request(_request(manifest_payload=frozen, client_command_id=f"c{index}", **overrides))
            assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_legacy_intent_row_without_declared_mode_does_not_replay(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """P1-03：旧行 manifest 未声明 mode/purpose → 无法证明 → 不得按完整重放。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        frozen = json_doc({"schema_version": 1, "source_module": "ui", "text": "研究三家公司"})
        await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c1"))
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == "u1", AgentExecution.business_request_id == "req-1")
                .values(intent_digest=sha256_hex(frozen))
            )
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(manifest_payload=frozen, client_command_id="c2"))

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
        after = {t: await _count(engine, t) for t in before}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_intent_digest_row_does_not_fall_into_new_acceptance(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """P1-03：意图摘要缺失（NULL）的历史行不得被当新受理，重传按冲突处理。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.accept_request(_request())
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == "u1", AgentExecution.business_request_id == "req-1")
                .values(intent_digest=None)
            )
        before = {t: await _count(engine, t) for t in ("agent_execution", "agent_command", "agent_message")}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request())

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
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
        """session_seq 从 1 起单调分配；session.event_seq GREATEST 推进（R22，检视 D01）。"""
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
        # 水位起点 1：事件 codec 要求 session_seq ≥ 1，受理消息不得为 0（检视 D01）。
        assert seq1 == 1
        assert [r[0] for r in rows] == [1, 2]
        assert wm1 == 1 and wm2 == 2
        # 真实受理 → 首次观察：after_seq=0 追平必须取到首条受理事件，
        # 不得被「seq > 0」过滤跳过（不经 seq=1 手工种子的正例，检视 D01）。
        page = await dao.read_events(EventReadQuery(user_scope="u1", session_id="s1", after_seq=0))
        assert [e.session_seq for e in page.events] == [1, 2]
        assert page.events[0].event_kind is EventKind.PUBLIC_MESSAGE_SAVED
        assert page.last_seq == 2 and page.has_more is False
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


async def _set_draft_state(dao: AgentDao, *, revision: int, payload: dict | None) -> None:
    """直接置会话草稿版（真实 save_draft 会推进内容；此处只构造 CAS 前置）。"""
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造草稿版前置
        await conn.execute(
            sa.update(AgentSession)
            .where(AgentSession.user_scope == "u1", AgentSession.session_id == "s1")
            .values(draft_revision=revision, draft_payload=payload)
        )


_FACT_TABLES = ("agent_execution", "agent_command", "agent_message", "agent_asset_version", "agent_source_link")


class TestAcceptDraftRevisionCas:
    """R2-I15：受理须与当前草稿版原子 CAS，并拒绝与请求矛盾的冻结 manifest。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_draft_revision_rejected_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        """旧窗口视图（expected < 当前 draft_revision）→ REVISION_CONFLICT，零新事实。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _set_draft_state(dao, revision=3, payload={"schema_version": 2})
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(expected_revision=1))

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        after = {t: await _count(engine, t) for t in _FACT_TABLES}
        assert after == before, f"stale draft must not create facts: {before} -> {after}"
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_current_draft_revision_accepted_and_frozen(self, real_embedded_pg: ConnectionInfo) -> None:
        """与当前草稿版一致 → 受理成功，命令行冻结该预期版。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _set_draft_state(dao, revision=3, payload={"schema_version": 2})

        receipt = await dao.accept_request(_request(expected_revision=3))

        assert receipt.command.expected_revision == 3
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            stored = (await conn.execute(text("SELECT expected_revision FROM agent_command"))).scalar_one()
        assert stored == 3
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_zero_baseline_rejected_once_draft_saved(self, real_embedded_pg: ConnectionInfo) -> None:
        """0=无草稿基线只对从未保存草稿的会话成立；草稿存在时必须显式给版本。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _set_draft_state(dao, revision=2, payload={"schema_version": 2})
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(expected_revision=0))

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        after = {t: await _count(engine, t) for t in _FACT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_replay_after_draft_advance_still_returns_original_receipt(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """重传先查回原回执：已受理后的草稿推进不使原受理失效（幂等优先）。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        first = await dao.accept_request(_request())
        await _set_draft_state(dao, revision=5, payload={"schema_version": 2})
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        replay = await dao.accept_request(_request(client_command_id="client-2"))

        assert replay == first
        after = {t: await _count(engine, t) for t in _FACT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_manifest_contradicting_request_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        """新受理的冻结 manifest 与实际正文矛盾 → INPUT_INVALID，零新事实。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        stale_manifest = json_doc(
            {
                "schema_version": 1,
                "source_module": "ui",
                "business_request_id": "req-1",
                "mode": "discuss",
                "purpose": "concept",
                "text": "旧稿正文",
            }
        )
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(manifest_payload=stale_manifest))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        after = {t: await _count(engine, t) for t in _FACT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_comparison_manifest_missing_frozen_section_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        """比较组冻结段缺失（角色/模型/资料/允许历史）→ INPUT_INVALID，不按缺省放行。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        shared = {
            "text": "研究三家公司",
            "mode": "discuss",
            "purpose": "concept",
            "cutoff": None,
            "source_refs": [],
            "visible_history_message_ids": [],
            "visible_history_artifact_refs": [],
            "role_prompt_refs": [],
            "skill_refs": [],
            "mcp_scope_digest": "a" * 64,
        }
        manifest = json_doc(
            {
                "schema_version": 1,
                "group_ref": "group-1",
                "user_scope": "u1",
                "session_id": "s1",
                "source_module": "ui",
                "input_digest": "b" * 64,
                "shared": shared,
                "lanes": [],
            }
        )
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(
                _request(
                    manifest_payload=manifest,
                    comparison_group_ref="group-1",
                    comparison_lane_ref="lane-1",
                )
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        after = {t: await _count(engine, t) for t in _FACT_TABLES}
        assert after == before
        await engine.dispose()


class TestAcceptRequestArchiveGate:
    """R2-I22：归档会话可只读/可查回旧命令，但新研究受理须先解档。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_archived_session_rejects_new_intent_zero_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1", archived=True)
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert "archived" in excinfo.value.sanitized_message
        after = {t: await _count(engine, t) for t in _FACT_TABLES}
        assert after == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_archived_session_replays_original_receipt_zero_new_facts(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        """归档后原命令重放不受影响：查回原回执，零新事实；新意图仍拒绝。"""
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        first = await dao.accept_request(_request())
        await _set_archived(dao, archived=True)
        engine = create_test_engine(real_embedded_pg.url)
        before = {t: await _count(engine, t) for t in _FACT_TABLES}

        replay = await dao.accept_request(_request(client_command_id="client-2"))

        assert replay == first
        assert {t: await _count(engine, t) for t in _FACT_TABLES} == before
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request(business_request_id="req-2", client_command_id="client-3"))
        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert {t: await _count(engine, t) for t in _FACT_TABLES} == before
        await engine.dispose()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unarchive_then_accept_succeeds(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1", archived=True)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.accept_request(_request())
        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED

        await _set_archived(dao, archived=False)
        receipt = await dao.accept_request(_request())

        assert receipt.session_id == "s1"
        assert receipt.commit_state.value == "committed"
