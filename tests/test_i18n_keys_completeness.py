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


if __name__ == "__main__":
    unittest.main()
