import datetime
import math
from typing import Any, TypedDict
from collections.abc import Callable

import pandas as pd

from core.i18n import Message


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
    on_progress: Callable[[int, int, Message], None]
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
    _metadata: dict[str, Any]
    # D3-4: 策略执行期业务警告通道。策略检测到非致命参数/数据问题时 append
    # Message(i18n key + params)，交由 base filter 初始化本次运行通道、VM 透传、
    # View 在结果区上方渲染。符合 §3.2 策略只产出 i18n key 约束。
    warnings: list[Message]
