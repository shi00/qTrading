"""
K-Line Chart utilities.

Provides two rendering paths:
1. ``generate_kline_figure`` — static matplotlib Figure via mplfinance (legacy).
2. ``generate_kline_chart_data`` — flet-charts native data for interactive
   ``CandlestickChart`` + ``BarChart`` (dynamic, with hover tooltips).
"""

import logging
from dataclasses import dataclass

import flet as ft
import flet_charts as fch
import mplfinance as mpf
import pandas as pd
from matplotlib.figure import Figure

from ui.theme import AppColors

logger = logging.getLogger(__name__)

# ── A-share market style: red rise / green fall ──────────────────────
_RISE_COLOR = "#F44336"  # Red for rise (China convention)
_FALL_COLOR = "#26A69A"  # Green for fall


def _build_market_colors(is_dark: bool):
    """Build mplfinance MarketColors matching the app theme."""
    return mpf.make_marketcolors(
        up=_RISE_COLOR,
        down=_FALL_COLOR,
        edge="inherit",
        wick="inherit",
        volume="in",
        ohlc="inherit",
    )


def _build_style(is_dark: bool):
    """Build a full mplfinance style dict."""
    mc = _build_market_colors(is_dark)
    base = "nightclouds" if is_dark else "charles"
    return mpf.make_mpf_style(
        base_mpf_style=base,
        marketcolors=mc,
        rc={
            "font.size": 9,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "font.family": ["Microsoft YaHei", "SimHei", "sans-serif"],
            "axes.unicode_minus": False,
        },
    )


def generate_kline_figure(
    df: pd.DataFrame,
    title: str = "",
    figsize: tuple[float, float] = (8.8, 4.4),
    theme_mode: str | None = None,
) -> Figure:
    """
    Generate a K-line chart and return it as a ``matplotlib.figure.Figure``,
    ready for ``flet_charts.MatplotlibChartWithToolbar(figure=...)``.

    The figure is detached from pyplot's global ``Gcf.figs`` registry to
    prevent memory leak (flet_charts ``MatplotlibChart.will_unmount`` does
    not close the figure). The caller owns the figure reference; when the
    MatplotlibChart control is garbage-collected, the figure is freed.

    Theme is detected once at generation time; switching theme after the
    figure is rendered will NOT re-render (caller must re-invoke if needed).

    :param df: DataFrame requiring columns: trade_date, open, high, low, close.
               Optional: vol (volume).
    :param title: Chart title text.
    :param figsize: Initial figure (width, height) in inches for aspect ratio.
                    The MatplotlibChart control resizes adaptively.
    :param theme_mode: "light" | "dark" | None (auto-detect from AppColors).
    :returns: matplotlib.figure.Figure instance.
    """
    if df is None or df.empty:
        raise ValueError("Empty DataFrame — cannot render chart")

    # ── 1. Prepare OHLCV DataFrame with DatetimeIndex ────────────
    chart_df = df.copy()

    # Ensure date column
    if "trade_date" in chart_df.columns:
        chart_df["trade_date"] = pd.to_datetime(chart_df["trade_date"])
        chart_df = chart_df.set_index("trade_date")
    elif not isinstance(chart_df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame needs a 'trade_date' column or DatetimeIndex")

    # Standardise column names to what mplfinance expects
    rename_map = {}
    for col in ("Open", "High", "Low", "Close", "Volume"):
        lower = col.lower()
        if lower in chart_df.columns:
            rename_map[lower] = col
    # Handle tushare 'vol' → 'Volume'
    if "vol" in chart_df.columns and "Volume" not in chart_df.columns:
        rename_map["vol"] = "Volume"
    if rename_map:
        chart_df = chart_df.rename(columns=rename_map)

    # Sort chronologically
    chart_df = chart_df.sort_index()

    has_volume = bool("Volume" in chart_df.columns and chart_df["Volume"].sum() > 0)

    # ── 2. Theme ─────────────────────────────────────────────────
    if theme_mode is None:
        is_dark = AppColors._CURRENT_THEME_MODE == ft.ThemeMode.DARK
    else:
        is_dark = theme_mode == "dark"

    style = _build_style(is_dark)

    # ── 3. Moving Averages ───────────────────────────────────────
    mav = (5, 10, 20)

    # ── 4. Render to Figure ──────────────────────────────────────
    # returnfig=True returns (fig, axeslist). Detach the figure from pyplot's
    # global Gcf.figs registry so it does not accumulate across dialog opens
    # (flet_charts MatplotlibChart.will_unmount does not close the figure).
    fig, _axes = mpf.plot(
        chart_df,
        type="candle",
        style=style,
        title=title,
        mav=mav,
        volume=has_volume,
        figsize=figsize,
        tight_layout=True,
        returnfig=True,
    )
    from matplotlib._pylab_helpers import Gcf

    fig_number = getattr(fig, "number", None)
    if fig_number is not None:
        Gcf.figs.pop(fig_number, None)
    return fig


# ── flet-charts native dynamic chart data ────────────────────────────


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


# MA periods for tooltip display (matches generate_kline_figure's mav).
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

    # Standardise column names (reuse generate_kline_figure's rename logic)
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
                            color=_RISE_COLOR if is_rise else _FALL_COLOR,
                        )
                    ],
                )
            )

        if i % label_step == 0 or i == n - 1:
            date_labels.append(
                fch.ChartAxisLabel(
                    value=x,
                    label=ft.Text(row["trade_date"].strftime("%m-%d"), size=10),
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
