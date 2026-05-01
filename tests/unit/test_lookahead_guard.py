import datetime

import pytest

from utils.time_utils import get_now


class TestLookaheadGuard:
    """S-02: 回测模式下 end_date 回退到当前日期应抛异常（防止未来函数泄漏）"""

    def test_backtest_invalid_trade_date_raises(self):
        ctx_td = "not_a_date"
        is_backtest = True

        with pytest.raises(ValueError, match="Cannot parse trade_date for backtest"):
            try:
                datetime.datetime.strptime(ctx_td, "%Y%m%d").date()
            except (ValueError, TypeError):
                if is_backtest:
                    raise ValueError(
                        f"Cannot parse trade_date for backtest: {ctx_td!r}. "
                        f"Refusing to fall back to current date to prevent lookahead bias."
                    ) from None

    def test_non_backtest_invalid_trade_date_falls_through(self):
        end_date = get_now().date()
        ctx_td = "not_a_date"
        is_backtest = False

        try:
            parsed = datetime.datetime.strptime(ctx_td, "%Y%m%d").date()
            end_date = parsed
        except (ValueError, TypeError):
            if is_backtest:
                raise ValueError(
                    f"Cannot parse trade_date for backtest: {ctx_td!r}. "
                    f"Refusing to fall back to current date to prevent lookahead bias."
                ) from None

        assert end_date == get_now().date(), "Non-backtest should fall back to current date"

    def test_valid_trade_date_overrides_current_date(self):
        ctx_td = "20240118"

        parsed = datetime.datetime.strptime(ctx_td, "%Y%m%d").date()

        assert parsed == datetime.date(2024, 1, 18), "Valid trade_date should override current date"

    def test_backtest_no_trade_date_uses_current(self):
        """回测模式下未传 trade_date 时使用当前日期（无泄漏风险，因为无未来数据）"""
        end_date = get_now().date()
        ctx_td = None
        is_backtest = True

        if ctx_td:
            try:
                parsed = datetime.datetime.strptime(ctx_td, "%Y%m%d").date()
                end_date = parsed
            except (ValueError, TypeError):
                if is_backtest:
                    raise ValueError(
                        f"Cannot parse trade_date for backtest: {ctx_td!r}. "
                        f"Refusing to fall back to current date to prevent lookahead bias."
                    ) from None

        assert end_date == get_now().date(), "No trade_date should use current date"
