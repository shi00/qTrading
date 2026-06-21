import logging
from contextlib import ExitStack

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

from tests.integration.conftest import TEST_DB_URL
from data.cache.cache_manager import CacheManager
from data.persistence.db_url_override import override_db_url
from data.persistence.models import Base

pytestmark = pytest.mark.integration

# 动态从 ORM metadata 生成 truncate 表清单，避免与 schema 真相源脱钩（P1-1）。
# 排除 alembic_version（迁移版本表，不应被清空）。
_EXCLUDED_TABLES = frozenset({"alembic_version"})
TABLE_NAMES = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in _EXCLUDED_TABLES]


async def _truncate_all_tables(engine: AsyncEngine):
    """Truncate all tables for test isolation (fast, no DDL)."""
    tables_str = ", ".join(TABLE_NAMES)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE TABLE {tables_str} CASCADE"))
        return
    except (OperationalError, ProgrammingError) as e:
        logger.warning("[TestDB] TRUNCATE all tables failed, falling back to per-table: %s", e)
    for table in TABLE_NAMES:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        except Exception as e:  # noqa: BLE001
            logger.warning("[TestDB] TRUNCATE %s failed: %s", table, e)


def make_clean_db_fixture(tables: list[str] | None = None):
    """工厂函数：创建 clean_db autouse fixture，DELETE 模式清理表。

    Args:
        tables: 指定表名列表；若为 None 则从 ORM metadata 动态生成（排除 alembic_version）。
    """
    table_list = tables if tables is not None else TABLE_NAMES

    @pytest_asyncio.fixture(autouse=True)
    async def clean_db(test_engine: AsyncEngine):
        """每个测试前清理数据库表（容错处理表不存在）。"""
        async with test_engine.begin() as conn:
            for table in table_list:
                try:
                    await conn.execute(text(f"DELETE FROM {table}"))
                except Exception as e:  # noqa: BLE001
                    logger.warning("[TestDB] DELETE %s failed: %s", table, e)
        yield

    return clean_db


@pytest.fixture
def mock_singletons():
    """Mock all Singletons to prevent real execution by setting their _instances.

    合并自 test_graceful_shutdown.py 与 test_shutdown_step_failure_recovery.py（INT-P2-2）。
    """
    from unittest.mock import AsyncMock, MagicMock

    from data.cache.cache_manager import CacheManager
    from data.data_processor import DataProcessor
    from data.domain_services.market_data_service import MarketDataService
    from services.local_model_manager import LocalModelManager
    from services.news_subscription_service import NewsSubscriptionService
    from services.task_manager import TaskManager
    from utils.scheduler_service import SchedulerService
    from utils.thread_pool import ThreadPoolManager

    orig_tm = TaskManager._instance
    orig_news = NewsSubscriptionService._instance
    orig_dp = DataProcessor._instance
    orig_cache = CacheManager._instance
    orig_mds = MarketDataService._instance
    orig_llm = LocalModelManager._instance
    orig_tp = ThreadPoolManager._instance
    svc = SchedulerService()
    orig_scheduler_running = getattr(svc.scheduler, "running", None) if hasattr(svc, "scheduler") else None
    orig_scheduler_stop = getattr(svc, "stop", None)

    try:
        TaskManager._instance = AsyncMock()
        NewsSubscriptionService._instance = AsyncMock()
        DataProcessor._instance = AsyncMock()
        CacheManager._instance = AsyncMock()
        CacheManager._instance.engine = AsyncMock()
        MarketDataService._instance = AsyncMock()
        LocalModelManager._instance = MagicMock()
        LocalModelManager._instance._llm = MagicMock()
        LocalModelManager._instance._worker_ready = True
        ThreadPoolManager._instance = MagicMock()
        svc.scheduler = MagicMock()
        svc.scheduler.running = True
        svc.stop = MagicMock()

        yield {
            "TaskManager": TaskManager,
            "scheduler": svc,
            "NewsSubscriptionService": NewsSubscriptionService,
            "DataProcessor": DataProcessor,
            "CacheManager": CacheManager,
            "MarketDataService": MarketDataService,
            "LocalModelManager": LocalModelManager,
            "ThreadPoolManager": ThreadPoolManager,
        }
    finally:
        TaskManager._instance = orig_tm
        NewsSubscriptionService._instance = orig_news
        DataProcessor._instance = orig_dp
        CacheManager._instance = orig_cache
        MarketDataService._instance = orig_mds
        LocalModelManager._instance = orig_llm
        ThreadPoolManager._instance = orig_tp
        if orig_scheduler_running is not None:
            svc.scheduler.running = orig_scheduler_running
        if orig_scheduler_stop is not None:
            svc.stop = orig_scheduler_stop


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
        self._url_stack = ExitStack()
        self._url_stack.enter_context(override_db_url(TEST_DB_URL))

        CacheManager._instance = None
        CacheManager._initialized = False

        self.engine = self._test_engine_ref

        self.cache = CacheManager()
        await self.cache.init_db(auto_migrate=True)

        await _truncate_all_tables(self.cache.engine)

    async def asyncTearDown(self):
        if hasattr(self, "cache"):
            await self.cache.close()

        self._url_stack.close()

        CacheManager._instance = None
        CacheManager._initialized = False
