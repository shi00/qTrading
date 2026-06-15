import polars as pl
import pandas as pd


def qfq_ratio_expr(col_name: str = "adj_factor", group_col: str | None = "ts_code") -> pl.Expr:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio expression in Polars.
    Normalizes adjustment factors to the LATEST available date (base="latest").

    If the first factor is missing, it is backward filled first to prevent price drift.
    Then forward fill is applied for subsequent missing values.
    """
    # First forward fill, then backward fill to ensure all elements are filled.
    # Note: we group by group_col (e.g. ts_code) if specified.
    expr = pl.col(col_name).forward_fill().backward_fill()
    if group_col:
        filled = expr.over(group_col)
        latest = filled.last().over(group_col)
    else:
        filled = expr
        latest = filled.last()

    # Avoid division by zero or None. If latest factor is 0 or null, treat as 1.0.
    safe_latest = pl.when((latest == 0) | latest.is_null()).then(1.0).otherwise(latest)

    return (filled.fill_null(safe_latest) / safe_latest).alias("qfq_ratio")


def qfq_ratio_series(series: pd.Series) -> pd.Series | None:
    """
    Calculate Point-in-Time Forward Adjusted Price (QFQ) ratio series in Pandas.
    Normalizes adjustment factors to the LATEST available date (base="latest").

    If the first factor is missing, it is backward filled first to prevent price drift.
    Then forward fill is applied for subsequent missing values.

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
