"""E-01-5 回测严格读四分支：成功 / 不存在 / DB 失败 / disposed / 取消。"""

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
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
        # backtest_results 的真实时间列是 executed_at（无 created_at）。
        "executed_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
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
    async def test_unknown_available_at_with_cutoff_is_not_mislabeled(self) -> None:
        # R21：时点未知 ≠ 晚于截止；必须给可审计的独立原因。
        row = _row()
        row.pop("executed_at")
        result = await read_backtest_snapshot(
            FakeReader(row=row),
            user_scope="u1",
            result_id="run-1",
            cutoff="2026-02-01T00:00:00.000000Z",
        )
        assert result.source is None
        assert result.read_state is ReadState.UNAVAILABLE
        assert result.missing_reason == "available_at_unknown"

    @pytest.mark.asyncio
    async def test_unknown_available_at_without_cutoff_stays_unknown(self) -> None:
        row = _row()
        row.pop("executed_at")
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert result.source is not None
        assert result.source.source.available_at is None
        assert result.read_state is ReadState.READY

    @pytest.mark.asyncio
    async def test_nat_available_at_is_unknown_not_an_exception(self) -> None:
        # N3：pandas NaT 是 datetime 实例；不得进入 astimezone 抛裸 ValueError。
        row = _row()
        row["executed_at"] = pd.NaT
        with_cutoff = await read_backtest_snapshot(
            FakeReader(row=row),
            user_scope="u1",
            result_id="run-1",
            cutoff="2026-02-01T00:00:00.000000Z",
        )
        assert with_cutoff.source is None
        assert with_cutoff.missing_reason == "available_at_unknown"
        without_cutoff = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert without_cutoff.source is not None
        assert without_cutoff.source.source.available_at is None

    @pytest.mark.asyncio
    async def test_naive_cutoff_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await read_backtest_snapshot(
                FakeReader(row=_row()), user_scope="u1", result_id="run-1", cutoff="2026-02-01T00:00:00"
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_naive_executed_at_is_zone_unknown_not_utc(self) -> None:
        """I64：无时区时间不得猜成 UTC；时区不可证时 fail-closed 并给独立原因。"""
        row = _row()
        row["executed_at"] = datetime(2026, 1, 2, 3, 4, 5)
        without_cutoff = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert without_cutoff.source is None
        assert without_cutoff.read_state is ReadState.UNAVAILABLE
        assert without_cutoff.missing_reason == "available_at_zone_unknown"
        with_cutoff = await read_backtest_snapshot(
            FakeReader(row=row),
            user_scope="u1",
            result_id="run-1",
            cutoff="2026-02-01T00:00:00.000000Z",
        )
        # 不得误报为「晚于截止」：那是另一种可审计原因。
        assert with_cutoff.missing_reason == "available_at_zone_unknown"

    @pytest.mark.asyncio
    async def test_pandas_naive_timestamp_is_zone_unknown(self) -> None:
        """生产 DAO 经 pandas 返回 naive Timestamp；同样不得补成 UTC。"""
        row = _row()
        row["executed_at"] = pd.Timestamp("2026-01-02 03:04:05")
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert result.source is None
        assert result.missing_reason == "available_at_zone_unknown"

    @pytest.mark.asyncio
    async def test_date_only_value_is_not_padded_to_midnight(self) -> None:
        """纯日期不含时刻：不补 T00:00:00Z，按未知处理（设计正本「未知不补午夜」）。"""
        row = _row()
        row["executed_at"] = date(2026, 1, 2)
        result = await read_backtest_snapshot(
            FakeReader(row=row),
            user_scope="u1",
            result_id="run-1",
            cutoff="2026-02-01T00:00:00.000000Z",
        )
        assert result.source is None
        assert result.missing_reason == "available_at_unknown"

    @pytest.mark.asyncio
    async def test_aware_non_utc_timestamp_is_converted_to_utc(self) -> None:
        """自带偏移的时间按其时区换算为 UTC（不按本机时区近似）。"""
        row = _row()
        row["executed_at"] = datetime(2026, 1, 2, 11, 4, 5, tzinfo=timezone(timedelta(hours=8)))
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert result.source is not None
        assert result.source.source.available_at == "2026-01-02T03:04:05.000000Z"
        assert result.read_state is ReadState.READY


def _rich_row() -> dict:
    """带完整参数/指标/质量元数据的结果行（模拟 BT-03 起落库形态）。"""
    row = _row()
    row.update(
        {
            "annualized_return": 0.1,
            "max_drawdown": -0.2,
            "total_trades": 12,
            "app_version": "1.2.3",
            "duration_ms": 1234,
            "params_snapshot": {"lookback": 60, "top_k": 20, "delist_recovery_rate": 0.25},
            "config_json": {
                "initial_capital": 1000000.0,
                "price_adjustment": "qfq",
                "execution_price": "next_open",
                "rebalance_freq": "weekly",
                "delist_recovery_rate": 0.25,
            },
            "quality_json": {
                "data_warnings": [
                    "[range_quality_gaps] 2025-02-01-2025-02-03: 3 stocks affected. missing 2 trade days"
                ],
                "failed_signal_dates": [{"signal_date": "2025-03-01", "reason": "no candidates"}],
                "skipped_order_count": 2,
                "has_real_score": False,
                "real_score_ratio": 0.0,
            },
        }
    )
    return row


class TestBacktestSnapshotBody:
    """I75：按结果版本准备完整受管正文（参数/指标/告警/失败日/已知限制）。"""

    @pytest.mark.asyncio
    async def test_body_carries_params_metrics_warnings_and_failed_dates(self) -> None:
        result = await read_backtest_snapshot(FakeReader(row=_rich_row()), user_scope="u1", result_id="run-1")
        assert result.source is not None
        body = result.source.body_text
        assert body is not None
        assert body.startswith("# 回测结果 run-1")
        assert "策略参数 lookback：60" in body
        assert "策略参数 top_k：20" in body
        assert "复权口径：qfq" in body
        assert "成交价口径：next_open" in body
        assert "总收益（比例小数）：0.123" in body
        # NaN 不得渲染成 0 或「无」：显式未提供（R21）
        assert "夏普比率：未提供" in body
        assert "[range_quality_gaps]" in body
        assert "signal_date=2025-03-01" in body
        assert "reason=no candidates" in body
        assert "退市清算回收率假设：0.25" in body
        assert "DAT-08②" in body and "DAT-07" in body and "BT-05" in body
        assert "## 可用性说明" in body

    @pytest.mark.asyncio
    async def test_missing_quality_snapshot_is_unknown_not_clean(self) -> None:
        row = _rich_row()
        row.pop("quality_json")
        row.pop("config_json")
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert result.source is not None
        body = result.source.body_text or ""
        assert "质量快照：未提供" in body
        assert "失败日清单：未提供" in body
        assert "完整配置快照：未提供" in body
        assert "数据告警：无" not in body
        assert "失败日清单：无" not in body

    @pytest.mark.asyncio
    async def test_empty_warning_lists_are_explicit_no_warnings(self) -> None:
        row = _row()
        row["quality_json"] = {"data_warnings": [], "failed_signal_dates": []}
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert result.source is not None
        body = result.source.body_text or ""
        assert "数据告警：无（质量快照中为空列表）" in body
        assert "失败日清单：无（质量快照中为空列表）" in body

    @pytest.mark.asyncio
    async def test_body_is_deterministic_and_bound_to_result_version(self) -> None:
        first = await read_backtest_snapshot(FakeReader(row=_rich_row()), user_scope="u1", result_id="run-1")
        second = await read_backtest_snapshot(FakeReader(row=_rich_row()), user_scope="u1", result_id="run-1")
        assert first.source is not None and second.source is not None
        assert first.source.body_text == second.source.body_text
        assert first.source.body_text is not None
        assert f"结果版本（内容摘要前缀）：{first.source.source.digest[:12]}" in first.source.body_text

    @pytest.mark.asyncio
    async def test_unavailable_branch_has_no_body(self) -> None:
        result = await read_backtest_snapshot(FakeReader(row=None), user_scope="u1", result_id="run-1")
        assert result.source is None

    @pytest.mark.asyncio
    async def test_decimal_nan_metric_is_missing_not_a_value(self) -> None:
        # R21：数值列 NaN（Decimal）不得原样输出成「有效指标」。
        row = _row()
        row["sharpe_ratio"] = Decimal("NaN")
        row["calmar_ratio"] = Decimal("0.5")
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        assert result.source is not None
        body = result.source.body_text or ""
        assert "夏普比率：未提供" in body
        assert "卡玛比率：0.5" in body

    @pytest.mark.asyncio
    async def test_unknown_available_at_is_marked_unknown_in_body(self) -> None:
        row = _row()
        row.pop("executed_at")
        result = await read_backtest_snapshot(FakeReader(row=row), user_scope="u1", result_id="run-1")
        body = (result.source.body_text or "") if result.source is not None else ""
        assert result.read_state is ReadState.READY
        assert "执行时点（UTC）：未知" in body
