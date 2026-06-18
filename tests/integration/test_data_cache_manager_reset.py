"""Integration tests for CacheManager.clear_all_cache() → init_db() flow.

Verifies that after clear_all_cache(), all tables are recreated and
alembic_version has a valid version string.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from data.cache.cache_manager import CacheManager
from tests.conftest import singleton_state


@pytest.mark.integration
class TestClearAllCacheReset:
    """Integration tests for the clear_all_cache → init_db round-trip."""

    @pytest_asyncio.fixture(autouse=True)
    async def _db_url_override(self, test_db_url_override):
        """自动应用 DB URL 覆盖（P2-4），避免每个测试方法重复 with override_db_url。"""

    @pytest_asyncio.fixture
    async def cache_mgr(self, test_engine: AsyncEngine):
        """Provide an isolated CacheManager instance wired to the test engine."""
        with singleton_state(CacheManager, extra_attrs=["_initialized"]):
            mgr = CacheManager()
            mgr.engine = test_engine
            mgr._disposed = False
            mgr._schema_initialized = False

            # Wire all DAO engines to the test engine
            for dao_attr in (
                "stock_dao",
                "quote_dao",
                "financial_dao",
                "sync_dao",
                "market_dao",
                "screener_dao",
                "macro_dao",
                "holder_dao",
                "backtest_dao",
            ):
                dao = getattr(mgr, dao_attr, None)
                if dao is not None:
                    dao.engine = test_engine

            await mgr.init_db(auto_migrate=True)

            yield mgr

            try:
                await mgr.close()
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_clear_all_cache_recreates_tables(self, cache_mgr: CacheManager, test_engine: AsyncEngine):
        """After clear_all_cache(), all application tables should exist."""
        await cache_mgr.clear_all_cache()

        # Verify key tables exist by querying them (empty but present)
        async with test_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                )
            )
            tables = {row[0] for row in result.fetchall()}

        # At minimum, these core tables must exist after reset
        expected_tables = {
            "stock_basic",
            "daily_quotes",
            "daily_indicators",
            "financial_reports",
            "alembic_version",
        }
        assert expected_tables.issubset(tables), f"Missing tables after clear_all_cache: {expected_tables - tables}"

    @pytest.mark.asyncio
    async def test_clear_all_cache_has_valid_alembic_version(self, cache_mgr: CacheManager, test_engine: AsyncEngine):
        """After clear_all_cache(), alembic_version should contain a valid revision."""
        await cache_mgr.clear_all_cache()

        async with test_engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            rows = result.fetchall()

        assert len(rows) >= 1, "alembic_version table is empty after clear_all_cache"
        version = rows[0][0]
        assert version is not None, "alembic_version.version_num is NULL"
        assert len(str(version)) > 0, "alembic_version.version_num is empty"

    @pytest.mark.asyncio
    async def test_clear_all_cache_schema_initialized_flag(self, cache_mgr: CacheManager, test_engine: AsyncEngine):
        """After clear_all_cache(), _schema_initialized should be True."""
        await cache_mgr.clear_all_cache()

        assert cache_mgr._schema_initialized is True, "_schema_initialized should be True after clear_all_cache"

    @pytest.mark.asyncio
    async def test_clear_all_cache_idempotent(self, cache_mgr: CacheManager, test_engine: AsyncEngine):
        """Calling clear_all_cache() twice should succeed without errors."""
        await cache_mgr.clear_all_cache()
        await cache_mgr.clear_all_cache()

        async with test_engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            rows = result.fetchall()

        assert len(rows) >= 1, "alembic_version should still have a version after double clear_all_cache"
