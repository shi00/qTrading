"""``AgentDao.read_operation`` 集成测试（DD §4.2/§9.9/§9.10.1，F-02-3）。

真 embedded PG 上验证操作读取：

- 调用元数据 + effect/io 三维事实单列；``phase/effect_state='unknown'`` 原样
  返回且不给结果（不猜成功/失败，§9.9）；
- 返回体优先 ``result_ref`` 同库正文（owner 自洽/编码/长度/SHA-256 + 码点分页），
  否则 ``result_payload`` 原子；``result_saved`` 却无返回按坏行拒绝，所属会话已
  清理时报 ``result_cleaned``（不把已清当坏行，也不拿空结果冒充成功）；
- 查询给出的 execution/round/tool_call 与行不符 → ``INPUT_INVALID``；不存在/跨
  用户同；坏枚举/坏载荷 → ``OPERATION_UNKNOWN``/``PERSISTENCE_UNAVAILABLE``。

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
    EffectState,
    OperationReadQuery,
    sha256_hex,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import (
    AgentAssetVersion,
    AgentBodyPayload,
    AgentExecution,
    AgentOperation,
    AgentRound,
    AgentSession,
)
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u-op"


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


async def _seed_chain(dao: AgentDao) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(sa.insert(AgentSession).values(user_scope=_USER, session_id="s1", title="操作会话"))
        await conn.execute(
            sa.insert(AgentExecution).values(
                user_scope=_USER,
                execution_id="exec-1",
                session_id="s1",
                execution_kind="root",
                node_id="root",
                display_state="queued",
                goal_coverage="unknown",
                source_module="ui",
                business_request_id="req-exec-1",
                intent_digest="a" * 64,
            )
        )
        await conn.execute(
            sa.insert(AgentRound).values(
                user_scope=_USER,
                round_id="round-1",
                execution_id="exec-1",
                round_number=1,
                workflow_id="wf-round-1",
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


async def _seed_result_body(dao: AgentDao, body_ref: str, value: str, *, tamper: bool = False) -> None:
    data = value.encode("utf-8")
    digest = "e" * 64 if tamper else sha256_hex(data)
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.insert(AgentAssetVersion).values(
                user_scope=_USER,
                asset_id="asset-tool-result",
                asset_version="v1",
                asset_kind="summary",
                display_name="工具返回",
                digest="f" * 64,
                read_state="ready",
                verification_state="program_verified",
                body_ref=body_ref,
            )
        )
        await conn.execute(
            sa.insert(AgentBodyPayload).values(
                user_scope=_USER,
                body_ref=body_ref,
                owner_kind="asset",
                owner_id="asset-tool-result",
                owner_version="v1",
                asset_id="asset-tool-result",
                asset_version="v1",
                encoding="utf-8",
                payload_bytes=data,
                byte_length=len(data),
                payload_digest=digest,
            )
        )


async def _seed_operation(
    dao: AgentDao,
    *,
    operation_id: str = "op-1",
    phase: str = "result_saved",
    effect_state: str = "confirmed",
    io_state: str = "returned",
    result_payload: dict[str, object] | None = None,
    result_ref: str | None = None,
    tool_call_id: str | None = "call-1",
    ordinal: int = 1,
    error_code: str | None = None,
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.insert(AgentOperation).values(
                user_scope=_USER,
                operation_id=operation_id,
                session_id="s1",
                execution_id="exec-1",
                round_id="round-1",
                workflow_id="wf-1",
                step_id="step-1",
                call_kind="tool",
                ordinal=ordinal,
                tool_call_id=tool_call_id,
                owner_epoch=1,
                run_epoch=1,
                phase=phase,
                effect_state=effect_state,
                io_state=io_state,
                result_payload=result_payload,
                result_ref=result_ref,
                error_code=error_code,
                deadline_at=datetime(2026, 6, 2, 8, 0, 10, tzinfo=UTC),
                dispatched_at=datetime(2026, 6, 2, 8, 0, 1, tzinfo=UTC),
                exited_at=datetime(2026, 6, 2, 8, 0, 2, tzinfo=UTC),
                active_ms=1000,
                elapsed_ms=1000,
            )
        )


def _query(**overrides: object) -> OperationReadQuery:
    values: dict[str, object] = {"operation_id": "op-1"}
    values.update(overrides)
    return OperationReadQuery(**values)  # pyright: ignore[reportArgumentType]


class TestReadOperationFacts:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_returns_metadata_effect_and_payload(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao, result_payload={"rows": [1, 2]})

        read = await dao.read_operation(_query(execution_id="exec-1", tool_call_id="call-1"), user_scope=_USER)

        assert read.operation_id == "op-1"
        assert read.phase == "result_saved"
        assert read.effect_state is EffectState.CONFIRMED
        assert read.io_state == "returned"
        assert read.tool_call_id == "call-1"
        assert read.execution_id == "exec-1"
        assert read.result_payload is not None
        assert '"rows"' in read.result_payload
        assert read.result_text is not None
        assert '"rows"' in read.result_text
        assert read.result_available is True
        assert read.deadline_at is not None
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_result_ref_body_is_paged(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        body = "回" * 3000
        await _seed_result_body(dao, "body-op", body)
        await _seed_operation(dao, result_ref="body-op")

        first = await dao.read_operation(_query(limit=1), user_scope=_USER)
        assert first.result_text is not None and len(first.result_text) == 2000
        assert first.next_cursor == "o:op-1:2001"

        second = await dao.read_operation(_query(limit=1, body_cursor=first.next_cursor), user_scope=_USER)
        assert second.result_text == body[2000:]
        assert second.next_cursor is None
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_effect_returns_no_result(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao, phase="unknown", effect_state="unknown", io_state="unknown")

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.phase == "unknown"
        assert read.effect_state is EffectState.UNKNOWN
        assert read.result_text is None
        assert read.result_available is False
        assert read.unavailable_reason == "effect_unknown"
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_failed_operation_reports_failure_not_empty_result(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(
            dao, phase="failed_confirmed", effect_state="not_dispatched", io_state="failed", error_code="TOOL_FAILED"
        )

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.error_code == "TOOL_FAILED"
        assert read.result_available is False
        assert read.unavailable_reason == "failed_confirmed"
        await dao.engine.dispose()  # type: ignore[union-attr]


class TestReadOperationFailures:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_result_saved_without_return_is_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_operation(_query(), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cleaned_session_reports_result_cleaned_not_bad_row(self, real_embedded_pg: ConnectionInfo) -> None:
        """清理后结果缺失是预期态：按会话 ``cleaned_at`` 报 ``result_cleaned``，不当坏行。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao)
        async with dao.engine.begin() as conn:  # type: ignore[union-attr]
            await conn.execute(
                sa.update(AgentSession)
                .where(AgentSession.user_scope == _USER, AgentSession.session_id == "s1")
                .values(cleaned_at=sa.func.now())
            )

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.phase == "result_saved"
        assert read.result_payload is None
        assert read.result_text is None
        assert read.result_available is False
        assert read.unavailable_reason == "result_cleaned"
        await dao.engine.dispose()  # type: ignore[union-attr]
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_scope_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao, result_payload={"ok": True})

        for mismatch in (
            {"execution_id": "exec-other"},
            {"execution_id": "exec-1", "round_id": "round-other"},
            {"tool_call_id": "call-other"},
        ):
            with pytest.raises(AgentServiceError) as excinfo:
                await dao.read_operation(_query(**mismatch), user_scope=_USER)
            assert excinfo.value.code is AgentErrorCode.INPUT_INVALID, mismatch
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_operation_not_found(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_operation(_query(operation_id="missing"), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_payload_path_rejects_body_cursor(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao, result_payload={"rows": [1, 2]})

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_operation(_query(body_cursor="o:op-1:2"), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "atomic" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cursor_from_other_operation_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        body = "回" * 3000
        await _seed_result_body(dao, "body-op", body)
        await _seed_operation(dao, result_ref="body-op")
        await _seed_operation(dao, operation_id="op-2", result_ref="body-op", tool_call_id="call-2", ordinal=2)

        first = await dao.read_operation(_query(limit=1), user_scope=_USER)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_operation(
                _query(operation_id="op-2", limit=1, body_cursor=first.next_cursor), user_scope=_USER
            )
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert "invalid_cursor" in excinfo.value.sanitized_message
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_other_user_scope_not_found(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_operation(dao, result_payload={"ok": True})

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_operation(_query(), user_scope="u-other")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        await dao.engine.dispose()  # type: ignore[union-attr]

    @pytest.mark.asyncio(loop_scope="session")
    async def test_corrupt_result_body_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_chain(dao)
        await _seed_result_body(dao, "body-op", "返回正文", tamper=True)
        await _seed_operation(dao, result_ref="body-op")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.read_operation(_query(), user_scope=_USER)
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
        await dao.engine.dispose()  # type: ignore[union-attr]
