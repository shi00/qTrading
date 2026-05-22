"""strategies/strategy_prompts.py 单元测试"""

from unittest.mock import patch

from strategies.strategy_prompts import (
    _UNIVERSAL_RULES,
    STRATEGY_PROMPTS,
    get_base_prompt,
    resolve_prompt,
)


class TestStrategyPromptsConstants:
    def test_universal_rules_not_empty(self):
        assert _UNIVERSAL_RULES
        assert len(_UNIVERSAL_RULES) > 100
        assert "【输出格式】" in _UNIVERSAL_RULES
        assert "conclusion_label" in _UNIVERSAL_RULES

    def test_strategy_prompts_dict_not_empty(self):
        assert len(STRATEGY_PROMPTS) > 0
        assert "value" in STRATEGY_PROMPTS
        assert "growth" in STRATEGY_PROMPTS
        assert "oversold" in STRATEGY_PROMPTS

    def test_all_strategy_prompts_not_empty(self):
        for key, prompt in STRATEGY_PROMPTS.items():
            assert prompt, f"Prompt for {key} should not be empty"


class TestCleanRules:
    def test_clean_rules_with_empty_text(self):
        with (
            patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None),
            patch("utils.config_handler.ConfigHandler.get_ai_system_prompt", return_value=""),
        ):
            result = get_base_prompt("unknown_strategy")
            assert result == ""

    def test_clean_rules_exact_match_removal(self):
        text_with_rules = "Some prompt content\n\n" + _UNIVERSAL_RULES

        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=text_with_rules):
            result = get_base_prompt("value")
            assert _UNIVERSAL_RULES not in result
            assert "Some prompt content" in result

    def test_clean_rules_heuristic_marker_removal(self):
        text_with_marker = (
            "Some prompt content\n\n【输出格式】你必须只返回一个合法 JSON 对象，包含 conclusion_label 字段..."
        )

        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=text_with_marker):
            result = get_base_prompt("value")
            assert "【输出格式】" not in result


class TestGetBasePrompt:
    def test_user_prompt_takes_priority(self):
        user_prompt = "Custom user prompt for testing"

        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=user_prompt):
            result = get_base_prompt("value")
            assert result == user_prompt.strip()

    def test_strategy_default_when_no_user_prompt(self):
        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None):
            result = get_base_prompt("value")
            assert result
            assert "价值投资" in result or "格雷厄姆" in result

    def test_global_fallback_when_no_strategy_key(self):
        global_prompt = "Global fallback prompt"

        with (
            patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None),
            patch("utils.config_handler.ConfigHandler.get_ai_system_prompt", return_value=global_prompt),
        ):
            result = get_base_prompt("unknown_strategy")
            assert result == global_prompt.strip()

    def test_empty_user_prompt_falls_back_to_default(self):
        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=""):
            result = get_base_prompt("value")
            assert result
            assert "价值投资" in result or "格雷厄姆" in result


class TestResolvePrompt:
    def test_resolve_prompt_with_base(self):
        base_prompt = "Test base prompt"

        with patch("strategies.strategy_prompts.get_base_prompt", return_value=base_prompt):
            result = resolve_prompt("value")
            assert result == base_prompt + "\n\n" + _UNIVERSAL_RULES
            assert _UNIVERSAL_RULES in result

    def test_resolve_prompt_with_empty_base(self):
        with patch("strategies.strategy_prompts.get_base_prompt", return_value=""):
            result = resolve_prompt("value")
            assert result == _UNIVERSAL_RULES

    def test_resolve_prompt_appends_universal_rules(self):
        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None):
            result = resolve_prompt("value")
            assert _UNIVERSAL_RULES in result
            assert result.endswith(_UNIVERSAL_RULES.strip())

    def test_resolve_prompt_for_oversold_strategy(self):
        with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None):
            result = resolve_prompt("oversold")
            assert result
            assert "超跌反弹" in result or "均值回归" in result
            assert _UNIVERSAL_RULES in result
