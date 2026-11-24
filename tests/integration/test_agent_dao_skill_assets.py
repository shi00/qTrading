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
    SourceStopCommand,
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

    @pytest.mark.asyncio
    async def test_directory_source_registration_has_no_archive_digest(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        request = SkillAssetRegistration(
            user_scope=USER_SCOPE,
            skill_id="demo",
            version=1,
            display_name="demo",
            content_digest=sha256_hex(b"content"),
            normalized_name="demo",
            license_id="MIT",
            archive_digest=None,
            file_count=1,
            expanded_bytes=10,
            source_kind="directory",
        )
        await dao.register_skill_asset(request)
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            metadata = (
                await conn.execute(
                    sa.select(AgentAssetVersion.metadata_payload).where(AgentAssetVersion.asset_id == "demo")
                )
            ).scalar_one()
        assert metadata["archive_digest"] is None
        assert metadata["source_kind"] == "directory"

    @pytest.mark.asyncio
    async def test_disabled_skill_is_not_a_usable_dependency(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        assert await dao.has_skill(user_scope=USER_SCOPE, skill_id="base-skill") is True
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试直接置禁用事实
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(AgentAssetVersion.asset_id == "base-skill")
                .values(read_state="disabled")
            )
        assert await dao.has_skill(user_scope=USER_SCOPE, skill_id="base-skill") is False


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


class TestSkillAvailabilityLifecycle:
    @pytest.mark.asyncio
    async def test_disable_then_enable_restores_ready(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))

        assert await dao.set_skill_availability(user_scope=USER_SCOPE, skill_id="demo", version=1, enabled=False) == (
            "disabled",
            False,
        )
        assert await dao.set_skill_availability(user_scope=USER_SCOPE, skill_id="demo", version=1, enabled=False) == (
            "disabled",
            True,
        )
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            disabled_row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.disabled_at,
                        AgentAssetVersion.disabled_revision,
                    ).where(AgentAssetVersion.asset_id == "demo")
                )
            ).one()
        assert disabled_row.read_state == "disabled"
        assert disabled_row.disabled_at is not None
        assert disabled_row.disabled_revision > 0

        assert await dao.set_skill_availability(user_scope=USER_SCOPE, skill_id="demo", version=1, enabled=True) == (
            "ready",
            False,
        )
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试断言读取
            ready_row = (
                await conn.execute(
                    sa.select(
                        AgentAssetVersion.read_state,
                        AgentAssetVersion.disabled_at,
                        AgentAssetVersion.disabled_revision,
                    ).where(AgentAssetVersion.asset_id == "demo")
                )
            ).one()
        assert ready_row.read_state == "ready"
        assert ready_row.disabled_at is None
        assert ready_row.disabled_revision == 0

    @pytest.mark.asyncio
    async def test_disable_referenced_version_is_denied(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        await dao.save_role_skill_assignment(
            _assignment((SkillVersionRef("demo", 1),), (SkillVersionRef("demo", 1),)),
            user_scope=USER_SCOPE,
            display_name="角色",
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.set_skill_availability(user_scope=USER_SCOPE, skill_id="demo", version=1, enabled=False)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 引用保护不落半态
            state = (
                await conn.execute(sa.select(AgentAssetVersion.read_state).where(AgentAssetVersion.asset_id == "demo"))
            ).scalar_one()
        assert state == "ready"

    @pytest.mark.asyncio
    async def test_unavailable_version_cannot_be_toggled(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试直接置不可用事实
            await conn.execute(
                sa.update(AgentAssetVersion)
                .where(AgentAssetVersion.asset_id == "demo")
                .values(read_state="unavailable")
            )
        with pytest.raises(AgentServiceError) as exc:
            await dao.set_skill_availability(user_scope=USER_SCOPE, skill_id="demo", version=1, enabled=True)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

    @pytest.mark.asyncio
    async def test_stop_source_use_cannot_bypass_reference_protection(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        await dao.save_role_skill_assignment(
            _assignment((SkillVersionRef("demo", 1),), (SkillVersionRef("demo", 1),)),
            user_scope=USER_SCOPE,
            display_name="角色",
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.stop_source_use(
                SourceStopCommand(user_scope=USER_SCOPE, source_id="demo", source_version="1", command_id="cmd-1")
            )
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

    @pytest.mark.asyncio
    async def test_missing_version_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.set_skill_availability(user_scope=USER_SCOPE, skill_id="ghost", version=1, enabled=False)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_read_role_skill_assignment_roundtrip(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        assert (
            await dao.read_role_skill_assignment(user_scope=USER_SCOPE, role_id="role-main", role_version="1") is None
        )
        await dao.register_skill_asset(_registration("demo"))
        assignment = _assignment((SkillVersionRef("demo", 1),), (SkillVersionRef("demo", 1),))
        await dao.save_role_skill_assignment(assignment, user_scope=USER_SCOPE, display_name="角色")
        stored = await dao.read_role_skill_assignment(user_scope=USER_SCOPE, role_id="role-main", role_version="1")
        assert stored == assignment


class TestSkillVersionRemoval:
    """W-05：列表投影与卸载（角色引用阻删、无引用删行）。"""

    @pytest.mark.asyncio
    async def test_list_skill_version_views_returns_projection(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        views = await dao.list_skill_version_views(user_scope=USER_SCOPE)
        assert [(view.skill_id, view.version, view.read_state) for view in views] == [("demo", 1, "ready")]
        assert views[0].file_count == 2
        assert views[0].expanded_bytes == 128
        assert views[0].dependencies == ("base-skill",)

    @pytest.mark.asyncio
    async def test_remove_unreferenced_skill_version_deletes_row(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("demo"))
        await dao.remove_skill_version(user_scope=USER_SCOPE, skill_id="demo", version=1)
        assert await dao.list_skill_version_views(user_scope=USER_SCOPE) == ()

    @pytest.mark.asyncio
    async def test_remove_role_referenced_skill_version_is_denied(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.register_skill_asset(_registration("base-skill"))
        await dao.save_role_skill_assignment(
            _assignment((SkillVersionRef("base-skill", 1),), (SkillVersionRef("base-skill", 1),)),
            user_scope=USER_SCOPE,
            display_name="角色",
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.remove_skill_version(user_scope=USER_SCOPE, skill_id="base-skill", version=1)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "role-main@1" in str(exc.value)
        assert len(await dao.list_skill_version_views(user_scope=USER_SCOPE)) == 1

    @pytest.mark.asyncio
    async def test_remove_missing_skill_version_is_input_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.remove_skill_version(user_scope=USER_SCOPE, skill_id="ghost", version=1)
        assert exc.value.code is AgentErrorCode.INPUT_INVALID


async def _count(conn: AsyncConnection, model: type[object], asset_id: str | None) -> int:
    query = sa.select(sa.func.count()).select_from(model)
    if asset_id is not None and model is AgentAssetVersion:
        query = query.where(AgentAssetVersion.asset_id == asset_id)
    result = await conn.execute(query)
    return int(result.scalar_one())
