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

from core.i18n import (
    I18n,
    LOCALE_MAP,  # noqa: F401  # facade re-export：外部经 ui.i18n 访问
    LOCALE_NAMES,  # noqa: F401  # facade re-export：外部经 ui.i18n 访问
    SUPPORTED_LOCALES,  # noqa: F401  # facade re-export：外部经 ui.i18n 访问
    DEFAULT_LOCALE,
    STRATEGY_NAME_FALLBACK_MAP,  # noqa: F401  # E3 单源映射 re-export（迁移脚本等经 core 引用）
    translate_strategy_name,  # noqa: F401  # E3 迁移至 core 单源：本模块薄委托，保留既有导入路径
)  # facade re-export：外部经 ui.i18n 访问

from utils.error_classifier import classify_error, get_error_message  # noqa: F401  # facade re-export

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

    R.4.2: 同步失效 ``MetaDataManager`` 别名缓存, 防止旧 locale 翻译残留.
    lazy import 避免模块加载副作用 + 防循环依赖 (data 层反向 import ui 时
    不触发 ui.i18n 模块加载).
    """
    get_observable_state().locale = I18n.current_locale()
    # R.4.2: lazy import 避免 R1 架构越界 (data 层 import ui 的反向依赖) + 模块加载副作用.
    from data.persistence.metadata_manager import MetaDataManager

    MetaDataManager.invalidate_cache()


# 模块加载时订阅 locale 变更通知 (core 层通知抽象 → ui 层 Observable 同步).
# sync_immediately=True: 首次订阅时同步当前 locale, 保证 state 不 stale.
I18n.subscribe(_sync_i18n_state)


# translate_strategy_name 已迁至 core/i18n.py 单源（E3）：
# 本模块顶部从 core.i18n re-export，保留既有 `from ui.i18n import translate_strategy_name`
# 导入路径零改动（策略名兜底逻辑见 core 层实现）。
