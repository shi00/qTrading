"""
K-Line Chart utilities.

Provides ``generate_kline_chart_data`` — flet-charts native data for
interactive ``CandlestickChart`` + ``BarChart`` (dynamic, with hover tooltips).
"""

import logging
from dataclasses import dataclass

import flet as ft
import flet_charts as fch
import pandas as pd

from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


@dataclass
class KlineChartData:
    """flet-charts K-line chart data (pure data, no rendering).

    Returned by :func:`generate_kline_chart_data` for consumption by
    :class:`flet_charts.CandlestickChart` + :class:`flet_charts.BarChart`.
    """

    spots: list[fch.CandlestickChartSpot]
    volume_groups: list[fch.BarChartGroup]
    date_labels: list[fch.ChartAxisLabel]
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    max_volume: float


# MA periods for tooltip display.
_MA_PERIODS = (5, 10, 20)


def generate_kline_chart_data(
    df: pd.DataFrame,
    title: str = "",
    theme_mode: str | None = None,
) -> KlineChartData:
    """Generate flet-charts data for K-line + volume chart.

    Pure data conversion (no matplotlib/CPU-bound work), runs synchronously
    on the event loop without ThreadPoolManager (R16 exemption: pure memory op).

    :param df: DataFrame requiring columns: trade_date, open, high, low, close.
               Optional: vol (volume).
    :param title: Chart title text (unused in data conversion, for API compat).
    :param theme_mode: "light" | "dark" | None (unused, colors are theme-independent).
    :returns: KlineChartData with spots, volume groups, axis labels, and ranges.
    """
    if df is None or df.empty:
        raise ValueError("Empty DataFrame — cannot render chart")

    # ── 1. Prepare OHLCV DataFrame ───────────────────────────────
    chart_df = df.copy()

    if "trade_date" not in chart_df.columns:
        raise ValueError("DataFrame needs a 'trade_date' column")

    chart_df["trade_date"] = pd.to_datetime(chart_df["trade_date"])

    # Standardise column names (lowercase ohlc → Capitalised OHLC)
    rename_map = {}
    for col in ("Open", "High", "Low", "Close"):
        lower = col.lower()
        if lower in chart_df.columns:
            rename_map[lower] = col
    if "vol" in chart_df.columns and "Volume" not in chart_df.columns:
        rename_map["vol"] = "Volume"
    if rename_map:
        chart_df = chart_df.rename(columns=rename_map)

    # Sort chronologically and reset index for positional x-axis
    chart_df = chart_df.sort_values("trade_date").reset_index(drop=True)

    for col in ("Open", "High", "Low", "Close"):
        if col not in chart_df.columns:
            raise ValueError(f"DataFrame missing required column: {col}")

    has_volume = bool("Volume" in chart_df.columns and chart_df["Volume"].sum() > 0)

    # ── 2. Moving Averages ───────────────────────────────────────
    # NOTE(lazy): MA 均线暂不在图表上绘制 (Stack+LineChart 坐标对齐复杂度高),
    #   改为在 CandlestickChartSpot.tooltip 文本中展示 MA5/10/20 数值.
    #   ceiling: 后续需可视化 MA 趋势时. upgrade: 用户明确要求 MA 均线可视化,
    #   或 Flet 提供 charts 联动 API.
    ma_values: dict[int, list[float | None]] = {p: [None] * len(chart_df) for p in _MA_PERIODS}
    for p in _MA_PERIODS:
        if len(chart_df) >= p:
            ma_series = chart_df["Close"].rolling(p).mean()
            ma_values[p] = [None if pd.isna(v) else float(v) for v in ma_series]

    # ── 3. Build spots and volume groups ─────────────────────────
    spots: list[fch.CandlestickChartSpot] = []
    volume_groups: list[fch.BarChartGroup] = []
    date_labels: list[fch.ChartAxisLabel] = []

    n = len(chart_df)
    label_step = max(1, n // 8)

    for i in range(n):
        row = chart_df.iloc[i]
        x = float(i)
        open_v = float(row["Open"])
        high_v = float(row["High"])
        low_v = float(row["Low"])
        close_v = float(row["Close"])
        date_str = row["trade_date"].strftime("%Y-%m-%d")

        # Tooltip: date + OHLCV + MA (standard financial abbreviations, no i18n)
        tooltip_lines = [
            date_str,
            f"O:{open_v:.2f} H:{high_v:.2f}",
            f"L:{low_v:.2f} C:{close_v:.2f}",
        ]
        if has_volume:
            vol_v = float(row.get("Volume", 0))
            tooltip_lines.append(f"Vol:{vol_v:.0f}")
        for p in _MA_PERIODS:
            v = ma_values[p][i]
            if v is not None:
                tooltip_lines.append(f"MA{p}:{v:.2f}")

        spots.append(
            fch.CandlestickChartSpot(
                x=x,
                open=open_v,
                high=high_v,
                low=low_v,
                close=close_v,
                tooltip="\n".join(tooltip_lines),
            )
        )

        if has_volume:
            vol_v = float(row.get("Volume", 0))
            is_rise = close_v >= open_v
            volume_groups.append(
                fch.BarChartGroup(
                    x=i,
                    rods=[
                        fch.BarChartRod(
                            from_y=0,
                            to_y=vol_v,
                            color=AppColors.UP_RED if is_rise else AppColors.DOWN_GREEN,
                        )
                    ],
                )
            )

        if i % label_step == 0 or i == n - 1:
            date_labels.append(
                fch.ChartAxisLabel(
                    value=x,
                    label=ft.Text(row["trade_date"].strftime("%m-%d"), size=AppStyles.FONT_SIZE_CAPTION),
                )
            )

    # ── 4. Compute axis ranges ───────────────────────────────────
    min_y = float(chart_df["Low"].min())
    max_y = float(chart_df["High"].max())
    y_padding = (max_y - min_y) * 0.05
    min_y -= y_padding
    max_y += y_padding

    max_volume = float(chart_df["Volume"].max()) if has_volume else 0.0

    logger.debug("Generated KlineChartData: %d spots, title=%s", n, title)

    return KlineChartData(
        spots=spots,
        volume_groups=volume_groups,
        date_labels=date_labels,
        min_x=0.0,
        max_x=float(n - 1),
        min_y=min_y,
        max_y=max_y,
        max_volume=max_volume,
    )
