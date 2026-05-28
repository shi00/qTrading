"""回测指标计算模块"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    pass


class BacktestMetrics:
    """回测指标计算器"""

    @staticmethod
    def calc_nav_curve(
        positions: pl.DataFrame,
        initial_capital: float,
        trade_dates: list,
    ) -> pl.Series:
        if positions.is_empty():
            return pl.Series([initial_capital] * len(trade_dates))
        return positions["total_value"]

    @staticmethod
    def calc_daily_returns(nav_curve: pl.Series) -> pl.Series:
        if len(nav_curve) <= 1:
            return pl.Series([0.0] * len(nav_curve))
        returns = nav_curve.pct_change()
        return returns.fill_nan(0.0).fill_null(0.0)

    @staticmethod
    def calc_total_return(nav_curve: pl.Series) -> float:
        if len(nav_curve) == 0:
            return 0.0
        return float((nav_curve[-1] / nav_curve[0]) - 1)

    @staticmethod
    def calc_annualized_return(
        total_return: float,
        num_days: int,
        trading_days_per_year: int = 252,
    ) -> float:
        if num_days <= 0:
            return 0.0
        years = num_days / trading_days_per_year
        return float((1 + total_return) ** (1 / years) - 1)

    @staticmethod
    def calc_volatility(
        daily_returns: pl.Series,
        trading_days_per_year: int = 252,
    ) -> float:
        if len(daily_returns) < 2:
            return 0.0
        std_val = daily_returns.std()
        if std_val is None:
            return 0.0
        return float(std_val * math.sqrt(trading_days_per_year))

    @staticmethod
    def calc_sharpe_ratio(
        daily_returns: pl.Series,
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = 252,
    ) -> float:
        if len(daily_returns) < 2:
            return 0.0

        daily_rf = risk_free_rate / trading_days_per_year
        excess_returns = daily_returns - daily_rf

        excess_std = excess_returns.std()
        if excess_std is None:
            return 0.0
        excess_std_float = float(excess_std)
        if excess_std_float == 0:
            return 0.0

        excess_mean = excess_returns.mean()
        if excess_mean is None:
            return 0.0

        return float(excess_mean) / excess_std_float * math.sqrt(trading_days_per_year)

    @staticmethod
    def calc_max_drawdown(nav_curve: pl.Series) -> tuple[float, int, int]:
        if len(nav_curve) == 0:
            return 0.0, 0, 0

        peak = nav_curve[0]
        max_dd = 0.0
        peak_idx = 0
        trough_idx = 0
        current_peak_idx = 0

        for i in range(len(nav_curve)):
            if nav_curve[i] > peak:
                peak = nav_curve[i]
                current_peak_idx = i
            else:
                dd = float((peak - nav_curve[i]) / peak)
                if dd > max_dd:
                    max_dd = dd
                    peak_idx = current_peak_idx
                    trough_idx = i

        return max_dd, peak_idx, trough_idx

    @staticmethod
    def calc_calmar_ratio(
        annualized_return: float,
        max_drawdown: float,
    ) -> float:
        if max_drawdown <= 0:
            return 0.0
        return annualized_return / max_drawdown

    @staticmethod
    def calc_win_rate(trades: pl.DataFrame) -> float:
        if len(trades) == 0:
            return 0.0
        profitable = trades.filter(pl.col("realized_pnl") > 0)
        return len(profitable) / len(trades)

    @staticmethod
    def calc_profit_factor(trades: pl.DataFrame) -> float:
        if len(trades) == 0:
            return 0.0
        gross_profit_raw = trades.filter(pl.col("realized_pnl") > 0)["realized_pnl"].sum()
        gross_loss_raw = trades.filter(pl.col("realized_pnl") < 0)["realized_pnl"].sum()
        gross_profit = float(gross_profit_raw) if gross_profit_raw is not None else 0.0
        gross_loss = abs(float(gross_loss_raw)) if gross_loss_raw is not None else 0.0
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @staticmethod
    def calc_ic(
        signal_rank: pl.Series,
        forward_return: pl.Series,
    ) -> float:
        if len(signal_rank) < 3 or len(forward_return) < 3:
            return 0.0
        df = pl.DataFrame(
            {
                "signal_rank": signal_rank,
                "forward_return": forward_return,
            }
        )
        correlation = df.select(pl.corr("signal_rank", "forward_return", method="spearman")).item()
        return float(correlation) if correlation is not None else 0.0

    @staticmethod
    def calc_ir(ic_series: pl.Series) -> float:
        if len(ic_series) < 2:
            return 0.0
        ic_mean = float(ic_series.mean()) if ic_series.mean() is not None else 0.0
        ic_std_val = ic_series.std()
        if ic_std_val is None:
            return 0.0
        ic_std_float = float(ic_std_val)
        if ic_std_float < 1e-10:
            return 0.0
        return ic_mean / ic_std_float * math.sqrt(252)

    @staticmethod
    def calc_information_ratio(
        daily_returns: pl.Series,
        benchmark_returns: pl.Series,
        trading_days_per_year: int = 252,
    ) -> tuple[float, float]:
        if len(daily_returns) < 2 or len(benchmark_returns) < 2:
            return 0.0, 0.0

        excess_returns = daily_returns - benchmark_returns

        tracking_error = excess_returns.std()
        if tracking_error is None:
            return 0.0, 0.0
        tracking_error_float = float(tracking_error)
        if tracking_error_float == 0:
            return 0.0, 0.0

        excess_mean = excess_returns.mean()
        if excess_mean is None:
            return 0.0, 0.0

        tracking_error_annual = tracking_error_float * math.sqrt(trading_days_per_year)
        information_ratio = float(excess_mean) * trading_days_per_year / tracking_error_annual

        return information_ratio, tracking_error_annual

    @staticmethod
    def calc_all_metrics(
        nav_curve: pl.Series,
        daily_returns: pl.Series,
        benchmark_returns: pl.Series,
        trades: pl.DataFrame,
        ic_series: pl.Series,
        risk_free_rate: float = 0.02,
    ) -> dict[str, float]:
        total_return = BacktestMetrics.calc_total_return(nav_curve)
        ann_return = BacktestMetrics.calc_annualized_return(total_return, len(nav_curve))
        volatility = BacktestMetrics.calc_volatility(daily_returns)
        sharpe = BacktestMetrics.calc_sharpe_ratio(daily_returns, risk_free_rate)
        max_dd, _, _ = BacktestMetrics.calc_max_drawdown(nav_curve)
        calmar = BacktestMetrics.calc_calmar_ratio(ann_return, max_dd)

        information_ratio, tracking_error = BacktestMetrics.calc_information_ratio(daily_returns, benchmark_returns)

        return {
            "total_return": total_return,
            "annualized_return": ann_return,
            "volatility": volatility,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "calmar_ratio": calmar,
            "win_rate": BacktestMetrics.calc_win_rate(trades),
            "profit_factor": BacktestMetrics.calc_profit_factor(trades),
            "total_trades": len(trades),
            "ic_mean": float(ic_series.mean()) if len(ic_series) > 0 else 0.0,
            "ic_ir": BacktestMetrics.calc_ir(ic_series),
            "information_ratio": information_ratio,
            "tracking_error": tracking_error,
        }
