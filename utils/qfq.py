import polars as pl
import pandas as pd
from typing import Literal


def qfq_ratio_expr(
    col_name: str = "adj_factor",
    group_col: str | None = "ts_code",
    ref: Literal["last", "first"] = "last",
) -> pl.Expr:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio expression in Polars.

    ``ref`` selects the base factor used to normalize absolute price levels:
    - ``"last"``  (default): base = latest available date -> live/display and technical analysis.
    - ``"first"``: base = first trading day -> backtest Point-in-Time, so the absolute price
      level does NOT depend on future ex-right information (reproducible backtests).

    The base is derived from the FILLED factor series (forward/backward fill), so leading
    nulls never collapse the group to an unadjusted ratio. If factor values are missing, they
    are forward filled first to carry forward existing factors, then backward filled to handle
    any remaining leading nulls.
    """
    expr = pl.col(col_name).forward_fill().backward_fill()
    if group_col:
        filled = expr.over(group_col)
        base = (expr.first() if ref == "first" else expr.last()).over(group_col)
    else:
        filled = expr
        base = expr.first() if ref == "first" else expr.last()

    ratio = pl.when((base == 0) | base.is_null()).then(1.0).otherwise(filled / base)
    return ratio.alias("qfq_ratio")


def qfq_ratio_series(series: pd.Series) -> pd.Series | None:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio series in Pandas.
    Normalizes adjustment factors to the LATEST available date (base="latest").

    If factor values are missing, they are forward filled first to carry forward existing factors.
    Then backward fill is applied to handle any remaining leading nulls.

    Returns:
        pd.Series: The adjustment ratio series.
        None: If no adjustment is needed (e.g. all factors are identical, series is empty, etc.).
    """
    if series is None or series.empty:
        return None

    # Fill missing values: first forward fill, then backward fill.
    filled = series.ffill().bfill()

    # If all values are still null, return None.
    if filled.isna().all():
        return None

    latest_factor = filled.iloc[-1]

    # If latest factor is 0 or null, return None (no adjustment).
    if latest_factor == 0 or pd.isna(latest_factor):
        return None

    # For optimization, return None if all factors are identical to latest_factor.
    if (filled == latest_factor).all():
        return None

    return filled / latest_factor
