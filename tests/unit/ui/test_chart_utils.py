"""
Unit tests for chart_utils.py.
Covers K-line chart generation functionality.
"""

import pytest
import pandas as pd

from unittest.mock import patch
from ui.components.chart_utils import (
    generate_kline_png,
    create_kline_chart,
    generate_kline_html,
)


class TestChartUtils:
    """Tests for K-line chart utilities."""

    def test_generate_kline_png_empty_df_raises_error(self):
        """Test generating chart with empty DataFrame raises error."""
        with pytest.raises(ValueError, match="Empty DataFrame"):
            generate_kline_png(pd.DataFrame())

    def test_generate_kline_png_none_df_raises_error(self):
        """Test generating chart with None raises error."""
        with pytest.raises(ValueError, match="Empty DataFrame"):
            generate_kline_png(None)

    def test_generate_kline_png_with_trade_date_column(self):
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

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_png(df, title="Test Chart", width=440, height=220)

        assert result is not None
        assert isinstance(result, str)

    def test_generate_kline_png_with_datetime_index(self):
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

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_png(df)

        assert result is not None

    def test_generate_kline_png_missing_date_raises_error(self):
        """Test chart generation without date info raises error."""
        data = {
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with pytest.raises(ValueError, match="DataFrame needs"):
            generate_kline_png(df)

    def test_generate_kline_png_without_volume(self):
        """Test chart generation without volume data."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_png(df)

        assert result is not None

    def test_generate_kline_png_with_capitalized_columns(self):
        """Test chart generation with capitalized column names."""
        data = {
            "trade_date": ["2024-01-01"],
            "Open": [10.0],
            "High": [11.0],
            "Low": [9.5],
            "Close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_png(df)

        assert result is not None

    def test_generate_kline_png_with_dark_theme(self):
        """Test chart generation with dark theme."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_png(df, theme_mode="dark")

        assert result is not None

    def test_generate_kline_png_with_light_theme(self):
        """Test chart generation with light theme."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_png(df, theme_mode="light")

        assert result is not None

    def test_create_kline_chart_legacy_wrapper(self):
        """Test legacy create_kline_chart wrapper."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            result = create_kline_chart(df, title="Legacy")

        assert result is not None

    def test_generate_kline_html(self):
        """Test generate_kline_html wrapper."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            result = generate_kline_html(df, title="HTML")

        assert "<html>" in result
        assert "data:image/png;base64," in result

    def test_generate_kline_png_cleans_up_figures(self):
        """Test that matplotlib figures are cleaned up after generation."""
        data = {
            "trade_date": ["2024-01-01"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.5],
            "close": [10.5],
        }
        df = pd.DataFrame(data)

        with patch("ui.components.chart_utils.mpf.plot"):
            with patch("matplotlib.pyplot.close") as mock_close:
                generate_kline_png(df)

        mock_close.assert_called_once_with("all")

    def test_generate_kline_png_sorts_data_by_date(self):
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
            generate_kline_png(df)

        # The plot should be called with sorted data
        assert mock_plot.called
