"""Spike: 性能基准（Phase 3.0.4）。

验证目标：
1. ScreenerView 流式响应：100 行表格 + LLM 流式 chunk，声明式 reconcile <50ms/帧
2. ResizableSplitter 拖拽：60fps 拖拽，use_state(width) 触发 reconcile <16ms/帧

**无法在 Windows/headless Linux 运行**（依赖 ``ft.run_async`` 真实渲染）。
按 CLAUDE.md §1.5「无法运行的验证需说明原因，不得跳过不报」。

技术债：性能基准需 CI Linux + ``xvfb`` + ``flet_desktop`` 验证。
记录到 ``.claude/state/reviews/phase-3.0-review.md``。

降级方案：若基准超阈值，对应 Task（3.4.1 ResizableSplitter / 3.6.2 ScreenerView）
降级为 ``use_ref`` + 局部 ``.update()``（需用户裁决，违反纯声明式但保证性能）。

Spike 设计说明：
- 通过按钮点击触发 ``set_state``（state 变更触发 reconcile，与拖拽事件性能特征一致）
- 测量 ``page.update()`` 耗时（声明式组件 state 变更后 Flet 的 reconcile + diff 发送耗时）
- 真实 ResizableSplitter 需 ``ft.GestureDetector`` 包装（Container 无 ``on_horizontal_drag_update``）
"""

import os
import sys
import time

import flet as ft
import pytest

_IS_HEADLESS_LINUX = sys.platform == "linux" and not os.environ.get("DISPLAY")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32" or _IS_HEADLESS_LINUX,
        reason="性能基准需 ft.run_async 真实渲染，Windows/headless Linux 不支持；待 CI Linux + xvfb 验证",
    ),
]

# 性能阈值（方案 DoD）
STREAM_FRAME_THRESHOLD_MS = 50  # 流式 reconcile 每帧 <50ms
DRAG_FRAME_THRESHOLD_MS = 16  # 拖拽 reconcile 每帧 <16ms（60fps）
_ITERATIONS = 10  # 测量次数


@ft.component
def _spike_streaming_view() -> ft.Control:
    """模拟 ScreenerView 流式响应的声明式组件。

    范式：
    - ``use_state(chunks)`` 驱动流式内容渲染
    - ``set_chunks`` 触发 reconcile
    - 100 行表格模拟（``ft.Column`` 含 100 个 ``ft.Text``）
    - 按钮点击触发 ``set_chunks``（spike 简化，真实实现由 VM 流式回调触发）
    """
    _empty_chunks: tuple[str, ...] = ()
    chunks, set_chunks = ft.use_state(_empty_chunks)

    def _append_chunk(_e: ft.ControlEvent) -> None:
        set_chunks(chunks + (f"chunk-{len(chunks)}",))

    rows = [ft.Text(f"row-{i}: {chunks[i] if i < len(chunks) else ''}") for i in range(100)]
    return ft.Column(
        [ft.ElevatedButton("append", on_click=_append_chunk), *rows],
        scroll=ft.ScrollMode.AUTO,
    )


@ft.component
def _spike_drag_view() -> ft.Control:
    """模拟 ResizableSplitter 拖拽的声明式组件。

    范式：
    - ``use_state(width)`` 驱动宽度
    - ``set_width`` 触发 reconcile
    - 按钮点击触发 ``set_width``（spike 简化，真实实现需 ``ft.GestureDetector`` 包装）

    NOTE(lazy): spike 用按钮替代 GestureDetector 触发 state 变更。
    ceiling: spike 仅验证 set_state 触发 reconcile 的耗时，不验证手势识别。
    upgrade: Phase 3.4.1 重写 ResizableSplitter 时用 GestureDetector 实现。
    """
    width, set_width = ft.use_state(400.0)

    def _on_drag(_e: ft.ControlEvent) -> None:
        set_width(max(100.0, width + 10.0))

    return ft.Column(
        [
            ft.ElevatedButton("drag", on_click=_on_drag),
            ft.Container(
                content=ft.Text(f"width: {width:.1f}"),
                width=width,
                height=200,
                bgcolor=ft.Colors.BLUE_100,
            ),
        ]
    )


async def test_streaming_reconcile_under_threshold(flet_test_page):
    """DoD 1: 流式 chunk 触发 reconcile <50ms/帧（100 行表格）。"""
    page = flet_test_page.page
    page.add(_spike_streaming_view())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(
                lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "append"
            )
            is not None
        ),
        timeout=2.0,
    )

    append_btn = flet_test_page.find_control(
        lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "append"
    )
    assert append_btn is not None

    # 预热一次（首次 reconcile 含初始化开销）
    append_btn.on_click(_mock_event())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "chunk-0")
            is not None
        ),
        timeout=2.0,
    )

    # 测量 _ITERATIONS 次 state 变更 + reconcile 耗时
    durations = []
    for i in range(1, _ITERATIONS + 1):
        start = time.perf_counter()
        append_btn.on_click(_mock_event())
        page.update()
        end = time.perf_counter()
        durations.append((end - start) * 1000)
        # 等待 reconcile 完成（chunk-N 出现）
        target = f"chunk-{i}"

        def _predicate(_target: str = target) -> bool:
            return (
                flet_test_page.find_control(lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == _target)
                is not None
            )

        flet_test_page.wait_for_condition(_predicate, timeout=2.0)

    avg_ms = sum(durations) / len(durations)
    assert avg_ms < STREAM_FRAME_THRESHOLD_MS, (
        f"流式 reconcile 平均 {avg_ms:.1f}ms 超阈值 {STREAM_FRAME_THRESHOLD_MS}ms"
    )


async def test_drag_reconcile_under_threshold(flet_test_page):
    """DoD 2: 拖拽触发 reconcile <16ms/帧（60fps）。"""
    page = flet_test_page.page
    page.add(_spike_drag_view())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(
                lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "drag"
            )
            is not None
        ),
        timeout=2.0,
    )

    drag_btn = flet_test_page.find_control(
        lambda c: isinstance(c, ft.ElevatedButton) and getattr(c, "text", None) == "drag"
    )
    assert drag_btn is not None

    # 预热一次
    drag_btn.on_click(_mock_event())
    flet_test_page.wait_for_condition(
        lambda: (
            flet_test_page.find_control(
                lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == "width: 410.0"
            )
            is not None
        ),
        timeout=2.0,
    )

    # 测量 _ITERATIONS 次 state 变更 + reconcile 耗时
    durations = []
    for i in range(2, _ITERATIONS + 2):
        start = time.perf_counter()
        drag_btn.on_click(_mock_event())
        page.update()
        end = time.perf_counter()
        durations.append((end - start) * 1000)
        # 等待 reconcile 完成（width: N.0 出现）
        target_width = f"width: {400.0 + i * 10.0:.1f}"

        def _predicate(_target: str = target_width) -> bool:
            return (
                flet_test_page.find_control(lambda c: isinstance(c, ft.Text) and getattr(c, "value", None) == _target)
                is not None
            )

        flet_test_page.wait_for_condition(_predicate, timeout=2.0)

    avg_ms = sum(durations) / len(durations)
    assert avg_ms < DRAG_FRAME_THRESHOLD_MS, f"拖拽 reconcile 平均 {avg_ms:.1f}ms 超阈值 {DRAG_FRAME_THRESHOLD_MS}ms"


def _mock_event() -> ft.ControlEvent:
    """构造最小 ControlEvent mock（避免 ControlEvent 签名复杂）。"""
    from unittest.mock import MagicMock

    return MagicMock()
