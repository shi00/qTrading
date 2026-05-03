import pandas as pd

from data.persistence.data_quality import DataQualityService


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
