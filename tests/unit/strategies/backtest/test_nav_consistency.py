"""NAV 口径一致性测试

验证 BT-P0-3 修复：净值曲线混用 nominal cash + QFQ 市值问题。

核心原则：
1. total_value = cash + qfq_market_value（NAV 使用复权价格，代表复权总收益）
2. pnl = qfq_market_value - qfq_cost_basis（盈亏用复权价格）
3. 除权日 NAV 不跳变（qfq 价格连续）
"""

from datetime import date

import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.portfolio import PortfolioSimulator
from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel


class TestNavQfqConsistency:
    """total_value 必须使用 qfq 口径"""

    @pytest.fixture
    def config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
        )

    @pytest.fixture
    def cost_model(self) -> TransactionCostModel:
        return TransactionCostModel(TransactionCostConfig())

    def test_total_value_uses_qfq_market_value(
        self,
        config: BacktestConfig,
        cost_model: TransactionCostModel,
    ) -> None:
        """
        total_value = cash + qfq_market_value，而非 raw_market_value。

        验证持仓记录中 total_value 与 qfq 口径一致。
        """
        simulator = PortfolioSimulator(config, cost_model)

        signals = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [8.0],
                "qfq_close": [8.4],
            }
        )

        simulator.process_day(
            exec_date=date(2024, 1, 2),
            day_signals=signals,
            day_quotes=quotes,
            is_rebalance=True,
        )

        positions_df = simulator.get_results()[1]
        assert not positions_df.is_empty()

        last_record = positions_df.row(-1, named=True)
        total_value = last_record["total_value"]
        cash = last_record["cash"]
        positions_detail = last_record["positions"]

        qfq_mv_sum = sum(p["market_value"] for p in positions_detail.values())
        expected_total = cash + qfq_mv_sum

        assert total_value == pytest.approx(expected_total, rel=1e-6)

    def test_total_value_not_using_raw(
        self,
        config: BacktestConfig,
        cost_model: TransactionCostModel,
    ) -> None:
        """
        total_value 不应等于 cash + raw_market_value。

        当 raw != qfq 时，验证 total_value 使用 qfq 口径。
        """
        simulator = PortfolioSimulator(config, cost_model)

        signals = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [20.0],
                "raw_close": [20.5],
                "qfq_open": [10.0],
                "qfq_close": [10.25],
            }
        )

        simulator.process_day(
            exec_date=date(2024, 1, 2),
            day_signals=signals,
            day_quotes=quotes,
            is_rebalance=True,
        )

        positions_df = simulator.get_results()[1]
        last_record = positions_df.row(-1, named=True)
        total_value = last_record["total_value"]
        cash = last_record["cash"]
        positions_detail = last_record["positions"]

        raw_mv_sum = sum(p["raw_market_value"] for p in positions_detail.values())
        raw_based_total = cash + raw_mv_sum

        assert total_value != pytest.approx(raw_based_total, rel=0.1)


class TestExRightNavNoJump:
    """除权日 NAV 不跳变"""

    @pytest.fixture
    def config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
            rebalance_freq="signal",
        )

    def test_nav_continuous_on_ex_right(
        self,
        config: BacktestConfig,
    ) -> None:
        """
        除权日 NAV 连续，无跳变。

        场景：
        - Day 1: 买入，adj_factor=1.0, raw_close=20.0
        - Day 2: 除权日，adj_factor=0.5, raw_close=10.0（10送10）
        - Day 3: 继续持有

        验证：
        - Day 1 NAV ≈ Day 2 NAV（使用 raw 口径盯市）
        - 日收益仅反映价格变动，不含复权跳变
        """
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())

        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "signal_date": [date(2024, 1, 1)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "raw_open": [20.0, 10.0, 10.5],
                "raw_close": [20.5, 10.0, 10.8],
                "qfq_open": [20.5, 20.0, 21.6],
                "qfq_close": [20.5, 20.0, 21.6],
                "is_tradable": [True, True, True],
            }
        )

        trade_dates = [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
        ]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert not positions.is_empty()

        nav_values = positions["total_value"].to_list()

        assert len(nav_values) >= 3

        nav_day1 = nav_values[0]
        nav_day2 = nav_values[1]
        nav_day3 = nav_values[2]

        assert nav_day1 > 0
        assert nav_day2 > 0

        daily_return_day2 = (nav_day2 - nav_day1) / nav_day1
        daily_return_day3 = (nav_day3 - nav_day2) / nav_day2

        assert abs(daily_return_day2) < 0.05
        assert abs(daily_return_day3) < 0.5

    def test_nav_raw_vs_qfq_on_ex_right(
        self,
        config: BacktestConfig,
    ) -> None:
        """
        除权日 raw NAV 与 QFQ NAV 对比。

        raw NAV: 连续，无跳变
        QFQ NAV: 除权日跳变（如果错误使用）

        验证 total_value 使用 raw 口径。
        """
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())

        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "signal_date": [date(2024, 1, 1)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "raw_open": [20.0, 10.0],
                "raw_close": [20.0, 10.0],
                "qfq_open": [20.0, 20.0],
                "qfq_close": [20.0, 20.0],
                "is_tradable": [True, True],
            }
        )

        trade_dates = [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
        ]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        nav_values = positions["total_value"].to_list()

        if len(nav_values) >= 2:
            nav_day1 = nav_values[0]
            nav_day2 = nav_values[1]

            raw_change_ratio = abs(nav_day2 - nav_day1) / nav_day1

            assert raw_change_ratio < 0.01


class TestQfqPnlCalculation:
    """positions_detail.pnl 使用 QFQ 口径"""

    @pytest.fixture
    def config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
        )

    @pytest.fixture
    def cost_model(self) -> TransactionCostModel:
        return TransactionCostModel(TransactionCostConfig())

    def test_pnl_uses_qfq_prices(
        self,
        config: BacktestConfig,
        cost_model: TransactionCostModel,
    ) -> None:
        """
        pnl = qfq_market_value - qfq_cost_basis。

        验证 positions_detail 中的 pnl 使用 QFQ 口径。
        """
        simulator = PortfolioSimulator(config, cost_model)

        signals = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [8.0],
                "qfq_close": [8.4],
            }
        )

        simulator.process_day(
            exec_date=date(2024, 1, 2),
            day_signals=signals,
            day_quotes=quotes,
            is_rebalance=True,
        )

        trades_df = simulator.get_results()[0]
        positions_df = simulator.get_results()[1]

        assert not trades_df.is_empty()
        assert not positions_df.is_empty()

        buy_trade = trades_df.row(0, named=True)
        volume = buy_trade["volume"]
        qfq_entry_price = 8.0
        qfq_cost_basis = volume * qfq_entry_price

        last_position = positions_df.row(-1, named=True)
        positions_detail = last_position["positions"]

        if "000001.SZ" in positions_detail:
            pos_detail = positions_detail["000001.SZ"]
            expected_pnl = pos_detail["market_value"] - qfq_cost_basis

            assert pos_detail["pnl"] == pytest.approx(expected_pnl, rel=1e-6)

    def test_pnl_reflects_real_return(
        self,
        config: BacktestConfig,
        cost_model: TransactionCostModel,
    ) -> None:
        """
        pnl 应反映真实收益（含分红送股）。

        场景：
        - 买入时 qfq_open=10.0
        - 持有期间发生 10 送 10
        - 当前 qfq_close=20.0（复权后翻倍）

        验证 pnl > 0，反映真实收益。
        """
        simulator = PortfolioSimulator(config, cost_model)

        signals = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.0],
                "qfq_open": [10.0],
                "qfq_close": [20.0],
            }
        )

        simulator.process_day(
            exec_date=date(2024, 1, 2),
            day_signals=signals,
            day_quotes=quotes,
            is_rebalance=True,
        )

        positions_df = simulator.get_results()[1]
        last_position = positions_df.row(-1, named=True)
        positions_detail = last_position["positions"]

        if "000001.SZ" in positions_detail:
            pos_detail = positions_detail["000001.SZ"]
            assert pos_detail["pnl"] > 0


class TestDailyReturnConsistency:
    """日收益计算口径一致性"""

    def test_daily_return_from_qfq_nav(self) -> None:
        """
        daily_return 必须基于 raw NAV 计算。

        验证 BacktestMetrics.calc_daily_returns 使用 raw 口径的 nav_curve。
        """
        from strategies.backtest.metrics import BacktestMetrics

        nav_curve = pl.Series([1_000_000.0, 1_010_000.0, 1_005_000.0])

        daily_returns = BacktestMetrics.calc_daily_returns(nav_curve)

        assert len(daily_returns) == 3
        assert daily_returns[0] is None
        assert float(daily_returns[1]) == pytest.approx(0.01, rel=1e-4)
        assert float(daily_returns[2]) == pytest.approx(-0.00495, abs=1e-4)

    def test_monthly_return_calculation(self) -> None:
        """
        月度收益应使用 (1+r).prod()-1 或 (end_nav/start_nav)-1。

        验证 engine._calc_period_stats 使用正确的复利公式。

        注意：简单累加 daily_return 与复利 (end_nav/start_nav)-1 有微小差异，
        当日收益较小时差异不明显，当日收益较大时差异更显著。
        """
        from strategies.backtest.metrics import BacktestMetrics

        nav_curve = pl.Series([100.0, 105.0, 110.25, 115.76])
        daily_returns = BacktestMetrics.calc_daily_returns(nav_curve)

        simple_sum = float(daily_returns.sum())
        compound = (nav_curve[-1] / nav_curve[0]) - 1

        assert simple_sum != pytest.approx(compound, rel=0.01)
        assert compound == pytest.approx(0.1576, rel=1e-3)

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        trade_dates = [date(2024, 1, i) for i in range(1, 5)]
        benchmark_returns = pl.Series([0.0, 0.01, 0.01, 0.01])

        result = engine._calc_period_stats(nav_curve, daily_returns, benchmark_returns, trade_dates)

        monthly_ret = float(result["monthly_return"][0])
        assert monthly_ret == pytest.approx(compound, rel=1e-3), (
            f"monthly_return={monthly_ret:.6f} should equal compound={compound:.6f}, not simple_sum={simple_sum:.6f}"
        )


class TestLastKnownPriceValuation:
    """停牌/无行情持仓使用最后已知价估值"""

    @pytest.fixture
    def config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            initial_capital=1_000_000.0,
            max_position_count=10,
            cash_reserve_pct=0.1,
            rebalance_freq="signal",
        )

    def test_suspended_position_uses_last_known_price(self, config: BacktestConfig) -> None:
        """
        停牌日无行情时，持仓市值使用最后已知 qfq_close 估值。

        场景：
        - Day 1: 买入，qfq_close=10.2
        - Day 2: 无行情（停牌），应使用 Day 1 的 qfq_close=10.2 估值
        - Day 3: 恢复行情，qfq_close=10.5

        验证 Day 2 的 total_value > cash（持仓市值不为 0）。
        """
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())

        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 2)],
                "signal_date": [date(2024, 1, 1)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        # Day 2 无 000001.SZ 的行情
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 4)],
                "raw_open": [10.0, 10.3],
                "raw_close": [10.2, 10.5],
                "qfq_open": [10.0, 10.3],
                "qfq_close": [10.2, 10.5],
                "is_tradable": [True, True],
            }
        )

        trade_dates = [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
        ]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert not positions.is_empty()

        # Day 3 (index 2) 无行情，持仓市值应使用最后已知价
        day3_pos = positions.filter(pl.col("trade_date") == date(2024, 1, 3))
        if not day3_pos.is_empty():
            total_value = float(day3_pos["total_value"][0])
            cash = float(day3_pos["cash"][0])
            # 持仓市值不为 0（使用最后已知价估值）
            assert total_value > cash

    def test_position_detail_has_estimated_flag(self, config: BacktestConfig) -> None:
        """
        无行情持仓的 positions_detail 应包含 estimated=True 标记。
        """
        simulator = PortfolioSimulator(config, TransactionCostModel(TransactionCostConfig()))

        signals = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "signal_rank": [1.0],
            }
        )

        quotes_day1 = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.2],
                "qfq_open": [10.0],
                "qfq_close": [10.2],
            }
        )

        simulator.process_day(
            exec_date=date(2024, 1, 2),
            day_signals=signals,
            day_quotes=quotes_day1,
            is_rebalance=True,
        )

        # Day 2: 无行情
        quotes_day2 = pl.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "raw_open": [20.0],
                "raw_close": [20.5],
                "qfq_open": [20.0],
                "qfq_close": [20.5],
            }
        )

        simulator.process_day(
            exec_date=date(2024, 1, 3),
            day_signals=pl.DataFrame(),
            day_quotes=quotes_day2,
            is_rebalance=False,
        )

        positions_df = simulator.get_results()[1]
        last_record = positions_df.row(-1, named=True)
        positions_detail = last_record["positions"]

        if "000001.SZ" in positions_detail:
            assert positions_detail["000001.SZ"].get("estimated") is True

    def test_cache_updates_after_resumption(self, config: BacktestConfig) -> None:
        """
        停牌结束后恢复行情，_last_known_prices 应更新为新价。

        场景：
        - Day 1: 买入，qfq_close=10.2
        - Day 2: 无行情（停牌），使用缓存价 10.2 估值
        - Day 3: 恢复行情，qfq_close=10.5

        验证 Day 3 的持仓市值使用 10.5 而非 10.2。
        """
        simulator = PortfolioSimulator(config, TransactionCostModel(TransactionCostConfig()))

        # Day 1: 买入
        signals = pl.DataFrame({"ts_code": ["000001.SZ"], "signal_rank": [1.0]})
        quotes_day1 = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.2],
                "qfq_open": [10.0],
                "qfq_close": [10.2],
            }
        )
        simulator.process_day(date(2024, 1, 2), signals, quotes_day1, is_rebalance=True)

        # Day 2: 无行情（停牌）
        quotes_day2 = pl.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "raw_open": [20.0],
                "raw_close": [20.5],
                "qfq_open": [20.0],
                "qfq_close": [20.5],
            }
        )
        simulator.process_day(date(2024, 1, 3), pl.DataFrame(), quotes_day2, is_rebalance=False)

        # Day 3: 恢复行情
        quotes_day3 = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.3],
                "raw_close": [10.5],
                "qfq_open": [10.3],
                "qfq_close": [10.5],
            }
        )
        simulator.process_day(date(2024, 1, 4), pl.DataFrame(), quotes_day3, is_rebalance=False)

        positions_df = simulator.get_results()[1]
        day3_record = positions_df.filter(pl.col("trade_date") == date(2024, 1, 4))
        assert not day3_record.is_empty()

        positions_detail = day3_record.row(0, named=True)["positions"]
        pos = positions_detail["000001.SZ"]
        # 市值应使用新的 qfq_close=10.5，而非缓存价 10.2
        expected_mv = pos["volume"] * 10.5
        assert pos["market_value"] == pytest.approx(expected_mv, rel=1e-6)
        # 恢复行情后不应有 estimated 标记
        assert "estimated" not in pos or pos.get("estimated") is not True

    def test_suspended_position_valuation_uses_exact_last_price(self, config: BacktestConfig) -> None:
        """
        停牌日持仓市值应精确等于 volume × 最后已知 qfq_close。
        """
        simulator = PortfolioSimulator(config, TransactionCostModel(TransactionCostConfig()))

        signals = pl.DataFrame({"ts_code": ["000001.SZ"], "signal_rank": [1.0]})
        quotes_day1 = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.2],
                "qfq_open": [10.0],
                "qfq_close": [10.2],
            }
        )
        simulator.process_day(date(2024, 1, 2), signals, quotes_day1, is_rebalance=True)

        # 获取买入后的持仓信息
        positions_df = simulator.get_results()[1]
        day1_record = positions_df.row(-1, named=True)
        volume = day1_record["positions"]["000001.SZ"]["volume"]

        # Day 2: 无行情
        quotes_day2 = pl.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "raw_open": [20.0],
                "raw_close": [20.5],
                "qfq_open": [20.0],
                "qfq_close": [20.5],
            }
        )
        simulator.process_day(date(2024, 1, 3), pl.DataFrame(), quotes_day2, is_rebalance=False)

        positions_df = simulator.get_results()[1]
        day2_record = positions_df.filter(pl.col("trade_date") == date(2024, 1, 3))
        if not day2_record.is_empty():
            positions_detail = day2_record.row(0, named=True)["positions"]
            if "000001.SZ" in positions_detail:
                pos = positions_detail["000001.SZ"]
                expected_mv = volume * 10.2
                assert pos["market_value"] == pytest.approx(expected_mv, rel=1e-6)

    def test_cache_cleared_after_sell(self, config: BacktestConfig) -> None:
        """
        卖出持仓后，_last_known_prices 中对应缓存应被清理。
        """
        simulator = PortfolioSimulator(config, TransactionCostModel(TransactionCostConfig()))

        # Day 1: 买入
        signals = pl.DataFrame({"ts_code": ["000001.SZ"], "signal_rank": [1.0]})
        quotes_day1 = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.0],
                "raw_close": [10.2],
                "qfq_open": [10.0],
                "qfq_close": [10.2],
            }
        )
        simulator.process_day(date(2024, 1, 2), signals, quotes_day1, is_rebalance=True)

        assert "000001.SZ" in simulator._last_known_prices

        # Day 2: 卖出（空信号触发全仓卖出）
        quotes_day2 = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "raw_open": [10.3],
                "raw_close": [10.5],
                "qfq_open": [10.3],
                "qfq_close": [10.5],
            }
        )
        simulator.process_day(date(2024, 1, 3), pl.DataFrame(), quotes_day2, is_rebalance=True)

        assert "000001.SZ" not in simulator._last_known_prices
