"""D-03 恢复 DAO 集成测试（HANDOFF §8.4，设计 §9.2/§9.5/§9.8）。

真实 embedded PG 上验证：

- ``list_recovery_executions``：非终态投影 + 当前有效轮原生引用（终态/无轮分明）；
- ``mark_dispatch_closed``：按 workflow 身份定位、幂等、未知 ID 不计失败；
- ``save_startup_isolation``：同身份同快照幂等、异快照 INPUT_CONFLICT、
  ``finalize_startup_isolation`` 回填回执与缺失拒绝；
- ``grant_recovery_epoch``：精确集合授予（A 不动 B）、用户终止/终态/无轮/代次
  不前进明确进 ``blocked``、``dispatch_closed`` 重开与 ``run_epoch`` 落库。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    DispatchCloseRequest,
    RecoveryGrantRequest,
    StartupIsolationRecord,
    json_doc,
    stable_id,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import AgentCommand, AgentExecution, AgentOperation, AgentRound, AgentSession
from services.agent_service.recovery import resume_selected_workflows
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "user-recovery"


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


async def _seed_session(dao: AgentDao, session_id: str) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造前置会话
        await conn.execute(
            insert(AgentSession).values(user_scope=_USER, session_id=session_id, title=f"session {session_id}")
        )


async def _seed_execution(
    dao: AgentDao,
    *,
    execution_id: str,
    session_id: str,
    display_state: str = "running",
    kind: str = "root",
    node_id: str = "root",
    dispatch_closed: bool = False,
    owner_epoch: int = 1,
) -> None:
    values: dict = {
        "user_scope": _USER,
        "execution_id": execution_id,
        "session_id": session_id,
        "execution_kind": kind,
        "node_id": node_id,
        "display_state": display_state,
        "goal_coverage": "unknown",
        "dispatch_closed": dispatch_closed,
        "owner_epoch": owner_epoch,
    }
    if kind in ("root", "comparison_lane"):
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造前置执行
        await conn.execute(insert(AgentExecution).values(**values))


async def _link_round(dao: AgentDao, *, execution_id: str, round_id: str) -> None:
    """建立 execution.current_round_id 循环引用（先插执行+轮次，再回填，避免向前 FK）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造循环引用
        await conn.execute(
            sa.update(AgentExecution)
            .where(AgentExecution.user_scope == _USER, AgentExecution.execution_id == execution_id)
            .values(current_round_id=round_id)
        )


async def _seed_round(
    dao: AgentDao,
    *,
    round_id: str,
    execution_id: str,
    workflow_id: str,
    run_epoch: int = 1,
    stop_reason: str | None = None,
    active: bool = True,
    workflow_code_version: str = "0.11.7",
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造前置轮次
        await conn.execute(
            insert(AgentRound).values(
                user_scope=_USER,
                round_id=round_id,
                execution_id=execution_id,
                round_number=1,
                workflow_id=workflow_id,
                workflow_code_version=workflow_code_version,
                run_epoch=run_epoch,
                input_revision=1,
                plan_revision=1,
                stop_reason=stop_reason,
                active_round=active,
            )
        )


async def _seed_operation(
    dao: AgentDao,
    *,
    operation_id: str,
    execution_id: str,
    round_id: str,
    phase: str = "dispatched",
) -> None:
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内构造未收口 operation
        await conn.execute(
            insert(AgentOperation).values(
                user_scope=_USER,
                operation_id=operation_id,
                session_id="s-1",
                execution_id=execution_id,
                round_id=round_id,
                workflow_id=f"agent_research:{execution_id}",
                step_id=f"step-{operation_id}",
                call_kind="model",
                ordinal=1,
                owner_epoch=1,
                run_epoch=1,
                phase=phase,
                effect_state="dispatched",
                io_state="running",
            )
        )


async def _fetch_execution(dao: AgentDao, execution_id: str) -> Any:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试内直接查回
        return (
            await conn.execute(
                sa.select(
                    AgentExecution.owner_epoch,
                    AgentExecution.dispatch_closed,
                    AgentExecution.revision,
                ).where(
                    AgentExecution.user_scope == _USER,
                    AgentExecution.execution_id == execution_id,
                )
            )
        ).one()


async def _fetch_round(dao: AgentDao, round_id: str) -> int:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试内直接查回
        return (
            await conn.execute(
                sa.select(AgentRound.run_epoch).where(AgentRound.user_scope == _USER, AgentRound.round_id == round_id)
            )
        ).scalar_one()


class TestListRecoveryExecutions:
    async def test_projects_non_terminal_with_current_round(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-open", session_id="s-1")
            await _seed_round(dao, round_id="round-1", execution_id="exec-open", workflow_id="agent_research:open")
            await _link_round(dao, execution_id="exec-open", round_id="round-1")
            await _seed_execution(dao, execution_id="exec-ended", session_id="s-1", display_state="ended")
            await _seed_execution(dao, execution_id="exec-no-round", session_id="s-1")

            items = await dao.list_recovery_executions(_USER)
            by_id = {item.execution_id: item for item in items}

            assert set(by_id) == {"exec-open", "exec-no-round"}
            opened = by_id["exec-open"]
            assert opened.workflow_id == "agent_research:open"
            assert opened.round_id == "round-1"
            assert opened.run_epoch == 1
            assert opened.dispatch_closed is False
            assert by_id["exec-no-round"].workflow_id is None
            assert by_id["exec-no-round"].round_id is None
            assert by_id["exec-no-round"].ended_at is None
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]  # 释放测试引擎


class TestDispatchFence:
    async def test_closes_by_workflow_identity_idempotently(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1")
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")

            first = await dao.mark_dispatch_closed(
                DispatchCloseRequest(user_scope=_USER, workflow_ids=("agent_research:a", "agent_research:unknown"))
            )
            assert first.closed_execution_ids == ("exec-a",)
            row = await _fetch_execution(dao, "exec-a")
            assert row.dispatch_closed is True
            revision_after_first = row.revision

            second = await dao.mark_dispatch_closed(
                DispatchCloseRequest(user_scope=_USER, workflow_ids=("agent_research:a",))
            )
            assert second.closed_execution_ids == ("exec-a",)
            row = await _fetch_execution(dao, "exec-a")
            assert row.revision == revision_after_first  # 幂等零写回
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]


class TestStartupIsolationCommand:
    async def test_save_is_idempotent_and_conflicts_on_new_snapshot(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            snapshot = json_doc(
                {"workflow_ids": ["agent_research:a"], "original_states": [["agent_research:a", "PENDING"]]}
            )
            record = StartupIsolationRecord(
                user_scope=_USER,
                client_command_id="startup-1",
                target_id="qtrading-agent-runtime",
                snapshot=snapshot,
                receipt_payload=json_doc({"phase": "prepared"}),
            )
            first = await dao.save_startup_isolation(record)
            second = await dao.save_startup_isolation(record)
            assert first == second
            assert first == stable_id("command", [_USER, "qtrading-agent-runtime", "startup-1"])

            async with dao.engine.connect() as conn:  # type: ignore[union-attr]
                kind = (
                    await conn.execute(
                        sa.select(AgentCommand.command_kind).where(AgentCommand.command_record_id == first)
                    )
                ).scalar_one()
                count = (
                    await conn.execute(
                        sa.select(sa.func.count())
                        .select_from(AgentCommand)
                        .where(AgentCommand.command_record_id == first)
                    )
                ).scalar_one()
            assert kind == "startup_isolation"
            assert count == 1

            conflicting = StartupIsolationRecord(
                user_scope=_USER,
                client_command_id="startup-1",
                target_id="qtrading-agent-runtime",
                snapshot=json_doc({"workflow_ids": ["agent_research:other"]}),
                receipt_payload=json_doc({"phase": "prepared"}),
            )
            with pytest.raises(AgentServiceError) as exc_info:
                await dao.save_startup_isolation(conflicting)
            assert exc_info.value.code is AgentErrorCode.INPUT_CONFLICT
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_finalize_updates_receipt_and_rejects_missing(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            command_id = await dao.save_startup_isolation(
                StartupIsolationRecord(
                    user_scope=_USER,
                    client_command_id="startup-2",
                    target_id="qtrading-agent-runtime",
                    snapshot=json_doc({"workflow_ids": []}),
                    receipt_payload=json_doc({"phase": "prepared"}),
                )
            )
            receipt = json_doc({"ready": True, "blocking_reasons": []})
            await dao.finalize_startup_isolation(
                user_scope=_USER, isolation_command_id=command_id, receipt_payload=receipt
            )
            async with dao.engine.connect() as conn:  # type: ignore[union-attr]
                payload = (
                    await conn.execute(
                        sa.select(AgentCommand.receipt_payload).where(AgentCommand.command_record_id == command_id)
                    )
                ).scalar_one()
            assert payload == {"ready": True, "blocking_reasons": []}

            with pytest.raises(AgentServiceError) as exc_info:
                await dao.finalize_startup_isolation(
                    user_scope=_USER,
                    isolation_command_id=stable_id("command", [_USER, "qtrading-agent-runtime", "missing"]),
                    receipt_payload=receipt,
                )
            assert exc_info.value.code is AgentErrorCode.INPUT_INVALID
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]


class TestRecoveryGrant:
    async def test_grants_exact_selection_and_blocks_ineligible(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao, "s-1")
            # A：可恢复
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1", dispatch_closed=True)
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            # B：用户终止（不复活）
            await _seed_execution(dao, execution_id="exec-b", session_id="s-1")
            await _seed_round(
                dao,
                round_id="round-b",
                execution_id="exec-b",
                workflow_id="agent_research:b",
                stop_reason="user_termination",
            )
            await _link_round(dao, execution_id="exec-b", round_id="round-b")
            # C：终态
            await _seed_execution(dao, execution_id="exec-c", session_id="s-1", display_state="failed")
            # D：无有效轮
            await _seed_execution(dao, execution_id="exec-d", session_id="s-1")
            # E：代次未前进
            await _seed_execution(dao, execution_id="exec-e", session_id="s-1", owner_epoch=9)
            await _seed_round(
                dao, round_id="round-e", execution_id="exec-e", workflow_id="agent_research:e", run_epoch=9
            )
            await _link_round(dao, execution_id="exec-e", round_id="round-e")
            # F：版本不兼容（兼容矩阵门）
            await _seed_execution(dao, execution_id="exec-f", session_id="s-1")
            await _seed_round(
                dao,
                round_id="round-f",
                execution_id="exec-f",
                workflow_id="agent_research:f",
                workflow_code_version="9.9.9",
            )
            await _link_round(dao, execution_id="exec-f", round_id="round-f")
            # G：旧执行仍有未收口 operation
            await _seed_execution(dao, execution_id="exec-g", session_id="s-1")
            await _seed_round(dao, round_id="round-g", execution_id="exec-g", workflow_id="agent_research:g")
            await _link_round(dao, execution_id="exec-g", round_id="round-g")
            await _seed_operation(dao, operation_id="op-g", execution_id="exec-g", round_id="round-g")
            # H：未核状态（unknown）
            await _seed_execution(dao, execution_id="exec-h", session_id="s-1", display_state="unknown")
            await _seed_round(dao, round_id="round-h", execution_id="exec-h", workflow_id="agent_research:h")
            await _link_round(dao, execution_id="exec-h", round_id="round-h")
            # I：暂停中（须走继续语义，不得直接 resume 绕过暂停原因）
            await _seed_execution(dao, execution_id="exec-i", session_id="s-1", display_state="paused")
            await _seed_round(dao, round_id="round-i", execution_id="exec-i", workflow_id="agent_research:i")
            await _link_round(dao, execution_id="exec-i", round_id="round-i")

            fact = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=(
                        "exec-a",
                        "exec-b",
                        "exec-c",
                        "exec-d",
                        "exec-e",
                        "exec-f",
                        "exec-g",
                        "exec-h",
                        "exec-i",
                        "exec-missing",
                    ),
                    owner_epoch=9,
                    run_epoch=9,
                    application_version="0.11.7",
                )
            )
            assert fact.granted_execution_ids == ("exec-a",)
            assert fact.workflow_ids == ("agent_research:a",)
            blocked = dict(fact.blocked)
            assert blocked == {
                "exec-b": "user_termination",
                "exec-c": "already_terminal",
                "exec-d": "no_active_round",
                "exec-e": "epoch_not_advanced",
                "exec-f": "version_mismatch",
                "exec-g": "operation_in_flight",
                "exec-h": "state_unverified",
                "exec-i": "state_unverified",
                "exec-missing": "not_found",
            }

            granted = await _fetch_execution(dao, "exec-a")
            assert granted.owner_epoch == 9
            assert granted.dispatch_closed is False
            assert await _fetch_round(dao, "round-a") == 9
            # A 不动 B：B 的代次与门闩保持原样
            untouched_b = await _fetch_execution(dao, "exec-b")
            assert untouched_b.owner_epoch == 1
            assert await _fetch_round(dao, "round-b") == 1
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_replay_same_epoch_is_blocked(self, real_embedded_pg) -> None:
        """重复恢复同代次不双写（R22：代次必须前进）。"""
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1")
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            first = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-a",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version="0.11.7",
                )
            )
            assert first.granted_execution_ids == ("exec-a",)
            replay = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-a",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version="0.11.7",
                )
            )
            assert replay.granted_execution_ids == ()
            assert dict(replay.blocked)["exec-a"] == "epoch_not_advanced"
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_grant_then_resume_exact_set_and_replay_no_double(self, real_embedded_pg) -> None:
        """M4：真实 DAO 授予 + resume 精确集合；同代次重放不再 resume（不双记）。"""
        dao = await _setup(real_embedded_pg.url)
        resumed: list[str] = []

        class _Client:
            async def list_workflows_async(self, **kwargs: Any) -> list[Any]:
                # 必要叶解析：本用例无直属叶执行行，返回空集合（不扩入其他研究）
                return []

            async def cancel_workflows_async(self, workflow_ids: list[str], **kwargs: Any) -> None:
                raise AssertionError("cancel not expected")

            async def resume_workflows_async(self, workflow_ids: list[str]) -> Any:
                resumed.extend(workflow_ids)
                return []

        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1")
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            # B：用户终止，不因同一批选择被复活
            await _seed_execution(dao, execution_id="exec-b", session_id="s-1")
            await _seed_round(
                dao,
                round_id="round-b",
                execution_id="exec-b",
                workflow_id="agent_research:b",
                stop_reason="user_termination",
            )
            await _link_round(dao, execution_id="exec-b", round_id="round-b")

            first = await resume_selected_workflows(
                user_scope=_USER,
                execution_ids=("exec-a", "exec-b"),
                owner_epoch=3,
                run_epoch=3,
                application_version="0.11.7",
                client=_Client(),
                registry=dao,
            )
            assert first.ready is True
            assert resumed == ["agent_research:a"]
            assert dict(first.blocked)["exec-b"] == "user_termination"

            replay = await resume_selected_workflows(
                user_scope=_USER,
                execution_ids=("exec-a", "exec-b"),
                owner_epoch=3,
                run_epoch=3,
                application_version="0.11.7",
                client=_Client(),
                registry=dao,
            )
            assert replay.ready is False
            assert replay.blocking_reasons == ("no_granted_execution",)
            assert resumed == ["agent_research:a"]  # 重放未追加 resume（不双记）
            assert dict(replay.blocked)["exec-a"] == "epoch_not_advanced"
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]


async def _direct_blocking(fn: Any) -> Any:
    return fn()


class TestAdvisoryLockInstanceGuard:
    """真实 PG：互斥取得、释放后可接管（HANDOFF §8.4 第 1 条 / §8.6）。"""

    async def test_mutual_exclusion_and_takeover_after_release(self, real_embedded_pg) -> None:
        from services.agent_service.recovery import AdvisoryLockInstanceGuard

        url = real_embedded_pg.url
        first = AdvisoryLockInstanceGuard(url, blocking_runner=_direct_blocking)
        second = AdvisoryLockInstanceGuard(url, blocking_runner=_direct_blocking)
        try:
            first_evidence = await first.acquire()
            assert first_evidence.acquired is True
            assert first_evidence.prior_writer_exited is True
            # 同实例重复取锁幂等（不重复建连）
            again = await first.acquire()
            assert again.detail == "already-held"

            blocked = await second.acquire()
            assert blocked.acquired is False
            assert blocked.prior_writer_exited is False
            assert blocked.detail == "lock-held"

            first.release()
            taken = await second.acquire()
            assert taken.acquired is True
            assert taken.detail == "acquired"
        finally:
            first.release()
            second.release()

    async def test_released_lock_can_be_reacquired(self, real_embedded_pg) -> None:
        from services.agent_service.recovery import AdvisoryLockInstanceGuard

        guard = AdvisoryLockInstanceGuard(real_embedded_pg.url, blocking_runner=_direct_blocking)
        assert (await guard.acquire()).acquired is True
        guard.release()
        reacquired = await guard.acquire()
        assert reacquired.acquired is True
        guard.release()
