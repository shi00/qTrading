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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
