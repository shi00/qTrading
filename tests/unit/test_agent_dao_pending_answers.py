"""跨会话待处理聚合与答复命令读取单测（P1-05）。

``read_pending_answers`` 必须跨会话聚合记录/交付/消费三态，达到单类上限即如实
标注截断（R21）；``read_answer_command`` 只按原命令身份读，坏事实（非 answer、
未知枚举、载荷缺失）不得读成确定状态。真实 SQL 语义由 sidecar PG 集成测试覆盖；
本文件用 capture conn 断言分类、映射与校验分支。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentErrorCode, AgentServiceError, ConsumptionState, DeliveryState
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 11, 23, 1, 2, 3, tzinfo=UTC)


class _PendingConn:
    """按 SQL 目标返回聚合行/命令行，模拟三类聚合与命令读取。"""

    def __init__(
        self,
        *,
        answer_rows: tuple[Any, ...] = (),
        delivery_rows: tuple[Any, ...] = (),
        consumption_rows: tuple[Any, ...] = (),
        command_row: Any = None,
    ) -> None:
        self.answer_rows = answer_rows
        self.delivery_rows = delivery_rows
        self.consumption_rows = consumption_rows
        self.command_row = command_row
        self.sql: list[str] = []

    async def execute(self, statement: Any) -> Any:
        sql = str(statement)
        self.sql.append(sql)
        result = MagicMock()
        result.all.return_value = ()
        result.first.return_value = None
        if "answer_command_id" in sql and "agent_command" in sql:
            result.all.return_value = self.delivery_rows if "delivery_state !=" in sql else self.consumption_rows
        elif "FROM agent_decision" in sql:
            result.all.return_value = self.answer_rows
        elif "FROM agent_command" in sql:
            result.first.return_value = self.command_row
        return result


def _dao(conn: _PendingConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=cm)
    return AgentDao(engine)


def _row(
    decision_id: str = "d-1",
    *,
    session_id: str = "s1",
    created_at: datetime = _CREATED_AT,
    command_record_id: str | None = None,
    delivery_state: str | None = None,
    consumption_state: str | None = None,
) -> Any:
    return SimpleNamespace(
        session_id=session_id,
        session_title="研究 A",
        decision_id=decision_id,
        execution_id="e-1",
        round_id="r-1",
        decision_kind="user_choice",
        revision=2,
        created_at=created_at,
        expires_at=None,
        question_payload={"question": "选哪个候选？"},
        command_record_id=command_record_id,
        delivery_state=delivery_state,
        consumption_state=consumption_state,
    )


class TestReadPendingAnswers:
    @pytest.mark.asyncio
    async def test_classifies_stages_and_counts(self) -> None:
        conn = _PendingConn(
            answer_rows=(_row("d-1"),),
            delivery_rows=(_row("d-2", command_record_id="cmd-2", delivery_state="not_delivered"),),
            consumption_rows=(
                _row(
                    "d-3",
                    command_record_id="cmd-3",
                    delivery_state="delivered",
                    consumption_state="not_consumed",
                ),
            ),
        )

        page = await _dao(conn).read_pending_answers(user_scope="u1")

        assert page.answer_count == 1
        assert page.delivery_count == 1
        assert page.consumption_count == 1
        assert page.truncated is False
        assert page.read_moment.endswith("Z")
        by_stage = {item.stage: item for item in page.items}
        assert by_stage["answer"].command_record_id is None
        assert by_stage["delivery"].command_record_id == "cmd-2"
        assert by_stage["delivery"].delivery_state is DeliveryState.NOT_DELIVERED
        assert by_stage["consumption"].consumption_state is ConsumptionState.NOT_CONSUMED
        assert json.loads(by_stage["answer"].question_payload or "{}") == {"question": "选哪个候选？"}

    @pytest.mark.asyncio
    async def test_marks_truncation_when_any_stage_hits_limit(self) -> None:
        conn = _PendingConn(answer_rows=(_row("d-1"), _row("d-2")))

        page = await _dao(conn).read_pending_answers(user_scope="u1", limit=1)

        assert page.truncated is True
        assert page.answer_count == 1
        assert len(page.items) == 1

    @pytest.mark.asyncio
    async def test_orders_items_newest_first(self) -> None:
        newer = _row("d-2", created_at=datetime(2026, 11, 23, 2, 0, 0, tzinfo=UTC))
        conn = _PendingConn(answer_rows=(_row("d-1"), newer))

        page = await _dao(conn).read_pending_answers(user_scope="u1")

        assert [item.decision_id for item in page.items] == ["d-2", "d-1"]

    @pytest.mark.parametrize("limit", [0, -1, 501, True])
    @pytest.mark.asyncio
    async def test_rejects_out_of_range_limit(self, limit: Any) -> None:
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(_PendingConn()).read_pending_answers(user_scope="u1", limit=limit)
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_rejects_empty_scope(self) -> None:
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(_PendingConn()).read_pending_answers(user_scope="")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID


class TestReadAnswerCommand:
    @pytest.mark.asyncio
    async def test_maps_answer_command_fact(self) -> None:
        command_row = SimpleNamespace(
            command_record_id="cmd-1",
            client_command_id="ui-answer:d-1",
            target_id="d-1",
            session_id="s1",
            execution_id="e-1",
            round_id="r-1",
            command_kind="answer",
            delivery_state="not_delivered",
            consumption_state="not_consumed",
            input_digest="sha256:abc",
            request_payload={"text": "选择第一家"},
            verified_at=None,
        )

        fact = await _dao(_PendingConn(command_row=command_row)).read_answer_command(
            user_scope="u1", command_record_id="cmd-1"
        )

        assert fact.decision_id == "d-1"
        assert fact.client_command_id == "ui-answer:d-1"
        assert fact.delivery_state is DeliveryState.NOT_DELIVERED
        assert json.loads(fact.request_payload) == {"text": "选择第一家"}
        assert fact.verified_at is None

    @pytest.mark.asyncio
    async def test_rejects_missing_or_non_answer_command(self) -> None:
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(_PendingConn()).read_answer_command(user_scope="u1", command_record_id="cmd-1")
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

        non_answer = SimpleNamespace(
            command_record_id="cmd-1",
            client_command_id="c-1",
            target_id="t-1",
            session_id="s1",
            execution_id="e-1",
            round_id="r-1",
            command_kind="submit",
            delivery_state="not_delivered",
            consumption_state="not_consumed",
            input_digest="sha256:abc",
            request_payload={"text": "x"},
            verified_at=None,
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(_PendingConn(command_row=non_answer)).read_answer_command(
                user_scope="u1", command_record_id="cmd-1"
            )
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_unknown_enums_are_not_read_as_confirmed_state(self) -> None:
        command_row = SimpleNamespace(
            command_record_id="cmd-1",
            client_command_id="c-1",
            target_id="d-1",
            session_id="s1",
            execution_id="e-1",
            round_id="r-1",
            command_kind="answer",
            delivery_state="bogus",
            consumption_state="not_consumed",
            input_digest="sha256:abc",
            request_payload={"text": "x"},
            verified_at=None,
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(_PendingConn(command_row=command_row)).read_answer_command(
                user_scope="u1", command_record_id="cmd-1"
            )
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN

    @pytest.mark.asyncio
    async def test_missing_payload_is_not_read_as_answered(self) -> None:
        command_row = SimpleNamespace(
            command_record_id="cmd-1",
            client_command_id="c-1",
            target_id="d-1",
            session_id="s1",
            execution_id="e-1",
            round_id="r-1",
            command_kind="answer",
            delivery_state="not_delivered",
            consumption_state="not_consumed",
            input_digest=None,
            request_payload=None,
            verified_at=None,
        )
        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(_PendingConn(command_row=command_row)).read_answer_command(
                user_scope="u1", command_record_id="cmd-1"
            )
        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
