import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import get_logger, setup_logging, update_log_level


class TestLogger(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for logs
        self.test_dir = tempfile.TemporaryDirectory()
        self.log_dir = os.path.join(self.test_dir.name, "logs")

        # Patch LOG_DIR in logger module
        self.patcher = patch("utils.logger.LOG_DIR", self.log_dir)
        self.patcher.start()

        # Ensure log dir exists
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

        # Reset logger handlers to avoid interference
        self.root_logger = logging.getLogger()
        self.original_handlers = self.root_logger.handlers[:]
        self.root_logger.handlers = []

    def tearDown(self):
        # Restore handlers
        self.root_logger.handlers = self.original_handlers
        self.patcher.stop()
        self.test_dir.cleanup()

    def test_setup_logging_defaults(self):
        """Test logging setup with default settings"""
        # Mock ConfigHandler to return defaults
        with (
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            logger = setup_logging("test_logger")

            # Check handlers
            self.assertEqual(len(logger.handlers), 3)  # Console, App File, Error File

            # Check file handlers properties
            file_handler = [
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore
                and "app.log" in h.baseFilename  # type: ignore
            ][0]
            self.assertEqual(file_handler.maxBytes, 5 * 1024 * 1024)  # type: ignore
            self.assertEqual(file_handler.backupCount, 5)  # type: ignore

    def test_setup_logging_custom_config(self):
        """Test logging setup with custom configuration"""
        with (
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=1),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=2,
            ),
        ):
            logger = setup_logging()

            file_handler = [
                h
                for h in logger.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore
                and "app.log" in h.baseFilename  # type: ignore
            ][0]
            self.assertEqual(file_handler.maxBytes, 1 * 1024 * 1024)  # type: ignore
            self.assertEqual(file_handler.backupCount, 2)  # type: ignore

    def test_logger_writing(self):
        """Test that logger actually writes to file"""
        setup_logging()
        logger = get_logger()

        message = "Test log message unique string"
        logger.info(message)

        # Verify log file content
        log_file = os.path.join(self.log_dir, "app.log")
        self.assertTrue(os.path.exists(log_file))

        with open(log_file, encoding="utf-8") as f:
            content = f.read()
            self.assertIn(message, content)

    def test_get_logger_name(self):
        """Test get_logger with name"""
        logger = get_logger("my_module")
        self.assertEqual(logger.name, "my_module")


if __name__ == "__main__":
    unittest.main()


class TestGetLogger:
    def test_get_root_logger(self):
        logger = get_logger()
        assert logger is not None

    def test_get_named_logger(self):
        logger = get_logger("test_module")
        assert logger.name == "test_module"


class TestSetupLoggingNoisyLibs:
    def test_noisy_libs_suppressed(self):
        with patch("utils.logger.LOG_DIR", "/tmp/test_logs"):
            setup_logging()
        noisy_libs = ["urllib3", "requests", "flet", "apscheduler"]
        for lib in noisy_libs:
            assert logging.getLogger(lib).level >= logging.WARNING


class TestUpdateLogLevel:
    def setup_method(self):
        self.root_logger = logging.getLogger()
        self.original_handlers = self.root_logger.handlers[:]
        self.root_logger.handlers = []

    def teardown_method(self):
        self.root_logger.handlers = self.original_handlers

    def test_update_to_debug(self):
        with patch("utils.logger.LOG_DIR", "/tmp/test_logs"):
            setup_logging()
        update_log_level("DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_update_to_warning(self):
        with patch("utils.logger.LOG_DIR", "/tmp/test_logs"):
            setup_logging()
        update_log_level("WARNING")
        assert logging.getLogger().level == logging.WARNING

    def test_update_to_error(self):
        with patch("utils.logger.LOG_DIR", "/tmp/test_logs"):
            setup_logging()
        update_log_level("ERROR")
        assert logging.getLogger().level == logging.ERROR

    def test_update_unknown_defaults_to_info(self):
        with patch("utils.logger.LOG_DIR", "/tmp/test_logs"):
            setup_logging()
        update_log_level("UNKNOWN_LEVEL")
        assert logging.getLogger().level == logging.INFO
