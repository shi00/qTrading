"""回测服务入口

提供统一的回测编排，集成引擎运行与持久化。
位于 services 层，负责策略查找、引擎实例化和结果持久化。
UI / 任务系统通过此服务调用回测，不直接实例化引擎。

依赖注入契约：
- `engine_factory` 与 `strategy_lookup` 由调用方（如 ui 层 viewmodel）注入，
  避免 services 层运行时依赖 strategies 层（CLAUDE.md §3.1 R1 红线）。
- `get_result` / `list_results` / `delete_result` / `_persist_result` 不需要工厂注入。
- `run_backtest` 需要 `engine_factory` + `strategy_lookup`。
- `run_backtest_with_strategy` 仅需要 `engine_factory`。
- 若调用方未注入所需工厂，对应方法会 `raise RuntimeError`（fail-late）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from data.cache.cache_manager import CacheManager

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestConfig, BacktestResult
    from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BacktestService:
    """
    回测服务入口。

    功能：
    1. 按策略 key 查找并实例化策略（通过 `strategy_lookup` 注入）
    2. 运行回测引擎（通过 `engine_factory` 注入）
    3. 持久化回测结果
    """

    def __init__(
        self,
        cache: CacheManager,
        data_processor: Any = None,
        engine_factory: Callable[[CacheManager, BacktestConfig, Any | None], Any] | None = None,
        strategy_lookup: Callable[[str], type | None] | None = None,
    ):
        if cache is None:
            raise ValueError("BacktestService requires a CacheManager instance (dependency injection).")
        self.cache = cache
        self.data_processor = data_processor
        self._engine_factory = engine_factory
        self._strategy_lookup = strategy_lookup

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

        engine = self._create_engine(config)

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
        engine = self._create_engine(config)

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

    def _create_engine(self, config: BacktestConfig) -> Any:
        """通过注入的 engine_factory 创建引擎实例。

        Raises:
            RuntimeError: 若未注入 engine_factory。
        """
        if self._engine_factory is None:
            raise RuntimeError("BacktestService requires 'engine_factory' to be injected to run backtest.")
        return self._engine_factory(self.cache, config, self.data_processor)

    def _get_strategy(self, strategy_key: str) -> BaseStrategy | None:
        """按 key 查找策略类并实例化。

        - 实例化职责保留在 services 层（编排逻辑）。
        - 仅 "查 registry" 委托给注入的 strategy_lookup。

        Raises:
            RuntimeError: 若未注入 strategy_lookup。
        """
        if self._strategy_lookup is None:
            raise RuntimeError(
                "BacktestService requires 'strategy_lookup' to be injected to resolve strategy for run_backtest."
            )
        strategy_class = self._strategy_lookup(strategy_key)
        if strategy_class is None:
            return None

        try:
            instance: BaseStrategy = strategy_class()  # type: ignore[call-arg]  # 子类 __init__ 签名各异
            instance.key = strategy_key  # type: ignore[attr-defined]  # 动态打标，pre-existing 行为
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
