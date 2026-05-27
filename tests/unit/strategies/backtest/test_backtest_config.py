"""BacktestConfig 默认值测试"""

from datetime import date


from strategies.backtest.config import BacktestConfig


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
