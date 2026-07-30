# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import json
import threading

import pytest

from utils.error_classifier import classify_error, classify_severity, get_error_message
from utils.time_utils import get_now

pytestmark = pytest.mark.unit


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

    def test_orphaned_revision(self):
        result = classify_error(Exception("Can't locate revision identified by '0004'"), context="db")
        assert result["code"] == "orphaned_revision"
        assert result["message_key"] == "db_err_orphaned_revision"
        assert result["format_args"] == {"error": "Can't locate revision identified by '0004'"}

    # P3-M5-ClassifyError-System-Gap: 扩展 interrupted code 识别 asyncpg/SQLAlchemy
    # 连接断开异常字符串（原 base_dao.py 手写字符串匹配移除）
    def test_interrupted_no_active_connection(self):
        result = classify_error(Exception("no active connection"), context="db")
        assert result["code"] == "interrupted"
        assert result["message_key"] == "db_err_interrupted"

    def test_interrupted_database_is_closed(self):
        result = classify_error(Exception("database is closed"), context="db")
        assert result["code"] == "interrupted"
        assert result["message_key"] == "db_err_interrupted"

    def test_interrupted_connection_does_not_exist(self):
        result = classify_error(Exception("ConnectionDoesNotExistError"), context="db")
        assert result["code"] == "interrupted"
        assert result["message_key"] == "db_err_interrupted"

    def test_interrupted_closed_in_middle(self):
        result = classify_error(Exception("closed in the middle of operation"), context="db")
        assert result["code"] == "interrupted"
        assert result["message_key"] == "db_err_interrupted"

    # P3-M5-ClassifyError-System-Gap: 扩展 not_found code 识别 "no such table"
    def test_not_found_no_such_table(self):
        result = classify_error(Exception("no such table: stock_basic"), context="db")
        assert result["code"] == "not_found"

    def test_embedded_postgres_sidecar_not_found(self):
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        exc = EmbeddedPostgresStartError("sidecar binary not found: sidecars\\qtrading-pg-sidecar.exe")
        result = classify_error(exc, context="db")
        assert result["code"] == "sidecar_not_found"
        assert result["message_key"] == "db_err_sidecar_not_found"
        assert result["format_args"]["path"] == "sidecars\\qtrading-pg-sidecar.exe"

    def test_embedded_postgres_sidecar_not_executable(self):
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        exc = EmbeddedPostgresStartError("sidecar binary not executable: sidecars\\qtrading-pg-sidecar.exe")
        result = classify_error(exc, context="db")
        assert result["code"] == "sidecar_not_executable"
        assert result["message_key"] == "db_err_sidecar_not_executable"
        assert result["format_args"]["path"] == "sidecars\\qtrading-pg-sidecar.exe"

    def test_embedded_postgres_sha256_mismatch(self):
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        exc = EmbeddedPostgresStartError("sidecar binary sha256 mismatch")
        result = classify_error(exc, context="db")
        assert result["code"] == "sha256_mismatch"
        assert result["message_key"] == "db_err_sidecar_sha256_mismatch"

    def test_embedded_postgres_exit_codes(self):
        from data.persistence.embedded_postgres.service import EmbeddedPostgresStartError

        assert (
            classify_error(EmbeddedPostgresStartError("initdb failed (exit=11)"), context="db")["code"]
            == "initdb_failed"
        )
        assert classify_error(EmbeddedPostgresStartError("disk full (exit=15)"), context="db")["code"] == "disk_space"
        assert (
            classify_error(EmbeddedPostgresStartError("password error (exit=16)"), context="db")["code"]
            == "password_error"
        )
        assert (
            classify_error(EmbeddedPostgresStartError("qTrading already running (exit=50)"), context="db")["code"]
            == "already_running"
        )
        assert (
            classify_error(EmbeddedPostgresStartError("sidecar crashed (exit=60)"), context="db")["code"] == "crashed"
        )


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


class TestClassifyErrorSystemGapIntegration:
    """P3-M5-ClassifyError-System-Gap: 验证 cache_manager/base_dao 接入分类器后的关键路径。

    覆盖 DoD ③ 三级分类断言：
    - system 级 → raise（EngineDisposedError 转换路径）
    - recoverable 级 → logger.warning（降级路径）
    - operational 级 → logger.debug（业务降级路径）
    """

    def test_system_level_memory_error_classified_as_system(self):
        """system 级：MemoryError 必须归为 system，触发 raise 路径。"""
        assert classify_severity(MemoryError("out of memory"), "db") == "system"

    def test_system_level_connection_interrupted_classified_for_engine_disposed(self):
        """system 级等价路径：连接中断字符串识别为 interrupted code，触发 EngineDisposedError 转换。"""
        for err_msg in [
            "no active connection",
            "database is closed",
            "ConnectionDoesNotExistError",
            "closed in the middle of operation",
            "connection was closed",
        ]:
            result = classify_error(Exception(err_msg), "db")
            assert result["code"] == "interrupted", f"Failed for: {err_msg}"

    def test_recoverable_level_timeout_classified_as_recoverable(self):
        """recoverable 级：timeout 归为 recoverable，触发 logger.warning 降级。"""
        assert classify_severity(TimeoutError("db timeout"), "db") == "recoverable"

    def test_recoverable_level_connection_refused_classified_as_recoverable(self):
        """recoverable 级：connection refused 归为 recoverable（网络层可重试）。"""
        assert classify_severity(Exception("connection refused"), "db") == "recoverable"

    def test_operational_level_value_error_classified_as_operational(self):
        """operational 级：ValueError 归为 operational，触发 logger.debug 业务降级。"""
        assert classify_severity(ValueError("invalid format"), "db") == "operational"

    def test_operational_level_not_found_classified_for_table_missing(self):
        """operational 级等价路径：'no such table' 识别为 not_found code，cache_manager 走 warning 降级。"""
        result = classify_error(Exception("no such table: stock_basic"), "db")
        assert result["code"] == "not_found"


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
        task._cancel_event = threading.Event()
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
        task._cancel_event = threading.Event()
        task._coroutine_gen = lambda: self._raise_operational()

        tm._tasks[task.id] = task

        with caplog.at_level(logging.ERROR, logger="services.task_manager"):
            await tm._task_runner(task.id)

        assert task.status == TaskStatus.FAILED
        assert any("operational" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_task_manager_running_log_is_info(self, caplog):
        import logging
        from services.task_manager import TaskManager, AppTask, TaskStatus

        tm = TaskManager()
        tm._initialized = True
        tm._tasks = {}
        tm._subscribers = []
        tm._background_tasks = set()
        tm._db_ready = False

        task = AppTask(
            name="test_running_info_log",
            task_type="System",
            cancellable=True,
        )
        task.status = TaskStatus.RUNNING
        task.started_at = get_now()
        task._cancel_event = threading.Event()

        async def dummy_coro():
            return "ok"

        task._coroutine_gen = dummy_coro

        tm._tasks[task.id] = task

        with caplog.at_level(logging.INFO, logger="services.task_manager"):
            await tm._task_runner(task.id)

        assert task.status == TaskStatus.COMPLETED
        # Verify there is an INFO log starting with "[TaskManager] Running:"
        running_logs = [r for r in caplog.records if r.levelno == logging.INFO and "Running:" in r.message]
        assert len(running_logs) == 1
        assert "[TaskManager] Running:" in running_logs[0].message

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


class TestClassifyErrorBackwardCompat:
    """ui.i18n re-export 契约测试（合并自 tests/unit/test_onboarding_wizard.py）。"""

    def test_re_export_from_ui_i18n(self):
        from ui.i18n import classify_error as ce_from_i18n
        from utils.error_classifier import classify_error as ce_from_utils

        assert ce_from_i18n is ce_from_utils, "ui.i18n.classify_error must re-export from utils.error_classifier"

    def test_re_export_functional(self):
        from ui.i18n import classify_error

        e = FileNotFoundError("test")
        result = classify_error(e)
        assert result["code"] == "file_not_found"


class TestClassifySeverityTusharePermission:
    """TushareAPIPermissionError 应归为 recoverable，避免 token 失效刷屏 ERROR + traceback。
    用动态创建同名异常类测试，避免 utils 测试反向依赖 data 层（R1 架构边界）。"""

    def test_tushare_api_permission_error_is_recoverable(self):
        # 动态创建类名为 TushareAPIPermissionError 的异常，模拟真实异常的 type 匹配
        TushareAPIPermissionError = type("TushareAPIPermissionError", (Exception,), {})
        e = TushareAPIPermissionError("Token marked invalid")
        assert classify_severity(e) == "recoverable"

    def test_tushare_api_permission_error_in_any_context(self):
        # 无论何种 context，token 熔断都应识别为 recoverable
        TushareAPIPermissionError = type("TushareAPIPermissionError", (Exception,), {})
        for ctx in ("general", "token", "llm", "db", "chart"):
            e = TushareAPIPermissionError("test")
            assert classify_severity(e, context=ctx) == "recoverable", f"context={ctx} 应识别为 recoverable"

    def test_other_permission_named_exception_not_affected(self):
        # 验证不会误伤其他名称相似的异常
        OtherPermissionError = type("OtherPermissionError", (Exception,), {})
        assert classify_severity(OtherPermissionError("test")) == "operational"

    def test_builtin_permission_error_still_system(self):
        # 内置 PermissionError（文件系统权限）仍归为 system，不受影响
        assert classify_severity(PermissionError("access denied")) == "system"
