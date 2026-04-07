"""
Data Dictionary Alignment Tests

Tests that ensure data dictionary contains all ORM model columns
and validates column-level consistency.

Run: pytest tests/test_data_dictionary_alignment.py -v
"""

import inspect

from data.data_dictionary import COMMON_COLUMNS, TABLE_DEFINITIONS
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.holder_dao import HolderDao
from data.persistence.models import (
    Base,
    BlockTrade,
    DailyQuotes,
    Dividend,
    FinaAudit,
    FinancialReports,
    LimitList,
    MoneyflowDaily,
    SuspendD,
    Top10Holders,
    TopList,
)

from .helpers import get_model_columns


def get_data_dict_columns(table_name: str) -> set:
    """Get all column names defined in data dictionary for a table."""
    columns = set(COMMON_COLUMNS.keys())

    if table_name in TABLE_DEFINITIONS:
        table_specific = TABLE_DEFINITIONS[table_name].get("columns", {})
        columns.update(table_specific.keys())

    return columns


class TestDataDictionaryAlignment:
    """Test that data dictionary contains all ORM model columns."""

    def test_daily_quotes_data_dict(self):
        model_cols = get_model_columns(DailyQuotes)
        dict_cols = get_data_dict_columns("daily_quotes")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for daily_quotes: {missing}"

    def test_top_list_data_dict(self):
        model_cols = get_model_columns(TopList)
        dict_cols = get_data_dict_columns("top_list")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for top_list: {missing}"

    def test_block_trade_data_dict(self):
        model_cols = get_model_columns(BlockTrade)
        dict_cols = get_data_dict_columns("block_trade")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for block_trade: {missing}"

    def test_limit_list_data_dict(self):
        model_cols = get_model_columns(LimitList)
        dict_cols = get_data_dict_columns("limit_list")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for limit_list: {missing}"

    def test_suspend_d_data_dict(self):
        model_cols = get_model_columns(SuspendD)
        dict_cols = get_data_dict_columns("suspend_d")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for suspend_d: {missing}"

    def test_moneyflow_daily_data_dict(self):
        model_cols = get_model_columns(MoneyflowDaily)
        dict_cols = get_data_dict_columns("moneyflow_daily")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for moneyflow_daily: {missing}"

    def test_financial_reports_data_dict(self):
        model_cols = get_model_columns(FinancialReports)
        dict_cols = get_data_dict_columns("financial_reports")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for financial_reports: {missing}"

    def test_dividend_data_dict(self):
        model_cols = get_model_columns(Dividend)
        dict_cols = get_data_dict_columns("dividend")
        expected = model_cols - {"updated_at", "created_at"}
        missing = expected - dict_cols
        assert not missing, f"Data dictionary missing columns for dividend: {missing}"


class TestDataDictionaryColumnValidation:
    """Test that data dictionary column validation catches mismatches."""

    def test_data_dict_phantom_columns_detection(self):
        phantom_columns = {}

        for table_name, table_obj in Base.metadata.tables.items():
            orm_cols = {c.name for c in table_obj.columns}
            dd_entry = TABLE_DEFINITIONS.get(table_name, {})
            dd_cols = set(dd_entry.get("columns", {}).keys())

            phantom = dd_cols - orm_cols - set(COMMON_COLUMNS.keys())
            if phantom:
                phantom_columns[table_name] = phantom

        assert not phantom_columns, (
            f"Data dictionary has phantom columns (defined but not in ORM): {phantom_columns}. "
            f"These should be removed from data dictionary or added to ORM."
        )

    def test_orm_columns_in_data_dict(self):
        missing_in_dd = {}

        for table_name, table_obj in Base.metadata.tables.items():
            if table_name in {"stock_sync_status", "alembic_version"}:
                continue

            orm_cols = {c.name for c in table_obj.columns}
            dd_entry = TABLE_DEFINITIONS.get(table_name, {})
            dd_cols = set(dd_entry.get("columns", {}).keys())
            all_dd_cols = dd_cols | set(COMMON_COLUMNS.keys())

            missing = orm_cols - all_dd_cols - {"updated_at", "created_at"}
            if missing:
                missing_in_dd[table_name] = missing

        assert not missing_in_dd, f"ORM columns missing in data dictionary: {missing_in_dd}"


class TestTop10HoldersHoldChange:
    """Test that top10_holders data dictionary matches ORM/DAO."""

    def test_hold_change_in_orm(self):
        model_cols = get_model_columns(Top10Holders)
        assert "hold_change" in model_cols, "Top10Holders ORM should have hold_change column"

    def test_hold_change_in_data_dictionary(self):
        dd_cols = get_data_dict_columns("top10_holders")
        assert "hold_change" in dd_cols, "Data dictionary defines hold_change for top10_holders"

    def test_top10_holders_consistency(self):
        import re

        get_model_columns(Top10Holders) - {"updated_at", "created_at"}

        source = inspect.getsource(HolderDao.save_top10_holders)
        pattern = r"(?:cols|columns)\s*=\s*\[([^\]]+)\]"
        match = re.search(pattern, source, re.DOTALL)
        assert match, "Could not find cols in save_top10_holders"

        cols_str = match.group(1)
        dao_cols = set()
        for item in cols_str.split(","):
            item = item.strip().strip('"').strip("'")
            if item and not item.startswith("#"):
                dao_cols.add(item)

        get_data_dict_columns("top10_holders")

        assert "hold_change" in dao_cols, "DAO should save hold_change"
        assert "hold_float_ratio" in dao_cols, "DAO should save hold_float_ratio"


class TestFinaAuditAuditSign:
    """Test that fina_audit API request fields match ORM/DAO columns."""

    def test_audit_sign_in_orm(self):
        model_cols = get_model_columns(FinaAudit)
        assert "audit_sign" in model_cols, "FinaAudit ORM should have audit_sign column"

    def test_audit_sign_in_api_request(self):
        from data.external.tushare_client import TushareClient

        source = inspect.getsource(TushareClient.get_fina_audit)
        assert "audit_sign" in source, "get_fina_audit API requests audit_sign field"

    def test_fina_audit_dao_includes_audit_sign(self):
        import re

        source = inspect.getsource(FinancialDao.save_fina_audit)
        pattern = r"(?:cols|columns)\s*=\s*\[([^\]]+)\]"
        match = re.search(pattern, source, re.DOTALL)
        assert match, "Could not find cols in save_fina_audit"

        cols_str = match.group(1)
        dao_cols = set()
        for item in cols_str.split(","):
            item = item.strip().strip('"').strip("'")
            if item and not item.startswith("#"):
                dao_cols.add(item)

        assert "audit_sign" in dao_cols, "save_fina_audit should include audit_sign"
