"""
K-Line Chart utilities using mplfinance.

Generates professional candlestick charts with volume subplots and
moving averages, returning a ``matplotlib.figure.Figure`` for inline
display via ``flet_charts.MatplotlibChartWithToolbar``.
"""

import logging

import mplfinance as mpf
import pandas as pd

import flet as ft
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
