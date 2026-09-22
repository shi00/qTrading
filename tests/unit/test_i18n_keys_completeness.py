import ast
import json
import re
import unittest
from pathlib import Path
import pytest


pytestmark = pytest.mark.unit

_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# OSS-04：动态引用键白名单（精确枚举，非前缀豁免）。
# 这些键在生产/测试代码中以 f-string 动态拼接引用，AST 静态扫描无法检出其字面引用，
# 但运行时真实使用——必须存在于 locale（受 test_no_new_unreferenced_i18n_keys 断言守护），
# 且不得登记进死键 baseline（互斥断言）。新增动态键时在此显式追加。
_DYNAMIC_KEY_ALLOWLIST: frozenset[str] = frozenset(
    {
        # data/data_processor.py: report_step(1..6) → I18n.get(f"init_step_{step_num}")
        "init_step_1",
        "init_step_2",
        "init_step_3",
        "init_step_4",
        "init_step_5",
        "init_step_6",
        # ui/views/settings_tabs/tier_api_panel.py + ui/components/config_panels/tushare_config_panel.py:
        # TUSHARE_POINT_TIERS → I18n.get(f"sys_tier_{tier}_label")
        "sys_tier_points_120_label",
        "sys_tier_points_2000_label",
        "sys_tier_points_5000_label",
        "sys_tier_points_10000_label",
        "sys_tier_points_15000_label",
        # services/ai_service/news_classifier.py: I18n.get(f"news_l1_{l1_code}", l1_code)
        # 枚举源 utils/config_models.py NEWS_CATEGORY_MAP 全集 5 L1
        "news_l1_finance",
        "news_l1_industry",
        "news_l1_macro_economy",
        "news_l1_geopolitics",
        "news_l1_other",
        # services/ai_service/news_classifier.py: I18n.get(f"news_l2_{l2_code}", l2_code)
        # 枚举源 NEWS_CATEGORY_MAP 全集 17 L2
        "news_l2_a_stock",
        "news_l2_hk_us",
        "news_l2_intl_macro",
        "news_l2_fiscal_policy",
        "news_l2_futures",
        "news_l2_forex",
        "news_l2_consumer",
        "news_l2_energy",
        "news_l2_energy_sector",
        "news_l2_financial_sector",
        "news_l2_entertainment",
        "news_l2_livelihood",
        "news_l2_conflict",
        "news_l2_precious_metals",
        "news_l2_macro_policy",
        "news_l2_macro_data",
        "news_l2_tech",
        # ui/components/config_panels/llm_config_panel.py: I18n.get(f"llm_provider_{provider_id}")
        # 枚举源同文件 provider_id 硬编码清单（deepseek..minimax / openai..mistral）
        "llm_provider_openai",
        "llm_provider_anthropic",
        "llm_provider_azure",
        "llm_provider_google",
        "llm_provider_mistral",
        "llm_provider_qwen",
        "llm_provider_moonshot",
        "llm_provider_minimax",
        "llm_provider_deepseek",
        "llm_provider_zhipu",
        "llm_provider_custom",
        # ui/viewmodels/llm_config_panel_view_model.py: llm_switch_provider_hint 的
        # provider_key → I18n.get(f"llm_provider_{provider_id}")，枚举源 LLM_PROVIDERS 全集（含 custom）
        # ui/views/settings_tabs/data_source_tab.py: I18n.get(f"quality_tier_{result.quality_tier}")
        # 枚举源 quality_tier 取值域 0-3
        "quality_tier_0",
        "quality_tier_1",
        "quality_tier_2",
        "quality_tier_3",
    }
)

# OSS-04：baseline 死键数上限（棘轮天花板）。清理死键递减后须手动下调；
# 防止向 baseline 塞键 + count 同步 +1 静默放宽棘轮（对抗检视 D-2 加固）。
_BASELINE_CEILING = 216


class TestI18nKeysCompleteness(unittest.TestCase):
    LOCALES_DIR = Path(__file__).parent.parent.parent / "locales"

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
        from data.data_dictionary import TABLE_DEFINITIONS, column_i18n_key, columns_of

        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        # OSS-01：表 alias 仍来自 TABLE_DEFINITIONS 表级元数据；列级 i18n key 经
        # column_i18n_key 统一解析（非 None 时必须在 zh/en 两个 locale 中定义）。
        missing_aliases_zh: list[str] = []
        missing_aliases_en: list[str] = []
        missing_cols_zh: list[str] = []
        missing_cols_en: list[str] = []
        for table_name, meta in TABLE_DEFINITIONS.items():
            alias = meta.get("alias")
            if alias:
                if alias not in zh_keys:
                    missing_aliases_zh.append(f"{table_name}:{alias}")
                if alias not in en_keys:
                    missing_aliases_en.append(f"{table_name}:{alias}")
            for col_name in columns_of(table_name):
                i18n_key = column_i18n_key(table_name, col_name)
                if i18n_key is None:
                    continue  # 无标签（回退裸列名），不要求 locale 存在
                if i18n_key not in zh_keys:
                    missing_cols_zh.append(f"{table_name}.{col_name}:{i18n_key}")
                if i18n_key not in en_keys:
                    missing_cols_en.append(f"{table_name}.{col_name}:{i18n_key}")

        self.assertFalse(
            missing_aliases_zh,
            f"Missing table alias i18n keys in zh_CN: {missing_aliases_zh[:10]}",
        )
        self.assertFalse(
            missing_aliases_en,
            f"Missing table alias i18n keys in en_US: {missing_aliases_en[:10]}",
        )
        self.assertFalse(
            missing_cols_zh,
            f"Missing column i18n keys in zh_CN: {missing_cols_zh[:10]}",
        )
        self.assertFalse(
            missing_cols_en,
            f"Missing column i18n keys in en_US: {missing_cols_en[:10]}",
        )

    def test_common_columns_i18n_keys_exist(self):
        """OSS-01B: COMMON_COLUMNS 全量兜底键必须在 zh/en locale 中定义。

        列级 i18n key 经 column_i18n_key 从 ORM 列派生，不属于任何 ORM 列的
        COMMON_COLUMNS 条目（如 rsi_6 / volume 等动态列）不会被
        test_data_dictionary_i18n_keys_exist 遍历到，须在此独立守护双语存在性。
        """
        from data.data_dictionary import COMMON_COLUMNS

        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        missing_zh = sorted({k for k in COMMON_COLUMNS.values() if k not in zh_keys})
        missing_en = sorted({k for k in COMMON_COLUMNS.values() if k not in en_keys})

        self.assertFalse(
            missing_zh,
            f"Missing COMMON_COLUMNS i18n keys in zh_CN: {missing_zh}",
        )
        self.assertFalse(
            missing_en,
            f"Missing COMMON_COLUMNS i18n keys in en_US: {missing_en}",
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
            "db_upgrade_needed_title",
            "db_upgrade_needed_content",
            "db_upgrade_btn",
            "db_upgrade_in_progress_title",
            "db_upgrade_in_progress_content",
            "db_upgrade_success_title",
            "db_upgrade_success_content",
            "db_upgrade_error_title",
            "db_upgrade_error_content",
            "exit_program",
            "retry_upgrade",
        ]

        missing_zh = [k for k in required_keys if k not in zh_keys]
        missing_en = [k for k in required_keys if k not in en_keys]

        self.assertFalse(missing_zh, f"Missing main.py keys in zh_CN: {missing_zh}")
        self.assertFalse(missing_en, f"Missing main.py keys in en_US: {missing_en}")

    def test_deprecated_qfq_keys_removed(self):
        """P0-1: col_qfq_* keys should be removed since qfq columns no longer exist in DB."""
        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        deprecated_keys = {
            "col_qfq_open",
            "col_qfq_high",
            "col_qfq_low",
            "col_qfq_close",
        }

        remaining_zh = deprecated_keys & zh_keys
        remaining_en = deprecated_keys & en_keys

        self.assertFalse(remaining_zh, f"Deprecated qfq keys still in zh_CN: {remaining_zh}")
        self.assertFalse(remaining_en, f"Deprecated qfq keys still in en_US: {remaining_en}")

    def test_db_upgrade_skip_key_removed(self):
        """P0-6: db_upgrade_skip key should be removed since upgrade is now mandatory."""
        zh_keys = self._load_keys("zh_CN")
        en_keys = self._load_keys("en_US")

        deprecated_key = "db_upgrade_skip"

        self.assertNotIn(deprecated_key, zh_keys, f"Deprecated key '{deprecated_key}' still in zh_CN")
        self.assertNotIn(deprecated_key, en_keys, f"Deprecated key '{deprecated_key}' still in en_US")

    def test_no_chinese_fallback_in_i18n_get(self):
        """UI-003: I18n.get() default parameter must not contain CJK characters.

        Chinese fallback values silently mask missing keys in non-Chinese locales.
        All i18n text should live in locale files; if a key is missing, the warning
        log (triggered when default=None) makes the gap visible.

        Note: Dynamic defaults (e.g. I18n.get(warning, warning)) cannot be detected
        by AST analysis. These are manually verified to use English enum values only:
        - screener_view.py / ai_brain_tab.py: validate_prompt() returns English keys
        - news_feed.py: news tags are English enums (stock, policy, etc.)
        - ai_service.py: AI classification codes are English enums
        """
        project_root = Path(__file__).parent.parent.parent
        violations: list[str] = []

        for py_file in project_root.rglob("*.py"):
            # Skip test directories and virtual environments
            parts = py_file.relative_to(project_root).parts
            if parts[0] in (
                "tests",
                "scripts",
                ".venv",
                "venv",
                "node_modules",
                ".worktrees",
                ".git",
                "tiktoken_cache",
            ):
                continue

            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))

            for node in ast.walk(tree):
                # Match I18n.get(...) calls
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
                    continue
                if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "I18n"):
                    continue

                # Check positional default (2nd arg)
                if len(node.args) >= 2:
                    val = node.args[1]
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        if _CJK_PATTERN.search(val.value):
                            key_name = (
                                node.args[0].value
                                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                                else "?"
                            )
                            rel = py_file.relative_to(project_root)
                            violations.append(f'{rel}:{node.lineno} key="{key_name}" (positional default)')

                # Check keyword default=
                for kw in node.keywords:
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        if _CJK_PATTERN.search(kw.value.value):
                            key_name = (
                                node.args[0].value
                                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                                else "?"
                            )
                            rel = py_file.relative_to(project_root)
                            violations.append(f'{rel}:{node.lineno} key="{key_name}" (keyword default)')

        self.assertFalse(
            violations,
            f"Found {len(violations)} I18n.get() calls with CJK fallback defaults:\n" + "\n".join(violations),
        )

    def test_no_new_unreferenced_i18n_keys(self):
        """UIX-16: Ratchet baseline gate for dead / unreferenced i18n keys.

        Every key defined in locales/zh_CN/strings.json must appear in Python AST string constants
        across the project, be covered by a dynamic-key allowlist / prefix, or be registered in
        tests/i18n_dead_keys_baseline.json. New unreferenced keys are forbidden; the total
        unreferenced key count is ratcheted (may only stay equal or decrease).

        OSS-01：``col_`` / ``tab_`` 等前缀为动态拼接键（``column_i18n_key`` / 表 alias 在
        运行时按 ``col_<列名>`` / ``tab_<表名>`` 拼接），AST 静态扫描无法检出它们的字面引用，
        属系统性假阳性，予以豁免（检视报告 §5 同款前缀白名单思路）。

        OSS-04 增强（a-e 五断言，堵检视报告 §5 复核发现的危害链）：
        1. 新增死键禁止 + 总数棘轮（原 UIX-16）
        2. allowlist ⊆ zh_keys：动态引用键误删 locale 定义时 FAIL（此前零拦截、运行时回退裸键名）
        3. baseline keys ⊆ zh_keys：死键清理须在同一提交原子完成「删 zh 键 + 删 en 键 +
           摘 baseline 键 + 减 count」四处修改（任一中间态提交都会被本组断言拦截）
        4. len(baseline keys) == count：baseline JSON 自洽，防 count 虚高静默放宽棘轮
        5. baseline 与 allowlist 互斥：动态活键不得登记进死键 baseline（语义分离）
        6. count ≤ _BASELINE_CEILING：棘轮天花板，防「塞键 + count 同步递增」绕过棘轮
        """
        project_root = Path(__file__).parent.parent.parent
        baseline_path = project_root / "tests" / "i18n_dead_keys_baseline.json"
        self.assertTrue(baseline_path.exists(), f"Missing i18n dead keys baseline: {baseline_path}")

        with open(baseline_path, encoding="utf-8") as f:
            baseline_data = json.load(f)
            baseline_keys = set(baseline_data.get("keys", []))
            baseline_count = baseline_data.get("count", len(baseline_keys))

        zh_keys = self._load_keys("zh_CN")

        # OSS-04 断言 c：baseline JSON 自洽（keys 与 count 漂移须先核对再改）
        self.assertEqual(
            len(baseline_keys),
            baseline_count,
            "i18n_dead_keys_baseline.json 的 count 与 keys 数量不一致，请核对后同步",
        )
        # OSS-04 断言 e：棘轮天花板，防塞键 + count 同步递增绕过
        self.assertLessEqual(
            baseline_count,
            _BASELINE_CEILING,
            f"baseline count ({baseline_count}) 超过棘轮天花板 {_BASELINE_CEILING}；"
            f"死键只减不增，清理后须下调 _BASELINE_CEILING 而非上调",
        )
        # OSS-04 断言 d：动态活键与死键登记互斥
        overlap = baseline_keys & _DYNAMIC_KEY_ALLOWLIST
        self.assertFalse(
            overlap,
            f"以下动态引用活键被误登记进死键 baseline（应移入 _DYNAMIC_KEY_ALLOWLIST）: {sorted(overlap)}",
        )
        # OSS-04 断言 a：动态引用键必须在 locale 定义（误删会导致运行时回退裸键名）
        missing_dynamic = _DYNAMIC_KEY_ALLOWLIST - zh_keys
        self.assertFalse(
            missing_dynamic,
            f"动态引用键从 strings.json 丢失（运行时将回退裸键名）: {sorted(missing_dynamic)}；"
            f"若为有意下线，须先删除代码中的动态引用点并同步收缩 _DYNAMIC_KEY_ALLOWLIST",
        )
        # OSS-04 断言 b：baseline 死键须仍在 locale（清理须原子完成四处同步修改）
        vanished = baseline_keys - zh_keys
        self.assertFalse(
            vanished,
            f"baseline 登记的死键已从 strings.json 消失: {sorted(vanished)[:20]}；"
            f"清理死键须在同一提交完成：删 zh 键 + 删 en 键 + 摘 baseline 条目 + 减 count",
        )

        used_strings: set[str] = set()
        scanned_dirs = ["ui", "core", "data", "services", "strategies", "utils", "app", "tests"]
        for d in scanned_dirs:
            target_dir = project_root / d
            if not target_dir.exists():
                continue
            for py_file in target_dir.rglob("*.py"):
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"), filename=str(py_file))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Constant) and isinstance(node.value, str):
                            used_strings.add(node.value)
                # NOTE(lazy): 扫描失败静默跳过. ceiling: 语法错误文件. upgrade: 引入扫描失败计数告警.
                # fail-safe 方向：used_strings 缺失只会放大 unreferenced_keys（假阳性 FAIL），不会漏检。
                except Exception:
                    pass

        # 动态拼接前缀（运行时拼键，静态不可检查）：col_/tab_ 由派生逻辑按约定拼接；
        # _DYNAMIC_KEY_ALLOWLIST 为精确枚举的动态引用键（见模块级注释）
        dynamic_prefixes = ("col_", "tab_", "strategy_", "err_")
        unreferenced_keys = {
            k for k in zh_keys - used_strings if not k.startswith(dynamic_prefixes)
        } - _DYNAMIC_KEY_ALLOWLIST
        new_unreferenced = unreferenced_keys - baseline_keys

        self.assertFalse(
            new_unreferenced,
            f"New unreferenced / dead i18n keys detected (not in baseline or code): {sorted(new_unreferenced)[:20]}",
        )
        self.assertLessEqual(
            len(unreferenced_keys),
            baseline_count,
            f"Unreferenced i18n keys count ({len(unreferenced_keys)}) exceeded baseline ({baseline_count}). "
            f"Ratchet invariant violated: dead key count may only decrease.",
        )


if __name__ == "__main__":
    unittest.main()
