import atexit
import copy
import json
import logging
import os
import queue
import sys
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

import config
from utils.config_handler import ConfigHandler
from utils.time_utils import get_now

LOG_DIR = os.path.join(config.USER_DATA_ROOT, "logs")
# D8-4：旧日志目录（APP_ROOT 即安装/源码目录）——升级迁移的一次性复制源。
_LOG_DIR_LEGACY = os.path.join(config.APP_ROOT, "logs")

# OSS G1：文件写盘经 QueueHandler + QueueListener 解耦到后台线程（见 setup_logging）。
# _QUEUE_MAXSIZE：队列满时 QueueHandler 以 put_nowait 直接丢弃并提供 handleError 提示，
# 事件循环线程绝不因写盘阻塞（CLAUDE.md R16）；日志为尽力而为，极端突发峰值时的少量
# 丢弃相对于回阻主循环可接受。
_QUEUE_MAXSIZE = 1000
_FILE_QUEUE: queue.Queue | None = None
_LISTENER: QueueListener | None = None

logger = logging.getLogger(__name__)


def _migrate_legacy_log_dir() -> None:
    """把 APP_ROOT 下的旧 logs 目录一次性迁移到 USER_DATA_ROOT/logs。

    仅在新日志目录不存在而旧目录存在时复制；保留源目录（不删除），防降级安装
    丢失日志。惰性在 setup_logging 前触发；幂等。
    """
    if os.path.isdir(LOG_DIR) or not os.path.isdir(_LOG_DIR_LEGACY):
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        import shutil

        for name in os.listdir(_LOG_DIR_LEGACY):
            src = os.path.join(_LOG_DIR_LEGACY, name)
            dst = os.path.join(LOG_DIR, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    except OSError as e:
        logger.error("Failed to migrate legacy log dir: %s", e, exc_info=True)


class _SanitizingFormatter(logging.Formatter):
    """对 exc_info 输出的 traceback 做文件路径脱敏的 logging.Formatter。

    覆写 formatException 使 exc_info=True 输出的文本（含完整文件路径、
    可能含系统用户名）中的路径被替换为 <PATH>。文本与 JSON 两种格式均复用。

    __init__ 透传 logging.Formatter，无额外初始化参数。
    """

    def formatException(self, ei):
        res = super().formatException(ei)
        from utils.sanitizers import DataSanitizer

        return DataSanitizer.sanitize_paths(res)


class _QueueHandler(QueueHandler):
    """保留结构化输出与脱敏（R9）的 QueueHandler。

    stdlib ``QueueHandler.prepare`` 会用默认 formatter 把记录重写为纯文本字符串
    （将 traceback 拼进 message）并清空 exc_info，从而绕过 JSONFormatter /
    _SanitizingFormatter 的结构化输出与路径脱敏。本项目队列为单进程内线程间引用
    传递（无跨进程 pickling 需求），无需该改写；这里仅做浅拷贝入队，exc_info 保留
    到 listener 线程由文件 handler 的 formatter 负责格式化与脱敏。
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)


class JSONFormatter(_SanitizingFormatter):
    """
    JSON formatter for structured logging.
    Outputs logs in JSON format suitable for centralized log systems
    (Loki, ELK, Datadog, CloudWatch, etc.).
    """

    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", "-"),
            "thread": record.threadName,
            "file": f"{record.filename}:{record.lineno}",
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data, ensure_ascii=False)


def _get_formatter(use_json: bool = False) -> logging.Formatter:
    """
    Get the appropriate formatter based on configuration.

    Args:
        use_json: If True, use JSON formatter; otherwise use text formatter.

    Returns:
        logging.Formatter instance.
    """
    if use_json:
        return JSONFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    return _SanitizingFormatter(
        "%(asctime)s [%(levelname)s] [%(correlation_id)s] [%(threadName)s] [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(name="astock_screener"):
    """
    Setup structured logging with rotation.
    - Console: user configured level (default: INFO)
    - File: user configured level (default: INFO)
    - Supports JSON format via ConfigHandler.get_log_format()
    """
    global _FILE_QUEUE, _LISTENER
    _migrate_legacy_log_dir()
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        # NOTE(lazy): 日志目录创建失败兜底(权限/磁盘满). ceiling: 系统级磁盘故障无法恢复. upgrade: 引入启动预检或告警上报.
        except Exception as e:
            sys.stderr.write(f"Failed to create log directory {LOG_DIR}: {e}\n")

    try:
        current_level = ConfigHandler.get_log_level()
    except (ValueError, OSError, RuntimeError):
        current_level = "INFO"

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    logging_level = level_map.get(current_level, logging.INFO)

    try:
        log_format = ConfigHandler.get_log_format()
    except (ValueError, OSError, RuntimeError):
        log_format = "text"
    use_json = log_format.lower() == "json"

    logger = logging.getLogger()
    logger.setLevel(logging_level)

    has_console = any(type(h) is logging.StreamHandler for h in logger.handlers)

    formatter = _get_formatter(use_json)

    from utils.correlation import CorrelationFilter

    correlation_filter = CorrelationFilter()

    # Load config limits
    try:
        max_mb = ConfigHandler.get_log_max_mb()
        backup_count = ConfigHandler.get_log_backup_count()
    except (ValueError, OSError, RuntimeError):
        max_mb = 5
        backup_count = 10
    max_bytes = int(max_mb * 1024 * 1024)

    # 3. Console Handler (user configured level)
    if not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(correlation_filter)
        logger.addHandler(console_handler)

    # 4-6. File Handlers 经 QueueHandler + QueueListener 解耦到后台写盘线程
    # 调用线程仅 put 队列；RotatingFileHandler/FileHandler（含轮转 rename）在
    # listener 后台线程执行，事件循环线程不再同步写盘（CLAUDE.md R16）。
    # 首次 setup 构建文件 handler 并启动 listener；重复 setup 复用既有 listener
    # 及其 handler（保证 latest.log 不二次截断、handler 不重复）。
    has_queue_handler = any(isinstance(h, QueueHandler) for h in logger.handlers)

    if _LISTENER is None:
        file_handlers: list[logging.Handler] = []

        # 4. App File Handler (DEBUG+, Rotating)
        log_file_path = os.path.join(LOG_DIR, "app.log")
        try:
            app_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            app_handler.setLevel(logging_level)
            app_handler.setFormatter(formatter)
            app_handler.addFilter(correlation_filter)
            file_handlers.append(app_handler)
        # NOTE(lazy): app.log 文件 handler 创建失败兜底(权限/磁盘满). ceiling: 系统级磁盘故障无法恢复. upgrade: 引入启动预检或降级到仅控制台输出.
        except Exception as e:
            sys.stderr.write(f"Failed to setup file logging: {e}\n")

        # 5. Separate Error Log (ERROR+)
        error_log_path = os.path.join(LOG_DIR, "error.log")
        try:
            error_handler = RotatingFileHandler(
                error_log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            error_handler.addFilter(correlation_filter)
            file_handlers.append(error_handler)
        except (OSError, ValueError) as e:
            sys.stderr.write(f"Failed to setup error logging: {e}\n")

        # 6. Latest Log File Handler (Overwrite on startup, mode='w')
        latest_log_path = os.path.join(LOG_DIR, "latest.log")
        try:
            latest_handler = logging.FileHandler(
                latest_log_path,
                mode="w",
                encoding="utf-8",
            )
            latest_handler.setLevel(logging_level)
            latest_handler.setFormatter(formatter)
            latest_handler.addFilter(correlation_filter)
            file_handlers.append(latest_handler)
        # NOTE(lazy): latest.log 文件 handler 创建失败兜底(权限/磁盘满). ceiling: 系统级磁盘故障无法恢复. upgrade: 引入启动预检或降级到仅控制台输出.
        except Exception as e:
            sys.stderr.write(f"Failed to setup latest file logging: {e}\n")

        if file_handlers and not has_queue_handler:
            _FILE_QUEUE = queue.Queue(maxsize=_QUEUE_MAXSIZE)
            # handlers 以元组承载（CPython 3.13 QueueListener.handlers 不可变），
            # 首次启动即纳入全部文件 handler。
            _LISTENER = QueueListener(_FILE_QUEUE, *file_handlers)
            _LISTENER.start()
            queue_handler = _QueueHandler(_FILE_QUEUE)
            queue_handler.setLevel(logging_level)
            logger.addHandler(queue_handler)
    elif _LISTENER is not None:
        # 复用已启动的 listener：仅按当前配置同步既有文件 handler 的级别
        for h in _LISTENER.handlers:
            is_error_log = hasattr(h, "baseFilename") and "error.log" in h.baseFilename  # type: ignore[attr-defined]  # pyright 无法经 hasattr 收窄任意 logging.Handler 的 baseFilename
            h.setLevel(logging.ERROR if is_error_log else logging_level)

    # 7. Suppress noisy third-party logs
    noisy_libs = [
        "urllib3",
        "requests",
        "asyncio",
        "flet",
        "apscheduler",
        "PIL",
        "websockets",
        "litellm",
    ]
    for lib in noisy_libs:
        logging.getLogger(lib).setLevel(logging.WARNING)

    logger.info("--- Log Session Started: %s ---", get_now())
    return logger


def update_log_level(level_str):
    """
    Update log level at runtime.

    Note: The error.log handler is always kept at ERROR level regardless of the
    new setting, ensuring monitoring tools can reliably capture errors even when
    the runtime level is lowered to DEBUG/INFO.
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    new_level = level_map.get(level_str.upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(new_level)

    # root 上的 console/QueueHandler 与 listener 内的文件 handler 一并更新级别
    # （error.log 始终保留 ERROR，供监控工具可靠捕获错误）。
    file_handlers = list(_LISTENER.handlers) if _LISTENER is not None else []
    for h in list(logger.handlers) + file_handlers:
        # Update file handler (excluding error.log which is always ERROR for monitoring tools)
        is_error_log = hasattr(h, "baseFilename") and "error.log" in h.baseFilename  # type: ignore[attr-defined]
        if not is_error_log:
            h.setLevel(new_level)

    logger.info("Log level updated to %s", level_str)


def get_logger(name=None):
    """
    Get a logger instance with the specified name.
    If name is None, returns the root logger.
    """
    return logging.getLogger(name)


def flush_logging() -> None:
    """阻塞等待已入队的日志全部被 listener 后台线程处理完毕。

    依赖 queue.Queue 的 task_done 计数：QueueListener 在每条记录实际写完后调用
    task_done()，join() 在其归零时返回。仅用于测试与退出前确定性断言；生产高频
    路径不调用（不阻塞调用线程）。
    """
    if _FILE_QUEUE is None:
        return
    _FILE_QUEUE.join()


def stop_logging() -> None:
    """停止并回收日志队列 listener 线程及其 QueueHandler（幂等）。

    QueueListener.stop() 会入队 sentinel 排空队列中的遗留记录并 join 后台线程，
    随后从 root logger 移除 QueueHandler。生产在进程退出（atexit）时调用；测试在
    teardown 调用以保证不泄漏线程、不残留队列任务（CLAUDE.md R7 测试隔离）。
    """
    global _FILE_QUEUE, _LISTENER
    if _LISTENER is not None:
        try:
            _LISTENER.stop()
        finally:
            _LISTENER = None
            _FILE_QUEUE = None
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, QueueHandler):
            root.removeHandler(h)


atexit.register(stop_logging)
