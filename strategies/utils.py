import datetime
import math
from typing import Any, TypedDict
from collections.abc import Callable

import pandas as pd

from core.errors import StrategyParamError
from core.i18n import Message
from data.constants import get_column_unit


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


# 金额单位换算基准（统一换算到人民币元）。取值与 data/constants.py 列单位元数据一致。
_UNIT_SCALE_TO_YUAN = {"yuan": 1.0, "wan_cny": 1e4, "million_cny": 1e6, "yi_cny": 1e8}


def threshold_in_data_unit(df, column: str, declared_unit: str, value: float, value_unit: str) -> float:
    """把 UI 阈值 value（单位 value_unit）换算到 df[column] 的实际单位，用于金额类 gating/过滤。

    - 实际单位优先读取 df 上挂载的列单位元数据（data.constants.attach_*_column_units）。
    - df 不带 attrs（如 _filter_logic 内的 polars LazyFrame）时回退到 declared_unit（已知契约）。
    - 单位未知时抛 StrategyParamError：金额比较绝不允许按假设继续，由调用方拒绝 gating/过滤。

    例：value=50（亿，value_unit='yi_cny'），data 为 million_cny → 返回 50*1e8/1e6=5000（百万）。
    """
    data_unit = declared_unit
    if isinstance(getattr(df, "attrs", None), dict):
        # 单位元数据嵌套在 df.attrs["column_units"][column]（pandas DataFrame）。
        # polars LazyFrame 无 .attrs，回退到 declared_unit（已知契约）。
        data_unit = get_column_unit(df, column, declared_unit)

    if data_unit not in _UNIT_SCALE_TO_YUAN or value_unit not in _UNIT_SCALE_TO_YUAN:
        raise StrategyParamError(
            Message(
                "strategy_unit_unknown", {"column": column, "data_unit": str(data_unit), "value_unit": str(value_unit)}
            )
        )
    return value * _UNIT_SCALE_TO_YUAN[value_unit] / _UNIT_SCALE_TO_YUAN[data_unit]


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
