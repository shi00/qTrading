"""BacktestView splitter + 高度维度响应式 单元测试 — v4.3 响应式布局 Task 4。

覆盖：
- _build_content 使用 ResizableSplitter 替代 expand=1/2 分栏
- BacktestConfigPanel Slider 移除 width=200 硬编码
- BacktestResultPanel.set_chart_min_height 局部更新图表容器高度
- BacktestView.handle_resize 基于 COMPACT_HEIGHT_THRESHOLD 调整高度
- _fixed_vertical_chrome_height 返回值合理性
- handle_resize 异常降级与判空保护
"""

import logging
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import flet as ft
import flet_charts as fch
import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from ui.components.backtest.backtest_config_panel import BacktestConfigPanel
from ui.components.backtest.backtest_result_panel import BacktestResultPanel
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
def backtest_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
    )


@pytest.fixture
def sample_result(backtest_config: BacktestConfig) -> BacktestResult:
    return BacktestResult(
        config=backtest_config,
        strategy_name="test_strategy",
        params_snapshot={},
        nav_curve=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "nav": [1_000_000.0, 1_010_000.0],
            }
        ),
        daily_returns=pl.Series([0.0, 0.01]),
        benchmark_returns=pl.Series([0.0, 0.008]),
        trades=pl.DataFrame(),
        positions=pl.DataFrame(),
        skipped_orders=pl.DataFrame(),
        metrics={
            "total_return": 0.15,
            "annualized_return": 0.20,
            "sharpe_ratio": 1.8,
            "max_drawdown": 0.08,
            "ic_mean": 0.06,
            "ic_ir": 0.8,
        },
        ic_series=pl.Series([0.02, 0.04, -0.01, 0.05]),
        period_stats=pl.DataFrame(),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="test_run",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=1000,
    )


def _make_view(mock_page: MagicMock) -> BacktestView:
    """构建 BacktestView，mock 掉 VM/面板/I18n/ConfigHandler.get_typed。"""
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
        return BacktestView(mock_page)


class TestBacktestViewSplitter:
    """SubTask 4.1: splitter + 高度维度测试。"""

    def test_content_contains_resizable_splitter(self, mock_page: MagicMock) -> None:
        """_build_content 必须使用 ResizableSplitter 替代 expand=1/2 分栏。"""
        view = _make_view(mock_page)
        controls = list(_walk_controls(view.content))
        assert any(isinstance(c, ResizableSplitter) for c in controls), (
            "BacktestView content 必须包含 ResizableSplitter 实例"
        )

    def test_config_panel_sliders_have_no_width_200(self) -> None:
        """BacktestConfigPanel 的 Slider 不再硬编码 width=200。"""
        with patch("ui.components.backtest.backtest_config_panel.I18n.get", return_value="mock_text"):
            panel = BacktestConfigPanel(on_run_backtest=MagicMock())
        sliders = [c for c in _walk_controls(panel.content) if isinstance(c, ft.Slider)]
        assert sliders, "BacktestConfigPanel 应至少包含 3 个 Slider"
        for s in sliders:
            assert s.width != 200, f"Slider 不应硬编码 width=200，实际: {s.width}"

    def test_result_panel_has_set_chart_min_height(self) -> None:
        """BacktestResultPanel 必须实现 set_chart_min_height 方法。"""
        with patch("ui.components.backtest.backtest_result_panel.I18n.get", return_value="mock_text"):
            panel = BacktestResultPanel()
        assert hasattr(panel, "set_chart_min_height")

    def test_set_chart_min_height_updates_chart_containers(self, sample_result: BacktestResult) -> None:
        """set_chart_min_height 调用后，图表容器高度应变化（局部更新）。"""
        with patch("ui.components.backtest.backtest_result_panel.I18n.get", return_value="mock_text"):
            panel = BacktestResultPanel()
        panel.page = MagicMock()
        panel.update = MagicMock()

        panel.set_result(sample_result)

        chart_containers_before = [
            c
            for c in _walk_controls(panel.content)
            if isinstance(c, ft.Container) and isinstance(getattr(c, "content", None), (fch.LineChart, fch.BarChart))
        ]
        assert chart_containers_before, "set_result 后应构建出图表容器"
        for c in chart_containers_before:
            assert c.height != 240

        panel.set_chart_min_height(240)

        chart_containers_after = [
            c
            for c in _walk_controls(panel.content)
            if isinstance(c, ft.Container) and isinstance(getattr(c, "content", None), (fch.LineChart, fch.BarChart))
        ]
        assert chart_containers_after, "调用后仍应存在图表容器"
        for c in chart_containers_after:
            assert c.height == 240, f"图表容器高度应为 240，实际: {c.height}"

    def test_fixed_vertical_chrome_height_positive_and_below_window(self, mock_page: MagicMock) -> None:
        """_fixed_vertical_chrome_height 返回值 > 0 且 < 窗口最小高度（§1.5 懒代码必须验证）。"""
        view = _make_view(mock_page)
        val = view._fixed_vertical_chrome_height()
        assert 0 < val < 720, f"_fixed_vertical_chrome_height 应在 (0, 720)，实际: {val}"

    def test_handle_resize_compact_height_calls_240(self, mock_page: MagicMock) -> None:
        """可用高度 < COMPACT_HEIGHT_THRESHOLD 时调用 set_chart_min_height(240)。"""
        view = _make_view(mock_page)
        # 可用高度 = height - 160；height=700 -> available=540 < 560
        view.handle_resize(width=1280, height=700)
        view.result_panel.set_chart_min_height.assert_called_once_with(240)

    def test_handle_resize_tall_height_calls_360(self, mock_page: MagicMock) -> None:
        """可用高度 >= COMPACT_HEIGHT_THRESHOLD 时调用 set_chart_min_height(360)。"""
        view = _make_view(mock_page)
        # 可用高度 = height - 160；height=800 -> available=640 >= 560
        view.handle_resize(width=1280, height=800)
        view.result_panel.set_chart_min_height.assert_called_once_with(360)

    def test_handle_resize_result_panel_none_no_raise(self, mock_page: MagicMock) -> None:
        """result_panel 为 None 时 handle_resize 不抛异常。"""
        view = _make_view(mock_page)
        view.result_panel = None
        # 不应抛出
        view.handle_resize(width=1280, height=800)

    def test_handle_resize_swallows_exception_and_logs_debug(self, mock_page: MagicMock, caplog) -> None:
        """set_chart_min_height 抛异常时 handle_resize 不抛出 + 记 debug 日志。"""
        view = _make_view(mock_page)
        view.result_panel.set_chart_min_height.side_effect = RuntimeError("chart boom")
        with caplog.at_level(logging.DEBUG, logger=view_logger.name):
            # 不应抛出
            view.handle_resize(width=1280, height=800)
        assert any("handle_resize skipped" in r.message and "chart boom" in r.message for r in caplog.records)

    def test_handle_resize_height_zero_returns_early(self, mock_page: MagicMock) -> None:
        """height=0 时 handle_resize 提前返回，不调用 set_chart_min_height。"""
        view = _make_view(mock_page)
        view.handle_resize(width=1280, height=0)
        view.result_panel.set_chart_min_height.assert_not_called()
