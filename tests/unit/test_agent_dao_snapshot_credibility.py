"""执行快照可信度/费用投影纯函数契约（R2-I30，R21）。

快照不得把「未核验/未就绪/费用未知」显示为可靠或无限制。费用状态由原账本
（``ai_attempt.cost_cny`` + ``ai_invocation.reservations``）如实聚合，本文件
覆盖引用来解与状态判定；真实 PG 上的端到端语义见
``tests/integration/test_agent_dao_execution_snapshot.py``。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.agent_types import (
    COST_STATE_KNOWN,
    COST_STATE_RESERVED,
    COST_STATE_UNKNOWN,
    COST_STATE_UNPRICED,
)
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


class TestDecodeAttemptRefs:
    def test_none_is_empty(self) -> None:
        assert AgentDao._decode_attempt_refs(None) == ()

    def test_string_list_dedupes_preserving_order(self) -> None:
        assert AgentDao._decode_attempt_refs(["a2", "a1", "a2"]) == ("a2", "a1")

    def test_json_string_list(self) -> None:
        assert AgentDao._decode_attempt_refs('["a1", "a2"]') == ("a1", "a2")

    def test_mapping_identity_keys(self) -> None:
        assert AgentDao._decode_attempt_refs({"attempt": ["a1", "a2"]}) == ("a1", "a2")
        assert AgentDao._decode_attempt_refs({"attempt": "a1"}) == ("a1",)
        assert AgentDao._decode_attempt_refs({"other": ["a1"]}) == ()

    @pytest.mark.parametrize("raw", ["not-json", {"attempt": [1]}, [""], 42, [1]])
    def test_unreadable_shapes_return_none(self, raw: object) -> None:
        assert AgentDao._decode_attempt_refs(raw) is None


class TestAttemptCostIsPriced:
    def test_priced_values(self) -> None:
        assert AgentDao._attempt_cost_is_priced(Decimal("0")) is True
        assert AgentDao._attempt_cost_is_priced(Decimal("1.25")) is True
        assert AgentDao._attempt_cost_is_priced("2.5") is True

    @pytest.mark.parametrize("value", [None, True, False, "-1", "NaN", "Infinity", "bad", ""])
    def test_unpriced_values(self, value: object) -> None:
        assert AgentDao._attempt_cost_is_priced(value) is False


class TestDeriveSnapshotCostState:
    def test_no_refs_and_zero_attempts_is_known_zero(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=(),
                declared_attempts=(),
                attempt_costs=(),
                observed_attempt_ids=(),
                has_active_reservation=False,
                model_attempt_count=0,
            )
            == COST_STATE_KNOWN
        )

    def test_no_refs_with_attempts_is_unknown(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=(),
                declared_attempts=(),
                attempt_costs=(),
                observed_attempt_ids=(),
                has_active_reservation=False,
                model_attempt_count=2,
            )
            == COST_STATE_UNKNOWN
        )

    def test_all_attempts_priced_is_known(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=("a1", "a2"),
                attempt_costs=(Decimal("0"), Decimal("1.5")),
                observed_attempt_ids=("a1", "a2"),
                has_active_reservation=False,
                model_attempt_count=2,
            )
            == COST_STATE_KNOWN
        )

    def test_declared_attempt_without_ledger_row_is_unpriced(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=("a1", "a2"),
                attempt_costs=(Decimal("1.5"),),
                observed_attempt_ids=("a1",),
                has_active_reservation=False,
                model_attempt_count=2,
            )
            == COST_STATE_UNPRICED
        )

    def test_null_cost_is_unpriced(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=("a1",),
                attempt_costs=(None,),
                observed_attempt_ids=("a1",),
                has_active_reservation=False,
                model_attempt_count=1,
            )
            == COST_STATE_UNPRICED
        )

    def test_active_reservation_without_attempts_is_reserved(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=(),
                attempt_costs=(),
                observed_attempt_ids=(),
                has_active_reservation=True,
                model_attempt_count=1,
            )
            == COST_STATE_RESERVED
        )

    def test_active_reservation_with_priced_attempts_is_reserved(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=("a1",),
                attempt_costs=(Decimal("0.5"),),
                observed_attempt_ids=("a1",),
                has_active_reservation=True,
                model_attempt_count=1,
            )
            == COST_STATE_RESERVED
        )

    def test_refs_without_ledger_rows_is_unknown(self) -> None:
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=(),
                attempt_costs=(),
                observed_attempt_ids=(),
                has_active_reservation=False,
                model_attempt_count=1,
            )
            == COST_STATE_UNKNOWN
        )

    def test_partial_attempts_never_mark_known(self) -> None:
        """仅查到部分调用（1/2）时已计价子集不能证明整体费用已知（R21）。"""
        assert (
            AgentDao._derive_snapshot_cost_state(
                ledger_refs=("inv-1",),
                declared_attempts=("a1",),
                attempt_costs=(Decimal("1.0"),),
                observed_attempt_ids=("a1",),
                has_active_reservation=False,
                model_attempt_count=2,
            )
            == COST_STATE_UNKNOWN
        )


class _StubResultRows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _StubConn:
    """按预设结果序列返回的只读连接桩（无真实 SQL 语义）。"""

    def __init__(self, results: list) -> None:
        self._results = list(results)

    async def execute(self, statement: object, *_args: object, **_kwargs: object) -> _StubResultRows:
        return _StubResultRows(self._results.pop(0))


class TestSnapshotCostStateCoverage:
    """快照费用限制须报出覆盖缺口，不得以部分 attempt 冒充已知（R21）。"""

    @pytest.mark.asyncio
    async def test_partial_attempts_report_coverage_gap(self) -> None:
        dao = AgentDao(None)
        conn = _StubConn(
            [
                [SimpleNamespace(invocation_id="inv-1", attempt_refs=["a1"])],
                [SimpleNamespace(attempt_id="a1", invocation_id="inv-1", cost_cny=Decimal("1.0"))],
                [],
            ]
        )
        state, limitations = await dao._snapshot_cost_state(
            conn, user_scope="user-1", execution_id="exec-1", model_attempt_count=2
        )
        assert state == COST_STATE_UNKNOWN
        assert [limitation.code for limitation in limitations] == ["cost_unknown"]
        assert "仅覆盖 1/2" in limitations[0].message

    @pytest.mark.asyncio
    async def test_full_coverage_unknown_keeps_reference_message(self) -> None:
        dao = AgentDao(None)
        conn = _StubConn(
            [
                [SimpleNamespace(invocation_id="inv-1", attempt_refs=None)],
                [],
                [],
            ]
        )
        state, limitations = await dao._snapshot_cost_state(
            conn, user_scope="user-1", execution_id="exec-1", model_attempt_count=0
        )
        assert state == COST_STATE_UNKNOWN
        assert "原账本无可计价引用" in limitations[0].message


class TestSnapshotCostStateReservationLease:
    """N25：过期普通预留不计活跃占用；未过期仍计；held_unconfirmed 明示保留。"""

    @staticmethod
    def _conn(reservation_state: str, lease: datetime) -> _StubConn:
        return _StubConn(
            [
                [SimpleNamespace(invocation_id="inv-1", attempt_refs=["a1"])],
                [SimpleNamespace(attempt_id="a1", invocation_id="inv-1", cost_cny=Decimal("1.0"))],
                [
                    SimpleNamespace(
                        reservations=[{"state": reservation_state, "amount_cny": "0.5"}],
                        owner_lease_expires_at=lease,
                    )
                ],
            ]
        )

    @pytest.mark.asyncio
    async def test_expired_ordinary_reservation_is_not_active(self) -> None:
        dao = AgentDao(None)
        conn = self._conn("reserved", datetime(2020, 1, 1))
        state, _ = await dao._snapshot_cost_state(
            conn, user_scope="user-1", execution_id="exec-1", model_attempt_count=1
        )
        assert state == COST_STATE_KNOWN

    @pytest.mark.asyncio
    async def test_unexpired_ordinary_reservation_is_active(self) -> None:
        dao = AgentDao(None)
        conn = self._conn("reserved", datetime(2099, 1, 1))
        state, _ = await dao._snapshot_cost_state(
            conn, user_scope="user-1", execution_id="exec-1", model_attempt_count=1
        )
        assert state == COST_STATE_RESERVED

    @pytest.mark.asyncio
    async def test_held_unconfirmed_survives_expired_lease(self) -> None:
        dao = AgentDao(None)
        conn = self._conn("held_unconfirmed", datetime(2020, 1, 1))
        state, _ = await dao._snapshot_cost_state(
            conn, user_scope="user-1", execution_id="exec-1", model_attempt_count=1
        )
        assert state == COST_STATE_RESERVED
