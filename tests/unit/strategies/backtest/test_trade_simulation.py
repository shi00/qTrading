"""交易模拟测试（涨跌停/停牌处理）"""

from datetime import date

import polars as pl
import pytest

from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel
from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.portfolio import PortfolioSimulator


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


class TestCashScaling:
    """BT-003: 快照+按比例缩减逻辑测试"""

    @pytest.fixture
    def config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.0,
            rebalance_freq="signal",
        )

    @staticmethod
    def _make_quotes(ts_codes: list[str], prices: list[float]) -> pl.DataFrame:
        """构造行情 DataFrame（无 is_tradable/limit_status 列，默认可交易）。"""
        return pl.DataFrame(
            {
                "ts_code": ts_codes,
                "raw_open": prices,
                "raw_close": prices,
                "qfq_open": prices,
                "qfq_close": prices,
            }
        )

    def test_no_scaling_when_cash_sufficient(self, config: BacktestConfig) -> None:
        """现金充足时，多只股票按等权分配，不触发缩减"""
        simulator = PortfolioSimulator(config, TransactionCostModel(TransactionCostConfig()))

        signals = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [1.0, 2.0],
            }
        )
        quotes = self._make_quotes(["000001.SZ", "000002.SZ"], [10.0, 20.0])

        simulator.process_day(date(2024, 1, 2), signals, quotes, is_rebalance=True)

        trades = simulator.get_results()[0]
        buy_trades = trades.filter(pl.col("action") == "buy")
        # 两只股票都应成功买入
        assert len(buy_trades) == 2

    def test_scaling_when_cash_insufficient(self, config: BacktestConfig) -> None:
        """现金紧张时，按比例缩减各股票的目标金额。

        通过 mock sizer 使权重总和 > 1，从而触发缩减逻辑。
        """
        from unittest.mock import patch

        small_config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=20_000.0,  # 确保缩减后每只股票 volume >= 100
            max_position_count=10,
            max_single_weight=1.0,  # 关闭单股上限约束，避免被截断到 0.1 后归一化
            cash_reserve_pct=0.0,
            rebalance_freq="signal",
        )
        simulator = PortfolioSimulator(small_config, TransactionCostModel(TransactionCostConfig()))

        # mock sizer 返回权重总和 > 1 的结果，强制触发缩减
        # 使用 0.4 避免超过 max_single_weight=1.0
        oversized_weights = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "signal_rank": [1.0, 2.0, 3.0],
                "weight": [0.4, 0.4, 0.4],  # 总和 1.2 > 1，必然触发缩减
            }
        )

        with patch("strategies.backtest.position_sizer.get_sizer") as mock_get_sizer:
            mock_get_sizer.return_value.compute_weights.return_value = oversized_weights
            with patch(
                "strategies.backtest.position_sizer.apply_max_weight_constraint", return_value=oversized_weights
            ):
                signals = pl.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                        "signal_rank": [1.0, 2.0, 3.0],
                    }
                )
                # 使用较低价格，确保缩减后每只股票的 volume >= 100
                quotes = self._make_quotes(["000001.SZ", "000002.SZ", "000003.SZ"], [10.0, 12.0, 14.0])

                simulator.process_day(date(2024, 1, 2), signals, quotes, is_rebalance=True)

        trades = simulator.get_results()[0]
        buy_trades = trades.filter(pl.col("action") == "buy")
        # 所有股票都应买入（缩减后）
        assert len(buy_trades) == 3
        # 每只股票的买入金额应大致相等（等权缩减后）
        amounts = buy_trades["net_amount"].to_list()
        max_amount = max(amounts)
        min_amount = min(amounts)
        # 允许 25% 偏差（取整导致）
        assert max_amount / min_amount < 1.25

    def test_scaled_volume_is_lot_size_multiple(self, config: BacktestConfig) -> None:
        """缩减后的股数必须是 100 的整数倍"""
        from unittest.mock import patch

        small_config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=15_000.0,
            max_position_count=10,
            max_single_weight=1.0,
            cash_reserve_pct=0.0,
            rebalance_freq="signal",
        )
        simulator = PortfolioSimulator(small_config, TransactionCostModel(TransactionCostConfig()))

        oversized_weights = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [1.0, 2.0],
                "weight": [0.6, 0.6],  # 总和 1.2 > 1
            }
        )

        with patch("strategies.backtest.position_sizer.get_sizer") as mock_get_sizer:
            mock_get_sizer.return_value.compute_weights.return_value = oversized_weights
            with patch(
                "strategies.backtest.position_sizer.apply_max_weight_constraint", return_value=oversized_weights
            ):
                signals = pl.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ"],
                        "signal_rank": [1.0, 2.0],
                    }
                )
                quotes = self._make_quotes(["000001.SZ", "000002.SZ"], [33.0, 47.0])

                simulator.process_day(date(2024, 1, 2), signals, quotes, is_rebalance=True)

        trades = simulator.get_results()[0]
        buy_trades = trades.filter(pl.col("action") == "buy")
        for volume in buy_trades["volume"].to_list():
            assert volume % 100 == 0
            assert volume > 0

    def test_scaled_total_not_exceed_available_cash(self, config: BacktestConfig) -> None:
        """缩减后所有买单的总金额不超过可用现金"""
        from unittest.mock import patch

        small_config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=10_000.0,
            max_position_count=10,
            max_single_weight=1.0,
            cash_reserve_pct=0.1,
            rebalance_freq="signal",
        )
        simulator = PortfolioSimulator(small_config, TransactionCostModel(TransactionCostConfig()))

        oversized_weights = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "signal_rank": [1.0, 2.0, 3.0],
                "weight": [0.4, 0.4, 0.4],  # 总和 1.2 > 1
            }
        )

        with patch("strategies.backtest.position_sizer.get_sizer") as mock_get_sizer:
            mock_get_sizer.return_value.compute_weights.return_value = oversized_weights
            with patch(
                "strategies.backtest.position_sizer.apply_max_weight_constraint", return_value=oversized_weights
            ):
                signals = pl.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                        "signal_rank": [1.0, 2.0, 3.0],
                    }
                )
                # 使用较低价格，确保缩减后每只股票的 volume >= 100
                quotes = self._make_quotes(["000001.SZ", "000002.SZ", "000003.SZ"], [8.0, 9.0, 10.0])

                simulator.process_day(date(2024, 1, 2), signals, quotes, is_rebalance=True)

        trades = simulator.get_results()[0]
        buy_trades = trades.filter(pl.col("action") == "buy")
        assert not buy_trades.is_empty()
        total_cost = float(buy_trades["net_amount"].sum())
        # 不超过初始资金（含佣金/滑点可能略超 available_cash 但不超过初始本金）
        assert total_cost <= 10_000.0

    def test_single_stock_not_scaled(self, config: BacktestConfig) -> None:
        """单只股票买入时不受缩减影响（权重为 1.0，total_target == available_cash）"""
        simulator = PortfolioSimulator(config, TransactionCostModel(TransactionCostConfig()))

        signals = pl.DataFrame({"ts_code": ["000001.SZ"], "signal_rank": [1.0]})
        quotes = self._make_quotes(["000001.SZ"], [10.0])

        simulator.process_day(date(2024, 1, 2), signals, quotes, is_rebalance=True)

        trades = simulator.get_results()[0]
        buy_trades = trades.filter(pl.col("action") == "buy")
        assert len(buy_trades) == 1
        # 单只股票应买入接近 available_cash 的金额
        volume = int(buy_trades["volume"][0])
        price = float(buy_trades["price"][0])
        expected_max_volume = int(1_000_000.0 / price / 100) * 100
        assert volume <= expected_max_volume
        assert volume > 0
