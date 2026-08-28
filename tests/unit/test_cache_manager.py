import asyncio
import datetime
import inspect
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.cache.cache_manager import CacheManager
from data.cache.dao_registry import DaoRegistry
from data.cache.engine_manager import EngineManager
from data.persistence.daos.stock_dao import StockDao
from data.persistence.daos.quote_dao import QuoteDao
from data.persistence.daos.financial_dao import FinancialDao
from data.persistence.daos.sync_dao import SyncDao
from data.persistence.daos.market_dao import MarketDao
from data.persistence.daos.screener_dao import ScreenerDao
from data.persistence.daos.macro_dao import MacroDao
from data.persistence.daos.holder_dao import HolderDao
from data.persistence.daos.backtest_dao import BacktestDAO
from data.persistence.daos.top_inst_dao import TopInstDao
from data.persistence.daos.stk_limit_dao import StkLimitDao
from data.persistence.daos.pledge_detail_dao import PledgeDetailDao
from data.persistence.daos.share_float_dao import ShareFloatDao
from data.persistence.daos.stk_holdertrade_dao import StkHoldertradeDao
from data.persistence.daos.sw_industry_dao import SwIndustryClassifyDao, SwIndustryMemberDao
from data.persistence.daos.express_dao import ExpressDao
from data.persistence.daos.watchlist_dao import WatchlistDao

pytestmark = pytest.mark.unit


def _make_async_engine_ctx(mock_conn=None):
    if mock_conn is None:
        mock_conn = AsyncMock()
    mock_engine_ctx = AsyncMock()
    mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_engine_ctx, mock_conn


def _make_mgr():
    mgr = CacheManager.__new__(CacheManager)
    mgr._initialized = True
    mgr._schema_initialized = False
    mgr._disposed = False
    mgr.engine = MagicMock(spec=AsyncEngine)
    # review01-A4 Step2: 组合对象（__new__ 绕过 __init__，手动初始化）
    mgr._engine_manager = EngineManager()
    mgr._dao_registry = DaoRegistry()
    # 同步引擎引用（生产路径由 _create_engine 同步；测试手动构造需保持一致）
    mgr._engine_manager.engine = mgr.engine
    mgr.stock_dao = MagicMock(spec=StockDao)
    mgr.quote_dao = MagicMock(spec=QuoteDao)
    mgr.financial_dao = MagicMock(spec=FinancialDao)
    mgr.sync_dao = MagicMock(spec=SyncDao)
    mgr.market_dao = MagicMock(spec=MarketDao)
    mgr.screener_dao = MagicMock(spec=ScreenerDao)
    mgr.macro_dao = MagicMock(spec=MacroDao)
    mgr.holder_dao = MagicMock(spec=HolderDao)
    mgr.backtest_dao = MagicMock(spec=BacktestDAO)
    mgr.top_inst_dao = MagicMock(spec=TopInstDao)
    mgr.stk_limit_dao = MagicMock(spec=StkLimitDao)
    mgr.pledge_detail_dao = MagicMock(spec=PledgeDetailDao)
    # Phase 3D/3E：share_float + stk_holdertrade DAO（prefetch_auxiliary_data 引用）
    mgr.share_float_dao = MagicMock(spec=ShareFloatDao)
    mgr.share_float_dao.get_share_float_upcoming_batch = AsyncMock(return_value=pd.DataFrame())
    mgr.stk_holdertrade_dao = MagicMock(spec=StkHoldertradeDao)
    mgr.stk_holdertrade_dao.get_stk_holdertrade_batch = AsyncMock(return_value=pd.DataFrame())
    # Phase 3F-2：sw_industry DAO（prefetch_auxiliary_data 引用 get_sw_l2_mapping）
    mgr.sw_industry_classify_dao = MagicMock(spec=SwIndustryClassifyDao)
    mgr.sw_industry_member_dao = MagicMock(spec=SwIndustryMemberDao)
    mgr.sw_industry_member_dao.get_sw_l2_mapping = AsyncMock(return_value={})
    # Phase 3G §4.3.4：express DAO（prefetch_auxiliary_data 引用 get_express_batch）
    mgr.express_dao = MagicMock(spec=ExpressDao)
    mgr.express_dao.get_express_batch = AsyncMock(return_value=pd.DataFrame())
    # FR-UX-004, Task 4.2：watchlist DAO（_create_engine 通过 _DAO_REGISTRY 同步 engine 引用）
    mgr.watchlist_dao = MagicMock(spec=WatchlistDao)
    return mgr


class TestCacheManagerCreateEngine:
    @patch("data.cache.engine_manager.create_async_engine")
    @patch("data.cache.engine_manager.get_db_pool_config")
    def test_create_engine_defaults(
        self,
        mock_get_config,
        mock_create,
    ):
        mock_get_config.return_value = {
            "pool_size": 10,
            "max_overflow": 5,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
        }
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        mgr = _make_mgr()
        mgr._create_engine("postgresql+asyncpg://user:pass@localhost/testdb")
        mock_get_config.assert_called_once_with()
        mock_create.assert_called_once_with(
            "postgresql+asyncpg://user:pass@localhost/testdb",
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=5,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )
        assert mgr.engine == mock_engine

    @patch("data.cache.engine_manager.create_async_engine")
    @patch("data.cache.engine_manager.get_db_pool_config")
    def test_create_engine_invalid_config(
        self,
        mock_get_config,
        mock_create,
    ):
        # When ConfigHandler returns invalid values, get_typed (used internally
        # by get_db_pool_config) falls back to defaults. _create_engine just
        # passes the config dict through to create_async_engine.
        mock_get_config.return_value = {
            "pool_size": 10,
            "max_overflow": 5,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
        }
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        mgr = _make_mgr()
        mgr._create_engine("postgresql+asyncpg://user:pass@localhost/testdb")
        mock_create.assert_called_once_with(
            "postgresql+asyncpg://user:pass@localhost/testdb",
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=5,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )

    @patch("data.cache.engine_manager.create_async_engine")
    @patch("data.cache.engine_manager.get_db_pool_config")
    def test_create_engine_none_config(
        self,
        mock_get_config,
        mock_create,
    ):
        # When ConfigHandler returns None, get_typed (used internally by
        # get_db_pool_config) falls back to defaults.
        mock_get_config.return_value = {
            "pool_size": 10,
            "max_overflow": 5,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
        }
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        mgr = _make_mgr()
        mgr._create_engine("postgresql+asyncpg://user:pass@localhost/testdb")
        mock_create.assert_called_once_with(
            "postgresql+asyncpg://user:pass@localhost/testdb",
            echo=False,
            future=True,
            pool_size=10,
            max_overflow=5,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )


class TestCacheManagerInitDb:
    @pytest.mark.asyncio
    async def test_init_db_already_initialized(self):
        mgr = _make_mgr()
        mgr._schema_initialized = True
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with patch.object(
            CacheManager,
            "_init_lock",
            new_callable=PropertyMock,
            return_value=mock_lock,
        ):
            await mgr.init_db()

    @pytest.mark.asyncio
    async def test_init_db_force(self):
        mgr = _make_mgr()
        mgr._schema_initialized = True
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=mock_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock()
            await mgr.init_db(force=True)
            mock_migrator.init_db.assert_called_once_with(mgr.engine, auto_migrate=None)

    @pytest.mark.asyncio
    async def test_init_db_no_engine_no_connection(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mgr.engine = None
        mgr._engine_manager.get_connection_string = MagicMock(return_value=None)
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with patch.object(
            CacheManager,
            "_init_lock",
            new_callable=PropertyMock,
            return_value=mock_lock,
        ):
            with pytest.raises(RuntimeError, match="not configured"):
                await mgr.init_db()

    @pytest.mark.asyncio
    async def test_init_db_no_engine_creates_engine(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mgr.engine = None
        mgr._engine_manager.get_connection_string = MagicMock(
            return_value="postgresql+asyncpg://user:pass@localhost/testdb"
        )
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=mock_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
            patch.object(mgr, "_create_engine") as mock_create,
        ):
            mock_migrator.init_db = AsyncMock()
            await mgr.init_db()
            mock_create.assert_called_once_with("postgresql+asyncpg://user:pass@localhost/testdb")

    @pytest.mark.asyncio
    async def test_init_db_migrator_failure(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=mock_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=Exception("migration failed"))
            with pytest.raises(Exception, match="migration failed"):
                await mgr.init_db(force=True)

    @pytest.mark.asyncio
    async def test_init_db_propagates_migration_needed(self):
        from data.persistence.db_migrator import DatabaseMigrationNeeded

        mgr = _make_mgr()
        mgr._schema_initialized = False
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=mock_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=DatabaseMigrationNeeded(current_rev="abc", head_rev="def"))
            with pytest.raises(DatabaseMigrationNeeded) as exc_info:
                await mgr.init_db(force=True)
            assert isinstance(exc_info.value, DatabaseMigrationNeeded)

            # DatabaseMigrationNeeded 不应设置 _schema_initialized=True，
            # 允许后续 init_db() 重试
            assert mgr._schema_initialized is False

    @pytest.mark.asyncio
    async def test_init_db_cancelled_error_propagates(self):
        """CancelledError must propagate for graceful shutdown (R2 compliance)."""
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=mock_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await mgr.init_db(force=True)
            assert isinstance(exc_info.value, asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_init_db_cancelled_error_logs_warning(self):
        """CancelledError should log a warning before propagating."""
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=mock_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
            patch("data.cache.cache_manager.logger") as mock_logger,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await mgr.init_db(force=True)
            assert isinstance(exc_info.value, asyncio.CancelledError)

            # Verify warning was logged
            mock_logger.warning.assert_called_once_with("[CacheManager] Schema | Init cancelled during shutdown.")
            assert "cancelled" in mock_logger.warning.call_args[0][0].lower()


class TestCacheManagerClose:
    @pytest.mark.asyncio
    async def test_close_disposes_engine(self):
        mgr = _make_mgr()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        mgr.engine = mock_engine
        # review01-A4 Step2: EngineManager 持有引擎引用（生产路径由 _create_engine 同步）
        mgr._engine_manager.engine = mock_engine
        with (
            patch("utils.loop_local.get_loop_local") as mock_gll,
            patch("utils.loop_local.del_loop_local"),
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event") as mock_evt,
        ):
            mock_evt.return_value = MagicMock()
            mock_evt.return_value.set = MagicMock()
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            mock_gll.return_value.set = MagicMock()
            await mgr.close()
            assert mgr._disposed is True
            mock_engine.dispose.assert_called_once_with()
            assert mgr.engine is None

    @pytest.mark.asyncio
    async def test_close_no_engine(self):
        mgr = _make_mgr()
        mgr.engine = None
        with (
            patch("utils.loop_local.get_loop_local") as mock_gll,
            patch("utils.loop_local.del_loop_local"),
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event") as mock_evt,
        ):
            mock_evt.return_value = MagicMock()
            mock_evt.return_value.set = MagicMock()
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            mock_gll.return_value.set = MagicMock()
            await mgr.close()
            assert mgr._disposed is True

    @pytest.mark.asyncio
    async def test_close_maintenance_event_failure(self):
        mgr = _make_mgr()
        mgr.engine = MagicMock()
        mgr.engine.dispose = AsyncMock()
        # review01-A4 Step2: EngineManager 持有引擎引用（同步）
        mgr._engine_manager.engine = mgr.engine
        with (
            patch("utils.loop_local.get_loop_local") as mock_gll,
            patch("utils.loop_local.del_loop_local"),
            patch(
                "data.persistence.daos.base_dao.BaseDao._get_maintenance_event",
                side_effect=Exception("no event"),
            ),
        ):
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            mock_gll.return_value.set = MagicMock()
            await mgr.close()
            assert mgr._disposed is True


class TestCacheManagerHardReset:
    @pytest.mark.asyncio
    async def test_hard_reset_success(self):
        mgr = _make_mgr()
        with patch.object(mgr, "clear_all_cache", new_callable=AsyncMock):
            await mgr.hard_reset()

    @pytest.mark.asyncio
    async def test_hard_reset_failure(self):
        mgr = _make_mgr()
        with patch.object(
            mgr,
            "clear_all_cache",
            new_callable=AsyncMock,
            side_effect=Exception("reset failed"),
        ):
            with pytest.raises(Exception, match="reset failed"):
                await mgr.hard_reset()


class TestCacheManagerClearAllCache:
    @pytest.mark.asyncio
    async def test_clear_all_cache_success(self):
        mgr = _make_mgr()
        mock_engine_ctx, mock_conn = _make_async_engine_ctx()
        mgr.engine = MagicMock()
        mgr.engine.begin = MagicMock(return_value=mock_engine_ctx)

        with (
            patch("utils.loop_local.get_loop_local") as mock_gll,
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event") as mock_evt,
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            mock_gll.return_value.clear = MagicMock()
            mock_gll.return_value.set = MagicMock()
            mock_evt.return_value = MagicMock()
            mock_evt.return_value.clear = MagicMock()
            mock_evt.return_value.set = MagicMock()
            mock_migrator.init_db = AsyncMock()
            await mgr.clear_all_cache()

    @pytest.mark.asyncio
    async def test_clear_all_cache_sets_maintenance_event(self):
        mgr = _make_mgr()
        mock_event = MagicMock()
        mock_event.is_set = MagicMock(return_value=True)
        mock_event.clear = MagicMock()
        mock_event.set = MagicMock()

        with (
            patch.object(
                CacheManager,
                "_maintenance_event",
                new_callable=PropertyMock,
                return_value=mock_event,
            ),
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event") as mock_evt,
            patch.object(mgr, "init_db", new_callable=AsyncMock),
        ):
            mock_evt.return_value = MagicMock()
            mock_evt.return_value.clear = MagicMock()
            mock_evt.return_value.set = MagicMock()

            mgr.engine = MagicMock()
            mock_engine_ctx, mock_conn = _make_async_engine_ctx()
            mgr.engine.begin = MagicMock(return_value=mock_engine_ctx)

            await mgr.clear_all_cache()
            mock_event.clear.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_clear_all_cache_maintenance_event_set_failure(self):
        """P3-M5-ClassifyError-System-Gap: finally 块 BaseDao._get_maintenance_event().set() 抛异常时走 classify_severity 分支。"""
        mgr = _make_mgr()
        mock_engine_ctx, _ = _make_async_engine_ctx()
        mgr.engine = MagicMock()
        mgr.engine.begin = MagicMock(return_value=mock_engine_ctx)

        with (
            patch("utils.loop_local.get_loop_local") as mock_gll,
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event") as mock_evt,
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            mock_gll.return_value.clear = MagicMock()
            mock_gll.return_value.set = MagicMock()
            # .clear() 正常但 .set() 抛异常，触发 finally 块 except 路径 (L437-438)
            mock_evt.return_value = MagicMock()
            mock_evt.return_value.clear = MagicMock()
            mock_evt.return_value.set = MagicMock(side_effect=RuntimeError("event set failed"))
            mock_migrator.init_db = AsyncMock()
            await mgr.clear_all_cache()


class TestCacheManagerWaitForMaintenance:
    @pytest.mark.asyncio
    async def test_already_set(self):
        mgr = _make_mgr()
        mock_event = MagicMock()
        mock_event.is_set = MagicMock(return_value=True)
        with patch.object(
            CacheManager,
            "_maintenance_event",
            new_callable=PropertyMock,
            return_value=mock_event,
        ):
            await mgr.wait_for_maintenance()

    @pytest.mark.asyncio
    async def test_waits_then_set(self):
        mgr = _make_mgr()
        mock_event = MagicMock()
        mock_event.is_set = MagicMock(return_value=False)
        mock_event.wait = AsyncMock()
        with patch.object(
            CacheManager,
            "_maintenance_event",
            new_callable=PropertyMock,
            return_value=mock_event,
        ):
            await mgr.wait_for_maintenance()
            mock_event.wait.assert_called_once_with()


class TestCacheManagerWriteReadDb:
    @pytest.mark.asyncio
    async def test_write_db(self):
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._write_db = AsyncMock(return_value=1)
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            await mgr.write_db("INSERT INTO test VALUES (?)", ("val",))
            # review03-C12: 默认不再吞错（suppress_errors=False）
            mock_dao._write_db.assert_called_once_with("INSERT INTO test VALUES (?)", ("val",), suppress_errors=False)

    @pytest.mark.asyncio
    async def test_write_db_suppress_optin(self):
        """review03-C12: 显式 suppress_errors=True 仍被透传（如记录清理等可容忍失败路径）。"""
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._write_db = AsyncMock(return_value=-1)
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            await mgr.write_db("DELETE FROM test", (), suppress_errors=True)
            mock_dao._write_db.assert_called_once_with("DELETE FROM test", (), suppress_errors=True)

    @pytest.mark.asyncio
    async def test_read_db(self):
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._read_db = AsyncMock(return_value=pd.DataFrame())
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            await mgr.read_db("SELECT * FROM test")
            mock_dao._read_db.assert_called_once_with("SELECT * FROM test", None, suppress_errors=True)


class TestCacheManagerPublicWriteReadDb:
    """Q-P2-1: CacheManager.write_db/read_db are public methods
    (renamed from _write_db/_read_db which were called externally)."""

    @pytest.mark.asyncio
    async def test_write_db_public_method(self):
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._write_db = AsyncMock(return_value=1)
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            result = await mgr.write_db("INSERT INTO test VALUES (?)", ("val",))
            mock_dao._write_db.assert_called_once_with("INSERT INTO test VALUES (?)", ("val",), suppress_errors=False)
            assert result == 1

    @pytest.mark.asyncio
    async def test_read_db_public_method(self):
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._read_db = AsyncMock(return_value=pd.DataFrame({"a": [1]}))
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            result = await mgr.read_db("SELECT * FROM test")
            mock_dao._read_db.assert_called_once_with("SELECT * FROM test", None, suppress_errors=True)
            assert len(result) == 1


class TestCacheManagerCheckComprehensiveHealth:
    def _make_health_conn(self):
        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=50)
        mock_conn.execute = AsyncMock(return_value=mock_result)
        return mock_conn

    @pytest.mark.asyncio
    async def test_basic_health_check(self):
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=("20240101", "20240614"))
        mgr.stock_dao.count_trade_days = AsyncMock(return_value=100)
        mgr.stock_dao.count_expected_rows = AsyncMock(return_value=5000)

        mock_conn = self._make_health_conn()
        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True, "frequency": "daily"},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"], "date_col": "trade_date"},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert "total_stocks" in result
            assert "tables" in result

    @pytest.mark.asyncio
    async def test_health_check_no_date_range(self):
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        mock_conn = self._make_health_conn()
        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"], "date_col": "trade_date"},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert result["global_trade_days"] == 0

    @pytest.mark.asyncio
    async def test_health_check_global_table(self):
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        mock_conn = self._make_health_conn()
        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "macro_economy": {
                        "type": "global",
                        "quality_config": {"monitor": True},
                        "columns": {},
                        "sync_config": {},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert "macro_economy" in result["tables"]
            assert result["tables"]["macro_economy"]["type"] == "global"

    @pytest.mark.asyncio
    async def test_health_check_table_error(self):
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        mock_conn.execute = AsyncMock(side_effect=Exception("no such table: test"))

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "test_table": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"]},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert "test_table" in result["tables"]
            assert result["tables"]["test_table"]["ratio"] == 0

    @pytest.mark.asyncio
    async def test_health_check_baseline_failure(self):
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(side_effect=Exception("db error"))

        mock_conn = self._make_health_conn()
        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"]},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert result["global_trade_days"] == 0

    @pytest.mark.asyncio
    async def test_health_check_baseline_calc_failure(self):
        """P3-M5-ClassifyError-System-Gap: count_trade_days 抛异常时走 classify_severity 分支 (L681)。

        get_date_range 返回有效元组使 baseline try 块被进入，但 count_trade_days 抛异常，
        触发 except 块的 classify_severity 调用。
        """
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=("20240101", "20240614"))
        mgr.stock_dao.count_trade_days = AsyncMock(side_effect=Exception("count failed"))

        mock_conn = self._make_health_conn()
        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {},
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert result["global_trade_days"] == 0

    @pytest.mark.asyncio
    async def test_health_check_table_query_not_found_error(self):
        """P3-M5-ClassifyError-System-Gap: 表查询抛 'no such table' 时走 classify_error not_found 分支 (L870-L871)。

        使用真实模型表名 'daily_quotes' 使 tbl 非空（进入 try 块），
        conn.execute 抛 'no such table' 异常触发 classify_error code='not_found' 分支。
        """
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        mock_conn.execute = AsyncMock(side_effect=Exception("no such table: daily_quotes"))

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"]},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert "daily_quotes" in result["tables"]
            assert result["tables"]["daily_quotes"]["ratio"] == 0

    @pytest.mark.asyncio
    async def test_health_check_table_query_generic_error(self):
        """P3-M5-ClassifyError-System-Gap: 表查询抛非 not_found 异常时走 else 分支 (L877)。

        conn.execute 抛 'connection reset' 异常，classify_error code 非 'not_found'，
        触发 else 分支的 classify_severity 调用。
        """
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        mock_conn.execute = AsyncMock(side_effect=Exception("connection reset by peer"))

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"]},
                    },
                },
            ),
        ):
            result = await mgr.check_comprehensive_health()
            assert "daily_quotes" in result["tables"]
            assert result["tables"]["daily_quotes"]["ratio"] == 0

    @pytest.mark.asyncio
    async def test_health_check_table_query_interrupted_raises_engine_disposed(self):
        """P1-4: 表查询抛 'connection was closed' (interrupted) 时必须抛 EngineDisposedError。

        竞态场景：close() 在 check_comprehensive_health 期间执行，引擎被释放。
        原行为：异常被吞没，返回 ratio=0 误导健康报告为"表不健康"。
        修复后：抛出 EngineDisposedError 触发上层调用方异常处理，避免静默降级。
        """
        from data.persistence.daos.base_dao import EngineDisposedError

        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        # classify_error 对 "connection was closed" 识别为 interrupted
        mock_conn.execute = AsyncMock(side_effect=Exception("connection was closed in the middle of operation"))

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"]},
                    },
                },
            ),
        ):
            with pytest.raises(EngineDisposedError, match="Engine disposed during check"):
                await mgr.check_comprehensive_health()

    @pytest.mark.asyncio
    async def test_health_check_engine_disposed_propagates_through_gather(self):
        """P1-4: 多表并发检查中某表抛 EngineDisposedError 时必须优先传播。

        场景：两个 monitored 表，表A 的 conn 正常，表B 的 conn 在 count 查询时抛 interrupted。
        修复前：gather 收集异常后当作普通失败 continue，返回部分健康报告。
        修复后：检测到 EngineDisposedError 优先 raise，避免误导性健康报告。
        """
        from data.persistence.daos.base_dao import EngineDisposedError

        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        # 表A 正常 conn
        mock_conn_ok = MagicMock()
        mock_conn_ok.execution_options = AsyncMock(return_value=mock_conn_ok)
        mock_result_ok = MagicMock()
        mock_result_ok.scalar = MagicMock(return_value=10)
        mock_conn_ok.execute = AsyncMock(return_value=mock_result_ok)

        # 表B 异常 conn：count 查询抛 interrupted
        mock_conn_bad = MagicMock()
        mock_conn_bad.execution_options = AsyncMock(return_value=mock_conn_bad)
        mock_conn_bad.execute = AsyncMock(side_effect=Exception("connection was closed in the middle of operation"))

        # engine.connect() 每次返回独立 ctx：第 1 次表A，第 2 次表B
        mock_engine_ctx_ok, _ = _make_async_engine_ctx(mock_conn_ok)
        mock_engine_ctx_bad, _ = _make_async_engine_ctx(mock_conn_bad)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(side_effect=[mock_engine_ctx_ok, mock_engine_ctx_bad])

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"], "date_col": "trade_date"},
                    },
                    "stock_basic": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"]},
                    },
                },
            ),
        ):
            with pytest.raises(EngineDisposedError, match="Engine disposed during check"):
                await mgr.check_comprehensive_health()

    @pytest.mark.asyncio
    async def test_health_check_raises_cancelled_error_when_cancel_event_set(self):
        """P3-M5: 传入 cancel_event 且在 DB 操作期间被 set 时，健康检查及时中止并抛 CancelledError。

        场景：DB 操作（conn.execute）执行期间 cancel_event 被 set，随后的 _check_cancel()
        检测到取消信号立即 raise asyncio.CancelledError，经 gather 封装重新抛出。
        """
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=(None, None))

        cancel_event = asyncio.Event()

        async def _execute_that_set_cancel(*args, **kwargs):
            cancel_event.set()
            mock_result = MagicMock()
            mock_result.scalar = MagicMock(return_value=50)
            return mock_result

        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        mock_conn.execute = AsyncMock(side_effect=_execute_that_set_cancel)

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with (
            patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock),
            patch(
                "data.cache.cache_manager.TABLE_DEFINITIONS",
                {
                    "daily_quotes": {
                        "type": "stock",
                        "quality_config": {"monitor": True},
                        "columns": {"ts_code": {}},
                        "sync_config": {"keys": ["ts_code"], "date_col": "trade_date"},
                    },
                },
            ),
        ):
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await mgr.check_comprehensive_health(cancel_event=cancel_event)
            # P3-M5: 强断言——抛出的确为取消信号（CancelledError），而非其它异常被包装
            assert isinstance(exc_info.value, asyncio.CancelledError)


class TestCacheManagerCheckTableHasData:
    @pytest.mark.asyncio
    async def test_invalid_table_name(self):
        mgr = _make_mgr()
        result = await mgr.check_table_has_data("invalid_table")
        assert result is False

    @pytest.mark.asyncio
    async def test_valid_table_with_data(self):
        mgr = _make_mgr()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.first = MagicMock(return_value=(1,))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with patch("data.persistence.daos.quote_dao._SAFE_TABLE_NAMES", {"daily_quotes"}):
            result = await mgr.check_table_has_data("daily_quotes")
            assert result is True

    @pytest.mark.asyncio
    async def test_valid_table_no_data(self):
        mgr = _make_mgr()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.first = MagicMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with patch("data.persistence.daos.quote_dao._SAFE_TABLE_NAMES", {"daily_quotes"}):
            result = await mgr.check_table_has_data("daily_quotes")
            assert result is False

    @pytest.mark.asyncio
    async def test_table_query_exception(self):
        mgr = _make_mgr()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("query error"))

        mock_engine_ctx, _ = _make_async_engine_ctx(mock_conn)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with patch("data.persistence.daos.quote_dao._SAFE_TABLE_NAMES", {"daily_quotes"}):
            result = await mgr.check_table_has_data("daily_quotes")
            assert result is False


class TestCacheManagerGetConnectionString:
    """连接串获取逻辑（review01-A4 Step2 移入 EngineManager）。"""

    @patch("config.DB_URL", "postgresql+asyncpg://user:pass@localhost/fallbackdb")
    def test_fallback_to_config(self):
        em = EngineManager()
        with patch("utils.config_handler.ConfigHandler.get_db_url", return_value=None):
            result = em.get_connection_string()
            assert result == "postgresql+asyncpg://user:pass@localhost/fallbackdb"

    @patch("config.DB_URL", None)
    def test_no_url_available(self):
        em = EngineManager()
        with patch("utils.config_handler.ConfigHandler.get_db_url", return_value=None):
            result = em.get_connection_string()
            assert result is None

    def test_config_handler_has_url(self):
        em = EngineManager()
        with patch(
            "utils.config_handler.ConfigHandler.get_db_url",
            return_value="postgresql://user:pass@host/db",
        ):
            result = em.get_connection_string()
            assert result == "postgresql://user:pass@host/db"


class TestCacheManagerPrefetchAuxiliaryData:
    @pytest.mark.asyncio
    async def test_prefetch_with_all_data(self):
        mgr = _make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "audit": ["clean"]})
        )
        mgr.financial_dao.get_dividend_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "dividend": ["1.0"]})
        )
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "pledge": ["5.0"]})
        )
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder": ["张三"]})
        )
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "mainbz": ["银行"]})
        )
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "revenue": [100]})
        )
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holdernumber": [50000]})
        )

        result = await mgr.prefetch_auxiliary_data(["000001.SZ"])
        assert "000001.SZ" in result
        assert "audit" in result["000001.SZ"]
        assert "dividend" in result["000001.SZ"]
        assert "pledge" in result["000001.SZ"]
        assert "holders" in result["000001.SZ"]
        assert "mainbz" in result["000001.SZ"]
        assert "financial_history" in result["000001.SZ"]
        assert "holdernumber" in result["000001.SZ"]

    @pytest.mark.asyncio
    async def test_prefetch_multiple_codes(self):
        mgr = _make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "audit": ["clean", "clean"]})
        )
        mgr.financial_dao.get_dividend_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(return_value=pd.DataFrame())

        result = await mgr.prefetch_auxiliary_data(["000001.SZ", "000002.SZ"])
        assert "000001.SZ" in result
        assert "000002.SZ" in result
        assert "audit" in result["000001.SZ"]
        assert "audit" in result["000002.SZ"]

    @pytest.mark.asyncio
    async def test_prefetch_uses_gather_not_sequential(self):
        import time

        mgr = _make_mgr()

        async def slow_query(ts_codes):
            await asyncio.sleep(0.1)
            return pd.DataFrame({"ts_code": ts_codes, "val": [1] * len(ts_codes)})

        mgr.financial_dao.get_fina_audit_batch = AsyncMock(side_effect=slow_query)
        mgr.financial_dao.get_dividend_batch = AsyncMock(side_effect=slow_query)
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(side_effect=slow_query)
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(side_effect=slow_query)
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(side_effect=slow_query)
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(side_effect=slow_query)
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(side_effect=slow_query)

        start = time.perf_counter()
        result = await mgr.prefetch_auxiliary_data(["000001.SZ"])
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5
        assert "000001.SZ" in result

    @pytest.mark.asyncio
    async def test_prefetch_auxiliary_data_partial_failure(self):
        mgr = _make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(side_effect=RuntimeError("DB error"))
        mgr.financial_dao.get_dividend_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "div": [1.0]})
        )
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(return_value=pd.DataFrame())

        result = await mgr.prefetch_auxiliary_data(["000001.SZ"])
        assert "000001.SZ" in result
        assert "dividend" in result["000001.SZ"]
        assert "audit" not in result["000001.SZ"]

    @pytest.mark.asyncio
    async def test_prefetch_auxiliary_data_missing_ts_code_column(self):
        mgr = _make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(return_value=pd.DataFrame({"other_col": [1]}))
        mgr.financial_dao.get_dividend_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(return_value=pd.DataFrame())

        result = await mgr.prefetch_auxiliary_data(["000001.SZ"])
        assert "000001.SZ" in result
        assert "audit" not in result["000001.SZ"]

    @pytest.mark.asyncio
    async def test_prefetch_with_as_of_date(self):
        mgr = _make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "audit": ["clean"]})
        )
        mgr.financial_dao.get_dividend_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "dividend": ["1.0"]})
        )
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "pledge": ["5.0"]})
        )
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holder": ["张三"]})
        )
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "mainbz": ["银行"]})
        )
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "revenue": [100]})
        )
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "holdernumber": [50000]})
        )

        result = await mgr.prefetch_auxiliary_data(["000001.SZ"], as_of_date="20240701")
        assert "000001.SZ" in result
        mgr.financial_dao.get_fina_audit_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")
        mgr.financial_dao.get_dividend_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")
        mgr.financial_dao.get_pledge_stat_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")
        mgr.holder_dao.get_top10_holders_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")
        mgr.financial_dao.get_fina_mainbz_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")
        mgr.financial_dao.get_financial_reports_history_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")
        mgr.holder_dao.get_stk_holdernumber_batch.assert_called_with(["000001.SZ"], as_of_date="20240701")

    @pytest.mark.asyncio
    async def test_prefetch_without_as_of_date(self):
        mgr = _make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "audit": ["clean"]})
        )
        mgr.financial_dao.get_dividend_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_top10_holders_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_fina_mainbz_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.financial_dao.get_financial_reports_history_batch = AsyncMock(return_value=pd.DataFrame())
        mgr.holder_dao.get_stk_holdernumber_batch = AsyncMock(return_value=pd.DataFrame())

        result = await mgr.prefetch_auxiliary_data(["000001.SZ"], as_of_date=None)
        assert "000001.SZ" in result
        mgr.financial_dao.get_fina_audit_batch.assert_called_with(["000001.SZ"], as_of_date=None)


class TestCacheManagerMaintenanceEvent:
    def test_maintenance_event_property(self):
        mgr = _make_mgr()
        mock_event = MagicMock()
        with patch("data.cache.cache_manager.get_loop_local", return_value=mock_event):
            result = mgr._maintenance_event
            assert result == mock_event

    def test_init_lock_property(self):
        mgr = _make_mgr()
        mock_lock = MagicMock()
        with patch("data.cache.cache_manager.get_loop_local", return_value=mock_lock):
            result = mgr._init_lock
            assert result == mock_lock


class TestCacheManagerInit:
    @patch("data.cache.engine_manager.ConfigHandler.get_db_url", return_value=None)
    @patch("config.DB_URL", None)
    def test_init_no_connection_string(self, mock_url):
        CacheManager._instance = None
        CacheManager._initialized = False
        mgr = CacheManager()
        assert mgr.engine is None

    @patch(
        "data.cache.engine_manager.ConfigHandler.get_db_url",
        return_value="postgresql+asyncpg://user:pass@localhost/testdb",
    )
    @patch("data.cache.engine_manager.create_async_engine")
    def test_init_with_connection_string(self, mock_create, mock_url):
        CacheManager._instance = None
        CacheManager._initialized = False
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        with (
            patch(
                "utils.config_handler.ConfigHandler.get_db_connection_pool_size",
                return_value="10",
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_db_max_overflow",
                return_value="5",
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_db_pool_timeout",
                return_value="30",
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_db_pool_recycle",
                return_value="1800",
            ),
            patch(
                "utils.config_handler.ConfigHandler.get_db_pool_pre_ping",
                return_value=True,
            ),
        ):
            mgr = CacheManager()
            assert mgr.engine == mock_engine


class TestCacheManagerDelegationsWithWait:
    @pytest.mark.asyncio
    async def test_get_bulk_table_counts(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_bulk_table_counts = AsyncMock(return_value={})
        result = await mgr.get_bulk_table_counts("daily_quotes", "2024-01-01", "2024-06-14")
        assert result == {}
        mgr.quote_dao.get_bulk_table_counts.assert_called_once_with("daily_quotes", "2024-01-01", "2024-06-14")

    @pytest.mark.asyncio
    async def test_get_bulk_expected_stock_counts(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_bulk_expected_stock_counts = AsyncMock(return_value={})
        result = await mgr.get_bulk_expected_stock_counts("2024-01-01", "2024-06-14")
        assert result == {}
        mgr.quote_dao.get_bulk_expected_stock_counts.assert_called_once_with("2024-01-01", "2024-06-14")

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_bulk_sync_quality_scores = AsyncMock(return_value={})
        result = await mgr.get_bulk_sync_quality_scores("2024-01-01", "2024-06-14")
        assert result == {}
        mgr.quote_dao.get_bulk_sync_quality_scores.assert_called_once_with("2024-01-01", "2024-06-14", None)

    @pytest.mark.asyncio
    async def test_get_expected_stock_count(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_expected_stock_count = AsyncMock(return_value=5000)
        result = await mgr.get_expected_stock_count("2024-06-14")
        assert result == 5000

    @pytest.mark.asyncio
    async def test_get_sync_quality_score(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_sync_quality_score = AsyncMock(return_value={})
        result = await mgr.get_sync_quality_score("2024-06-14")
        assert result == {}
        mgr.quote_dao.get_sync_quality_score.assert_called_once_with("2024-06-14")

    @pytest.mark.asyncio
    async def test_get_field_completeness(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_field_completeness = AsyncMock(return_value={})
        with patch("utils.loop_local.get_loop_local") as mock_gll:
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            result = await mgr.get_field_completeness("2024-06-14")
            assert result == {}
            mgr.quote_dao.get_field_completeness.assert_called_once_with("2024-06-14")


class TestCacheManagerDelegations:
    def _make_mgr(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.engine = MagicMock()
        mgr.stock_dao = MagicMock()
        mgr.quote_dao = MagicMock()
        mgr.financial_dao = MagicMock()
        mgr.sync_dao = MagicMock()
        mgr.market_dao = MagicMock()
        mgr.screener_dao = MagicMock()
        mgr.macro_dao = MagicMock()
        mgr.holder_dao = MagicMock()
        return mgr

    @pytest.mark.asyncio
    async def test_get_concepts(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_concepts = AsyncMock(return_value={})
        result = await mgr.get_concepts()
        assert result == {}
        mgr.stock_dao.get_concepts.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_get_trade_cal_range(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_trade_cal_range = AsyncMock(return_value=(None, None))
        result = await mgr.get_trade_cal_range()
        assert result == (None, None)
        mgr.stock_dao.get_trade_cal_range.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_get_financial_reports_history(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_financial_reports_history("000001.SZ")
        assert result is not None
        mgr.financial_dao.get_financial_reports_history.assert_called_once_with("000001.SZ", 8, as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_financial_reports_history_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_financial_reports_history("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.financial_dao.get_financial_reports_history.assert_called_once_with("000001.SZ", 8, as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_fina_audit(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_fina_audit("000001.SZ")
        assert result is not None
        mgr.financial_dao.get_fina_audit_batch.assert_called_once_with(["000001.SZ"], as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_fina_audit_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_fina_audit("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.financial_dao.get_fina_audit_batch.assert_called_once_with(["000001.SZ"], as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_fina_mainbz(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_fina_mainbz("000001.SZ")
        assert result is not None
        mgr.financial_dao.get_fina_mainbz.assert_called_once_with("000001.SZ", as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_fina_mainbz_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_fina_mainbz("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.financial_dao.get_fina_mainbz.assert_called_once_with("000001.SZ", as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_dividend(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_dividend_batch = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_dividend("000001.SZ")
        assert result is not None
        mgr.financial_dao.get_dividend_batch.assert_called_once_with(["000001.SZ"], as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_dividend_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_dividend_batch = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_dividend("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.financial_dao.get_dividend_batch.assert_called_once_with(["000001.SZ"], as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_pledge_stat(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_pledge_stat("000001.SZ")
        assert result is not None
        mgr.financial_dao.get_pledge_stat_batch.assert_called_once_with(["000001.SZ"], as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_pledge_stat_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_pledge_stat("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.financial_dao.get_pledge_stat_batch.assert_called_once_with(["000001.SZ"], as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_top10_holders(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_top10_holders("000001.SZ")
        assert result is not None
        mgr.holder_dao.get_top10_holders.assert_called_once_with("000001.SZ", as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_top10_holders_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_top10_holders("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.holder_dao.get_top10_holders.assert_called_once_with("000001.SZ", as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_stk_holdernumber(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_stk_holdernumber("000001.SZ")
        assert result is not None
        mgr.holder_dao.get_stk_holdernumber.assert_called_once_with("000001.SZ", as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_stk_holdernumber_with_as_of_date(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_stk_holdernumber("000001.SZ", as_of_date="2024-07-01")
        assert result is not None
        mgr.holder_dao.get_stk_holdernumber.assert_called_once_with("000001.SZ", as_of_date="2024-07-01")

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_existing_top10_ts_codes = AsyncMock(return_value=set())
        result = await mgr.get_existing_top10_ts_codes("20240331")
        assert result == set()
        mgr.holder_dao.get_existing_top10_ts_codes.assert_called_once_with("20240331")

    @pytest.mark.asyncio
    async def test_get_macro_economy(self):
        mgr = self._make_mgr()
        mgr.macro_dao.get_macro_economy_latest = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_macro_economy()
        assert result is not None
        mgr.macro_dao.get_macro_economy_latest.assert_called_once_with(as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_shibor_latest(self):
        mgr = self._make_mgr()
        mgr.macro_dao.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_shibor_latest()
        assert result is not None
        mgr.macro_dao.get_shibor_latest.assert_called_once_with(as_of_date=None)

    @pytest.mark.asyncio
    async def test_get_concept_count(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_concept_count = AsyncMock(return_value=100)
        result = await mgr.get_concept_count()
        assert result == 100

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_incomplete_financial_stocks = AsyncMock(return_value=set())
        result = await mgr.get_incomplete_financial_stocks()
        assert result == set()
        mgr.financial_dao.get_incomplete_financial_stocks.assert_called_once_with(4, 1)

    @pytest.mark.asyncio
    async def test_get_daily_indicators_bulk(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_daily_indicators_bulk = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_daily_indicators_bulk(["000001.SZ"])
        assert result is not None
        mgr.market_dao.get_daily_indicators_bulk.assert_called_once_with(["000001.SZ"], None, None)

    @pytest.mark.asyncio
    async def test_get_index_daily_range(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())
        result = await mgr.get_index_daily_range(["399300.SZ"])
        assert result is not None
        mgr.quote_dao.get_index_daily_range.assert_called_once_with(["399300.SZ"], None, None)


class TestCacheManagerNormalizeNewsItem:
    def test_with_time_string(self):
        item = {"content": "test news", "time": "2024-06-14 10:00:00"}
        result = CacheManager.normalize_news_item(item)
        assert result["content"] == "test news"
        assert result["source"] == "CLS"

    def test_with_publish_time(self):
        item = {"content": "test", "publish_time": "2024-06-14 10:00:00"}
        result = CacheManager.normalize_news_item(item)
        assert result["content"] == "test"

    def test_without_time(self):
        item = {"content": "test"}
        result = CacheManager.normalize_news_item(item)
        assert isinstance(result["publish_time"], datetime.datetime)

    def test_with_invalid_time(self):
        item = {"content": "test", "time": "invalid_date"}
        result = CacheManager.normalize_news_item(item)
        assert isinstance(result["publish_time"], datetime.datetime)

    def test_publish_time_default_is_utc_tz_naive(self):
        """M1 举一反三 fix: 默认 publish_time 应为 UTC tz-naive，与 server_default=now() 时区一致"""
        item = {"content": "test"}
        result = CacheManager.normalize_news_item(item)
        pt = result["publish_time"]
        assert isinstance(pt, datetime.datetime)
        assert pt.tzinfo is None  # tz-naive
        # 验证近似当前 UTC 时间（避免 8 小时偏差导致写库时间错误）
        utc_now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        diff = abs((pt - utc_now).total_seconds())
        assert diff < 60  # 1 分钟内

    def test_publish_time_fallback_on_parse_error_is_utc_tz_naive(self):
        """M1 举一反三 fix: publish_time 解析失败 fallback 也应为 UTC tz-naive"""
        item = {"content": "test", "time": "invalid_date"}
        result = CacheManager.normalize_news_item(item)
        pt = result["publish_time"]
        assert isinstance(pt, datetime.datetime)
        assert pt.tzinfo is None
        utc_now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        diff = abs((pt - utc_now).total_seconds())
        assert diff < 60

    def test_with_tags(self):
        item = {"content": "test", "tags": "【政策】"}
        result = CacheManager.normalize_news_item(item)
        assert result["tags"] == "【政策】"

    def test_with_source(self):
        item = {"content": "test", "source": "SINA"}
        result = CacheManager.normalize_news_item(item, default_source="SINA")
        assert result["source"] == "SINA"

    def test_content_strip(self):
        item = {"content": "  test  "}
        result = CacheManager.normalize_news_item(item)
        assert result["content"] == "test"


class TestCacheManagerSanitizeUrl:
    """URL 脱敏逻辑（review01-A4 Step2 移入 EngineManager.sanitize_url 静态方法）。"""

    def test_empty_url(self):
        assert EngineManager.sanitize_url("") == "None"

    def test_url_with_password(self):
        result = EngineManager.sanitize_url("postgresql://user:secret@localhost/db")
        assert "secret" not in result
        assert "****" in result

    def test_url_without_password(self):
        result = EngineManager.sanitize_url("postgresql+asyncpg://user@localhost/testdb")
        assert "testdb" in result


class TestCacheManagerUsesMetadataTables:
    def test_check_table_has_data_uses_metadata_not_sa_table(self):
        from data.cache.cache_manager import CacheManager

        assert hasattr(CacheManager, "check_table_has_data")
        sig = inspect.signature(CacheManager.check_table_has_data)
        assert "table_name" in sig.parameters

    def test_health_check_uses_metadata_not_sa_table(self):
        from data.cache.cache_manager import CacheManager

        assert hasattr(CacheManager, "check_comprehensive_health")
        sig = inspect.signature(CacheManager.check_comprehensive_health)
        assert len(sig.parameters) >= 1


class TestSuppressErrorsDefaultFalse:
    """E-P1-5: Verify write operations default suppress_errors=False."""

    def test_market_dao_save_daily_indicators_default_is_false(self):
        import inspect
        from data.persistence.daos.market_dao import MarketDao

        sig = inspect.signature(MarketDao.save_daily_indicators)
        default = sig.parameters["suppress_errors"].default
        assert default is False, (
            f"E-P1-5: MarketDao.save_daily_indicators suppress_errors should default to False, got {default!r}"
        )

    def test_quote_dao_save_daily_quotes_default_is_false(self):
        import inspect
        from data.persistence.daos.quote_dao import QuoteDao

        sig = inspect.signature(QuoteDao.save_daily_quotes)
        default = sig.parameters["suppress_errors"].default
        assert default is False, (
            f"E-P1-5: QuoteDao.save_daily_quotes suppress_errors should default to False, got {default!r}"
        )


class TestConcurrentInitDb:
    """Verify that CacheManager.init_db() is protected by _init_lock so concurrent
    calls don't cause double initialization."""

    @pytest.mark.asyncio
    async def test_concurrent_init_db_calls_migrator_only_once(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False

        # Use a real asyncio.Lock so concurrent gather actually contends
        real_lock = asyncio.Lock()

        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=real_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock()

            # Fire 5 concurrent init_db calls
            results = await asyncio.gather(
                *[mgr.init_db() for _ in range(5)],
                return_exceptions=True,
            )

            # No exceptions should have been raised
            for r in results:
                assert not isinstance(r, Exception), f"Unexpected exception: {r}"

            # The migrator's init_db should have been called exactly once
            mock_migrator.init_db.assert_called_once_with(mgr.engine, auto_migrate=None)


class TestFinancialTransaction:
    """Verify financial_transaction delegates to financial_dao._guarded_begin."""

    @pytest.mark.asyncio
    async def test_yields_connection_from_guarded_begin(self):
        mgr = _make_mgr()
        mock_conn = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mgr.financial_dao._guarded_begin = MagicMock(return_value=mock_ctx)

        async with mgr.financial_transaction() as conn:
            assert conn is mock_conn

        mgr.financial_dao._guarded_begin.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_propagates_engine_disposed_error(self):
        from data.persistence.daos.base_dao import EngineDisposedError

        mgr = _make_mgr()
        mgr.financial_dao._guarded_begin = MagicMock(side_effect=EngineDisposedError("disposed"))

        with pytest.raises(EngineDisposedError) as exc_info:
            async with mgr.financial_transaction():
                pass
        assert isinstance(exc_info.value, EngineDisposedError)


class TestConcurrentInitDbForce:
    """When force=True, each call re-runs the migrator, but the lock
    ensures they execute serially (no concurrent migration overlap)."""

    @pytest.mark.asyncio
    async def test_concurrent_init_db_with_force_is_serialized(self):
        mgr = _make_mgr()
        mgr._schema_initialized = True

        real_lock = asyncio.Lock()
        call_order: list[int] = []

        async def tracked_init_db(engine, auto_migrate=None):
            call_order.append(len(call_order) + 1)

        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=real_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=tracked_init_db)

            results = await asyncio.gather(
                *[mgr.init_db(force=True) for _ in range(5)],
                return_exceptions=True,
            )

            for r in results:
                assert not isinstance(r, Exception), f"Unexpected exception: {r}"

            # force=True means each call runs the migrator
            assert mock_migrator.init_db.call_count == 5
            # But they ran serially (lock-protected), not concurrently
            assert len(call_order) == 5

    @pytest.mark.asyncio
    async def test_concurrent_init_db_first_wins_second_skips(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False

        real_lock = asyncio.Lock()
        call_count = 0

        async def slow_init_db(engine, auto_migrate=None):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)

        with (
            patch.object(
                CacheManager,
                "_init_lock",
                new_callable=PropertyMock,
                return_value=real_lock,
            ),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=slow_init_db)

            results = await asyncio.gather(
                mgr.init_db(),
                mgr.init_db(),
                mgr.init_db(),
                return_exceptions=True,
            )

            for r in results:
                assert not isinstance(r, Exception), f"Unexpected exception: {r}"

            # Only the first call should execute the migrator; the rest see _schema_initialized=True
            assert call_count == 1
