"""component_renderer 公共渲染辅助的边界场景测试。

验证 ``tests/unit/ui/component_renderer.py`` 提供的渲染辅助在
mount/render/unmount 各阶段的行为正确性，为后续 28 个 UI 文件的
组件体测试提供基础设施保障。
"""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest
from flet.controls.context import _context_page
from flet.components.component import Component

from tests.unit.ui.component_renderer import (
    FakePage,
    FakeSession,
    attach_fake_page,
    make_component,
    render_once,
    run_mount_effects,
    run_render_effects,
    run_unmount_effects,
)

pytestmark = pytest.mark.unit


# ============================================================================
# 测试用组件
# ============================================================================

effect_log: list[str] = []


@ft.component
def SimpleComponent(label: str = "default"):
    """无状态组件：仅接收 props 返回 ft.Text。"""
    return ft.Text(value=label)


@ft.component
def StatefulComponent(initial: int = 0):
    """有状态组件：use_state + use_effect，验证 effect 生命周期。"""
    count, set_count = ft.use_state(initial)

    def _setup() -> None:
        effect_log.append("setup")

    def _cleanup() -> None:
        effect_log.append("cleanup")

    ft.use_effect(_setup, [], _cleanup)
    return ft.Text(value=f"count={count}")


@ft.component
def ReactiveComponent(step: int = 0):
    """deps 变化触发 effect 的组件：验证 run_render_effects 行为。"""

    def _setup() -> None:
        effect_log.append(f"effect:step={step}")

    def _cleanup() -> None:
        effect_log.append(f"cleanup:step={step}")

    ft.use_effect(_setup, [step], _cleanup)
    return ft.Text(value=f"step={step}")


# ============================================================================
# 测试用例
# ============================================================================


class TestMakeComponent:
    """make_component 行为验证。"""

    def test_returns_component_instance(self) -> None:
        """返回 Component 实例。"""
        component = make_component(SimpleComponent, label="hello")
        assert isinstance(component, Component)

    def test_mounted_flag_set(self) -> None:
        """创建后 mounted=True（允许后续 effect 调度）。"""
        component = make_component(SimpleComponent)
        assert component._state.mounted is True

    def test_args_kwargs_preserved(self) -> None:
        """args/kwargs 被保存到 Component 实例。"""
        component = make_component(SimpleComponent, label="custom")
        assert component.kwargs == {"label": "custom"}


class TestRenderOnce:
    """render_once 行为验证。"""

    def test_returns_fn_return_value(self) -> None:
        """返回组件函数的返回值（ft.Control）。"""
        component = make_component(SimpleComponent, label="hello")
        result = render_once(component)
        assert isinstance(result, ft.Text)
        assert result.value == "hello"

    def test_resets_hook_cursor(self) -> None:
        """每次调用重置 hook_cursor（允许重复渲染）。"""
        component = make_component(StatefulComponent, initial=42)
        # 第一次渲染
        render_once(component)
        # 第二次渲染（若 hook_cursor 未重置会越界）
        result = render_once(component)
        assert isinstance(result, ft.Text)


class TestAttachFakePage:
    """attach_fake_page 行为验证。"""

    def test_sets_context_page(self) -> None:
        """设置 _context_page ContextVar。"""
        component = make_component(SimpleComponent)
        page = attach_fake_page(component)
        assert _context_page.get() is page

    def test_default_page_created(self) -> None:
        """未传入 page 时自动创建 FakePage。"""
        component = make_component(SimpleComponent)
        page = attach_fake_page(component)
        assert isinstance(page, FakePage)
        assert isinstance(page.session, FakeSession)

    def test_custom_page_used(self) -> None:
        """传入 page 时使用该 page。"""
        custom_page = FakePage()
        component = make_component(SimpleComponent)
        result = attach_fake_page(component, custom_page)
        assert result is custom_page
        assert _context_page.get() is custom_page


class TestFakeSession:
    """FakeSession 行为验证。"""

    def test_schedule_update_captures_component(self) -> None:
        """schedule_update 捕获 component 调用。"""
        session = FakeSession()
        component = make_component(SimpleComponent)
        session.schedule_update(component)
        assert component in session.scheduled_updates

    def test_schedule_effect_setup_executes_synchronously(self) -> None:
        """schedule_effect(is_cleanup=False) 同步执行 setup。"""
        effect_log.clear()
        session = FakeSession()

        class FakeHook:
            def __init__(self) -> None:
                self.cleanup: Any = None

            def setup(self) -> None:
                effect_log.append("setup")

        hook = FakeHook()
        session.schedule_effect(hook, is_cleanup=False)
        assert "setup" in effect_log

    def test_schedule_effect_cleanup_executes_synchronously(self) -> None:
        """schedule_effect(is_cleanup=True) 同步执行 cleanup。"""
        effect_log.clear()
        session = FakeSession()

        class FakeHook:
            def __init__(self) -> None:
                self.cleanup: Any = None

            def setup(self) -> None:
                pass

        hook = FakeHook()
        hook.cleanup = lambda: effect_log.append("cleanup")  # noqa: E731
        session.schedule_effect(hook, is_cleanup=True)
        assert "cleanup" in effect_log

    def test_schedule_effect_cleanup_none_does_not_raise(self) -> None:
        """cleanup 为 None 时不抛异常。"""
        session = FakeSession()

        class FakeHook:
            def __init__(self) -> None:
                self.cleanup = None

            def setup(self) -> None:
                pass

        hook = FakeHook()
        # 不应抛异常
        session.schedule_effect(hook, is_cleanup=True)


class TestLifecycleHelpers:
    """run_mount_effects / run_render_effects / run_unmount_effects 生命周期验证。"""

    def setup_method(self) -> None:
        effect_log.clear()

    def test_mount_effects_triggers_setup(self) -> None:
        """run_mount_effects 触发 effect setup。"""
        component = make_component(StatefulComponent, initial=0)
        run_mount_effects(component)
        assert "setup" in effect_log
        assert "cleanup" not in effect_log

    def test_unmount_effects_triggers_cleanup(self) -> None:
        """run_unmount_effects 触发 effect cleanup。"""
        component = make_component(StatefulComponent, initial=0)
        run_mount_effects(component)
        assert "setup" in effect_log
        effect_log.clear()

        run_unmount_effects(component)
        assert "cleanup" in effect_log

    def test_mount_then_unmount_full_lifecycle(self) -> None:
        """完整生命周期：mount → unmount。"""
        component = make_component(StatefulComponent, initial=0)
        run_mount_effects(component)
        assert effect_log == ["setup"]

        effect_log.clear()
        run_unmount_effects(component)
        assert effect_log == ["cleanup"]

    def test_render_effects_does_not_retrigger_setup_with_empty_deps(self) -> None:
        """空 deps 的 effect 在 re-render 时不重新执行 setup。"""
        component = make_component(StatefulComponent, initial=0)
        run_mount_effects(component)
        assert effect_log == ["setup"]

        effect_log.clear()
        run_render_effects(component)
        # 空 deps → re-render 不重新 setup，也不 cleanup
        assert effect_log == []

    def test_render_effects_retriggers_with_changed_deps(self) -> None:
        """deps 变化时 re-render 触发 cleanup + setup。

        注意：Flet 的 use_effect cleanup 闭包捕获的是当前渲染的 step 值
        （非注册时的值），因此 cleanup 标记用新 step 值。本测试验证
        cleanup + setup 均被触发，不依赖具体 step 值。
        """
        component = make_component(ReactiveComponent, step=0)
        run_mount_effects(component)
        assert "effect:step=0" in effect_log

        effect_log.clear()
        # 改变 kwargs 模拟 deps 变化
        component.kwargs = {"step": 1}
        run_render_effects(component)
        # cleanup + setup 均被触发（step 值为当前渲染值 1）
        assert any("cleanup:step=" in entry for entry in effect_log)
        assert "effect:step=1" in effect_log

    def test_unmount_sets_mounted_false(self) -> None:
        """run_unmount_effects 设置 mounted=False。"""
        component = make_component(StatefulComponent, initial=0)
        run_mount_effects(component)
        assert component._state.mounted is True

        run_unmount_effects(component)
        assert component._state.mounted is False

    def test_mount_returns_page(self) -> None:
        """run_mount_effects 返回 FakePage 实例（用于后续断言）。"""
        component = make_component(StatefulComponent, initial=0)
        page = run_mount_effects(component)
        assert isinstance(page, FakePage)
