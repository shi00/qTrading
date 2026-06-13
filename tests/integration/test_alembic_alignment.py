"""Alembic schema alignment tests based on real migration execution.

Uses the session-scoped PostgreSQL test database (via test_engine fixture).
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.models import Base

ALL_TABLES = [(name, table) for name, table in Base.metadata.tables.items() if name != "alembic_version"]


async def _get_table_columns(engine: AsyncEngine, table_name: str) -> set[str]:
    """Reflect column names from the PostgreSQL test database via run_sync."""
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns(table_name)}
        )


async def _get_table_indexes(engine: AsyncEngine, table_name: str) -> set[str]:
    """Reflect index names from the PostgreSQL test database via run_sync."""
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: {
                idx["name"] for idx in inspect(sync_conn).get_indexes(table_name) if idx["name"] is not None
            }
        )


class TestAlembicMigrationAlignment:
    """Ensure ORM models match reflected schema after running migrations."""

    @pytest.mark.parametrize("table_name,table", ALL_TABLES)
    async def test_model_matches_reflected_schema(self, test_engine: AsyncEngine, table_name: str, table):
        orm_cols = {c.name for c in table.columns}
        reflected_cols = await _get_table_columns(test_engine, table_name)

        missing_in_db = orm_cols - reflected_cols
        extra_in_db = reflected_cols - orm_cols

        assert not missing_in_db, f"DB missing columns for {table_name}: {missing_in_db}"
        assert not extra_in_db, f"DB has extra columns for {table_name}: {extra_in_db}"

    async def test_screening_history_pending_index_exists(self, test_engine: AsyncEngine):
        idx_names = await _get_table_indexes(test_engine, "screening_history")
        assert "idx_sh_pending" in idx_names
