# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pandas as pd
import pytest

from matplotlib.figure import Figure
from ui.components.chart_utils import KlineChartData, generate_kline_chart_data, generate_kline_figure

pytestmark = pytest.mark.unit


def _make_ohlcv_df(n=30):
    dates = pd.bdate_range("2024-01-02", periods=n)
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [10.0 + i * 0.1 for i in range(n)],
            "high": [10.5 + i * 0.1 for i in range(n)],
            "low": [9.5 + i * 0.1 for i in range(n)],
            "close": [10.2 + i * 0.1 for i in range(n)],
            "vol": [1000 + i * 10 for i in range(n)],
        },
    )
    return df


class TestGenerateKlineFigure:
    def test_returns_valid_figure(self):
        df = _make_ohlcv_df()
        result = generate_kline_figure(df, title="Test")
        assert isinstance(result, Figure)

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            generate_kline_figure(pd.DataFrame(), title="Empty")

    def test_none_df_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            generate_kline_figure(None, title="None")

    def test_no_volume_column(self):
        df = _make_ohlcv_df()
        df = df.drop(columns=["vol"])
        result = generate_kline_figure(df, title="NoVol")
        assert isinstance(result, Figure)


class TestKlineFigureAsyncViaThreadPool:
    @pytest.mark.asyncio
    async def test_generate_kline_figure_via_thread_pool(self):
        from utils.thread_pool import ThreadPoolManager, TaskType

        df = _make_ohlcv_df()
        result = await ThreadPoolManager().run_async(
            TaskType.CPU,
            generate_kline_figure,
            df,
            title="AsyncTest",
        )
        assert isinstance(result, Figure)


class TestFigureNoGcfLeak:
    """Regression: generate_kline_figure must detach figure from Gcf.figs to prevent leak."""

    def test_no_gcf_accumulation_after_multiple_calls(self):
        from matplotlib._pylab_helpers import Gcf

        df = _make_ohlcv_df()
        for _ in range(5):
            generate_kline_figure(df, title="LeakTest")
        assert len(Gcf.figs) == 0, f"Gcf.figs leaked {len(Gcf.figs)} figures"


class TestGenerateKlineChartData:
    """Unit tests for generate_kline_chart_data → flet-charts native data."""

    def test_valid_df_returns_kline_chart_data(self):
        df = _make_ohlcv_df()
        result = generate_kline_chart_data(df, title="Test")
        assert isinstance(result, KlineChartData)

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            generate_kline_chart_data(pd.DataFrame(), title="Empty")

    def test_none_df_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            generate_kline_chart_data(None, title="None")

    def test_missing_trade_date_raises(self):
        df = pd.DataFrame({"open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5]})
        with pytest.raises(ValueError, match="trade_date"):
            generate_kline_chart_data(df)

    def test_missing_open_column_raises(self):
        df = pd.DataFrame(
            {
                "trade_date": pd.bdate_range("2024-01-02", periods=3),
                "high": [11.0, 12.0, 13.0],
                "low": [9.0, 10.0, 11.0],
                "close": [10.5, 11.5, 12.5],
            }
        )
        with pytest.raises(ValueError, match="Open"):
            generate_kline_chart_data(df)

    def test_spots_count_matches_df_rows(self):
        df = _make_ohlcv_df(n=10)
        result = generate_kline_chart_data(df)
        assert len(result.spots) == 10

    def test_spot_properties_correct(self):
        df = _make_ohlcv_df(n=3)
        result = generate_kline_chart_data(df)
        spot = result.spots[0]
        assert spot.x == 0.0
        assert spot.open == 10.0
        assert spot.high == 10.5
        assert spot.low == 9.5
        assert spot.close == 10.2

    def test_spot_tooltip_contains_ohlc(self):
        df = _make_ohlcv_df(n=3)
        result = generate_kline_chart_data(df)
        tooltip = result.spots[0].tooltip
        assert isinstance(tooltip, str)
        assert "O:" in tooltip
        assert "H:" in tooltip
        assert "L:" in tooltip
        assert "C:" in tooltip

    def test_spot_tooltip_contains_volume(self):
        df = _make_ohlcv_df(n=3)
        result = generate_kline_chart_data(df)
        assert "Vol:" in str(result.spots[0].tooltip)

    def test_spot_tooltip_contains_ma5(self):
        df = _make_ohlcv_df(n=20)
        result = generate_kline_chart_data(df)
        # MA5 available from index 4 onwards
        assert "MA5:" in str(result.spots[5].tooltip)

    def test_spot_tooltip_contains_ma10(self):
        df = _make_ohlcv_df(n=20)
        result = generate_kline_chart_data(df)
        # MA10 available from index 9 onwards
        assert "MA10:" in str(result.spots[10].tooltip)

    def test_spot_tooltip_contains_ma20(self):
        df = _make_ohlcv_df(n=20)
        result = generate_kline_chart_data(df)
        # MA20 available from index 19 onwards
        assert "MA20:" in str(result.spots[19].tooltip)

    def test_spot_tooltip_no_ma_for_insufficient_data(self):
        df = _make_ohlcv_df(n=3)
        result = generate_kline_chart_data(df)
        tooltip = str(result.spots[0].tooltip)
        assert "MA5:" not in tooltip
        assert "MA10:" not in tooltip
        assert "MA20:" not in tooltip

    def test_volume_groups_count_matches_df_rows(self):
        df = _make_ohlcv_df(n=10)
        result = generate_kline_chart_data(df)
        assert len(result.volume_groups) == 10

    def test_volume_group_x_position(self):
        df = _make_ohlcv_df(n=5)
        result = generate_kline_chart_data(df)
        for i, group in enumerate(result.volume_groups):
            assert group.x == i

    def test_volume_rod_rise_color(self):
        """Rise (close >= open) should use _RISE_COLOR (#F44336)."""
        df = _make_ohlcv_df(n=1)
        result = generate_kline_chart_data(df)
        # close (10.2) > open (10.0) → rise
        rod = result.volume_groups[0].rods[0]
        assert rod.color == "#F44336"

    def test_volume_rod_fall_color(self):
        """Fall (close < open) should use _FALL_COLOR (#26A69A)."""
        dates = pd.bdate_range("2024-01-02", periods=1)
        df = pd.DataFrame(
            {
                "trade_date": dates,
                "open": [10.5],
                "high": [11.0],
                "low": [9.0],
                "close": [10.0],
                "vol": [1000],
            }
        )
        result = generate_kline_chart_data(df)
        rod = result.volume_groups[0].rods[0]
        assert rod.color == "#26A69A"

    def test_no_volume_column_empty_groups(self):
        df = _make_ohlcv_df()
        df = df.drop(columns=["vol"])
        result = generate_kline_chart_data(df)
        assert len(result.volume_groups) == 0
        assert result.max_volume == 0.0

    def test_date_labels_generated(self):
        df = _make_ohlcv_df(n=30)
        result = generate_kline_chart_data(df)
        assert len(result.date_labels) > 0
        assert result.date_labels[0].value == 0.0
        assert result.date_labels[-1].value == 29.0

    def test_x_axis_range(self):
        df = _make_ohlcv_df(n=10)
        result = generate_kline_chart_data(df)
        assert result.min_x == 0.0
        assert result.max_x == 9.0

    def test_y_axis_range_with_padding(self):
        df = _make_ohlcv_df(n=10)
        result = generate_kline_chart_data(df)
        # min_y should be below the minimum low (9.5)
        assert result.min_y < 9.5
        # max_y should be above the maximum high (11.4)
        assert result.max_y > 11.4

    def test_max_volume(self):
        df = _make_ohlcv_df(n=10)
        result = generate_kline_chart_data(df)
        # vol: 1000 + i*10, max at i=9: 1090
        assert result.max_volume == 1090.0

    def test_uppercase_column_names(self):
        """Uppercase column names (Open/High/Low/Close) should be handled."""
        dates = pd.bdate_range("2024-01-02", periods=3)
        df = pd.DataFrame(
            {
                "trade_date": dates,
                "Open": [10.0, 11.0, 12.0],
                "High": [10.5, 11.5, 12.5],
                "Low": [9.5, 10.5, 11.5],
                "Close": [10.2, 11.2, 12.2],
                "vol": [1000, 2000, 3000],
            }
        )
        result = generate_kline_chart_data(df)
        assert len(result.spots) == 3
        assert result.spots[0].open == 10.0

    def test_sorts_by_date(self):
        """Data should be sorted chronologically."""
        dates = pd.to_datetime(["2024-01-10", "2024-01-02", "2024-01-06"])
        df = pd.DataFrame(
            {
                "trade_date": dates,
                "open": [30.0, 10.0, 20.0],
                "high": [31.0, 11.0, 21.0],
                "low": [29.0, 9.0, 19.0],
                "close": [30.5, 10.5, 20.5],
                "vol": [3000, 1000, 2000],
            }
        )
        result = generate_kline_chart_data(df)
        # After sorting: 01-02 (open=10), 01-06 (open=20), 01-10 (open=30)
        assert result.spots[0].open == 10.0
        assert result.spots[1].open == 20.0
        assert result.spots[2].open == 30.0
