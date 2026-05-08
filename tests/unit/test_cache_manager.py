import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
import pandas as pd

from data.cache.cache_manager import CacheManager


@pytest.fixture(autouse=True)
def reset_singleton():
    CacheManager._instance = None
    CacheManager._initialized = False
    yield
    CacheManager._instance = None
    CacheManager._initialized = False


def _make_mgr():
    mgr = CacheManager.__new__(CacheManager)
    mgr._initialized = True
    mgr._schema_initialized = False
    mgr._disposed = False
    mgr.engine = MagicMock()
    mgr.stock_dao = MagicMock()
    mgr.quote_dao = MagicMock()
    mgr.financial_dao = MagicMock()
    mgr.sync_dao = MagicMock()
    mgr.market_dao = MagicMock()
    mgr.screener_dao = MagicMock()
    mgr.macro_dao = MagicMock()
    mgr.holder_dao = MagicMock()
    mgr._maintenance_event_lazy = None
    mgr._init_lock_lazy = None
    return mgr


class TestCacheManagerCreateEngine:
    @patch("data.cache.cache_manager.create_async_engine")
    @patch("utils.config_handler.ConfigHandler.get_db_connection_pool_size", return_value="10")
    @patch("utils.config_handler.ConfigHandler.get_db_max_overflow", return_value="5")
    @patch("utils.config_handler.ConfigHandler.get_db_pool_timeout", return_value="30")
    @patch("utils.config_handler.ConfigHandler.get_db_pool_recycle", return_value="1800")
    @patch("utils.config_handler.ConfigHandler.get_db_pool_pre_ping", return_value=True)
    def test_create_engine_defaults(
        self, mock_ping, mock_recycle, mock_timeout, mock_overflow, mock_pool_size, mock_create
    ):
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        mgr = _make_mgr()
        mgr._create_engine("sqlite:///test.db")
        mock_create.assert_called_once()
        assert mgr.engine == mock_engine

    @patch("data.cache.cache_manager.create_async_engine")
    @patch("utils.config_handler.ConfigHandler.get_db_connection_pool_size", return_value="invalid")
    @patch("utils.config_handler.ConfigHandler.get_db_max_overflow", return_value="invalid")
    @patch("utils.config_handler.ConfigHandler.get_db_pool_timeout", return_value="invalid")
    @patch("utils.config_handler.ConfigHandler.get_db_pool_recycle", return_value="invalid")
    @patch("utils.config_handler.ConfigHandler.get_db_pool_pre_ping", side_effect=TypeError)
    def test_create_engine_invalid_config(
        self, mock_ping, mock_recycle, mock_timeout, mock_overflow, mock_pool_size, mock_create
    ):
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        mgr = _make_mgr()
        mgr._create_engine("sqlite:///test.db")
        mock_create.assert_called_once()

    @patch("data.cache.cache_manager.create_async_engine")
    @patch("utils.config_handler.ConfigHandler.get_db_connection_pool_size", return_value=None)
    @patch("utils.config_handler.ConfigHandler.get_db_max_overflow", return_value=None)
    @patch("utils.config_handler.ConfigHandler.get_db_pool_timeout", return_value=None)
    @patch("utils.config_handler.ConfigHandler.get_db_pool_recycle", return_value=None)
    @patch("utils.config_handler.ConfigHandler.get_db_pool_pre_ping", side_effect=ValueError)
    def test_create_engine_none_config(
        self, mock_ping, mock_recycle, mock_timeout, mock_overflow, mock_pool_size, mock_create
    ):
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        mgr = _make_mgr()
        mgr._create_engine("sqlite:///test.db")
        mock_create.assert_called_once()


class TestCacheManagerInitDb:
    @pytest.mark.asyncio
    async def test_init_db_already_initialized(self):
        mgr = _make_mgr()
        mgr._schema_initialized = True
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with patch.object(CacheManager, "_init_lock", new_callable=PropertyMock, return_value=mock_lock):
            await mgr.init_db()

    @pytest.mark.asyncio
    async def test_init_db_force(self):
        mgr = _make_mgr()
        mgr._schema_initialized = True
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(CacheManager, "_init_lock", new_callable=PropertyMock, return_value=mock_lock),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock()
            await mgr.init_db(force=True)
            mock_migrator.init_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_db_no_engine_no_connection(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mgr.engine = None
        mgr._get_connection_string = MagicMock(return_value=None)
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with patch.object(CacheManager, "_init_lock", new_callable=PropertyMock, return_value=mock_lock):
            with pytest.raises(RuntimeError, match="not configured"):
                await mgr.init_db()

    @pytest.mark.asyncio
    async def test_init_db_no_engine_creates_engine(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mgr.engine = None
        mgr._get_connection_string = MagicMock(return_value="sqlite:///test.db")
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(CacheManager, "_init_lock", new_callable=PropertyMock, return_value=mock_lock),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
            patch.object(mgr, "_create_engine") as mock_create,
        ):
            mock_migrator.init_db = AsyncMock()
            await mgr.init_db()
            mock_create.assert_called_once_with("sqlite:///test.db")

    @pytest.mark.asyncio
    async def test_init_db_migrator_failure(self):
        mgr = _make_mgr()
        mgr._schema_initialized = False
        mock_lock = AsyncMock()
        mock_lock.__aenter__ = AsyncMock(return_value=None)
        mock_lock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch.object(CacheManager, "_init_lock", new_callable=PropertyMock, return_value=mock_lock),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock(side_effect=Exception("migration failed"))
            with pytest.raises(Exception, match="migration failed"):
                await mgr.init_db(force=True)


class TestCacheManagerClose:
    @pytest.mark.asyncio
    async def test_close_disposes_engine(self):
        mgr = _make_mgr()
        mgr.engine = MagicMock()
        mgr.engine.dispose = AsyncMock()
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
            mgr.engine.dispose.assert_called_once()

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
        with (
            patch("utils.loop_local.get_loop_local") as mock_gll,
            patch("utils.loop_local.del_loop_local"),
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event", side_effect=Exception("no event")),
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
        with patch.object(mgr, "clear_all_cache", new_callable=AsyncMock, side_effect=Exception("reset failed")):
            with pytest.raises(Exception, match="reset failed"):
                await mgr.hard_reset()


class TestCacheManagerClearAllCache:
    @pytest.mark.asyncio
    async def test_clear_all_cache_success(self):
        mgr = _make_mgr()
        mock_conn = AsyncMock()
        mock_engine_ctx = AsyncMock()
        mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
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
            patch.object(CacheManager, "_maintenance_event", new_callable=PropertyMock, return_value=mock_event),
            patch("data.persistence.daos.base_dao.BaseDao._get_maintenance_event") as mock_evt,
            patch.object(mgr, "init_db", new_callable=AsyncMock),
        ):
            mock_evt.return_value = MagicMock()
            mock_evt.return_value.clear = MagicMock()
            mock_evt.return_value.set = MagicMock()

            mgr.engine = MagicMock()
            mock_conn = AsyncMock()
            mock_engine_ctx = AsyncMock()
            mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
            mgr.engine.begin = MagicMock(return_value=mock_engine_ctx)

            await mgr.clear_all_cache()
            mock_event.clear.assert_called()


class TestCacheManagerWaitForMaintenance:
    @pytest.mark.asyncio
    async def test_already_set(self):
        mgr = _make_mgr()
        mock_event = MagicMock()
        mock_event.is_set = MagicMock(return_value=True)
        with patch.object(CacheManager, "_maintenance_event", new_callable=PropertyMock, return_value=mock_event):
            await mgr.wait_for_maintenance()

    @pytest.mark.asyncio
    async def test_waits_then_set(self):
        mgr = _make_mgr()
        mock_event = MagicMock()
        mock_event.is_set = MagicMock(return_value=False)
        mock_event.wait = AsyncMock()
        with patch.object(CacheManager, "_maintenance_event", new_callable=PropertyMock, return_value=mock_event):
            await mgr.wait_for_maintenance()
            mock_event.wait.assert_called_once()


class TestCacheManagerWriteReadDb:
    @pytest.mark.asyncio
    async def test_write_db(self):
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._write_db = AsyncMock(return_value=1)
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            await mgr._write_db("INSERT INTO test VALUES (?)", ("val",))
            mock_dao._write_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_read_db(self):
        mgr = _make_mgr()
        mock_dao = MagicMock()
        mock_dao._read_db = AsyncMock(return_value=pd.DataFrame())
        with patch("data.cache.cache_manager.BaseDao", return_value=mock_dao):
            await mgr._read_db("SELECT * FROM test")
            mock_dao._read_db.assert_called_once()


class TestCacheManagerCheckComprehensiveHealth:
    def _make_health_conn(self):
        mock_conn = MagicMock()
        mock_conn.execution_options = AsyncMock(return_value=mock_conn)
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=50)
        mock_conn.execute = AsyncMock(return_value=mock_result)
        return mock_conn

    def _make_engine_ctx(self, mock_conn):
        mock_engine_ctx = MagicMock()
        mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
        return mock_engine_ctx

    @pytest.mark.asyncio
    async def test_basic_health_check(self):
        mgr = _make_mgr()
        mgr.stock_dao.get_active_stock_count = AsyncMock(return_value=100)
        mgr.quote_dao.get_date_range = AsyncMock(return_value=("20240101", "20240614"))
        mgr.stock_dao.count_trade_days = AsyncMock(return_value=100)
        mgr.stock_dao.count_expected_rows = AsyncMock(return_value=5000)

        mock_conn = self._make_health_conn()
        mock_engine_ctx = self._make_engine_ctx(mock_conn)
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
        mock_engine_ctx = self._make_engine_ctx(mock_conn)
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
        mock_engine_ctx = self._make_engine_ctx(mock_conn)
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

        mock_engine_ctx = self._make_engine_ctx(mock_conn)
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
        mock_engine_ctx = self._make_engine_ctx(mock_conn)
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

        mock_engine_ctx = AsyncMock()
        mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
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

        mock_engine_ctx = AsyncMock()
        mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
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

        mock_engine_ctx = AsyncMock()
        mock_engine_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine_ctx.__aexit__ = AsyncMock(return_value=None)
        mgr.engine = MagicMock()
        mgr.engine.connect = MagicMock(return_value=mock_engine_ctx)

        with patch("data.persistence.daos.quote_dao._SAFE_TABLE_NAMES", {"daily_quotes"}):
            result = await mgr.check_table_has_data("daily_quotes")
            assert result is False


class TestCacheManagerGetConnectionString:
    @patch("config.DB_URL", "sqlite:///fallback.db")
    def test_fallback_to_config(self):
        mgr = CacheManager.__new__(CacheManager)
        with patch.object(CacheManager, "__init__", lambda self: None):
            mgr._initialized = False
            with patch("utils.config_handler.ConfigHandler.get_db_url", return_value=None):
                result = mgr._get_connection_string()
                assert result == "sqlite:///fallback.db"

    @patch("config.DB_URL", None)
    def test_no_url_available(self):
        mgr = CacheManager.__new__(CacheManager)
        with patch.object(CacheManager, "__init__", lambda self: None):
            mgr._initialized = False
            with patch("utils.config_handler.ConfigHandler.get_db_url", return_value=None):
                result = mgr._get_connection_string()
                assert result is None

    def test_config_handler_has_url(self):
        mgr = CacheManager.__new__(CacheManager)
        with patch.object(CacheManager, "__init__", lambda self: None):
            mgr._initialized = False
            with patch("utils.config_handler.ConfigHandler.get_db_url", return_value="postgresql://user:pass@host/db"):
                result = mgr._get_connection_string()
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
    @patch("data.cache.cache_manager.ConfigHandler.get_db_url", return_value=None)
    @patch("config.DB_URL", None)
    def test_init_no_connection_string(self, mock_url):
        CacheManager._instance = None
        CacheManager._initialized = False
        mgr = CacheManager()
        assert mgr.engine is None

    @patch("data.cache.cache_manager.ConfigHandler.get_db_url", return_value="sqlite:///test.db")
    @patch("data.cache.cache_manager.create_async_engine")
    def test_init_with_connection_string(self, mock_create, mock_url):
        CacheManager._instance = None
        CacheManager._initialized = False
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        with (
            patch("utils.config_handler.ConfigHandler.get_db_connection_pool_size", return_value="10"),
            patch("utils.config_handler.ConfigHandler.get_db_max_overflow", return_value="5"),
            patch("utils.config_handler.ConfigHandler.get_db_pool_timeout", return_value="30"),
            patch("utils.config_handler.ConfigHandler.get_db_pool_recycle", return_value="1800"),
            patch("utils.config_handler.ConfigHandler.get_db_pool_pre_ping", return_value=True),
        ):
            mgr = CacheManager()
            assert mgr.engine == mock_engine


class TestCacheManagerDelegationsWithWait:
    @pytest.mark.asyncio
    async def test_check_data_exists(self):
        mgr = _make_mgr()
        mgr.quote_dao.check_data_exists = AsyncMock(return_value=True)
        with patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock):
            result = await mgr.check_data_exists("20240614")
            assert result is True

    @pytest.mark.asyncio
    async def test_get_latest_trade_date(self):
        mgr = _make_mgr()
        mgr.quote_dao.get_latest_trade_date = AsyncMock(return_value="20240614")
        with patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock):
            result = await mgr.get_latest_trade_date()
            assert result == "20240614"

    @pytest.mark.asyncio
    async def test_get_cached_trade_dates(self):
        mgr = _make_mgr()
        mgr.quote_dao.get_cached_trade_dates = AsyncMock(return_value=set())
        with patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock):
            await mgr.get_cached_trade_dates()
            mgr.quote_dao.get_cached_trade_dates.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_dates_for_table(self):
        mgr = _make_mgr()
        mgr.quote_dao.get_cached_dates_for_table = AsyncMock(return_value=set())
        with patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock):
            await mgr.get_cached_dates_for_table("daily_quotes")
            mgr.quote_dao.get_cached_dates_for_table.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_field_completeness(self):
        mgr = _make_mgr()
        mgr.quote_dao.get_field_completeness = AsyncMock(return_value={})
        with patch.object(CacheManager, "wait_for_maintenance", new_callable=AsyncMock):
            await mgr.get_field_completeness("20240614")
            mgr.quote_dao.get_field_completeness.assert_called_once()


class TestCacheManagerBulkQualityScores:
    @pytest.mark.asyncio
    async def test_get_bulk_table_counts(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_bulk_table_counts = AsyncMock(return_value={})
        await mgr.get_bulk_table_counts("daily_quotes", "2024-01-01", "2024-06-14")
        mgr.quote_dao.get_bulk_table_counts.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_bulk_expected_stock_counts(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_bulk_expected_stock_counts = AsyncMock(return_value={})
        await mgr.get_bulk_expected_stock_counts("2024-01-01", "2024-06-14")
        mgr.quote_dao.get_bulk_expected_stock_counts.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_bulk_sync_quality_scores(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_bulk_sync_quality_scores = AsyncMock(return_value={})
        await mgr.get_bulk_sync_quality_scores("2024-01-01", "2024-06-14")
        mgr.quote_dao.get_bulk_sync_quality_scores.assert_called_once()

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
        await mgr.get_sync_quality_score("2024-06-14")
        mgr.quote_dao.get_sync_quality_score.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_field_completeness(self):
        mgr = CacheManager.__new__(CacheManager)
        mgr._initialized = True
        mgr._maintenance_event_lazy = None
        mgr.quote_dao = MagicMock()
        mgr.quote_dao.get_field_completeness = AsyncMock(return_value={})
        with patch("utils.loop_local.get_loop_local") as mock_gll:
            mock_gll.return_value = MagicMock()
            mock_gll.return_value.is_set = MagicMock(return_value=True)
            await mgr.get_field_completeness("2024-06-14")
            mgr.quote_dao.get_field_completeness.assert_called_once()


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
    async def test_save_stock_basic(self):
        mgr = self._make_mgr()
        mgr.stock_dao.save_stock_basic = AsyncMock(return_value=1)
        await mgr.save_stock_basic(pd.DataFrame({"ts_code": ["000001.SZ"]}))
        mgr.stock_dao.save_stock_basic.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_stock_basic(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_stock_basic = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_stock_basic()
        mgr.stock_dao.get_stock_basic.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_daily_quotes(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_daily_quotes = AsyncMock(return_value=1)
        await mgr.save_daily_quotes(pd.DataFrame())
        mgr.quote_dao.save_daily_quotes.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_daily_quotes(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_daily_quotes(ts_code="000001.SZ")
        mgr.quote_dao.get_daily_quotes.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_daily_indicators(self):
        mgr = self._make_mgr()
        mgr.market_dao.save_daily_indicators = AsyncMock(return_value=1)
        await mgr.save_daily_indicators(pd.DataFrame())
        mgr.market_dao.save_daily_indicators.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_daily_indicators(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_daily_indicators(ts_code="000001.SZ")
        mgr.market_dao.get_daily_indicators.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_financial_reports(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_financial_reports = AsyncMock(return_value=1)
        await mgr.save_financial_reports(pd.DataFrame())
        mgr.financial_dao.save_financial_reports.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_sync_status(self):
        mgr = self._make_mgr()
        mgr.sync_dao.update_sync_status = AsyncMock()
        await mgr.update_sync_status("test_table", "20240614", 100)
        mgr.sync_dao.update_sync_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_sync_status(self):
        mgr = self._make_mgr()
        mgr.sync_dao.get_sync_status = AsyncMock(return_value=None)
        await mgr.get_sync_status("test_table")
        mgr.sync_dao.get_sync_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_market_news(self):
        mgr = self._make_mgr()
        mgr.market_dao.save_market_news = AsyncMock()
        await mgr.save_market_news({"content": "test"})
        mgr.market_dao.save_market_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_market_news(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_market_news = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_market_news()
        mgr.market_dao.get_market_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_screening_data(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_screening_data()
        mgr.screener_dao.get_screening_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_concepts(self):
        mgr = self._make_mgr()
        mgr.stock_dao.save_concepts = AsyncMock(return_value=1)
        await mgr.save_concepts(pd.DataFrame())
        mgr.stock_dao.save_concepts.assert_called_once()

    @pytest.mark.asyncio
    async def test_overwrite_concepts(self):
        mgr = self._make_mgr()
        mgr.stock_dao.overwrite_concepts = AsyncMock(return_value=1)
        await mgr.overwrite_concepts(pd.DataFrame())
        mgr.stock_dao.overwrite_concepts.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_concepts(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_concepts = AsyncMock(return_value={})
        await mgr.get_concepts()
        mgr.stock_dao.get_concepts.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_moneyflow(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_moneyflow = AsyncMock(return_value=1)
        await mgr.save_moneyflow(pd.DataFrame())
        mgr.quote_dao.save_moneyflow.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_northbound(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_northbound = AsyncMock(return_value=1)
        await mgr.save_northbound(pd.DataFrame())
        mgr.quote_dao.save_northbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_fina_forecast(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_fina_forecast = AsyncMock(return_value=1)
        await mgr.save_fina_forecast(pd.DataFrame())
        mgr.financial_dao.save_fina_forecast.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_fina_mainbz(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_fina_mainbz = AsyncMock(return_value=1)
        await mgr.save_fina_mainbz(pd.DataFrame())
        mgr.financial_dao.save_fina_mainbz.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_pledge_stat(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_pledge_stat = AsyncMock(return_value=1)
        await mgr.save_pledge_stat(pd.DataFrame())
        mgr.financial_dao.save_pledge_stat.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_repurchase(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_repurchase = AsyncMock(return_value=1)
        await mgr.save_repurchase(pd.DataFrame())
        mgr.financial_dao.save_repurchase.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_dividend(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_dividend = AsyncMock(return_value=1)
        await mgr.save_dividend(pd.DataFrame())
        mgr.financial_dao.save_dividend.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_index_daily(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_index_daily = AsyncMock(return_value=1)
        await mgr.save_index_daily(pd.DataFrame())
        mgr.quote_dao.save_index_daily.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_index_dailybasic(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_index_dailybasic = AsyncMock(return_value=1)
        await mgr.save_index_dailybasic(pd.DataFrame())
        mgr.quote_dao.save_index_dailybasic.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_index_daily(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_index_daily = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_index_daily()
        mgr.quote_dao.get_index_daily.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_limit_list(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_limit_list = AsyncMock(return_value=1)
        await mgr.save_limit_list(pd.DataFrame())
        mgr.quote_dao.save_limit_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_margin_daily(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_margin_daily = AsyncMock(return_value=1)
        await mgr.save_margin_daily(pd.DataFrame())
        mgr.quote_dao.save_margin_daily.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_suspend_d(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_suspend_d = AsyncMock(return_value=1)
        await mgr.save_suspend_d(pd.DataFrame())
        mgr.quote_dao.save_suspend_d.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_fina_audit(self):
        mgr = self._make_mgr()
        mgr.financial_dao.save_fina_audit = AsyncMock(return_value=1)
        await mgr.save_fina_audit(pd.DataFrame())
        mgr.financial_dao.save_fina_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_top_list(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_top_list = AsyncMock(return_value=1)
        await mgr.save_top_list(pd.DataFrame())
        mgr.quote_dao.save_top_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_top_list(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_top_list = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_top_list()
        mgr.quote_dao.get_top_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_block_trade(self):
        mgr = self._make_mgr()
        mgr.quote_dao.save_block_trade = AsyncMock(return_value=1)
        await mgr.save_block_trade(pd.DataFrame())
        mgr.quote_dao.save_block_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_block_trade(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_block_trade()
        mgr.quote_dao.get_block_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_moneyflow(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_moneyflow()
        mgr.quote_dao.get_moneyflow.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_northbound(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_northbound = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_northbound()
        mgr.quote_dao.get_northbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_screening_history(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_screening_history = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_screening_history()
        mgr.screener_dao.get_screening_history.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_history_tree(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_history_tree = AsyncMock(return_value=[])
        await mgr.get_history_tree()
        mgr.screener_dao.get_history_tree.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_history_records(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_history_records = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_history_records(trade_date="20240614")
        mgr.screener_dao.get_history_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pending_reviews(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_pending_reviews = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_pending_reviews()
        mgr.screener_dao.get_pending_reviews.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_learning_examples(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_learning_examples = AsyncMock(return_value=[])
        await mgr.get_learning_examples()
        mgr.screener_dao.get_learning_examples.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_completed_step4_stocks(self):
        mgr = self._make_mgr()
        mgr.sync_dao.get_completed_step4_stocks = AsyncMock(return_value=set())
        await mgr.get_completed_step4_stocks()
        mgr.sync_dao.get_completed_step4_stocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_stock_step4_completed(self):
        mgr = self._make_mgr()
        mgr.sync_dao.mark_stock_step4_completed = AsyncMock()
        await mgr.mark_stock_step4_completed("000001.SZ")
        mgr.sync_dao.mark_stock_step4_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_step4_sync_status(self):
        mgr = self._make_mgr()
        mgr.sync_dao.clear_step4_sync_status = AsyncMock()
        await mgr.clear_step4_sync_status()
        mgr.sync_dao.clear_step4_sync_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_trade_cal(self):
        mgr = self._make_mgr()
        mgr.stock_dao.save_trade_cal = AsyncMock(return_value=1)
        await mgr.save_trade_cal(pd.DataFrame())
        mgr.stock_dao.save_trade_cal.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_trade_cal(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_trade_cal = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_trade_cal()
        mgr.stock_dao.get_trade_cal.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_trade_cal_range(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_trade_cal_range = AsyncMock(return_value=(None, None))
        await mgr.get_trade_cal_range()
        mgr.stock_dao.get_trade_cal_range.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_macro_economy(self):
        mgr = self._make_mgr()
        mgr.macro_dao.save_macro_economy = AsyncMock(return_value=1)
        await mgr.save_macro_economy(pd.DataFrame())
        mgr.macro_dao.save_macro_economy.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_shibor_daily(self):
        mgr = self._make_mgr()
        mgr.macro_dao.save_shibor_daily = AsyncMock(return_value=1)
        await mgr.save_shibor_daily(pd.DataFrame())
        mgr.macro_dao.save_shibor_daily.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_holder_number(self):
        mgr = self._make_mgr()
        mgr.holder_dao.save_holder_number = AsyncMock(return_value=1)
        await mgr.save_holder_number(pd.DataFrame())
        mgr.holder_dao.save_holder_number.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_top10_holders(self):
        mgr = self._make_mgr()
        mgr.holder_dao.save_top10_holders = AsyncMock(return_value=1)
        await mgr.save_top10_holders(pd.DataFrame())
        mgr.holder_dao.save_top10_holders.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_index_weights(self):
        mgr = self._make_mgr()
        mgr.market_dao.save_index_weights = AsyncMock(return_value=1)
        await mgr.save_index_weights(pd.DataFrame())
        mgr.market_dao.save_index_weights.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_index_weights(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_index_weights = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_index_weights("399300.SZ", "20240614")
        mgr.market_dao.get_index_weights.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_latest_index_weight_date(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_latest_index_weight_date = AsyncMock(return_value=None)
        await mgr.get_latest_index_weight_date()
        mgr.market_dao.get_latest_index_weight_date.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_moneyflow_hsgt(self):
        mgr = self._make_mgr()
        mgr.market_dao.save_moneyflow_hsgt = AsyncMock(return_value=1)
        await mgr.save_moneyflow_hsgt(pd.DataFrame())
        mgr.market_dao.save_moneyflow_hsgt.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_moneyflow_hsgt(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_moneyflow_hsgt()
        mgr.market_dao.get_moneyflow_hsgt.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_financial_reports_history(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_financial_reports_history("000001.SZ")
        mgr.financial_dao.get_financial_reports_history.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_fina_audit(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_fina_audit_batch = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_fina_audit("000001.SZ")
        mgr.financial_dao.get_fina_audit_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_fina_mainbz(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_fina_mainbz("000001.SZ")
        mgr.financial_dao.get_fina_mainbz.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_dividend(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_dividend_batch = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_dividend("000001.SZ")
        mgr.financial_dao.get_dividend_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pledge_stat(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_pledge_stat_batch = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_pledge_stat("000001.SZ")
        mgr.financial_dao.get_pledge_stat_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_top10_holders(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_top10_holders("000001.SZ")
        mgr.holder_dao.get_top10_holders.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_stk_holdernumber(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_stk_holdernumber("000001.SZ")
        mgr.holder_dao.get_stk_holdernumber.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_existing_top10_ts_codes(self):
        mgr = self._make_mgr()
        mgr.holder_dao.get_existing_top10_ts_codes = AsyncMock(return_value=set())
        await mgr.get_existing_top10_ts_codes("20240331")
        mgr.holder_dao.get_existing_top10_ts_codes.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_macro_economy(self):
        mgr = self._make_mgr()
        mgr.macro_dao.get_macro_economy_latest = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_macro_economy()
        mgr.macro_dao.get_macro_economy_latest.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_shibor_latest(self):
        mgr = self._make_mgr()
        mgr.macro_dao.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_shibor_latest()
        mgr.macro_dao.get_shibor_latest.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_concept_count(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_concept_count = AsyncMock(return_value=100)
        result = await mgr.get_concept_count()
        assert result == 100

    @pytest.mark.asyncio
    async def test_get_latest_northbound(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_latest_northbound = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_latest_northbound()
        mgr.quote_dao.get_latest_northbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_latest_indicators(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_latest_indicators = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_latest_indicators()
        mgr.financial_dao.get_latest_indicators.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_indicator_dates(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_cached_indicator_dates = AsyncMock(return_value=set())
        await mgr.get_cached_indicator_dates()
        mgr.financial_dao.get_cached_indicator_dates.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_financial_records(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_cached_financial_records = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_cached_financial_records()
        mgr.financial_dao.get_cached_financial_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_fundamental_screening_data(self):
        mgr = self._make_mgr()
        mgr.screener_dao.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_fundamental_screening_data()
        mgr.screener_dao.get_fundamental_screening_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_incomplete_financial_stocks(self):
        mgr = self._make_mgr()
        mgr.financial_dao.get_incomplete_financial_stocks = AsyncMock(return_value=set())
        await mgr.get_incomplete_financial_stocks()
        mgr.financial_dao.get_incomplete_financial_stocks.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_daily_indicators_bulk(self):
        mgr = self._make_mgr()
        mgr.market_dao.get_daily_indicators_bulk = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_daily_indicators_bulk(["000001.SZ"])
        mgr.market_dao.get_daily_indicators_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_index_daily_range(self):
        mgr = self._make_mgr()
        mgr.quote_dao.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())
        await mgr.get_index_daily_range(["399300.SZ"])
        mgr.quote_dao.get_index_daily_range.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_start_date_by_trade_days(self):
        mgr = self._make_mgr()
        mgr.stock_dao.get_start_date_by_trade_days = AsyncMock(return_value="20240101")
        await mgr.get_start_date_by_trade_days("20240614", 250)
        mgr.stock_dao.get_start_date_by_trade_days.assert_called_once()


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
        assert result["publish_time"] is not None

    def test_with_invalid_time(self):
        item = {"content": "test", "time": "invalid_date"}
        result = CacheManager.normalize_news_item(item)
        assert result["publish_time"] is not None

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
    def test_empty_url(self):
        mgr = CacheManager.__new__(CacheManager)
        assert mgr._sanitize_url("") == "None"

    def test_url_with_password(self):
        mgr = CacheManager.__new__(CacheManager)
        result = mgr._sanitize_url("postgresql://user:secret@localhost/db")
        assert "secret" not in result
        assert "****" in result

    def test_url_without_password(self):
        mgr = CacheManager.__new__(CacheManager)
        result = mgr._sanitize_url("sqlite:///test.db")
        assert "test.db" in result


class TestCacheManagerUsesMetadataTables:
    def test_check_table_has_data_uses_metadata_not_sa_table(self):
        import inspect
        from data.cache.cache_manager import CacheManager

        source = inspect.getsource(CacheManager.check_table_has_data)
        assert "ModelsBase.metadata.tables" in source
        assert "sa.table(" not in source

    def test_health_check_uses_metadata_not_sa_table(self):
        import inspect
        from data.cache.cache_manager import CacheManager

        source = inspect.getsource(CacheManager.check_comprehensive_health)
        assert "ModelsBase.metadata.tables" in source
        assert "sa.table(" not in source
