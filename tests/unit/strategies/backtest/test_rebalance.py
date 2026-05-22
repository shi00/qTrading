from datetime import date

import polars as pl
import pytest

from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel
from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine


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

    def test_daily_rebalance_sells_every_day(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "ts_code": ["000001.SZ", "000001.SZ"],
                "signal_rank": [1, 1],
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
        assert len(buy_trades) >= 2
        assert len(sell_trades) >= 1

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

    def test_weekly_rebalance_only_on_week_boundary(self):
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
            date(2024, 1, 15),
        ]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10), date(2024, 1, 15)],
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
        assert len(buy_trades) == 2

    def test_monthly_rebalance_only_on_month_boundary(self):
        engine = self._make_engine(rebalance_freq="monthly")
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
        assert len(buy_trades) == 2

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

    def test_market_value_uses_qfq_close_after_ex_dividend(self):
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
        buggy_raw_value = volume * 5.1
        assert market_value == pytest.approx(expected_qfq_value, rel=1e-4)
        assert market_value != pytest.approx(buggy_raw_value, rel=1e-2)


class TestExecutionPrice:
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
                "limit_status": ["up_limit"],
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
                "limit_status": ["up_limit"],
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
                "limit_status": [None, "down_limit", None],
            }
        )
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)
        down_limit_skips = (
            skipped.filter(pl.col("reason") == "down_limit") if not skipped.is_empty() else pl.DataFrame()
        )
        assert len(down_limit_skips) >= 1

    def test_allow_limit_down_sell(self):
        engine = self._make_engine(rebalance_freq="daily", allow_limit_down_sell=True)
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
                "limit_status": [None, "down_limit"],
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
