"""AgentService 费用只读投影单测（检视 R14：五态分列保真 + 只读 + 引用完整）。

投影只经账本既有读取方法取数（``read_request_attempts`` /
``read_active_reserved_cny``），零写入、零估算补齐：

- 0（已计价且确实为零）与已知金额、预留中、未计价、读取失败分列，不合并；
- ``known_cny is None``（未知）与 ``Decimal("0")``（已知零）必须可区分（R21）；
- 原账本 invocation 引用与 attempt 引用去重保序回传，缺行不丢引用；
- 读取失败只报失败态，金额不倒推扑零；``CancelledError`` 继续传播（R2）。
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Any, cast

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, CostProjection
from data.persistence.daos.agent_dao import AgentDao
from services.agent_service.service import AgentService

pytestmark = pytest.mark.unit


class _FakeUsageDao:
    """只实现投影用到的两个只读方法；无任何写方法（写入即 AttributeError）。"""

    def __init__(
        self,
        *,
        attempts: dict[str, list[dict[str, Any]]] | None = None,
        reserved: dict[str, Decimal] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._attempts = attempts or {}
        self._reserved = reserved or {}
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def read_request_attempts(self, *, invocation_id: str) -> list[dict[str, Any]]:
        self.calls.append(("read_request_attempts", {"invocation_id": invocation_id}))
        if self._error is not None:
            raise self._error
        return list(self._attempts.get(invocation_id, []))

    async def read_active_reserved_cny(self, *, invocation_ids: list[str] | tuple[str, ...]) -> dict[str, Decimal]:
        self.calls.append(("read_active_reserved_cny", {"invocation_ids": tuple(invocation_ids)}))
        if self._error is not None:
            raise self._error
        wanted = set(invocation_ids)
        return {key: value for key, value in self._reserved.items() if key in wanted}


def _service(usage: _FakeUsageDao | None) -> AgentService:
    return AgentService(cast(AgentDao, object()), cast(Any, usage))


def _attempt(attempt_id: str, *, cost: Any = None, input_tokens: int | None = None, output_tokens: int | None = None):
    return {
        "attempt_id": attempt_id,
        "cost_cny": cost,
        "input_total_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


class TestCostProjectionFiveStates:
    """五态分列：0 / 已知金额 / 预留 / 未计价 / 读取失败。"""

    @pytest.mark.asyncio
    async def test_states_are_reported_separately(self) -> None:
        usage = _FakeUsageDao(
            attempts={
                "inv-1": [
                    _attempt("att-zero", cost=Decimal("0")),
                    _attempt("att-known", cost=Decimal("0.50")),
                    _attempt("att-unpriced", cost=None, input_tokens=120, output_tokens=30),
                ]
            },
            reserved={"inv-1": Decimal("1.25")},
        )
        projection = await _service(usage).read_cost_projection(ledger_refs=["inv-1"])

        assert projection.read_state == "ok"
        assert projection.read_error is None
        assert projection.zero_cost_attempts == 1
        assert projection.known_cny == Decimal("0.50")
        assert projection.reserved_cny == Decimal("1.25")
        assert projection.unpriced_attempts == 1
        assert projection.unpriced_input_tokens == 120
        assert projection.unpriced_output_tokens == 30

    @pytest.mark.asyncio
    async def test_zero_known_amount_is_distinguishable_from_unknown(self) -> None:
        """已知零（Decimal("0")）与无已计价 attempt（None）不合并（R21）。"""

        zero_usage = _FakeUsageDao(attempts={"inv-zero": [_attempt("att-1", cost=Decimal("0"))]})
        zero = await _service(zero_usage).read_cost_projection(ledger_refs=["inv-zero"])
        assert zero.zero_cost_attempts == 1
        assert zero.known_cny == Decimal("0")

        unknown_usage = _FakeUsageDao(attempts={"inv-unknown": []})
        unknown = await _service(unknown_usage).read_cost_projection(ledger_refs=["inv-unknown"])
        assert unknown.zero_cost_attempts == 0
        assert unknown.known_cny is None
        assert unknown.unpriced_attempts == 0

    @pytest.mark.asyncio
    async def test_no_active_reservation_is_fact_zero(self) -> None:
        """账本可读但无活跃预留 → 事实零；无引用可投影 → None（不伪造）。"""

        usage = _FakeUsageDao(attempts={"inv-1": [_attempt("att-1", cost=Decimal("0.10"))]})
        projection = await _service(usage).read_cost_projection(ledger_refs=["inv-1"])
        assert projection.reserved_cny == Decimal("0")

        empty = await _service(_FakeUsageDao()).read_cost_projection(ledger_refs=[])
        assert empty.read_state == "ok"
        assert empty.reserved_cny is None
        assert empty.known_cny is None

    @pytest.mark.asyncio
    async def test_bad_amount_reads_as_unpriced_not_free(self) -> None:
        """坏/负/非有限金额读成未知成本，不得伪装为 0（R21）。"""

        usage = _FakeUsageDao(
            attempts={
                "inv-1": [
                    _attempt("att-bad", cost="not-a-number", input_tokens=7, output_tokens=3),
                    _attempt("att-neg", cost=Decimal("-1.00")),
                    _attempt("att-nan", cost=Decimal("NaN")),
                ]
            }
        )
        projection = await _service(usage).read_cost_projection(ledger_refs=["inv-1"])
        assert projection.known_cny is None
        assert projection.zero_cost_attempts == 0
        assert projection.unpriced_attempts == 3
        assert projection.unpriced_input_tokens == 7
        assert projection.unpriced_output_tokens == 3

    @pytest.mark.asyncio
    async def test_read_failure_reports_failed_without_zero_fill(self) -> None:
        usage = _FakeUsageDao(error=RuntimeError("ledger down"))
        projection = await _service(usage).read_cost_projection(ledger_refs=["inv-1"], attempt_refs=["att-1"])

        assert projection.read_state == "failed"
        assert isinstance(projection.read_error, str) and projection.read_error
        assert projection.known_cny is None
        assert projection.reserved_cny is None
        assert projection.zero_cost_attempts == 0
        assert projection.ledger_refs == ("inv-1",)
        assert projection.attempt_refs == ("att-1",)

    @pytest.mark.asyncio
    async def test_missing_ledger_dao_reports_failed(self) -> None:
        projection = await _service(None).read_cost_projection(ledger_refs=["inv-1"])
        assert projection.read_state == "failed"
        assert projection.read_error == "ledger_unavailable"
        assert projection.known_cny is None

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self) -> None:
        """R2：账本读取取消继续传播，不降级成「读取失败」投影。"""

        usage = _FakeUsageDao(error=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await _service(usage).read_cost_projection(ledger_refs=["inv-1"])
        assert isinstance(excinfo.value, asyncio.CancelledError)


class TestCostProjectionContract:
    """只读、引用完整与入参/关闭契约。"""

    @pytest.mark.asyncio
    async def test_projection_is_read_only(self) -> None:
        usage = _FakeUsageDao(
            attempts={"inv-1": [_attempt("att-1", cost=Decimal("0.10"))]},
            reserved={"inv-1": Decimal("0.20")},
        )
        service = _service(usage)
        await service.read_cost_projection(ledger_refs=["inv-1"], attempt_refs=["att-1"])

        # 假 DAO 不提供任何写方法（写入即 AttributeError）；此处额外确认只走读取入口。
        assert usage.calls == [
            ("read_request_attempts", {"invocation_id": "inv-1"}),
            ("read_active_reserved_cny", {"invocation_ids": ("inv-1",)}),
        ]

    @pytest.mark.asyncio
    async def test_refs_are_complete_and_ordered(self) -> None:
        """调用方引用与账本实读 attempt 合并去重保序；缺行引用不丢且计入未计价。"""

        usage = _FakeUsageDao(attempts={"inv-1": [_attempt("att-ledger", cost=Decimal("0.30"))]})
        projection = await _service(usage).read_cost_projection(
            ledger_refs=["inv-1", "inv-1", "inv-2"],
            attempt_refs=["att-declared", "att-ledger", "att-declared"],
        )

        assert projection.ledger_refs == ("inv-1", "inv-2")
        assert projection.attempt_refs == ("att-declared", "att-ledger")
        # att-declared 账本无行 = 成本未知（未计价），引用保留
        assert projection.unpriced_attempts == 1
        assert projection.known_cny == Decimal("0.30")

    @pytest.mark.asyncio
    async def test_invalid_refs_rejected(self) -> None:
        service = _service(_FakeUsageDao())
        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_cost_projection(ledger_refs=[""])
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_closed_service_rejects_projection(self) -> None:
        service = _service(_FakeUsageDao())
        await service.aclose()
        with pytest.raises(AgentServiceError) as excinfo:
            await service.read_cost_projection(ledger_refs=["inv-1"])
        assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    def test_projection_is_frozen(self) -> None:
        projection = CostProjection(
            read_state="ok",
            read_error=None,
            ledger_refs=(),
            attempt_refs=(),
            zero_cost_attempts=0,
            known_cny=None,
            reserved_cny=None,
            unpriced_attempts=0,
            unpriced_input_tokens=0,
            unpriced_output_tokens=0,
        )
        with pytest.raises(FrozenInstanceError) as excinfo:
            projection.known_cny = Decimal("1")  # type: ignore[misc]  # frozen dataclass 赋值拒绝
        assert "known_cny" in str(excinfo.value)
        assert projection.known_cny is None
