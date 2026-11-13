"""``AgentDao.read_artifact`` 集成测试（DD §4.2/§9.7/§9.10.1，F-02-2）。

真 embedded PG 上验证成果读取：

- 准确版本与所属 session/execution/round；身份不符 → ``INPUT_INVALID``（不借
  execution_id 放行他人/他轮成果）；
- 正文取 ``body_text`` 或 ``body_ref`` 载荷（核 owner/编码/长度/SHA-256），按码点
  分块传输并返回下一游标，不静默截断；
- 正文未保存与已按用户确认清理分开报告（``body_not_saved`` / ``body_cleaned``），
  不把缺失伪装成空成果（R21）；
- 坏行/坏载荷 → ``OPERATION_UNKNOWN`` / ``PERSISTENCE_UNAVAILABLE``。

依赖 ``real_embedded_pg`` fixture（临时目录隔离）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ArtifactReadQuery,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentArtifact, AgentBodyPayload, AgentExecution, AgentRound, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u-art"


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


async def _seed_chain(dao: AgentDao, *, execution_id: str = "exec-1", round_id: str = "round-1") -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(sa.insert(AgentSession).values(user_scope=_USER, session_id="s1", title="成果会话"))
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


async def _seed_artifact(
    dao: AgentDao,
    *,
    artifact_id: str = "art-1",
    version: int = 1,
    execution_id: str = "exec-1",
    round_id: str = "round-1",
    body_text: str | None = "成果正文",
    body_ref: str | None = None,
    payload_text: str | None = None,
    payload_tamper: str | None = None,
    cleaned: bool = False,
    artifact_kind: str = "plan",
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        if body_ref is not None and payload_text is not None:
            data = payload_text.encode("utf-8")
            digest = sha256_hex(data)
            if payload_tamper == "digest":
                digest = "d" * 64
            await conn.execute(
                sa.insert(AgentBodyPayload).values(
                    user_scope=_USER,
                    body_ref=body_ref,
                    owner_kind="artifact",
                    owner_id=artifact_id,
                    owner_version=str(version),
                    artifact_id=artifact_id,
                    artifact_version=version,
                    encoding="utf-8",
                    payload_bytes=data,
                    byte_length=len(data),
                    payload_digest=digest,
                )
            )
        await conn.execute(
            sa.insert(AgentArtifact).values(
                user_scope=_USER,
                artifact_id=artifact_id,
                artifact_version=version,
                session_id="s1",
                execution_id=execution_id,
                round_id=round_id,
                artifact_kind=artifact_kind,
                input_revision=1,
                actual_input_digest="b" * 64,
                actual_model_ref="model/main",
                result_digest="c" * 64,
                source_available_state="current",
                published_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
                created_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC),
                summary="成果摘要",
                body_text=body_text,
                body_ref=body_ref,
                result_payload={"evidence": ["src-1"]},
                cleaned_at=datetime(2026, 6, 3, tzinfo=UTC) if cleaned else None,
            )
        )


def _query(**overrides: object) -> ArtifactReadQuery:
    values: dict[str, object] = {
        "artifact_id": "art-1",
        "artifact_version": 1,
        "execution_id": "exec-1",
        "round_id": "round-1",
    }
    values.update(overrides)
    return ArtifactReadQuery(**values)  # pyright: ignore[reportArgumentType]


class TestReadArtifactContent:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_returns_exact_version_metadata_and_full_body(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao)

        read = await dao.read_artifact(_query(), user_scope=_USER)

        assert read.artifact_id == "art-1"
        assert read.artifact_version == 1
        assert read.session_id == "s1"
        assert read.execution_id == "exec-1"
        assert read.round_id == "round-1"
        assert read.artifact_kind == "plan"
        assert read.result_digest == "c" * 64
        assert read.source_available_state == "current"
        assert read.summary == "成果摘要"
        assert read.result_payload is not None
        assert '"src-1"' in read.result_payload
        assert read.body_text == "成果正文"
        assert read.next_cursor is None
        assert read.body_available is True
        assert read.unavailable_reason is None
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pages_long_body_by_codepoints_without_truncation(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        body = "".join(chr(0x4E00 + index % 100) for index in range(5000))
        await _seed_artifact(dao, body_text=body)

        first = await dao.read_artifact(_query(limit=1), user_scope=_USER)
        assert first.body_text is not None and len(first.body_text) == 2000
        assert first.next_cursor == "b:2001"

        second = await dao.read_artifact(_query(limit=1, body_cursor=first.next_cursor), user_scope=_USER)
        assert second.body_text is not None and len(second.body_text) == 2000
        assert second.next_cursor == "b:4001"

        third = await dao.read_artifact(_query(limit=1, body_cursor=second.next_cursor), user_scope=_USER)
        assert third.body_text == body[4000:]
        assert third.next_cursor is None

        assert (first.body_text + second.body_text + third.body_text) == body  # type: ignore[operator]
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_reads_body_ref_payload(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao, body_text=None, body_ref="body-art", payload_text="载荷成果正文")

        read = await dao.read_artifact(_query(), user_scope=_USER)

        assert read.body_text == "载荷成果正文"
        assert read.body_available is True
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_empty_body_is_available_but_missing_body_is_not(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao, body_text="")
        empty = await dao.read_artifact(_query(), user_scope=_USER)
        assert empty.body_text == ""
        assert empty.body_available is True

        await _seed_artifact(dao, artifact_id="art-2", body_text=None)
        missing = await dao.read_artifact(_query(artifact_id="art-2"), user_scope=_USER)
        assert missing.body_text is None
        assert missing.body_available is False
        assert missing.unavailable_reason == "body_not_saved"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cleaned_artifact_reports_cleaned_not_empty(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao, body_text=None, cleaned=True)

        read = await dao.read_artifact(_query(), user_scope=_USER)

        assert read.body_available is False
        assert read.unavailable_reason == "body_cleaned"
        assert read.cleaned_at is not None
        await dao.engine.dispose()  # type: ignore[union-attr]


class TestReadArtifactFailures:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_identity_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_artifact(_query(execution_id="exec-other"), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_artifact_not_found(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_artifact(_query(artifact_id="missing"), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_stale_cursor_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao, body_text="短正文")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_artifact(_query(body_cursor="b:99"), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "invalid_cursor" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_broken_artifact_row_is_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao)

        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(sa.text("UPDATE agent_artifact SET actual_input_digest = 'short'"))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_artifact(_query(), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_corrupt_body_payload_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_artifact(dao, body_text=None, body_ref="body-art", payload_text="正文", payload_tamper="digest")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_artifact(_query(), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        await dao.engine.dispose()  # type: ignore[union-attr]
