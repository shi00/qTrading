"""CRITICAL-02: UI 层「列 → 原始单位 → 展示单位」元数据单一数据源测试。

覆盖 DoD（结果表与详情框单位口径一致，杜绝 1 万倍偏差）：
- zh: ``amount``（千元）5_000_000 → 结果表与详情框均 "50.00亿"；
- en: 同值 → "5.00B"（locale 量级差异：zh「亿」=1e8，en「B」=1e9）；
- ``total_mv``（万元）→ 亿；``vol``（手）→ 万手；
- ``is_tradable`` → 是/否；百分列带 "%"；列标题带单位。

边界：None / NaN / 0 / 负数 / 极大值 / 未知列 / 非数值类型。
"""

import pytest

from ui.components import unit_format
from ui.components.stock_detail_dialog import format_amount as dialog_format_amount
from ui.components.unit_format import (
    COLUMN_UNIT_SPECS,
    column_header_unit,
    format_amount,
    format_bool,
    format_metadata_cell,
    format_mv,
    format_pct,
    format_vol,
    is_valid_number,
)
from ui.i18n import I18n

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_locale_to_zh():
    """每个用例前归位 zh_CN，避免 locale 泄漏导致 flaky。"""
    I18n.initialize()
    I18n.set_locale("zh_CN")
    yield
    I18n.set_locale("zh_CN")


class TestDoDUnitConsistency:
    """DoD 四条：结果表与详情框共用同一换算，zh/en 量级正确。"""

    def test_amount_zh_result_table_and_detail_dialog_both_yi(self):
        from ui.views.screener_view import _format_cell_value

        # amount 原始单位千元：5_000_000 千元 = 5e9 元 = 50 亿
        assert _format_cell_value("amount", 5_000_000) == "50.00亿"
        assert dialog_format_amount(5_000_000) == "50.00亿"

    def test_amount_en_uses_billion_scale(self):
        I18n.set_locale("en_US")
        # 英文本无「亿」，项目用十亿 B（1e9）表达：5e9 元 = 5.00B
        assert format_amount(5_000_000) == "5.00B"

    def test_total_mv_wan_to_yi(self):
        # total_mv 原始单位万元：500_000 万元 = 5e9 元 = 50.0 亿
        assert format_mv(500_000) == "50.0亿"

    def test_vol_lot_to_wanshou(self):
        # vol 原始单位手：50_000 手 = 5.0 万手
        assert format_vol(50_000) == "5.0万手"

    def test_bool_maps_to_yes_no(self):
        assert format_bool(True) == "是"
        assert format_bool(False) == "否"

    def test_bool_column_via_metadata(self):
        assert format_metadata_cell("is_tradable", True) == "是"
        assert format_metadata_cell("is_tradable", False) == "否"

    def test_bool_missing_not_masqueraded(self):
        # R21: 缺失不得伪装为「否」
        assert format_bool(None) == "-"
        assert format_bool(float("nan")) == "-"
        assert format_metadata_cell("is_tradable", None) == "-"

    def test_bool_string_forms(self):
        assert format_bool("true") == "是"
        assert format_bool("1") == "是"
        assert format_bool("0") == "否"

    def test_pct_column_appends_percent(self):
        assert format_pct("t1_pct", 1.23) == "+1.23%"
        assert format_pct("t5_pct", -1.23) == "-1.23%"
        assert format_pct("t5_pct", 0.0) == "0.00%"


class TestColumnHeaderUnit:
    """列标题单位标注（与单元格换算同源）。"""

    def test_amount_header_unit_zh(self):
        assert column_header_unit("amount") == "亿"

    def test_amount_header_unit_en(self):
        I18n.set_locale("en_US")
        assert column_header_unit("amount") == "B"

    def test_vol_header_unit_zh(self):
        assert column_header_unit("vol") == "万手"
        assert column_header_unit("volume") == "万手"

    def test_unitless_column_returns_none(self):
        assert column_header_unit("close") is None
        assert column_header_unit("ts_code") is None


class TestMetadataCellDispatch:
    """元数据分发：布尔 / 单位 / 百分比 / 无规则列。"""

    def test_unknown_column_returns_none(self):
        assert format_metadata_cell("ts_code", "000001.SZ") is None
        assert format_metadata_cell("name", "平安银行") is None

    def test_bool_detected_by_value_type(self):
        # 未登记进 BOOL_COLS 但值为 bool 时也按是/否渲染
        assert format_metadata_cell("some_flag", True) == "是"

    def test_amount_spec_is_single_source(self):
        # 结果表与详情框共用同一 spec（元数据单一数据源）
        assert COLUMN_UNIT_SPECS["amount"].raw_unit == "thousand_cny"
        assert COLUMN_UNIT_SPECS["vol"].raw_unit == "lot"


class TestBoundaries:
    """边界：缺失 / NaN / 0 / 负数 / 极大值 / 非数值类型（R21 缺失不伪装）。"""

    def test_missing_returns_dash_not_zero(self):
        assert format_amount(None) == "-"
        assert format_amount(float("nan")) == "-"
        assert format_mv(None) == "-"
        assert format_vol(None) == "-"
        assert format_metadata_cell("amount", None) == "-"

    def test_zero_is_valid_value(self):
        assert format_amount(0) == "0.00亿"
        assert format_vol(0) == "0.0万手"

    def test_negative_value_preserved(self):
        assert format_amount(-1_000_000) == "-10.00亿"
        assert format_vol(-5000) == "-0.5万手"

    def test_extreme_value(self):
        assert format_amount(100_000_000) == "1000.00亿"

    def test_non_numeric_type_returns_dash(self):
        assert format_amount("invalid") == "-"
        assert format_vol([1, 2, 3]) == "-"

    def test_display_scale_unknown_locale_falls_back_zh(self):
        I18n.set_locale("en_US")
        assert unit_format.display_scale("cny") == 1_000_000_000.0
        assert unit_format.display_scale("lot") == 10_000.0


class TestIsValidNumber:
    """is_valid_number 覆盖 None / NaN / int / float / 字符串 / 容器。"""

    def test_none_and_nan(self):
        assert is_valid_number(None) is False
        assert is_valid_number(float("nan")) is False

    def test_numbers(self):
        assert is_valid_number(0) is True
        assert is_valid_number(3.14) is True
        assert is_valid_number(-1.5) is True

    def test_non_numbers(self):
        assert is_valid_number("abc") is False
        assert is_valid_number([1, 2]) is False


class TestCritical02ResidualColumns:
    """CRITICAL-02 残留列回归：选股结果集里曾以裸浮点显示的列。

    - ``n_income``（Tushare 原始单位「元」）必须与 amount/total_mv 同源换算为「亿/B」，
      不得再以裸浮点（如 ``1900000000.00``）展示；
    - ``gpm_prev``（上年同期毛利率，值本身即百分数）必须带 "%"。
    """

    def test_n_income_yuan_to_yi_zh(self):
        # n_income 原始单位元：1.9e9 元 = 19 亿
        assert format_metadata_cell("n_income", 1_900_000_000) == "19.00亿"

    def test_n_income_yuan_to_billion_en(self):
        I18n.set_locale("en_US")
        assert format_metadata_cell("n_income", 1_900_000_000) == "1.90B"

    def test_n_income_header_unit(self):
        assert column_header_unit("n_income") == "亿"

    def test_n_income_missing_not_masqueraded(self):
        # R21: 缺失 → "-"，不得伪装为 0
        assert format_metadata_cell("n_income", None) == "-"
        assert format_metadata_cell("n_income", float("nan")) == "-"

    def test_gpm_prev_appends_percent(self):
        assert format_metadata_cell("gpm_prev", 38.0) == "38.00%"
        assert format_metadata_cell("gpm_prev", -5.5) == "-5.50%"

    def test_gpm_prev_missing_returns_dash(self):
        assert format_metadata_cell("gpm_prev", None) == "-"
