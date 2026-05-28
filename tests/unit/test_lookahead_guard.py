import datetime

import pytest

from data.constants import SAFE_LIVE_LEARNING_OFFSET_DAYS
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
    def test_backtest_shifts_learning_as_of_by_15_days(self):
        result = AIStrategyMixin.compute_learning_as_of("20240118", is_backtest=True)
        expected = datetime.date(2024, 1, 18) - datetime.timedelta(days=15)
        assert result == expected

    def test_non_backtest_does_not_shift_learning_as_of(self):
        result = AIStrategyMixin.compute_learning_as_of("20240118", is_backtest=False)
        assert result == datetime.date(2024, 1, 18)

    def test_none_trade_date_backtest_raises(self):
        with pytest.raises(ValueError, match="Cannot compute learning as_of for backtest"):
            AIStrategyMixin.compute_learning_as_of(None, is_backtest=True)

    def test_invalid_trade_date_backtest_raises(self):
        with pytest.raises(ValueError, match="Cannot compute learning as_of for backtest"):
            AIStrategyMixin.compute_learning_as_of("not_a_date", is_backtest=True)

    def test_none_trade_date_non_backtest_returns_safe_fallback(self):
        result = AIStrategyMixin.compute_learning_as_of(None, is_backtest=False)
        assert result is not None
        expected = get_now().date() - datetime.timedelta(days=SAFE_LIVE_LEARNING_OFFSET_DAYS)
        assert result == expected

    def test_invalid_trade_date_non_backtest_returns_safe_fallback(self):
        result = AIStrategyMixin.compute_learning_as_of("not_a_date", is_backtest=False)
        assert result is not None
        expected = get_now().date() - datetime.timedelta(days=SAFE_LIVE_LEARNING_OFFSET_DAYS)
        assert result == expected
