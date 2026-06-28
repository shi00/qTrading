"""回测服务入口

提供统一的回测编排，集成引擎运行与持久化。
位于 services 层，负责策略查找、引擎实例化和结果持久化。
UI / 任务系统通过此服务调用回测，不直接实例化引擎。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from data.cache.cache_manager import CacheManager

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestConfig, BacktestResult
    from strategies.base_strategy import BaseStrategy

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
        cache: CacheManager,
        data_processor=None,
    ):
        if cache is None:
            raise ValueError("BacktestService requires a CacheManager instance (dependency injection).")
        self.cache = cache
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

        from strategies.backtest.engine import VectorBacktestEngine

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
        from strategies.backtest.engine import VectorBacktestEngine

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
            result_dict = result.to_persist_dict()
            result_dict["app_version"] = self._get_app_version()

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
                exc_info=True,
            )
            new_warnings = list(result.data_warnings) + [f"persist_failed: {e}"]
            return result.with_warnings(new_warnings)

    def _get_strategy(self, strategy_key: str) -> BaseStrategy | None:
        from strategies.base_strategy import get_strategy_registry

        registry = get_strategy_registry()
        strategy_class = registry.get(strategy_key)
        if strategy_class is None:
            return None

        try:
            instance = strategy_class()
            instance.key = strategy_key
            return instance
        except Exception as e:
            logger.error(
                "[BacktestService] Failed to instantiate strategy %s: %s",
                strategy_key,
                e,
                exc_info=True,
            )
            return None

    @staticmethod
    def _get_app_version() -> str:
        try:
            from importlib.metadata import version

            return version("astock-screener")
        except Exception as e:
            logger.debug("[BacktestService] Failed to get app version: %s", e, exc_info=True)
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
