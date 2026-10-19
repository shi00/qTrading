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

# 「模型管理」页签 role → i18n label key（与 ui/components/model_management_panel 页签顺序对齐）
_MODEL_TAB_LABEL_KEYS: dict[str, str] = {
    "connection": "model_management_tab_connections",
    "routes": "model_management_tab_routes",
    "usage": "model_management_tab_usage",
}

# 连接状态过滤 key → i18n label key（与 model_management_panel._STATUS_FILTER_KEYS 对齐）
_MODEL_STATUS_FILTER_KEYS: dict[str, str] = {
    "all": "model_management_filter_all",
    "enabled": "model_management_filter_enabled",
    "disabled": "model_management_filter_disabled",
}

# 使用记录来源过滤 key → i18n label key（与 model_management_panel._ORIGIN_FILTER_KEYS 对齐）
_MODEL_ORIGIN_FILTER_KEYS: dict[str, str] = {
    "all": "model_management_filter_all",
    "business": "model_usage_origin_business",
    "test": "model_usage_origin_test",
}

# 场景 id → i18n label key（与 scenario_route_editor._SCENARIO_LABEL_KEYS 对齐）
_MODEL_SCENARIO_LABEL_KEYS: dict[str, str] = {
    "news_annotation": "model_routes_scenario_news_annotation",
    "stock_analysis": "model_routes_scenario_stock_analysis",
    "news_risk": "model_routes_scenario_news_risk",
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


def model_tab_label(role: str) -> str:
    """根据「模型管理」页签 role 返回本地化文案（role ∈ connection/routes/usage）。"""
    label_key = _MODEL_TAB_LABEL_KEYS.get(role, f"model_management_tab_{role}")
    return I18n.get(label_key)


def model_status_filter_label(filter_key: str) -> str:
    """根据连接状态过滤 key 返回本地化选项文案（all/enabled/disabled）。"""
    label_key = _MODEL_STATUS_FILTER_KEYS.get(filter_key, filter_key)
    return I18n.get(label_key)


def model_origin_filter_label(filter_key: str) -> str:
    """根据使用记录来源过滤 key 返回本地化选项文案（all/business/test）。"""
    label_key = _MODEL_ORIGIN_FILTER_KEYS.get(filter_key, filter_key)
    return I18n.get(label_key)


def model_scenario_label(scenario_id: str) -> str:
    """根据场景 id 返回本地化导航文案（news_annotation/stock_analysis/news_risk）。"""
    label_key = _MODEL_SCENARIO_LABEL_KEYS.get(scenario_id, scenario_id)
    return I18n.get(label_key)
