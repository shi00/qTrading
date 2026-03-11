import unittest
import os
import logging
import tempfile
import sys
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import setup_logging, get_logger


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
                if isinstance(h, logging.handlers.RotatingFileHandler)
                and "app.log" in h.baseFilename
            ][0]
            self.assertEqual(file_handler.maxBytes, 5 * 1024 * 1024)
            self.assertEqual(file_handler.backupCount, 5)

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
                if isinstance(h, logging.handlers.RotatingFileHandler)
                and "app.log" in h.baseFilename
            ][0]
            self.assertEqual(file_handler.maxBytes, 1 * 1024 * 1024)
            self.assertEqual(file_handler.backupCount, 2)

    def test_logger_writing(self):
        """Test that logger actually writes to file"""
        setup_logging()
        logger = get_logger()

        message = "Test log message unique string"
        logger.info(message)

        # Verify log file content
        log_file = os.path.join(self.log_dir, "app.log")
        self.assertTrue(os.path.exists(log_file))

        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn(message, content)

    def test_get_logger_name(self):
        """Test get_logger with name"""
        logger = get_logger("my_module")
        self.assertEqual(logger.name, "my_module")


if __name__ == "__main__":
    unittest.main()
