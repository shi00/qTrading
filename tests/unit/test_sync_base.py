import datetime
from unittest.mock import MagicMock

from data.sync.base import SyncContext, SyncResult


class TestSyncContext:
    def test_creation(self):
        ctx = SyncContext(api="api", cache="cache")
        assert ctx.api == "api"
        assert ctx.cache == "cache"

    def test_processor_none(self):
        ctx = SyncContext(api="api", cache="cache")
        assert ctx.processor is None

    def test_processor_setter_with_weakref(self):
        ctx = SyncContext(api="api", cache="cache")
        obj = MagicMock()
        ctx.processor = obj
        assert ctx.processor is obj

    def test_processor_setter_none(self):
        ctx = SyncContext(api="api", cache="cache")
        ctx.processor = None
        assert ctx.processor is None


class TestSyncResult:
    def test_default_values(self):
        result = SyncResult()
        assert result.added == 0
        assert result.updated == 0
        assert result.skipped == 0
        assert result.errors == []
        assert result.status == "success"

    def test_custom_values(self):
        result = SyncResult(added=10, updated=5, status="partial")
        assert result.added == 10
        assert result.updated == 5
        assert result.status == "partial"

    def test_merge_adds_counts(self):
        r1 = SyncResult(added=5, updated=3)
        r2 = SyncResult(added=2, updated=1)
        r1.merge(r2)
        assert r1.added == 7
        assert r1.updated == 4

    def test_merge_errors(self):
        r1 = SyncResult(errors=["err1"])
        r2 = SyncResult(errors=["err2"])
        r1.merge(r2)
        assert r1.errors == ["err1", "err2"]

    def test_merge_quality_scores(self):
        r1 = SyncResult(quality_scores={datetime.date(2024, 1, 1): 0.9})
        r2 = SyncResult(quality_scores={datetime.date(2024, 1, 2): 0.8})
        r1.merge(r2)
        assert len(r1.quality_scores) == 2

    def test_merge_quality_scores_string_keys(self):
        r1 = SyncResult(quality_scores={})
        r2 = SyncResult(quality_scores={"20240101": 0.9})
        r1.merge(r2)
        assert datetime.date(2024, 1, 1) in r1.quality_scores

    def test_merge_table_stats_new(self):
        r1 = SyncResult(table_stats={"daily": {"count": 10}})
        r2 = SyncResult(table_stats={"weekly": {"count": 5}})
        r1.merge(r2)
        assert "weekly" in r1.table_stats

    def test_merge_table_stats_existing(self):
        r1 = SyncResult(table_stats={"daily": {"count": 10}})
        r2 = SyncResult(table_stats={"daily": {"count": 5}})
        r1.merge(r2)
        assert r1.table_stats["daily"]["count"] == 15
