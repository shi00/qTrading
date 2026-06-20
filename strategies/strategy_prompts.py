"""Default LLM system prompts for each stock screening strategy.

ARCH-C1 / R1 红线修复：纯数据（``_UNIVERSAL_RULES`` / ``STRATEGY_PROMPTS`` /
``FORBIDDEN_STATIC_HEADERS``）与无依赖解析逻辑已下沉到 ``core/prompt_base.py``。
本模块保留向后兼容的 1-arg 包装器（绑定 ``ConfigHandler``），供 strategies/ui 层直接调用。
services 层应直接从 ``core.prompt_base`` 导入以避免 R1 架构越界。
"""

from __future__ import annotations

from core.prompt_base import (
    FORBIDDEN_STATIC_HEADERS,
    STRATEGY_PROMPTS,
    _UNIVERSAL_RULES,
    get_base_prompt as _core_get_base_prompt,
)

__all__ = [
    "FORBIDDEN_STATIC_HEADERS",
    "STRATEGY_PROMPTS",
    "_UNIVERSAL_RULES",
    "get_base_prompt",
    "resolve_prompt",
]


def get_base_prompt(strategy_key: str) -> str:
    """获取基础 prompt（不含通用规则），1-arg 向后兼容包装器。

    绑定 ``ConfigHandler`` 后委托给 ``core.prompt_base.get_base_prompt``。
    供 strategies/ui 层调用；services 层应直接使用 ``core.prompt_base`` 的 3-arg 版本。
    """
    from utils.config_handler import ConfigHandler

    return _core_get_base_prompt(
        strategy_key,
        ConfigHandler.get_strategy_prompt,
        ConfigHandler.get_ai_system_prompt,
    )


def resolve_prompt(strategy_key: str) -> str:
    """获取完整 prompt（含通用规则），1-arg 向后兼容包装器。

    调用本模块的 ``get_base_prompt`` 并追加 ``_UNIVERSAL_RULES``，
    保持与旧实现一致的 patch 行为（patch ``get_base_prompt`` 会影响本函数）。
    """
    base = get_base_prompt(strategy_key)
    if base:
        return base + "\n\n" + _UNIVERSAL_RULES
    return _UNIVERSAL_RULES
