"""回测指标计算模块"""

from __future__ import annotations

import enum
import math
from typing import cast

import polars as pl


# 盈亏分类阈值：realized_pnl > PROFIT_THRESHOLD 为盈利（WIN），
# < PROFIT_THRESHOLD 为亏损（LOSS），== PROFIT_THRESHOLD 为平局（DRAW）。
# report.py 与 calc_win_rate 共享此常量，确保 0 归类一致。
PROFIT_THRESHOLD: float = 0.0


class ExitReason(enum.StrEnum):
    """平仓原因（D4-6）。

    区分主动决策交易（计入胜率等交易指标）与非策略决策交易（排除）：

    - SIGNAL：策略信号触发的主动平仓（当前 engine 由再平衡剥离后未直接产生）。
    - REBALANCE：再平衡调仓触发的主动平仓（正常卖出/部分减持）。
    - DELISTED：退市强制清算（非策略决策，亏损属强制簿记），不计入胜率分母。

    存于 trades 的 exit_reason 列为字符串值（enum 成员原值）。
    """

    SIGNAL = "SIGNAL"
    REBALANCE = "REBALANCE"
    DELISTED = "DELISTED"


class BacktestMetrics:
    """回测指标计算器"""

    # D1-M4: 年化外推的最短区间阈值（约一个季度）。低于此不做年化外推——
    # 20 个交易日的回测 years≈0.079，指数高达 12.6，区间收益 ±8% 会被外推成
    # +160% / −65%，高次方放大不可信。其余统计量（波动率/胜率/盈亏比等）均有
    # 样本量守卫，年化此前唯独缺失，此为对齐内部标准。
    _MIN_ANNUALIZE_DAYS = 60

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
        # 首项保持 null（pct_change 产生）；nan 和 inf 替换为 0.0
        return (
            returns.to_frame("_r")
            .select(pl.when(pl.col("_r").is_infinite() | pl.col("_r").is_nan()).then(0.0).otherwise(pl.col("_r")))
            .to_series()
        )

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
    ) -> float | None:
        # D1-M4: 短区间不年化（返回 None，经 report/UI 渲染 N/A），避免高次方外推失真。
        # 与 calc_win_rate / calc_profit_factor 的 None 语义对齐（R21：无定义用 None 哨兵，
        # 不伪装为合法值）。
        if num_days < BacktestMetrics._MIN_ANNUALIZE_DAYS:
            return None
        if 1 + total_return <= 0:
            return -1.0  # 本金归零
        years = num_days / trading_days_per_year
        return float((1 + total_return) ** (1 / years) - 1)

    @staticmethod
    def calc_volatility(
        daily_returns: pl.Series,
        trading_days_per_year: int = 252,
    ) -> float | None:
        """有效样本不足 2 时波动率无定义，返回 None（R21：不可返回 0.0——
        「零波动」是具体业务含义，且会低估爆仓后的真实波动）。"""
        # 排除首项 null（pct_change 产生的伪样本）
        valid_returns = daily_returns.drop_nulls()
        if len(valid_returns) < 2:
            return None
        std_val = valid_returns.std()
        if std_val is None or not isinstance(std_val, (int, float)):
            return None
        return float(std_val) * math.sqrt(trading_days_per_year)

    @staticmethod
    def calc_sharpe_ratio(
        daily_returns: pl.Series,
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = 252,
    ) -> float | None:
        """夏普比率：有效样本不足、或超额收益零波动/异常时无定义，返回 None
        （R21：不可返回 0.0——「风险调整后收益恰好等于无风险利率」是具体业务含义，
        会掩盖样本不足或恒定收益的真实状态）。"""
        # 排除首项 null（pct_change 产生的伪样本）
        valid_returns = daily_returns.drop_nulls()
        if len(valid_returns) < 2:
            return None

        daily_rf = risk_free_rate / trading_days_per_year
        excess_returns = valid_returns - daily_rf

        excess_std = excess_returns.std()
        if excess_std is None or not isinstance(excess_std, (int, float)):
            return None
        excess_std_float = float(excess_std)
        if excess_std_float == 0:
            return None

        excess_mean = excess_returns.mean()
        if excess_mean is None or not isinstance(excess_mean, (int, float)):
            return None

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
        annualized_return: float | None,
        max_drawdown: float,
    ) -> float | None:
        # D1-M4: 年化为 None（区间过短不年化）时 Calmar 亦无定义，返回 None 经
        # report/UI 渲染 N/A，避免用 0.0 伪装真实比值。
        if annualized_return is None:
            return None
        # R21: max_drawdown == 0（全程无回撤）时 Calmar 数学上为 +∞，是最优状态而非
        # 「单位回撤收益为零」。记为 0.0 会让最好的结果显示为最差，被排序/寻优系统性淘汰。
        if max_drawdown <= 0:
            return None
        return annualized_return / max_drawdown

    @staticmethod
    def calc_win_rate(trades: pl.DataFrame) -> float | None:
        """计算胜率，仅统计卖出/平仓交易中由策略主动决策的平仓。

        买入交易 realized_pnl=0.0 不计入分母；退市强平等非策略决策平仓
        （exit_reason == DELISTED）也不计入分母——退市标的通常必然亏损，
        计入会系统性拉低胜率、使策略间不可比、掩盖退市风险（D4-6）。

        无主动决策平仓或空 trades 时返回 None（指标无定义），与 calc_profit_factor
        语义一致，report 层渲染 N/A。盈亏阈值由 PROFIT_THRESHOLD 共享常量定义，
        与 report.py 保持一致。
        """
        if len(trades) == 0:
            return None
        if "exit_reason" not in trades.columns:
            # 历史/无退出原因标注的 trades 无法区分主动与非策略决策平仓 → 无定义
            return None
        decision_sells = trades.filter(
            (pl.col("action") == "sell")
            & pl.col("exit_reason").is_in([ExitReason.SIGNAL.value, ExitReason.REBALANCE.value])
        )
        if len(decision_sells) == 0:
            return None
        profitable = decision_sells.filter(pl.col("realized_pnl") > PROFIT_THRESHOLD)
        return len(profitable) / len(decision_sells)

    @staticmethod
    def calc_profit_factor(trades: pl.DataFrame) -> float | None:
        """计算盈亏比，仅统计卖出/平仓交易。

        无亏损交易（gross_loss <= 0）或无平仓交易时返回 None（指标无定义），
        不返回 inf —— inf 无法 JSON 序列化，且写入 numeric 列会被 PostgreSQL 拒绝
        （D4-5）。
        """
        if len(trades) == 0:
            return None
        sell_trades = trades.filter(pl.col("action") == "sell")
        if len(sell_trades) == 0:
            return None
        gross_profit_raw = sell_trades.filter(pl.col("realized_pnl") > 0)["realized_pnl"].sum()
        gross_loss_raw = sell_trades.filter(pl.col("realized_pnl") < 0)["realized_pnl"].sum()
        gross_profit = float(gross_profit_raw) if gross_profit_raw is not None else 0.0
        gross_loss = abs(float(gross_loss_raw)) if gross_loss_raw is not None else 0.0
        if gross_loss <= 0:
            return None
        return gross_profit / gross_loss

    @staticmethod
    def calc_ic(
        signal_rank: pl.Series,
        forward_return: pl.Series,
    ) -> float | None:
        """样本不足或相关不可算时 IC 无定义，返回 None（R21：不可返回 0.0——
        IC=0 的业务含义是「信号无预测力」，与「候选股不足无法计算」是完全不同的结论，
        后者只是未测量，前者是对策略的判决）。"""
        if len(signal_rank) < 3 or len(forward_return) < 3:
            return None
        df = pl.DataFrame(
            {
                "signal_rank": signal_rank,
                "forward_return": forward_return,
            }
        )
        correlation = df.select(pl.corr("signal_rank", "forward_return", method="spearman")).item()
        return float(correlation) if correlation is not None else None

    @staticmethod
    def calc_ir(
        ic_series: pl.Series,
        num_days: int = 252,
        trading_days_per_year: int = 252,
    ) -> float | None:
        """计算 IC 信息比率 (IR)。

        年化系数 = sqrt(ic_count / years)，其中 years = num_days / 252。
        IC 序列按调仓频率计算（非日频），不能用固定 sqrt(252) 年化。
        无定义（有效样本不足 / IC 零波动）时返回 None（R21）。"""

        # R21: 先剔除「该期无法计算」（None → null）的无效样本；有效样本不足 2 个时
        # IR 无定义，返回 None。若不剔除，全 null 序列会被误判为「信号无稳定性」(0.0)。
        valid = ic_series.drop_nulls()
        if len(valid) < 2:
            return None
        ic_mean_raw = valid.mean()
        ic_mean = float(cast(float, ic_mean_raw)) if ic_mean_raw is not None else None
        ic_std_val = valid.std()
        if ic_std_val is None:
            return None
        ic_std_float = float(cast(float, ic_std_val))
        if ic_std_float < 1e-10:
            return None
        if ic_mean is None:
            return None
        # 年化系数: ic_count / years = 每年 IC 样本数
        years = num_days / trading_days_per_year if num_days > 0 else 1.0
        ic_count = len(valid)
        annualization_factor = math.sqrt(ic_count / years)
        return ic_mean / ic_std_float * annualization_factor

    @staticmethod
    def calc_information_ratio(
        daily_returns: pl.Series,
        benchmark_returns: pl.Series,
        trading_days_per_year: int = 252,
    ) -> tuple[float | None, float | None]:
        # D1-M1: 两序列按共同有效样本对齐后相减。缺口（基准缺失日 / 净值爆仓日）
        #   不参与超额计算，避免 null 传播污染跟踪误差与信息比率，也防止"基准缺失被
        #   伪装成 0"系统性拉低超额。对齐后不足 2 个样本则视为无超额（沿用旧语义）。
        # R21: 无定义时返回 (None, None)——「无超额收益、零跟踪误差」是具体业务含义，
        #   会把「无法计算」伪装成「业绩与基准完全持平」。
        aligned = pl.DataFrame({"daily_returns": daily_returns, "benchmark_returns": benchmark_returns}).drop_nulls()
        if len(aligned) < 2:
            return None, None

        excess_returns = aligned["daily_returns"] - aligned["benchmark_returns"]

        tracking_error = excess_returns.std()
        if tracking_error is None:
            return None, None
        tracking_error_float = float(cast(float, tracking_error))
        if tracking_error_float == 0:
            return None, None

        excess_mean = excess_returns.mean()
        if excess_mean is None:
            return None, None

        tracking_error_annual = tracking_error_float * math.sqrt(trading_days_per_year)
        information_ratio = float(cast(float, excess_mean)) * trading_days_per_year / tracking_error_annual

        return information_ratio, tracking_error_annual

    @staticmethod
    def calc_investment_metrics(positions: pl.DataFrame) -> dict[str, float | int]:
        """计算仓位可见性指标（BT-03）。

        基于每日持仓快照（columns: trade_date, cash, total_value）统计资金运用效率：
        - 每日投资比例 invested_pct = (total_value - cash) / total_value（现金及未投出部分占比）
        - avg_invested_pct: 平均投资比例
        - min_invested_pct: 最低投资比例
        - cash_drag_days: 投资比例 < 80% 的天数（现金拖累）

        持仓为空（无信号回测等）时视为 0% 投资。这些指标让「信号稀疏 → 资金闲置」
        变得可见：avg_invested_pct 低说明收益被现金稀释（volatility/回撤被压低但 Sharpe
        也被拉低），用户据此调低 max_single_weight 或增加选股数量。
        """
        if positions.is_empty() or "total_value" not in positions.columns or "cash" not in positions.columns:
            return {"avg_invested_pct": 0.0, "min_invested_pct": 0.0, "cash_drag_days": 0}
        total = positions["total_value"].cast(pl.Float64).fill_nan(0.0).fill_null(0.0)
        cash = positions["cash"].cast(pl.Float64).fill_nan(0.0).fill_null(0.0)
        invested = total - cash
        invested_pct = (invested / total).fill_nan(0.0).fill_null(0.0)
        _mean = invested_pct.mean()
        _min = invested_pct.min()
        _drag = (invested_pct < 0.8).sum()
        return {
            "avg_invested_pct": float(cast(float, _mean)) if len(invested_pct) > 0 else 0.0,
            "min_invested_pct": float(cast(float, _min)) if len(invested_pct) > 0 else 0.0,
            "cash_drag_days": int(cast(float, _drag) if _drag is not None else 0.0),
        }

    @staticmethod
    def calc_all_metrics(
        nav_curve: pl.Series,
        daily_returns: pl.Series,
        benchmark_returns: pl.Series,
        trades: pl.DataFrame,
        ic_series: pl.Series,
        risk_free_rate: float = 0.02,
    ) -> dict[str, float | None]:
        total_return = BacktestMetrics.calc_total_return(nav_curve)
        ann_return = BacktestMetrics.calc_annualized_return(total_return, len(nav_curve))
        volatility = BacktestMetrics.calc_volatility(daily_returns)
        sharpe = BacktestMetrics.calc_sharpe_ratio(daily_returns, risk_free_rate)
        max_dd, _, _ = BacktestMetrics.calc_max_drawdown(nav_curve)
        calmar = BacktestMetrics.calc_calmar_ratio(ann_return, max_dd)

        information_ratio, tracking_error = BacktestMetrics.calc_information_ratio(daily_returns, benchmark_returns)

        # R21: IC 序列中的 None（该期无法计算）剔除后取均值——否则样本不足的期数会把
        # 均值系统性拉向 0，伪装成「信号无效」。全为 None 或空序列时均值无定义 → None。
        _valid_ic = ic_series.drop_nulls()
        _ic_mean_raw = _valid_ic.mean() if len(_valid_ic) > 0 else None
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
            "ic_mean": float(cast(float, _ic_mean_raw)) if _ic_mean_raw is not None else None,
            "ic_ir": BacktestMetrics.calc_ir(ic_series, num_days=len(nav_curve)),
            "information_ratio": information_ratio,
            "tracking_error": tracking_error,
        }
