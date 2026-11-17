"""UX-04 选股结果结构化归因 —— 数据契约 (策略层内部, 不依赖 ui/services/app).

模型说明：
- ``FilterCondition``  单个筛选条件的展示化描述。仅在筛选幸存行上生成, 故「是否通过」恒真,
  用运算符表达 (gt/lt/between/geq/leq), 不设冗余 is_passed 死字段 (二次检视 M5)。
- ``RankAttribution``  排序归因。口径恒为「strategy 输出 (AI 截断前) 候选池内」:
  ``position`` = 该行在候选池中的序位 (由策略自身排序决定, base 不二次重排, 检视 MINOR-01),
  ``field`` / ``ascending`` 声明主排序依据, ``total`` 由 base 统一计算
  (一次检视 Major#1 修复: total 不因 AI 截断失真)。
- ``FilterAttribution`` 整只股票的归因, 以 JSON 序列化进结果 DataFrame 的 ``_filter_attribution`` 列。

归因只携带 ``column`` (列名), 展示文案由 View 经 ``get_column_alias`` 翻译为 i18n key,
strategy 层不感知 locale (§3.2 MVVM)。
"""

from __future__ import annotations

import json
import math
import typing

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

import polars as pl

# 结果 DataFrame 中承载归因的列名。
ATTRIBUTION_COLUMN = "_filter_attribution"

# 归因条件允许的运算符 (与 FilterCondition.__post_init__ 校验集合一致)。
_OPERATORS = frozenset({"gt", "geq", "lt", "leq", "between"})


def fnum(x) -> float | None:
    """把数值/None/NaN/inf 归一为有限 float 或 None (二次检视 m2: NaN 归一, 防 ScreenerRow 相等性击穿)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _finite_float(value: object) -> float | None:
    """JSON 数值严格归一：仅接受 int/float（含 0）的有限值，bool/字符串/NaN/inf → None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def _optional_positive_int(value: object) -> tuple[bool, int | None]:
    """可选正整数校验：None → (True, None)；非法（含 bool/0/负数/字符串）→ (False, None)。"""
    if value is None:
        return True, None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return False, None
    return True, value


@dataclass(frozen=True)
class FilterCondition:
    """单个筛选条件：实际值 vs 阈值 (数据单位)。

    ``threshold`` 为数值时为单阈值比较; 为二元组时为 between 区间。
    金额/数量类阈值必须经 ``threshold_in_data_unit`` 换算到数据单位 (R20)。
    """

    column: str
    operator: str  # "gt" | "geq" | "lt" | "leq" | "between"
    threshold: float | tuple[float, float]
    actual: float | None = None  # NaN 统一归一为 None (二次检视 m2)

    def __post_init__(self) -> None:
        if self.operator not in {"gt", "geq", "lt", "leq", "between"}:
            raise ValueError(f"unsupported operator: {self.operator}")
        if self.operator == "between" and not isinstance(self.threshold, tuple):
            raise ValueError("between 运算符必须提供 (lo, hi) 二元组阈值")
        if self.operator != "between" and isinstance(self.threshold, tuple):
            raise ValueError(f"{self.operator} 运算符必须提供单值阈值")


def condition_to_expr(cond: FilterCondition) -> pl.Expr:
    """FilterCondition → Polars 表达式（SC-07 空集回溯复用）。

    空集归因（PolarsBaseStrategy._diagnose_empty）用同一批结构化条件
    重新统计通过数，找出把候选池砍到 0 的条件，避免条件声明二次漂移。
    """
    col = pl.col(cond.column)
    if cond.operator == "gt":
        return col > cond.threshold  # type: ignore[operator]
    if cond.operator == "geq":
        return col >= cond.threshold  # type: ignore[operator]
    if cond.operator == "lt":
        return col < cond.threshold  # type: ignore[operator]
    if cond.operator == "leq":
        return col <= cond.threshold  # type: ignore[operator]
    # between: (lo, hi) 二元组
    lo, hi = cond.threshold  # type: ignore[misc]
    return col.is_between(lo, hi)


@dataclass(frozen=True)
class RankAttribution:
    """排序归因 (候选池内序位 + 主排序依据)."""

    field: str
    ascending: bool = False
    value: float | None = None
    position: int | None = None  # 1-based, 候选池 (策略输出序) 序位
    total: int | None = None


@dataclass(frozen=True)
class FilterAttribution:
    conditions: tuple[FilterCondition, ...] = field(default_factory=tuple)
    rank: RankAttribution | None = None


def attribution_to_json(attr: FilterAttribution | None) -> str | None:
    """序列化归因为紧凑 JSON; attr 为 None 时返回 None (行不生成归因)."""
    if attr is None:
        return None
    return json.dumps(asdict(attr), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def attribution_from_json(raw: str | Mapping[str, typing.Any] | None) -> FilterAttribution | None:
    """反序列化归因; 接受 JSON 字符串或已解析 Mapping, 非法/缺失时返回 None (View 忽略, 二次检视 m4 守卫).

    VM 层 ``_decode_cell`` 已将 ``_filter_attribution`` 列解析为 dict 并经 ``_deep_freeze``
    冻结为只读 Mapping (D16-008), 而导出回归测试可能直供 JSON 串, 故 JSON 串与 Mapping
    皆接受 (View 渲染路径统一入口)。

    D11-002: 严格校验，非法条件/排名一律剔除或整体拒绝，**不得合成 ``gt 0.0`` 等假条件**
    （否则内部坏 JSON 会让 View 渲染「> 0」并附带绿色通过标，违反 R21 缺失值伪装约定）。
    """
    if not raw:
        return None
    if isinstance(raw, Mapping):
        data: object = raw
    else:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, Mapping):
        return None
    raw_conditions = data.get("conditions", ())
    # 条件容器形态非法时整体拒绝（None/标量/字符串不可迭代，逐个剔除会掩盖容器级损坏）。
    if not isinstance(raw_conditions, (list, tuple)):
        return None
    conditions = tuple(cond for cond in map(_condition_from_dict, raw_conditions) if cond is not None)
    rank = _rank_from_dict(data.get("rank"))
    return FilterAttribution(conditions=conditions, rank=rank)


def _condition_from_dict(data: object) -> FilterCondition | None:
    """严格解析单条条件；任何字段缺失/类型错误/非有限值 → None（剔除，不造阈值）。"""
    if not isinstance(data, Mapping):
        return None
    column = data.get("column")
    operator = data.get("operator")
    if not isinstance(column, str) or not column:
        return None
    if not isinstance(operator, str) or operator not in _OPERATORS:
        return None

    raw_threshold = data.get("threshold")
    if operator == "between":
        # between 阈值在 asdict→JSON→loads 后为 list; 经 _deep_freeze 冻结后为 tuple,
        # 两者均须还原为二元 tuple 以通过 __post_init__ 校验 (D16-008: 冻结不得把
        # tuple 阈值误判为非法后回退成假条件)。长度必须恰为 2 且上下界有限、有序。
        if not isinstance(raw_threshold, (list, tuple)) or len(raw_threshold) != 2:
            return None
        lo = _finite_float(raw_threshold[0])
        hi = _finite_float(raw_threshold[1])
        if lo is None or hi is None or lo > hi:
            return None
        threshold: float | tuple[float, float] = (lo, hi)
    else:
        if isinstance(raw_threshold, (list, tuple)):
            return None
        single = _finite_float(raw_threshold)
        if single is None:
            return None
        threshold = single

    raw_actual = data.get("actual")
    actual = None if raw_actual is None else _finite_float(raw_actual)
    return FilterCondition(column=column, operator=operator, threshold=threshold, actual=actual)


def _rank_from_dict(data: object) -> RankAttribution | None:
    """严格解析排名归因；字段类型/取值非法时返回 None（不伪造序位）。"""
    if not isinstance(data, Mapping):
        return None
    field = data.get("field")
    ascending = data.get("ascending", False)
    if not isinstance(field, str) or not field or not isinstance(ascending, bool):
        return None

    value: float | None = None
    raw_value = data.get("value")
    if raw_value is not None:
        value = _finite_float(raw_value)
        if value is None:
            return None

    valid_position, position = _optional_positive_int(data.get("position"))
    valid_total, total = _optional_positive_int(data.get("total"))
    if not valid_position or not valid_total:
        return None
    if position is not None and total is not None and position > total:
        return None
    return RankAttribution(field=field, ascending=ascending, value=value, position=position, total=total)
