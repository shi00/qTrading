"""``AgentDao.read_operation`` 状态映射（N37）：未派发初始态给出确定「未派发」。

- ``phase=prepared`` 且 ``effect_state=not_dispatched``：返回
  ``unavailable_reason="not_dispatched"``，正常返回不报错，不把初始态冒充成
  「结果处理中」（不猜已派发/已成功，R21）；
- 已认领/已派发但未终结仍为 ``result_pending``；``unknown`` 仍 ``effect_unknown``；
- 有已保存结果时优先返回结果：派发状态不影响读取有效结果，也不弹无关错误。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import EffectState, OperationReadQuery
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 8, 0, 0, tzinfo=UTC)
_USER = "user-1"


class _Result:
    def __init__(self, *, first: Any = None, scalar: Any = None) -> None:
        self._first = first
        self._scalar = scalar

    def first(self) -> Any:
        return self._first

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Conn:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(statement)
        return self.results.pop(0)


def _row(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "session_id": "session-1",
        "execution_id": "execution-1",
        "round_id": "round-1",
        "tool_call_id": "call-1",
        "workflow_id": "wf-1",
        "step_id": "n1",
        "call_kind": "tool_call",
        "ordinal": 1,
        "phase": "prepared",
        "effect_state": "not_dispatched",
        "io_state": "not_started",
        "owner_epoch": 1,
        "run_epoch": 1,
        "grant_id": "grant-1",
        "grant_revision": 1,
        "parameter_digest": "a" * 64,
        "request_ref": None,
        "result_ref": None,
        "result_payload": None,
        "worker_refs": None,
        "deadline_at": None,
        "dispatched_at": None,
        "exited_at": None,
        "verified_at": None,
        "active_ms": 0,
        "elapsed_ms": 0,
        "error_code": None,
        "error_category": None,
        "artifact_id": None,
        "artifact_version": None,
        "updated_at": _NOW,
        "revision": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _dao(*results: _Result) -> AgentDao:
    conn = _Conn(list(results))
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=cm)
    return AgentDao(engine)


def _query() -> OperationReadQuery:
    return OperationReadQuery(operation_id="op-1")


class TestReadOperationDispatchState:
    async def test_prepared_not_dispatched_is_determinate(self) -> None:
        dao = _dao(_Result(first=_row()))

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.phase == "prepared"
        assert read.effect_state is EffectState.NOT_DISPATCHED
        assert read.result_available is False
        assert read.unavailable_reason == "not_dispatched"

    async def test_dispatch_claimed_stays_pending(self) -> None:
        dao = _dao(_Result(first=_row(phase="dispatch_claimed")))

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.unavailable_reason == "result_pending"

    async def test_dispatched_stays_pending(self) -> None:
        dao = _dao(_Result(first=_row(phase="dispatched", effect_state="dispatched", io_state="running")))

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.unavailable_reason == "result_pending"

    async def test_unknown_effect_stays_unknown(self) -> None:
        dao = _dao(_Result(first=_row(phase="unknown", effect_state="unknown", io_state="unknown")))

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.unavailable_reason == "effect_unknown"

    async def test_saved_result_returned_even_when_not_dispatched_effect(self) -> None:
        """已保存结果优先返回：派发状态不遮蔽可读结果，也不弹无关错误。"""

        dao = _dao(_Result(first=_row(result_payload={"state": "completed", "text": "思考"})))

        read = await dao.read_operation(_query(), user_scope=_USER)

        assert read.result_available is True
        assert read.result_text is not None and "思考" in read.result_text
        assert read.unavailable_reason is None
