"""Flet 0.86.0 flet_charts API 兼容性验证（spike）。

目的：在 spike worktree（flet 0.86.0 + flet_charts 0.86.0）中固化项目
``ui/components/backtest/backtest_result_panel.py`` 深度依赖的 flet_charts
API 契约。任一断言失败即表示 0.86.0 引入了破坏性变更，需记录到 spike
报告并标记升级阻塞（Plans-flet-0.86.0-upgrade.md Task 0.4 DoD）。

覆盖范围（spike 任务清单）：
1. 7 个核心类存在性：``LineChart`` / ``BarChart`` / ``LineChartData`` /
   ``LineChartDataPoint`` / ``BarChartGroup`` / ``BarChartRod`` / ``ChartAxis``
2. ``LineChart`` 三个关键字段：``data_series`` / ``left_axis`` / ``bottom_axis``
3. R7 契约——``LineChart.data_points`` 字段**已移除**：项目曾使用该字段，
   后被 ``data_series`` 替代。若 0.86.0 重新引入该字段会破坏 R7 契约，
   需如实记录为升级阻塞。

依赖点（项目内使用位置）：
- ``fch.LineChart(data_series=..., left_axis=..., bottom_axis=...)``：
  ``ui/components/backtest/backtest_result_panel.py::_build_nav_chart``
- ``fch.BarChart(groups=..., left_axis=..., bottom_axis=...)``：
  ``ui/components/backtest/backtest_result_panel.py::_build_ic_chart``
- ``fch.LineChartData(points=[fch.LineChartDataPoint(x=, y=)])``：
  NAV 曲线点序列构造
- ``fch.BarChartGroup(x=, rods=[fch.BarChartRod(from_y=, to_y=, color=, width=)])``：
  IC 柱状图分组构造
- ``fch.ChartAxis(label_size=)``：左右轴标签字号

验证手段：``inspect.isclass`` + ``__dataclass_fields__`` +
``inspect.signature(__init__)`` 双重检查。

注意：``flet_charts`` 0.86.0 的自定义 dataclass 装饰器对带默认值的字段
（``left_axis``/``bottom_axis``）会设置为类属性，但对必传字段
（``data_series``，默认值 ``MISSING``）不会设置类属性。因此
``hasattr(fch.LineChart, 'data_series')`` 返回 ``False``——
不能在类上用 ``hasattr`` 检测字段存在性，必须查 ``__dataclass_fields__``
或 ``__init__`` 签名参数。
"""

from __future__ import annotations

import inspect

import flet_charts as fch
import pytest

pytestmark = pytest.mark.unit


def _has_dataclass_field(cls: type, field: str) -> bool:
    """检查 dataclass 类是否声明了指定字段。

    用 ``__dataclass_fields__`` 字典而非 ``hasattr``，因为 flet_charts
    的自定义 dataclass 装饰器对必传字段（默认值 MISSING）不设置类属性，
    导致 ``hasattr`` 在类上返回 False（误判字段缺失）。
    """
    return field in getattr(cls, "__dataclass_fields__", {})


def _has_init_param(cls: type, param: str) -> bool:
    """检查 ``__init__`` 签名是否含指定参数（不含 self）。"""
    sig = inspect.signature(cls.__init__)
    return param in sig.parameters


# ============================================================================
# 1-7. 核心类存在性
# ============================================================================


def test_line_chart_class_exists() -> None:
    """``fch.LineChart`` 类存在。

    ``ui/components/backtest/backtest_result_panel.py::_build_nav_chart`` 用其
    构造 NAV 曲线图。
    """
    assert inspect.isclass(fch.LineChart)


def test_bar_chart_class_exists() -> None:
    """``fch.BarChart`` 类存在。

    ``ui/components/backtest/backtest_result_panel.py::_build_ic_chart`` 用其
    构造 IC 柱状图。
    """
    assert inspect.isclass(fch.BarChart)


def test_line_chart_data_class_exists() -> None:
    """``fch.LineChartData`` 类存在。

    用于构造单条折线（含 ``points``/``color``/``stroke_width`` 字段）。
    """
    assert inspect.isclass(fch.LineChartData)


def test_line_chart_data_point_class_exists() -> None:
    """``fch.LineChartDataPoint`` 类存在。

    用于构造折线数据点（含 ``x``/``y`` 必传字段）。
    """
    assert inspect.isclass(fch.LineChartDataPoint)


def test_bar_chart_group_class_exists() -> None:
    """``fch.BarChartGroup`` 类存在。

    用于构造柱状图分组（含 ``x``/``rods`` 字段）。
    """
    assert inspect.isclass(fch.BarChartGroup)


def test_bar_chart_rod_class_exists() -> None:
    """``fch.BarChartRod`` 类存在。

    用于构造单根柱子（含 ``from_y``/``to_y``/``color``/``width`` 字段）。
    """
    assert inspect.isclass(fch.BarChartRod)


def test_chart_axis_class_exists() -> None:
    """``fch.ChartAxis`` 类存在。

    用于构造坐标轴（含 ``label_size`` 字段）。
    """
    assert inspect.isclass(fch.ChartAxis)


# ============================================================================
# 8. LineChart data_series / left_axis / bottom_axis 字段
# ============================================================================


def test_line_chart_has_data_series_field() -> None:
    """``fch.LineChart`` 含 ``data_series`` 字段（折线数据序列）。

    ``backtest_result_panel._build_nav_chart`` 通过
    ``fch.LineChart(data_series=chart_data, ...)`` 传入折线数据列表；
    字段缺失或重命名将破坏 NAV 曲线渲染。
    """
    assert _has_dataclass_field(fch.LineChart, "data_series")
    assert _has_init_param(fch.LineChart, "data_series")


def test_line_chart_has_left_axis_field() -> None:
    """``fch.LineChart`` 含 ``left_axis`` 字段（左侧 Y 轴配置）。

    ``backtest_result_panel._build_nav_chart`` 通过
    ``left_axis=fch.ChartAxis(label_size=50)`` 配置左侧轴。
    """
    assert _has_dataclass_field(fch.LineChart, "left_axis")
    assert _has_init_param(fch.LineChart, "left_axis")


def test_line_chart_has_bottom_axis_field() -> None:
    """``fch.LineChart`` 含 ``bottom_axis`` 字段（底部 X 轴配置）。

    ``backtest_result_panel._build_nav_chart`` 通过
    ``bottom_axis=fch.ChartAxis(label_size=40)`` 配置底部轴。
    """
    assert _has_dataclass_field(fch.LineChart, "bottom_axis")
    assert _has_init_param(fch.LineChart, "bottom_axis")


# ============================================================================
# 9. R7 契约——data_points 字段必须已移除
# ============================================================================


def test_line_chart_data_points_field_removed() -> None:
    """``fch.LineChart.data_points`` 字段**已移除**（R7 契约）。

    项目曾使用 ``LineChart.data_points`` 字段，后被 ``data_series`` 替代
    并删除该字段（R7 契约——项目源码已不再依赖该字段）。若 0.86.0
    重新引入该字段（即便向后兼容），表明上游 API 演化方向与项目契约
    出现分歧，需记录为升级阻塞并由人工评审是否需要更新 R7 契约或
    重命名隔离。

    双重断言：既不在 ``__dataclass_fields__``，也不在 ``__init__`` 签名。
    """
    assert not _has_dataclass_field(fch.LineChart, "data_points"), (
        "R7 契约破坏：fch.LineChart.data_points 字段被重新引入——项目已删除该字段改用 data_series，需人工评审升级路径"
    )
    assert not _has_init_param(fch.LineChart, "data_points"), (
        "R7 契约破坏：fch.LineChart.__init__ 含 data_points 参数——项目已删除该字段改用 data_series，需人工评审升级路径"
    )
