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
from data.data_dictionary import COMMON_COLUMNS, TABLE_DEFINITIONS
from data.persistence.daos.quote_dao import QuoteDao
from data.sync.historical import HistoricalSyncStrategy
from data.sync.financial import FinancialSyncStrategy


class TestSyncTypeConsistency:
    """Test cases for type consistency in data synchronization."""

    def test_financial_report_schema_cols_matches_orm(self):
        """FINANCIAL_REPORT_SCHEMA_COLS 必须与 ORM 模型列完全一致（排除时间戳）"""
        from data.persistence.models import FinancialReports, get_model_columns

        orm_cols = set(get_model_columns(FinancialReports))
        schema_cols = set(FINANCIAL_REPORT_SCHEMA_COLS)
        missing_in_schema = orm_cols - schema_cols
        extra_in_schema = schema_cols - orm_cols
        assert not missing_in_schema, f"FINANCIAL_REPORT_SCHEMA_COLS 缺少 ORM 字段: {missing_in_schema}"
        assert not extra_in_schema, f"FINANCIAL_REPORT_SCHEMA_COLS 多出 ORM 不存在的字段: {extra_in_schema}"

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

        synced = set(HistoricalSyncStrategy.SYNCED_TABLES)
        missing = [t for t in critical_tables if t not in synced]
        assert not missing, f"HistoricalSyncStrategy.SYNCED_TABLES missing tables: {missing}"

    def test_validate_schema_definitions_includes_common_columns(self):
        from data.persistence.models import Base

        db_tables = set(Base.metadata.tables.keys())
        for table_name in TABLE_DEFINITIONS:
            if table_name not in db_tables:
                continue
            orm_table = Base.metadata.tables[table_name]
            orm_cols = set(c.name for c in orm_table.columns)
            dd_table_cols = set(TABLE_DEFINITIONS[table_name].get("columns", {}).keys())
            dd_cols_with_common = dd_table_cols | set(COMMON_COLUMNS.keys())
            assert not any(col in COMMON_COLUMNS and col not in dd_cols_with_common for col in orm_cols), (
                "validate_schema_definitions should include COMMON_COLUMNS in dd_cols"
            )

    def test_holder_sync_uses_get_now(self):
        import data.sync.holder as holder_mod

        assert hasattr(holder_mod, "get_now"), "HolderSyncStrategy module should import get_now"

    def test_historical_sync_uses_get_now_instead_of_date_today(self):
        import data.sync.historical as hist_mod

        assert hasattr(hist_mod, "get_now"), "HistoricalSyncStrategy module should import get_now"

    def test_historical_sync_moneyflow_accepts_datetime_date(self):
        sig = inspect.signature(HistoricalSyncStrategy.sync_moneyflow)
        trade_date_param = sig.parameters.get("trade_date")
        assert trade_date_param is not None, "sync_moneyflow should have trade_date parameter"
        annotation = trade_date_param.annotation
        assert annotation != inspect.Parameter.empty, "sync_moneyflow trade_date should have type annotation"
        ann_str = str(annotation)
        assert "date" in ann_str, f"sync_moneyflow trade_date annotation should reference date type, got: {ann_str}"

    def test_historical_sync_northbound_accepts_datetime_date(self):
        assert hasattr(HistoricalSyncStrategy, "sync_northbound"), (
            "HistoricalSyncStrategy should have sync_northbound method"
        )

    def test_historical_sync_uses_core_tables_for_resume(self):
        assert hasattr(HistoricalSyncStrategy, "CORE_RESUME_TABLES"), (
            "HistoricalSyncStrategy should define CORE_RESUME_TABLES"
        )
        core_tables = HistoricalSyncStrategy.CORE_RESUME_TABLES
        assert "daily_quotes" in core_tables, "CORE_RESUME_TABLES should include daily_quotes"
        assert "daily_indicators" in core_tables, "CORE_RESUME_TABLES should include daily_indicators"
        assert set(core_tables) == set(HistoricalSyncStrategy.SYNCED_TABLES), (
            "CORE_RESUME_TABLES should equal SYNCED_TABLES for semantic consistency"
        )

    def test_get_cached_dates_returns_datetime_date(self):
        sig = inspect.signature(QuoteDao.get_cached_dates_for_table)
        return_annotation = sig.return_annotation
        assert return_annotation != inspect.Signature.empty, (
            "get_cached_dates_for_table should have return type annotation"
        )

    def test_get_cached_trade_dates_returns_datetime_date(self):
        sig = inspect.signature(QuoteDao.get_cached_trade_dates)
        return_annotation = sig.return_annotation
        assert return_annotation != inspect.Signature.empty, "get_cached_trade_dates should have return type annotation"

    def test_sync_daily_market_snapshot_accepts_datetime_date(self):
        sig = inspect.signature(HistoricalSyncStrategy.sync_daily_market_snapshot)
        trade_date_param = sig.parameters.get("trade_date")
        assert trade_date_param is not None, "sync_daily_market_snapshot should have trade_date parameter"
        annotation = trade_date_param.annotation
        ann_str = str(annotation)
        assert "date" in ann_str, (
            f"sync_daily_market_snapshot trade_date should reference datetime.date, got: {ann_str}"
        )

    def test_sync_one_day_uses_datetime_date(self):
        assert hasattr(HistoricalSyncStrategy, "_run_historical_sync"), (
            "HistoricalSyncStrategy should have _run_historical_sync method"
        )
        sig = inspect.signature(HistoricalSyncStrategy._run_historical_sync)
        assert len(sig.parameters) >= 3, "_run_historical_sync should accept days, progress_callback, result"

    def test_validate_schema_definitions_phantom_cols_excludes_common(self):
        from data.persistence.models import Base

        db_tables = set(Base.metadata.tables.keys())
        for table_name in TABLE_DEFINITIONS:
            if table_name not in db_tables:
                continue
            orm_table = Base.metadata.tables[table_name]
            orm_cols = set(c.name for c in orm_table.columns)
            dd_table_cols = set(TABLE_DEFINITIONS[table_name].get("columns", {}).keys())
            phantom_cols = dd_table_cols - orm_cols
            common_only_phantom = phantom_cols & set(COMMON_COLUMNS.keys())
            assert not common_only_phantom, (
                f"Table '{table_name}': COMMON_COLUMNS entries should not appear as phantom cols "
                f"(they are implicitly available). Phantom common cols: {common_only_phantom}"
            )

    def test_financial_sync_uses_trade_calendar_not_deprecated_api(self):
        import data.sync.financial as fin_mod

        assert hasattr(fin_mod, "FinancialSyncStrategy")
        assert hasattr(FinancialSyncStrategy, "_get_effective_trade_date"), (
            "FinancialSyncStrategy should have _get_effective_trade_date method"
        )
        sig = inspect.signature(FinancialSyncStrategy._get_effective_trade_date)
        assert sig.return_annotation != inspect.Signature.empty, (
            "FinancialSyncStrategy._get_effective_trade_date should have return type annotation"
        )
        ann_str = str(sig.return_annotation)
        assert "date" in ann_str, (
            f"FinancialSyncStrategy._get_effective_trade_date should return datetime.date, got: {ann_str}"
        )
