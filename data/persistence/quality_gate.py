import functools
import inspect
import logging
import os
import typing
from enum import IntEnum

logger = logging.getLogger(__name__)

_STRICT_QUALITY_GATE = os.environ.get("STRICT_QUALITY_GATE", "true").lower() in ("true", "1", "yes")


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


def _check_tier(processor: typing.Any, min_tier: typing.Any, func_name: typing.Any):
    """Shared logic to verify quality tier."""
    if os.environ.get("E2E_TESTING") == "true":
        logger.info("[QualityGate] E2E mode: bypassing quality check for %s", func_name)
        return

    if processor is None:
        if _STRICT_QUALITY_GATE:
            raise QualityGateError(
                f"QualityGate STRICT mode: DataProcessor not found for {func_name}. "
                f"Set STRICT_QUALITY_GATE=false to bypass (not recommended in production)."
            )
        logger.warning(
            "[QualityGate] Bypassed for %s: DataProcessor not found in context. (Could be test env or context missing)",
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
        logger.warning("[QualityGate] %s", msg)
        raise QualityGateError(msg)


_CallableT = typing.TypeVar("_CallableT", bound=typing.Callable)


def require_quality(min_tier: QualityTier):
    """
    Decorator to enforce data quality requirements.
    Supports both sync and async methods.

    Usage:
        @require_quality(QualityTier.SILVER)
        def _filter_logic(self, lf: typing.Any, context: typing.Any):
            ...

        @require_quality(QualityTier.SILVER)
        async def filter(self, context: typing.Any):
            ...
    """

    def decorator(func: _CallableT) -> _CallableT:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(self, *args: typing.Any, **kwargs: typing.Any):
                processor = _find_processor(self, args, kwargs)
                _check_tier(processor, min_tier, func.__name__)
                return await func(self, *args, **kwargs)

            return typing.cast(_CallableT, async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(self, *args: typing.Any, **kwargs: typing.Any):
            processor = _find_processor(self, args, kwargs)
            _check_tier(processor, min_tier, func.__name__)
            return func(self, *args, **kwargs)

        return typing.cast(_CallableT, sync_wrapper)

    return decorator
