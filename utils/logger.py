import logging
import os
import sys
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
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG) # Catch all, handlers will filter
    
    # Avoid duplicate handlers if setup is called multiple times
    if logger.handlers:
        return logger

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

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
        maxBytes=5*1024*1024, # 5MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 3. Separate Error Log (ERROR+)
    error_log_path = os.path.join(LOG_DIR, "error.log")
    error_handler = RotatingFileHandler(
        error_log_path,
        maxBytes=5*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)
    
    logger.info(f"--- Log Session Started: {datetime.datetime.now()} ---")
    return logger

def get_logger(name=None):
    """
    Get a logger instance with the specified name.
    If name is None, returns the root logger.
    """
    return logging.getLogger(name)
