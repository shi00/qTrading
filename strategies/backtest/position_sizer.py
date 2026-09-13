"""仓位分配器模块

负责根据配置策略计算每只股票的目标仓位权重。
支持三种分配方式：
1. equal_weight: 等权重分配
2. market_cap_weight: 市值加权
3. rank_weighted: 按信号排名线性递减加权
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestConfig

logger = logging.getLogger(__name__)


class PositionSizer(ABC):
    """仓位分配器基类"""

    @abstractmethod
    def compute_weights(
        self,
        signals: pl.DataFrame,
        quotes: pl.DataFrame,
        config: BacktestConfig,
    ) -> pl.DataFrame:
        """
        计算每只股票的目标权重。

        Args:
            signals: 信号 DataFrame（含 ts_code, signal_rank）
            quotes: 行情 DataFrame（含 ts_code, total_mv 等）
            config: 回测配置

        Returns:
            DataFrame with columns: ts_code, signal_rank (if present), weight
        """
        pass


class EqualWeightSizer(PositionSizer):
    """等权重分配器

    每只股票分配相同的权重。
    权重 = 1 / N（N 为股票数量）
    """

    def compute_weights(
        self,
        signals: pl.DataFrame,
        quotes: pl.DataFrame,
        config: BacktestConfig,
    ) -> pl.DataFrame:
        num_positions = len(signals)
        if num_positions == 0:
            return signals.select("ts_code").with_columns(pl.lit(0.0).alias("weight"))

        weight = 1.0 / num_positions

        cols = ["ts_code"]
        if "signal_rank" in signals.columns:
            cols.append("signal_rank")

        result = signals.select(cols).with_columns(pl.lit(weight).alias("weight"))
        return result


class MarketCapWeightSizer(PositionSizer):
    """市值加权分配器

    按总市值加权，大市值股票获得更高权重。
    权重 = 个股市值 / 所有候选股总市值
    """

    def compute_weights(
        self,
        signals: pl.DataFrame,
        quotes: pl.DataFrame,
        config: BacktestConfig,
    ) -> pl.DataFrame:
        if "total_mv" not in quotes.columns:
            logger.warning("[MarketCapWeightSizer] total_mv column not found, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        unique_quotes = quotes.select(["ts_code", "total_mv"]).unique(subset=["ts_code"])

        signals_with_mv = signals.join(unique_quotes, on="ts_code", how="inner")

        # 过滤非正市值（数据异常或退市残留），避免负权重污染
        signals_with_mv = signals_with_mv.filter(pl.col("total_mv") > 0)

        if signals_with_mv.is_empty():
            logger.warning("[MarketCapWeightSizer] No valid market cap data, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        total_mv_sum = signals_with_mv.select(pl.col("total_mv").sum()).item()

        if total_mv_sum is None or total_mv_sum <= 0:
            logger.warning("[MarketCapWeightSizer] Total market cap is zero or null, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        cols = ["ts_code", "total_mv"]
        if "signal_rank" in signals.columns:
            cols = ["ts_code", "signal_rank", "total_mv"]

        result = (
            signals_with_mv.select(cols)
            .with_columns((pl.col("total_mv") / total_mv_sum).alias("weight"))
            .drop("total_mv")
        )

        return result


class RankWeightedSizer(PositionSizer):
    """按信号排名线性递减分配权重的仓位分配器

    M10-001 修复：统一 signal_rank 语义为 "rank 大 = 信号强"。
    使用 signal_rank 本身作为权重代理：rank 大 = 信号强 = 权重大。

    语义：signal_rank 数值越大信号越强，因此权重与 signal_rank 成正比
    （rank N 最强，权重最高）。

    计算公式：weight_i = rank_i / sum(rank)

    注意：本 sizer 不含任何风险度量。若需按波动率控制风险暴露，
    见 InverseVolatilitySizer（如已实现）。

    D3-9 说明 —— 仅 ordinal rank 的权衡：
    signal_rank 是 1..N 的整数**序数**，不携带信号强度的量级信息：
      - 打分 95/94/93（几乎等价）与 95/50/20（差异巨大）会得到**相同**的 rank(1,2,3)
        与相同权重——本 sizer 不感知打分间距；
      - 权重依赖候选总数 N：同一排名第 2 的标的，在 3 个候选时权重远高于 30 个候选时。
    这是"排名加权"这一分配方式的固有语义（按定义只用排名，不用打分量级）。
    若需按信号**强度**（而非仅排名）分配，应使用支持原始打分归一化的 sizer
    （如 adapter 同时输出 signal_score 时改用打分加权）。
    """

    def compute_weights(
        self,
        signals: pl.DataFrame,
        quotes: pl.DataFrame,
        config: BacktestConfig,
    ) -> pl.DataFrame:
        if "signal_rank" not in signals.columns:
            logger.warning("[RankWeightedSizer] signal_rank column not found, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        # 过滤非正 signal_rank（避免除零）和 null 值
        valid_signals = signals.filter((pl.col("signal_rank") > 0) & pl.col("signal_rank").is_not_null())
        if valid_signals.is_empty():
            logger.warning("[RankWeightedSizer] No valid signal_rank data, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        signals_sorted = valid_signals.sort("signal_rank", descending=True)

        rank_sum = signals_sorted.select(pl.col("signal_rank").sum()).item()

        if rank_sum is None or rank_sum <= 0:
            logger.warning("[RankWeightedSizer] Invalid rank sum, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        result = signals_sorted.with_columns((pl.col("signal_rank") / rank_sum).alias("weight"))

        return result


def apply_max_weight_constraint(
    weights_df: pl.DataFrame,
    max_weight: float,
    renormalize: bool = False,
) -> pl.DataFrame:
    """
    应用单股权重上限约束（迭代收敛算法）。

    截断后重新归一化可能导致权重再次超过 max_weight（例: [0.5,0.3,0.2] max=0.4 →
    截断 [0.4,0.3,0.2] → 归一化 [0.444,...] → 0.444 > 0.4 失效）。
    因此采用"截断→归一化"迭代直到收敛，保证 max(weight) <= max_weight + tolerance。

    边界: N * max_weight < 1 时无法归一化到 1。此时所有标的取 max_weight（剩余留现金），
    即"信号稀疏 → 资金闲置"（BT-03）。当 renormalize=True 时放宽单票硬上限，
    等比放大到满仓（权总=1），避免资金闲置（资金效率优先）。
    兜底: 100 次迭代未收敛则强制截断（总权重可能 < 1，剩余留现金，优于违反约束）。

    Args:
        weights_df: 包含 ts_code 和 weight 列的 DataFrame
        max_weight: 单股权重上限
        renormalize: 截顶后是否重新归一化到满仓（BT-03，仅影响 N*max_weight < 1 的稀疏信号分支；
            该分支以上 N*max_weight >= 1 时迭代已收敛至权总=1，语义不受影响）

    Returns:
        截断并归一化后的 DataFrame
    """
    if weights_df.is_empty():
        return weights_df

    n = weights_df.height
    # N * max_weight < 1: 无法归一化到 1，所有取 max_weight（剩余留现金）
    if n * max_weight < 1.0:
        if renormalize:
            # 资金效率优先：全部触顶即无「未触顶」标的可承接盈余，放宽单票上限等比放大到满仓，
            # 每只权重 1/n（> max_weight，超出硬上限是 renormalize 语义的固有取舍）。
            return weights_df.select("ts_code").with_columns(pl.lit(1.0 / n).alias("weight"))
        return weights_df.with_columns(pl.lit(max_weight).alias("weight"))

    result = weights_df
    max_iterations = 100  # 线性收敛比率≈0.4，达 1e-6 需约 15 次，100 次足够
    tolerance = 1e-6  # 放宽容差避免振荡

    for _ in range(max_iterations):
        # 截断
        result = result.with_columns(
            pl.when(pl.col("weight") > max_weight).then(pl.lit(max_weight)).otherwise(pl.col("weight")).alias("weight")
        )
        # 归一化
        total_weight = result.select(pl.col("weight").sum()).item()
        if total_weight is None or total_weight <= 0:
            return weights_df
        result = result.with_columns((pl.col("weight") / total_weight).alias("weight"))
        # 检查收敛
        max_current = result.select(pl.col("weight").max()).item()
        if max_current is not None and max_current <= max_weight + tolerance:
            break

    # 兜底：未收敛时强制截断（总权重可能 < 1，剩余留现金，优于违反约束）
    max_current = result.select(pl.col("weight").max()).item()
    if max_current is not None and max_current > max_weight + tolerance:
        result = result.with_columns(
            pl.when(pl.col("weight") > max_weight).then(pl.lit(max_weight)).otherwise(pl.col("weight")).alias("weight")
        )

    return result


def get_sizer(position_sizing: str) -> PositionSizer:
    """
    工厂函数：根据配置获取仓位分配器。

    Args:
        position_sizing: 分配策略名称

    Returns:
        对应的仓位分配器实例
    """
    sizers: dict[str, type[PositionSizer]] = {
        "equal_weight": EqualWeightSizer,
        "market_cap_weight": MarketCapWeightSizer,
        "rank_weighted": RankWeightedSizer,
    }
    sizer_cls = sizers.get(position_sizing, EqualWeightSizer)
    return sizer_cls()
