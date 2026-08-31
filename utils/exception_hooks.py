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

from utils.error_classifier import classify_severity
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

_original_sys_excepthook: Callable | None = None
_original_threading_excepthook: Callable | None = None


def _sys_excepthook(exctype: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    """
    sys.excepthook 替代实现

    捕获主线程未处理异常，脱敏后记录日志。级别由 classify_severity 决定：
    system→critical / recoverable→warning（防止可恢复网络错误污染 CRITICAL 信号）/ 其他→error。
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

        severity = classify_severity(value)  # type: ignore[arg-type]
        if severity == "system":
            log_level = logger.critical
        elif severity == "recoverable":
            log_level = logger.warning
        else:
            log_level = logger.error
        sanitized_msg = DataSanitizer.sanitize_error(value)  # type: ignore[arg-type]
        log_level(
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


def _format_task_stack(context: dict) -> str:
    """从 asyncio 异常处理器的 context 中提取任务/未来的异常与栈帧，构造可读调用栈。

    asyncio 异常处理器回调时 sys.exc_info() 为空 (exc_info=True 无法记录真实 traceback)，
    故从 context["task"]/context["future"] 提取。返回形如 ``\\n  File "...", line N, in ...``
    的字符串；无可用帧时返回空串。
    """
    lines: list[str] = []
    obj = context.get("task") or context.get("future")
    if obj is None:
        return ""
    # 仅当 context 未携带 exception 时才从任务补取, 避免与调用方已打印的主消息重复。
    # 已取消任务 exception() 会抛 CancelledError (R2 禁止吞没), 故先经 cancelled() 预判跳过,
    # 不触碰取消路径; 未完成任务 exception() 抛 InvalidStateError, except 仅回退到纯栈帧。
    if "exception" not in context and hasattr(obj, "exception"):
        if not getattr(obj, "cancelled", lambda: False)():
            try:
                exc = obj.exception()
                sanitized = DataSanitizer.sanitize_error(exc)  # type: ignore[arg-type]
                lines.append(f"\n  Task exception: {type(exc).__name__}: {sanitized}")
            except asyncio.CancelledError:
                # 观察的任务在 cancelled() 检查后被取消, exception() 抛 CancelledError。
                # 仅读取他人任务取消状态, 非本钩子操作被取消, 不违反 R2; 忽略并继续取栈帧。
                # R2_ALLOWED: 读取他人任务取消状态, 非本钩子请求的取消, 不传播。
                pass
            except Exception:  # R2_ALLOWED: 仅兜底 InvalidStateError, except Exception 不捕获 CancelledError
                # 任务仍在运行/未完成时 exception() 抛 InvalidStateError, 忽略并继续取栈帧。
                pass
    if hasattr(obj, "get_stack"):
        for frame in obj.get_stack():
            fname = frame.f_code.co_filename
            lineno = frame.f_lineno
            qualname = frame.f_code.co_qualname
            lines.append(f'  File "{fname}", line {lineno}, in {qualname}')
    return "".join(lines)


def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """
    asyncio 事件循环异常处理器

    捕获事件循环中未处理异常，脱敏后记录 CRITICAL 日志。
    特殊处理 CancelledError（正常关闭行为，降级为 WARNING）。
    网络瞬时错误（ConnectionResetError 等）按 recoverable 降级为 WARNING，
    与 ai_service.py 处理同类异常的级别一致 (ai_service.py:922-933)。
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

        # 网络瞬时错误 (WinError 10054/1236 等) 按 recoverable 降级;
        # 不 raise: exception_handler 不参与传播, 异常已终止对应 transport.
        # 与 ai_service.py / db_config_service.py 处理同类异常的级别一致.
        if isinstance(exception, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            sanitized_msg = DataSanitizer.sanitize_error(exception)  # type: ignore[arg-type]
            logger.warning(
                "[AsyncioHandler] Network connection reset/aborted in event loop: %s: %s",
                type(exception).__name__,
                sanitized_msg,
            )
            return

        if exception is not None:
            sanitized_msg = DataSanitizer.sanitize_error(exception)  # type: ignore[arg-type]
            stack_hint = _format_task_stack(context)
            logger.critical(
                "[AsyncioHandler] Unhandled exception in event loop: %s: %s%s",
                type(exception).__name__,
                sanitized_msg,
                stack_hint,
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
