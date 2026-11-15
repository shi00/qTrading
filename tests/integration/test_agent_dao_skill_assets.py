"""E-03-4b Skill 资产与角色版本引用集成测试（真实 embedded PG）。

覆盖：Skill 资产落库/幂等/冲突/版本查询、角色版本单事务引用边、
缺失或禁用版本拒绝、缺依赖拒绝、同角色版异内容冲突、引用阻删（FK RESTRICT）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    RoleSkillAssignment,
    SkillAssetRegistration,
    SkillVersionRef,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentAssetVersion, AgentSourceLink
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "scope-skill"


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


def _registration(
    skill_id: str,
    version: int = 1,
    *,
    content_digest: str | None = None,
    missing_dependencies: tuple[str, ...] = (),
) -> SkillAssetRegistration:
    return SkillAssetRegistration(
        user_scope=USER_SCOPE,
        skill_id=skill_id,
        version=version,
        display_name=skill_id,
        content_digest=content_digest or sha256_hex(f"{skill_id}@{version}".encode()),
        normalized_name=skill_id,
        license_id="MIT",
        archive_digest=sha256_hex(f"archive:{skill_id}@{version}".encode()),
        file_count=2,
        expanded_bytes=128,
        dependencies=("base-skill",) if skill_id != "base-skill" else (),
        missing_dependencies=missing_dependencies,
        ignored_behavioral_fields=("allowed-tools",),
    )


def _assignment(
    enabled: tuple[SkillVersionRef, ...],
    defaults: tuple[SkillVersionRef, ...],
    *,
    role_version: str = "1",
) -> RoleSkillAssignment:
    return RoleSkillAssignment(
        role_id="role-main",
        role_version=role_version,
        enabled=enabled,
        defaults=defaults,
    )


class TestSkillAssetPersistence:
    @pytest.mark.asyncio
    async def test_register_skill_asset_writes_metadata_and_versions(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        receipt = await dao.register_skill_asset(_registration("demo"))
        assert receipt.replayed is False
        assert receipt.version == 1
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.asset_kind,
                        AgentAssetVersion.display_name,
                        AgentAssetVersion.digest,
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.verification_state,
                        AgentAssetVersion.metadata_payload,
                    ).where(AgentAssetVersion.asset_id == "demo")
                )
            ).one()
        assert row.asset_kind == "skill"
        assert row.read_state == "ready"
        assert row.verification_state == "program_verified"
        assert row.metadata_payload["license"] == "MIT"
        assert row.metadata_payload["file_count"] == 2
        assert row.metadata_payload["dependencies"] == ["base-skill"]
        assert await dao.list_skill_versions(user_scope=USER_SCOPE, skill_id="demo") == ((1, row.digest),)
        assert await dao.has_skill(user_scope=USER_SCOPE, skill_id="demo") is True
        assert await dao.has_skill(user_scope=USER_SCOPE, skill_id="absent") is False

    @pytest.mark.asyncio
    async def test_replay_same_skill_version_is_idempotent(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        replay = await dao.register_skill_asset(_registration("demo"))
        assert replay.replayed is True
        assert len(await dao.list_skill_versions(user_scope=USER_SCOPE, skill_id="demo")) == 1

    @pytest.mark.asyncio
    async def test_same_version_different_content_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        with pytest.raises(AgentServiceError) as exc:
            await dao.register_skill_asset(_registration("demo", content_digest=sha256_hex(b"other")))
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert len(await dao.list_skill_versions(user_scope=USER_SCOPE, skill_id="demo")) == 1

    @pytest.mark.asyncio
    async def test_list_versions_returns_all_sorted(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo", 2))
        await dao.register_skill_asset(_registration("demo", 1))
        versions = await dao.list_skill_versions(user_scope=USER_SCOPE, skill_id="demo")
        assert [item[0] for item in versions] == [1, 2]


class TestRoleSkillAssignmentPersistence:
    @pytest.mark.asyncio
    async def test_assignment_writes_role_asset_and_reference_edges(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        await dao.register_skill_asset(_registration("extra"))
        assignment = _assignment(
            (SkillVersionRef("base-skill", 1), SkillVersionRef("extra", 1)),
            (SkillVersionRef("base-skill", 1),),
        )
        receipt = await dao.save_role_skill_assignment(assignment, user_scope=USER_SCOPE, display_name="主研究角色")
        assert receipt.replayed is False
        assert [ref.skill_id for ref in receipt.linked_skill_versions] == ["base-skill", "extra"]
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            role = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.asset_kind,
                        AgentAssetVersion.metadata_payload,
                    ).where(AgentAssetVersion.asset_id == "role-main")
                )
            ).one()
            links = (
                await conn.execute(
                    sa.select(
                        AgentSourceLink.source_id,
                        AgentSourceLink.relation_kind,
                        AgentSourceLink.consumer_kind,
                        AgentSourceLink.derived_asset_id,
                    ).where(AgentSourceLink.consumer_id == "role-main")
                )
            ).all()
        assert role.asset_kind == "role"
        assert role.metadata_payload["role_skill_assignment"]["defaults"] == [{"skill_id": "base-skill", "version": 1}]
        assert {link.source_id for link in links} == {"base-skill", "extra"}
        assert {link.relation_kind for link in links} == {"role_skill_binding"}
        assert {link.consumer_kind for link in links} == {"asset"}
        assert {link.derived_asset_id for link in links} == {"role-main"}

    @pytest.mark.asyncio
    async def test_replay_same_assignment_is_idempotent(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        assignment = _assignment((SkillVersionRef("base-skill", 1),), (SkillVersionRef("base-skill", 1),))
        await dao.save_role_skill_assignment(assignment, user_scope=USER_SCOPE, display_name="角色")
        replay = await dao.save_role_skill_assignment(assignment, user_scope=USER_SCOPE, display_name="角色")
        assert replay.replayed is True
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 断言只读
            assert await _count(conn, AgentAssetVersion, "role-main") == 1
            assert await _count(conn, AgentSourceLink, None) == 1

    @pytest.mark.asyncio
    async def test_same_role_version_different_content_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        await dao.register_skill_asset(_registration("extra"))
        first = _assignment((SkillVersionRef("base-skill", 1),), (SkillVersionRef("base-skill", 1),))
        await dao.save_role_skill_assignment(first, user_scope=USER_SCOPE, display_name="角色")
        second = _assignment(
            (SkillVersionRef("base-skill", 1), SkillVersionRef("extra", 1)),
            (SkillVersionRef("base-skill", 1),),
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_role_skill_assignment(second, user_scope=USER_SCOPE, display_name="角色")
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT

    @pytest.mark.asyncio
    async def test_missing_skill_version_rejected_without_role_row(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_role_skill_assignment(
                _assignment((SkillVersionRef("ghost", 1),), (SkillVersionRef("ghost", 1),)),
                user_scope=USER_SCOPE,
                display_name="角色",
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 断言零新事实
            assert await _count(conn, AgentAssetVersion, None) == 0

    @pytest.mark.asyncio
    async def test_disabled_skill_version_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试直接置禁用事实
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(AgentAssetVersion.asset_id == "base-skill")
                .values(read_state="disabled")
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_role_skill_assignment(
                _assignment((SkillVersionRef("base-skill", 1),), (SkillVersionRef("base-skill", 1),)),
                user_scope=USER_SCOPE,
                display_name="角色",
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

    @pytest.mark.asyncio
    async def test_missing_dependencies_block_role_assignment(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("extra", missing_dependencies=("absent",)))
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_role_skill_assignment(
                _assignment((SkillVersionRef("extra", 1),), (SkillVersionRef("extra", 1),)),
                user_scope=USER_SCOPE,
                display_name="角色",
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

    @pytest.mark.asyncio
    async def test_referenced_skill_version_cannot_be_deleted(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        await dao.save_role_skill_assignment(
            _assignment((SkillVersionRef("base-skill", 1),), (SkillVersionRef("base-skill", 1),)),
            user_scope=USER_SCOPE,
            display_name="角色",
        )
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 验证 FK RESTRICT
            with pytest.raises(IntegrityError) as exc:
                async with conn.begin_nested():
                    await conn.execute(
                        delete(AgentAssetVersion).where(
                            AgentAssetVersion.asset_id == "base-skill",
                            AgentAssetVersion.asset_version == "1",
                        )
                    )
            assert "agent_source_link" in str(exc.value).lower()
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 原文仍可查
            assert await _count(conn, AgentAssetVersion, "base-skill") == 1


async def _count(conn: AsyncConnection, model: type[object], asset_id: str | None) -> int:
    query = sa.select(sa.func.count()).select_from(model)
    if asset_id is not None and model is AgentAssetVersion:
        query = query.where(AgentAssetVersion.asset_id == asset_id)
    result = await conn.execute(query)
    return int(result.scalar_one())
