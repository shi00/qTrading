"""
Field Mapping Tests

Tests that ensure field mappings are intentional and necessary.
Database columns should match Tushare API fields directly where possible.

Run: pytest tests/field_alignment/test_field_mapping.py -v
"""

import inspect

from data.external.tushare_client import TushareClient
from data.persistence.models import (
    BlockTrade,
    LimitList,
    SuspendD,
    TopList,
)
import pytest


pytestmark = pytest.mark.unit


class TestNoUnnecessaryFieldMappings:
    """Test that there are no unnecessary field mappings in _COLUMN_RENAMES."""

    def test_no_stock_data_mappings(self):
        unnecessary_mappings = {
            "top_list",
            "block_trade",
            "limit_list",
            "suspend_d",
        }
        actual_mappings = set(TushareClient._COLUMN_RENAMES.keys())
        unwanted = actual_mappings & unnecessary_mappings
        assert not unwanted, (
            f"Unnecessary field mappings found: {unwanted}. Database columns should match Tushare API fields directly."
        )

    def test_macro_mappings_are_intentional(self):
        # Phase 2D §3.2.6：cn_gdp 加入 macro mappings（quarter → period）
        allowed_macro_mappings = {"cn_cpi", "cn_ppi", "cn_m", "cn_gdp"}
        actual_mappings = set(TushareClient._COLUMN_RENAMES.keys())
        macro_mappings = actual_mappings - {
            "top_list",
            "block_trade",
            "limit_list",
            "suspend_d",
            # Phase 3D：share_float 重命名 float_type → share_type（与 ORM 列名对齐）
            "share_float",
        }
        assert macro_mappings <= allowed_macro_mappings, (
            f"Unexpected macro mappings: {macro_mappings - allowed_macro_mappings}"
        )


class TestOrmFieldNamesMatchTushare:
    """Test that ORM field names match Tushare API response field names."""

    def test_top_list_pct_change(self):
        assert hasattr(TopList, "pct_change"), "TopList should have 'pct_change' (Tushare API field)"
        assert not hasattr(TopList, "pct_chg") or hasattr(TopList, "pct_change"), (
            "TopList.pct_chg should be renamed to pct_change to match Tushare API"
        )

    def test_block_trade_vol(self):
        assert hasattr(BlockTrade, "vol"), "BlockTrade should have 'vol' (Tushare API field)"
        assert not hasattr(BlockTrade, "volume") or hasattr(BlockTrade, "vol"), (
            "BlockTrade.volume should be renamed to vol to match Tushare API"
        )

    def test_limit_list_limit(self):
        assert hasattr(LimitList, "limit"), "LimitList should have 'limit' (Tushare API field)"
        assert not hasattr(LimitList, "limit_type") or hasattr(LimitList, "limit"), (
            "LimitList.limit_type should be renamed to limit to match Tushare API"
        )

    def test_suspend_d_suspend_type(self):
        assert hasattr(SuspendD, "suspend_type"), "SuspendD should have 'suspend_type' (Tushare API field)"
        assert not hasattr(SuspendD, "suspend_type_name") or hasattr(SuspendD, "suspend_type"), (
            "SuspendD.suspend_type_name should be renamed to suspend_type to match Tushare API"
        )


class TestMacroEconomyColumnRenames:
    """Test that macro economy data uses correct column renames."""

    def test_column_renames_for_cpi(self):
        assert "cn_cpi" in TushareClient._COLUMN_RENAMES
        assert TushareClient._COLUMN_RENAMES["cn_cpi"].get("nt_val") == "cpi"
        assert TushareClient._COLUMN_RENAMES["cn_cpi"].get("month") == "period"

    def test_column_renames_for_ppi(self):
        assert "cn_ppi" in TushareClient._COLUMN_RENAMES
        assert TushareClient._COLUMN_RENAMES["cn_ppi"].get("ppi_yoy") == "ppi"
        assert TushareClient._COLUMN_RENAMES["cn_ppi"].get("month") == "period"

    def test_column_renames_for_m(self):
        assert "cn_m" in TushareClient._COLUMN_RENAMES
        assert TushareClient._COLUMN_RENAMES["cn_m"].get("month") == "period"


class TestShiborDailyColumnNames:
    """Test that shibor_daily ORM attribute names vs DB column names are handled correctly."""

    def test_shibor_dao_uses_db_column_names(self):
        from data.persistence.models import ShiborDaily, get_model_columns

        cols = get_model_columns(ShiborDaily)
        db_column_names = {"1w", "2w", "1m", "3m", "6m", "9m", "1y"}
        for col in db_column_names:
            assert col in cols, f"save_shibor_daily should use DB column name '{col}'"

    def test_shibor_orm_uses_python_attribute_names(self):

        from data.persistence.models import ShiborDaily

        columns = set()
        for name, attr in inspect.getmembers(ShiborDaily):
            if hasattr(attr, "property") and hasattr(attr.property, "columns"):
                columns.add(name)

        python_attrs = {"w1", "w2", "m1", "m3", "m6", "m9", "y1"}

        for attr in python_attrs:
            assert attr in columns, f"ShiborDaily ORM should have Python attribute '{attr}'"
