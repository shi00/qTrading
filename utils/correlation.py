"""
S5-3 fix: Correlation ID support for cross-module log tracing.

Provides a ContextVar-based correlation_id that can be injected into
log formatters to trace requests across async task boundaries.
"""

import contextlib
import uuid
from contextvars import ContextVar

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """Generate a new short correlation ID (8 chars for readability)."""
    return uuid.uuid4().hex[:8]


def set_correlation_id(cid: str | None = None) -> str:
    """Set the correlation_id for the current context. Returns the ID set."""
    cid = cid or new_correlation_id()
    correlation_id.set(cid)
    return cid


def get_correlation_id() -> str | None:
    """Get the current correlation_id."""
    return correlation_id.get()


def clear_correlation_id() -> None:
    """Clear the correlation_id for the current context."""
    correlation_id.set(None)


@contextlib.contextmanager
def correlation_scope(cid: str | None = None):
    """Context manager that sets a correlation_id and clears it on exit."""
    old = correlation_id.get()
    set_correlation_id(cid)
    try:
        yield correlation_id.get()
    finally:
        correlation_id.set(old)


class CorrelationFilter:
    """
    Logging filter that adds 'correlation_id' to log records.
    Usage in logging config:
        "filters": {"correlation": {"()": "utils.correlation.CorrelationFilter"}}
        "format": "%(correlation_id)s %(message)s"
    """

    def filter(self, record):
        cid = correlation_id.get()
        record.correlation_id = cid or "-"
        return True
