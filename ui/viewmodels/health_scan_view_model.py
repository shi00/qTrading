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
- status_text 字段值是 data 层 progress_callback 回调透传的 Message（key+params），
  VM 仅透传不解析，View 渲染时 I18n.get(status_text.key, **status_text.params)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from data.constants import (
    HEALTH_CHECK_TABLES as HEALTH_CHECK_TABLES,  # re-export for HealthReportDialog (过渡期, 完全 MVVM 改造见技术债)
    HEALTH_DEPTH_WARNING_RATIO as HEALTH_DEPTH_WARNING_RATIO,
    HEALTH_REPORT_ORDER as HEALTH_REPORT_ORDER,
    HEALTH_THRESHOLD_BREADTH as HEALTH_THRESHOLD_BREADTH,
    HEALTH_THRESHOLD_FINANCIAL_COVERAGE as HEALTH_THRESHOLD_FINANCIAL_COVERAGE,
    HEALTH_THRESHOLD_FINANCIAL_EXCELLENT as HEALTH_THRESHOLD_FINANCIAL_EXCELLENT,
)
from data.data_processor import DataProcessor
from core.i18n import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityScanResult:
    """深度扫描评分汇总 (D10: 替换 ``dict[str, Any]`` 裸 dict, 兑现 frozen 契约).

    渲染就绪字段: View 只读, 不可就地修改 state.result (frozen dataclass).

    Attributes:
        score: 综合评分 0~100
        tier: 质量等级 1~3
        sample_size: 抽样股票数
        avg_continuity: 平均连续覆盖率
        avg_lag: 平均滞后天数
        avg_fundamental: 基本面字段完整度 (未计算时为 None)
        fin_recency_ok: 财务报告新鲜度是否达标
    """

    score: int = 0
    tier: int = 0
    sample_size: int = 0
    avg_continuity: float = 0.0
    avg_lag: float = 99.0
    avg_fundamental: float | None = None
    fin_recency_ok: bool = False


@dataclass(frozen=True)
class HealthScanState:
    """HealthScanViewModel 的不可变状态快照。View 通过 subscribe 接收。

    Attributes:
        scan_state: 扫描状态 ("idle" | "scanning" | "done" | "error")
        progress: 进度 0.0~1.0
        status_text: data 层 progress_callback 回调透传的 Message（key+params）
            (data/mixins/health_mixin.py 构造 Message 后传入；VM 不调 I18n.get)
        result: 扫描结果 (D10: QualityScanResult frozen dataclass, scan_state="done" 时非 None)
        error_key: 错误状态 i18n key（View 渲染时 I18n.get(error_key)），非错误时为 None
    """

    scan_state: str = "idle"
    progress: float = 0.0
    status_text: Message | str | None = ""
    result: QualityScanResult | None = None
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
        # 保留 _futures 集合用于测试契约稳定性（当前 on_progress 已不再写入，
        # 但测试手动填充调用 cancel_pending_futures 的断言仍需该属性存在）。
        self._futures: set[asyncio.Future] = set()
        # Mixin 字段初始化（跨线程修复）- 不再单独维护 self._main_loop
        self._init_mixin_fields()
        # P2-3: dispose 后阻止延迟回调 (_update_progress 跨线程) 更新 state.
        # 对齐 ScreenerViewModel 的 _disposed flag 模式.
        self._disposed: bool = False

    def _set_state(self, **changes: Any) -> None:
        """Update state fields and notify subscribers (P2-3: 加 _disposed guard).

        dispose 后跨线程回调仍可能触发 _set_state; guard 使其短路, 避免更新已清理的
        state/subscribers (对齐 ScreenerViewModel 模式). 与 Mixin._set_state disposed
        guard 冗余但不冲突，保留作为短路优化。
        """
        if self._disposed:
            return
        super()._set_state(**changes)

    async def start_scan(self) -> None:
        """启动扫描任务（业务 command）。

        - data_processor 为 None 时设置 error state
        - on_progress 来自工作线程，不再手动 run_coroutine_threadsafe 封送；
          Mixin._set_state -> _notify 自动检测跨线程并封送（统一双轨消除 P1-3）
        - R2: CancelledError 必须 raise
        """
        if self._data_processor is None:
            self._set_state(scan_state="error", error_key="db_err_format")
            return

        self._set_state(scan_state="scanning")

        def on_progress(current: int, total: int, msg: Message) -> None:
            """工作线程回调：直接调 _set_state，由 Mixin 自动封送回主 loop。"""
            self._set_state(progress=current / total, status_text=msg)

        try:
            raw_result = await self._data_processor.run_quality_scan(
                sample_size=50,
                progress_callback=on_progress,
            )
            self._set_state(result=self._to_quality_scan_result(raw_result), scan_state="done")
        except asyncio.CancelledError:
            raise  # R2: CancelledError 必须传播以配合优雅停机
        except Exception as ex:
            logger.error("[HealthScanVM] Scan failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            self._set_state(scan_state="error", error_key="db_err_format")

    @staticmethod
    def _to_quality_scan_result(raw: dict | None) -> QualityScanResult:
        """将 data 层扫描结果 dict 收敛为渲染就绪 frozen 对象 (D10).

        raw 可能缺部分字段 (如 run_quality_scan 无数据早退仅返回 score/tier),
        逐字段 get 取默认值, 不抛异常.
        """
        if raw is None:
            return QualityScanResult()
        avg_fundamental = raw.get("avg_fundamental")
        return QualityScanResult(
            score=int(raw.get("score", 0)),
            tier=int(raw.get("tier", 1)),
            sample_size=int(raw.get("sample_size", 0)),
            avg_continuity=float(raw.get("avg_continuity", 0.0)),
            avg_lag=float(raw.get("avg_lag", 99.0)),
            avg_fundamental=float(avg_fundamental) if avg_fundamental is not None else None,
            fin_recency_ok=bool(raw.get("fin_recency_ok", False)),
        )

    def cancel_pending_futures(self) -> None:
        """取消 pending futures（R2 兼容不重新抛出）。

        之前 on_progress 用 run_coroutine_threadsafe 的 Future 跟踪已移除（Mixin 自动封送）。
        保留该属性与逻辑用于接口稳定性 + 测试契约；若未来需要引入可取消的进度 Future，
        可在此处扩展。``future.cancel()`` 在 future 已完成时返回 False，未完成时触发
        ``CancelledError`` 由 future 内部消化，不向调用方传播——符合关机清理语义。

        由 View 的 use_effect cleanup 调用（open 变化或卸载时）。
        """
        for f in list(self._futures):
            if not f.done():
                f.cancel()
        self._futures.clear()

    def dispose(self) -> None:
        """清理资源：先标记 disposed 短路延迟回调，再取消 pending futures + 清空订阅者。"""
        # P2-3: 先置 _disposed=True, 使后续延迟回调触发的 _set_state 短路
        self._disposed = True
        self.cancel_pending_futures()
        # Mixin 统一清理 subscribers / loop / pending handle / deque
        super().dispose()
