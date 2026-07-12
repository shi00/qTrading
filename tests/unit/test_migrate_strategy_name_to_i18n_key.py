"""Tests for scripts/migrate_strategy_name_to_i18n_key.py."""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# pyright can't statically resolve scripts/ modules (not on extraPaths); runtime sys.path works.
from migrate_strategy_name_to_i18n_key import migrate_strategy_name  # type: ignore[reportMissingImports]  # noqa: E402


class TestMigrateStrategyName:
    """R.3.2 migrate_strategy_name 纯函数契约测试."""

    def test_migrate_strategy_name_identifier_mapped(self):
        """identifier (如 AI_Auto_Nightly) 应映射到 i18n key."""
        assert migrate_strategy_name("AI_Auto_Nightly") == "strategy_ai_nightly_name"

    def test_migrate_strategy_name_zh_mapped(self):
        """zh_CN 翻译字符串应映射到 i18n key."""
        assert migrate_strategy_name("价值投资") == "strategy_value_name"
        assert migrate_strategy_name("高成长策略") == "strategy_growth_name"
        assert migrate_strategy_name("AI 深度精选 (Beta)") == "strategy_ai_active_name"
        assert migrate_strategy_name("AI 自动夜间选股") == "strategy_ai_nightly_name"
        assert migrate_strategy_name("放量突破") == "strategy_volume_breakout_name"

    def test_migrate_strategy_name_en_mapped(self):
        """en_US 翻译字符串应映射到 i18n key."""
        assert migrate_strategy_name("Value Investing") == "strategy_value_name"
        assert migrate_strategy_name("High Growth") == "strategy_growth_name"
        assert migrate_strategy_name("AI Deep Dive (Beta)") == "strategy_ai_active_name"
        assert migrate_strategy_name("AI Auto Nightly Screening") == "strategy_ai_nightly_name"
        assert migrate_strategy_name("Volume Breakout") == "strategy_volume_breakout_name"

    def test_migrate_strategy_name_idempotent(self):
        """DoD: 已 startswith('strategy_') 的 i18n key 不应被重复处理 (幂等)."""
        assert migrate_strategy_name("strategy_value_name") == "strategy_value_name"
        assert migrate_strategy_name("strategy_ai_nightly_name") == "strategy_ai_nightly_name"
        assert migrate_strategy_name("strategy_growth_name") == "strategy_growth_name"
        assert migrate_strategy_name("strategy_volume_breakout_name") == "strategy_volume_breakout_name"

    def test_migrate_strategy_name_unknown_preserved(self):
        """DoD: 未覆盖值应保留原值 (调用方记 warning)."""
        assert migrate_strategy_name("自定义策略") == "自定义策略"
        assert migrate_strategy_name("Custom Strategy") == "Custom Strategy"
        assert migrate_strategy_name("UnknownStrategy") == "UnknownStrategy"

    def test_migrate_strategy_name_none_and_empty(self):
        """None/空字符串应原样返回."""
        assert migrate_strategy_name(None) is None
        assert migrate_strategy_name("") == ""


class TestStrategyNameMapSync:
    """R.3.3 scripts/ 副本完整性守护.

    R.3.3 删除 ui/i18n.py:_STRATEGY_NAME_MAP 后, scripts/ 副本成为唯一来源。
    本测试类断言 scripts/ 副本覆盖所有 strategy_*_name i18n key 的反向映射,
    防止新增 strategy 时遗漏迁移脚本的反向映射条目。
    """

    def test_scripts_map_covers_all_strategy_keys(self):
        """scripts/ 副本应覆盖所有 strategy_*_name i18n key 的反向映射.

        遍历 locales/zh_CN/strings.json 中所有 strategy_*_name key,
        断言每个 key 在 scripts._STRATEGY_NAME_MAP.values() 中至少出现一次。
        """
        import json
        from pathlib import Path

        locales_dir = Path(__file__).resolve().parent.parent.parent / "locales"
        with open(locales_dir / "zh_CN" / "strings.json", encoding="utf-8") as f:
            zh_strings = json.load(f)

        strategy_keys = {k for k in zh_strings if k.startswith("strategy_") and k.endswith("_name")}
        assert strategy_keys, "locales/zh_CN/strings.json 应至少有一个 strategy_*_name key"

        from migrate_strategy_name_to_i18n_key import _STRATEGY_NAME_MAP as scripts_map  # type: ignore[reportMissingImports]

        mapped_values = set(scripts_map.values())
        missing = strategy_keys - mapped_values
        assert not missing, (
            f"scripts/_STRATEGY_NAME_MAP 缺少以下 strategy_*_name key 的反向映射: {missing}. "
            "新增策略时必须在 scripts/migrate_strategy_name_to_i18n_key.py:_STRATEGY_NAME_MAP 中添加对应的 zh/en 反向映射。"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
