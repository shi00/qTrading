"""
Tests for TechnicalAnalysis utility class.

验证技术指标计算的正确性，包括 MACD、KDJ、RSI、趋势分析等。
"""

import unittest

import numpy as np
import pandas as pd

from utils.technical_analysis import TechnicalAnalysis


class TestQfqCalculation(unittest.TestCase):
    """测试前复权计算"""

    def test_qfq_normal(self):
        """正常前复权计算"""
        df = pd.DataFrame([
            {"trade_date": "20240101", "close": 10.0, "high": 10.5, "low": 9.5, "open": 10.0, "adj_factor": 1.0},
            {"trade_date": "20240102", "close": 11.0, "high": 11.5, "low": 10.5, "open": 10.5, "adj_factor": 1.0},
            {"trade_date": "20240103", "close": 10.0, "high": 10.5, "low": 9.5, "open": 10.5, "adj_factor": 2.0},
        ])

        result = TechnicalAnalysis._get_qfq_df(df)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result["close"].iloc[0], 5.0, places=2)
        self.assertAlmostEqual(result["close"].iloc[1], 5.5, places=2)
        self.assertAlmostEqual(result["close"].iloc[2], 10.0, places=2)

    def test_qfq_no_adj_factor(self):
        """无复权因子"""
        df = pd.DataFrame([
            {"trade_date": "20240101", "close": 10.0, "high": 10.5, "low": 9.5, "open": 10.0},
        ])

        result = TechnicalAnalysis._get_qfq_df(df)

        self.assertEqual(result["close"].iloc[0], 10.0)

    def test_qfq_all_factors_same(self):
        """因子全部相同"""
        df = pd.DataFrame([
            {"trade_date": "20240101", "close": 10.0, "high": 10.5, "low": 9.5, "open": 10.0, "adj_factor": 1.0},
            {"trade_date": "20240102", "close": 11.0, "high": 11.5, "low": 10.5, "open": 10.5, "adj_factor": 1.0},
        ])

        result = TechnicalAnalysis._get_qfq_df(df)

        self.assertEqual(result["close"].iloc[0], 10.0)
        self.assertEqual(result["close"].iloc[1], 11.0)

    def test_qfq_empty_df(self):
        """空 DataFrame"""
        result = TechnicalAnalysis._get_qfq_df(None)
        self.assertIsNone(result)

        result = TechnicalAnalysis._get_qfq_df(pd.DataFrame())
        self.assertTrue(result.empty)

    def test_qfq_zero_factor(self):
        """零因子处理"""
        df = pd.DataFrame([
            {"trade_date": "20240101", "close": 10.0, "adj_factor": 0.0},
        ])

        result = TechnicalAnalysis._get_qfq_df(df)

        self.assertEqual(result["close"].iloc[0], 10.0)


class TestMACD(unittest.TestCase):
    """测试 MACD 计算"""

    def setUp(self):
        np.random.seed(42)
        n = 50
        self.df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=n),
            "close": 10 + np.cumsum(np.random.randn(n) * 0.5),
            "high": 10.5 + np.cumsum(np.random.randn(n) * 0.5),
            "low": 9.5 + np.cumsum(np.random.randn(n) * 0.5),
            "open": 10 + np.cumsum(np.random.randn(n) * 0.5),
        })

    def test_macd_calculation(self):
        """MACD 计算返回正确格式"""
        status, macd_val, hist_val = TechnicalAnalysis.get_macd(self.df)

        self.assertIn(status, ["GOLDEN_CROSS", "DEATH_CROSS", "BULLISH", "BEARISH", "NEUTRAL"])
        self.assertIsInstance(macd_val, (int, float))
        self.assertIsInstance(hist_val, (int, float))

    def test_macd_golden_cross(self):
        """金叉检测 - 构造金叉数据"""
        df = pd.DataFrame({
            "close": [10.0] * 20 + [10.0 + i * 0.5 for i in range(1, 15)],
        })

        status, _, _ = TechnicalAnalysis.get_macd(df)

        self.assertIn(status, ["GOLDEN_CROSS", "BULLISH"])

    def test_macd_death_cross(self):
        """死叉检测 - 构造死叉数据"""
        df = pd.DataFrame({
            "close": [20.0] * 20 + [20.0 - i * 0.5 for i in range(1, 15)],
        })

        status, _, _ = TechnicalAnalysis.get_macd(df)

        self.assertIn(status, ["DEATH_CROSS", "BEARISH"])

    def test_macd_insufficient_data(self):
        """数据不足"""
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})

        status, macd_val, hist_val = TechnicalAnalysis.get_macd(df)

        self.assertEqual(status, "UNKNOWN")
        self.assertEqual(macd_val, 0)
        self.assertEqual(hist_val, 0)

    def test_macd_none_input(self):
        """None 输入"""
        status, macd_val, hist_val = TechnicalAnalysis.get_macd(None)

        self.assertEqual(status, "UNKNOWN")
        self.assertEqual(macd_val, 0)
        self.assertEqual(hist_val, 0)


class TestKDJ(unittest.TestCase):
    """测试 KDJ 计算"""

    def setUp(self):
        np.random.seed(42)
        n = 30
        self.df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=n),
            "close": 10 + np.cumsum(np.random.randn(n) * 0.3),
            "high": 10.5 + np.cumsum(np.random.randn(n) * 0.3),
            "low": 9.5 + np.cumsum(np.random.randn(n) * 0.3),
            "open": 10 + np.cumsum(np.random.randn(n) * 0.3),
        })

    def test_kdj_calculation(self):
        """KDJ 计算返回正确格式"""
        status, k, d, j = TechnicalAnalysis.get_kdj(self.df)

        self.assertIn(status, ["OVERBOUGHT", "OVERSOLD", "NEUTRAL"])
        self.assertIsInstance(k, (int, float))
        self.assertIsInstance(d, (int, float))
        self.assertIsInstance(j, (int, float))

    def test_kdj_overbought(self):
        """超买检测 - 构造超买数据"""
        df = pd.DataFrame({
            "high": [10.0 + i * 0.5 for i in range(15)],
            "low": [9.5 + i * 0.5 for i in range(15)],
            "close": [10.0 + i * 0.5 for i in range(15)],
        })

        status, k, d, j = TechnicalAnalysis.get_kdj(df)

        self.assertGreater(k, 80)
        self.assertEqual(status, "OVERBOUGHT")

    def test_kdj_oversold(self):
        """超卖检测 - 构造超卖数据"""
        df = pd.DataFrame({
            "high": [20.0 - i * 0.5 for i in range(15)],
            "low": [19.5 - i * 0.5 for i in range(15)],
            "close": [20.0 - i * 0.5 for i in range(15)],
        })

        status, k, d, j = TechnicalAnalysis.get_kdj(df)

        self.assertLess(k, 20)
        self.assertEqual(status, "OVERSOLD")

    def test_kdj_insufficient_data(self):
        """数据不足"""
        df = pd.DataFrame({
            "high": [10.5],
            "low": [9.5],
            "close": [10.0],
        })

        status, k, d, j = TechnicalAnalysis.get_kdj(df)

        self.assertEqual(status, "UNKNOWN")
        self.assertEqual(k, 0)
        self.assertEqual(d, 0)
        self.assertEqual(j, 0)

    def test_kdj_none_input(self):
        """None 输入"""
        status, k, d, j = TechnicalAnalysis.get_kdj(None)

        self.assertEqual(status, "UNKNOWN")
        self.assertEqual(k, 0)


class TestRSI(unittest.TestCase):
    """测试 RSI 计算"""

    def setUp(self):
        np.random.seed(42)
        n = 30
        self.df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=n),
            "close": 10 + np.cumsum(np.random.randn(n) * 0.3),
        })

    def test_rsi_calculation(self):
        """RSI 计算返回正确范围"""
        rsi = TechnicalAnalysis.get_rsi(self.df, period=6)

        self.assertIsInstance(rsi, float)
        self.assertGreaterEqual(rsi, 0)
        self.assertLessEqual(rsi, 100)

    def test_rsi_overbought(self):
        """超买区 - 持续上涨"""
        df = pd.DataFrame({
            "close": [10.0 + i * 0.5 for i in range(20)],
        })

        rsi = TechnicalAnalysis.get_rsi(df, period=6)

        self.assertGreater(rsi, 70)

    def test_rsi_oversold(self):
        """超卖区 - 持续下跌"""
        df = pd.DataFrame({
            "close": [20.0 - i * 0.5 for i in range(20)],
        })

        rsi = TechnicalAnalysis.get_rsi(df, period=6)

        self.assertLess(rsi, 30)

    def test_rsi_insufficient_data(self):
        """数据不足"""
        df = pd.DataFrame({"close": [10.0, 11.0]})

        rsi = TechnicalAnalysis.get_rsi(df, period=6)

        self.assertEqual(rsi, 50.0)

    def test_rsi_none_input(self):
        """None 输入"""
        rsi = TechnicalAnalysis.get_rsi(None, period=6)

        self.assertEqual(rsi, 50.0)

    def test_rsi_different_periods(self):
        """不同周期计算"""
        rsi_6 = TechnicalAnalysis.get_rsi(self.df, period=6)
        rsi_14 = TechnicalAnalysis.get_rsi(self.df, period=14)

        self.assertIsInstance(rsi_6, float)
        self.assertIsInstance(rsi_14, float)


class TestTrendAnalysis(unittest.TestCase):
    """测试趋势分析"""

    def test_trend_up(self):
        """上升趋势"""
        df = pd.DataFrame({
            "close": [10.0 + i * 0.5 for i in range(30)],
        })

        trend = TechnicalAnalysis.analyze_trend(df)

        self.assertEqual(trend, "UP")

    def test_trend_down(self):
        """下降趋势"""
        df = pd.DataFrame({
            "close": [20.0 - i * 0.5 for i in range(30)],
        })

        trend = TechnicalAnalysis.analyze_trend(df)

        self.assertEqual(trend, "DOWN")

    def test_trend_insufficient_data(self):
        """数据不足"""
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})

        trend = TechnicalAnalysis.analyze_trend(df)

        self.assertEqual(trend, "UNKNOWN")

    def test_trend_none_input(self):
        """None 输入"""
        trend = TechnicalAnalysis.analyze_trend(None)

        self.assertEqual(trend, "UNKNOWN")


class TestRSIPandas(unittest.TestCase):
    """测试 RSI Pandas 序列计算"""

    def test_rsi_series_calculation(self):
        """RSI 序列计算"""
        close = pd.Series([10.0 + i * 0.3 for i in range(30)])

        rsi = TechnicalAnalysis.calculate_rsi_pandas(close, period=14)

        self.assertEqual(len(rsi), len(close))
        self.assertTrue((rsi >= 0).all())
        self.assertTrue((rsi <= 100).all())

    def test_rsi_series_insufficient_data(self):
        """数据不足返回空序列"""
        close = pd.Series([10.0, 11.0])

        rsi = TechnicalAnalysis.calculate_rsi_pandas(close, period=14)

        self.assertTrue(rsi.empty)

    def test_rsi_series_none_input(self):
        """None 输入"""
        rsi = TechnicalAnalysis.calculate_rsi_pandas(None, period=14)

        self.assertTrue(rsi.empty)


class TestRSIOversoldFeatures(unittest.TestCase):
    """测试 RSI 超卖特征分析"""

    def test_consecutive_oversold_days(self):
        """连续超卖天数检测"""
        close = pd.Series([20.0 - i * 0.5 for i in range(30)])

        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)

        self.assertIn("consecutive_oversold_days", result)
        self.assertIn("days_since_healthy", result)
        self.assertIn("stagnation_detected", result)
        self.assertIn("feature_text", result)
        self.assertIsInstance(result["consecutive_oversold_days"], int)

    def test_days_since_healthy(self):
        """距健康状态天数"""
        close = pd.Series([10.0] * 10 + [10.0 - i * 0.3 for i in range(20)])

        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)

        self.assertIn("days_since_healthy", result)

    def test_stagnation_detection(self):
        """钝化检测"""
        close = pd.Series([10.0] * 10 + [10.0 - i * 0.1 for i in range(20)])

        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)

        self.assertIn("stagnation_detected", result)
        self.assertIn(result["stagnation_detected"], [True, False])

    def test_insufficient_data(self):
        """数据不足"""
        close = pd.Series([10.0, 11.0, 12.0])

        result = TechnicalAnalysis.analyze_rsi_oversold_features(close, period=14)

        self.assertEqual(result["consecutive_oversold_days"], 0)
        self.assertEqual(result["days_since_healthy"], 99)
        self.assertIn("缺乏足够历史数据", result["feature_text"])


class TestPolarsExpressions(unittest.TestCase):
    """测试 Polars 表达式"""

    def test_rsi_expr(self):
        """RSI Polars 表达式"""
        import polars as pl

        df = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 30,
            "close": [10.0 + i * 0.3 for i in range(30)],
        })

        lf = pl.from_pandas(df).lazy()
        result = lf.with_columns(
            TechnicalAnalysis.get_rsi_expr("close", period=6, alias="rsi")
        ).collect()

        self.assertIn("rsi", result.columns)
        rsi_values = result["rsi"].to_list()
        self.assertTrue(all(0 <= v <= 100 for v in rsi_values if not pd.isna(v)))

    def test_macd_expr(self):
        """MACD Polars 表达式"""
        import polars as pl

        df = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 50,
            "close": [10.0 + i * 0.2 for i in range(50)],
        })

        lf = pl.from_pandas(df).lazy()
        result = lf.with_columns(
            TechnicalAnalysis.get_macd_expr("close")
        ).collect()

        self.assertIn("macd_struct", result.columns)

    def test_kdj_expr(self):
        """KDJ Polars 表达式"""
        import polars as pl

        df = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 20,
            "high": [10.5 + i * 0.2 for i in range(20)],
            "low": [9.5 + i * 0.2 for i in range(20)],
            "close": [10.0 + i * 0.2 for i in range(20)],
        })

        lf = pl.from_pandas(df).lazy()
        result = lf.with_columns(
            TechnicalAnalysis.get_kdj_expr()
        ).collect()

        self.assertIn("kdj_struct", result.columns)


if __name__ == "__main__":
    unittest.main()
