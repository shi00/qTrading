"""Schema consistency tests between metadata.create_all() and Alembic migrations.

This test suite ensures that:
1. ORM metadata.create_all() creates the same tables as Alembic migrations
2. Both approaches create identical column sets
3. Indexes are correctly created by both approaches

Uses isolated PostgreSQL databases for testing (not SQLite or schema isolation).
Each test creates isolated databases to compare the two approaches.
"""

import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import quote_plus

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from data.persistence.models import Base

EXCLUDED_COLS = {"updated_at", "created_at"}


def _get_pg_connection_params() -> dict:
    """Get PostgreSQL connection parameters from environment or defaults."""
    host = os.environ.get("TEST_DB_HOST", "localhost")
    port = int(os.environ.get("TEST_DB_PORT", "5432"))
    user = os.environ.get("TEST_DB_USER", "postgres")
    password = os.environ.get("TEST_DB_PASSWORD") or os.environ.get("CI_PG_PASSWORD", "")
    return {"host": host, "port": port, "user": user, "password": password}


def _make_alembic_cfg(db_url: str) -> Config:
    """Create Alembic config.

    Note: Uses attributes['database_url'] to pass URL, bypassing ConfigParser
    interpolation which would fail on URLs containing percent-encoded characters.
    """
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    # Use attributes to pass URL directly, avoiding ConfigParser interpolation issues
    # with special characters like '%40' (URL-encoded '@')
    cfg.attributes["database_url"] = db_url
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.attributes["configure_logger"] = False
    return cfg


def _get_table_columns(engine, table_name: str) -> set[str]:
    """Get column names for a table."""
    inspector = inspect(engine)
    return {col["name"] for col in inspector.get_columns(table_name)}


def _get_table_names(engine) -> set[str]:
    """Get table names."""
    inspector = inspect(engine)
    return set(inspector.get_table_names())


def _get_index_names(engine, table_name: str) -> set[str]:
    """Get index names for a table."""
    inspector = inspect(engine)
    return {idx["name"] for idx in inspector.get_indexes(table_name) if idx["name"]}


@pytest.fixture
def pg_params():
    """Provide PostgreSQL connection parameters."""
    return _get_pg_connection_params()


@pytest.fixture
def metadata_db_engine(pg_params):
    """Create an isolated database and initialize with metadata.create_all()."""
    db_name = f"meta_test_{uuid.uuid4().hex[:8]}"
    params = pg_params

    async def _create_db():
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        await conn.close()

    asyncio.run(_create_db())

    # Create engine and tables
    db_url = f"postgresql://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{db_name}"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    yield engine, db_name

    # Cleanup: drop database
    async def _drop_db():
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.close()

    asyncio.run(_drop_db())
    engine.dispose()


@pytest.fixture
def alembic_db_engine(pg_params):
    """Create an isolated database and initialize with Alembic migrations."""
    db_name = f"alembic_test_{uuid.uuid4().hex[:8]}"
    params = pg_params

    async def _create_db():
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        await conn.close()

    asyncio.run(_create_db())

    # Create engine and run Alembic migration
    # URL-encode password to handle special characters like '@', ':', '/' etc.
    encoded_pwd = quote_plus(params["password"]) if params.get("password") else ""
    db_url = f"postgresql://{params['user']}:{encoded_pwd}@{params['host']}:{params['port']}/{db_name}"
    engine = create_engine(db_url)

    # Set DATABASE_URL environment variable so Alembic env.py picks it up
    original_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url

    # Also override config.DB_URL
    import config

    original_config_db_url = config.DB_URL
    config.DB_URL = db_url

    cfg = _make_alembic_cfg(db_url)
    command.upgrade(cfg, "head")

    # Restore original values
    config.DB_URL = original_config_db_url
    if original_db_url is not None:
        os.environ["DATABASE_URL"] = original_db_url
    elif "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]

    yield engine, db_name

    # Cleanup: drop database
    async def _drop_db():
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        await conn.close()

    asyncio.run(_drop_db())
    engine.dispose()


class TestMetadataVsAlembicConsistency:
    """Verify that metadata.create_all() produces the same schema as Alembic."""

    def test_both_approaches_create_same_tables(self, metadata_db_engine, alembic_db_engine):
        """Verify both approaches create the same set of tables."""
        meta_engine, _ = metadata_db_engine
        alembic_engine, _ = alembic_db_engine

        meta_tables = _get_table_names(meta_engine)
        alembic_tables = _get_table_names(alembic_engine)

        # Exclude alembic_version table from comparison
        alembic_tables.discard("alembic_version")

        missing_in_meta = alembic_tables - meta_tables
        extra_in_meta = meta_tables - alembic_tables

        assert not missing_in_meta, f"Metadata missing tables: {missing_in_meta}"
        assert not extra_in_meta, f"Metadata has extra tables: {extra_in_meta}"

    # 从 Base.metadata 动态生成参数化列表，确保全覆盖
    @pytest.mark.parametrize(
        "table_name",
        sorted(Base.metadata.tables.keys()),
    )
    def test_both_approaches_create_same_columns(self, metadata_db_engine, alembic_db_engine, table_name):
        """Verify both approaches create the same columns for key tables."""
        meta_engine, _ = metadata_db_engine
        alembic_engine, _ = alembic_db_engine

        meta_cols = _get_table_columns(meta_engine, table_name) - EXCLUDED_COLS
        alembic_cols = _get_table_columns(alembic_engine, table_name) - EXCLUDED_COLS

        missing_in_meta = alembic_cols - meta_cols
        extra_in_meta = meta_cols - alembic_cols

        assert not missing_in_meta, f"Metadata missing columns in {table_name}: {missing_in_meta}"
        assert not extra_in_meta, f"Metadata has extra columns in {table_name}: {extra_in_meta}"

    def test_screening_history_indexes(self, metadata_db_engine, alembic_db_engine):
        """Verify key indexes exist in both approaches."""
        meta_engine, _ = metadata_db_engine
        alembic_engine, _ = alembic_db_engine

        meta_indexes = _get_index_names(meta_engine, "screening_history")
        alembic_indexes = _get_index_names(alembic_engine, "screening_history")

        # Check key indexes exist
        key_indexes = {"idx_sh_date_strategy", "idx_sh_date_code", "idx_sh_run_id"}
        for idx in key_indexes:
            assert idx in meta_indexes, f"Metadata missing index: {idx}"
            assert idx in alembic_indexes, f"Alembic missing index: {idx}"

    def test_jsonb_columns_exist(self, metadata_db_engine, alembic_db_engine):
        """Verify JSONB columns are created correctly in both approaches."""
        meta_engine, _ = metadata_db_engine
        alembic_engine, _ = alembic_db_engine

        # Check screening_history.params_snapshot exists
        meta_cols = _get_table_columns(meta_engine, "screening_history")
        assert "params_snapshot" in meta_cols, "params_snapshot column missing in metadata"

        alembic_cols = _get_table_columns(alembic_engine, "screening_history")
        assert "params_snapshot" in alembic_cols, "params_snapshot column missing in Alembic"

        # Check backtest_results JSONB columns
        for col in ["params_snapshot", "nav_curve_json", "trades_json", "period_stats_json"]:
            assert col in _get_table_columns(meta_engine, "backtest_results"), (
                f"{col} missing in backtest_results (metadata)"
            )
            assert col in _get_table_columns(alembic_engine, "backtest_results"), (
                f"{col} missing in backtest_results (Alembic)"
            )


class TestFreshDatabaseInitialization:
    """Test the fresh database initialization flow using PostgreSQL."""

    @pytest.mark.asyncio
    async def test_init_fresh_database_with_real_engine(self, test_engine):
        """Integration test: verify _init_fresh_database creates correct schema."""
        from data.persistence.db_migrator import DatabaseMigrator
        from sqlalchemy import text

        # Create a fresh isolated database for this test
        params = _get_pg_connection_params()
        db_name = f"fresh_init_{uuid.uuid4().hex[:8]}"

        # Create database
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
        )
        await conn.execute(f'CREATE DATABASE "{db_name}"')
        await conn.close()

        try:
            # Create async engine for the new database
            from sqlalchemy.ext.asyncio import create_async_engine

            db_url = f"postgresql+asyncpg://{params['user']}:{params['password']}@{params['host']}:{params['port']}/{db_name}"
            schema_engine = create_async_engine(db_url)

            # Initialize fresh database via init_db (handles fresh DB detection internally)
            await DatabaseMigrator.init_db(schema_engine, auto_migrate=True)

            # Get expected head revision
            head_rev = await DatabaseMigrator._get_head_revision()

            # Verify alembic_version table exists with correct version
            async with schema_engine.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                row = result.fetchone()
                assert row is not None
                assert row[0] == head_rev, f"Expected version {head_rev}, got {row[0]}"

            # Verify key tables exist
            async with schema_engine.connect() as conn:
                tables_result = await conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
                )
                tables = {row[0] for row in tables_result}

            assert "stock_basic" in tables
            assert "daily_quotes" in tables
            assert "financial_reports" in tables
            assert "screening_history" in tables
            assert "alembic_version" in tables

            await schema_engine.dispose()

        finally:
            # Cleanup: drop database
            conn = await asyncpg.connect(
                host=params["host"],
                port=params["port"],
                user=params["user"],
                password=params["password"],
                database="postgres",
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            await conn.close()
