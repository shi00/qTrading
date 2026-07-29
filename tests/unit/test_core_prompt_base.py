"""core/prompt_base.py 专门测试.

core-002: 补充 core 层 prompt_base 模块的直接单元测试.

现有 tests/unit/strategies/test_strategy_prompts.py 测试的是 strategies/strategy_prompts.py
的 1-arg 包装器 (绑定 ConfigHandler), 本文件直接测试 core/prompt_base.py 的 3-arg
原始函数 (get_base_prompt / resolve_prompt) 和 _clean_rules 内部函数, 覆盖:

1. _clean_rules 边界条件 (None / 空字符串 / 纯空白 / 含 _UNIVERSAL_RULES / 含【输出格式】块)
2. get_base_prompt 3-tier fallback chain + callable 注入验证
3. resolve_prompt base 非空/空场景

不重复 test_strategy_prompts.py 已覆盖的数据资产完整性测试 (FORBIDDEN_STATIC_HEADERS /
STRATEGY_PROMPTS key 完整性等).
"""

from __future__ import annotations

import pytest

from core.prompt_base import (
    _UNIVERSAL_RULES,
    _clean_rules,
    get_base_prompt,
    resolve_prompt,
)

pytestmark = pytest.mark.unit


def _noop_user_prompt(_key: str) -> str | None:
    return None


def _noop_global_prompt() -> str:
    return ""


class TestCleanRulesBoundaries:
    """_clean_rules 边界条件直接测试."""

    def test_none_returns_empty(self):
        assert _clean_rules(None) == ""

    def test_empty_string_returns_empty(self):
        assert _clean_rules("") == ""

    def test_whitespace_only_returns_empty(self):
        assert _clean_rules("   \n\t  ") == ""

    def test_plain_text_stripped(self):
        assert _clean_rules("  hello world  ") == "hello world"

    def test_universal_rules_removed(self):
        text = "Some prompt content" + _UNIVERSAL_RULES
        result = _clean_rules(text)
        assert _UNIVERSAL_RULES not in result
        assert "Some prompt content" in result

    def test_output_format_block_removed(self):
        """含 【输出格式】 且后续含 conclusion_label 的文本, 从 【输出格式】 处截断."""
        text = "Some content\n【输出格式】你必须只返回一个合法 JSON 对象，包含 conclusion_label 字段..."
        result = _clean_rules(text)
        assert "【输出格式】" not in result
        assert "Some content" in result

    def test_output_format_marker_without_conclusion_label_preserved(self):
        """【输出格式】 标记后不含 conclusion_label 时不截断 (rfind + content check 双条件)."""
        text = "Some content\n【输出格式】这里没有任何 json 关键字"
        result = _clean_rules(text)
        # 【输出格式】 仍在, 因为缺少 conclusion_label 双重确认
        assert "【输出格式】" in result
        assert "Some content" in result

    def test_universal_rules_and_output_format_both_removed(self):
        text = "Base prompt" + _UNIVERSAL_RULES + "\n【输出格式】json with conclusion_label"
        result = _clean_rules(text)
        assert _UNIVERSAL_RULES not in result
        assert "【输出格式】" not in result
        assert "Base prompt" in result

    def test_only_universal_rules_strips_output_format_but_keeps_rules(self):
        """纯 _UNIVERSAL_RULES 输入: 【输出格式】块被移除, 但【铁律】保留.

        边界行为: _UNIVERSAL_RULES 以 \\n\\n 开头, strip 后 text 不含前导 \\n\\n,
        ``_UNIVERSAL_RULES in text`` 匹配失败, 故 replace 不执行.
        但 rfind("【输出格式】") + conclusion_label 双条件仍触发, 截断【输出格式】块.
        结果: 【铁律1-4】保留, 【输出格式】块移除.
        """
        result = _clean_rules(_UNIVERSAL_RULES)
        assert "【输出格式】" not in result
        assert "【铁律1】" in result
        assert "conclusion_label" not in result


class TestGetBasePromptFallbackChain:
    """get_base_prompt 3-arg 3-tier fallback chain 直接测试."""

    def test_tier1_user_prompt_takes_priority(self):
        def get_user_prompt(_key: str) -> str | None:
            return "User custom prompt"

        def get_global_prompt() -> str:
            return "Global fallback"

        result = get_base_prompt("value", get_user_prompt, get_global_prompt)
        assert result == "User custom prompt"

    def test_tier1_user_prompt_cleaned(self):
        """user_prompt 含 _UNIVERSAL_RULES 时被 _clean_rules 清理."""
        user_prompt = "My custom rules" + _UNIVERSAL_RULES

        def get_user_prompt(_key: str) -> str | None:
            return user_prompt

        def get_global_prompt() -> str:
            return "Global"

        result = get_base_prompt("value", get_user_prompt, get_global_prompt)
        assert _UNIVERSAL_RULES not in result
        assert "My custom rules" in result

    def test_tier2_strategy_default_when_user_prompt_none(self):
        """user_prompt 为 None 时 fallback 到 STRATEGY_PROMPTS[strategy_key]."""
        result = get_base_prompt("value", _noop_user_prompt, _noop_global_prompt)
        assert result
        assert "价值投资" in result or "格雷厄姆" in result
        assert _UNIVERSAL_RULES not in result

    def test_tier2_strategy_default_when_user_prompt_empty(self):
        """user_prompt 为空字符串 (strip 后为空) 时 fallback 到策略默认."""

        def get_user_prompt(_key: str) -> str | None:
            return "   "

        result = get_base_prompt("value", get_user_prompt, _noop_global_prompt)
        assert result
        assert "价值投资" in result or "格雷厄姆" in result

    def test_tier3_global_fallback_when_strategy_unknown(self):
        """strategy_key 不在 STRATEGY_PROMPTS 中且 user_prompt 为 None 时 fallback 到 global."""
        global_prompt = "Global fallback prompt"

        def get_global_prompt() -> str:
            return global_prompt

        result = get_base_prompt("unknown_strategy_xyz", _noop_user_prompt, get_global_prompt)
        assert result == "Global fallback prompt"

    def test_tier3_global_fallback_cleaned(self):
        """global_prompt 含 _UNIVERSAL_RULES 时被 _clean_rules 清理."""
        global_prompt = "Global rules" + _UNIVERSAL_RULES

        def get_global_prompt() -> str:
            return global_prompt

        result = get_base_prompt("unknown_strategy_xyz", _noop_user_prompt, get_global_prompt)
        assert _UNIVERSAL_RULES not in result
        assert "Global rules" in result

    def test_callable_get_user_prompt_receives_strategy_key(self):
        """验证 get_user_prompt callable 接收的参数是 strategy_key."""
        received_keys: list[str] = []

        def get_user_prompt(key: str) -> str | None:
            received_keys.append(key)
            return None

        get_base_prompt("growth", get_user_prompt, _noop_global_prompt)
        assert received_keys == ["growth"]

    def test_callable_get_global_prompt_called_only_when_needed(self):
        """tier1/tier2 命中时不应调用 get_global_prompt."""
        global_calls: list[int] = []

        def get_global_prompt() -> str:
            global_calls.append(1)
            return "Global"

        # tier1 命中, 不应调用 global
        get_base_prompt("value", lambda _k: "User prompt", get_global_prompt)
        assert global_calls == []

        # tier2 命中, 不应调用 global
        get_base_prompt("value", _noop_user_prompt, get_global_prompt)
        assert global_calls == []

        # tier3 命中, 应调用 global 1 次
        get_base_prompt("unknown_strategy", _noop_user_prompt, get_global_prompt)
        assert len(global_calls) == 1


class TestResolvePrompt:
    """resolve_prompt 3-arg 直接测试."""

    def test_base_non_empty_appends_universal_rules(self):
        def get_user_prompt(_key: str) -> str | None:
            return "Base prompt"

        result = resolve_prompt("value", get_user_prompt, _noop_global_prompt)
        assert result == "Base prompt" + "\n\n" + _UNIVERSAL_RULES
        assert result.endswith(_UNIVERSAL_RULES.strip())

    def test_base_empty_returns_only_universal_rules(self):
        """base 为空时仅返回 _UNIVERSAL_RULES (不追加 \\n\\n)."""

        def get_global_prompt() -> str:
            return ""

        result = resolve_prompt("unknown_strategy", _noop_user_prompt, get_global_prompt)
        assert result == _UNIVERSAL_RULES

    def test_strategy_default_resolved_with_universal_rules(self):
        """使用策略默认 prompt 时, resolve_prompt 返回 base + _UNIVERSAL_RULES."""
        result = resolve_prompt("oversold", _noop_user_prompt, _noop_global_prompt)
        assert _UNIVERSAL_RULES in result
        assert "超跌反弹" in result or "均值回归" in result
        assert result.endswith(_UNIVERSAL_RULES.strip())

    def test_resolve_prompt_uses_same_fallback_as_get_base_prompt(self):
        """resolve_prompt 内部调用 get_base_prompt, fallback 行为一致."""

        # tier1: user prompt 优先
        def get_user_prompt(_key: str) -> str | None:
            return "Custom base"

        result = resolve_prompt("value", get_user_prompt, _noop_global_prompt)
        assert result.startswith("Custom base")
        assert _UNIVERSAL_RULES in result
