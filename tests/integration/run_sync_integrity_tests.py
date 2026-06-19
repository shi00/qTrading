"""
Simple test runner for sync integrity tests
"""


def test_sync_result_merge():
    """测试 SyncResult.merge 的状态合并逻辑。"""
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
    """契约检查：CORE_RESUME_TABLES 应包含核心行情表。"""
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
    """契约检查：HistoricalSyncStrategy 不应直接持有 DAO 或暴露 _read_db。"""
    from data.sync.historical import HistoricalSyncStrategy

    # 契约：策略不应直接持有 quote_dao 属性
    assert not hasattr(HistoricalSyncStrategy, "quote_dao"), "HistoricalSyncStrategy 不应直接持有 quote_dao"
    # 契约：策略不应暴露 _read_db 方法（应通过 SyncContext.cache 访问）
    assert "_read_db" not in dir(HistoricalSyncStrategy), "HistoricalSyncStrategy 不应暴露 _read_db"


def test_fina_audit_table():
    """契约检查：FinancialDao 应提供财务完整性校验方法。"""
    from data.persistence.daos.financial_dao import FinancialDao

    assert hasattr(FinancialDao, "verify_stock_financial_integrity"), (
        "FinancialDao should have verify_stock_financial_integrity method"
    )


def test_delisted_stock_sql():
    """契约检查：QuoteDao 应提供预期股票数统计方法。"""
    from data.persistence.daos.quote_dao import QuoteDao

    assert hasattr(QuoteDao, "get_expected_stock_count"), "QuoteDao should have get_expected_stock_count method"


def test_quality_weights_usage():
    """契约检查：QuoteDao 应提供批量同步质量评分方法。"""
    from data.persistence.daos.quote_dao import QuoteDao

    assert hasattr(QuoteDao, "get_bulk_sync_quality_scores"), "QuoteDao should have get_bulk_sync_quality_scores method"


def test_low_frequency_tables():
    """契约检查：LOW_FREQUENCY_TABLES 应为集合并包含低频表。"""
    from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

    assert isinstance(LOW_FREQUENCY_TABLES, (list, set, tuple)), "LOW_FREQUENCY_TABLES should be a collection"
    assert "block_trade" in LOW_FREQUENCY_TABLES or "limit_list_d" in LOW_FREQUENCY_TABLES, (
        "Expected low frequency tables not found"
    )
