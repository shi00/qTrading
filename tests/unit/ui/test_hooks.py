"""use_viewmodel hook 单元测试。

测试策略：
- 创建符合 VM 契约的 FakeViewModel（state 属性、subscribe/callback、dispose）
- 用 Flet 组件渲染辅助驱动 @ft.component 组件生命周期
- 验证 hook 在 mount/render/unmount 各阶段的行为
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    attach_fake_page,
    make_component,
    render_once,
    run_mount_effects,
    run_render_effects,
    run_unmount_effects,
)
from ui.hooks import use_viewmodel

pytestmark = pytest.mark.unit


# ============================================================================
# FakeViewModel（符合 VM 契约：state 属性、subscribe/callback、dispose）
# ============================================================================


@dataclass(frozen=True)
class FakeState:
    """不可变 state snapshot（模拟 frozen dataclass 契约）。"""

    value: int = 0
    label: str = "initial"


class FakeViewModel:
    """符合 VM 契约的测试用 ViewModel。

    契约：
    - ``state`` 属性：不可变 snapshot
    - ``subscribe(callback) -> unsub``：订阅 state 变化
    - ``dispose()``：清理资源
    - ``_notify()``：VM 内部调用，遍历订阅者调 callback(self.state)
    """

    def __init__(self) -> None:
        self._state: FakeState = FakeState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.subscribe_called: bool = False

    @property
    def state(self) -> FakeState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self.subscribe_called = True
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in self._subscribers:
            cb(snapshot)

    def set_value(self, new_value: int) -> None:
        """command：修改 state -> _notify -> 订阅者收到新 snapshot。"""
        self._state = replace(self._state, value=new_value, label=f"v{new_value}")
        self._notify()

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()


# ============================================================================
# 测试用组件
# ============================================================================

vm_creation_count: dict[str, int] = {"count": 0}


def make_vm() -> FakeViewModel:
    vm_creation_count["count"] += 1
    return FakeViewModel()


@ft.component
def ConsumingComponent():
    """消费 use_viewmodel 的测试组件。"""
    state, vm = use_viewmodel(make_vm)
    return ft.Column(
        controls=[
            ft.Text(value=f"value={state.value}"),
            ft.Text(value=f"label={state.label}"),
        ],
    )


@ft.component
def ConsumingExternalComponent(vm: FakeViewModel):
    """外部 VM 模式消费 use_viewmodel 的测试组件（vm 由消费方注入）。"""
    state, _ = use_viewmodel(vm=vm)
    return ft.Column(controls=[ft.Text(value=f"value={state.value}")])


# ============================================================================
# 测试用例
# ============================================================================


class TestUseViewmodelHook:
    """use_viewmodel hook 行为断言（方案 §3.3.0 配套测试）。"""

    def setup_method(self) -> None:
        """每个测试前重置 VM 创建计数。"""
        vm_creation_count["count"] = 0

    def test_first_render_instantiates_vm_and_returns_current_state(self) -> None:
        """首次渲染实例化 VM 并返回当前 state。"""
        component = make_component(ConsumingComponent)
        run_mount_effects(component)

        # VM 被实例化 1 次
        assert vm_creation_count["count"] == 1

        # subscribe 被调用（mount effect 注册订阅）
        # 通过 hook 链验证：第 3 个 hook 是 use_effect，其 setup 已执行
        # 直接验证：VM 的 subscribe_called 标记
        # 但 VM 实例在 ref 中，需从 component 取
        # use_ref hook 是第 0 个 hook，ref.current 是 VM
        vm_ref_hook: Any = component._state.hooks[0]
        vm = vm_ref_hook.ref.current
        assert isinstance(vm, FakeViewModel)
        assert vm.subscribe_called is True

        # use_state hook 是第 1 个 hook，其 value 是 state snapshot
        state_hook: Any = component._state.hooks[1]
        assert isinstance(state_hook.value, FakeState)
        assert state_hook.value.value == 0
        assert state_hook.value.label == "initial"

    def test_notify_triggers_state_update(self) -> None:
        """_notify 触发后 state 更新（set_state 被调用）。

        VM._notify 遍历订阅者调 callback(self.state)，hook 注册的 callback
        即 set_state(new_state)，触发 state_hook.value 更新。
        """
        component = make_component(ConsumingComponent)
        page = run_mount_effects(component)

        # 获取 VM 实例
        vm_ref_hook: Any = component._state.hooks[0]
        vm = vm_ref_hook.ref.current

        # 获取 state_hook 初始值
        state_hook: Any = component._state.hooks[1]
        assert state_hook.value.value == 0

        # 模拟 VM command：set_value -> _notify -> set_state
        vm.set_value(42)

        # state_hook.value 应被 set_state 更新为新 snapshot
        assert state_hook.value.value == 42
        assert state_hook.value.label == "v42"

        # 验证 schedule_update 被调用（set_state 触发重渲染调度）
        assert len(page.session.scheduled_updates) > 0

    def test_unmount_calls_unsub_and_dispose(self) -> None:
        """卸载时 unsub + dispose 被调用。"""
        component = make_component(ConsumingComponent)
        run_mount_effects(component)

        vm_ref_hook: Any = component._state.hooks[0]
        vm = vm_ref_hook.ref.current

        # unmount 前订阅者列表非空
        assert len(vm._subscribers) == 1
        assert vm.dispose_called is False

        run_unmount_effects(component)

        # dispose 被调用
        assert vm.dispose_called is True
        # dispose 清空订阅者列表
        assert len(vm._subscribers) == 0

    def test_multiple_renders_do_not_reinstantiate_vm(self) -> None:
        """多次渲染不重复实例化 VM（VM 实例稳定引用）。"""
        component = make_component(ConsumingComponent)
        run_mount_effects(component)

        # 首次渲染后 VM 创建 1 次
        assert vm_creation_count["count"] == 1

        vm_ref_hook: Any = component._state.hooks[0]
        vm_id_first = id(vm_ref_hook.ref.current)

        # 模拟 re-render
        run_render_effects(component)
        assert vm_creation_count["count"] == 1  # 仍只创建 1 次

        run_render_effects(component)
        assert vm_creation_count["count"] == 1  # 仍只创建 1 次

        # VM 实例引用稳定（同一对象）
        vm_id_after = id(vm_ref_hook.ref.current)
        assert vm_id_first == vm_id_after

    def test_subscribe_compensates_mount_before_notify_latest_state(self) -> None:
        """UIX-03: 订阅建立前已发出的 VM 变更在 mount 后经补偿同步被接收。

        复现场景：VM 在渲染期构造、mount effect 订阅建立之前即完成加载并变更 state
        （本地缓存命中最易触发）。此变更对空订阅者 _notify 静默丢弃（不进 pending 缓冲）。
        修复要求 setup() 订阅后补一次 `set_state(resolved_vm.state)`，使首帧渲染到最终状态。
        """
        component = make_component(ConsumingComponent)
        attach_fake_page(component)
        render_once(component)

        # 首次渲染已构造 VM（use_ref factory），但 mount effects 尚未执行 → 尚未 subscribe
        vm_ref_hook: Any = component._state.hooks[0]
        vm = vm_ref_hook.ref.current
        state_hook: Any = component._state.hooks[1]
        assert state_hook.value.value == 0  # 首帧快照（use_state 初始化器已捕获）

        # 模拟 VM 在订阅建立前完成为加载并变更 state（_notify 时无订阅者，丢弃）
        vm.set_value(99)
        assert vm._subscribers == []  # hook 尚未 subscribe

        # 执行 mount effects：setup() 订阅 + 补偿同步
        component._state.mounted = True
        component._run_mount_effects()

        # 修复后 state 已同步为最新快照（无修复时停留在首帧 0）
        assert state_hook.value.value == 99
        assert state_hook.value.label == "v99"

    def test_external_vm_swaps_instance_resubscribes(self) -> None:
        """UIX-04: 外部 VM 模式切换实例后自动重订阅并同步新实例快照。

        deps 用 `resolved_vm` 对象身份后, 实例变化会 cleanup 旧订阅 + 重订阅 + 补偿同步,
        而相同实例下 effect 不重跑(保证现网外部调用点零行为变化)。
        """
        vm1 = FakeViewModel()
        vm1.set_value(1)
        component = make_component(ConsumingExternalComponent, vm=vm1)
        run_mount_effects(component)

        # 外部模式: use_state 是 hook0; state 已同步为 vm1 快照, 已订阅 vm1
        state_hook: Any = component._state.hooks[0]
        assert state_hook.value.value == 1
        assert len(vm1._subscribers) == 1

        # 同一实例的 re-render: effect 不重跑(不重复订阅, 与现网一致)
        run_render_effects(component)
        assert len(vm1._subscribers) == 1

        # 更换实例: 父组件传入不同 vm
        vm2 = FakeViewModel()
        vm2.set_value(2)
        component.kwargs = {"vm": vm2}
        run_render_effects(component)

        # vm1 被退订, vm2 被订阅, state 同步为 vm2 快照(原 deps=[] 会停留在 vm1)
        assert len(vm1._subscribers) == 0
        assert len(vm2._subscribers) == 1
        assert state_hook.value.value == 2
