import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import config
from utils.config_handler import ConfigHandler
from utils.time_utils import get_now

LOG_DIR = os.path.join(config.APP_ROOT, "logs")


class JSONFormatter(logging.Formatter):
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
    return logging.Formatter(
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
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
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

    has_app_log = False
    has_error_log = False

    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            if "app.log" in h.baseFilename:
                has_app_log = True
                h.setLevel(logging_level)
            elif "error.log" in h.baseFilename:
                has_error_log = True

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

    # 4. File Handler (DEBUG+, Rotating)
    if not has_app_log:
        log_file_path = os.path.join(LOG_DIR, "app.log")
        try:
            file_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(logging_level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(correlation_filter)
            logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(f"Failed to setup file logging: {e}\n")

    # 5. Separate Error Log (ERROR+)
    if not has_error_log:
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
            logger.addHandler(error_handler)
        except (OSError, ValueError):
            pass

    # 6. Suppress noisy third-party logs
    noisy_libs = [
        "urllib3",
        "requests",
        "asyncio",
        "flet",
        "apscheduler",
        "matplotlib",
        "PIL",
        "websockets",
        "litellm",
    ]
    for lib in noisy_libs:
        logging.getLogger(lib).setLevel(logging.WARNING)

    logger.info(f"--- Log Session Started: {get_now()} ---")
    return logger


def update_log_level(level_str):
    """
    Update log level at runtime.
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

    for h in logger.handlers:
        # Update file handler (excluding error.log which is always ERROR for monitoring tools)
        if (isinstance(h, RotatingFileHandler) and "error.log" not in h.baseFilename) or isinstance(
            h, logging.StreamHandler
        ):
            h.setLevel(new_level)

    logger.info(f"Log level updated to {level_str}")


def get_logger(name=None):
    """
    Get a logger instance with the specified name.
    If name is None, returns the root logger.
    """
    return logging.getLogger(name)
