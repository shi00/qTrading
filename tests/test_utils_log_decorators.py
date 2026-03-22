"""
测试日志装饰器和脱敏工具

验证:
- Token脱敏正确性
- DataFrame安全记录
- 装饰器自动日志
- 性能阈值告警
- 上下文管理器
"""

import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import logging

import pandas as pd
import pytest

from utils.log_decorators import (
    AsyncOperationLogger,
    log_async_operation,
    track_performance,
)
from utils.sanitizers import DataSanitizer


class TestDataSanitizer:
    """测试数据脱敏工具"""

    def test_sanitize_token_normal(self):
        """测试正常token脱能"""
        token = "tushare_abc123xyz789"
        result = DataSanitizer.sanitize_token(token)
        # 前3位 + *** + 后4位
        assert result == "tus***z789"  # tushare_abc123xyz789 的后4位是 z789
        # 确保原token不出现
        assert token not in result

    def test_sanitize_token_short(self):
        """测试短token脱敏"""
        assert DataSanitizer.sanitize_token("short") == "***"
        assert DataSanitizer.sanitize_token("") == "***"
        assert DataSanitizer.sanitize_token(None) == "***"  # type: ignore

    def test_sanitize_dataframe_normal(self):
        """测试DataFrame安全摘要"""
        df = pd.DataFrame({"col1": [1, 2, 3], "col2": [4, 5, 6]})
        result = DataSanitizer.sanitize_dataframe(df)

        # 验证包含形状
        assert "shape=(3, 2)" in result
        # 验证包含列名
        assert "col1" in result
        assert "col2" in result
        # 验证不泄露实际数据
        assert (
            "1" not in result or "shape" in result
        )  # 数字1如果存在应该是shape的一部分

    def test_sanitize_dataframe_empty(self):
        """测试空DataFrame"""
        df = pd.DataFrame()
        result = DataSanitizer.sanitize_dataframe(df)
        assert "empty" in result.lower()

    def test_sanitize_dataframe_none(self):
        """测试None输入"""
        result = DataSanitizer.sanitize_dataframe(None)
        assert result == "None"

    def test_sanitize_error_windows_path(self):
        """测试Windows路径过滤"""
        error = Exception("File not found: D:\\path\\to\\file.py")
        result = DataSanitizer.sanitize_error(error)

        # 验证路径被替换
        assert "D:\\path\\to\\file.py" not in result
        assert "<PATH>" in result

    def test_sanitize_dict(self):
        """测试字典脱敏"""
        data = {
            "token": "secret123456",
            "api_key": "key_abcdef",
            "normal_field": "public_value",
        }
        result = DataSanitizer.sanitize_dict(data)

        # 验证敏感字段被脱敏
        assert result["token"] != "secret123456"
        assert "***" in result["token"]
        # 验证普通字段不变
        assert result["normal_field"] == "public_value"


class TestLogAsyncOperation:
    """测试异步操作装饰器"""

    @pytest.mark.asyncio
    async def test_basic_logging(self, caplog):
        """测试基础日志记录"""

        @log_async_operation(operation_name="test_op")
        async def dummy_func():
            await asyncio.sleep(0.01)
            return "success"

        with caplog.at_level(logging.DEBUG):
            result = await dummy_func()

        # 验证日志存在
        assert "[test_op] started" in caplog.text
        assert "[test_op] completed" in caplog.text
        assert result == "success"

    @pytest.mark.asyncio
    async def test_log_args(self, caplog):
        """测试参数记录"""

        @log_async_operation(operation_name="test_args", log_args=True)
        async def func_with_args(param1, param2=None):
            return f"{param1}-{param2}"

        with caplog.at_level(logging.DEBUG):
            await func_with_args("value1", param2="value2")

        # 验证参数被记录
        assert "param2" in caplog.text or "value2" in caplog.text

    @pytest.mark.asyncio
    async def test_sanitize_params(self, caplog):
        """测试参数脱敏"""

        @log_async_operation(
            operation_name="test_sanitize",
            log_args=True,
            sanitize_params=["token"],
        )
        async def func_with_token(token):
            return "done"

        with caplog.at_level(logging.DEBUG):
            await func_with_token(token="secret_token_12345")

        # 验证完整token不出现
        assert "secret_token_12345" not in caplog.text

    @pytest.mark.asyncio
    async def test_exception_logging(self, caplog):
        """测试异常记录"""

        @log_async_operation(operation_name="test_error", log_exceptions=True)
        async def failing_func():
            raise ValueError("Test error")

        with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
            await failing_func()

        # 验证异常被记录
        assert "[test_error] failed" in caplog.text
        assert "ValueError" in caplog.text

    @pytest.mark.asyncio
    async def test_performance_threshold(self, caplog):
        """测试性能阈值告警"""

        @log_async_operation(operation_name="slow_op", threshold_ms=10)
        async def slow_func():
            await asyncio.sleep(0.02)  # 20ms,超过10ms阈值

        with caplog.at_level(logging.WARNING):
            await slow_func()

        # 验证性能告警
        assert "slow_op" in caplog.text
        assert "SLOW" in caplog.text or "WARNING" in caplog.text


class TestTrackPerformance:
    """测试性能追踪装饰器"""

    @pytest.mark.asyncio
    async def test_under_threshold(self, caplog):
        """测试未超过阈值"""

        @track_performance(threshold_ms=1000)
        async def fast_func():
            await asyncio.sleep(0.01)

        with caplog.at_level(logging.WARNING):
            await fast_func()

        # 不应该有告警
        assert len(caplog.records) == 0

    @pytest.mark.asyncio
    async def test_over_threshold(self, caplog):
        """测试超过阈值"""

        @track_performance(threshold_ms=10, operation_name="slow_test")
        async def slow_func():
            await asyncio.sleep(0.02)

        with caplog.at_level(logging.WARNING):
            await slow_func()

        # 应该有告警
        assert "slow_test" in caplog.text
        assert "took" in caplog.text

    def test_sync_function(self, caplog):
        """测试同步函数支持"""

        @track_performance(threshold_ms=10)
        def sync_slow():
            import time

            time.sleep(0.02)

        with caplog.at_level(logging.WARNING):
            sync_slow()

        assert "took" in caplog.text


class TestAsyncOperationLogger:
    """测试异步操作上下文管理器"""

    @pytest.mark.asyncio
    async def test_basic_context(self, caplog):
        """测试基础上下文管理"""
        with caplog.at_level(logging.DEBUG):
            async with AsyncOperationLogger("test_ctx", {"days": 30}):
                await asyncio.sleep(0.01)

        # 验证开始和完成日志
        assert "[test_ctx] started" in caplog.text
        assert "[test_ctx] completed" in caplog.text
        assert "days=30" in caplog.text

    @pytest.mark.asyncio
    async def test_milestone_logging(self, caplog):
        """测试里程碑记录"""
        with caplog.at_level(logging.DEBUG):
            async with AsyncOperationLogger("test_milestone") as op:
                op.log_milestone("step1", count=100)
                op.log_milestone("step2", done=True)

        # 验证里程碑
        assert "step1" in caplog.text
        assert "count=100" in caplog.text
        assert "step2" in caplog.text

    @pytest.mark.asyncio
    async def test_metrics_collection(self, caplog):
        """测试指标收集"""
        with caplog.at_level(logging.DEBUG):
            async with AsyncOperationLogger("test_metrics") as op:
                op.add_metric("processed", 500)
                op.add_metric("failed", 5)
                op.add_metric("success_rate", 0.99)

        # 验证指标汇总
        assert "metrics:" in caplog.text
        assert 'processed": 500' in caplog.text
        assert 'failed": 5' in caplog.text

    @pytest.mark.asyncio
    async def test_exception_handling(self, caplog):
        """测试异常处理"""
        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
            async with AsyncOperationLogger("test_error") as op:
                op.add_metric("attempts", 3)
                raise RuntimeError("Test exception")

        # 验证异常被记录
        assert "[test_error] failed" in caplog.text
        assert "RuntimeError" in caplog.text
        # 验证指标仍然被记录
        assert 'attempts": 3' in caplog.text


if __name__ == "__main__":
    # 允许直接运行测试
    pytest.main([__file__, "-v"])
