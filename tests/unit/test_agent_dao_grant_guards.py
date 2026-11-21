"""派发认领守卫契约测试（检视 D07）。

``claim_tool_dispatch`` 首生效必须在同一事务内绑定准确 operation/owner/run
epoch，并核期限与轮次：过期、执行终止、旧轮、错 operation、旧代次一律
``POLICY_DENIED``（零新派发）；胜者 CAS 更新同写 ``dispatch_operation_id``；
重放只读原事实，换 operation 的重放按竞争失败返回 ``REVISION_CONFLICT``。
``revoke_tool_grant`` 不绑定 operation、不核期限/轮次。

真实行锁与 PG 约束行为由 sidecar PG 集成测试
（``tests/integration/test_agent_dao_command_transactions.py::TestGrantCas``）
覆盖；本文件用 capture conn 断言校验分支、写入字段与「拒绝先于写事实」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentErrorCode, AgentServiceError, GrantRevisionRequest, ToolGrantState
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


class _GrantGuardConn:
    """按 SQL 目标表返回决定/执行/轮/operation 事实，并记录读取/写入序列。"""

    def __init__(
        self,
        *,
        decision: Any,
        execution: Any = None,
        round_row: Any = None,
        operation: Any = None,
        cas_rowcount: int = 1,
    ) -> None:
        self.decision = decision
        self.execution = (
            execution
            if execution is not None
            else SimpleNamespace(current_round_id="round-1", display_state="running", owner_epoch=1)
        )
        self.round_row = (
            round_row if round_row is not None else SimpleNamespace(active_round=True, stop_reason=None, run_epoch=1)
        )
        self.operation = operation
        self.cas_rowcount = cas_rowcount
        self.reads: list[str] = []
        self.updates: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        result = MagicMock()
        result.first.return_value = None
        result.rowcount = 1
        if sql.lstrip().upper().startswith("UPDATE"):
            self.updates.append(statement)
            result.rowcount = self.cas_rowcount
        elif "FROM agent_decision" in sql:
            self.reads.append("agent_decision")
            result.first.return_value = self.decision
        elif "FROM agent_execution" in sql:
            self.reads.append("agent_execution")
            result.first.return_value = self.execution
        elif "FROM agent_round" in sql:
            self.reads.append("agent_round")
            result.first.return_value = self.round_row
        elif "FROM agent_operation" in sql:
            self.reads.append("agent_operation")
            result.first.return_value = self.operation
        return result


def _dao(conn: _GrantGuardConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _decision(
    *,
    grant_state: str = "granted",
    grant_revision: int = 1,
    dispatch_operation_id: str | None = None,
    expires_at: datetime | None = None,
    execution_id: str = "exec-1",
    round_id: str = "round-1",
) -> Any:
    return SimpleNamespace(
        grant_id="grant-1",
        grant_revision=grant_revision,
        grant_state=grant_state,
        execution_id=execution_id,
        round_id=round_id,
        expires_at=expires_at,
        dispatch_operation_id=dispatch_operation_id,
    )


def _operation(
    *,
    execution_id: str = "exec-1",
    round_id: str = "round-1",
    grant_id: str | None = "grant-1",
    owner_epoch: int = 1,
    run_epoch: int = 1,
) -> Any:
    return SimpleNamespace(
        execution_id=execution_id,
        round_id=round_id,
        grant_id=grant_id,
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
    )


def _claim_request(expected: int = 1, operation_id: str | None = "op-1") -> GrantRevisionRequest:
    return GrantRevisionRequest(
        user_scope="u1",
        decision_id="d-1",
        expected_grant_revision=expected,
        operation_id=operation_id,
    )


def _revoke_request(expected: int = 1) -> GrantRevisionRequest:
    return GrantRevisionRequest(user_scope="u1", decision_id="d-1", expected_grant_revision=expected)


def _update_params(statement: Any) -> dict[str, Any]:
    return dict(statement.compile().params)


class TestClaimGrantGuards:
    """D07：首生效绑定 operation/owner/run epoch 并核期限/轮次。"""

    async def test_claim_requires_operation_id(self) -> None:
        conn = _GrantGuardConn(decision=_decision())
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request(operation_id=None))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.reads == [] and conn.updates == []

    async def test_claim_expired_rejected_zero_new(self) -> None:
        conn = _GrantGuardConn(decision=_decision(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_superseded_round_rejected(self) -> None:
        conn = _GrantGuardConn(
            decision=_decision(),
            execution=SimpleNamespace(current_round_id="round-2", display_state="running", owner_epoch=1),
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_operation_in_other_round_rejected(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation(round_id="round-other"))
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_operation_in_other_execution_rejected(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation(execution_id="exec-other"))
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_operation_with_other_grant_rejected(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation(grant_id="grant-other"))
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_stale_owner_epoch_rejected(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation(owner_epoch=0))
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_stale_run_epoch_rejected(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation(run_epoch=0))
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
        assert conn.updates == []

    async def test_claim_missing_operation_rejected(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=None)
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.updates == []

    async def test_claim_success_binds_dispatch_operation_id(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation())
        fact = await _dao(conn).claim_tool_dispatch(_claim_request())

        assert fact.grant_state is ToolGrantState.CLAIMED and fact.grant_revision == 2
        assert len(conn.updates) == 1
        params = _update_params(conn.updates[0])
        assert params["dispatch_operation_id"] == "op-1"
        assert params["grant_state"] == "claimed"

    async def test_claim_replay_returns_fact_without_write(self) -> None:
        conn = _GrantGuardConn(
            decision=_decision(grant_state="claimed", grant_revision=2, dispatch_operation_id="op-1"),
        )
        fact = await _dao(conn).claim_tool_dispatch(_claim_request())

        assert fact.grant_state is ToolGrantState.CLAIMED and fact.grant_revision == 2
        assert conn.reads == ["agent_decision"] and conn.updates == []

    async def test_claim_replay_by_other_operation_conflicts(self) -> None:
        conn = _GrantGuardConn(
            decision=_decision(grant_state="claimed", grant_revision=2, dispatch_operation_id="op-1"),
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request(operation_id="op-2"))

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT
        assert conn.updates == []

    async def test_claim_cas_lost_conflicts(self) -> None:
        conn = _GrantGuardConn(decision=_decision(), operation=_operation(), cas_rowcount=0)
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).claim_tool_dispatch(_claim_request())

        assert excinfo.value.code is AgentErrorCode.REVISION_CONFLICT


class TestRevokeGrantGuards:
    """撤销不绑定 operation、不核期限/轮次（过期许可仍必须可撤销）。"""

    async def test_revoke_does_not_bind_operation(self) -> None:
        conn = _GrantGuardConn(decision=_decision(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        fact = await _dao(conn).revoke_tool_grant(_revoke_request())

        assert fact.grant_state is ToolGrantState.REVOKED and fact.grant_revision == 2
        assert len(conn.updates) == 1
        params = _update_params(conn.updates[0])
        assert "dispatch_operation_id" not in params
        assert params["grant_state"] == "revoked"
        assert "agent_operation" not in conn.reads
