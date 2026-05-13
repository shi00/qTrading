"""
Database Configuration Service

Provides database connection testing, creation, and configuration management.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote_plus, unquote_plus, urlparse

import asyncpg

from core.i18n import I18n

logger = logging.getLogger(__name__)


class ConnectionStatus(Enum):
    SUCCESS = "success"
    CONNECTION_ERROR = "connection_error"
    AUTHENTICATION_ERROR = "authentication_error"
    DATABASE_NOT_FOUND = "database_not_found"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class ConnectionResult:
    status: ConnectionStatus
    message: str
    server_version: str | None = None
    database_exists: bool = False


@dataclass
class DatabaseInfo:
    version: str
    size: str
    table_count: int


class DatabaseConfigService:
    """Service for database configuration and management."""

    DEFAULT_PORT = 5432
    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_USER = "postgres"
    DEFAULT_DATABASE = "astock"

    CONNECTION_TIMEOUT = 5.0

    @classmethod
    async def test_connection(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str | None = None,
    ) -> ConnectionResult:
        """
        Test database connection.

        Args:
            host: Database host
            port: Database port
            user: Database user
            password: Database password
            database: Database name (optional, connects to 'postgres' if not provided)

        Returns:
            ConnectionResult with status and message
        """
        target_db = database or "postgres"

        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=target_db,
                ),
                timeout=cls.CONNECTION_TIMEOUT,
            )

            version = await conn.fetchval("SELECT version()")
            await conn.close()

            return ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message=I18n.get("db_msg_success"),
                server_version=version.split(",")[0] if version else None,
                database_exists=True,
            )

        except asyncpg.InvalidPasswordError:
            return ConnectionResult(
                status=ConnectionStatus.AUTHENTICATION_ERROR,
                message=I18n.get("db_err_auth"),
            )

        except asyncpg.InvalidCatalogNameError:
            return ConnectionResult(
                status=ConnectionStatus.DATABASE_NOT_FOUND,
                message=I18n.get("db_err_not_found").format(database=database),
                database_exists=False,
            )

        except TimeoutError:
            return ConnectionResult(
                status=ConnectionStatus.TIMEOUT,
                message=I18n.get("db_err_timeout"),
            )

        except OSError as e:
            if "Connection refused" in str(e) or "No route to host" in str(e):
                return ConnectionResult(
                    status=ConnectionStatus.CONNECTION_ERROR,
                    message=I18n.get("db_err_refused").format(host=host, port=port),
                )
            if "WinError 64" in str(e):
                return ConnectionResult(
                    status=ConnectionStatus.CONNECTION_ERROR,
                    message=I18n.get("db_err_proxy"),
                )
            return ConnectionResult(
                status=ConnectionStatus.CONNECTION_ERROR,
                message=I18n.get("db_err_network"),
            )

        except asyncpg.exceptions.PostgresError as e:
            error_str = str(e).lower()
            error_type_name = type(e).__name__

            if error_type_name == "ConnectionDoesNotExistError":
                if "password" in error_str or "authentication" in error_str or "was closed" in error_str:
                    return ConnectionResult(
                        status=ConnectionStatus.AUTHENTICATION_ERROR,
                        message=I18n.get("db_err_auth"),
                    )
                return ConnectionResult(
                    status=ConnectionStatus.CONNECTION_ERROR,
                    message=I18n.get("db_err_interrupted"),
                )
            if error_type_name == "InvalidPasswordError":
                return ConnectionResult(
                    status=ConnectionStatus.AUTHENTICATION_ERROR,
                    message=I18n.get("db_err_auth"),
                )
            return ConnectionResult(
                status=ConnectionStatus.CONNECTION_ERROR,
                message=I18n.get("db_err_db_error"),
            )

        except Exception as e:
            error_str = str(e).lower()
            error_type = type(e).__name__

            if "InvalidPasswordError" in error_type or "password" in error_str:
                return ConnectionResult(
                    status=ConnectionStatus.AUTHENTICATION_ERROR,
                    message=I18n.get("db_err_auth"),
                )
            if "WinError 64" in str(e):
                return ConnectionResult(
                    status=ConnectionStatus.CONNECTION_ERROR,
                    message=I18n.get("db_err_interrupted"),
                )
            logger.error(f"Unexpected error testing connection: {e}", exc_info=True)
            return ConnectionResult(
                status=ConnectionStatus.UNKNOWN_ERROR,
                message=I18n.get("db_err_unknown"),
            )

    @classmethod
    async def database_exists(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> bool:
        """
        Check if a database exists.

        Returns:
            True if database exists, False otherwise
        """
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database="postgres",
                ),
                timeout=cls.CONNECTION_TIMEOUT,
            )

            exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
            await conn.close()

            return exists is not None

        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug(f"[DBConfigService] db_exists check failed: {exc}")
            return False

    @classmethod
    async def create_database(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> tuple[bool, str]:
        """
        Create a new database.

        Returns:
            Tuple of (success, message)
        """
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database="postgres",
                ),
                timeout=cls.CONNECTION_TIMEOUT,
            )

            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", database):
                return False, f"Invalid database name: '{database}'"
            safe_name = database.replace('"', '""')
            await conn.execute(f'CREATE DATABASE "{safe_name}"')
            await conn.close()

            logger.info(f"Database '{database}' created successfully")
            return True, f"Database '{database}' created successfully"

        except asyncpg.DuplicateDatabaseError:
            return False, f"Database '{database}' already exists"

        except asyncpg.InsufficientPrivilegeError:
            return False, "Insufficient privileges to create database"

        except Exception as e:
            logger.error(f"Failed to create database: {e}", exc_info=True)
            return False, f"Failed to create database: {str(e)}"

    @classmethod
    def build_url(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        async_driver: bool = True,
    ) -> str:
        """
        Build PostgreSQL connection URL.

        Args:
            host: Database host
            port: Database port
            user: Database user
            password: Database password
            database: Database name
            async_driver: Use async driver (asyncpg) if True

        Returns:
            Connection URL string
        """
        encoded_user = quote_plus(user)
        encoded_password = quote_plus(password)

        driver = "+asyncpg" if async_driver else ""
        return f"postgresql{driver}://{encoded_user}:{encoded_password}@{host}:{port}/{database}"

    @classmethod
    def parse_url(cls, url: str) -> dict | None:
        """
        Parse PostgreSQL connection URL into components.

        Args:
            url: Connection URL string

        Returns:
            Dictionary with connection components or None if parsing fails
        """
        try:
            parsed = urlparse(url)

            if parsed.scheme.startswith("postgresql"):
                return {
                    "host": parsed.hostname or cls.DEFAULT_HOST,
                    "port": parsed.port or cls.DEFAULT_PORT,
                    "user": unquote_plus(parsed.username) if parsed.username else cls.DEFAULT_USER,
                    "password": unquote_plus(parsed.password) if parsed.password else "",
                    "database": parsed.path.lstrip("/") or cls.DEFAULT_DATABASE,
                }

            return None

        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug(f"[DBConfigService] get_db_info failed: {exc}")
            return None

    @classmethod
    async def run_migrations(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> tuple[bool, str]:
        """
        Run database migrations using Alembic.

        Automatically detects legacy databases and handles schema upgrades.
        This method is called during application startup and database configuration.

        Args:
            host: Database host
            port: Database port
            user: Database user
            password: Database password
            database: Database name

        Returns:
            Tuple of (success, message)
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        from data.persistence.db_migrator import DatabaseMigrator

        url = cls.build_url(host, port, user, password, database, async_driver=True)

        try:
            engine = create_async_engine(url, echo=False)

            await DatabaseMigrator.init_db(engine)

            await engine.dispose()

            logger.info(f"Database migrations completed successfully for '{database}'")
            return True, I18n.get("db_migrations_success")

        except Exception as e:
            logger.error(f"Failed to run migrations: {e}", exc_info=True)
            return False, f"Failed to create tables: {str(e)}"

    @classmethod
    async def ensure_tables_exist(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> tuple[bool, str]:
        """
        Ensure all required tables exist in the database.

        This is a convenience method that checks if tables exist and
        creates them if needed.

        Args:
            host: Database host
            port: Database port
            user: Database user
            password: Database password
            database: Database name

        Returns:
            Tuple of (success, message)
        """
        info = await cls.get_database_info(host, port, user, password, database)

        if info is None:
            return False, I18n.get("db_err_connection")

        if info.table_count > 0:
            logger.info(f"Database '{database}' already has {info.table_count} tables")
            return True, I18n.get("db_tables_exist")

        logger.info(f"Database '{database}' is empty, creating tables...")
        return await cls.run_migrations(host, port, user, password, database)

    @classmethod
    async def get_database_info(
        cls,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> DatabaseInfo | None:
        """
        Get database information (version, size, table count).
        """
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                ),
                timeout=cls.CONNECTION_TIMEOUT,
            )

            version = await conn.fetchval("SELECT version()")
            version_short = version.split(",")[0] if version else "Unknown"

            size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size($1))", database)

            table_count = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
            )

            await conn.close()

            return DatabaseInfo(
                version=version_short,
                size=size or "Unknown",
                table_count=table_count or 0,
            )

        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug(f"[DBConfigService] get_db_stats failed: {exc}")
            return None
