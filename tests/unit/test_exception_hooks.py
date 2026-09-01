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
    _format_task_stack,
    _sys_excepthook,
    _threading_excepthook,
    install_asyncio_handler_for_loop,
    install_global_exception_hooks,
    restore_global_exception_hooks,
)

pytestmark = pytest.mark.unit


class TestSysExcepthook:
    """测试 sys.excepthook 实现"""

    def test_value_error_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """普通异常应记录 ERROR 级别 (E6: 不再用 CRITICAL 冲刷信号)"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(ValueError, ValueError("test error"), None)

        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert all(r.levelno != logging.CRITICAL for r in caplog.records)
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

    def test_recoverable_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """可恢复错误（网络/超时）应记录 WARNING 而非 CRITICAL（避免污染严重度信号）"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(TimeoutError, TimeoutError("network failure"), None)

        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert all(r.levelno != logging.CRITICAL for r in caplog.records)

    def test_system_error_logs_critical(self, caplog: pytest.LogCaptureFixture) -> None:
        """system 级错误应记录 CRITICAL"""
        with caplog.at_level(logging.DEBUG):
            _sys_excepthook(OSError, OSError("No space left on device"), None)

        assert any(r.levelno == logging.CRITICAL for r in caplog.records)

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

    def test_connection_reset_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """ConnectionResetError (WinError 10054) 应记 WARNING 而非 CRITICAL"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()
        context = {
            "exception": ConnectionResetError("[WinError 10054] test"),
            "message": "connection reset",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any(
                "Network connection reset/aborted" in r.message and "ConnectionResetError" in r.message
                for r in caplog.records
            )
            assert all(r.levelno != logging.CRITICAL for r in caplog.records)
        finally:
            loop.close()

    def test_connection_aborted_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """ConnectionAbortedError 应记 WARNING 而非 CRITICAL"""
        loop = asyncio.new_event_loop()
        context = {
            "exception": ConnectionAbortedError("[WinError 1236] test"),
            "message": "connection aborted",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any(
                "Network connection reset/aborted" in r.message and "ConnectionAbortedError" in r.message
                for r in caplog.records
            )
            assert all(r.levelno != logging.CRITICAL for r in caplog.records)
        finally:
            loop.close()

    def test_broken_pipe_error_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """BrokenPipeError 应记 WARNING 而非 CRITICAL"""
        # NOTE(lazy): Uses asyncio.new_event_loop() to create a loop for testing _asyncio_exception_handler. ceiling: Python 3.16 removes asyncio.new_event_loop. upgrade: When Python 3.16 is adopted, refactor to use asyncio.Runner or a loop_factory-based approach.
        loop = asyncio.new_event_loop()
        context = {
            "exception": BrokenPipeError("test"),
            "message": "broken pipe",
        }
        try:
            with caplog.at_level(logging.DEBUG):
                _asyncio_exception_handler(loop, context)

            assert any(
                "Network connection reset/aborted" in r.message and "BrokenPipeError" in r.message
                for r in caplog.records
            )
            assert all(r.levelno != logging.CRITICAL for r in caplog.records)
        finally:
            loop.close()


class TestFormatTaskStack:
    """测试 _format_task_stack 取证辅助函数"""

    def test_no_task_returns_empty(self) -> None:
        """无 task/future 时返回空串"""
        assert _format_task_stack({}) == ""

    def test_task_exception_extracted(self) -> None:
        """应从已完成 task 提取真实异常与栈帧"""

        async def _boom() -> None:
            await asyncio.sleep(0)
            raise ValueError("boom")

        async def _run() -> None:
            task = asyncio.create_task(_boom())
            try:
                await task
            except ValueError:
                pass
            hint = _format_task_stack({"task": task})
            assert "Task exception: ValueError: boom" in hint
            assert "_boom" in hint

        asyncio.run(_run())

    def test_task_stack_frames_included(self) -> None:
        """应包含栈帧 File/line 信息"""

        async def _inner() -> None:
            await asyncio.Event().wait()  # 挂起, 保留栈

        async def _run() -> None:
            task = asyncio.create_task(_inner())
            await asyncio.sleep(0)
            hint = _format_task_stack({"task": task})
            assert "File" in hint and "_inner" in hint
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # 测试清理, 吞掉取消异常

        asyncio.run(_run())

    def test_cancelled_error_from_exception_does_not_escape(self) -> None:
        """obj.exception() 抛 CancelledError 时不应逃逸钩子 (防护竞态窗口).

        真实竞态: cancelled() 预判返回 False 后、exception() 调用前任务被取消,
        exception() 抛 CancelledError (BaseException, 不被 except Exception 捕获)。
        本用例用假对象直接触发该分支, 锁定新加的
        `except asyncio.CancelledError` 兜底。"""

        class _FakeTask:
            def cancelled(self) -> bool:
                return False  # 模拟竞态窗口内预判为未取消

            def exception(self):
                raise asyncio.CancelledError()

            def get_stack(self):
                return []

        hint = _format_task_stack({"task": _FakeTask()})
        assert hint == ""  # 无异常行、无栈帧, 且不抛异常


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
