import datetime

import pandas as pd
import polars as pl
import pytest
from unittest.mock import AsyncMock, MagicMock

from utils.technical_analysis import TechnicalAnalysis
from strategies.oversold_strategy import OversoldStrategy

# 分层归属：单组件（OversoldStrategy）+ DataProcessor mock，无 DB 依赖 → 单元层
pytestmark = pytest.mark.unit


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


# CP13-T01: rsi_period=2 → 最小样本门 period*5=10；15 根同时覆盖 5 日量比窗口与 RSI 预热。
_SPLIT_SAMPLE_ROWS = 15
_SPLIT_DATES = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(_SPLIT_SAMPLE_ROWS)]


def _build_split_history(*, with_adj_factor: bool) -> pd.DataFrame:
    """构造 2 拆 1 拆股序列：拆股日原始价减半、原始量翻倍、复权因子翻倍。"""
    split_ratio = 2.0
    raw_closes = [10.0 - i * 0.1 for i in range(_SPLIT_SAMPLE_ROWS - 1)]
    raw_closes.append(raw_closes[-1] / split_ratio)
    rows = [
        {
            "ts_code": "000001.SZ",
            "trade_date": d,
            "open": c,
            "high": c + 0.5,
            "low": c - 0.5,
            "close": c,
            "vol": 200.0 if i == _SPLIT_SAMPLE_ROWS - 1 else 100.0,
        }
        for i, (d, c) in enumerate(zip(_SPLIT_DATES, raw_closes, strict=True))
    ]
    if with_adj_factor:
        for i, row in enumerate(rows):
            row["adj_factor"] = split_ratio if i == _SPLIT_SAMPLE_ROWS - 1 else 1.0
    return pd.DataFrame(rows)


class TestOversoldVolumeThreshold:
    def test_vol_ratio_filter_reduces_candidates(self):
        # SC-05: day_count 门槛 period*5=70，数据须 ≥ 70 天才有候选可筛
        dates = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(75)]

        high_vol_df = _build_quotes_df("000001.SZ", dates, 10.0, 1000.0, vol_spike_at=70)
        low_vol_df = _build_quotes_df("000002.SZ", dates, 8.0, 100.0)
        df = pl.concat([high_vol_df, low_vol_df])

        rsi_col = "rsi_14"
        rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name="qfq_close", period=14, alias=rsi_col)
        vol_ratio_expr = (
            pl.when(pl.col("vol").shift(1).rolling_mean(5).over("ts_code") > 0)
            .then(pl.col("vol") / pl.col("vol").shift(1).rolling_mean(5).over("ts_code"))
            .otherwise(None)
            .alias("vol_ratio_5d")
        )

        result = (
            df.lazy()
            .with_columns(
                [
                    rsi_expr.over("ts_code"),
                    vol_ratio_expr,
                    pl.col("close").count().over("ts_code").alias("day_count"),
                ]
            )
            .filter(pl.col("trade_date") == dates[-1])
            .filter(pl.col("day_count") >= 70)  # SC-05: period=14 × 5（原 period*2=28）
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
        # SC-05: day_count 门槛 period*5=70，数据须 ≥ 70 天才有候选可筛
        dates = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(75)]
        df = _build_quotes_df("000004.SZ", dates, 6.0, 200.0)

        rsi_col = "rsi_14"
        rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name="qfq_close", period=14, alias=rsi_col)
        vol_ratio_expr = (
            pl.when(pl.col("vol").shift(1).rolling_mean(5).over("ts_code") > 0)
            .then(pl.col("vol") / pl.col("vol").shift(1).rolling_mean(5).over("ts_code"))
            .otherwise(None)
            .alias("vol_ratio_5d")
        )

        result = (
            df.lazy()
            .with_columns(
                [
                    rsi_expr.over("ts_code"),
                    vol_ratio_expr,
                    pl.col("close").count().over("ts_code").alias("day_count"),
                ]
            )
            .filter(pl.col("trade_date") == dates[-1])
            .filter(pl.col("day_count") >= 70)  # SC-05: period=14 × 5（原 period*2=28）
            .filter(pl.col(rsi_col) < 30)
            .filter(pl.col("vol_ratio_5d") >= 999.0)
            .collect()
        )

        assert result.height == 0, "Extreme vol_ratio_threshold should exclude all stocks"

    def test_zero_threshold_includes_low_volume(self):
        # SC-05: day_count 门槛 period*5=70，数据须 ≥ 70 天才有候选可筛
        dates = [datetime.date(2026, 4, 1) + datetime.timedelta(days=i) for i in range(75)]
        df = _build_quotes_df("000003.SZ", dates, 5.0, 50.0)

        rsi_col = "rsi_14"
        rsi_expr = TechnicalAnalysis.get_rsi_expr(col_name="qfq_close", period=14, alias=rsi_col)
        vol_ratio_expr = (
            pl.when(pl.col("vol").shift(1).rolling_mean(5).over("ts_code") > 0)
            .then(pl.col("vol") / pl.col("vol").shift(1).rolling_mean(5).over("ts_code"))
            .otherwise(None)
            .alias("vol_ratio_5d")
        )

        result = (
            df.lazy()
            .with_columns(
                [
                    rsi_expr.over("ts_code"),
                    vol_ratio_expr,
                    pl.col("close").count().over("ts_code").alias("day_count"),
                ]
            )
            .filter(pl.col("trade_date") == dates[-1])
            .filter(pl.col("day_count") >= 70)  # SC-05: period=14 × 5（原 period*2=28）
            .filter(pl.col(rsi_col) < 30)
            .filter(pl.col("vol_ratio_5d") >= 0.0)
            .collect()
        )

        if result.height > 0:
            assert "000003.SZ" in result["ts_code"].to_list()

    @pytest.mark.asyncio
    async def test_split_artifact_volume_spike_should_not_pass_threshold(self):
        """
        N-7:
        A split can double raw volume on the event day; strategy should use adjusted
        volume basis so this synthetic spike does not pass vol_ratio threshold.

        CP13-T01: 原用例仅 6 根样本，低于 day_count >= rsi_period*5=10 的门槛，前置门
        直接短路，复权/成交量逻辑从未执行。现使用 15 根样本（满足门槛，并覆盖 5 日量比
        窗口与 RSI 预热），同时保留「未复权原始放量能通过阈值」的对照组，确保用例不再空转。
        """
        strategy = OversoldStrategy()
        dp = MagicMock()
        dp.cache = MagicMock()
        dp.trade_calendar = MagicMock()
        dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=_SPLIT_DATES[-1])
        dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=_SPLIT_DATES[0])

        snapshot = pd.DataFrame([{"ts_code": "000001.SZ", "name": "demo"}])
        context = {
            "screening_data": snapshot,
            "data_processor": dp,
            "trade_date": _SPLIT_DATES[-1],
        }

        # 复权口径：拆股日原始量翻倍被 2.0 倍复权因子还原，量比 = 1.0。
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=_build_split_history(with_adj_factor=True))
        adjusted_hit = await strategy._math_filter(
            context=context,
            rsi_period=2,
            rsi_threshold=30,
            vol_ratio_threshold=0.9,
        )
        assert len(adjusted_hit) == 1, "足量样本应通过 day_count/RSI 前置门并进入成交量筛"
        assert adjusted_hit["ts_code"].tolist() == ["000001.SZ"]
        assert adjusted_hit["vol_ratio_5d"].iloc[0] == pytest.approx(1.0)
        assert adjusted_hit["rsi_2"].iloc[0] < 30

        out = await strategy._math_filter(
            context=context,
            rsi_period=2,
            rsi_threshold=30,
            vol_ratio_threshold=1.5,
        )
        assert out.empty, "Split-only raw volume spike should be neutralized by adjusted volume basis"

        # 对照组：同一行情的 split 日不提供 adj_factor 时，原始量比 2.0 应通过 1.5 阈值，
        # 证明空集由复权口径而非样本不足造成。
        dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=_build_split_history(with_adj_factor=False))
        raw_unadjusted = await strategy._math_filter(
            context=context,
            rsi_period=2,
            rsi_threshold=30,
            vol_ratio_threshold=1.5,
        )
        assert len(raw_unadjusted) == 1, "未复权原始放量应命中，证明样本门已通过且复权口径是唯一差异"
        assert raw_unadjusted["vol_ratio_5d"].iloc[0] == pytest.approx(2.0)
