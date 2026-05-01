"""
Simple test runner for sync integrity tests
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_sync_result_merge():
    from data.sync.base import SyncResult

    # Test merge_basic_fields
    result1 = SyncResult(status="success", added=10, updated=5, skipped=2)
    result2 = SyncResult(status="partial", added=5, updated=3, skipped=1)
    result1.merge(result2)
    assert result1.added == 15, f"Expected 15, got {result1.added}"
    assert result1.updated == 8, f"Expected 8, got {result1.updated}"
    assert result1.skipped == 3, f"Expected 3, got {result1.skipped}"
    print("test_merge_basic_fields: PASSED")

    # Test merge_failed_plus_success_equals_partial
    result1 = SyncResult(status="failed")
    result2 = SyncResult(status="success")
    result1.merge(result2)
    assert result1.status == "partial", f"Expected partial, got {result1.status}"
    print("test_merge_failed_plus_success_equals_partial: PASSED")

    # Test merge_cancelled_takes_priority
    result1 = SyncResult(status="success")
    result2 = SyncResult(status="cancelled")
    result1.merge(result2)
    assert result1.status == "cancelled", f"Expected cancelled, got {result1.status}"
    print("test_merge_cancelled_takes_priority: PASSED")

    # Test merge_failed_plus_failed_equals_failed
    result1 = SyncResult(status="failed")
    result2 = SyncResult(status="failed")
    result1.merge(result2)
    assert result1.status == "failed", f"Expected failed, got {result1.status}"
    print("test_merge_failed_plus_failed_equals_failed: PASSED")

    # Test merge_partial_plus_success_equals_partial
    result1 = SyncResult(status="partial")
    result2 = SyncResult(status="success")
    result1.merge(result2)
    assert result1.status == "partial", f"Expected partial, got {result1.status}"
    print("test_merge_partial_plus_success_equals_partial: PASSED")

    print("\nAll SyncResult.merge tests PASSED!")


def test_core_resume_tables():
    from unittest.mock import MagicMock

    from data.sync.historical import HistoricalSyncStrategy

    mock_context = MagicMock()
    strategy = HistoricalSyncStrategy(mock_context)

    assert hasattr(strategy, "CORE_RESUME_TABLES"), "CORE_RESUME_TABLES not defined"
    assert "daily_quotes" in strategy.CORE_RESUME_TABLES, "daily_quotes not in CORE_RESUME_TABLES"
    assert "daily_indicators" in strategy.CORE_RESUME_TABLES, "daily_indicators not in CORE_RESUME_TABLES"
    assert "block_trade" not in strategy.CORE_RESUME_TABLES, "block_trade should not be in CORE_RESUME_TABLES"
    print("test_core_resume_tables: PASSED")


def test_no_direct_dao_access():
    import inspect

    from data.sync.historical import HistoricalSyncStrategy

    source = inspect.getsource(HistoricalSyncStrategy)
    assert "quote_dao._read_db" not in source, "Direct DAO access found in HistoricalSyncStrategy"
    print("test_no_direct_dao_access: PASSED")


def test_fina_audit_table():
    import inspect

    from data.persistence.daos.financial_dao import FinancialDao

    source = inspect.getsource(FinancialDao.verify_stock_financial_integrity)
    assert "fina_audit" in source, "fina_audit not found in verify_stock_financial_integrity"
    assert "fina_indicator" not in source, "fina_indicator should not be in verify_stock_financial_integrity"
    print("test_fina_audit_table: PASSED")


def test_delisted_stock_sql():
    import inspect

    from data.persistence.daos.quote_dao import QuoteDao

    source = inspect.getsource(QuoteDao.get_expected_stock_count)
    assert "list_status = 'L'" in source or "list_status='L'" in source, "L status check not found"
    assert "delist_date IS NOT NULL" in source or "delist_date is not null" in source.lower(), (
        "delist_date IS NOT NULL check not found"
    )
    print("test_delisted_stock_sql: PASSED")


def test_quality_weights_usage():
    import inspect

    from data.persistence.daos.quote_dao import QuoteDao

    source = inspect.getsource(QuoteDao.get_bulk_sync_quality_scores)
    assert "quality_weights" in source, "quality_weights not used in scoring"
    print("test_quality_weights_usage: PASSED")


def test_low_frequency_tables():
    from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

    assert isinstance(LOW_FREQUENCY_TABLES, (list, set, tuple)), "LOW_FREQUENCY_TABLES should be a collection"
    assert "block_trade" in LOW_FREQUENCY_TABLES or "limit_list_d" in LOW_FREQUENCY_TABLES, (
        "Expected low frequency tables not found"
    )
    print("test_low_frequency_tables: PASSED")


if __name__ == "__main__":
    print("Running sync integrity tests...\n")

    tests = [
        test_sync_result_merge,
        test_core_resume_tables,
        test_no_direct_dao_access,
        test_fina_audit_table,
        test_delisted_stock_sql,
        test_quality_weights_usage,
        test_low_frequency_tables,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"{test.__name__}: FAILED - {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'=' * 50}")

    sys.exit(0 if failed == 0 else 1)
