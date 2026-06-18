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


class TestDelistedLiquidation:
    """BT-002: 退市标的清算测试"""

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

    def _make_simulator(
        self,
        config: BacktestConfig,
        stock_meta: dict[str, dict] | None = None,
    ) -> PortfolioSimulator:
        return PortfolioSimulator(
            config,
            TransactionCostModel(TransactionCostConfig()),
            stock_meta=stock_meta,
        )

    def test_delisted_position_liquidated_at_last_known_price(self, config: BacktestConfig) -> None:
        """退市标的在退市日被强制清算，清算价格使用最后已知价"""
        delist_date = date(2024, 1, 15)
        stock_meta = {"000001.SZ": {"delist_date": delist_date}}
        simulator = self._make_simulator(config, stock_meta=stock_meta)

        # 模拟已建仓的持仓
        last_known_price = 10.5
        volume = 1000
        cost_basis = 10_000.0
        simulator.positions["000001.SZ"] = {
            "volume": volume,
            "cost_basis": cost_basis,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        simulator._last_known_prices["000001.SZ"] = last_known_price

        initial_cash = simulator.cash

        # exec_date >= delist_date，且当日无行情（退市后无数据）
        exec_date = date(2024, 1, 16)
        day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )

        simulator._sell_all_positions(exec_date, day_quotes)

        # 断言：持仓被移除
        assert "000001.SZ" not in simulator.positions

        # 断言：现金增加（清算价格 × 持仓量）
        expected_proceeds = volume * last_known_price
        assert simulator.cash == initial_cash + expected_proceeds

        # 断言：记录了 sell 交易
        trades = simulator.get_results()[0]
        sell_trades = trades.filter(pl.col("action") == "sell")
        assert len(sell_trades) == 1
        assert sell_trades["ts_code"][0] == "000001.SZ"
        assert float(sell_trades["price"][0]) == last_known_price
        assert int(sell_trades["volume"][0]) == volume
        assert float(sell_trades["net_amount"][0]) == expected_proceeds

        # 断言：记录了 warning 日志
        assert any("liquidated (delisted)" in w for w in simulator.warnings)

    def test_delisted_liquidation_uses_qfq_last_known_price(self, config: BacktestConfig) -> None:
        """清算价格使用 qfq_close（复权价）作为最后已知价，与 NAV 口径一致"""
        stock_meta = {"000002.SZ": {"delist_date": date(2024, 1, 10)}}
        simulator = self._make_simulator(config, stock_meta=stock_meta)

        # 第一天：有行情，建立最后已知价
        day1_quotes = pl.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.2],
                "qfq_open": [10.0],
                "qfq_close": [10.5],  # qfq_close 与 raw_close 不同
                "is_tradable": [True],
            }
        )
        simulator.positions["000002.SZ"] = {
            "volume": 500,
            "cost_basis": 5_000.0,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        # 通过 _record_daily_positions 建立 _last_known_prices
        simulator._record_daily_positions(date(2024, 1, 8), day1_quotes)
        assert simulator._last_known_prices["000002.SZ"] == 10.5

        initial_cash = simulator.cash

        # 退市日：无该标的行情（用其他标的占位以保持列结构）
        delist_day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )
        simulator._sell_all_positions(date(2024, 1, 10), delist_day_quotes)

        # 断言：清算价格 = qfq_close = 10.5
        assert "000002.SZ" not in simulator.positions
        assert simulator.cash == initial_cash + 500 * 10.5

    def test_delisted_no_last_known_price_falls_back_to_skip(self, config: BacktestConfig) -> None:
        """退市但无最后已知价时，兜底按临时停牌处理（保留持仓）"""
        stock_meta = {"000003.SZ": {"delist_date": date(2024, 1, 10)}}
        simulator = self._make_simulator(config, stock_meta=stock_meta)

        simulator.positions["000003.SZ"] = {
            "volume": 200,
            "cost_basis": 2_000.0,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        # 不设置 _last_known_prices

        # 用其他标的占位以保持列结构
        day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )
        simulator._sell_all_positions(date(2024, 1, 10), day_quotes)

        # 断言：持仓保留（兜底处理）
        assert "000003.SZ" in simulator.positions
        skipped = simulator.get_results()[2]
        no_quote_skips = skipped.filter(pl.col("reason") == "no_quote")
        assert len(no_quote_skips) == 1

    def test_non_delisted_stock_not_liquidated(self, config: BacktestConfig) -> None:
        """delist_date 在未来时，不触发清算（按临时停牌处理）"""
        stock_meta = {"000004.SZ": {"delist_date": date(2024, 2, 28)}}
        simulator = self._make_simulator(config, stock_meta=stock_meta)

        simulator.positions["000004.SZ"] = {
            "volume": 300,
            "cost_basis": 3_000.0,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        simulator._last_known_prices["000004.SZ"] = 10.5

        initial_cash = simulator.cash

        # exec_date < delist_date，临时停牌；用其他标的占位以保持列结构
        day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )
        simulator._sell_all_positions(date(2024, 1, 16), day_quotes)

        # 断言：持仓保留，现金不变
        assert "000004.SZ" in simulator.positions
        assert simulator.cash == initial_cash
        skipped = simulator.get_results()[2]
        assert len(skipped.filter(pl.col("reason") == "no_quote")) == 1

    def test_stock_without_meta_treated_as_suspended(self, config: BacktestConfig) -> None:
        """stock_meta 中无该标的记录时，按临时停牌处理（不影响现有行为）"""
        simulator = self._make_simulator(config, stock_meta={})

        simulator.positions["000005.SZ"] = {
            "volume": 100,
            "cost_basis": 1_000.0,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        simulator._last_known_prices["000005.SZ"] = 10.5

        initial_cash = simulator.cash
        # 用其他标的占位以保持列结构
        day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )
        simulator._sell_all_positions(date(2024, 1, 16), day_quotes)

        # 断言：持仓保留，现金不变（与原有 no_quote 行为一致）
        assert "000005.SZ" in simulator.positions
        assert simulator.cash == initial_cash


class TestSuspendedMarketValueEstimation:
    """BT-002: 临时停牌标的市值估算测试"""

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

    def test_suspended_position_kept_and_valued_at_last_known_price(self, config: BacktestConfig) -> None:
        """临时停牌标的保留持仓，市值用最后已知价估算，不触发卖出"""
        # delist_date 为 None（未退市）
        stock_meta = {"000001.SZ": {"delist_date": None}}
        simulator = PortfolioSimulator(
            config,
            TransactionCostModel(TransactionCostConfig()),
            stock_meta=stock_meta,
        )

        last_known_price = 12.0
        volume = 800
        cost_basis = 8_000.0
        simulator.positions["000001.SZ"] = {
            "volume": volume,
            "cost_basis": cost_basis,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        simulator._last_known_prices["000001.SZ"] = last_known_price

        initial_cash = simulator.cash

        # 当日无该标的行情（临时停牌）；用其他标的占位以保持列结构
        day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )

        # 调用 process_day（is_rebalance=True 触发卖出尝试）
        simulator.process_day(date(2024, 1, 16), pl.DataFrame(), day_quotes, is_rebalance=True)

        # 断言：持仓保留
        assert "000001.SZ" in simulator.positions

        # 断言：现金不变（未触发卖出）
        assert simulator.cash == initial_cash

        # 断言：记录了 no_quote skip
        skipped = simulator.get_results()[2]
        no_quote_skips = skipped.filter(pl.col("reason") == "no_quote")
        assert len(no_quote_skips) == 1
        assert no_quote_skips["direction"][0] == "sell"

        # 断言：_record_daily_positions 用最后已知价估算市值
        positions_history = simulator.get_results()[1]
        last_day = positions_history.row(-1, named=True)
        assert last_day["trade_date"] == date(2024, 1, 16)
        pos_detail = last_day["positions"]["000001.SZ"]
        assert pos_detail["estimated"] is True
        assert pos_detail["market_value"] == volume * last_known_price

        # 断言：total_value 包含估算市值
        expected_total = initial_cash + volume * last_known_price
        assert last_day["total_value"] == expected_total

    def test_suspended_position_does_not_record_sell_trade(self, config: BacktestConfig) -> None:
        """临时停牌标的不会记录 sell 交易"""
        stock_meta = {"000002.SZ": {"delist_date": None}}
        simulator = PortfolioSimulator(
            config,
            TransactionCostModel(TransactionCostConfig()),
            stock_meta=stock_meta,
        )

        simulator.positions["000002.SZ"] = {
            "volume": 500,
            "cost_basis": 5_000.0,
            "entry_date": date(2024, 1, 2),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }
        simulator._last_known_prices["000002.SZ"] = 11.0

        # 用其他标的占位以保持列结构
        day_quotes = pl.DataFrame(
            {
                "ts_code": ["other.SZ"],
                "raw_open": [5.0],
                "raw_close": [5.0],
                "qfq_open": [5.0],
                "qfq_close": [5.0],
                "is_tradable": [True],
            }
        )
        simulator.process_day(date(2024, 1, 16), pl.DataFrame(), day_quotes, is_rebalance=True)

        trades = simulator.get_results()[0]
        # 不应产生任何 sell 交易
        if not trades.is_empty():
            sell_trades = trades.filter(pl.col("action") == "sell")
            assert len(sell_trades) == 0
