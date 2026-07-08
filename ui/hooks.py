"""use_viewmodel hook — ViewModel 桥接 hook（CLAUDE.md §3.3 阻塞项）。

实现依据：
- 方案 §3.0.3 契约：``use_viewmodel(factory) -> (state, commands)``
- spike 项 5 结论：``use_ref(factory)`` 持久化 VM（factory 仅首次调用 1 次）；
  ``use_state(factory())`` 陷阱：每次渲染实例化
- spike 项 2 结论：``use_effect`` cleanup 是显式第三参数，非 setup 返回值
- spike 项 1 结论：``Observable.subscribe`` 弱引用，需保留 disposer（本 hook 用
  ref 持久化 unsub，防止 GC）

VM 契约（批次 2 改造后满足）：
- ``state`` 属性：不可变 snapshot（frozen dataclass）
- ``subscribe(callback) -> unsub``：订阅 state 变化
- ``dispose()``：清理资源
- ``_notify()``：VM 内部调用，遍历订阅者调 ``callback(self.state)``
"""

from collections.abc import Callable
from typing import Any, Protocol

import flet as ft


class _ViewModelProtocol(Protocol):
    """ViewModel 契约（hook 消费方所需的最低接口）。

    批次 2 改造后，所有 ViewModel 基类均满足此契约。定义为 Protocol（结构性
    类型）而非 ABC，避免引入运行时继承耦合；hook 仅依赖接口形状，不依赖具体基类。
    """

    @property
    def state(self) -> Any: ...

    def subscribe(self, callback: Any) -> Any: ...

    def dispose(self) -> None: ...


def use_viewmodel[T: _ViewModelProtocol](factory: Callable[[], T]) -> tuple[Any, T]:
    """消费 ViewModel 的桥接 hook。

    首次渲染实例化 VM + 订阅 ``_notify``；卸载时退订 + dispose。
    返回 ``(state snapshot, vm 实例)``。

    契约（对齐 CONTRIBUTING.md「MVVM 表现层」+ 方案 §3.0.3）：
    - 首次渲染：``factory()`` 实例化 VM，``vm.subscribe(set_state)`` 注册
    - ``_notify`` 触发：VM 遍历订阅者调 ``callback(self.state)``，hook 注册的
      callback 即 ``set_state(new_state)``，触发组件重渲染
    - 卸载时：``use_effect`` cleanup 调 ``unsub()`` + ``vm.dispose()``

    实现要点（spike 结论落地）：
    - ``use_ref(factory)`` 持久化 VM 实例（spike 项 5：factory 仅首次调用 1 次，
      避免 ``use_state(factory())`` 每次渲染实例化陷阱）
    - ``use_state(lambda: vm.state)`` 初始化 state snapshot（callable 形式，
      仅首次渲染访问 vm.state）
    - ``use_effect(setup, dependencies=[], cleanup=cleanup)`` 订阅/退订（spike 项 2：
      cleanup 是显式第三参数，非 setup 返回值）
    - ``unsub_ref`` 持久化退订函数，供 cleanup 使用
    """
    vm_ref = ft.use_ref(factory)
    vm = vm_ref.current
    # use_ref.current 类型为 T | None（MutableRef 构造允许 None），但 factory 在
    # 首次渲染时已执行，current 保证非 None。assert 收窄类型供后续属性访问。
    assert vm is not None

    state, set_state = ft.use_state(lambda: vm.state)

    unsub_ref = ft.use_ref(lambda: None)

    def setup() -> None:
        unsub_ref.current = vm.subscribe(lambda new_state: set_state(new_state))

    def cleanup() -> None:
        if unsub_ref.current is not None:
            unsub_ref.current()
            unsub_ref.current = None
        vm.dispose()

    ft.use_effect(setup, dependencies=[], cleanup=cleanup)

    return state, vm
