"""AgentDao C-04-2 资源票据/累计计数集成测试（§5.4 锁序第 1 条/§5.5/§9.11）。

真实 embedded PG 上的事务语义验证：

- ``claim_resource``：guard 单例行预置+行锁串行、事务内最严限额（§9.11 + §9.13：
  全局 2 总/4 叶与每会话 1 总/每总 3 叶同时生效，跨 user_scope 的开放票据都计入；
  每计划 20 叶为计划级验证 D-02，不在票据事务重复检查）、幂等重放、
  slot_id 复用拒绝、fence 拒绝、kind 与执行类别核验；执行行锁内权威身份/
  代次门（检视 D21：旧 owner_epoch/错节点/历史轮/dispatch 关闭拒绝新票，
  run_epoch 取所属轮次权威值）；
- ``release_resource``：完整匹配（旧票不释新票）、exit_verified 分态
  （verified 释放容量 / unverified 容量保留不放空）、reserved→confirmed 升级；
- ``record_usage_counters``：绝对累计 GREATEST 合并（R22 乱序最终取最大值）、
  重复 checkpoint 幂等零写回、execution 缺失 INPUT_INVALID；
- ``admit_usage``：先准入后发起（检视 R2-I33）——按执行树累计 100/200 额度与
  60/15 分钟活跃下界的准入、超额拒绝不落计数（不虚增）、重启后读持久事实
  （不重置）、与 GREATEST checkpoint 同向不回退；``claim_resource`` 同样在
  guard 行锁内读取累计额度并拒新票据。

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
    ResourceTicket,
    UsageAdmissionRequest,
    UsageBudgetLimits,
    UsageCounterUpdate,
    validate_resource_claim_request,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.embedded_postgres.protocol import ConnectionInfo
from data.persistence.models import AgentExecution, AgentResourceSlot, AgentRound, AgentSession
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
    owner_epoch: int = 1,
    dispatch_closed: bool = False,
    current_round_id: str | None = None,
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
        "owner_epoch": owner_epoch,
        "dispatch_closed": dispatch_closed,
        "current_round_id": current_round_id,
    }
    if kind in ("root", "comparison_lane"):
        values["source_module"] = "ui"
        values["business_request_id"] = f"req-{execution_id}"
    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置执行
        await conn.execute(insert(AgentExecution).values(**values))


async def _seed_round(
    dao: AgentDao,
    user_scope: str,
    round_id: str,
    execution_id: str,
    *,
    run_epoch: int,
    round_number: int = 1,
) -> None:
    """直接构造轮次行（测试前置；run_epoch 为票据代次的唯一权威来源）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置轮次
        await conn.execute(
            insert(AgentRound).values(
                user_scope=user_scope,
                round_id=round_id,
                execution_id=execution_id,
                round_number=round_number,
                workflow_id=f"wf-{round_id}",
                workflow_code_version="1.0.0",
                run_epoch=run_epoch,
                input_revision=1,
                plan_revision=1,
            )
        )


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
    user_scope: str = "u1",
    execution_id: str = "root-1",
    slot_id: str | None = None,
    *,
    node_id: str = "root",
    round_id: str | None = None,
    owner_epoch: int = 1,
) -> ResourceClaimRequest:
    return ResourceClaimRequest(
        user_scope=user_scope,
        slot_id=slot_id or f"slot-{uuid.uuid4().hex[:12]}",
        slot_kind=ResourceSlotKind.TOTAL,
        execution_id=execution_id,
        node_id=node_id,
        round_id=round_id,
        owner_epoch=owner_epoch,
        operation_refs=None,
    )


def _leaf_request(
    user_scope: str = "u1",
    execution_id: str = "leaf-1",
    slot_id: str | None = None,
    *,
    node_id: str = "node-a",
    round_id: str | None = None,
    owner_epoch: int = 1,
) -> ResourceClaimRequest:
    return ResourceClaimRequest(
        user_scope=user_scope,
        slot_id=slot_id or f"slot-{uuid.uuid4().hex[:12]}",
        slot_kind=ResourceSlotKind.LEAF,
        execution_id=execution_id,
        node_id=node_id,
        round_id=round_id,
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

    async def test_claim_operation_refs_jsonb_round_trip(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")

        request = ResourceClaimRequest(
            user_scope="u1",
            slot_id="slot-refs",
            slot_kind=ResourceSlotKind.TOTAL,
            execution_id="root-1",
            node_id="root",
            round_id=None,
            owner_epoch=1,
            operation_refs='{"operation_id":"op-1","tool_call_id":"tc-1"}',
        )
        await dao.claim_resource(request)

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            refs = (
                await conn.execute(
                    text(
                        "SELECT operation_refs FROM agent_resource_slot "
                        "WHERE user_scope = 'u1' AND slot_id = 'slot-refs'"
                    )
                )
            ).scalar_one()
            assert refs == {"operation_id": "op-1", "tool_call_id": "tc-1"}
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
            await dao.claim_resource(_total_request(slot_id="slot-c", execution_id="lane-1", node_id="lane"))
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
            await dao.claim_resource(_total_request(execution_id="lane-1", node_id="lane", slot_id="slot-t2"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "session total" in excinfo.value.sanitized_message

    async def test_global_total_limit_two_across_sessions_and_scopes(self, real_embedded_pg: ConnectionInfo) -> None:
        """检视 D27：全局 2 总为 DAO 内硬上限（跨 user_scope），第 3 个总被拒。"""

        dao = await _setup(real_embedded_pg.url)
        for index in range(1, 4):
            await _seed_session(dao, "u1", f"s{index}")
            await _seed_execution(dao, "u1", f"s{index}", f"root-{index}", "root", "root")
        await _seed_session(dao, "u2", "s2u")
        await _seed_execution(dao, "u2", "s2u", "root-u2", "root", "root")

        # 每会话各 1 总本可并存，但全局只容 2 个推进中的总执行。
        await dao.claim_resource(_total_request(execution_id="root-1", slot_id="slot-g1"))
        await dao.claim_resource(_total_request(execution_id="root-2", slot_id="slot-g2"))
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(execution_id="root-3", slot_id="slot-g3"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "global total" in excinfo.value.sanitized_message

        # 跨用户域同样受全局上限约束（guard 是全局哨兵，非按域计数）。
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(user_scope="u2", execution_id="root-u2", slot_id="slot-g-u2"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "global total" in excinfo.value.sanitized_message

        # 同票重放不重新计数：全局已满仍返回原票据（恢复竞争不误拒）。
        replay = await dao.claim_resource(_total_request(execution_id="root-1", slot_id="slot-g1"))
        assert replay.slot_id == "slot-g1"

        # 已确认释放才降占用：verified 释放后第 3 个总放行。
        await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-g1",
                execution_id="root-1",
                node_id="root",
                owner_epoch=1,
                exit_verified=True,
            )
        )
        ticket = await dao.claim_resource(_total_request(execution_id="root-3", slot_id="slot-g3"))
        assert ticket.slot_id == "slot-g3"

    async def test_global_total_limit_counts_unknown_in_flight(self, real_embedded_pg: ConnectionInfo) -> None:
        """检视 D27：未知退出（released_at 已记但 release_verified=false）不放空全局容量。"""

        dao = await _setup(real_embedded_pg.url)
        for index in range(1, 4):
            await _seed_session(dao, "u1", f"s{index}")
            await _seed_execution(dao, "u1", f"s{index}", f"root-{index}", "root", "root")

        await dao.claim_resource(_total_request(execution_id="root-1", slot_id="slot-k1"))
        await dao.claim_resource(_total_request(execution_id="root-2", slot_id="slot-k2"))
        # 尾段未知释放：容量保留。
        await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-k1",
                execution_id="root-1",
                node_id="root",
                owner_epoch=1,
                exit_verified=False,
            )
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(execution_id="root-3", slot_id="slot-k3"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "global total" in excinfo.value.sanitized_message

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

    async def test_claim_cross_user_slot_id_collision_conflict(self, real_embedded_pg: ConnectionInfo) -> None:
        """检视闭环 m-3 回归锚：跨用户域 slot_id 撞单列全局 PK 时转 INPUT_CONFLICT
        （数据库失败明确分类，不裸 IntegrityError）。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await dao.claim_resource(_total_request(slot_id="shared-slot"))
        await _seed_session(dao, "u2", "s2u")
        await _seed_execution(dao, "u2", "s2u", "root-u2", "root", "root")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(user_scope="u2", execution_id="root-u2", slot_id="shared-slot"))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)

    async def test_concurrent_session_total_claim_single_winner(self, real_embedded_pg: ConnectionInfo) -> None:
        """检视闭环 m-6 并发冒烟：guard 行锁串行下同会话两并发 total 认领恰一胜
        （每会话 1 总占位），败者 BUDGET_BLOCKED 不伪成功。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_execution(dao, "u1", "s1", "lane-1", "comparison_lane", "lane")

        results = await asyncio.gather(
            dao.claim_resource(_total_request(execution_id="root-1", slot_id="slot-c1")),
            dao.claim_resource(_total_request(execution_id="lane-1", node_id="lane", slot_id="slot-c2")),
            return_exceptions=True,
        )
        outcomes = sorted(
            "ok"
            if isinstance(r, ResourceTicket)
            else ("budget" if isinstance(r, AgentServiceError) and r.code == AgentErrorCode.BUDGET_BLOCKED else "other")
            for r in results
        )
        assert outcomes == ["budget", "ok"], f"expect exactly one winner, got {outcomes}"

    async def test_concurrent_leaf_claim_respects_per_root_limit(self, real_embedded_pg: ConnectionInfo) -> None:
        """检视闭环 m-6 并发冒烟：同 root 四并发 leaf 认领恰三胜（每总 3），败者
        BUDGET_BLOCKED；guard 行锁保证计数无竞态。"""

        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-a", "root", "root")
        for index in range(4):
            await _seed_execution(
                dao, "u1", "s1", f"root-a-leaf-{index}", "leaf", f"n-{index}", parent_execution_id="root-a"
            )
        results = await asyncio.gather(
            *(
                dao.claim_resource(
                    _leaf_request(execution_id=f"root-a-leaf-{index}", node_id=f"n-{index}", slot_id=f"slot-l{index}")
                )
                for index in range(4)
            ),
            return_exceptions=True,
        )
        outcomes = sorted(
            "ok"
            if isinstance(r, ResourceTicket)
            else ("budget" if isinstance(r, AgentServiceError) and r.code == AgentErrorCode.BUDGET_BLOCKED else "other")
            for r in results
        )
        assert outcomes == ["budget", "ok", "ok", "ok"], f"expect exactly three winners, got {outcomes}"


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
            await dao.claim_resource(_leaf_request(execution_id="root-a-leaf-new", node_id="node-new"))
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "per-root" in excinfo.value.sanitized_message

    async def test_per_root_limit_independent_across_roots(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await self._seed_root_with_leaves(dao, "root-a", 3)
        # 另一 root 的叶子不受 root-a 树影响（每总 3 按树独立核定；全局 3 < 4 仍有余量）
        await _seed_execution(dao, "u1", "s1", "root-b", "root", "root")
        await _seed_execution(dao, "u1", "s1", "root-b-leaf-0", "leaf", "node-b0", parent_execution_id="root-b")
        ticket = await dao.claim_resource(_leaf_request(execution_id="root-b-leaf-0", node_id="node-b0"))
        assert ticket.execution_id == "root-b-leaf-0"


# ------------------------------------------------------------------
# 全局上限（检视 D27：全局 2 总 / 4 叶，跨 user_scope）
# ------------------------------------------------------------------


class TestGlobalLimits:
    async def _seed_tree_executions(
        self,
        dao: AgentDao,
        root_id: str,
        leaf_count: int,
        *,
        user_scope: str = "u1",
        session_id: str = "s1",
    ) -> None:
        """只建 root/leaf 执行行（票据由 claim 真实产生，避免预置行重复计数）。"""

        await _seed_execution(dao, user_scope, session_id, root_id, "root", "root")
        for index in range(leaf_count):
            await _seed_execution(
                dao,
                user_scope,
                session_id,
                f"{root_id}-leaf-{index}",
                "leaf",
                f"node-{index}",
                parent_execution_id=root_id,
            )

    async def _claim_leaves(self, dao: AgentDao, root_id: str, count: int, *, user_scope: str = "u1") -> None:
        for index in range(count):
            await dao.claim_resource(
                _leaf_request(
                    user_scope=user_scope,
                    execution_id=f"{root_id}-leaf-{index}",
                    node_id=f"node-{index}",
                    slot_id=f"slot-gl-{user_scope}-{root_id}-{index}",
                )
            )

    async def test_global_leaf_limit_four_across_trees(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await self._seed_tree_executions(dao, "root-a", 2)
        await self._seed_tree_executions(dao, "root-b", 2)
        await self._seed_tree_executions(dao, "root-c", 1)
        # 两棵树各 2 叶：每总 2 < 3，全局恰好 4 占满。
        await self._claim_leaves(dao, "root-a", 2)
        await self._claim_leaves(dao, "root-b", 2)

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(
                _leaf_request(execution_id="root-c-leaf-0", node_id="node-0", slot_id="slot-gl-c0")
            )
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "global leaf" in excinfo.value.sanitized_message

    async def test_global_leaf_limit_spans_user_scopes(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_session(dao, "u2", "s2")
        await self._seed_tree_executions(dao, "root-a", 2, user_scope="u1", session_id="s1")
        await self._seed_tree_executions(dao, "root-u", 2, user_scope="u2", session_id="s2")
        await self._seed_tree_executions(dao, "root-u2", 1, user_scope="u2", session_id="s2")
        await self._claim_leaves(dao, "root-a", 2, user_scope="u1")
        await self._claim_leaves(dao, "root-u", 2, user_scope="u2")

        # 另一用户域的第 5 个叶同样被全局上限拒绝（每总仅 2 未触发树内限额）。
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(
                _leaf_request(user_scope="u2", execution_id="root-u2-leaf-0", node_id="node-0", slot_id="slot-u2-c0")
            )
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "global leaf" in excinfo.value.sanitized_message

    async def test_global_leaf_unknown_in_flight_keeps_capacity(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await self._seed_tree_executions(dao, "root-a", 2)
        await self._seed_tree_executions(dao, "root-b", 2)
        await self._seed_tree_executions(dao, "root-c", 1)

        await dao.claim_resource(
            _leaf_request(execution_id="root-a-leaf-0", node_id="node-0", slot_id="slot-unknown-a0")
        )
        # 尾段未知释放：released_at 已记但退出未确认，全局占用不释放。
        await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-unknown-a0",
                execution_id="root-a-leaf-0",
                node_id="node-0",
                owner_epoch=1,
                exit_verified=False,
            )
        )
        await dao.claim_resource(
            _leaf_request(execution_id="root-a-leaf-1", node_id="node-1", slot_id="slot-unknown-a1")
        )
        await self._claim_leaves(dao, "root-b", 2)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(
                _leaf_request(execution_id="root-c-leaf-0", node_id="node-0", slot_id="slot-unknown-c0")
            )
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "global leaf" in excinfo.value.sanitized_message

        # 用户保未知结案（verified 升级）后容量释放，第 5 个叶放行。
        await dao.release_resource(
            ResourceReleaseRequest(
                user_scope="u1",
                slot_id="slot-unknown-a0",
                execution_id="root-a-leaf-0",
                node_id="node-0",
                owner_epoch=1,
                exit_verified=True,
            )
        )
        ticket = await dao.claim_resource(
            _leaf_request(execution_id="root-c-leaf-0", node_id="node-0", slot_id="slot-unknown-c1")
        )
        assert ticket.execution_id == "root-c-leaf-0"

    async def test_concurrent_global_total_claim_two_winners(self, real_embedded_pg: ConnectionInfo) -> None:
        """guard 行锁序化：三会话并发认领总票恰两胜（全局 2），败者 BUDGET_BLOCKED。"""

        dao = await _setup(real_embedded_pg.url)
        for index in range(1, 4):
            await _seed_session(dao, "u1", f"s{index}")
            await _seed_execution(dao, "u1", f"s{index}", f"root-{index}", "root", "root")

        results = await asyncio.gather(
            *(
                dao.claim_resource(_total_request(execution_id=f"root-{index}", slot_id=f"slot-gc{index}"))
                for index in range(1, 4)
            ),
            return_exceptions=True,
        )
        outcomes = sorted(
            "ok"
            if isinstance(r, ResourceTicket)
            else ("budget" if isinstance(r, AgentServiceError) and r.code == AgentErrorCode.BUDGET_BLOCKED else "other")
            for r in results
        )
        assert outcomes == ["budget", "ok", "ok"], f"expect exactly two winners, got {outcomes}"


# ------------------------------------------------------------------
# 权威身份/代次门（检视 D21）
# ------------------------------------------------------------------


async def _bind_current_round(dao: AgentDao, execution_id: str, round_id: str) -> None:
    """轮次行建好后把 execution 的现行轮绑定到它（后置更新避免循环 FK 顺序问题）。"""

    async with dao.engine.begin() as conn:  # type: ignore[union-attr]  # 测试内直接构造前置绑定
        await conn.execute(
            sa.update(AgentExecution)
            .where(AgentExecution.user_scope == "u1", AgentExecution.execution_id == execution_id)
            .values(current_round_id=round_id)
        )


class TestClaimAuthority:
    async def test_stale_owner_epoch_rejected_current_epoch_claims(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root", owner_epoch=2)
        await _seed_round(dao, "u1", "round-1", "root-1", run_epoch=2)
        await _bind_current_round(dao, "root-1", "round-1")

        # 旧 owner_epoch 拿不到新票（恢复竞争中的旧代次）。
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-stale", round_id="round-1", owner_epoch=1))
        _expect_error(excinfo, AgentErrorCode.POLICY_DENIED)

        ticket = await dao.claim_resource(_total_request(slot_id="slot-current", round_id="round-1", owner_epoch=2))
        assert ticket.owner_epoch == 2
        assert ticket.run_epoch == 2

    async def test_node_mismatch_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-node", node_id="other-node"))
        _expect_error(excinfo, AgentErrorCode.POLICY_DENIED)

    async def test_round_of_other_execution_rejected(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_execution(dao, "u1", "s1", "root-2", "root", "root")
        await _seed_round(dao, "u1", "round-9", "root-2", run_epoch=1)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-wrong-round", round_id="round-9"))
        _expect_error(excinfo, AgentErrorCode.POLICY_DENIED)

    async def test_historical_round_cannot_claim_new_ticket(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_round(dao, "u1", "round-1", "root-1", run_epoch=1, round_number=1)
        await _seed_round(dao, "u1", "round-2", "root-1", run_epoch=2, round_number=2)
        await _bind_current_round(dao, "root-1", "round-2")

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-old-round", round_id="round-1"))
        _expect_error(excinfo, AgentErrorCode.POLICY_DENIED)

        ticket = await dao.claim_resource(_total_request(slot_id="slot-new-round", round_id="round-2"))
        assert ticket.round_id == "round-2"
        assert ticket.run_epoch == 2

    async def test_dispatch_closed_rejects_new_ticket_but_replay_returns_original(
        self, real_embedded_pg: ConnectionInfo
    ) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_round(dao, "u1", "round-1", "root-1", run_epoch=5)
        await _bind_current_round(dao, "root-1", "round-1")
        first = await dao.claim_resource(_total_request(slot_id="slot-closed", round_id="round-1"))

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentExecution)
                .where(AgentExecution.user_scope == "u1", AgentExecution.execution_id == "root-1")
                .values(dispatch_closed=True)
            )
        await engine.dispose()

        # 同票重放返回原事实（零新容量），不受 dispatch 关闭影响。
        replay = await dao.claim_resource(_total_request(slot_id="slot-closed", round_id="round-1"))
        assert replay == first
        # 关闭后新票拒绝。
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-closed-new", round_id="round-1"))
        _expect_error(excinfo, AgentErrorCode.POLICY_DENIED)

    async def test_run_epoch_taken_from_authoritative_round(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_round(dao, "u1", "round-1", "root-1", run_epoch=9)
        await _bind_current_round(dao, "root-1", "round-1")

        ticket = await dao.claim_resource(_total_request(slot_id="slot-epoch", round_id="round-1"))
        assert ticket.run_epoch == 9

        engine = create_test_engine(real_embedded_pg.url)
        async with engine.connect() as conn:
            stored = (
                await conn.execute(
                    text("SELECT run_epoch FROM agent_resource_slot WHERE user_scope = 'u1' AND slot_id = 'slot-epoch'")
                )
            ).scalar_one()
            assert stored == 9
        await engine.dispose()

    async def test_different_run_epoch_not_idempotent(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_session(dao, "u1", "s1")
        await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
        await _seed_round(dao, "u1", "round-1", "root-1", run_epoch=1)
        await _bind_current_round(dao, "root-1", "round-1")
        await dao.claim_resource(_total_request(slot_id="slot-gen", round_id="round-1"))

        # 轮代次推进后同一 slot 身份不再等价（跨代次不得当同票幂等复用）。
        engine = create_test_engine(real_embedded_pg.url)
        async with engine.begin() as conn:
            await conn.execute(
                sa.update(AgentRound)
                .where(AgentRound.user_scope == "u1", AgentRound.round_id == "round-1")
                .values(run_epoch=2)
            )
        await engine.dispose()

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-gen", round_id="round-1"))
        _expect_error(excinfo, AgentErrorCode.INPUT_CONFLICT)


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


# ------------------------------------------------------------------
# admit_usage（先准入后发起 + 累计额度，检视 R2-I33）
# ------------------------------------------------------------------


async def _seed_root_and_leaf(dao: AgentDao, *, leaf_id: str = "leaf-1") -> None:
    await _seed_session(dao, "u1", "s1")
    await _seed_execution(dao, "u1", "s1", "root-1", "root", "root")
    await _seed_execution(dao, "u1", "s1", leaf_id, "leaf", "node-a", parent_execution_id="root-1")


async def _counters(dao: AgentDao, execution_id: str) -> tuple[int, int, int]:
    engine = create_test_engine(dao.engine.url.render_as_string(hide_password=False))  # type: ignore[union-attr]  # 测试直读
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT model_attempt_count, tool_call_count, active_ms, revision "
                        "FROM agent_execution WHERE user_scope = 'u1' AND execution_id = :execution_id"
                    ).bindparams(execution_id=execution_id)
                )
            ).first()
    finally:
        await engine.dispose()
    assert row is not None
    return int(row.model_attempt_count), int(row.tool_call_count), int(row.active_ms)


def _admission(execution_id: str = "root-1", *, model: int = 1, tools: int = 0) -> UsageAdmissionRequest:
    return UsageAdmissionRequest(
        user_scope="u1",
        execution_id=execution_id,
        model_attempts=model,
        tool_calls=tools,
    )


class TestAdmitUsage:
    async def test_charge_lands_counters_and_accumulates(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        limits = UsageBudgetLimits()

        first = await dao.admit_usage(_admission(model=2, tools=1), limits=limits)
        assert (first.model_attempt_count, first.tool_call_count) == (2, 1)
        assert first.remaining_model_attempts == 98
        assert first.remaining_tool_calls == 199

        second = await dao.admit_usage(_admission(model=3), limits=limits)
        assert (second.model_attempt_count, second.tool_call_count) == (5, 1)
        assert await _counters(dao, "root-1") == (5, 1, 0)

    async def test_tree_cumulative_limit_blocks_without_inflation(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        limits = UsageBudgetLimits(model_attempt_limit=5, tool_call_limit=5)

        await dao.admit_usage(_admission("root-1", model=3), limits=limits)
        await dao.admit_usage(_admission("leaf-1", model=2), limits=limits)
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.admit_usage(_admission("leaf-1", model=1), limits=limits)
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "model attempt budget (5)" in excinfo.value.sanitized_message
        # 被拒的入口未发生：树内计数保持 5，不虚增。
        assert (await _counters(dao, "root-1"))[0] + (await _counters(dao, "leaf-1"))[0] == 5

    async def test_admission_reads_persisted_facts_after_restart(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        limits = UsageBudgetLimits(model_attempt_limit=4)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="leaf-1", model_attempt_count=4, tool_call_count=0, active_ms=0
            )
        )
        # 模拟重启：另一引擎/DAO 实例只凭持久事实判定，不重置也不虚增。
        restarted = AgentDao(create_test_engine(real_embedded_pg.url))
        with pytest.raises(AgentServiceError) as excinfo:
            await restarted.admit_usage(_admission("root-1", model=1), limits=limits)
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert await _counters(dao, "root-1") == (0, 0, 0)
        await restarted.engine.dispose()  # type: ignore[union-attr]  # 测试内释放自建引擎

    async def test_leaf_and_root_active_limits_diverge(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        limits = UsageBudgetLimits(total_active_ms_limit=1000, leaf_active_ms_limit=100)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="leaf-1", model_attempt_count=0, tool_call_count=0, active_ms=100
            )
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.admit_usage(_admission("leaf-1"), limits=limits)
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "active time budget (100 ms)" in excinfo.value.sanitized_message
        # 同一活跃下界对总执行（1000 ms 上限）仍可准入。
        fact = await dao.admit_usage(_admission("root-1"), limits=limits)
        assert fact.remaining_active_ms == 1000

    async def test_record_after_charge_never_lowers(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        await dao.admit_usage(_admission(model=3), limits=UsageBudgetLimits())
        # 后到的偏低绝对 checkpoint 不回退（R22：GREATEST 与增量同向）。
        fact = await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=2, tool_call_count=0, active_ms=0
            )
        )
        assert fact.model_attempt_count == 3
        assert await _counters(dao, "root-1") == (3, 0, 0)


class TestClaimAgainstUsageBudget:
    async def test_claim_blocked_after_model_attempt_budget_exhausted(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        limits = UsageBudgetLimits(model_attempt_limit=3)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=3, tool_call_count=0, active_ms=0
            )
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(_total_request(slot_id="slot-exhausted"), usage_limits=limits)
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "model attempt budget (3)" in excinfo.value.sanitized_message

    async def test_claim_blocked_after_root_active_time_exhausted(self, real_embedded_pg: ConnectionInfo) -> None:
        dao = await _setup(real_embedded_pg.url)
        await _seed_root_and_leaf(dao)
        limits = UsageBudgetLimits(total_active_ms_limit=500)
        await dao.record_usage_counters(
            UsageCounterUpdate(
                user_scope="u1", execution_id="root-1", model_attempt_count=0, tool_call_count=0, active_ms=500
            )
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await dao.claim_resource(
                _leaf_request(execution_id="leaf-1", slot_id="slot-leaf-exhausted"),
                usage_limits=limits,
            )
        _expect_error(excinfo, AgentErrorCode.BUDGET_BLOCKED)
        assert "active time budget (500 ms)" in excinfo.value.sanitized_message
