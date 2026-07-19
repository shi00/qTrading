"""Synchronous integrity contract tests.

P3-18: 迁移自 ``run_sync_integrity_tests.py``（已删除）。
保留原 6 类契约检查（12 个测试方法）：SyncResult.merge、CORE_RESUME_TABLES、
无直接 DAO 访问、FinancialDao/QuoteDao 方法存在性、LOW_FREQUENCY_TABLES。

no_db: 仅断言类属性/方法存在性与纯内存 SyncResult 合并逻辑，不需要真实 DB。
"""

from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.no_db]


class TestSyncResultMerge:
    """契约检查：SyncResult.merge 的状态合并逻辑。"""

    def test_merge_basic_fields(self):
        from data.sync.base import SyncResult

        result1 = SyncResult(status="success", added=10, updated=5, skipped=2)
        result2 = SyncResult(status="partial", added=5, updated=3, skipped=1)
        result1.merge(result2)
        assert result1.added == 15, f"Expected 15, got {result1.added}"
        assert result1.updated == 8, f"Expected 8, got {result1.updated}"
        assert result1.skipped == 3, f"Expected 3, got {result1.skipped}"

    def test_merge_failed_plus_success_equals_partial(self):
        from data.sync.base import SyncResult

        result1 = SyncResult(status="failed")
        result2 = SyncResult(status="success")
        result1.merge(result2)
        assert result1.status == "partial", f"Expected partial, got {result1.status}"

    def test_merge_cancelled_takes_priority(self):
        from data.sync.base import SyncResult

        result1 = SyncResult(status="success")
        result2 = SyncResult(status="cancelled")
        result1.merge(result2)
        assert result1.status == "cancelled", f"Expected cancelled, got {result1.status}"

    def test_merge_failed_plus_failed_equals_failed(self):
        from data.sync.base import SyncResult

        result1 = SyncResult(status="failed")
        result2 = SyncResult(status="failed")
        result1.merge(result2)
        assert result1.status == "failed", f"Expected failed, got {result1.status}"

    def test_merge_partial_plus_success_equals_partial(self):
        from data.sync.base import SyncResult

        result1 = SyncResult(status="partial")
        result2 = SyncResult(status="success")
        result1.merge(result2)
        assert result1.status == "partial", f"Expected partial, got {result1.status}"


class TestCoreResumeTables:
    """契约检查：CORE_RESUME_TABLES 应包含核心行情表。"""

    def test_core_resume_tables_contains_core_tables(self):
        from data.sync.historical import HistoricalSyncStrategy

        mock_context = MagicMock()
        strategy = HistoricalSyncStrategy(mock_context)

        assert hasattr(strategy, "CORE_RESUME_TABLES"), "CORE_RESUME_TABLES not defined"
        assert "daily_quotes" in strategy.CORE_RESUME_TABLES, "daily_quotes not in CORE_RESUME_TABLES"
        assert "daily_indicators" in strategy.CORE_RESUME_TABLES, "daily_indicators not in CORE_RESUME_TABLES"
        assert "block_trade" in strategy.CORE_RESUME_TABLES, (
            "block_trade should be in CORE_RESUME_TABLES (unified with SYNCED_TABLES)"
        )


class TestNoDirectDaoAccess:
    """契约检查：HistoricalSyncStrategy 不应直接持有 DAO 或暴露 _read_db。"""

    def test_strategy_does_not_hold_quote_dao(self):
        from data.sync.historical import HistoricalSyncStrategy

        assert not hasattr(HistoricalSyncStrategy, "quote_dao"), "HistoricalSyncStrategy 不应直接持有 quote_dao"

    def test_strategy_does_not_expose_read_db(self):
        from data.sync.historical import HistoricalSyncStrategy

        assert "_read_db" not in dir(HistoricalSyncStrategy), "HistoricalSyncStrategy 不应暴露 _read_db"


class TestFinancialDaoContract:
    """契约检查：FinancialDao 应提供财务完整性校验方法。"""

    def test_financial_dao_has_integrity_verify_method(self):
        from data.persistence.daos.financial_dao import FinancialDao

        assert hasattr(FinancialDao, "verify_stock_financial_integrity"), (
            "FinancialDao should have verify_stock_financial_integrity method"
        )


class TestQuoteDaoContract:
    """契约检查：QuoteDao 应提供预期股票数统计与批量同步质量评分方法。"""

    def test_quote_dao_has_get_expected_stock_count(self):
        from data.persistence.daos.quote_dao import QuoteDao

        assert hasattr(QuoteDao, "get_expected_stock_count"), "QuoteDao should have get_expected_stock_count method"

    def test_quote_dao_has_get_bulk_sync_quality_scores(self):
        from data.persistence.daos.quote_dao import QuoteDao

        assert hasattr(QuoteDao, "get_bulk_sync_quality_scores"), (
            "QuoteDao should have get_bulk_sync_quality_scores method"
        )


class TestLowFrequencyTables:
    """契约检查：LOW_FREQUENCY_TABLES 应为集合并包含低频表。"""

    def test_low_frequency_tables_is_collection_with_low_freq_entries(self):
        from data.persistence.daos.quote_dao import LOW_FREQUENCY_TABLES

        assert isinstance(LOW_FREQUENCY_TABLES, (list, set, tuple)), "LOW_FREQUENCY_TABLES should be a collection"
        assert "block_trade" in LOW_FREQUENCY_TABLES or "limit_list" in LOW_FREQUENCY_TABLES, (
            "Expected low frequency tables not found"
        )
