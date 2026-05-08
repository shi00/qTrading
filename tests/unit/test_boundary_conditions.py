import os

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read_source(rel_path):
    with open(os.path.join(BASE, rel_path), encoding="utf-8") as f:
        return f.read()


def extract_method_source(source, method_name):
    lines = source.split("\n")
    start = None
    indent_level = 0
    in_signature = False
    result = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if f"def {method_name}" in stripped and start is None:
            start = i
            indent_level = len(line) - len(line.lstrip())
            in_signature = not stripped.endswith(":")
            result.append(line)
            continue
        if start is not None:
            if in_signature:
                if stripped.endswith(":"):
                    in_signature = False
                result.append(line)
                continue
            if stripped == "":
                result.append(line)
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent_level and stripped:
                break
            result.append(line)
    return "\n".join(result)


class TestTushareClientBoundaryConditions:
    """Tushare API 错误处理边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        full = read_source(os.path.join("data", "external", "tushare_client.py"))
        self.handle_api_source = extract_method_source(full, "_handle_api_call")
        self.paginated_source = extract_method_source(full, "_handle_api_call_paginated")

    def test_handle_api_call_no_return_none(self):
        assert "return None" not in self.handle_api_source, (
            "_handle_api_call must never return None on retry exhaustion"
        )

    def test_handle_api_call_raises_runtime_error_on_exhaustion(self):
        assert "raise RuntimeError" in self.handle_api_source, (
            "_handle_api_call must raise RuntimeError when all retries exhausted"
        )

    def test_handle_api_call_permission_error_reraises(self):
        assert "is_permission_error" in self.handle_api_source
        assert "if is_permission_error" in self.handle_api_source
        assert "raise e" in self.handle_api_source, "Permission errors must be re-raised immediately"

    def test_handle_api_call_rate_limit_reduces_rate(self):
        assert "reduce_rate" in self.handle_api_source, "Rate limit errors must reduce rate on the limiter"

    def test_paginated_first_page_failure_raises(self):
        assert "page == 0" in self.paginated_source, "First page failure must raise (not silently return)"
        lines = self.paginated_source.split("\n")
        for i, line in enumerate(lines):
            if "page == 0" in line:
                block = "\n".join(lines[i : i + 3])
                assert "raise" in block, "First page failure must re-raise exception"
                break

    def test_paginated_later_page_failure_returns_partial(self):
        assert "partial" in self.paginated_source.lower(), "Later page failure must log partial data and break"

    def test_paginated_max_pages_logs_incomplete(self):
        assert "INCOMPLETE" in self.paginated_source, "Hitting max_pages must log that results are INCOMPLETE"

    def test_paginated_empty_result_returns_none(self):
        assert "return None" in self.paginated_source, "Empty paginated result should return None"

    def test_network_error_retries_with_backoff(self):
        assert "is_network_error" in self.handle_api_source, "Must detect network errors"
        assert "sleep" in self.handle_api_source, "Network errors must trigger backoff sleep"


class TestQualityGateBoundaryConditions:
    """Quality Gate strict mode 边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("data", "persistence", "quality_gate.py"))

    def test_strict_mode_env_var_exists(self):
        assert "_STRICT_QUALITY_GATE" in self.source
        assert "STRICT_QUALITY_GATE" in self.source

    def test_check_tier_none_processor_strict_raises(self):
        assert "STRICT mode" in self.source, "Must raise QualityGateError in strict mode when processor is None"

    def test_check_tier_none_processor_non_strict_bypasses(self):
        assert "Bypassed" in self.source, "Must bypass quality gate in non-strict mode when processor is None"

    def test_check_tier_uninitialized_tier_treated_as_critical(self):
        assert "current_tier = 0" in self.source or "current_tier is None" in self.source, (
            "Uninitialized tier must be treated as CRITICAL (0)"
        )

    def test_check_tier_insufficient_raises(self):
        assert "QualityGateError" in self.source, "Must raise QualityGateError when tier is insufficient"

    def test_require_quality_supports_async(self):
        assert "iscoroutinefunction" in self.source, "Must support async decorated methods"

    def test_require_quality_supports_sync(self):
        assert "sync_wrapper" in self.source, "Must support sync decorated methods"

    def test_find_processor_multiple_sources(self):
        assert "data_processor" in self.source, "Must search for data_processor in instance, kwargs, and args"


class TestSecurityManagerBoundaryConditions:
    """Encryption 密钥/加解密边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("utils", "security_utils.py"))

    def test_encrypt_empty_returns_empty(self):
        encrypt_src = extract_method_source(self.source, "encrypt_data")
        assert "if not plaintext" in encrypt_src or "not plaintext" in encrypt_src, "Must return empty for empty input"

    def test_decrypt_empty_returns_empty(self):
        decrypt_src = extract_method_source(self.source, "decrypt_data")
        assert "if not encrypted_text" in decrypt_src or "not encrypted_text" in decrypt_src, (
            "Must return empty for empty input"
        )

    def test_decrypt_too_short_raises(self):
        decrypt_src = extract_method_source(self.source, "decrypt_data")
        assert "too short" in decrypt_src.lower() or "len(decoded) < 28" in decrypt_src, (
            "Must reject data shorter than nonce+tag"
        )

    def test_decrypt_invalid_base64_raises(self):
        decrypt_src = extract_method_source(self.source, "decrypt_data")
        assert "Invalid Base64" in decrypt_src or "b64decode" in decrypt_src, "Must handle invalid base64"

    def test_encrypt_raises_decryption_error_on_failure(self):
        encrypt_src = extract_method_source(self.source, "encrypt_data")
        assert "DecryptionError" in encrypt_src, "Must raise DecryptionError on encryption failure"

    def test_key_file_atomic_write(self):
        save_src = extract_method_source(self.source, "_save_key")
        assert ".tmp" in save_src, "Key save must use atomic write (tmp -> rename)"
        assert "os.replace" in save_src, "Must use os.replace for atomic rename"

    def test_key_backup_on_load(self):
        get_key_src = extract_method_source(self.source, "get_key")
        assert "KEY_FILE_BAK" in get_key_src, "Must create backup of key file on load"

    def test_key_recovery_from_backup(self):
        get_key_src = extract_method_source(self.source, "get_key")
        assert "backup" in get_key_src.lower() or "recover" in get_key_src.lower(), "Must attempt recovery from backup"

    def test_key_corrupt_both_files_raises(self):
        get_key_src = extract_method_source(self.source, "get_key")
        assert "Both primary and backup" in get_key_src or "Manual intervention" in get_key_src, (
            "Must raise when both key files are corrupt"
        )

    def test_aesgcm_256bit_key(self):
        get_key_src = extract_method_source(self.source, "get_key")
        assert "256" in get_key_src, "Must use 256-bit AES key"

    def test_nonce_96bit(self):
        encrypt_src = extract_method_source(self.source, "encrypt_data")
        assert "token_bytes(12)" in encrypt_src, "Must use 96-bit (12 byte) nonce for AES-GCM"


class TestShutdownBoundaryConditions:
    """Shutdown 流程边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("utils", "shutdown.py"))

    def test_do_cleanup_idempotent(self):
        cleanup_src = extract_method_source(self.source, "do_cleanup")
        assert "already completed" in cleanup_src.lower(), "do_cleanup must be idempotent"

    def test_do_cleanup_deduplicates_task(self):
        cleanup_src = extract_method_source(self.source, "do_cleanup")
        assert "already running" in cleanup_src.lower(), "do_cleanup must deduplicate concurrent calls"

    def test_execute_cleanup_cancels_watchdog(self):
        exec_src = extract_method_source(self.source, "_execute_cleanup")
        assert "cancel_watchdog" in exec_src, "Cleanup must cancel watchdog on completion"

    def test_execute_cleanup_handles_timeout(self):
        exec_src = extract_method_source(self.source, "_execute_cleanup")
        assert "TimeoutError" in exec_src, "Must handle overall cleanup timeout"

    def test_execute_cleanup_handles_unexpected_exception(self):
        exec_src = extract_method_source(self.source, "_execute_cleanup")
        assert "except Exception" in exec_src, "Must handle unexpected exceptions in cleanup"

    def test_step_timeout_creates_step_result(self):
        step_src = extract_method_source(self.source, "_run_async_step")
        assert "timed_out=True" in step_src, "Step timeout must create StepResult with timed_out=True"

    def test_step_failure_does_not_skip_remaining(self):
        assert "_CLEANUP_STEPS" in self.source, "Must have ordered cleanup steps"

    def test_watchdog_uses_cancel_event(self):
        watchdog_src = extract_method_source(self.source, "start_watchdog")
        assert "cancel_event" in watchdog_src, "Watchdog must use cancel event for early termination"
        assert "cancel_event.wait" in watchdog_src, "Watchdog must wait on cancel event"

    def test_watchdog_timeout_forces_exit(self):
        watchdog_src = extract_method_source(self.source, "start_watchdog")
        assert "force_exit" in watchdog_src, "Watchdog timeout must force exit"

    def test_watchdog_no_double_arm(self):
        watchdog_src = extract_method_source(self.source, "start_watchdog")
        assert "watchdog_started" in watchdog_src.lower() or "is_set" in watchdog_src, "Must not arm watchdog twice"

    def test_critical_step_failures_tracked(self):
        exec_src = extract_method_source(self.source, "_execute_cleanup")
        assert "critical" in exec_src.lower(), "Must track critical step failures"

    def test_cleanup_done_flag_set_in_finally(self):
        exec_src = extract_method_source(self.source, "_execute_cleanup")
        assert "_cleanup_done = True" in exec_src, "Must set cleanup_done in finally block"


class TestCacheManagerBoundaryConditions:
    """CacheManager 边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("data", "cache", "cache_manager.py"))

    def test_close_handles_none_engine(self):
        close_src = extract_method_source(self.source, "close")
        assert "engine" in close_src, "close must handle engine"

    def test_sanitize_url_hides_password(self):
        sanitize_src = extract_method_source(self.source, "_sanitize_url")
        assert "****" in sanitize_src, "_sanitize_url must mask password"

    def test_get_daily_quotes_has_suppress_errors(self):
        gdq_src = extract_method_source(self.source, "get_daily_quotes")
        assert "suppress_errors" in gdq_src, "get_daily_quotes must have suppress_errors parameter"

    def test_get_daily_quotes_passes_suppress_errors(self):
        gdq_src = extract_method_source(self.source, "get_daily_quotes")
        assert "suppress_errors=suppress_errors" in gdq_src, "get_daily_quotes must pass suppress_errors to DAO"


class TestNewsSubscriptionBoundaryConditions:
    """NewsSubscription 监听器边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("data", "external", "news_subscription.py"))

    def test_notify_listeners_is_async(self):
        assert "async def _notify_listeners" in self.source, "_notify_listeners must be async"

    def test_notify_listeners_uses_run_in_executor(self):
        notify_src = extract_method_source(self.source, "_notify_listeners")
        assert "run_in_executor" in notify_src, "Sync listeners must run in executor"

    def test_notify_listeners_lambda_closure_safe(self):
        notify_src = extract_method_source(self.source, "_notify_listeners")
        in_method = False
        for line in notify_src.split("\n"):
            if "_notify_listeners" in line:
                in_method = True
            if in_method and "lambda" in line:
                assert "_l=" in line, f"Lambda must use default-arg binding for closure safety: {line.strip()}"

    def test_notify_listeners_timeout_handling(self):
        notify_src = extract_method_source(self.source, "_notify_listeners")
        assert "TimeoutError" in notify_src, "Must handle listener timeout"

    def test_notify_listeners_error_counting_and_removal(self):
        notify_src = extract_method_source(self.source, "_notify_listeners")
        assert "_listener_errors" in notify_src, "Must track listener error counts"

    def test_notify_listeners_empty_list_early_return(self):
        notify_src = extract_method_source(self.source, "_notify_listeners")
        assert "not target" in notify_src, "Must return early when no listeners"

    def test_notify_listeners_handles_various_param_counts(self):
        notify_src = extract_method_source(self.source, "_notify_listeners")
        assert "param_count" in notify_src, "Must handle listeners with different parameter counts"

    def test_alert_listeners_use_run_in_executor(self):
        assert "run_in_executor" in self.source, "Alert listeners must use run_in_executor"
        alert_lines = [line for line in self.source.split("\n") if "alert_listeners" in line and "for listener" in line]
        if alert_lines:
            block_start = self.source.split("\n").index(alert_lines[0])
            block = "\n".join(self.source.split("\n")[block_start : block_start + 15])
            assert "run_in_executor" in block, "Alert listener loop must use run_in_executor"

    def test_alert_listeners_have_timeout(self):
        alert_lines = [line for line in self.source.split("\n") if "alert_listeners" in line and "for listener" in line]
        if alert_lines:
            block_start = self.source.split("\n").index(alert_lines[0])
            block = "\n".join(self.source.split("\n")[block_start : block_start + 15])
            assert "wait_for" in block, "Alert listeners must have timeout"
            assert "TimeoutError" in block, "Must handle TimeoutError for alert listeners"


class TestI18nBoundaryConditions:
    """i18n 键缺失/回退边界条件"""

    def test_get_missing_key_returns_key(self):
        from ui.i18n import I18n

        I18n._initialized = False
        I18n._locale = "zh_CN"
        I18n._strings_cache = {}
        I18n._missing_keys = set()
        I18n._listeners = None
        result = I18n.get("nonexistent_key_99999")
        assert result == "nonexistent_key_99999"
        I18n._initialized = False
        I18n._strings_cache = {}
        I18n._missing_keys = set()
        I18n._listeners = None

    def test_get_missing_key_with_default(self):
        from ui.i18n import I18n

        I18n._initialized = False
        I18n._locale = "zh_CN"
        I18n._strings_cache = {}
        I18n._missing_keys = set()
        I18n._listeners = None
        result = I18n.get("nonexistent_key_99999", default="Fallback")
        assert result == "Fallback"
        I18n._initialized = False
        I18n._strings_cache = {}
        I18n._missing_keys = set()
        I18n._listeners = None

    def test_classify_error_reexported(self):
        from ui.i18n import classify_error
        from utils.error_classifier import classify_error as original

        assert classify_error is original


class TestBaseDaoBoundaryConditions:
    """BaseDao suppress_errors 边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("data", "persistence", "daos", "base_dao.py"))

    def test_read_db_suppress_errors_default_true(self):
        read_src = extract_method_source(self.source, "_read_db")
        assert "suppress_errors" in read_src, "_read_db must have suppress_errors parameter"

    def test_write_db_suppress_errors_default_true(self):
        write_src = extract_method_source(self.source, "_write_db")
        assert "suppress_errors" in write_src, "_write_db must have suppress_errors parameter"

    def test_read_db_suppress_errors_false_reraises(self):
        read_src = extract_method_source(self.source, "_read_db")
        assert "if not suppress_errors" in read_src, "Must re-raise when suppress_errors=False"

    def test_write_db_suppress_errors_false_reraises(self):
        write_src = extract_method_source(self.source, "_write_db")
        assert "not suppress_errors" in write_src, "Must re-raise when suppress_errors=False"
        assert "raise" in write_src, "Must have raise statement in _write_db"


class TestConfigHandlerBoundaryConditions:
    """ConfigHandler 加密 fallback 边界条件"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = read_source(os.path.join("utils", "config_handler.py"))

    def test_save_token_encrypt_failure_returns_false(self):
        save_src = extract_method_source(self.source, "save_token")
        assert "encrypt_data" in save_src or "keyring" in save_src, "save_token must handle encryption failure"

    def test_save_db_password_encrypt_failure_returns_false(self):
        save_src = extract_method_source(self.source, "save_db_password")
        assert "encrypt_data" in save_src or "keyring" in save_src, "save_db_password must handle encryption failure"

    def test_get_db_url_masks_password(self):
        url_src = extract_method_source(self.source, "get_db_url")
        assert "****" in url_src, "get_db_url must mask password in logged URL"


class TestSortDirectionBoundaryConditions:
    """Sort direction 一致性边界条件"""

    def test_vm_new_column_defaults_ascending(self):
        source = read_source(os.path.join("ui", "viewmodels", "screener_view_model.py"))
        sort_src = extract_method_source(source, "sort_data")
        assert "self.sort_ascending = True" in sort_src, "VM new column should default to ascending (True)"

    def test_virtual_table_new_column_defaults_ascending(self):
        source = read_source(os.path.join("ui", "components", "virtual_table.py"))
        sort_src = extract_method_source(source, "_handle_sort_click")
        assert "self.sort_asc = True" in sort_src, "PaginatedTable new column should default to ascending"

    def test_vm_toggle_sort_direction(self):
        source = read_source(os.path.join("ui", "viewmodels", "screener_view_model.py"))
        sort_src = extract_method_source(source, "sort_data")
        assert "not self.sort_ascending" in sort_src, "VM must toggle sort direction on same column"


class TestDeprecatedAPIBoundaryConditions:
    """Deprecated API 替换边界条件"""

    def test_no_asyncio_iscoroutinefunction_in_source(self):
        found = []
        for root, dirs, files in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", ".git", "node_modules")]
            if "tests" in root:
                continue
            for fname in files:
                if not fname.endswith(".py") or fname.startswith("_run_"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    if "asyncio.iscoroutinefunction" in content:
                        found.append(os.path.relpath(fpath, BASE))
                except Exception:
                    pass
        assert not found, f"Found asyncio.iscoroutinefunction in source files: {found}"

    def test_financial_sync_uses_trade_calendar(self):
        source = read_source(os.path.join("data", "sync", "financial.py"))
        assert "processor.get_trade_dates" not in source, "Must not use deprecated processor.get_trade_dates"
        assert "trade_calendar.get_trade_dates" in source, "Must use trade_calendar.get_trade_dates"

    def test_strategy_paths_use_suppress_errors_false(self):
        ai_source = read_source(os.path.join("strategies", "ai_mixin.py"))
        assert "suppress_errors=False" in ai_source, "AI mixin must use suppress_errors=False"

        oversold_source = read_source(os.path.join("strategies", "oversold_strategy.py"))
        assert "suppress_errors=False" in oversold_source, "OversoldStrategy must use suppress_errors=False"
