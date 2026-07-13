"""ui/i18n.py Observable state 契约测试 (§4.2: Observable 下沉到 ui 层).

背景: 方案 A 将 I18nState(ft.Observable) 从 core/i18n.py 下沉到 ui/i18n.py,
对齐 AppColors 在 ui/theme.py 的合规模式. 本测试验证:
1. get_observable_state 返回单例 I18nState
2. I18n.set_locale 通过 _listeners 同步 ui 层 state
3. I18n.initialize 也触发同步 (A1 fix)
4. I18nState 是 ft.Observable 子类
"""

import flet as ft
import pytest

from core.i18n import DEFAULT_LOCALE, I18n
import ui.i18n as ui_i18n

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_i18n_state():
    """每个测试前重置 ui 层 _i18n_state 和 core 层状态.

    A2-fix3: 不清空 _listeners (保留 ui/i18n.py _sync_i18n_state 全局订阅).
    Regression fix: 保存/恢复 _listeners 快照, 清理测试中 subscribe 的泄漏回调.
    """
    saved_listeners = list(I18n._listeners) if I18n._listeners else None
    ui_i18n._i18n_state = None
    I18n._initialized = False
    I18n._locale = DEFAULT_LOCALE
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    yield
    I18n._listeners = saved_listeners
    ui_i18n._i18n_state = None
    I18n._initialized = False
    I18n._locale = DEFAULT_LOCALE
    I18n._strings_cache = {}
    I18n._missing_keys = set()


def test_get_observable_state_returns_singleton():
    """get_observable_state 返回同一单例."""
    s1 = ui_i18n.get_observable_state()
    s2 = ui_i18n.get_observable_state()
    assert s1 is s2


def test_state_is_ft_observable():
    """I18nState 必须是 ft.Observable 子类 (声明式 use_state 订阅前提)."""
    state = ui_i18n.get_observable_state()
    assert isinstance(state, ft.Observable)


def test_set_locale_syncs_state():
    """I18n.set_locale 通过 _listeners 同步 ui 层 I18nState.locale."""
    I18n.initialize("zh_CN")
    state = ui_i18n.get_observable_state()
    assert state.locale == "zh_CN"

    I18n.set_locale("en_US")
    assert state.locale == "en_US", "set_locale 必须通过 _listeners 回调同步 ui 层 state"


def test_initialize_triggers_state_sync():
    """A1 fix: I18n.initialize 也触发 _listeners 同步 state (不 stale)."""
    # 先 initialize zh_CN 建立 state
    I18n.initialize("zh_CN")
    state = ui_i18n.get_observable_state()
    assert state.locale == "zh_CN"

    # 重置 ui state (模拟首次 subscribe 前), 重新 initialize en_US
    ui_i18n._i18n_state = None
    I18n._initialized = False
    I18n.initialize("en_US")
    state2 = ui_i18n.get_observable_state()
    assert state2.locale == "en_US", "initialize 必须触发 _listeners 同步 state"


def test_initial_state_default_locale():
    """未 initialize 时, state.locale 为 DEFAULT_LOCALE."""
    state = ui_i18n.get_observable_state()
    assert state.locale == DEFAULT_LOCALE
