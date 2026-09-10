import functools
import inspect
import logging
import os
import typing
from enum import IntEnum

from utils.app_env import is_e2e_mode

# D2-9: 复用数据质量服务常量（MAX_MISSING_REPORT），与 DataProcessor 扫描口径一致。
# data/persistence 同层导入合法，无循环依赖。
from data.persistence.data_quality import DataQualityService

logger = logging.getLogger(__name__)

_STRICT_QUALITY_GATE = os.environ.get("STRICT_QUALITY_GATE", "true").lower() in ("true", "1", "yes")


def is_strict_quality_gate_enabled() -> bool:
    """返回 STRICT_QUALITY_GATE 判定结果（模块常量，单一事实来源，review03-C15）。

    bootstrap 启动校验与本模块共用此函数，避免 env 判定双源漂移。
    """
    return _STRICT_QUALITY_GATE


class QualityTier(IntEnum):
    CRITICAL = 0  # Data missing or broken
    BRONZE = 1  # Availability Check Passed (Tier 1)
    SILVER = 2  # Continuity/Recency Passed (Tier 2) - Safe for MA/RSI
    GOLD = 3  # Reliability/Cross-Validation Passed (Tier 3) - Safe for Alpha


class QualityGateError(Exception):
    """Raised when data quality is insufficient for the strategy."""

    pass


class QualityGate:
    """
    Gatekeeper for Strategy Execution.
    Ensures data quality meets the required tier before running a strategy.
    """


def _find_processor(instance: typing.Any, args: typing.Any, kwargs: typing.Any):
    """Shared logic to locate the DataProcessor instance.

    Args:
        instance: The bound 'self' of the decorated method (e.g. a Strategy object).
        args: Positional arguments passed to the decorated method.
        kwargs: Keyword arguments passed to the decorated method.
    """
    processor = getattr(instance, "data_processor", None)
    if not processor:
        processor = kwargs.get("data_processor")
    if not processor and len(args) > 0 and isinstance(args[0], dict):
        processor = args[0].get("data_processor")
    return processor


def _check_tier(
    processor: typing.Any,
    min_tier: typing.Any,
    func_name: typing.Any,
    require_continuous_window: bool = False,
):
    """Shared logic to verify quality tier, optionally enforcing window integrity."""
    if is_e2e_mode():
        logger.info("[QualityGate] E2E mode: bypassing quality check for %s", func_name)
        return

    if processor is None:
        if _STRICT_QUALITY_GATE:
            raise QualityGateError(
                f"QualityGate STRICT mode: DataProcessor not found for {func_name}. "
                f"Set STRICT_QUALITY_GATE=false to bypass (not recommended in production)."
            )
        logger.error(
            "[QualityGate] Bypassed for %s: DataProcessor not found in context "
            "AND STRICT_QUALITY_GATE disabled — quality gate is a safety mechanism, "
            "this should not happen.",
            func_name,
        )
        return
    current_tier = getattr(processor, "_quality_tier", None)
    if current_tier is None:
        current_tier = 0  # Treat uninitialized as CRITICAL
    if current_tier < min_tier:
        from core.i18n import I18n

        msg = I18n.get(
            "quality_err_too_low",
            required=min_tier.name,
            current=QualityTier(current_tier).name,
        )
        if msg == "quality_err_too_low":  # Fallback if I18n not initialized
            msg = f"Data Quality too low for {func_name}. Required: {min_tier.name}, Current: {QualityTier(current_tier).name}"
        # Task 5.2: 附归因信息 (落后表名 + lag_days) 从 health_cache 读取
        health_cache = getattr(processor, "_health_cache", None)
        if health_cache and isinstance(health_cache, dict):
            health_data = health_cache.get("data")
            if health_data and isinstance(health_data, dict):
                market_info = health_data.get("market") or {}
                lag_days = market_info.get("lag_days", 0) if isinstance(market_info, dict) else 0
                if isinstance(lag_days, (int, float)) and lag_days > 0:
                    attribution = I18n.get("quality_err_attribution", lag_days=int(lag_days))
                    if attribution != "quality_err_attribution":
                        msg = f"{msg} {attribution}"
        # D2-9: 附采样缺失交易日归因。质量门控除等级外，报告具体缺失的交易日
        # （采样代理证据，非全市场全量）。截断至 MAX_MISSING_REPORT 防消息过长。
        # 类型守卫：真实字段为 frozenset；MagicMock 替身返回非集合对象时静默跳过，
        # 不破坏既有门控测试。
        scan_missing = getattr(processor, "_scan_missing_dates", frozenset())
        if isinstance(scan_missing, frozenset) and scan_missing:
            top = sorted(scan_missing)[: DataQualityService.MAX_MISSING_REPORT]
            msg = f"{msg} | 采样缺失交易日: {', '.join(top)}"
            logger.debug("[QualityGate] missing_dates=%s", ",".join(sorted(scan_missing)))
        logger.warning("[QualityGate] %s", msg)
        raise QualityGateError(msg)

    # D2-9: 可选区间完整性检查（保守拦截）。仅当策略显式声明 require_continuous_window=True 时启用。
    # 等级达标（未走上方报错分支）后，若本轮质量扫描在采样中检测到缺失交易日（代理证据，
    # 非全市场全量），即使等级满足也拦截——防止"等级达标但策略所需窗口恰好缺某天"仍放行的核心风险。
    # 边界：fast-path（静默启动、未跑 run_quality_scan 深扫描）时 _scan_missing_dates 为空 frozenset
    # ⇒ 不拦截（不新增 DB 查询）；MagicMock 替身返回非 frozenset 集合对象时静默跳过，不破坏既有门控测试。
    if require_continuous_window:
        scan_missing = getattr(processor, "_scan_missing_dates", frozenset())
        if isinstance(scan_missing, frozenset) and scan_missing:
            top = sorted(scan_missing)[: DataQualityService.MAX_MISSING_REPORT]
            msg = (
                f"QualityGate: continuous window integrity check failed for {func_name}. "
                f"Scan detected missing trading dates: {', '.join(top)}"
            )
            logger.warning("[QualityGate] %s", msg)
            raise QualityGateError(msg)


_CallableT = typing.TypeVar("_CallableT", bound=typing.Callable)


def require_quality(
    min_tier: QualityTier | None = None,
    *,
    from_attr: str | None = None,
    require_continuous_window: bool = False,
):
    """
    Decorator to enforce data quality requirements.
    Supports both sync and async methods.

    两种形式（互斥，恰好提供其一）：
      @require_quality(QualityTier.SILVER)                          # 固定等级
      @require_quality(from_attr="required_quality_tier")           # 运行时读 self.<attr>

    Optional（D2-9）：
      require_continuous_window=True  // 等级达标后再做区间完整性检查（保守拦截）。
      // 依据 processor._scan_missing_dates（run_quality_scan 采样的缺失交易日代理证据）
      // 判定：声明后若采样检测到任何缺失日即便等级达标也抛 QualityGateError，防止
      // "策略所需窗口恰好缺某天"仍放行。默认 False，不改变既有策略行为。

    Usage:
        @require_quality(QualityTier.SILVER)
        def _filter_logic(self, lf: typing.Any, context: typing.Any):
            ...

        @require_quality(QualityTier.SILVER)
        async def filter(self, context: typing.Any):
            ...
    """
    if (min_tier is None) == (from_attr is None):
        raise TypeError("require_quality: exactly one of min_tier or from_attr must be provided")

    def _resolve_tier(instance: typing.Any) -> QualityTier:
        """解析本次调用的目标质量等级（D2-8 唯一门控入口的等级来源）。"""
        if min_tier is not None:
            return min_tier
        assert from_attr is not None  # 内部不变量：require_quality 顶部互斥校验已保证 min_tier 与 from_attr 恰好其一
        tier = getattr(instance, from_attr, None)
        if tier is None:
            raise QualityGateError(
                f"require_quality(from_attr={from_attr!r}): attribute missing or None on "
                f"{type(instance).__name__}. Set {from_attr!r} on the strategy class."
            )
        return tier

    def decorator(func: _CallableT) -> _CallableT:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(self, *args: typing.Any, **kwargs: typing.Any):
                processor = _find_processor(self, args, kwargs)
                _check_tier(
                    processor,
                    _resolve_tier(self),
                    func.__name__,
                    require_continuous_window=require_continuous_window,
                )
                return await func(self, *args, **kwargs)

            return typing.cast(_CallableT, async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(self, *args: typing.Any, **kwargs: typing.Any):
            processor = _find_processor(self, args, kwargs)
            _check_tier(
                processor,
                _resolve_tier(self),
                func.__name__,
                require_continuous_window=require_continuous_window,
            )
            return func(self, *args, **kwargs)

        return typing.cast(_CallableT, sync_wrapper)

    return decorator
