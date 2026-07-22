"""
Database Configuration Service

Provides database connection testing, creation, and configuration management.
"""

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote_plus, unquote_plus, urlparse

import asyncpg

from core.i18n import I18n
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

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
    @log_async_operation(operation_name="db_test_connection", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
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
            try:
                version = await conn.fetchval("SELECT version()")
            finally:
                await conn.close()

            return ConnectionResult(
                status=ConnectionStatus.SUCCESS,
                message=I18n.get("db_msg_success"),
                server_version=version.split(",")[0] if version else None,
                database_exists=True,
            )

        except asyncpg.InvalidPasswordError:
            logger.warning(
                "[DBConfigService] Authentication failed for user '%s'@%s:%s (database: %s)",
                user,
                host,
                port,
                target_db,
            )
            return ConnectionResult(
                status=ConnectionStatus.AUTHENTICATION_ERROR,
                message=I18n.get("db_err_auth"),
            )

        except asyncpg.InvalidCatalogNameError:
            logger.warning(
                "[DBConfigService] Database '%s' not found on %s:%s",
                database,
                host,
                port,
            )
            return ConnectionResult(
                status=ConnectionStatus.DATABASE_NOT_FOUND,
                message=I18n.get("db_err_not_found").format(database=database),
                database_exists=False,
            )

        except TimeoutError:
            logger.warning(
                "[DBConfigService] Connection timeout to %s:%s (database: %s) after %.1fs",
                host,
                port,
                target_db,
                cls.CONNECTION_TIMEOUT,
            )
            return ConnectionResult(
                status=ConnectionStatus.TIMEOUT,
                message=I18n.get("db_err_timeout"),
            )

        except OSError as e:
            if "Connection refused" in str(e) or "No route to host" in str(e):
                logger.warning(
                    "[DBConfigService] Connection refused: %s:%s (error: %s)",
                    host,
                    port,
                    DataSanitizer.sanitize_error(e),
                )
                return ConnectionResult(
                    status=ConnectionStatus.CONNECTION_ERROR,
                    message=I18n.get("db_err_refused").format(host=host, port=port),
                )
            if "WinError 64" in str(e):
                logger.warning(
                    "[DBConfigService] Network error (WinError 64) connecting to %s:%s: %s",
                    host,
                    port,
                    DataSanitizer.sanitize_error(e),
                )
                return ConnectionResult(
                    status=ConnectionStatus.CONNECTION_ERROR,
                    message=I18n.get("db_err_proxy"),
                )
            logger.warning(
                "[DBConfigService] OS error connecting to %s:%s: %s",
                host,
                port,
                DataSanitizer.sanitize_error(e),
            )
            return ConnectionResult(
                status=ConnectionStatus.CONNECTION_ERROR,
                message=I18n.get("db_err_network"),
            )

        except asyncpg.exceptions.PostgresError as e:
            error_type_name = type(e).__name__

            if error_type_name == "ConnectionDoesNotExistError":
                # asyncpg 对数据库不存在也抛 ConnectionDoesNotExistError
                # 需要先连接 postgres 验证认证，再判断是否是数据库不存在
                if target_db != "postgres":
                    verify_conn = None
                    try:
                        verify_conn = await asyncio.wait_for(
                            asyncpg.connect(
                                host=host,
                                port=port,
                                user=user,
                                password=password,
                                database="postgres",
                            ),
                            timeout=cls.CONNECTION_TIMEOUT,
                        )
                        # 认证成功，说明是目标数据库不存在
                        logger.warning(
                            "[DBConfigService] Database '%s' not found on %s:%s (auth verified)",
                            target_db,
                            host,
                            port,
                        )
                        return ConnectionResult(
                            status=ConnectionStatus.DATABASE_NOT_FOUND,
                            message=I18n.get("db_err_not_found").format(database=target_db),
                            database_exists=False,
                        )
                    except asyncpg.InvalidPasswordError:
                        logger.warning(
                            "[DBConfigService] Authentication failed for user '%s'@%s:%s",
                            user,
                            host,
                            port,
                        )
                        return ConnectionResult(
                            status=ConnectionStatus.AUTHENTICATION_ERROR,
                            message=I18n.get("db_err_auth"),
                        )
                    except (asyncpg.exceptions.PostgresError, TimeoutError, OSError) as verify_err:
                        # 二次验证也失败，无法确定是密码错误还是瞬态连接问题
                        # asyncpg 在 Windows 上对密码错误和瞬态连接问题都抛 ConnectionDoesNotExistError，
                        # 无法区分。返回 CONNECTION_ERROR 并建议用户重试，避免误判 AUTHENTICATION_ERROR
                        # 导致用户困惑（密码正确却提示认证失败）
                        logger.warning(
                            "[DBConfigService] Verification connection failed for user '%s'@%s:%s: %s",
                            user,
                            host,
                            port,
                            DataSanitizer.sanitize_error(verify_err),
                        )
                        return ConnectionResult(
                            status=ConnectionStatus.CONNECTION_ERROR,
                            message=I18n.get("db_err_interrupted"),
                        )
                    finally:
                        # 确保验证连接在所有路径（包括异常路径）中都被关闭
                        if verify_conn is not None:
                            with contextlib.suppress(Exception):
                                await verify_conn.close()

                # target_db == "postgres"，无法二次验证，按认证错误处理
                logger.warning(
                    "[DBConfigService] Authentication failed (%s) for user '%s'@%s:%s: %s",
                    error_type_name,
                    user,
                    host,
                    port,
                    DataSanitizer.sanitize_error(e),
                )
                return ConnectionResult(
                    status=ConnectionStatus.AUTHENTICATION_ERROR,
                    message=I18n.get("db_err_auth"),
                )
            logger.warning(
                "[DBConfigService] PostgreSQL error (%s) on %s:%s: %s",
                error_type_name,
                host,
                port,
                DataSanitizer.sanitize_error(e),
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
            logger.error("Unexpected error testing connection: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Unexpected error testing connection traceback:", exc_info=True)
            return ConnectionResult(
                status=ConnectionStatus.UNKNOWN_ERROR,
                message=I18n.get("db_err_unknown"),
            )

    @classmethod
    @log_async_operation(operation_name="db_database_exists", threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
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
            try:
                exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
            finally:
                await conn.close()

            return exists is not None

        except Exception as exc:
            logger.debug("[DBConfigService] db_exists check failed: %s", DataSanitizer.sanitize_error(exc))
            return False

    @classmethod
    @log_async_operation(operation_name="db_create_database", threshold_ms=PerfThreshold.DB_BULK_IO)
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

        SECURITY: ``CREATE DATABASE`` 使用 f-string 拼接 ``database`` 名称，无法参数化。
        原因：PostgreSQL DDL 语句（CREATE/DROP/ALTER DATABASE）不支持 bind parameters，
        数据库名是标识符（identifier）而非字面值，必须直接出现在 SQL 文本中。
        安全边界由以下两层防护保证：
        1. 白名单校验：``re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", database)``
           仅允许字母开头、字母数字下划线组合，拒绝任何特殊字符（含引号、分号、注释符）。
        2. 双引号转义：``database.replace('"', '""')`` 将双引号转义为两个双引号，
           防止引号注入（即使白名单已拒绝，仍作为深度防御保留）。
        """
        # PostgreSQL 标识符最大长度 (NAMEDATALEN - 1)
        MAX_DATABASE_NAME_LENGTH = 63

        if len(database) > MAX_DATABASE_NAME_LENGTH:
            return False, I18n.get("db_err_name_too_long").format(max_length=MAX_DATABASE_NAME_LENGTH)

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
            try:
                if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", database):
                    return False, I18n.get("db_err_invalid_name").format(database=database)
                safe_name = database.replace('"', '""')
                # SECURITY: CREATE DATABASE 不支持参数化（PostgreSQL DDL 限制），
                # safe_name 已通过白名单校验和双引号转义，安全边界见方法 docstring。
                await conn.execute(f'CREATE DATABASE "{safe_name}"')
            finally:
                await conn.close()

            logger.info("Database '%s' created successfully", database)
            return True, I18n.get("db_msg_created").format(database=database)

        except asyncpg.DuplicateDatabaseError:
            return False, I18n.get("db_err_already_exists").format(database=database)

        except asyncpg.InsufficientPrivilegeError:
            return False, I18n.get("db_err_no_privilege")

        except Exception as e:
            logger.error("Failed to create database: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Failed to create database traceback:", exc_info=True)
            return False, I18n.get("db_err_create_failed")

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

        except Exception as exc:
            logger.debug("[DBConfigService] get_db_info failed: %s", DataSanitizer.sanitize_error(exc))
            return None

    @classmethod
    @log_async_operation(operation_name="db_run_migrations", threshold_ms=PerfThreshold.DB_BULK_IO)
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
        from data.persistence.db_url_override import override_db_url

        url = cls.build_url(host, port, user, password, database, async_driver=True)

        try:
            engine = create_async_engine(url, echo=False)
            try:
                # Ensure Alembic env.py can find the correct database URL
                with override_db_url(url):
                    await DatabaseMigrator.init_db(engine, auto_migrate=True)

                logger.info("Database migrations completed successfully for '%s'", database)
                return True, I18n.get("db_migrations_success")
            finally:
                await engine.dispose()
        except Exception as e:
            logger.error("Failed to run migrations: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Failed to run migrations traceback:", exc_info=True)
            return False, I18n.get("db_err_migration_failed")

    @classmethod
    @log_async_operation(operation_name="db_ensure_tables", threshold_ms=PerfThreshold.DB_BULK_IO)
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

        Checks for the alembic_version table (the authoritative marker that
        Alembic has initialized this database). If missing, runs migrations
        regardless of whether other tables exist — the database may contain
        unrelated tables from another application.

        Args:
            host: Database host
            port: Database port
            user: Database user
            password: Database password
            database: Database name

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
                    database=database,
                ),
                timeout=cls.CONNECTION_TIMEOUT,
            )
            try:
                has_alembic = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version')"
                )
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("[DBConfigService] Failed to check alembic_version: %s", DataSanitizer.sanitize_error(exc))
            return False, I18n.get("db_err_connection")

        if has_alembic:
            logger.info("Database '%s' already managed by Alembic", database)
            return True, I18n.get("db_tables_exist")

        logger.info("Database '%s' has no alembic_version table, running migrations...", database)
        return await cls.run_migrations(host, port, user, password, database)

    @classmethod
    @log_async_operation(operation_name="db_get_info", threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
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
            try:
                version = await conn.fetchval("SELECT version()")
                version_short = version.split(",")[0] if version else "Unknown"

                size = await conn.fetchval("SELECT pg_size_pretty(pg_database_size($1))", database)

                table_count = await conn.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
                )
            finally:
                await conn.close()

            return DatabaseInfo(
                version=version_short,
                size=size or "Unknown",
                table_count=table_count or 0,
            )

        except Exception as exc:
            logger.debug("[DBConfigService] get_db_stats failed: %s", DataSanitizer.sanitize_error(exc))
            return None
