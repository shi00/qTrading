"""Opt-in PostgreSQL tests for the cross-process migration mutex.

The lock lives in the database (session-level advisory lock), so proving it
works needs a real PostgreSQL. These tests are skipped unless
``RUN_PG_MIGRATION_LOCK_TESTS=1`` is set *and* the shared integration-test
PostgreSQL (``TEST_DB_*`` env vars, see ``tests/integration/_db_config.py``) is
reachable; sandboxes without PostgreSQL report an explicit skip.
"""

import os

import pytest

if os.environ.get("RUN_PG_MIGRATION_LOCK_TESTS") != "1":
    pytest.skip(
        "opt-in PostgreSQL test: set RUN_PG_MIGRATION_LOCK_TESTS=1 with a reachable PostgreSQL",
        allow_module_level=True,
    )

import asyncio
import uuid

import pytest_asyncio
from alembic import command
from sqlalchemy import text

from data.persistence.db_migrator import DatabaseMigrator
from data.persistence.migration_lock import (
    MigrationLockTimeoutError,
    acquire_migration_lock,
    release_migration_lock,
)
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg
from tests.integration.test_data_db_migrator import _create_isolated_db, _drop_isolated_db

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_params():
    """PostgreSQL connection parameters shared by integration tests."""
    return get_pg_connection_params()


@pytest_asyncio.fixture(loop_scope="function")
async def isolated_db(pg_params):
    """Yield ``(sync_url, async_url)`` for a throwaway database."""
    db_name = f"migrator_lock_{uuid.uuid4().hex[:8]}"
    await _create_isolated_db(pg_params, db_name)
    try:
        sync_url, async_url = build_db_urls(pg_params, db_name)
        yield sync_url, async_url
    finally:
        await _drop_isolated_db(pg_params, db_name)


class TestAdvisoryLockSerialisation:
    """The lock is owned by a database session, which is what makes it cross-process."""

    @pytest.mark.asyncio
    async def test_second_session_is_rejected_and_can_acquire_after_release(self, isolated_db):
        _sync_url, async_url = isolated_db
        engine = create_test_engine(async_url)
        first = await engine.connect()
        second = await engine.connect()
        try:
            await acquire_migration_lock(first, timeout_s=0)
            with pytest.raises(MigrationLockTimeoutError):
                await acquire_migration_lock(second, timeout_s=0)

            await release_migration_lock(first)
            await acquire_migration_lock(second, timeout_s=0)
            await release_migration_lock(second)
        finally:
            await first.close()
            await second.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_closing_the_connection_drops_the_lock(self, isolated_db):
        """``alembic/env.py`` relies on connection close releasing the lock."""
        _sync_url, async_url = isolated_db
        engine = create_test_engine(async_url)
        try:
            first = await engine.connect()
            await acquire_migration_lock(first, timeout_s=0)
            await first.close()

            second = await engine.connect()
            try:
                await acquire_migration_lock(second, timeout_s=0)
                await release_migration_lock(second)
            finally:
                await second.close()
        finally:
            await engine.dispose()


class TestConcurrentAlembicUpgrade:
    """Two concurrent upgrade runs must serialise instead of replaying revisions."""

    @pytest.mark.asyncio
    async def test_concurrent_upgrades_reach_a_single_head(self, isolated_db):
        sync_url, async_url = isolated_db
        head_rev = await DatabaseMigrator._get_head_revision()

        cfg_a = make_alembic_cfg(sync_url)
        cfg_b = make_alembic_cfg(sync_url)
        await asyncio.gather(
            asyncio.to_thread(command.upgrade, cfg_a, "head"),
            asyncio.to_thread(command.upgrade, cfg_b, "head"),
        )

        engine = create_test_engine(async_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                rows = [row[0] for row in result.fetchall()]
        finally:
            await engine.dispose()

        assert rows == [head_rev]
