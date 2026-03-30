import json
from pathlib import Path

import pytest

from ui.i18n import DEFAULT_LOCALE, LOCALE_MAP, SUPPORTED_LOCALES, I18n


@pytest.fixture(autouse=True)
def reset_i18n():
    I18n._initialized = False
    I18n._locale = DEFAULT_LOCALE
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    I18n._listeners = None
    yield
    I18n._initialized = False
    I18n._locale = DEFAULT_LOCALE
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    I18n._listeners = None


class TestLocaleResourceIntegrity:
    """Test that locale JSON files are complete and valid."""

    @pytest.fixture
    def locales_dir(self):
        return Path(__file__).parent.parent / "locales"

    @pytest.fixture
    def zh_strings(self, locales_dir):
        with open(locales_dir / "zh_CN" / "strings.json", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture
    def en_strings(self, locales_dir):
        with open(locales_dir / "en_US" / "strings.json", encoding="utf-8") as f:
            return json.load(f)

    def test_zh_cn_file_exists(self, locales_dir):
        assert (locales_dir / "zh_CN" / "strings.json").exists(), (
            "zh_CN strings.json file must exist"
        )

    def test_en_us_file_exists(self, locales_dir):
        assert (locales_dir / "en_US" / "strings.json").exists(), (
            "en_US strings.json file must exist"
        )

    def test_zh_strings_not_empty(self, zh_strings):
        assert len(zh_strings) > 0, "zh_CN strings should not be empty"
        assert len(zh_strings) >= 500, (
            f"zh_CN should have at least 500 keys, got {len(zh_strings)}"
        )

    def test_en_strings_not_empty(self, en_strings):
        assert len(en_strings) > 0, "en_US strings should not be empty"
        assert len(en_strings) >= 500, (
            f"en_US should have at least 500 keys, got {len(en_strings)}"
        )

    def test_both_locales_have_same_keys(self, zh_strings, en_strings):
        zh_keys = set(zh_strings.keys())
        en_keys = set(en_strings.keys())

        missing_in_en = zh_keys - en_keys
        missing_in_zh = en_keys - zh_keys

        error_msg = ""
        if missing_in_en:
            error_msg += f"\nKeys missing in en_US ({len(missing_in_en)}): {sorted(list(missing_in_en)[:10])}..."
        if missing_in_zh:
            error_msg += f"\nKeys missing in zh_CN ({len(missing_in_zh)}): {sorted(list(missing_in_zh)[:10])}..."

        assert zh_keys == en_keys, f"Locale keys must match!{error_msg}"

    def test_no_empty_values_in_zh(self, zh_strings):
        empty_keys = [k for k, v in zh_strings.items() if not v or not v.strip()]
        assert len(empty_keys) == 0, (
            f"zh_CN has empty values for keys: {empty_keys[:10]}"
        )

    def test_no_empty_values_in_en(self, en_strings):
        empty_keys = [k for k, v in en_strings.items() if not v or not v.strip()]
        assert len(empty_keys) == 0, (
            f"en_US has empty values for keys: {empty_keys[:10]}"
        )

    def test_all_values_are_strings(self, zh_strings, en_strings):
        non_string_zh = [k for k, v in zh_strings.items() if not isinstance(v, str)]
        non_string_en = [k for k, v in en_strings.items() if not isinstance(v, str)]

        assert len(non_string_zh) == 0, (
            f"zh_CN has non-string values: {non_string_zh[:10]}"
        )
        assert len(non_string_en) == 0, (
            f"en_US has non-string values: {non_string_en[:10]}"
        )

    def test_format_placeholders_consistency(self, zh_strings, en_strings):
        import re

        placeholder_pattern = re.compile(r"\{(\w+)\}")

        inconsistent = []
        for key in zh_strings.keys() & en_strings.keys():
            zh_placeholders = set(placeholder_pattern.findall(zh_strings[key]))
            en_placeholders = set(placeholder_pattern.findall(en_strings[key]))

            if zh_placeholders != en_placeholders:
                inconsistent.append(
                    {
                        "key": key,
                        "zh": zh_strings[key],
                        "en": en_strings[key],
                        "zh_placeholders": zh_placeholders,
                        "en_placeholders": en_placeholders,
                    }
                )

        assert len(inconsistent) == 0, (
            f"Found {len(inconsistent)} keys with inconsistent placeholders: {inconsistent[:5]}"
        )


class TestI18nAPICompatibility:
    """Test that the I18n API remains compatible with existing callers."""

    def test_get_returns_string(self):
        I18n.initialize()
        result = I18n.get("app_title")
        assert isinstance(result, str)
        assert result == "A股智能选股助手"

    def test_get_with_format_args(self):
        I18n.initialize()
        result = I18n.get("screener_done", count=10)
        assert "10" in result
        assert result == "筛选完成，共 10 只股票"

    def test_get_missing_key_returns_key(self):
        I18n.initialize()
        result = I18n.get("nonexistent_key_12345")
        assert result == "nonexistent_key_12345"

    def test_get_missing_key_with_default(self):
        I18n.initialize()
        result = I18n.get("nonexistent_key_12345", default="Default Value")
        assert result == "Default Value"

    def test_set_locale_changes_language(self):
        I18n.initialize()

        I18n.set_locale("zh_CN")
        assert I18n.current_locale() == "zh_CN"
        assert I18n.get("app_title") == "A股智能选股助手"

        I18n.set_locale("en_US")
        assert I18n.current_locale() == "en_US"
        assert I18n.get("app_title") == "A-Share Intelligent Screener"

    def test_set_locale_with_short_code(self):
        I18n.initialize()

        I18n.set_locale("zh")
        assert I18n.current_locale() == "zh_CN"

        I18n.set_locale("en")
        assert I18n.current_locale() == "en_US"

    def test_initialize_is_idempotent(self):
        I18n.initialize()
        first_locale = I18n.current_locale()

        I18n.initialize()
        second_locale = I18n.current_locale()

        assert first_locale == second_locale

    def test_subscribe_unsubscribe(self):
        callback_called = []

        def callback():
            callback_called.append(True)

        I18n.subscribe(callback)
        I18n.set_locale("en_US")

        assert len(callback_called) == 1

        I18n.unsubscribe(callback)
        I18n.set_locale("zh_CN")

        assert len(callback_called) == 1

    def test_subscribe_returns_subscription_id(self):
        callback_called = []

        def callback():
            callback_called.append(True)

        subscription_id = I18n.subscribe(callback)

        assert subscription_id is callback

        I18n.set_locale("en_US")
        assert len(callback_called) == 1

        I18n.unsubscribe(subscription_id)
        I18n.set_locale("zh_CN")

        assert len(callback_called) == 1

    def test_subscribe_idempotent(self):
        callback_called = []

        def callback():
            callback_called.append(True)

        I18n.subscribe(callback)
        I18n.subscribe(callback)
        I18n.subscribe(callback)

        I18n.set_locale("en_US")

        assert len(callback_called) == 1

    def test_get_supported_locales(self):
        locales = I18n.get_supported_locales()
        assert "zh_CN" in locales
        assert "en_US" in locales
        assert isinstance(locales, list)


class TestI18nDynamicLoading:
    """Test dynamic loading and caching mechanisms."""

    def test_strings_are_cached(self):
        I18n.initialize()

        I18n.get("app_title")
        assert I18n.current_locale() in I18n._strings_cache

        I18n.set_locale("en_US")
        I18n.get("app_title")
        assert "en_US" in I18n._strings_cache

    def test_reload_locale_reloads_strings(self):
        I18n.initialize()
        I18n.get("app_title")

        assert I18n.current_locale() in I18n._strings_cache

        I18n.reload_locale()

        assert I18n.current_locale() in I18n._strings_cache

    def test_lazy_initialization(self):
        assert not I18n._initialized

        result = I18n.get("app_title")

        assert I18n._initialized
        assert isinstance(result, str)

    def test_missing_keys_are_deduplicated(self):
        I18n.initialize()
        I18n._missing_keys = set()

        I18n.get("missing_key_1")
        I18n.get("missing_key_1")
        I18n.get("missing_key_1")

        assert len(I18n._missing_keys) == 1


class TestI18nEdgeCases:
    """Test edge cases and error handling."""

    def test_format_without_placeholders_returns_template(self):
        I18n.initialize()
        result = I18n.get("screener_done")
        assert "{count}" in result

    def test_format_with_extra_placeholders(self):
        I18n.initialize()
        result = I18n.get("screener_done", count=10, extra="ignored")
        assert "10" in result

    def test_set_unsupported_locale(self):
        I18n.initialize()
        original = I18n.current_locale()

        I18n.set_locale("fr_FR")

        assert I18n.current_locale() == original

    def test_locale_map_completeness(self):
        assert "zh" in LOCALE_MAP
        assert "zh_CN" in LOCALE_MAP
        assert "en" in LOCALE_MAP
        assert "en_US" in LOCALE_MAP

        assert LOCALE_MAP["zh"] == "zh_CN"
        assert LOCALE_MAP["en"] == "en_US"

    def test_supported_locales_constant(self):
        assert DEFAULT_LOCALE in SUPPORTED_LOCALES
        assert "zh_CN" in SUPPORTED_LOCALES
        assert "en_US" in SUPPORTED_LOCALES


class TestI18nBackwardCompatibility:
    """Test backward compatibility with existing code patterns."""

    def test_common_keys_exist(self):
        """Test that commonly used keys exist in both locales."""
        I18n.initialize()

        common_keys = [
            "app_title",
            "nav_market",
            "nav_screener",
            "nav_settings",
            "settings_title",
            "common_save",
            "common_cancel",
            "common_confirm",
            "status_ready",
            "status_error",
        ]

        for key in common_keys:
            zh_result = I18n.get(key)
            assert zh_result != key, f"Key '{key}' missing in zh_CN"

            I18n.set_locale("en_US")
            en_result = I18n.get(key)
            assert en_result != key, f"Key '{key}' missing in en_US"

            I18n.set_locale("zh_CN")

    def test_strategy_keys_exist(self):
        """Test that strategy-related keys exist."""
        I18n.initialize()

        strategy_keys = [
            "strategy_value_name",
            "strategy_growth_name",
            "strategy_dividend_name",
            "strategy_tech_breakout_name",
            "strategy_northbound_name",
            "strategy_oversold_name",
        ]

        for key in strategy_keys:
            result = I18n.get(key)
            assert result != key, f"Strategy key '{key}' is missing"

    def test_llm_config_keys_exist(self):
        """Test that LLM configuration keys exist."""
        I18n.initialize()

        llm_keys = [
            "llm_select_provider",
            "llm_select_model",
            "llm_api_key",
            "llm_test_connection",
            "llm_test_success",
            "llm_test_failed",
        ]

        for key in llm_keys:
            result = I18n.get(key)
            assert result != key, f"LLM key '{key}' is missing"
