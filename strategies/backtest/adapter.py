"""策略适配层

将现有 BaseStrategy.filter(context) 输出规范化为回测信号。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from strategies.base_strategy import BaseStrategy
    from strategies.utils import StrategyContext

logger = logging.getLogger(__name__)


class BacktestStrategyAdapter:
    """
    将现有 BaseStrategy.filter(context) 输出规范化为回测信号。

    关键设计：
    1. 调用 strategy.check_dependencies(context) 检查依赖
    2. 处理 unready/degraded 状态
    3. 规范化输出为标准信号 schema
    """

    async def generate_signal(
        self,
        strategy: BaseStrategy,
        context: StrategyContext,
        signal_date: date,
        execution_date: date,
    ) -> pl.DataFrame:
        """
        生成回测信号。

        步骤：
        1. 检查策略依赖：dep_result = strategy.check_dependencies(context)
        2. 如果 unready：记录警告，返回空 DataFrame
        3. 如果 degraded：记录信息，继续执行
        4. 调用 strategy.filter(context)
        5. 规范化输出为标准信号 schema

        Args:
            strategy: 策略实例
            context: 策略上下文
            signal_date: 信号日期
            execution_date: 执行日期（T+1）

        Returns:
            标准信号 DataFrame，包含以下列：
            - signal_date: date
            - execution_date: date
            - ts_code: str
            - score: float | None
            - rank: int
            - target_weight: float | None
            - reason: str | None
        """
        dep_result = strategy.check_dependencies(context)

        if dep_result["status"] == "unready":
            logger.warning(
                "[BacktestAdapter] Strategy %s dependencies unready: missing_keys=%s, missing_tables=%s",
                strategy.name,
                dep_result["missing_keys"],
                dep_result["missing_tables"],
            )
            return pl.DataFrame()

        if dep_result["status"] == "degraded":
            logger.info(
                "[BacktestAdapter] Strategy %s running in degraded mode: empty_keys=%s",
                strategy.name,
                dep_result["empty_keys"],
            )

        context["_dependency_status"] = dep_result

        result_df = await strategy.filter(context)

        return self._normalize_signal_output(
            result_df,
            signal_date,
            execution_date,
        )

    def _normalize_signal_output(
        self,
        result_df,
        signal_date: date,
        execution_date: date,
    ) -> pl.DataFrame:
        """
        规范化策略输出为标准信号 schema。

        标准信号输出 schema：
        - signal_date: date
        - execution_date: date
        - ts_code: str
        - score: float | None
        - rank: int
        - target_weight: float | None
        - reason: str | None
        """
        if result_df is None or (hasattr(result_df, "empty") and result_df.empty):
            return pl.DataFrame()

        import pandas as pd

        if isinstance(result_df, pd.DataFrame):
            if "ts_code" not in result_df.columns:
                logger.warning("[BacktestAdapter] Strategy result missing 'ts_code' column, returning empty DataFrame")
                return pl.DataFrame()

            df = pl.from_pandas(result_df)
        elif isinstance(result_df, pl.DataFrame):
            df = result_df
            if "ts_code" not in df.columns:
                logger.warning("[BacktestAdapter] Strategy result missing 'ts_code' column, returning empty DataFrame")
                return pl.DataFrame()
        else:
            logger.warning(
                "[BacktestAdapter] Strategy returned unexpected type: %s, returning empty DataFrame",
                type(result_df),
            )
            return pl.DataFrame()

        num_rows = len(df)
        ts_codes = df["ts_code"].to_list()

        score_col = None
        for score_name in ["score", "signal_score", "rank_score"]:
            if score_name in df.columns:
                score_col = df[score_name].to_list()
                break

        reason_col = None
        for reason_name in ["reason", "signal_reason", "note"]:
            if reason_name in df.columns:
                reason_col = df[reason_name].to_list()
                break

        ranks = list(range(num_rows, 0, -1))

        equal_weight = 1.0 / num_rows if num_rows > 0 else 0.0
        target_weights = [equal_weight] * num_rows

        signal_data = {
            "signal_date": [signal_date] * num_rows,
            "execution_date": [execution_date] * num_rows,
            "ts_code": ts_codes,
            "score": score_col if score_col else [None] * num_rows,
            "rank": ranks,
            "target_weight": target_weights,
            "reason": reason_col if reason_col else [None] * num_rows,
        }

        return pl.DataFrame(signal_data)
