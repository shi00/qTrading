import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import config
from utils.config_handler import ConfigHandler
from utils.time_utils import get_now

# Define logs dir path (creation happens in setup_logging)
LOG_DIR = os.path.join(config.APP_ROOT, "logs")


def setup_logging(name="astock_screener"):
    """
    Setup structured logging with rotation.
    - Console: INFO level
    - File: DEBUG level, max 5MB per file, keep last 5 files
    """
    # 1. Ensure logs directory exists (Safe side-effect)
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception as e:
            # Fallback for permission errors - print to stderr
            sys.stderr.write(f"Failed to create log directory {LOG_DIR}: {e}\n")

    # 2. Load config with robustness
    try:
        current_level = ConfigHandler.get_log_level()
    except Exception:
        # Fallback if config is broken
        current_level = "INFO"

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    logging_level = level_map.get(current_level, logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(logging_level)

    # Check what we already have
    # Note: StreamHandler is a parent of FileHandler, so strict type check or order matters.
    # We want standard Stdout handler.
    has_console = any(type(h) is logging.StreamHandler for h in logger.handlers)

    # Check for our specific file handlers by filename
    has_app_log = False
    has_error_log = False

    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            if "app.log" in h.baseFilename:
                has_app_log = True
                h.setLevel(logging_level)  # Update level if config changed
            elif "error.log" in h.baseFilename:
                has_error_log = True

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(threadName)s] [%(filename)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Load config limits
    try:
        max_mb = ConfigHandler.get_log_max_mb()
        backup_count = ConfigHandler.get_log_backup_count()
    except Exception:
        max_mb = 5
        backup_count = 5
    max_bytes = int(max_mb * 1024 * 1024)

    # 3. Console Handler (user configured level)
    if not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # 4. File Handler (DEBUG+, Rotating)
    if not has_app_log:
        log_file_path = os.path.join(LOG_DIR, "app.log")
        try:
            # Force rollover on startup if file exists and has content
            # This ensures each run starts with a fresh log file, while keeping history via rotation
            if os.path.exists(log_file_path) and os.path.getsize(log_file_path) > 0:
                try:
                    # Create a temporary handler to force rollover
                    temp_handler = RotatingFileHandler(
                        log_file_path,
                        maxBytes=max_bytes,
                        backupCount=backup_count,
                        encoding="utf-8",
                    )
                    temp_handler.doRollover()
                    temp_handler.close()
                except Exception as e:
                    sys.stderr.write(f"Failed to rotate old log: {e}\n")

            file_handler = RotatingFileHandler(
                log_file_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setLevel(logging_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(f"Failed to setup file logging: {e}\n")

    # 5. Separate Error Log (ERROR+)
    if not has_error_log:
        error_log_path = os.path.join(LOG_DIR, "error.log")
        try:
            # Force rollover for error log as well
            if os.path.exists(error_log_path) and os.path.getsize(error_log_path) > 0:
                try:
                    temp_err_handler = RotatingFileHandler(
                        error_log_path,
                        maxBytes=max_bytes,
                        backupCount=backup_count,
                        encoding="utf-8",
                    )
                    temp_err_handler.doRollover()
                    temp_err_handler.close()
                except Exception:
                    pass

            error_handler = RotatingFileHandler(
                error_log_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)
        except Exception:
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
        # Update file handler (excluding error.log which is always ERROR)
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
