"""Spike 脚本共享辅助。

提供在不启动完整 Flet app 的前提下驱动 `@ft.component` 组件渲染的最小框架，
用于验证 hooks（use_state / use_effect / use_ref）在 mount/update/unmount
各阶段的行为。

设计依据（flet 0.85.3 源码）：
- `Component.before_update()` / `Component.update()` 内部调用
  `Renderer(self).render(self.fn, *self.args, **self.kwargs)`，此时
  `Renderer._root_component = self`，`_Frame.__enter__` 将 self 推入
  `_render_stack`，使 `current_component()` 可返回该组件，hooks 得以绑定。
- `_run_mount_effects` / `_run_render_effects` / `_run_unmount_effects`
  依赖 `context.page.session.schedule_effect(...)`。spike 中通过伪造一个
  轻量 session 对象捕获 effect 调度调用，从而验证 cleanup 执行顺序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flet.components.component import Component, Renderer


@dataclass
class FakeSession:
    """伪造的 page.session，捕获 schedule_update / schedule_effect 调用。

    `Component._schedule_update` 调用 `context.page.session.schedule_update(self)`；
    `Component._schedule_effect` 调用 `context.page.session.schedule_effect(hook, is_cleanup)`。
    """

    scheduled_updates: list[Component] = field(default_factory=list)
    scheduled_effects: list[tuple[Any, bool]] = field(default_factory=list)

    def schedule_update(self, component: Component) -> None:
        self.scheduled_updates.append(component)

    def schedule_effect(self, hook: Any, is_cleanup: bool) -> None:
        self.scheduled_effects.append((hook, is_cleanup))

    def patch_control(self, *args, **kwargs) -> None:  # noqa: ARG002
        # spike 不验证 patch 细节
        pass


@dataclass
class FakePage:
    """伪造的 page 对象，仅暴露 session 与 enable_components_mode 所需接口。"""

    session: FakeSession = field(default_factory=FakeSession)


def make_component(fn: Any, *args: Any, **kwargs: Any) -> Component:
    """创建一个 `@ft.component` 装饰函数对应的 `Component` 实例。

    绕过 `Renderer.render_component` 的正常路径（需要 current_renderer），
    直接构造 `Component`，便于 spike 中手动驱动其生命周期。
    """
    c = Component(fn=fn, args=args, kwargs=kwargs)
    c._state.mounted = True
    return c


def render_once(component: Component) -> Any:
    """在 Renderer 上下文中驱动一次组件函数执行，返回 fn 返回值。

    模拟 `Component.before_update` 中的核心渲染步骤（不触发 session.patch）。
    hooks 在此过程中被创建/读取。

    关键：必须调用 `fn.__component_impl__`（原始函数）而非 `fn`（包装器），
    否则 `fn()` 会通过 `component_wrapper` → `render_component` 创建子 Component
    而非执行函数体，hooks 不会被创建。
    """
    component._state.hook_cursor = 0
    component._detach_observable_subscriptions()
    component._subscribe_observable_args(component.args, component.kwargs)
    renderer = Renderer(component)
    # __component_impl__ 由 @ft.component 装饰器设置，指向原始未包装函数
    fn = getattr(component.fn, "__component_impl__", component.fn)
    with renderer.with_context(), renderer._Frame(renderer, component):
        return fn(*component.args, **component.kwargs)


def attach_fake_page(component: Component, page: FakePage | None = None) -> FakePage:  # noqa: ARG001
    """为组件绑定伪造 page，使 effect 调度路径可走通。

    `Component._schedule_effect` → `context.page.session.schedule_effect`。
    `context.page` 由 `flet.controls.context._context_page` ContextVar 提供，
    spike 中通过 `_context_page.set(page)` 实现。
    """
    if page is None:
        page = FakePage()
    from flet.controls.context import _context_page

    _context_page.set(page)
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
