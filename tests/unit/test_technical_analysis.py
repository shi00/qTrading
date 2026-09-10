"""
Tests for TechnicalAnalysis utility class.

验证技术指标计算的正确性，包括 MACD、KDJ、RSI、趋势分析、前复权等。
"""

# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 Optional 成员访问（mock 返回 None）, Optional 下标访问。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import datetime

import numpy as np
import pandas as pd
import pytest

from utils.technical_analysis import TechnicalAnalysis

pytestmark = pytest.mark.unit


def _make_df(rows, with_trade_date=True, with_adj=True):
    data = {
        "close": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "open": [r[3] for r in rows],
    }
    if with_adj:
        data["adj_factor"] = [r[4] for r in rows]
    if with_trade_date:
        data["trade_date"] = [r[5] for r in rows]
    return pd.DataFrame(data)


class TestGetQfqDf:
    def test_sorted_ascending(self):
        rows = [
            (10.0, 11.0, 9.0, 10.0, 1.0, datetime.date(2024, 1, 2)),
            (11.0, 12.0, 10.0, 10.5, 1.1, datetime.date(2024, 1, 3)),
            (12.0, 13.0, 11.0, 11.0, 1.2, datetime.date(2024, 1, 4)),
        ]
        df = _make_df(rows)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result is not None
        assert not result.empty

    def test_sorted_descending_latest_factor_used(self):
        rows = [
            (12.0, 13.0, 11.0, 11.0, 1.2, datetime.date(2024, 1, 4)),
            (11.0, 12.0, 10.0, 10.5, 1.1, datetime.date(2024, 1, 3)),
            (10.0, 11.0, 9.0, 10.0, 1.0, datetime.date(2024, 1, 2)),
        ]
        df = _make_df(rows)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result is not None
        latest_row = result.iloc[-1]
        assert latest_row["close"] == pytest.approx(12.0, rel=1e-6)

    def test_unsorted_uses_latest_date_factor(self):
        rows = [
            (12.0, 13.0, 11.0, 11.0, 1.2, datetime.date(2024, 1, 4)),
            (10.0, 11.0, 9.0, 10.0, 1.0, datetime.date(2024, 1, 2)),
            (11.0, 12.0, 10.0, 10.5, 1.1, datetime.date(2024, 1, 3)),
        ]
        df = _make_df(rows)
        result = TechnicalAnalysis._get_qfq_df(df)
        sorted_result = result.sort_values("trade_date")
        latest = sorted_result.iloc[-1]
        assert latest["close"] == pytest.approx(12.0, rel=1e-6)
        earliest = sorted_result.iloc[0]
        assert earliest["close"] == pytest.approx(10.0 * 1.0 / 1.2, rel=1e-4)

    def test_no_adj_factor(self):
        rows = [
            (10.0, 11.0, 9.0, 10.0, 1.0, datetime.date(2024, 1, 2)),
        ]
        df = _make_df(rows, with_adj=False)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert isinstance(result, pd.DataFrame)
        pd.testing.assert_frame_equal(result, df)

    def test_empty_df(self):
        df = pd.DataFrame()
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result is not None
        assert result.empty

    def test_none_df(self):
        result = TechnicalAnalysis._get_qfq_df(None)
        assert result is None

    def test_all_same_adj_factor(self):
        rows = [
            (10.0, 11.0, 9.0, 10.0, 1.0, datetime.date(2024, 1, 2)),
            (11.0, 12.0, 10.0, 10.5, 1.0, datetime.date(2024, 1, 3)),
        ]
        df = _make_df(rows)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result["close"].iloc[0] == pytest.approx(10.0, rel=1e-6)

    def test_zero_latest_factor(self):
        rows = [
            (10.0, 11.0, 9.0, 10.0, 1.0, datetime.date(2024, 1, 2)),
            (11.0, 12.0, 10.0, 10.5, 0.0, datetime.date(2024, 1, 3)),
        ]
        df = _make_df(rows)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert isinstance(result, pd.DataFrame)

    def test_no_trade_date_column(self):
        rows = [
            (10.0, 11.0, 9.0, 10.0, 1.0, None),
            (11.0, 12.0, 10.0, 10.5, 1.1, None),
        ]
        df = _make_df(rows, with_trade_date=False)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result is not None
        assert not result.empty

    def test_qfq_normal(self):
        df = pd.DataFrame(
            [
                {
                    "trade_date": "20240101",
                    "close": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "open": 10.0,
                    "adj_factor": 1.0,
                },
                {
                    "trade_date": "20240102",
                    "close": 11.0,
                    "high": 11.5,
                    "low": 10.5,
                    "open": 10.5,
                    "adj_factor": 1.0,
                },
                {
                    "trade_date": "20240103",
                    "close": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "open": 10.5,
                    "adj_factor": 2.0,
                },
            ]
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result is not None
        assert len(result) == 3
        assert result["close"].iloc[-1] == pytest.approx(10.0, rel=1e-2)

    def test_qfq_null_adj_factor_degradation(self):
        df = pd.DataFrame(
            [
                {
                    "trade_date": "20240101",
                    "close": 10.0,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "adj_factor": None,
                },
                {
                    "trade_date": "20240102",
                    "close": 11.0,
                    "open": 11.0,
                    "high": 11.5,
                    "low": 10.5,
                    "adj_factor": 1.1,
                },
                {
                    "trade_date": "20240103",
                    "close": 12.0,
                    "open": 12.0,
                    "high": 12.5,
                    "low": 11.5,
                    "adj_factor": 1.2,
                },
            ]
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        assert not result["close"].isna().any(), "NULL adj_factor should be degraded, not produce NaN prices"

    def test_qfq_all_null_adj_factor(self):
        df = pd.DataFrame(
            [
                {"trade_date": "20240101", "close": 10.0, "adj_factor": None},
                {"trade_date": "20240102", "close": 11.0, "adj_factor": None},
                {"trade_date": "20240103", "close": 12.0, "adj_factor": None},
            ]
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        assert not result["close"].isna().any()

    def test_qfq_adjusts_volume_consistently(self):
        df = pd.DataFrame(
            [
                {
                    "trade_date": "20240101",
                    "close": 10.0,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "vol": 100.0,
                    "adj_factor": 1.0,
                },
                {
                    "trade_date": "20240102",
                    "close": 10.0,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "vol": 100.0,
                    "adj_factor": 1.0,
                },
                {
                    "trade_date": "20240103",
                    "close": 10.0,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "vol": 200.0,
                    "adj_factor": 2.0,
                },
            ]
        )
        result = TechnicalAnalysis._get_qfq_df(df)
        assert "vol" in result.columns


class TestMACD:
    @pytest.fixture(autouse=True)
    def setup(self):
        np.random.seed(42)
        n = 50
        self.df = pd.DataFrame(
            {
                "trade_date": pd.date_range("2024-01-01", periods=n),
                "close": 10 + np.cumsum(np.random.randn(n) * 0.5),
                "high": 10.5 + np.cumsum(np.random.randn(n) * 0.5),
                "low": 9.5 + np.cumsum(np.random.randn(n) * 0.5),
                "open": 10 + np.cumsum(np.random.randn(n) * 0.5),
            }
        )

    def test_macd_calculation(self):
        status, macd_val, hist_val = TechnicalAnalysis.get_macd(self.df)
        assert status in [
            "GOLDEN_CROSS",
            "DEATH_CROSS",
            "BULLISH",
            "BEARISH",
            "NEUTRAL",
        ]
        assert isinstance(macd_val, (int, float))
        assert isinstance(hist_val, (int, float))

    def test_macd_golden_cross(self):
        df = pd.DataFrame({"close": [10.0] * 20 + [10.0 + i * 0.5 for i in range(1, 15)]})
        status, _, _ = TechnicalAnalysis.get_macd(df)
        assert status in ["GOLDEN_CROSS", "BULLISH"]

    def test_macd_death_cross(self):
        df = pd.DataFrame({"close": [20.0] * 20 + [20.0 - i * 0.5 for i in range(1, 15)]})
        status, _, _ = TechnicalAnalysis.get_macd(df)
        assert status in ["DEATH_CROSS", "BEARISH"]

    def test_macd_insufficient_data(self):
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})
        status, macd_val, hist_val = TechnicalAnalysis.get_macd(df)
        assert status == "UNKNOWN"
        assert macd_val == 0
        assert hist_val == 0

    def test_macd_none_input(self):
        status, macd_val, hist_val = TechnicalAnalysis.get_macd(None)
        assert status == "UNKNOWN"
        assert macd_val == 0
        assert hist_val == 0


class TestKDJ:
    @pytest.fixture(autouse=True)
    def setup(self):
        np.random.seed(42)
        n = 30
        self.df = pd.DataFrame(
            {
                "trade_date": pd.date_range("2024-01-01", periods=n),
                "close": 10 + np.cumsum(np.random.randn(n) * 0.3),
                "high": 10.5 + np.cumsum(np.random.randn(n) * 0.3),
                "low": 9.5 + np.cumsum(np.random.randn(n) * 0.3),
                "open": 10 + np.cumsum(np.random.randn(n) * 0.3),
            }
        )

    def test_kdj_calculation(self):
        status, k, d, j = TechnicalAnalysis.get_kdj(self.df)
        assert status in ["OVERBOUGHT", "OVERSOLD", "NEUTRAL"]
        assert isinstance(k, (int, float))
        assert isinstance(d, (int, float))
        assert isinstance(j, (int, float))

    def test_kdj_overbought(self):
        df = pd.DataFrame(
            {
                "high": [10.0 + i * 0.5 for i in range(15)],
                "low": [9.5 + i * 0.5 for i in range(15)],
                "close": [10.0 + i * 0.5 for i in range(15)],
            }
        )
        status, k, d, j = TechnicalAnalysis.get_kdj(df)
        assert k > 80
        assert status == "OVERBOUGHT"

    def test_kdj_oversold(self):
        df = pd.DataFrame(
            {
                "high": [20.0 - i * 0.5 for i in range(15)],
                "low": [19.5 - i * 0.5 for i in range(15)],
                "close": [20.0 - i * 0.5 for i in range(15)],
            }
        )
        status, k, d, j = TechnicalAnalysis.get_kdj(df)
        assert k < 20
        assert status == "OVERSOLD"

    def test_kdj_insufficient_data(self):
        df = pd.DataFrame({"high": [10.5], "low": [9.5], "close": [10.0]})
        status, k, d, j = TechnicalAnalysis.get_kdj(df)
        assert status == "UNKNOWN"
        assert k == 0
        assert d == 0
        assert j == 0

    def test_kdj_none_input(self):
        status, k, d, j = TechnicalAnalysis.get_kdj(None)
        assert status == "UNKNOWN"
        assert k == 0


class TestRSI:
    @pytest.fixture(autouse=True)
    def setup(self):
        np.random.seed(42)
        n = 30
        self.df = pd.DataFrame(
            {
                "trade_date": pd.date_range("2024-01-01", periods=n),
                "close": 10 + np.cumsum(np.random.randn(n) * 0.3),
            }
        )

    def test_rsi_calculation(self):
        rsi = TechnicalAnalysis.get_rsi(self.df, period=6)
        assert isinstance(rsi, float)
        assert 0 <= rsi <= 100

    def test_rsi_overbought(self):
        df = pd.DataFrame({"close": [10.0 + i * 0.5 for i in range(20)]})
        rsi = TechnicalAnalysis.get_rsi(df, period=6)
        assert rsi > 70

    def test_rsi_oversold(self):
        df = pd.DataFrame({"close": [20.0 - i * 0.5 for i in range(20)]})
        rsi = TechnicalAnalysis.get_rsi(df, period=6)
        assert rsi < 30

    def test_rsi_insufficient_data(self):
        df = pd.DataFrame({"close": [10.0, 11.0]})
        rsi = TechnicalAnalysis.get_rsi(df, period=6)
        assert rsi == 50.0

    def test_rsi_none_input(self):
        rsi = TechnicalAnalysis.get_rsi(None, period=6)
        assert rsi == 50.0

    def test_rsi_different_periods(self):
        rsi_6 = TechnicalAnalysis.get_rsi(self.df, period=6)
        rsi_14 = TechnicalAnalysis.get_rsi(self.df, period=14)
        assert isinstance(rsi_6, float)
        assert isinstance(rsi_14, float)


class TestTrendAnalysis:
    def test_trend_up(self):
        df = pd.DataFrame({"close": [10.0 + i * 0.5 for i in range(30)]})
        trend = TechnicalAnalysis.analyze_trend(df)
        assert trend == "UP"

    def test_trend_down(self):
        df = pd.DataFrame({"close": [20.0 - i * 0.5 for i in range(30)]})
        trend = TechnicalAnalysis.analyze_trend(df)
        assert trend == "DOWN"

    def test_trend_insufficient_data(self):
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})
        trend = TechnicalAnalysis.analyze_trend(df)
        assert trend == "UNKNOWN"

    def test_trend_none_input(self):
        trend = TechnicalAnalysis.analyze_trend(None)
        assert trend == "UNKNOWN"


class TestRSIPandas:
    def test_rsi_series_calculation(self):
        close = pd.Series([10.0 + i * 0.3 for i in range(30)])
        rsi = TechnicalAnalysis.calculate_rsi_pandas(close, period=14)
        assert len(rsi) == len(close)
        # 移除 fillna(50) 掩盖后，EMA 预热期（不足 period 个观测）产生 NaN 属正确语义，
        # 值域断言仅针对有效值段。
        valid = rsi.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_direction_is_correct(self):
        """RSI 方向性断言：单调上涨必须接近 100，单调下跌必须接近 0。

        回归防护：修复前（loss = (-delta).clip(upper=0) 的语义反转 + fillna(50)/clip 掩盖）
        该测试对单调上涨返回 0.0、对单调下跌返回 50.0，均会失败。
        """
        up = pd.Series(np.arange(100.0, 130.0))
        down = pd.Series(np.arange(130.0, 100.0, -1.0))
        assert TechnicalAnalysis.calculate_rsi_pandas(up, 14).iloc[-1] == pytest.approx(100.0)
        assert TechnicalAnalysis.calculate_rsi_pandas(down, 14).iloc[-1] == pytest.approx(0.0)

    def test_rsi_pandas_matches_polars(self):
        """跨实现一致性（D3-2 防漂移门禁）。

        get_rsi / get_rsi_expr / calculate_rsi_pandas 三套实现对同一输入
        必须给出相同的末值。D3-1 的漂移正是由于三份独立分解实现无法同步演进，
        本测试确保未来任何一份再被改动时立即被捕获。
        """
        import polars as pl

        np.random.seed(42)
        close = pd.Series(100.0 + np.cumsum(np.random.randn(60)))
        period = 14
        df = pd.DataFrame({"close": close})

        polars_last = (
            pl.from_pandas(df)
            .lazy()
            .with_columns(TechnicalAnalysis.get_rsi_expr("close", period=period, alias="rsi"))
            .collect()["rsi"]
            .to_list()[-1]
        )
        pandas_point = TechnicalAnalysis.get_rsi(df, period=period)
        pandas_series = TechnicalAnalysis.calculate_rsi_pandas(close, period=period).iloc[-1]

        assert polars_last == pytest.approx(pandas_point, rel=1e-6)
        assert polars_last == pytest.approx(pandas_series, rel=1e-6)

    def test_rsi_series_insufficient_data(self):
        close = pd.Series([10.0, 11.0])
        rsi = TechnicalAnalysis.calculate_rsi_pandas(close, period=14)
        assert rsi.empty

    def test_rsi_series_none_input(self):
        rsi = TechnicalAnalysis.calculate_rsi_pandas(None, period=14)  # type: ignore[arg-type]
        assert rsi.empty


class TestStrongNumericAssertionsD38:
    """D3-8: 指标函数"已知输入 → 精确期望值"强数值断言。

    仅值域断言是零测试（错误输出被 clip/填充压进合法域也无法捕获），
    本类对核心指标建立单调方向边界 + 固定序列参考值断言，用于捕获
    D3-1 这类"算错但不出域"的计算缺陷。
    """

    @staticmethod
    def _real_ohlc(n: int = 40) -> pd.DataFrame:
        # 确定性序列（不含 adj_factor，走非 QFQ 分支），便于复算。
        close = 100.0 + np.arange(n) * 2.0 + np.sin(np.arange(n)) * 5.0
        return pd.DataFrame({"close": close, "high": close + 3.0, "low": close - 3.0})

    # ---------- RSI ----------
    def test_rsi_boundaries_known_input(self):
        """单调上涨→100、单调下跌→0、横盘→50（固定精确期望）。"""
        up = pd.Series(np.arange(100.0, 130.0))
        down = pd.Series(np.arange(130.0, 100.0, -1.0))
        flat = pd.Series(np.full(30, 100.0))
        assert TechnicalAnalysis.calculate_rsi_pandas(flat, 14).iloc[-1] == pytest.approx(50.0)
        # get_rsi 点值三边界（flat→100 为 D3-8 修复前惰性错误，修复后应归中性）
        assert TechnicalAnalysis.get_rsi(pd.DataFrame({"close": up}), period=6) == pytest.approx(100.0)
        assert TechnicalAnalysis.get_rsi(pd.DataFrame({"close": down}), period=6) == pytest.approx(0.0)
        assert TechnicalAnalysis.get_rsi(pd.DataFrame({"close": flat}), period=6) == pytest.approx(50.0)

    def test_rsi_real_series_exact_value(self):
        """固定序列 → 精确 RSI 末值（D3-1 回归 + D3-8 强断言）。"""
        close = self._real_ohlc()["close"]
        assert TechnicalAnalysis.calculate_rsi_pandas(close, 14).iloc[-1] == pytest.approx(85.108693, abs=1e-4)

    # ---------- MACD ----------
    def test_macd_real_series_exact_value(self):
        """固定序列 → 精确 macd/hist 与状态。"""
        df = self._real_ohlc()
        status, macd, hist = TechnicalAnalysis.get_macd(df)
        assert status == "BULLISH"
        assert macd == pytest.approx(12.959179, abs=1e-4)
        assert hist == pytest.approx(0.793880, abs=1e-4)

    def test_macd_direction_exact_hist(self):
        """单调上涨/下跌 → hist 精确为 ±0.272172，状态 BULLISH/BEARISH。"""
        # get_macd 仅使用 close，high/low 仅供 get_kdj 使用，此处保持一致以避免混淆。
        up = pd.DataFrame({"close": np.arange(100.0, 140.0)})
        status, _, hist = TechnicalAnalysis.get_macd(up)
        assert status == "BULLISH" and hist == pytest.approx(0.272172, abs=1e-4)
        down = pd.DataFrame({"close": np.arange(140.0, 100.0, -1.0)})
        status2, _, hist2 = TechnicalAnalysis.get_macd(down)
        assert status2 == "BEARISH" and hist2 == pytest.approx(-0.272172, abs=1e-4)

    # ---------- KDJ ----------
    def test_kdj_real_series_exact_value(self):
        """固定序列 → 精确 k/d/j 与状态。"""
        df = self._real_ohlc()
        status, k, d, j = TechnicalAnalysis.get_kdj(df)
        assert status == "OVERBOUGHT"
        assert k == pytest.approx(85.203612, abs=1e-3)
        assert d == pytest.approx(82.564299, abs=1e-3)
        assert j == pytest.approx(90.482238, abs=1e-3)


class TestRSIOversoldFeatures:
    def test_consecutive_oversold_days(self):
        close = pd.Series([20.0 - i * 0.5 for i in range(30)])
        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)
        assert "consecutive_oversold_days" in result
        assert "days_since_healthy" in result
        assert "stagnation_detected" in result
        assert "feature_text" in result
        assert isinstance(result["consecutive_oversold_days"], int)

    def test_days_since_healthy(self):
        close = pd.Series([10.0] * 10 + [10.0 - i * 0.3 for i in range(20)])
        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)
        assert "days_since_healthy" in result

    def test_stagnation_detection(self):
        close = pd.Series([10.0] * 10 + [10.0 - i * 0.1 for i in range(20)])
        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)
        assert "stagnation_detected" in result
        assert result["stagnation_detected"] in [True, False]

    def test_insufficient_data(self):
        close = pd.Series([10.0, 11.0, 12.0])
        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)
        assert result["consecutive_oversold_days"] == 0
        assert result["days_since_healthy"] is None
        assert "暂不解读超卖形态" in result["feature_text"]

    def test_days_since_healthy_is_capped_by_recent_window(self):
        close = pd.Series([10.0 + i * 0.2 for i in range(80)] + [26.0 - i * 0.25 for i in range(80)])
        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)
        assert result["days_since_healthy"] is None
        assert "近60日内未回到多头状态" in result["feature_text"]


class TestPolarsExpressions:
    def test_rsi_expr(self):
        import polars as pl

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 30,
                "close": [10.0 + i * 0.3 for i in range(30)],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = lf.with_columns(TechnicalAnalysis.get_rsi_expr("close", period=6, alias="rsi")).collect()
        assert "rsi" in result.columns
        rsi_values = result["rsi"].to_list()
        assert all(0 <= v <= 100 for v in rsi_values if not pd.isna(v))

    def test_macd_expr(self):
        import polars as pl

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 50,
                "close": [10.0 + i * 0.2 for i in range(50)],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = lf.with_columns(TechnicalAnalysis.get_macd_expr("close")).collect()
        assert "macd_struct" in result.columns

    def test_kdj_expr(self):
        import polars as pl

        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 20,
                "high": [10.5 + i * 0.2 for i in range(20)],
                "low": [9.5 + i * 0.2 for i in range(20)],
                "close": [10.0 + i * 0.2 for i in range(20)],
            }
        )
        lf = pl.from_pandas(df).lazy()
        result = lf.with_columns(TechnicalAnalysis.get_kdj_expr()).collect()
        assert "kdj_struct" in result.columns
