"""E2E 测试本地化文案映射。

集中提供策略 key、主题 key、语言 key 到本地化显示名的转换，
消除散落在测试代码与 helper 中的领域硬编码映射。

约定：
- 策略 name_key 由 `@register_strategy("key")` 注册的 key 派生：`strategy_<key>_name`
- 主题 key 来自 `ui.theme.ThemeName`（dark/light/navy/dracula）
- 语言 key 来自 `core.i18n.SUPPORTED_LOCALES`（zh_CN/en_US）
"""

from core.i18n import I18n

# 主题 key → i18n label key 映射（与 ui.theme.ThemeName 对齐）
_THEME_LABEL_KEYS: dict[str, str] = {
    "dark": "theme_dark",
    "light": "theme_light",
    "navy": "theme_navy",
    "dracula": "theme_dracula",
}

# 语言 key → i18n label key 映射（与 core.i18n.SUPPORTED_LOCALES 对齐）
_LANGUAGE_LABEL_KEYS: dict[str, str] = {
    "zh_CN": "settings_lang_zh",
    "en_US": "settings_lang_en",
}


def strategy_label(strategy_key: str) -> str:
    """根据策略 key 返回本地化显示名。

    约定：策略 name_key = f"strategy_{strategy_key}_name"
    （由 `@register_strategy("key")` 装饰器注册的 key 派生）
    """
    return I18n.get(f"strategy_{strategy_key}_name")


def strategy_desc_label(strategy_key: str) -> str:
    """根据策略 key 返回本地化描述文案。"""
    return I18n.get(f"strategy_{strategy_key}_desc")


def theme_label(theme_key: str) -> str:
    """根据主题 key 返回本地化文案。"""
    label_key = _THEME_LABEL_KEYS.get(theme_key, f"theme_{theme_key}")
    return I18n.get(label_key)


def language_label(lang_key: str) -> str:
    """根据语言 key 返回本地化文案。"""
    label_key = _LANGUAGE_LABEL_KEYS.get(lang_key, f"settings_lang_{lang_key}")
    return I18n.get(label_key)
