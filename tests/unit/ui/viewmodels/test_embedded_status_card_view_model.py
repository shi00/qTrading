"""EmbeddedStatusCardViewModel 单元测试 (P3-9).

覆盖:
1. EmbeddedStatusCardState frozen dataclass 字段 + 默认值
2. EmbeddedStatusCardViewModel 初始化默认状态 (embedded_pg_ready + info)
3. state property 返回 frozen snapshot
4. subscribe/dispose 协议 (ObservableViewModelMixin)
5. VM 不感知 locale (state 用 Message 产出 i18n key, 不调 I18n.get)
"""

from dataclasses import FrozenInstanceError

import pytest

from ui.viewmodels import Message
from ui.viewmodels.embedded_status_card_view_model import (
    EmbeddedStatusCardState,
    EmbeddedStatusCardViewModel,
)

pytestmark = pytest.mark.unit


class TestEmbeddedStatusCardState:
    """EmbeddedStatusCardState frozen dataclass 契约测试。"""

    def test_state_is_frozen(self) -> None:
        """DoD: state 必须 frozen (MVVM §3.2 不可变 snapshot)。"""
        state = EmbeddedStatusCardState()
        with pytest.raises(FrozenInstanceError):
            state.status_message = Message("test")  # type: ignore[misc]

    def test_default_status_type_is_info(self) -> None:
        """默认 status_type=info。"""
        assert EmbeddedStatusCardState().status_type == "info"

    def test_default_status_message_is_none(self) -> None:
        """默认 status_message=None。"""
        assert EmbeddedStatusCardState().status_message is None

    def test_default_info_message_is_none(self) -> None:
        """默认 info_message=None。"""
        assert EmbeddedStatusCardState().info_message is None


class TestEmbeddedStatusCardViewModelInit:
    """EmbeddedStatusCardViewModel 初始化测试。"""

    def test_init_sets_embedded_ready_status(self) -> None:
        """DoD: VM 初始化设置 status_message=embedded_pg_ready Message。"""
        vm = EmbeddedStatusCardViewModel()
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "embedded_pg_ready"

    def test_init_sets_success_status_type(self) -> None:
        """DoD: VM 初始化 status_type=success。"""
        vm = EmbeddedStatusCardViewModel()
        assert vm.state.status_type == "success"

    def test_init_sets_info_message(self) -> None:
        """DoD: VM 初始化 info_message 含 embedded_pg_no_config_needed key。"""
        vm = EmbeddedStatusCardViewModel()
        assert vm.state.info_message is not None
        assert vm.state.info_message.key == "embedded_pg_no_config_needed"

    def test_init_does_not_import_i18n(self) -> None:
        """DoD: VM 不 import I18n (避免 flet 污染, MVVM §3.2 + i18n 分层矩阵)."""
        import ui.viewmodels.embedded_status_card_view_model as mod

        assert not hasattr(mod, "I18n"), "VM 不应 import I18n (应从 core.i18n 导入, 避免 flet 污染)"


class TestEmbeddedStatusCardViewModelProtocol:
    """EmbeddedStatusCardViewModel subscribe/dispose 协议测试 (ObservableViewModelMixin)。"""

    def test_state_property_returns_snapshot(self) -> None:
        """state property 返回当前 snapshot。"""
        vm = EmbeddedStatusCardViewModel()
        snapshot = vm.state
        assert snapshot is not None
        assert snapshot.status_type == "success"

    def test_subscribe_returns_unsub_callable(self) -> None:
        """subscribe 返回退订函数。"""
        vm = EmbeddedStatusCardViewModel()
        unsub = vm.subscribe(lambda _state: None)
        assert callable(unsub)

    def test_subscribe_callback_added_to_subscribers(self) -> None:
        """subscribe 将 callback 加入 _subscribers 列表。"""
        vm = EmbeddedStatusCardViewModel()
        callback = lambda _state: None  # noqa: E731
        unsub = vm.subscribe(callback)
        assert callback in vm._subscribers
        unsub()

    def test_unsub_removes_callback(self) -> None:
        """unsub 将 callback 从 _subscribers 移除。"""
        vm = EmbeddedStatusCardViewModel()
        callback = lambda _state: None  # noqa: E731
        unsub = vm.subscribe(callback)
        unsub()
        assert callback not in vm._subscribers

    def test_dispose_clears_subscribers(self) -> None:
        """dispose 清空 _subscribers。"""
        vm = EmbeddedStatusCardViewModel()
        vm.subscribe(lambda _state: None)
        vm.subscribe(lambda _state: None)
        vm.dispose()
        assert len(vm._subscribers) == 0
