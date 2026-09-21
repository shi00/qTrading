import math
from datetime import date

import polars as pl
import pytest

from data.domain_services.transaction_cost import (
    TransactionCostConfig,
    TransactionCostModel,
)
from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.portfolio import PortfolioSimulator

pytestmark = pytest.mark.unit


class TestRebalanceLogic:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_daily_rebalance_no_unnecessary_turnover(self):
        """D4-4：等权重持仓在后续调仓日仍处于目标区间时，不得被卖出再买回。

        旧实现每次调仓全清全买；差集调仓后，单一持仓保持目标权重时应
        只进行一次建仓，后续调仓日无多余买卖（消除虚假换手）。
        """
        engine = self._make_engine(rebalance_freq="daily", min_rebalance_delta_pct=0.05)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "ts_code": ["000001.SZ", "000001.SZ"],
                "signal_rank": [1, 1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buy_trades = trades.filter(pl.col("action") == "buy") if not trades.is_empty() else pl.DataFrame()
        sell_trades = trades.filter(pl.col("action") == "sell") if not trades.is_empty() else pl.DataFrame()
        assert len(buy_trades) == 1
        assert len(sell_trades) == 0

    def test_signal_rebalance_holds_between_signals(self):
        engine = self._make_engine(rebalance_freq="signal")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5, 11.0],
                "raw_close": [10.2, 10.7, 11.2],
                "qfq_open": [10.0, 10.5, 11.0],
                "qfq_close": [10.2, 10.7, 11.2],
                "is_tradable": [True, True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buy_trades = trades.filter(pl.col("action") == "buy") if not trades.is_empty() else pl.DataFrame()
        sell_trades = trades.filter(pl.col("action") == "sell") if not trades.is_empty() else pl.DataFrame()
        assert len(buy_trades) == 1
        assert len(sell_trades) == 0

    def test_weekly_rebalance_no_unnecessary_turnover(self):
        """D4-4：周度调仓边界触发时，若持仓仍处于目标区间则不产生多余交易。

        旧实现每次周度调仓对重叠持仓全清全买；差集调仓后应只在权重漂移
        超过阈值时才调整，目标保持的持仓不产生买卖 → 仅首次建仓 1 笔买入。
        """
        engine = self._make_engine(rebalance_freq="weekly", min_rebalance_delta_pct=0.05)
        trade_dates = [
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
            date(2024, 1, 15),
        ]
        signals = pl.DataFrame(
            {
                "execution_date": [
                    date(2024, 1, 8),
                    date(2024, 1, 9),
                    date(2024, 1, 10),
                    date(2024, 1, 15),
                ],
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ", "000001.SZ"],
                "signal_rank": [1, 1, 1, 1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 4,
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5, 11.0, 11.5],
                "raw_close": [10.2, 10.7, 11.2, 11.7],
                "qfq_open": [10.0, 10.5, 11.0, 11.5],
                "qfq_close": [10.2, 10.7, 11.2, 11.7],
                "is_tradable": [True, True, True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buy_trades = trades.filter(pl.col("action") == "buy") if not trades.is_empty() else pl.DataFrame()
        sell_trades = trades.filter(pl.col("action") == "sell") if not trades.is_empty() else pl.DataFrame()
        assert len(buy_trades) == 1
        assert len(sell_trades) == 0

    def test_monthly_rebalance_no_unnecessary_turnover(self):
        """D4-4：月度调仓边界触发时，若持仓仍处于目标区间则不产生多余交易。

        与 weekly 同理：单一只在目标权重的持仓，跨月度边界时保持不动，
        仅首次建仓 1 笔买入，无虚假换手。
        """
        engine = self._make_engine(rebalance_freq="monthly", min_rebalance_delta_pct=0.05)
        trade_dates = [
            date(2024, 1, 2),
            date(2024, 1, 15),
            date(2024, 2, 1),
        ]
        signals = pl.DataFrame(
            {
                "execution_date": trade_dates,
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "signal_rank": [1, 1, 1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 3,
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5, 11.0],
                "raw_close": [10.2, 10.7, 11.2],
                "qfq_open": [10.0, 10.5, 11.0],
                "qfq_close": [10.2, 10.7, 11.2],
                "is_tradable": [True, True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buy_trades = trades.filter(pl.col("action") == "buy") if not trades.is_empty() else pl.DataFrame()
        sell_trades = trades.filter(pl.col("action") == "sell") if not trades.is_empty() else pl.DataFrame()
        assert len(buy_trades) == 1
        assert len(sell_trades) == 0

    def test_no_signals_no_trades(self):
        engine = self._make_engine(rebalance_freq="signal")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        assert trades.is_empty()


class TestNAVCalculation:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_market_value_uses_volume_times_qfq_close(self):
        engine = self._make_engine(rebalance_freq="signal", cash_reserve_pct=0.1)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        assert not trades.is_empty()
        buy_trade = trades.filter(pl.col("action") == "buy")
        assert not buy_trade.is_empty()
        volume = buy_trade["volume"][0]
        day2_pos = positions.filter(pl.col("trade_date") == date(2024, 1, 3))
        assert not day2_pos.is_empty()
        total_value = float(day2_pos["total_value"][0])
        cash = float(day2_pos["cash"][0])
        expected_market_value = volume * 10.7
        assert total_value == pytest.approx(cash + expected_market_value, rel=1e-4)

    def test_market_value_not_amplified_by_fees(self):
        engine = self._make_engine(rebalance_freq="signal", cash_reserve_pct=0.1)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buy_trade = trades.filter(pl.col("action") == "buy")
        assert not buy_trade.is_empty()
        volume = buy_trade["volume"][0]
        cost_basis = float(buy_trade["net_amount"][0])
        day2_pos = positions.filter(pl.col("trade_date") == date(2024, 1, 3))
        assert not day2_pos.is_empty()
        total_value = float(day2_pos["total_value"][0])
        cash = float(day2_pos["cash"][0])
        market_value = total_value - cash
        buggy_value = cost_basis * (10.7 / 10.2)
        correct_value = volume * 10.7
        assert market_value == pytest.approx(correct_value, rel=1e-4)
        assert market_value != pytest.approx(buggy_value, rel=1e-2)

    def test_market_value_uses_raw_close_after_ex_dividend(self):
        """
        验证除权日市值使用 qfq 口径。

        除权日 raw_close 从 10.2 变为 5.1（10 送 10），
        qfq_close 保持 10.2 不变。

        NAV 使用 qfq_close 计算，市值正确反映除权：
        - Day 2 市值 = volume * 10.2 (qfq_close)
        - 这与 raw 价格计算的市值不同，是正确的行为
        """
        engine = self._make_engine(rebalance_freq="signal", cash_reserve_pct=0.1)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 5.0],
                "raw_close": [10.2, 5.1],
                "qfq_open": [10.0, 10.0],
                "qfq_close": [10.2, 10.2],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buy_trade = trades.filter(pl.col("action") == "buy")
        assert not buy_trade.is_empty()
        volume = buy_trade["volume"][0]
        day2_pos = positions.filter(pl.col("trade_date") == date(2024, 1, 3))
        assert not day2_pos.is_empty()
        total_value = float(day2_pos["total_value"][0])
        cash = float(day2_pos["cash"][0])
        market_value = total_value - cash
        expected_qfq_value = volume * 10.2
        assert market_value == pytest.approx(expected_qfq_value, rel=1e-4)


class TestExecutionPrice:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig(slippage_bps=0.0))
        return engine

    def test_next_open_execution(self):
        engine = self._make_engine(rebalance_freq="signal", execution_price="next_open")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        if not trades.is_empty():
            buy_trades = trades.filter(pl.col("action") == "buy")
            if not buy_trades.is_empty():
                assert buy_trades["price"][0] == 10.0

    def test_next_close_execution(self):
        engine = self._make_engine(rebalance_freq="signal", execution_price="next_close")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        if not trades.is_empty():
            buy_trades = trades.filter(pl.col("action") == "buy")
            if not buy_trades.is_empty():
                assert buy_trades["price"][0] == 10.2


class TestLimitControl:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_default_skip_limit_up_buy(self):
        engine = self._make_engine(rebalance_freq="daily", allow_limit_up_buy=False)
        trade_dates = [date(2024, 1, 2)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [10.0],
                "qfq_close": [10.5],
                "is_tradable": [True],
                "limit_up_price": [10.0],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        assert len(trades) == 0
        assert len(skipped) >= 1

    def test_allow_limit_up_buy(self):
        engine = self._make_engine(rebalance_freq="daily", allow_limit_up_buy=True)
        trade_dates = [date(2024, 1, 2)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [10.0],
                "qfq_close": [10.5],
                "is_tradable": [True],
                "limit_up_price": [10.0],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        assert len(trades) >= 1

    def test_default_skip_limit_down_sell(self):
        engine = self._make_engine(rebalance_freq="daily", allow_limit_down_sell=False)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [2, 1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)],
                "raw_open": [10.0, 10.5, 20.0],
                "raw_close": [10.5, 10.0, 20.5],
                "qfq_open": [10.0, 10.5, 20.0],
                "qfq_close": [10.5, 10.0, 20.5],
                "is_tradable": [True, True, True],
                "limit_down_price": [None, 10.5, None],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        down_limit_skips = (
            skipped.filter(pl.col("reason") == "down_limit") if not skipped.is_empty() else pl.DataFrame()
        )
        assert len(down_limit_skips) >= 1

    def test_allow_limit_down_sell(self):
        engine = self._make_engine(rebalance_freq="daily", allow_limit_down_sell=True, on_empty_signal="liquidate")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "raw_open": [10.0, 10.5],
                "raw_close": [10.5, 10.0],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.5, 10.0],
                "is_tradable": [True, True],
                "limit_down_price": [None, 10.5],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        sell_trades = trades.filter(pl.col("action") == "sell") if not trades.is_empty() else pl.DataFrame()
        assert len(sell_trades) >= 1


class TestCashReserve:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_default_cash_reserve(self):
        engine = self._make_engine(rebalance_freq="daily", cash_reserve_pct=0.1)
        trade_dates = [date(2024, 1, 2)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [10.0],
                "qfq_close": [10.5],
                "is_tradable": [True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        if not positions.is_empty():
            cash = positions["cash"][0]
            assert cash > 0

    def test_zero_cash_reserve(self):
        engine = self._make_engine(rebalance_freq="daily", cash_reserve_pct=0.0)
        trade_dates = [date(2024, 1, 2)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [10.0],
                "qfq_close": [10.5],
                "is_tradable": [True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        if not positions.is_empty():
            buy_trades = trades.filter(pl.col("action") == "buy") if not trades.is_empty() else pl.DataFrame()
            if not buy_trades.is_empty():
                volume = buy_trades["volume"][0]
                assert volume > 0


class TestNextRebalanceDate:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 28),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_weekly_next_rebalance_uses_calendar_not_fixed_offset(self):
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
            date(2024, 1, 11),
            date(2024, 1, 15),
            date(2024, 1, 16),
        ]
        next_rb = engine._get_next_rebalance_date(date(2024, 1, 8), trade_dates, "weekly")
        assert next_rb == date(2024, 1, 15)

    def test_monthly_next_rebalance_uses_calendar_not_fixed_offset(self):
        engine = self._make_engine(rebalance_freq="monthly")
        trade_dates = [
            date(2024, 1, 29),
            date(2024, 1, 30),
            date(2024, 1, 31),
            date(2024, 2, 1),
            date(2024, 2, 2),
        ]
        next_rb = engine._get_next_rebalance_date(date(2024, 1, 29), trade_dates, "monthly")
        assert next_rb == date(2024, 2, 1)

    def test_daily_next_rebalance_is_next_day(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        next_rb = engine._get_next_rebalance_date(date(2024, 1, 2), trade_dates, "daily")
        assert next_rb == date(2024, 1, 3)

    def test_signal_next_rebalance_is_next_day(self):
        engine = self._make_engine(rebalance_freq="signal")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        next_rb = engine._get_next_rebalance_date(date(2024, 1, 2), trade_dates, "signal")
        assert next_rb == date(2024, 1, 3)

    def test_next_rebalance_returns_none_at_end(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        next_rb = engine._get_next_rebalance_date(date(2024, 1, 3), trade_dates, "daily")
        assert next_rb is None


class TestPnlAfterExDividend:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_pnl_uses_qfq_market_value_not_raw_after_ex_dividend(self):
        """
        验证 PnL 使用 qfq_market_value 而非 raw_market_value。

        同时验证 PnL 口径一致性：cost_basis 也使用 qfq 价格计算。
        """
        engine = self._make_engine(rebalance_freq="signal", cash_reserve_pct=0.1)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 5.0],
                "raw_close": [10.2, 5.1],
                "qfq_open": [10.0, 10.0],
                "qfq_close": [10.2, 10.2],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        day2_pos = positions.filter(pl.col("trade_date") == date(2024, 1, 3))
        assert not day2_pos.is_empty()
        pos_detail = day2_pos["positions"][0]
        assert "000001.SZ" in pos_detail
        stock_pos = pos_detail["000001.SZ"]
        qfq_mv = stock_pos["market_value"]

        buy_trade = trades.filter(pl.col("action") == "buy")
        volume = int(buy_trade["volume"][0])
        qfq_entry_price = float(quotes_df.filter(pl.col("trade_date") == date(2024, 1, 2)).select("qfq_open").item())
        qfq_cost_basis = volume * qfq_entry_price

        expected_pnl = qfq_mv - qfq_cost_basis
        buggy_raw_pnl = stock_pos["raw_market_value"] - qfq_cost_basis
        assert stock_pos["pnl"] == pytest.approx(expected_pnl, rel=1e-4)
        assert stock_pos["pnl"] != pytest.approx(buggy_raw_pnl, rel=1e-2)

    def test_pnl_uses_qfq_cost_basis_after_ex_dividend(self):
        """
        验证 PnL 口径一致性：cost_basis 也应使用 qfq 价格计算。

        场景：
        - Day 1: 买入，raw_open=10.0, qfq_open=10.0
        - Day 2: 除权日，raw_open=5.0, qfq_open=10.0（复权价格不变）

        如果 cost_basis 使用 raw 价格计算，PnL 会显示虚假亏损。
        正确做法：cost_basis 也用 qfq 价格计算，PnL = qfq_market_value - qfq_cost_basis。
        """
        engine = self._make_engine(rebalance_freq="signal", cash_reserve_pct=0.1)
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 5.0],
                "raw_close": [10.2, 5.1],
                "qfq_open": [10.0, 10.0],
                "qfq_close": [10.2, 10.2],
                "is_tradable": [True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        buy_trade = trades.filter(pl.col("action") == "buy")
        assert not buy_trade.is_empty()
        volume = int(buy_trade["volume"][0])

        entry_qfq_price = float(quotes_df.filter(pl.col("trade_date") == date(2024, 1, 2)).select("qfq_open").item())
        qfq_cost_basis = volume * entry_qfq_price

        day2_pos = positions.filter(pl.col("trade_date") == date(2024, 1, 3))
        assert not day2_pos.is_empty()
        pos_detail = day2_pos["positions"][0]
        stock_pos = pos_detail["000001.SZ"]

        qfq_market_value = volume * 10.2
        expected_pnl = qfq_market_value - qfq_cost_basis

        assert stock_pos["pnl"] == pytest.approx(expected_pnl, rel=1e-4), (
            f"PnL should use qfq_cost_basis. "
            f"Got pnl={stock_pos['pnl']}, expected={expected_pnl}, "
            f"qfq_cost_basis={qfq_cost_basis}, qfq_market_value={qfq_market_value}"
        )


class TestICCalculationWithRebalanceFreq:
    """IC 计算与调仓频率对齐的专项测试"""

    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 28),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_ic_with_daily_freq_uses_next_day_return(self) -> None:
        """测试 daily 频率下 IC 使用次日收益。

        F3-03: IC 计算需 >=3 只股票（Spearman 相关性要求），使用 3 只股票。
        daily 频率：next_rebalance_date = execution_date 的下一日。
        """
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
        ]
        codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)] * 3,
                "execution_date": [date(2024, 1, 3)] * 3,
                "ts_code": codes,
                "signal_rank": [1, 2, 3],
            }
        )
        # 3 只股票 × 3 个交易日 = 9 行
        quotes_df = pl.DataFrame(
            {
                "ts_code": codes * 3,
                "trade_date": [d for d in trade_dates for _ in range(3)],
                "raw_open": [10.0, 20.0, 30.0, 10.0, 20.0, 30.0, 10.5, 20.8, 31.0],
                "raw_close": [10.0, 20.0, 30.0, 10.2, 20.4, 30.6, 10.8, 21.2, 31.5],
                "qfq_open": [10.0, 20.0, 30.0, 10.0, 20.0, 30.0, 10.5, 20.8, 31.0],
                "qfq_close": [10.0, 20.0, 30.0, 10.2, 20.4, 30.6, 10.8, 21.2, 31.5],
            }
        )
        ic_series, ic_dates = engine._calc_ic_series(signals, quotes_df, trade_dates)
        # 仅 signal_date=1/2 有信号 → 1 个 IC；signal_date=1/3 无信号 → 跳过
        assert len(ic_series) == 1
        assert not math.isnan(ic_series[0])

    def test_ic_with_weekly_freq_uses_weekly_return(self) -> None:
        """测试 weekly 频率下 IC 使用周度收益。

        场景：
        - 信号日：2024-01-08（周一）
        - 执行日：2024-01-09
        - 下一次调仓日：2024-01-15（下周一）
        - IC 应计算 01-09 open 到 01-15 open 的收益
        F3-03: 使用 3 只股票满足 IC 计算最低要求。
        """
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
            date(2024, 1, 11),
            date(2024, 1, 15),
            date(2024, 1, 16),
        ]
        codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 8)] * 3,
                "execution_date": [date(2024, 1, 9)] * 3,
                "ts_code": codes,
                "signal_rank": [1, 2, 3],
            }
        )
        # 3 只股票 × 6 个交易日 = 18 行
        quotes_df = pl.DataFrame(
            {
                "ts_code": codes * 6,
                "trade_date": [d for d in trade_dates for _ in range(3)],
                "raw_open": [
                    10.0,
                    20.0,
                    30.0,  # 1/8
                    10.0,
                    20.0,
                    30.0,  # 1/9 exec
                    10.2,
                    20.3,
                    30.4,  # 1/10
                    10.3,
                    20.4,
                    30.5,  # 1/11
                    10.5,
                    20.8,
                    31.0,  # 1/15 next rebalance
                    10.6,
                    20.9,
                    31.1,  # 1/16
                ],
                "raw_close": [
                    10.0,
                    20.0,
                    30.0,  # 1/8
                    10.1,
                    20.2,
                    30.3,  # 1/9
                    10.3,
                    20.4,
                    30.5,  # 1/10
                    10.4,
                    20.5,
                    30.6,  # 1/11
                    10.7,
                    21.0,
                    31.3,  # 1/15
                    10.8,
                    21.1,
                    31.4,  # 1/16
                ],
                "qfq_open": [
                    10.0,
                    20.0,
                    30.0,
                    10.0,
                    20.0,
                    30.0,
                    10.2,
                    20.3,
                    30.4,
                    10.3,
                    20.4,
                    30.5,
                    10.5,
                    20.8,
                    31.0,
                    10.6,
                    20.9,
                    31.1,
                ],
                "qfq_close": [
                    10.0,
                    20.0,
                    30.0,
                    10.1,
                    20.2,
                    30.3,
                    10.3,
                    20.4,
                    30.5,
                    10.4,
                    20.5,
                    30.6,
                    10.7,
                    21.0,
                    31.3,
                    10.8,
                    21.1,
                    31.4,
                ],
            }
        )
        ic_series, ic_dates = engine._calc_ic_series(signals, quotes_df, trade_dates)
        # 仅 signal_date=1/8 有信号 → 1 个 IC
        assert len(ic_series) == 1
        assert not math.isnan(ic_series[0])

    def test_ic_with_monthly_freq_uses_monthly_return(self) -> None:
        """测试 monthly 频率下 IC 使用月度收益。

        场景：
        - 信号日：2024-01-29
        - 执行日：2024-01-30
        - 下一次调仓日：2024-02-01（下月初）
        F3-03: 使用 3 只股票满足 IC 计算最低要求。
        """
        engine = self._make_engine(rebalance_freq="monthly")
        trade_dates = [
            date(2024, 1, 29),
            date(2024, 1, 30),
            date(2024, 1, 31),
            date(2024, 2, 1),
            date(2024, 2, 2),
        ]
        codes = ["000001.SZ", "000002.SZ", "000003.SZ"]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 29)] * 3,
                "execution_date": [date(2024, 1, 30)] * 3,
                "ts_code": codes,
                "signal_rank": [1, 2, 3],
            }
        )
        # 3 只股票 × 5 个交易日 = 15 行
        quotes_df = pl.DataFrame(
            {
                "ts_code": codes * 5,
                "trade_date": [d for d in trade_dates for _ in range(3)],
                "raw_open": [
                    10.0,
                    20.0,
                    30.0,  # 1/29
                    10.0,
                    20.0,
                    30.0,  # 1/30 exec
                    10.1,
                    20.1,
                    30.1,  # 1/31
                    10.5,
                    20.6,
                    30.7,  # 2/1 next rebalance
                    10.6,
                    20.7,
                    30.8,  # 2/2
                ],
                "raw_close": [
                    10.0,
                    20.0,
                    30.0,  # 1/29
                    10.1,
                    20.2,
                    30.2,  # 1/30
                    10.2,
                    20.3,
                    30.3,  # 1/31
                    10.7,
                    20.9,
                    31.0,  # 2/1
                    10.8,
                    21.0,
                    31.1,  # 2/2
                ],
                "qfq_open": [
                    10.0,
                    20.0,
                    30.0,
                    10.0,
                    20.0,
                    30.0,
                    10.1,
                    20.1,
                    30.1,
                    10.5,
                    20.6,
                    30.7,
                    10.6,
                    20.7,
                    30.8,
                ],
                "qfq_close": [
                    10.0,
                    20.0,
                    30.0,
                    10.1,
                    20.2,
                    30.2,
                    10.2,
                    20.3,
                    30.3,
                    10.7,
                    20.9,
                    31.0,
                    10.8,
                    21.0,
                    31.1,
                ],
            }
        )
        ic_series, ic_dates = engine._calc_ic_series(signals, quotes_df, trade_dates)
        # 仅 signal_date=1/29 有信号 → 1 个 IC
        assert len(ic_series) == 1
        assert not math.isnan(ic_series[0])

    def test_ic_returns_zero_for_insufficient_data(self) -> None:
        """测试数据不足时 IC 返回空序列。"""
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "signal_date": [],
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": trade_dates,
                "raw_open": [10.0, 10.5],
                "raw_close": [10.2, 10.7],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.2, 10.7],
            }
        )
        ic_series, ic_dates = engine._calc_ic_series(signals, quotes_df, trade_dates)
        assert len(ic_series) == 0


class TestDiffRebalance:
    """D4-4：差集调仓——只交易集合差与权重差，不做全清全买。"""

    def _make_engine(self, **kwargs) -> VectorBacktestEngine:
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig(slippage_bps=0.0))
        return engine

    @staticmethod
    def _quote(exec_date: date, price: float = 10.0) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [exec_date],
                "raw_open": [price],
                "raw_close": [price],
                "qfq_open": [price],
                "qfq_close": [price],
                "is_tradable": [True],
                "avg_daily_volume": [5_000_000.0],
            }
        )

    def test_dropped_position_sold_and_overlap_kept(self) -> None:
        """目标移除的持仓被清仓，重叠持仓不产生反向买卖。

        场景（weekly，平盘价无漂移）：
        - 1/8 再平衡：目标 {A, B} → 建仓 A、B
        - 1/15 再平衡：目标 {B} → A 被移除 → 卖出 A；B 保持
        """
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [date(2024, 1, 8), date(2024, 1, 15)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8), date(2024, 1, 15)],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [1, 1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 8), date(2024, 1, 8), date(2024, 1, 15), date(2024, 1, 15)],
                "raw_open": [10.0, 10.0, 10.0, 10.0],
                "raw_close": [10.0, 10.0, 10.0, 10.0],
                "qfq_open": [10.0, 10.0, 10.0, 10.0],
                "qfq_close": [10.0, 10.0, 10.0, 10.0],
                "is_tradable": [True, True, True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        sells = trades.filter(pl.col("action") == "sell") if not trades.is_empty() else pl.DataFrame()
        assert len(sells) == 1
        assert sells["ts_code"][0] == "000001.SZ"
        final_pos = positions.tail(1)["positions"][0]
        # 已清仓标的在持仓记录中被标记为 None（closed）
        assert final_pos["000001.SZ"] is None
        assert final_pos["000002.SZ"] is not None

    def test_new_position_bought_and_overlap_kept(self) -> None:
        """新增目标被买入，重叠持仓被减持但不清仓（不重建）。

        场景（weekly，平盘价无漂移）：
        - 1/8 再平衡：目标 {A} → 建仓 A
        - 1/15 再平衡：目标 {A, B} → B 新增 → 买入 B；A 减持但保留
        """
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [date(2024, 1, 8), date(2024, 1, 15)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8), date(2024, 1, 15), date(2024, 1, 15)],
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "signal_rank": [1, 1, 2],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 8), date(2024, 1, 8), date(2024, 1, 15), date(2024, 1, 15)],
                "raw_open": [10.0, 10.0, 10.0, 10.0],
                "raw_close": [10.0, 10.0, 10.0, 10.0],
                "qfq_open": [10.0, 10.0, 10.0, 10.0],
                "qfq_close": [10.0, 10.0, 10.0, 10.0],
                "is_tradable": [True, True, True, True],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        buys = trades.filter(pl.col("action") == "buy") if not trades.is_empty() else pl.DataFrame()
        assert len(buys) >= 1
        assert "000002.SZ" in set(buys["ts_code"].to_list())
        final_pos = positions.tail(1)["positions"][0]
        assert "000001.SZ" in final_pos
        assert "000002.SZ" in final_pos

    def test_partial_sell_amortizes_cost_basis_and_records_trade(self) -> None:
        """部分减持记录 sell 交易并按平均成本摊销剩余 cost_basis（A7/A9）。

        场景：持仓 1000 股@10（成本 10000），减持至目标市值 5000 → 卖出约一半。
        """
        sim, config = self._make_simulator(cash_reserve_pct=0.1)
        sim.cash = 0.0
        sim.positions["000001.SZ"] = {
            "volume": 1000,
            "cost_basis": 10000.0,
            "entry_date": date(2024, 1, 1),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        quote = self._quote(date(2024, 1, 2), price=10.0)
        sim._sell_position_to_value(date(2024, 1, 2), "000001.SZ", quote, target_value=5000.0)

        sells = pl.DataFrame(sim.trades_list).filter(pl.col("action") == "sell") if sim.trades_list else pl.DataFrame()
        assert len(sells) == 1
        assert sells["volume"][0] == 500
        pos = sim.positions["000001.SZ"]
        assert pos["volume"] == 500
        # 平均成本摊销：成本按卖出比例减半
        assert pos["cost_basis"] == pytest.approx(5000.0, abs=1e-6)
        assert sim.cash == pytest.approx(float(sells["net_amount"][0]), abs=1e-6)

    def _make_simulator(self, **kwargs) -> tuple[PortfolioSimulator, BacktestConfig]:
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        simulator = PortfolioSimulator(
            config,
            TransactionCostModel(TransactionCostConfig(slippage_bps=0.0)),
        )
        return simulator, config

    @staticmethod
    def _add_pos(sim: PortfolioSimulator, ts_code: str, volume: int, price: float) -> None:
        sim.positions[ts_code] = {
            "volume": volume,
            "cost_basis": float(volume * price),
            "entry_date": date(2024, 1, 1),
            "entry_price": price,
            "qfq_entry_price": price,
        }

    def test_reset_restores_default_state(self) -> None:
        """reset() 清空交易/持仓/现金回初始资本（覆盖 51-57）。

        用于引擎可能复用一个模拟器实例的路径，保证状态可重建。
        """
        sim, config = self._make_simulator()
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        sim.cash = 50.0
        sim.trades_list.append({"action": "sell"})
        sim.skipped_list.append({"reason": "no_quote"})
        sim.positions_list.append({"trade_date": date(2024, 1, 1)})
        sim.warnings.append("warn")
        sim._last_known_prices["000001.SZ"] = 10.0

        sim.reset()

        assert sim.cash == config.initial_capital
        assert sim.positions == {}
        assert sim.trades_list == []
        assert sim.skipped_list == []
        assert sim.positions_list == []
        assert sim.warnings == []
        assert sim._last_known_prices == {}

    def test_rebalance_with_invalid_weights_sells_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """权重计算无效（空权重）时按全清仓处理，不买入任何新仓（覆盖 100-101）。

        语义：无法确定目标权重属于数据异常，选择 on_empty_signal="liquidate"
        时保守全清仓；该分支与空信号分支共用 _handle_empty_signal。
        """
        sim, _config = self._make_simulator(on_empty_signal="liquidate")
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        sim.cash = 0.0

        from strategies.backtest import position_sizer as _ps

        class _EmptySizer:
            def compute_weights(self, signals, quotes, config):
                return pl.DataFrame(schema={"ts_code": pl.Utf8, "weight": pl.Float64})

        monkeypatch.setattr(_ps, "get_sizer", lambda sizing: _EmptySizer())

        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8)],
                "ts_code": ["000002.SZ"],
                "signal_rank": [1],
            }
        )
        sim._rebalance_diff(date(2024, 1, 8), signals, self._quote(date(2024, 1, 8)))

        assert "000001.SZ" in [t["ts_code"] for t in sim.trades_list if t["action"] == "sell"]
        assert "000002.SZ" not in sim.positions

    def test_empty_signal_hold_keeps_positions(self) -> None:
        """D1-M5：on_empty_signal="hold"（默认）时空信号再平衡日保留持仓、不产生交易。

        场景：周度再平衡，1/8 建仓后，1/15 当日信号为空 → 保持现有持仓，
        卖出表为空、持仓仍存在，warnings 记录「无信号 → hold」。
        """
        sim, _config = self._make_simulator(rebalance_freq="weekly", on_empty_signal="hold")
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        sim.cash = 10000.0
        empty_signals = pl.DataFrame(
            {
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )
        sim._rebalance_diff(date(2024, 1, 15), empty_signals, self._quote(date(2024, 1, 15)))

        assert "000001.SZ" in sim.positions
        assert not [t for t in sim.trades_list if t["action"] == "sell"]
        assert any("no signal → hold" in w for w in sim.warnings)

    def test_empty_signal_liquidate_sells_all(self) -> None:
        """D1-M5：on_empty_signal="liquidate" 时空信号再平衡日全清仓。

        语义：显式选择「全部清仓」时沿用旧行为——清空所有持仓并记录警告。
        """
        sim, _config = self._make_simulator(rebalance_freq="weekly", on_empty_signal="liquidate")
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        sim.cash = 10000.0
        empty_signals = pl.DataFrame(
            {
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )
        sim._rebalance_diff(date(2024, 1, 15), empty_signals, self._quote(date(2024, 1, 15)))

        assert "000001.SZ" not in sim.positions
        assert [t for t in sim.trades_list if t["action"] == "sell"]
        assert any("no signal → liquidate" in w for w in sim.warnings)

    def test_config_validate_rejects_bad_on_empty_signal(self) -> None:
        """D1-M5：on_empty_signal 取值非法时 validate() 报错。"""
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            on_empty_signal="sell_all",  # type: ignore[arg-type]
        )
        errors = config.validate()
        assert any("on_empty_signal" in e for e in errors)

    def test_rebalance_uses_entry_price_when_no_quote_for_held_position(self) -> None:
        """已持仓标的当日无报价时用 entry_price 兜底估算市值（覆盖 115）。

        场景：B 在目标内但当日行情缺失，不影响再平衡计算（用成本兜底）。
        """
        sim, config = self._make_simulator(rebalance_freq="weekly", min_rebalance_delta_pct=0.001)
        sim.cash = 50000.0
        self._add_pos(sim, "000002.SZ", 1000, 10.0)
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8), date(2024, 1, 8)],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [1, 2],
            }
        )
        # 仅 000001.SZ 有报价（000002.SZ 缺失）
        quotes = self._quote(date(2024, 1, 8))
        sim.stock_meta = {"000002.SZ": {"delist_date": None}}
        sim.process_day(date(2024, 1, 8), signals, quotes, is_rebalance=True)
        # 不应抛异常；000002.SZ 以 entry_price 兜底参与市值，不被误清仓
        assert "000002.SZ" in sim.positions

    def test_sell_position_no_quote_and_not_delisted_skips(self) -> None:
        """卖出时无报价且未退市 → 记 no_quote 跳过（覆盖 202-212）。"""
        sim, _config = self._make_simulator()
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        sim.stock_meta = {"000001.SZ": {"delist_date": None}}
        sim._sell_position(date(2024, 1, 8), "000001.SZ", sim.positions["000001.SZ"], quote=None)
        assert any(r["reason"] == "no_quote" for r in sim.skipped_list)
        assert "000001.SZ" in sim.positions
        assert any("sell skipped (no_quote)" in w for w in sim.warnings)

    def test_sell_position_to_value_no_quote_and_not_delisted_skips(self) -> None:
        """减持时无报价且未退市 → 记 no_quote 跳过（覆盖 285-298）。"""
        sim, _config = self._make_simulator()
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        sim.stock_meta = {"000001.SZ": {"delist_date": None}}
        sim._sell_position_to_value(date(2024, 1, 8), "000001.SZ", None, target_value=5000.0)
        assert any(r["reason"] == "no_quote" for r in sim.skipped_list)
        assert "000001.SZ" in sim.positions

    def test_sell_position_suspended_skips(self) -> None:
        """卖出遇停牌 → 记 suspended 跳过（覆盖 202-212 的 suspended 分支）。"""
        sim, _config = self._make_simulator()
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        q = self._quote(date(2024, 1, 8), 10.0).with_columns(pl.lit(False).alias("is_tradable"))
        sim._sell_position(date(2024, 1, 8), "000001.SZ", sim.positions["000001.SZ"], q)
        assert any(r["reason"] == "suspended" for r in sim.skipped_list)
        assert "000001.SZ" in sim.positions

    def test_sell_position_to_value_suspended_skips(self) -> None:
        """减持遇停牌 → 记 suspended 跳过（覆盖 302-312）。"""
        sim, _config = self._make_simulator()
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        q = self._quote(date(2024, 1, 8), 10.0).with_columns(pl.lit(False).alias("is_tradable"))
        sim._sell_position_to_value(date(2024, 1, 8), "000001.SZ", q, target_value=5000.0)
        assert any(r["reason"] == "suspended" for r in sim.skipped_list)
        assert "000001.SZ" in sim.positions

    def test_sell_position_to_value_next_close(self) -> None:
        """减持用 next_close 成交价（覆盖 329）并正确摊销成本。"""
        sim, _config = self._make_simulator(execution_price="next_close")
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        q = self._quote(date(2024, 1, 8), price=10.0)
        sim._sell_position_to_value(date(2024, 1, 8), "000001.SZ", q, target_value=5000.0)
        sells = [t for t in sim.trades_list if t["action"] == "sell"]
        assert len(sells) == 1
        assert sim.positions["000001.SZ"]["volume"] < 1000
        assert sim.positions["000001.SZ"]["cost_basis"] == pytest.approx(5000.0, abs=1e-6)

    def test_sell_position_to_value_full_sell_fallback(self) -> None:
        """减持量无效（>= 现持仓）→ 复用全额清仓（覆盖 337-339）。"""
        sim, _config = self._make_simulator()
        self._add_pos(sim, "000001.SZ", 1000, 10.0)
        q = self._quote(date(2024, 1, 8), price=10.0)
        # target_value 为 0 → sell_value = current_value → volume >= pos.volume → 全额清仓
        sim._sell_position_to_value(date(2024, 1, 8), "000001.SZ", q, target_value=0.0)
        assert "000001.SZ" not in sim.positions
        sells = [t for t in sim.trades_list if t["action"] == "sell"]
        assert len(sells) == 1
        assert sells[0]["volume"] == 1000

    def test_buy_to_target_scales_when_exceeds_budget(self) -> None:
        """买入总额超预算时按比例缩减（覆盖 386-390）。"""
        sim, _config = self._make_simulator()
        sim.cash = 5000.0
        targets = {"000001.SZ": 3000.0, "000002.SZ": 3000.0}
        base = self._quote(date(2024, 1, 8), 10.0)
        q1 = base
        q2 = base.with_columns(pl.lit("000002.SZ").alias("ts_code"))
        quotes_by_code = {"000001.SZ": q1, "000002.SZ": q2}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=4000.0)
        buys = [t for t in sim.trades_list if t["action"] == "buy"]
        assert len(buys) == 2
        # 缩减后总买入额不超过 budget
        total_amount = sum(t["gross_amount"] for t in buys)
        assert total_amount <= 4000.0 + 1e-6

    def test_buy_to_target_insufficient_cash_skips(self) -> None:
        """买入所需现金不足 → 记 insufficient_cash 跳过（覆盖 cash 边界分支）。

        构造 volume>0 但 net_amount 超出 sim.cash 的场景（价格 10 元足够 100 股整手，
        目标 10 万元 → volume=10000 股，成本 10 万 > cash 100 元）。
        """
        sim, _config = self._make_simulator()
        sim.cash = 100.0
        targets = {"000001.SZ": 100000.0}
        quotes_by_code = {"000001.SZ": self._quote(date(2024, 1, 8), 10.0)}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=100000.0)
        # 10 元/股 × 10000 股 → net_amount 远超现金 → skipped
        assert any(r["reason"] == "insufficient_cash" for r in sim.skipped_list)
        assert "000001.SZ" not in sim.positions

    def test_buy_to_target_adds_to_existing_position(self) -> None:
        """加仓已存在持仓 → 累加 volume/cost_basis（覆盖 506-519 existing 分支）。"""
        sim, _config = self._make_simulator()
        sim.cash = 50000.0
        self._add_pos(sim, "000001.SZ", 100, 10.0)
        targets = {"000001.SZ": 10000.0}
        quotes_by_code = {"000001.SZ": self._quote(date(2024, 1, 8), 10.0)}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=10000.0)
        assert sim.positions["000001.SZ"]["volume"] > 100
        assert sim.positions["000001.SZ"]["cost_basis"] > 1000.0

    def test_buy_to_target_lot_size_indivisible_attribution(self) -> None:
        """BT-05: 单笔预算不足一手 → 归因 lot_size_indivisible（非 insufficient_cash），
        并带 target_value / min_lot_cost 供用户判断差多少。"""
        sim, _config = self._make_simulator()
        sim.cash = 500000.0
        # 股价 10000 元/手=100 万，单笔目标 1.8 万不足一手
        targets = {"000001.SZ": 18000.0}
        quotes_by_code = {"000001.SZ": self._quote(date(2024, 1, 8), 10000.0)}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=18000.0)
        assert len(sim.skipped_list) == 1
        skip = sim.skipped_list[0]
        assert skip["reason"] == "lot_size_indivisible"
        assert skip["target_value"] == pytest.approx(18000.0)
        assert skip["min_lot_cost"] == pytest.approx(10000.0 * 100)
        assert "000001.SZ" not in sim.positions

    def test_buy_to_target_insufficient_cash_keeps_attribution_for_real_cash_shortfall(self) -> None:
        """BT-05: 真实现金不足（volume>0 但 net_amount>cash）仍归因 insufficient_cash，
        与 lot_size_indivisible 区分开。"""
        sim, _config = self._make_simulator()
        sim.cash = 100.0
        # 10000 元/股，目标 1000 万股 → volume>0，但 net_amount 远超现金
        targets = {"000001.SZ": 100000.0}
        quotes_by_code = {"000001.SZ": self._quote(date(2024, 1, 8), 10.0)}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=100000.0)
        assert any(r["reason"] == "insufficient_cash" for r in sim.skipped_list)
        assert not any(r["reason"] == "lot_size_indivisible" for r in sim.skipped_list)

    def test_buy_to_target_reallocate_unfilled_closes_gap(self) -> None:
        """BT-05: reallocate_unfilled=True 时，高价股释放的预算按信号强度降序补分配到
        信号靠前但未满额的标的；默认 False 时行为不变（不产生补分配）。"""
        sim, config = self._make_simulator(reallocate_unfilled=True)
        sim.cash = 100000.0
        # 高价股 A（信号 rank 2，较弱）买不起一手；低价股 B（信号 rank 1，较强）已部分买入。
        # 设置 targets 使 A 释放预算，B 的缺口可被补足。
        high_price = 500.0  # A 一手成本 5 万
        low_price = 10.0  # B 一手成本 1 千
        targets = {"000001.SZ": 30000.0, "000002.SZ": 30000.0}
        q_high = self._quote(date(2024, 1, 8), high_price)
        q_low = self._quote(date(2024, 1, 8), low_price).with_columns(pl.lit("000002.SZ").alias("ts_code"))
        quotes_by_code = {"000001.SZ": q_high, "000002.SZ": q_low}
        signal_order = ["000002.SZ", "000001.SZ"]  # B 信号强
        sim._buy_to_target(
            date(2024, 1, 8),
            targets,
            quotes_by_code,
            budget=60000.0,
            signal_order=signal_order,
        )
        # A 买不起一手 → lot_size_indivisible 跳过
        a_skips = [r for r in sim.skipped_list if r["ts_code"] == "000001.SZ"]
        assert len(a_skips) == 1
        assert a_skips[0]["reason"] == "lot_size_indivisible"
        # B 第一遍满额（3 万目标，10 元/股）→ A 释放的预算无处可补（无缺口），
        # 但不会产生第二笔 B 买入（未超目标金额）
        b_trades = [t for t in sim.trades_list if t["ts_code"] == "000002.SZ"]
        assert len(b_trades) == 1
        # 补分配在 B 有缺口时才执行——B 已满额，故无 reallocate warning
        assert not any("reallocate_unfilled" in w for w in sim.warnings)

    def test_buy_to_target_reallocate_unfilled_fills_insufficient_target(self) -> None:
        """BT-05: 补分配的缺口续投——低价标的现金不足未满额、高价标释放预算时，
        释放预算被续投到信号更强的标的（B），且不突破目标金额。"""
        sim, _config = self._make_simulator(reallocate_unfilled=True)
        # B 目标 5 万但现金仅 4.5 万（第一遍 B 现金不足被跳过，产生 5 万缺口）；
        # A 高价（500 元/股）目标 3 万不足一手 → 释放 3 万预算。
        sim.cash = 45000.0
        low_price = 10.0
        high_price = 500.0
        targets = {"000001.SZ": 30000.0, "000002.SZ": 50000.0}
        q_high = self._quote(date(2024, 1, 8), high_price)
        q_low = self._quote(date(2024, 1, 8), low_price).with_columns(pl.lit("000002.SZ").alias("ts_code"))
        quotes_by_code = {"000001.SZ": q_high, "000002.SZ": q_low}
        signal_order = ["000002.SZ", "000001.SZ"]  # B 信号强
        sim._buy_to_target(
            date(2024, 1, 8),
            targets,
            quotes_by_code,
            budget=80000.0,  # 不触发等比缩放（total_target 8 万 == budget）
            signal_order=signal_order,
        )
        # A 高价股 lot_size_indivisible 跳过
        assert any(s["ts_code"] == "000001.SZ" and s["reason"] == "lot_size_indivisible" for s in sim.skipped_list)
        # 补分配把 A 释放的预算续投给 B（第一遍 B 现金不足被跳过，未满额）
        b_trades = [t for t in sim.trades_list if t["ts_code"] == "000002.SZ"]
        assert len(b_trades) == 1
        b_spent_cash = sum(t["net_amount"] for t in b_trades)
        assert b_spent_cash <= 50000.0 + 1e-6  # 不突破 B 的目标金额
        assert sim.cash >= -1e-6
        # 补分配确实发生（B 来自 reallocate）
        assert any("reallocate_unfilled" in w for w in sim.warnings)

    def test_buy_to_target_lot_indivisible_over_threshold_hints(self) -> None:
        """BT-05: lot_size_indivisible 笔数超过候选 10% 时追加可执行建议（调参方向）。"""
        sim, _config = self._make_simulator()
        sim.cash = 1_000_000.0
        high_price = 200.0  # 一手 2 万
        targets = {"000001.SZ": 18000.0, "000002.SZ": 18000.0, "000003.SZ": 18000.0}
        q1 = self._quote(date(2024, 1, 8), high_price)
        q2 = self._quote(date(2024, 1, 8), high_price).with_columns(pl.lit("000002.SZ").alias("ts_code"))
        q3 = self._quote(date(2024, 1, 8), 10.0).with_columns(pl.lit("000003.SZ").alias("ts_code"))
        quotes_by_code = {"000001.SZ": q1, "000002.SZ": q2, "000003.SZ": q3}
        sim._buy_to_target(
            date(2024, 1, 8),
            targets,
            quotes_by_code,
            budget=sum(targets.values()),
        )
        # 2/3 候选（>10%）被 lot_size_indivisible 跳过 → 触发可执行建议
        assert any("candidates skipped (lot_size_indivisible)" in w for w in sim.warnings)
        assert any("max_position_count" in w for w in sim.warnings)
        # 提示不影响成交（低价股仍买入）
        assert any(t["ts_code"] == "000003.SZ" for t in sim.trades_list)

    def test_rebalance_buy_budget_respects_cash_when_insufficient(self) -> None:
        """item1：买入预算受真实可用现金钳制（触发等比缩放分支）。

        构造 investable 目标超过手头 cash 的场景，验证 budget 取
        min(investable, cash) 后，多标的买单被等比缩放到现金内，
        买入总额不超 cash，且现金不出现负值。此用例走通「总额超预算」
        缩放分支，确保预算语义确实落到 _buy_to_target 的缩放逻辑上。
        """
        sim, _config = self._make_simulator()
        sim.cash = 5000.0
        # 各标的目标金额远大于预算 → 触发等比缩放（scale=4000/6000≈0.667）
        targets = {"000001.SZ": 2000.0, "000002.SZ": 2000.0, "000003.SZ": 2000.0}
        base = self._quote(date(2024, 1, 8), 10.0)
        q1 = base
        q2 = base.with_columns(pl.lit("000002.SZ").alias("ts_code"))
        q3 = base.with_columns(pl.lit("000003.SZ").alias("ts_code"))
        quotes_by_code = {"000001.SZ": q1, "000002.SZ": q2, "000003.SZ": q3}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=4000.0)
        buys = [t for t in sim.trades_list if t["action"] == "buy"]
        total_buy = sum(t["net_amount"] for t in buys)
        # 缩放后买入总额不超过预算（4000）
        assert total_buy <= 4000.0 + 1e-6
        assert sim.cash >= -1e-6

    def test_rebalance_buy_order_independent_of_dict_insertion(self) -> None:
        """item1：现金不足（逐单现金边界截断）时，成交取舍与 dict 插入顺序无关。

        _buy_to_target 直测：预算充足但现金不足——缩放不足以让全部买单成交，
        逐单按 net_amount 累计消耗 cash，最后一个标的会因 cash 不足被跳过。
        该「谁被跳过」不应依赖调用方传入 buy_targets 的插入顺序（已按 ts_code 排序）。
        断言：以正序/乱序两种插入顺序传入，最终成交的标的集合与 volume 完全一致。
        """

        def run(order: dict[str, float]) -> tuple[frozenset, tuple]:
            sim, _ = self._make_simulator()
            # 预算充足（不触发缩放），但现金只够前两笔
            sim.cash = 2500.0
            base = self._quote(date(2024, 1, 8), 10.0)
            quotes_by_code = {c: base.with_columns(pl.lit(c).alias("ts_code")) for c in order}
            sim._buy_to_target(date(2024, 1, 8), order, quotes_by_code, budget=1_000_000.0)
            buys = [t for t in sim.trades_list if t["action"] == "buy"]
            held = frozenset(v["ts_code"] for v in buys)
            vols = tuple(sorted((v["ts_code"], v["volume"]) for v in buys))
            return held, vols

        # 每只目标 2000 元/10元 → 200 股（2手）；现金 2500 只够买 1 笔整手 + 部分次笔，
        # 落在「整手+残差」的现金边界处 → 最后一笔因现金不足被跳过
        a = {"000001.SZ": 2000.0, "000002.SZ": 2000.0, "000003.SZ": 2000.0}
        b = dict(reversed(a.items()))
        held_a, vols_a = run(a)
        held_b, vols_b = run(b)
        assert held_a == held_b
        assert vols_a == vols_b

    def test_buy_to_target_eliminates_low_target_when_cash_exhausted(self) -> None:
        """BT-06: 兜底遍历按「目标金额降序」——现金耗尽时淘汰目标金额最小而非
        ts_code 字母序靠后的标的（消除上海/科创板/北交所被系统性先淘汰的板块偏好）。

        构造：高目标(5000)的 600000.SH（字母序靠后）先买，低目标(1000)的
        000001.SZ（字母序靠前）在现金耗尽后成为被淘汰者。
        若仍按旧 ts_code 序，被淘汰的将是 600000.SH（同额排序下字母序靠后者）。
        """
        sim, _config = self._make_simulator()
        sim.cash = 5500.0  # 只够高目标（约 5k）整单买入
        targets = {"000001.SZ": 1000.0, "600000.SH": 5000.0}
        q_sz = self._quote(date(2024, 1, 8), 10.0)
        q_sh = self._quote(date(2024, 1, 8), 10.0).with_columns(pl.lit("600000.SH").alias("ts_code"))
        quotes_by_code = {"000001.SZ": q_sz, "600000.SH": q_sh}
        sim._buy_to_target(date(2024, 1, 8), targets, quotes_by_code, budget=6000.0)

        # 高目标优先成交（目标金额降序），低目标因现金不足被淘汰
        held = {t["ts_code"] for t in sim.trades_list if t["action"] == "buy"}
        assert held == {"600000.SH"}
        skipped = [r for r in sim.skipped_list if r["direction"] == "buy"]
        assert any(r["ts_code"] == "000001.SZ" and r["reason"] == "insufficient_cash" for r in skipped)
        assert not any(r["ts_code"] == "600000.SH" and r["reason"] == "insufficient_cash" for r in skipped)

    def test_buy_to_target_elimination_deterministic_across_dict_order(self) -> None:
        """BT-06: 目标金额降序兜底仍是确定性排序——传入 dict 插入顺序不影响淘汰集合。"""
        targets_a = {"000001.SZ": 1000.0, "600000.SH": 5000.0}
        targets_b = dict(reversed(targets_a.items()))

        outcome = []
        for targets in (targets_a, targets_b):
            sim, _config = self._make_simulator()
            sim.cash = 5500.0
            q_sz = self._quote(date(2024, 1, 8), 10.0)
            q_sh = self._quote(date(2024, 1, 8), 10.0).with_columns(pl.lit("600000.SH").alias("ts_code"))
            sim._buy_to_target(date(2024, 1, 8), targets, {"000001.SZ": q_sz, "600000.SH": q_sh}, budget=6000.0)
            held = frozenset(t["ts_code"] for t in sim.trades_list if t["action"] == "buy")
            skipped = frozenset(r["ts_code"] for r in sim.skipped_list if r["direction"] == "buy")
            outcome.append((held, skipped))

        assert outcome[0] == outcome[1]

    def test_rebalance_cash_ratio_respects_reserve_floor(self) -> None:
        """现金预留语义：先扣预留，再在可投资金内分配；实际现金比例 >= 预留比例（D4-10）。

        零成本模型下，investable = total_assets * (1 - cash_reserve_pct)；
        整手取整产生的余数计入现金，故回测后现金占比不低于 cash_reserve_pct。
        非整手价格（10.5）确保取整余数落入现金，精确验证下界。
        """
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            cash_reserve_pct=0.3,
        )
        sim = PortfolioSimulator(
            config,
            TransactionCostModel(
                TransactionCostConfig(
                    commission_rate=0.0,
                    commission_min=0.0,
                    stamp_duty_rate=0.0,
                    transfer_fee_rate=0.0,
                    slippage_bps=0.0,
                )
            ),
        )
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        sim._rebalance_diff(date(2024, 1, 8), signals, self._quote(date(2024, 1, 8), price=10.5))

        total_assets = sim.cash + sum(p["volume"] * p["qfq_entry_price"] for p in sim.positions.values())
        assert total_assets == pytest.approx(config.initial_capital, abs=1e-6)
        # 整手取整余数累积，实际现金占比不低于预留比例
        assert sim.cash / total_assets >= config.cash_reserve_pct - 1e-9
        # 且预留比例被真正扣除（investable 已按 1 - reserve 缩小，现金不可能是满仓结算）
        assert sim.cash / total_assets < 1.0 - 0.01

    def test_sparse_signal_cap_appends_warning(self) -> None:
        """D1-M3：候选数 N < 1/max_single_weight 时向 warnings 注入仓位上限告警。"""
        sim, _config = self._make_simulator(rebalance_freq="weekly", max_single_weight=0.1)
        sim.cash = 100000.0
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8)] * 3,
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "signal_rank": [3, 2, 1],
            }
        )
        quotes = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "trade_date": [date(2024, 1, 8)] * 3,
                "raw_open": [10.0] * 3,
                "raw_close": [10.0] * 3,
                "qfq_open": [10.0] * 3,
                "qfq_close": [10.0] * 3,
                "is_tradable": [True] * 3,
                "avg_daily_volume": [5_000_000.0] * 3,
            }
        )
        sim._rebalance_diff(date(2024, 1, 8), signals, quotes)

        assert any("sparse signals" in w for w in sim.warnings)
        assert any("effective position ceiling" in w for w in sim.warnings)


class TestRebalanceValuationExecPrice:
    """BT-01：_rebalance_diff 估值口径与 execution_price 对齐（防未来函数/系统性失真）。

    制作 bug 场景：持有标的 A 当日 qfq_open=10、qfq_close=20（日内 +100%）。
    rank_weighted 下 A 目标权重 = rank_A / (rank_A + rank_B) = 2/3。
    - next_open（默认）时成交按 open，估值也必须按 open 计算当前市值：
      修复前硬编码 qfq_close(20) → 高估 A → 误判超配而减持；
      修复后用 qfq_open(10) → A 目标权重高于现值 → 不减持。
    """

    def _make_simulator(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        return PortfolioSimulator(
            config,
            TransactionCostModel(TransactionCostConfig(slippage_bps=0.0)),
        ), config

    def _rebalance_rank_weighted(self, execution_price: str) -> PortfolioSimulator:
        """构造 rank_weighted 再平衡：A 持仓(open10/close20)，B 无仓(10/10)。"""
        sim, _ = self._make_simulator(
            position_sizing="rank_weighted",
            min_rebalance_delta_pct=0.001,
            cash_reserve_pct=0.0,
            max_single_weight=0.9,
            execution_price=execution_price,
        )
        sim.cash = 7000.0
        sim.positions["000001.SZ"] = {
            "volume": 1000,
            "cost_basis": 10000.0,
            "entry_date": date(2024, 1, 1),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8), date(2024, 1, 8)],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [2, 1],
            }
        )
        quotes = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 8), date(2024, 1, 8)],
                "raw_open": [10.0, 10.0],
                "raw_close": [20.0, 10.0],
                "qfq_open": [10.0, 10.0],
                "qfq_close": [20.0, 10.0],
                "is_tradable": [True, True],
                "avg_daily_volume": [5_000_000.0, 5_000_000.0],
            }
        )
        sim._rebalance_diff(date(2024, 1, 8), signals, quotes)
        return sim

    def test_rebalance_values_holding_by_open_when_next_open(self) -> None:
        """BT-01 核心：next_open 时估值按 qfq_open，日内上涨股不被误判超配而减配。

        修复前估值用 qfq_close(20) → current_value=20000 > target_A=18000 → 误触发减持；
        修复后估值用 qfq_open(10) → current_value=10000 < target_A=(2/3)*17000 → 保持不卖。
        """
        sim = self._rebalance_rank_weighted(execution_price="next_open")
        a_sells = [t for t in sim.trades_list if t["action"] == "sell" and t["ts_code"] == "000001.SZ"]
        assert a_sells == [], f"A 被误以为超配而减持(应保持)：{a_sells}"

    def test_rebalance_values_holding_by_close_when_next_close(self) -> None:
        """BT-01 防回归：next_close 时估值仍用 qfq_close（与执行价一致）。

        next_close 下成交在 close(20)，估值用 close 使 A 的目标(2/3)低于现值 → 减持到目标。
        断言 A 发生部分减持；若误用 open(10) 估值则 A 目标高于现值 → 不会减持 → 测试失败。
        """
        sim = self._rebalance_rank_weighted(execution_price="next_close")
        a_sells = [t for t in sim.trades_list if t["action"] == "sell" and t["ts_code"] == "000001.SZ"]
        assert len(a_sells) == 1, f"A 应依 next_close 减持到目标：{a_sells}"
        assert a_sells[0]["volume"] == 100  # 部分减持 100 股（从 1000 减到目标市值 18000/20=900 股）
