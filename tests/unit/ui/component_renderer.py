"""@ft.component 声明式组件渲染辅助（单元测试基础设施）。

提供在无真实 Flet Renderer 上下文下驱动有状态声明式组件的能力：
- ``FakeSession``：同步执行 ``schedule_effect``（setup/cleanup 真实执行）
- ``FakePage``：含 session 的轻量 page
- ``make_component``：创建 Component 实例
- ``render_once``：在 Renderer 上下文中驱动一次组件函数执行
- ``attach_fake_page``：注入 ``_context_page`` ContextVar
- ``run_mount_effects`` / ``run_render_effects`` / ``run_unmount_effects``：模拟生命周期

典型用法::

    from tests.unit.ui.component_renderer import (
        make_component, run_mount_effects, run_unmount_effects,
    )

    def test_xxx(mock_i18n_state, mock_app_colors_state):
        component = make_component(MyView, prop1=val1)
        run_mount_effects(component)
        # 验证控件树 / 事件处理器 / VM 交互
        run_unmount_effects(component)

注意：需配合 ``conftest.py`` 的 ``_reset_context_page`` autouse fixture
（清理 ``_context_page`` ContextVar，防止 FakePage 跨测试泄漏）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

from flet.components.component import Component, Renderer


@dataclass
class FakeSession:
    """伪造的 page.session，捕获 schedule_update / schedule_effect 调用。

    ``schedule_effect`` 同步执行 hook.setup() 或 hook.cleanup()，
    使测试能验证 subscribe/dispose 等副作用的实际执行行为。
    """

    scheduled_updates: list[Component] = field(default_factory=list)
    scheduled_effects: list[tuple[Any, bool]] = field(default_factory=list)

    def schedule_update(self, component: Component) -> None:
        self.scheduled_updates.append(component)

    def schedule_effect(self, hook: Any, is_cleanup: bool) -> None:
        self.scheduled_effects.append((hook, is_cleanup))
        # 同步执行 effect（测试需验证 setup/cleanup 实际执行，而非仅调度）
        result: Any = None
        if is_cleanup:
            if hook.cleanup is not None:
                result = hook.cleanup()
        else:
            result = hook.setup()
        # async effect：在新事件循环中同步执行返回的 coroutine
        if asyncio.iscoroutine(result):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(result)
            finally:
                loop.close()

    def patch_control(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        pass


@dataclass
class FakePage:
    """伪造的 page 对象，仅暴露 session 与 enable_components_mode 所需接口。

    含 ``_services`` 属性（``FakeServiceRegistry``），使 ``ft.FilePicker``
    等服务控件在 ``init()`` 时调用 ``context.page._services.register_service``
    不抛异常。
    """

    session: FakeSession = field(default_factory=FakeSession)

    def __post_init__(self) -> None:
        self._services = FakeServiceRegistry()
        self.services: list[Any] = []


class FakeServiceRegistry:
    """伪造的 ServiceRegistry，吸收 register_service/unregister_services 调用。"""

    def register_service(self, service: Any) -> None:  # noqa: ARG002
        pass

    def unregister_services(self) -> None:
        pass


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
