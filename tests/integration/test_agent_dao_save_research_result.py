"""``AgentDao.save_research_result`` 集成测试（R2-I08 成果可靠保存）。

真 embedded PG 上验证：

- 成果、``final_saved`` 与 ``artifact_saved`` 事件同事务落库；
- 同内容幂等（版本/事件不增长），内容变化追加不可变新版本；
- 执行/轮归属不符 → ``INPUT_INVALID``；
- 正文只在 ``body_text`` 存一份，载荷标记 ``body_saved``（R21 不伪装）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import command
from core.agent_types import AgentErrorCode, AgentServiceError, ResearchResultDraft, research_result_digest
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentArtifact, AgentExecution, AgentMessage, AgentRound, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u-result"


async def _reset_schema(url: str) -> None:
    engine = create_test_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(sa.text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _setup(url: str) -> AgentDao:
    await _reset_schema(url)
    cfg = make_alembic_cfg(url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return AgentDao(create_test_engine(url))


async def _seed_chain(dao: AgentDao, *, execution_id: str = "exec-1", round_id: str = "round-1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            pg_insert(AgentSession).values(user_scope=_USER, session_id="s1", title="成果会话").on_conflict_do_nothing()
        )
        await conn.execute(
            sa.insert(AgentExecution).values(
                user_scope=_USER,
                execution_id=execution_id,
                session_id="s1",
                execution_kind="root",
                node_id="root",
                display_state="queued",
                goal_coverage="unknown",
                source_module="ui",
                business_request_id=f"req-{execution_id}",
                intent_digest="a" * 64,
            )
        )
        await conn.execute(
            sa.insert(AgentRound).values(
                user_scope=_USER,
                round_id=round_id,
                execution_id=execution_id,
                round_number=1,
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


def _draft(*, body_text: str | None = "研究成果正文", result_kind: str = "explanation") -> ResearchResultDraft:
    return ResearchResultDraft(
        user_scope=_USER,
        session_id="s1",
        execution_id="exec-1",
        round_id="round-1",
        node_id="root",
        input_revision=1,
        actual_input_digest="b" * 64,
        actual_model_ref="model/main",
        result_kind=result_kind,
        summary="摘要",
        key_points=("要点一",),
        body_text=body_text,
        structured_fields=None,
        object_rows=(),
        evidence_links=("src-1",),
        source_available_state="unknown",
        coverage=None,
        missing_fields=("score",),
        failed_signal_dates=(),
        data_warnings=(),
        limitations=(),
        verification_state="unverified",
        execution_state="running",
        goal_coverage="unknown",
        convergence_confirmed=False,
        unknown_operation_refs=(),
        ledger_refs=(),
        plan_version=None,
        parent_artifact_refs=(),
        source_refs=(),
    )


class TestSaveResearchResult:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_saves_artifact_event_and_final_saved_in_one_transaction(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)

        saved = await dao.save_research_result(_draft())

        assert saved.final_output_saved is True
        assert saved.artifact_version == 1
        assert saved.body_text == "研究成果正文"
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            artifact = (
                await conn.execute(sa.select(AgentArtifact).where(AgentArtifact.artifact_id == saved.artifact_id))
            ).one()
            round_row = (await conn.execute(sa.select(AgentRound).where(AgentRound.round_id == "round-1"))).one()
            events = (
                await conn.execute(sa.select(AgentMessage).where(AgentMessage.event_kind == "artifact_saved"))
            ).all()
        assert artifact.result_digest == research_result_digest(_draft())
        assert artifact.body_text == "研究成果正文"
        assert artifact.result_payload["output"]["body_saved"] is True
        assert artifact.result_payload["completion"]["final_output_saved"] is True
        assert round_row.final_saved is True
        assert len(events) == 1
        assert events[0].subject_id == saved.artifact_id
        assert events[0].subject_revision == 1
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_same_digest_is_idempotent_and_changed_content_versions(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)

        first = await dao.save_research_result(_draft())
        replay = await dao.save_research_result(_draft())
        second = await dao.save_research_result(_draft(body_text="修订后的成果"))

        assert (replay.artifact_id, replay.artifact_version) == (first.artifact_id, 1)
        assert second.artifact_version == 2
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            versions = (
                (await conn.execute(sa.select(AgentArtifact.artifact_version).order_by(AgentArtifact.artifact_version)))
                .scalars()
                .all()
            )
            event_count = (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentMessage)
                    .where(AgentMessage.event_kind == "artifact_saved")
                )
            ).scalar_one()
        assert versions == [1, 2]
        assert event_count == 2
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_execution_is_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_research_result(replace(_draft(), execution_id="exec-missing"))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_round_from_other_execution_is_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_chain(dao, execution_id="exec-2", round_id="round-2")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_research_result(replace(_draft(), round_id="round-2"))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]
