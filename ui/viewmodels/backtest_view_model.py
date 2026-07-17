"""回测 ViewModel

遵循项目 MVVM 模式（V1 声明式范式）：
- frozen dataclass BacktestState + subscribe/_notify
- 调用 BacktestService 运行回测
- 通过 TaskManager.submit_task() 异步执行
- 回测结果直接放入 state.result (L771 合规, 无 dual-track version + property)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date

from data.cache.cache_manager import CacheManager
from services.backtest_service import BacktestService
from services.task_manager import TaskManager
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.base_strategy import get_strategy_registry
from ui.viewmodels import Message

logger = logging.getLogger(__name__)

TASK_NAME_PREFIX = "backtest"


@dataclass(frozen=True, eq=False)
class BacktestState:
    """BacktestViewModel 的不可变状态快照 (L771 合规, 无 dual-track).

    NOTE(lazy): result 字段类型为 BacktestResult | None (strategies 层 frozen
    dataclass 领域对象, 内部含 pl.DataFrame/pl.Series). 自定义 __eq__ 让 result
    用 identity 比较, 避免 BacktestResult.__eq__ 触发 DataFrame __eq__ 抛
    TypeError (Flet use_state setter L110 `if new_value != hook.value:` 安全性,
    spec.md §Flet use_state setter 安全性).
    ceiling: BacktestResult 拆解为 tuple[Row, ...] 需重写 BacktestResultPanel.
    upgrade: BacktestResultPanel 接收 tuple[Row, ...] 形式时, 移除自定义 __eq__/__hash__.
    """

    is_running: bool = False
    progress: float = 0.0
    progress_message: Message | None = None
    status_message: Message | None = None
    status_color: str = ""
    # 回测结果直接放入 state (BacktestResult 是 strategies 层 frozen dataclass 领域对象)
    result: BacktestResult | None = None

    def __eq__(self, other: object) -> bool:
        """自定义 __eq__: result 字段用 identity 比较, 避免 DataFrame __eq__ 抛 TypeError."""
        if not isinstance(other, BacktestState):
            return NotImplemented
        return (
            self.is_running == other.is_running
            and self.progress == other.progress
            and self.progress_message == other.progress_message
            and self.status_message == other.status_message
            and self.status_color == other.status_color
            and self.result is other.result
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.is_running,
                self.progress,
                self.progress_message,
                self.status_message,
                self.status_color,
                id(self.result),
            )
        )


class BacktestViewModel:
    """
    回测 ViewModel（V1 声明式范式）。

    职责：
    1. 管理回测配置状态（frozen BacktestState snapshot）
    2. 调用 BacktestService 运行回测
    3. 通过 TaskManager 异步执行
    4. 回测结果直接放入 state.result (L771 合规, 无 dual-track)
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

        self._task_id: str | None = None
        self._state: BacktestState = BacktestState()
        self._subscribers: list[Callable[[BacktestState], None]] = []

    @property
    def state(self) -> BacktestState:
        return self._state

    def subscribe(self, callback: Callable[[BacktestState], None]) -> Callable[[], None]:
        """订阅状态变更。返回取消订阅函数。"""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self):
        """清理资源：先取消运行中任务（防孤儿），再清引用与状态。"""
        self.cancel_backtest()
        self._task_id = None
        self._subscribers.clear()
        self._state = BacktestState()

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

        if self.state.is_running:
            self._set_state(
                status_message=Message("backtest_already_running"),
                status_color="warning",
            )
            return

        self._task_id = None
        self._set_state(
            is_running=True,
            progress=0.0,
            progress_message=Message("backtest_initializing"),
            status_message=Message("backtest_starting"),
            status_color="info",
            result=None,
        )

        async def _execute_backtest(task_id: str, **kwargs):
            try:

                def _progress_callback(progress: float, message: str):
                    if not self.state.is_running:
                        return
                    # NOTE(lazy): message 是 service/engine 层硬编码英文字符串(非 i18n key),
                    #   暂以原字符串作为 Message.key 直接透传。
                    #   ceiling: service 传 i18n key + params 或新增 backtest_progress 通用 key。
                    #   upgrade: BacktestView 声明式重写已完成(Phase C.2), i18n 改造待 Phase R.2.3 执行.
                    self._set_state(
                        progress=progress,
                        progress_message=Message(message, {}),
                    )
                    TaskManager().update_progress(task_id, progress, message)

                def _cancel_check() -> bool:
                    return TaskManager().is_cancelled(task_id)

                result = await self.service.run_backtest(
                    strategy_key=strategy_key,
                    config=config,
                    params=params,
                    progress_callback=_progress_callback,
                    cancel_check=_cancel_check,
                )

                # await 后重新读取 self._state 获取最新快照 (竞态安全);
                # result 直接放入 state.result (L771 合规, 无 dual-track)
                self._set_state(
                    result=result,
                    status_message=Message(
                        "backtest_completed",
                        {"duration": result.duration_ms},
                    ),
                    status_color="success",
                )

                return Message("backtest_success", {"sharpe": f"{result.metrics['sharpe_ratio']:.2f}"})

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[BacktestVM] Backtest failed: %s", e, exc_info=True)
                self._set_state(
                    status_message=Message("backtest_failed"),
                    status_color="error",
                )
                raise
            finally:
                self._set_state(
                    is_running=False,
                    progress=1.0,
                    progress_message=Message("backtest_done"),
                )

        strategy_obj = get_strategy_registry().get(strategy_key)
        name_key = getattr(strategy_obj, "name_key", None) if strategy_obj else None
        # Task 3.1: VM 不调 I18n.get; task name 改为 Message, View 渲染时翻译.
        # name_key 是 i18n key (策略名), 用 *_key params 约定传给 View.
        # 若 strategy_obj 不存在或缺 name_key, 回退到 strategy_key 字面值 (无翻译).
        task_id = TaskManager().submit_task(
            name=Message(
                "task_name_backtest",
                {"name_key": name_key or strategy_key, "fallback": strategy_key},
            ),
            task_type=Message("task_type_backtest"),
            coroutine_factory=_execute_backtest,
            cancellable=True,
        )

        self._task_id = task_id

        if task_id is None:
            self._set_state(
                is_running=False,
                status_message=Message("backtest_task_rejected"),
                status_color="warning",
            )

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
