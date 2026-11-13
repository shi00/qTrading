"""AgentDao C-04-4 阅读水位/决策批次集成测试（§4.2 L436/§9.11/AC33/RR04）。

真实 embedded PG 上的事务语义验证：

- ``save_read_mark_max``：session marker GREATEST 水位（乱序最终取 max）、精确
  已读身份并集合并（不全局清零）、无变化重放（merge 天然幂等）、会话不存在
  INPUT_INVALID；
- ``begin_decision_batch``：global marker 行锁下 batch_seq 单调递增；
- ``postpone_decision_batch``：postponed_through_seq GREATEST 合并、乱序不回退
  不延期限、无批次 INPUT_INVALID（AC33：稍后仅现批次、乱序不造批次）。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ReadMarkCommand,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_DIGEST = "c" * 64


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
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置会话
        await conn.execute(
            insert(AgentSession).values(user_scope=user_scope, session_id=session_id, title=f"session {session_id}")
        )


def _expect_error(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    err = excinfo.value
    assert isinstance(err, AgentServiceError)
    assert err.code == code, f"expect {code.value}, got {err.code.value}: {err.sanitized_message}"


def _mark(
    session_id: str = "s1",
    message_ids: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
    observed_through_seq: int | None = None,
    command_id: str = "cmd-1",
) -> ReadMarkCommand:
    return ReadMarkCommand(
        user_scope="u1",
        session_id=session_id,
        command_id=command_id,
        message_ids=message_ids,
        artifact_refs=artifact_refs,
        observed_through_seq=observed_through_seq,
        observation_scope_digest=None if observed_through_seq is None else _DIGEST,
    )


async def _read_marker(url: str, user_scope: str, kind: str, target_id: str) -> tuple[int, int, int, dict | None]:
    engine = create_test_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT last_seq, batch_seq, postponed_through_seq, read_targets FROM agent_read_mark "
                    "WHERE user_scope = :us AND marker_kind = :kind AND target_id = :target"
                ),
                {"us": user_scope, "kind": kind, "target": target_id},
            )
        ).first()
    await engine.dispose()
    assert row is not None, f"marker {kind}/{target_id} not found"
    return row[0], row[1], row[2], row[3]


# ------------------------------------------------------------------
# save_read_mark_max
# ------------------------------------------------------------------


class TestSaveReadMark:
    async def test_exact_ids_merge_and_persist(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        receipt = await dao.save_read_mark_max(_mark(message_ids=("m-2", "m-1"), artifact_refs=("a-1@2",)))
        assert receipt.last_seq == 0
        assert receipt.read_target_count == 3
        assert receipt.replayed is False

        _last, _batch, _postponed, targets = await _read_marker(real_embedded_pg.url, "u1", "session", "s1")
        assert targets is not None
        assert targets["message_ids"] == ["m-1", "m-2"]
        assert targets["artifact_refs"] == ["a-1@2"]

    async def test_watermark_greatest_out_of_order(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        first = await dao.save_read_mark_max(_mark(observed_through_seq=42))
        assert first.last_seq == 42
        # 乱序写入：最终取最大值（R22），无变化即重放
        replay = await dao.save_read_mark_max(_mark(observed_through_seq=7))
        assert replay.last_seq == 42
        assert replay.replayed is True

    async def test_union_merge_keeps_previous_targets(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.save_read_mark_max(_mark(message_ids=("m-1",), command_id="cmd-a"))
        second = await dao.save_read_mark_max(
            _mark(message_ids=("m-1", "m-2"), artifact_refs=("a-1@1",), command_id="cmd-b")
        )
        assert second.read_target_count == 3
        # 全重复标记：无变化即重放（不造新事实）
        third = await dao.save_read_mark_max(
            _mark(message_ids=("m-2", "m-1"), artifact_refs=("a-1@1",), command_id="cmd-c")
        )
        assert third.replayed is True
        assert third.read_target_count == 3

        _last, _batch, _postponed, targets = await _read_marker(real_embedded_pg.url, "u1", "session", "s1")
        assert targets is not None
        assert targets["message_ids"] == ["m-1", "m-2"]
        assert targets["artifact_refs"] == ["a-1@1"]

    async def test_unknown_session_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_read_mark_max(_mark(session_id="missing"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_scope_pair_rejected_before_db(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        bad = ReadMarkCommand(
            user_scope="u1",
            session_id="s1",
            command_id="cmd-1",
            message_ids=(),
            artifact_refs=(),
            observed_through_seq=5,
            observation_scope_digest=None,
        )
        with pytest.raises(ValueError) as excinfo:
            await dao.save_read_mark_max(bad)
        from core.agent_types import AgentValidationError

        assert isinstance(excinfo.value, AgentValidationError)
        assert excinfo.value.code == "scope_pair_required"


# ------------------------------------------------------------------
# 决策批次（AC33/RR04）
# ------------------------------------------------------------------


class TestDecisionBatch:
    async def test_begin_batch_monotone(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        first = await dao.begin_decision_batch(user_scope="u1")
        assert first.batch_seq == 1
        assert first.replayed is False
        second = await dao.begin_decision_batch(user_scope="u1")
        assert second.batch_seq == 2

        _last, batch, postponed, _targets = await _read_marker(real_embedded_pg.url, "u1", "global", "u1")
        assert batch == 2
        assert postponed == 0

    async def test_postpone_greatest_and_out_of_order(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await dao.begin_decision_batch(user_scope="u1")  # batch 1
        fact = await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=1)
        assert fact.postponed_through_seq == 1
        assert fact.replayed is False
        # 乱序（小于现值）：不回退不延期限，无变化即重放
        stale = await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=0)
        assert stale.postponed_through_seq == 1
        assert stale.replayed is True
        # 新批次后稍后推进
        await dao.begin_decision_batch(user_scope="u1")  # batch 2
        later = await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=2)
        assert later.postponed_through_seq == 2
        assert later.batch_seq == 2

    async def test_postpone_without_batch_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.postpone_decision_batch(user_scope="u1", through_batch_seq=1)
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_batch_and_session_marks_coexist(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await dao.begin_decision_batch(user_scope="u1")
        await dao.save_read_mark_max(_mark(message_ids=("m-1",)))
        # 两类 marker 行独立共存（global 行 session_id NULL）
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT marker_kind, session_id FROM agent_read_mark WHERE user_scope = 'u1' ORDER BY marker_kind"
                    )
                )
            ).all()
            assert {r[0] for r in rows} == {"global", "session"}
            global_row = next(r for r in rows if r[0] == "global")
            assert global_row[1] is None
        await engine.dispose()
