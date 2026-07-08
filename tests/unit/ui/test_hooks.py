"""use_viewmodel hook 单元测试。

测试策略：
- 创建符合 VM 契约的 FakeViewModel（state 属性、subscribe/callback、dispose）
- 用 Flet 组件渲染辅助驱动 @ft.component 组件生命周期
- 验证 hook 在 mount/render/unmount 各阶段的行为

渲染辅助参考 scripts/spike_ui_debt/_spike_helpers.py（批次 0 spike 验证用），
批次 2.5 将新增正式的 tests/unit/ui/render_helper.py，届时迁移。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, cast

import flet as ft
import pytest
from flet.components.component import Component, Renderer

from ui.hooks import use_viewmodel

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_context_page():
    """每个测试后清理 _context_page，防止 FakePage 泄漏到其他测试。

    attach_fake_page 调用 _context_page.set(FakePage) 修改 ContextVar，
    若不清理会跨测试泄漏，导致后续 UI 测试因 page 类型不匹配而失败。
    """
    yield
    from flet.controls.context import _context_page

    _context_page.set(None)


# ============================================================================
# 最小组件渲染辅助（参考 spike helpers，批次 2.5 迁移到 render_helper.py）
# ============================================================================


@dataclass
class FakeSession:
    """伪造的 page.session，捕获 schedule_update / schedule_effect 调用。

    与 spike helpers 的 FakeSession 不同，本测试需要 effect 实际执行（验证
    subscribe/dispose 调用），因此 ``schedule_effect`` 同步执行 hook.setup()
    或 hook.cleanup()。spike helpers 仅验证调度行为，此处需验证执行行为。
    """

    scheduled_updates: list[Component] = field(default_factory=list)
    scheduled_effects: list[tuple[Any, bool]] = field(default_factory=list)

    def schedule_update(self, component: Component) -> None:
        self.scheduled_updates.append(component)

    def schedule_effect(self, hook: Any, is_cleanup: bool) -> None:
        self.scheduled_effects.append((hook, is_cleanup))
        # 同步执行 effect（测试需验证 setup/cleanup 实际执行，而非仅调度）
        if is_cleanup:
            if hook.cleanup is not None:
                hook.cleanup()
        else:
            hook.setup()

    def patch_control(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass


@dataclass
class FakePage:
    """伪造的 page 对象，仅暴露 session 与 enable_components_mode 所需接口。"""

    session: FakeSession = field(default_factory=FakeSession)


def make_component(fn: Any, *args: Any, **kwargs: Any) -> Component:
    """创建 @ft.component 装饰函数对应的 Component 实例。"""
    c = Component(fn=fn, args=args, kwargs=kwargs)
    c._state.mounted = True
    return c


def render_once(component: Component) -> Any:
    """在 Renderer 上下文中驱动一次组件函数执行，返回 fn 返回值。"""
    component._state.hook_cursor = 0
    component._detach_observable_subscriptions()
    component._subscribe_observable_args(component.args, component.kwargs)
    renderer = Renderer(component)
    fn = getattr(component.fn, "__component_impl__", component.fn)
    with renderer.with_context(), renderer._Frame(renderer, component):
        return fn(*component.args, **component.kwargs)


def attach_fake_page(component: Component, page: FakePage | None = None) -> FakePage:
    """为组件绑定伪造 page，使 effect 调度路径可走通。"""
    if page is None:
        page = FakePage()
    from flet.controls.context import _context_page

    # _context_page 期望 Page | None，测试注入 FakePage 驱动 effect 调度路径
    _context_page.set(cast(Any, page))
    return page


def run_mount_effects(component: Component, page: FakePage | None = None) -> FakePage:
    """模拟组件 mount：驱动首次渲染 + 触发 mount effects。"""
    page = attach_fake_page(component, page)
    render_once(component)
    component._state.mounted = True
    component._run_mount_effects()
    return page


def run_render_effects(component: Component) -> None:
    """模拟组件 re-render 后的 effect 调度（deps 变化检测）。"""
    render_once(component)
    component._run_render_effects()


def run_unmount_effects(component: Component) -> None:
    """模拟组件 unmount：触发 cleanup effects。"""
    component._state.mounted = False
    component._detach_observable_subscriptions()
    component._run_unmount_effects()


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
