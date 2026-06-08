import asyncio
import json

import pytest

from utils.error_classifier import classify_error, classify_severity, get_error_message
from utils.time_utils import get_now


class TestClassifyErrorTokenContext:
    def test_invalid_token(self):
        result = classify_error(Exception("invalid token not set"), context="token")
        assert result["code"] == "invalid"
        assert result["message_key"] == "wizard_err_token_invalid"

    def test_token_timeout(self):
        result = classify_error(Exception("request timed out"), context="token")
        assert result["code"] == "timeout"
        assert result["message_key"] == "wizard_err_token_timeout"

    def test_token_network(self):
        result = classify_error(Exception("connection refused"), context="token")
        assert result["code"] == "network"
        assert result["message_key"] == "wizard_err_token_network"

    def test_token_server_chinese(self):
        result = classify_error(Exception("抱歉，每分钟限制"), context="token")
        assert result["code"] == "server"
        assert result["message_key"] == "wizard_err_token_server"

    def test_token_unknown_falls_back_to_invalid(self):
        result = classify_error(Exception("something unexpected"), context="token")
        assert result["code"] == "invalid"
        assert result["message_key"] == "wizard_err_token_invalid"

    def test_token_http_403(self):
        result = classify_error(Exception("403 forbidden: api error"), context="token")
        assert result["code"] == "invalid"
        assert result["message_key"] == "wizard_err_token_invalid"

    def test_token_http_401(self):
        result = classify_error(Exception("401 unauthorized"), context="token")
        assert result["code"] == "invalid"
        assert result["message_key"] == "wizard_err_token_invalid"

    def test_token_chinese_auth_error(self):
        result = classify_error(Exception("权限不足，请检查token"), context="token")
        assert result["code"] == "invalid"
        assert result["message_key"] == "wizard_err_token_invalid"

    def test_token_unauthorized_keyword(self):
        result = classify_error(Exception("unauthorized access"), context="token")
        assert result["code"] == "invalid"
        assert result["message_key"] == "wizard_err_token_invalid"


class TestClassifyErrorLLMContext:
    def test_auth_failed_401(self):
        result = classify_error(Exception("401 unauthorized"), context="llm")
        assert result["code"] == "auth_failed"
        assert result["message_key"] == "llm_err_auth_failed"

    def test_forbidden_403(self):
        result = classify_error(Exception("403 forbidden"), context="llm")
        assert result["code"] == "forbidden"
        assert result["message_key"] == "llm_err_forbidden"

    def test_not_found_404(self):
        result = classify_error(Exception("404 not found"), context="llm")
        assert result["code"] == "not_found"
        assert result["message_key"] == "llm_err_not_found"

    def test_rate_limit_429(self):
        result = classify_error(Exception("429 too many requests"), context="llm")
        assert result["code"] == "rate_limit"
        assert result["message_key"] == "llm_err_rate_limit"

    def test_server_error_500(self):
        result = classify_error(Exception("502 bad gateway"), context="llm")
        assert result["code"] == "server_error"
        assert result["message_key"] == "llm_err_server"

    def test_timeout(self):
        result = classify_error(Exception("request timed out"), context="llm")
        assert result["code"] == "timeout"
        assert result["message_key"] == "llm_err_timeout"

    def test_network(self):
        result = classify_error(Exception("connection refused"), context="llm")
        assert result["code"] == "network"
        assert result["message_key"] == "llm_err_network"

    def test_dns(self):
        result = classify_error(Exception("getaddrinfo failed"), context="llm")
        assert result["code"] == "dns"
        assert result["message_key"] == "llm_err_dns"

    def test_ssl(self):
        result = classify_error(Exception("ssl certificate verify failed"), context="llm")
        assert result["code"] == "ssl"
        assert result["message_key"] == "llm_err_ssl"

    def test_model_not_found(self):
        result = classify_error(Exception("model unsupported in api"), context="llm")
        assert result["code"] == "model_not_found"
        assert result["message_key"] == "llm_err_model_not_found"

    def test_unknown(self):
        result = classify_error(Exception("something weird"), context="llm")
        assert result["code"] == "unknown"
        assert result["message_key"] == "llm_err_unknown"

    def test_should_retry_field_for_permanent_errors(self):
        result = classify_error(Exception("401 unauthorized"), context="llm")
        assert result["code"] == "auth_failed"
        assert result.get("should_retry") is False

    def test_should_retry_field_for_transient_errors(self):
        result = classify_error(Exception("429 too many requests"), context="llm")
        assert result["code"] == "rate_limit"
        assert result.get("should_retry") is True

    def test_content_policy_violation(self):
        result = classify_error(Exception("content policy violation"), context="llm")
        assert result["code"] == "content_policy"
        assert result.get("should_retry") is False

    def test_insufficient_quota(self):
        result = classify_error(Exception("insufficient_quota error"), context="llm")
        assert result["code"] == "insufficient_quota"
        assert result.get("should_retry") is False


class TestClassifyErrorDBContext:
    def test_value_error_format(self):
        result = classify_error(ValueError("bad format"), context="db")
        assert result["code"] == "format"
        assert result["message_key"] == "db_err_format"
        assert result["format_args"] == {"error": "bad format"}

    def test_auth_password(self):
        result = classify_error(Exception("authentication failed for password"), context="db")
        assert result["code"] == "auth"
        assert result["message_key"] == "db_err_auth"

    def test_timeout(self):
        result = classify_error(Exception("timeout waiting for db"), context="db")
        assert result["code"] == "timeout"
        assert result["message_key"] == "db_err_timeout"

    def test_refused(self):
        result = classify_error(Exception("connection refused"), context="db")
        assert result["code"] == "refused"
        assert result["message_key"] == "db_err_refused"

    def test_unknown(self):
        result = classify_error(Exception("unexpected db error"), context="db")
        assert result["code"] == "unknown"
        assert result["message_key"] == "db_err_unknown"


class TestClassifyErrorChartContext:
    def test_timeout(self):
        result = classify_error(Exception("chart timed out"), context="chart")
        assert result["code"] == "timeout"
        assert result["message_key"] == "detail_err_chart_timeout"

    def test_network(self):
        result = classify_error(Exception("network error"), context="chart")
        assert result["code"] == "network"
        assert result["message_key"] == "detail_err_chart_network"

    def test_data_empty(self):
        result = classify_error(Exception("data is empty"), context="chart")
        assert result["code"] == "data"
        assert result["message_key"] == "detail_err_chart_data"

    def test_null_data(self):
        result = classify_error(Exception("null data received"), context="chart")
        assert result["code"] == "data"
        assert result["message_key"] == "detail_err_chart_data"

    def test_unknown(self):
        result = classify_error(Exception("something went wrong"), context="chart")
        assert result["code"] == "unknown"
        assert result["message_key"] == "detail_err_chart_unknown"


class TestClassifyErrorGeneralContext:
    def test_json_decode_error(self):
        result = classify_error(json.JSONDecodeError("msg", "doc", 0), context="general")
        assert result["code"] == "json_parse"
        assert result["message_key"] == "common_err_json_parse"

    def test_file_not_found(self):
        result = classify_error(FileNotFoundError("no such file"), context="general")
        assert result["code"] == "file_not_found"
        assert result["message_key"] == "common_err_file_not_found"

    def test_file_exists(self):
        result = classify_error(FileExistsError("file exists"), context="general")
        assert result["code"] == "file_not_found"
        assert result["message_key"] == "common_err_file_not_found"

    def test_permission_error(self):
        result = classify_error(PermissionError("access denied"), context="general")
        assert result["code"] == "permission"
        assert result["message_key"] == "common_err_permission"

    def test_oserror_disk_space(self):
        result = classify_error(OSError("No space left on device"), context="general")
        assert result["code"] == "disk_space"
        assert result["message_key"] == "common_err_disk_space"

    def test_general_timeout(self):
        result = classify_error(Exception("timeout occurred"), context="general")
        assert result["code"] == "timeout"
        assert result["message_key"] == "common_err_timeout"

    def test_general_network(self):
        result = classify_error(Exception("network failure"), context="general")
        assert result["code"] == "network"
        assert result["message_key"] == "common_err_network"

    def test_general_server_500(self):
        result = classify_error(Exception("503 service unavailable"), context="general")
        assert result["code"] == "server"
        assert result["message_key"] == "common_err_server"

    def test_general_unknown(self):
        result = classify_error(Exception("something unexpected"), context="general")
        assert result["code"] == "unknown"
        assert result["message_key"] == "common_err_unknown"


class TestClassifyErrorNoI18nDependency:
    def test_classify_error_does_not_import_ui(self):
        import utils.error_classifier as mod

        assert "I18n" not in dir(mod), "error_classifier module should not expose I18n"
        import inspect

        members = dict(inspect.getmembers(mod.classify_error))
        assert "I18n" not in members, "classify_error should not reference I18n"

    def test_classify_error_returns_result_without_i18n(self):
        result = classify_error(Exception("invalid token"), context="token")
        assert "code" in result
        assert "message_key" in result
        assert not result.get("message_key", "").startswith("I18n")

    def test_no_ui_import_at_module_level(self):
        import utils.error_classifier as mod

        assert not hasattr(mod, "I18n"), "error_classifier should not have I18n at module level"


class TestGetErrorMessage:
    def test_translates_message_key(self):
        from unittest.mock import patch

        with patch("core.i18n.I18n.get", return_value="翻译后的消息"):
            result = get_error_message({"code": "test", "message_key": "some_key"})
            assert result == "翻译后的消息"

    def test_passes_format_args(self):
        from unittest.mock import patch

        with patch("core.i18n.I18n.get", return_value="格式化: bad") as mock_get:
            get_error_message(
                {
                    "code": "format",
                    "message_key": "db_err_format",
                    "format_args": {"error": "bad"},
                }
            )
            mock_get.assert_called_once_with("db_err_format", error="bad")

    def test_no_format_args(self):
        from unittest.mock import patch

        with patch("core.i18n.I18n.get", return_value="简单消息") as mock_get:
            get_error_message({"code": "test", "message_key": "some_key"})
            mock_get.assert_called_once_with("some_key")

    def test_default_key_when_missing(self):
        from unittest.mock import patch

        with patch("core.i18n.I18n.get", return_value="未知错误") as mock_get:
            get_error_message({"code": "test"})
            mock_get.assert_called_once_with("common_err_unknown")


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

    def test_rate_limit_exception_is_recoverable(self):
        result = classify_severity(Exception("429 rate limit"), context="llm")
        assert result == "recoverable"

    def test_server_error_is_recoverable(self):
        result = classify_severity(Exception("500 server error"), context="llm")
        assert result == "recoverable"

    def test_dns_error_is_recoverable(self):
        result = classify_severity(Exception("dns resolution failed"), context="llm")
        assert result == "recoverable"

    def test_ssl_error_is_recoverable(self):
        result = classify_severity(Exception("ssl certificate error"), context="llm")
        assert result == "recoverable"

    def test_connection_refused_is_recoverable(self):
        result = classify_severity(Exception("connection refused"), context="db")
        assert result == "recoverable"


class TestClassifySeverity:
    def test_memory_error_is_system(self):
        result = classify_severity(MemoryError("out of memory"))
        assert result == "system"

    def test_recursion_error_is_system(self):
        result = classify_severity(RecursionError("max depth"))
        assert result == "system"

    def test_permission_error_is_system(self):
        result = classify_severity(PermissionError("access denied"))
        assert result == "system"

    def test_timeout_is_recoverable(self):
        result = classify_severity(TimeoutError("request timed out"), context="llm")
        assert result == "recoverable"

    def test_connection_error_is_recoverable(self):
        result = classify_severity(ConnectionError("connection refused"), context="db")
        assert result == "recoverable"

    def test_value_error_is_operational(self):
        result = classify_severity(ValueError("invalid format"), context="db")
        assert result == "operational"

    def test_rate_limit_is_recoverable(self):
        result = classify_severity(Exception("429 rate limit exceeded"), context="llm")
        assert result == "recoverable"

    def test_generic_exception_is_operational(self):
        result = classify_severity(Exception("something went wrong"), context="general")
        assert result == "operational"

    def test_value_error_with_space_word_is_not_system(self):
        assert classify_severity(ValueError("namespace conflict")) == "operational"
        assert classify_severity(RuntimeError("workspace empty")) == "operational"
        assert classify_severity(Exception("replace foo with bar")) == "operational"

    def test_oserror_disk_or_space_still_system(self):
        assert classify_severity(OSError("No space left on device")) == "system"
        assert classify_severity(OSError("disk full")) == "system"


class TestClassifySeverityIntegration:
    def test_task_manager_imports_classify_severity(self):
        from services.task_manager import TaskManager

        assert hasattr(TaskManager, "_task_runner"), "TaskManager should have _task_runner method"

    @pytest.mark.asyncio
    async def test_task_manager_system_error_uses_critical_log(self, caplog):
        import logging

        from services.task_manager import TaskManager, AppTask, TaskStatus

        tm = TaskManager()
        tm._initialized = True
        tm._tasks = {}
        tm._subscribers = []
        tm._background_tasks = set()
        tm._db_ready = False

        task = AppTask(
            name="test_system_error",
            task_type="System",
            cancellable=True,
        )
        task.status = TaskStatus.RUNNING
        task.started_at = get_now()
        task._cancel_event = asyncio.Event()
        task._coroutine_gen = lambda: self._raise_system()

        tm._tasks[task.id] = task

        with caplog.at_level(logging.CRITICAL, logger="services.task_manager"):
            await tm._task_runner(task.id)

        assert task.status == TaskStatus.FAILED
        assert any("SYSTEM-LEVEL" in r.message for r in caplog.records if r.levelno == logging.CRITICAL)

    @pytest.mark.asyncio
    async def test_task_manager_includes_severity_in_error_log(self, caplog):
        import logging

        from services.task_manager import TaskManager, AppTask, TaskStatus

        tm = TaskManager()
        tm._initialized = True
        tm._tasks = {}
        tm._subscribers = []
        tm._background_tasks = set()
        tm._db_ready = False

        task = AppTask(
            name="test_operational_error",
            task_type="System",
            cancellable=True,
        )
        task.status = TaskStatus.RUNNING
        task.started_at = get_now()
        task._cancel_event = asyncio.Event()
        task._coroutine_gen = lambda: self._raise_operational()

        tm._tasks[task.id] = task

        with caplog.at_level(logging.ERROR, logger="services.task_manager"):
            await tm._task_runner(task.id)

        assert task.status == TaskStatus.FAILED
        assert any("operational" in r.message.lower() for r in caplog.records)

    @staticmethod
    async def _raise_system():
        raise MemoryError("out of memory")

    @staticmethod
    async def _raise_operational():
        raise ValueError("bad input")


class TestClassifyErrorDBTypeMatching:
    def test_asyncpg_invalid_password(self):
        import asyncpg

        result = classify_error(asyncpg.InvalidPasswordError("bad password"), context="db")
        assert result["code"] == "auth"
        assert result["message_key"] == "db_err_auth"

    def test_asyncpg_invalid_catalog_name(self):
        import asyncpg

        result = classify_error(asyncpg.InvalidCatalogNameError("no db"), context="db")
        assert result["code"] == "not_found"
        assert result["message_key"] == "db_err_not_found"

    def test_asyncpg_type_takes_priority_over_string(self):
        import asyncpg

        exc = asyncpg.InvalidPasswordError("bad password")
        result = classify_error(exc, context="db")
        assert result["code"] == "auth"

    def test_string_fallback_still_works_without_asyncpg_type(self):
        result = classify_error(Exception("password authentication failed"), context="db")
        assert result["code"] == "auth"
