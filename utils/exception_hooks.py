"""
全局异常钩子 - Global Exception Hooks

提供三个全局异常钩子，确保所有未捕获异常都被记录和脱敏：
- sys.excepthook: 捕获主线程未处理异常
- threading.excepthook: 捕获线程池 worker 未捕获异常
- asyncio loop exception_handler: 捕获事件循环未处理异常

遵循 R2 红线：asyncio.CancelledError 必须传播，不在此处吞没。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Callable
from types import TracebackType

from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

_original_sys_excepthook: Callable | None = None
_original_threading_excepthook: Callable | None = None


def _sys_excepthook(exctype: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    """
    sys.excepthook 替代实现

    捕获主线程未处理异常，脱敏后记录 CRITICAL 日志。
    特殊处理：
    - KeyboardInterrupt: INFO 日志，正常退出
    - SystemExit(0): 忽略
    - CancelledError: WARNING，指示 shutdown bug
    """
    try:
        if issubclass(exctype, KeyboardInterrupt):
            logger.info("[SysExcepthook] KeyboardInterrupt received, exiting gracefully.")
            return

        if issubclass(exctype, SystemExit):
            if isinstance(value, SystemExit) and value.code == 0:
                return
            exit_code = getattr(value, "code", None)
            logger.warning("[SysExcepthook] SystemExit with code %s", exit_code)
            return

        if issubclass(exctype, asyncio.CancelledError):
            logger.warning(
                "[SysExcepthook] CancelledError leaked to sys.excepthook - this indicates a bug in shutdown logic."
            )
            return

        sanitized_msg = DataSanitizer.sanitize_error(value)  # type: ignore[arg-type]
        logger.critical(
            "[SysExcepthook] Unhandled exception in main thread: %s: %s",
            exctype.__name__,
            sanitized_msg,
            exc_info=True,
        )
    # NOTE(lazy): 主线程钩子内兜底避免钩子自身崩溃导致系统失控. ceiling: 钩子内逻辑不应抛异常. upgrade: 钩子内部分类异常处理或移除兜底.
    except Exception as e:
        sys.__excepthook__(exctype, value, tb)
        print(f"[CRITICAL] Exception hook failed: {value} (hook error: {e})", file=sys.stderr)


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """
    threading.excepthook 替代实现

    捕获线程池 worker 未捕获异常，脱敏后记录 CRITICAL 日志。
    """
    try:
        if issubclass(args.exc_type, asyncio.CancelledError):
            logger.warning(
                "[ThreadingExcepthook] CancelledError in thread %s - this indicates a bug in shutdown logic.",
                args.thread.name if args.thread else "unknown",
            )
            return

        sanitized_msg = DataSanitizer.sanitize_error(args.exc_value)  # type: ignore[arg-type]
        thread_name = args.thread.name if args.thread else "unknown"
        logger.critical(
            "[ThreadingExcepthook] Unhandled exception in thread '%s': %s: %s",
            thread_name,
            args.exc_type.__name__,
            sanitized_msg,
            exc_info=True,
        )
    # NOTE(lazy): 线程钩子内兜底避免钩子自身崩溃. ceiling: 钩子内逻辑不应抛异常. upgrade: 钩子内部分类异常处理或移除兜底.
    except Exception as e:
        print(
            f"[CRITICAL] ThreadingExcepthook failed: {args.exc_value} (hook error: {e})",
            file=sys.stderr,
        )


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """
    asyncio 事件循环异常处理器

    捕获事件循环中未处理异常，脱敏后记录 CRITICAL 日志。
    特殊处理 CancelledError（正常关闭行为，降级为 WARNING）。
    """
    try:
        exception = context.get("exception")
        message = context.get("message", "No message")

        if isinstance(exception, asyncio.CancelledError):
            logger.warning(
                "[AsyncioHandler] CancelledError in event loop (likely during shutdown): %s",
                message,
            )
            return

        if isinstance(exception, KeyboardInterrupt):
            logger.info("[AsyncioHandler] KeyboardInterrupt in event loop, exiting gracefully.")
            return

        if exception is not None:
            sanitized_msg = DataSanitizer.sanitize_error(exception)  # type: ignore[arg-type]
            logger.critical(
                "[AsyncioHandler] Unhandled exception in event loop: %s: %s",
                type(exception).__name__,
                sanitized_msg,
                exc_info=True,
            )
        else:
            logger.critical("[AsyncioHandler] Event loop error (no exception): %s", message)
    # NOTE(lazy): asyncio 钩子内兜底避免钩子自身崩溃. ceiling: 钩子内逻辑不应抛异常. upgrade: 钩子内部分类异常处理或移除兜底.
    except Exception as e:
        print(f"[CRITICAL] AsyncioHandler failed: {context} (hook error: {e})", file=sys.stderr)


def install_global_exception_hooks(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """
    安装三个全局异常钩子

    Args:
        loop: asyncio 事件循环。如果为 None，尝试获取当前运行的事件循环。
              若无运行中的 loop，仅安装 sys + threading 钩子。

    调用时机：
        - 入口处调用安装 sys + threading 钩子
        - 在 main(page) 内部获取 loop 后调用安装 asyncio handler
    """
    global _original_sys_excepthook, _original_threading_excepthook

    _original_sys_excepthook = sys.excepthook
    sys.excepthook = _sys_excepthook
    logger.debug("[ExceptionHooks] sys.excepthook installed.")

    _original_threading_excepthook = threading.excepthook
    threading.excepthook = _threading_excepthook
    logger.debug("[ExceptionHooks] threading.excepthook installed.")

    if loop is not None:
        loop.set_exception_handler(_asyncio_exception_handler)
        logger.debug("[ExceptionHooks] asyncio loop exception_handler installed.")
    else:
        try:
            running_loop = asyncio.get_running_loop()
            running_loop.set_exception_handler(_asyncio_exception_handler)
            logger.debug("[ExceptionHooks] asyncio loop exception_handler installed for running loop.")
        except RuntimeError:
            logger.debug("[ExceptionHooks] No running event loop, asyncio handler will be installed later.")


def install_asyncio_handler_for_loop(loop: asyncio.AbstractEventLoop) -> None:
    """
    为指定事件循环安装异常处理器

    用于 Flet 等框架在内部创建事件循环的场景。
    """
    loop.set_exception_handler(_asyncio_exception_handler)
    logger.debug("[ExceptionHooks] asyncio loop exception_handler installed for loop %s", id(loop))


def restore_global_exception_hooks() -> None:
    """
    恢复原始异常钩子

    仅用于测试场景，生产环境不需要调用。
    """
    global _original_sys_excepthook, _original_threading_excepthook

    if _original_sys_excepthook is not None:
        sys.excepthook = _original_sys_excepthook
        _original_sys_excepthook = None
        logger.debug("[ExceptionHooks] sys.excepthook restored.")

    if _original_threading_excepthook is not None:
        threading.excepthook = _original_threading_excepthook
        _original_threading_excepthook = None
        logger.debug("[ExceptionHooks] threading.excepthook restored.")
