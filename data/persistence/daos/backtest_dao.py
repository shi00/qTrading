"""回测结果持久化 DAO"""

from __future__ import annotations

import logging

import pandas as pd
import sqlalchemy as sa

from data.persistence.daos.base_dao import BaseDao, EngineDisposedError
from data.persistence.models import BacktestResultModel, get_model_columns, get_model_pk_columns

logger = logging.getLogger(__name__)


class BacktestDAO(BaseDao):
    """回测结果数据访问对象"""

    def __init__(self, engine=None):
        super().__init__(engine)

    async def save_result(self, result: dict) -> int:
        """
        保存回测结果到数据库。

        Args:
            result: 回测结果字典，由调用方从 BacktestResult 转换而来

        Returns:
            插入的记录 ID
        """
        nav_curve_data = []
        nav_curve = result.get("nav_curve")
        if nav_curve is not None and not nav_curve.is_empty():
            nav_curve_data = nav_curve.to_dicts()

        trades_data = []
        trades = result.get("trades")
        if trades is not None and not trades.is_empty():
            trades_data = trades.to_dicts()

        period_stats_data = []
        period_stats = result.get("period_stats")
        if period_stats is not None and not period_stats.is_empty():
            period_stats_data = period_stats.to_dicts()

        config = result.get("config")
        metrics = result.get("metrics", {})

        profit_factor = metrics.get("profit_factor")
        if profit_factor is not None and not isinstance(profit_factor, (int, float)):
            profit_factor = None
        if profit_factor is not None and profit_factor == float("inf"):
            profit_factor = None

        df = pd.DataFrame(
            [
                {
                    "run_id": result.get("run_id"),
                    "strategy_name": result.get("strategy_name"),
                    "params_snapshot": result.get("params_snapshot"),
                    "start_date": result.get("start_date") or (config.start_date if config else None),
                    "end_date": result.get("end_date") or (config.end_date if config else None),
                    "initial_capital": result.get("initial_capital") or (config.initial_capital if config else None),
                    "total_return": metrics.get("total_return"),
                    "annualized_return": metrics.get("annualized_return"),
                    "sharpe_ratio": metrics.get("sharpe_ratio"),
                    "max_drawdown": metrics.get("max_drawdown"),
                    "calmar_ratio": metrics.get("calmar_ratio"),
                    "ic_mean": metrics.get("ic_mean"),
                    "ic_ir": metrics.get("ic_ir"),
                    "win_rate": metrics.get("win_rate"),
                    "profit_factor": profit_factor,
                    "total_trades": metrics.get("total_trades"),
                    "volatility": metrics.get("volatility"),
                    "information_ratio": metrics.get("information_ratio"),
                    "tracking_error": metrics.get("tracking_error"),
                    "nav_curve_json": nav_curve_data,
                    "trades_json": trades_data,
                    "period_stats_json": period_stats_data,
                    "execution_price": result.get("execution_price"),
                    "allow_limit_up_buy": result.get("allow_limit_up_buy"),
                    "allow_limit_down_sell": result.get("allow_limit_down_sell"),
                    "slippage_model": result.get("slippage_model"),
                    "app_version": result.get("app_version"),
                    "duration_ms": result.get("duration_ms"),
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
        except EngineDisposedError:
            logger.warning("[BacktestDAO] Engine disposed, skipping delete for %s", run_id)
            return False
        except Exception as e:
            logger.error("[BacktestDAO] Failed to delete result %s: %s", run_id, e)
            return False
