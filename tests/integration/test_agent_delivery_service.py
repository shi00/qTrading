"""E-05-4b 交付编排真文件 + 真 PG 集成（DD SQ-09 / §10.5）。

真实 embedded PG + 真实 ``WindowsManagedFileStore``：
- 阶段逐条落库到 ``published_confirmed``，目标文件内容与回读字段一致，无暂存残留；
- 跨游标正文（>100,000 码点）完整交付，不截断；
- 取消先胜：旧目标字节不变、只清本交付暂存、落 ``cancelled_before_publish``；
- 非标准来源状态按 ``unknown`` 记录，不伪装可用。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    ArtifactDeliveryCommand,
    CommandRef,
    DeliveryTarget,
    decode_delivery_manifest,
    derive_delivery_id,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentArtifact,
    AgentExecution,
    AgentOperation,
    AgentRound,
    AgentSession,
)
from services.agent_service.delivery import WindowsManagedFileStore
from services.agent_service.delivery_render import read_delivery_fields
from services.agent_service.delivery_service import DeliveryCommandService, DeliveryContext
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [
    pytest.mark.integration,
    pytest.mark.embedded_real,
    pytest.mark.skipif(sys.platform != "win32", reason="交付目标保护仅在 win32 验证"),
]

USER_SCOPE = "synthetic-user"
_CLOCK = "2026-11-15T08:00:00.000000Z"


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


async def _seed(dao: AgentDao, *, body_text: str, source_state: str = "available") -> None:
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
                artifact_version=2,
                session_id="s1",
                execution_id="exec-1",
                round_id="round-1",
                artifact_kind="explanation",
                input_revision=1,
                actual_input_digest="b" * 64,
                actual_model_ref="model/main",
                result_digest="c" * 64,
                source_available_state=source_state,
                body_text=body_text,
                result_payload={"s": "00123", "n": None},
            )
        )
        await conn.execute(
            insert(AgentOperation).values(
                user_scope=USER_SCOPE,
                operation_id="op-deliver",
                owner_epoch=1,
                run_epoch=1,
                phase="dispatched",
                effect_state="dispatched",
                io_state="open",
            )
        )


def _command(target: Path, *, format_name: str = "md", **overrides: object) -> ArtifactDeliveryCommand:
    values: dict[str, object] = {
        "command": CommandRef(
            user_scope=USER_SCOPE,
            source_module="ui.artifact",
            business_request_id="req-1",
            command_id="cmd-1",
            expected_revision=1,
        ),
        "artifact_id": "art-1",
        "artifact_version": 2,
        "format": format_name,
        "format_rule_version": 1,
        "delivery_id": "delivery-placeholder",
        "target": DeliveryTarget(
            target_path=str(target),
            expected_existing=False,
            expected_existing_digest=None,
            overwrite_confirmed=False,
        ),
        "expected_delivery_revision": 1,
    }
    values.update(overrides)
    draft = ArtifactDeliveryCommand(**values)  # pyright: ignore[reportArgumentType]
    return ArtifactDeliveryCommand(
        command=draft.command,
        artifact_id=draft.artifact_id,
        artifact_version=draft.artifact_version,
        format=draft.format,
        format_rule_version=draft.format_rule_version,
        delivery_id=derive_delivery_id(draft),
        target=draft.target,
        expected_delivery_revision=draft.expected_delivery_revision,
    )


def _context() -> DeliveryContext:
    return DeliveryContext(
        operation_id="op-deliver",
        command_record_id="rec-1",
        execution_id="exec-1",
        round_id="round-1",
    )


def _service(dao: AgentDao) -> DeliveryCommandService:
    return DeliveryCommandService(
        artifacts=dao,
        dao=dao,
        store=WindowsManagedFileStore(),
        clock=lambda: _CLOCK,
    )


async def _stored_manifest(dao: AgentDao):
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
    return row, decode_delivery_manifest(row.delivery_manifest)


class TestDeliveryRoundTrip:
    @pytest.mark.asyncio
    async def test_publishes_without_staging_residue(self, real_embedded_pg, tmp_path: Path) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed(dao, body_text="成果正文")
        target = tmp_path / "研究结果.md"

        outcome = await _service(dao).deliver(_command(target), _context())

        assert outcome.receipt.phase == "published_confirmed"
        assert outcome.receipt.publish_effect == "published"
        row, manifest = await _stored_manifest(dao)
        assert manifest.phase == "published_confirmed"
        assert manifest.staged_path is None
        assert row.artifact_id == "art-1" and row.artifact_version == 2
        assert row.revision == 6
        assert manifest.output_digest == sha256_hex(target.read_bytes())
        assert [path for path in tmp_path.iterdir()] == [target]

        index = {field.path: field for field in read_delivery_fields(target.read_bytes(), "md")}
        assert index["metadata:artifact_id"].value_text == "art-1"
        assert index["body:"].value_text == "成果正文"
        assert index["payload:/s"].value_text == "00123"
        assert index["payload:/n"].value_type == "null"

    @pytest.mark.asyncio
    async def test_long_body_is_delivered_across_cursors(self, real_embedded_pg, tmp_path: Path) -> None:
        body = "长" * 250_000
        dao = await _setup(real_embedded_pg.url)
        await _seed(dao, body_text=body)
        target = tmp_path / "long.csv"

        outcome = await _service(dao).deliver(_command(target, format_name="csv"), _context())

        assert outcome.receipt.phase == "published_confirmed"
        fields = read_delivery_fields(target.read_bytes(), "csv")
        delivered = "".join(field.value_text for field in fields if field.record == "body")
        assert delivered == body

    @pytest.mark.asyncio
    async def test_non_standard_source_state_is_recorded_as_unknown(self, real_embedded_pg, tmp_path: Path) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed(dao, body_text="正文", source_state="current")
        target = tmp_path / "state.md"

        await _service(dao).deliver(_command(target), _context())

        _, manifest = await _stored_manifest(dao)
        assert manifest.source_state == "unknown"


class TestCancelKeepsOldTarget:
    @pytest.mark.asyncio
    async def test_cancel_before_publish_leaves_existing_file_untouched(self, real_embedded_pg, tmp_path: Path) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed(dao, body_text="新成果")
        target = tmp_path / "existing.md"
        target.write_bytes(b"old content")
        old_digest = sha256_hex(b"old content")
        command = replace(
            _command(target),
            target=DeliveryTarget(
                target_path=str(target),
                expected_existing=True,
                expected_existing_digest=old_digest,
                overwrite_confirmed=True,
            ),
        )

        outcome = await _service(dao).deliver(command, _context(), cancel_requested=lambda: True)

        assert outcome.receipt.phase == "cancelled_before_publish"
        assert outcome.staged_cleaned is True
        assert target.read_bytes() == b"old content"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.md"]
        _, manifest = await _stored_manifest(dao)
        assert manifest.phase == "cancelled_before_publish"
        assert manifest.output_digest is not None
