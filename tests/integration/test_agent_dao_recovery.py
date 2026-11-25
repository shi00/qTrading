"""D-03 恢复 DAO 集成测试（HANDOFF §8.4，设计 §9.2/§9.5/§9.8）。

真实 embedded PG 上验证：

- ``list_recovery_executions``：非终态投影 + 当前有效轮原生引用（终态/无轮分明）；
- ``mark_dispatch_closed``：按 workflow 身份定位、幂等、未知 ID 不计失败；
- ``save_startup_isolation``：同身份同快照幂等、异快照 INPUT_CONFLICT、
  ``finalize_startup_isolation`` 回填回执与缺失拒绝；
- ``grant_recovery_epoch``：精确集合授予（A 不动 B）、用户终止/终态/无轮/代次
  不前进明确进 ``blocked``、``dispatch_closed`` 重开与 ``run_epoch`` 落库；
  合法恢复承接原总占位（只占一张、新代可重放/可释放、旧代迟到释放不释新票），
  承接不可核时保原票并拒绝继续（N33）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
    ResourceClaimRequest,
    ResourceReleaseRequest,
    ResourceSlotKind,
    StartupIsolationRecord,
    assess_research_completion,
    json_doc,
    stable_id,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.models import (
    AgentCommand,
    AgentExecution,
    AgentOperation,
    AgentResourceSlot,
    AgentRound,
    AgentSession,
)
from services.agent_service.recovery import resume_selected_workflows
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]

_USER = "user-recovery"

#: 恢复兼容矩阵中的当前应用版本（与 ``_seed_round`` 默认代码版本一致）。
_APP_VERSION = "0.11.7"


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
    parent_execution_id: str | None = None,
    ended_at: datetime | None = None,
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
        "parent_execution_id": parent_execution_id,
        "ended_at": ended_at,
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


async def _claim_total(
    dao: AgentDao,
    *,
    slot_id: str = "slot-total",
    execution_id: str = "exec-a",
    round_id: str = "round-a",
    node_id: str = "root",
    owner_epoch: int = 1,
) -> Any:
    return await dao.claim_resource(
        ResourceClaimRequest(
            user_scope=_USER,
            slot_id=slot_id,
            slot_kind=ResourceSlotKind.TOTAL,
            execution_id=execution_id,
            node_id=node_id,
            round_id=round_id,
            owner_epoch=owner_epoch,
            operation_refs=None,
        )
    )


async def _seed_open_total_slot(
    dao: AgentDao,
    *,
    slot_id: str = "slot-total",
    execution_id: str = "exec-a",
    node_id: str = "root",
    round_id: str = "round-a",
    owner_epoch: int = 1,
    run_epoch: int = 1,
    released: bool = False,
) -> None:
    """直接构造已占用总票（含不可核形态：错节点/释放尾段未知）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造票据
        await conn.execute(
            insert(AgentResourceSlot).values(
                user_scope=_USER,
                slot_id=slot_id,
                slot_kind=ResourceSlotKind.TOTAL.value,
                execution_id=execution_id,
                node_id=node_id,
                round_id=round_id,
                owner_epoch=owner_epoch,
                run_epoch=run_epoch,
                claimed_at=sa.func.now(),
                released_at=sa.func.now() if released else None,
                release_verified=False,
            )
        )


async def _fetch_slot(dao: AgentDao, slot_id: str = "slot-total") -> Any:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试内直接查回
        return (
            await conn.execute(
                sa.select(
                    AgentResourceSlot.owner_epoch,
                    AgentResourceSlot.run_epoch,
                    AgentResourceSlot.round_id,
                    AgentResourceSlot.node_id,
                    AgentResourceSlot.released_at,
                    AgentResourceSlot.release_verified,
                ).where(AgentResourceSlot.user_scope == _USER, AgentResourceSlot.slot_id == slot_id)
            )
        ).one()


async def _fetch_state(dao: AgentDao, execution_id: str) -> Any:
    """逐项查回执行权威事实（状态/代次/结束时刻/修订号）。"""

    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试内直接查回
        return (
            await conn.execute(
                sa.select(
                    AgentExecution.display_state,
                    AgentExecution.owner_epoch,
                    AgentExecution.ended_at,
                    AgentExecution.revision,
                ).where(AgentExecution.user_scope == _USER, AgentExecution.execution_id == execution_id)
            )
        ).one()


async def _count_open_session_total(dao: AgentDao, session_id: str) -> int:
    async with dao.engine.connect() as conn:  # type: ignore[union-attr]  # 测试内统计会话开放总票
        return (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(AgentResourceSlot)
                .join(
                    AgentExecution,
                    sa.and_(
                        AgentExecution.user_scope == AgentResourceSlot.user_scope,
                        AgentExecution.execution_id == AgentResourceSlot.execution_id,
                    ),
                )
                .where(
                    AgentResourceSlot.user_scope == _USER,
                    AgentResourceSlot.slot_kind == ResourceSlotKind.TOTAL.value,
                    AgentExecution.session_id == session_id,
                    sa.or_(
                        AgentResourceSlot.released_at.is_(None),
                        AgentResourceSlot.release_verified.is_(False),
                    ),
                )
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
        """M4：真实 DAO 授予 + resume 精确集合；同代次重试精确查回原授权并补做。"""
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
            # 重试按 (owner_epoch, run_epoch) 从登记投影精确查回原授权：不再授予新代次，
            # 但可补做 resume；未被该授权覆盖的 exec-b 保持原拒绝理由。
            assert replay.ready is True
            assert replay.granted_execution_ids == ("exec-a",)
            assert dict(replay.blocked)["exec-b"] == "user_termination"
            assert resumed == ["agent_research:a", "agent_research:a"]
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]


async def _seed_recoverable_research(dao: AgentDao, *, execution_id: str = "exec-a", round_id: str = "round-a") -> Any:
    """会话 + 可恢复执行/活动轮，并在隔离前认领一张总票（N33 前置现场）。"""

    await _seed_session(dao, "s-1")
    await _seed_execution(dao, execution_id=execution_id, session_id="s-1", owner_epoch=1)
    await _seed_round(dao, round_id=round_id, execution_id=execution_id, workflow_id=f"agent_research:{execution_id}")
    await _link_round(dao, execution_id=execution_id, round_id=round_id)
    ticket = await _claim_total(dao, execution_id=execution_id, round_id=round_id)
    await dao.mark_dispatch_closed(
        DispatchCloseRequest(user_scope=_USER, workflow_ids=(f"agent_research:{execution_id}",))
    )
    return ticket


class TestRecoverySlotTakeover:
    """N33：合法恢复承接原总占位；旧代迟到释放不释新票；承接不可核保原票拒绝。"""

    async def test_grant_adopts_open_total_ticket_and_keeps_single_placement(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            ticket = await _seed_recoverable_research(dao)

            fact = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-a",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version="0.11.7",
                )
            )
            assert fact.granted_execution_ids == ("exec-a",)

            slot = await _fetch_slot(dao)
            assert slot.owner_epoch == 2
            assert slot.run_epoch == 2
            assert slot.round_id == "round-a"
            assert slot.node_id == "root"
            assert slot.released_at is None and slot.release_verified is False
            # 始终只占一张：承接原票而非新增/释放重取，会话开放总票恒为 1。
            assert await _count_open_session_total(dao, "s-1") == 1

            # 新代按同一 slot_id 重放：原票据原样返回（零新事实，仍只占一张）。
            replay = await _claim_total(dao, owner_epoch=2)
            assert replay.slot_id == ticket.slot_id
            assert replay.claimed_at == ticket.claimed_at
            assert replay.owner_epoch == 2 and replay.run_epoch == 2
            assert await _count_open_session_total(dao, "s-1") == 1
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_old_generation_late_release_cannot_release_adopted_ticket(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_recoverable_research(dao)
            await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-a",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version="0.11.7",
                )
            )

            with pytest.raises(AgentServiceError) as excinfo:
                await dao.release_resource(
                    ResourceReleaseRequest(
                        user_scope=_USER,
                        slot_id="slot-total",
                        execution_id="exec-a",
                        node_id="root",
                        owner_epoch=1,
                        exit_verified=True,
                    )
                )
            assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT
            slot = await _fetch_slot(dao)
            assert slot.owner_epoch == 2
            assert slot.released_at is None and slot.release_verified is False
            assert await _count_open_session_total(dao, "s-1") == 1

            # 新代释放成功后才放空容量（真实退出确认）。
            receipt = await dao.release_resource(
                ResourceReleaseRequest(
                    user_scope=_USER,
                    slot_id="slot-total",
                    execution_id="exec-a",
                    node_id="root",
                    owner_epoch=2,
                    exit_verified=True,
                )
            )
            assert receipt.released is True and receipt.release_verified is True
            assert await _count_open_session_total(dao, "s-1") == 0
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_unverifiable_takeover_keeps_original_ticket_and_blocks_grant(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1", dispatch_closed=True, owner_epoch=1)
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            # 票据节点与执行当前节点不一致：承接无法核对归属。
            await _seed_open_total_slot(dao, node_id="other-node")

            fact = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-a",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version="0.11.7",
                )
            )
            assert fact.granted_execution_ids == ()
            assert dict(fact.blocked)["exec-a"] == "slot_takeover_unverified"
            # 保原票：原代次/占用不动，执行不被授予（保原票并拒绝继续）。
            slot = await _fetch_slot(dao)
            assert slot.owner_epoch == 1 and slot.run_epoch == 1
            assert slot.released_at is None and slot.release_verified is False
            granted = await _fetch_execution(dao, "exec-a")
            assert granted.owner_epoch == 1 and granted.dispatch_closed is True
            assert await _count_open_session_total(dao, "s-1") == 1
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_unknown_release_tail_blocks_takeover(self, real_embedded_pg) -> None:
        """旧代释放尾段未知（released_at 有值但未核）不放空，也不得被承接。"""

        dao = await _setup(real_embedded_pg.url)
        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1", dispatch_closed=True, owner_epoch=1)
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            await _seed_open_total_slot(dao, released=True)

            fact = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-a",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version="0.11.7",
                )
            )
            assert fact.granted_execution_ids == ()
            assert dict(fact.blocked)["exec-a"] == "slot_takeover_unverified"
            slot = await _fetch_slot(dao)
            assert slot.owner_epoch == 1 and slot.release_verified is False
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]

    async def test_recovery_receipt_not_ready_when_takeover_unverifiable(self, real_embedded_pg) -> None:
        """承接不可核即不授予 → 手动恢复回执不得报 ready（不把不可用当恢复成功）。"""

        dao = await _setup(real_embedded_pg.url)
        resumed: list[str] = []

        class _Client:
            async def list_workflows_async(self, **kwargs: Any) -> list[Any]:
                return []

            async def cancel_workflows_async(self, workflow_ids: list[str], **kwargs: Any) -> None:
                raise AssertionError("cancel not expected")

            async def resume_workflows_async(self, workflow_ids: list[str]) -> Any:
                resumed.extend(workflow_ids)
                return []

        try:
            await _seed_session(dao, "s-1")
            await _seed_execution(dao, execution_id="exec-a", session_id="s-1", dispatch_closed=True, owner_epoch=1)
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_research:a")
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            await _seed_open_total_slot(dao, node_id="other-node")

            receipt = await resume_selected_workflows(
                user_scope=_USER,
                execution_ids=("exec-a",),
                owner_epoch=2,
                run_epoch=2,
                application_version="0.11.7",
                client=_Client(),
                registry=dao,
            )
            assert receipt.ready is False
            assert receipt.granted_execution_ids == ()
            assert resumed == []
            assert dict(receipt.blocked)["exec-a"] == "slot_takeover_unverified"
            assert "no_granted_execution" in receipt.blocking_reasons
        finally:
            await dao.engine.dispose()  # type: ignore[union-attr]


class _RecordingClient:
    """记录被复接的 workflow（必要叶由原生列举负责，本用例无直属叶）。"""

    def __init__(self) -> None:
        self.resumed: list[str] = []

    async def list_workflows_async(self, **kwargs: Any) -> list[Any]:
        return []

    async def cancel_workflows_async(self, workflow_ids: list[str], **kwargs: Any) -> None:
        raise AssertionError("cancel not expected")

    async def resume_workflows_async(self, workflow_ids: list[str]) -> list[Any]:
        self.resumed.extend(workflow_ids)
        return []


class TestDagInterruptedRecovery:
    """A/B/C DAG 中断后恢复逐项查回（QAC05/QAC07/NFR-R01/R02）。

    场景：根 R 已结束；叶 A 已成功结束；叶 B 崩溃时在途（``display_state=unknown``）；
    叶 C 依赖 B、尚未开始。恢复必须同时满足：

    - **不重派已完成节点**：A/R 不进入恢复候选；误把已完成节点纳入选择时整批
      ``preflight_incomplete`` 拒绝（不静默跳过、不部分恢复）；
    - **不丢未完成节点**：C 可被授予新代次并复接，重启后仍逐项查回；
    - **未知态显式呈现**：B 阻塞为 ``state_unverified``，完成核定保持 ``unknown``，
      不因「有成果/目标 full」被伪装成完成。
    """

    async def test_dag_recovery_never_redispatches_done_and_keeps_unknown(self, real_embedded_pg) -> None:
        dao = await _setup(real_embedded_pg.url)
        ended_at = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
        try:
            await _seed_session(dao, "s-1")
            # R：整棵研究已结束
            await _seed_execution(
                dao, execution_id="exec-root", session_id="s-1", display_state="ended", ended_at=ended_at
            )
            # A：已完成成功叶（不应再被派发）
            await _seed_execution(
                dao,
                execution_id="exec-a",
                session_id="s-1",
                kind="leaf",
                node_id="a",
                parent_execution_id="exec-root",
                display_state="ended",
                ended_at=ended_at,
            )
            await _seed_round(dao, round_id="round-a", execution_id="exec-a", workflow_id="agent_leaf:a", active=False)
            await _link_round(dao, execution_id="exec-a", round_id="round-a")
            # B：崩溃时在途，结果未知（不得伪装完成）
            await _seed_execution(
                dao,
                execution_id="exec-b",
                session_id="s-1",
                kind="leaf",
                node_id="b",
                parent_execution_id="exec-root",
                display_state="unknown",
            )
            await _seed_round(dao, round_id="round-b", execution_id="exec-b", workflow_id="agent_leaf:b")
            await _link_round(dao, execution_id="exec-b", round_id="round-b")
            # C：依赖 B 的后继，尚未开始（不得丢失）
            await _seed_execution(
                dao,
                execution_id="exec-c",
                session_id="s-1",
                kind="leaf",
                node_id="c",
                parent_execution_id="exec-root",
                display_state="queued",
            )
            await _seed_round(dao, round_id="round-c", execution_id="exec-c", workflow_id="agent_leaf:c")
            await _link_round(dao, execution_id="exec-c", round_id="round-c")

            # 1) 候选投影只含非终态节点：已完成 A/R 不重回派发面。
            items = await dao.list_recovery_executions(_USER)
            assert [item.execution_id for item in items] == ["exec-b", "exec-c"]
            by_id = {item.execution_id: item for item in items}
            assert by_id["exec-b"].display_state == "unknown"
            assert by_id["exec-b"].workflow_id == "agent_leaf:b"
            assert by_id["exec-c"].workflow_id == "agent_leaf:c"

            client = _RecordingClient()
            # 2) 误选已完成节点：整批 fail-closed（不静默跳过、零授权零复接）。
            rejected = await resume_selected_workflows(
                user_scope=_USER,
                execution_ids=("exec-root", "exec-a", "exec-b", "exec-c"),
                owner_epoch=2,
                run_epoch=2,
                application_version=_APP_VERSION,
                client=client,
                registry=dao,
            )
            assert rejected.ready is False
            assert rejected.granted_execution_ids == ()
            assert rejected.resumed_workflow_ids == ()
            assert client.resumed == []
            assert dict(rejected.blocked) == {
                "exec-root": "workflow_unknown",
                "exec-a": "workflow_unknown",
            }
            assert rejected.blocking_reasons == ("preflight_incomplete",)

            # 3) 只选未完成 C：授予新代次并复接；不扩入未选节点。
            recovered = await resume_selected_workflows(
                user_scope=_USER,
                execution_ids=("exec-c",),
                owner_epoch=2,
                run_epoch=2,
                application_version=_APP_VERSION,
                client=client,
                registry=dao,
            )
            assert recovered.ready is True
            assert recovered.granted_execution_ids == ("exec-c",)
            assert recovered.resumed_workflow_ids == ("agent_leaf:c",)
            assert recovered.blocked == ()
            assert client.resumed == ["agent_leaf:c"]

            # 4) 未知 B 显式拒绝恢复（不是「已完成」，也不是可静默跳过）。
            blocked_b = await dao.grant_recovery_epoch(
                RecoveryGrantRequest(
                    user_scope=_USER,
                    execution_ids=("exec-b",),
                    owner_epoch=2,
                    run_epoch=2,
                    application_version=_APP_VERSION,
                )
            )
            assert blocked_b.granted_execution_ids == ()
            assert dict(blocked_b.blocked) == {"exec-b": "state_unverified"}

            state_a = await _fetch_state(dao, "exec-a")
            assert (state_a.display_state, state_a.owner_epoch) == ("ended", 1)
            assert state_a.ended_at is not None
            state_b = await _fetch_state(dao, "exec-b")
            assert (state_b.display_state, state_b.owner_epoch, state_b.ended_at) == ("unknown", 1, None)
            state_c = await _fetch_state(dao, "exec-c")
            assert state_c.owner_epoch == 2
            assert await _fetch_round(dao, "round-c") == 2

            # 5) 模拟崩溃重启：新 DAO 实例只凭持久事实逐项查回。
            restarted = AgentDao(create_test_engine(real_embedded_pg.url))
            try:
                reread = await restarted.list_recovery_executions(_USER)
                assert [item.execution_id for item in reread] == ["exec-b", "exec-c"]
                reread_c = next(item for item in reread if item.execution_id == "exec-c")
                assert (reread_c.owner_epoch, reread_c.run_epoch, reread_c.dispatch_closed) == (2, 2, False)
                assert next(item for item in reread if item.execution_id == "exec-b").display_state == "unknown"

                # 同代次重放：不双写、不丢 C（R22 代次必须前进）。
                revision_before = (await _fetch_state(restarted, "exec-c")).revision
                replay = await restarted.grant_recovery_epoch(
                    RecoveryGrantRequest(
                        user_scope=_USER,
                        execution_ids=("exec-c",),
                        owner_epoch=2,
                        run_epoch=2,
                        application_version=_APP_VERSION,
                    )
                )
                assert replay.granted_execution_ids == ()
                assert dict(replay.blocked) == {"exec-c": "epoch_not_advanced"}
                assert (await _fetch_state(restarted, "exec-c")).revision == revision_before

                # 未知态在重启后仍是 unknown，且完成核定不冒充完成。
                unknown_state = (await _fetch_state(restarted, "exec-b")).display_state
                assert unknown_state == "unknown"
                assessment = assess_research_completion(
                    execution_state=unknown_state,
                    final_output_saved=True,
                    goal_coverage="full",
                    convergence_confirmed=True,
                )
                assert assessment.execution_ended is None
                assert assessment.outcome == "unknown"
            finally:
                await restarted.engine.dispose()  # type: ignore[union-attr]  # 释放测试引擎
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
