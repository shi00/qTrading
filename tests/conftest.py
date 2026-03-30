import os
import sys
import tempfile

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

TEST_DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT = int(os.environ.get("TEST_DB_PORT", "5432"))
TEST_DB_USER = os.environ.get("TEST_DB_USER", "postgres")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "123456")
TEST_DB_NAME = "test_astock"

TEST_DB_URL = f"postgresql+asyncpg://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
TEST_DB_SYNC_URL = f"postgresql://{TEST_DB_USER}:{TEST_DB_PASSWORD}@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"

_temp_config_dir = tempfile.mkdtemp(prefix="astock_test_config_")
_temp_config_file = os.path.join(_temp_config_dir, "test_user_settings.json")


def pytest_configure(config):
    """
    Hook that runs before any test collection or import.
    Patch CONFIG_FILE and DATABASE_URL before any modules are imported.
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    os.environ["DATABASE_URL"] = TEST_DB_URL

    import utils.config_handler

    utils.config_handler.CONFIG_FILE = _temp_config_file


def pytest_unconfigure(config):
    """
    Hook that runs after all tests complete.
    Cleanup temp config directory.
    """
    import shutil

    if os.path.exists(_temp_config_dir):
        shutil.rmtree(_temp_config_dir, ignore_errors=True)


@pytest.fixture(autouse=True, scope="session")
def isolate_config_file():
    """
    Ensure config isolation is active throughout the test session.
    The actual patching is done in pytest_configure for early interception.
    """
    yield _temp_config_file


_test_engine = None
_test_db_initialized = False


async def _ensure_test_db():
    """Ensure test database exists - called lazily"""
    global _test_db_initialized
    if _test_db_initialized:
        return

    conn = await asyncpg.connect(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        user=TEST_DB_USER,
        password=TEST_DB_PASSWORD,
        database="postgres",
        timeout=5.0,  # type: ignore
    )
    try:
        # 强制断开其他连接，防止 ObjectInUseError
        await conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = 'test_astock'
              AND pid <> pg_backend_pid();
            """
        )
        await conn.execute("DROP DATABASE IF EXISTS test_astock")
        await conn.execute("CREATE DATABASE test_astock")
        _test_db_initialized = True
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Session-scoped test database engine - lazily initialized"""
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


@pytest_asyncio.fixture
async def db_connection(test_engine: AsyncEngine):
    """Connection with automatic rollback for test isolation"""
    async with test_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture
async def db_transaction(test_engine: AsyncEngine):
    """Transaction fixture with automatic rollback"""
    async with test_engine.begin() as conn:
        yield conn
