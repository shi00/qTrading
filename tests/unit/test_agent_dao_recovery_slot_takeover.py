"""恢复授代的总占位承接契约测试（N33）。

``grant_recovery_epoch`` 在 guard 行锁后按 guard → execution → 票据锁序读取
候选与开放总票：可核时同一票据行原地承接给新代次（不释放、不新增），使恢复后
研究仍只占会话唯一总位且新代可重放/释放；承接不可核（开放总票不唯一、节点或
当前轮不一致、释放尾段未知、代次不回退）即保原票并把该项拒绝在 ``blocked``。

真实行锁与 PG 约束行为由 sidecar PG 集成测试
（``tests/integration/test_agent_dao_recovery.py::TestRecoverySlotTakeover``）
覆盖；本文件用 capture conn 断言锁序、承接写入值与不可核零写回。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import RecoveryGrantRequest
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


class _RecoveryConn:
    """按语句形态分派恢复候选/开放总票事实，并记录全部语句与更新值。"""

    def __init__(
        self,
        *,
        open_total_slots: list[Any] | None = None,
        session_open_total: int = 1,
        execution: Any = None,
    ) -> None:
        self.open_total_slots = [] if open_total_slots is None else list(open_total_slots)
        self.session_open_total = session_open_total
        self.execution = execution if execution is not None else _execution()
        self.statements: list[str] = []
        self.updates: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if sql.lstrip().upper().startswith("UPDATE "):
            self.updates.append(statement)
            return _Result()
        if "INSERT INTO agent_resource_slot" in sql:
            return _Result()
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=[])
        if "FROM agent_operation" in sql:
            return _Result(rows=[])
        if "FROM agent_execution" in sql:
            return _Result(rows=[self.execution])
        if "FROM agent_resource_slot" in sql:
            if "JOIN agent_execution" in sql and "count(" in sql:
                return _Result(rows=[self.session_open_total])
            if "agent_resource_slot.execution_id" in sql:
                return _Result(rows=self.open_total_slots)
            return _Result(first=SimpleNamespace(slot_id="global_guard"))
        return _Result()

    def _index_of(self, needle: str) -> int:
        return next(i for i, sql in enumerate(self.statements) if needle in sql)

    def _update_params(self, needle: str) -> dict[str, Any]:
        statement = next(stmt for stmt in self.updates if needle in str(stmt))
        return dict(statement.compile().params)


def _dao(conn: _RecoveryConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _execution(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "execution_id": "exec-1",
        "session_id": "s1",
        "display_state": "running",
        "owner_epoch": 1,
        "node_id": "root",
        "round_id": "round-1",
        "workflow_id": "wf-1",
        "run_epoch": 1,
        "stop_reason": None,
        "active_round": True,
        "workflow_code_version": "1.0.0",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _slot(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "slot_id": "slot-total",
        "node_id": "root",
        "round_id": "round-1",
        "owner_epoch": 1,
        "run_epoch": 1,
        "released_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(**overrides: Any) -> RecoveryGrantRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "execution_ids": ("exec-1",),
        "owner_epoch": 2,
        "run_epoch": 2,
        "application_version": "1.0.0",
    }
    values.update(overrides)
    return RecoveryGrantRequest(**values)


class TestRecoverySlotTakeover:
    async def test_guard_lock_precedes_execution_and_ticket(self) -> None:
        conn = _RecoveryConn(open_total_slots=[_slot()])

        fact = await _dao(conn).grant_recovery_epoch(_request())

        assert fact.granted_execution_ids == ("exec-1",)
        guard_select = conn._index_of("FROM agent_resource_slot")
        execution_select = conn._index_of("FROM agent_execution")
        ticket_select = conn._index_of("agent_resource_slot.execution_id")
        assert conn._index_of("INSERT INTO agent_resource_slot") < guard_select < execution_select < ticket_select

    async def test_verifiable_slot_is_adopted_in_place(self) -> None:
        conn = _RecoveryConn(open_total_slots=[_slot()])

        fact = await _dao(conn).grant_recovery_epoch(_request())

        assert fact.granted_execution_ids == ("exec-1",)
        params = conn._update_params("UPDATE agent_resource_slot")
        assert params["owner_epoch"] == 2
        assert params["run_epoch"] == 2
        assert params["round_id"] == "round-1"
        # 承接只更新原票：本事务仅 guard 幂等预置一条 INSERT，无新票据行。
        assert len([sql for sql in conn.statements if "INSERT INTO agent_resource_slot" in sql]) == 1

    @pytest.mark.parametrize(
        ("slots", "session_open_total", "execution"),
        [
            ([_slot(), _slot(slot_id="slot-total-2")], 2, _execution()),
            ([_slot(node_id="other-node")], 1, _execution()),
            ([_slot(round_id="round-old")], 1, _execution()),
            ([_slot(released_at=object())], 1, _execution()),
            ([_slot(owner_epoch=2)], 1, _execution()),
        ],
        ids=["two-owned", "node-mismatch", "round-mismatch", "release-tail-unknown", "epoch-not-monotonic"],
    )
    async def test_unverifiable_takeover_blocks_grant_with_zero_slot_writes(
        self, slots: list[Any], session_open_total: int, execution: Any
    ) -> None:
        conn = _RecoveryConn(
            open_total_slots=slots,
            session_open_total=session_open_total,
            execution=execution,
        )

        fact = await _dao(conn).grant_recovery_epoch(_request())

        assert fact.granted_execution_ids == ()
        assert dict(fact.blocked)["exec-1"] == "slot_takeover_unverified"
        assert not any("UPDATE agent_resource_slot" in sql for sql in conn.statements)
        assert not any("UPDATE agent_execution" in sql for sql in conn.statements)

    async def test_without_open_slot_grant_does_not_touch_ticket(self) -> None:
        conn = _RecoveryConn(open_total_slots=[])

        fact = await _dao(conn).grant_recovery_epoch(_request())

        assert fact.granted_execution_ids == ("exec-1",)
        assert not any("UPDATE agent_resource_slot" in sql for sql in conn.statements)
