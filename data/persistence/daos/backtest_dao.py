"""回测结果持久化 DAO"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import sqlalchemy as sa

from data.persistence.daos.base_dao import BaseDao
from data.persistence.models import BacktestResultModel, get_model_columns, get_model_pk_columns

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestResult

logger = logging.getLogger(__name__)


class BacktestDAO(BaseDao):
    """回测结果数据访问对象"""

    def __init__(self, engine=None):
        super().__init__(engine)

    async def save_result(self, result: BacktestResult) -> int:
        """
        保存回测结果到数据库。

        Args:
            result: 回测结果对象

        Returns:
            插入的记录 ID
        """
        nav_curve_data = []
        if result.nav_curve is not None and not result.nav_curve.is_empty():
            nav_curve_data = result.nav_curve.to_dicts()

        trades_data = []
        if result.trades is not None and not result.trades.is_empty():
            trades_data = result.trades.to_dicts()

        period_stats_data = []
        if result.period_stats is not None and not result.period_stats.is_empty():
            period_stats_data = result.period_stats.to_dicts()

        df = pd.DataFrame(
            [
                {
                    "run_id": result.run_id,
                    "strategy_name": result.strategy_name,
                    "params_snapshot": result.params_snapshot,
                    "start_date": result.config.start_date,
                    "end_date": result.config.end_date,
                    "initial_capital": result.config.initial_capital,
                    "total_return": result.metrics.get("total_return"),
                    "annualized_return": result.metrics.get("annualized_return"),
                    "sharpe_ratio": result.metrics.get("sharpe_ratio"),
                    "max_drawdown": result.metrics.get("max_drawdown"),
                    "calmar_ratio": result.metrics.get("calmar_ratio"),
                    "ic_mean": result.metrics.get("ic_mean"),
                    "ic_ir": result.metrics.get("ic_ir"),
                    "win_rate": result.metrics.get("win_rate"),
                    "profit_factor": result.metrics.get("profit_factor"),
                    "total_trades": result.metrics.get("total_trades"),
                    "nav_curve_json": nav_curve_data,
                    "trades_json": trades_data,
                    "period_stats_json": period_stats_data,
                    "duration_ms": result.duration_ms,
                }
            ]
        )

        return await self._save_upsert(
            df,
            "backtest_results",
            get_model_columns(BacktestResultModel),
            pk_columns=get_model_pk_columns(BacktestResultModel),
        )

    async def get_result(self, run_id: str) -> dict | None:
        """
        根据 run_id 获取回测结果。

        Args:
            run_id: 回测运行 ID

        Returns:
            回测结果字典，如果不存在返回 None
        """
        stmt = sa.select(BacktestResultModel).where(BacktestResultModel.run_id == run_id).limit(1)
        results = await self._read_db_select(stmt)
        if results is None or results.empty:
            return None
        return results.iloc[0].to_dict()

    async def list_results(
        self,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        列出回测结果。

        Args:
            strategy_name: 策略名称过滤（可选）
            limit: 返回数量限制

        Returns:
            回测结果摘要列表
        """
        stmt = sa.select(
            BacktestResultModel.run_id,
            BacktestResultModel.strategy_name,
            BacktestResultModel.start_date,
            BacktestResultModel.end_date,
            BacktestResultModel.sharpe_ratio,
            BacktestResultModel.max_drawdown,
            BacktestResultModel.executed_at,
        )
        if strategy_name:
            stmt = stmt.where(BacktestResultModel.strategy_name == strategy_name)
        stmt = stmt.order_by(BacktestResultModel.executed_at.desc()).limit(limit)
        results = await self._read_db_select(stmt)
        if results is None or results.empty:
            return []

        return [
            {
                "run_id": r["run_id"],
                "strategy_name": r["strategy_name"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "sharpe_ratio": r["sharpe_ratio"],
                "max_drawdown": r["max_drawdown"],
                "executed_at": r["executed_at"],
            }
            for r in results.to_dict("records")
        ]

    async def delete_result(self, run_id: str) -> bool:
        """
        删除回测结果。

        Args:
            run_id: 回测运行 ID

        Returns:
            是否删除成功
        """
        stmt = sa.delete(BacktestResultModel).where(BacktestResultModel.run_id == run_id)
        try:
            await self._write_db(stmt)
            return True
        except Exception as e:
            logger.error("[BacktestDAO] Failed to delete result %s: %s", run_id, e)
            return False
