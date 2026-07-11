"""Spike: 性能基准声明式模式单元测试（Phase 3.0.4）。

纯逻辑验证（不依赖 ``ft.run_async``，Windows 可运行）：
- grep 守护：spike 组件用 ``use_state`` 驱动 state，不用 ``use_ref`` cache 命令式实例
- API 验证：``ft.use_state`` 是 Flet 0.85.3 官方 hook
- 阈值常量存在且符合方案 DoD

性能基准真实验证在 ``tests/integration/test_spike_perf_baseline.py``（Windows/headless Linux skip）。
"""

import inspect

import flet as ft
import pytest

from tests.integration.test_spike_perf_baseline import (
    DRAG_FRAME_THRESHOLD_MS,
    STREAM_FRAME_THRESHOLD_MS,
    _spike_drag_view,
    _spike_streaming_view,
)

pytestmark = pytest.mark.unit


def test_spike_perf_no_use_ref_cache():
    """DoD 1: grep 守护——spike 组件不用 use_ref cache 命令式实例（纯声明式红线）。"""
    streaming_source = inspect.getsource(_spike_streaming_view)
    drag_source = inspect.getsource(_spike_drag_view)
    assert "use_ref" not in streaming_source, "spike_streaming_view 不应用 use_ref cache（纯声明式红线）"
    assert "use_ref" not in drag_source, "spike_drag_view 不应用 use_ref cache（纯声明式红线）"
    assert "use_state" in streaming_source, "spike_streaming_view 必须用 use_state 驱动 state"
    assert "use_state" in drag_source, "spike_drag_view 必须用 use_state 驱动 state"


def test_spike_perf_uses_ft_use_state_api():
    """DoD 2: 验证 ft.use_state 是 Flet 0.85.3 官方 API（非自定义垫片）。"""
    assert hasattr(ft, "use_state"), "ft.use_state 必须存在（Flet 0.85.3 官方 hook）"
    sig = inspect.signature(ft.use_state)
    assert "initial" in sig.parameters, "ft.use_state 必须接受 initial 参数"


def test_spike_perf_views_are_ft_components():
    """DoD 3: spike 组件必须被 @ft.component 装饰。"""
    assert hasattr(_spike_streaming_view, "__wrapped__"), "spike_streaming_view 必须用 @ft.component 装饰"
    assert hasattr(_spike_drag_view, "__wrapped__"), "spike_drag_view 必须用 @ft.component 装饰"


def test_spike_perf_thresholds_match_plan():
    """DoD 4: 性能阈值常量符合方案 DoD（流式 <50ms/帧，拖拽 <16ms/帧）。"""
    assert STREAM_FRAME_THRESHOLD_MS == 50, f"流式阈值必须为 50ms（方案 DoD），实际 {STREAM_FRAME_THRESHOLD_MS}"
    assert DRAG_FRAME_THRESHOLD_MS == 16, f"拖拽阈值必须为 16ms（方案 DoD），实际 {DRAG_FRAME_THRESHOLD_MS}"


def test_spike_perf_streaming_view_renders_100_rows():
    """DoD 5: spike_streaming_view 渲染 100 行表格（模拟 ScreenerView 流式响应）。"""
    source = inspect.getsource(_spike_streaming_view)
    assert "range(100)" in source, "spike_streaming_view 必须渲染 100 行（模拟 ScreenerView 流式响应）"


def test_spike_perf_drag_view_has_no_imperative_container_drag():
    """DoD 6: spike_drag_view 不在 Container 上设置 on_horizontal_drag_update（Container 不支持）。"""
    source = inspect.getsource(_spike_drag_view)
    # Container 无 on_horizontal_drag_update 参数，spike 用按钮替代触发 state 变更
    assert "on_horizontal_drag_update" not in source, (
        "spike_drag_view 不应在 Container 上设 on_horizontal_drag_update（Container 不支持，需 GestureDetector）"
    )
