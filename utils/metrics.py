"""
进程内运行时指标聚合 - In-process runtime metrics aggregation (review05-E19)

在不引入外部监控依赖（Prometheus / OpenTelemetry）的前提下，对
``utils.log_decorators`` 与 ``utils.error_classifier`` 等观测接入点记录的
操作耗时与错误码做进程内聚合，供系统诊断导出
（``utils.diagnostics.SystemDiagnosticsCollector.export``）持久化到诊断包。

纯进程内聚合器：单线程 UI 读取模型下由 ``threading.Lock`` 保护，
以兼容 ThreadPoolManager 线程池回调路径的并发写入。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class OperationStats:
    """单个操作类型的聚合统计值对象。"""

    count: int = 0
    error_count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    error_codes: dict[str, int] = field(default_factory=dict)

    def record_elapsed(self, elapsed_ms: float) -> None:
        """记录一次耗时的取值，刷新总耗时与最大耗时（成功/失败路径均可）。"""
        self.count += 1
        self.total_ms += elapsed_ms
        if elapsed_ms > self.max_ms:
            self.max_ms = elapsed_ms

    def record_error(self, error_code: str) -> None:
        """记录一次错误并累计错误码分布（不改变耗时计数）。"""
        self.error_count += 1
        self.error_codes[error_code] = self.error_codes.get(error_code, 0) + 1


class MetricsRegistry:
    """进程内运行时指标聚合注册表（线程安全）。

    语义：``count`` 为操作总次数（含成功与失败），``error_count`` 为其中失败
    次数（始终 ≤ count），``error_codes`` 提供错误码维度分布。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, OperationStats] = {}

    def record(self, operation: str, elapsed_ms: float, error_code: str | None = None) -> None:
        """记录一次操作：累计耗时，若 ``error_code`` 非空则同时累计错误。"""
        with self._lock:
            stats = self._stats.setdefault(operation, OperationStats())
            stats.record_elapsed(elapsed_ms)
            if error_code is not None:
                stats.record_error(error_code)

    def record_error(self, operation: str, error_code: str) -> None:
        """记录一次无耗时信息的错误事件（仅累计错误码分布）。"""
        with self._lock:
            stats = self._stats.setdefault(operation, OperationStats())
            stats.record_error(error_code)

    def snapshot(self) -> dict[str, OperationStats]:
        """返回当前聚合快照（防御性拷贝，外部修改不影响内部状态）。"""
        with self._lock:
            return {
                op: OperationStats(
                    count=s.count,
                    error_count=s.error_count,
                    total_ms=s.total_ms,
                    max_ms=s.max_ms,
                    error_codes=dict(s.error_codes),
                )
                for op, s in self._stats.items()
            }

    def reset(self) -> None:
        """清空全部聚合数据（测试隔离/诊断重置）。"""
        with self._lock:
            self._stats.clear()


# 进程内全局注册表实例，由 log_decorators / error_classifier / diagnostics 共享引用。
metrics_registry = MetricsRegistry()
