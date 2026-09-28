"""UI 层数值单元格的「列 → 原始单位 → 展示单位」单一数据源（CRITICAL-02）。

Tushare 原始数据单位（真值，禁改）：
- ``amount``（成交额）：千元
- ``vol`` / ``volume``（成交量）：手
- ``total_mv`` / ``circ_mv``（市值）：万元

展示口径分两步（先归一到基准单位，再按 locale 量级换算）：
1. 原始值 → 基准单位：货币类 → 元，数量类 → 手；
2. 基准单位 → 展示单位：按当前 locale 的展示量级取整——
   - 货币 zh_CN：「亿」= 10^8 元（``unit_yi``）
   - 货币 en_US：「B」= 10^9 元（``unit_yi``；英文本无「亿」，项目用十亿 B 表达，故量级取 10^9）
   - 数量：「万手」= 10^4 手（``unit_wanshou``）

结果表（``ui/views/screener_view.py``）与股票详情弹窗
（``ui/components/stock_detail_dialog.py``）共用本模块的
``format_metadata_cell`` / ``format_mv`` / ``format_vol`` / ``format_amount``，
杜绝两套口径（R20 已知金额/数量列统一换算入口）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ui.i18n import I18n

# Tushare 原始单位 → zh（亿）口径换算因子（对外保留的公开常量，供外部引用/校验）。
TUSHARE_MV_UNIT = 10_000  # 万元 → 亿元（zh 口径）
TUSHARE_AMOUNT_UNIT = 100_000  # 千元 → 亿元（zh 口径）
TUSHARE_VOL_UNIT = 10_000  # 手 → 万手

# 展示单位的绝对量级（按 locale）：zh「亿」= 1e8，en「B」= 1e9。
_CNY_YI = 100_000_000.0
_CNY_SCALE_BY_LOCALE: Mapping[str, float] = MappingProxyType(
    {
        "zh_CN": _CNY_YI,
        "en_US": 1_000_000_000.0,
    }
)
_LOT_SCALE = 10_000.0  # 手 → 万手


def is_valid_number(val: Any) -> bool:
    """判断 val 是否为有效（非 NaN）数值。"""
    if val is None:
        return False
    if isinstance(val, float):
        return not math.isnan(val)
    if isinstance(val, int):
        return True
    try:
        return not math.isnan(float(val))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class ColumnUnitSpec:
    """「列 → 原始单位 → 展示单位」元数据（单一数据源）。

    Attributes:
        raw_unit: Tushare 原始单位标识。
        family: 展示单位族（``"cny"`` 货币 / ``"lot"`` 数量）。
        to_base: 原始值 → 基准单位（元 / 手）的换算因子。
        decimals: 展示小数位。
        unit_i18n_key: 展示单位文案的 i18n key。
    """

    raw_unit: str
    family: str
    to_base: float
    decimals: int
    unit_i18n_key: str

    def format(self, val: Any) -> str:
        """按元数据换算并格式化原始值；缺失/非数值返回 "-"（R21 不伪装）。"""
        if not is_valid_number(val):
            return "-"
        try:
            base = float(val) * self.to_base
        except (ValueError, TypeError):
            return "-"
        return f"{base / display_scale(self.family):.{self.decimals}f}{I18n.get(self.unit_i18n_key)}"


def display_scale(family: str) -> float:
    """返回当前 locale 下该单位族的展示量级（未知 locale 回退 zh 口径）。"""
    if family == "cny":
        return _CNY_SCALE_BY_LOCALE.get(I18n.current_locale(), _CNY_YI)
    return _LOT_SCALE


COLUMN_UNIT_SPECS: Mapping[str, ColumnUnitSpec] = MappingProxyType(
    {
        "amount": ColumnUnitSpec("thousand_cny", "cny", 1_000.0, 2, "unit_yi"),
        "total_mv": ColumnUnitSpec("wan_cny", "cny", 10_000.0, 1, "unit_yi"),
        "circ_mv": ColumnUnitSpec("wan_cny", "cny", 10_000.0, 1, "unit_yi"),
        "vol": ColumnUnitSpec("lot", "lot", 1.0, 1, "unit_wanshou"),
        "volume": ColumnUnitSpec("lot", "lot", 1.0, 1, "unit_wanshou"),
    }
)

# 布尔列（True/False → 是/否）。值本身为 bool 的列无需登记也会被识别。
BOOL_COLS: frozenset[str] = frozenset({"is_tradable"})

# 百分比列（值本身即百分数，展示追加 "%"）；SIGNED 子集为涨跌/超额类，带正号。
SIGNED_PCT_COLS: frozenset[str] = frozenset({"t1_pct", "t5_pct", "alpha", "pct_chg"})
PCT_COLS: frozenset[str] = SIGNED_PCT_COLS | frozenset(
    {
        "turnover_rate",
        "dv_ttm",
        "roe",
        "grossprofit_margin",
        "debt_to_assets",
        "or_yoy",
        "netprofit_yoy",
    }
)


def format_mv(val: Any) -> str:
    """格式化市值（原始万元）→ 按 locale 展示「亿/B」。"""
    return COLUMN_UNIT_SPECS["total_mv"].format(val)


def format_amount(val: Any) -> str:
    """格式化成交额（原始千元）→ 按 locale 展示「亿/B」。"""
    return COLUMN_UNIT_SPECS["amount"].format(val)


def format_vol(val: Any) -> str:
    """格式化成交量（原始手）→ 展示「万手」。"""
    return COLUMN_UNIT_SPECS["vol"].format(val)


def format_bool(val: Any) -> str:
    """布尔值 → 是/否（接受 bool 与 "true"/"1"/"y" 等字符串形态）。

    缺失（``None`` / ``NaN`` / 空串）返回 "-"（R21：缺失不得伪装为业务合法值「否」）。
    """
    if val is None:
        return "-"
    if isinstance(val, str):
        if not val.strip():
            return "-"
        truthy = val.strip().lower() in {"true", "1", "yes", "y", "t"}
        return I18n.get("common_yes") if truthy else I18n.get("common_no")
    if not is_valid_number(val):
        return "-"
    return I18n.get("common_yes") if bool(val) else I18n.get("common_no")


def format_pct(col: str, val: Any) -> str:
    """百分比值 → 2 位小数 + "%"；涨跌/超额列带正号。"""
    if not is_valid_number(val):
        return "-"
    try:
        v = float(val)
    except (ValueError, TypeError):
        return "-"
    sign = "+" if v > 0 and col in SIGNED_PCT_COLS else ""
    return f"{sign}{v:.2f}%"


def format_metadata_cell(col: str, val: Any) -> str | None:
    """按「列 → 原始单位 → 展示单位」元数据格式化单元格：布尔 / 单位换算 / 百分比。

    返回 ``None`` 表示该列无元数据规则，调用方应走通用数值/字符串回退。
    """
    if col in BOOL_COLS or isinstance(val, bool):
        return format_bool(val)
    spec = COLUMN_UNIT_SPECS.get(col)
    if spec is not None:
        return spec.format(val)
    if col in PCT_COLS:
        return format_pct(col, val)
    return None


def column_header_unit(col: str) -> str | None:
    """返回列标题应标注的单位文案（无单位列返回 ``None``）。"""
    spec = COLUMN_UNIT_SPECS.get(col)
    if spec is None:
        return None
    return I18n.get(spec.unit_i18n_key)
