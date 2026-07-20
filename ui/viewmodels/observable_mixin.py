"""ObservableViewModelMixin — 消除 16 个 VM 的 subscribe/_notify/_set_state/dispose 样板 (P2-4)。

设计约束 (F1/F12):
- 不继承 ft.Observable (会破坏 hooks.py:112 的 ``(new_state)`` 单参数协议)
- subscribe 保持 ``(callback) -> unsub`` 单参数签名
- hooks.py:24-36 的 ``_ViewModelProtocol`` 是 Protocol (结构性类型), mixin 不破坏这个决策
- 子类可覆盖 subscribe/dispose 保留自定义逻辑:
  - ScreenerViewModel.subscribe 捕获 _main_loop (screener_view_model.py:180-192)
  - ScreenerViewModel.dispose 清理 _disposed/_background_tasks/_threadsafe_futures
    (screener_view_model.py:229-256)
  - TaskCenterViewModel.subscribe 捕获 _main_loop
  - HomeViewModel._notify 用 try/except 包裹 subscriber 调用
  - DataExplorerViewModel._notify 用 try/except 包裹 subscriber 调用
  - 3 个 config panel VM 的 _notify_on_change 链式通知 (独立于 subscribe/_notify 体系, 保留)

Mixin 不持有业务状态, 子类在 ``__init__`` 中初始化 ``self._state`` 和 ``self._subscribers``。
子类继承时指定泛型参数: ``class FooVM(ObservableViewModelMixin[FooState]):``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast


class ObservableViewModelMixin[T]:
    """ViewModel 可观察协议默认实现 mixin (P2-4)。

    消除 16 个 VM 重复的 subscribe/_notify/_set_state/dispose 样板。
    子类需在 ``__init__`` 中初始化:
        - ``self._state: T``  (具体 frozen dataclass)
        - ``self._subscribers: list[Callable[[T], None]]``

    子类可覆盖 subscribe/dispose 保留自定义逻辑
    (如 ScreenerViewModel.subscribe 捕获 _main_loop, dispose 清理 _background_tasks)。

    Mixin 不继承 ft.Observable, 不破坏 hooks.py:112 ``(new_state)`` 单参数协议
    (F1)。hooks.py:24-36 ``_ViewModelProtocol`` 是 Protocol, 子类通过结构性类型
    满足契约, mixin 仅提供默认实现 (F12)。
    """

    _state: T
    _subscribers: list[Callable[[T], None]]

    @property
    def state(self) -> T:
        """View 只读 state snapshot, 不可变。"""
        return self._state

    def subscribe(self, callback: Callable[[T], None]) -> Callable[[], None]:
        """订阅 state 变化, 返回退订函数。

        子类可覆盖以捕获 main loop (如 ScreenerViewModel/TaskCenterViewModel)。
        """
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        """state 变化后调所有订阅者, 传入新 snapshot。

        用 ``list(self._subscribers)`` 快照避免迭代中订阅者修改列表。
        子类可覆盖以用 try/except 包裹 subscriber 调用 (如 HomeViewModel/DataExplorerViewModel)。
        """
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes: Any) -> None:
        """Update state fields (``dataclasses.replace``) and notify subscribers.

        子类可覆盖以加 disposed guard (如 ScreenerViewModel)。
        """
        self._state = replace(cast(Any, self._state), **changes)
        self._notify()

    def dispose(self) -> None:
        """清理订阅者。

        子类可覆盖以清理额外资源 (background tasks, event loops, service subscriptions),
        但应在覆盖末尾调用 ``super().dispose()`` 或显式 ``self._subscribers.clear()``
        以保持订阅者清理。
        """
        self._subscribers.clear()
