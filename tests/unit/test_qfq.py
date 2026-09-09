import pytest
import polars as pl
import pandas as pd
from utils.qfq import qfq_ratio_expr, qfq_ratio_series

pytestmark = pytest.mark.unit


class TestQFQ:
    def test_qfq_ratio_expr_polars(self):
        df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": ["20240101", "20240102", "20240103"],
                "adj_factor": [
                    None,
                    2.0,
                    1.0,
                ],  # MKT-003: First is None, requires backward_fill
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

    def test_qfq_ratio_expr_multi_group(self):
        # Multiple stocks with different adjustment factors, to ensure robust over() execution
        df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ"],
                "trade_date": ["20240101", "20240101", "20240102", "20240102"],
                "adj_factor": [2.0, None, 1.0, 3.0],
            }
        )

        # For 000001.SZ: [2.0, 1.0]. Latest: 1.0. Ratios: [2.0, 1.0]
        # For 000002.SZ: [None, 3.0] -> bfill to [3.0, 3.0]. Latest: 3.0. Ratios: [1.0, 1.0]
        # Expected ratios in order of df:
        # Row 0 (000001.SZ, 20240101): 2.0 / 1.0 = 2.0
        # Row 1 (000002.SZ, 20240101): 3.0 / 3.0 = 1.0
        # Row 2 (000001.SZ, 20240102): 1.0 / 1.0 = 1.0
        # Row 3 (000002.SZ, 20240102): 3.0 / 3.0 = 1.0

        result = df.with_columns(qfq_ratio_expr())
        ratios = result["qfq_ratio"].to_list()
        assert ratios == pytest.approx([2.0, 1.0, 1.0, 1.0])

    def test_qfq_ratio_expr_all_null(self):
        df = pl.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "adj_factor": [None, None]})
        result = df.with_columns(qfq_ratio_expr())
        ratios = result["qfq_ratio"].to_list()
        assert ratios == pytest.approx([1.0, 1.0])

    def test_qfq_ratio_expr_zero_latest(self):
        df = pl.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "adj_factor": [2.0, 0.0]})
        result = df.with_columns(qfq_ratio_expr())
        ratios = result["qfq_ratio"].to_list()
        assert ratios == pytest.approx([1.0, 1.0])

    def test_qfq_ratio_expr_empty(self):
        df = pl.DataFrame(
            {
                "ts_code": pl.Series(dtype=pl.String),
                "adj_factor": pl.Series(dtype=pl.Float64),
            }
        )
        result = df.with_columns(qfq_ratio_expr())
        assert result.height == 0
        assert "qfq_ratio" in result.columns

    def test_qfq_ratio_expr_pit_first_ref(self):
        # ref="first"（回测 PIT 起点基准）
        df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": ["20240101", "20240102", "20240103"],
                "adj_factor": [None, 2.0, 1.0],
            }
        )
        result = df.with_columns(qfq_ratio_expr(ref="first"))
        ratios = result["qfq_ratio"].to_list()
        # 首行 None 经 bfill → [2.0, 2.0, 1.0]；base=2.0 => [2/2, 2/2, 1/2]
        assert ratios == pytest.approx([1.0, 1.0, 0.5])

    def test_qfq_ratio_expr_pit_absolute_invariance(self):
        # PIT 起点基准：追加未来除权记录不改变历史绝对量纲（可复现）；
        # 对照 latest 基准会漂移。
        def ratios(with_future: bool):
            factors = [1.0, 1.0, 2.0] if not with_future else [1.0, 1.0, 2.0, 4.0]
            df = pl.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * len(factors),
                    "trade_date": [f"2024010{i + 1}" for i in range(len(factors))],
                    "adj_factor": factors,
                }
            )
            pit = df.with_columns(qfq_ratio_expr(ref="first"))["qfq_ratio"].to_list()
            latest = df.with_columns(qfq_ratio_expr())["qfq_ratio"].to_list()
            return pit, latest

        pit_short, latest_short = ratios(with_future=False)
        pit_long, latest_long = ratios(with_future=True)

        # PIT：历史前三行绝对量纲不因未来除权漂移
        assert pit_short == pytest.approx([1.0, 1.0, 2.0])
        assert pit_long[:3] == pytest.approx(pit_short)
        # latest：历史前三行随未来除权漂移
        assert latest_short == pytest.approx([0.5, 0.5, 1.0])
        assert latest_long[:3] == pytest.approx([0.25, 0.25, 0.5])

    def test_qfq_ratio_series_pandas_all_null(self):
        series = pd.Series([None, None])
        result = qfq_ratio_series(series)
        assert result is None

    def test_qfq_ratio_series_pandas_zero_latest(self):
        series = pd.Series([2.0, 0.0])
        result = qfq_ratio_series(series)
        assert result is None

    def test_qfq_ratio_series_pandas_empty(self):
        series = pd.Series(dtype=float)
        result = qfq_ratio_series(series)
        assert result is None
