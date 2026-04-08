"""
Sync Type Consistency Tests

Tests for ensuring type consistency in data synchronization:
- Date types: datetime.date vs string
- Schema completeness: required columns exist
- Cache method coverage: all critical tables supported
- Breakpoint resume: core tables configuration

These tests prevent regression of known type-related issues.

Run: pytest tests/test_sync_type_consistency.py -v
"""

import inspect

from data.constants import FINANCIAL_REPORT_SCHEMA_COLS
from data.data_dictionary import validate_schema_definitions
from data.persistence.daos.quote_dao import QuoteDao
from data.sync.historical import HistoricalSyncStrategy
from data.sync.holder import HolderSyncStrategy


class TestSyncTypeConsistency:
    """Test cases for type consistency in data synchronization."""

    def test_financial_report_schema_cols_has_goodwill_and_audit_result(self):
        assert "goodwill" in FINANCIAL_REPORT_SCHEMA_COLS, (
            "FINANCIAL_REPORT_SCHEMA_COLS missing 'goodwill' - incremental sync will drop this field"
        )
        assert "audit_result" in FINANCIAL_REPORT_SCHEMA_COLS, (
            "FINANCIAL_REPORT_SCHEMA_COLS missing 'audit_result' - incremental sync will drop this field"
        )

    def test_get_cached_dates_for_table_has_all_critical_tables(self):
        critical_tables = [
            "daily_quotes",
            "daily_indicators",
            "moneyflow_daily",
            "limit_list",
            "suspend_d",
            "margin_daily",
            "northbound_holding",
            "moneyflow_hsgt",
            "top_list",
            "block_trade",
            "index_daily",
            "index_dailybasic",
        ]

        source = inspect.getsource(QuoteDao.get_cached_dates_for_table)
        missing = []
        for table in critical_tables:
            if f'"{table}"' not in source and f"'{table}'" not in source:
                missing.append(table)

        assert not missing, f"get_cached_dates_for_table date_col_map missing tables: {missing}"

    def test_validate_schema_definitions_includes_common_columns(self):
        source = inspect.getsource(validate_schema_definitions)
        assert "COMMON_COLUMNS" in source, "validate_schema_definitions should include COMMON_COLUMNS in dd_cols"

    def test_holder_sync_uses_get_now(self):
        source = inspect.getsource(HolderSyncStrategy)
        assert "get_now()" in source, "HolderSyncStrategy should use get_now() instead of datetime.date.today()"
        assert "datetime.date.today()" not in source, "HolderSyncStrategy should not use datetime.date.today()"

    def test_historical_sync_moneyflow_accepts_datetime_date(self):
        source = inspect.getsource(HistoricalSyncStrategy.sync_moneyflow)
        assert "datetime.date" in source or "date | None" in source, (
            "sync_moneyflow should accept datetime.date parameter"
        )

    def test_historical_sync_northbound_accepts_datetime_date(self):
        source = inspect.getsource(HistoricalSyncStrategy.sync_northbound)
        assert "datetime.date" in source or "date | None" in source, (
            "sync_northbound should accept datetime.date parameter"
        )

    def test_historical_sync_uses_core_tables_for_resume(self):
        source = inspect.getsource(HistoricalSyncStrategy._run_historical_sync)
        assert "CORE_RESUME_TABLES" in source or "core_tables" in source, (
            "_run_historical_sync should use CORE_RESUME_TABLES for breakpoint resume"
        )
        class_source = inspect.getsource(HistoricalSyncStrategy)
        assert "daily_quotes" in class_source and "daily_indicators" in class_source, (
            "CORE_RESUME_TABLES should include daily_quotes and daily_indicators"
        )

    def test_get_cached_dates_returns_datetime_date(self):
        source = inspect.getsource(QuoteDao.get_cached_dates_for_table)
        assert 'strftime("%Y%m%d")' not in source, (
            "get_cached_dates_for_table should return datetime.date objects, not strings. "
            "API calls handle date->string conversion in _handle_api_call."
        )

    def test_get_cached_trade_dates_returns_datetime_date(self):
        source = inspect.getsource(QuoteDao.get_cached_trade_dates)
        assert 'strftime("%Y%m%d")' not in source, (
            "get_cached_trade_dates should return datetime.date objects for consistency"
        )

    def test_sync_daily_market_snapshot_accepts_datetime_date(self):
        source = inspect.getsource(HistoricalSyncStrategy.sync_daily_market_snapshot)
        assert "datetime.date" in source, "sync_daily_market_snapshot should accept datetime.date, not str"

    def test_sync_one_day_uses_datetime_date(self):
        source = inspect.getsource(HistoricalSyncStrategy._run_historical_sync)
        assert "datetime.date" in source, "sync_one_day should use datetime.date for type consistency"

    def test_validate_schema_definitions_phantom_cols_excludes_common(self):
        source = inspect.getsource(validate_schema_definitions)
        assert "dd_table_cols" in source, (
            "validate_schema_definitions should use dd_table_cols for phantom_cols check, "
            "not dd_cols_with_common (which includes COMMON_COLUMNS)"
        )
        assert "phantom_cols = dd_table_cols - orm_cols" in source, (
            "phantom_cols should be calculated from dd_table_cols, not dd_cols_with_common"
        )
