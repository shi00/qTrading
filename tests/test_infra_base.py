import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import asyncio
import contextlib
import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from data.cache.cache_manager import CacheManager
from data.persistence.models import Base

TEST_DB_URL = "postgresql+asyncpg://postgres:123456@localhost:5432/test_astock"

_SESSION_ENGINE: AsyncEngine | None = None
_TABLES_INITIALIZED = False

TABLE_NAMES = [
    "daily_quotes",
    "index_daily",
    "index_dailybasic",
    "block_trade",
    "limit_list",
    "top_list",
    "margin_daily",
    "suspend_d",
    "moneyflow_daily",
    "northbound_holding",
    "trade_cal",
    "stock_basic",
    "stock_concepts",
    "financial_reports",
    "daily_indicators",
    "fina_forecast",
    "fina_mainbz",
    "fina_audit",
    "pledge_stat",
    "repurchase",
    "dividend",
    "screener_predictions",
    "screener_results",
    "news_raw",
    "news_processed",
    "macro_china_ppi",
    "macro_china_cpi",
    "macro_china_m",
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
    async with engine.begin() as conn:
        for table in TABLE_NAMES:
            with contextlib.suppress(Exception):
                await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))


class TestDatabaseBase(unittest.IsolatedAsyncioTestCase):
    """Base class for all tests that need database access.

    Performance optimization:
    - Session-level engine (created once per test session)
    - Session-level table creation (DDL executed once)
    - Per-test TRUNCATE for data isolation (fast, no DDL)
    """

    _session_engine: AsyncEngine = None  # type: ignore

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

        await _truncate_all_tables(self.engine)

        self.cache = CacheManager()
        await self.cache.init_db()

    async def asyncTearDown(self):
        if hasattr(self, "cache"):
            await self.cache.close()

        import config

        config.DB_URL = self._original_config_db_url

        CacheManager._instance = None
        CacheManager._initialized = False
