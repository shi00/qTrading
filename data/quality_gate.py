import functools
import inspect
import logging
from enum import IntEnum

logger = logging.getLogger(__name__)


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

    @staticmethod
    def get_current_tier():
        """
        Get global quality tier from DataProcessor singleton or context.

        NOTE: This is a placeholder — not used at runtime.
        The @require_quality decorator uses _find_processor() to locate the
        DataProcessor instance and reads _quality_tier directly from it.
        """
        return 0


def _find_processor(instance, args, kwargs):
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


def _check_tier(processor, min_tier, func_name):
    """Shared logic to verify quality tier."""
    if processor is None:
        logger.warning(
            f"[QualityGate] Bypassed for {func_name}: DataProcessor not found in context. (Could be test env or context missing)",
        )
        return  # Skip check if no processor found
    current_tier = getattr(processor, "_quality_tier", None)
    if current_tier is None:
        current_tier = 0  # Treat uninitialized as CRITICAL
    if current_tier < min_tier:
        from ui.i18n import I18n

        msg = I18n.get(
            "quality_err_too_low",
            required=min_tier.name,
            current=QualityTier(current_tier).name,
        )
        if msg == "quality_err_too_low":  # Fallback if I18n not initialized
            msg = f"Data Quality too low for {func_name}. Required: {min_tier.name}, Current: {QualityTier(current_tier).name}"
        logger.warning(f"[QualityGate] {msg}")
        raise QualityGateError(msg)


def require_quality(min_tier: QualityTier):
    """
    Decorator to enforce data quality requirements.
    Supports both sync and async methods.

    Usage:
        @require_quality(QualityTier.SILVER)
        def _filter_logic(self, lf, context):
            ...

        @require_quality(QualityTier.SILVER)
        async def filter(self, context):
            ...
    """

    def decorator(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                processor = _find_processor(self, args, kwargs)
                _check_tier(processor, min_tier, func.__name__)
                return await func(self, *args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(self, *args, **kwargs):
            processor = _find_processor(self, args, kwargs)
            _check_tier(processor, min_tier, func.__name__)
            return func(self, *args, **kwargs)

        return sync_wrapper

    return decorator
