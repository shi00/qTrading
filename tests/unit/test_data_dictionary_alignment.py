"""
Data Dictionary Alignment Tests

Tests that ensure data dictionary contains all ORM model columns
and validates column-level consistency.

Run: pytest tests/test_data_dictionary_alignment.py -v
"""

from data.data_dictionary import COMMON_COLUMNS, columns_of
from data.persistence.models import (
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

from tests._helpers import extract_fields_from_api_method, get_model_columns
import pytest


pytestmark = pytest.mark.unit


def get_data_dict_columns(table_name: str) -> set:
    """Get all column names defined in data dictionary for a table.

    OSS-01：列集合从 ORM 派生（columns_of），COMMON_COLUMNS 兜底公共列。
    """
    columns = set(COMMON_COLUMNS.keys())
    columns.update(columns_of(table_name))
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
    """OSS-01：列集合已从 ORM 派生，列级"幽灵列/缺失列"比对由派生保证物理不可能，
    语义由 test_i18n_keys_completeness.TestI18nKeysCompleteness.test_data_dictionary_i18n_keys_exist
    与 test_data_dictionary_derivation.py 的 LEGACY 快照等价断言接管（本类不再重复比对）。"""


class TestTop10HoldersHoldChange:
    """Test that top10_holders data dictionary matches ORM/DAO."""

    def test_hold_change_in_orm(self):
        model_cols = get_model_columns(Top10Holders)
        assert "hold_change" in model_cols, "Top10Holders ORM should have hold_change column"

    def test_hold_change_in_data_dictionary(self):
        dd_cols = get_data_dict_columns("top10_holders")
        assert "hold_change" in dd_cols, "Data dictionary defines hold_change for top10_holders"

    def test_top10_holders_consistency(self):
        dao_cols = get_model_columns(Top10Holders)
        assert "hold_change" in dao_cols, "DAO should save hold_change"
        assert "hold_float_ratio" in dao_cols, "DAO should save hold_float_ratio"


class TestFinaAuditAuditSign:
    """Test that fina_audit API request fields match ORM/DAO columns."""

    def test_audit_sign_in_orm(self):
        model_cols = get_model_columns(FinaAudit)
        assert "audit_sign" in model_cols, "FinaAudit ORM should have audit_sign column"

    def test_audit_sign_in_api_request(self):
        from data.external.tushare_client import TushareClient

        api_fields = extract_fields_from_api_method(TushareClient.get_fina_audit)
        assert "audit_sign" in api_fields, "get_fina_audit API requests audit_sign field"

    def test_fina_audit_dao_includes_audit_sign(self):
        dao_cols = get_model_columns(FinaAudit)
        assert "audit_sign" in dao_cols, "save_fina_audit should include audit_sign"
