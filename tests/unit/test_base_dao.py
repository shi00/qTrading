import asyncio
import datetime
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import numpy as np

from data.persistence.daos.base_dao import BaseDao


class TestBaseDaoPrepareDataParams:
    def test_none_df(self):
        result = BaseDao._prepare_data_params(None, ["col1"])
        assert result is None

    def test_empty_df(self):
        result = BaseDao._prepare_data_params(pd.DataFrame(), ["col1"])
        assert result is None

    def test_missing_cols_filled_with_none(self):
        df = pd.DataFrame({"col1": [1]})
        result = BaseDao._prepare_data_params(df, ["col1", "col2"])
        assert result is not None
        assert len(result) == 1
        assert result[0][1] is None

    def test_numpy_int_conversion(self):
        df = pd.DataFrame({"col1": [np.int64(42)]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] == 42
        assert isinstance(result[0][0], int)

    def test_numpy_float_conversion(self):
        df = pd.DataFrame({"col1": [np.float64(3.14)]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] == 3.14
        assert isinstance(result[0][0], float)

    def test_numpy_bool_conversion(self):
        df = pd.DataFrame({"col1": [np.bool_(True)]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] is True
        assert isinstance(result[0][0], bool)

    def test_nan_to_none(self):
        df = pd.DataFrame({"col1": [float("nan")]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] is None

    def test_none_value_stays_none(self):
        df = pd.DataFrame({"col1": [None]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] is None

    def test_timestamp_conversion(self):
        df = pd.DataFrame({"col1": [pd.Timestamp("2024-06-15")]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] is not None

    def test_multiple_rows(self):
        df = pd.DataFrame({"col1": [1, 2, 3]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert len(result) == 3


class TestBaseDaoQuoteColumns:
    def test_single_column(self):
        result = BaseDao._quote_columns(["col1"])
        assert result == '"col1"'

    def test_multiple_columns(self):
        result = BaseDao._quote_columns(["col1", "col2"])
        assert result == '"col1","col2"'

    def test_reserved_word(self):
        result = BaseDao._quote_columns(["date"])
        assert result == '"date"'


class TestBaseDaoInit:
    def test_engine_stored(self):
        engine = MagicMock()
        dao = BaseDao(engine)
        assert dao.engine is engine


class TestBaseDaoConvertParam:
    def test_none(self):
        dao = BaseDao(MagicMock())
        assert dao._convert_param_for_asyncpg(None) is None

    def test_string(self):
        dao = BaseDao(MagicMock())
        assert dao._convert_param_for_asyncpg("hello") == "hello"

    def test_int(self):
        dao = BaseDao(MagicMock())
        assert dao._convert_param_for_asyncpg(42) == 42

    def test_float(self):
        dao = BaseDao(MagicMock())
        assert dao._convert_param_for_asyncpg(3.14) == 3.14

    def test_date(self):
        import datetime

        dao = BaseDao(MagicMock())
        d = datetime.date(2024, 6, 15)
        result = dao._convert_param_for_asyncpg(d)
        assert result == d


class TestBaseDaoWriteDb:
    @pytest.mark.asyncio
    async def test_engine_none_raises(self):
        dao = BaseDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao._write_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_is_many_no_params(self):
        dao = BaseDao(MagicMock())
        result = await dao._write_db("INSERT", is_many=True, params=None)
        assert result == 0


class TestBaseDaoMaintenanceEvent:
    def test_get_maintenance_event(self):
        evt = BaseDao._get_maintenance_event()
        assert evt is not None


class TestBaseDaoConvertParamForAsyncpg:
    def test_none(self):
        assert BaseDao._convert_param_for_asyncpg(None) is None

    def test_yyyymmdd_string(self):
        result = BaseDao._convert_param_for_asyncpg("20240615")
        assert isinstance(result, datetime.date)
        assert result == datetime.date(2024, 6, 15)

    def test_yyyy_mm_dd_string(self):
        result = BaseDao._convert_param_for_asyncpg("2024-06-15")
        assert isinstance(result, datetime.date)
        assert result == datetime.date(2024, 6, 15)

    def test_yyyy_slash_mm_slash_dd_string(self):
        result = BaseDao._convert_param_for_asyncpg("2024/06/15")
        assert isinstance(result, datetime.date)
        assert result == datetime.date(2024, 6, 15)

    def test_iso_datetime_string(self):
        result = BaseDao._convert_param_for_asyncpg("2024-06-15T10:30:00")
        assert isinstance(result, datetime.date)

    def test_non_date_string(self):
        result = BaseDao._convert_param_for_asyncpg("hello")
        assert result == "hello"

    def test_int_value(self):
        result = BaseDao._convert_param_for_asyncpg(42)
        assert result == 42

    def test_date_object(self):
        d = datetime.date(2024, 6, 15)
        result = BaseDao._convert_param_for_asyncpg(d)
        assert result == d

    def test_invalid_date_string(self):
        result = BaseDao._convert_param_for_asyncpg("99999999")
        assert result == "99999999"

    def test_short_digit_string(self):
        result = BaseDao._convert_param_for_asyncpg("1234567")
        assert result == "1234567"


class TestBaseDaoReadDb:
    @pytest.mark.asyncio
    async def test_engine_none_raises(self):
        dao = BaseDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_read_success(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1, "test")]
        mock_result.keys.return_value = ["id", "name"]
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
        ):
            mock_cm._instance = None
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame([(1, "test")], columns=["id", "name"]))
            result = await dao._read_db("SELECT * FROM t")
            assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_read_with_params(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = []
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
        ):
            mock_cm._instance = None
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame())
            result = await dao._read_db("SELECT * FROM t WHERE id = $1", params=[1])
            assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_read_error_returns_empty_df(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Read Error")
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._read_db("SELECT * FROM t", suppress_errors=True)
            assert isinstance(result, pd.DataFrame)
            assert result.empty

    @pytest.mark.asyncio
    async def test_read_no_suppress_raises(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Read Error")
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(Exception, match="Read Error"):
                await dao._read_db("SELECT * FROM t", suppress_errors=False)


class TestBaseDaoSaveUpsert:
    @pytest.mark.asyncio
    async def test_engine_none_raises(self):
        dao = BaseDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao._save_upsert(pd.DataFrame({"a": [1]}), "t", ["a"], ["a"])

    @pytest.mark.asyncio
    async def test_none_df(self):
        dao = BaseDao(MagicMock())
        result = await dao._save_upsert(None, "t", ["a"], ["a"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_df(self):
        dao = BaseDao(MagicMock())
        result = await dao._save_upsert(pd.DataFrame(), "t", ["a"], ["a"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_table_not_found(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
        ):
            mock_cm._instance = None
            mock_meta.tables = {}
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "nonexistent", ["a"], ["a"])
            assert result == 0

    @pytest.mark.asyncio
    async def test_pk_missing_in_df(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        mock_table = MagicMock()
        mock_table.columns = {}
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a", "pk_col"], ["pk_col"])
            assert result == 0


class TestBaseDaoWriteDbExtended:
    @pytest.mark.asyncio
    async def test_write_success_with_conn(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)", params=(1,), conn=mock_conn)
            mock_conn.exec_driver_sql.assert_called_once()
            assert result == 1

    @pytest.mark.asyncio
    async def test_write_success_without_conn(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)", params=(1,))
            assert result == 1

    @pytest.mark.asyncio
    async def test_write_is_many_with_params(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            params = [(1, "a"), (2, "b")]
            result = await dao._write_db("INSERT INTO t VALUES ($1, $2)", params=params, is_many=True, conn=mock_conn)
            assert result == 2

    @pytest.mark.asyncio
    async def test_write_disposed_engine(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            result = await dao._write_db("INSERT INTO t VALUES ($1)")
            assert result == 0

    @pytest.mark.asyncio
    async def test_write_sync_engine_none(self):
        mock_engine = MagicMock()
        mock_engine.sync_engine = None
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)")
            assert result == 0

    @pytest.mark.asyncio
    async def test_write_error_suppressed(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Write failed")
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn, suppress_errors=True)
            assert result == 0

    @pytest.mark.asyncio
    async def test_write_error_not_suppressed(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Write failed")
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(Exception, match="Write failed"):
                await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn, suppress_errors=False)

    @pytest.mark.asyncio
    async def test_write_cancelled_error_propagates(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = asyncio.CancelledError()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(asyncio.CancelledError):
                await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn)

    @pytest.mark.asyncio
    async def test_write_connection_closed_during_shutdown(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("no active connection")
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn)
            assert result == 0

    @pytest.mark.asyncio
    async def test_write_params_as_tuple(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)", params=(1,), conn=mock_conn)
            assert result == 1

    @pytest.mark.asyncio
    async def test_write_no_params(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("DELETE FROM t", conn=mock_conn)
            assert result == 1


class TestBaseDaoSaveUpsertExtended:
    @pytest.mark.asyncio
    async def test_upsert_success_with_conn(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], conn=mock_conn)
            assert result == 1

    @pytest.mark.asyncio
    async def test_upsert_no_update_cols(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_table.columns = {}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], conn=mock_conn)
            assert result == 1

    @pytest.mark.asyncio
    async def test_upsert_missing_non_pk_cols_filled(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_col_b = MagicMock()
        mock_col_b.name = "b"
        mock_table.c = {"a": mock_col_a, "b": mock_col_b}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1, "b": None}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a", "b"], ["a"], conn=mock_conn)
            assert result == 1

    @pytest.mark.asyncio
    async def test_upsert_error_suppressed(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("Upsert failed")
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            result = await dao._save_upsert(
                pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], suppress_errors=True, conn=mock_conn
            )
            assert result == 0

    @pytest.mark.asyncio
    async def test_upsert_cancelled_propagates(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = asyncio.CancelledError()
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            with pytest.raises(asyncio.CancelledError):
                await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], conn=mock_conn)

    @pytest.mark.asyncio
    async def test_upsert_connection_closed_during_shutdown(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("database is closed")
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], conn=mock_conn)
            assert result == 0

    @pytest.mark.asyncio
    async def test_upsert_without_conn(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"a": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            result = await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"])
            assert result == 1


class TestBaseDaoReadDbExtended:
    @pytest.mark.asyncio
    async def test_read_disposed_engine(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            result = await dao._read_db("SELECT 1")
            assert isinstance(result, pd.DataFrame)
            assert result.empty

    @pytest.mark.asyncio
    async def test_read_cancelled_propagates(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = asyncio.CancelledError()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(asyncio.CancelledError):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_read_connection_closed_during_shutdown(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("no active connection")
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._read_db("SELECT 1")
            assert isinstance(result, pd.DataFrame)
            assert result.empty

    @pytest.mark.asyncio
    async def test_read_params_as_list(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = []
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
        ):
            mock_cm._instance = None
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame())
            result = await dao._read_db("SELECT * FROM t WHERE id = $1", params=[1])
            assert isinstance(result, pd.DataFrame)


class TestBaseDaoPrepareDataParamsExtended:
    def test_with_table_name_date_conversion(self):
        mock_table = MagicMock()
        mock_date_col = MagicMock()
        mock_date_col.name = "trade_date"
        mock_date_col.type = MagicMock(spec=[])
        from sqlalchemy import Date

        mock_date_col.type = Date()
        mock_table.columns = [mock_date_col]

        df = pd.DataFrame({"trade_date": ["20240615"], "col1": [1]})
        with patch("data.persistence.models.Base.metadata") as mock_meta:
            mock_meta.tables = {"test_table": mock_table}
            result = BaseDao._prepare_data_params(df, ["trade_date", "col1"], table_name="test_table")
            assert result is not None

    def test_nat_to_none(self):
        df = pd.DataFrame({"col1": [pd.NaT]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] is None

    def test_numpy_int32_conversion(self):
        df = pd.DataFrame({"col1": [np.int32(42)]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] == 42
        assert isinstance(result[0][0], int)

    def test_numpy_float32_conversion(self):
        df = pd.DataFrame({"col1": [np.float32(3.14)]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert isinstance(result[0][0], float)

    def test_regular_value_passthrough(self):
        df = pd.DataFrame({"col1": ["hello"]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result[0][0] == "hello"
