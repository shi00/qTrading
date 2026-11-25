"""执行控制命令族（W-01）DAO 集成测试（真实 embedded PG）。

验证：暂停只停目标不连带其它执行、终止/未知结案不释放未核实名额、命令键幂等、
CAS 冲突显式报错、未知结案不改写成果元数据、排队撤回按「确证未启动」归还名额、
重试事实的范围落库与可重试状态门。

本地无 PG sidecar 时由 ``real_embedded_pg`` fixture 自动 skip（见 _real_pg_gate）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, select, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ExecutionControlKind,
    ExecutionControlRequest,
    RetryCommandRecord,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentCommand, AgentExecution, AgentResourceSlot, AgentRound, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "u1"
_SESSION = "s1"


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


async def _seed_session(dao: AgentDao) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(insert(AgentSession).values(user_scope=_USER, session_id=_SESSION, title="t"))


async def _seed_execution(
    dao: AgentDao,
    execution_id: str,
    *,
    kind: str = "root",
    node_id: str | None = None,
    parent_execution_id: str | None = None,
    display_state: str = "running",
    current_round_id: str | None = None,
    dispatch_closed: bool = False,
    goal_coverage: str = "unknown",
) -> None:
    """按既有约定插入执行（根/比较路填请求身份+意图摘要；叶为 NULL）。

    注意：``current_round_id`` 指向 ``agent_round``（循环 FK），需要轮存在的场景
    必须用 :func:`_seed_execution_with_round` 按 execution → round 顺序 seed。
    """

    values: dict = {
        "user_scope": _USER,
        "execution_id": execution_id,
        "session_id": _SESSION,
        "execution_kind": kind,
        "node_id": node_id or execution_id,
        "display_state": display_state,
        "goal_coverage": goal_coverage,
        "parent_execution_id": parent_execution_id,
        "owner_epoch": 1,
        "dispatch_closed": dispatch_closed,
        "current_round_id": current_round_id,
    }
    if kind in ("root", "comparison_lane"):
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
        values["intent_digest"] = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_execution_with_round(
    dao: AgentDao,
    execution_id: str,
    round_id: str,
    *,
    round_number: int = 1,
    **execution_kwargs: Any,
) -> None:
    """按依赖顺序 seed 执行及其有效轮：execution → round → 回填 current_round_id。

    先在 ``current_round_id=NULL`` 下插入执行（满足 ``agent_round`` 的外键先决
    条件），再插轮（同一执行仅一轮 ``active_round=True``），最后回填当前轮。
    """

    await _seed_execution(dao, execution_id, current_round_id=None, **execution_kwargs)
    await _seed_round(dao, round_id, execution_id, round_number=round_number)
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.update(AgentExecution)
            .where(AgentExecution.user_scope == _USER, AgentExecution.execution_id == execution_id)
            .values(current_round_id=round_id)
        )


async def _seed_round(dao: AgentDao, round_id: str, execution_id: str, *, round_number: int = 1) -> None:
    """插入轮次（调用前 execution 必须已存在，否则外键失败）。

    同一 execution 仅允许一个 ``active_round=True``：新轮插入前先把旧轮置 False。
    """

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            sa.update(AgentRound)
            .where(AgentRound.user_scope == _USER, AgentRound.execution_id == execution_id)
            .values(active_round=False)
        )
        await conn.execute(
            insert(AgentRound).values(
                user_scope=_USER,
                round_id=round_id,
                execution_id=execution_id,
                round_number=round_number,
                workflow_id=f"wf-{round_id}",
                workflow_code_version="1.0.0",
                run_epoch=1,
                input_revision=1,
                plan_revision=1,
                active_round=True,
            )
        )


async def _seed_slot(dao: AgentDao, slot_id: str, execution_id: str) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]
        await conn.execute(
            insert(AgentResourceSlot).values(
                user_scope=_USER,
                slot_id=slot_id,
                slot_kind="leaf",
                execution_id=execution_id,
                node_id=f"n-{slot_id}",
            )
        )


async def _execution_row(dao: AgentDao, execution_id: str) -> sa.Row:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]
        return (
            await conn.execute(
                select(
                    AgentExecution.display_state,
                    AgentExecution.goal_coverage,
                    AgentExecution.dispatch_closed,
                    AgentExecution.ended_at,
                    AgentExecution.revision,
                ).where(AgentExecution.user_scope == _USER, AgentExecution.execution_id == execution_id)
            )
        ).one()


async def _slot_row(dao: AgentDao, slot_id: str) -> sa.Row:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]
        return (
            await conn.execute(
                select(AgentResourceSlot.released_at, AgentResourceSlot.release_verified).where(
                    AgentResourceSlot.user_scope == _USER, AgentResourceSlot.slot_id == slot_id
                )
            )
        ).one()


async def _command_count(dao: AgentDao) -> int:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]
        return int(
            (
                await conn.execute(
                    select(sa.func.count()).select_from(AgentCommand).where(AgentCommand.user_scope == _USER)
                )
            ).scalar_one()
        )


def _request(
    execution_id: str,
    kind: ExecutionControlKind,
    *,
    expected_revision: int = 1,
    command_id: str = "cmd-1",
    round_id: str | None = None,
    reason: str | None = None,
) -> ExecutionControlRequest:
    return ExecutionControlRequest(
        user_scope=_USER,
        session_id=_SESSION,
        execution_id=execution_id,
        client_command_id=command_id,
        expected_execution_revision=expected_revision,
        kind=kind,
        round_id=round_id,
        reason=reason,
    )


class TestExecutionControl:
    async def test_pause_only_stops_target_execution(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")
            await _seed_execution_with_round(dao, "e2", "r2")
            await _seed_slot(dao, "slot-e1", "e1")

            receipt = await dao.apply_execution_control(_request("e1", ExecutionControlKind.PAUSE, round_id="r1"))

            assert receipt.applied is True
            assert receipt.display_state == "paused"
            assert receipt.open_slot_ids == ("slot-e1",)
            assert (await _execution_row(dao, "e1")).display_state == "paused"
            assert (await _execution_row(dao, "e1")).dispatch_closed is True
            assert (await _execution_row(dao, "e2")).display_state == "running"
            assert (await _execution_row(dao, "e2")).dispatch_closed is False
            # 退出未核定：票据不释放、容量保持占用。
            slot = await _slot_row(dao, "slot-e1")
            assert slot.released_at is None
            assert slot.release_verified is False
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_terminate_does_not_release_unverified_slot(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")
            await _seed_slot(dao, "slot-e1", "e1")

            receipt = await dao.apply_execution_control(
                _request("e1", ExecutionControlKind.TERMINATE, round_id="r1", reason="用户终止")
            )

            assert receipt.display_state == "ended"
            assert receipt.released_slot_ids == ()
            assert receipt.open_slot_ids == ("slot-e1",)
            slot = await _slot_row(dao, "slot-e1")
            assert slot.released_at is None
            assert slot.release_verified is False
            async with dao.engine.connect() as conn:  # type: ignore[union-attr]
                stop_reason = (
                    await conn.execute(
                        select(AgentRound.stop_reason).where(
                            AgentRound.user_scope == _USER, AgentRound.round_id == "r1"
                        )
                    )
                ).scalar_one()
            assert stop_reason == "user_termination"
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_repeat_command_is_idempotent(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")
            request = _request("e1", ExecutionControlKind.PAUSE, round_id="r1")

            first = await dao.apply_execution_control(request)
            second = await dao.apply_execution_control(request)

            assert first.replayed is False
            assert second.replayed is True
            assert second.display_state == "paused"
            assert second.command_record_id == first.command_record_id
            assert await _command_count(dao) == 1
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_same_key_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")
            await dao.apply_execution_control(_request("e1", ExecutionControlKind.PAUSE, round_id="r1"))

            with pytest.raises(AgentServiceError) as info:
                await dao.apply_execution_control(
                    _request("e1", ExecutionControlKind.PAUSE, round_id="r1", reason="changed")
                )
            assert info.value.code is AgentErrorCode.INPUT_CONFLICT
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_stale_revision_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")

            with pytest.raises(AgentServiceError) as info:
                await dao.apply_execution_control(
                    _request("e1", ExecutionControlKind.PAUSE, expected_revision=9, round_id="r1")
                )
            assert info.value.code is AgentErrorCode.REVISION_CONFLICT
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_round_identity_mismatch_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")

            with pytest.raises(AgentServiceError) as info:
                await dao.apply_execution_control(_request("e1", ExecutionControlKind.PAUSE, round_id="r-other"))
            assert info.value.code is AgentErrorCode.INPUT_CONFLICT
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_close_unknown_keeps_goal_coverage(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1", goal_coverage="unknown")

            receipt = await dao.apply_execution_control(
                _request("e1", ExecutionControlKind.CLOSE_UNKNOWN, round_id="r1", reason="证据不足")
            )

            assert receipt.display_state == "closed_unknown"
            row = await _execution_row(dao, "e1")
            assert row.display_state == "closed_unknown"
            # 未知结案不伪造完成：成果覆盖仍为 unknown。
            assert row.goal_coverage == "unknown"
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_withdraw_queued_returns_slot(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution(dao, "e1", display_state="queued")
            await _seed_execution(dao, "e2", display_state="queued")
            await _seed_slot(dao, "slot-e1", "e1")
            await _seed_slot(dao, "slot-e2", "e2")

            receipt = await dao.apply_execution_control(_request("e1", ExecutionControlKind.WITHDRAW_QUEUED))

            assert receipt.display_state == "withdrawn"
            assert receipt.released_slot_ids == ("slot-e1",)
            slot = await _slot_row(dao, "slot-e1")
            assert slot.released_at is not None
            assert slot.release_verified is True
            # 其它排队项名额不受影响。
            other = await _slot_row(dao, "slot-e2")
            assert other.released_at is None
            assert (await _execution_row(dao, "e2")).display_state == "queued"
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_withdraw_dispatched_is_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")

            with pytest.raises(AgentServiceError) as info:
                await dao.apply_execution_control(_request("e1", ExecutionControlKind.WITHDRAW_QUEUED))
            assert info.value.code is AgentErrorCode.REVISION_CONFLICT
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_pause_does_not_abort_in_flight_completion(self, real_embedded_pg: ConnectionInfo) -> None:
        """设计取向（B）：暂停只关派发门，不中止在途；在途完成后按原身份保存。

        断言：① 暂停不改轮次的原生身份（workflow_id/run_epoch 不变）；② 在途步
        完成后终态投影正常覆盖 ``paused``，执行身份与轮次身份均不变（不被暂停吞掉）。
        """

        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1")

            receipt = await dao.apply_execution_control(_request("e1", ExecutionControlKind.PAUSE, round_id="r1"))
            assert receipt.display_state == "paused"
            assert receipt.native_control_state == "not_wired"

            async with dao.engine.connect() as conn:  # type: ignore[union-attr]
                round_identity = (
                    await conn.execute(
                        select(AgentRound.workflow_id, AgentRound.run_epoch).where(
                            AgentRound.user_scope == _USER, AgentRound.round_id == "r1"
                        )
                    )
                ).one()
            assert round_identity.workflow_id == "wf-r1"
            assert round_identity.run_epoch == 1

            # 在途步完成：同执行、同轮写入终态，按原身份保存（pause 不吞掉成果）。
            fact = await dao.record_execution_terminal_state(execution_id="e1", round_id="r1", display_state="complete")
            assert fact.projected is True
            row = await _execution_row(dao, "e1")
            assert row.display_state == "complete"
            async with dao.engine.connect() as conn:  # type: ignore[union-attr]
                executed = (
                    await conn.execute(
                        select(AgentExecution.execution_id, AgentExecution.current_round_id).where(
                            AgentExecution.user_scope == _USER, AgentExecution.execution_id == "e1"
                        )
                    )
                ).one()
            assert executed.execution_id == "e1"
            assert executed.current_round_id == "r1"
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]


class TestRetryCommand:
    async def test_retry_records_exact_scope(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1", display_state="failed")
            request = RetryCommandRecord(
                user_scope=_USER,
                session_id=_SESSION,
                execution_id="e1",
                client_command_id="cmd-retry",
                expected_execution_revision=1,
                round_id="r1",
                targets=("n1",),
                new_round_id="r2",
                redo=("n1", "n2"),
                preserved=("n3",),
                plan_digest="plan-digest",
                payload_digest="payload-digest",
            )

            receipt = await dao.record_retry_command(request)
            replay = await dao.record_retry_command(request)

            assert receipt.applied is True
            assert receipt.redo == ("n1", "n2")
            assert receipt.preserved == ("n3",)
            assert replay.replayed is True
            assert (await _execution_row(dao, "e1")).display_state == "planned"
            assert (await _execution_row(dao, "e1")).dispatch_closed is False
            assert await _command_count(dao) == 1
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_retry_rejected_for_closed_unknown(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao)
            await _seed_execution_with_round(dao, "e1", "r1", display_state="closed_unknown")
            request = RetryCommandRecord(
                user_scope=_USER,
                session_id=_SESSION,
                execution_id="e1",
                client_command_id="cmd-retry",
                expected_execution_revision=1,
                round_id="r1",
                targets=("n1",),
                new_round_id="r2",
                redo=("n1",),
                preserved=(),
                plan_digest="d",
                payload_digest="p",
            )

            with pytest.raises(AgentServiceError) as info:
                await dao.record_retry_command(request)
            assert info.value.code is AgentErrorCode.REVISION_CONFLICT
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]
