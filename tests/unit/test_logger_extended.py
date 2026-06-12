import logging
import os
import tempfile
import unittest
from unittest.mock import patch

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
                if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore[untyped]
                and "app.log" in h.baseFilename  # type: ignore[untyped]
            ][0]
            self.assertEqual(file_handler.maxBytes, 5 * 1024 * 1024)  # type: ignore[untyped]
            self.assertEqual(file_handler.backupCount, 5)  # type: ignore[untyped]

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
                if isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore[untyped]
                and "app.log" in h.baseFilename  # type: ignore[untyped]
            ][0]
            self.assertEqual(file_handler.maxBytes, 1 * 1024 * 1024)  # type: ignore[untyped]
            self.assertEqual(file_handler.backupCount, 2)  # type: ignore[untyped]

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
        assert isinstance(logger, logging.Logger)

    def test_get_named_logger(self):
        logger = get_logger("test_module")
        assert logger.name == "test_module"


class TestSetupLoggingNoisyLibs:
    def test_noisy_libs_suppressed(self, tmp_path):
        with patch("utils.logger.LOG_DIR", str(tmp_path / "test_logs")):
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

    def test_update_to_debug(self, tmp_path):
        with patch("utils.logger.LOG_DIR", str(tmp_path / "test_logs")):
            setup_logging()
        update_log_level("DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_update_to_warning(self, tmp_path):
        with patch("utils.logger.LOG_DIR", str(tmp_path / "test_logs")):
            setup_logging()
        update_log_level("WARNING")
        assert logging.getLogger().level == logging.WARNING

    def test_update_to_error(self, tmp_path):
        with patch("utils.logger.LOG_DIR", str(tmp_path / "test_logs")):
            setup_logging()
        update_log_level("ERROR")
        assert logging.getLogger().level == logging.ERROR

    def test_update_unknown_defaults_to_info(self, tmp_path):
        with patch("utils.logger.LOG_DIR", str(tmp_path / "test_logs")):
            setup_logging()
        update_log_level("UNKNOWN_LEVEL")
        assert logging.getLogger().level == logging.INFO


class TestSetupLoggingDegradation:
    def setup_method(self):
        self.root_logger = logging.getLogger()
        self.original_handlers = self.root_logger.handlers[:]
        self.root_logger.handlers = []

    def teardown_method(self):
        self.root_logger.handlers = self.original_handlers

    def test_makedirs_failure_continues(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("os.path.exists", return_value=False),
            patch("os.makedirs", side_effect=PermissionError("no access")),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch("utils.config_handler.ConfigHandler.get_log_backup_count", return_value=5),
        ):
            logger = setup_logging("degradation_test")
        assert logger is not None
        console_handlers = [h for h in logger.handlers if type(h) is logging.StreamHandler]
        assert len(console_handlers) >= 1

    def test_config_log_level_exception_defaults_info(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", side_effect=ValueError("bad config")),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch("utils.config_handler.ConfigHandler.get_log_backup_count", return_value=5),
        ):
            logger = setup_logging("level_fallback_test")
        assert logger.level == logging.INFO

    def test_config_rotation_exception_defaults(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", side_effect=OSError("unreadable")),
            patch("utils.config_handler.ConfigHandler.get_log_backup_count", side_effect=OSError("unreadable")),
        ):
            logger = setup_logging("rotation_fallback_test")
        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler) and "app.log" in h.baseFilename
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == 5 * 1024 * 1024

    def test_no_app_log_rollover_on_startup(self, tmp_path):
        """验证启动时不再强制轮转日志，而是直接追加日志并输出运行周期标志"""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()
        app_log = log_dir / "app.log"
        app_log.write_text("old log content\n", encoding="utf-8")
        with (
            patch("utils.logger.LOG_DIR", str(log_dir)),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch("utils.config_handler.ConfigHandler.get_log_backup_count", return_value=5),
        ):
            setup_logging("no_rollover_test")

        # 验证 app.log.1 不应被创建 (即没有发生 rollover)
        assert not (log_dir / "app.log.1").exists()

        # 验证原 app.log 中依然保留旧内容，并追加了新会话日志
        content = app_log.read_text(encoding="utf-8")
        assert "old log content" in content
        assert "--- Log Session Started" in content


class TestJSONFormatter:
    def test_json_formatter_basic(self):
        import json

        from utils.logger import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="test message",
            args=(),
            exc_info=None,
        )
        record.threadName = "MainThread"
        record.correlation_id = "test-123"

        result = formatter.format(record)
        data = json.loads(result)

        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "test message"
        assert data["correlation_id"] == "test-123"
        assert data["thread"] == "MainThread"
        assert data["file"] == "test.py:10"

    def test_json_formatter_with_exception(self):
        import json
        import sys

        from utils.logger import JSONFormatter

        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=20,
                msg="error occurred",
                args=(),
                exc_info=exc_info,
            )
            record.threadName = "MainThread"

        result = formatter.format(record)
        data = json.loads(result)

        assert data["level"] == "ERROR"
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_json_formatter_missing_correlation_id(self):
        import json

        from utils.logger import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="test message",
            args=(),
            exc_info=None,
        )
        record.threadName = "MainThread"

        result = formatter.format(record)
        data = json.loads(result)

        assert data["correlation_id"] == "-"


class TestLogFormatSelection:
    def setup_method(self):
        self.root_logger = logging.getLogger()
        self.original_handlers = self.root_logger.handlers[:]
        self.root_logger.handlers = []

    def teardown_method(self):
        self.root_logger.handlers = self.original_handlers

    def test_text_format_by_default(self, tmp_path):
        from utils.logger import _get_formatter

        formatter = _get_formatter(use_json=False)
        assert isinstance(formatter, logging.Formatter)
        assert not isinstance(formatter, type("JSONFormatter", (), {}))

    def test_json_format_when_configured(self, tmp_path):
        from utils.logger import JSONFormatter, _get_formatter

        formatter = _get_formatter(use_json=True)
        assert isinstance(formatter, JSONFormatter)

    def test_setup_logging_uses_json_format(self, tmp_path):
        from utils.logger import JSONFormatter

        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_format", return_value="json"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch("utils.config_handler.ConfigHandler.get_log_backup_count", return_value=5),
        ):
            logger = setup_logging("json_format_test")

        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler) and "app.log" in h.baseFilename
        ]
        assert len(file_handlers) == 1
        assert isinstance(file_handlers[0].formatter, JSONFormatter)

    def test_setup_logging_uses_text_format(self, tmp_path):
        from utils.logger import JSONFormatter

        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_format", return_value="text"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch("utils.config_handler.ConfigHandler.get_log_backup_count", return_value=5),
        ):
            logger = setup_logging("text_format_test")

        file_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler) and "app.log" in h.baseFilename
        ]
        assert len(file_handlers) == 1
        assert not isinstance(file_handlers[0].formatter, JSONFormatter)
