"""
Simple test runner for sync integrity tests
"""


def test_sync_result_merge():
    from data.sync.base import SyncResult

    # Test merge_basic_fields
    result1 = SyncResult(status="success", added=10, updated=5, skipped=2)
    result2 = SyncResult(status="partial", added=5, updated=3, skipped=1)
    result1.merge(result2)
    assert result1.added == 15, f"Expected 15, got {result1.added}"
    assert result1.updated == 8, f"Expected 8, got {result1.updated}"
    assert result1.skipped == 3, f"Expected 3, got {result1.skipped}"

    # Test merge_failed_plus_success_equals_partial
    result1 = SyncResult(status="failed")
    result2 = SyncResult(status="success")
    result1.merge(result2)
    assert result1.status == "partial", f"Expected partial, got {result1.status}"

    # Test merge_cancelled_takes_priority
    result1 = SyncResult(status="success")
    result2 = SyncResult(status="cancelled")
    result1.merge(result2)
    assert result1.status == "cancelled", f"Expected cancelled, got {result1.status}"

    # Test merge_failed_plus_failed_equals_failed
    result1 = SyncResult(status="failed")
    result2 = SyncResult(status="failed")
    result1.merge(result2)
    assert result1.status == "failed", f"Expected failed, got {result1.status}"

    # Test merge_partial_plus_success_equals_partial
    result1 = SyncResult(status="partial")
    result2 = SyncResult(status="success")
    result1.merge(result2)
    assert result1.status == "partial", f"Expected partial, got {result1.status}"


def test_core_resume_tables():
    from unittest.mock import MagicMock

    from data.sync.historical import HistoricalSyncStrategy

    mock_context = MagicMock()
    strategy = HistoricalSyncStrategy(mock_context)

    assert hasattr(strategy, "CORE_RESUME_TABLES"), "CORE_RESUME_TABLES not defined"
    assert "daily_quotes" in strategy.CORE_RESUME_TABLES, "daily_quotes not in CORE_RESUME_TABLES"
    assert "daily_indicators" in strategy.CORE_RESUME_TABLES, "daily_indicators not in CORE_RESUME_TABLES"
    assert "block_trade" in strategy.CORE_RESUME_TABLES, (
        "block_trade should be in CORE_RESUME_TABLES (unified with SYNCED_TABLES)"
    )


def test_no_direct_dao_access():
    from data.sync.historical import HistoricalSyncStrategy

    assert not hasattr(HistoricalSyncStrategy, "quote_dao") or "_read_db" not in dir(HistoricalSyncStrategy), (
        "Direct DAO access found in HistoricalSyncStrategy"
    )


def test_fina_audit_table():
    from data.persistence.daos.financial_dao import FinancialDao

    assert hasattr(FinancialDao, "verify_stock_financial_integrity"), (
        "FinancialDao should have verify_stock_financial_integrity method"
    )


def test_delisted_stock_sql():
    from data.persistence.daos.quote_dao import QuoteDao

    assert hasattr(QuoteDao, "get_expected_stock_count"), "QuoteDao should have get_expected_stock_count method"


def test_quality_weights_usage():
    from data.persistence.daos.quote_dao import QuoteDao

    assert hasattr(QuoteDao, "get_bulk_sync_quality_scores"), "QuoteDao should have get_bulk_sync_quality_scores method"


def test_low_frequency_tables():
    from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

    assert isinstance(LOW_FREQUENCY_TABLES, (list, set, tuple)), "LOW_FREQUENCY_TABLES should be a collection"
    assert "block_trade" in LOW_FREQUENCY_TABLES or "limit_list_d" in LOW_FREQUENCY_TABLES, (
        "Expected low frequency tables not found"
    )
