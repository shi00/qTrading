"""strategies/strategy_prompts.py 的快照测试（纯 pytest，不依赖 syrupy）。

目标：确保 STRATEGY_PROMPTS 和 _UNIVERSAL_RULES 的内容稳定。
当内容变化时测试失败，提示更新期望值。
"""

from strategies.strategy_prompts import (
    _UNIVERSAL_RULES,
    FORBIDDEN_STATIC_HEADERS,
    STRATEGY_PROMPTS,
)


# 完整 key 集合：新增或删除 key 时此测试失败，提示更新
EXPECTED_STRATEGY_KEYS = frozenset(
    {
        "value",
        "growth",
        "dividend",
        "cashflow",
        "large_pe",
        "northbound_holding",
        "northbound_flow",
        "institutional",
        "block_trade",
        "oversold",
        "ai_active",
        "volume_breakout",
    }
)

EXPECTED_UNIVERSAL_RULES_MIN_LENGTH = 500  # _UNIVERSAL_RULES 至少 500 字符
EXPECTED_FORBIDDEN_HEADERS_MIN_COUNT = 3  # 至少 3 个禁止头


def test_strategy_prompts_keys_stable():
    """STRATEGY_PROMPTS 的完整 key 集合必须与期望一致。

    新增或删除任何 key 都会失败，提示更新 EXPECTED_STRATEGY_KEYS。
    """
    actual_keys = set(STRATEGY_PROMPTS.keys())
    assert actual_keys == EXPECTED_STRATEGY_KEYS, (
        f"STRATEGY_PROMPTS key 集合变化。\n"
        f"缺失: {EXPECTED_STRATEGY_KEYS - actual_keys}\n"
        f"多余: {actual_keys - EXPECTED_STRATEGY_KEYS}\n"
        f"如为有意修改，请更新 EXPECTED_STRATEGY_KEYS。"
    )


def test_universal_rules_content_stable():
    """_UNIVERSAL_RULES 内容非空且达到最小长度。

    覆盖 strategy_prompts.py:17-29 的 _UNIVERSAL_RULES 常量。
    """
    assert _UNIVERSAL_RULES, "_UNIVERSAL_RULES 不应为空"
    assert len(_UNIVERSAL_RULES) >= EXPECTED_UNIVERSAL_RULES_MIN_LENGTH, (
        f"_UNIVERSAL_RULES 长度 {len(_UNIVERSAL_RULES)} < {EXPECTED_UNIVERSAL_RULES_MIN_LENGTH}，"
        "内容可能被意外截断。如为有意修改，请更新 EXPECTED_UNIVERSAL_RULES_MIN_LENGTH。"
    )


def test_forbidden_headers_stable():
    """FORBIDDEN_STATIC_HEADERS 非空且达到最小数量。

    覆盖 strategy_prompts.py 的 FORBIDDEN_STATIC_HEADERS 常量。
    """
    assert FORBIDDEN_STATIC_HEADERS, "FORBIDDEN_STATIC_HEADERS 不应为空"
    assert len(FORBIDDEN_STATIC_HEADERS) >= EXPECTED_FORBIDDEN_HEADERS_MIN_COUNT, (
        f"FORBIDDEN_STATIC_HEADERS 数量 {len(FORBIDDEN_STATIC_HEADERS)} "
        f"< {EXPECTED_FORBIDDEN_HEADERS_MIN_COUNT}，"
        "内容可能被意外清空。如为有意修改，请更新 EXPECTED_FORBIDDEN_HEADERS_MIN_COUNT。"
    )


def test_all_prompts_non_empty():
    """所有策略 prompt 非空且达到最小长度（50 字符）。

    覆盖 strategy_prompts.py:32+ 的 STRATEGY_PROMPTS dict 所有 value。
    """
    for key, prompt in STRATEGY_PROMPTS.items():
        assert prompt, f"STRATEGY_PROMPTS['{key}'] 不应为空"
        assert len(prompt.strip()) >= 50, (
            f"STRATEGY_PROMPTS['{key}'] 长度 {len(prompt.strip())} < 50，内容可能被意外截断。"
        )
