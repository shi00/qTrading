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
    """DB-P1-8: Verify market_news content_hash unique constraint is single-column."""

    def test_content_hash_unique_constraint_is_single_column(self):
        constraint_col_sets = []
        for constraint in MarketNews.__table__.constraints:
            if isinstance(constraint, sa.UniqueConstraint):
                col_names = tuple(c.name for c in constraint.columns)
                constraint_col_sets.append(col_names)

        has_single_hash = any(cols == ("content_hash",) for cols in constraint_col_sets)
        has_composite = any("content_hash" in cols and "publish_time" in cols for cols in constraint_col_sets)

        assert has_single_hash, (
            f"Expected single-column UniqueConstraint on content_hash. "
            f"Found constraint column sets: {constraint_col_sets}"
        )
        assert not has_composite, (
            f"Composite UniqueConstraint(content_hash, publish_time) should be replaced "
            f"with single-column UniqueConstraint(content_hash). "
            f"Found: {constraint_col_sets}"
        )
