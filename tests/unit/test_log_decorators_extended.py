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
    """替代 caplog，兼容 pytest-asyncio pytest_asyncio_loop_factories hook"""
    logger = logging.getLogger()
    handler = logging.Handler()
    records = []

    def emit(record):
        handler.format(record)
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
        result = DataSanitizer.sanitize_token("tushare_abc123xyz789012345678901234")
        assert result == "tus***1234"
        assert "tushare_abc123xyz789012345678901234" not in result

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
    async def test_log_args_positional_and_method_types(self, log_capture):
        """验证 log_args 对独立函数、实例方法、类方法、静态方法的参数记录差异 (self/cls 应被跳过)。"""

        # 1. Standalone function - should log positional arguments
        @log_async_operation(operation_name="test_standalone", log_args=True)
        async def standalone_func(a, b):
            return a + b

        await standalone_func(1, 2)
        assert "args: ('1', '2')" in log_capture.text

        log_capture.clear()

        # 2. Class method / instance method - should skip first argument
        class Dummy:
            @log_async_operation(operation_name="test_method", log_args=True)
            async def instance_method(self, x):
                return x

            @classmethod
            @log_async_operation(operation_name="test_classmethod", log_args=True)
            async def class_method(cls, y):
                return y

            @staticmethod
            @log_async_operation(operation_name="test_staticmethod", log_args=True)
            async def static_method(z):
                return z

        d = Dummy()
        await d.instance_method(10)
        # self is skipped, so args should just be (10,)
        assert "args: ('10',)" in log_capture.text

        log_capture.clear()

        await Dummy.class_method(20)
        # cls is skipped, so args should just be (20,)
        assert "args: ('20',)" in log_capture.text

        log_capture.clear()

        await Dummy.static_method(30)
        # staticmethod has no self/cls, z is first argument, should be (30,)
        assert "args: ('30',)" in log_capture.text

    @pytest.mark.asyncio
    async def test_sanitize_params(self, log_capture):
        """sanitize_params 指定的参数在日志中应被脱敏，明文不得泄露。"""

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
        """被装饰函数抛异常时应记录 failed 日志并包含异常类型，随后重新抛出。"""

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
        from tests.virtual_clock import VirtualClock

        clock = VirtualClock()
        with patch("time.perf_counter", clock.now), patch("time.sleep", clock.sleep):

            @track_performance(threshold_ms=10)
            def sync_slow():
                import time

                time.sleep(0.02)

            sync_slow()

        assert "took" in log_capture.text

    @pytest.mark.asyncio
    async def test_track_performance_async_exception_logs_elapsed(self, log_capture):
        """async 异常路径应记录 ERROR 级别日志，消息含耗时（after 关键字）与异常类型，并重新抛出。"""

        @track_performance(threshold_ms=1000, operation_name="async_fail")
        async def failing_async():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await failing_async()

        error_records = [r for r in log_capture.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        text = "\n".join(logging.Formatter().format(r) for r in error_records)
        assert "[async_fail]" in text
        assert "after" in text
        assert "ValueError" in text

    def test_track_performance_sync_exception_logs_elapsed(self, log_capture):
        """sync 异常路径应记录 ERROR 级别日志，消息含耗时（after 关键字）与异常类型，并重新抛出。"""

        @track_performance(threshold_ms=1000, operation_name="sync_fail")
        def failing_sync():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            failing_sync()

        error_records = [r for r in log_capture.records if r.levelno >= logging.ERROR]
        assert len(error_records) >= 1
        text = "\n".join(logging.Formatter().format(r) for r in error_records)
        assert "[sync_fail]" in text
        assert "after" in text
        assert "ValueError" in text

    @pytest.mark.asyncio
    async def test_track_performance_slow_operation_logs_warning(self, log_capture):
        """慢操作应记录 WARNING 级别日志，消息含耗时（took 关键字）。"""

        @track_performance(threshold_ms=10, operation_name="slow_warn")
        async def slow_func():
            await asyncio.sleep(0.02)

        await slow_func()

        warning_records = [r for r in log_capture.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1
        text = "\n".join(logging.Formatter().format(r) for r in warning_records)
        assert "[slow_warn]" in text
        assert "took" in text


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
        """上下文内抛异常时应记录 failed 日志并附带已收集的 metrics，随后重新抛出。"""
        with pytest.raises(RuntimeError):
            async with AsyncOperationLogger("test_error") as op:
                op.add_metric("attempts", 3)
                raise RuntimeError("Test exception")

        assert "[test_error] failed" in log_capture.text
        assert "RuntimeError" in log_capture.text
        assert 'attempts": 3' in log_capture.text


class TestLogUiAction:
    """测试 log_ui_action 装饰器对同步/异步 UI 动作的日志记录。"""

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
    """测试 UILogger.log_action 的消息格式化 (组件/动作/目标/详情)。"""

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


class TestObservabilityFixes:
    """验证 OBS-001 和 OBS-003 的修复"""

    @pytest.mark.asyncio
    async def test_decorator_logger_name_and_stacklevel(self, log_capture):
        """OBS-001: 装饰器日志的 logger name 与 filename 应指向调用方测试模块而非 log_decorators 模块。"""

        @log_async_operation(operation_name="test_obs001")
        async def dummy_operation():
            return "ok"

        await dummy_operation()

        # 验证日志中的 logger 名称应该为当前测试模块的名称 (tests.unit.test_log_decorators_extended)
        # 并且文件名应该为当前测试文件 (test_log_decorators_extended.py)，而不是 log_decorators.py
        records = [r for r in log_capture.records if "test_obs001" in r.message]
        assert len(records) >= 2  # started and completed

        for record in records:
            assert record.name == __name__
            assert record.filename == "test_log_decorators_extended.py"

    @pytest.mark.asyncio
    async def test_recoverable_exception_severity(self, log_capture):
        """OBS-003: 可恢复异常 (如 TimeoutError) 应记为 WARNING 且不附带 traceback。"""

        # TimeoutError 被 classify_severity 归类为 recoverable (可恢复)
        @log_async_operation(operation_name="test_obs003")
        async def failing_operation():
            raise TimeoutError("Connection timed out")

        with pytest.raises(TimeoutError):
            await failing_operation()

        records = [r for r in log_capture.records if "test_obs003" in r.message]
        error_records = [r for r in records if r.levelno >= logging.WARNING]

        assert len(error_records) == 1
        record = error_records[0]

        # 应记录为 WARNING 级，而不是 ERROR 或 CRITICAL
        assert record.levelno == logging.WARNING
        # 不应附带 traceback (exc_info 为空或 None)
        assert record.exc_info is None

    @pytest.mark.asyncio
    async def test_system_exception_severity(self, log_capture):
        """系统级异常 (如 MemoryError) 应记为 CRITICAL 并附带 exc_info traceback。"""

        # MemoryError 属于 system-level，应记录为 CRITICAL 并附带 exc_info
        @log_async_operation(operation_name="test_system_err")
        async def system_error_op():
            raise MemoryError("Out of memory")

        with pytest.raises(MemoryError):
            await system_error_op()

        records = [r for r in log_capture.records if "test_system_err" in r.message]
        critical_records = [r for r in records if r.levelno == logging.CRITICAL]

        assert len(critical_records) == 1
        record = critical_records[0]
        assert record.exc_info is not None

    @pytest.mark.asyncio
    async def test_async_operation_logger_custom_name(self, log_capture):
        """AsyncOperationLogger 应支持自定义 logger_name，所有日志记录使用该 name。"""
        async with AsyncOperationLogger("test_custom_logger", logger_name="custom.logger") as op:
            op.log_milestone("milestone_1")

        records = [r for r in log_capture.records if "test_custom_logger" in r.message]
        assert len(records) >= 2
        for r in records:
            assert r.name == "custom.logger"
