"""BacktestDAO 单元测试"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import polars as pl
import pytest

from data.persistence.daos.backtest_dao import BacktestDAO, _serialize_jsonb_value
from data.persistence.daos.base_dao import EngineDisposedError
from strategies.backtest.config import BacktestConfig, BacktestResult

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_engine() -> MagicMock:
    return MagicMock()


@pytest.fixture
def dao(mock_engine: MagicMock) -> BacktestDAO:
    return BacktestDAO(mock_engine)


@pytest.fixture
def backtest_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
    )


def _result_to_dict(result: BacktestResult) -> dict:
    return {
        "run_id": result.run_id,
        "strategy_name": result.strategy_name,
        "params_snapshot": result.params_snapshot,
        "config": result.config,
        "metrics": result.metrics,
        "nav_curve": result.nav_curve,
        "trades": result.trades,
        "period_stats": result.period_stats,
        "duration_ms": result.duration_ms,
    }


@pytest.fixture
def backtest_result(backtest_config: BacktestConfig) -> BacktestResult:
    return BacktestResult(
        config=backtest_config,
        strategy_name="test_strategy",
        params_snapshot={"param1": "value1"},
        nav_curve=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "nav": [1_000_000.0, 1_010_000.0],
            }
        ),
        daily_returns=pl.Series([0.0, 0.01]),
        benchmark_returns=pl.Series([0.0, 0.008]),
        trades=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "action": ["buy"],
                "price": [10.0],
                "volume": [1000],
                "realized_pnl": [0.0],
            }
        ),
        positions=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "total_value": [1_000_000.0, 1_010_000.0],
            }
        ),
        skipped_orders=pl.DataFrame(),
        metrics={
            "total_return": 0.01,
            "annualized_return": 0.12,
            "sharpe_ratio": 1.5,
            "max_drawdown": 0.05,
            "calmar_ratio": 2.4,
            "ic_mean": 0.03,
            "ic_ir": 0.5,
            "win_rate": 0.6,
            "profit_factor": 1.8,
            "total_trades": 10,
        },
        ic_series=pl.Series([0.02, 0.04]),
        period_stats=pl.DataFrame(
            {
                "year_month": ["2024-01"],
                "monthly_return": [0.01],
                "benchmark_return": [0.008],
                "excess_return": [0.002],
                "start_nav": [100.0],
                "end_nav": [101.0],
            }
        ),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="test_run_001",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=1000,
    )


@pytest.fixture
def empty_result(backtest_config: BacktestConfig) -> BacktestResult:
    return BacktestResult(
        config=backtest_config,
        strategy_name="empty_strategy",
        params_snapshot={},
        nav_curve=pl.DataFrame(),
        daily_returns=pl.Series(),
        benchmark_returns=pl.Series(),
        trades=pl.DataFrame(),
        positions=pl.DataFrame(),
        skipped_orders=pl.DataFrame(),
        metrics={},
        ic_series=pl.Series(),
        period_stats=pl.DataFrame(),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="empty_run_001",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=500,
    )


class TestBacktestDAO:
    @pytest.mark.asyncio
    async def test_save_result_success(
        self,
        dao: BacktestDAO,
        backtest_result: BacktestResult,
    ) -> None:
        dao._save_upsert = AsyncMock(return_value=1)

        result_id = await dao.save_result(_result_to_dict(backtest_result))

        assert result_id == 1
        dao._save_upsert.assert_called_once()
        call_args = dao._save_upsert.call_args
        assert call_args[0][1] == "backtest_results"

    @pytest.mark.asyncio
    async def test_save_result_with_empty_data(
        self,
        dao: BacktestDAO,
        empty_result: BacktestResult,
    ) -> None:
        dao._save_upsert = AsyncMock(return_value=1)

        result_id = await dao.save_result(_result_to_dict(empty_result))

        assert result_id == 1
        dao._save_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_result_found(self, dao: BacktestDAO) -> None:
        mock_df = pd.DataFrame(
            {
                "run_id": ["test_run_001"],
                "strategy_name": ["test_strategy"],
                "sharpe_ratio": [1.5],
            }
        )
        dao._read_db_select = AsyncMock(return_value=mock_df)

        result = await dao.get_result("test_run_001")

        assert result is not None
        assert result["run_id"] == "test_run_001"
        assert result["strategy_name"] == "test_strategy"

    @pytest.mark.asyncio
    async def test_get_result_not_found(self, dao: BacktestDAO) -> None:
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())

        result = await dao.get_result("nonexistent_run")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_result_none_returned(self, dao: BacktestDAO) -> None:
        dao._read_db_select = AsyncMock(return_value=None)

        result = await dao.get_result("nonexistent_run")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_results_no_filter(self, dao: BacktestDAO) -> None:
        mock_df = pd.DataFrame(
            {
                "run_id": ["run1", "run2"],
                "strategy_name": ["strategy1", "strategy2"],
                "start_date": [date(2024, 1, 1), date(2024, 2, 1)],
                "end_date": [date(2024, 1, 31), date(2024, 2, 28)],
                "sharpe_ratio": [1.5, 2.0],
                "max_drawdown": [0.05, 0.03],
                "executed_at": [datetime(2024, 1, 31), datetime(2024, 2, 28)],
            }
        )
        dao._read_db_select = AsyncMock(return_value=mock_df)

        results = await dao.list_results()

        assert len(results) == 2
        assert results[0]["run_id"] == "run1"
        assert results[1]["run_id"] == "run2"

    @pytest.mark.asyncio
    async def test_list_results_with_strategy_filter(self, dao: BacktestDAO) -> None:
        mock_df = pd.DataFrame(
            {
                "run_id": ["run1"],
                "strategy_name": ["test_strategy"],
                "start_date": [date(2024, 1, 1)],
                "end_date": [date(2024, 1, 31)],
                "sharpe_ratio": [1.5],
                "max_drawdown": [0.05],
                "executed_at": [datetime(2024, 1, 31)],
            }
        )
        dao._read_db_select = AsyncMock(return_value=mock_df)

        results = await dao.list_results(strategy_name="test_strategy")

        assert len(results) == 1
        assert results[0]["strategy_name"] == "test_strategy"

    @pytest.mark.asyncio
    async def test_list_results_empty(self, dao: BacktestDAO) -> None:
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame())

        results = await dao.list_results()

        assert results == []

    @pytest.mark.asyncio
    async def test_list_results_none_returned(self, dao: BacktestDAO) -> None:
        dao._read_db_select = AsyncMock(return_value=None)

        results = await dao.list_results()

        assert results == []

    @pytest.mark.asyncio
    async def test_list_results_with_limit(self, dao: BacktestDAO) -> None:
        mock_df = pd.DataFrame(
            {
                "run_id": ["run1"],
                "strategy_name": ["strategy1"],
                "start_date": [date(2024, 1, 1)],
                "end_date": [date(2024, 1, 31)],
                "sharpe_ratio": [1.5],
                "max_drawdown": [0.05],
                "executed_at": [datetime(2024, 1, 31)],
            }
        )
        dao._read_db_select = AsyncMock(return_value=mock_df)

        results = await dao.list_results(limit=10)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_delete_result_success(self, dao: BacktestDAO) -> None:
        dao._check_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        dao.engine.begin = MagicMock(return_value=mock_begin)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            success = await dao.delete_result("test_run_001")

        assert success is True
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_result_failure(self, dao: BacktestDAO) -> None:
        dao._check_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("DB error"))
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        dao.engine.begin = MagicMock(return_value=mock_begin)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            success = await dao.delete_result("test_run_001")

        assert success is False

    @pytest.mark.asyncio
    async def test_delete_result_engine_disposed(self, dao: BacktestDAO) -> None:
        """R5: _check_engine 抛 EngineDisposedError 时必须传播，不再转为 False。"""
        dao._check_engine = MagicMock(side_effect=EngineDisposedError("disposed"))

        with pytest.raises(EngineDisposedError):
            await dao.delete_result("test_run_001")

    @pytest.mark.asyncio
    async def test_delete_result_engine_disposed_during_delete(self, dao: BacktestDAO) -> None:
        """R5: _guarded_begin 抛 EngineDisposedError 时必须传播，不再转为 False。"""
        dao._check_engine = MagicMock()

        @asynccontextmanager
        async def mock_begin_disposed(conn=None):
            raise EngineDisposedError("disposed")
            yield  # pragma: no cover - unreachable, required for asynccontextmanager

        dao._guarded_begin = mock_begin_disposed

        with pytest.raises(EngineDisposedError):
            await dao.delete_result("test_run_001")

    @pytest.mark.asyncio
    async def test_delete_result_cancelled_error_propagates(self, dao: BacktestDAO) -> None:
        dao._check_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=asyncio.CancelledError())
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        dao.engine.begin = MagicMock(return_value=mock_begin)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(asyncio.CancelledError):
                await dao.delete_result("test_run_001")

    @pytest.mark.asyncio
    async def test_delete_result_shutdown_connection_error(self, dao: BacktestDAO) -> None:
        """R5: _guarded_begin 把 'no active connection' 转为 EngineDisposedError，必须传播。"""
        dao._check_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("no active connection"))
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        dao.engine.begin = MagicMock(return_value=mock_begin)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(EngineDisposedError):
                await dao.delete_result("test_run_001")

    @pytest.mark.asyncio
    async def test_delete_result_cache_manager_disposed(self, dao: BacktestDAO) -> None:
        """R5: CacheManager 已 disposed 时 _check_engine 抛 EngineDisposedError，必须传播。"""
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_instance = MagicMock()
            mock_instance._disposed = True
            mock_cm._instance = mock_instance
            with pytest.raises(EngineDisposedError):
                await dao.delete_result("test_run_001")

    @pytest.mark.asyncio
    async def test_delete_result_engine_not_initialized(self, dao: BacktestDAO) -> None:
        dao._check_engine = MagicMock(side_effect=RuntimeError("Engine not initialized"))

        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao.delete_result("test_run_001")


class TestBacktestDAOJsonbSerialization:
    """JSONB date/datetime 序列化测试"""

    @pytest.mark.asyncio
    async def test_save_result_serializes_dates_and_datetimes(
        self,
        dao: BacktestDAO,
        backtest_config: BacktestConfig,
    ) -> None:
        """nav_curve 的 date 列与 trades 的 datetime 列应转为 ISO 字符串。"""
        dao._save_upsert = AsyncMock(return_value=1)

        result = {
            "run_id": "test_serialization",
            "strategy_name": "test_strategy",
            "config": backtest_config,
            "metrics": {},
            "nav_curve": pl.DataFrame(
                {
                    "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                    "nav": [1_000_000.0, 1_010_000.0],
                }
            ),
            "trades": pl.DataFrame(
                {
                    "trade_time": [datetime(2024, 1, 1, 10, 30, 0)],
                    "ts_code": ["000001.SZ"],
                }
            ),
            "period_stats": pl.DataFrame(
                {
                    "period": ["2024-01"],
                    "return": [0.01],
                }
            ),
            "duration_ms": 100,
        }

        await dao.save_result(result)

        call_args = dao._save_upsert.call_args
        df = call_args[0][0]
        record = df.iloc[0]

        nav_curve_json = record["nav_curve_json"]
        assert len(nav_curve_json) == 2
        for row in nav_curve_json:
            assert isinstance(row["trade_date"], str)
        assert nav_curve_json[0]["trade_date"] == "2024-01-01"
        assert nav_curve_json[1]["trade_date"] == "2024-01-02"

        trades_json = record["trades_json"]
        assert len(trades_json) == 1
        assert isinstance(trades_json[0]["trade_time"], str)
        assert trades_json[0]["trade_time"] == "2024-01-01T10:30:00"

    @pytest.mark.asyncio
    async def test_save_result_empty_data_unaffected(
        self,
        dao: BacktestDAO,
        empty_result: BacktestResult,
    ) -> None:
        """nav_curve/trades/period_stats 为空时 JSONB 列保持空列表。"""
        dao._save_upsert = AsyncMock(return_value=1)

        await dao.save_result(_result_to_dict(empty_result))

        call_args = dao._save_upsert.call_args
        df = call_args[0][0]
        record = df.iloc[0]

        assert record["nav_curve_json"] == []
        assert record["trades_json"] == []
        assert record["period_stats_json"] == []

    @pytest.mark.asyncio
    async def test_save_result_none_data_unaffected(
        self,
        dao: BacktestDAO,
        backtest_config: BacktestConfig,
    ) -> None:
        """nav_curve/trades/period_stats 为 None 时 JSONB 列保持空列表。"""
        dao._save_upsert = AsyncMock(return_value=1)

        result = {
            "run_id": "test_none",
            "strategy_name": "test_strategy",
            "config": backtest_config,
            "metrics": {},
            "nav_curve": None,
            "trades": None,
            "period_stats": None,
            "duration_ms": 100,
        }

        await dao.save_result(result)

        call_args = dao._save_upsert.call_args
        df = call_args[0][0]
        record = df.iloc[0]

        assert record["nav_curve_json"] == []
        assert record["trades_json"] == []
        assert record["period_stats_json"] == []

    def test_serialize_jsonb_value_plain_data_unchanged(self) -> None:
        """int/float/str/None/bool 等基础类型应原样返回。"""
        assert _serialize_jsonb_value(1) == 1
        assert _serialize_jsonb_value(1.5) == 1.5
        assert _serialize_jsonb_value("str") == "str"
        assert _serialize_jsonb_value(None) is None
        assert _serialize_jsonb_value(True) is True

    def test_serialize_jsonb_value_converts_date_and_datetime(self) -> None:
        """date 转为 'YYYY-MM-DD'，datetime 转为 ISO 8601 带时间。"""
        assert _serialize_jsonb_value(date(2024, 1, 1)) == "2024-01-01"
        assert _serialize_jsonb_value(datetime(2024, 1, 1, 12, 0, 0)) == "2024-01-01T12:00:00"

    def test_serialize_jsonb_value_recurses_nested_structures(self) -> None:
        """嵌套 dict/list 中的 date/datetime 应递归转换。"""
        value = {
            "date": date(2024, 1, 1),
            "datetime": datetime(2024, 1, 1, 12, 0, 0),
            "nested": {
                "list": [date(2024, 1, 2), {"inner": date(2024, 1, 3)}],
                "scalar": 42,
            },
        }

        result = _serialize_jsonb_value(value)

        assert result == {
            "date": "2024-01-01",
            "datetime": "2024-01-01T12:00:00",
            "nested": {
                "list": ["2024-01-02", {"inner": "2024-01-03"}],
                "scalar": 42,
            },
        }
