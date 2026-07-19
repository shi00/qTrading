"""ResizableSplitter 契约守护测试 — Phase A.3 声明式重写 (P1-1/P2-1 回调模式).

覆盖:
- 纯函数: _clamp_width / _DragCache
- 组件契约: @ft.component 装饰标记、参数签名、返回类型注解、回调参数默认值

声明式组件组合 (@ft.component + use_state/use_effect) 是有状态的, 在无 renderer
环境下会抛 RuntimeError, 由集成测试 (flet_test_page fixture) 覆盖, 不在本单元测试范围
(对齐 test_task_center_view.py 模式)。

P1-1/P2-1 改造: _load_persisted_width / _persist_width 模块级函数已移除,
宽度读写经 on_load_width / on_persist_width 回调上抛父 View VM (具体行为覆盖见
test_resizable_splitter_body.py)。
"""

import inspect

import flet as ft
import pytest

from ui.components.resizable_splitter import (
    _clamp_width,
    _DragCache,
    ResizableSplitter,
)

pytestmark = pytest.mark.unit

DEFAULT_WIDTH = 360
MIN_WIDTH = 280
MAX_WIDTH = 600
CONFIG_KEY = "test_panel_width"


# --- 1. _clamp_width ---


class TestClampWidth:
    def test_within_bounds_returns_int(self):
        assert _clamp_width(450, MIN_WIDTH, MAX_WIDTH) == 450

    def test_exceeds_max_clamps_to_max(self):
        assert _clamp_width(700, MIN_WIDTH, MAX_WIDTH) == MAX_WIDTH

    def test_below_min_clamps_to_min(self):
        assert _clamp_width(200, MIN_WIDTH, MAX_WIDTH) == MIN_WIDTH

    def test_float_truncated_to_int(self):
        assert _clamp_width(350.9, MIN_WIDTH, MAX_WIDTH) == 350

    def test_at_min_boundary(self):
        assert _clamp_width(MIN_WIDTH, MIN_WIDTH, MAX_WIDTH) == MIN_WIDTH

    def test_at_max_boundary(self):
        assert _clamp_width(MAX_WIDTH, MIN_WIDTH, MAX_WIDTH) == MAX_WIDTH


# --- 2. _DragCache ---


class TestDragCache:
    def test_initial_state(self):
        """_DragCache 初始 width=None, last_time=0.0。"""
        cache = _DragCache()
        assert cache.width is None
        assert cache.last_time == 0.0

    def test_width_assignable_to_int(self):
        cache = _DragCache()
        cache.width = 420
        assert cache.width == 420

    def test_last_time_assignable_to_float(self):
        cache = _DragCache()
        cache.last_time = 12345.678
        assert cache.last_time == 12345.678


# --- 3. 组件契约 (声明式标记 + 签名) ---


class TestComponentContract:
    """验证 ResizableSplitter 是 @ft.component 声明式函数组件。"""

    def test_is_callable(self):
        """ResizableSplitter 必须是可调用对象 (函数组件)。"""
        assert callable(ResizableSplitter)

    def test_has_wrapped_attribute(self):
        """@ft.component 装饰后保留 __wrapped__ 指向原函数。"""
        assert hasattr(ResizableSplitter, "__wrapped__")

    def test_signature_defaults(self):
        """参数默认值契约: default_width=360, min_width=280, max_width=600, 回调默认 None。"""
        sig = inspect.signature(ResizableSplitter)
        params = sig.parameters
        assert params["default_width"].default == 360
        assert params["min_width"].default == 280
        assert params["max_width"].default == 600
        assert params["on_resize"].default is None
        assert params["on_load_width"].default is None
        assert params["on_persist_width"].default is None
        assert params["drag_interval"].default == 16
        assert params["collapsible"].default is False
        assert params["collapsed"].default is False

    def test_signature_required_params(self):
        """left_content / right_content / config_key 为必传参数 (无默认值)。"""
        sig = inspect.signature(ResizableSplitter)
        params = sig.parameters
        assert params["left_content"].default is inspect.Parameter.empty
        assert params["right_content"].default is inspect.Parameter.empty
        assert params["config_key"].default is inspect.Parameter.empty

    def test_return_annotation_is_container(self):
        """返回类型注解为 ft.Container (声明式组件返回控件)。"""
        sig = inspect.signature(ResizableSplitter)
        assert sig.return_annotation is ft.Container

    def test_no_pagerefmixin_import(self):
        """模块不得依赖 PageRefMixin (CLAUDE.md §3.3 技术债消除)。"""
        import ui.components.resizable_splitter as mod

        assert not hasattr(mod, "PageRefMixin")
        assert "PageRefMixin" not in dir(mod)

    def test_no_confighandler_import(self):
        """P1-1: 模块不得直接 import ConfigHandler (经回调上抛父 VM)."""
        import ui.components.resizable_splitter as mod

        assert not hasattr(mod, "ConfigHandler")
        assert "ConfigHandler" not in dir(mod)


# --- 4. 无 renderer 环境下组件实例化抛 RuntimeError (契约验证) ---


class TestRendererRequirement:
    """有状态 @ft.component 在无 renderer 下抛 RuntimeError (由集成测试覆盖渲染)。"""

    def test_calling_without_renderer_raises(self):
        """无 renderer 环境下调用 ResizableSplitter 抛 RuntimeError。

        这是有状态声明式组件的预期行为 (含 use_state/use_effect), 验证组件确实
        依赖 renderer 上下文, 而非静默返回错误结果。集成测试用 flet_test_page 覆盖。
        P1-1: 回调模式下无需 patch ConfigHandler (on_load_width 默认 None, 不触发回调)。
        """
        with pytest.raises(RuntimeError):
            ResizableSplitter(
                left_content=ft.Container(width=100),
                right_content=ft.Container(width=100),
                config_key=CONFIG_KEY,
            )
