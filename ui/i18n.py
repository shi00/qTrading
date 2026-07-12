"""
UI-layer i18n module.

A-P0-2 fix: The I18n class has been moved to core/i18n.py to eliminate
reverse dependency (strategies/utils should not import from ui layer).
This module re-exports I18n for backward compatibility with existing UI code.

Non-UI modules (strategies, utils, data, services) should import from core.i18n:
    from core.i18n import I18n

UI modules can continue to import from ui.i18n:
    from ui.i18n import I18n

§4.2 合规: I18nState(ft.Observable) Observable 状态源定义在本模块 (ui 层),
对齐 AppColorsState 在 ui/theme.py 的合规模式. core/i18n.py 仅保留 _listeners
通知抽象, locale 变更时通过 _listeners 回调同步本模块的 Observable state.
"""

import logging
from dataclasses import dataclass

import flet as ft

from core.i18n import I18n, LOCALE_MAP, LOCALE_NAMES, SUPPORTED_LOCALES, DEFAULT_LOCALE  # noqa: F401

from utils.error_classifier import classify_error, get_error_message  # noqa: F401

logger = logging.getLogger(__name__)


@ft.observable
@dataclass
class I18nState(ft.Observable):
    """i18n Observable 状态源 (UI 层, 对齐 AppColorsState 模式).

    声明式组件通过 ``ft.use_state(get_observable_state)`` 订阅,
    ``I18n.set_locale`` 经 ``_listeners`` 回调同步 ``state.locale`` 触发重渲染.

    显式继承 ``ft.Observable`` 使 pyright 识别 ``subscribe`` 等方法;
    ``@ft.observable`` 检测 ``Observable in __mro__`` 后 no-op 返回原类.

    与 AppColorsState 的差异 (§4.2 约束的必然结果):
    - state 持有: 模块级全局 vs AppColors._state 类属性 (I18n 在 core 层不可持有 Observable)
    - accessor: 模块函数 vs classmethod (匹配各自作用域)
    - 同步触发: _listeners 回调 vs load_theme 直接赋值 (core 层不可 import ui, 回调是唯一合规路径)
    """

    locale: str = DEFAULT_LOCALE


_i18n_state: I18nState | None = None


def get_observable_state() -> I18nState:
    """获取 i18n Observable 状态源单例 (对齐 AppColors.get_observable_state).

    声明式组件通过 ``ft.use_state(get_observable_state)`` 订阅,
    locale 变更时 ``I18n.set_locale``/``initialize`` 经 ``_listeners`` 通知
    ``_sync_i18n_state`` 同步 ``state.locale`` 触发自动重渲染.
    """
    global _i18n_state
    if _i18n_state is None:
        _i18n_state = I18nState()
    return _i18n_state


def _sync_i18n_state() -> None:
    """I18n._listeners 回调: locale 变更/初始化时同步 ui 层 Observable state.

    被 ``I18n.subscribe`` 注册到 core 层 ``_listeners``, 在 ``set_locale``/
    ``initialize`` 时触发. 操作仅 ``state.locale = current_locale()``, 无 raise 路径.
    """
    get_observable_state().locale = I18n.current_locale()


# 模块加载时订阅 locale 变更通知 (core 层通知抽象 → ui 层 Observable 同步).
# sync_immediately=True: 首次订阅时同步当前 locale, 保证 state 不 stale.
I18n.subscribe(_sync_i18n_state)


def translate_strategy_name(name: str | None) -> str | None:
    """
    Translate strategy name to localized version.

    R.3.3: 简化为 i18n key 直接翻译 + 非 i18n key 兜底原样返回。
    历史数据 (identifier/zh/en 翻译字符串) 已由 scripts/migrate_strategy_name_to_i18n_key.py
    迁移为 i18n key, 未迁移值原样返回 (兜底兼容)。

    Args:
        name: i18n key (如 "strategy_value_name") 或非 i18n key 字符串 (兜底原样返回)

    Returns:
        本地化策略名; 非 i18n key 原样返回
    """
    if name and name.startswith("strategy_"):
        return I18n.get(name)
    return name


def refresh_dropdown_options(
    dropdown: ft.Dropdown,
    new_options: list[ft.dropdown.Option],
) -> None:
    """重建 Dropdown options 并确保显示文本正确刷新。

    本函数为 V1 永久方案（非临时垫片）：Flet 0.85.3 改用 ``Prop`` 描述符（V0 的
    ``_set_attr_internal`` 已删除），``Prop.__set__`` 在 ``old == value`` 时跳过赋值
    （值相等优化），导致批量 ``page.update()`` 中 ``value`` 从 X->None->X 的最终值
    等于原值时，前端 DropdownButton 不触发 rebuild。此行为是 V1 渲染管线的固有特性，
    非临时 bug，故本函数需长期保留。

    通过分两步 ``control.update()`` 解决：
    步骤1: 提交 options + value=None，前端清除选中项显示
    步骤2: 提交 value=saved，前端用新 options 的 text 更新显示

    异常处理说明（R2 合规）：本函数为同步路径（非 async），不会出现 ``CancelledError``；
    两处 ``except (RuntimeError, AttributeError)`` 仅吞 ``update()`` 在控件未挂载时抛出的
    ``RuntimeError``（V1 行为）/ ``AttributeError``（V0 兼容），后续 ``page.update()`` 兜底刷新。

    参考: CONTRIBUTING.md §5.8 规范 4
    """
    saved_value = dropdown.value
    dropdown.value = None
    dropdown.options = new_options
    try:
        dropdown.update()
    except (RuntimeError, AttributeError):
        # 控件未挂载时跳过，后续 page.update() 兜底
        pass
    dropdown.value = saved_value
    try:
        dropdown.update()
    except (RuntimeError, AttributeError):
        # 控件未挂载时跳过，后续 page.update() 兜底
        pass
