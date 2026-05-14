import os

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import (
    TEST_DB_HOST,
    TEST_DB_NAME,
    TEST_DB_PASSWORD,
    TEST_DB_PORT,
    TEST_DB_URL,
    TEST_DB_USER,
)


def pytest_collection_modifyitems(items):
    for item in items:
        if not any(marker.name in ("unit", "integration", "e2e") for marker in item.iter_markers()):
            item.add_marker(pytest.mark.integration)


_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "")

_test_engine = None
_test_db_initialized = False


async def _ensure_test_db():
    global _test_db_initialized
    if _test_db_initialized:
        return

    conn = await asyncpg.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        user=TEST_DB_USER,
        password=TEST_DB_PASSWORD,
        database="postgres",
        timeout=5.0,
    )
    try:
        existing = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            TEST_DB_NAME,
        )
        if existing:
            _test_db_initialized = True
            return

        db_name_sql = TEST_DB_NAME.replace('"', '""')
        await conn.execute(f'CREATE DATABASE "{db_name_sql}"')
        _test_db_initialized = True
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    global _test_engine

    if _test_engine is None:
        await _ensure_test_db()

        _test_engine = create_async_engine(TEST_DB_URL, echo=False)

        from data.persistence.models import Base

        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    yield _test_engine

    if _test_engine is not None:
        await _test_engine.dispose()
        _test_engine = None

    if _xdist_worker:
        try:
            conn = await asyncpg.connect(
                host=TEST_DB_HOST,
                port=TEST_DB_PORT,
                user=TEST_DB_USER,
                password=TEST_DB_PASSWORD,
                database="postgres",
                timeout=5.0,
            )
            try:
                await conn.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = $1
                      AND pid <> pg_backend_pid();
                    """,
                    TEST_DB_NAME,
                )
                db_name_sql = TEST_DB_NAME.replace('"', '""')
                await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}"')
            finally:
                await conn.close()
        except OSError, asyncpg.PostgresError:
            pass


@pytest_asyncio.fixture
async def db_connection(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture
async def db_transaction(test_engine: AsyncEngine):
    async with test_engine.begin() as conn:
        yield conn
