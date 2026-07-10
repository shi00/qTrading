"""BacktestView splitter + 高度维度响应式 单元测试 — v4.3 响应式布局 Task 4。

覆盖：
- _build_content 使用 ResizableSplitter 替代 expand=1/2 分栏
- BacktestConfigPanel Slider 移除 width=200 硬编码
- BacktestView.handle_resize 基于 COMPACT_HEIGHT_THRESHOLD 通过 props 推送 chart_min_height
  （Phase 3.2.6 后：声明式 result_panel 不再有 set_chart_min_height 方法，
  BacktestView 改为 _refresh_result_panel 重新实例化推送 props）
- _fixed_vertical_chrome_height 返回值合理性
- handle_resize 异常降级与判空保护
"""

import logging
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from ui.components.resizable_splitter import ResizableSplitter
from ui.views.backtest_view import BacktestView, logger as view_logger

pytestmark = pytest.mark.unit


def _walk_controls(control):
    """深度优先遍历 Flet 控件树，yield 每个控件实例。"""
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk_controls(content)
    controls = getattr(control, "controls", None)
    if controls:
        for c in controls:
            if isinstance(c, ft.Control):
                yield from _walk_controls(c)
    tabs = getattr(control, "tabs", None)
    if tabs:
        for t in tabs:
            tab_content = getattr(t, "content", None)
            if isinstance(tab_content, ft.Control):
                yield from _walk_controls(tab_content)


@pytest.fixture
def mock_page() -> MagicMock:
    page = MagicMock(spec=ft.Page)
    page.run_task = MagicMock()
    page.update = MagicMock()
    return page


@pytest.fixture
def view_with_mock_result(mock_page: MagicMock) -> Iterator[tuple[BacktestView, MagicMock]]:
    """构建 BacktestView，mock 掉 VM/面板/I18n/ConfigHandler.get_typed。

    用 yield fixture 让 patch 持续整个测试方法（with 块在 fixture teardown 时才退出），
    确保 handle_resize 调用 ``BacktestResultPanel(...)`` 时仍是 mock（无 renderer 环境下
    原始 ``@ft.component`` 会抛 RuntimeError "No current renderer is set"）。

    Yields:
        (view, mock_result_cls) 元组，mock_result_cls 供测试断言
        BacktestResultPanel 的重新实例化调用记录。
    """
    with (
        patch("ui.views.backtest_view.BacktestViewModel") as mock_vm_cls,
        patch("ui.views.backtest_view.BacktestConfigPanel") as mock_config_cls,
        patch("ui.views.backtest_view.BacktestResultPanel") as mock_result_cls,
        patch("ui.views.backtest_view.I18n.get", return_value="mock_text"),
        patch("utils.config_handler.ConfigHandler.get_typed", return_value=360),
    ):
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {"strategy1": "策略1"}
        mock_config_cls.return_value = MagicMock()
        mock_result_cls.return_value = MagicMock()
        view = BacktestView(mock_page)
        # _result_container 替换为 MagicMock，避免 _refresh_result_panel 中
        # ft.Container.content = value 触发 V1 renderer 检查（无 renderer 环境下抛 RuntimeError）
        view._result_container = MagicMock()
        yield view, mock_result_cls


class TestBacktestViewSplitter:
    """SubTask 4.1: splitter + 高度维度测试。"""

    def test_content_contains_resizable_splitter(self, view_with_mock_result: tuple[BacktestView, MagicMock]) -> None:
        """_build_content 必须使用 ResizableSplitter 替代 expand=1/2 分栏。"""
        view, _ = view_with_mock_result
        controls = list(_walk_controls(view.content))
        assert any(isinstance(c, ResizableSplitter) for c in controls), (
            "BacktestView content 必须包含 ResizableSplitter 实例"
        )

    def test_config_panel_sliders_have_no_width_200(self) -> None:
        """BacktestConfigPanel 的 Slider 不再硬编码 width=200（源码检查，声明式组件无法在无 renderer 下实例化）。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_config_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")
        # Slider 不应有 width=200 硬编码
        assert "width=200" not in content, "Slider 不应硬编码 width=200"
        # 应至少有 3 个 Slider（commission/stamp_duty/slippage）
        assert content.count("ft.Slider(") >= 3

    def test_fixed_vertical_chrome_height_positive_and_below_window(
        self, view_with_mock_result: tuple[BacktestView, MagicMock]
    ) -> None:
        """_fixed_vertical_chrome_height 返回值 > 0 且 < 窗口最小高度（§1.5 懒代码必须验证）。"""
        view, _ = view_with_mock_result
        val = view._fixed_vertical_chrome_height()
        assert 0 < val < 720, f"_fixed_vertical_chrome_height 应在 (0, 720)，实际: {val}"

    def test_handle_resize_compact_height_sets_240(self, view_with_mock_result: tuple[BacktestView, MagicMock]) -> None:
        """可用高度 < COMPACT_HEIGHT_THRESHOLD 时通过 props 推送 chart_min_height=240。

        Phase 3.2.6 后：声明式 result_panel 不再有 set_chart_min_height 方法，
        BacktestView 改为 _refresh_result_panel 重新实例化推送 chart_min_height prop。
        """
        view, mock_result_cls = view_with_mock_result
        initial_call_count = mock_result_cls.call_count
        # 可用高度 = height - 160；height=700 -> available=540 < 560
        view.handle_resize(width=1280, height=700)
        assert view._chart_min_height == 240
        # _refresh_result_panel 应重新实例化 BacktestResultPanel with chart_min_height=240
        assert mock_result_cls.call_count == initial_call_count + 1
        last_call = mock_result_cls.call_args
        assert last_call.kwargs.get("chart_min_height") == 240

    def test_handle_resize_tall_height_sets_360(self, view_with_mock_result: tuple[BacktestView, MagicMock]) -> None:
        """可用高度 >= COMPACT_HEIGHT_THRESHOLD 时通过 props 推送 chart_min_height=360。"""
        view, mock_result_cls = view_with_mock_result
        initial_call_count = mock_result_cls.call_count
        # 可用高度 = height - 160；height=800 -> available=640 >= 560
        view.handle_resize(width=1280, height=800)
        assert view._chart_min_height == 360
        assert mock_result_cls.call_count == initial_call_count + 1
        last_call = mock_result_cls.call_args
        assert last_call.kwargs.get("chart_min_height") == 360

    def test_handle_resize_swallows_exception_and_logs_debug(
        self, view_with_mock_result: tuple[BacktestView, MagicMock], caplog
    ) -> None:
        """_refresh_result_panel 抛异常时 handle_resize 不抛出 + 记 debug 日志。"""
        view, mock_result_cls = view_with_mock_result
        # 让 BacktestResultPanel 构造抛异常（_refresh_result_panel 内部调用）
        mock_result_cls.side_effect = RuntimeError("chart boom")
        with caplog.at_level(logging.DEBUG, logger=view_logger.name):
            # 不应抛出
            view.handle_resize(width=1280, height=800)
        assert any("handle_resize skipped" in r.message and "chart boom" in r.message for r in caplog.records)

    def test_handle_resize_height_zero_returns_early(
        self, view_with_mock_result: tuple[BacktestView, MagicMock]
    ) -> None:
        """height=0 时 handle_resize 提前返回，不更新 chart_min_height。"""
        view, mock_result_cls = view_with_mock_result
        initial_call_count = mock_result_cls.call_count
        view.handle_resize(width=1280, height=0)
        assert view._chart_min_height is None
        # 没有重新实例化 result_panel
        assert mock_result_cls.call_count == initial_call_count

    def test_handle_resize_same_height_no_refresh(self, view_with_mock_result: tuple[BacktestView, MagicMock]) -> None:
        """chart_min_height 未变化时不重新实例化 result_panel（避免重复 resize 不必要刷新）。"""
        view, mock_result_cls = view_with_mock_result
        # 第一次 resize 触发实例化
        view.handle_resize(width=1280, height=800)
        call_count_after_first = mock_result_cls.call_count
        # 第二次 resize 同高度，不应重新实例化
        view.handle_resize(width=1280, height=800)
        assert mock_result_cls.call_count == call_count_after_first
