import datetime

import pytest

from strategies.ai_mixin import AIStrategyMixin
from utils.time_utils import get_now


class TestLookaheadGuard:
    def test_backtest_invalid_trade_date_raises(self):
        with pytest.raises(ValueError, match="Cannot parse trade_date for backtest"):
            AIStrategyMixin.resolve_end_date("not_a_date", is_backtest=True)

    def test_non_backtest_invalid_trade_date_falls_through(self):
        result = AIStrategyMixin.resolve_end_date("not_a_date", is_backtest=False)
        assert result == get_now().date()

    def test_valid_trade_date_overrides_current_date(self):
        result = AIStrategyMixin.resolve_end_date("20240118", is_backtest=True)
        assert result == datetime.date(2024, 1, 18)

    def test_backtest_no_trade_date_uses_current(self):
        result = AIStrategyMixin.resolve_end_date(None, is_backtest=True)
        assert result == get_now().date()


class TestBacktestLearningContextAsOf:
    def test_backtest_shifts_learning_as_of_by_8_days(self):
        result = AIStrategyMixin.compute_learning_as_of("20240118", is_backtest=True)
        expected = datetime.date(2024, 1, 18) - datetime.timedelta(days=8)
        assert result == expected

    def test_non_backtest_does_not_shift_learning_as_of(self):
        result = AIStrategyMixin.compute_learning_as_of("20240118", is_backtest=False)
        assert result == datetime.date(2024, 1, 18)

    def test_none_trade_date_returns_none(self):
        result = AIStrategyMixin.compute_learning_as_of(None, is_backtest=True)
        assert result is None

    def test_invalid_trade_date_returns_none(self):
        result = AIStrategyMixin.compute_learning_as_of("not_a_date", is_backtest=True)
        assert result is None
