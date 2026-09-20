"""Unit tests for SC-01: PolarsBaseStrategy 基类统一排除 ST / 风险警示股。

覆盖 ``_apply_exclude_st`` 与 pandas 助手 ``filter_exclude_st`` 的行为：
- 默认排除（is_st 列存在时过滤 ST 行）；
- 排除数量经 D3-4 warnings 通道透传（Message(key, params)）；
- ``ctx["exclude_st"]=False`` 运行时关闭；
- 无 is_st 列（测试构造/旧数据源）时跳过，保持向后兼容；
- NULL is_st（stock_basic.name 可空）按「非 ST」处理：不排除且不计数。
"""
# pyright: reportArgumentType=false, reportUnknownVariableType=false

import pandas as pd
import polars as pl
import pytest

from core.i18n import Message
from strategies.polars_base import PolarsBaseStrategy
from strategies.utils import filter_exclude_st

pytestmark = pytest.mark.unit


def _make_lf(st_flags: list[bool | None] | None = None) -> pl.LazyFrame:
    """构造含 is_st 列的 LazyFrame；st_flags=None 时不含该列（模拟旧数据源）。"""
    rows = [
        {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0},
        {"ts_code": "000002.SZ", "name": "万科A", "close": 20.0},
        {"ts_code": "600001.SS", "name": "*ST 某某", "close": 5.0},
    ]
    if st_flags is not None:
        for row, flag in zip(rows, st_flags, strict=True):
            row["is_st"] = flag
    return pl.DataFrame(rows).lazy()


class _StubStrategy(PolarsBaseStrategy):
    key = "test_exclude_st"

    def __init__(self):
        super().__init__("test_exclude_st", "desc")

    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        return lf


class TestApplyExcludeSt:
    def test_default_excludes_st_rows(self):
        """SC-01: 缺省 exclude_st=True，is_st 行被过滤。"""
        s = _StubStrategy()
        lf = _make_lf(st_flags=[False, False, True])
        result = s._apply_exclude_st(lf, {}).collect()
        assert len(result) == 2
        assert "600001.SS" not in result["ts_code"].to_list()

    def test_warnings_channel_reports_excluded_count(self):
        """SC-01 建议 3: 排除数量经 warnings 通道透传（Message(key, params)）。"""
        s = _StubStrategy()
        lf = _make_lf(st_flags=[False, True, True])
        ctx: dict = {}
        result = s._apply_exclude_st(lf, ctx).collect()
        assert len(result) == 1
        warnings = ctx.get("warnings", [])
        assert len(warnings) == 1
        msg = warnings[0]
        assert isinstance(msg, Message)
        assert msg.key == "strategy_excluded_st"
        assert msg.params == {"count": 2}

    def test_no_warning_when_nothing_excluded(self):
        """无 ST 行时不产生警告，避免空噪音。"""
        s = _StubStrategy()
        lf = _make_lf(st_flags=[False, False, False])
        ctx: dict = {}
        s._apply_exclude_st(lf, ctx)
        assert ctx.get("warnings") is None

    def test_context_override_disables(self):
        """ctx["exclude_st"]=False 运行时覆盖类属性，跳过过滤。"""
        s = _StubStrategy()
        lf = _make_lf(st_flags=[False, False, True])
        result = s._apply_exclude_st(lf, {"exclude_st": False}).collect()
        assert len(result) == 3

    def test_context_override_enables_when_class_attr_false(self):
        """类属性关闭但 ctx 显式开启时仍过滤（UI 开关为运行时真值源）。"""
        s = _StubStrategy()
        s.exclude_st = False
        lf = _make_lf(st_flags=[False, False, True])
        result = s._apply_exclude_st(lf, {"exclude_st": True}).collect()
        assert len(result) == 2

    def test_missing_is_st_column_returns_unchanged(self):
        """无 is_st 列（旧数据源/测试构造）时跳过，保持向后兼容。"""
        s = _StubStrategy()
        lf = _make_lf()
        result = s._apply_exclude_st(lf, {}).collect()
        assert len(result) == 3

    def test_class_attr_default_true(self):
        """SC-01 建议 2: 基类缺省排除（专门研究 ST 的策略可覆盖为 False）。"""
        assert _StubStrategy().exclude_st is True

    def test_null_is_st_not_excluded_not_counted(self):
        """NULL is_st（stock_basic.name 可空）按「非 ST」处理：不排除且不计数。

        ``fill_null(False)`` 防护：避免 NULL 行被 ``~pl.col()`` 过滤掉造成静默漏股。
        """
        s = _StubStrategy()
        lf = _make_lf(st_flags=[False, None, True])
        ctx: dict = {}
        result = s._apply_exclude_st(lf, ctx).collect()
        assert len(result) == 2
        assert "000002.SZ" in result["ts_code"].to_list()
        warnings = ctx.get("warnings", [])
        assert len(warnings) == 1
        assert warnings[0].params == {"count": 1}


def _make_pandas_df(st_flags: list[bool | None] | None = None) -> pd.DataFrame:
    """构造含 is_st 列的 pandas DataFrame；st_flags=None 时不含该列。"""
    rows = {
        "ts_code": ["000001.SZ", "000002.SZ", "600001.SS"],
        "name": ["平安银行", "万科A", "*ST 某某"],
        "close": [10.0, 20.0, 5.0],
    }
    df = pd.DataFrame(rows)
    if st_flags is not None:
        df["is_st"] = st_flags
    return df


class TestFilterExcludeStPandas:
    """SC-01: strategies.utils.filter_exclude_st（pandas 助手，Oversold 等非 Polars 路径）。"""

    def test_default_excludes_st_rows(self):
        df = _make_pandas_df(st_flags=[False, False, True])
        result, excluded = filter_exclude_st(df, {})
        assert excluded == 1
        assert "600001.SS" not in result["ts_code"].tolist()
        assert len(result) == 2

    def test_context_override_disables(self):
        df = _make_pandas_df(st_flags=[False, False, True])
        result, excluded = filter_exclude_st(df, {"exclude_st": False})
        assert excluded == 0
        assert len(result) == 3

    def test_context_enable_overrides_class_default_false(self):
        df = _make_pandas_df(st_flags=[False, False, True])
        result, excluded = filter_exclude_st(df, {"exclude_st": True}, default_exclude=False)
        assert excluded == 1
        assert len(result) == 2

    def test_missing_is_st_column_unchanged(self):
        df = _make_pandas_df()
        result, excluded = filter_exclude_st(df, {})
        assert excluded == 0
        assert len(result) == 3

    def test_null_is_st_treated_as_non_st(self):
        df = _make_pandas_df(st_flags=[False, None, True])
        result, excluded = filter_exclude_st(df, {})
        assert excluded == 1
        assert len(result) == 2
        assert "000002.SZ" in result["ts_code"].tolist()

    def test_no_exclusion_returns_same_object(self):
        df = _make_pandas_df(st_flags=[False, False, False])
        result, excluded = filter_exclude_st(df, {})
        assert excluded == 0
        assert result is df  # 无排除时原样返回，避免无谓拷贝
