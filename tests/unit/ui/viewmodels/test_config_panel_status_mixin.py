"""ConfigPanelViewModelBase 单元测试 (P2-A12/A15 吸收 ConfigPanelStatusMixin)。

原 P3-Duplicate-VM-Helpers (Task 4.2 TDD) 的 test_config_panel_status_mixin.py
直接演进：ConfigPanelStatusMixin 被 ConfigPanelViewModelBase 泛型基类吸收，本文件
改为验证基类提供的状态助手行为:
- _show_error: 设置 status_type="error" + status_message
- _show_warning: 设置 status_type="warning" + status_message
- _show_info: 设置 status_type="info" + status_message
- _raw_message: 将动态字符串包装为 Message(_RAW_MSG_KEY, {"default": text})

文件名保留 test_config_panel_status_mixin.py 以维持 weak_assertion_baseline.json
的条目稳定（重命名会触发增量门禁误报）。基类继承 ObservableViewModelMixin, 测试
通过构造 dummy VM (继承 ConfigPanelViewModelBase) 验证行为不变。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from ui.viewmodels import Message
from ui.viewmodels.config_panel_view_model_base import ConfigPanelViewModelBase, _RAW_MSG_KEY

pytestmark = pytest.mark.unit


# ============================================================
# 测试用 frozen state dataclass (含 status_message/status_type 字段,
# 模拟真实 config panel VM 的 state 结构)
# ============================================================


@dataclass(frozen=True)
class _DummyState:
    """测试用 frozen state (模拟 config panel VM 的 state)。"""

    status_message: Message | None = None
    status_type: str = "info"


# ============================================================
# Dummy VM: 继承 ConfigPanelViewModelBase
# 验证基类与 ObservableViewModelMixin 协作行为
# ============================================================


class _DummyConfigVM(ConfigPanelViewModelBase[_DummyState]):
    """测试用 VM: 验证 ConfigPanelViewModelBase 状态助手行为。"""

    def __init__(self) -> None:
        self._state = _DummyState()
        self._subscribers: list[Callable[[_DummyState], None]] = []


# ============================================================
# Tests
# ============================================================


class TestConfigPanelStatusMixinShowError:
    """_show_error: 设置 status_type="error" + status_message。"""

    def test_show_error_sets_status_type(self):
        vm = _DummyConfigVM()
        msg = Message("err_key")
        vm._show_error(msg)
        assert vm.state.status_type == "error"

    def test_show_error_sets_status_message(self):
        vm = _DummyConfigVM()
        msg = Message("err_key", {"field": "timeout"})
        vm._show_error(msg)
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "err_key"
        assert vm.state.status_message.params == {"field": "timeout"}

    def test_show_error_notifies_subscribers(self):
        """_show_error 通过 _set_state 触发订阅者通知。"""
        vm = _DummyConfigVM()
        received: list[_DummyState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._show_error(Message("err_key"))
        assert len(received) == 1
        assert received[0].status_type == "error"


class TestConfigPanelStatusMixinShowWarning:
    """_show_warning: 设置 status_type="warning" + status_message。"""

    def test_show_warning_sets_status_type(self):
        vm = _DummyConfigVM()
        msg = Message("warn_key")
        vm._show_warning(msg)
        assert vm.state.status_type == "warning"

    def test_show_warning_sets_status_message(self):
        vm = _DummyConfigVM()
        msg = Message("warn_key")
        vm._show_warning(msg)
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "warn_key"

    def test_show_warning_notifies_subscribers(self):
        vm = _DummyConfigVM()
        received: list[_DummyState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._show_warning(Message("warn_key"))
        assert len(received) == 1
        assert received[0].status_type == "warning"


class TestConfigPanelStatusMixinShowInfo:
    """_show_info: 设置 status_type="info" + status_message (仅 LLMConfigPanelViewModel 用)。"""

    def test_show_info_sets_status_type(self):
        vm = _DummyConfigVM()
        msg = Message("info_key")
        vm._show_info(msg)
        assert vm.state.status_type == "info"

    def test_show_info_sets_status_message(self):
        vm = _DummyConfigVM()
        msg = Message("info_key", {"provider": "deepseek"})
        vm._show_info(msg)
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "info_key"
        assert vm.state.status_message.params == {"provider": "deepseek"}

    def test_show_info_notifies_subscribers(self):
        vm = _DummyConfigVM()
        received: list[_DummyState] = []
        vm.subscribe(lambda s: received.append(s))
        vm._show_info(Message("info_key"))
        assert len(received) == 1
        assert received[0].status_type == "info"


class TestConfigPanelStatusMixinRawMessage:
    """_raw_message: 将动态字符串包装为 Message(_RAW_MSG_KEY, {"default": text})。"""

    def test_raw_message_returns_message_instance(self):
        msg = _DummyConfigVM._raw_message("dynamic error text")
        assert isinstance(msg, Message)

    def test_raw_message_uses_raw_msg_key(self):
        msg = _DummyConfigVM._raw_message("dynamic error text")
        assert msg.key == _RAW_MSG_KEY
        assert msg.key == "_raw_msg_"

    def test_raw_message_wraps_text_in_default_param(self):
        msg = _DummyConfigVM._raw_message("Token verified — Restricted APIs: foo")
        assert msg.params["default"] == "Token verified — Restricted APIs: foo"

    def test_raw_message_empty_string(self):
        msg = _DummyConfigVM._raw_message("")
        assert msg.params["default"] == ""

    def test_raw_message_is_static_method(self):
        """_raw_message 是 staticmethod, 无需实例即可调用。"""
        msg = _DummyConfigVM._raw_message("test")
        assert isinstance(msg, Message)
        assert msg.params["default"] == "test"

    def test_raw_message_callable_from_instance(self):
        """实例上也可调用 _raw_message (与现有 VM 行为一致)。"""
        vm = _DummyConfigVM()
        msg = vm._raw_message("from instance")
        assert msg.params["default"] == "from instance"
