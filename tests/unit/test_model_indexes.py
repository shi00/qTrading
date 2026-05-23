import pytest
import sqlalchemy as sa

from data.persistence.models import TaskHistory, MarketNews


class TestTaskHistoryIndexes:
    """DB-P1-7: Verify task_history has indexes on frequently queried columns."""

    def test_status_created_at_composite_index_exists(self):
        idx_names = {idx.name for idx in TaskHistory.__table__.indexes}
        assert "idx_task_history_status_created" in idx_names, (
            f"Missing composite index 'idx_task_history_status_created'. Found: {idx_names}"
        )

    def test_completed_at_index_exists(self):
        idx_names = {idx.name for idx in TaskHistory.__table__.indexes}
        assert "idx_task_history_completed" in idx_names, (
            f"Missing index 'idx_task_history_completed'. Found: {idx_names}"
        )

    def test_status_created_at_index_covers_status_and_created_at(self):
        for idx in TaskHistory.__table__.indexes:
            if idx.name == "idx_task_history_status_created":
                col_names = [c.name for c in idx.columns]
                assert col_names == ["status", "created_at"], f"Expected ['status', 'created_at'], got {col_names}"
                break
        else:
            pytest.fail("Index idx_task_history_status_created not found")

    def test_completed_at_index_covers_completed_at(self):
        for idx in TaskHistory.__table__.indexes:
            if idx.name == "idx_task_history_completed":
                col_names = [c.name for c in idx.columns]
                assert col_names == ["completed_at"], f"Expected ['completed_at'], got {col_names}"
                break
        else:
            pytest.fail("Index idx_task_history_completed not found")


class TestMarketNewsUniqueConstraint:
    """Verify market_news has composite unique constraint on (content_hash, publish_time)."""

    def test_content_hash_publish_time_composite_unique_constraint(self):
        constraint_col_sets = []
        for constraint in MarketNews.__table__.constraints:
            if isinstance(constraint, sa.UniqueConstraint):
                col_names = tuple(c.name for c in constraint.columns)
                constraint_col_sets.append(col_names)

        has_composite = any(cols == ("content_hash", "publish_time") for cols in constraint_col_sets)
        has_single_hash = any(cols == ("content_hash",) for cols in constraint_col_sets)

        assert has_composite, (
            f"Expected composite UniqueConstraint on (content_hash, publish_time). "
            f"Found constraint column sets: {constraint_col_sets}"
        )
        assert not has_single_hash, (
            f"Single-column UniqueConstraint(content_hash) should be replaced "
            f"with composite UniqueConstraint(content_hash, publish_time). "
            f"Found: {constraint_col_sets}"
        )

    def test_publish_time_is_not_nullable(self):
        col = MarketNews.__table__.c.publish_time
        assert col.nullable is False, "publish_time must be NOT NULL for composite unique constraint to work correctly"
