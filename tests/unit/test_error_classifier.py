import json
from unittest.mock import patch

from utils.error_classifier import classify_error, classify_severity


class TestClassifyErrorTokenContext:
    @patch("ui.i18n.I18n")
    def test_invalid_token(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "Token无效"
        result = classify_error(Exception("invalid token not set"), context="token")
        assert result["code"] == "invalid"

    @patch("ui.i18n.I18n")
    def test_token_timeout(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "超时"
        result = classify_error(Exception("request timed out"), context="token")
        assert result["code"] == "timeout"

    @patch("ui.i18n.I18n")
    def test_token_network(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "网络错误"
        result = classify_error(Exception("connection refused"), context="token")
        assert result["code"] == "network"

    @patch("ui.i18n.I18n")
    def test_token_server_chinese(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "服务器错误"
        result = classify_error(Exception("抱歉，每分钟限制"), context="token")
        assert result["code"] == "server"

    @patch("ui.i18n.I18n")
    def test_token_unknown(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "未知错误"
        result = classify_error(Exception("something unexpected"), context="token")
        assert result["code"] == "unknown"


class TestClassifyErrorLLMContext:
    @patch("ui.i18n.I18n")
    def test_auth_failed_401(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "认证失败"
        result = classify_error(Exception("401 unauthorized"), context="llm")
        assert result["code"] == "auth_failed"

    @patch("ui.i18n.I18n")
    def test_forbidden_403(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "禁止访问"
        result = classify_error(Exception("403 forbidden"), context="llm")
        assert result["code"] == "forbidden"

    @patch("ui.i18n.I18n")
    def test_not_found_404(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "未找到"
        result = classify_error(Exception("404 not found"), context="llm")
        assert result["code"] == "not_found"

    @patch("ui.i18n.I18n")
    def test_rate_limit_429(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "限流"
        result = classify_error(Exception("429 too many requests"), context="llm")
        assert result["code"] == "rate_limit"

    @patch("ui.i18n.I18n")
    def test_server_error_500(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "服务器错误"
        result = classify_error(Exception("502 bad gateway"), context="llm")
        assert result["code"] == "server_error"

    @patch("ui.i18n.I18n")
    def test_timeout(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "超时"
        result = classify_error(Exception("request timed out"), context="llm")
        assert result["code"] == "timeout"

    @patch("ui.i18n.I18n")
    def test_network(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "网络错误"
        result = classify_error(Exception("connection refused"), context="llm")
        assert result["code"] == "network"

    @patch("ui.i18n.I18n")
    def test_dns(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "DNS错误"
        result = classify_error(Exception("getaddrinfo failed"), context="llm")
        assert result["code"] == "dns"

    @patch("ui.i18n.I18n")
    def test_ssl(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "SSL错误"
        result = classify_error(Exception("ssl certificate verify failed"), context="llm")
        assert result["code"] == "ssl"

    @patch("ui.i18n.I18n")
    def test_model_not_found(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "模型未找到"
        result = classify_error(Exception("model unsupported in api"), context="llm")
        assert result["code"] == "model_not_found"

    @patch("ui.i18n.I18n")
    def test_unknown(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "未知错误"
        result = classify_error(Exception("something weird"), context="llm")
        assert result["code"] == "unknown"


class TestClassifyErrorDBContext:
    @patch("ui.i18n.I18n")
    def test_value_error_format(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "格式错误: {error}"
        result = classify_error(ValueError("bad format"), context="db")
        assert result["code"] == "format"

    @patch("ui.i18n.I18n")
    def test_auth_password(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "认证失败"
        result = classify_error(Exception("authentication failed for password"), context="db")
        assert result["code"] == "auth"

    @patch("ui.i18n.I18n")
    def test_timeout(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "超时"
        result = classify_error(Exception("timeout waiting for db"), context="db")
        assert result["code"] == "timeout"

    @patch("ui.i18n.I18n")
    def test_refused(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "连接被拒"
        result = classify_error(Exception("connection refused"), context="db")
        assert result["code"] == "refused"

    @patch("ui.i18n.I18n")
    def test_unknown(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "未知错误"
        result = classify_error(Exception("unexpected db error"), context="db")
        assert result["code"] == "unknown"


class TestClassifyErrorChartContext:
    @patch("ui.i18n.I18n")
    def test_timeout(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "图表超时"
        result = classify_error(Exception("chart timed out"), context="chart")
        assert result["code"] == "timeout"

    @patch("ui.i18n.I18n")
    def test_network(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "图表网络错误"
        result = classify_error(Exception("network error"), context="chart")
        assert result["code"] == "network"

    @patch("ui.i18n.I18n")
    def test_data_empty(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "图表数据错误"
        result = classify_error(Exception("data is empty"), context="chart")
        assert result["code"] == "data"

    @patch("ui.i18n.I18n")
    def test_null_data(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "图表数据错误"
        result = classify_error(Exception("null data received"), context="chart")
        assert result["code"] == "data"

    @patch("ui.i18n.I18n")
    def test_unknown(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "图表未知错误"
        result = classify_error(Exception("something went wrong"), context="chart")
        assert result["code"] == "unknown"


class TestClassifyErrorGeneralContext:
    @patch("ui.i18n.I18n")
    def test_json_decode_error(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "JSON解析错误"
        result = classify_error(json.JSONDecodeError("msg", "doc", 0), context="general")
        assert result["code"] == "json_parse"

    @patch("ui.i18n.I18n")
    def test_file_not_found(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "文件未找到"
        result = classify_error(FileNotFoundError("no such file"), context="general")
        assert result["code"] == "file_not_found"

    @patch("ui.i18n.I18n")
    def test_file_exists(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "文件已存在"
        result = classify_error(FileExistsError("file exists"), context="general")
        assert result["code"] == "file_not_found"

    @patch("ui.i18n.I18n")
    def test_permission_error(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "权限错误"
        result = classify_error(PermissionError("access denied"), context="general")
        assert result["code"] == "permission"

    @patch("ui.i18n.I18n")
    def test_oserror_disk_space(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "磁盘空间不足"
        result = classify_error(OSError("No space left on device"), context="general")
        assert result["code"] == "disk_space"

    @patch("ui.i18n.I18n")
    def test_general_timeout(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "超时"
        result = classify_error(Exception("timeout occurred"), context="general")
        assert result["code"] == "timeout"

    @patch("ui.i18n.I18n")
    def test_general_network(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "网络错误"
        result = classify_error(Exception("network failure"), context="general")
        assert result["code"] == "network"

    @patch("ui.i18n.I18n")
    def test_general_server_500(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "服务器错误"
        result = classify_error(Exception("503 service unavailable"), context="general")
        assert result["code"] == "server"

    @patch("ui.i18n.I18n")
    def test_general_unknown(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "未知错误"
        result = classify_error(Exception("something unexpected"), context="general")
        assert result["code"] == "unknown"


class TestClassifySeverityAdditional:
    def test_system_exit_is_system(self):
        result = classify_severity(SystemExit(1))
        assert result == "system"

    def test_keyboard_interrupt_is_system(self):
        result = classify_severity(KeyboardInterrupt())
        assert result == "system"

    def test_file_not_found_is_operational(self):
        result = classify_severity(FileNotFoundError("missing file"))
        assert result == "operational"

    @patch("ui.i18n.I18n")
    def test_rate_limit_exception_is_recoverable(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "限流"
        result = classify_severity(Exception("429 rate limit"), context="llm")
        assert result == "recoverable"

    @patch("ui.i18n.I18n")
    def test_server_error_is_recoverable(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "服务器错误"
        result = classify_severity(Exception("500 server error"), context="llm")
        assert result == "recoverable"

    @patch("ui.i18n.I18n")
    def test_dns_error_is_recoverable(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "DNS错误"
        result = classify_severity(Exception("dns resolution failed"), context="llm")
        assert result == "recoverable"

    @patch("ui.i18n.I18n")
    def test_ssl_error_is_recoverable(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "SSL错误"
        result = classify_severity(Exception("ssl certificate error"), context="llm")
        assert result == "recoverable"

    @patch("ui.i18n.I18n")
    def test_connection_refused_is_recoverable(self, mock_i18n_cls):
        mock_i18n_cls.get.return_value = "连接被拒"
        result = classify_severity(Exception("connection refused"), context="db")
        assert result == "recoverable"


class TestClassifySeverity:
    """S5-4: classify_severity distinguishes system vs recoverable vs operational"""

    def test_memory_error_is_system(self):
        """MemoryError should be classified as system-level"""
        from utils.error_classifier import classify_severity

        result = classify_severity(MemoryError("out of memory"))
        assert result == "system"

    def test_recursion_error_is_system(self):
        """RecursionError should be classified as system-level"""
        from utils.error_classifier import classify_severity

        result = classify_severity(RecursionError("max depth"))
        assert result == "system"

    def test_permission_error_is_system(self):
        """PermissionError should be classified as system-level"""
        from utils.error_classifier import classify_severity

        result = classify_severity(PermissionError("access denied"))
        assert result == "system"

    def test_timeout_is_recoverable(self):
        """Timeout errors should be classified as recoverable"""
        from utils.error_classifier import classify_severity

        result = classify_severity(TimeoutError("request timed out"), context="llm")
        assert result == "recoverable"

    def test_connection_error_is_recoverable(self):
        """Connection errors should be classified as recoverable"""
        from utils.error_classifier import classify_severity

        result = classify_severity(ConnectionError("connection refused"), context="db")
        assert result == "recoverable"

    def test_value_error_is_operational(self):
        """ValueError (bad input) should be classified as operational"""
        from utils.error_classifier import classify_severity

        result = classify_severity(ValueError("invalid format"), context="db")
        assert result == "operational"

    def test_rate_limit_is_recoverable(self):
        """Rate limit errors should be classified as recoverable"""
        from utils.error_classifier import classify_severity

        result = classify_severity(Exception("429 rate limit exceeded"), context="llm")
        assert result == "recoverable"

    def test_generic_exception_is_operational(self):
        """Unknown exceptions should default to operational"""
        from utils.error_classifier import classify_severity

        result = classify_severity(Exception("something went wrong"), context="general")
        assert result == "operational"

    def test_value_error_with_space_word_is_not_system(self):
        """H-1: 'space' substring on non-OSError must NOT be classified as system."""
        from utils.error_classifier import classify_severity

        assert classify_severity(ValueError("namespace conflict")) == "operational"
        assert classify_severity(RuntimeError("workspace empty")) == "operational"
        assert classify_severity(Exception("replace foo with bar")) == "operational"

    def test_oserror_disk_or_space_still_system(self):
        """Regression: real OSError with disk/space remains system."""
        from utils.error_classifier import classify_severity

        assert classify_severity(OSError("No space left on device")) == "system"
        assert classify_severity(OSError("disk full")) == "system"


class TestClassifySeverityIntegration:
    """Verify classify_severity is properly integrated in TaskManager error handling."""

    def test_task_manager_imports_classify_severity(self):
        """TaskManager should import classify_severity from utils.error_classifier."""
        import inspect
        from services.task_manager import TaskManager

        source = inspect.getsource(TaskManager)
        assert "classify_severity" in source, "TaskManager should use classify_severity for error classification"

    def test_task_manager_system_error_uses_critical_log(self):
        """TaskManager should log system-level errors with CRITICAL level."""
        import inspect
        from services.task_manager import TaskManager

        source = inspect.getsource(TaskManager)
        assert "logger.critical" in source, "TaskManager should use logger.critical for system-level errors"
        assert 'severity == "system"' in source or "severity=='system'" in source, (
            "TaskManager should check severity == 'system'"
        )

    def test_task_manager_includes_severity_in_error_log(self):
        """TaskManager should include severity level in error log messages."""
        import inspect
        from services.task_manager import TaskManager

        source = inspect.getsource(TaskManager)
        assert "severity" in source, "TaskManager should reference severity in error handling"
