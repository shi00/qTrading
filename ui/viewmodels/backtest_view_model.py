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
from dataclasses import dataclass
from datetime import date

from data.cache.cache_manager import CacheManager
from services.backtest_service import BacktestService
from services.task_manager import TaskManager
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.base_strategy import get_strategy_registry
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

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


class BacktestViewModel(ObservableViewModelMixin[BacktestState]):
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
        # P2-1: 跟踪 fire-and-forget task 生命周期，dispose 时取消避免孤儿 (对齐 ScreenerViewModel)
        self._background_tasks: set = set()

    def dispose(self):
        """清理资源：先取消运行中任务（防孤儿），再清引用与状态。"""
        self.cancel_backtest()
        self._task_id = None
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        # NOTE(lazy): 不立即 clear _background_tasks — done_callback (_on_background_task_done)
        # 会在任务完成时移除并读取 exception(), 避免 'Task exception was never retrieved'.
        # ceiling: 事件循环关闭导致 callback 不触发时, 任务随 VM 一起被 GC.
        # upgrade: 引入 async_dispose() 显式 await drain (本任务范围内不引入以保持微创).
        self._subscribers.clear()
        self._state = BacktestState()

    def _on_background_task_done(self, task: asyncio.Task) -> None:
        """Done callback: 移除已完成任务并记录非取消异常.

        - 丢弃任务引用前读取 task.exception() 标记异常已 retrieved,
          避免 'Task exception was never retrieved' 警告.
        - CancelledError 不记录为 error, 取消正常传播 (R2).
        """
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("[BacktestVM] Background task failed: %s", exc, exc_info=exc)

    def get_splitter_width(self, config_key: str, default_width: int) -> int:
        """读取持久化的 splitter 宽度 (P1-1: 经 VM 读取, View 不再直接 import ConfigHandler).

        ConfigHandler._config_cache 命中是纯内存读 (非 IO); 首次未命中触发小 JSON
        文件读 (单次 < 5ms), 在 use_effect 上下文中可接受。返回值由 ResizableSplitter
        内部 clamp 到 [min_width, max_width]。
        """
        from utils.config_handler import ConfigHandler

        return ConfigHandler.get_typed(config_key, int, default_width)

    def persist_splitter_width(self, config_key: str, width: int) -> None:
        """持久化 splitter 宽度 (P1-1/P2-1: 异步写盘, R16 合规). fire-and-forget.

        同步签名以满足 ResizableSplitter ``on_persist_width`` 回调契约; 内部经
        ThreadPoolManager.run_async 提交 IO 写盘, 不阻塞 Flet 事件处理器。
        复用 _background_tasks + _on_background_task_done 跟踪 task 生命周期。
        """
        from utils.config_handler import ConfigHandler
        from utils.thread_pool import TaskType, ThreadPoolManager

        async def _persist() -> None:
            try:
                await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_typed, config_key, width)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("[BacktestVM] persist_splitter_width failed: %s", e, exc_info=True)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环 (测试环境), 静默跳过
        task = loop.create_task(_persist())
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

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
