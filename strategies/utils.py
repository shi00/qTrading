import datetime
import math
from typing import Any, TypedDict
from collections.abc import Callable

import pandas as pd


def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        fval = float(val)
        return default if math.isnan(fval) else fval
    except (ValueError, TypeError):
        return default


def fmt_val(val, fmt_spec=".2f", suffix=""):
    if val is None:
        return "N/A"
    try:
        fval = float(val)
        if math.isnan(fval):
            return "N/A"
        if fval == int(fval) and not suffix:
            return str(int(fval))
        return f"{fval:{fmt_spec}}{suffix}"
    except (ValueError, TypeError):
        return "N/A"


class StrategyContext(TypedDict, total=False):
    screening_data: pd.DataFrame | None
    fundamental_screening_data: pd.DataFrame | None
    data: pd.DataFrame | None
    data_processor: Any
    params: dict[str, Any]
    on_progress: Callable[[int, int, str], None]
    on_result: Callable
    on_stream_result: Callable
    on_stream_start: Callable
    northbound_data: pd.DataFrame | None
    northbound_flow_data: pd.DataFrame | None
    moneyflow_data: pd.DataFrame | None
    top_list: pd.DataFrame | None
    block_trade: pd.DataFrame | None
    trade_date: datetime.date | datetime.datetime | str
    is_backtest: bool
    _task_id: str
    _disable_ai: bool
    _dependency_status: dict[str, Any]
    _diagnostics: dict[str, Any]
