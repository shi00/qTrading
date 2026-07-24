"""
Unit tests for chart_utils.py.
Covers K-line chart generation functionality.
"""

# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
import pandas as pd

from unittest.mock import patch
from matplotlib.figure import Figure
from ui.components.chart_utils import generate_kline_figure

pytestmark = pytest.mark.unit


class TestChartUtils:
    """Tests for K-line chart utilities."""

    def test_generate_kline_figure_empty_df_raises_error(self):
        """Test generating chart with empty DataFrame raises error."""
        with pytest.raises(ValueError, match="Empty DataFrame"):
            generate_kline_figure(pd.DataFrame())

    def test_generate_kline_figure_none_df_raises_error(self):
        """Test generating chart with None raises error."""
        with pytest.raises(ValueError, match="Empty DataFrame"):
            generate_kline_figure(None)

    def test_generate_kline_figure_with_trade_date_column(self):
        """Test chart generation with trade_date column."""
        data = {
            "trade_date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "open": [10.0, 10.5, 11.0],
            "high": [11.0, 11.5, 12.0],
            "low": [9.5, 10.0, 10.5],
            "close": [10.5, 11.0, 11.5],
            "vol": [1000000, 1100000, 1200000],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            result = generate_kline_figure(df, title="Test Chart")

        assert result is not None
        assert isinstance(result, Figure)

    def test_generate_kline_figure_with_datetime_index(self):
        """Test chart generation with DatetimeIndex."""
        data = {
            "Open": [10.0, 10.5, 11.0],
            "High": [11.0, 11.5, 12.0],
            "Low": [9.5, 10.0, 10.5],
            "Close": [10.5, 11.0, 11.5],
            "Volume": [1000000, 1100000, 1200000],
        }
        dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
        df = pd.DataFrame(data, index=dates)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            result = generate_kline_figure(df)

        assert isinstance(result, Figure)

    def test_generate_kline_figure_missing_date_raises_error(self):
        """Test chart generation without date info raises error."""
        data = {
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with pytest.raises(ValueError, match="DataFrame needs"):
            generate_kline_figure(df)

    def test_generate_kline_figure_without_volume(self):
        """Test chart generation without volume data."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            result = generate_kline_figure(df)

        assert isinstance(result, Figure)

    def test_generate_kline_figure_with_capitalized_columns(self):
        """Test chart generation with capitalized column names."""
        data = {
            "trade_date": ["2024-01-01"],
            "Open": [10.0],
            "High": [11.0],
            "Low": [9.5],
            "Close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            result = generate_kline_figure(df)

        assert isinstance(result, Figure)

    def test_generate_kline_figure_with_dark_theme(self):
        """Test chart generation with dark theme."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            result = generate_kline_figure(df, theme_mode="dark")

        assert isinstance(result, Figure)

    def test_generate_kline_figure_with_light_theme(self):
        """Test chart generation with light theme."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            result = generate_kline_figure(df, theme_mode="light")

        assert isinstance(result, Figure)

    def test_generate_kline_figure_sorts_data_by_date(self):
        """Test that data is sorted chronologically."""
        # Unsorted dates
        data = {
            "trade_date": ["2024-01-03", "2024-01-01", "2024-01-02"],
            "open": [10.0, 10.5, 11.0],
            "high": [11.0, 11.5, 12.0],
            "low": [9.5, 10.0, 10.5],
            "close": [10.5, 11.0, 11.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot") as mock_plot:
            mock_plot.return_value = (Figure(), [])
            generate_kline_figure(df)

        # The plot should be called with sorted data
        assert mock_plot.called
        plotted_df = mock_plot.call_args[0][0]
        assert list(plotted_df.index) == sorted(plotted_df.index)
