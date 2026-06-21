"""BacktestConfig 默认值测试"""

from datetime import date, datetime

import polars as pl

from strategies.backtest.config import BacktestConfig, BacktestResult


class TestBacktestConfigDefaults:
    def test_default_stamp_duty_rate_is_auto(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.stamp_duty_rate is None

    def test_explicit_stamp_duty_rate(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            stamp_duty_rate=1e-3,
        )
        assert config.stamp_duty_rate == 1e-3

    def test_stamp_duty_rate_zero(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            stamp_duty_rate=0.0,
        )
        assert config.stamp_duty_rate == 0.0

    def test_get_cost_config_stamps_duty_none(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        cost_config = config.get_cost_config()
        assert cost_config.stamp_duty_rate is None

    def test_get_cost_config_stamps_duty_explicit(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            stamp_duty_rate=5e-4,
        )
        cost_config = config.get_cost_config()
        assert cost_config.stamp_duty_rate == 5e-4

    def test_default_commission_rate(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.commission_rate == 3e-4

    def test_default_initial_capital(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.initial_capital == 1_000_000.0

    def test_default_preload_max_days(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        assert config.preload_max_days == 366

    def test_custom_preload_max_days(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=730,
        )
        assert config.preload_max_days == 730


class TestBacktestConfigValidation:
    def test_validate_valid_config(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        errors = config.validate()
        assert len(errors) == 0

    def test_validate_invalid_date_range(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 12, 31),
            end_date=date(2023, 1, 1),
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "start_date must be before end_date" in errors[0]

    def test_validate_negative_capital(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            initial_capital=-1000.0,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "initial_capital must be positive" in errors[0]

    def test_validate_preload_max_days_zero(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=0,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "preload_max_days must be at least 30" in errors[0]

    def test_validate_preload_max_days_below_minimum(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=29,
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "preload_max_days must be at least 30" in errors[0]

    def test_validate_preload_max_days_at_minimum(self) -> None:
        config = BacktestConfig(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            preload_max_days=30,
        )
        errors = config.validate()
        assert len(errors) == 0


def _make_result(**overrides) -> BacktestResult:
    """构造 BacktestResult 测试实例，支持覆盖部分字段。"""
    config = overrides.pop("config", None) or BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
        execution_price="next_open",
        allow_limit_up_buy=False,
        allow_limit_down_sell=False,
        slippage_model="fixed_bps",
    )
    defaults = dict(
        config=config,
        strategy_name="test_strategy",
        params_snapshot={"param1": "value1"},
        nav_curve=pl.DataFrame({"trade_date": [date(2024, 1, 1)], "nav": [1_000_000.0]}),
        daily_returns=pl.Series([0.0]),
        benchmark_returns=pl.Series([0.0]),
        trades=pl.DataFrame(),
        positions=pl.DataFrame(),
        skipped_orders=pl.DataFrame(),
        metrics={"total_return": 0.01, "sharpe_ratio": 1.5},
        ic_series=pl.Series([0.02]),
        period_stats=pl.DataFrame(),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="test_run_001",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=1000,
    )
    defaults.update(overrides)
    return BacktestResult(**defaults)


class TestBacktestResultToPersistDict:
    """Task 6.10: BacktestResult.to_persist_dict() 持久化字典生成。"""

    def test_to_persist_dict_contains_all_required_fields(self) -> None:
        result = _make_result()
        d = result.to_persist_dict()
        expected_keys = {
            "run_id",
            "strategy_name",
            "params_snapshot",
            "start_date",
            "end_date",
            "initial_capital",
            "metrics",
            "nav_curve",
            "trades",
            "period_stats",
            "duration_ms",
            "execution_price",
            "allow_limit_up_buy",
            "allow_limit_down_sell",
            "slippage_model",
        }
        assert expected_keys.issubset(set(d.keys()))

    def test_to_persist_dict_excludes_app_version(self) -> None:
        """app_version 由调用方（BacktestService）补充，不应出现在结果字典中。"""
        result = _make_result()
        d = result.to_persist_dict()
        assert "app_version" not in d

    def test_to_persist_dict_flattens_config_fields(self) -> None:
        """config 中的字段应被平铺到顶层，而非嵌套在 config 子字典中。"""
        result = _make_result()
        d = result.to_persist_dict()
        assert d["start_date"] == result.config.start_date
        assert d["end_date"] == result.config.end_date
        assert d["initial_capital"] == result.config.initial_capital
        assert d["execution_price"] == result.config.execution_price
        assert d["allow_limit_up_buy"] == result.config.allow_limit_up_buy
        assert d["allow_limit_down_sell"] == result.config.allow_limit_down_sell
        assert d["slippage_model"] == result.config.slippage_model
        assert "config" not in d

    def test_to_persist_dict_references_result_attributes(self) -> None:
        """顶层结果字段应直接引用 BacktestResult 属性。"""
        result = _make_result()
        d = result.to_persist_dict()
        assert d["run_id"] == result.run_id
        assert d["strategy_name"] == result.strategy_name
        assert d["params_snapshot"] is result.params_snapshot
        assert d["metrics"] is result.metrics
        assert d["nav_curve"] is result.nav_curve
        assert d["trades"] is result.trades
        assert d["period_stats"] is result.period_stats
        assert d["duration_ms"] == result.duration_ms
