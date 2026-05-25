"""仓位分配器模块

负责根据配置策略计算每只股票的目标仓位权重。
支持三种分配方式：
1. equal_weight: 等权重分配
2. market_cap_weight: 市值加权
3. risk_parity: 风险平价（简化版，使用信号排名倒数）
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

        if signals_with_mv.is_empty():
            logger.warning("[MarketCapWeightSizer] No matching quotes, falling back to equal weight")
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


class RiskParitySizer(PositionSizer):
    """风险平价分配器（简化版）

    简化实现：使用 signal_rank 的倒数作为风险代理。
    signal_rank 数值越大，inv_rank 越小，权重越低。

    语义：signal_rank 表示信号强度排名，数值越小信号越强，
    因此权重与信号强度成正比（rank 1 最强，权重最高）。

    计算公式：weight_i = inv_rank_i / sum(inv_rank)
    其中 inv_rank_i = 1 / signal_rank_i
    """

    def compute_weights(
        self,
        signals: pl.DataFrame,
        quotes: pl.DataFrame,
        config: BacktestConfig,
    ) -> pl.DataFrame:
        if "signal_rank" not in signals.columns:
            logger.warning("[RiskParitySizer] signal_rank column not found, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        signals_sorted = signals.sort("signal_rank", descending=True)

        signals_with_inv_rank = signals_sorted.with_columns((1.0 / pl.col("signal_rank")).alias("inv_rank"))

        inv_rank_sum = signals_with_inv_rank.select(pl.col("inv_rank").sum()).item()

        if inv_rank_sum is None or inv_rank_sum <= 0:
            logger.warning("[RiskParitySizer] Invalid inverse rank sum, falling back to equal weight")
            return EqualWeightSizer().compute_weights(signals, quotes, config)

        result = signals_with_inv_rank.with_columns((pl.col("inv_rank") / inv_rank_sum).alias("weight")).drop(
            "inv_rank"
        )

        return result


def apply_max_weight_constraint(
    weights_df: pl.DataFrame,
    max_weight: float,
) -> pl.DataFrame:
    """
    应用单股权重上限约束。

    对超过上限的权重进行截断，然后重新归一化使总权重为 1。

    Args:
        weights_df: 包含 ts_code 和 weight 列的 DataFrame
        max_weight: 单股权重上限

    Returns:
        截断并归一化后的 DataFrame
    """
    if weights_df.is_empty():
        return weights_df

    result = weights_df.with_columns(
        pl.when(pl.col("weight") > max_weight)
        .then(pl.lit(max_weight))
        .otherwise(pl.col("weight"))
        .alias("weight_capped")
    )

    total_weight = result.select(pl.col("weight_capped").sum()).item()

    if total_weight is None or total_weight <= 0:
        logger.warning("[apply_max_weight_constraint] Total weight is zero or null, returning original weights")
        return weights_df

    result = result.with_columns((pl.col("weight_capped") / total_weight).alias("weight")).drop("weight_capped")

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
        "risk_parity": RiskParitySizer,
    }
    sizer_cls = sizers.get(position_sizing, EqualWeightSizer)
    return sizer_cls()
