"""计划提案持久化集成测试（DD §8 F21 / §9.10 / §10.3，E-04-2）。

真 embedded PG 上验证：

- 提案是不可变 asset 版本（``asset_kind='plan'``，``asset_id=plan:<execution>``，
  ``asset_version=<revision>``），只 INSERT；
- 写入以 ``expected_revision`` CAS 推进，冲突零新事实；同内容重写不增版；
- 读取固定精确版 / 取最大版；无行与跨用户 → ``None``；
- 行内正文、摘要、revision 自相矛盾 → ``OPERATION_UNKNOWN``（fail-closed）；
- 执行缺失/跨用户写 → ``INPUT_INVALID``；并发同预期版一胜一冲突。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    PlanItemSnapshot,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentExecution, AgentSession
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


async def _seed_execution(dao: AgentDao, *, user_scope: str = "u1", execution_id: str = "exec-1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(sa.insert(AgentSession).values(user_scope=user_scope, session_id="s1", title="计划会话"))
        await conn.execute(
            sa.insert(AgentExecution).values(
                user_scope=user_scope,
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


def _item(item_id: str = "a1b2c3d4", *, content: str = "收集来源", status: str = "pending") -> PlanItemSnapshot:
    return PlanItemSnapshot(
        id=item_id,
        content=content,
        status=status,
        active_form="正在收集来源",
        parent_id=None,
        depends_on=(),
    )


async def _count_versions(dao: AgentDao, *, user_scope: str = "u1") -> int:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]
        return int(
            (
                await conn.execute(
                    sa.select(sa.func.count())
                    .select_from(AgentAssetVersion)
                    .where(
                        AgentAssetVersion.user_scope == user_scope,
                        AgentAssetVersion.asset_kind == "plan",
                    )
                )
            ).scalar_one()
        )


class TestPlanProposalPersistence:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_no_proposal_reads_none(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)

        assert await dao.read_plan_proposal(user_scope="u1", execution_id="exec-1", plan_revision=1) is None
        assert await dao.read_current_plan_proposal(user_scope="u1", execution_id="exec-1") is None
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_first_write_creates_revision_one(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)

        saved = await dao.save_plan_proposal(
            user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),)
        )
        assert saved.plan_revision == 1
        assert saved.items == (_item(),)
        assert await dao.read_plan_proposal(user_scope="u1", execution_id="exec-1", plan_revision=1) == saved
        assert await dao.read_current_plan_proposal(user_scope="u1", execution_id="exec-1") == saved
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_asset_row_is_immutable_plan_version(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))

        async with dao.engine.connect() as conn:  # type: ignore[union-attr]
            row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.asset_kind,
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.verification_state,
                        AgentAssetVersion.body_ref,
                        AgentAssetVersion.metadata_payload,
                    ).where(
                        AgentAssetVersion.user_scope == "u1",
                        AgentAssetVersion.asset_id == "plan:exec-1",
                        AgentAssetVersion.asset_version == "1",
                    )
                )
            ).one()
        assert row.asset_kind == "plan"
        assert row.read_state == "ready"
        # 模型提案只经结构校验，不得冒 program_verified（R21）
        assert row.verification_state == "unverified"
        assert row.body_ref is None
        assert row.metadata_payload == {"plan_store_version": 1, "plan_revision": 1, "item_count": 1}
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_current_reads_latest_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))
        second = await dao.save_plan_proposal(
            user_scope="u1",
            execution_id="exec-1",
            expected_revision=1,
            items=(_item(), _item("b", content="分析", status="in_progress")),
        )

        assert second.plan_revision == 2
        current = await dao.read_current_plan_proposal(user_scope="u1", execution_id="exec-1")
        assert current == second
        # 旧版仍按精确 revision 可读（不可变，不原地覆盖）
        assert (await dao.read_plan_proposal(user_scope="u1", execution_id="exec-1", plan_revision=1)) is not None
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_identical_content_does_not_create_new_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        first = await dao.save_plan_proposal(
            user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),)
        )
        again = await dao.save_plan_proposal(
            user_scope="u1", execution_id="exec-1", expected_revision=1, items=(_item(),)
        )

        assert again == first
        assert await _count_versions(dao) == 1
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_expected_revision_conflicts_without_new_facts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))

        with pytest.raises(AgentServiceError) as error:
            await dao.save_plan_proposal(
                user_scope="u1",
                execution_id="exec-1",
                expected_revision=0,
                items=(_item("b", content="另一版"),),
            )
        assert error.value.code is AgentErrorCode.REVISION_CONFLICT
        assert await _count_versions(dao) == 1
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_concurrent_same_expected_revision_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)

        async def _write(item_id: str) -> object:
            return await dao.save_plan_proposal(
                user_scope="u1",
                execution_id="exec-1",
                expected_revision=0,
                items=(_item(item_id, content=f"并行 {item_id}"),),
            )

        results = await asyncio.gather(_write("aaa"), _write("bbb"), return_exceptions=True)
        successes = [item for item in results if not isinstance(item, BaseException)]
        failures = [item for item in results if isinstance(item, AgentServiceError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code is AgentErrorCode.REVISION_CONFLICT
        assert await _count_versions(dao) == 1
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_other_user_scope_is_invisible(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))

        assert await dao.read_plan_proposal(user_scope="u2", execution_id="exec-1", plan_revision=1) is None
        assert await dao.read_current_plan_proposal(user_scope="u2", execution_id="exec-1") is None
        with pytest.raises(AgentServiceError) as error:
            await dao.save_plan_proposal(user_scope="u2", execution_id="exec-1", expected_revision=0, items=(_item(),))
        assert error.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_missing_execution_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as error:
            await dao.save_plan_proposal(
                user_scope="u1", execution_id="exec-missing", expected_revision=0, items=(_item(),)
            )
        assert error.value.code is AgentErrorCode.INPUT_INVALID
        assert await _count_versions(dao) == 0
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_invalid_expected_revision_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        for bad in (True, -1):
            with pytest.raises(AgentServiceError) as error:
                await dao.save_plan_proposal(
                    user_scope="u1",
                    execution_id="exec-1",
                    expected_revision=bad,  # type: ignore[arg-type]  # 越界形态须被拒绝
                    items=(_item(),),
                )
            assert error.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_duplicate_item_id_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        with pytest.raises(AgentServiceError) as error:
            await dao.save_plan_proposal(
                user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item("dup"), _item("dup"))
            )
        assert error.value.code is AgentErrorCode.INPUT_INVALID
        assert await _count_versions(dao) == 0
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_tampered_body_is_operation_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(
                    AgentAssetVersion.user_scope == "u1",
                    AgentAssetVersion.asset_id == "plan:exec-1",
                    AgentAssetVersion.asset_version == "1",
                )
                .values(body_text='{"items":[],"plan_revision":1,"plan_store_version":1}')
            )

        with pytest.raises(AgentServiceError) as error:
            await dao.read_plan_proposal(user_scope="u1", execution_id="exec-1", plan_revision=1)
        assert error.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_tampered_digest_is_operation_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(
                    AgentAssetVersion.user_scope == "u1",
                    AgentAssetVersion.asset_id == "plan:exec-1",
                    AgentAssetVersion.asset_version == "1",
                )
                .values(digest="c" * 64)
            )

        with pytest.raises(AgentServiceError) as error:
            await dao.read_current_plan_proposal(user_scope="u1", execution_id="exec-1")
        assert error.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_empty_items_is_a_valid_revision(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        saved = await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=())
        assert saved.items == ()
        assert await dao.read_current_plan_proposal(user_scope="u1", execution_id="exec-1") == saved
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_non_ready_row_is_operation_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(
                    AgentAssetVersion.user_scope == "u1",
                    AgentAssetVersion.asset_id == "plan:exec-1",
                    AgentAssetVersion.asset_version == "1",
                )
                .values(read_state="unavailable")
            )

        with pytest.raises(AgentServiceError) as error:
            await dao.read_plan_proposal(user_scope="u1", execution_id="exec-1", plan_revision=1)
        assert error.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_non_numeric_version_fails_closed_without_db_error(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_execution(dao)
        await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=0, items=(_item(),))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                sa.insert(AgentAssetVersion).values(
                    user_scope="u1",
                    asset_id="plan:exec-1",
                    asset_version="bad",
                    asset_kind="plan",
                    display_name="plan bad",
                    digest="c" * 64,
                    metadata_payload={"plan_store_version": 1},
                    body_text="{}",
                    read_state="ready",
                    verification_state="unverified",
                )
            )

        with pytest.raises(AgentServiceError) as read_error:
            await dao.read_current_plan_proposal(user_scope="u1", execution_id="exec-1")
        assert read_error.value.code is AgentErrorCode.OPERATION_UNKNOWN
        with pytest.raises(AgentServiceError) as save_error:
            await dao.save_plan_proposal(user_scope="u1", execution_id="exec-1", expected_revision=1, items=(_item(),))
        assert save_error.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert await _count_versions(dao) == 2
        await dao.engine.dispose()  # type: ignore[union-attr]
