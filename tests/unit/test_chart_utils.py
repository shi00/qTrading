# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import base64

import pandas as pd
import pytest

from ui.components.chart_utils import generate_kline_png

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


class TestGenerateKlinePng:
    def test_returns_valid_base64(self):
        df = _make_ohlcv_df()
        result = generate_kline_png(df, title="Test", width=400, height=200)
        assert isinstance(result, str)
        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            generate_kline_png(pd.DataFrame(), title="Empty")

    def test_none_df_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            generate_kline_png(None, title="None")

    def test_no_volume_column(self):
        df = _make_ohlcv_df()
        df = df.drop(columns=["vol"])
        result = generate_kline_png(df, title="NoVol", width=400, height=200)
        assert isinstance(result, str)


class TestKlinePngAsyncViaThreadPool:
    @pytest.mark.asyncio
    async def test_generate_kline_png_via_thread_pool(self):
        from utils.thread_pool import ThreadPoolManager, TaskType

        df = _make_ohlcv_df()
        result = await ThreadPoolManager().run_async(
            TaskType.CPU,
            generate_kline_png,
            df,
            title="AsyncTest",
            width=400,
            height=200,
        )
        assert isinstance(result, str)
        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"
