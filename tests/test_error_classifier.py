"""
Tests for error_classifier severity classification.

S5-4: Distinguish recoverable business errors from system-level errors.
"""

import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestClassifySeverity:
    """S5-4: classify_severity distinguishes system vs recoverable vs operational"""

    def test_memory_error_is_system(self):
        """MemoryError should be classified as system-level"""
        from utils.error_classifier import classify_severity

        result = classify_severity(MemoryError("out of memory"))
        assert result == "system"

    def test_recursion_error_is_system(self):
        """RecursionError should be classified as system-level"""
        from utils.error_classifier import classify_severity

        result = classify_severity(RecursionError("max depth"))
        assert result == "system"

    def test_permission_error_is_system(self):
        """PermissionError should be classified as system-level"""
        from utils.error_classifier import classify_severity

        result = classify_severity(PermissionError("access denied"))
        assert result == "system"

    def test_timeout_is_recoverable(self):
        """Timeout errors should be classified as recoverable"""
        from utils.error_classifier import classify_severity

        result = classify_severity(TimeoutError("request timed out"), context="llm")
        assert result == "recoverable"

    def test_connection_error_is_recoverable(self):
        """Connection errors should be classified as recoverable"""
        from utils.error_classifier import classify_severity

        result = classify_severity(ConnectionError("connection refused"), context="db")
        assert result == "recoverable"

    def test_value_error_is_operational(self):
        """ValueError (bad input) should be classified as operational"""
        from utils.error_classifier import classify_severity

        result = classify_severity(ValueError("invalid format"), context="db")
        assert result == "operational"

    def test_rate_limit_is_recoverable(self):
        """Rate limit errors should be classified as recoverable"""
        from utils.error_classifier import classify_severity

        result = classify_severity(Exception("429 rate limit exceeded"), context="llm")
        assert result == "recoverable"

    def test_generic_exception_is_operational(self):
        """Unknown exceptions should default to operational"""
        from utils.error_classifier import classify_severity

        result = classify_severity(Exception("something went wrong"), context="general")
        assert result == "operational"

    def test_value_error_with_space_word_is_not_system(self):
        """H-1: 'space' substring on non-OSError must NOT be classified as system."""
        from utils.error_classifier import classify_severity

        assert classify_severity(ValueError("namespace conflict")) == "operational"
        assert classify_severity(RuntimeError("workspace empty")) == "operational"
        assert classify_severity(Exception("replace foo with bar")) == "operational"

    def test_oserror_disk_or_space_still_system(self):
        """Regression: real OSError with disk/space remains system."""
        from utils.error_classifier import classify_severity

        assert classify_severity(OSError("No space left on device")) == "system"
        assert classify_severity(OSError("disk full")) == "system"


class TestClassifySeverityIntegration:
    """Verify classify_severity is properly integrated in TaskManager error handling."""

    def test_task_manager_imports_classify_severity(self):
        """TaskManager should import classify_severity from utils.error_classifier."""
        import inspect
        from services.task_manager import TaskManager

        source = inspect.getsource(TaskManager)
        assert "classify_severity" in source, "TaskManager should use classify_severity for error classification"

    def test_task_manager_system_error_uses_critical_log(self):
        """TaskManager should log system-level errors with CRITICAL level."""
        import inspect
        from services.task_manager import TaskManager

        source = inspect.getsource(TaskManager)
        assert "logger.critical" in source, "TaskManager should use logger.critical for system-level errors"
        assert 'severity == "system"' in source or "severity=='system'" in source, (
            "TaskManager should check severity == 'system'"
        )

    def test_task_manager_includes_severity_in_error_log(self):
        """TaskManager should include severity level in error log messages."""
        import inspect
        from services.task_manager import TaskManager

        source = inspect.getsource(TaskManager)
        assert "severity" in source, "TaskManager should reference severity in error handling"
