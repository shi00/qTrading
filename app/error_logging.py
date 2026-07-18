"""统一错误日志辅助函数，消除 main.py 中重复的 classify + severity + log level 模式.

抽取自 main.py 中 3 处相同的错误处理模式（Window destroy / Window center /
Window destroy during upgrade exit），按 severity 等级分发到对应日志级别。
"""

import logging

from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


def log_exception_with_severity(
    e: Exception,
    context: str,
    operation_label: str,
    *,
    logger_: logging.Logger | None = None,
) -> None:
    """按 severity 等级记录异常日志.

    Args:
        e: 捕获的异常
        context: 错误分类上下文（传给 classify_error/classify_severity）
        operation_label: 操作标签，用于日志前缀（如 "Main window destroy failed"）
        logger_: 可选的 logger 实例（默认用本模块 logger）

    """
    log = logger_ if logger_ is not None else logger
    error_info = classify_error(e, context=context)
    severity = classify_severity(e, context=context)
    log_fn = log.critical if severity == "system" else log.warning if severity == "recoverable" else log.error
    log_fn(
        "[%s] (%s): %s",
        operation_label,
        error_info["code"],
        DataSanitizer.sanitize_error(e),
        exc_info=True,
    )
