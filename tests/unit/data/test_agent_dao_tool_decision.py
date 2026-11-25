"""``save_tool_decision`` / ``save_tool_result`` 事务事实（SQ-03 / N39）。

- 决定 + operation 同事务落库，grant 初值 ``pending`` / revision 1（P1-10：本次
  人答消费前不得是可派发 ``granted``）；
- 有效期必填（``None`` 拒绝），不把「无限期」当默认；
- 同 ``decision_id`` 重放只读原事实；归属/操作缺失即拒绝；
- 结果三态映射 phase/effect/io，operation 不存在拒绝写入；
- 结果事实与 ``tool_result_saved`` 可靠事件同事务提交：提交后可从原水位按
  ``after_seq`` 续读到，载荷指向 operation 与结果引用；失败与同结果重放不增事件。
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
    EventKind,
    EventReadQuery,
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


_MISSING: Any = object()


class _EventResult:
    def __init__(self, *, first: Any = None, rows: list[Any] | None = None) -> None:
        self._first = first
        self._rows = [] if rows is None else list(rows)

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _EventResult:
        return self

    def scalar_one(self) -> Any:
        return self._rows[0] if self._rows else 0

    def scalar_one_or_none(self) -> Any:
        return self._first


def _operation_fact(**overrides: Any) -> Any:
    """UPDATE ... RETURNING 的操作行事实（工具操作必绑会话/执行/轮/节点）。"""

    values: dict[str, Any] = {
        "session_id": "session-1",
        "execution_id": "execution-1",
        "round_id": "round-1",
        "step_id": "n1",
        "tool_call_id": "call-1",
        "parameter_digest": _DIGEST,
        "revision": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _event_row(values: dict[str, Any]) -> Any:
    """insert 参数 → AgentMessage 行事实（供 read_events 严格解码）。"""

    return SimpleNamespace(
        user_scope=values["user_scope"],
        message_id=values["message_id"],
        session_id=values["session_id"],
        execution_id=values.get("execution_id"),
        node_id=values.get("node_id"),
        round_id=values.get("round_id"),
        event_id=values["event_id"],
        session_seq=values["session_seq"],
        execution_seq=values.get("execution_seq"),
        event_kind=values["event_kind"],
        payload_kind=values["payload_kind"],
        subject_id=values["subject_id"],
        subject_revision=values["subject_revision"],
        input_revision=values.get("input_revision"),
        occurred_at=values["occurred_at"],
        committed_at=values["committed_at"],
        native_fact_ref=values.get("native_fact_ref"),
        payload=values.get("payload"),
    )


class _ToolResultConn:
    """按表分派最小行事实：operation 更新 + 同事务事件写入/读取。"""

    def __init__(
        self,
        *,
        operation: Any = _MISSING,
        event_seq: int = 0,
        session_exists: bool = True,
    ) -> None:
        self.operation = _operation_fact() if operation is _MISSING else operation
        self.event_seq = event_seq
        self.session_exists = session_exists
        self.operation_values: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []
        self.statements: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> _EventResult:
        sql = str(statement)
        self.statements.append(statement)
        if "UPDATE agent_operation" in sql:
            self.operation_values = dict(statement.compile().params)
            return _EventResult(first=self.operation)
        if "INSERT INTO agent_message" in sql:
            values = dict(statement.compile().params)
            self.messages.append(values)
            self.event_seq = max(self.event_seq, int(values["session_seq"]))
            return _EventResult()
        if "FROM agent_message" in sql:
            if "session_seq > " in sql:
                bound = dict(statement.compile().params)
                after_seq = int(next(v for k, v in bound.items() if "session_seq" in k))
                rows = [_event_row(values) for values in self.messages if int(values["session_seq"]) > after_seq]
                return _EventResult(rows=sorted(rows, key=lambda row: row.session_seq))
            # 事件身份查重（_append_session_event）：同 (session, event_id) 已存在。
            bound = dict(statement.compile().params)
            wanted = next((v for k, v in bound.items() if "event_id" in k), None)
            hit = next((item for item in self.messages if item["event_id"] == wanted), None)
            return _EventResult(first=None if hit is None else SimpleNamespace(message_id=hit["message_id"]))
        if "FROM agent_session" in sql:
            if not self.session_exists:
                return _EventResult(first=None)
            return _EventResult(first=SimpleNamespace(event_seq=self.event_seq))
        if "UPDATE agent_session" in sql:
            new_seq = dict(statement.compile().params).get("event_seq")
            if isinstance(new_seq, int):
                self.event_seq = max(self.event_seq, new_seq)
            return _EventResult()
        return _EventResult()


def _event_dao(conn: _ToolResultConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm_begin = MagicMock()
    cm_begin.__aenter__ = AsyncMock(return_value=conn)
    cm_begin.__aexit__ = AsyncMock(return_value=False)
    cm_connect = MagicMock()
    cm_connect.__aenter__ = AsyncMock(return_value=conn)
    cm_connect.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm_begin)
    engine.connect = MagicMock(return_value=cm_connect)
    return AgentDao(engine)


class TestSaveToolResult:
    async def test_completed_maps_to_result_saved(self) -> None:
        conn = _ToolResultConn()

        await _event_dao(conn).save_tool_result(
            SaveToolResultRequest(
                user_scope="user-1",
                operation_id="operation-1",
                outcome_state="completed",
                result_payload=json.dumps({"answer": 1}),
            )
        )

        params = conn.operation_values
        assert params["phase"] == "result_saved"
        assert params["effect_state"] == "confirmed"
        assert params["io_state"] == "completed"

    async def test_committed_result_leaves_resumable_event(self) -> None:
        conn = _ToolResultConn()
        dao = _event_dao(conn)

        await dao.save_tool_result(
            SaveToolResultRequest(
                user_scope="user-1",
                operation_id="operation-1",
                outcome_state="completed",
                result_payload=json.dumps({"answer": 1}),
                result_ref="artifact://result/1",
            )
        )

        assert len(conn.messages) == 1
        values = conn.messages[0]
        assert values["event_kind"] == EventKind.TOOL_RESULT_SAVED.value
        assert values["payload_kind"] == "tool_result_saved"
        assert values["session_seq"] == 1
        assert values["subject_id"] == "operation-1"
        assert values["subject_revision"] == 2
        payload = values["payload"]
        assert payload["schema_version"] == 1
        assert payload["operation_id"] == "operation-1"
        assert payload["tool_call_id"] == "call-1"
        assert payload["execution_id"] == "execution-1"
        assert payload["round_id"] == "round-1"
        assert payload["node_id"] == "n1"
        assert payload["parameter_digest"] == _DIGEST
        assert payload["outcome_state"] == "completed"
        assert payload["result_ref"] == "artifact://result/1"
        # 结果事实先于事件写入（同事务内事件不早于 operation 更新）。
        update_index = conn.statements.index(next(s for s in conn.statements if "UPDATE agent_operation" in str(s)))
        insert_index = conn.statements.index(next(s for s in conn.statements if "INSERT INTO agent_message" in str(s)))
        assert update_index < insert_index

        # 增量订阅从原水位续读：after_seq=0 取到该事件，追平后不再返回。
        page = await dao.read_events(EventReadQuery(user_scope="user-1", session_id="session-1", after_seq=0))
        assert [event.event_kind for event in page.events] == [EventKind.TOOL_RESULT_SAVED]
        assert page.events[0].session_seq == 1
        assert page.last_seq == 1
        resumed = await dao.read_events(EventReadQuery(user_scope="user-1", session_id="session-1", after_seq=1))
        assert resumed.events == ()

    async def test_replay_does_not_append_second_event(self) -> None:
        conn = _ToolResultConn()
        dao = _event_dao(conn)
        request = SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="completed")

        await dao.save_tool_result(request)
        await dao.save_tool_result(request)

        assert len(conn.messages) == 1
        assert conn.event_seq == 1

    async def test_unknown_maps_to_unknown_and_keeps_effect_unknown(self) -> None:
        conn = _ToolResultConn()

        await _event_dao(conn).save_tool_result(
            SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="unknown")
        )

        params = conn.operation_values
        assert params["phase"] == "unknown"
        assert params["effect_state"] == "unknown"
        # R21：未知态事件显式标 unknown，不填业务上合法的成功值/占位引用。
        payload = conn.messages[0]["payload"]
        assert payload["outcome_state"] == "unknown"
        assert payload["phase"] == "unknown"
        assert payload["result_ref"] is None

    async def test_session_watermark_advances_from_existing_seq(self) -> None:
        conn = _ToolResultConn(event_seq=4)

        await _event_dao(conn).save_tool_result(
            SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="completed")
        )

        assert conn.messages[0]["session_seq"] == 5
        assert conn.event_seq == 5

    async def test_missing_operation_is_rejected_without_event(self) -> None:
        conn = _ToolResultConn(operation=None)

        with pytest.raises(AgentServiceError) as excinfo:
            await _event_dao(conn).save_tool_result(
                SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="failed")
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.messages == []

    async def test_operation_without_session_is_rejected_without_event(self) -> None:
        conn = _ToolResultConn(operation=_operation_fact(session_id=None))

        with pytest.raises(AgentServiceError) as excinfo:
            await _event_dao(conn).save_tool_result(
                SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="completed")
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.messages == []

    async def test_missing_session_is_rejected_without_event(self) -> None:
        conn = _ToolResultConn(session_exists=False)

        with pytest.raises(AgentServiceError) as excinfo:
            await _event_dao(conn).save_tool_result(
                SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="completed")
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.messages == []

    async def test_invalid_outcome_state_is_rejected_before_write(self) -> None:
        conn = _ToolResultConn()

        with pytest.raises(AgentServiceError) as excinfo:
            await _event_dao(conn).save_tool_result(
                SaveToolResultRequest(user_scope="user-1", operation_id="operation-1", outcome_state="maybe")
            )

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert conn.statements == []
