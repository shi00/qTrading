"""ConfigPanelViewModelBase — config panel VM 泛型基类 (P2-A12/A15 收敛)。

吸收原 ConfigPanelStatusMixin (P3-Duplicate-VM-Helpers) 的状态助手方法
(_show_error/_show_warning/_show_info/_raw_message)，并提供 config panel VM
共用的骨架 (_show_success/_notify_on_change/reload_config)，消除 5 个
config panel VM (Tushare/LLM/Database/LocalModel/Failover) 的同构代码。

子类继承本基类即同时获得 ObservableViewModelMixin 的可观察协议。

设计约束:
- 基类不持有业务状态，仅提供助手方法与骨架（与 ObservableViewModelMixin 一致）
- _set_state 由 ObservableViewModelMixin 提供；self._state 需含
  status_message/status_type 字段，由子类在 __init__ 中初始化
- _RAW_MSG_KEY 为模块级常量，仅 _raw_message 使用
- reload_config 调用子类实现的 _load_config_to_state()（隐式协议，非
  abstractmethod：FailoverConfigPanelViewModel 覆盖 reload_config 且不实现
  _load_config_to_state，abstractmethod 会使其无法实例化）
"""

from __future__ import annotations

from collections.abc import Callable

from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

_RAW_MSG_KEY = "_raw_msg_"


class ConfigPanelViewModelBase[TState](ObservableViewModelMixin[TState]):
    """config panel VM 泛型基类。

    提供 _show_error/_show_warning/_show_info/_show_success/_raw_message 状态助手
    与 _notify_on_change/reload_config 共用骨架。save/test/validate 等业务命令
    由各子类实现（差异大，不做统一抽象）。
    """

    # 由子类 __init__ 注入的 on_change 回调（_notify_on_change 骨架使用）
    _on_change: Callable[[], None] | None

    # --- 状态助手 ---

    def _show_error(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="error")

    def _show_warning(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="warning")

    def _show_info(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="info")

    def _show_success(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="success")

    @staticmethod
    def _raw_message(text: str) -> Message:
        """将动态字符串包装为 Message。I18n.get(_RAW_MSG_KEY, default=text) 返回 text。"""
        return Message(_RAW_MSG_KEY, {"default": text})

    # --- 共用骨架 ---

    def _notify_on_change(self) -> None:
        if self._on_change:
            self._on_change()

    def reload_config(self) -> None:
        """重新从 ConfigHandler 加载配置到 state。"""
        self._load_config_to_state()
        self._notify()

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（子类实现）。"""
        raise NotImplementedError("子类必须实现 _load_config_to_state()")
