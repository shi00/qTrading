"""回测服务入口

提供统一的回测编排，集成引擎运行与持久化。
位于 services 层，负责策略查找、引擎实例化和结果持久化。
UI / 任务系统通过此服务调用回测，不直接实例化引擎。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from data.cache.cache_manager import CacheManager
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.backtest.engine import VectorBacktestEngine
from strategies.base_strategy import BaseStrategy, get_strategy_registry

logger = logging.getLogger(__name__)


class BacktestService:
    """
    回测服务入口。

    功能：
    1. 按策略 key 查找并实例化策略
    2. 运行回测引擎
    3. 持久化回测结果
    """

    def __init__(
        self,
        cache: CacheManager | None = None,
        data_processor=None,
    ):
        self.cache = cache or CacheManager()
        self.data_processor = data_processor

    async def run_backtest(
        self,
        strategy_key: str,
        config: BacktestConfig,
        params: dict | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        persist: bool = True,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        strategy = self._get_strategy(strategy_key)
        if strategy is None:
            raise ValueError(f"Strategy not found: {strategy_key}")

        engine = VectorBacktestEngine(self.cache, config, data_processor=self.data_processor)

        result = await engine.run(
            strategy=strategy,
            params=params,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

        if persist:
            result = await self._persist_result(result)

        return result

    async def run_backtest_with_strategy(
        self,
        strategy: BaseStrategy,
        config: BacktestConfig,
        params: dict | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        persist: bool = True,
        cancel_check: Callable[[], bool] | None = None,
    ) -> BacktestResult:
        engine = VectorBacktestEngine(self.cache, config, data_processor=self.data_processor)

        result = await engine.run(
            strategy=strategy,
            params=params,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

        if persist:
            result = await self._persist_result(result)

        return result

    async def _persist_result(self, result: BacktestResult) -> BacktestResult:
        try:
            result_dict = {
                "run_id": result.run_id,
                "strategy_name": result.strategy_name,
                "params_snapshot": result.params_snapshot,
                "start_date": result.config.start_date,
                "end_date": result.config.end_date,
                "initial_capital": result.config.initial_capital,
                "metrics": result.metrics,
                "nav_curve": result.nav_curve,
                "trades": result.trades,
                "period_stats": result.period_stats,
                "duration_ms": result.duration_ms,
                "execution_price": result.config.execution_price,
                "allow_limit_up_buy": result.config.allow_limit_up_buy,
                "allow_limit_down_sell": result.config.allow_limit_down_sell,
                "slippage_model": result.config.slippage_model,
                "app_version": self._get_app_version(),
            }

            await self.cache.backtest_dao.save_result(result_dict)
            logger.info(
                "[BacktestService] Saved backtest result: run_id=%s, strategy=%s",
                result.run_id,
                result.strategy_name,
            )
            return result
        except Exception as e:
            logger.error(
                "[BacktestService] Failed to persist backtest result: %s",
                e,
            )
            new_warnings = list(result.data_warnings) + [f"persist_failed: {e}"]
            return result.with_warnings(new_warnings)

    def _get_strategy(self, strategy_key: str) -> BaseStrategy | None:
        registry = get_strategy_registry()
        strategy_class = registry.get(strategy_key)
        if strategy_class is None:
            return None

        try:
            return strategy_class()
        except Exception as e:
            logger.error(
                "[BacktestService] Failed to instantiate strategy %s: %s",
                strategy_key,
                e,
            )
            return None

    @staticmethod
    def _get_app_version() -> str:
        try:
            from importlib.metadata import version

            return version("astock-screener")
        except Exception:
            return "dev"

    async def get_result(self, run_id: str) -> dict | None:
        return await self.cache.backtest_dao.get_result(run_id)

    async def list_results(
        self,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.cache.backtest_dao.list_results(
            strategy_name=strategy_name,
            limit=limit,
        )

    async def delete_result(self, run_id: str) -> bool:
        return await self.cache.backtest_dao.delete_result(run_id)
