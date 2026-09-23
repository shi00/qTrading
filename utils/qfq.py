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


def qfq_ratio_series(
    series: pd.Series,
    ref: Literal["last", "first"] = "last",
) -> pd.Series | None:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio series in Pandas.

    ``ref`` semantics match ``qfq_ratio_expr``:
    - ``"last"`` (default): base = latest available value -> live/display and technical analysis.
    - ``"first"``: base = first available value -> backtest Point-in-Time, so the absolute
      price level does NOT depend on future ex-right information (reproducible backtests).
      Calling a pandas-path consumer that needs PIT without ``ref="first"`` would silently
      degrade to ``"last"`` and introduce lookahead bias (the D4 defect this fixes).

    If factor values are missing, they are forward filled first to carry forward existing
    factors. Then backward fill is applied to handle any remaining leading nulls.

    Returns:
        pd.Series: The adjustment ratio series.
        None: If no adjustment is needed (empty, all-null, base zero-or-null, all-identical).
    """
    if series is None or series.empty:
        return None

    # Fill missing values: first forward fill, then backward fill.
    filled = series.ffill().bfill()

    # If all values are still null, return None.
    if filled.isna().all():
        return None

    base = filled.iloc[0] if ref == "first" else filled.iloc[-1]

    # If base is 0 or null, return None (no adjustment).
    if base == 0 or pd.isna(base):
        return None

    # For optimization, return None if all factors are identical to base.
    if (filled == base).all():
        return None

    return filled / base
