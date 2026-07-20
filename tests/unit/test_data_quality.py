# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
import pandas as pd

from data.persistence.data_quality import DataQualityService

pytestmark = pytest.mark.unit


class TestDataQualityServiceCheckContinuity:
    def test_empty_df(self):
        result = DataQualityService.check_continuity(pd.DataFrame(), "date", pd.DataFrame())
        assert result["missing_count"] == 0
        assert result["coverage_ratio"] == 0.0

    def test_full_coverage(self):
        df = pd.DataFrame({"date": pd.date_range("2024-01-02", periods=5, freq="B")})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.date_range("2024-01-02", periods=5, freq="B"),
                "is_open": [1, 1, 1, 1, 1],
            }
        )
        result = DataQualityService.check_continuity(df, "date", trade_cal)
        assert result["missing_count"] == 0
        assert result["coverage_ratio"] == 1.0

    def test_missing_dates(self):
        dates = pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-08"])
        df = pd.DataFrame({"date": dates})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.date_range("2024-01-02", periods=5, freq="B"),
                "is_open": [1, 1, 1, 1, 1],
            }
        )
        result = DataQualityService.check_continuity(df, "date", trade_cal)
        assert result["missing_count"] > 0
        assert result["coverage_ratio"] < 1.0

    def test_string_dates(self):
        df = pd.DataFrame({"date": ["20240102", "20240103", "20240104"]})
        trade_cal = pd.DataFrame(
            {
                "cal_date": ["20240102", "20240103", "20240104"],
                "is_open": [1, 1, 1],
            }
        )
        result = DataQualityService.check_continuity(df, "date", trade_cal)
        assert result["coverage_ratio"] >= 0.0

    def test_no_expected_trading_dates(self):
        df = pd.DataFrame({"date": pd.date_range("2024-01-02", periods=3)})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.date_range("2024-02-01", periods=3),
                "is_open": [0, 0, 0],
            }
        )
        result = DataQualityService.check_continuity(df, "date", trade_cal)
        assert result["coverage_ratio"] == 1.0

    def test_max_missing_report(self):
        assert DataQualityService.MAX_MISSING_REPORT == 10


class TestDataQualityServiceCheckRecency:
    def test_empty_df(self):
        result = DataQualityService.check_recency(pd.DataFrame(), "date", "20240110")
        assert result["lag_days"] == DataQualityService.LAG_DEFAULT

    def test_fresh_data(self):
        df = pd.DataFrame({"date": ["20240110"]})
        result = DataQualityService.check_recency(df, "date", "20240110")
        assert result["lag_days"] == 0

    def test_stale_data(self):
        df = pd.DataFrame({"date": ["20240101"]})
        result = DataQualityService.check_recency(df, "date", "20240110")
        assert result["lag_days"] > 0

    def test_na_date(self):
        df = pd.DataFrame({"date": [None]})
        result = DataQualityService.check_recency(df, "date", "20240110")
        assert result["lag_days"] == DataQualityService.LAG_ERROR

    def test_constants(self):
        assert DataQualityService.LAG_DEFAULT == 9999
        assert DataQualityService.LAG_ERROR == -1

    def test_datetime_column(self):
        df = pd.DataFrame({"date": pd.to_datetime(["2024-01-10"])})
        result = DataQualityService.check_recency(df, "date", "2024-01-10")
        assert result["lag_days"] == 0
        assert result["latest_data_date"] == "20240110"


class TestDataQualityServiceCheckNulls:
    def test_empty_df(self):
        result = DataQualityService.check_nulls(pd.DataFrame())
        assert result == {}

    def test_no_nulls(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        result = DataQualityService.check_nulls(df)
        assert result["a"] == 0.0
        assert result["b"] == 0.0

    def test_some_nulls(self):
        df = pd.DataFrame({"a": [1, None, 3], "b": [4, 5, 6]})
        result = DataQualityService.check_nulls(df)
        assert result["a"] == pytest.approx(1 / 3)
        assert result["b"] == 0.0

    def test_specific_columns(self):
        df = pd.DataFrame({"a": [1, None], "b": [None, None], "c": [1, 2]})
        result = DataQualityService.check_nulls(df, columns=["a", "c"])
        assert "b" not in result
        assert result["a"] == 0.5
        assert result["c"] == 0.0


class TestDataQualityServiceCheckCrossValidation:
    def test_none_df(self):
        result = DataQualityService.check_cross_validation(None, [])
        assert result == []

    def test_empty_df(self):
        result = DataQualityService.check_cross_validation(pd.DataFrame(), [])
        assert result == []

    def test_no_rules(self):
        df = pd.DataFrame({"vol": [100], "buy_vol": [60], "sell_vol": [40]})
        result = DataQualityService.check_cross_validation(df, [])
        assert result == []

    def test_passing_rule(self):
        df = pd.DataFrame({"vol": [100], "buy_vol": [60], "sell_vol": [40]})
        rules = [("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)]
        result = DataQualityService.check_cross_validation(df, rules)
        assert result == []

    def test_failing_rule(self):
        df = pd.DataFrame({"vol": [100], "buy_vol": [30], "sell_vol": [30]})
        rules = [("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)]
        result = DataQualityService.check_cross_validation(df, rules)
        assert len(result) == 1
        assert "VolCheck" in result[0]

    def test_execution_error(self):
        df = pd.DataFrame({"a": [1]})
        rules = [("BadRule", "nonexistent_col * 2", 0.1)]
        result = DataQualityService.check_cross_validation(df, rules)
        assert len(result) == 1
        assert "BadRule" in result[0]
        assert "error" in result[0].lower()

    def test_valid_expression(self):
        df = pd.DataFrame({"close": [10], "open": [10]})
        rules = [("DiffCheck", "close - open", 0.05)]
        result = DataQualityService.check_cross_validation(df, rules)
        assert result == []

    def test_invalid_expression_semicolon_raises(self):
        df = pd.DataFrame({"close": [10], "open": [8]})
        rules = [("Injection", "close; import os", 0.05)]
        with pytest.raises(ValueError, match="Invalid expression"):
            DataQualityService.check_cross_validation(df, rules)

    def test_invalid_expression_quotes_raises(self):
        df = pd.DataFrame({"close": [10]})
        rules = [("Injection", "__import__('os')", 0.05)]
        with pytest.raises(ValueError, match="Invalid expression"):
            DataQualityService.check_cross_validation(df, rules)

    def test_unrecoverable_exception_propagates(self):
        # ValueError is unrecoverable: re-raised with schema/type error message
        df = pd.DataFrame({"a": [1]})
        rules = [("BadRule", "a; rm -rf /", 0.1)]
        with pytest.raises(ValueError, match="Data quality check failed"):
            DataQualityService.check_cross_validation(df, rules)

    def test_recoverable_exception_logged(self, caplog):
        # NameError (UndefinedVariableError) is recoverable: logged and continues
        df = pd.DataFrame({"a": [1]})
        rules = [("BadRule", "nonexistent_col * 2", 0.1)]
        with caplog.at_level("WARNING"):
            result = DataQualityService.check_cross_validation(df, rules)
        assert len(result) == 1
        assert "BadRule" in result[0]
        assert any("Data quality check warning" in r.getMessage() for r in caplog.records)


class TestDataQualityServiceCheckContinuityExtended:
    def test_all_nan_dates(self):
        df = pd.DataFrame({"date": [None, None]})
        trade_cal = pd.DataFrame({"cal_date": ["20240102"], "is_open": [1]})
        result = DataQualityService.check_continuity(df, "date", trade_cal)
        assert result["missing_count"] == 0
        assert result["coverage_ratio"] == 0.0

    def test_trade_cal_with_datetime(self):
        df = pd.DataFrame({"date": pd.date_range("2024-01-02", periods=3, freq="B")})
        trade_cal = pd.DataFrame(
            {
                "cal_date": pd.date_range("2024-01-02", periods=5, freq="B"),
                "is_open": [1, 1, 1, 1, 1],
            }
        )
        result = DataQualityService.check_continuity(df, "date", trade_cal)
        assert result["missing_count"] >= 0
        assert result["coverage_ratio"] <= 1.0
