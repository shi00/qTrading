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
    screening_data: pd.DataFrame
    data: pd.DataFrame
    data_processor: Any
    params: dict[str, Any]
    on_progress: Callable[[int, int, str], None]
    on_result: Callable
    on_stream_result: Callable
    on_stream_start: Callable
    northbound_data: pd.DataFrame
    moneyflow_data: pd.DataFrame
    top_list: pd.DataFrame
    block_trade: pd.DataFrame
    trade_date: datetime.date | datetime.datetime | str
    _task_id: str
    _dependency_status: dict[str, Any]
