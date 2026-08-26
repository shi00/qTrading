"""回测 ViewModel

遵循项目 MVVM 模式（V1 声明式范式）：
- frozen dataclass BacktestState + subscribe/_notify
- 调用 BacktestService 运行回测
- 通过 TaskManager.submit_task() 异步执行
- 回测结果拆解为渲染就绪字段放入 state (D11, L771 合规, 无 dual-track version + property)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from data.cache.cache_manager import CacheManager
from services.backtest_service import BacktestService
from services.task_manager import TaskManager
from strategies.backtest.config import BacktestConfig
from strategies.base_strategy import get_strategy_registry
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

TASK_NAME_PREFIX = "backtest"

# Task 8.3: 选股→回测参数透传 — 模块级 pending prefill stash.
# 选股页跳转前写入, 回测页 mount 时 consume_pending_prefill() 读取并清空.
# 单次消费语义, 无持久化 (YAGNI: 跨视图临时传递, 不引入全局状态服务).
_pending_prefill: dict[str, object] = {}


def set_pending_prefill(strategy_key: str, params: dict | None = None) -> None:
    """选股页调用: 暂存待透传的 strategy_key + params (Task 8.3)."""
    _pending_prefill.clear()
    _pending_prefill["strategy_key"] = strategy_key
    _pending_prefill["params"] = dict(params) if params else {}


def consume_pending_prefill() -> dict[str, object] | None:
    """回测页 mount 时调用: 读取并清空 pending prefill (单次消费).

    Returns:
        含 strategy_key/params 的 dict, 或 None 表示无待消费 prefill.
    """
    if not _pending_prefill:
        return None
    data = dict(_pending_prefill)
    _pending_prefill.clear()
    return data


@dataclass(frozen=True)
class TradeRow:
    """回测成交记录行 (D11: 渲染就绪, 与 engine trades DataFrame 列对齐).

    列对应 portfolio.py trades_list: trade_date / ts_code / action /
    price / volume / realized_pnl (buy 记录 realized_pnl=0).
    """

    trade_date: str
    ts_code: str
    action: str
    price: float
    volume: float
    realized_pnl: float


def _to_trade_rows(trades_df: Any) -> tuple[TradeRow, ...]:
    """BacktestResult.trades (pl.DataFrame) → tuple[TradeRow, ...] (D11 拆解)."""
    if trades_df is None or trades_df.is_empty():
        return ()
    return tuple(
        TradeRow(
            trade_date=str(r.get("trade_date", "")),
            ts_code=r.get("ts_code", "") or "",
            action=r.get("action", "") or "",
            price=float(r.get("price", 0) or 0),
            volume=float(r.get("volume", 0) or 0),
            realized_pnl=float(r.get("realized_pnl", 0) or 0),
        )
        for r in trades_df.iter_rows(named=True)
    )


def _to_period_stats_rows(period_df: Any) -> tuple[tuple[str, float, float, float], ...]:
    """BacktestResult.period_stats (pl.DataFrame) → 渲染行 (D11 拆解).

    行 = (year_month, monthly_return, benchmark_return, excess_return), 均为原始值,
    View 渲染时格式化百分比.
    """
    if period_df is None or period_df.is_empty():
        return ()
    return tuple(
        (
            str(r.get("year_month", "")),
            float(r.get("monthly_return", 0) or 0),
            float(r.get("benchmark_return", 0) or 0),
            float(r.get("excess_return", 0) or 0),
        )
        for r in period_df.iter_rows(named=True)
    )


@dataclass(frozen=True)
class BacktestState:
    """BacktestViewModel 的不可变状态快照 (L771 合规, 无 dual-track).

    D11: result (BacktestResult 领域对象, 含 pl.DataFrame/pl.Series) 拆解为
    渲染就绪字段, 全部 frozen + 可哈希 → 使用 dataclass 默认 __eq__/__hash__
    (Flet use_state setter 同值跳过安全).
    D2: strategies/selected_strategy/last_run 业务状态下沉 VM (对齐 Screener R.2.2),
    View 不再持 use_state 业务状态.
    """

    # D2: 可用策略 (key, name_key) 序列 + 选中策略 key (VM 初始化时装配, 不感知 locale)
    available_strategies: tuple[tuple[str, str], ...] = ()
    selected_strategy_key: str | None = None
    # D2: 上次回测提交 (strategy_key, config), 供 ErrorState on_retry 复用
    last_run_summary: tuple[str, BacktestConfig] | None = None

    is_running: bool = False
    progress: float = 0.0
    progress_message: Message | None = None
    status_message: Message | None = None
    status_color: str = ""
    # 回测结果 (渲染就绪, 源自 BacktestResult):
    # - metrics: (key, value) 键值对序列 (result.metrics 为 dict[str, float])
    # - trades: 成交记录 (分页渲染)
    # - nav_curve / ic_series: 图表点序列
    # - period_stats: 月收益表行 (year_month, monthly_return, benchmark_return, excess_return)
    metrics: tuple[tuple[str, float], ...] = ()
    trades: tuple[TradeRow, ...] = ()
    nav_curve: tuple[float, ...] = ()
    ic_series: tuple[float, ...] = ()
    period_stats: tuple[tuple[str, float, float, float], ...] = ()
    # 已脱敏的错误详情 (Task 11.4): run_backtest 失败时由 DataSanitizer.sanitize_error 产出
    error_detail: str | None = None


class BacktestViewModel(ObservableViewModelMixin[BacktestState]):
    """
    回测 ViewModel（V1 声明式范式）。

    职责：
    1. 管理回测配置状态（frozen BacktestState snapshot）
    2. 调用 BacktestService 运行回测
    3. 通过 TaskManager 异步执行
    4. 回测结果拆解为渲染就绪字段放入 state（D11, L771 合规, 无 dual-track）
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
        # D2: 初始化时从 StrategyManager 装配可用策略 + 默认选中首个策略
        # (View 不再持 use_state 业务状态; 装配是内存读, 非 IO, 可同步在 __init__).
        _strategies = self._load_strategies()
        self._state: BacktestState = BacktestState(
            available_strategies=_strategies,
            selected_strategy_key=next((k for k, _ in _strategies), None),
        )
        self._subscribers: list[Callable[[BacktestState], None]] = []
        # P2-1: 跟踪 fire-and-forget task 生命周期，dispose 时取消避免孤儿 (对齐 ScreenerViewModel)
        self._background_tasks: set = set()
        self._init_mixin_fields()  # F3-08: 初始化 mixin 字段 (锁/loop/disposed flag)

    def dispose(self):
        """清理资源：先取消运行中任务（防孤儿），再调 super().dispose() 统一清理。"""
        self._disposed = True  # F3-07: 业务短路（对齐 ScreenerVM），防 cancel_backtest 回调写状态
        self.cancel_backtest()
        self._task_id = None
        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        # NOTE(lazy): 不立即 clear _background_tasks — done_callback (_on_background_task_done)
        # 会在任务完成时移除并读取 exception(), 避免 'Task exception was never retrieved'.
        # ceiling: 事件循环关闭导致 callback 不触发时, 任务随 VM 一起被 GC.
        # upgrade: 引入 async_dispose() 显式 await drain (本任务范围内不引入以保持微创).
        super().dispose()  # F3-07: mixin 统一清理 _disposed=True + clear subscribers(锁内) + cancel handle + 置 loop=None
        # F3-07: 重置 state 到默认终态（UI 残留 is_running=True/progress=0.5 会导致 dispose 后仍渲染运行态）
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
            logger.error("[BacktestVM] Background task failed: %s", DataSanitizer.sanitize_error(exc), exc_info=exc)

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
                logger.debug(
                    "[BacktestVM] persist_splitter_width failed: %s", DataSanitizer.sanitize_error(e), exc_info=True
                )

        loop = self._get_loop_or_none()  # F3-13: 统一 loop 获取（disposed 后返回 None，避免孤儿 task）
        if loop is None:
            return  # 无事件循环或已 disposed, 静默跳过
        task = loop.create_task(_persist())
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    def _load_strategies(self) -> tuple[tuple[str, str], ...]:
        """内部加载可用策略 (D2: 装配进 state, 不感知 locale).

        name_key 是策略 i18n key (raw), View 每次渲染按当前 locale 翻译
        (D16: VM 不调 I18n.get, 避免 stale 翻译).
        """
        from strategies.all_strategies import StrategyManager

        return tuple((key, getattr(s, "name_key", key) or key) for key, s in StrategyManager().strategies.items())

    def select_strategy(self, key: str | None) -> None:
        """选择策略 command (D2: 选中策略关键状态下沉 VM, 对齐 Screener R.2.2)."""
        self._set_state(selected_strategy_key=key)

    def record_last_run(self, strategy_key: str, config: BacktestConfig) -> None:
        """记录上次回测提交, 供 ErrorState on_retry 复用 (D2: last_run 下沉 VM).

        由 View 提交回测时调用 (_on_run_backtest), 与既有一致 (提交即记录, retry 复用).
        """
        self._set_state(last_run_summary=(strategy_key, config))

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

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
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
            metrics=(),
            trades=(),
            nav_curve=(),
            ic_series=(),
            period_stats=(),
            error_detail=None,
        )

        async def _execute_backtest(task_id: str, **kwargs):
            try:

                def _progress_callback(progress: float, message: Message):
                    if not self.state.is_running:
                        return
                    self._set_state(
                        progress=progress,
                        progress_message=message,
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

                # 成功终态: is_running=False + progress=1.0 + 拆解后渲染字段 (D11)
                self._set_state(
                    metrics=tuple(result.metrics.items()),
                    trades=_to_trade_rows(result.trades),
                    nav_curve=tuple(float(v) for v in result.nav_curve["nav"].to_list()),
                    ic_series=tuple(float(v) for v in result.ic_series.to_list()),
                    period_stats=_to_period_stats_rows(result.period_stats),
                    is_running=False,
                    progress=1.0,
                    progress_message=Message("backtest_done"),
                    status_message=Message(
                        "backtest_completed",
                        {"duration": result.duration_ms},
                    ),
                    status_color="success",
                    error_detail=None,
                )

                return Message("backtest_success", {"sharpe": f"{result.metrics.get('sharpe_ratio', 0):.2f}"})

            except asyncio.CancelledError:
                # F3-11: 取消路径显式终态（progress 清空避免 UI 残留文案）
                self._set_state(
                    is_running=False,
                    progress=0.0,
                    progress_message=None,
                    status_message=Message("backtest_cancelled"),
                    status_color="warning",
                )
                raise
            except Exception as e:
                # F3-09: 分级日志收口到 log_classified
                log_classified(
                    logger,
                    e,
                    "general",
                    "[BacktestVM] Backtest failed (%s): %s",
                    exc_info=True,
                )
                # F3-11: 失败路径显式终态（progress 清空避免 UI 残留文案）
                self._set_state(
                    is_running=False,
                    progress=0.0,
                    progress_message=None,
                    status_message=Message("backtest_failed"),
                    status_color="error",
                    error_detail=DataSanitizer.sanitize_error(e),
                )
                raise

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

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def get_historical_results(
        self,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """获取历史回测结果列表。"""
        return await self.service.list_results(strategy_name=strategy_name, limit=limit)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def load_historical_result(self, run_id: str) -> dict | None:
        """加载历史回测结果。"""
        result = await self.service.get_result(run_id)
        return result
