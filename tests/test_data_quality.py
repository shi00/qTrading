import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest

import pandas as pd

from data.persistence.data_quality import DataQualityService


class TestCheckContinuity(unittest.TestCase):
    """测试 check_continuity 方法"""

    def test_no_missing_dates(self):
        """无缺失交易日：coverage_ratio = 1.0"""
        dates = pd.date_range("2024-01-02", "2024-01-05", freq="B")
        df = pd.DataFrame({"trade_date": dates, "close": [10, 11, 12, 13]})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.date_range("2024-01-01", "2024-01-31"),
                "is_open": [1 if d.weekday() < 5 else 0 for d in pd.date_range("2024-01-01", "2024-01-31")],
            },
        )

        result = DataQualityService.check_continuity(df, "trade_date", trade_cal)

        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["coverage_ratio"], 1.0)

    def test_missing_dates_detected(self):
        """缺失交易日被正确检测"""
        dates = pd.to_datetime(["2024-01-02", "2024-01-05"])
        df = pd.DataFrame({"trade_date": dates, "close": [10, 13]})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.date_range("2024-01-01", "2024-01-31"),
                "is_open": [1 if d.weekday() < 5 else 0 for d in pd.date_range("2024-01-01", "2024-01-31")],
            },
        )

        result = DataQualityService.check_continuity(df, "trade_date", trade_cal)

        self.assertGreater(result["missing_count"], 0)
        self.assertLess(result["coverage_ratio"], 1.0)

    def test_empty_df(self):
        """空 DataFrame 返回默认值"""
        df = pd.DataFrame()
        trade_cal = pd.DataFrame({"cal_date": [], "is_open": []})

        result = DataQualityService.check_continuity(df, "trade_date", trade_cal)

        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["coverage_ratio"], 0.0)

    def test_string_date_column(self):
        """字符串日期列也能正确处理"""
        df = pd.DataFrame({"trade_date": ["20240102", "20240104"], "close": [10, 11]})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
                "is_open": [1, 1, 1],
            },
        )

        result = DataQualityService.check_continuity(df, "trade_date", trade_cal)

        self.assertEqual(result["missing_count"], 1)
        self.assertLess(result["coverage_ratio"], 1.0)

    def test_missing_dates_limited_report(self):
        """缺失日期列表不超过 MAX_MISSING_REPORT"""
        dates = pd.to_datetime(["2024-01-02"])
        df = pd.DataFrame({"trade_date": dates, "close": [10]})
        all_dates = pd.date_range("2024-01-01", "2024-12-31")
        trade_cal = pd.DataFrame(
            {
                "cal_date": all_dates,
                "is_open": [1 if d.weekday() < 5 else 0 for d in all_dates],
            },
        )

        result = DataQualityService.check_continuity(df, "trade_date", trade_cal)

        self.assertLessEqual(len(result["missing_dates"]), DataQualityService.MAX_MISSING_REPORT)


class TestCheckRecency(unittest.TestCase):
    """测试 check_recency 方法"""

    def test_fresh_data(self):
        """数据最新日期与参考日期相同：lag_days = 0"""
        df = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-05"]), "close": [10]})

        result = DataQualityService.check_recency(df, "trade_date", "20240105")

        self.assertEqual(result["lag_days"], 0)
        self.assertEqual(result["latest_data_date"], "20240105")

    def test_stale_data(self):
        """数据滞后：lag_days > 0"""
        df = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-03"]), "close": [10]})

        result = DataQualityService.check_recency(df, "trade_date", "20240105")

        self.assertEqual(result["lag_days"], 2)

    def test_empty_df(self):
        """空 DataFrame 返回 LAG_DEFAULT"""
        df = pd.DataFrame()

        result = DataQualityService.check_recency(df, "trade_date", "20240105")

        self.assertEqual(result["lag_days"], DataQualityService.LAG_DEFAULT)
        self.assertIsNone(result["latest_data_date"])


class TestCheckNulls(unittest.TestCase):
    """测试 check_nulls 方法"""

    def test_no_nulls(self):
        """无空值时所有列返回 0.0"""
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

        result = DataQualityService.check_nulls(df)

        self.assertEqual(result["a"], 0.0)
        self.assertEqual(result["b"], 0.0)

    def test_with_nulls(self):
        """有空值时正确计算空值率"""
        df = pd.DataFrame({"a": [1, None, 3], "b": [None, None, 6]})

        result = DataQualityService.check_nulls(df)

        self.assertAlmostEqual(result["a"], 1 / 3, places=2)
        self.assertAlmostEqual(result["b"], 2 / 3, places=2)

    def test_specific_columns(self):
        """只检查指定列"""
        df = pd.DataFrame({"a": [1, None, 3], "b": [None, None, 6], "c": [7, 8, 9]})

        result = DataQualityService.check_nulls(df, columns=["a", "c"])

        self.assertNotIn("b", result)
        self.assertAlmostEqual(result["a"], 1 / 3, places=2)
        self.assertEqual(result["c"], 0.0)

    def test_empty_df(self):
        """空 DataFrame 返回空字典"""
        df = pd.DataFrame()

        result = DataQualityService.check_nulls(df)

        self.assertEqual(result, {})

    def test_return_type(self):
        """返回类型为 dict[str, float]"""
        df = pd.DataFrame({"col_a": [1, None], "col_b": [3, 4]})

        result = DataQualityService.check_nulls(df)

        for k, v in result.items():
            self.assertIsInstance(k, str)
            self.assertIsInstance(v, float)


class TestCheckCrossValidation(unittest.TestCase):
    """测试 check_cross_validation 方法"""

    def test_no_issues(self):
        """差异在容差内不报错"""
        df = pd.DataFrame(
            {
                "vol": [100.0, 200.0],
                "buy_vol": [60.0, 120.0],
                "sell_vol": [40.0, 80.0],
            },
        )

        rules = [("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)]
        result = DataQualityService.check_cross_validation(df, rules)

        self.assertEqual(result, [])

    def test_issues_detected(self):
        """差异超出容差报错"""
        df = pd.DataFrame(
            {
                "vol": [100.0, 200.0],
                "buy_vol": [30.0, 60.0],
                "sell_vol": [30.0, 60.0],
            },
        )

        rules = [("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)]
        result = DataQualityService.check_cross_validation(df, rules)

        self.assertEqual(len(result), 1)
        self.assertIn("VolCheck", result[0])

    def test_empty_df(self):
        """空 DataFrame 返回空列表"""
        df = pd.DataFrame()

        rules = [("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)]
        result = DataQualityService.check_cross_validation(df, rules)

        self.assertEqual(result, [])

    def test_invalid_expression(self):
        """无效表达式返回错误信息而非抛出"""
        df = pd.DataFrame({"a": [1, 2]})

        rules = [("BadRule", "nonexistent_col * 2", 0.1)]
        result = DataQualityService.check_cross_validation(df, rules)

        self.assertEqual(len(result), 1)
        self.assertIn("BadRule", result[0])
        self.assertIn("error", result[0].lower())

    def test_multiple_rules(self):
        """多条规则独立执行"""
        df = pd.DataFrame(
            {
                "vol": [100.0],
                "buy_vol": [60.0],
                "sell_vol": [40.0],
                "amount": [1000.0],
                "close": [10.0],
            },
        )

        rules = [
            ("VolCheck", "vol - (buy_vol + sell_vol)", 0.05),
            ("AmtCheck", "amount - vol * close", 1.0),
        ]
        result = DataQualityService.check_cross_validation(df, rules)

        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
