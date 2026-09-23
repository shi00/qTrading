import logging
import os
import tempfile
import unittest
from unittest.mock import patch
from logging.handlers import RotatingFileHandler

from utils.logger import flush_logging, get_logger, setup_logging, update_log_level
import pytest


pytestmark = pytest.mark.unit


def _listener_file_handlers():
    """返回 QueueListener 后台承载的文件 handler 列表（文件写盘已迁至后台线程）。

    需经模块引用动态读取 _LISTENER（其为模块级可变全局，import 值绑定会读到旧值）。
    """
    import utils.logger as _ul

    return list(_ul._LISTENER.handlers) if _ul._LISTENER is not None else []


def _flush_and_get_logger_files(log_dir: str):
    """记录写入后刷新队列，返回 (listener文件handlers, app.log内容)。"""
    flush_logging()
    app_log = os.path.join(log_dir, "app.log")
    with open(app_log, encoding="utf-8") as _f:
        content = _f.read()
    return _listener_file_handlers(), content


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

        # 回收上一个用例遗留的 listener 线程，再重置 handlers（R7 隔离）
        from utils.logger import stop_logging

        stop_logging()

        # Reset logger handlers to avoid interference
        self.root_logger = logging.getLogger()
        self.original_handlers = self.root_logger.handlers[:]
        self.root_logger.handlers = []

    def tearDown(self):
        # 先停 listener（排空队列并 join 后台线程），再恢复/清理，避免线程写已删目录
        from utils.logger import stop_logging

        stop_logging()
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

            # 文件 handler 已迁至 QueueListener 后台；root 上为 console + QueueHandler
            self.assertEqual(len(logger.handlers), 2)  # Console + QueueHandler

            # Check file handlers properties (now on the listener)
            file_handler = [
                h
                for h in _listener_file_handlers()
                if isinstance(h, RotatingFileHandler) and "app.log" in h.baseFilename
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
            setup_logging()

            file_handler = [
                h
                for h in _listener_file_handlers()
                if isinstance(h, RotatingFileHandler) and "app.log" in h.baseFilename
            ][0]
            self.assertEqual(file_handler.maxBytes, 1 * 1024 * 1024)
            self.assertEqual(file_handler.backupCount, 2)

    def test_logger_writing(self):
        """Test that logger actually writes to file"""
        setup_logging()
        logger = get_logger()

        message = "Test log message unique string"
        logger.info(message)

        # Verify log file content (drain queue so listener has written to disk)
        _, content = _flush_and_get_logger_files(self.log_dir)
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
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            logger = setup_logging("degradation_test")
        assert logger is not None
        console_handlers = [h for h in logger.handlers if type(h) is logging.StreamHandler]
        assert len(console_handlers) >= 1

    def test_config_log_level_exception_defaults_info(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch(
                "utils.config_handler.ConfigHandler.get_log_level",
                side_effect=ValueError("bad config"),
            ),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            logger = setup_logging("level_fallback_test")
        assert logger.level == logging.INFO

    def test_config_rotation_exception_defaults(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch(
                "utils.config_handler.ConfigHandler.get_log_max_mb",
                side_effect=OSError("unreadable"),
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                side_effect=OSError("unreadable"),
            ),
        ):
            setup_logging("rotation_fallback_test")
        file_handlers = [
            h for h in _listener_file_handlers() if isinstance(h, RotatingFileHandler) and "app.log" in h.baseFilename
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
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("no_rollover_test")
        flush_logging()

        # 验证 app.log.1 不应被创建 (即没有发生 rollover)
        assert not (log_dir / "app.log.1").exists()

        # 验证原 app.log 中依然保留旧内容，并追加了新会话日志
        content = app_log.read_text(encoding="utf-8")
        assert "old log content" in content
        assert "--- Log Session Started" in content

    def test_error_log_failure_writes_to_stderr(self, tmp_path, capsys):
        """ERR-M2 / SEC-L2: error.log handler failure must write to stderr, not silently pass."""
        log_dir = str(tmp_path / "test_logs")

        class FailingRotatingFileHandler(RotatingFileHandler):
            def __init__(self, *args, **kwargs):
                raise OSError("permission denied")

        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
            patch("utils.logger.RotatingFileHandler", FailingRotatingFileHandler),
        ):
            setup_logging("error_log_stderr_test")

        captured = capsys.readouterr()
        assert "Failed to setup error logging" in captured.err
        assert "permission denied" in captured.err


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
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("json_format_test")

        file_handlers = [
            h for h in _listener_file_handlers() if isinstance(h, RotatingFileHandler) and "app.log" in h.baseFilename
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
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("text_format_test")

        file_handlers = [
            h for h in _listener_file_handlers() if isinstance(h, RotatingFileHandler) and "app.log" in h.baseFilename
        ]
        assert len(file_handlers) == 1
        assert not isinstance(file_handlers[0].formatter, JSONFormatter)


class TestLatestLog:
    """latest.log 覆写机制与运行时行为验证"""

    def setup_method(self):
        self.root_logger = logging.getLogger()
        self.original_handlers = self.root_logger.handlers[:]
        self.root_logger.handlers = []

    def teardown_method(self):
        self.root_logger.handlers = self.original_handlers

    def test_latest_log_overwritten_on_startup(self, tmp_path):
        """验证启动时 latest.log 总是被覆写（清空），不保留历史日志"""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()
        latest_log = log_dir / "latest.log"
        latest_log.write_text("old latest log content\n", encoding="utf-8")

        with (
            patch("utils.logger.LOG_DIR", str(log_dir)),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("latest_overwrite_test")
        flush_logging()

        # 验证原 latest.log 内容被清空，只包含本次启动的日志
        content = latest_log.read_text(encoding="utf-8")
        assert "old latest log content" not in content
        assert "--- Log Session Started" in content

    def test_latest_log_receives_log_writes(self, tmp_path):
        """验证 latest.log 实际接收日志写入，且内容与 app.log 一致"""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()

        with (
            patch("utils.logger.LOG_DIR", str(log_dir)),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("latest_write_test")
            logger = get_logger()
            logger.info("unique_latest_log_marker_12345")
        flush_logging()

        latest_content = (log_dir / "latest.log").read_text(encoding="utf-8")
        app_content = (log_dir / "app.log").read_text(encoding="utf-8")
        assert "unique_latest_log_marker_12345" in latest_content
        assert "unique_latest_log_marker_12345" in app_content

    def test_update_log_level_affects_latest_log(self, tmp_path):
        """验证 update_log_level 运行时更新会同步作用于 latest.log handler"""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()

        with (
            patch("utils.logger.LOG_DIR", str(log_dir)),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("latest_level_update_test")
            latest_handler = [
                h
                for h in _listener_file_handlers()
                if type(h) is logging.FileHandler and "latest.log" in h.baseFilename
            ][0]
            assert latest_handler.level == logging.INFO

            update_log_level("WARNING")
            assert latest_handler.level == logging.WARNING

    def test_latest_log_not_duplicated_on_reinit(self, tmp_path):
        """验证重复调用 setup_logging 不会重复添加 latest.log handler，也不会二次截断文件"""
        log_dir = tmp_path / "test_logs"
        log_dir.mkdir()
        latest_log = log_dir / "latest.log"

        with (
            patch("utils.logger.LOG_DIR", str(log_dir)),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("reinit_first")
            logger = get_logger()
            logger.info("first_session_marker")
            flush_logging()
            first_content = latest_log.read_text(encoding="utf-8")
            assert "first_session_marker" in first_content

            # 第二次调用：不应重建 handler，不应截断已写入内容
            setup_logging("reinit_second")
            latest_handlers = [
                h
                for h in _listener_file_handlers()
                if type(h) is logging.FileHandler and "latest.log" in h.baseFilename
            ]
            assert len(latest_handlers) == 1  # 唯一性：未重复添加

            logger.info("second_session_marker")
            flush_logging()
            second_content = latest_log.read_text(encoding="utf-8")
            # 第一次的内容必须保留（证明未二次截断）
            assert "first_session_marker" in second_content
            assert "second_session_marker" in second_content

    def test_latest_log_failure_writes_to_stderr(self, tmp_path, capsys):
        """latest.log handler 初始化失败时必须写入 stderr，不得静默吞没"""
        log_dir = str(tmp_path / "test_logs")

        class FailingFileHandler(logging.FileHandler):
            def __init__(self, *args, **kwargs):
                raise OSError("permission denied")

        # patch logging.FileHandler 仅影响 latest.log 的直接构造；
        # RotatingFileHandler 的 MRO 在类定义时已绑定原始 FileHandler，不受此 patch 影响。
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
            patch("utils.logger.logging.FileHandler", FailingFileHandler),
        ):
            setup_logging("latest_log_stderr_test")

        captured = capsys.readouterr()
        assert "Failed to setup latest file logging" in captured.err
        assert "permission denied" in captured.err


class TestFileHandlingMovedToListenerThread:
    """OSS G1：文件写盘已迁至 QueueListener 后台线程（R16 事件循环不阻塞）。"""

    def _setup(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("queue_structure_test")
        return log_dir

    def test_file_handlers_not_on_calling_thread(self, tmp_path):
        """结构断言：RotatingFileHandler 不在 root（调用线程）上，写盘经 QueueHandler 委托后台线程。"""
        import threading

        from logging.handlers import QueueHandler

        import utils.logger as _ul

        self._setup(tmp_path)
        try:
            root_handlers = logging.getLogger().handlers
            assert not any(isinstance(h, RotatingFileHandler) for h in root_handlers)
            assert any(isinstance(h, QueueHandler) for h in root_handlers)
            assert len(_listener_file_handlers()) == 3  # app / error / latest 均挂在 listener
            assert _ul._LISTENER is not None
            assert _ul._LISTENER._thread is not threading.current_thread()
        finally:
            from utils.logger import stop_logging

            stop_logging()
        assert _ul._LISTENER is None  # 收尾后无残留 listener 线程

    def test_large_volume_non_blocking_and_flushed(self, tmp_path):
        """调用线程只 put 队列：大批量日志快速返回，flush 后全部落盘。"""
        self._setup(tmp_path)
        try:
            logger = get_logger()
            for i in range(400):
                logger.info("bulk_line_%d", i)
            flush_logging()
            content = (tmp_path / "test_logs" / "app.log").read_text(encoding="utf-8")
            for i in range(400):
                assert f"bulk_line_{i}" in content
        finally:
            from utils.logger import stop_logging

            stop_logging()


class TestQueueSanitization:
    """OSS G1：经 QueueHandler 链路写盘后脱敏仍生效（R9）。"""

    def test_queue_path_sanitizes_exception(self, tmp_path):
        import json

        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_format", return_value="json"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("queue_sanitize_test")
            logger = get_logger()
            try:
                raise ValueError("secret_value_boom")
            except ValueError:
                logger.exception("boom_message")
        flush_logging()

        content = (tmp_path / "test_logs" / "app.log").read_text(encoding="utf-8")
        line = next((sl for sl in content.splitlines() if "boom_message" in sl), None)
        assert line is not None
        data = json.loads(line)
        assert data["message"] == "boom_message"
        # 异常内容被保留
        assert "secret_value_boom" in data["exception"]
        # 脱敏生效：traceback 中的绝对路径已被替换为 <PATH>
        assert "<PATH>" in data["exception"]
        # 真实绝对路径不应以原始形态暴露
        assert os.path.dirname(os.path.abspath(__file__)) not in content

    def test_stop_drains_queued_records(self, tmp_path):
        """stop_logging 时队列中遗留记录被排空写盘，不残留队列任务（R7）。"""
        log_dir = str(tmp_path / "test_logs")
        with (
            patch("utils.logger.LOG_DIR", log_dir),
            patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
            patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
            patch(
                "utils.config_handler.ConfigHandler.get_log_backup_count",
                return_value=5,
            ),
        ):
            setup_logging("drain_test")
            logger = get_logger()
            logger.info("drain_marker_888")
            # 不 flush，直接 stop：stop 内部经 sentinel 排空队列
            from utils.logger import stop_logging

            stop_logging()

        content = (tmp_path / "test_logs" / "app.log").read_text(encoding="utf-8")
        assert "drain_marker_888" in content
        assert not _listener_file_handlers()  # listener 已回收，QueueHandler 已从 root 移除


class TestLoggingListenerLifecycle:
    """OSS G1：listener 线程生命周期正确，多次 configure + stop 无线程泄漏（R7）。"""

    def test_repeated_setup_stop_no_thread_leak(self, tmp_path):
        from logging.handlers import QueueHandler

        import utils.logger as _ul
        from utils.logger import stop_logging

        for i in range(5):
            log_dir = str(tmp_path / f"run{i}")
            with (
                patch("utils.logger.LOG_DIR", log_dir),
                patch("utils.config_handler.ConfigHandler.get_log_level", return_value="INFO"),
                patch("utils.config_handler.ConfigHandler.get_log_max_mb", return_value=5),
                patch(
                    "utils.config_handler.ConfigHandler.get_log_backup_count",
                    return_value=5,
                ),
            ):
                setup_logging(f"lifecycle_{i}")
            assert _ul._LISTENER is not None
            thread = _ul._LISTENER._thread
            assert thread is not None and thread.is_alive()

            get_logger().info(f"cycle_{i}_marker")
            stop_logging()

            assert not thread.is_alive()  # 线程已 join，无悬挂监听线程
            assert _ul._LISTENER is None
            assert not any(isinstance(h, QueueHandler) for h in logging.getLogger().handlers)
