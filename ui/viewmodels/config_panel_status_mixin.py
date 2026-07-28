"""ConfigPanelStatusMixin — config panel VM 状态助手混入 (P3-Duplicate-VM-Helpers)。

消除 4 个 config panel ViewModel 的 _show_error/_show_warning/_raw_message/_show_info 重复。
签名与原 VM 实现逐字节一致, 行为不变。

依赖: 子类需同时继承 ObservableViewModelMixin (提供 _set_state) 并在 __init__ 中
初始化 self._state (含 status_message/status_type 字段)。

设计约束:
- Mixin 不持有业务状态, 仅提供助手方法 (与 ObservableViewModelMixin 一致)
- _set_state 声明为类级类型注解, 由 ObservableViewModelMixin 在具体 VM 中提供
- _RAW_MSG_KEY 为模块级常量, 仅 _raw_message 使用
"""

from __future__ import annotations

from collections.abc import Callable

from ui.viewmodels import Message

_RAW_MSG_KEY = "_raw_msg_"


class ConfigPanelStatusMixin:
    """Config panel VM 状态助手混入 (P3-Duplicate-VM-Helpers)。

    提供 _show_error/_show_warning/_show_info/_raw_message 4 个助手方法,
    消除 4 个 config panel VM (LocalModel/Tushare/LLM/Database) 的同构代码。

    子类需同时继承 ObservableViewModelMixin 并初始化 self._state
    (含 status_message/status_type 字段)。
    """

    # Provided by ObservableViewModelMixin in concrete VMs
    _set_state: Callable[..., None]

    def _show_error(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="error")

    def _show_warning(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="warning")

    def _show_info(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="info")

    @staticmethod
    def _raw_message(text: str) -> Message:
        """将动态字符串包装为 Message。I18n.get(_RAW_MSG_KEY, default=text) 返回 text。"""
        return Message(_RAW_MSG_KEY, {"default": text})
