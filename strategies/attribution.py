"""UX-04 选股结果结构化归因 —— 数据契约 (策略层内部, 不依赖 ui/services/app).

模型说明：
- ``FilterCondition``  单个筛选条件的展示化描述。仅在筛选幸存行上生成, 故「是否通过」恒真,
  用运算符表达 (gt/lt/between/geq/leq), 不设冗余 is_passed 死字段 (二次检视 M5)。
- ``RankAttribution``  排序归因。口径恒为「按 strategy 输出 (AI 截断前) 候选池内,
  按 rank_field 排序」, 由 base 统一计算 (一次检视 Major#1 修复: total 不因 AI 截断失真)。
- ``FilterAttribution`` 整只股票的归因, 以 JSON 序列化进结果 DataFrame 的 ``_filter_attribution`` 列。

归因只携带 ``column`` (列名), 展示文案由 View 经 ``get_column_alias`` 翻译为 i18n key,
strategy 层不感知 locale (§3.2 MVVM)。
"""

from __future__ import annotations

import json

from dataclasses import asdict, dataclass, field

# 结果 DataFrame 中承载归因的列名。
ATTRIBUTION_COLUMN = "_filter_attribution"


def fnum(x) -> float | None:
    """把数值/None/NaN/inf 归一为有限 float 或 None (二次检视 m2: NaN 归一, 防 ScreenerRow 相等性击穿)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    import math

    if math.isnan(f) or math.isinf(f):
        return None
    return f


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


@dataclass(frozen=True)
class RankAttribution:
    """排序归因 (候选池内按某字段排序)."""

    field: str
    ascending: bool = False
    value: float | None = None
    position: int | None = None  # 1-based
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


def attribution_from_json(raw: str | dict | None) -> FilterAttribution | None:
    """反序列化归因; 接受 JSON 字符串或已解析 dict, 非法/缺失时返回 None (View 忽略, 二次检视 m4 守卫).

    VM 层 ``_decode_cell`` 已将 ``_filter_attribution`` 列解析为 dict,
    而导出回归测试可能直供 JSON 串, 故两者皆接受 (View 渲染路径统一入口)。
    """
    if not raw:
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    conditions = tuple(_condition_from_dict(c) for c in data.get("conditions", ()))
    rank = _rank_from_dict(data.get("rank"))
    return FilterAttribution(conditions=conditions, rank=rank)


def _condition_from_dict(data: dict) -> FilterCondition:
    try:
        t: object = data["threshold"]
        # between 阈值在 asdict→JSON→loads 后为 list, 须还原为 tuple 以通过 __post_init__ 校验
        # (二次检视 a3: 反序列化击穿, 原先降级为 gt 0.0 假条件)。
        if isinstance(t, list) and len(t) == 2:
            threshold: float | tuple[float, float] = (float(t[0]), float(t[1]))
        elif isinstance(t, (int, float)):
            threshold = float(t)
        else:
            raise ValueError("invalid threshold")
        return FilterCondition(
            column=data["column"],
            operator=data["operator"],
            threshold=threshold,
            actual=data.get("actual"),
        )
    except (KeyError, TypeError, ValueError):
        return FilterCondition(column=data.get("column", ""), operator="gt", threshold=0.0)


def _rank_from_dict(data) -> RankAttribution | None:
    if not isinstance(data, dict):
        return None
    return RankAttribution(
        field=data.get("field", ""),
        ascending=bool(data.get("ascending", False)),
        value=data.get("value"),
        position=data.get("position"),
        total=data.get("total"),
    )
