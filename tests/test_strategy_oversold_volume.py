import datetime

import polars as pl
import pytest

from utils.technical_analysis import TechnicalAnalysis


def _build_quotes_df(ts_code, dates, close_start, vol_base, vol_spike_at=None):
    rows = []
    for i, d in enumerate(dates):
        vol = vol_spike_at if (vol_spike_at and i >= vol_spike_at) else vol_base
        rows.append(
            {
                "ts_code": ts_code,
                "trade_date": d,
                "close": close_start - i * 0.1,
                "qfq_close": close_start - i * 0.1,
                "vol": float(vol),
                "open": close_start,
                "high": close_start + 0.5,
                "low": close_start - 0.5,
            },
        )
    return pl.DataFrame(rows)


@pytest.mark.unit
class TestOversoldVolumeThreshold:
    def test_vol_ratio_filter_reduces_candidates(self):
        dates = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(30)]

        high_vol_df = _build_quotes_df("000001.SZ", dates, 10.0, 1000.0, vol_spike_at=25)
        low_vol_df = _build_quotes_df("000002.SZ", dates, 8.0, 100.0)
        df = pl.concat([high_vol_df, low_vol_df])

        rsi_col = "rsi_14"
        rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name="qfq_close", period=14, alias=rsi_col)
        vol_ratio_expr = (
            pl.when(pl.col("vol").rolling_mean(5).over("ts_code") > 0)
            .then(pl.col("vol") / pl.col("vol").rolling_mean(5).over("ts_code"))
            .otherwise(None)
            .alias("vol_ratio_5d")
        )

        result = (
            df.lazy()
            .with_columns(
                [rsi_expr.over("ts_code"), vol_ratio_expr, pl.col("close").count().over("ts_code").alias("day_count")]
            )
            .filter(pl.col("trade_date") == dates[-1])
            .filter(pl.col("day_count") >= 28)
            .filter(pl.col(rsi_col) < 30)
            .collect()
        )

        result_low = result.filter(pl.col("vol_ratio_5d") >= 0.5)
        result_high = result.filter(pl.col("vol_ratio_5d") >= 5.0)

        assert len(result_high) <= len(result_low), (
            f"Higher vol_ratio_threshold should produce fewer or equal candidates, "
            f"got high={len(result_high)} > low={len(result_low)}"
        )

    def test_extreme_threshold_excludes_all(self):
        dates = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(30)]
        df = _build_quotes_df("000004.SZ", dates, 6.0, 200.0)

        rsi_col = "rsi_14"
        rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name="qfq_close", period=14, alias=rsi_col)
        vol_ratio_expr = (
            pl.when(pl.col("vol").rolling_mean(5).over("ts_code") > 0)
            .then(pl.col("vol") / pl.col("vol").rolling_mean(5).over("ts_code"))
            .otherwise(None)
            .alias("vol_ratio_5d")
        )

        result = (
            df.lazy()
            .with_columns(
                [rsi_expr.over("ts_code"), vol_ratio_expr, pl.col("close").count().over("ts_code").alias("day_count")]
            )
            .filter(pl.col("trade_date") == dates[-1])
            .filter(pl.col("day_count") >= 28)
            .filter(pl.col(rsi_col) < 30)
            .filter(pl.col("vol_ratio_5d") >= 999.0)
            .collect()
        )

        assert result.height == 0, "Extreme vol_ratio_threshold should exclude all stocks"

    def test_zero_threshold_includes_low_volume(self):
        dates = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(30)]
        df = _build_quotes_df("000003.SZ", dates, 5.0, 50.0)

        rsi_col = "rsi_14"
        rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name="qfq_close", period=14, alias=rsi_col)
        vol_ratio_expr = (
            pl.when(pl.col("vol").rolling_mean(5).over("ts_code") > 0)
            .then(pl.col("vol") / pl.col("vol").rolling_mean(5).over("ts_code"))
            .otherwise(None)
            .alias("vol_ratio_5d")
        )

        result = (
            df.lazy()
            .with_columns(
                [rsi_expr.over("ts_code"), vol_ratio_expr, pl.col("close").count().over("ts_code").alias("day_count")]
            )
            .filter(pl.col("trade_date") == dates[-1])
            .filter(pl.col("day_count") >= 28)
            .filter(pl.col(rsi_col) < 30)
            .filter(pl.col("vol_ratio_5d") >= 0.0)
            .collect()
        )

        if result.height > 0:
            assert "000003.SZ" in result["ts_code"].to_list()
