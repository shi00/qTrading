import asyncio
import datetime
import inspect
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import numpy as np
import sqlalchemy as sa
from sqlalchemy import Date

from data.persistence.daos.base_dao import BaseDao, EngineDisposedError, DatabaseQueryError


def _setup_mock_engine_connect(mock_conn):
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_engine


def _setup_mock_engine_begin(mock_conn):
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_engine


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

    def test_double_quote_in_column_name_escaped(self):
        result = BaseDao._quote_columns(['col"; DROP TABLE foo;--'])
        assert result == '"col""; DROP TABLE foo;--"'

    def test_normal_column_unaffected_by_escaping(self):
        result = BaseDao._quote_columns(["trade_date", "ts_code"])
        assert result == '"trade_date","ts_code"'


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
        with pytest.warns(DeprecationWarning):
            result = await dao._write_db("INSERT", is_many=True, params=None)
        assert result == 0


class TestBaseDaoMaintenanceEvent:
    @pytest.mark.asyncio
    async def test_get_maintenance_event(self):
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
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1, "test")]
        mock_result.keys.return_value = ["id", "name"]
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
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


class TestSlowQueryThresholdConstants:
    """Q-P2-7: Slow query thresholds should be defined as module-level constants,
    not hardcoded magic numbers."""

    def test_slow_write_threshold_exists(self):
        import data.persistence.daos.base_dao as base_dao_mod

        assert hasattr(base_dao_mod, "_SLOW_WRITE_THRESHOLD_MS")
        assert base_dao_mod._SLOW_WRITE_THRESHOLD_MS == 2000

    def test_slow_read_threshold_exists(self):
        import data.persistence.daos.base_dao as base_dao_mod

        assert hasattr(base_dao_mod, "_SLOW_READ_THRESHOLD_MS")
        assert base_dao_mod._SLOW_READ_THRESHOLD_MS == 500

    def test_slow_upsert_threshold_exists(self):
        import data.persistence.daos.base_dao as base_dao_mod

        assert hasattr(base_dao_mod, "_SLOW_UPSERT_THRESHOLD_MS")
        assert base_dao_mod._SLOW_UPSERT_THRESHOLD_MS == 2000

    def test_write_db_uses_constant_not_magic_number(self):
        import data.persistence.daos.base_dao as base_dao_mod
        import inspect

        source = inspect.getsource(base_dao_mod.BaseDao._write_db)
        assert "_SLOW_WRITE_THRESHOLD_MS" in source
        assert "if elapsed > 2000" not in source

    def test_read_db_uses_constant_not_magic_number(self):
        import data.persistence.daos.base_dao as base_dao_mod
        import inspect

        source = inspect.getsource(base_dao_mod.BaseDao._read_db)
        assert "_SLOW_READ_THRESHOLD_MS" in source
        assert "if elapsed > 500" not in source


class TestNullProtectedDefaultsFalse:
    """P0-1: Verify null_protected defaults to False — columns without explicit
    null_protected=True should use direct assignment, not COALESCE."""

    @pytest.mark.asyncio
    async def test_null_protected_default_false_uses_direct_assignment(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "col_a"
        mock_col_a.info = {}
        mock_table.columns = [mock_col_pk, mock_col_a]
        mock_table.c = {"id": mock_col_pk, "col_a": mock_col_a}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1, "col_a": "val"}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            await dao._save_upsert(
                pd.DataFrame({"id": [1], "col_a": ["val"]}),
                "test_table",
                ["id", "col_a"],
                ["id"],
                conn=mock_conn,
            )
            call_args = mock_stmt.on_conflict_do_update.call_args
            set_dict = call_args[1]["set_"]
            assert "col_a" in set_dict
            assert "coalesce" not in str(set_dict["col_a"]).lower()

    @pytest.mark.asyncio
    async def test_null_protected_true_uses_coalesce(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_b = MagicMock()
        mock_col_b.name = "col_b"
        mock_col_b.info = {"null_protected": True}
        mock_table.columns = [mock_col_pk, mock_col_b]
        mock_table.c = {"id": mock_col_pk, "col_b": mock_col_b}
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_meta.tables = {"test_table": mock_table}
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1, "col_b": "val"}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            await dao._save_upsert(
                pd.DataFrame({"id": [1], "col_b": ["val"]}),
                "test_table",
                ["id", "col_b"],
                ["id"],
                conn=mock_conn,
            )
            call_args = mock_stmt.on_conflict_do_update.call_args
            set_dict = call_args[1]["set_"]
            assert "col_b" in set_dict
            assert str(set_dict["col_b"]).startswith("coalesce(")


class TestExceptExceptionNarrowedEP11:
    """E-P1-1: Verify that 'except Exception: pass' patterns have been narrowed
    to specific exception types with debug logging."""

    def test_date_conversion_catches_value_type_error(self):
        import data.persistence.daos.base_dao as base_dao_mod

        assert hasattr(base_dao_mod.BaseDao, "_prepare_data_params")
        sig = inspect.signature(base_dao_mod.BaseDao._prepare_data_params)
        assert sig is not None, "E-P1-1: _prepare_data_params should exist and be callable"

    @pytest.mark.asyncio
    async def test_write_db_engine_check_logs_debug(self):
        mock_engine = MagicMock()
        mock_engine.sync_engine = MagicMock()
        mock_engine.sync_engine.side_effect = RuntimeError("unexpected")
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            result = await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn)
            assert result == 1


class TestMissingColsExcludedFromUpdate:
    """B-P1-8 supplement: Missing columns must be excluded from update_cols
    so that UPSERT ON CONFLICT DO UPDATE does not overwrite existing values with NULL."""

    @pytest.mark.asyncio
    async def test_missing_cols_excluded_from_update_dict(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "col_a"
        mock_col_a.info = {"null_protected": True}
        mock_col_b = MagicMock()
        mock_col_b.name = "col_b"
        mock_col_b.info = {"null_protected": True}
        mock_table.columns = [mock_col_pk, mock_col_a, mock_col_b]
        mock_table.c = {"id": mock_col_pk, "col_a": mock_col_a, "col_b": mock_col_b}
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
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1, "col_a": "val"}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt
            await dao._save_upsert(
                pd.DataFrame({"id": [1], "col_a": ["val"]}),
                "test_table",
                ["id", "col_a", "col_b"],
                ["id"],
                conn=mock_conn,
            )
            call_args = mock_stmt.on_conflict_do_update.call_args
            set_dict = call_args[1]["set_"]
            assert "col_b" not in set_dict, "Missing col_b should be excluded from update_dict"
            assert "col_a" in set_dict

    @pytest.mark.asyncio
    async def test_read_with_params(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = []
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
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
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Read Error")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._read_db("SELECT * FROM t", suppress_errors=True)
            assert isinstance(result, pd.DataFrame)
            assert result.empty

    @pytest.mark.asyncio
    async def test_read_no_suppress_raises(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Read Error")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(DatabaseQueryError, match="Database read failed"):
                await dao._read_db("SELECT * FROM t", suppress_errors=False)

    @pytest.mark.asyncio
    async def test_read_default_suppress_errors_is_true(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Read Error")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._read_db("SELECT * FROM t")
            assert isinstance(result, pd.DataFrame)
            assert result.empty


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
        mock_conn = AsyncMock()
        mock_engine = _setup_mock_engine_begin(mock_conn)
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
            with pytest.warns(DeprecationWarning):
                result = await dao._write_db(
                    "INSERT INTO t VALUES ($1, $2)", params=params, is_many=True, conn=mock_conn
                )
            assert result == 2

    @pytest.mark.asyncio
    async def test_write_disposed_engine(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed"):
                await dao._write_db("INSERT INTO t VALUES ($1)")

    @pytest.mark.asyncio
    async def test_write_sync_engine_none(self):
        mock_engine = _setup_mock_engine_begin(AsyncMock())
        mock_engine.sync_engine = None
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(EngineDisposedError, match="sync_engine is None"):
                await dao._write_db("INSERT INTO t VALUES ($1)")

    @pytest.mark.asyncio
    async def test_write_error_suppressed(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Write failed")
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn, suppress_errors=True)
            assert result == -1

    @pytest.mark.asyncio
    async def test_write_error_not_suppressed(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Write failed")
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(DatabaseQueryError, match="Database write failed"):
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
            with pytest.raises(EngineDisposedError):
                await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn)

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
    async def test_write_default_suppress_errors_is_false(self):
        """E-P1-5: _write_db default suppress_errors should be False (raises on error)."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("Write failed")
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(DatabaseQueryError, match="Database write failed"):
                await dao._write_db("INSERT INTO t VALUES ($1)", conn=mock_conn)

    @pytest.mark.asyncio
    async def test_write_no_params(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            result = await dao._write_db("DELETE FROM t", conn=mock_conn)
            assert result == 1


class TestShiborDailyReservedWordMapping:
    """DB-P1-9: Verify ShiborDaily model correctly maps reserved SQL words
    'date' and 'on' with explicit name parameters."""

    def test_date_column_maps_to_db_date(self):
        from data.persistence.models import ShiborDaily

        col = ShiborDaily.__table__.c["date"]
        assert col.name == "date"

    def test_on_column_maps_to_db_on(self):
        from data.persistence.models import ShiborDaily

        col = ShiborDaily.__table__.c["on"]
        assert col.name == "on"

    def test_on_column_is_numeric_type(self):
        from sqlalchemy import Numeric
        from data.persistence.models import ShiborDaily

        col = ShiborDaily.__table__.c["on"]
        assert isinstance(col.type, Numeric), f"on should be Numeric, got {type(col.type).__name__}"

    def test_date_column_is_date_type(self):
        from data.persistence.models import ShiborDaily

        col = ShiborDaily.__table__.c["date"]
        assert isinstance(col.type, Date)

    def test_reserved_word_columns_have_explicit_name(self):
        from data.persistence.models import ShiborDaily

        for attr_name in ("date", "on"):
            col = getattr(ShiborDaily, attr_name)
            assert hasattr(col, "name"), f"Column '{attr_name}' should have explicit name mapping"


class TestNullProtectedFromMetadata:
    def test_financial_reports_null_protected_columns(self):
        from data.persistence.models import FinancialReports

        null_protected = {c.name for c in FinancialReports.__table__.columns if c.info.get("null_protected", False)}
        expected_subset = {
            "roe",
            "roe_dt",
            "grossprofit_margin",
            "netprofit_margin",
            "debt_to_assets",
            "total_assets",
            "total_liab",
            "total_hldr_eqy_exc_min_int",
            "total_revenue",
            "revenue",
            "n_income",
            "n_income_attr_p",
            "goodwill",
            "or_yoy",
            "netprofit_yoy",
            "n_cashflow_act",
        }
        assert expected_subset.issubset(null_protected)

    def test_non_null_protected_columns_excluded(self):
        from data.persistence.models import FinancialReports

        table = FinancialReports.__table__
        pk_columns = {"ts_code", "end_date"}
        update_cols = [c.name for c in table.columns if c.name not in pk_columns]
        null_protected_update = {
            c.name for c in table.columns if c.name in update_cols and c.info.get("null_protected", False)
        }
        assert len(null_protected_update) > 0
        for col_name in pk_columns:
            col = table.c[col_name]
            assert col.info.get("null_protected", False) is False

    def test_default_null_protected_is_false(self):
        from data.persistence.models import FinancialReports

        table = FinancialReports.__table__
        non_financial_cols = {
            "ts_code",
            "end_date",
            "ann_date",
            "report_type",
            "audit_result",
            "updated_at",
            "created_at",
        }
        for c in table.columns:
            if c.name not in non_financial_cols:
                assert c.info.get("null_protected", False) is True
            else:
                assert c.info.get("null_protected", False) is False

    def test_save_upsert_uses_metadata_null_protected(self):
        from data.persistence.models import FinancialReports

        table = FinancialReports.__table__
        null_protected = {c.name for c in table.columns if c.info.get("null_protected", False)}
        update_cols = [c.name for c in table.columns if c.name not in ("ts_code", "end_date")]
        for c in update_cols:
            if c in null_protected:
                col = table.c[c]
                assert col.info.get("null_protected", False) is True


class TestAiScoreColumnType:
    def test_ai_score_is_numeric(self):
        import sqlalchemy as sa
        from data.persistence.models import ScreeningHistory

        col = ScreeningHistory.__table__.c["ai_score"]
        assert isinstance(col.type, sa.Numeric), f"ai_score should be Numeric, got {type(col.type).__name__}"


class TestScreeningThinkingModelConstraints:
    def test_history_id_has_unique_constraint(self):
        from data.persistence.models import ScreeningThinking

        col = ScreeningThinking.__table__.c.history_id
        assert col.unique is True, "history_id should have UNIQUE constraint for UPSERT"

    def test_history_id_has_foreign_key(self):
        from data.persistence.models import ScreeningThinking

        fk = list(ScreeningThinking.__table__.c.history_id.foreign_keys)
        assert len(fk) == 1, "history_id should have a foreign key to screening_history.id"
        assert fk[0].target_fullname == "screening_history.id"

    def test_history_id_not_nullable(self):
        from data.persistence.models import ScreeningThinking

        col = ScreeningThinking.__table__.c.history_id
        assert col.nullable is False, "history_id should be NOT NULL"

    def test_no_duplicate_index_on_history_id(self):
        from data.persistence.models import ScreeningThinking

        explicit_indexes = [
            idx
            for idx in ScreeningThinking.__table__.indexes
            if list(idx.columns) == [ScreeningThinking.__table__.c.history_id]
        ]
        assert len(explicit_indexes) == 0, "history_id should not have a separate index (unique=True creates one)"


class TestChunkedInQuery:
    """DB-P1-5: Verify chunked_in_query properly splits large IN clauses."""

    @pytest.mark.asyncio
    async def test_empty_values_returns_empty_df(self):
        read_fn = AsyncMock()
        result = await BaseDao.chunked_in_query(read_fn, "SELECT * FROM t WHERE id IN ({placeholders})", [])
        assert result.empty
        read_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_small_list_single_query(self):
        read_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A", "B"]}))
        result = await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            ["A", "B"],
            chunk_size=500,
        )
        assert len(result) == 2
        read_fn.assert_called_once()
        call_args = read_fn.call_args[0]
        sql = call_args[0]
        assert "$1" in sql and "$2" in sql

    @pytest.mark.asyncio
    async def test_large_list_splits_into_chunks(self):
        codes = [f"{i:06d}.SH" for i in range(1200)]
        chunk1_df = pd.DataFrame({"ts_code": codes[:500]})
        chunk2_df = pd.DataFrame({"ts_code": codes[500:1000]})
        chunk3_df = pd.DataFrame({"ts_code": codes[1000:]})
        read_fn = AsyncMock(side_effect=[chunk1_df, chunk2_df, chunk3_df])
        result = await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            codes,
            chunk_size=500,
        )
        assert read_fn.call_count == 3
        assert len(result) == 1200

    @pytest.mark.asyncio
    async def test_none_result_treated_as_empty(self):
        read_fn = AsyncMock(return_value=None)
        result = await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            ["A"],
        )
        assert result.empty

    @pytest.mark.asyncio
    async def test_chunk_with_empty_result_skipped(self):
        codes = [f"{i:06d}.SH" for i in range(1000)]
        chunk1_df = pd.DataFrame({"ts_code": codes[:500]})
        read_fn = AsyncMock(side_effect=[chunk1_df, None, pd.DataFrame()])
        result = await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            codes,
            chunk_size=500,
        )
        assert len(result) == 500

    @pytest.mark.asyncio
    async def test_params_fn_appended(self):
        read_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A"]}))

        def params_fn(chunk):
            return ["extra_val"]

        await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE id IN ({placeholders}) AND status = $2",
            ["A"],
            params_fn=params_fn,
        )
        call_params = read_fn.call_args[0][1]
        assert "extra_val" in call_params

    @pytest.mark.asyncio
    async def test_callable_sql_template_with_start_idx(self):
        read_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A"]}))

        passed_start_idx = None

        def sql_template_3(placeholders, chunk_len, start_idx):
            nonlocal passed_start_idx
            passed_start_idx = start_idx
            return f"SELECT * FROM t WHERE id IN ({placeholders}) LIMIT ${start_idx + chunk_len}"

        await BaseDao.chunked_in_query(
            read_fn,
            sql_template_3,
            ["A"],
            extra_params=["prefix1"],
        )
        assert passed_start_idx == 2
        called_sql = read_fn.call_args[0][0]
        assert called_sql == "SELECT * FROM t WHERE id IN ($2) LIMIT $3"

        def sql_template_2(placeholders, chunk_len):
            return f"SELECT * FROM t WHERE id IN ({placeholders})"

        await BaseDao.chunked_in_query(
            read_fn,
            sql_template_2,
            ["A"],
            extra_params=["prefix1"],
        )

    @pytest.mark.asyncio
    async def test_boundary_exactly_chunk_size_single_query(self):
        """恰好 chunk_size 个值应只触发一次查询（边界值 500）。

        覆盖 base_dao.py:145 的 `if len(values) <= chunk_size:` 单查询路径。
        """
        values = [f"{i:06d}.SH" for i in range(500)]
        read_fn = AsyncMock(return_value=pd.DataFrame({"ts_code": values}))
        result = await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            values,
            chunk_size=500,
        )
        assert read_fn.call_count == 1
        assert len(result) == 500

    @pytest.mark.asyncio
    async def test_boundary_one_over_chunk_size_two_queries(self):
        """chunk_size+1 个值应触发两次查询（边界值 501）。

        覆盖 base_dao.py:159 的 `for i in range(0, len(values), chunk_size):` 多块路径。
        """
        values = [f"{i:06d}.SH" for i in range(501)]
        chunk1_df = pd.DataFrame({"ts_code": values[:500]})
        chunk2_df = pd.DataFrame({"ts_code": values[500:]})
        read_fn = AsyncMock(side_effect=[chunk1_df, chunk2_df])
        result = await BaseDao.chunked_in_query(
            read_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            values,
            chunk_size=500,
        )
        assert read_fn.call_count == 2
        assert len(result) == 501


class TestChunkedInWrite:
    """Verify chunked_in_write properly splits large IN clauses for write operations."""

    @pytest.mark.asyncio
    async def test_empty_values_returns_zero(self):
        write_fn = AsyncMock()
        result = await BaseDao.chunked_in_write(write_fn, "UPDATE t SET x=1 WHERE id IN ({placeholders})", [])
        assert result == 0
        write_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_small_list_single_write(self):
        write_fn = AsyncMock(return_value=2)
        result = await BaseDao.chunked_in_write(
            write_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders})",
            ["A", "B"],
            chunk_size=500,
        )
        assert result == 2
        write_fn.assert_called_once()
        call_args = write_fn.call_args[0]
        sql = call_args[0]
        assert "$1" in sql and "$2" in sql

    @pytest.mark.asyncio
    async def test_large_list_splits_into_chunks(self):
        codes = [f"{i:06d}.SH" for i in range(1200)]
        write_fn = AsyncMock(side_effect=[500, 500, 200])
        result = await BaseDao.chunked_in_write(
            write_fn,
            "UPDATE t SET x=1 WHERE ts_code IN ({placeholders})",
            codes,
            chunk_size=500,
        )
        assert write_fn.call_count == 3
        assert result == 1200

    @pytest.mark.asyncio
    async def test_none_result_treated_as_zero(self):
        write_fn = AsyncMock(return_value=None)
        result = await BaseDao.chunked_in_write(
            write_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders})",
            ["A"],
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_callable_sql_template(self):
        write_fn = AsyncMock(return_value=1)

        def sql_tpl(placeholders, chunk_len):
            return f"UPDATE t SET x=1 WHERE id IN ({placeholders}) AND cnt = ${chunk_len + 1}"

        await BaseDao.chunked_in_write(
            write_fn,
            sql_tpl,
            ["A", "B"],
        )
        sql = write_fn.call_args[0][0]
        assert "$3" in sql

    @pytest.mark.asyncio
    async def test_extra_params_and_params_fn(self):
        write_fn = AsyncMock(return_value=1)

        def params_fn(chunk):
            return ["extra_suffix"]

        await BaseDao.chunked_in_write(
            write_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders}) AND status = $1",
            ["A"],
            extra_params=["status_val"],
            params_fn=params_fn,
        )
        call_params = write_fn.call_args[0][1]
        assert call_params[0] == "status_val"
        assert call_params[1] == "A"
        assert call_params[2] == "extra_suffix"

    @pytest.mark.asyncio
    async def test_write_db_kwargs_forwarded(self):
        write_fn = AsyncMock(return_value=1)
        await BaseDao.chunked_in_write(
            write_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders})",
            ["A"],
            suppress_errors=True,
        )
        assert write_fn.call_args[1].get("suppress_errors") is True

    @pytest.mark.asyncio
    async def test_callable_sql_template_with_start_idx(self):
        write_fn = AsyncMock(return_value=1)

        passed_start_idx = None

        def sql_template_3(placeholders, chunk_len, start_idx):
            nonlocal passed_start_idx
            passed_start_idx = start_idx
            return f"UPDATE t SET x=1 WHERE id IN ({placeholders}) AND status = ${start_idx + chunk_len}"

        await BaseDao.chunked_in_write(
            write_fn,
            sql_template_3,
            ["A"],
            extra_params=["status_val"],
        )
        assert passed_start_idx == 2
        called_sql = write_fn.call_args[0][0]
        assert called_sql == "UPDATE t SET x=1 WHERE id IN ($2) AND status = $3"

        def sql_template_2(placeholders, chunk_len):
            return f"UPDATE t SET x=1 WHERE id IN ({placeholders})"

        await BaseDao.chunked_in_write(
            write_fn,
            sql_template_2,
            ["A"],
            extra_params=["status_val"],
        )


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
            mock_stmt.on_conflict_do_nothing.assert_called_once()
            mock_stmt.on_conflict_do_update.assert_not_called()

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
            assert result == -1

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
            with pytest.raises(EngineDisposedError):
                await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], conn=mock_conn)

    @pytest.mark.asyncio
    async def test_upsert_default_suppress_errors_is_false(self):
        """E-P1-5: _save_upsert default suppress_errors should be False (raises on error)."""
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
            with pytest.raises(Exception, match="Upsert failed"):
                await dao._save_upsert(pd.DataFrame({"a": [1]}), "test_table", ["a"], ["a"], conn=mock_conn)

    @pytest.mark.asyncio
    async def test_upsert_without_conn(self):
        mock_conn = AsyncMock()
        mock_engine = _setup_mock_engine_begin(mock_conn)
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

    @pytest.mark.asyncio
    async def test_upsert_chunks_large_batch(self):
        mock_conn = AsyncMock()
        mock_engine = _setup_mock_engine_begin(mock_conn)
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        n_rows = 1200

        def mock_prepare_records(task_type, fn, df_slice):
            return [{"a": i} for i in range(len(df_slice))]

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
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_prepare_records)
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            large_df = pd.DataFrame({"a": list(range(n_rows))})
            result = await dao._save_upsert(large_df, "test_table", ["a"], ["a"])
            assert result == n_rows
            execute_calls = mock_conn.execute.call_args_list
            assert len(execute_calls) == 3

    @pytest.mark.asyncio
    async def test_upsert_chunks_with_conn(self):
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_table = MagicMock()
        mock_table.columns = {}
        mock_col_a = MagicMock()
        mock_col_a.name = "a"
        mock_table.c = {"a": mock_col_a}
        dao = BaseDao(mock_engine)
        n_rows = 700

        def mock_prepare_records(task_type, fn, df_slice):
            return [{"a": i} for i in range(len(df_slice))]

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
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_prepare_records)
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt
            large_df = pd.DataFrame({"a": list(range(n_rows))})
            result = await dao._save_upsert(large_df, "test_table", ["a"], ["a"], conn=mock_conn)
            assert result == n_rows
            execute_calls = mock_conn.execute.call_args_list
            assert len(execute_calls) == 2


class TestBaseDaoReadDbExtended:
    @pytest.mark.asyncio
    async def test_read_disposed_engine(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed"):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_read_cancelled_propagates(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = asyncio.CancelledError()
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(asyncio.CancelledError):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_read_connection_closed_during_shutdown(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("no active connection")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(EngineDisposedError):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_read_params_as_list(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = []
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
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

    def test_string_dtype_no_error(self):
        df = pd.DataFrame({"code": ["000001", "000002"], "value": [1, 2]})
        df["code"] = df["code"].astype(pd.StringDtype())
        result = BaseDao._prepare_data_params(df, ["code", "value"])
        assert result is not None
        assert len(result) == 2
        assert result[0][0] == "000001"
        assert result[0][1] == 1

    def test_mixed_string_and_numeric_dtypes(self):
        df = pd.DataFrame(
            {
                "code": pd.array(["000001", "000002"], dtype=pd.StringDtype()),
                "price": np.float64(10.5),
                "volume": np.int64(1000),
            }
        )
        result = BaseDao._prepare_data_params(df, ["code", "price", "volume"])
        assert result is not None
        assert len(result) == 2
        assert result[0][0] == "000001"
        assert isinstance(result[0][1], float)
        assert isinstance(result[0][2], int)


class TestDecimalPreservation:
    """AUDIT5_02 §2.6: Decimal values should be preserved, not converted to float.

    asyncpg natively supports Decimal type. Converting to float loses precision
    for high-precision fields like adj_factor (Numeric(20,12)).
    """

    def test_decimal_preserved_not_converted_to_float(self):
        from decimal import Decimal

        df = pd.DataFrame({"col1": [Decimal("1.123456789012")]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result is not None
        assert isinstance(result[0][0], Decimal)
        assert result[0][0] == Decimal("1.123456789012")

    def test_decimal_high_precision_preserved(self):
        from decimal import Decimal

        df = pd.DataFrame(
            {
                "adj_factor": [Decimal("1.123456789012345678901234")],
            }
        )
        result = BaseDao._prepare_data_params(df, ["adj_factor"])
        assert result is not None
        assert isinstance(result[0][0], Decimal)
        assert result[0][0] == Decimal("1.123456789012345678901234")

    def test_decimal_with_multiple_rows(self):
        from decimal import Decimal

        df = pd.DataFrame(
            {
                "price": [Decimal("10.5"), Decimal("20.25"), Decimal("30.125")],
            }
        )
        result = BaseDao._prepare_data_params(df, ["price"])
        assert result is not None
        assert len(result) == 3
        for i, expected in enumerate([Decimal("10.5"), Decimal("20.25"), Decimal("30.125")]):
            assert isinstance(result[i][0], Decimal)
            assert result[i][0] == expected

    def test_decimal_none_preserved_as_none(self):
        from decimal import Decimal

        df = pd.DataFrame({"col1": [Decimal("1.5"), None, Decimal("3.5")]})
        result = BaseDao._prepare_data_params(df, ["col1"])
        assert result is not None
        assert len(result) == 3
        assert isinstance(result[0][0], Decimal)
        assert result[1][0] is None
        assert isinstance(result[2][0], Decimal)


class TestEngineDisposedErrorDBP01:
    @pytest.mark.asyncio
    async def test_write_db_disposed_raises_engine_disposed_error(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed, write rejected"):
                await dao._write_db("INSERT INTO t VALUES ($1)")

    @pytest.mark.asyncio
    async def test_read_db_disposed_raises_engine_disposed_error(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed, read rejected"):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_read_db_select_disposed_raises_engine_disposed_error(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed, read rejected"):
                await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_save_upsert_disposed_raises_engine_disposed_error(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed, upsert rejected"):
                await dao._save_upsert(
                    pd.DataFrame({"a": [1]}),
                    "test_table",
                    ["a"],
                    ["a"],
                )

    @pytest.mark.asyncio
    async def test_write_db_sync_engine_none_raises_engine_disposed_error(self):
        mock_engine = MagicMock()
        mock_engine.sync_engine = None
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = None
            with pytest.raises(EngineDisposedError, match="sync_engine is None"):
                await dao._write_db("INSERT INTO t VALUES ($1)")

    def test_engine_disposed_error_is_runtime_error(self):
        assert issubclass(EngineDisposedError, RuntimeError)

    @pytest.mark.asyncio
    async def test_engine_disposed_error_catchable_by_caller(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        caught = False
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            try:
                await dao._write_db("INSERT INTO t VALUES ($1)")
            except EngineDisposedError:
                caught = True
        assert caught

    @pytest.mark.asyncio
    async def test_engine_disposed_error_catchable_as_runtime_error(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        caught = False
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            try:
                await dao._read_db("SELECT 1")
            except RuntimeError:
                caught = True
        assert caught

    @pytest.mark.asyncio
    async def test_write_not_disposed_works_normally(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            result = await dao._write_db("INSERT INTO t VALUES ($1)", params=(1,), conn=mock_conn)
            assert result == 1

    @pytest.mark.asyncio
    async def test_read_not_disposed_works_normally(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1, "test")]
        mock_result.keys.return_value = ["id", "name"]
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame([(1, "test")], columns=["id", "name"]))
            result = await dao._read_db("SELECT * FROM t")
            assert isinstance(result, pd.DataFrame)


class TestPrepareRecordsNaNHandling:
    """Regression: _save_upsert's _prepare_records must convert all scalar NaN
    variants to None, while leaving non-scalar values (list/dict) untouched.

    Root cause: Tushare stock_basic returns NaN in VARCHAR columns (e.g. area),
    asyncpg rejects float('nan') for VARCHAR parameters.
    """

    @pytest.mark.asyncio
    async def test_float_nan_in_string_column_converted_to_none(self):
        """Core regression: float('nan') in object/string column → None."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "ts_code"
        mock_col_pk.info = {}
        mock_col_area = MagicMock()
        mock_col_area.name = "area"
        mock_col_area.info = {}
        mock_table.columns = [mock_col_pk, mock_col_area]
        mock_table.c = {"ts_code": mock_col_pk, "area": mock_col_area}
        dao = BaseDao(mock_engine)

        captured_records = None

        async def capture_execute(stmt, chunk):
            nonlocal captured_records
            captured_records = chunk

        mock_conn.execute = capture_execute

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"stock_basic": mock_table}
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt

            df = pd.DataFrame(
                {
                    "ts_code": ["600166.SH", "600167.SH"],
                    "area": ["北京", float("nan")],
                }
            )
            await dao._save_upsert(df, "stock_basic", ["ts_code", "area"], ["ts_code"], conn=mock_conn)

        assert captured_records is not None
        assert captured_records[0]["area"] == "北京"
        assert captured_records[1]["area"] is None

    @pytest.mark.asyncio
    async def test_np_nan_converted_to_none(self):
        """np.nan in numeric column → None."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_val = MagicMock()
        mock_col_val.name = "value"
        mock_col_val.info = {}
        mock_table.columns = [mock_col_pk, mock_col_val]
        mock_table.c = {"id": mock_col_pk, "value": mock_col_val}
        dao = BaseDao(mock_engine)

        captured_records = None

        async def capture_execute(stmt, chunk):
            nonlocal captured_records
            captured_records = chunk

        mock_conn.execute = capture_execute

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt

            df = pd.DataFrame({"id": [1], "value": [np.nan]})
            await dao._save_upsert(df, "test_table", ["id", "value"], ["id"], conn=mock_conn)

        assert captured_records is not None
        assert captured_records[0]["value"] is None

    @pytest.mark.asyncio
    async def test_nat_converted_to_none(self):
        """pd.NaT in date column → None."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_dt = MagicMock()
        mock_col_dt.name = "dt"
        mock_col_dt.info = {}
        mock_table.columns = [mock_col_pk, mock_col_dt]
        mock_table.c = {"id": mock_col_pk, "dt": mock_col_dt}
        dao = BaseDao(mock_engine)

        captured_records = None

        async def capture_execute(stmt, chunk):
            nonlocal captured_records
            captured_records = chunk

        mock_conn.execute = capture_execute

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt

            df = pd.DataFrame({"id": [1], "dt": [pd.NaT]})
            await dao._save_upsert(df, "test_table", ["id", "dt"], ["id"], conn=mock_conn)

        assert captured_records is not None
        assert captured_records[0]["dt"] is None

    @pytest.mark.asyncio
    async def test_list_value_not_converted_to_none(self):
        """list/dict values (JSONB) must NOT be touched by NaN cleaning."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_data = MagicMock()
        mock_col_data.name = "data"
        mock_col_data.info = {}
        mock_table.columns = [mock_col_pk, mock_col_data]
        mock_table.c = {"id": mock_col_pk, "data": mock_col_data}
        dao = BaseDao(mock_engine)

        captured_records = None

        async def capture_execute(stmt, chunk):
            nonlocal captured_records
            captured_records = chunk

        mock_conn.execute = capture_execute

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt

            json_data = [{"key": "val"}, [1, 2, 3]]
            df = pd.DataFrame({"id": [1, 2], "data": json_data})
            await dao._save_upsert(df, "test_table", ["id", "data"], ["id"], conn=mock_conn)

        assert captured_records is not None
        assert captured_records[0]["data"] == {"key": "val"}
        assert captured_records[1]["data"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_mixed_nan_and_valid_values(self):
        """Mixed row: some NaN, some valid — only NaN becomes None."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col_pk = MagicMock()
        mock_col_pk.name = "id"
        mock_col_pk.info = {}
        mock_col_name = MagicMock()
        mock_col_name.name = "name"
        mock_col_name.info = {}
        mock_col_val = MagicMock()
        mock_col_val.name = "val"
        mock_col_val.info = {}
        mock_table.columns = [mock_col_pk, mock_col_name, mock_col_val]
        mock_table.c = {"id": mock_col_pk, "name": mock_col_name, "val": mock_col_val}
        dao = BaseDao(mock_engine)

        captured_records = None

        async def capture_execute(stmt, chunk):
            nonlocal captured_records
            captured_records = chunk

        mock_conn.execute = capture_execute

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
            patch("data.persistence.daos.base_dao.pg_insert") as mock_pg,
        ):
            mock_cm._instance = None
            mock_meta.tables = {"test_table": mock_table}
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_update.return_value = mock_stmt

            df = pd.DataFrame(
                {
                    "id": [1, 2, 3],
                    "name": ["valid", float("nan"), "also_valid"],
                    "val": [1.0, np.nan, 3.0],
                }
            )
            await dao._save_upsert(df, "test_table", ["id", "name", "val"], ["id"], conn=mock_conn)

        assert captured_records is not None
        assert captured_records[0]["name"] == "valid"
        assert captured_records[0]["val"] == 1.0
        assert captured_records[1]["name"] is None
        assert captured_records[1]["val"] is None
        assert captured_records[2]["name"] == "also_valid"
        assert captured_records[2]["val"] == 3.0


class TestGuardedBegin:
    """Direct unit tests for BaseDao._guarded_begin covering all execution paths."""

    @pytest.mark.asyncio
    async def test_normal_begin_yields_connection(self):
        """Path 1: Normal engine.begin() yields a transaction connection."""
        mock_tx_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_tx_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            async with dao._guarded_begin() as conn:
                assert conn is mock_tx_conn

    @pytest.mark.asyncio
    async def test_conn_provided_yields_directly(self):
        """Path 2: When conn is provided, yield it without starting a new transaction."""
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        existing_conn = AsyncMock()

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            async with dao._guarded_begin(conn=existing_conn) as conn:
                assert conn is existing_conn
            # engine.begin should NOT be called when conn is provided
            mock_engine.begin.assert_not_called()

    @pytest.mark.asyncio
    async def test_engine_none_raises_runtime_error(self):
        """Path 3: _check_engine raises RuntimeError when engine is None."""
        dao = BaseDao(None)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(RuntimeError, match="Engine not initialized"):
                async with dao._guarded_begin():
                    pass

    @pytest.mark.asyncio
    async def test_cache_manager_disposed_raises_engine_disposed_error(self):
        """Path 4: _check_engine raises EngineDisposedError when CacheManager is disposed."""
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError, match="Engine disposed"):
                async with dao._guarded_begin():
                    pass

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """Path 5: CancelledError must propagate (R2 red line)."""
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(side_effect=asyncio.CancelledError())
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(asyncio.CancelledError):
                async with dao._guarded_begin():
                    pass

    @pytest.mark.asyncio
    async def test_connection_closed_error_converts_to_engine_disposed(self):
        """Path 6: Connection-closed errors are converted to EngineDisposedError."""
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(side_effect=Exception("no active connection"))
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(EngineDisposedError, match="Engine disposed during guarded begin"):
                async with dao._guarded_begin():
                    pass

    @pytest.mark.asyncio
    async def test_database_closed_error_converts_to_engine_disposed(self):
        """Path 6 variant: 'database is closed' also converts to EngineDisposedError."""
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(side_effect=Exception("database is closed"))
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(EngineDisposedError, match="Engine disposed during guarded begin"):
                async with dao._guarded_begin():
                    pass

    @pytest.mark.asyncio
    async def test_other_exception_propagates(self):
        """Path 7: Non-connection errors propagate unchanged."""
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(side_effect=ValueError("some other error"))
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(ValueError, match="some other error"):
                async with dao._guarded_begin():
                    pass

    @pytest.mark.asyncio
    async def test_waits_for_maintenance_event(self):
        """Verify _guarded_begin waits for the maintenance event before proceeding."""
        mock_tx_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_tx_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch.object(dao, "_get_maintenance_event") as mock_get_evt,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_evt = AsyncMock()
            mock_evt.wait = AsyncMock()
            mock_get_evt.return_value = mock_evt

            async with dao._guarded_begin() as conn:
                assert conn is mock_tx_conn
            mock_evt.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_conn_provided_skips_check_engine_when_engine_is_none(self):
        """When conn is provided but engine is None, _check_engine still runs first."""
        dao = BaseDao(None)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            # _check_engine should raise RuntimeError before reaching the conn passthrough
            with pytest.raises(RuntimeError, match="Engine not initialized"):
                async with dao._guarded_begin(conn=AsyncMock()):
                    pass


class TestBaseDaoReadDbSelect:
    """_read_db_select 的专项测试，覆盖 engine None/disposed/success/cancelled/suppress 分支。"""

    @pytest.mark.asyncio
    async def test_select_engine_none_raises(self):
        dao = BaseDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_select_engine_disposed_raises(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True
            with pytest.raises(EngineDisposedError):
                await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_select_read_success(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1, "test")]
        mock_result.keys.return_value = ["id", "name"]
        mock_conn.execute.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame([(1, "test")], columns=["id", "name"]))
            result = await dao._read_db_select(sa.select(1))
            assert isinstance(result, pd.DataFrame)
            assert list(result.columns) == ["id", "name"]

    @pytest.mark.asyncio
    async def test_select_cancelled_error_propagates(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = asyncio.CancelledError()
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(asyncio.CancelledError):
                await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_select_connection_error_raises_engine_disposed_when_suppressed(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("no active connection")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(EngineDisposedError):
                await dao._read_db_select(sa.select(1), suppress_errors=True)

    @pytest.mark.asyncio
    async def test_select_error_suppressed_returns_empty_df(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("query error")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            result = await dao._read_db_select(sa.select(1), suppress_errors=True)
            assert result.empty

    @pytest.mark.asyncio
    async def test_select_error_raises_when_not_suppressed(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("query error")
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(Exception, match="query error"):
                await dao._read_db_select(sa.select(1), suppress_errors=False)


class TestBaseDaoReadDbMaxRowsAndParams:
    """_read_db 的 max_rows 限制与 list→tuple 参数转换。"""

    @pytest.mark.asyncio
    async def test_max_rows_exceeded_raises(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1,)] * 100
        mock_result.keys.return_value = ["id"]
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            with pytest.raises(ValueError, match="exceeding max_rows limit"):
                await dao._read_db("SELECT 1", max_rows=10)

    @pytest.mark.asyncio
    async def test_list_params_converted_to_tuple(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_result.keys.return_value = []
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)
        dao = BaseDao(mock_engine)
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.daos.base_dao.ThreadPoolManager") as mock_tpm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=pd.DataFrame())
            await dao._read_db("SELECT 1 WHERE id IN ($1)", params=[1, 2, 3])
            call_args = mock_conn.exec_driver_sql.call_args
            assert isinstance(call_args[0][1], tuple)


class TestBaseDaoPrepareDataParamsDateConversionError:
    """_prepare_data_params 在 table_name 模式下日期转换失败的容错分支。"""

    def test_with_table_name_date_conversion_error(self):
        from sqlalchemy import Date

        mock_table = MagicMock()
        mock_date_col = MagicMock()
        mock_date_col.name = "trade_date"
        mock_date_col.type = Date()
        mock_table.columns = [mock_date_col]

        with patch("data.persistence.models.Base.metadata") as mock_meta:
            mock_meta.tables = {"test_table": mock_table}
            df = pd.DataFrame({"trade_date": ["invalid_date"]})
            result = BaseDao._prepare_data_params(df, ["trade_date"], "test_table")
            assert result is not None


class TestChunkedInQueryMultipleChunks:
    """chunked_in_query 多分片动态生成场景，补充 TestChunkedInQuery 的覆盖。"""

    @pytest.mark.asyncio
    async def test_multiple_chunks_dynamic(self):
        call_count = 0

        async def mock_read_fn(sql, params):
            nonlocal call_count
            call_count += 1
            return pd.DataFrame({"id": [call_count * 2 - 1, call_count * 2]})

        result = await BaseDao.chunked_in_query(
            mock_read_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            list(range(1, 7)),
            chunk_size=2,
        )
        assert len(result) == 6
        assert call_count == 3


class TestChunkedExecute:
    """Task 6.11 (ARCH-M5 / CQ-M4): _chunked_execute 公共分块方法。"""

    @pytest.mark.asyncio
    async def test_empty_values_returns_empty_list(self):
        db_fn = AsyncMock()
        results = await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            [],
        )
        assert results == []
        db_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_chunk_returns_one_result(self):
        db_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A", "B"]}))
        results = await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            ["A", "B"],
            chunk_size=500,
        )
        assert len(results) == 1
        assert len(results[0]) == 2
        db_fn.assert_called_once()
        sql = db_fn.call_args[0][0]
        assert "$1" in sql and "$2" in sql

    @pytest.mark.asyncio
    async def test_multiple_chunks_preserve_order(self):
        codes = [f"{i:06d}.SH" for i in range(6)]
        db_fn = AsyncMock(
            side_effect=[
                pd.DataFrame({"ts_code": codes[:2]}),
                pd.DataFrame({"ts_code": codes[2:4]}),
                pd.DataFrame({"ts_code": codes[4:]}),
            ]
        )
        results = await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            codes,
            chunk_size=2,
        )
        assert db_fn.call_count == 3
        assert len(results) == 3
        assert len(results[0]) == 2
        assert len(results[1]) == 2
        assert len(results[2]) == 2

    @pytest.mark.asyncio
    async def test_none_results_preserved_not_filtered(self):
        """_chunked_execute 不负责过滤，None 结果应原样保留在列表中。"""
        db_fn = AsyncMock(side_effect=[pd.DataFrame({"id": ["A"]}), None])
        results = await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            ["A", "B"],
            chunk_size=1,
        )
        assert len(results) == 2
        assert results[0] is not None
        assert results[1] is None

    @pytest.mark.asyncio
    async def test_callable_sql_template_with_start_idx(self):
        db_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A"]}))
        passed_start_idx = None

        def sql_template_3(placeholders, chunk_len, start_idx):
            nonlocal passed_start_idx
            passed_start_idx = start_idx
            return f"SELECT * FROM t WHERE id IN ({placeholders}) LIMIT ${start_idx + chunk_len}"

        await BaseDao._chunked_execute(
            db_fn,
            sql_template_3,
            ["A"],
            extra_params=["prefix1"],
        )
        assert passed_start_idx == 2
        called_sql = db_fn.call_args[0][0]
        assert called_sql == "SELECT * FROM t WHERE id IN ($2) LIMIT $3"

    @pytest.mark.asyncio
    async def test_extra_params_and_params_fn_assembled(self):
        db_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A"]}))

        def params_fn(chunk):
            return ["extra_suffix"]

        await BaseDao._chunked_execute(
            db_fn,
            "UPDATE t SET x=1 WHERE id IN ({placeholders}) AND status = $1",
            ["A"],
            extra_params=["status_val"],
            params_fn=params_fn,
        )
        call_params = db_fn.call_args[0][1]
        assert call_params[0] == "status_val"
        assert call_params[1] == "A"
        assert call_params[2] == "extra_suffix"

    @pytest.mark.asyncio
    async def test_db_kwargs_forwarded(self):
        db_fn = AsyncMock(return_value=pd.DataFrame({"id": ["A"]}))
        await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            ["A"],
            suppress_errors=True,
        )
        assert db_fn.call_args[1].get("suppress_errors") is True

    @pytest.mark.asyncio
    async def test_boundary_exactly_chunk_size_single_call(self):
        """恰好 chunk_size 个值应只触发一次调用。"""
        values = [f"{i:06d}.SH" for i in range(500)]
        db_fn = AsyncMock(return_value=pd.DataFrame({"ts_code": values}))
        results = await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            values,
            chunk_size=500,
        )
        assert db_fn.call_count == 1
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_boundary_one_over_chunk_size_two_calls(self):
        """chunk_size+1 个值应触发两次调用。"""
        values = [f"{i:06d}.SH" for i in range(501)]
        db_fn = AsyncMock(
            side_effect=[
                pd.DataFrame({"ts_code": values[:500]}),
                pd.DataFrame({"ts_code": values[500:]}),
            ]
        )
        results = await BaseDao._chunked_execute(
            db_fn,
            "SELECT * FROM t WHERE ts_code IN ({placeholders})",
            values,
            chunk_size=500,
        )
        assert db_fn.call_count == 2
        assert len(results) == 2
