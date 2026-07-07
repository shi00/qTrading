"""
UI-layer i18n module.

A-P0-2 fix: The I18n class has been moved to core/i18n.py to eliminate
reverse dependency (strategies/utils should not import from ui layer).
This module re-exports I18n for backward compatibility with existing UI code.

Non-UI modules (strategies, utils, data, services) should import from core.i18n:
    from core.i18n import I18n

UI modules can continue to import from ui.i18n:
    from ui.i18n import I18n
"""

import logging

import flet as ft

from core.i18n import I18n, LOCALE_MAP, LOCALE_NAMES, SUPPORTED_LOCALES, DEFAULT_LOCALE  # noqa: F401

from utils.error_classifier import classify_error, get_error_message  # noqa: F401

logger = logging.getLogger(__name__)


_STRATEGY_NAME_MAP = {
    "AI_Auto_Nightly": "strategy_ai_nightly_name",
    "AI 深度精选 (Beta)": "strategy_ai_active_name",
    "AI Deep Dive (Beta)": "strategy_ai_active_name",
    "价值投资": "strategy_value_name",
    "Value Investing": "strategy_value_name",
    "高成长策略": "strategy_growth_name",
    "高股息策略": "strategy_dividend_name",
    "北向持股": "strategy_northbound_holding_name",
    "北向净流入": "strategy_northbound_flow_name",
    "超跌反弹": "strategy_oversold_name",
    "龙虎榜机构": "strategy_institutional_name",
    "筹码集中 (暂不可用)": "strategy_chips_name",
    "大宗交易": "strategy_block_trade_name",
    "现金流优质": "strategy_cashflow_name",
    "大盘低估": "strategy_large_pe_name",
}


def translate_strategy_name(name: str | None) -> str | None:
    """
    Translate strategy name to localized version.

    Args:
        name: Strategy name (either an identifier like 'AI_Auto_Nightly' or already localized)

    Returns:
        Localized strategy name
    """
    if not name:
        return name

    if name in _STRATEGY_NAME_MAP:
        return I18n.get(_STRATEGY_NAME_MAP[name])

    return name


def refresh_dropdown_options(
    dropdown: ft.Dropdown,
    new_options: list[ft.dropdown.Option],
) -> None:
    """重建 Dropdown options 并确保显示文本正确刷新。

    Flet 0.85.3 改用 ``Prop`` 描述符（V0 的 ``_set_attr_internal`` 已删除），
    ``Prop.__set__`` 在 ``old == value`` 时跳过赋值（值相等优化），
    可能导致批量 ``page.update()`` 中 ``value`` 从 X->None->X 的最终值
    等于原值时，前端 DropdownButton 不触发 rebuild。

    通过分两步 ``control.update()`` 解决：
    步骤1: 提交 options + value=None，前端清除选中项显示
    步骤2: 提交 value=saved，前端用新 options 的 text 更新显示

    参考: CONTRIBUTING.md §5.8 规范 4
    """
    saved_value = dropdown.value
    dropdown.value = None
    dropdown.options = new_options
    try:
        dropdown.update()
    except Exception:
        # 控件未挂载时跳过，后续 page.update() 兜底
        pass
    dropdown.value = saved_value
    try:
        dropdown.update()
    except Exception:
        pass
