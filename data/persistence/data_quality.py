import logging
import re
import typing
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_EXPR_WHITELIST = re.compile(r"^[a-zA-Z0-9_\s+\-*/().]+$")


def _validate_expr(expr: str) -> None:
    if not _EXPR_WHITELIST.match(expr):
        raise ValueError("Invalid expression: contains forbidden characters")


class DataQualityService:
    """
    Service for performing Deep Health Checks (Tier 2 & Tier 3).
    Stateless logic provider.
    """

    MAX_MISSING_REPORT = 10
    LAG_DEFAULT = 9999
    LAG_ERROR = -1

    @classmethod
    def check_continuity(cls, df: pd.DataFrame, date_col: str, trade_cal: pd.DataFrame) -> dict[str, Any]:
        """
        Tier 2: Check for missing trading days in a time-series.

        Args:
            df: Data to check.
            date_col: Name of the date column in df.
            trade_cal: DataFrame containing 'cal_date' and 'is_open'.

        Returns:
            Dict with 'missing_count', 'missing_dates', 'coverage_ratio'.
        """
        if df.empty:
            return {"missing_count": 0, "missing_dates": [], "coverage_ratio": 0.0}

        # CR-05 fix: avoid modifying caller's DataFrame in-place.
        # Work only with a local series of date strings.
        if pd.api.types.is_datetime64_any_dtype(df[date_col]):
            date_series = df[date_col]
        else:
            date_series = pd.to_datetime(df[date_col], errors="coerce")

        start_date = date_series.min()
        end_date = date_series.max()

        if pd.isna(start_date) or pd.isna(end_date):  # type: ignore[untyped]
            return {"missing_count": 0, "missing_dates": [], "coverage_ratio": 0.0}

        target_dates = set(date_series.dt.date)

        start_date_obj = start_date.date() if hasattr(start_date, "date") else start_date
        end_date_obj = end_date.date() if hasattr(end_date, "date") else end_date

        # Convert trade_cal cal_date to datetime for proper comparison
        if not pd.api.types.is_datetime64_any_dtype(trade_cal["cal_date"]):
            trade_cal_dates = pd.to_datetime(trade_cal["cal_date"], errors="coerce")
        else:
            trade_cal_dates = trade_cal["cal_date"]

        mask = (
            (trade_cal["is_open"] == 1)
            & (trade_cal_dates.dt.date >= start_date_obj)
            & (trade_cal_dates.dt.date <= end_date_obj)
        )

        cal_dates = trade_cal.loc[mask, "cal_date"]
        if pd.api.types.is_datetime64_any_dtype(cal_dates):
            expected_dates = set(cal_dates.dt.date)
        else:
            expected_dates = set(pd.to_datetime(cal_dates, errors="coerce").dt.date)

        missing = expected_dates - target_dates
        missing_list = sorted(list(missing))

        total_expected = len(expected_dates)
        if total_expected == 0:
            ratio = 1.0  # No expected trading dates in range — treat as fully covered
        else:
            ratio = 1.0 - (len(missing) / total_expected)

        return {
            "missing_count": len(missing),
            "missing_dates": missing_list[: cls.MAX_MISSING_REPORT],  # Report top N
            "coverage_ratio": ratio,
        }

    @classmethod
    def check_recency(cls, df: pd.DataFrame, date_col: str, ref_date: typing.Any) -> dict[str, Any]:
        """
        Tier 2: Check data freshness against a reference date (usually latest trading day).
        ref_date can be a string (YYYYMMDD), datetime.date, or datetime.datetime.
        """
        if df.empty:
            return {"lag_days": cls.LAG_DEFAULT, "latest_data_date": None}

        # Get latest date in DF
        max_date = df[date_col].max()
        if pd.isna(max_date):  # type: ignore[untyped]
            return {"lag_days": cls.LAG_ERROR, "latest_data_date": None}

        # Handle string vs datetime
        if pd.api.types.is_datetime64_any_dtype(df[date_col]):
            latest = max_date.strftime("%Y%m%d")
        else:
            latest = str(max_date)

        # Calculate lag
        try:
            d_latest = pd.to_datetime(latest)
            d_ref = pd.to_datetime(ref_date)
            lag = (d_ref - d_latest).days
        except (ValueError, TypeError) as exc:
            logger.debug(f"[DataQuality] Date parse failed for lag calc: {exc}")
            lag = cls.LAG_ERROR

        return {"lag_days": lag, "latest_data_date": latest}

    @staticmethod
    def check_nulls(df: pd.DataFrame, columns: list[str] = None) -> dict[str, float]:  # type: ignore[untyped]
        """
        Tier 2: Critical column null-rate analysis.
        If columns is None, checks all.
        """
        if df.empty:
            return {}

        check_cols = columns if columns else df.columns
        null_counts = df[check_cols].isnull().sum()
        total = len(df)

        ratios = (null_counts / total).to_dict()
        return {str(k): float(v) for k, v in ratios.items()}

    @staticmethod
    def check_cross_validation(df: pd.DataFrame, rules: list[tuple[str, str, float]]) -> list[str]:
        """
        Tier 3: Reliability Cross-Validation using simple expression evaluation.

        Args:
            df: Data
            rules: List of (name, expression, tolerance).
                   Expression should be eval-able string using df columns.
                   e.g. ("VolCheck", "vol - (buy_vol + sell_vol)", 0.05)
                   Expression should return a Series (diff).
                   We check if abs(diff) / val > tolerance.

        Current implementation is simplified:
        We expect the caller to provide specific check logic or we hardcode common patterns here.
        Using `eval` on user strings is risky, so we implement specific named checks.
        """
        issues = []
        if df is None or df.empty:
            return issues

        for name, expr, tolerance in rules:
            try:
                _validate_expr(expr)
                # CC-06: Implement actual cross-validation logic
                # Calculate difference based on expression
                diff = df.eval(expr)

                # Check absolute difference against tolerance
                # Using fillna(0) to handle potential NaNs in calculation safely
                failures = diff.abs().fillna(0) > tolerance  # type: ignore[union-attr]
                fail_count = int(failures.sum())  # type: ignore[untyped]
                if fail_count > 0:
                    sample = df[failures].index[0]
                    issues.append(
                        f"Rule '{name}' failed: {fail_count} rows exceed tolerance {tolerance} (e.g. index {sample})",
                    )
            except (KeyError, ValueError, TypeError) as e:
                raise type(e)(f"Data quality check failed due to schema/type error: {e}") from e
            except Exception as e:
                issues.append(f"Rule '{name}' execution error: {e!s}")
                logger.warning("Data quality check warning: %s", e, exc_info=True)

        return issues
