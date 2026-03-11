"""
K-Line Chart utilities using mplfinance.

Generates professional candlestick charts with volume subplots and
moving averages, rendered as in-memory PNG for inline display via ft.Image.
"""

import io
import base64
import logging

import pandas as pd
import mplfinance as mpf
import matplotlib

matplotlib.use("Agg")  # Non-interactive backend, safe for threading

import flet as ft
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


def generate_kline_png(
    df: pd.DataFrame,
    title: str = "",
    width: int = 880,
    height: int = 440,
    theme_mode=None,
) -> str:
    """
    Generate a K-line chart PNG and return it as a **base64 encoded string**,
    ready for ``ft.Image(src_base64=...)``.

    :param df: DataFrame requiring columns: trade_date, open, high, low, close.
               Optional: vol (volume).
    :param title: Chart title text.
    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param theme_mode: "light" | "dark" | None (auto-detect from AppColors).
    :returns: base64 PNG string.
    """
    if df is None or df.empty:
        raise ValueError("Empty DataFrame — cannot render chart")

    # ── 1. Prepare OHLCV DataFrame with DatetimeIndex ────────────
    chart_df = df.copy()

    # Ensure date column
    if "trade_date" in chart_df.columns:
        chart_df["trade_date"] = pd.to_datetime(chart_df["trade_date"].astype(str))
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

    # ── 4. Render to PNG buffer ──────────────────────────────────
    buf = io.BytesIO()
    dpi = 100
    figsize = (width / dpi, height / dpi)

    mpf.plot(
        chart_df,
        type="candle",
        style=style,
        title=title,
        mav=mav,
        volume=has_volume,
        figsize=figsize,
        tight_layout=True,
        savefig=dict(fname=buf, dpi=dpi, bbox_inches="tight"),
    )
    buf.seek(0)

    # ── 5. Base64 encode ─────────────────────────────────────────
    b64 = base64.b64encode(buf.read()).decode("ascii")
    buf.close()

    # ── 6. Cleanup matplotlib figures to prevent memory leak ─────
    import matplotlib.pyplot as plt

    plt.close("all")

    return b64


# ── Legacy compatibility wrappers (kept for any external callers) ──


def create_kline_chart(df, title="", theme_mode=None):
    """Legacy wrapper — returns base64 PNG string instead of Plotly Figure."""
    return generate_kline_png(df, title=title, theme_mode=theme_mode)


def generate_kline_html(df, title="", theme_mode=None):
    """Legacy wrapper — returns an <img> HTML tag with embedded base64 PNG."""
    b64 = generate_kline_png(df, title=title, theme_mode=theme_mode)
    return f'<html><body style="margin:0"><img src="data:image/png;base64,{b64}" style="width:100%"></body></html>'
