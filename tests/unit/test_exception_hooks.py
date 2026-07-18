"""
tests/unit/test_exception_hooks.py

单元测试：全局异常钩子
"""

import asyncio
import logging
import sys
import threading

import pytest

from utils.exception_hooks import (
    _asyncio_exception_handler,
    _sys_excepthook,
    _threading_excepthook,
    install_asyncio_handler_for_loop,
    install_global_exception_hooks,
    restore_global_exception_hooks,
)

pytestmark = pytest.mark.unit


class TestSysExcepthook:
    """测试 sys.excepthook 实现"""

    def test_value_error_logs_critical(self, caplog: pytest.LogCaptureFixture) -> None:
        """普通异常应记录 CRITICAL 级别"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(ValueError, ValueError("test error"), None)

        assert any(r.levelno == logging.CRITICAL for r in caplog.records)
        assert any("ValueError" in r.message for r in caplog.records)

    def test_keyboard_interrupt_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """KeyboardInterrupt 应记录 INFO 级别"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)

        assert any("KeyboardInterrupt" in r.message for r in caplog.records)
        assert all(r.levelno != logging.CRITICAL for r in caplog.records)

    def test_cancelled_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """CancelledError 应记录 WARNING 级别"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(asyncio.CancelledError, asyncio.CancelledError(), None)

        assert any("CancelledError" in r.message for r in caplog.records)
        assert all(r.levelno != logging.CRITICAL for r in caplog.records)

    def test_system_exit_zero_no_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """SystemExit(0) 应忽略，无日志"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(SystemExit, SystemExit(0), None)

        assert len(caplog.records) == 0

    def test_system_exit_nonzero_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """SystemExit(非零) 应记录 WARNING"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(SystemExit, SystemExit(1), None)

        assert any("SystemExit" in r.message for r in caplog.records)


class TestThreadingExcepthook:
    """测试 threading.excepthook 实现"""

    def test_value_error_logs_critical(self, caplog: pytest.LogCaptureFixture) -> None:
        """普通异常应记录 CRITICAL 级别"""
        args = threading.ExceptHookArgs((ValueError, ValueError("thread error"), None, threading.current_thread()))
        with caplog.at_level(logging.DEBUG):
            _threading_excepthook(args)

        assert any(r.levelno == logging.CRITICAL for r in caplog.records)

    def test_cancelled_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """CancelledError 应记录 WARNING 级别"""
        args = threading.ExceptHookArgs(
            (
                asyncio.CancelledError,
                asyncio.CancelledError(),
                None,
                threading.current_thread(),
            )
        )
        with caplog.at_level(logging.DEBUG):
            _threading_excepthook(args)

        assert any("CancelledError" in r.message for r in caplog.records)
        assert all(r.levelno != logging.CRITICAL for r in caplog.records)

    def test_logs_thread_name(self, caplog: pytest.LogCaptureFixture) -> None:
        """应包含线程名"""
        thread = threading.current_thread()
        args = threading.ExceptHookArgs((ValueError, ValueError("error"), None, thread))
        with caplog.at_level(logging.DEBUG):
            _threading_excepthook(args)

        assert any(thread.name in r.message for r in caplog.records)


class TestAsyncioExceptionHandler:
    """测试 asyncio 异常处理器"""

    def test_exception_logs_critical(self, caplog: pytest.LogCaptureFixture) -> None:
        """有异常时应记录 CRITICAL"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()
        context = {
            "exception": ValueError("async error"),
            "message": "task failed",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any("ValueError" in r.message for r in caplog.records)
        finally:
            loop.close()

    def test_cancelled_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """CancelledError 应记录 WARNING"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()
        context = {
            "exception": asyncio.CancelledError(),
            "message": "task cancelled",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any("CancelledError" in r.message for r in caplog.records)
            assert all(r.levelno != logging.CRITICAL for r in caplog.records)
        finally:
            loop.close()

    def test_no_exception_logs_message(self, caplog: pytest.LogCaptureFixture) -> None:
        """无异常时应记录 message"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()
        context = {
            "message": "asyncio error without exception",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any("asyncio error without exception" in r.message for r in caplog.records)
        finally:
            loop.close()

    def test_keyboard_interrupt_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """KeyboardInterrupt 应记录 INFO"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()
        context = {
            "exception": KeyboardInterrupt(),
            "message": "keyboard interrupt",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any("KeyboardInterrupt" in r.message for r in caplog.records)
            assert all(r.levelno != logging.CRITICAL for r in caplog.records)
        finally:
            loop.close()


class TestSanitization:
    """测试脱敏功能"""

    def test_api_key_sanitized(self, caplog: pytest.LogCaptureFixture) -> None:
        """API key 应被脱敏"""
        error = ValueError("api_key=secret12345 failed")
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(ValueError, error, None)

        messages = [r.message for r in caplog.records]
        combined = " ".join(messages)
        assert "secret12345" not in combined

    def test_url_credentials_sanitized(self, caplog: pytest.LogCaptureFixture) -> None:
        """URL 凭证应被脱敏"""
        error = ValueError("Connection to postgresql://user:password123@localhost failed")
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(ValueError, error, None)

        messages = [r.message for r in caplog.records]
        combined = " ".join(messages)
        assert "password123" not in combined


class TestInstallAndRestore:
    """测试安装和恢复功能"""

    def test_install_sets_hooks(self) -> None:
        """安装应设置钩子"""
        original_sys = sys.excepthook
        original_threading = threading.excepthook

        try:
            install_global_exception_hooks()

            assert sys.excepthook is not original_sys
            assert threading.excepthook is not original_threading
        finally:
            restore_global_exception_hooks()

    def test_restore_reverts_hooks(self) -> None:
        """恢复应还原原始钩子"""
        original_sys = sys.excepthook
        original_threading = threading.excepthook

        install_global_exception_hooks()
        restore_global_exception_hooks()

        assert sys.excepthook is original_sys
        assert threading.excepthook is original_threading

    @pytest.mark.asyncio
    async def test_install_asyncio_handler(self) -> None:
        """应为指定 loop 安装 handler"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()

        try:
            install_asyncio_handler_for_loop(loop)

            assert loop.get_exception_handler() is not None
        finally:
            loop.set_exception_handler(None)
            loop.close()
