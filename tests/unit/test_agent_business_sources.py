"""E-01-5 回测严格读四分支：成功 / 不存在 / DB 失败 / disposed / 取消。"""

import asyncio
from datetime import UTC, datetime

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, ReadState, SourceKind, VerificationState
from data.persistence.daos.base_dao import DatabaseQueryError, EngineDisposedError
from services.agent_service.business_sources import BusinessSnapshotRead, read_backtest_snapshot


class FakeReader:
    def __init__(self, *, row: dict | None = None, error: BaseException | None = None) -> None:
        self.row = row
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def get_result(self, run_id: str, *, suppress_errors: bool = True) -> dict | None:
        self.calls.append((run_id, suppress_errors))
        if self.error is not None:
            raise self.error
        return self.row


def _row() -> dict:
    return {
        "run_id": "run-1",
        "strategy_name": "northbound",
        "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "total_return": 0.123,
        "sharpe_ratio": float("nan"),
    }


class TestBacktestSnapshotRead:
    @pytest.mark.asyncio
    async def test_success_constructs_program_verified_source(self) -> None:
        reader = FakeReader(row=_row())
        result = await read_backtest_snapshot(reader, user_scope="u1", result_id="run-1")
        assert reader.calls == [("run-1", False)]
        assert result.read_state is ReadState.READY
        assert result.missing_reason is None
        assert result.source is not None
        source = result.source.source
        assert source.source_kind is SourceKind.BUSINESS_SNAPSHOT
        assert source.source_id == "backtest:run-1"
        assert len(source.digest) == 64
        assert source.available_at == "2026-01-02T03:04:05.000000Z"
        assert source.period == "2025-01-01/2025-12-31"
        assert result.source.verification is VerificationState.PROGRAM_VERIFIED

    @pytest.mark.asyncio
    async def test_missing_row_is_unavailable_not_ready(self) -> None:
        result = await read_backtest_snapshot(FakeReader(row=None), user_scope="u1", result_id="run-1")
        assert result == BusinessSnapshotRead(
            source=None, read_state=ReadState.UNAVAILABLE, missing_reason="result_not_found"
        )

    @pytest.mark.asyncio
    async def test_database_failure_is_persistence_unavailable(self) -> None:
        reader = FakeReader(error=DatabaseQueryError("db down"))
        with pytest.raises(AgentServiceError) as exc:
            await read_backtest_snapshot(reader, user_scope="u1", result_id="run-1")
        assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_disposed_engine_propagates(self) -> None:
        reader = FakeReader(error=EngineDisposedError("disposed"))
        raised = False
        try:
            await read_backtest_snapshot(reader, user_scope="u1", result_id="run-1")
        except EngineDisposedError as exc:
            raised = True
            assert type(exc) is EngineDisposedError
        assert raised is True
        assert reader.calls == [("run-1", False)]

    @pytest.mark.asyncio
    async def test_cancellation_is_never_swallowed(self) -> None:
        reader = FakeReader(error=asyncio.CancelledError())
        cancelled = False
        try:
            await read_backtest_snapshot(reader, user_scope="u1", result_id="run-1")
        except asyncio.CancelledError:
            cancelled = True
        assert cancelled is True

    @pytest.mark.asyncio
    async def test_cutoff_before_available_at_yields_no_source(self) -> None:
        result = await read_backtest_snapshot(
            FakeReader(row=_row()),
            user_scope="u1",
            result_id="run-1",
            cutoff="2026-01-01T00:00:00.000000Z",
        )
        assert result.source is None
        assert result.read_state is ReadState.UNAVAILABLE
        assert result.missing_reason == "available_after_cutoff"

    @pytest.mark.asyncio
    async def test_cutoff_after_available_at_keeps_source(self) -> None:
        result = await read_backtest_snapshot(
            FakeReader(row=_row()),
            user_scope="u1",
            result_id="run-1",
            cutoff="2026-02-01T00:00:00.000000Z",
        )
        assert result.source is not None
        assert result.source.source.source_id == "backtest:run-1"
        assert len(result.source.source.digest) == 64
        assert result.source.verification is VerificationState.PROGRAM_VERIFIED
        assert result.read_state is ReadState.READY

    @pytest.mark.asyncio
    async def test_naive_cutoff_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await read_backtest_snapshot(
                FakeReader(row=_row()), user_scope="u1", result_id="run-1", cutoff="2026-02-01T00:00:00"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID
