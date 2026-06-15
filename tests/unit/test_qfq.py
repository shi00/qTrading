import pytest
import polars as pl
import pandas as pd
from utils.qfq import qfq_ratio_expr, qfq_ratio_series


class TestQFQ:
    def test_qfq_ratio_expr_polars(self):
        df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": ["20240101", "20240102", "20240103"],
                "adj_factor": [None, 2.0, 1.0],  # MKT-003: First is None, requires backward_fill
            }
        )

        # MKT-001: Base is "latest" (1.0)
        # Day 1: None -> bfill to 2.0. Ratio: 2.0 / 1.0 = 2.0
        # Day 2: 2.0. Ratio: 2.0 / 1.0 = 2.0
        # Day 3: 1.0. Ratio: 1.0 / 1.0 = 1.0

        result = df.with_columns(qfq_ratio_expr())
        ratios = result["qfq_ratio"].to_list()
        assert ratios == pytest.approx([2.0, 2.0, 1.0])

    def test_qfq_ratio_series_pandas(self):
        series = pd.Series([None, 2.0, 1.0])
        result = qfq_ratio_series(series)
        assert result is not None
        assert result.tolist() == pytest.approx([2.0, 2.0, 1.0])

    def test_qfq_ratio_series_returns_none(self):
        # All same factors should return None for optimization
        series = pd.Series([1.0, 1.0, 1.0])
        result = qfq_ratio_series(series)
        assert result is None
