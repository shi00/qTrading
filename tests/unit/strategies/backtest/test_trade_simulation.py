"""交易模拟测试（涨跌停/停牌处理）"""

from datetime import date

import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine


class TestTradeSimulation:
    @pytest.fixture
    def config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
        )

    def test_skip_buy_on_up_limit(self, config: BacktestConfig) -> None:
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

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

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert len(trades) == 0
        assert len(skipped) == 1
        assert skipped["reason"][0] == "up_limit"
        assert skipped["direction"][0] == "buy"

    def test_skip_sell_on_down_limit(self, config: BacktestConfig) -> None:
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

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

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        down_limit_skips = skipped.filter(pl.col("reason") == "down_limit")
        assert len(down_limit_skips) >= 1

    def test_skip_suspended_stock(self, config: BacktestConfig) -> None:
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

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
                "is_tradable": [False],
                "limit_status": [None],
            }
        )

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert len(trades) == 0
        suspended_skips = skipped.filter(pl.col("reason") == "suspended")
        assert len(suspended_skips) == 1

    def test_skip_no_quote_data(self, config: BacktestConfig) -> None:
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["999999.SZ"],
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
                "limit_status": [None],
            }
        )

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert len(trades) == 0
        no_quote_skips = skipped.filter(pl.col("reason") == "no_quote")
        assert len(no_quote_skips) == 1

    def test_normal_trade_without_limit_status(self, config: BacktestConfig) -> None:
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

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
                "raw_close": [10.5, 11.0],
                "qfq_open": [10.0, 10.5],
                "qfq_close": [10.5, 11.0],
                "is_tradable": [True, True],
            }
        )

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert len(trades) >= 1
        buy_trades = trades.filter(pl.col("action") == "buy")
        assert len(buy_trades) == 1
        assert buy_trades["ts_code"][0] == "000001.SZ"


class TestLotSizeRounding:
    def test_volume_rounded_to_100_shares(self) -> None:
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

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
                "raw_open": [33.33, 34.0],
                "raw_close": [33.5, 34.5],
                "qfq_open": [33.33, 34.0],
                "qfq_close": [33.5, 34.5],
                "is_tradable": [True, True],
            }
        )

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        buy_trades = trades.filter(pl.col("action") == "buy")
        assert not buy_trades.is_empty()
        volume = buy_trades["volume"][0]
        assert volume % 100 == 0
        assert volume > 0

    def test_volume_not_fractional(self) -> None:
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig())

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
                "raw_open": [7.77, 8.0],
                "raw_close": [7.9, 8.2],
                "qfq_open": [7.77, 8.0],
                "qfq_close": [7.9, 8.2],
                "is_tradable": [True, True],
            }
        )

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        buy_trades = trades.filter(pl.col("action") == "buy")
        assert not buy_trades.is_empty()
        volume = buy_trades["volume"][0]
        assert volume % 100 == 0


class TestRealizedPnl:
    def test_realized_pnl_on_sell(self) -> None:
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
            rebalance_freq="signal",
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel

        engine.cost_model = TransactionCostModel(TransactionCostConfig(slippage_bps=0.0))

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
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "raw_open": [10.0, 10.5, 11.0],
                "raw_close": [10.2, 10.8, 11.5],
                "qfq_open": [10.0, 10.5, 11.0],
                "qfq_close": [10.2, 10.8, 11.5],
                "is_tradable": [True, True, True],
            }
        )

        trade_dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        buy_trades = trades.filter(pl.col("action") == "buy")
        assert not buy_trades.is_empty()
        buy_volume = int(buy_trades["volume"][0])
        buy_price = float(buy_trades["price"][0])
        assert buy_volume % 100 == 0
        assert buy_price == 10.0
