"""``read_authoritative_data_consent`` 权威命令绑定契约（N35）。

同 execution/round/node 可能有多个资料许可决定，且每个新决定独立起版（revision
只在本决定内单调）。因此跨决定选权**不得**按行内 revision 排序，必须按各决定
自己最近一次已提交 ``data_consent_save`` 权威命令绑定：

- 同轮合法重批：新批准的 save 命令更晚 → 选中新决定（即使旧决定 revision 更高）；
- 旧决定保持历史（本方法只读，不改写旧行）；
- 同 revision 的两个决定不再被判未知，仍按权威命令时刻区分；
- 同一权威命令时刻且载荷不同 → ``OPERATION_UNKNOWN``（fail-closed，R21）；
- 无已提交 save 命令的决定不参与（无权威绑定即不授权）→ ``None``。

真实查询（join/order）由 sidecar PG 集成测试
``tests/integration/test_agent_dao_data_consent.py::TestReadAuthoritativeDataConsent``
覆盖；本文件用 capture conn 断言选权语义与零写。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import AgentErrorCode, AgentServiceError, decode_data_consent_binding
from data.persistence.daos.agent_dao import AgentDao, DataConsentAuthority
from tests.unit.test_agent_data_consent import _binding_payload

pytestmark = pytest.mark.unit

_EXECUTION_ID = "exec-1"
_ROUND_ID = "round-1"
_NODE_ID = "node-1"

_FACTS = SimpleNamespace(
    user_scope="synthetic-user",
    owner_epoch=1,
    run_epoch=1,
    session_id="session-1",
    input_revision=1,
)


class _AuthorityConn:
    """按 SQL 目标表返回叶派发事实 / 决定行，并记录执行过的语句（断言零写）。"""

    def __init__(self, rows: list[Any], *, facts: Any = _FACTS) -> None:
        self.rows = rows
        self.facts = facts
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        result = MagicMock()
        result.first.return_value = None
        result.all.return_value = []
        if "FROM agent_execution" in sql:
            result.first.return_value = self.facts
        elif "FROM agent_decision" in sql:
            result.all.return_value = list(self.rows)
        return result


def _dao(conn: _AuthorityConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=cm)
    return AgentDao(engine)


def _row(
    *,
    decision_id: str,
    revision: int,
    command_at: datetime,
    command_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    wire = (
        payload if payload is not None else _binding_payload({"consent_id": decision_id, "consent_revision": revision})
    )
    return SimpleNamespace(
        decision_id=decision_id,
        revision=revision,
        data_consent_payload=wire,
        authority_command_at=command_at,
        authority_command_id=command_id or f"cmd-{decision_id}",
    )


async def _read(rows: list[Any]) -> Any:
    conn = _AuthorityConn(rows)
    authority = await _dao(conn).read_authoritative_data_consent(
        execution_id=_EXECUTION_ID,
        round_id=_ROUND_ID,
        node_id=_NODE_ID,
    )
    assert not [sql for sql in conn.statements if sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
    return authority


class TestReadAuthoritativeDataConsent:
    async def test_reapproval_binds_newer_command_despite_old_higher_revision(self) -> None:
        """旧决定（等待中过期，revision 已推进到 3）不得因行内 revision 更高而压过新批准。"""

        authority = await _read(
            [
                _row(
                    decision_id="dec-new",
                    revision=1,
                    command_at=datetime(2026, 6, 1, 12, 0, 1, tzinfo=UTC),
                    command_id="cmd-new",
                ),
                _row(
                    decision_id="dec-old",
                    revision=3,
                    command_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
                    command_id="cmd-old",
                ),
            ]
        )

        assert authority is not None
        assert authority.consent_id == "dec-new"
        assert authority.revision == 1
        assert authority.binding.consent_id == "dec-new"

    async def test_same_revision_two_decisions_are_not_ambiguous(self) -> None:
        """两个决定同 revision 但权威命令时刻不同 → 取更晚的那次明确批准。"""

        authority = await _read(
            [
                _row(decision_id="dec-b", revision=1, command_at=datetime(2026, 6, 1, 12, 0, 2, tzinfo=UTC)),
                _row(decision_id="dec-a", revision=1, command_at=datetime(2026, 6, 1, 12, 0, 1, tzinfo=UTC)),
            ]
        )

        assert authority == DataConsentAuthority(
            consent_id="dec-b",
            revision=1,
            binding=decode_data_consent_binding(_binding_payload({"consent_id": "dec-b", "consent_revision": 1}), "$"),
        )

    async def test_latest_command_per_decision_wins_over_earlier_command(self) -> None:
        """同决定多行 save 命令：只按该决定最近一次命令参与排序。"""

        authority = await _read(
            [
                _row(
                    decision_id="dec-a",
                    revision=2,
                    command_at=datetime(2026, 6, 1, 12, 0, 5, tzinfo=UTC),
                    command_id="cmd-a2",
                    payload=_binding_payload({"consent_id": "dec-a", "consent_revision": 2}),
                ),
                _row(
                    decision_id="dec-a",
                    revision=1,
                    command_at=datetime(2026, 6, 1, 12, 0, 1, tzinfo=UTC),
                    command_id="cmd-a1",
                ),
                _row(decision_id="dec-b", revision=1, command_at=datetime(2026, 6, 1, 12, 0, 3, tzinfo=UTC)),
            ]
        )

        assert authority is not None
        assert authority.consent_id == "dec-a"
        assert authority.revision == 2

    async def test_same_command_time_with_different_payload_fails_closed(self) -> None:
        tie_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

        with pytest.raises(AgentServiceError) as excinfo:
            await _read(
                [
                    _row(decision_id="dec-a", revision=1, command_at=tie_at),
                    _row(decision_id="dec-b", revision=1, command_at=tie_at),
                ]
            )

        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN

    async def test_same_command_time_with_identical_payload_is_deterministic(self) -> None:
        tie_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        payload = _binding_payload({"consent_id": "dec-a", "consent_revision": 1})

        authority = await _read(
            [
                _row(decision_id="dec-b", revision=1, command_at=tie_at, payload=payload),
                _row(decision_id="dec-a", revision=1, command_at=tie_at, payload=payload),
            ]
        )

        assert authority == DataConsentAuthority(
            consent_id="dec-a",
            revision=1,
            binding=decode_data_consent_binding(payload, "$"),
        )

    async def test_no_authoritative_binding_returns_none(self) -> None:
        """决定行无已提交 save 命令时不参与选择（join 为空）→ 无许可。"""

        assert await _read([]) is None

    async def test_unreadable_payload_fails_closed(self) -> None:
        authority_row = _row(decision_id="dec-a", revision=1, command_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC))
        authority_row.data_consent_payload = None

        with pytest.raises(AgentServiceError) as excinfo:
            await _read([authority_row])

        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
