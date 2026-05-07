import datetime

import pandas as pd
import pytest

from utils.technical_analysis import TechnicalAnalysis


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
        assert result is not None
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
        assert result is not None

    def test_no_trade_date_column(self):
        rows = [
            (10.0, 11.0, 9.0, 10.0, 1.0, None),
            (11.0, 12.0, 10.0, 10.5, 1.1, None),
        ]
        df = _make_df(rows, with_trade_date=False)
        result = TechnicalAnalysis._get_qfq_df(df)
        assert result is not None
        assert not result.empty
