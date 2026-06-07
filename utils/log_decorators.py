"""
日志装饰器工具集 - Log Decorators

提供装饰器和上下文管理器,实现:
- 自动操作日志记录
- 性能监控
- 参数和返回值脱敏
- 异常追踪
"""

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable)

from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


class PerfThreshold:
    """标准性能红线界定（毫秒）"""

    MEMORY_COMPUTE = 50  # 内存与本地纯计算
    DB_SINGLE_QUERY = 200  # 数据库单行/少数读写
    EXTERNAL_NETWORK = 2000  # 外部公网接口调用 (如 Tushare)
    DB_BULK_IO = 5000  # 数据库大批量聚合插入
    AI_INFERENCE = 15000  # 本地大模型推理计算
    GLOBAL_INIT = 15000  # 全局大动作


class UILogger:
    """UI 交互动作全量埋点专用日志"""

    _logger = logging.getLogger("ui.action")

    @classmethod
    def log_action(
        cls,
        component: str,
        action: str,
        target: str | None = None,
        details: str | None = None,
    ):
        """
        静态辅助方法：为匿名 Lambda 等无法挂接装饰器的场景准备的单行打点入口
        """
        msg = f"[UI_ACTION] {component} | action={action}"
        if target:
            msg += f" | target={target}"
        if details:
            msg += f" | {details}"
        cls._logger.info(msg)


def log_ui_action(
    component_name: str,
    action_type: str = "Click",
    target_name: str | None = None,
):
    """
    UI 动作强制埋点装饰器 (适用于绑定在类方法的 Event Handler)
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            UILogger.log_action(component_name, action_type, target_name)
            return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            UILogger.log_action(component_name, action_type, target_name)
            return await func(*args, **kwargs)

        return cast(F, async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper)

    return decorator


def log_async_operation(
    operation_name: str | None = None,
    sanitize_params: list[str] | None = None,
    log_args: bool = False,
    log_result: bool = False,
    log_exceptions: bool = True,
    threshold_ms: int | None = None,
    log_level: int = logging.DEBUG,
):
    """
    异步操作自动日志装饰器

    自动记录:
    - 操作开始和结束
    - 执行耗时
    - 参数(可选,脱敏)
    - 返回值摘要(可选)
    - 异常信息

    Args:
        operation_name: 操作名称(默认使用函数名)
        sanitize_params: 需要脱敏的参数名列表
        log_args: 是否记录参数
        log_result: 是否记录返回值
        log_exceptions: 是否记录异常
        threshold_ms: 性能阈值(毫秒),超过则记录WARNING
        log_level: 默认日志级别，默认为 DEBUG，不打扰控制台

    Usage:
        @log_async_operation(operation_name="sync_data", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
        async def sync_daily_market_snapshot(self, trade_date=None):
            ...
    """

    def decorator(func: F) -> F:
        # 确定操作名称
        op_name = operation_name or func.__name__

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()

            # 构建参数日志
            if log_args:
                # 脱敏参数
                clean_args, clean_kwargs = DataSanitizer.sanitize_args(
                    *(args[1:] if args else []),  # 跳过self
                    sensitive_patterns=sanitize_params or [],
                    **kwargs,
                )
                logger.debug(f"[{op_name}] args: {clean_kwargs}")

            logger.log(log_level, f"[{op_name}] started")

            try:
                # 执行原函数
                result = await func(*args, **kwargs)

                # 计算耗时
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                elapsed_str = f"{elapsed_ms:.1f}ms" if elapsed_ms < 1000 else f"{elapsed_ms / 1000:.2f}s"

                # 构建结果日志
                result_info = ""
                if log_result and result is not None:
                    if hasattr(result, "__len__"):
                        result_info = f" | result: {type(result).__name__}({len(result)} items)"
                    else:
                        result_info = f" | result: {type(result).__name__}"

                # 性能检查
                final_level = log_level
                perf_warning = ""
                if threshold_ms is not None and elapsed_ms > threshold_ms:
                    final_level = logging.WARNING
                    perf_warning = f" [SLOW: >{threshold_ms}ms threshold]"

                logger.log(
                    final_level,
                    f"[{op_name}] completed in {elapsed_str}{result_info}{perf_warning}",
                )

                return result

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                elapsed_str = f"{elapsed_ms:.1f}ms" if elapsed_ms < 1000 else f"{elapsed_ms / 1000:.2f}s"

                if log_exceptions:
                    # 脱敏异常信息
                    error_msg = DataSanitizer.sanitize_error(e)
                    # Always log traceback for errors to ensure debuggability
                    logger.error(
                        f"[{op_name}] failed after {elapsed_str}: {type(e).__name__} - {error_msg}",
                        exc_info=True,
                    )

                # 重新抛出异常
                raise

        return cast(F, wrapper)

    return decorator


def track_performance(
    threshold_ms: int = PerfThreshold.DB_BULK_IO,
    alert_level: str = "WARNING",
    operation_name: str | None = None,
):
    """
    性能追踪装饰器

    超过阈值时自动记录告警

    Args:
        threshold_ms: 性能阈值(毫秒)
        alert_level: 告警级别(INFO/WARNING/ERROR)
        operation_name: 操作名称(默认使用函数名)

    Usage:
        @track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
        async def slow_operation():
            ...
    """

    def decorator(func: F) -> F:
        op_name = operation_name or func.__name__
        level = getattr(logging, alert_level.upper(), logging.WARNING)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.perf_counter()
                result = await func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time
                elapsed_ms = elapsed * 1000

                if elapsed_ms > threshold_ms:
                    logger.log(
                        level,
                        f"[{op_name}] took {elapsed:.2f}s (threshold: {threshold_ms}ms)",
                    )

                return result

            return cast(F, async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start_time
            elapsed_ms = elapsed * 1000

            if elapsed_ms > threshold_ms:
                logger.log(
                    level,
                    f"[{op_name}] took {elapsed:.2f}s (threshold: {threshold_ms}ms)",
                )

            return result

        return cast(F, sync_wrapper)

    return decorator


class AsyncOperationLogger:
    """
    异步操作上下文管理器

    用于复杂流程的分段日志记录

    Usage:
        async with AsyncOperationLogger("historical_sync", {"days": 365}) as op:
            op.log_milestone("fetched_dates", count=243)
            op.add_metric("failed", 5)
            # 自动在退出时汇总
    """

    def __init__(
        self,
        operation: str,
        context: dict | None = None,
        log_level: int = logging.DEBUG,
    ):
        """
        Args:
            operation: 操作名称
            context: 上下文信息
            log_level: 日志级别
        """
        self.operation = operation
        self.context = context or {}
        self.metrics = {}
        self.start_time = None
        self.logger = logging.getLogger(__name__)
        self.log_level = log_level

    async def __aenter__(self):
        """进入上下文"""
        self.start_time = time.perf_counter()

        # 记录开始
        context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
        params_str = f" | {context_str}" if context_str else ""
        self.logger.log(self.log_level, f"[{self.operation}] started{params_str}")

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文"""
        elapsed = time.perf_counter() - self.start_time  # type: ignore[untyped]
        # 构建度量摘要
        metrics_str = ""
        if self.metrics:
            import datetime
            import json

            def _json_serial(obj):
                if isinstance(obj, (datetime.datetime, datetime.date)):
                    return obj.isoformat()
                raise TypeError(f"Type {type(obj)} not serializable")

            metrics_str = f" | metrics: {json.dumps(self.metrics, default=_json_serial)}"

        # 记录完成
        if exc_type is None:
            self.logger.log(
                self.log_level,
                f"[{self.operation}] completed in {elapsed:.1f}s{metrics_str}",
            )
        else:
            # 异常情况
            error_msg = DataSanitizer.sanitize_error(exc_val)
            self.logger.error(
                f"[{self.operation}] failed after {elapsed:.1f}s: {exc_type.__name__} - {error_msg}{metrics_str}",
                exc_info=True,
            )

        return False  # 不抑制异常

    def log_milestone(self, milestone: str, **kwargs):
        """
        记录阶段性里程碑

        Args:
            milestone: 里程碑名称
            **kwargs: 附加信息
        """
        info_str = ", ".join(f"{k}={v}" for k, v in kwargs.items())
        params_str = f" | {info_str}" if info_str else ""
        self.logger.log(self.log_level, f"[{self.operation}] {milestone}{params_str}")

    def add_metric(self, key: str, value: Any):
        """
        添加度量指标

        Args:
            key: 指标名
            value: 指标值
        """
        self.metrics[key] = value
