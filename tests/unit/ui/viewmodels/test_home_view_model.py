"""HomeViewModel clear_state 扩展 + set_load_error/set_loading 单元测试 (P1-3 批次 2).

测试 VM state 命令的行为 (state-driven, 不依赖 Flet 渲染)。
仅测 clear_state/set_load_error/set_loading 命令, 不调用 init() (避免 service 订阅)。
subscribe/dispose 由 test_home_view.py 通过 FakeHomeViewModel 覆盖。
"""

from unittest.mock import patch

import pytest

from ui.viewmodels import Message
from ui.viewmodels.home_view_model import HomeViewModel

pytestmark = pytest.mark.unit


@pytest.fixture
def vm():
    """HomeViewModel with mocked DataProcessor (避免真实初始化)."""
    with patch("ui.viewmodels.home_view_model.DataProcessor"):
        return HomeViewModel()


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
