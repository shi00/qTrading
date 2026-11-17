"""AgentDao 事件登记行锁序契约测试（检视 D04）。

真实行锁竞争/死锁行为由 sidecar PG 集成测试
（``tests/integration/test_agent_dao_events.py::TestRecordEventLockOrder``）覆盖；
本文件用 capture conn 断言 SQL 级锁序：execution 事件必须先
``SELECT ... FROM agent_execution ... FOR UPDATE``，再锁 session——与
``bind_actual_input`` / ``revise_queued`` 的 execution → session 序一致，
双连接不再持锁互等成环。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import EventKind, RecordEventRequest, json_doc
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


class _LockOrderConn:
    """记录 ``FOR UPDATE`` 目标表顺序，并按表返回最小行事实。"""

    def __init__(self) -> None:
        self.locked: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        result = MagicMock()
        result.first.return_value = None
        result.rowcount = 1
        if "FOR UPDATE" in sql and "FROM agent_execution" in sql:
            self.locked.append("agent_execution")
            result.first.return_value = SimpleNamespace(event_seq=0)
        elif "FOR UPDATE" in sql and "FROM agent_session" in sql:
            self.locked.append("agent_session")
            result.first.return_value = SimpleNamespace(event_seq=0)
        elif "FROM agent_round" in sql:
            result.first.return_value = SimpleNamespace(execution_id="exec-1")
        return result

    def begin_nested(self) -> Any:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm


def _dao(conn: _LockOrderConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _request(**overrides: Any) -> RecordEventRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "session_id": "s1",
        "message_id": "m-1",
        "native_event_id": "native-1",
        "event_kind": EventKind.SESSION_CHANGED,
        "subject_id": "s1",
        "subject_revision": 1,
        "payload_kind": "session_changed.v1",
        "payload_canonical_json": json_doc({"revision": 1}),
        "occurred_at": "2026-06-01T12:00:00.000000Z",
    }
    values.update(overrides)
    return RecordEventRequest(**values)


class TestRecordEventLockOrder:
    """D04：execution 事件先 execution 后 session；纯 session 事件只锁 session。"""

    async def test_execution_event_locks_execution_before_session(self) -> None:
        conn = _LockOrderConn()
        fact = await _dao(conn).record_event(
            _request(
                execution_id="exec-1",
                node_id="root",
                round_id="round-1",
                event_kind=EventKind.PUBLIC_MESSAGE_SAVED,
                payload_kind="public_message_saved.v1",
            )
        )
        assert conn.locked == ["agent_execution", "agent_session"]
        assert fact.execution_seq == 1
        assert fact.session_event_seq == 1

    async def test_session_only_event_locks_session_only(self) -> None:
        conn = _LockOrderConn()
        fact = await _dao(conn).record_event(_request())
        assert conn.locked == ["agent_session"]
        assert fact.execution_seq is None
