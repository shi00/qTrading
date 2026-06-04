import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import asyncpg

from data.persistence.db_config_service import (
    DatabaseConfigService,
    ConnectionStatus,
    DatabaseInfo,
)


def _make_mock_conn(fetchval_return="PostgreSQL 16.2, compiled by gcc"):
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=fetchval_return)
    mock_conn.execute = AsyncMock()
    mock_conn.close = AsyncMock()
    return mock_conn


class TestTestConnection:
    @pytest.mark.asyncio
    async def test_success_returns_server_version(self):
        mock_conn = _make_mock_conn()
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass", "mydb")
        assert result.status == ConnectionStatus.SUCCESS
        assert result.server_version == "PostgreSQL 16.2"
        assert result.database_exists is True

    @pytest.mark.asyncio
    async def test_success_without_database_defaults_to_postgres(self):
        mock_conn = _make_mock_conn()
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn) as mock_connect:
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.SUCCESS
        call_kwargs = mock_connect.call_args
        assert call_kwargs.kwargs.get("database") == "postgres" or call_kwargs[1].get("database") == "postgres"

    @pytest.mark.asyncio
    async def test_success_null_version_returns_none(self):
        mock_conn = _make_mock_conn(fetchval_return=None)
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass", "mydb")
        assert result.status == ConnectionStatus.SUCCESS
        assert result.server_version is None

    @pytest.mark.asyncio
    async def test_invalid_password_returns_auth_error(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=asyncpg.InvalidPasswordError):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "wrong")
        assert result.status == ConnectionStatus.AUTHENTICATION_ERROR

    @pytest.mark.asyncio
    async def test_database_not_found(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=asyncpg.InvalidCatalogNameError):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass", "missing_db")
        assert result.status == ConnectionStatus.DATABASE_NOT_FOUND
        assert result.database_exists is False

    @pytest.mark.asyncio
    async def test_timeout(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=TimeoutError):
            result = await DatabaseConfigService.test_connection("192.168.1.1", 5432, "user", "pass")
        assert result.status == ConnectionStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_os_error_connection_refused(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=OSError("Connection refused")):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_os_error_no_route_to_host(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=OSError("No route to host")):
            result = await DatabaseConfigService.test_connection("10.0.0.1", 5432, "user", "pass")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_os_error_winerror_64(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=OSError("WinError 64")):
            result = await DatabaseConfigService.test_connection("proxy.host", 5432, "user", "pass")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_os_error_other_network(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=OSError("Network unreachable")):
            result = await DatabaseConfigService.test_connection("host", 5432, "user", "pass")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_postgres_error_connection_does_not_exist_with_password(self):
        """ConnectionDoesNotExistError + target_db=postgres → auth error (no retry possible)"""
        exc = asyncpg.exceptions.ConnectionDoesNotExistError("password authentication failed")
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=exc):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.AUTHENTICATION_ERROR

    @pytest.mark.asyncio
    async def test_connection_not_exist_db_missing_auth_ok(self):
        """ConnectionDoesNotExistError for non-postgres db, but auth to postgres succeeds → DATABASE_NOT_FOUND"""
        exc = asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
        mock_verify_conn = _make_mock_conn()
        with patch(
            "data.persistence.db_config_service.asyncpg.connect",
            side_effect=[exc, mock_verify_conn],
        ):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass", "missing_db")
        assert result.status == ConnectionStatus.DATABASE_NOT_FOUND
        assert result.database_exists is False

    @pytest.mark.asyncio
    async def test_connection_not_exist_auth_also_fails(self):
        """ConnectionDoesNotExistError for non-postgres db, auth to postgres also fails → CONNECTION_ERROR"""
        exc = asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
        verify_exc = asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
        with patch(
            "data.persistence.db_config_service.asyncpg.connect",
            side_effect=[exc, verify_exc],
        ):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "wrong", "missing_db")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_connection_not_exist_verify_invalid_password(self):
        """ConnectionDoesNotExistError for non-postgres db, verify raises InvalidPasswordError → AUTHENTICATION_ERROR"""
        exc = asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
        verify_exc = asyncpg.InvalidPasswordError("password authentication failed for user")
        with patch(
            "data.persistence.db_config_service.asyncpg.connect",
            side_effect=[exc, verify_exc],
        ):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "wrong", "missing_db")
        assert result.status == ConnectionStatus.AUTHENTICATION_ERROR

    @pytest.mark.asyncio
    async def test_connection_not_exist_verify_timeout(self):
        """ConnectionDoesNotExistError for non-postgres db, verify connection times out → CONNECTION_ERROR"""
        exc = asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")
        with patch(
            "data.persistence.db_config_service.asyncpg.connect",
            side_effect=[exc, TimeoutError()],
        ):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass", "missing_db")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_connection_not_exist_verify_os_error(self):
        """ConnectionDoesNotExistError for non-postgres db, verify connection gets OSError → CONNECTION_ERROR"""
        exc = asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed")
        with patch(
            "data.persistence.db_config_service.asyncpg.connect",
            side_effect=[exc, OSError("Connection refused")],
        ):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass", "missing_db")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_postgres_error_invalid_password_via_type_name(self):
        exc = asyncpg.InvalidPasswordError("auth failed")
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=exc):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.AUTHENTICATION_ERROR

    @pytest.mark.asyncio
    async def test_postgres_error_other(self):
        exc = asyncpg.exceptions.PostgresError("internal error")
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=exc):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_with_password_in_type(self):
        exc = type("InvalidPasswordError", (Exception,), {})()
        exc.__class__.__name__ = "InvalidPasswordError"
        exc.__str__ = lambda self: "auth fail"
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=exc):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.AUTHENTICATION_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_with_password_in_message(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=Exception("password required")):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.AUTHENTICATION_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_with_winerror_64(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=Exception("WinError 64")):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.CONNECTION_ERROR

    @pytest.mark.asyncio
    async def test_generic_exception_unknown(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=RuntimeError("unexpected")):
            result = await DatabaseConfigService.test_connection("localhost", 5432, "user", "pass")
        assert result.status == ConnectionStatus.UNKNOWN_ERROR


class TestDatabaseExists:
    @pytest.mark.asyncio
    async def test_exists_returns_true(self):
        mock_conn = _make_mock_conn(fetchval_return=1)
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn):
            result = await DatabaseConfigService.database_exists("localhost", 5432, "user", "pass", "mydb")
        assert result is True

    @pytest.mark.asyncio
    async def test_not_exists_returns_false(self):
        mock_conn = _make_mock_conn(fetchval_return=None)
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn):
            result = await DatabaseConfigService.database_exists("localhost", 5432, "user", "pass", "missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_connection_failure_returns_false(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=OSError("refused")):
            result = await DatabaseConfigService.database_exists("localhost", 5432, "user", "pass", "mydb")
        assert result is False


class TestCreateDatabase:
    @pytest.mark.asyncio
    async def test_duplicate_database_error(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=asyncpg.DuplicateDatabaseError):
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "mydb")
        assert ok is False
        assert "mydb" in msg

    @pytest.mark.asyncio
    async def test_insufficient_privilege_error(self):
        with patch(
            "data.persistence.db_config_service.asyncpg.connect", side_effect=asyncpg.InsufficientPrivilegeError
        ):
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "mydb")
        assert ok is False
        assert len(msg) > 0

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=RuntimeError("disk full")):
            ok, msg = await DatabaseConfigService.create_database("localhost", 5432, "user", "pass", "mydb")
        assert ok is False
        assert "disk full" in msg


class TestBuildUrl:
    def test_async_driver_true(self):
        url = DatabaseConfigService.build_url("localhost", 5432, "user", "pass", "mydb", async_driver=True)
        assert url.startswith("postgresql+asyncpg://")
        assert "user:pass@localhost:5432/mydb" in url

    def test_async_driver_false(self):
        url = DatabaseConfigService.build_url("localhost", 5432, "user", "pass", "mydb", async_driver=False)
        assert url.startswith("postgresql://")
        assert "+asyncpg" not in url

    def test_special_characters_encoded(self):
        url = DatabaseConfigService.build_url("localhost", 5432, "user", "p@ss:word", "mydb")
        assert "p%40ss%3Aword" in url
        assert "@" not in url.split("://")[1].split("user")[0]


class TestParseUrl:
    def test_valid_postgresql_url(self):
        result = DatabaseConfigService.parse_url("postgresql+asyncpg://myuser:mypass@dbhost:5433/mydb")
        assert result is not None
        assert result["host"] == "dbhost"
        assert result["port"] == 5433
        assert result["user"] == "myuser"
        assert result["password"] == "mypass"
        assert result["database"] == "mydb"

    def test_url_with_defaults(self):
        result = DatabaseConfigService.parse_url("postgresql://user@localhost/mydb")
        assert result is not None
        assert result["port"] == DatabaseConfigService.DEFAULT_PORT
        assert result["password"] == ""

    def test_url_missing_host_uses_default(self):
        result = DatabaseConfigService.parse_url("postgresql://user:pass@/mydb")
        assert result is not None
        assert result["host"] == DatabaseConfigService.DEFAULT_HOST

    def test_url_missing_database_uses_default(self):
        result = DatabaseConfigService.parse_url("postgresql://user:pass@localhost:5432")
        assert result is not None
        assert result["database"] == DatabaseConfigService.DEFAULT_DATABASE

    def test_non_postgresql_url_returns_none(self):
        result = DatabaseConfigService.parse_url("mysql://user:pass@localhost/mydb")
        assert result is None

    def test_url_with_encoded_password(self):
        result = DatabaseConfigService.parse_url("postgresql://user:p%40ss@localhost/mydb")
        assert result is not None
        assert result["password"] == "p@ss"


class TestRunMigrations:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        with (
            patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine),
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock()
            ok, msg = await DatabaseConfigService.run_migrations("localhost", 5432, "user", "pass", "mydb")
        assert ok is True
        mock_migrator.init_db.assert_awaited_once_with(mock_engine, auto_migrate=True)
        mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure(self):
        with (
            patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=RuntimeError("boom")),
        ):
            ok, msg = await DatabaseConfigService.run_migrations("localhost", 5432, "user", "pass", "mydb")
        assert ok is False
        assert "boom" in msg


class TestEnsureTablesExist:
    @pytest.mark.asyncio
    async def test_info_none_returns_connection_error(self):
        with patch.object(DatabaseConfigService, "get_database_info", return_value=None):
            ok, msg = await DatabaseConfigService.ensure_tables_exist("localhost", 5432, "user", "pass", "mydb")
        assert ok is False

    @pytest.mark.asyncio
    async def test_existing_tables_skips_migration(self):
        info = DatabaseInfo(version="16.2", size="100 MB", table_count=5)
        with (
            patch.object(DatabaseConfigService, "get_database_info", return_value=info),
            patch.object(DatabaseConfigService, "run_migrations") as mock_migrate,
        ):
            ok, msg = await DatabaseConfigService.ensure_tables_exist("localhost", 5432, "user", "pass", "mydb")
        assert ok is True
        mock_migrate.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_database_runs_migration(self):
        info = DatabaseInfo(version="16.2", size="0 MB", table_count=0)
        with (
            patch.object(DatabaseConfigService, "get_database_info", return_value=info),
            patch.object(DatabaseConfigService, "run_migrations", return_value=(True, "ok")) as mock_migrate,
        ):
            ok, msg = await DatabaseConfigService.ensure_tables_exist("localhost", 5432, "user", "pass", "mydb")
        assert ok is True
        mock_migrate.assert_awaited_once()


class TestGetDatabaseInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=["PostgreSQL 16.2, compiled by gcc", "150 MB", 7])
        mock_conn.close = AsyncMock()
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn):
            info = await DatabaseConfigService.get_database_info("localhost", 5432, "user", "pass", "mydb")
        assert info is not None
        assert info.version == "PostgreSQL 16.2"
        assert info.size == "150 MB"
        assert info.table_count == 7

    @pytest.mark.asyncio
    async def test_null_values_use_defaults(self):
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(side_effect=["PostgreSQL 16.2", None, None])
        mock_conn.close = AsyncMock()
        with patch("data.persistence.db_config_service.asyncpg.connect", return_value=mock_conn):
            info = await DatabaseConfigService.get_database_info("localhost", 5432, "user", "pass", "mydb")
        assert info is not None
        assert info.size == "Unknown"
        assert info.table_count == 0

    @pytest.mark.asyncio
    async def test_connection_failure_returns_none(self):
        with patch("data.persistence.db_config_service.asyncpg.connect", side_effect=OSError("refused")):
            info = await DatabaseConfigService.get_database_info("localhost", 5432, "user", "pass", "mydb")
        assert info is None
