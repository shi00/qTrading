"""E-05-4a 交付阶段 CAS 集成测试（agent_operation.delivery_manifest，§9.9/§10.2）。

真实 embedded PG 验证：
- 首次保存写入 manifest 与 artifact 身份；
- 同内容重放幂等（revision 不变）；不同内容旧预期版 → REVISION_CONFLICT；
- 非法阶段迁移拒绝，unknown/终态不可绕过；
- 同 operation 绑定其他 delivery → INPUT_CONFLICT；跨用户 → INPUT_INVALID。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ArtifactDeliveryCommand,
    CommandRef,
    DeliveryManifest,
    DeliveryTarget,
    decode_delivery_manifest,
    derive_delivery_id,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentArtifact,
    AgentExecution,
    AgentOperation,
    AgentRound,
    AgentSession,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

USER_SCOPE = "synthetic-user"
OTHER_SCOPE = "other-user"
_TIMESTAMP = "2026-11-15T08:00:00.000000Z"
_STAGED = "C:\\Users\\admin\\Desktop\\.out.md.abc.staged"


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
    await asyncio.to_thread(command.upgrade, make_alembic_cfg(url), "head")
    return AgentDao(create_test_engine(url))


async def _seed_operation(dao: AgentDao, operation_id: str = "op-deliver") -> None:
    async with dao.engine.begin() as conn:
        await conn.execute(insert(AgentSession).values(user_scope=USER_SCOPE, session_id="s1", title="交付会话"))
        await conn.execute(
            insert(AgentExecution).values(
                user_scope=USER_SCOPE,
                execution_id="exec-1",
                session_id="s1",
                execution_kind="root",
                node_id="root",
                display_state="queued",
                goal_coverage="unknown",
                source_module="ui",
                business_request_id="req-1",
                intent_digest="a" * 64,
            )
        )
        await conn.execute(
            insert(AgentRound).values(
                user_scope=USER_SCOPE,
                round_id="round-1",
                execution_id="exec-1",
                round_number=1,
                workflow_id="wf-1",
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
        await conn.execute(
            insert(AgentArtifact).values(
                user_scope=USER_SCOPE,
                artifact_id="art-1",
                artifact_version=1,
                session_id="s1",
                execution_id="exec-1",
                round_id="round-1",
                artifact_kind="plan",
                input_revision=1,
                actual_input_digest="b" * 64,
                actual_model_ref="model/main",
                result_digest="c" * 64,
                source_available_state="current",
                body_text="成果正文",
            )
        )
        await conn.execute(
            insert(AgentOperation).values(
                user_scope=USER_SCOPE,
                operation_id=operation_id,
                owner_epoch=1,
                run_epoch=1,
                phase="dispatched",
                effect_state="dispatched",
                io_state="open",
            )
        )


def _manifest(*, command_id: str = "cmd-1", user_scope: str = USER_SCOPE, **overrides: object) -> DeliveryManifest:
    command = CommandRef(
        user_scope=user_scope,
        source_module="backtest",
        business_request_id="req-1",
        command_id=command_id,
        expected_revision=0,
    )
    target = DeliveryTarget(
        target_path="C:\\Users\\admin\\Desktop\\out.md",
        expected_existing=False,
        expected_existing_digest=None,
        overwrite_confirmed=False,
    )
    draft = ArtifactDeliveryCommand(
        command=command,
        artifact_id="art-1",
        artifact_version=1,
        format="md",
        format_rule_version=1,
        delivery_id="delivery-placeholder",
        target=target,
        expected_delivery_revision=0,
    )
    base = DeliveryManifest(
        delivery_id=derive_delivery_id(draft),
        command=command,
        artifact_id="art-1",
        artifact_version=1,
        artifact_digest="a" * 64,
        format="md",
        format_rule_version=1,
        target=target,
        phase="manifest_saved",
        source_state="available",
        output_digest=None,
        output_bytes=None,
        staged_path=None,
        verified_at=_TIMESTAMP,
        error_code=None,
    )
    return replace(base, **overrides)


class TestDeliveryPhaseCas:
    @pytest.mark.asyncio
    async def test_first_save_creates_manifest_and_artifact_identity(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        manifest = _manifest()
        receipt = await dao.save_delivery_phase(
            user_scope=USER_SCOPE,
            operation_id="op-deliver",
            expected_revision=1,
            manifest=manifest,
        )
        assert receipt.revision == 2
        assert receipt.idempotent is False
        assert receipt.delivery_id == manifest.delivery_id
        async with dao.engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT delivery_manifest, artifact_id, artifact_version, revision "
                        "FROM agent_operation WHERE user_scope = :u AND operation_id = :o"
                    ),
                    {"u": USER_SCOPE, "o": "op-deliver"},
                )
            ).first()
        assert row is not None
        assert decode_delivery_manifest(row.delivery_manifest) == manifest
        assert row.artifact_id == "art-1"
        assert row.artifact_version == 1
        assert row.revision == 2

    @pytest.mark.asyncio
    async def test_same_content_replay_is_idempotent(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        manifest = _manifest()
        first = await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=manifest
        )
        replay = await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=manifest
        )
        assert first.revision == 2
        assert replay.revision == 2
        assert replay.idempotent is True
        assert replay.phase == "manifest_saved"

    @pytest.mark.asyncio
    async def test_different_content_with_stale_revision_conflicts(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=_manifest()
        )
        staged = _manifest(phase="staged", output_digest="b" * 64, output_bytes=10, staged_path=_STAGED)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_delivery_phase(
                user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=staged
            )
        assert exc.value.code is AgentErrorCode.REVISION_CONFLICT

    @pytest.mark.asyncio
    async def test_illegal_transition_is_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=_manifest()
        )
        published = _manifest(phase="published_confirmed", output_digest="b" * 64, output_bytes=10)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_delivery_phase(
                user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=2, manifest=published
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_unknown_is_absorbing(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=_manifest()
        )
        unknown = _manifest(phase="unknown", error_code="delivery_publish_unverified")
        second = await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=2, manifest=unknown
        )
        assert second.revision == 3
        staged = _manifest(phase="staged", output_digest="b" * 64, output_bytes=10, staged_path=_STAGED)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_delivery_phase(
                user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=3, manifest=staged
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_operation_bound_to_other_delivery_is_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        await dao.save_delivery_phase(
            user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=1, manifest=_manifest()
        )
        other = _manifest(
            command_id="cmd-2", phase="staged", output_digest="b" * 64, output_bytes=10, staged_path=_STAGED
        )
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_delivery_phase(
                user_scope=USER_SCOPE, operation_id="op-deliver", expected_revision=2, manifest=other
            )
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT

    @pytest.mark.asyncio
    async def test_cross_user_manifest_is_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_operation(dao)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_delivery_phase(
                user_scope=USER_SCOPE,
                operation_id="op-deliver",
                expected_revision=1,
                manifest=_manifest(user_scope=OTHER_SCOPE),
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_missing_operation_is_rejected(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as exc:
            await dao.save_delivery_phase(
                user_scope=USER_SCOPE, operation_id="op-missing", expected_revision=0, manifest=_manifest()
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
