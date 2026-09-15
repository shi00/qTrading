"""复盘聚合统计服务（UX-05）。

对 DAO 日级聚合 ``screener_dao.get_strategy_review_stats`` 的产出做**日序列统计**计算：

- **样本单位 = 交易日**：各指标按非 NULL 日序列算独立 N / 均值 / 标准差
  （先按 trade_date 聚类求日组合均值，消除横截面相关，三审 M1）。
- **置信区间用 t 分布**（``mean ± t_{0.975, n-1} * std / sqrt(n)``）。scipy 未入
  pyproject，采用固定内插表 ``_T_CRIT_0_975`` 覆盖 df=1..179（四审 L2），
  df>179 时钳制到表尾近似值。
- **胜率用股票行 N 独立计算**（四审 M2），与均值 CI 的日序列 N **独立分级**。
- **基准 NULL 组**（benchmark_code IS NULL）归入「基准未知」组：alpha 指标无有效
  样本（n=0），仅 T+1/T+5 独立均值/N 可见。

纯函数、无 DB 访问，可无 DB 单测（设计 v5 §2）。不引用 strategies/ 或 ui/，不违反 R1。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

import pandas as pd

# 样本量分级常量（二审 Skeptic 定稿：硬编码模块常量，不引 ConfigHandler，YAGNI）。
# 均值/CI 与胜率共用同一阈值，但各自按独立 N 判定（四审 M2），由 UI 分行呈现。
REVIEW_MIN_SAMPLE = 30
REVIEW_ADEQUATE_SAMPLE = 100

# t 分布双侧 0.975 分位临界值，df=1..179（索引 = df-1），5 位小数。
# 生成自正则化不完全 Beta（Numerical Recipes betacf），参考文献值误差 <1e-9。
# df=29/30/99/100 边界为 2.04523/2.04227/1.98422/1.98397，由单测精确断言（四审 L2）。
_T_CRIT_0_975: tuple[float, ...] = (
    12.70620,
    4.30265,
    3.18245,
    2.77645,
    2.57058,
    2.44691,
    2.36462,
    2.30600,
    2.26216,
    2.22814,
    2.20099,
    2.17881,
    2.16037,
    2.14479,
    2.13145,
    2.11991,
    2.10982,
    2.10092,
    2.09302,
    2.08596,
    2.07961,
    2.07387,
    2.06866,
    2.06390,
    2.05954,
    2.05553,
    2.05183,
    2.04841,
    2.04523,
    2.04227,
    2.03951,
    2.03693,
    2.03452,
    2.03224,
    2.03011,
    2.02809,
    2.02619,
    2.02439,
    2.02269,
    2.02108,
    2.01954,
    2.01808,
    2.01669,
    2.01537,
    2.01410,
    2.01290,
    2.01174,
    2.01063,
    2.00958,
    2.00856,
    2.00758,
    2.00665,
    2.00575,
    2.00488,
    2.00404,
    2.00324,
    2.00247,
    2.00172,
    2.00100,
    2.00030,
    1.99962,
    1.99897,
    1.99834,
    1.99773,
    1.99714,
    1.99656,
    1.99601,
    1.99547,
    1.99495,
    1.99444,
    1.99394,
    1.99346,
    1.99300,
    1.99254,
    1.99210,
    1.99167,
    1.99125,
    1.99085,
    1.99045,
    1.99006,
    1.98969,
    1.98932,
    1.98896,
    1.98861,
    1.98827,
    1.98793,
    1.98761,
    1.98729,
    1.98698,
    1.98667,
    1.98638,
    1.98609,
    1.98580,
    1.98552,
    1.98525,
    1.98498,
    1.98472,
    1.98447,
    1.98422,
    1.98397,
    1.98373,
    1.98350,
    1.98326,
    1.98304,
    1.98282,
    1.98260,
    1.98238,
    1.98217,
    1.98197,
    1.98177,
    1.98157,
    1.98137,
    1.98118,
    1.98099,
    1.98081,
    1.98063,
    1.98045,
    1.98027,
    1.98010,
    1.97993,
    1.97976,
    1.97960,
    1.97944,
    1.97928,
    1.97912,
    1.97897,
    1.97882,
    1.97867,
    1.97852,
    1.97838,
    1.97824,
    1.97810,
    1.97796,
    1.97783,
    1.97769,
    1.97756,
    1.97743,
    1.97730,
    1.97718,
    1.97705,
    1.97693,
    1.97681,
    1.97669,
    1.97658,
    1.97646,
    1.97635,
    1.97623,
    1.97612,
    1.97601,
    1.97591,
    1.97580,
    1.97569,
    1.97559,
    1.97549,
    1.97539,
    1.97529,
    1.97519,
    1.97509,
    1.97500,
    1.97490,
    1.97481,
    1.97472,
    1.97462,
    1.97453,
    1.97445,
    1.97436,
    1.97427,
    1.97419,
    1.97410,
    1.97402,
    1.97393,
    1.97385,
    1.97377,
    1.97369,
    1.97361,
    1.97353,
    1.97346,
    1.97338,
    1.97331,
)


class SampleGrade(StrEnum):
    """样本量分级（供 UI 映射独立 i18n key）。"""

    INSUFFICIENT = "insufficient"  # n < REVIEW_MIN_SAMPLE
    LIMITED = "limited"  # REVIEW_MIN_SAMPLE <= n < REVIEW_ADEQUATE_SAMPLE
    ADEQUATE = "adequate"  # n >= REVIEW_ADEQUATE_SAMPLE


@dataclass(frozen=True)
class MetricStat:
    """单指标日序列统计（样本单位=交易日）。

    - n: 非 NULL 日序列长度（交易日数）
    - mean / std: 日组合收益序列均值与标准差（std 于 n<3 或无效/NULL 时置 None）
    - ci_lower / ci_upper: t 分布 95% 置信区间（n<30 或 std 不可用/无效时置 None）
    """

    n: int
    mean: float | None
    std: float | None
    ci_lower: float | None
    ci_upper: float | None


@dataclass(frozen=True)
class StrategyStatRow:
    """单个 (strategy_name, benchmark_code) 组的复盘聚合统计。

    benchmark_code 为 None 表示「基准未知」组（alpha 指标无有效样本，四审 M2/L3）。
    alpha / t1 / t5 使用日序列 N；胜率使用股票行 N（win_count + loss_count），独立分级。
    """

    strategy_name: str
    benchmark_code: str | None
    avg_daily_count: float  # 日均纳入股票数（等权日组合均值提醒组合规模波动，四审 M3）
    t1: MetricStat
    t5: MetricStat
    alpha: MetricStat
    win_count: int
    loss_count: int

    @property
    def benchmark_known(self) -> bool:
        return self.benchmark_code is not None

    @property
    def win_n(self) -> int:
        """胜率独立样本量（逐股二项判定的股票行总数，跨日累计）。"""
        return self.win_count + self.loss_count

    @property
    def win_rate(self) -> float | None:
        """WIN / (WIN+LOSS)。无任一判定时返回 None（缺失以 None 哨兵表达，R21 不伪装 0）。"""
        total = self.win_n
        if total == 0:
            return None
        return self.win_count / total

    @property
    def alpha_grade(self) -> SampleGrade:
        """主指标 T+1 Alpha 的日序列 N 分级。"""
        return grade_for(self.alpha.n)

    @property
    def win_grade(self) -> SampleGrade:
        """胜率的股票行 N 分级（与 alpha 日序列 N 独立判定，四审 M2）。"""
        return grade_for(self.win_n)


def _t_crit(df: int) -> float:
    """t 分布双侧 0.975 分位临界值。df 钳制到 [1, 179]。"""
    if df >= len(_T_CRIT_0_975):
        return _T_CRIT_0_975[-1]
    return _T_CRIT_0_975[df - 1]


def grade_for(n: int) -> SampleGrade:
    """按样本量分级（n<30 / 30-100 / >=100）。"""
    if n < REVIEW_MIN_SAMPLE:
        return SampleGrade.INSUFFICIENT
    if n < REVIEW_ADEQUATE_SAMPLE:
        return SampleGrade.LIMITED
    return SampleGrade.ADEQUATE


def _metric_stat(series: pd.Series) -> MetricStat:
    """对单指标的非 NULL 日序列计算 N/均值/标准差/置信区间。"""
    values = series.dropna().astype("float64")
    n = int(values.size)
    if n == 0:
        return MetricStat(0, None, None, None, None)

    mean = float(values.mean())
    std: float | None = None
    if n >= 3:
        s = float(values.std(ddof=1))
        if math.isfinite(s) and s > 0:
            std = s

    ci_lower: float | None = None
    ci_upper: float | None = None
    if std is not None and n >= REVIEW_MIN_SAMPLE:
        half = _t_crit(n - 1) * std / math.sqrt(n)
        ci_lower = mean - half
        ci_upper = mean + half

    return MetricStat(n, mean, std, ci_lower, ci_upper)


def compute_strategy_review_stats(df: pd.DataFrame) -> tuple[StrategyStatRow, ...]:
    """将 DAO 日级聚合 DataFrame 换算为按 (strategy_name, benchmark_code) 分组的统计行。

    分组内各日期为独立样本点；指标独立 N（t1/t5/alpha 各以非 NULL 日序列计算），
    胜率以股票行 N 独立累计。基准 NULL 组自成一组并始终排在同名策略的可比组之后。

    Args:
        df: ``get_strategy_review_stats`` 产出，列含 trade_date / strategy_name /
            benchmark_code / daily_cnt / t1_mean / t5_mean / alpha_mean / win_cnt / loss_cnt。

    Returns:
        ``StrategyStatRow`` 元组，基准可比组在前、基准未知组在后；每个组内按
        strategy_name、benchmark_code 排序。空输入返回空元组。
    """
    if df.empty:
        return ()

    rows: list[StrategyStatRow] = []
    for (strategy_name, benchmark_code), group in df.groupby(
        ["strategy_name", "benchmark_code"], dropna=False, sort=False
    ):
        bm: str | None = (
            None if pd.isna(cast("float | str | None", benchmark_code)) else str(benchmark_code)
        )  # NULL 组归一为 None
        rows.append(
            StrategyStatRow(
                strategy_name=str(strategy_name),
                benchmark_code=bm,
                avg_daily_count=float(group["daily_cnt"].mean()),
                t1=_metric_stat(group["t1_mean"]),
                t5=_metric_stat(group["t5_mean"]),
                alpha=_metric_stat(group["alpha_mean"]),
                win_count=int(group["win_cnt"].sum()),
                loss_count=int(group["loss_cnt"].sum()),
            )
        )

    rows.sort(key=lambda r: (not r.benchmark_known, r.strategy_name, r.benchmark_code or ""))
    return tuple(rows)


@dataclass(frozen=True)
class AiAttributionRow:
    """单个 (strategy_name, benchmark_code, has_ai) 组的 AI 归因统计（BIZ-04 第二层）。

    has_ai=True 表示该组记录历史上真实产生过 AI 判断（ai_score 非空，代理口径见 ADR-0009），
    has_ai=False 为无 AI 组（对照组）。benchmark_code 为 None 表示「基准未知」组（alpha 无
    有效样本）。alpha / t1 / t5 使用日序列 N；胜率使用股票行 N，独立分级。
    """

    strategy_name: str
    benchmark_code: str | None
    has_ai: bool
    avg_daily_count: float  # 日均纳入股票数（等权日组合均值）
    t1: MetricStat
    t5: MetricStat
    alpha: MetricStat
    win_count: int
    loss_count: int

    @property
    def benchmark_known(self) -> bool:
        return self.benchmark_code is not None

    @property
    def win_n(self) -> int:
        """胜率独立样本量（逐股二项判定的股票行总数，跨日累计）。"""
        return self.win_count + self.loss_count

    @property
    def win_rate(self) -> float | None:
        """WIN / (WIN+LOSS)。无任一判定时返回 None（R21 None 哨兵）。"""
        total = self.win_n
        if total == 0:
            return None
        return self.win_count / total

    @property
    def alpha_grade(self) -> SampleGrade:
        return grade_for(self.alpha.n)

    @property
    def win_grade(self) -> SampleGrade:
        return grade_for(self.win_n)


def compute_ai_attribution_stats(df: pd.DataFrame) -> tuple[AiAttributionRow, ...]:
    """将 DAO 日级聚合 DataFrame 换算为按 (strategy_name, benchmark_code, has_ai) 分组的归因行。

    与 ``compute_strategy_review_stats`` 同口径（日序列 N/均值/std/CI + 胜率股票行 N），
    仅分组键增加 has_ai 维度。AI 组（has_ai=True）在前、无 AI 组在后，便于 UI 并排对比。

    Args:
        df: ``get_ai_attribution_stats`` 产出，列含 trade_date / strategy_name /
            benchmark_code / has_ai / daily_cnt / t1_mean / t5_mean / alpha_mean /
            win_cnt / loss_cnt。

    Returns:
        ``AiAttributionRow`` 元组；空输入返回空元组。
    """
    if df.empty:
        return ()

    rows: list[AiAttributionRow] = []
    for (strategy_name, benchmark_code, has_ai), group in df.groupby(
        ["strategy_name", "benchmark_code", "has_ai"], dropna=False, sort=False
    ):
        bm: str | None = (
            None if pd.isna(cast("float | str | None", benchmark_code)) else str(benchmark_code)
        )  # NULL 组归一为 None
        rows.append(
            AiAttributionRow(
                strategy_name=str(strategy_name),
                benchmark_code=bm,
                has_ai=bool(has_ai),
                avg_daily_count=float(group["daily_cnt"].mean()),
                t1=_metric_stat(group["t1_mean"]),
                t5=_metric_stat(group["t5_mean"]),
                alpha=_metric_stat(group["alpha_mean"]),
                win_count=int(group["win_cnt"].sum()),
                loss_count=int(group["loss_cnt"].sum()),
            )
        )

    rows.sort(key=lambda r: (not r.benchmark_known, r.strategy_name, r.benchmark_code or "", not r.has_ai))
    return tuple(rows)
