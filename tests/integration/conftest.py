import hashlib
import os

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from data.persistence.db_url_override import override_db_url

TEST_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
TEST_DB_USER = os.environ.get("TEST_DB_USER", "postgres")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD") or os.environ.get("CI_PG_PASSWORD")
if not TEST_DB_PASSWORD:
    _run_id = os.environ.get("GITHUB_RUN_ID", "")
    if _run_id:
        TEST_DB_PASSWORD = hashlib.sha256(f"astock_ci_{_run_id}".encode()).hexdigest()[:24]
    else:
        import getpass

        try:
            _local_user = getpass.getuser()
        except (OSError, KeyError):
            _local_user = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
        TEST_DB_PASSWORD = hashlib.sha256(f"astock_local_{_local_user}".encode()).hexdigest()[:24]
    import warnings

    warnings.warn(
        "Using derived test DB password. Set TEST_DB_PASSWORD or CI_PG_PASSWORD env var for production CI.",
        UserWarning,
        stacklevel=2,
    )

_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", f"test_astock_{_xdist_worker}" if _xdist_worker else "test_astock")
if _xdist_worker and _xdist_worker not in TEST_DB_NAME:
    TEST_DB_NAME = f"{TEST_DB_NAME}_{_xdist_worker}"
if not TEST_DB_NAME.startswith("test_"):
    raise ValueError(f"TEST_DB_NAME must start with 'test_' for safety, got: {TEST_DB_NAME!r}")
if not TEST_DB_NAME.replace("_", "").isalnum():
    raise ValueError("TEST_DB_NAME must contain only letters, digits, and underscores")
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
if TEST_DB_HOST not in _ALLOWED_HOSTS:
    raise ValueError(f"TEST_DB_HOST must be one of {_ALLOWED_HOSTS} for safety, got: {TEST_DB_HOST!r}")

TEST_DB_URL = f"postgresql+asyncpg://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"


def pytest_collection_modifyitems(items):
    for item in items:
        if not any(marker.name in ("unit", "integration", "e2e") for marker in item.iter_markers()):
            item.add_marker(pytest.mark.integration)


_test_engine: AsyncEngine | None = None
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
        db_name_sql = TEST_DB_NAME.replace('"', '""')
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db_name_sql}"')
        _test_db_initialized = True
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_engine():
    global _test_engine

    if _test_engine is None:
        await _ensure_test_db()

        _test_engine = create_async_engine(TEST_DB_URL, echo=False)

        from data.persistence.db_migrator import DatabaseMigrator

        with override_db_url(TEST_DB_URL):
            await DatabaseMigrator.init_db(_test_engine, auto_migrate=True)

    yield _test_engine

    try:
        if _test_engine is not None:
            await _test_engine.dispose()
    finally:
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
                db_name_sql = TEST_DB_NAME.replace('"', '""')
                await conn.execute(f'DROP DATABASE IF EXISTS "{db_name_sql}" WITH (FORCE)')
            finally:
                await conn.close()
        except (OSError, asyncpg.PostgresError):
            pass


@pytest_asyncio.fixture
async def db_connection(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        txn = await conn.begin()
        nested = await conn.begin_nested()
        yield conn
        if nested.is_active:
            await nested.rollback()
        await txn.rollback()


@pytest_asyncio.fixture
async def db_transaction(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        txn = await conn.begin()
        yield conn
        await txn.rollback()


@pytest.fixture(autouse=True)
def _reset_thread_pool():
    from utils.thread_pool import ThreadPoolManager

    ThreadPoolManager._reset_singleton()
    yield
    ThreadPoolManager._reset_singleton()


@pytest_asyncio.fixture(autouse=True)
async def db_schema_ready(test_engine):
    from data.persistence.db_migrator import DatabaseMigrator

    with override_db_url(TEST_DB_URL):
        await DatabaseMigrator.init_db(test_engine, auto_migrate=True)
