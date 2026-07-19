"""HealthScanViewModel — HealthScanDialog 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（HealthScanState frozen dataclass）
- subscribe/_notify 通知机制（hook 通过 use_viewmodel 订阅）
- commands 作为实例方法（稳定引用，View 事件处理器直接调用）

线程模型：
- DataProcessor.run_quality_scan 的 progress_callback 来自工作线程
- on_progress 通过 run_coroutine_threadsafe 调度回主 loop（R11 loop-local 守卫）
- _futures 集合持久化 pending futures；cancel_pending_futures 取消未完成的 future
  （R2 兼容：CancelledError 在 future 内部消化，不向调用方传播）

i18n 状态驱动（CLAUDE.md §3.2）：
- VM 不调 I18n.get，不感知 locale
- error_key 为 i18n key，View 渲染时 I18n.get(error_key)
- status_text 字段值是 data 层 progress_callback 回调透传的已翻译字符串
  （data/mixins/health_mixin.py 调 I18n.get 后传入），VM 仅透传不解析
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from data.data_processor import DataProcessor
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthScanState:
    """HealthScanViewModel 的不可变状态快照。View 通过 subscribe 接收。

    Attributes:
        scan_state: 扫描状态 ("idle" | "scanning" | "done" | "error")
        progress: 进度 0.0~1.0
        status_text: data 层 progress_callback 回调透传的已翻译字符串
            (data/mixins/health_mixin.py 调 I18n.get 后传入；VM 不调 I18n.get)
        result: 扫描结果字典（scan_state="done" 时非 None）
        error_key: 错误状态 i18n key（View 渲染时 I18n.get(error_key)），非错误时为 None
    """

    scan_state: str = "idle"
    progress: float = 0.0
    status_text: str = ""
    result: dict[str, Any] | None = None
    error_key: str | None = None


class HealthScanViewModel(ObservableViewModelMixin[HealthScanState]):
    """HealthScanDialog 的 ViewModel（CLAUDE.md §3.2 MVVM）。

    暴露方法：
        - ``start_scan()``：业务 command，启动扫描任务
        - ``cancel_pending_futures()``：生命周期清理，取消 pending futures
          （由 View use_effect cleanup 调用；关闭即取消）
        - ``dispose()``：清理资源（由 use_viewmodel 卸载时调用）

    DataProcessor 经构造函数注入（DI），便于测试替身传入。
    """

    def __init__(self, data_processor: DataProcessor | None = None) -> None:
        self._data_processor = data_processor
        self._state: HealthScanState = HealthScanState()
        self._subscribers: list[Callable[[HealthScanState], None]] = []
        self._futures: set[asyncio.Future] = set()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # P2-3: dispose 后阻止延迟回调 (_update_progress via run_coroutine_threadsafe) 更新 state.
        # 对齐 ScreenerViewModel 的 _disposed flag 模式.
        self._disposed: bool = False

    def subscribe(self, callback: Callable[[HealthScanState], None]) -> Callable[[], None]:
        """订阅 state 变化，返回退订函数。同时捕获 main loop（hook 在主循环注册）。"""
        self._subscribers.append(callback)
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[HealthScanVM] subscribed without running loop (test mode)")

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _set_state(self, **changes: Any) -> None:
        """Update state fields and notify subscribers (P2-3: 加 _disposed guard).

        dispose 后跨线程回调 (run_coroutine_threadsafe 调度的 _update_progress)
        可能仍触发 _set_state; guard 使其短路, 避免更新已清理的 state/subscribers
        (对齐 ScreenerViewModel:190-195 模式).
        """
        if self._disposed:
            return
        self._state = replace(self._state, **changes)
        self._notify()

    async def start_scan(self) -> None:
        """启动扫描任务（业务 command）。

        - data_processor 为 None 时设置 error state
        - 通过 run_coroutine_threadsafe 调度 on_progress 回调到主 loop（R11）
        - R2: CancelledError 必须 raise
        """
        if self._data_processor is None:
            self._set_state(scan_state="error", error_key="db_err_format")
            return

        loop = asyncio.get_running_loop()
        self._main_loop = loop
        self._set_state(scan_state="scanning")

        def on_progress(current: int, total: int, msg: str) -> None:
            """工作线程回调：调度 _update_progress 到主 loop（线程安全）。"""
            fut = asyncio.run_coroutine_threadsafe(
                self._update_progress(current, total, msg),
                loop,
            )
            self._futures.add(fut)
            fut.add_done_callback(self._futures.discard)

        try:
            result = await self._data_processor.run_quality_scan(
                sample_size=50,
                progress_callback=on_progress,
            )
            self._set_state(result=result, scan_state="done")
        except asyncio.CancelledError:
            raise  # R2: CancelledError 必须传播以配合优雅停机
        except Exception as ex:
            logger.error("[HealthScanVM] Scan failed: %s", ex, exc_info=True)
            self._set_state(scan_state="error", error_key="db_err_format")

    async def _update_progress(self, current: int, total: int, msg: str) -> None:
        """主 loop 上更新进度 state（跨线程通过 run_coroutine_threadsafe 调度）。"""
        self._set_state(progress=current / total, status_text=msg)

    def cancel_pending_futures(self) -> None:
        """取消 pending futures（R2 兼容不重新抛出）。

        ``future.cancel()`` 在 future 已完成时返回 False，未完成时触发
        ``CancelledError`` 由 future 内部消化（run_coroutine_threadsafe 的 coroutine
        收到 CancelledError），不向调用方传播——符合关机清理语义。

        由 View 的 use_effect cleanup 调用（open 变化或卸载时）。
        """
        for f in list(self._futures):
            if not f.done():
                f.cancel()
        self._futures.clear()

    def dispose(self) -> None:
        """清理资源：先标记 disposed 短路延迟回调，再取消 pending futures + 清空订阅者。"""
        # P2-3: 先置 _disposed=True, 使后续延迟回调 (_update_progress via run_coroutine_threadsafe)
        # 触发的 _set_state 短路, 避免更新已清理的 state/subscribers.
        self._disposed = True
        self.cancel_pending_futures()
        self._subscribers.clear()
