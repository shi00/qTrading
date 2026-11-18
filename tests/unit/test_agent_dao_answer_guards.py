"""答复首生效守卫契约测试（检视 D06）。

``save_answer`` / ``consume_answer`` 首次生效必须在同一事务内新鲜核原问题：
答复 schema/选项集、过期、当前轮、终止态；不满足即零新答复/零新消费。
重放路径只读原事实回执，不按当前状态重判。

真实行锁与 PG 约束行为由 sidecar PG 集成测试
（``tests/integration/test_agent_dao_command_transactions.py::TestAnswerLifecycle``）
覆盖；本文件用 capture conn 断言校验分支与「拒绝先于写事实」。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ConsumeAnswerRequest,
    ConsumptionState,
    DeliveryState,
    SaveAnswerRequest,
    json_doc,
    sha256_hex,
    stable_id,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


class _AnswerGuardConn:
    """按 SQL 目标表返回原问题事实，并记录读取/写入序列。"""

    def __init__(
        self,
        *,
        decision: Any,
        execution: Any = None,
        round_row: Any = None,
        existing_command: Any = None,
        original_command: Any = None,
        command_fact: Any = None,
    ) -> None:
        self.decision = decision
        self.execution = (
            execution
            if execution is not None
            else SimpleNamespace(current_round_id="round-1", display_state="queued", owner_epoch=1)
        )
        self.round_row = (
            round_row if round_row is not None else SimpleNamespace(active_round=True, stop_reason=None, run_epoch=1)
        )
        self.existing_command = existing_command
        self.original_command = original_command
        self.command_fact = command_fact
        self.reads: list[str] = []
        self.writes: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        result = MagicMock()
        result.first.return_value = None
        result.rowcount = 1
        if sql.lstrip().upper().startswith("INSERT"):
            self.writes.append("insert")
        elif sql.lstrip().upper().startswith("UPDATE"):
            self.writes.append("update")
        elif "FROM agent_session" in sql:
            self.reads.append("agent_session")
            result.first.return_value = SimpleNamespace(event_seq=0)
        elif "FROM agent_decision" in sql:
            self.reads.append("agent_decision")
            result.first.return_value = self.decision
        elif "FROM agent_command" in sql:
            if "client_command_id" in sql:
                self.reads.append("agent_command_key")
                result.first.return_value = self.existing_command
            else:
                self.reads.append("agent_command_fact")
                result.first.return_value = self.command_fact or self.original_command
        elif "FROM agent_execution" in sql:
            self.reads.append("agent_execution")
            result.first.return_value = self.execution
        elif "FROM agent_round" in sql:
            self.reads.append("agent_round")
            result.first.return_value = self.round_row
        return result


def _dao(conn: _AnswerGuardConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _decision(
    *,
    expires_at: datetime | None = None,
    answer_schema: Any = None,
    answer_command_id: str | None = None,
    answer_consumed_at: datetime | None = None,
    round_id: str = "round-1",
) -> Any:
    return SimpleNamespace(
        decision_id="d-1",
        session_id="s1",
        execution_id="exec-1",
        round_id=round_id,
        revision=1,
        answer_command_id=answer_command_id,
        answer_consumed_at=answer_consumed_at,
        expires_at=expires_at,
        answer_schema=answer_schema,
    )


def _save_request(payload: object | None = None, **overrides: Any) -> SaveAnswerRequest:
    values: dict[str, Any] = {
        "user_scope": "u1",
        "decision_id": "d-1",
        "client_command_id": "cmd-1",
        "expected_decision_revision": 1,
        "answer_payload": json_doc({"schema_version": 1, "approved": True} if payload is None else payload),
    }
    values.update(overrides)
    return SaveAnswerRequest(**values)


def _consume_request(command_record_id: str = "command-1") -> ConsumeAnswerRequest:
    return ConsumeAnswerRequest(user_scope="u1", command_record_id=command_record_id)


def _assert_rejected(excinfo: pytest.ExceptionInfo[AgentServiceError], code: AgentErrorCode) -> None:
    assert excinfo.value.code is code


class TestSaveAnswerFirstEffectGuards:
    """D06：save_answer 首生效新鲜核原问题，拒绝先于写事实。"""

    async def test_expired_question_rejected_without_new_fact(self) -> None:
        conn = _AnswerGuardConn(decision=_decision(expires_at=datetime.now(UTC) - timedelta(seconds=1)))

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_terminated_execution_rejected_without_new_fact(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(),
            execution=SimpleNamespace(current_round_id="round-1", display_state="ended"),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_superseded_round_rejected_without_new_fact(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(),
            execution=SimpleNamespace(current_round_id="round-2", display_state="queued"),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_stopped_round_rejected_without_new_fact(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(),
            round_row=SimpleNamespace(active_round=False, stop_reason="user_termination"),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_illegal_option_rejected_by_original_schema(self) -> None:
        schema = {
            "type": "object",
            "required": ["option"],
            "properties": {"option": {"enum": ["a", "b"]}},
        }
        conn = _AnswerGuardConn(decision=_decision(answer_schema=schema))

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request({"option": "c"}))

        _assert_rejected(excinfo, AgentErrorCode.INPUT_INVALID)
        assert conn.writes == []

    async def test_schema_version_mismatch_rejected(self) -> None:
        schema = {
            "type": "object",
            "required": ["schema_version"],
            "properties": {"schema_version": {"const": 2}},
        }
        conn = _AnswerGuardConn(decision=_decision(answer_schema=schema))

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request({"schema_version": 1}))

        _assert_rejected(excinfo, AgentErrorCode.INPUT_INVALID)
        assert conn.writes == []

    async def test_uninterpretable_schema_fails_closed(self) -> None:
        conn = _AnswerGuardConn(decision=_decision(answer_schema="tool_answer_v1"))

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_answer_payload_must_be_json_object(self) -> None:
        conn = _AnswerGuardConn(decision=_decision())

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).save_answer(_save_request([1, 2]))

        _assert_rejected(excinfo, AgentErrorCode.INPUT_INVALID)
        assert conn.writes == []

    async def test_current_question_with_schema_option_saves(self) -> None:
        schema = {
            "type": "object",
            "required": ["option"],
            "properties": {"option": {"enum": ["a", "b"]}},
        }
        conn = _AnswerGuardConn(decision=_decision(answer_schema=schema))

        receipt = await _dao(conn).save_answer(_save_request({"option": "a"}))

        assert receipt.decision_id == "d-1"
        assert receipt.delivery_state is DeliveryState.NOT_DELIVERED
        assert receipt.consumption_state is ConsumptionState.NOT_CONSUMED
        # 命令 + 决定 CAS，随后同事务可靠事件（消息行 + 会话水位）。
        assert conn.writes == ["insert", "update", "insert", "update"]

    async def test_replay_reads_original_fact_without_revalidation(self) -> None:
        """重放（决定已指向同命令）即便问题现已过期，也返回原回执且零校验读。"""

        request = _save_request()
        command_record_id = stable_id("command", [request.user_scope, request.decision_id, request.client_command_id])
        conn = _AnswerGuardConn(
            decision=_decision(
                expires_at=datetime.now(UTC) - timedelta(days=1),
                answer_command_id=command_record_id,
            ),
            existing_command=SimpleNamespace(input_digest=sha256_hex(request.answer_payload)),
            original_command=SimpleNamespace(
                delivery_state=DeliveryState.NOT_DELIVERED.value,
                consumption_state=ConsumptionState.NOT_CONSUMED.value,
                verified_at=None,
            ),
        )

        replay = await _dao(conn).save_answer(request)

        assert replay.command_record_id == command_record_id
        assert replay.decision_revision == 1
        assert "agent_execution" not in conn.reads
        assert "agent_round" not in conn.reads
        assert conn.writes == []


class TestConsumeAnswerFirstEffectGuards:
    """D06：consume_answer 首生效核原问题与已存答复；重放读原事实。"""

    async def test_expired_question_zero_new_consumption(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            command_fact=SimpleNamespace(
                consumption_state=ConsumptionState.NOT_CONSUMED.value,
                consumed_at=None,
                request_payload={"schema_version": 1, "approved": True},
            ),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).consume_answer(_consume_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_terminated_execution_zero_new_consumption(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(),
            execution=SimpleNamespace(current_round_id="round-1", display_state="withdrawn"),
            command_fact=SimpleNamespace(
                consumption_state=ConsumptionState.NOT_CONSUMED.value,
                consumed_at=None,
                request_payload={"schema_version": 1, "approved": True},
            ),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).consume_answer(_consume_request())

        _assert_rejected(excinfo, AgentErrorCode.POLICY_DENIED)
        assert conn.writes == []

    async def test_stored_answer_violating_schema_zero_new_consumption(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(
                answer_schema={
                    "type": "object",
                    "required": ["option"],
                    "properties": {"option": {"enum": ["a", "b"]}},
                }
            ),
            command_fact=SimpleNamespace(
                consumption_state=ConsumptionState.NOT_CONSUMED.value,
                consumed_at=None,
                request_payload={"option": "c"},
            ),
        )

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).consume_answer(_consume_request())

        _assert_rejected(excinfo, AgentErrorCode.INPUT_INVALID)
        assert conn.writes == []

    async def test_valid_consumption_writes_command_then_decision(self) -> None:
        conn = _AnswerGuardConn(
            decision=_decision(),
            command_fact=SimpleNamespace(
                consumption_state=ConsumptionState.NOT_CONSUMED.value,
                consumed_at=None,
                request_payload={"schema_version": 1, "approved": True},
            ),
        )

        fact = await _dao(conn).consume_answer(_consume_request())

        assert fact.decision_id == "d-1"
        assert fact.consumption_state is ConsumptionState.CONSUMED
        # 命令消费 + 决定 CAS，随后同事务可靠事件（消息行 + 会话水位）。
        assert conn.writes == ["update", "update", "insert", "update"]

    async def test_already_consumed_replay_skips_revalidation(self) -> None:
        consumed_at = datetime.now(UTC)
        conn = _AnswerGuardConn(
            decision=_decision(
                expires_at=datetime.now(UTC) - timedelta(days=1),
                answer_consumed_at=consumed_at,
            ),
            execution=SimpleNamespace(current_round_id="round-1", display_state="ended"),
            command_fact=SimpleNamespace(
                consumption_state=ConsumptionState.CONSUMED.value,
                consumed_at=consumed_at,
                request_payload=None,
            ),
        )

        replay = await _dao(conn).consume_answer(_consume_request())

        assert replay.consumption_state is ConsumptionState.CONSUMED
        assert "agent_execution" not in conn.reads
        assert conn.writes == []
