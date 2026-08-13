"""HomeViewModel clear_state 扩展 + set_load_error/set_loading + stop() 单元测试.

测试 VM state 命令的行为 (state-driven, 不依赖 Flet 渲染)。
- clear_state/set_load_error/set_loading 仅测命令, 不调用 init() (避免 service 订阅)。
- stop() 用 mock service 防重型实例化 (NewsSubscriptionService/MarketDataService 单例
  构造触发 AIService/litellm import, 见 home_view.py E2E 注释)。
subscribe/dispose 由 test_home_view.py 通过 FakeHomeViewModel 覆盖。
"""

from unittest.mock import MagicMock, patch

import pytest

from ui.viewmodels import Message
from ui.viewmodels.home_view_model import HomeViewModel

pytestmark = pytest.mark.unit


@pytest.fixture
def vm():
    """HomeViewModel with mocked DataProcessor (避免真实初始化)."""
    with patch("ui.viewmodels.home_view_model.DataProcessor"):
        return HomeViewModel()


@pytest.fixture
def vm_with_service_mocks(monkeypatch):
    """HomeViewModel with mocked DataProcessor + service singletons.

    防重型实例化: NewsSubscriptionService/MarketDataService 单例构造会触发
    AIService/litellm import 等副作用。stop() 测试仅验证 add_listener/
    remove_listener 调用契约, 不关心服务内部行为 (与既有 DataProcessor mock 一致)。
    """
    news_service = MagicMock()
    market_service = MagicMock()
    monkeypatch.setattr("ui.viewmodels.home_view_model.NewsSubscriptionService", lambda: news_service)
    monkeypatch.setattr("ui.viewmodels.home_view_model.MarketDataService", lambda: market_service)
    with patch("ui.viewmodels.home_view_model.DataProcessor"):
        vm = HomeViewModel()
    return vm, news_service, market_service


class TestClearStateExtended:
    """P1-3 批次 2: clear_state 扩展重置 is_loading_more + 新增 4 字段."""

    def test_clear_state_resets_is_loading_more_bug_fix(self, vm):
        """修复 bug: 原 clear_state 不重置 is_loading_more, 导致卡在 True."""
        vm._set_state(is_loading_more=True)
        assert vm.state.is_loading_more is True
        vm.clear_state()
        assert vm.state.is_loading_more is False

    def test_clear_state_resets_new_fields(self, vm):
        """clear_state 重置 P1-3 批次 2 新增的 4 字段."""
        vm._set_state(
            is_loading=True,
            has_market_data=True,
            has_news_data=True,
            load_error=Message("home_load_failed_title", {}),
        )
        vm.clear_state()
        assert vm.state.is_loading is False
        assert vm.state.has_market_data is False
        assert vm.state.has_news_data is False
        assert vm.state.load_error is None


class TestSetLoadError:
    """P1-3 批次 2: set_load_error 命令."""

    def test_set_load_error_sets_message(self, vm):
        msg = Message("home_load_failed_title", {})
        vm.set_load_error(msg)
        assert vm.state.load_error is msg

    def test_set_load_error_none_clears(self, vm):
        vm.set_load_error(Message("home_load_failed_title", {}))
        vm.set_load_error(None)
        assert vm.state.load_error is None


class TestSetLoading:
    """P1-3 批次 2: set_loading 命令."""

    def test_set_loading_true(self, vm):
        vm.set_loading(True)
        assert vm.state.is_loading is True

    def test_set_loading_false(self, vm):
        vm.set_loading(True)
        vm.set_loading(False)
        assert vm.state.is_loading is False


class TestStopServiceListeners:
    """Task 1: HomeViewModel.stop() 移除 service listener (与 init() 配对, 幂等).

    覆盖 home_view_model.py stop():
    - init() 后 stop(): 两 service 的 remove_listener 以对应回调调用
    - 幂等: 重复调用不抛错 (remove_listener 基于 set.remove + try/except KeyError)
    - init() → stop() → init(): listener 重新注册 (切回 active 恢复)

    HomeView 常驻 app_layout ft.Stack 永不卸载, 失活时经 effect cleanup 调
    stop() 退订, 否则被新闻/行情 push 反复重渲染。
    """

    def test_init_then_stop_removes_both_listeners(self, vm_with_service_mocks):
        """init() 后 stop(): 两 service 的 remove_listener 以对应回调调用."""
        vm, news_service, market_service = vm_with_service_mocks

        vm.init()
        news_service.add_listener.assert_called_once_with(vm._on_news_service_update)
        market_service.add_listener.assert_called_once_with(vm._on_market_service_update)

        vm.stop()
        news_service.remove_listener.assert_called_once_with(vm._on_news_service_update)
        market_service.remove_listener.assert_called_once_with(vm._on_market_service_update)

    def test_stop_is_idempotent(self, vm_with_service_mocks):
        """stop() 幂等: 重复调用不抛错 (remove_listener 重复删除安全)."""
        vm, _news_service, _market_service = vm_with_service_mocks

        vm.init()
        vm.stop()
        vm.stop()  # 幂等: 重复调用不抛错即通过

    def test_stop_then_init_reactivates_listeners(self, vm_with_service_mocks):
        """init() → stop() → init(): listener 重新注册 (切回 active 恢复)."""
        vm, news_service, market_service = vm_with_service_mocks

        vm.init()
        news_service.add_listener.reset_mock()
        market_service.add_listener.reset_mock()

        vm.stop()
        vm.init()  # 重新注册
        news_service.add_listener.assert_called_once_with(vm._on_news_service_update)
        market_service.add_listener.assert_called_once_with(vm._on_market_service_update)


class TestDetectAiTagged:
    """Task 8.1: _detect_ai_tagged 检测新闻是否被 AI 打标."""

    def test_empty_tags_and_source_returns_false(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("", "") is False
        assert _detect_ai_tagged("", None) is False
        assert _detect_ai_tagged(None, "") is False

    def test_ai_prefix_tag_returns_true(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("AI_LLM_科技", "") is True
        assert _detect_ai_tagged("AI,科技", "") is True
        assert _detect_ai_tagged("ai,科技", "") is True

    def test_non_ai_tag_returns_false(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("科技,金融", "") is False
        assert _detect_ai_tagged("利好", "") is False

    def test_ai_source_returns_true(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("", "AI") is True
        assert _detect_ai_tagged("", "ai") is True

    def test_non_ai_source_returns_false(self):
        from ui.viewmodels.home_view_model import _detect_ai_tagged

        assert _detect_ai_tagged("", "CLS") is False
        assert _detect_ai_tagged("", "Tushare") is False


class TestNewsRowAiTagged:
    """Task 8.1: NewsRow.is_ai_tagged 通过 _news_item_to_row 正确设置."""

    def test_news_item_with_ai_tag_sets_is_ai_tagged(self):
        from ui.viewmodels.home_view_model import _news_item_to_row

        item = {"content": "test", "tags": "AI_LLM_科技", "source": "CLS"}
        with patch("data.cache.cache_manager.CacheManager.normalize_news_item", return_value=item):
            row = _news_item_to_row(item)
        assert row.is_ai_tagged is True

    def test_news_item_without_ai_tag(self):
        from ui.viewmodels.home_view_model import _news_item_to_row

        item = {"content": "test", "tags": "科技", "source": "CLS"}
        with patch("data.cache.cache_manager.CacheManager.normalize_news_item", return_value=item):
            row = _news_item_to_row(item)
        assert row.is_ai_tagged is False
