import json
import unittest
from pathlib import Path


class TestI18nKeysCompleteness(unittest.TestCase):
    LOCALES_DIR = Path(__file__).parent.parent / "locales"

    def _load_keys(self, locale: str) -> set[str]:
        path = self.LOCALES_DIR / locale / "strings.json"
        with open(path, encoding="utf-8") as f:
            return set(json.load(f).keys())

    def test_zh_cn_and_en_us_have_same_keys(self):
        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        missing_in_en = zh_keys - en_keys
        missing_in_zh = en_keys - zh_keys

        self.assertFalse(
            missing_in_en,
            f"Keys in zh_CN but missing from en_US: {sorted(missing_in_en)[:20]}",
        )
        self.assertFalse(
            missing_in_zh,
            f"Keys in en_US but missing from zh_CN: {sorted(missing_in_zh)[:20]}",
        )

    def test_data_dictionary_i18n_keys_exist(self):
        from data.data_dictionary import TABLE_DEFINITIONS, COMMON_COLUMNS

        zh_keys = self._load_keys("zh_CN")

        missing_table_aliases = []
        for table_name, meta in TABLE_DEFINITIONS.items():
            alias = meta.get("alias")
            if alias and alias not in zh_keys:
                missing_table_aliases.append(f"{table_name}:{alias}")

        missing_col_keys = []
        for col_name, i18n_key in COMMON_COLUMNS.items():
            if i18n_key not in zh_keys:
                missing_col_keys.append(f"{col_name}:{i18n_key}")

        self.assertFalse(
            missing_table_aliases,
            f"Missing table alias i18n keys: {missing_table_aliases[:10]}",
        )
        self.assertFalse(
            missing_col_keys,
            f"Missing column i18n keys: {missing_col_keys[:10]}",
        )

    def test_no_empty_values(self):
        for locale in ["zh_CN", "en_US"]:
            path = self.LOCALES_DIR / locale / "strings.json"
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            empty_keys = [k for k, v in data.items() if not v or not v.strip()]
            self.assertFalse(
                empty_keys,
                f"Empty values in {locale}: {empty_keys[:10]}",
            )

    def test_main_py_i18n_keys_exist(self):
        """Verify all i18n keys used in main.py exist in both locale files."""
        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        required_keys = [
            "error_db_init_failed",
            "error_db_engine_missing",
            "warning_skip_db",
            "retry",
            "skip",
            "app_title",
            "exit_confirm_title",
            "exit_confirm_content",
            "common_cancel",
            "common_confirm",
        ]

        missing_zh = [k for k in required_keys if k not in zh_keys]
        missing_en = [k for k in required_keys if k not in en_keys]

        self.assertFalse(missing_zh, f"Missing main.py keys in zh_CN: {missing_zh}")
        self.assertFalse(missing_en, f"Missing main.py keys in en_US: {missing_en}")

    def test_deprecated_qfq_keys_removed(self):
        """P0-1: col_qfq_* keys should be removed since qfq columns no longer exist in DB."""
        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        deprecated_keys = {"col_qfq_open", "col_qfq_high", "col_qfq_low", "col_qfq_close"}

        remaining_zh = deprecated_keys & zh_keys
        remaining_en = deprecated_keys & en_keys

        self.assertFalse(remaining_zh, f"Deprecated qfq keys still in zh_CN: {remaining_zh}")
        self.assertFalse(remaining_en, f"Deprecated qfq keys still in en_US: {remaining_en}")


if __name__ == "__main__":
    unittest.main()
