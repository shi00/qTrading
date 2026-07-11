"""
Unit tests for ui/i18n.py.
Covers strategy name translation functionality and I18nState Observable 状态源.
"""

from unittest.mock import patch

import pytest

import ui.i18n as ui_i18n
from ui.i18n import DEFAULT_LOCALE, I18n, I18nState, get_observable_state, translate_strategy_name

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_i18n():
    """每个测试前后重置 I18n 全局状态（含 ui 层 Observable state）。

    A2-fix3: 不清空 _listeners (保留 ui/i18n.py _sync_i18n_state 全局订阅),
    仅重置 core 层 locale 状态和 ui 层 _i18n_state 单例.
    Regression fix: 保存/恢复 _listeners 快照, 清理测试中 subscribe 的泄漏回调.
    """
    saved_listeners = list(I18n._listeners) if I18n._listeners else None
    I18n._initialized = False
    I18n._locale = DEFAULT_LOCALE
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    ui_i18n._i18n_state = None
    yield
    I18n._listeners = saved_listeners
    I18n._initialized = False
    I18n._locale = DEFAULT_LOCALE
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    ui_i18n._i18n_state = None


class TestTranslateStrategyName:
    """Tests for translate_strategy_name function.

    旧接口兼容性测试（阶段 4 删除时同步移除）。
    """

    def test_translate_none_returns_none(self):
        """Test translating None returns None."""
        result = translate_strategy_name(None)
        assert result is None

    def test_translate_empty_string_returns_empty(self):
        """Test translating empty string returns empty."""
        result = translate_strategy_name("")
        assert result == ""

    def test_translate_known_strategy_id(self):
        """Test translating a known strategy ID."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "AI Nightly Strategy"
            result = translate_strategy_name("AI_Auto_Nightly")
        assert result == "AI Nightly Strategy"

    def test_translate_known_strategy_name_chinese(self):
        """Test translating a known Chinese strategy name."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "Value Investing"
            result = translate_strategy_name("价值投资")
        assert result == "Value Investing"

    def test_translate_known_strategy_name_english(self):
        """Test translating a known English strategy name."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "Value Investing"
            result = translate_strategy_name("Value Investing")
        assert result == "Value Investing"

    def test_translate_unknown_strategy_returns_original(self):
        """Test translating unknown strategy returns original."""
        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.side_effect = lambda x: x  # Return key as-is
            result = translate_strategy_name("Unknown Strategy")
        assert result == "Unknown Strategy"

    def test_translate_all_known_strategy_ids(self):
        """Test translating all known strategy IDs in the map."""
        test_cases = [
            ("AI_Auto_Nightly", "strategy_ai_nightly_name"),
            ("AI 深度精选 (Beta)", "strategy_ai_active_name"),
            ("AI Deep Dive (Beta)", "strategy_ai_active_name"),
            ("价值投资", "strategy_value_name"),
            ("Value Investing", "strategy_value_name"),
            ("高成长策略", "strategy_growth_name"),
            ("高股息策略", "strategy_dividend_name"),
            ("北向持股", "strategy_northbound_holding_name"),
            ("北向净流入", "strategy_northbound_flow_name"),
            ("超跌反弹", "strategy_oversold_name"),
            ("龙虎榜机构", "strategy_institutional_name"),
            ("筹码集中 (暂不可用)", "strategy_chips_name"),
            ("大宗交易", "strategy_block_trade_name"),
            ("现金流优质", "strategy_cashflow_name"),
            ("大盘低估", "strategy_large_pe_name"),
        ]

        with patch("ui.i18n.I18n.get") as mock_get:
            mock_get.return_value = "Translated"
            for strategy_name, _ in test_cases:
                result = translate_strategy_name(strategy_name)
                assert result == "Translated"

    def test_translate_returns_original_when_i18n_fails(self):
        """Test that original name is returned if I18n lookup fails."""
        # Test that non-mapped names pass through
        result = translate_strategy_name("Unknown Strategy Name")
        assert result == "Unknown Strategy Name"


class TestI18nObservable:
    """I18nState Observable 状态源断言（声明式组件自动重渲染基础）。

    声明式组件通过 ``ft.use_state(get_observable_state)`` 订阅，
    locale 变更时 ``set_locale`` 更新 ``state.locale`` 触发 Observable 通知，
    框架自动重渲染订阅该 state 的组件。
    """

    def test_get_observable_state_returns_singleton(self):
        """多次调用 get_observable_state 返回同一实例（单例）。"""
        state1 = get_observable_state()
        state2 = get_observable_state()
        assert state1 is state2

    def test_observable_state_is_i18n_state_type(self):
        """get_observable_state 返回 I18nState 实例。"""
        state = get_observable_state()
        assert isinstance(state, I18nState)

    def test_observable_state_default_locale(self):
        """新创建的 Observable state 默认 locale 为 DEFAULT_LOCALE。"""
        # ui_i18n._i18n_state 在 fixture 中被重置为 None，get_observable_state 会 lazy 创建
        state = get_observable_state()
        assert state.locale == DEFAULT_LOCALE

    def test_set_locale_updates_observable_state(self):
        """set_locale 同步更新 state.locale。"""
        I18n.set_locale("en_US")
        assert get_observable_state().locale == "en_US"

    def test_set_locale_triggers_observable_notification(self):
        """set_locale 触发 Observable 通知（state.locale 赋值 → __setattr__ → _notify）。"""
        state = get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        # 必须保留 disposer，否则 subscribe 弱引用 lambda 会被 GC（spike 项 1.9）
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            I18n.set_locale("en_US")
        finally:
            disposer()
        assert len(notifications) == 1
        assert notifications[0][1] == "locale"

    def test_observable_notification_sender_is_state(self):
        """通知的 sender 是 state 实例本身。"""
        state = get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            I18n.set_locale("en_US")
        finally:
            disposer()
        assert notifications[0][0] is state

    def test_observable_subscribe_disposer_stops_notification(self):
        """disposer 后不再收到通知。"""
        state = get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        disposer()
        I18n.set_locale("en_US")
        assert len(notifications) == 0

    def test_set_locale_same_value_no_notification(self):
        """相同 locale 值不触发通知（Observable __setattr__ 值相等优化）。"""
        # 先设置为 en_US
        I18n.set_locale("en_US")
        state = get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            # 再次 set_locale("en_US")，locale 值未变，不应触发通知
            I18n.set_locale("en_US")
        finally:
            disposer()
        assert len(notifications) == 0

    def test_set_locale_unsupported_no_notification(self):
        """不支持的 locale 不触发通知（set_locale 不更新 state）。"""
        state = get_observable_state()
        notifications: list[tuple[object, str | None]] = []
        disposer = state.subscribe(lambda sender, field: notifications.append((sender, field)))
        try:
            # "fr_FR" 不在 SUPPORTED_LOCALES 中，set_locale 应跳过更新
            I18n.set_locale("fr_FR")
        finally:
            disposer()
        assert len(notifications) == 0
        assert state.locale == DEFAULT_LOCALE

    def test_initialize_updates_observable_state(self):
        """initialize 同步更新 state.locale。"""
        I18n.initialize("en_US")
        assert get_observable_state().locale == "en_US"

    def test_initialize_default_locale_updates_observable_state(self):
        """initialize() 无参数时使用 DEFAULT_LOCALE 同步 state。"""
        I18n.initialize()
        assert get_observable_state().locale == DEFAULT_LOCALE
