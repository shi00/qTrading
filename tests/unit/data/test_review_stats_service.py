"""复盘聚合统计服务单元测试（UX-05，设计 v5 §2/§6）。

统计计算为纯函数（无 DB），覆盖：
- t 分布固定内插表（df=1..179）与 30/100 边界精确值（四审 L2）
- 置信区间（n<30 置 None、n<3 std 置 None、n>=30 且 std 有效时计算）
- 指标独立 N（t1/t5/alpha 各以非 NULL 日序列独立计算，四审 M4）
- 胜率用股票行 N 独立于日序列 N、独立分级（四审 M2）
- 基准 NULL 组（benchmark_code IS NULL）归「基准未知」组并排在有可比组之后（四审 L3）
- 空输入安全降级、日均纳入股票数（四审 M3）
"""

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from data.domain_services.review_stats_service import (
    REVIEW_ADEQUATE_SAMPLE,
    REVIEW_MIN_SAMPLE,
    SampleGrade,
    _T_CRIT_0_975,
    _metric_stat,
    _t_crit,
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


class TestConfidenceInterval:
    def test_ci_present_when_n_ge_min_sample(self) -> None:
        """n=30（df=29）且 std 有效 → CI = mean ± t*std/sqrt(n)。"""
        rows = [_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)) for i in range(30)]
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 30
        assert row.alpha.mean == pytest.approx(14.5)
        expected_std = float(pd.Series(range(30)).std(ddof=1))
        assert row.alpha.std == pytest.approx(expected_std)
        half = _t_crit(29) * expected_std / math.sqrt(30)
        assert row.alpha.ci_lower == pytest.approx(14.5 - half)
        assert row.alpha.ci_upper == pytest.approx(14.5 + half)

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
        """胜率股票行 N 与日序列 N 独立，独立分级（四审 M2）。"""
        # 30 个交易日（日序列 N=30 → alpha LIMITED），但仅累计 1 胜（股票行 N=1 → INSUFFICIENT）
        rows: list[dict] = []
        rows.append(_metric_row(strat="sA", bm="sh000001", day=0, t1=0.0, alpha=0.0, win=1, loss=0))
        for i in range(1, 30):
            rows.append(_metric_row(strat="sA", bm="sh000001", day=i, t1=float(i), alpha=float(i)))
        (row,) = compute_strategy_review_stats(pd.DataFrame(rows))
        assert row.alpha.n == 30
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
