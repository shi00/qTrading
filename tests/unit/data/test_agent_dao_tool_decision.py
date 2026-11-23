"""``save_tool_decision`` / ``save_tool_result`` 事务事实（SQ-03）。

- 决定 + operation 同事务落库，grant 初值 ``pending`` / revision 1（P1-10：本次
  人答消费前不得是可派发 ``granted``）；
- 有效期必填（``None`` 拒绝），不把「无限期」当默认；
- 同 ``decision_id`` 重放只读原事实；归属/操作缺失即拒绝；
- 结果三态映射 phase/effect/io，operation 不存在拒绝写入。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    SaveToolDecisionRequest,
    SaveToolResultRequest,
    ToolGrantState,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_DIGEST = "a" * 64
_SCHEMA_DIGEST = "b" * 64
_SCOPE_DIGEST = "c" * 64
_EXPIRES_AT = (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class _Result:
    def __init__(self, *, first: Any = None, scalar: Any = 0, rowcount: int = 1) -> None:
        self._first = first
        self._scalar = scalar
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._first

    def scalar_one(self) -> Any:
        return self._scalar


class _Conn:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(statement)
        return self.results.pop(0)


def _dao(*results: _Result) -> tuple[AgentDao, _Conn]:
    conn = _Conn(list(results))
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    dao = AgentDao(engine)
    dao.read_leaf_dispatch_facts = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(user_scope="user-1", owner_epoch=3, run_epoch=2)
    )
    return dao, conn


def _request(**overrides: Any) -> SaveToolDecisionRequest:
    values: dict[str, Any] = {
        "user_scope": "user-1",
        "session_id": "session-1",
        "execution_id": "execution-1",
        "round_id": "round-1",
        "node_id": "n1",
        "decision_id": "decision-1",
        "operation_id": "operation-1",
        "tool_call_id": "call-1",
        "tool_name": "search",
        "parameter_digest": _DIGEST,
        "schema_digest": _SCHEMA_DIGEST,
        "audit_scope_digest": _SCOPE_DIGEST,
        "question_payload": json.dumps({"question": "allow?"}),
        "expires_at": _EXPIRES_AT,
    }
    values.update(overrides)
    return SaveToolDecisionRequest(**values)


class TestSaveToolDecision:
    async def test_creates_decision_and_prepared_operation(self) -> None:
        dao, conn = _dao(_Result(first=None), _Result(scalar=0), _Result(), _Result())

        fact = await dao.save_tool_decision(_request())

        assert fact.grant_state is ToolGrantState.PENDING
        assert fact.grant_revision == 1
        assert fact.grant_id
        assert len(conn.statements) == 4
        decision_params = conn.statements[2].compile().params
        assert decision_params["decision_kind"] == "tool_permission"
        assert decision_params["grant_state"] == "pending"
        assert decision_params["grant_revision"] == 1
        assert decision_params["parameter_digest"] == _DIGEST
        # P1-10：本次人答 schema 与有效期同事务持久化（未答不可派发）。
        assert decision_params["answer_schema"]["required"] == ["approve"]
        assert decision_params["expires_at"] is not None
        operation_params = conn.statements[3].compile().params
        assert operation_params["phase"] == "prepared"
        assert operation_params["effect_state"] == "not_dispatched"
        assert operation_params["call_kind"] == "tool_call"

    async def test_missing_validity_window_is_rejected(self) -> None:
        dao, conn = _dao()

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_tool_decision(_request(expires_at=None))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.statements == []

    async def test_replay_returns_original_fact(self) -> None:
        existing = SimpleNamespace(
            execution_id="execution-1",
            round_id="round-1",
            tool_call_id="call-1",
            grant_id="grant-9",
            grant_revision=2,
            grant_state="claimed",
        )
        dao, conn = _dao(_Result(first=existing), _Result(first=SimpleNamespace(operation_id="operation-1")))

        fact = await dao.save_tool_decision(_request())

        assert fact.grant_id == "grant-9"
        assert fact.grant_revision == 2
        assert fact.grant_state is ToolGrantState.CLAIMED
        assert len(conn.statements) == 2

    async def test_replay_with_wrong_owner_is_conflict(self) -> None:
        existing = SimpleNamespace(
            execution_id="other-execution",
            round_id="round-1",
            tool_call_id="call-1",
            grant_id="grant-9",
            grant_revision=1,
            grant_state="granted",
        )
        dao, _ = _dao(_Result(first=existing))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_tool_decision(_request())

        assert excinfo.value.code is AgentErrorCode.INPUT_CONFLICT

    async def test_replay_without_operation_is_rejected(self) -> None:
        existing = SimpleNamespace(
            execution_id="execution-1",
            round_id="round-1",
            tool_call_id="call-1",
            grant_id="grant-9",
            grant_revision=1,
            grant_state="granted",
        )
        dao, _ = _dao(_Result(first=existing), _Result(first=None))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_tool_decision(_request())

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    async def test_invalid_digest_is_rejected(self) -> None:
        dao, _ = _dao()

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_tool_decision(_request(parameter_digest="short"))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID


class TestSaveToolResult:
    async def test_completed_maps_to_result_saved(self) -> None:
        dao, conn = _dao(_Result())

        await dao.save_tool_result(
            SaveToolResultRequest(
                user_scope="user-1",
                operation_id="operation-1",
                outcome_state="completed",
                result_payload=json.dumps({"answer": 1}),
            )
        )

        params = conn.statements[0].compile().params
        assert params["phase"] == "result_saved"
        assert params["effect_state"] == "confirmed"
        assert params["io_state"] == "completed"

    async def test_unknown_maps_to_unknown_and_keeps_effect_unknown(self) -> None:
        dao, conn = _dao(_Result())

        await dao.save_tool_result(
            SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="unknown")
        )

        params = conn.statements[0].compile().params
        assert params["phase"] == "unknown"
        assert params["effect_state"] == "unknown"

    async def test_missing_operation_is_rejected(self) -> None:
        dao, _ = _dao(_Result(rowcount=0))

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_tool_result(
                SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="failed")
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    async def test_invalid_outcome_state_is_rejected_before_write(self) -> None:
        dao, conn = _dao()

        with pytest.raises(AgentServiceError) as excinfo:
            await dao.save_tool_result(
                SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="maybe")
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.statements == []
