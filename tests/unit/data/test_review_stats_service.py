"""复盘聚合统计服务单元测试（UX-05，设计 v5 §2/§6）。

统计计算为纯函数（无 DB），覆盖：
- t 分布固定内插表（df=1..179）与 30/100 边界精确值（四审 L2）
- 置信区间（n<30 置 None、n<3 std 置 None、n>=30 且 std 有效时计算）
- Newey-West HAC 修正（RV-08）：t5/alpha 重叠窗口标准误、overlap=1 零回归
- 有效样本量分级（RV-09）：n_eff = n/overlap，alpha_grade 按 n_eff 判定不再用名义 N
- 指标独立 N（t1/t5/alpha 各以非 NULL 日序列独立计算，四审 M4）
- 胜率用股票行 N 独立于日序列 N、独立分级（四审 M2）
- 基准 NULL 组（benchmark_code IS NULL）归「基准未知」组并排在有可比组之后（四审 L3）
- 空输入安全降级、日均纳入股票数（四审 M3）
"""

import math
import random
from datetime import date, timedelta

import pandas as pd
import pytest

from data.domain_services.review_stats_service import (
    HORIZON_WINDOW_OVERLAP,
    REVIEW_ADEQUATE_SAMPLE,
    REVIEW_MIN_SAMPLE,
    SampleGrade,
    T1_WINDOW_OVERLAP,
    _T_CRIT_0_975,
    _metric_stat,
    _newey_west_se,
    _t_crit,
    compute_ai_attribution_stats,
    compute_strategy_review_stats,
    grade_for,
)

pytestmark = pytest.mark.unit


def _metric_row(
    *,
    strat: str,
    bm: str | None,
    day: int,
    t1: float | None,
    alpha: float | None,
    t5: float | None = None,
    daily_cnt: int = 1,
    win: int = 0,
    loss: int = 0,
) -> dict:
    """构建一行 DAO 日级聚合（get_strategy_review_stats 产出形状）。"""
    return {
        "trade_date": date(2026, 1, 1) + timedelta(days=day),
        "strategy_name": strat,
        "benchmark_code": bm,
        "daily_cnt": daily_cnt,
        "t1_mean": t1,
        "t1_n": (0 if t1 is None else 1),
        "t5_mean": t5,
        "t5_n": (0 if t5 is None else 1),
        "alpha_mean": alpha,
        "alpha_n": (0 if alpha is None else 1),
        "win_cnt": win,
        "loss_cnt": loss,
    }


def _ai_metric_row(
    *,
    strat: str,
    bm: str | None,
    has_ai: bool,
    day: int,
    t1: float | None,
    alpha: float | None,
    t5: float | None = None,
    daily_cnt: int = 1,
    win: int = 0,
    loss: int = 0,
) -> dict:
    """构建一行 AI 归因 DAO 日级聚合（get_ai_attribution_stats 产出形状）。"""
    row = _metric_row(
        strat=strat,
        bm=bm,
        day=day,
        t1=t1,
        alpha=alpha,
        t5=t5,
        daily_cnt=daily_cnt,
        win=win,
        loss=loss,
    )
    row["has_ai"] = has_ai
    return row


class TestTFixedTable:
    """t 分布 0.975 分位内插表（四审 L2）。"""

    def test_table_covers_df_1_to_179(self) -> None:
        assert len(_T_CRIT_0_975) == 179
        # df=1 边界值（索引 0）应为最大临界值
        assert _T_CRIT_0_975[0] == pytest.approx(12.70620, rel=1e-5)
        # 随 df 增大临界值单调递减
        assert all(_T_CRIT_0_975[i] > _T_CRIT_0_975[i + 1] for i in range(len(_T_CRIT_0_975) - 1))

    def test_boundary_29_30_99_100(self) -> None:
        """df=29/30/99/100 精确匹配固定表（四审 L2 承诺的边界精度）。"""
        assert _t_crit(29) == pytest.approx(2.04523)
        assert _t_crit(30) == pytest.approx(2.04227)
        assert _t_crit(99) == pytest.approx(1.98422)
        assert _t_crit(100) == pytest.approx(1.98397)

    def test_clamp_beyond_179(self) -> None:
        # df>179 钳制到表尾近似值（近 1.96），保守偏宽
        assert _t_crit(200) == _T_CRIT_0_975[178]
        assert _t_crit(179) == _T_CRIT_0_975[178]


class TestGrade:
    def test_grade_boundaries(self) -> None:
        assert grade_for(0) is SampleGrade.INSUFFICIENT
        assert grade_for(REVIEW_MIN_SAMPLE - 1) is SampleGrade.INSUFFICIENT
        assert grade_for(REVIEW_MIN_SAMPLE) is SampleGrade.LIMITED
        assert grade_for(REVIEW_ADEQUATE_SAMPLE - 1) is SampleGrade.LIMITED
        assert grade_for(REVIEW_ADEQUATE_SAMPLE) is SampleGrade.ADEQUATE

    def test_grade_accepts_float_n_eff(self) -> None:
        """RV-09: grade_for 接受浮点 n_eff，浮点阈值比较天然正确（29.8 < 30）。"""
        assert grade_for(29.8) is SampleGrade.INSUFFICIENT
        assert grade_for(30.0) is SampleGrade.LIMITED
        assert grade_for(99.9) is SampleGrade.LIMITED
        assert grade_for(100.0) is SampleGrade.ADEQUATE


class TestEffectiveSampleSize:
    """RV-09: 样本量分级按有效样本量 n_eff = n/overlap 判定，不再用名义日序列 N。"""

    def test_alpha_grade_uses_n_eff_not_nominal_n(self) -> None:
        """n=120（≈180 自然日统计窗口上限）→ n_eff=24 → INSUFFICIENT。

        旧逻辑按名义 N=120 判 ADEQUATE，而 5 日重叠窗口的有效独立样本仅约
        24——「样本充足」是虚假安全感，RV-09 核心回归用例。
        """
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)) for i in range(120)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 120
        assert row.alpha.n_eff == pytest.approx(24.0)
        assert row.alpha_grade is SampleGrade.INSUFFICIENT

    def test_n_eff_grade_boundaries(self) -> None:
        """n_eff 边界：n=150→n_eff=30→LIMITED；n=500→n_eff=100→ADEQUATE（IEEE754 精确除法）。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)) for i in range(150)]
        (limited_row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert limited_row.alpha.n_eff == pytest.approx(30.0)
        assert limited_row.alpha_grade is SampleGrade.LIMITED

        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)) for i in range(500)]
        (adequate_row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert adequate_row.alpha.n_eff == pytest.approx(100.0)
        assert adequate_row.alpha_grade is SampleGrade.ADEQUATE

    def test_t1_n_eff_equals_n(self) -> None:
        """overlap=1（t1）时 n_eff == n，分级语义不变（零回归）。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=None) for i in range(40)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.t1.n == 40
        assert row.t1.n_eff == pytest.approx(40.0)

    def test_t5_n_eff_uses_horizon_overlap(self) -> None:
        """t5 与 alpha 同为 5 日窗口，n_eff 同口径折算。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=None, t5=float(i)) for i in range(35)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.t5.n == 35
        assert row.t5.n_eff == pytest.approx(7.0)

    def test_empty_series_n_eff_zero(self) -> None:
        stat = _metric_stat(pd.Series([float("nan"), float("nan")]))
        assert stat.n == 0
        assert stat.n_eff == 0.0

    def test_ai_attribution_alpha_grade_uses_n_eff(self) -> None:
        """AiAttributionRow.alpha_grade 与 StrategyStatRow 同口径（n_eff 判定）。"""
        rows = [
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=i, t1=float(i), alpha=float(i))
            for i in range(120)
        ]
        (row,) = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert row.alpha.n == 120
        assert row.alpha.n_eff == pytest.approx(24.0)
        assert row.alpha_grade is SampleGrade.INSUFFICIENT


class TestConfidenceInterval:
    def test_ci_present_when_n_ge_min_sample(self) -> None:
        """n=30（df=29）且 std 有效 → t1 CI = mean ± t*std/sqrt(n)（overlap=1 朴素公式，RV-08 后不变）；
        alpha（overlap=5）同序列改走 Newey-West，区间存在且宽于朴素公式。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)) for i in range(30)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.t1.n == 30
        assert row.t1.mean == pytest.approx(14.5)
        expected_std = float(pd.Series(range(30)).std(ddof=1))
        assert row.t1.std == pytest.approx(expected_std)
        half = _t_crit(29) * expected_std / math.sqrt(30)
        assert row.t1.ci_lower == pytest.approx(14.5 - half)
        assert row.t1.ci_upper == pytest.approx(14.5 + half)
        # RV-08：alpha 为 5 日重叠窗口，标准误经 NW 修正后区间必须宽于朴素公式
        assert row.alpha.ci_lower is not None
        assert row.alpha.ci_upper is not None
        assert (row.alpha.ci_upper - row.alpha.ci_lower) > 2.0 * half

    def test_ci_absent_when_n_below_min_sample(self) -> None:
        """n=29 < 30 → CI 置 None，但 std（n>=3）仍给出，均值给出。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)) for i in range(29)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 29
        assert row.alpha.mean == pytest.approx(14.0)
        assert row.alpha.std is not None
        assert row.alpha.ci_lower is None
        assert row.alpha.ci_upper is None

    def test_std_none_when_n_below_3(self) -> None:
        """n=2 < 3 → std 与 CI 均置 None，均值仍给出。"""
        rows = [
            _metric_row(strat="sA", bm="sh000001", day=0, t1=1.0, alpha=1.0),
            _metric_row(strat="sA", bm="sh000001", day=1, t1=2.0, alpha=2.0),
        ]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 2
        assert row.alpha.mean == pytest.approx(1.5)
        assert row.alpha.std is None
        assert row.alpha.ci_lower is None
        assert row.alpha.ci_upper is None

    def test_std_none_when_constant_series(self) -> None:
        """n>=30 但日组合均值恒定（std=0）→ CI 无可计算，置 None（不伪装有效区间）。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=0.0, alpha=0.0) for i in range(35)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 35
        assert row.alpha.std is None
        assert row.alpha.ci_lower is None
        assert row.alpha.ci_upper is None


class TestNeweyWest:
    """RV-08：重叠窗口（t5/alpha，overlap=5）标准误的 Newey-West HAC 修正。"""

    @staticmethod
    def _rolling_sum_series(n_days: int, window: int, seed: int) -> pd.Series:
        """构造滚动和序列（i.i.d. 日收益 e_t，x_t = Σ_{i=0..w-1} e_{t+i}）。

        即 t5_pct 的真实统计结构：window=5 时相邻点共享 4 天行情。i.i.d. 假设下
        NW/朴素 SE 比值的理论值为 sqrt(17/5) ≈ 1.844
        （V_NW = γ0 + 2·Σ_{l=1..4}(1-l/5)(5-l)σ² = 17σ²，朴素 var = 5σ²）；
        检视报告的 sqrt(5) 为 lag1-4 自相关全 1 的理想化上界。
        """
        rnd = random.Random(seed)
        e = [rnd.gauss(0.0, 1.0) for _ in range(n_days + window)]
        return pd.Series([sum(e[t : t + window]) for t in range(n_days)])

    def test_nw_se_exceeds_naive_on_overlapping_series(self) -> None:
        """判据用例（RV-08）：5 日重叠序列的 NW SE 显著大于朴素 SE，
        比值接近理论值 sqrt(17/5)≈1.844（容忍采样噪声）。修复前无 NW 路径，该断言必失败。"""
        values = self._rolling_sum_series(n_days=500, window=HORIZON_WINDOW_OVERLAP, seed=20260921)
        n = int(values.size)
        naive = float(values.std(ddof=1)) / math.sqrt(n)
        nw = _newey_west_se(values, lags=HORIZON_WINDOW_OVERLAP - 1)
        assert nw is not None
        ratio = nw / naive
        assert ratio > 1.5  # 修正生效：显著大于朴素
        assert 1.69 < ratio < 2.0  # 量级符合理论值 sqrt(17/5)≈1.844

    def test_overlap1_bitwise_identical_to_pre_fix_formula(self) -> None:
        """overlap=1（默认与显式传参）与修复前朴素公式逐位一致（RV-08 必补回归测试）。"""
        values = self._rolling_sum_series(n_days=60, window=1, seed=7)
        default_stat = _metric_stat(values)
        explicit_stat = _metric_stat(values, overlap=T1_WINDOW_OVERLAP)
        assert default_stat == explicit_stat
        # 与朴素公式手算逐位一致（表达式顺序与实现相同）
        n = int(values.size)
        std = float(values.std(ddof=1))
        half = _t_crit(n - 1) * std / math.sqrt(n)
        assert default_stat.ci_lower == float(values.mean()) - half
        assert default_stat.ci_upper == float(values.mean()) + half

    def test_t5_alpha_ci_wider_than_naive_via_compute(self) -> None:
        """经 compute_strategy_review_stats 全链路：t5/alpha（overlap=5）CI 宽于同序列
        朴素公式，t1 CI 与朴素公式一致（周期锯齿序列，lag1-4 强正自相关）。"""
        rows = [
            _metric_row(strat="sA", bm="sh000001", day=i, t1=float(i % 10), alpha=float(i % 10), t5=float(i % 10))
            for i in range(40)
        ]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        s = pd.Series([float(i % 10) for i in range(40)])
        n = int(s.size)
        std = float(s.std(ddof=1))
        naive_half = _t_crit(n - 1) * std / math.sqrt(n)
        for stat in (row.t5, row.alpha):
            assert stat.ci_lower is not None
            assert stat.ci_upper is not None
            assert (stat.ci_upper - stat.ci_lower) > 2.0 * naive_half
        assert row.t1.ci_lower == pytest.approx(float(s.mean()) - naive_half)
        assert row.t1.ci_upper == pytest.approx(float(s.mean()) + naive_half)

    def test_ai_attribution_alpha_ci_uses_nw(self) -> None:
        """compute_ai_attribution_stats 的 t5/alpha 同样走 NW 修正、t1 保持朴素（两处 compute 调用全覆盖）。"""
        rows = [
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=i, t1=float(i), alpha=float(i), t5=float(i))
            for i in range(40)
        ]
        (row,) = compute_ai_attribution_stats(pd.DataFrame(rows))
        s = pd.Series([float(i) for i in range(40)])
        n = int(s.size)
        std = float(s.std(ddof=1))
        naive_half = _t_crit(n - 1) * std / math.sqrt(n)
        for stat in (row.t5, row.alpha):
            assert stat.ci_lower is not None
            assert stat.ci_upper is not None
            assert (stat.ci_upper - stat.ci_lower) > 2.0 * naive_half
        assert row.t1.ci_lower == pytest.approx(float(s.mean()) - naive_half)
        assert row.t1.ci_upper == pytest.approx(float(s.mean()) + naive_half)


class TestMetricIndependentN:
    def test_metrics_use_independent_nonnull_daily_series(self) -> None:
        """alpha 仅部分日期有效：alpha 用其独立 N，t1 用其完整 N（四审 M4）。"""
        rows: list[dict] = []
        for i in range(40):
            # alpha 仅 i 为偶数时有效（20 日）
            alpha = float(i) if i % 2 == 0 else None
            rows.append(_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=alpha))
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.t1.n == 40
        assert row.alpha.n == 20
        assert row.alpha.mean is not None
        # alpha n=20 < 30 → CI None
        assert row.alpha.ci_lower is None
        assert row.alpha.ci_upper is None

    def test_metric_stat_empty_series(self) -> None:
        stat = _metric_stat(pd.Series([float("nan"), float("nan")]))
        assert stat.n == 0
        assert stat.mean is None
        assert stat.std is None
        assert stat.ci_lower is None
        assert stat.ci_upper is None


class TestWinRate:
    def test_win_rate_uses_stock_n_independent_of_daily_n(self) -> None:
        """胜率股票行 N 与日序列 N 独立，独立分级（四审 M2 + RV-09 n_eff）。"""
        # 150 个交易日（RV-09 后 alpha 按有效样本 n_eff=30 → LIMITED），
        # 但仅累计 1 胜（股票行 N=1 → INSUFFICIENT）——两级独立可不同档。
        rows: list[dict] = []
        rows.append(_metric_row(strat="sA", bm="sh000001", day=0, t1=0.0, alpha=0.0, win=1, loss=0))
        for i in range(1, 150):
            rows.append(_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)))
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 150
        assert row.alpha.n_eff == pytest.approx(30.0)
        assert row.win_n == 1
        assert row.win_rate == pytest.approx(1.0)
        assert row.alpha_grade is SampleGrade.LIMITED
        assert row.win_grade is SampleGrade.INSUFFICIENT

    def test_win_rate_ratio(self) -> None:
        rows = [
            _metric_row(strat="sA", bm="sh000001", day=0, t1=1.0, alpha=1.0, win=7, loss=3),
            _metric_row(strat="sA", bm="sh000001", day=1, t1=2.0, alpha=2.0, win=3, loss=4),
        ]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.win_n == 17
        assert row.win_rate == pytest.approx(10 / 17)
        assert row.loss_count == 7

    def test_win_rate_none_when_no_games(self) -> None:
        rows = [_metric_row(strat="sA", bm="sh000001", day=0, t1=1.0, alpha=1.0)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.win_n == 0
        assert row.win_rate is None  # 无样本以 None 哨兵表达，不伪装 0（R21）
        assert row.win_grade is SampleGrade.INSUFFICIENT


class TestGrouping:
    def test_benchmark_null_group_isolated_and_last(self) -> None:
        """基准 NULL 组归「基准未知」组，无 alpha 可比样本，且排有无可比组之后（四审 L3）。"""
        rows = [
            _metric_row(strat="sA", bm="sh000001", day=0, t1=1.0, alpha=1.0, win=1, loss=0),
            _metric_row(strat="sA", bm=None, day=0, t1=5.0, alpha=None, win=1, loss=1),
            _metric_row(strat="sA", bm="hs300", day=0, t1=2.0, alpha=2.0, win=2, loss=0),
        ]
        result = compute_strategy_review_stats(pd.DataFrame(rows))
        assert len(result) == 3
        # 基准未知组排最后；可比组内按 benchmark_code 升序（hs300 < sh000001）
        assert [r.benchmark_code for r in result] == ["hs300", "sh000001", None]
        unknown = result[-1]
        assert unknown.benchmark_known is False
        assert unknown.alpha.n == 0
        assert unknown.alpha.mean is None
        assert unknown.alpha.ci_lower is None
        # 已知组仍正常有 T+1/T+5 均值与胜率
        assert unknown.t1.mean == pytest.approx(5.0)
        assert unknown.win_rate == pytest.approx(0.5)

    def test_avg_daily_count(self) -> None:
        """日均纳入股票数 = 各交易日组合内股票数均值（四审 M3）。"""
        rows = [
            _metric_row(strat="sA", bm="sh000001", day=0, t1=1.0, alpha=1.0, daily_cnt=5),
            _metric_row(strat="sA", bm="sh000001", day=1, t1=2.0, alpha=2.0, daily_cnt=7),
            _metric_row(strat="sA", bm="sh000001", day=2, t1=3.0, alpha=3.0, daily_cnt=9),
        ]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.avg_daily_count == pytest.approx(7.0)

    def test_empty_input_returns_empty(self) -> None:
        assert compute_strategy_review_stats(pd.DataFrame()) == ()


class TestAiAttribution:
    """AI 结论快照回放归因统计（BIZ-04 第二层，ADR-0009）。

    覆盖：AI/无 AI 分组正确性、排序（AI 组在前）、基准未知组处理、
    指标独立 N、胜率股票行 N、空输入安全降级。
    """

    def test_ai_and_no_ai_groups_split(self) -> None:
        """同策略同基准下 has_ai=True/False 拆分为两个独立归因行，AI 组在前。"""
        rows = [
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=0, t1=1.0, alpha=1.0),
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=False, day=0, t1=2.0, alpha=2.0),
        ]
        result = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert len(result) == 2
        assert result[0].has_ai is True
        assert result[1].has_ai is False
        assert result[0].alpha.mean == pytest.approx(1.0)
        assert result[1].alpha.mean == pytest.approx(2.0)
        # 组内指标独立 N 各为 1
        assert result[0].alpha.n == 1
        assert result[1].alpha.n == 1

    def test_same_group_days_accumulate(self) -> None:
        """同组跨交易日样本累计：alpha 用日序列均值/独立 N，胜率用股票行 N 跨日累计。"""
        rows = [
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=0, t1=1.0, alpha=1.0, win=2, loss=1),
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=1, t1=3.0, alpha=3.0, win=1, loss=0),
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=False, day=0, t1=5.0, alpha=5.0, win=0, loss=1),
        ]
        result = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert len(result) == 2
        (ai_row,) = [r for r in result if r.has_ai]
        assert ai_row.alpha.n == 2
        assert ai_row.alpha.mean == pytest.approx(2.0)
        assert ai_row.win_count == 3
        assert ai_row.loss_count == 1
        assert ai_row.win_n == 4
        assert ai_row.win_rate == pytest.approx(3 / 4)

    def test_metrics_use_independent_nonnull_series(self) -> None:
        """alpha 部分日期有效时用其独立 N，与 t1 独立（复用 _metric_stat 口径）。"""
        rows: list[dict] = []
        for i in range(40):
            alpha = float(i) if i % 2 == 0 else None
            rows.append(_ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=i, t1=float(i), alpha=alpha))
        (row,) = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert row.t1.n == 40
        assert row.alpha.n == 20
        assert row.alpha.ci_lower is None  # n=20 < 30

    def test_benchmark_unknown_group_last(self) -> None:
        """基准未知组（bm=None）排最后，仍按 has_ai 拆分；alpha 无有效样本。"""
        rows = [
            _ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=0, t1=1.0, alpha=1.0),
            _ai_metric_row(strat="sA", bm="hs300", has_ai=True, day=0, t1=2.0, alpha=2.0),
            _ai_metric_row(strat="sA", bm=None, has_ai=True, day=0, t1=3.0, alpha=None),
            _ai_metric_row(strat="sA", bm=None, has_ai=False, day=0, t1=4.0, alpha=None),
        ]
        result = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert [r.benchmark_code for r in result] == ["hs300", "sh000001", None, None]
        unknown_ai, unknown_no_ai = result[-2], result[-1]
        assert unknown_ai.has_ai is True
        assert unknown_no_ai.has_ai is False
        assert unknown_ai.alpha.n == 0
        assert unknown_ai.alpha.mean is None
        assert unknown_no_ai.alpha.mean is None

    def test_win_rate_none_when_no_games(self) -> None:
        """无 WIN/LOSS 判定时胜率为 None（R21 None 哨兵），不伪装 0。"""
        rows = [_ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=0, t1=1.0, alpha=1.0)]
        (row,) = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert row.win_n == 0
        assert row.win_rate is None
        assert row.win_grade is SampleGrade.INSUFFICIENT

    def test_grade_independent(self) -> None:
        """日序列 N 与股票行 N 独立分级（复用四审 M2 口径，RV-09 后 alpha 按 n_eff 判定）。"""
        rows: list[dict] = []
        rows.append(_ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=0, t1=0.0, alpha=0.0, win=1))
        for i in range(1, 150):
            rows.append(_ai_metric_row(strat="sA", bm="sh000001", has_ai=True, day=i, t1=float(i), alpha=float(i)))
        (row,) = compute_ai_attribution_stats(pd.DataFrame(rows))
        assert row.alpha.n == 150
        assert row.alpha.n_eff == pytest.approx(30.0)
        assert row.win_n == 1
        assert row.alpha_grade is SampleGrade.LIMITED
        assert row.win_grade is SampleGrade.INSUFFICIENT

    def test_empty_input_returns_empty(self) -> None:
        assert compute_ai_attribution_stats(pd.DataFrame()) == ()
