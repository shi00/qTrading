"""render_component helper 单测（方案 §3.3.2）。

验证：
- 无状态组件可渲染并返回控件树
- 有状态组件抛错（use_state 需 Renderer 上下文）
"""

import flet as ft
import pytest

from tests.unit.ui.render_helper import render_component

pytestmark = pytest.mark.unit


@ft.component
def _StatelessLabel(label: str):
    """无状态组件：纯展示 Text。"""
    return ft.Text(label)


@ft.component
def _StatelessColumn(items: list[str]):
    """无状态组件：Column 内多个 Text。"""
    return ft.Column([ft.Text(item) for item in items])


@ft.component
def _StatefulCounter():
    """有状态组件：含 use_state。"""
    count, _ = ft.use_state(0)
    return ft.Text(f"count: {count}")


class TestRenderComponentStateless:
    def test_renders_simple_text(self):
        tree = render_component(_StatelessLabel, label="hello")
        assert isinstance(tree, ft.Text)
        assert tree.value == "hello"

    def test_renders_column_with_children(self):
        tree = render_component(_StatelessColumn, items=["a", "b", "c"])
        assert isinstance(tree, ft.Column)
        assert len(tree.controls) == 3
        # tree.controls 类型为 list[Control]，断言 narrowing 到 ft.Text 后访问 .value
        first = tree.controls[0]
        last = tree.controls[2]
        assert isinstance(first, ft.Text)
        assert isinstance(last, ft.Text)
        assert first.value == "a"
        assert last.value == "c"

    def test_returns_control_instance(self):
        tree = render_component(_StatelessLabel, label="test")
        # 返回的是真实 ft.Control 实例，非 MagicMock
        assert isinstance(tree, ft.Control)


class TestRenderComponentStateful:
    def test_stateful_component_raises(self):
        """有状态组件无 Renderer 上下文时抛 RuntimeError（use_state 依赖 Renderer）。"""
        with pytest.raises(RuntimeError, match="No current renderer"):
            render_component(_StatefulCounter)
