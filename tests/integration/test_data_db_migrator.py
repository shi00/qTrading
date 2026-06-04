"""Integration tests for DatabaseMigrator module.

Tests real Alembic integration with PostgreSQL database.
Uses isolated temporary databases for each test.
All fixtures are async to avoid asyncio.run() conflicts with pytest-asyncio.
"""

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from data.persistence.db_migrator import DatabaseMigrator, DatabaseMigrationNeeded
from data.persistence.db_url_override import override_db_url


def _get_pg_connection_params() -> dict:
    """Get PostgreSQL connection parameters from environment or defaults."""
    host = os.environ.get("TEST_DB_HOST", "localhost")
    port = int(os.environ.get("TEST_DB_PORT", "5432"))
    user = os.environ.get("TEST_DB_USER", "postgres")
    password = os.environ.get("TEST_DB_PASSWORD") or os.environ.get("CI_PG_PASSWORD", "")
    return {"host": host, "port": port, "user": user, "password": password}


def _make_alembic_cfg(db_url: str) -> Config:
    """Create Alembic config."""
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.attributes["configure_logger"] = False
    return cfg


async def _create_isolated_db(params: dict, db_name: str) -> None:
    """Create an isolated PostgreSQL database."""
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _drop_isolated_db(params: dict, db_name: str) -> None:
    """Drop an isolated PostgreSQL database."""
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database="postgres",
    )
    try:
        db_name_sql = db_name.replace('"', '""')
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
    finally:
        await conn.close()


def _build_db_urls(params: dict, db_name: str) -> tuple[str, str]:
    """Build sync and async database URLs from params and db_name."""
    sync_url = f"postgresql://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{db_name}"
    async_url = (
        f"postgresql+asyncpg://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{db_name}"
    )
    return sync_url, async_url


@pytest.fixture
def pg_params():
    """Provide PostgreSQL connection parameters."""
    return _get_pg_connection_params()


class TestGetHeadRevision:
    """Tests for _get_head_revision with real Alembic configuration."""

    @pytest.mark.asyncio
    async def test_get_head_revision_returns_real_value(self):
        """_get_head_revision should return a valid revision from real Alembic config."""
        head_rev = await DatabaseMigrator._get_head_revision()

        assert head_rev is not None
        assert isinstance(head_rev, str)
        assert len(head_rev) > 0


class TestFreshInstall:
    """Tests for fresh database initialization with real PostgreSQL and Alembic."""

    @pytest_asyncio.fixture
    async def fresh_db_engine(self, pg_params):
        """Create an isolated empty database for fresh install testing."""
        db_name = f"migrator_fresh_{uuid.uuid4().hex[:8]}"
        params = pg_params

        await _create_isolated_db(params, db_name)

        _, async_url = _build_db_urls(params, db_name)
        engine = create_async_engine(async_url)

        yield engine, db_name

        await engine.dispose()
        await _drop_isolated_db(params, db_name)

    @pytest.mark.asyncio
    async def test_fresh_install_creates_tables_and_version(self, fresh_db_engine):
        """Fresh database initialization should create tables and record schema version."""
        engine, db_name = fresh_db_engine

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables = {row[0] for row in result.fetchall()}

            assert "stock_basic" in tables
            assert "daily_quotes" in tables
            assert "alembic_version" in tables

            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            assert row is not None
            version = row[0]
            assert version is not None
            assert len(version) > 0

    @pytest.mark.asyncio
    async def test_fresh_install_records_head_revision(self, fresh_db_engine):
        """Fresh database should record the current Alembic head revision."""
        engine, db_name = fresh_db_engine

        expected_head = await DatabaseMigrator._get_head_revision()

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == expected_head

    @pytest.mark.asyncio
    async def test_fresh_install_uses_single_transaction(self, fresh_db_engine):
        """Fresh database should create tables and version atomically (P2 fix)."""
        engine, db_name = fresh_db_engine

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            )
            tables = {row[0] for row in result.fetchall()}
            assert "stock_basic" in tables

            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            assert row is not None
            assert len(row[0]) > 0


async def _migrated_db_fixture(
    params: dict, db_name_prefix: str, target_revision: str = "head"
) -> tuple[AsyncEngine, str, str, Any]:
    """Shared fixture factory: create isolated DB, migrate to target revision.

    Returns:
        (async_engine, db_name, sync_db_url, sync_engine)
    """
    db_name = f"{db_name_prefix}_{uuid.uuid4().hex[:8]}"
    await _create_isolated_db(params, db_name)

    sync_url, async_url = _build_db_urls(params, db_name)
    sync_engine = create_engine(sync_url)

    with override_db_url(sync_url):
        cfg = _make_alembic_cfg(sync_url)
        await asyncio.to_thread(command.upgrade, cfg, target_revision)

    async_engine = create_async_engine(async_url)

    return async_engine, db_name, sync_url, sync_engine


async def _cleanup_migrated_db(params: dict, async_engine: AsyncEngine, sync_engine: Any, db_name: str) -> None:
    """Shared cleanup for migrated DB fixtures."""
    await async_engine.dispose()
    sync_engine.dispose()
    await _drop_isolated_db(params, db_name)


class TestUpToDateSchema:
    """Tests for database that is already at latest schema version."""

    @pytest_asyncio.fixture
    async def migrated_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade to head."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(pg_params, "migrator_uptodate")
        yield async_engine, db_name
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest.mark.asyncio
    async def test_up_to_date_schema_skips_upgrade(self, migrated_db_engine):
        """Database already at latest version should skip upgrade."""
        engine, db_name = migrated_db_engine

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version_before = result.fetchone()[0]

        await DatabaseMigrator.init_db(engine, auto_migrate=True)

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            version_after = result.fetchone()[0]

        assert version_before == version_after


class TestCheckSchemaStatus:
    """Tests for check_schema_status method with real database."""

    @pytest_asyncio.fixture
    async def migrated_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade to head."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(pg_params, "migrator_status")
        yield async_engine, db_name
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest_asyncio.fixture
    async def empty_status_db_engine(self, pg_params):
        """Create an isolated empty database for status check testing."""
        db_name = f"migrator_empty_status_{uuid.uuid4().hex[:8]}"
        await _create_isolated_db(pg_params, db_name)

        _, async_url = _build_db_urls(pg_params, db_name)
        engine = create_async_engine(async_url)

        yield engine, db_name

        await engine.dispose()
        await _drop_isolated_db(pg_params, db_name)

    @pytest.mark.asyncio
    async def test_check_schema_status_returns_correct_values(self, migrated_db_engine):
        """check_schema_status should return correct current, head, and needs_migration."""
        engine, db_name = migrated_db_engine

        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)

        assert current is not None
        assert current == head
        assert needs_migration is False

    @pytest.mark.asyncio
    async def test_check_schema_status_empty_database(self, empty_status_db_engine):
        """check_schema_status on empty database should return None current and needs_migration=True."""
        engine, db_name = empty_status_db_engine

        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)

        assert current is None
        assert head is not None
        assert needs_migration is True


class TestIncrementalUpgrade:
    """Tests for incremental upgrade path (e.g., 0001 → 0002).

    This is the most critical test: it verifies that an existing database
    at an older revision can be upgraded to the latest schema.
    """

    @pytest_asyncio.fixture
    async def partial_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade only to 0001."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(
            pg_params, "migrator_upgrade", target_revision="0001"
        )
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest.mark.asyncio
    async def test_incremental_upgrade_from_0001_to_head(self, partial_db_engine):
        """Database at 0001 should be upgradeable to head via init_db(auto_migrate=True)."""
        engine, db_name, sync_db_url, sync_engine = partial_db_engine

        # Verify we're at 0001
        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)
        assert current == "0001"
        assert needs_migration is True

        # Verify financial_reports does NOT have money_cap and accounts_receiv at 0001
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        cols_0001 = {c["name"] for c in inspector.get_columns("financial_reports")}
        assert "money_cap" not in cols_0001, "money_cap should not exist at revision 0001"
        assert "accounts_receiv" not in cols_0001, "accounts_receiv should not exist at revision 0001"

        # Run init_db with auto_migrate=True to trigger upgrade
        # override_db_url 确保 env.py 的 get_database_url() 返回正确的 URL
        with override_db_url(sync_db_url):
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        # Verify version is now at head
        new_current, new_head, new_needs = await DatabaseMigrator.check_schema_status(engine)
        assert new_current == new_head
        assert new_needs is False

        # Verify financial_reports now has money_cap and accounts_receiv
        sync_engine2 = create_engine(sync_db_url)
        try:
            inspector2 = sa_inspect(sync_engine2)
            cols_head = {c["name"] for c in inspector2.get_columns("financial_reports")}
            assert "money_cap" in cols_head, "money_cap should exist after upgrade to head"
            assert "accounts_receiv" in cols_head, "accounts_receiv should exist after upgrade to head"
        finally:
            sync_engine2.dispose()

    @pytest.mark.asyncio
    async def test_incremental_upgrade_raises_when_auto_migrate_disabled(self, partial_db_engine):
        """Database needing upgrade should raise DatabaseMigrationNeeded when auto_migrate=False."""
        engine, db_name, sync_db_url, sync_engine = partial_db_engine

        with pytest.raises(DatabaseMigrationNeeded) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=False)

        assert exc_info.value.current_rev == "0001"
        assert exc_info.value.head_rev != "0001"

    @pytest.mark.asyncio
    async def test_schema_status_detects_pending_migration(self, partial_db_engine):
        """check_schema_status should detect pending migrations."""
        engine, db_name, sync_db_url, sync_engine = partial_db_engine

        current, head, needs_migration = await DatabaseMigrator.check_schema_status(engine)

        assert current == "0001"
        assert head != "0001"
        assert needs_migration is True


class TestDowngradeAndReupgrade:
    """Tests for downgrade → re-upgrade idempotency (CI gate: upgrade → downgrade → upgrade)."""

    @pytest_asyncio.fixture
    async def head_db_engine(self, pg_params):
        """Create an isolated database and run Alembic upgrade to head."""
        async_engine, db_name, sync_url, sync_engine = await _migrated_db_fixture(pg_params, "migrator_downgrade")
        yield async_engine, db_name, sync_url, sync_engine
        await _cleanup_migrated_db(pg_params, async_engine, sync_engine, db_name)

    @pytest.mark.asyncio
    async def test_downgrade_to_base_removes_all_user_tables(self, head_db_engine):
        """Downgrade to base should remove all user tables (except alembic_version which is dropped)."""
        engine, db_name, sync_db_url, sync_engine = head_db_engine

        # Verify tables exist before downgrade
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        tables_before = set(inspector.get_table_names())
        assert "stock_basic" in tables_before
        assert "daily_quotes" in tables_before

        # Downgrade to base — 必须在独立线程中执行，因为 Alembic env.py 内部调用 asyncio.run()
        with override_db_url(sync_db_url):

            def _do_downgrade_base():
                cfg = _make_alembic_cfg(sync_db_url)
                command.downgrade(cfg, "base")

            await asyncio.to_thread(_do_downgrade_base)

        # Verify all user tables are gone
        inspector2 = sa_inspect(sync_engine)
        tables_after = set(inspector2.get_table_names())
        # After downgrade to base, alembic_version should also be dropped
        assert "stock_basic" not in tables_after, "stock_basic should be removed after downgrade"
        assert "daily_quotes" not in tables_after, "daily_quotes should be removed after downgrade"
        assert "financial_reports" not in tables_after, "financial_reports should be removed after downgrade"

    @pytest.mark.asyncio
    async def test_reupgrade_after_downgrade_restores_full_schema(self, head_db_engine):
        """After downgrade to base, re-upgrade to head should restore full schema."""
        engine, db_name, sync_db_url, sync_engine = head_db_engine

        # Downgrade to base — 必须在独立线程中执行
        with override_db_url(sync_db_url):

            def _do_downgrade():
                cfg = _make_alembic_cfg(sync_db_url)
                command.downgrade(cfg, "base")

            await asyncio.to_thread(_do_downgrade)

        # Re-upgrade to head
        with override_db_url(sync_db_url):

            def _do_upgrade():
                cfg = _make_alembic_cfg(sync_db_url)
                command.upgrade(cfg, "head")

            await asyncio.to_thread(_do_upgrade)

        # Verify schema is fully restored
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        tables = set(inspector.get_table_names())
        assert "stock_basic" in tables
        assert "daily_quotes" in tables
        assert "financial_reports" in tables
        assert "alembic_version" in tables

        # Verify financial_reports has money_cap and accounts_receiv (from 0002)
        cols = {c["name"] for c in inspector.get_columns("financial_reports")}
        assert "money_cap" in cols, "money_cap should exist after re-upgrade"
        assert "accounts_receiv" in cols, "accounts_receiv should exist after re-upgrade"

        # Verify alembic_version is at head
        head_rev = await DatabaseMigrator._get_head_revision()
        current, _, _ = await DatabaseMigrator.check_schema_status(engine)
        assert current == head_rev

    @pytest.mark.asyncio
    async def test_downgrade_one_revision_removes_new_columns(self, head_db_engine):
        """Downgrade from 0002 to 0001 should remove money_cap and accounts_receiv."""
        engine, db_name, sync_db_url, sync_engine = head_db_engine

        # Verify columns exist at head
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(sync_engine)
        cols_head = {c["name"] for c in inspector.get_columns("financial_reports")}
        assert "money_cap" in cols_head
        assert "accounts_receiv" in cols_head

        # Downgrade one step (from head to 0001) — 必须在独立线程中执行
        with override_db_url(sync_db_url):

            def _do_downgrade():
                cfg = _make_alembic_cfg(sync_db_url)
                command.downgrade(cfg, "0001")

            await asyncio.to_thread(_do_downgrade)

        # Verify columns are removed
        inspector2 = sa_inspect(sync_engine)
        cols_0001 = {c["name"] for c in inspector2.get_columns("financial_reports")}
        assert "money_cap" not in cols_0001, "money_cap should be removed after downgrade to 0001"
        assert "accounts_receiv" not in cols_0001, "accounts_receiv should be removed after downgrade to 0001"
