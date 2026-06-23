"""专项测试：验证移除 {error} 占位符后 I18n 调用的兼容性。

本测试覆盖三种模式：
1. I18n.get("key", error=str(e)) — kwargs 传入 error 参数
2. I18n.get("key").format(error=...) — 链式 .format 调用
3. 非 {error} 占位符（如 {database}）仍正常工作
"""

import json
from pathlib import Path

import pytest

from ui.i18n import I18n

pytestmark = pytest.mark.unit

LOCALES_DIR = Path(__file__).parent.parent.parent / "locales"


@pytest.fixture(autouse=True)
def reset_i18n():
    I18n._initialized = False
    I18n._locale = "zh_CN"
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    I18n._listeners = None
    yield
    I18n._initialized = False
    I18n._locale = "zh_CN"
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    I18n._listeners = None


class TestNoErrorPlaceholdersInJSON:
    """验证 JSON 文件中不再有 {error} 占位符。"""

    def test_zh_cn_no_error_placeholder(self):
        with open(LOCALES_DIR / "zh_CN" / "strings.json", encoding="utf-8") as f:
            strings = json.load(f)
        error_keys = [k for k, v in strings.items() if "{error}" in v]
        assert error_keys == [], f"zh_CN 中仍有 {{error}} 占位符的 key: {error_keys}"

    def test_en_us_no_error_placeholder(self):
        with open(LOCALES_DIR / "en_US" / "strings.json", encoding="utf-8") as f:
            strings = json.load(f)
        error_keys = [k for k, v in strings.items() if "{error}" in v]
        assert error_keys == [], f"en_US 中仍有 {{error}} 占位符的 key: {error_keys}"


class TestNonErrorPlaceholdersPreserved:
    """验证非 {error} 占位符未被误删。"""

    def test_database_placeholder_preserved(self):
        I18n.initialize()
        I18n.set_locale("zh_CN")
        result = I18n.get("db_err_not_found", database="testdb")
        assert "testdb" in result

        I18n.set_locale("en_US")
        result = I18n.get("db_err_not_found", database="testdb")
        assert "testdb" in result

    def test_count_placeholder_preserved(self):
        I18n.initialize()
        I18n.set_locale("zh_CN")
        result = I18n.get("screener_done", count=42)
        assert "42" in result

        I18n.set_locale("en_US")
        result = I18n.get("screener_done", count=42)
        assert "42" in result


class TestI18nGetWithExtraErrorKwarg:
    """验证 I18n.get 传入多余的 error= kwargs 不抛异常。"""

    @pytest.mark.parametrize(
        "key",
        [
            "data_export_fail",
            "data_err_load_schema",
            "data_err_fetch",
            "data_sql_error",
            "screener_load_failed",
            "screener_filter_error",
            "screener_exec_error",
            "common_op_fail",
            "common_check_fail",
            "ds_clean_fail",
            "ds_verify_fail_fmt",
            "ds_init_fail_fmt",
            "ds_repair_fail",
            "db_err_format",
            "db_err_create_failed",
            "db_err_migration_failed",
            "wizard_err_verify_failed",
            "wizard_ai_error",
            "wizard_msg_sync_failed",
            "settings_status_verify_err",
            "settings_snack_ai_error",
            "settings_snack_token_fail",
            "settings_diagnostics_failed",
            "sys_init_failed",
            "sys_snack_save_err",
            "detail_err_load_chart",
            "data_sys_error",
            "backtest_failed",
        ],
    )
    def test_i18n_get_with_error_kwarg_no_crash(self, key):
        I18n.initialize()
        result = I18n.get(key, error="some raw exception text")
        assert isinstance(result, str)
        assert "some raw exception text" not in result, f"key '{key}' 的返回值不应包含原始异常文本"

    @pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
    def test_i18n_get_error_kwarg_ignored_both_locales(self, locale):
        I18n.initialize()
        I18n.set_locale(locale)
        result = I18n.get("data_export_fail", error="ConnectionRefusedError: connection refused")
        assert isinstance(result, str)
        assert "ConnectionRefusedError" not in result


class TestFormatErrorChainCall:
    """验证 .format(error=...) 链式调用在模板不含 {error} 时正常返回字符串。"""

    def test_format_error_ignored(self):
        I18n.initialize()
        template = I18n.get("common_op_fail")
        assert "{error}" not in template
        result = template.format(error="some detail")
        assert isinstance(result, str)
        assert "some detail" not in result

    def test_format_error_with_sanitize(self):
        I18n.initialize()
        template = I18n.get("db_err_create_failed")
        assert "{error}" not in template
        result = template.format(error="[SANITIZED]")
        assert isinstance(result, str)
        assert "[SANITIZED]" not in result

    def test_format_error_empty_string(self):
        I18n.initialize()
        template = I18n.get("common_check_fail")
        assert "{error}" not in template
        result = template.format(error="")
        assert isinstance(result, str)

    def test_format_error_with_get_error_message(self):
        I18n.initialize()
        template = I18n.get("common_op_fail")
        assert "{error}" not in template
        result = template.format(error="网络错误")
        assert isinstance(result, str)
        assert "网络错误" not in result

    def test_backtest_failed_format(self):
        I18n.initialize()
        template = I18n.get("backtest_failed")
        assert "{error}" not in template
        result = template.format(error=str(RuntimeError("test")))
        assert isinstance(result, str)
        assert "RuntimeError" not in result

    def test_screener_exec_error_format(self):
        I18n.initialize()
        template = I18n.get("screener_exec_error")
        assert "{error}" not in template
        result = template.format(error="内部错误")
        assert isinstance(result, str)
        assert "内部错误" not in result

    def test_db_upgrade_error_content_format(self):
        I18n.initialize()
        I18n.set_locale("zh_CN")
        template = I18n.get("db_upgrade_error_content")
        assert "{error}" not in template
        result = template.format(error="升级失败详情")
        assert isinstance(result, str)
        assert "升级失败详情" not in result
        assert "日志" in result  # 引导用户查看日志


class TestDatabasePlaceholderStillWorks:
    """验证 {database} 占位符仍正常工作。"""

    def test_db_err_not_found_zh(self):
        I18n.initialize()
        I18n.set_locale("zh_CN")
        result = I18n.get("db_err_not_found", database="mydb")
        assert "mydb" in result

    def test_db_err_not_found_en(self):
        I18n.initialize()
        I18n.set_locale("en_US")
        result = I18n.get("db_err_not_found", database="mydb")
        assert "mydb" in result
