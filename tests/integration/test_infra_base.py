import logging
import os

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

from tests.integration.conftest import TEST_DB_URL
from data.cache.cache_manager import CacheManager

TABLE_NAMES = [
    "block_trade",
    "daily_indicators",
    "daily_quotes",
    "dividend",
    "fina_audit",
    "fina_forecast",
    "fina_mainbz",
    "financial_reports",
    "index_daily",
    "index_dailybasic",
    "index_weight",
    "limit_list",
    "macro_economy",
    "margin_daily",
    "market_news",
    "moneyflow_daily",
    "moneyflow_hsgt",
    "northbound_holding",
    "pledge_stat",
    "repurchase",
    "screening_history",
    "screening_thinking",
    "shibor_daily",
    "stk_holdernumber",
    "stock_basic",
    "stock_concepts",
    "stock_sync_status",
    "suspend_d",
    "sync_status",
    "task_history",
    "top10_holders",
    "top_list",
    "trade_cal",
]


async def _truncate_all_tables(engine: AsyncEngine):
    """Truncate all tables for test isolation (fast, no DDL)."""
    tables_str = ", ".join(TABLE_NAMES)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {tables_str} CASCADE"))
        return
    except (OperationalError, ProgrammingError):
        pass
    for table in TABLE_NAMES:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        except Exception as e:
            logger.warning(f"[TestDB] TRUNCATE {table} failed: {e}")


class _AssertionMixin:
    def assertEqual(self, a, b, msg=None):
        assert a == b, msg or f"{a!r} != {b!r}"

    def assertNotEqual(self, a, b, msg=None):
        assert a != b, msg or f"{a!r} == {b!r}"

    def assertTrue(self, x, msg=None):
        assert x, msg or f"{x!r} is not truthy"

    def assertFalse(self, x, msg=None):
        assert not x, msg or f"{x!r} is not falsy"

    def assertIn(self, a, b, msg=None):
        assert a in b, msg or f"{a!r} not in {b!r}"

    def assertNotIn(self, a, b, msg=None):
        assert a not in b, msg or f"{a!r} in {b!r}"

    def assertIsNone(self, x, msg=None):
        assert x is None, msg or f"{x!r} is not None"

    def assertIsNotNone(self, x, msg=None):
        assert x is not None, msg or f"{x!r} is None"

    def assertGreater(self, a, b, msg=None):
        assert a > b, msg or f"{a!r} <= {b!r}"

    def assertGreaterEqual(self, a, b, msg=None):
        assert a >= b, msg or f"{a!r} < {b!r}"

    def assertLess(self, a, b, msg=None):
        assert a < b, msg or f"{a!r} >= {b!r}"

    def assertLessEqual(self, a, b, msg=None):
        assert a <= b, msg or f"{a!r} > {b!r}"

    def assertAlmostEqual(self, a, b, places=7, msg=None):
        assert round(abs(a - b), places) == 0, msg or f"{a!r} != {b!r} within {places} places"

    def assertCountEqual(self, a, b, msg=None):
        assert sorted(a) == sorted(b), msg or "counts not equal"

    def assertIsInstance(self, obj, cls, msg=None):
        assert isinstance(obj, cls), msg or f"{obj!r} is not instance of {cls!r}"

    def assertNotIsInstance(self, obj, cls, msg=None):
        assert not isinstance(obj, cls), msg or f"{obj!r} is instance of {cls!r}"

    def assertRaises(self, expected_exception):
        import pytest

        return pytest.raises(expected_exception)


class TestDatabaseBase(_AssertionMixin):
    """Base class for all tests that need database access.

    Uses pytest-asyncio fixtures with session-scoped test_engine,
    eliminating the cross-event-loop deadlock that occurred with
    unittest.IsolatedAsyncioTestCase + manual loop management.

    Subclasses can still use self.assertEqual / self.assertIn etc.
    via _AssertionMixin, and override asyncSetUp / asyncTearDown
    while calling await super().asyncSetUp().
    """

    engine: AsyncEngine
    cache: CacheManager

    @pytest_asyncio.fixture(autouse=True)
    async def _setup_base(self, test_engine):
        self._test_engine_ref = test_engine
        await self.asyncSetUp()
        yield
        await self.asyncTearDown()

    async def asyncSetUp(self):
        import config

        self._original_config_db_url = config.DB_URL
        config.DB_URL = TEST_DB_URL

        self._original_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DB_URL

        CacheManager._instance = None
        CacheManager._initialized = False

        self.engine = self._test_engine_ref

        self.cache = CacheManager()
        await self.cache.init_db()

        await _truncate_all_tables(self.cache.engine)

    async def asyncTearDown(self):
        if hasattr(self, "cache"):
            await self.cache.close()

        import config

        config.DB_URL = self._original_config_db_url

        if self._original_db_url is not None:
            os.environ["DATABASE_URL"] = self._original_db_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

        CacheManager._instance = None
        CacheManager._initialized = False
