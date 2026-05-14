import os

import asyncio
import logging
import unittest

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

from tests.conftest import TEST_DB_URL
from data.cache.cache_manager import CacheManager
from data.persistence.models import Base

_SESSION_ENGINE: AsyncEngine | None = None
_TABLES_INITIALIZED = False

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


async def _ensure_session_engine():
    """Ensure session-level engine is initialized (only once per test session)."""
    global _SESSION_ENGINE, _TABLES_INITIALIZED

    if _SESSION_ENGINE is None:
        _SESSION_ENGINE = create_async_engine(TEST_DB_URL, echo=False)

        if not _TABLES_INITIALIZED:
            async with _SESSION_ENGINE.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            _TABLES_INITIALIZED = True

    return _SESSION_ENGINE


async def _truncate_all_tables(engine: AsyncEngine):
    """Truncate all tables for test isolation (fast, no DDL)."""
    tables_str = ", ".join(TABLE_NAMES)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {tables_str} CASCADE"))
        return
    except OperationalError, ProgrammingError:
        pass
    for table in TABLE_NAMES:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        except Exception as e:
            logger.warning(f"[TestDB] TRUNCATE {table} failed: {e}")


class TestDatabaseBase(unittest.IsolatedAsyncioTestCase):
    """Base class for all tests that need database access.

    Performance optimization:
    - Session-level engine (created once per test session)
    - Session-level table creation (DDL executed once)
    - Per-test TRUNCATE for data isolation (fast, no DDL)
    """

    _session_engine: AsyncEngine = None  # type: ignore[assignment]

    @classmethod
    def setUpClass(cls):
        cls._original_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DB_URL

        if cls._session_engine is None:
            loop = asyncio.new_event_loop()
            cls._session_engine = loop.run_until_complete(_ensure_session_engine())
            loop.close()

    @classmethod
    def tearDownClass(cls):
        if cls._original_db_url:
            os.environ["DATABASE_URL"] = cls._original_db_url
        elif "DATABASE_URL" in os.environ:
            del os.environ["DATABASE_URL"]

    async def asyncSetUp(self):
        import config

        self._original_config_db_url = config.DB_URL
        config.DB_URL = TEST_DB_URL

        CacheManager._instance = None
        CacheManager._initialized = False

        self.engine = self._session_engine

        self.cache = CacheManager()
        await self.cache.init_db()

        await _truncate_all_tables(self.cache.engine)

    async def asyncTearDown(self):
        if hasattr(self, "cache"):
            await self.cache.close()

        import config

        config.DB_URL = self._original_config_db_url

        CacheManager._instance = None
        CacheManager._initialized = False
