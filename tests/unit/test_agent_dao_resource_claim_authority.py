"""资源票据认领的权威身份/代次门契约测试（检视 D21）。

``claim_resource`` 在 guard 行锁后、同一事务内锁目标 execution 行，读取当前
``owner_epoch`` / ``node_id`` / ``dispatch_closed`` / ``current_round_id`` 与所属
轮次的 ``run_epoch`` 作为权威事实（不按请求自报、不锁外读）：

- 旧 owner_epoch（恢复竞争中的旧代次）与错节点拒绝（``POLICY_DENIED``）；
- 请求轮必须属于本 execution；execution 有当前轮时历史轮不可领新票；
- ``dispatch_closed`` 后拒绝新票，既有同票重放仍返回原票据；
- 票据 ``run_epoch`` 取所属轮次 ``agent_round.run_epoch``，且参与重放同票判定；
- 全局上限（检视 D27）：guard 行锁内跨 user_scope 统计开放票据，全局 2 总/4 叶
  与每会话 1 总/每总 3 叶同时生效，受阻原因可区分；同票重放不重新计数；
- 使用额度（检视 R2-I33）：新票据前在 guard 行锁内读取执行树持久累计计数，
  已达 D05 额度（100 模型请求 / 200 工具调用 / 总执行 60 分钟活跃下界）即
  ``BUDGET_BLOCKED``，且受阻原因可区分；同票重放不受额度影响。

真实行锁竞争由 sidecar PG 集成测试
（``tests/integration/test_agent_dao_resource.py``）覆盖；本文件用 capture conn
断言门禁位置、查询列、锁序与错误码。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ResourceClaimRequest,
    ResourceSlotKind,
    UsageAdmissionRequest,
    UsageBudgetLimits,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, first: Any = None, rows: list[Any] | None = None) -> None:
        self._first = first
        self._rows = [] if rows is None else list(rows)
        self.rowcount = 1

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _Result:
        return self

    def scalar_one(self) -> Any:
        return self._rows[0] if self._rows else 0

    def scalar_one_or_none(self) -> Any:
        return self._first


class _ClaimConn:
    """按语句形态分派最小行事实，并记录全部语句与落库票据值。"""

    def __init__(
        self,
        *,
        execution: Any = None,
        round_row: Any = None,
        slot_row: Any = None,
        open_total: int = 0,
        open_leaf: int = 0,
        open_global_total: int = 0,
        open_global_leaf: int = 0,
        fenced_sessions: list[str] | None = None,
        tree_attempts: int = 0,
        tree_calls: int = 0,
        root_active_ms: int = 0,
    ) -> None:
        self.execution = execution
        self.round_row = round_row
        self.slot_row = slot_row
        self.open_total = open_total
        self.open_leaf = open_leaf
        self.open_global_total = open_global_total
        self.open_global_leaf = open_global_leaf
        self.fenced_sessions = [] if fenced_sessions is None else fenced_sessions
        self.tree_attempts = tree_attempts
        self.tree_calls = tree_calls
        self.root_active_ms = root_active_ms
        self.statements: list[str] = []
        self.insert_values: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=[{"native_ids": [], "session_ids": self.fenced_sessions, "asset_keys": []}])
        if "INSERT INTO agent_resource_slot" in sql:
            self.insert_values.append(dict(statement.compile().params))
            return _Result()
        if "SUM(x.model_attempt_count)" in sql:
            # 使用额度执行树聚合（guard 行锁内的持久累计读取）。
            return _Result(
                first=SimpleNamespace(
                    model_attempt_count=self.tree_attempts,
                    tool_call_count=self.tree_calls,
                    root_active_ms=self.root_active_ms,
                )
            )
        if "FROM agent_execution" in sql:
            return _Result(first=self.execution)
        if "FROM agent_round" in sql:
            return _Result(first=self.round_row)
        if "FROM agent_resource_slot" in sql:
            if ":open_kind" in sql:
                # 全局开放票据计数（文本 SQL，按绑定的 open_kind 分派）。
                kind = statement.compile().params.get("open_kind")
                return _Result(rows=[self.open_global_total if kind == "total" else self.open_global_leaf])
            if "WITH RECURSIVE" in sql:
                return _Result(rows=[self.open_leaf])
            if "JOIN agent_execution" in sql:
                return _Result(rows=[self.open_total])
            if "agent_resource_slot.slot_kind" in sql:
                return _Result(first=self.slot_row)
            return _Result(first=SimpleNamespace(slot_id="global_guard"))
        return _Result()

    def _index_of(self, needle: str) -> int:
        return next(i for i, sql in enumerate(self.statements) if needle in sql)

    def _ticket_inserts(self) -> list[dict[str, Any]]:
        # 第一条 INSERT 为 guard 幂等预置，其后才是票据落库。
        return self.insert_values[1:]


def _dao(conn: _ClaimConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _execution(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "execution_kind": "root",
        "session_id": "s1",
        "parent_execution_id": None,
        "deletion_fenced": False,
        "owner_epoch": 3,
        "node_id": "root",
        "dispatch_closed": False,
        "current_round_id": "round-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _round(*, execution_id: str = "exec-1", run_epoch: int = 7) -> Any:
    return SimpleNamespace(execution_id=execution_id, run_epoch=run_epoch)


def _slot(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "slot_kind": ResourceSlotKind.TOTAL.value,
        "execution_id": "exec-1",
        "node_id": "root",
        "round_id": "round-1",
        "owner_epoch": 3,
        "run_epoch": 7,
        "claimed_at": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        "released_at": None,
        "release_verified": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(**overrides: Any) -> ResourceClaimRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "slot_id": "slot-1",
        "slot_kind": ResourceSlotKind.TOTAL,
        "execution_id": "exec-1",
        "node_id": "root",
        "round_id": "round-1",
        "owner_epoch": 3,
        "operation_refs": None,
    }
    values.update(overrides)
    return ResourceClaimRequest(**values)


class TestClaimAuthorityGate:
    async def test_stale_owner_epoch_rejected(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round())
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request(owner_epoch=2))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "owner epoch" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_node_mismatch_rejected(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round())
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request(node_id="other-node"))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "node" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_round_of_other_execution_rejected(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(execution_id="exec-2"))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "round does not belong" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_historical_round_rejected_for_new_ticket(self) -> None:
        # 现行轮 round-2，请求 round-1（属本执行但已被替代）：历史轮不可领新票。
        conn = _ClaimConn(
            execution=_execution(current_round_id="round-2"),
            round_row=_round(run_epoch=7),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request(round_id="round-1"))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "current round" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_missing_round_rejected_when_execution_has_current_round(self) -> None:
        conn = _ClaimConn(execution=_execution(current_round_id="round-1"))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request(round_id=None))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert conn._ticket_inserts() == []

    async def test_dispatch_closed_rejects_new_ticket(self) -> None:
        conn = _ClaimConn(execution=_execution(dispatch_closed=True), round_row=_round())
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "dispatch is closed" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_dispatch_closed_replay_returns_original_ticket(self) -> None:
        conn = _ClaimConn(
            execution=_execution(dispatch_closed=True),
            round_row=_round(),
            slot_row=_slot(),
        )
        ticket = await _dao(conn).claim_resource(_request())
        assert ticket.slot_id == "slot-1"
        assert ticket.run_epoch == 7
        assert conn._ticket_inserts() == []


class TestClaimRunEpoch:
    async def test_new_ticket_uses_authoritative_round_run_epoch(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(run_epoch=42))
        ticket = await _dao(conn).claim_resource(_request())
        assert ticket.run_epoch == 42
        inserts = conn._ticket_inserts()
        assert len(inserts) == 1
        assert inserts[0]["run_epoch"] == 42
        assert inserts[0]["owner_epoch"] == 3

    async def test_replay_across_run_epoch_conflicts(self) -> None:
        # 同 slot 身份但代次不同（旧票据 run_epoch=1 vs 当前轮 7）→ 异输入冲突，
        # 不把跨代次票据当同票幂等复用。
        conn = _ClaimConn(
            execution=_execution(),
            round_row=_round(run_epoch=7),
            slot_row=_slot(run_epoch=1),
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.INPUT_CONFLICT
        assert conn._ticket_inserts() == []

    async def test_no_round_binding_keeps_zero_run_epoch(self) -> None:
        # 无轮次绑定（execution 无 current_round_id 且请求无 round_id）时无轮可
        # 派生，沿用 0 表示「无轮」。
        conn = _ClaimConn(execution=_execution(current_round_id=None))
        ticket = await _dao(conn).claim_resource(_request(round_id=None))
        assert ticket.run_epoch == 0
        assert conn._ticket_inserts()[0]["run_epoch"] == 0


class TestClaimGlobalLimits:
    """检视 D27：全局 2 总/4 叶在 DAO 事务内仲裁，不再由运行宿主承载。"""

    async def test_global_total_limit_blocks_third_ticket(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(), open_global_total=2)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "global total" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_global_leaf_limit_blocks_before_per_root_limit(self) -> None:
        conn = _ClaimConn(
            execution=_execution(execution_kind="leaf", node_id="node-a"),
            round_row=_round(),
            open_global_leaf=4,
            open_leaf=1,  # 每总 1 < 3：受阻原因必须指向全局而非每总
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request(node_id="node-a", slot_kind=ResourceSlotKind.LEAF))
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "global leaf" in exc.value.sanitized_message
        # 全局计数先判即拒：每总树计数（递归 CTE）不会被触及。
        assert conn._index_of(":open_kind") >= 0
        assert "WITH RECURSIVE" not in "".join(conn.statements)
        assert conn._ticket_inserts() == []

    async def test_global_under_limit_still_enforces_session_limit(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(), open_global_total=1, open_total=1)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "session total" in exc.value.sanitized_message

    async def test_global_count_is_cross_scope_open_predicate(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round())
        await _dao(conn).claim_resource(_request())
        global_sql = conn.statements[conn._index_of(":open_kind")]
        assert "slot_kind = :open_kind" in global_sql
        assert "released_at IS NULL" in global_sql
        assert "release_verified = false" in global_sql
        # 全局仲裁跨用户域：不带 user_scope 过滤，也不额外加锁（无新锁序边）。
        assert "user_scope" not in global_sql
        assert "FOR UPDATE" not in global_sql
        # 计数在 guard/execution 行锁之后、票据落库之前。
        assert conn._index_of("agent_resource_slot.slot_id") < conn._index_of(":open_kind")
        insert_indexes = [i for i, sql in enumerate(conn.statements) if "INSERT INTO agent_resource_slot" in sql]
        assert conn._index_of(":open_kind") < insert_indexes[-1]

    async def test_replay_at_global_limit_returns_original_without_recount(self) -> None:
        # 恢复/重放竞争：全局已满时同票重放仍返回原票据，不重新计数直接拒。
        conn = _ClaimConn(execution=_execution(), round_row=_round(), slot_row=_slot(), open_global_total=2)
        ticket = await _dao(conn).claim_resource(_request())
        assert ticket.slot_id == "slot-1"
        assert ":open_kind" not in "".join(conn.statements)
        assert conn._ticket_inserts() == []


class TestClaimAuthorityLockShape:
    async def test_execution_lock_precedes_round_and_reads_authority_columns(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round())
        await _dao(conn).claim_resource(_request())
        execution_sql = conn.statements[conn._index_of("FROM agent_execution")]
        assert "FOR UPDATE" in execution_sql
        # 权威代次/身份列在同一 execution 行锁查询内读取。
        for column in ("owner_epoch", "node_id", "dispatch_closed", "current_round_id"):
            assert f"agent_execution.{column}" in execution_sql
        round_sql = conn.statements[conn._index_of("FROM agent_round")]
        # 轮读取在同一事务内、不加行锁（不引入新锁序边）。
        assert "FOR UPDATE" not in round_sql
        assert "run_epoch" in round_sql
        assert conn._index_of("FROM agent_execution") < conn._index_of("FROM agent_round")

    async def test_slot_replay_read_precedes_authority_gate_write(self) -> None:
        # 锁序仍为 guard → execution → round → 票据；新票写入在门禁之后。
        conn = _ClaimConn(execution=_execution(), round_row=_round())
        await _dao(conn).claim_resource(_request())
        guard_index = conn._index_of("agent_resource_slot.slot_id")
        slot_index = conn._index_of("agent_resource_slot.slot_kind")
        assert guard_index < conn._index_of("FROM agent_execution") < slot_index


class TestClaimUsageBudgetGate:
    """检视 R2-I33：累计使用额度参与准入（guard 行锁内读持久累计）。"""

    async def test_leaf_blocked_when_tree_model_attempt_budget_exhausted(self) -> None:
        conn = _ClaimConn(
            execution=_execution(execution_kind="leaf", node_id="node-a"),
            round_row=_round(),
            tree_attempts=100,
        )
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request(node_id="node-a", slot_kind=ResourceSlotKind.LEAF))
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "model attempt budget" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_total_blocked_when_tree_tool_call_budget_exhausted(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(), tree_calls=200)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "tool call budget" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_blocked_when_root_active_time_lower_bound_reaches_limit(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(), root_active_ms=60 * 60 * 1000)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(_request())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "active time budget" in exc.value.sanitized_message
        assert conn._ticket_inserts() == []

    async def test_injected_limits_override_d05_defaults(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(), tree_attempts=5)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).claim_resource(
                _request(),
                usage_limits=UsageBudgetLimits(model_attempt_limit=5),
            )
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "model attempt budget (5)" in exc.value.sanitized_message

    async def test_usage_gate_runs_after_capacity_and_before_ticket_write(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round())
        await _dao(conn).claim_resource(_request())
        usage_index = conn._index_of("SUM(x.model_attempt_count)")
        insert_indexes = [i for i, sql in enumerate(conn.statements) if "INSERT INTO agent_resource_slot" in sql]
        assert conn._index_of(":open_kind") < usage_index < insert_indexes[-1]

    async def test_replay_at_exhausted_budget_returns_original_without_usage_read(self) -> None:
        conn = _ClaimConn(execution=_execution(), round_row=_round(), slot_row=_slot(), tree_attempts=100)
        ticket = await _dao(conn).claim_resource(_request())
        assert ticket.slot_id == "slot-1"
        assert "SUM(x.model_attempt_count)" not in "".join(conn.statements)
        assert conn._ticket_inserts() == []


class TestAdmitUsageGate:
    """``admit_usage``：先准入后发起的单事务（检视 R2-I33）。"""

    @staticmethod
    def _usage_execution(
        *,
        kind: str = "root",
        attempts: int = 0,
        calls: int = 0,
        active_ms: int = 0,
    ) -> Any:
        return SimpleNamespace(
            execution_kind=kind,
            model_attempt_count=attempts,
            tool_call_count=calls,
            active_ms=active_ms,
        )

    def _admission(self, **overrides: Any) -> Any:
        values: dict[str, Any] = {
            "user_scope": "u1",
            "execution_id": "exec-1",
            "model_attempts": 1,
            "tool_calls": 0,
        }
        values.update(overrides)
        return UsageAdmissionRequest(**values)

    async def test_charge_lands_counters_and_returns_remaining(self) -> None:
        conn = _ClaimConn(execution=self._usage_execution())
        fact = await _dao(conn).admit_usage(self._admission(model_attempts=2, tool_calls=3))
        assert (fact.model_attempt_count, fact.tool_call_count) == (2, 3)
        assert fact.remaining_model_attempts == 98
        assert fact.remaining_tool_calls == 197
        assert fact.remaining_active_ms == 60 * 60 * 1000
        assert any(sql.startswith("UPDATE agent_execution") for sql in conn.statements)

    async def test_blocked_charge_writes_nothing(self) -> None:
        conn = _ClaimConn(execution=self._usage_execution(attempts=99), tree_attempts=100)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).admit_usage(self._admission())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "model attempt budget (100)" in exc.value.sanitized_message
        assert not any(sql.startswith("UPDATE agent_execution") for sql in conn.statements)

    async def test_exact_limit_boundary_allows_last_charge(self) -> None:
        # 树累计 99 + 增量 1 = 100（== 上限）放行；再加 1 才拒。
        conn = _ClaimConn(execution=self._usage_execution(attempts=99), tree_attempts=99)
        fact = await _dao(conn).admit_usage(self._admission())
        assert fact.model_attempt_count == 100
        assert fact.remaining_model_attempts == 0

        blocked = _ClaimConn(execution=self._usage_execution(attempts=100), tree_attempts=100)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(blocked).admit_usage(self._admission())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED

    async def test_root_active_lower_bound_blocks_new_entry(self) -> None:
        conn = _ClaimConn(execution=self._usage_execution(active_ms=60 * 60 * 1000))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).admit_usage(self._admission())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "active time budget (3600000 ms)" in exc.value.sanitized_message

    async def test_leaf_uses_15_minute_active_limit(self) -> None:
        conn = _ClaimConn(execution=self._usage_execution(kind="leaf", active_ms=15 * 60 * 1000))
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).admit_usage(self._admission())
        assert exc.value.code is AgentErrorCode.BUDGET_BLOCKED
        assert "active time budget (900000 ms)" in exc.value.sanitized_message

    async def test_zero_delta_is_probe_only_without_write(self) -> None:
        conn = _ClaimConn(
            execution=self._usage_execution(attempts=7, calls=8, active_ms=1000), tree_attempts=7, tree_calls=8
        )
        fact = await _dao(conn).admit_usage(self._admission(model_attempts=0, tool_calls=0))
        assert (fact.model_attempt_count, fact.tool_call_count, fact.active_ms) == (7, 8, 1000)
        assert fact.remaining_model_attempts == 93
        assert not any(sql.startswith("UPDATE agent_execution") for sql in conn.statements)

    async def test_negative_delta_rejected(self) -> None:
        conn = _ClaimConn(execution=self._usage_execution())
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).admit_usage(self._admission(model_attempts=-1))
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    async def test_missing_execution_invalid(self) -> None:
        conn = _ClaimConn(execution=None)
        with pytest.raises(AgentServiceError) as exc:
            await _dao(conn).admit_usage(self._admission())
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
