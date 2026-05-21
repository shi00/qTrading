"""回测服务层

提供统一的回测服务入口，集成 TaskManager 进度/取消功能。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from data.cache.cache_manager import CacheManager
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.backtest.engine import VectorBacktestEngine
from strategies.base_strategy import BaseStrategy, get_strategy_registry

if TYPE_CHECKING:
    from utils.thread_pool import TaskManager

logger = logging.getLogger(__name__)


class BacktestService:
    """
    回测服务入口。

    功能：
    1. 按策略 key 查找并实例化策略
    2. 运行回测引擎
    3. 持久化回测结果
    4. 支持进度回调和取消
    """

    def __init__(
        self,
        cache: CacheManager | None = None,
        task_manager: TaskManager | None = None,
    ):
        self.cache = cache or CacheManager()
        self.task_manager = task_manager

    async def run_backtest(
        self,
        strategy_key: str,
        config: BacktestConfig,
        params: dict | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        persist: bool = True,
    ) -> BacktestResult:
        """
        运行回测。

        Args:
            strategy_key: 策略注册 key
            config: 回测配置
            params: 策略参数
            progress_callback: 进度回调函数
            persist: 是否持久化结果

        Returns:
            回测结果
        """
        strategy = self._get_strategy(strategy_key)
        if strategy is None:
            raise ValueError(f"Strategy not found: {strategy_key}")

        engine = VectorBacktestEngine(self.cache, config)

        result = await engine.run(
            strategy=strategy,
            params=params,
            progress_callback=progress_callback,
        )

        if persist:
            try:
                await self.cache.backtest_dao.save_result(result)
                logger.info(
                    "[BacktestService] Saved backtest result: run_id=%s, strategy=%s",
                    result.run_id,
                    result.strategy_name,
                )
            except Exception as e:
                logger.error(
                    "[BacktestService] Failed to persist backtest result: %s",
                    e,
                )

        return result

    async def run_backtest_with_strategy(
        self,
        strategy: BaseStrategy,
        config: BacktestConfig,
        params: dict | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        persist: bool = True,
    ) -> BacktestResult:
        """
        使用策略实例运行回测。

        Args:
            strategy: 策略实例
            config: 回测配置
            params: 策略参数
            progress_callback: 进度回调函数
            persist: 是否持久化结果

        Returns:
            回测结果
        """
        engine = VectorBacktestEngine(self.cache, config)

        result = await engine.run(
            strategy=strategy,
            params=params,
            progress_callback=progress_callback,
        )

        if persist:
            try:
                await self.cache.backtest_dao.save_result(result)
                logger.info(
                    "[BacktestService] Saved backtest result: run_id=%s, strategy=%s",
                    result.run_id,
                    result.strategy_name,
                )
            except Exception as e:
                logger.error(
                    "[BacktestService] Failed to persist backtest result: %s",
                    e,
                )

        return result

    def _get_strategy(self, strategy_key: str) -> BaseStrategy | None:
        """
        根据策略 key 获取策略实例。

        Args:
            strategy_key: 策略注册 key

        Returns:
            策略实例，如果不存在返回 None
        """
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

    async def get_result(self, run_id: str) -> dict | None:
        """
        获取已保存的回测结果。

        Args:
            run_id: 回测运行 ID

        Returns:
            回测结果字典
        """
        return await self.cache.backtest_dao.get_result(run_id)

    async def list_results(
        self,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        列出回测结果。

        Args:
            strategy_name: 策略名称过滤
            limit: 返回数量限制

        Returns:
            回测结果摘要列表
        """
        return await self.cache.backtest_dao.list_results(
            strategy_name=strategy_name,
            limit=limit,
        )

    async def delete_result(self, run_id: str) -> bool:
        """
        删除回测结果。

        Args:
            run_id: 回测运行 ID

        Returns:
            是否删除成功
        """
        return await self.cache.backtest_dao.delete_result(run_id)

    def submit_backtest_task(
        self,
        strategy_key: str,
        config: BacktestConfig,
        params: dict | None = None,
        task_name: str | None = None,
    ) -> str | None:
        """
        提交回测任务到 TaskManager。

        Args:
            strategy_key: 策略注册 key
            config: 回测配置
            params: 策略参数
            task_name: 任务名称

        Returns:
            任务 ID，如果 TaskManager 不可用返回 None
        """
        if self.task_manager is None:
            logger.warning("[BacktestService] TaskManager not available")
            return None

        async def _run_backtest():
            return await self.run_backtest(
                strategy_key=strategy_key,
                config=config,
                params=params,
                persist=True,
            )

        task_name = task_name or f"backtest_{strategy_key}"
        task_id = self.task_manager.submit(
            _run_backtest(),
            name=task_name,
            task_type="backtest",
        )
        return task_id
