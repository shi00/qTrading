"""
测试日志装饰器和脱敏工具

验证:
- Token脱敏正确性
- DataFrame安全记录
- 装饰器自动日志
- 性能阈值告警
- 上下文管理器
"""

import asyncio
import logging

import pandas as pd
import pytest
from unittest.mock import patch

from utils.log_decorators import (
    AsyncOperationLogger,
    log_async_operation,
    track_performance,
    log_ui_action,
    UILogger,
)
from utils.sanitizers import DataSanitizer


@pytest.fixture
def log_capture():
    """替代 caplog，兼容 pytest-asyncio event_loop_policy fixture"""
    logger = logging.getLogger()
    handler = logging.Handler()
    records = []

    def emit(record):
        records.append(record)

    handler.emit = emit
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.DEBUG)

    class LogCaptureHelper:
        @property
        def text(self):
            return "\n".join(logging.Formatter().format(r) for r in records)

        @property
        def records(self):
            return list(records)

        def clear(self):
            records.clear()

        def set_level(self, level):
            logger.setLevel(level)

    helper = LogCaptureHelper()
    yield helper

    logger.removeHandler(handler)
    logger.setLevel(original_level)


class TestDataSanitizer:
    """测试数据脱敏工具"""

    def test_sanitize_token_normal(self):
        result = DataSanitizer.sanitize_token("tushare_abc123xyz789")
        assert result == "tus***z789"
        assert "tushare_abc123xyz789" not in result

    def test_sanitize_token_short(self):
        assert DataSanitizer.sanitize_token("short") == "***"
        assert DataSanitizer.sanitize_token("") == "***"
        assert DataSanitizer.sanitize_token(None) == "***"  # type: ignore[untyped]

    def test_sanitize_dataframe_normal(self):
        df = pd.DataFrame({"col1": [1, 2, 3], "col2": [4, 5, 6]})
        result = DataSanitizer.sanitize_dataframe(df)
        assert "shape=(3, 2)" in result
        assert "col1" in result
        assert "col2" in result

    def test_sanitize_dataframe_empty(self):
        df = pd.DataFrame()
        result = DataSanitizer.sanitize_dataframe(df)
        assert "empty" in result.lower()

    def test_sanitize_dataframe_none(self):
        result = DataSanitizer.sanitize_dataframe(None)
        assert result == "None"

    def test_sanitize_error_windows_path(self):
        error = Exception("File not found: D:\\path\\to\\file.py")
        result = DataSanitizer.sanitize_error(error)
        assert "D:\\path\\to\\file.py" not in result
        assert "<PATH>" in result

    def test_sanitize_dict(self):
        data = {
            "token": "secret123456",
            "api_key": "key_abcdef",
            "normal_field": "public_value",
        }
        result = DataSanitizer.sanitize_dict(data)
        assert result["token"] != "secret123456"
        assert "***" in result["token"]
        assert result["normal_field"] == "public_value"


class TestLogAsyncOperation:
    """测试异步操作装饰器"""

    @pytest.mark.asyncio
    async def test_basic_logging(self, log_capture):
        @log_async_operation(operation_name="test_op")
        async def dummy_func():
            await asyncio.sleep(0.01)
            return "success"

        result = await dummy_func()

        assert "[test_op] started" in log_capture.text
        assert "[test_op] completed" in log_capture.text
        assert result == "success"

    @pytest.mark.asyncio
    async def test_log_args(self, log_capture):
        @log_async_operation(operation_name="test_args", log_args=True)
        async def func_with_args(param1, param2=None):
            return f"{param1}-{param2}"

        await func_with_args("value1", param2="value2")

        assert "param2" in log_capture.text or "value2" in log_capture.text

    @pytest.mark.asyncio
    async def test_sanitize_params(self, log_capture):
        @log_async_operation(
            operation_name="test_sanitize",
            log_args=True,
            sanitize_params=["token"],
        )
        async def func_with_token(token):
            return "done"

        await func_with_token(token="secret_token_12345")

        assert "secret_token_12345" not in log_capture.text

    @pytest.mark.asyncio
    async def test_exception_logging(self, log_capture):
        @log_async_operation(operation_name="test_error", log_exceptions=True)
        async def failing_func():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await failing_func()

        assert "[test_error] failed" in log_capture.text
        assert "ValueError" in log_capture.text

    @pytest.mark.asyncio
    async def test_performance_threshold(self, log_capture):
        @log_async_operation(operation_name="slow_op", threshold_ms=10)
        async def slow_func():
            await asyncio.sleep(0.02)

        await slow_func()

        assert "slow_op" in log_capture.text
        assert "SLOW" in log_capture.text or "WARNING" in log_capture.text


class TestTrackPerformance:
    """测试性能追踪装饰器"""

    @pytest.mark.asyncio
    async def test_under_threshold(self, log_capture):
        @track_performance(threshold_ms=1000)
        async def fast_func():
            await asyncio.sleep(0.01)

        log_capture.clear()
        await fast_func()

        warning_records = [r for r in log_capture.records if r.levelno >= logging.WARNING]
        assert len(warning_records) == 0

    @pytest.mark.asyncio
    async def test_over_threshold(self, log_capture):
        @track_performance(threshold_ms=10, operation_name="slow_test")
        async def slow_func():
            await asyncio.sleep(0.02)

        await slow_func()

        assert "slow_test" in log_capture.text
        assert "took" in log_capture.text

    @pytest.mark.slow
    def test_sync_function(self, log_capture):
        @track_performance(threshold_ms=10)
        def sync_slow():
            import time

            time.sleep(0.02)

        sync_slow()

        assert "took" in log_capture.text


class TestAsyncOperationLogger:
    """测试异步操作上下文管理器"""

    @pytest.mark.asyncio
    async def test_basic_context(self, log_capture):
        async with AsyncOperationLogger("test_ctx", {"days": 30}):
            await asyncio.sleep(0.01)

        assert "[test_ctx] started" in log_capture.text
        assert "[test_ctx] completed" in log_capture.text
        assert "days=30" in log_capture.text

    @pytest.mark.asyncio
    async def test_milestone_logging(self, log_capture):
        async with AsyncOperationLogger("test_milestone") as op:
            op.log_milestone("step1", count=100)
            op.log_milestone("step2", done=True)

        assert "step1" in log_capture.text
        assert "count=100" in log_capture.text
        assert "step2" in log_capture.text

    @pytest.mark.asyncio
    async def test_metrics_collection(self, log_capture):
        async with AsyncOperationLogger("test_metrics") as op:
            op.add_metric("processed", 500)
            op.add_metric("failed", 5)
            op.add_metric("success_rate", 0.99)

        assert "metrics:" in log_capture.text
        assert 'processed": 500' in log_capture.text
        assert 'failed": 5' in log_capture.text

    @pytest.mark.asyncio
    async def test_exception_handling(self, log_capture):
        with pytest.raises(RuntimeError):
            async with AsyncOperationLogger("test_error") as op:
                op.add_metric("attempts", 3)
                raise RuntimeError("Test exception")

        assert "[test_error] failed" in log_capture.text
        assert "RuntimeError" in log_capture.text
        assert 'attempts": 3' in log_capture.text


class TestLogUiAction:
    def test_sync_function(self):
        @log_ui_action("TestComponent", "Click", "TestTarget")
        def my_func():
            return 42

        with patch.object(UILogger._logger, "info"):
            result = my_func()
            assert result == 42

    @pytest.mark.asyncio
    async def test_async_function(self):
        @log_ui_action("TestComponent", "Click", "TestTarget")
        async def my_async_func():
            return 99

        with patch.object(UILogger._logger, "info"):
            result = await my_async_func()
            assert result == 99


class TestUILogger:
    def test_log_action_basic(self):
        with patch.object(UILogger._logger, "info") as mock_info:
            UILogger.log_action("Button", "Click")
            mock_info.assert_called_once()
            msg = mock_info.call_args[0][0]
            assert "Button" in msg
            assert "Click" in msg

    def test_log_action_with_target(self):
        with patch.object(UILogger._logger, "info") as mock_info:
            UILogger.log_action("Button", "Click", "Submit")
            msg = mock_info.call_args[0][0]
            assert "target=Submit" in msg

    def test_log_action_with_details(self):
        with patch.object(UILogger._logger, "info") as mock_info:
            UILogger.log_action("Button", "Click", details="extra info")
            msg = mock_info.call_args[0][0]
            assert "extra info" in msg
