"""回测指标计算模块单元测试"""

from datetime import date

import polars as pl
import pytest

from strategies.backtest.metrics import BacktestMetrics


class TestBacktestMetrics:
    @pytest.fixture
    def sample_nav_curve(self) -> pl.Series:
        return pl.Series([100.0, 101.0, 100.5, 102.0, 103.0, 101.5, 104.0])

    @pytest.fixture
    def sample_daily_returns(self) -> pl.Series:
        return pl.Series([0.01, -0.00495, 0.01493, 0.00980, -0.01456, 0.02463])

    @pytest.fixture
    def sample_benchmark_returns(self) -> pl.Series:
        return pl.Series([0.008, -0.003, 0.012, 0.007, -0.010, 0.020])

    def test_calc_total_return(self, sample_nav_curve: pl.Series) -> None:
        total_return = BacktestMetrics.calc_total_return(sample_nav_curve)
        assert total_return == pytest.approx(0.04, rel=0.01)

    def test_calc_total_return_empty(self) -> None:
        assert BacktestMetrics.calc_total_return(pl.Series([])) == 0.0

    def test_calc_annualized_return(self) -> None:
        ann_return = BacktestMetrics.calc_annualized_return(0.10, 252)
        assert ann_return == pytest.approx(0.10, rel=0.01)

    def test_calc_annualized_return_zero_days(self) -> None:
        assert BacktestMetrics.calc_annualized_return(0.10, 0) == 0.0

    def test_calc_volatility(self, sample_daily_returns: pl.Series) -> None:
        vol = BacktestMetrics.calc_volatility(sample_daily_returns)
        assert vol > 0

    def test_calc_volatility_insufficient_data(self) -> None:
        assert BacktestMetrics.calc_volatility(pl.Series([0.01])) == 0.0

    def test_calc_sharpe_ratio(self, sample_daily_returns: pl.Series) -> None:
        sharpe = BacktestMetrics.calc_sharpe_ratio(sample_daily_returns, risk_free_rate=0.02)
        assert sharpe > 0

    def test_calc_sharpe_ratio_insufficient_data(self) -> None:
        assert BacktestMetrics.calc_sharpe_ratio(pl.Series([0.01]), 0.02) == 0.0

    def test_calc_sharpe_ratio_zero_std(self) -> None:
        returns = pl.Series([0.01, 0.01, 0.01])
        assert BacktestMetrics.calc_sharpe_ratio(returns, 0.02) == 0.0

    def test_calc_max_drawdown(self) -> None:
        nav = pl.Series([100.0, 110.0, 105.0, 95.0, 100.0, 90.0, 95.0])
        max_dd, peak_idx, trough_idx = BacktestMetrics.calc_max_drawdown(nav)
        assert max_dd == pytest.approx(0.1818, rel=0.01)
        assert peak_idx == 1
        assert trough_idx == 5

    def test_calc_max_drawdown_empty(self) -> None:
        max_dd, peak_idx, trough_idx = BacktestMetrics.calc_max_drawdown(pl.Series([]))
        assert max_dd == 0.0
        assert peak_idx == 0
        assert trough_idx == 0

    def test_calc_calmar_ratio(self) -> None:
        calmar = BacktestMetrics.calc_calmar_ratio(0.15, 0.10)
        assert calmar == pytest.approx(1.5, rel=0.01)

    def test_calc_calmar_ratio_zero_drawdown(self) -> None:
        assert BacktestMetrics.calc_calmar_ratio(0.15, 0.0) == 0.0

    def test_calc_win_rate(self) -> None:
        trades = pl.DataFrame(
            {
                "realized_pnl": [100.0, -50.0, 200.0, -30.0, 50.0],
            }
        )
        win_rate = BacktestMetrics.calc_win_rate(trades)
        assert win_rate == pytest.approx(0.6, rel=0.01)

    def test_calc_win_rate_empty(self) -> None:
        assert BacktestMetrics.calc_win_rate(pl.DataFrame()) == 0.0

    def test_calc_profit_factor(self) -> None:
        trades = pl.DataFrame(
            {
                "realized_pnl": [100.0, -50.0, 200.0, -30.0, 50.0],
            }
        )
        pf = BacktestMetrics.calc_profit_factor(trades)
        assert pf == pytest.approx(350.0 / 80.0, rel=0.01)

    def test_calc_profit_factor_no_loss(self) -> None:
        trades = pl.DataFrame(
            {
                "realized_pnl": [100.0, 200.0, 50.0],
            }
        )
        assert BacktestMetrics.calc_profit_factor(trades) == float("inf")

    def test_calc_profit_factor_no_profit(self) -> None:
        trades = pl.DataFrame(
            {
                "realized_pnl": [-100.0, -50.0],
            }
        )
        assert BacktestMetrics.calc_profit_factor(trades) == 0.0

    def test_calc_ic(self) -> None:
        signal_rank = pl.Series([1, 2, 3, 4, 5])
        forward_return = pl.Series([5.0, 3.0, 0.0, -2.0, -4.0])
        ic = BacktestMetrics.calc_ic(signal_rank, forward_return)
        assert ic < 0

    def test_calc_ic_insufficient_data(self) -> None:
        assert BacktestMetrics.calc_ic(pl.Series([1, 2]), pl.Series([1.0, 2.0])) == 0.0

    def test_calc_ir(self) -> None:
        ic_series = pl.Series([0.05, 0.03, 0.07, 0.02, 0.04])
        ir = BacktestMetrics.calc_ir(ic_series)
        assert ir > 0

    def test_calc_ir_zero_std(self) -> None:
        ic_series = pl.Series([0.05, 0.05, 0.05])
        assert BacktestMetrics.calc_ir(ic_series) == 0.0

    def test_calc_information_ratio(
        self,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        ir, te = BacktestMetrics.calc_information_ratio(
            sample_daily_returns,
            sample_benchmark_returns,
        )
        assert ir > 0
        assert te > 0

    def test_calc_information_ratio_insufficient_data(self) -> None:
        ir, te = BacktestMetrics.calc_information_ratio(
            pl.Series([0.01]),
            pl.Series([0.008]),
        )
        assert ir == 0.0
        assert te == 0.0

    def test_calc_all_metrics(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        trades = pl.DataFrame(
            {
                "realized_pnl": [100.0, -50.0, 200.0],
            }
        )
        ic_series = pl.Series([0.05, 0.03, 0.07])

        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            ic_series,
            risk_free_rate=0.02,
        )

        assert "total_return" in metrics
        assert "annualized_return" in metrics
        assert "volatility" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "calmar_ratio" in metrics
        assert "win_rate" in metrics
        assert "profit_factor" in metrics
        assert "total_trades" in metrics
        assert "ic_mean" in metrics
        assert "ic_ir" in metrics
        assert "information_ratio" in metrics
        assert "tracking_error" in metrics

        assert metrics["total_return"] > 0
        assert metrics["sharpe_ratio"] > 0
        assert metrics["max_drawdown"] >= 0

    def test_calc_nav_curve_from_positions(self) -> None:
        positions = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "total_value": [1_000_000.0, 1_010_000.0, 1_005_000.0],
            }
        )
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        nav = BacktestMetrics.calc_nav_curve(positions, 1_000_000.0, trade_dates)
        assert len(nav) == 3
        assert float(nav[0]) == 1_000_000.0
        assert float(nav[-1]) == 1_005_000.0

    def test_calc_nav_curve_empty_positions(self) -> None:
        positions = pl.DataFrame()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        nav = BacktestMetrics.calc_nav_curve(positions, 500_000.0, trade_dates)
        assert len(nav) == 2
        assert all(v == 500_000.0 for v in nav.to_list())

    def test_calc_daily_returns(self) -> None:
        nav = pl.Series([1_000_000.0, 1_010_000.0, 1_005_000.0])
        returns = BacktestMetrics.calc_daily_returns(nav)
        assert len(returns) == 3
        assert float(returns[0]) == 0.0
        assert float(returns[1]) == pytest.approx(0.01, rel=1e-4)
        assert float(returns[2]) == pytest.approx(-0.00495, abs=1e-4)

    def test_calc_daily_returns_single_value(self) -> None:
        nav = pl.Series([1_000_000.0])
        returns = BacktestMetrics.calc_daily_returns(nav)
        assert len(returns) == 1
        assert float(returns[0]) == 0.0
