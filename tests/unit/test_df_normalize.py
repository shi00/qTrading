# pyright: reportAttributeAccessIssue=false
# 本文件含 Decimal/None 混合对象列测试，pyright 无法静态推断 pandas object 列 dtype。

"""test_df_normalize — Decimal 归一化工具单测（DAT-10/11）。

覆盖 ``normalize_decimal_columns`` 的正向转换、误伤防护与边界场景。
"""

from decimal import Decimal

import pandas as pd
import pytest

from utils.df_normalize import normalize_decimal_columns

pytestmark = pytest.mark.unit


class TestNormalizeDecimalColumns:
    def test_decimal_column_converted_to_float64(self):
        df = pd.DataFrame({"price": [Decimal("10.5"), Decimal("20.25")]})
        result = normalize_decimal_columns(df)
        assert result["price"].dtype == "float64"
        assert result["price"].iloc[0] == 10.5
        assert result["price"].iloc[1] == 20.25

    def test_decimal_with_none_converted_to_float64(self):
        df = pd.DataFrame({"price": [Decimal("1.5"), None, Decimal("3.5")]})
        result = normalize_decimal_columns(df)
        assert result["price"].dtype == "float64"
        assert result["price"].iloc[0] == 1.5
        assert result["price"].iloc[1] != result["price"].iloc[1]  # NaN
        assert result["price"].iloc[2] == 3.5

    def test_decimal_nan_becomes_nan(self):
        df = pd.DataFrame({"col": [Decimal("NaN"), Decimal("1.0")]})
        result = normalize_decimal_columns(df)
        assert result["col"].dtype == "float64"
        assert result["col"].iloc[0] != result["col"].iloc[0]

    def test_string_column_untouched(self):
        df = pd.DataFrame({"code": ["000001", "000002"]})
        result = normalize_decimal_columns(df)
        assert pd.api.types.is_string_dtype(result["code"])
        assert result["code"].iloc[0] == "000001"

    def test_date_column_untouched(self):
        df = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-01", "2024-01-02"])})
        result = normalize_decimal_columns(df)
        assert pd.api.types.is_datetime64_any_dtype(result["trade_date"])

    def test_int_column_untouched(self):
        df = pd.DataFrame({"n": [1, 2, 3]})
        result = normalize_decimal_columns(df)
        assert result["n"].dtype == "int64"

    def test_float_column_untouched(self):
        df = pd.DataFrame({"f": [1.5, 2.5]})
        result = normalize_decimal_columns(df)
        assert result["f"].dtype == "float64"

    def test_all_none_column_stays_object(self):
        """全 None 列 infer_dtype == 'empty'，不转换（下游 Polars 推断 Null dtype，无数据边界）。"""
        df = pd.DataFrame({"col": [None, None]})
        result = normalize_decimal_columns(df)
        assert result["col"].dtype == object

    def test_mixed_str_decimal_column_untouched(self):
        """str + Decimal 混合列 infer_dtype != 'decimal'，不转换（非全数值列）。"""
        df = pd.DataFrame({"col": ["1.5", Decimal("2.5")]})
        result = normalize_decimal_columns(df)
        assert result["col"].dtype == object

    def test_mixed_int_decimal_column_untouched(self):
        """int + Decimal 混合列（如 code 与 price 同列）保持 object，不误转。"""
        df = pd.DataFrame({"col": [Decimal("1.5"), 2]})
        result = normalize_decimal_columns(df)
        assert result["col"].dtype == object

    def test_empty_dataframe_ok(self):
        result = normalize_decimal_columns(pd.DataFrame())
        assert result.empty

    def test_mixed_columns_only_decimal_converted(self):
        df = pd.DataFrame(
            {
                "code": ["000001", "000002"],
                "price": [Decimal("10.5"), Decimal("20.25")],
                "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            }
        )
        result = normalize_decimal_columns(df)
        assert pd.api.types.is_string_dtype(result["code"])
        assert result["price"].dtype == "float64"
        assert pd.api.types.is_datetime64_any_dtype(result["date"])
