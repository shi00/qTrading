import logging
import os
import sys
from utils.config_handler import ConfigHandler
from logging.handlers import RotatingFileHandler
import datetime
import config

# Create logs dir if not exists
LOG_DIR = os.path.join(config.APP_ROOT, "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

def setup_logging(name="astock_screener"):
    """
    Setup structured logging with rotation.
    - Console: INFO level
    - File: DEBUG level, max 5MB per file, keep last 5 files
    """
    # Load config
    current_level = ConfigHandler.get_log_level()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR
    }
    logging_level = level_map.get(current_level, logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(logging_level) # Catch all, handlers will filter
    
    # Avoid duplicate handlers if setup is called multiple times
    if logger.handlers:
        # Update existing handlers level
        for h in logger.handlers:
            if isinstance(h, RotatingFileHandler) and not h.baseFilename.endswith("error.log"):
                 h.setLevel(logging_level)
        logger.setLevel(logging_level)
        return logger

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Load config
    try:
        max_mb = ConfigHandler.get_log_max_mb()
        backup_count = ConfigHandler.get_log_backup_count()
    except:
        max_mb = 5
        backup_count = 5
        
    max_bytes = int(max_mb * 1024 * 1024)

    # 1. Console Handler (INFO+)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. File Handler (DEBUG+, Rotating)
    # Use a fixed filename 'app.log' that rotates to app.log.1, app.log.2 etc.
    log_file_path = os.path.join(LOG_DIR, "app.log")
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(logging_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 3. Separate Error Log (ERROR+)
    error_log_path = os.path.join(LOG_DIR, "error.log")
    error_handler = RotatingFileHandler(
        error_log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)
    
    logger.info(f"--- Log Session Started: {datetime.datetime.now()} ---")
    return logger

def update_log_level(level_str):
    """
    Update log level at runtime.
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR
    }
    new_level = level_map.get(level_str.upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(new_level)
    
    for h in logger.handlers:
        # Update file handler (excluding error.log which is always ERROR)
        if isinstance(h, RotatingFileHandler) and "error.log" not in h.baseFilename:
            h.setLevel(new_level)
        # Update console handler
        elif isinstance(h, logging.StreamHandler):
             h.setLevel(new_level)
            
    logger.info(f"Log level updated to {level_str}")

def get_logger(name=None):
    """
    Get a logger instance with the specified name.
    If name is None, returns the root logger.
    """
    return logging.getLogger(name)
