"""回测 ViewModel

遵循项目 MVVM 模式：
- 调用 BacktestService 运行回测
- 通过 TaskManager.submit_task() 异步执行
- 管理回测状态和结果
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date

from core.i18n import I18n
from data.cache.cache_manager import CacheManager
from services.backtest_service import BacktestService
from services.task_manager import TaskManager
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.base_strategy import get_strategy_registry

logger = logging.getLogger(__name__)

TASK_NAME_PREFIX = "backtest"


class BacktestViewModel:
    """
    回测 ViewModel。

    职责：
    1. 管理回测配置状态
    2. 调用 BacktestService 运行回测
    3. 通过 TaskManager 异步执行
    4. 猡理回测结果和状态
    """

    def __init__(
        self,
        cache: CacheManager | None = None,
        service: BacktestService | None = None,
    ):
        self.cache = cache or CacheManager()
        if service is None:
            # 装配默认工厂：ui 层可导入 strategies（CLAUDE.md §4.1 允许 strategies ← ui），
            # 通过依赖注入传给 BacktestService，避免 services 层运行时依赖 strategies（R1 红线）。

            def _default_engine_factory(cache, config, data_processor):
                from strategies.backtest.engine import VectorBacktestEngine

                return VectorBacktestEngine(cache, config, data_processor=data_processor)

            def _default_strategy_lookup(strategy_key):
                return get_strategy_registry().get(strategy_key)

            service = BacktestService(
                cache=self.cache,
                engine_factory=_default_engine_factory,
                strategy_lookup=_default_strategy_lookup,
            )
        self.service = service

        self._result: BacktestResult | None = None
        self._is_running: bool = False
        self._task_id: str | None = None

        self.on_update: Callable | None = None
        self.on_status: Callable[[str, str], None] | None = None
        self.on_progress: Callable[[float, str], None] | None = None
        self.on_result: Callable[[BacktestResult], None] | None = None

    def bind(
        self,
        on_update: Callable | None = None,
        on_status: Callable[[str, str], None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        on_result: Callable[[BacktestResult], None] | None = None,
    ):
        """绑定 View 回调。"""
        self.on_update = on_update
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_result = on_result

    def dispose(self):
        """清理资源。"""
        self.on_update = None
        self.on_status = None
        self.on_progress = None
        self.on_result = None
        self._result = None

    @property
    def result(self) -> BacktestResult | None:
        return self._result

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_available_strategies(self) -> dict[str, str]:
        """获取可用策略列表。"""
        from strategies.all_strategies import StrategyManager

        return StrategyManager().get_all_names()

    def create_config(
        self,
        start_date: date,
        end_date: date,
        initial_capital: float = 1_000_000.0,
        commission_rate: float = 3e-4,
        commission_min: float = 5.0,
        stamp_duty_rate: float = 1e-3,
        slippage_bps: float = 5.0,
        rebalance_freq: str = "signal",
        max_position_count: int = 50,
        benchmark_code: str = "000300.SH",
        risk_free_rate: float = 0.02,
    ) -> BacktestConfig:
        """创建回测配置。"""
        return BacktestConfig(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            commission_rate=commission_rate,
            commission_min=commission_min,
            stamp_duty_rate=stamp_duty_rate,
            slippage_bps=slippage_bps,
            rebalance_freq=rebalance_freq,  # type: ignore[arg-type]
            max_position_count=max_position_count,
            benchmark_code=benchmark_code,
            risk_free_rate=risk_free_rate,
        )

    async def run_backtest(
        self,
        strategy_key: str,
        config: BacktestConfig,
        params: dict | None = None,
        persist: bool = True,
    ):
        """
        运行回测（通过 TaskManager 异步执行）。
        """
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()

        if self._is_running:
            if self.on_status:
                self.on_status(I18n.get("backtest_already_running"), "orange")
            return

        self._is_running = True
        self._result = None
        self._task_id = None

        if self.on_status:
            self.on_status(I18n.get("backtest_starting"), "blue")
        if self.on_progress:
            self.on_progress(0.0, I18n.get("backtest_initializing"))

        async def _execute_backtest(task_id: str, **kwargs):
            try:

                def _progress_callback(progress: float, message: str):
                    if not self._is_running:
                        return
                    if self.on_progress:
                        self.on_progress(progress, message)
                    TaskManager().update_progress(task_id, progress, message)

                def _cancel_check() -> bool:
                    return TaskManager().is_cancelled(task_id)

                result = await self.service.run_backtest(
                    strategy_key=strategy_key,
                    config=config,
                    params=params,
                    progress_callback=_progress_callback,
                    persist=persist,
                    cancel_check=_cancel_check,
                )

                self._result = result

                if self.on_result:
                    self.on_result(result)
                if self.on_status:
                    self.on_status(
                        I18n.get("backtest_completed").format(duration=result.duration_ms),
                        "green",
                    )
                if self.on_update:
                    self.on_update()

                return I18n.get("backtest_success").format(sharpe=f"{result.metrics['sharpe_ratio']:.2f}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[BacktestVM] Backtest failed: %s", e, exc_info=True)
                if self.on_status:
                    self.on_status(I18n.get("backtest_failed"), "red")
                raise
            finally:
                self._is_running = False
                if self.on_progress:
                    self.on_progress(1.0, I18n.get("backtest_done"))

        strategy_obj = get_strategy_registry().get(strategy_key)
        name_key = getattr(strategy_obj, "name_key", None) if strategy_obj else None
        strategy_name = I18n.get(name_key) if name_key else strategy_key
        task_id = TaskManager().submit_task(
            name=f"{TASK_NAME_PREFIX}: {strategy_name}",
            task_type=I18n.get("task_type_backtest"),
            coroutine_factory=_execute_backtest,
            cancellable=True,
        )

        self._task_id = task_id

        if task_id is None:
            self._is_running = False
            if self.on_status:
                self.on_status(I18n.get("backtest_task_rejected"), "orange")

    def cancel_backtest(self) -> None:
        if self._task_id:
            TaskManager().cancel_task(self._task_id)

    async def get_historical_results(
        self,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """获取历史回测结果列表。"""
        return await self.service.list_results(strategy_name=strategy_name, limit=limit)

    async def load_historical_result(self, run_id: str) -> dict | None:
        """加载历史回测结果。"""
        result = await self.service.get_result(run_id)
        return result
