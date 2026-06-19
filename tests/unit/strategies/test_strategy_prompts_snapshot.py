"""strategies/strategy_prompts.py 的 syrupy 快照测试。

目标：通过快照基线锁定 prompt 模板内容，捕捉无意的文本改动。
当 prompt 内容有意变化时，使用 `pytest --snapshot-update` 更新基线。
"""

from unittest.mock import patch

import pytest

from strategies.strategy_prompts import (
    FORBIDDEN_STATIC_HEADERS,
    STRATEGY_PROMPTS,
    _UNIVERSAL_RULES,
    get_base_prompt,
    resolve_prompt,
)

# 所有策略 key，按字母序排列以保证 parametrize ID 稳定
STRATEGY_KEYS = sorted(STRATEGY_PROMPTS.keys())


def test_universal_rules_snapshot(snapshot):
    """_UNIVERSAL_RULES 内容快照。"""
    assert snapshot == _UNIVERSAL_RULES


def test_forbidden_static_headers_snapshot(snapshot):
    """FORBIDDEN_STATIC_HEADERS 内容快照。"""
    assert snapshot == FORBIDDEN_STATIC_HEADERS


def test_strategy_prompts_dict_snapshot(snapshot):
    """STRATEGY_PROMPTS 完整字典内容快照。"""
    assert snapshot == STRATEGY_PROMPTS


@pytest.mark.parametrize("strategy_key", STRATEGY_KEYS)
def test_get_base_prompt_snapshot(strategy_key, snapshot):
    """get_base_prompt 默认输出快照（无用户配置时回退到策略默认模板）。"""
    with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None):
        result = get_base_prompt(strategy_key)
    assert result == snapshot


@pytest.mark.parametrize("strategy_key", STRATEGY_KEYS)
def test_resolve_prompt_snapshot(strategy_key, snapshot):
    """resolve_prompt 默认输出快照（base + 通用规则）。"""
    with patch("utils.config_handler.ConfigHandler.get_strategy_prompt", return_value=None):
        result = resolve_prompt(strategy_key)
    assert result == snapshot
