# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pandas as pd
import pytest

from matplotlib.figure import Figure
from ui.components.chart_utils import generate_kline_figure

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
