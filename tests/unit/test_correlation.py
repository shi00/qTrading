"""
Tests for correlation module.

S5-3: Correlation ID support for cross-module log tracing.
"""

import logging
import pytest


pytestmark = pytest.mark.unit


class TestCorrelationId:
    """S5-3: Correlation ID ContextVar"""

    def test_set_and_get_correlation_id(self):
        """set_correlation_id and get_correlation_id should work"""
        from utils.correlation import (
            set_correlation_id,
            get_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        assert get_correlation_id() is None

        cid = set_correlation_id("abc123")
        assert cid == "abc123"
        assert get_correlation_id() == "abc123"

        clear_correlation_id()
        assert get_correlation_id() is None

    def test_new_correlation_id_format(self):
        """new_correlation_id should return 8-char hex string"""
        from utils.correlation import new_correlation_id

        cid = new_correlation_id()
        assert len(cid) == 8
        assert all(c in "0123456789abcdef" for c in cid)

    def test_set_generates_id_if_none(self):
        """set_correlation_id(None) should auto-generate an ID"""
        from utils.correlation import set_correlation_id, clear_correlation_id

        clear_correlation_id()
        cid = set_correlation_id(None)
        assert cid is not None
        assert len(cid) == 8

        clear_correlation_id()

    def test_correlation_scope(self):
        """correlation_scope should set and restore correlation_id"""
        from utils.correlation import (
            correlation_scope,
            get_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()

        with correlation_scope("test123") as cid:
            assert cid == "test123"
            assert get_correlation_id() == "test123"

        assert get_correlation_id() is None

    def test_correlation_scope_restores_previous(self):
        """correlation_scope should restore previous correlation_id on exit"""
        from utils.correlation import (
            correlation_scope,
            get_correlation_id,
            set_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        set_correlation_id("outer")

        with correlation_scope("inner"):
            assert get_correlation_id() == "inner"

        assert get_correlation_id() == "outer"

        clear_correlation_id()

    def test_correlation_filter_adds_to_record(self):
        """CorrelationFilter should add correlation_id to log records"""
        from utils.correlation import (
            CorrelationFilter,
            set_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        filt = CorrelationFilter()

        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        result = filt.filter(record)
        assert result is True
        assert record.correlation_id == "-"

        set_correlation_id("abc")
        record2 = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record2)
        assert record2.correlation_id == "abc"

        clear_correlation_id()

    def test_ensure_correlation_id_generates_when_none(self):
        """ensure_correlation_id should generate a new ID when none exists"""
        from utils.correlation import (
            ensure_correlation_id,
            get_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        cid = ensure_correlation_id()
        assert cid is not None
        assert len(cid) == 8
        assert get_correlation_id() == cid

        clear_correlation_id()

    def test_ensure_correlation_id_returns_existing(self):
        """ensure_correlation_id should return existing ID when set"""
        from utils.correlation import (
            ensure_correlation_id,
            set_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        set_correlation_id("existing")

        cid = ensure_correlation_id()
        assert cid == "existing"

        clear_correlation_id()


class TestLoggerCorrelationIntegration:
    """Verify logger.py integrates CorrelationFilter into all handlers."""

    def test_logger_format_includes_correlation_id(self):
        """Logger format string should contain %(correlation_id)s placeholder."""
        from utils.logger import setup_logging

        test_logger = logging.getLogger("astock_screener")
        original_handlers = list(test_logger.handlers)
        original_level = test_logger.level

        try:
            setup_logging()

            for handler in test_logger.handlers:
                fmt_str = handler.formatter._fmt if handler.formatter and handler.formatter._fmt is not None else ""
                assert "%(correlation_id)s" in fmt_str, f"Handler {handler} missing correlation_id in format: {fmt_str}"

                has_correlation_filter = any(
                    isinstance(f, type) and f.__module__ == "utils.correlation" for f in handler.filters
                ) or any(f.__class__.__name__ == "CorrelationFilter" for f in handler.filters)
                assert has_correlation_filter, f"Handler {handler} missing CorrelationFilter"
        finally:
            test_logger.handlers = original_handlers
            test_logger.level = original_level

    def test_correlation_filter_on_new_handler(self):
        """Adding a new handler with CorrelationFilter should inject correlation_id."""
        from utils.correlation import (
            CorrelationFilter,
            set_correlation_id,
            clear_correlation_id,
        )

        clear_correlation_id()
        filt = CorrelationFilter()

        handler = logging.StreamHandler()
        handler.addFilter(filt)
        handler.setFormatter(logging.Formatter("[%(correlation_id)s] %(message)s"))

        test_logger = logging.getLogger("test_correlation_handler")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        try:
            set_correlation_id("test-logger-01")

            import io

            buffer = io.StringIO()
            handler.stream = buffer

            test_logger.info("test message")
            output = buffer.getvalue()
            assert "[test-logger-01]" in output
        finally:
            test_logger.removeHandler(handler)
            clear_correlation_id()
