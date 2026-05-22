"""data/persistence/daos/base_dao.py 补充测试 - _read_db_select、_save_upsert异常分支"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import Date, DateTime

from data.persistence.daos.base_dao import BaseDao, EngineDisposedError


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


class TestBaseDaoReadDbSelect:
    @pytest.mark.asyncio
    async def test_engine_none_raises(self):
        dao = BaseDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_engine_disposed_raises(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True

            with pytest.raises(EngineDisposedError):
                await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_read_success(self):
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

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = asyncio.CancelledError()
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            with pytest.raises(asyncio.CancelledError):
                await dao._read_db_select(sa.select(1))

    @pytest.mark.asyncio
    async def test_connection_error_returns_empty_df(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("no active connection")
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            result = await dao._read_db_select(sa.select(1), suppress_errors=True)
            assert result.empty

    @pytest.mark.asyncio
    async def test_error_suppressed_returns_empty_df(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("query error")
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            result = await dao._read_db_select(sa.select(1), suppress_errors=True)
            assert result.empty

    @pytest.mark.asyncio
    async def test_error_raises_when_not_suppressed(self):
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("query error")
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            with pytest.raises(Exception, match="query error"):
                await dao._read_db_select(sa.select(1), suppress_errors=False)


class TestBaseDaoSaveUpsert:
    @pytest.mark.asyncio
    async def test_engine_none_raises(self):
        dao = BaseDao(None)
        with pytest.raises(RuntimeError, match="Engine not initialized"):
            await dao._save_upsert(pd.DataFrame({"id": [1]}), "test_table", ["id"], ["id"])

    @pytest.mark.asyncio
    async def test_engine_disposed_raises(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True

            with pytest.raises(EngineDisposedError):
                await dao._save_upsert(pd.DataFrame({"id": [1]}), "test_table", ["id"], ["id"])

    @pytest.mark.asyncio
    async def test_empty_df_returns_zero(self):
        dao = BaseDao(MagicMock())
        result = await dao._save_upsert(pd.DataFrame(), "test_table", ["id"], ["id"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_none_df_returns_zero(self):
        dao = BaseDao(MagicMock())
        result = await dao._save_upsert(None, "test_table", ["id"], ["id"])
        assert result == 0

    @pytest.mark.asyncio
    async def test_table_not_found_returns_zero(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_meta.tables = {}

            result = await dao._save_upsert(pd.DataFrame({"id": [1]}), "nonexistent_table", ["id"], ["id"])
            assert result == 0

    @pytest.mark.asyncio
    async def test_missing_pk_columns_returns_zero(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.models.Base.metadata") as mock_meta,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False
            mock_table = MagicMock()
            mock_table.columns = []
            mock_meta.tables = {"test_table": mock_table}

            result = await dao._save_upsert(
                pd.DataFrame({"col_a": ["val"]}),
                "test_table",
                ["id", "col_a"],
                ["id"],
            )
            assert result == 0

    @pytest.mark.asyncio
    async def test_missing_non_pk_columns_filled_with_none(self):
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
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1, "col_a": None}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.excluded = MagicMock()
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt

            result = await dao._save_upsert(
                pd.DataFrame({"id": [1]}),
                "test_table",
                ["id", "col_a"],
                ["id"],
                conn=mock_conn,
            )
            assert result == 1

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = asyncio.CancelledError()
        mock_table = MagicMock()
        mock_col = MagicMock()
        mock_col.name = "id"
        mock_col.info = {}
        mock_table.columns = [mock_col]
        mock_table.c = {"id": mock_col}
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
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt

            with pytest.raises(asyncio.CancelledError):
                await dao._save_upsert(
                    pd.DataFrame({"id": [1]}),
                    "test_table",
                    ["id"],
                    ["id"],
                    conn=mock_conn,
                )

    @pytest.mark.asyncio
    async def test_connection_error_returns_zero(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("no active connection")
        mock_table = MagicMock()
        mock_col = MagicMock()
        mock_col.name = "id"
        mock_col.info = {}
        mock_table.columns = [mock_col]
        mock_table.c = {"id": mock_col}
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
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt

            result = await dao._save_upsert(
                pd.DataFrame({"id": [1]}),
                "test_table",
                ["id"],
                ["id"],
                conn=mock_conn,
            )
            assert result == 0

    @pytest.mark.asyncio
    async def test_suppress_errors_returns_negative_one(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("db error")
        mock_table = MagicMock()
        mock_col = MagicMock()
        mock_col.name = "id"
        mock_col.info = {}
        mock_table.columns = [mock_col]
        mock_table.c = {"id": mock_col}
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
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt

            result = await dao._save_upsert(
                pd.DataFrame({"id": [1]}),
                "test_table",
                ["id"],
                ["id"],
                suppress_errors=True,
                conn=mock_conn,
            )
            assert result == -1

    @pytest.mark.asyncio
    async def test_no_update_cols_uses_on_conflict_do_nothing(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_table = MagicMock()
        mock_col = MagicMock()
        mock_col.name = "id"
        mock_col.info = {}
        mock_table.columns = [mock_col]
        mock_table.c = {"id": mock_col}
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
            mock_tpm_instance.run_async = AsyncMock(return_value=[{"id": 1}])
            mock_stmt = MagicMock()
            mock_pg.return_value = mock_stmt
            mock_stmt.on_conflict_do_nothing.return_value = mock_stmt

            await dao._save_upsert(
                pd.DataFrame({"id": [1]}),
                "test_table",
                ["id"],
                ["id"],
                conn=mock_conn,
            )

            mock_stmt.on_conflict_do_nothing.assert_called_once()


class TestBaseDaoWriteDb:
    @pytest.mark.asyncio
    async def test_engine_disposed_raises(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True

            with pytest.raises(EngineDisposedError):
                await dao._write_db("INSERT INTO t VALUES (1)")

    @pytest.mark.asyncio
    async def test_connection_error_returns_zero(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("no active connection")
        mock_engine = _setup_mock_engine_begin(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            result = await dao._write_db("INSERT INTO t VALUES (1)")
            assert result == 0

    @pytest.mark.asyncio
    async def test_suppress_errors_returns_negative_one(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("db error")
        mock_engine = _setup_mock_engine_begin(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            result = await dao._write_db("INSERT INTO t VALUES (1)", suppress_errors=True)
            assert result == -1

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = asyncio.CancelledError()
        mock_engine = _setup_mock_engine_begin(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            with pytest.raises(asyncio.CancelledError):
                await dao._write_db("INSERT INTO t VALUES (1)")

    @pytest.mark.asyncio
    async def test_write_with_conn_uses_provided_conn(self):
        mock_conn = AsyncMock()
        mock_engine = MagicMock()

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            result = await dao._write_db("INSERT INTO t VALUES (1)", conn=mock_conn)
            mock_conn.exec_driver_sql.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_many_deprecation_warning(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with pytest.warns(DeprecationWarning, match="is deprecated"):
            await dao._write_db("INSERT INTO t VALUES (1)", is_many=True, params=None)


class TestBaseDaoReadDb:
    @pytest.mark.asyncio
    async def test_engine_disposed_raises(self):
        mock_engine = MagicMock()
        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = True

            with pytest.raises(EngineDisposedError):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_connection_error_returns_empty_df(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = Exception("no active connection")
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            result = await dao._read_db("SELECT 1")
            assert result.empty

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        mock_conn = AsyncMock()
        mock_conn.exec_driver_sql.side_effect = asyncio.CancelledError()
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
            mock_cm._instance = MagicMock()
            mock_cm._instance._disposed = False

            with pytest.raises(asyncio.CancelledError):
                await dao._read_db("SELECT 1")

    @pytest.mark.asyncio
    async def test_max_rows_exceeded_raises(self):
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(1,)] * 100
        mock_result.keys.return_value = ["id"]
        mock_conn.exec_driver_sql.return_value = mock_result
        mock_engine = _setup_mock_engine_connect(mock_conn)

        dao = BaseDao(mock_engine)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
        ):
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


class TestBaseDaoPrepareDataParamsWithTable:
    def test_with_table_name_date_conversion(self):
        mock_table = MagicMock()
        mock_date_col = MagicMock()
        mock_date_col.name = "trade_date"
        mock_date_col.type = Date()
        mock_datetime_col = MagicMock()
        mock_datetime_col.name = "created_at"
        mock_datetime_col.type = DateTime()
        mock_table.columns = [mock_date_col, mock_datetime_col]

        with patch("data.persistence.models.Base.metadata") as mock_meta:
            mock_meta.tables = {"test_table": mock_table}

            df = pd.DataFrame(
                {
                    "trade_date": ["20240101", "20240102"],
                    "created_at": ["2024-01-01 10:00:00", "2024-01-02 11:00:00"],
                }
            )

            result = BaseDao._prepare_data_params(df, ["trade_date", "created_at"], "test_table")

            assert result is not None
            assert len(result) == 2

    def test_with_table_name_date_conversion_error(self):
        mock_table = MagicMock()
        mock_date_col = MagicMock()
        mock_date_col.name = "trade_date"
        mock_date_col.type = Date()
        mock_table.columns = [mock_date_col]

        with patch("data.persistence.models.Base.metadata") as mock_meta:
            mock_meta.tables = {"test_table": mock_table}

            df = pd.DataFrame(
                {
                    "trade_date": ["invalid_date"],
                }
            )

            result = BaseDao._prepare_data_params(df, ["trade_date"], "test_table")

            assert result is not None


class TestBaseDaoChunkedInQuery:
    @pytest.mark.asyncio
    async def test_empty_values_returns_empty_df(self):
        result = await BaseDao.chunked_in_query(
            AsyncMock(),
            "SELECT * FROM t WHERE id IN ({placeholders})",
            [],
        )
        assert result.empty

    @pytest.mark.asyncio
    async def test_single_chunk(self):
        mock_read_fn = AsyncMock(return_value=pd.DataFrame({"id": [1, 2]}))

        result = await BaseDao.chunked_in_query(
            mock_read_fn,
            "SELECT * FROM t WHERE id IN ({placeholders})",
            [1, 2],
        )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_multiple_chunks(self):
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

    @pytest.mark.asyncio
    async def test_with_params_fn(self):
        mock_read_fn = AsyncMock(return_value=pd.DataFrame({"id": [1]}))

        result = await BaseDao.chunked_in_query(
            mock_read_fn,
            "SELECT * FROM t WHERE id IN ({placeholders}) AND status = $2",
            [1],
            params_fn=lambda vals: ["active"],
        )

        assert len(result) == 1


class TestEngineDisposedError:
    def test_is_runtime_error_subclass(self):
        assert issubclass(EngineDisposedError, RuntimeError)

    def test_can_be_raised(self):
        with pytest.raises(EngineDisposedError):
            raise EngineDisposedError("test")
