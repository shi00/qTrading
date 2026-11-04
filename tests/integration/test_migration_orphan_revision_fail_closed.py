"""Fail-closed integration tests for unverifiable ``alembic_version`` states.

Kept in a dedicated module so the shared ``test_data_db_migrator.py`` only
carries in-place updates to existing tests, avoiding merge conflicts with
other migration-fix branches. These tests require a real PostgreSQL and are
not run in sandboxes without one.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.db_migrator import DatabaseMigrator, OrphanedRevisionError
from data.persistence.db_url_override import override_db_url
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params, make_alembic_cfg
from tests.integration.test_data_db_migrator import _create_isolated_db, _drop_isolated_db

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_params():
    """Provide PostgreSQL connection parameters."""
    return get_pg_connection_params()


async def _upgrade_isolated_db_to(sync_url: str, target_revision: str) -> None:
    """Upgrade an isolated database to a specific revision (never head).

    ``sync_url`` must come from ``build_db_urls``: deriving it from
    ``str(engine.url)`` masks the password (``***``) and breaks authentication.
    """
    with override_db_url(sync_url):
        cfg = make_alembic_cfg(sync_url)
        await asyncio.to_thread(command.upgrade, cfg, target_revision)


async def _fetch_version_rows(engine: AsyncEngine) -> list[str]:
    """Read every row of alembic_version for side-effect assertions."""
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        return [row[0] for row in result.fetchall()]


async def _fetch_table_names(engine: AsyncEngine) -> set[str]:
    """Read the public schema table set for side-effect assertions."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        return {row[0] for row in result.fetchall()}


class TestOrphanedRevisionFailClosed:
    """Unverifiable alembic_version states must stop startup without side effects."""

    @pytest_asyncio.fixture(loop_scope="function")
    async def orphaned_test_db(self, pg_params):
        """Yield ``(async_engine, sync_url)`` backed by an isolated database."""
        db_name = f"migrator_orphaned_{uuid.uuid4().hex[:8]}"
        params = pg_params
        await _create_isolated_db(params, db_name)
        engine: AsyncEngine | None = None
        try:
            sync_url, async_url = build_db_urls(params, db_name)
            engine = create_test_engine(async_url)
            yield engine, sync_url
        finally:
            if engine is not None:
                await engine.dispose()
            await _drop_isolated_db(params, db_name)

    @staticmethod
    async def _set_version_rows(engine: AsyncEngine, rows: list[str]) -> None:
        """Replace alembic_version contents with the supplied rows."""
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE alembic_version SET version_num = :first_revision"),
                {"first_revision": rows[0]},
            )
            for revision in rows[1:]:
                await conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                    {"revision": revision},
                )

    async def test_orphaned_revision_fails_closed_and_preserves_cursor(
        self, orphaned_test_db: tuple[AsyncEngine, str]
    ) -> None:
        """Head schema + unknown cursor: raise and keep both cursor and schema unchanged."""
        engine, _sync_url = orphaned_test_db
        await DatabaseMigrator.init_db(engine, auto_migrate=True)
        head_rev = await DatabaseMigrator._get_head_revision()
        tables_before = await _fetch_table_names(engine)
        await self._set_version_rows(engine, ["9999_orphaned"])

        with pytest.raises(OrphanedRevisionError) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        assert exc_info.value.current_rev == "9999_orphaned"
        assert exc_info.value.head_rev == head_rev
        assert await _fetch_version_rows(engine) == ["9999_orphaned"]
        assert await _fetch_table_names(engine) == tables_before

    async def test_old_schema_with_unknown_cursor_fails_closed(self, orphaned_test_db: tuple[AsyncEngine, str]) -> None:
        """Old schema + unknown cursor: raise instead of stamping the old schema to head."""
        engine, sync_url = orphaned_test_db
        await _upgrade_isolated_db_to(sync_url, "0016")
        head_rev = await DatabaseMigrator._get_head_revision()
        tables_before = await _fetch_table_names(engine)
        assert "news_risk_brief" not in tables_before, "0028+ tables must not exist at revision 0016"
        await self._set_version_rows(engine, ["9999_orphaned"])

        with pytest.raises(OrphanedRevisionError) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        assert exc_info.value.current_rev == "9999_orphaned"
        assert exc_info.value.head_rev == head_rev
        assert await _fetch_version_rows(engine) == ["9999_orphaned"]
        assert await _fetch_table_names(engine) == tables_before
        assert "news_risk_brief" not in await _fetch_table_names(engine)

    async def test_orphan_fails_closed_when_auto_migrate_disabled(
        self, orphaned_test_db: tuple[AsyncEngine, str]
    ) -> None:
        """Orphan detection must raise before the auto_migrate flag is evaluated."""
        engine, sync_url = orphaned_test_db
        await _upgrade_isolated_db_to(sync_url, "0016")
        await self._set_version_rows(engine, ["9999_orphaned"])

        with pytest.raises(OrphanedRevisionError) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=False)

        assert exc_info.value.current_rev == "9999_orphaned"
        assert await _fetch_version_rows(engine) == ["9999_orphaned"]

    async def test_multiple_version_rows_fail_closed(self, orphaned_test_db: tuple[AsyncEngine, str]) -> None:
        """Two rows, one unknown: raise and preserve all rows and schema."""
        engine, _sync_url = orphaned_test_db
        await DatabaseMigrator.init_db(engine, auto_migrate=True)
        tables_before = await _fetch_table_names(engine)
        await self._set_version_rows(engine, ["9999_orphaned", "0043"])

        with pytest.raises(OrphanedRevisionError) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        assert set(exc_info.value.version_rows) == {"9999_orphaned", "0043"}
        assert set(await _fetch_version_rows(engine)) == {"9999_orphaned", "0043"}
        assert await _fetch_table_names(engine) == tables_before

    async def test_multiple_known_version_rows_fail_closed(self, orphaned_test_db: tuple[AsyncEngine, str]) -> None:
        """Two known rows are ambiguous in a single-head chain: raise without guessing."""
        engine, _sync_url = orphaned_test_db
        await DatabaseMigrator.init_db(engine, auto_migrate=True)
        head_rev = await DatabaseMigrator._get_head_revision()
        await self._set_version_rows(engine, ["0043", head_rev])

        with pytest.raises(OrphanedRevisionError) as exc_info:
            await DatabaseMigrator.init_db(engine, auto_migrate=True)

        assert "多行" in exc_info.value.reason
        assert set(await _fetch_version_rows(engine)) == {"0043", head_rev}

    async def test_assert_no_orphaned_revision_fresh_db_no_op(self, orphaned_test_db: tuple[AsyncEngine, str]) -> None:
        """Fresh database (no alembic_version) is a no-op."""
        engine, _sync_url = orphaned_test_db
        await DatabaseMigrator._assert_no_orphaned_revision(engine)
        current_rev = await DatabaseMigrator._get_current_revision(engine)
        assert current_rev is None

    async def test_assert_no_orphaned_revision_valid_revision_no_op(
        self, orphaned_test_db: tuple[AsyncEngine, str]
    ) -> None:
        """A single known revision passes without writes."""
        engine, _sync_url = orphaned_test_db
        await DatabaseMigrator.init_db(engine, auto_migrate=True)
        head_rev = await DatabaseMigrator._get_head_revision()

        await DatabaseMigrator._assert_no_orphaned_revision(engine)

        assert await _fetch_version_rows(engine) == [head_rev]
