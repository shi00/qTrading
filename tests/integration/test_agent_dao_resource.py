"""AgentDao C-04-2 资源票据/累计计数集成测试（§5.4 锁序第 1 条/§5.5/§9.11）。

真实 embedded PG 上的事务语义验证：

- ``claim_resource``：guard 单例行预置+行锁串行、事务内最严限额（§9.11 只查
  每会话 1 总/每总 3 叶；全局 2 总/4 叶为预分配预算由运行宿主准入承载、
  每计划 20 叶为计划级验证 D-02，均不在票据事务重复检查）、幂等重放、
  slot_id 复用拒绝、fence 拒绝、kind 与执行类别核验；
- ``release_resource``：完整匹配（旧票不释新票）、exit_verified 分态
  （verified 释放容量 / unverified 容量保留不放空）、reserved→confirmed 升级；
- ``record_usage_counters``：绝对累计 GREATEST 合并（R22 乱序最终取最大值）、
  重复 checkpoint 幂等零写回、execution 缺失 INPUT_INVALID。

依赖 ``real_embedded_pg`` fixture（临时目录隔离，符合隔离本地验证授权）。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import insert, text

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ResourceClaimRequest,
    ResourceReleaseRequest,
    ResourceSlotKind,
    UsageCounterUpdate,
    validate_resource_claim_request,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentExecution, AgentResourceSlot, AgentSession
from tests._helpers import create_test_engine, make_alembic_cfg

pytestmark = [pytest.mark.integration, pytest.mark.embedded_real]


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
            insert(AgentSession).values(
                user_scope=user_scope,
                session_id=session_id,
                title=f"session {session_id}",
            )
        )


async def _seed_execution(
    dao: AgentDao,
    user_scope: str,
    session_id: str,
    execution_id: str,
    kind: str,
    node_id: str,
    *,
    parent_execution_id: str | None = None,
) -> None:
    """直接构造 execution 行（测试前置；root/comparison_lane 须 intent 键）。"""

    values: dict = {
        "user_scope": user_scope,
        "execution_id": execution_id,
        "session_id": session_id,
        "execution_kind": kind,
        "node_id": node_id,
        "display_state": "queued",
        "goal_coverage": "unknown",
        "parent_execution_id": parent_execution_id,
    }
    if kind in ("root", "comparison_lane"):
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置执行
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_open_slot(
    dao: AgentDao,
    user_scope: str,
    slot_id: str,
    kind: str,
    execution_id: str,
    node_id: str,
    *,
    released: bool = False,
    release_verified: bool = False,
) -> None:
    """直接构造票据行（限额测试预置容量占用；开放谓词由 released/verified 组合驱动）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造票据
        await conn.execute(
            insert(AgentResourceSlot).values(
                user_scope=user_scope,
                slot_id=slot_id,
                slot_kind=kind,
                execution_id=execution_id,
                node_id=node_id,
                released_at=None if not released else sa.func.now(),
                release_verified=release_verified,
            )
        )


def _expect_error(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    """强断言错误码（weak-assertion 钩子合规）。"""

    err = excinfo.value
    assert isinstance(err, AgentServiceError)
    assert err.code == code, f"expect {code.value}, got {err.code.value}: {err.sanitized_message}"


def _total_request(
    user_scope: str = "u1", execution_id: str = "root-1", slot_id: str | None = None
) -> ResourceClaimRequest:
    return ResourceClaimRequest(
        user_scope=user_scope,
        slot_id=slot_id or f"slot-{uuid.uuid4().hex[:12]}",
        slot_kind=ResourceSlotKind.TOTAL,
        execution_id=execution_id,
        node_id="root",
        round_id=None,
        owner_epoch=1,
        operation_refs=None,
    )


def _leaf_request(
    user_scope: str = "u1",
    execution_id: str = "leaf-1",
    slot_id: str | None = None,
    *,
    node_id: str = "node-a",
    owner_epoch: int = 1,
) -> ResourceClaimRequest:
    return ResourceClaimRequest(
        user_scope=user_scope,
        slot_id=slot_id or f"slot-{uuid.uuid4().hex[:12]}",
        slot_kind=ResourceSlotKind.LEAF,
        execution_id=execution_id,
        node_id=node_id,
        round_id=None,
        owner_epoch=owner_epoch,
        operation_refs=None,
    )


# ------------------------------------------------------------------
# claim_resource
# ------------------------------------------------------------------


class TestClaimResource:
    async def test_claim_total_creates_guard_and_ticket(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")

        ticket = await dao.claim_resource(_total_request(slot_id="slot-total-1"))
        assert ticket.slot_id == "slot-total-1"
        assert ticket.slot_kind is ResourceSlotKind.TOTAL
        assert ticket.execution_id == "root-1"
        assert ticket.owner_epoch == 1
        assert ticket.run_epoch == 0

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            guard = (
                await conn.execute(
                    text("SELECT slot_kind, execution_id FROM agent_resource_slot WHERE slot_id = 'global_guard'")
                )
            ).first()
            assert guard is not None
            assert guard[0] == "global_guard"
            assert guard[1] is None
            row = (
                await conn.execute(
                    text(
                        "SELECT release_verified, released_at, owner_epoch FROM agent_resource_slot "
                        "WHERE user_scope = 'u1' AND slot_id = 'slot-total-1'"
                    )
                )
            ).first()
            assert row is not None
            assert row[0] is False
            assert row[1] is None
            assert row[2] == 1
        await engine.dispose()

    async def test_claim_replay_same_input_returns_original_ticket(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")

        first = await dao.claim_resource(_total_request(slot_id="slot-replay"))
        replay = await dao.claim_resource(_total_request(slot_id="slot-replay"))
        assert replay == first

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM agent_resource_slot WHERE user_scope = 'u1' AND slot_id = 'slot-replay'")
                )
            ).scalar_one()
            assert count == 1
        await engine.dispose()

    async def test_claim_same_slot_id_different_input_conflicts(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_execution(dao, "u1", "s1", "lane-1", "comparison_lane", "lane")
        await dao.claim_resource(_total_request(slot_id="slot-c", execution_id="root-1"))

        # 同 slot_id 异 execution → 冲突（票据身份完整比对，先于限额核定）
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-c", execution_id="lane-1"))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

    async def test_claim_unknown_execution_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(execution_id="missing"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_claim_total_on_leaf_execution_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "leaf-x", "leaf", "node-a")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(execution_id="leaf-x"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_claim_leaf_on_root_execution_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_leaf_request(execution_id="root-1"))
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_claim_reserved_slot_id_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        with pytest.raises(ValueError) as excinfo:
            validate_resource_claim_request(_total_request(slot_id="global_guard"))
        err = excinfo.value
        assert isinstance(err, AgentValidationError)
        assert err.code == "reserved_slot_id"
        assert err.field_path == "$.slot_id"

    async def test_fenced_execution_rejects_new_claim(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-f", "root", "root")
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == "u1", AgentExecution.execution_id == "root-f")
                .values(deletion_fenced=True)
            )
        await engine.dispose()
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(execution_id="root-f"))
        _expect_error(excinfo, AgentErrorCode.POLICY_DENIED)

    async def test_session_total_limit_one(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_execution(dao, "u1", "s1", "lane-1", "comparison_lane", "lane")
        await dao.claim_resource(_total_request(execution_id="root-1", slot_id="slot-t1"))
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(execution_id="lane-1", slot_id="slot-t2"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "session total" in excinfo.value.sanitized_message

    async def test_session_total_limit_independent_across_sessions(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        for index in range(1, 4):
            await _seed_session(dao, "u1", f"s{index}")
            await _seed_execution(dao, "u1", f"s{index}", f"root-{index}", "root", "root")
        # 每会话 1：三个不同会话各 1 总占位均放行（全局 2 总为预分配预算，
        # 由运行宿主准入承载——§9.11 事务内只查每会话 1/每总 3）
        await dao.claim_resource(_total_request(execution_id="root-1", slot_id="slot-g1"))
        await dao.claim_resource(_total_request(execution_id="root-2", slot_id="slot-g2"))
        await dao.claim_resource(_total_request(execution_id="root-3", slot_id="slot-g3"))

    async def test_unverified_release_keeps_capacity_verified_frees(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")

        await dao.claim_resource(_total_request(slot_id="slot-u1"))
        # 尾段未知释放：容量保留（unknown 在途不放空）
        receipt = await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-u1",
                execution_id="root-1",
                node_id="root",
                owner_epoch=1,
                exit_verified=False,
            )
        )
        assert receipt.released is False
        assert receipt.release_verified is False
        assert receipt.released_at is not None

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-u2"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)

        # 用户保未知结案：verified 升级后容量释放
        confirm = await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-u1",
                execution_id="root-1",
                node_id="root",
                owner_epoch=1,
                exit_verified=True,
            )
        )
        assert confirm.released is True
        assert confirm.release_verified is True

        ticket = await dao.claim_resource(_total_request(slot_id="slot-u2"))
        assert ticket.slot_id == "slot-u2"

    async def test_release_identity_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await dao.claim_resource(_total_request(slot_id="slot-m1"))

        # 旧 owner_epoch 不释新票（旧票不释新票/完整匹配）
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.release_resource(
                ResourceReleaseRequest(
                    user_scope="u1",
                    slot_id="slot-m1",
                    execution_id="root-1",
                    node_id="root",
                    owner_epoch=2,
                    exit_verified=True,
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.release_resource(
                ResourceReleaseRequest(
                    user_scope="u1",
                    slot_id="slot-m1",
                    execution_id="other",
                    node_id="root",
                    owner_epoch=1,
                    exit_verified=True,
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

    async def test_release_unknown_slot_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.release_resource(
                ResourceReleaseRequest(
                    user_scope="u1", slot_id="missing", execution_id="e", node_id="n", owner_epoch=1, exit_verified=True
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_released_slot_id_cannot_be_reused(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await dao.claim_resource(_total_request(slot_id="slot-r"))
        await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-r",
                execution_id="root-1",
                node_id="root",
                owner_epoch=1,
                exit_verified=True,
            )
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-r"))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)


# ------------------------------------------------------------------
# leaf 限额（全局 4 / 每总 3 / 每计划 20）
# ------------------------------------------------------------------


class TestLeafLimits:
    async def _seed_root_with_leaves(
        self, dao: AgentDao, root_id: str, leaf_count: int, *, session_id: str = "s1"
    ) -> None:
        await _seed_execution(dao, "u1", session_id, root_id, "root", "root")
        for index in range(leaf_count):
            leaf_id = f"{root_id}-leaf-{index}"
            await _seed_execution(dao, "u1", session_id, leaf_id, "leaf", f"node-{index}", parent_execution_id=root_id)
            await _seed_open_slot(dao, "u1", f"slot-{leaf_id}", "leaf", leaf_id, f"node-{index}")

    async def test_per_root_leaf_limit_three(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await self._seed_root_with_leaves(dao, "root-a", 3)
        # 第 4 个同 root 叶子拒绝（每总 3）
        await _seed_execution(dao, "u1", "s1", "root-a-leaf-new", "leaf", "node-new", parent_execution_id="root-a")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_leaf_request(execution_id="root-a-leaf-new"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "per-root" in excinfo.value.sanitized_message

    async def test_per_root_limit_independent_across_roots(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await self._seed_root_with_leaves(dao, "root-a", 3)
        # 另一 root 的叶子不受 root-a 树影响（每总 3 按树独立核定）
        await _seed_execution(dao, "u1", "s1", "root-b", "root", "root")
        await _seed_execution(dao, "u1", "s1", "root-b-leaf-0", "leaf", "node-b0", parent_execution_id="root-b")
        ticket = await dao.claim_resource(_leaf_request(execution_id="root-b-leaf-0"))
        assert ticket.execution_id == "root-b-leaf-0"


# ------------------------------------------------------------------
# record_usage_counters（GREATEST 合并，R22）
# ------------------------------------------------------------------


class TestUsageCounters:
    async def _seed_root(self, real_embedded_pg: ConnectionInfo) -> AgentDao:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        return dao

    async def test_counters_first_write(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await self._seed_root(real_embedded_pg)
        fact = await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=3, tool_call_count=7, active_ms=1500
            )
        )
        assert (fact.model_attempt_count, fact.tool_call_count, fact.active_ms) == (3, 7, 1500)

    async def test_counters_greatest_merge_ignores_lower(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await self._seed_root(real_embedded_pg)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=10, tool_call_count=5, active_ms=1200
            )
        )
        # 乱序/回退写入：最终取最大值（R22）
        fact = await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=4, tool_call_count=9, active_ms=800
            )
        )
        assert (fact.model_attempt_count, fact.tool_call_count, fact.active_ms) == (10, 9, 1200)

    async def test_counters_replay_no_revision_bump(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await self._seed_root(real_embedded_pg)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=5, tool_call_count=2, active_ms=100
            )
        )
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            before = (
                await conn.execute(
                    text("SELECT revision FROM agent_execution WHERE user_scope = 'u1' AND execution_id = 'root-1'")
                )
            ).scalar_one()
        await engine.dispose()
        # 同值重复 checkpoint：零写回
        fact = await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=5, tool_call_count=2, active_ms=100
            )
        )
        assert (fact.model_attempt_count, fact.tool_call_count, fact.active_ms) == (5, 2, 100)
        async with engine.connect() as conn:
            after = (
                await conn.execute(
                    text("SELECT revision FROM agent_execution WHERE user_scope = 'u1' AND execution_id = 'root-1'")
                )
            ).scalar_one()
        await engine.dispose()
        assert after == before

    async def test_counters_cross_run_accumulate(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await self._seed_root(real_embedded_pg)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=5, tool_call_count=0, active_ms=0
            )
        )
        fact = await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=8, tool_call_count=3, active_ms=500
            )
        )
        assert (fact.model_attempt_count, fact.tool_call_count, fact.active_ms) == (8, 3, 500)

    async def test_counters_unknown_execution_invalid(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.record_usage_counters(
                UsageCounterUpdate(
                    user_scope="u1", execution_id="missing", model_attempt_count=1, tool_call_count=0, active_ms=0
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)

    async def test_counters_negative_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await self._seed_root(real_embedded_pg)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.record_usage_counters(
                UsageCounterUpdate(
                    user_scope="u1", execution_id="root-1", model_attempt_count=-1, tool_call_count=0, active_ms=0
                )
            )
        _expect_error(excinfo, AgentErrorCode.INPUT_INVALID)
