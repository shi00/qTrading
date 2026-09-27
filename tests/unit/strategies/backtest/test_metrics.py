"""回测指标计算模块单元测试"""

import math
from datetime import date

import polars as pl
import pytest

from strategies.backtest.metrics import TRADING_DAYS_PER_YEAR, BacktestMetrics

pytestmark = pytest.mark.unit


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
        ann_return = BacktestMetrics.calc_annualized_return(0.10, TRADING_DAYS_PER_YEAR)
        assert ann_return == pytest.approx(0.10, rel=0.01)

    def test_calc_annualized_return_zero_days(self) -> None:
        # D1-M4: 区间不足最短年化阈值，年化返回 None（不伪装为 0.0）
        assert BacktestMetrics.calc_annualized_return(0.10, 0) is None

    def test_calc_annualized_return_short_span_none(self) -> None:
        # D1-M4: 短区间（< 60 交易日）不做年化外推，返回 None
        assert BacktestMetrics.calc_annualized_return(0.08, 20) is None
        assert BacktestMetrics.calc_annualized_return(-0.08, 20) is None
        # 恰好在阈值上（60 天）仍正常年化
        assert BacktestMetrics.calc_annualized_return(0.10, 60) is not None

    def test_calc_annualized_return_zero_capital(self) -> None:
        # D1-M4: 本金归零（total_return == -1）返回 -1.0，而非高次方 NaN
        assert BacktestMetrics.calc_annualized_return(-1.0, 252) == -1.0

    def test_calc_annualized_return_ashare_trading_days(self) -> None:
        """MINOR-01 回归：年化分母取 A 股口径（243 天/年），而非美股 252。

        252 个交易日在 A 股口径下是 252/243 = 1.037 年而非整一年，年化必然低于区间收益。
        硬编码量级护栏（0.0963 ± 1e-4）进一步锁定分母确为 243——244 口径为 0.096677、
        252 口径为 0.100000，均越界。
        """
        ann_return = BacktestMetrics.calc_annualized_return(0.10, 252)
        assert ann_return is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert ann_return < 0.10
        assert ann_return == pytest.approx((1.10 ** (TRADING_DAYS_PER_YEAR / 252)) - 1, rel=1e-9)
        assert ann_return == pytest.approx(0.0963, abs=1e-4)

    def test_calc_volatility(self, sample_daily_returns: pl.Series) -> None:
        vol = BacktestMetrics.calc_volatility(sample_daily_returns)
        assert vol is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，真实断言在下一行 >0
        assert vol > 0

    def test_calc_volatility_insufficient_data(self) -> None:
        assert BacktestMetrics.calc_volatility(pl.Series([0.01])) is None

    def test_calc_sharpe_ratio(self, sample_daily_returns: pl.Series) -> None:
        sharpe = BacktestMetrics.calc_sharpe_ratio(sample_daily_returns, risk_free_rate=0.02)
        assert sharpe is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，真实断言在下一行 >0
        assert sharpe > 0

    def test_calc_sharpe_ratio_insufficient_data(self) -> None:
        assert BacktestMetrics.calc_sharpe_ratio(pl.Series([0.01]), 0.02) is None

    def test_calc_sharpe_ratio_zero_std(self) -> None:
        returns = pl.Series([0.01, 0.01, 0.01])
        assert BacktestMetrics.calc_sharpe_ratio(returns, 0.02) is None

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

    def test_calc_max_drawdown_monotonic_increasing(self) -> None:
        """单调递增（无回撤）时最大回撤为 0，峰值/谷底回落首元素（D5-m2 向量化语义）。"""
        max_dd, peak_idx, trough_idx = BacktestMetrics.calc_max_drawdown(pl.Series([100.0, 101.0, 102.0, 103.0]))
        assert max_dd == 0.0
        assert peak_idx == 0
        assert trough_idx == 0

    def test_calc_max_drawdown_leading_zero_no_crash(self) -> None:
        """nav 首项为 0（净值归零起点）时不再除零崩溃，真实回撤仍被正确计算（D5-m2）。

        原 Python 版本首项 0 时 (peak - nav) / peak = 0/0 抛 ZeroDivisionError；
        向量化版经 fill_nan 收敛，后续 100→90 的正常 10% 回撤照常度量。"""
        max_dd, peak_idx, trough_idx = BacktestMetrics.calc_max_drawdown(pl.Series([0.0, 100.0, 90.0]))
        assert max_dd == pytest.approx(0.1, rel=1e-6)
        assert peak_idx == 1
        assert trough_idx == 2

    def test_calc_calmar_ratio(self) -> None:
        calmar = BacktestMetrics.calc_calmar_ratio(0.15, 0.10)
        assert calmar == pytest.approx(1.5, rel=0.01)

    def test_calc_calmar_ratio_zero_drawdown(self) -> None:
        # D5-C1: max_drawdown == 0（全程无回撤，数学上 +∞）时 Calmar 无定义，返回 None
        assert BacktestMetrics.calc_calmar_ratio(0.15, 0.0) is None

    def test_calc_calmar_ratio_none_annualized(self) -> None:
        # D1-M4: 年化为 None（区间过短）时 Calmar 亦无定义，返回 None
        assert BacktestMetrics.calc_calmar_ratio(None, 0.10) is None

    def test_calc_win_rate(self) -> None:
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell", "sell", "sell"],
                "exit_reason": ["REBALANCE"] * 5,
                "realized_pnl": [100.0, -50.0, 200.0, -30.0, 50.0],
            }
        )
        win_rate = BacktestMetrics.calc_win_rate(trades)
        assert win_rate == pytest.approx(0.6, rel=0.01)

    def test_calc_win_rate_empty(self) -> None:
        """空交易（无任何数据）时胜率无定义，返回 None（D4-6）。"""
        assert BacktestMetrics.calc_win_rate(pl.DataFrame()) is None

    def test_calc_win_rate_excludes_buy_trades(self) -> None:
        """胜率仅统计卖出/平仓交易，买入交易不计入分母。"""
        trades = pl.DataFrame(
            {
                "action": ["buy", "buy", "sell", "sell", "sell"],
                "exit_reason": [None, None, "REBALANCE", "REBALANCE", "REBALANCE"],
                "realized_pnl": [0.0, 0.0, 100.0, -50.0, 200.0],
            }
        )
        # 3 笔 sell 中 2 笔盈利 → 胜率 2/3
        win_rate = BacktestMetrics.calc_win_rate(trades)
        assert win_rate == pytest.approx(2 / 3, rel=0.01)

    def test_calc_win_rate_all_buy_trades(self) -> None:
        """全部为买入交易时无平仓样本，胜率无定义，返回 None（D4-6）。"""
        trades = pl.DataFrame(
            {
                "action": ["buy", "buy"],
                "realized_pnl": [0.0, 0.0],
            }
        )
        assert BacktestMetrics.calc_win_rate(trades) is None

    def test_calc_win_rate_excludes_delisted_liquidation(self) -> None:
        """胜率排除退市强平（DELISTED）样本，仅为策略主动决策平仓（D4-6）。

        3 笔 REBALANCE（2 盈 1 亏）+ 1 笔 DELISTED（必亏）：
        分母只统计 3 笔 REBALANCE → 胜率 2/3，而非 4 笔全卖 → 2/4。
        """
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell", "sell"],
                "exit_reason": ["REBALANCE", "REBALANCE", "REBALANCE", "DELISTED"],
                "realized_pnl": [100.0, 200.0, -50.0, -500.0],
            }
        )
        win_rate = BacktestMetrics.calc_win_rate(trades)
        assert win_rate == pytest.approx(2 / 3, rel=0.01)

    def test_calc_win_rate_only_delisted_returns_none(self) -> None:
        """全部为退市强平时无主动决策平仓样本，胜率无定义，返回 None（D4-6）。"""
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell"],
                "exit_reason": ["DELISTED", "DELISTED", "DELISTED"],
                "realized_pnl": [-100.0, -200.0, -300.0],
            }
        )
        assert BacktestMetrics.calc_win_rate(trades) is None

    def test_calc_profit_factor(self) -> None:
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell", "sell", "sell"],
                "exit_reason": ["REBALANCE"] * 5,
                "realized_pnl": [100.0, -50.0, 200.0, -30.0, 50.0],
            }
        )
        pf = BacktestMetrics.calc_profit_factor(trades)
        assert pf == pytest.approx(350.0 / 80.0, rel=0.01)

    def test_calc_profit_factor_no_exit_reason_returns_none(self) -> None:
        """trades 缺 exit_reason 列时盈亏比无法区分主动/非策略平仓 → 无定义（D5-M1）。

        与 calc_win_rate 语义一致：缺少退出原因标注时无法判定主动决策样本，返回 None
        而非退化为「按全部 sell 统计」的另一套口径。"""
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell"],
                "realized_pnl": [100.0, -50.0, 200.0],
            }
        )
        assert BacktestMetrics.calc_profit_factor(trades) is None

    def test_calc_profit_factor_no_loss(self) -> None:
        """无亏损交易（主动决策平仓中）时返回 None（指标无定义），不返回 inf（D4-5）。"""
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell"],
                "exit_reason": ["REBALANCE", "REBALANCE", "REBALANCE"],
                "realized_pnl": [100.0, 200.0, 50.0],
            }
        )
        assert BacktestMetrics.calc_profit_factor(trades) is None

    def test_calc_profit_factor_no_profit(self) -> None:
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell"],
                "exit_reason": ["REBALANCE", "REBALANCE"],
                "realized_pnl": [-100.0, -50.0],
            }
        )
        assert BacktestMetrics.calc_profit_factor(trades) == 0.0

    def test_calc_profit_factor_excludes_buy_trades(self) -> None:
        """盈亏比仅统计卖出/平仓交易。"""
        trades = pl.DataFrame(
            {
                "action": ["buy", "sell", "sell"],
                "exit_reason": [None, "REBALANCE", "REBALANCE"],
                "realized_pnl": [0.0, 100.0, -50.0],
            }
        )
        # 仅统计 sell: gross_profit=100, gross_loss=50 → pf=2.0
        pf = BacktestMetrics.calc_profit_factor(trades)
        assert pf == pytest.approx(2.0, rel=0.01)

    def test_calc_profit_factor_excludes_delisted(self) -> None:
        """盈亏比排除退市强平（DELISTED），与胜率共用同一决策平仓口径（D5-M1）。

        3 笔 REBALANCE（2 盈 1 亏）+ 1 笔 DELISTED 大额亏损：
        - 只统计 3 笔 REBALANCE → pf = 300/50 = 6.0（不含退市笔则 300/550 ≈ 0.545，口径差异被误读为「小赢大亏」）
        - win_rate 同基于这 3 笔 → 2/3
        """
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell", "sell"],
                "exit_reason": ["REBALANCE", "REBALANCE", "REBALANCE", "DELISTED"],
                "realized_pnl": [100.0, 200.0, -50.0, -500.0],
            }
        )
        pf = BacktestMetrics.calc_profit_factor(trades)
        win_rate = BacktestMetrics.calc_win_rate(trades)
        assert pf == pytest.approx(6.0, rel=0.01)
        assert win_rate == pytest.approx(2 / 3, rel=0.01)

    def test_calc_profit_factor_empty(self) -> None:
        """空交易（无任何数据）时返回 None（指标无定义）。
        覆盖空交易分支（无交易 → None），与 D4-5 语义一致。"""
        trades = pl.DataFrame()
        assert BacktestMetrics.calc_profit_factor(trades) is None

    def test_calc_profit_factor_only_buy_trades(self) -> None:
        """全部为买入交易（无卖出/平仓）时返回 None（指标无定义）。
        覆盖无平仓交易分支（无 sell → None），与 D4-5 语义一致。"""
        trades = pl.DataFrame(
            {
                "action": ["buy", "buy"],
                "realized_pnl": [0.0, 0.0],
            }
        )
        assert BacktestMetrics.calc_profit_factor(trades) is None

    def test_calc_ic(self) -> None:
        signal_rank = pl.Series([1, 2, 3, 4, 5])
        forward_return = pl.Series([5.0, 3.0, 0.0, -2.0, -4.0])
        ic = BacktestMetrics.calc_ic(signal_rank, forward_return)
        assert ic is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，真实断言在下一行 <0
        assert ic < 0

    def test_calc_ic_insufficient_data(self) -> None:
        # D5-C1: 样本 < 3 时 Spearman 相关无定义，返回 None（区分「未测量」vs「测量为无效」）
        assert BacktestMetrics.calc_ic(pl.Series([1, 2]), pl.Series([1.0, 2.0])) is None

    def test_calc_ir(self) -> None:
        ic_series = pl.Series([0.05, 0.03, 0.07, 0.02, 0.04])
        ir = BacktestMetrics.calc_ir(ic_series)
        assert ir is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，后续强断言验证具体值
        assert ir > 0

    def test_calc_ir_annualization_factor(self) -> None:
        """F3-03: 动态年化系数 sqrt(ic_count / years) 验证。

        相同 IC 序列、区间交易日数翻倍（years 翻倍）时 IR 应为 2^0.5 倍。
        years = num_days / TRADING_DAYS_PER_YEAR，本用例断言的是比值，故结论与年化
        口径取值无关（A 股 243 或美股 252 均成立）。
        """
        ic_series = pl.Series([0.05, 0.03, 0.07, 0.02, 0.04])
        ir_252 = BacktestMetrics.calc_ir(ic_series, num_days=252)
        ir_126 = BacktestMetrics.calc_ir(ic_series, num_days=126)
        # num_days 减半 → years 减半（1.037 → 0.519）→ factor 由 sqrt(5/1.037) 增至
        # sqrt(5/0.519)，比值恒为 sqrt(2) ≈ 1.4142
        assert ir_252 is not None
        assert ir_126 is not None
        assert ir_252 > 0
        assert ir_126 > 0
        assert abs(ir_126 / ir_252 - (2**0.5)) < 1e-6

    def test_calc_ir_default_assumes_one_year(self) -> None:
        """MINOR-01 回归：calc_ir 默认 num_days 与年化分母同口径 → 默认区间恰为 1 年。

        全默认参数下 years = 243/243 = 1.0，年化系数 sqrt(5/1) ≈ 2.2361，IR ≈ 4.8824。
        若 num_days 默认值与其分母脱节（如单独回退为 252 → years = 1.037），年化系数
        降至 2.1958、IR ≈ 4.7944，本用例失败。
        防护范围说明：本用例只挡「num_days 默认值与年化分母脱节」；两者同时回退为 252 时
        years 仍为 1.0、IR 不变（4.8824），该场景由 test_calc_annualized_return_ashare_trading_days
        的硬编码量级用例覆盖。
        """
        ir = BacktestMetrics.calc_ir(pl.Series([0.05, 0.03, 0.07, 0.02, 0.04]))
        assert ir is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert ir == pytest.approx(4.8824, rel=1e-3)

    def test_calc_ir_zero_std(self) -> None:
        # D5-C1: IC 零波动（信号无稳定性）时 IR 无定义，返回 None
        ic_series = pl.Series([0.05, 0.05, 0.05])
        assert BacktestMetrics.calc_ir(ic_series) is None

    def test_calc_ir_all_none(self) -> None:
        # D5-C1: IC 序列全部为 None（该期都无法计算）时 IR 无定义，返回 None，
        # 而非误判为「信号无稳定性」(0.0)。
        ic_series = pl.Series([None, None, None])
        assert BacktestMetrics.calc_ir(ic_series) is None

    def test_calc_ir_zero_num_days_uses_fallback(self) -> None:
        """F3-03: num_days=0 时 years fallback 到 1.0，不爆除零。"""
        ic_series = pl.Series([0.05, 0.03, 0.07, 0.02, 0.04])
        ir = BacktestMetrics.calc_ir(ic_series, num_days=0)
        assert ir is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，后续强断言验证具体值
        assert math.isfinite(ir)

    def test_calc_information_ratio(
        self,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        ir, te = BacktestMetrics.calc_information_ratio(
            sample_daily_returns,
            sample_benchmark_returns,
        )
        assert ir is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，后续强断言验证具体值
        assert te is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，后续强断言验证具体值
        assert ir > 0
        assert te > 0

    def test_calc_information_ratio_insufficient_data(self) -> None:
        ir, te = BacktestMetrics.calc_information_ratio(
            pl.Series([0.01]),
            pl.Series([0.008]),
        )
        # D5-C1: 有效样本不足 2 时信息比率/跟踪误差无定义，返回 None
        assert ir is None
        assert te is None

    def test_calc_information_ratio_drops_null_benchmark_days(self) -> None:
        # D1-M1: 基准缺失（null）不应污染信息比率；结果应与"调用前剔除 null 行"等价。
        ir, te = BacktestMetrics.calc_information_ratio(
            pl.Series([0.01, 0.02, 0.03, 0.04]),
            pl.Series([0.008, None, 0.006, 0.01]),
        )
        ir_expected, te_expected = BacktestMetrics.calc_information_ratio(
            pl.Series([0.01, 0.03, 0.04]),
            pl.Series([0.008, 0.006, 0.01]),
        )
        assert ir == pytest.approx(ir_expected)
        assert te == pytest.approx(te_expected)

    def test_annualization_factors_use_ashare_trading_days(
        self,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """MINOR-01 回归：年化系数为 √243（A 股口径），而非 √252。

        断言「显式 252 口径结果 == 默认结果 × √(252/243)」，等价于锁定默认分母为
        TRADING_DAYS_PER_YEAR：波动率/跟踪误差/信息比率严格满足该比例。夏普的日无风险
        利率 = risk_free_rate / trading_days_per_year 也随口径变化，故夏普部分固定
        risk_free_rate=0.0（此时严格按 √T 缩放）。

        本用例为**自参照比例断言**（scale 由 TRADING_DAYS_PER_YEAR 自身推导），只验证
        「默认参数 ≠ 显式 252」的缩放关系，不锁定常量取值本身；「常量必须为 243 而非
        252」由 test_calc_annualized_return_ashare_trading_days 的硬编码量级与
        test_calc_all_metrics_uses_ashare_annualization 的夏普绝对值用例锁定。
        """
        scale = math.sqrt(252 / TRADING_DAYS_PER_YEAR)

        vol = BacktestMetrics.calc_volatility(sample_daily_returns)
        vol_us = BacktestMetrics.calc_volatility(sample_daily_returns, trading_days_per_year=252)
        assert vol is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert vol_us is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert vol_us == pytest.approx(vol * scale, rel=1e-9)

        sharpe = BacktestMetrics.calc_sharpe_ratio(sample_daily_returns, risk_free_rate=0.0)
        sharpe_us = BacktestMetrics.calc_sharpe_ratio(
            sample_daily_returns, risk_free_rate=0.0, trading_days_per_year=252
        )
        assert sharpe is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert sharpe_us is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert sharpe_us == pytest.approx(sharpe * scale, rel=1e-9)

        ir, te = BacktestMetrics.calc_information_ratio(sample_daily_returns, sample_benchmark_returns)
        ir_us, te_us = BacktestMetrics.calc_information_ratio(
            sample_daily_returns,
            sample_benchmark_returns,
            trading_days_per_year=252,
        )
        assert ir is not None and te is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert ir_us is not None and te_us is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert ir_us == pytest.approx(ir * scale, rel=1e-9)
        assert te_us == pytest.approx(te * scale, rel=1e-9)

    def test_calc_all_metrics(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        trades = pl.DataFrame(
            {
                "action": ["sell", "sell", "sell"],
                "exit_reason": ["REBALANCE", "REBALANCE", "REBALANCE"],
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

        assert metrics["total_return"] is not None and metrics["total_return"] > 0
        assert metrics["sharpe_ratio"] is not None and metrics["sharpe_ratio"] > 0
        assert metrics["max_drawdown"] is not None and metrics["max_drawdown"] >= 0

    def test_calc_all_metrics_uses_ashare_annualization(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """MINOR-01 回归：生产链路 calc_all_metrics 走 A 股口径（未显式传美股 252）。

        该函数对年化相关指标不显式传参、依赖默认值，故其输出必须等于「显式 252 口径
        结果 ÷ √(252/243)」。此用例防的是另一种回归：日后有人在 calc_all_metrics 内
        硬编码 252，此时默认值层面的用例（如上一条）察觉不到。
        """
        scale = math.sqrt(252 / TRADING_DAYS_PER_YEAR)
        trades = pl.DataFrame({"action": ["sell"], "exit_reason": ["REBALANCE"], "realized_pnl": [10.0]})
        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            pl.Series([0.05, 0.03, 0.07]),
        )
        vol_us = BacktestMetrics.calc_volatility(sample_daily_returns, trading_days_per_year=252)
        ir_us, te_us = BacktestMetrics.calc_information_ratio(
            sample_daily_returns,
            sample_benchmark_returns,
            trading_days_per_year=252,
        )
        assert vol_us is not None and ir_us is not None and te_us is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert metrics["volatility"] is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert metrics["information_ratio"] is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert metrics["tracking_error"] is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert metrics["volatility"] == pytest.approx(vol_us / scale, rel=1e-9)
        assert metrics["information_ratio"] == pytest.approx(ir_us / scale, rel=1e-9)
        assert metrics["tracking_error"] == pytest.approx(te_us / scale, rel=1e-9)

        # ic_ir 同样依赖 trading_days_per_year 默认口径（calc_all_metrics 显式传
        # num_days=len(nav_curve)，年化分母仍取默认值），故亦按 √(252/243) 缩放——
        # 若有人在 calc_all_metrics 内硬编码 252 传参，本断言即失败。
        ic_ir_us = BacktestMetrics.calc_ir(
            pl.Series([0.05, 0.03, 0.07]),
            num_days=len(sample_nav_curve),
            trading_days_per_year=252,
        )
        assert ic_ir_us is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert metrics["ic_ir"] is not None  # noqa: weak-assertion 返回类型已放宽为 float|None，is not None 为类型收窄守卫，数值断言见下
        assert metrics["ic_ir"] == pytest.approx(ic_ir_us / scale, rel=1e-9)

        # 夏普走 rf=0.02 默认路径（calc_all_metrics 的默认无风险利率），不能按 √T 折算：
        # daily_rf = risk_free_rate / trading_days_per_year 也随口径变化，实测「252 / 默认」
        # = 1.0188（≠ 1.0184）。故用绝对量级锁定默认口径——252 口径为 7.3757，越界。
        assert metrics["sharpe_ratio"] == pytest.approx(7.2396, rel=1e-4)

    def test_calc_all_metrics_ic_mean_skips_none(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """D5-C1: ic_mean 剔除「该期无法计算」的 None，而非当作 0 参与平均拉低均值。

        若把 None 当 0，[0.05, None, 0.03] 会算出 0.0267，伪装成「信号无效」。
        """
        trades = pl.DataFrame({"action": ["sell"], "exit_reason": ["REBALANCE"], "realized_pnl": [100.0]})
        ic_series = pl.Series([0.05, None, 0.03])
        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            ic_series,
            risk_free_rate=0.02,
        )
        assert metrics["ic_mean"] == pytest.approx(0.04)
        assert metrics["ic_ir"] is not None  # noqa: weak-assertion ic_ir 由有效 IC 序列计算必为定义值，is not None 守卫类型收窄

    def test_calc_all_metrics_ic_mean_empty_series_is_none(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """D5-C1: IC 序列为空（无任何可计算期）时 ic_mean 无定义，返回 None。"""
        trades = pl.DataFrame({"action": ["sell"], "exit_reason": ["REBALANCE"], "realized_pnl": [100.0]})
        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            pl.Series([], dtype=pl.Float64),
            risk_free_rate=0.02,
        )
        assert metrics["ic_mean"] is None

    def test_calc_all_metrics_ic_weighted_by_sample_sizes(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """BT-07: ic_mean 按样本数加权 Σ(nᵢ·icᵢ)/Σ(nᵢ)，样本更充分的日期权重更高。"""
        trades = pl.DataFrame({"action": ["sell"], "exit_reason": ["REBALANCE"], "realized_pnl": [100.0]})
        ic_series = pl.Series([0.1, 0.0])
        ic_sample_sizes = pl.Series([100, 20])  # 第一天 100 样本（ic=0.1），第二天 20 样本（ic=0）
        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            ic_series,
            risk_free_rate=0.02,
            ic_sample_sizes=ic_sample_sizes,
        )
        # 权重均值 = (100*0.1 + 20*0.0) / 120 = 0.0833…，而非等权 0.05
        assert metrics["ic_mean"] == pytest.approx(100 * 0.1 / 120, rel=1e-6)
        assert metrics["ic_mean"] != pytest.approx(0.05, rel=1e-6)

    def test_calc_all_metrics_ic_attached_stats(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """BT-07: 附带 ic_valid_days（实际参与计算天数）与 ic_median_n（每日样本数中位数）。"""
        trades = pl.DataFrame({"action": ["sell"], "exit_reason": ["REBALANCE"], "realized_pnl": [100.0]})
        ic_series = pl.Series([0.1, 0.2, 0.15])
        ic_sample_sizes = pl.Series([100, 30, 30])
        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            ic_series,
            risk_free_rate=0.02,
            ic_sample_sizes=ic_sample_sizes,
        )
        assert metrics["ic_valid_days"] == 3
        assert metrics["ic_median_n"] == pytest.approx(30.0)  # 中位数 [30,30,100]

    def test_calc_all_metrics_ic_stats_empty_series(
        self,
        sample_nav_curve: pl.Series,
        sample_daily_returns: pl.Series,
        sample_benchmark_returns: pl.Series,
    ) -> None:
        """BT-07: 空 IC 序列时 ic_valid_days=0 且 ic_median_n=None。"""
        trades = pl.DataFrame({"action": ["sell"], "exit_reason": ["REBALANCE"], "realized_pnl": [100.0]})
        metrics = BacktestMetrics.calc_all_metrics(
            sample_nav_curve,
            sample_daily_returns,
            sample_benchmark_returns,
            trades,
            pl.Series([], dtype=pl.Float64),
            risk_free_rate=0.02,
        )
        assert metrics["ic_valid_days"] == 0
        assert metrics["ic_median_n"] is None

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
        assert returns[0] is None
        assert float(returns[1]) == pytest.approx(0.01, rel=1e-4)
        assert float(returns[2]) == pytest.approx(-0.00495, abs=1e-4)

    def test_calc_daily_returns_single_value(self) -> None:
        nav = pl.Series([1_000_000.0])
        returns = BacktestMetrics.calc_daily_returns(nav)
        assert len(returns) == 1
        assert float(returns[0]) == 0.0

    def test_calc_daily_returns_handles_inf(self) -> None:
        """nav_curve 含 0 值（爆仓）时 pct_change 产生 inf，应保留为 null 而非 0.0（D5-M2）。

        净值归零是终止条件不是数值噪声：抹成 0.0 会让 volatility 低估、sharpe 被高估，
        与 total_return/max_drawdown 的 -100% 自相矛盾。"""
        nav = pl.Series([0.0, 100.0, 105.0, 110.0])
        returns = BacktestMetrics.calc_daily_returns(nav)
        # 首项保持 null
        assert returns[0] is None
        # 爆仓（0→正）日收益无定义 → null，而非 0.0
        assert returns[1] is None
        # 不应包含 inf 或 nan
        assert not returns.is_infinite().any()
        assert not returns.is_nan().any()

    def test_calc_investment_metrics_avg_min_drag(self) -> None:
        """BT-03: 基于每日持仓快照正确计算平均/最低投资比例与现金拖累天数。"""
        positions = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
                "cash": [400_000.0, 500_000.0, 100_000.0, 900_000.0],
                "total_value": [1_000_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0],
            }
        )
        m = BacktestMetrics.calc_investment_metrics(positions)
        # invested_pct = (total - cash)/total = [0.6, 0.5, 0.9, 0.1]
        assert m["avg_invested_pct"] == pytest.approx(0.525, rel=1e-6)
        assert m["min_invested_pct"] == pytest.approx(0.1, rel=1e-6)
        # 投资比例 < 0.8 的天数 = 3（0.6, 0.5, 0.1）
        assert m["cash_drag_days"] == 3

    def test_calc_investment_metrics_full_invested(self) -> None:
        positions = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "cash": [0.0, 20_000.0],
                "total_value": [1_000_000.0, 1_000_000.0],
            }
        )
        m = BacktestMetrics.calc_investment_metrics(positions)
        assert m["avg_invested_pct"] == pytest.approx(0.99, rel=1e-2)
        assert m["min_invested_pct"] == pytest.approx(0.98, rel=1e-2)
        assert m["cash_drag_days"] == 0

    def test_calc_investment_metrics_empty_or_missing_columns(self) -> None:
        assert BacktestMetrics.calc_investment_metrics(pl.DataFrame()) == {
            "avg_invested_pct": 0.0,
            "min_invested_pct": 0.0,
            "cash_drag_days": 0,
        }
        missing = pl.DataFrame({"trade_date": [date(2024, 1, 2)], "cash": [100.0]})
        assert BacktestMetrics.calc_investment_metrics(missing)["avg_invested_pct"] == 0.0
