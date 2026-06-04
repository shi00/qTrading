import asyncio

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import asyncpg
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, ProgrammingError

from data.persistence.db_migrator import (
    DatabaseMigrator,
    DatabaseMigrationNeeded,
    _CONNECTION_EXCEPTIONS,
    _RELATION_NOT_FOUND_KEYWORDS,
)


class TestDatabaseMigrationNeeded:
    def test_exception_message(self):
        exc = DatabaseMigrationNeeded("old_rev", "new_rev")
        assert exc.current_rev == "old_rev"
        assert exc.head_rev == "new_rev"
        assert "old_rev" in str(exc)
        assert "new_rev" in str(exc)

    def test_exception_with_none_current_rev(self):
        exc = DatabaseMigrationNeeded(None, "new_rev")
        assert exc.current_rev is None
        assert exc.head_rev == "new_rev"


class TestShouldAutoMigrate:
    def test_auto_migrate_env_1(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "1")
        result = DatabaseMigrator._should_auto_migrate()
        assert result is True

    def test_auto_migrate_env_true(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "true")
        result = DatabaseMigrator._should_auto_migrate()
        assert result is True

    def test_auto_migrate_env_yes(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "yes")
        result = DatabaseMigrator._should_auto_migrate()
        assert result is True

    def test_auto_migrate_env_false(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "false")
        result = DatabaseMigrator._should_auto_migrate()
        assert result is False

    def test_auto_migrate_env_empty(self, monkeypatch):
        monkeypatch.delenv("AUTO_MIGRATE", raising=False)
        result = DatabaseMigrator._should_auto_migrate()
        assert result is False

    def test_auto_migrate_env_random(self, monkeypatch):
        monkeypatch.setenv("AUTO_MIGRATE", "random")
        result = DatabaseMigrator._should_auto_migrate()
        assert result is False


class TestGetAlembicConfig:
    """Tests for _get_alembic_config shared method (P6 fix)."""

    def test_returns_config_with_correct_paths(self):
        cfg = DatabaseMigrator._get_alembic_config()
        script_location = cfg.get_main_option("script_location")
        assert script_location is not None
        assert "alembic" in script_location

    def test_config_file_name_set(self):
        cfg = DatabaseMigrator._get_alembic_config()
        assert cfg.config_file_name is not None
        assert cfg.config_file_name.endswith("alembic.ini")


class TestCheckSchemaStatus:
    @pytest.mark.asyncio
    async def test_check_schema_status_returns_tuple(self):
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="head_rev")):
            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="current_rev")):
                current, head, needs = await DatabaseMigrator.check_schema_status(mock_engine)
                assert current == "current_rev"
                assert head == "head_rev"
                assert needs is True

    @pytest.mark.asyncio
    async def test_check_schema_status_no_migration_needed(self):
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="same_rev")):
            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="same_rev")):
                current, head, needs = await DatabaseMigrator.check_schema_status(mock_engine)
                assert current == "same_rev"
                assert head == "same_rev"
                assert needs is False


class TestInitDbFreshDatabase:
    """Tests for fresh database initialization through Alembic."""

    @pytest.mark.asyncio
    async def test_fresh_database_runs_alembic_upgrade(self):
        """Fresh database (no alembic_version) should run Alembic from base to head."""
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
            with patch.object(DatabaseMigrator, "_run_alembic_upgrade", AsyncMock(return_value=None)) as mock_upgrade:
                await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)

                mock_upgrade.assert_awaited_once_with(mock_engine)

    @pytest.mark.asyncio
    async def test_fresh_database_does_not_require_auto_migrate_flag(self):
        """Fresh database creation is required bootstrap work even when auto_migrate is disabled."""
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
            with patch.object(DatabaseMigrator, "_run_alembic_upgrade", AsyncMock(return_value=None)) as mock_upgrade:
                await DatabaseMigrator.init_db(mock_engine, auto_migrate=False)

                mock_upgrade.assert_awaited_once_with(mock_engine)


class TestSyncDatabaseUrl:
    def test_converts_asyncpg_url_to_sync_url(self):
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/app_db"

        result = DatabaseMigrator._get_sync_database_url(mock_engine)

        assert result == "postgresql://user:pass@localhost:5432/app_db"

    def test_converts_sqlalchemy_url_without_hiding_password(self):
        mock_engine = MagicMock()
        mock_engine.url = URL.create(
            "postgresql+asyncpg",
            username="user",
            password="secret",
            host="localhost",
            port=5432,
            database="app_db",
        )

        result = DatabaseMigrator._get_sync_database_url(mock_engine)

        assert result == "postgresql://user:secret@localhost:5432/app_db"

    def test_raises_when_engine_has_no_url(self):
        mock_engine = MagicMock()
        del mock_engine.url

        with pytest.raises(RuntimeError, match="does not expose a URL"):
            DatabaseMigrator._get_sync_database_url(mock_engine)


class TestInitDbExistingDatabase:
    """Tests for existing database with alembic_version table."""

    @pytest.mark.asyncio
    async def test_schema_up_to_date_no_upgrade(self):
        """Database already at latest version should skip upgrade."""
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0001")):
            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0001")):
                with patch("data.persistence.db_migrator.logger") as mock_logger:
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)

                    # Should log "up to date"
                    found = False
                    for call in mock_logger.info.call_args_list:
                        if "up to date" in str(call.args):
                            found = True
                            break
                    assert found

    @pytest.mark.asyncio
    async def test_raises_migration_needed_when_auto_migrate_disabled(self):
        """Existing database needing migration should raise if auto_migrate=False."""
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0002")):
            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0001")):
                with pytest.raises(DatabaseMigrationNeeded) as exc_info:
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=False)

                assert exc_info.value.current_rev == "0001"
                assert exc_info.value.head_rev == "0002"

    @pytest.mark.asyncio
    async def test_existing_database_runs_alembic_upgrade(self):
        """Existing database needing migration should run Alembic upgrade."""
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0002")):
            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0001")):
                with patch.object(DatabaseMigrator, "_run_alembic_upgrade", AsyncMock(return_value=None)):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)


class TestInitDbErrorHandling:
    @pytest.mark.asyncio
    async def test_get_head_revision_failure_raises(self):
        mock_engine = MagicMock()

        with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0001")):
            with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(side_effect=Exception("failed"))):
                with pytest.raises(Exception, match="failed"):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)


class TestDatabaseMigratorGetRevision:
    @pytest.mark.asyncio
    async def test_get_current_revision_returns_revision(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()

        mock_conn.run_sync = AsyncMock(return_value="0001")
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        result = await DatabaseMigrator._get_current_revision(mock_engine)
        assert result == "0001"

    @pytest.mark.asyncio
    async def test_get_current_revision_returns_none_on_programming_error(self):
        """ProgrammingError (table not found) should return None (fresh database)."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=ProgrammingError("relation does not exist", {}, Exception("orig")))  # type: ignore[reportArgumentType]

        result = await DatabaseMigrator._get_current_revision(mock_engine)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_non_relation_programming_error(self):
        """ProgrammingError not related to missing relation (e.g., syntax error) must be raised."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(
            side_effect=ProgrammingError("syntax error at or near SELECT", {}, Exception("orig"))  # type: ignore[reportArgumentType]
        )

        with pytest.raises(ProgrammingError):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_permission_programming_error(self):
        """ProgrammingError for permission denied must be raised, not swallowed."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(
            side_effect=ProgrammingError("permission denied for table alembic_version", {}, Exception("orig"))  # type: ignore[reportArgumentType]
        )

        with pytest.raises(ProgrammingError):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_relation_not_found_keywords_cover_common_cases(self):
        """Verify _RELATION_NOT_FOUND_KEYWORDS covers common PostgreSQL error messages."""
        for keyword in _RELATION_NOT_FOUND_KEYWORDS:
            assert isinstance(keyword, str)
            assert len(keyword) > 0
        # "does not exist" matches PostgreSQL "relation ... does not exist"
        assert "does not exist" in _RELATION_NOT_FOUND_KEYWORDS

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_operational_error(self):
        """OperationalError (permission/structure issues) must be raised, not swallowed."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=OperationalError("permission denied", {}, Exception("orig")))  # type: ignore[reportArgumentType]

        with pytest.raises(OperationalError):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_unexpected_error(self):
        """Unexpected errors (e.g., RuntimeError) must be raised, not swallowed as fresh database."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=RuntimeError("unexpected schema error"))

        with pytest.raises(RuntimeError, match="unexpected schema error"):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_get_current_revision_no_alembic_table(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()

        mock_conn.run_sync = AsyncMock(return_value=None)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch("data.persistence.db_migrator.inspect") as mock_inspect:
            mock_inspector = MagicMock()
            mock_inspector.get_table_names.return_value = ["stock_basic"]
            mock_inspect.return_value = mock_inspector

            result = await DatabaseMigrator._get_current_revision(mock_engine)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_connection_error(self):
        """Connection-level errors (OSError, ConnectionError, asyncpg) must be raised, not swallowed."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=ConnectionError("connection refused"))

        with pytest.raises(ConnectionError, match="connection refused"):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_os_error(self):
        """OSError must be raised, not swallowed as fresh database."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=OSError("network unreachable"))

        with pytest.raises(OSError, match="network unreachable"):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_get_current_revision_raises_on_asyncpg_connection_error(self):
        """asyncpg.PostgresConnectionError must be raised, not swallowed."""
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=asyncpg.PostgresConnectionError("server closed the connection"))

        with pytest.raises(asyncpg.PostgresConnectionError):
            await DatabaseMigrator._get_current_revision(mock_engine)

    @pytest.mark.asyncio
    async def test_connection_exceptions_tuple_includes_expected_types(self):
        """Verify _CONNECTION_EXCEPTIONS includes all expected types."""
        assert asyncpg.PostgresConnectionError in _CONNECTION_EXCEPTIONS
        assert ConnectionError in _CONNECTION_EXCEPTIONS
        assert OSError in _CONNECTION_EXCEPTIONS


class TestRunAlembicUpgradeConfig:
    """Tests for Alembic URL binding."""

    @pytest.mark.asyncio
    async def test_upgrade_passes_engine_url_to_alembic_config(self):
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/target_db"
        captured_cfg = MagicMock()
        captured_cfg.attributes = {}  # Use real dict to allow __setitem__/__getitem__ to work

        async def run_task(_task_type, func):
            func()

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
            mock_instance = MagicMock()
            mock_tp.return_value = mock_instance
            mock_instance.run_async = AsyncMock(side_effect=run_task)

            with patch.object(DatabaseMigrator, "_get_alembic_config", return_value=captured_cfg):
                with patch("data.persistence.db_migrator.command.upgrade") as mock_upgrade:
                    with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0002")):
                        with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0002")):
                            await DatabaseMigrator._run_alembic_upgrade(mock_engine)

        captured_cfg.set_main_option.assert_called_once_with(
            "sqlalchemy.url",
            "postgresql://user:pass@localhost:5432/target_db",
        )
        assert captured_cfg.attributes["database_url"] == "postgresql://user:pass@localhost:5432/target_db"
        mock_upgrade.assert_called_once_with(captured_cfg, "head")


class TestRunAlembicUpgradeErrorClassification:
    """Tests for _run_alembic_upgrade using standard error classification (E1 fix)."""

    @pytest.mark.asyncio
    async def test_upgrade_failure_uses_error_classifier(self):
        """Migration failure should use classify_error and classify_severity."""
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/test_db"

        with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0001")):
            with patch.object(
                DatabaseMigrator,
                "_get_head_revision",
                AsyncMock(return_value="0002"),
            ):
                with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
                    mock_instance = MagicMock()
                    mock_tp.return_value = mock_instance
                    mock_instance.run_async = AsyncMock(side_effect=OSError("connection lost"))

                    with patch(
                        "data.persistence.db_migrator.classify_error",
                        return_value={"code": "refused", "message_key": "db_err_refused"},
                    ) as mock_classify:
                        with patch(
                            "data.persistence.db_migrator.classify_severity",
                            return_value="recoverable",
                        ) as mock_severity:
                            with pytest.raises(OSError, match="connection lost"):
                                await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)

                            # Verify error classifiers were called
                            mock_classify.assert_called_once()
                            mock_severity.assert_called_once()


class TestRunAlembicUpgradePostVerification:
    """Tests for post-upgrade version verification."""

    @pytest.mark.asyncio
    async def test_upgrade_raises_on_version_mismatch(self):
        """If upgrade completes but version doesn't match head, raise RuntimeError."""
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/test_db"

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
            mock_instance = MagicMock()
            mock_tp.return_value = mock_instance
            mock_instance.run_async = AsyncMock(return_value=None)

            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0001")):
                with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0002")):
                    with pytest.raises(RuntimeError, match="version mismatch"):
                        await DatabaseMigrator._run_alembic_upgrade(mock_engine)

    @pytest.mark.asyncio
    async def test_upgrade_warns_on_none_revision(self):
        """If revision is None after upgrade, log a warning (not crash)."""
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/test_db"

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
            mock_instance = MagicMock()
            mock_tp.return_value = mock_instance
            mock_instance.run_async = AsyncMock(return_value=None)

            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0002")):
                    with patch("data.persistence.db_migrator.logger") as mock_logger:
                        await DatabaseMigrator._run_alembic_upgrade(mock_engine)
                        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_upgrade_succeeds_when_version_matches_head(self):
        """If upgrade completes and version matches head, no error."""
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/test_db"

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
            mock_instance = MagicMock()
            mock_tp.return_value = mock_instance
            mock_instance.run_async = AsyncMock(return_value=None)

            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="0002")):
                with patch.object(DatabaseMigrator, "_get_head_revision", AsyncMock(return_value="0002")):
                    await DatabaseMigrator._run_alembic_upgrade(mock_engine)


class TestRunAlembicUpgradeCancelledError:
    """Tests for CancelledError propagation in _run_alembic_upgrade (R2 compliance).

    R2: CancelledError must propagate for graceful shutdown.
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """CancelledError must propagate for graceful shutdown."""
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/test_db"

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
            mock_instance = MagicMock()
            mock_tp.return_value = mock_instance
            # Simulate CancelledError from run_async
            mock_instance.run_async = AsyncMock(side_effect=asyncio.CancelledError())

            with pytest.raises(asyncio.CancelledError):
                await DatabaseMigrator._run_alembic_upgrade(mock_engine)

    @pytest.mark.asyncio
    async def test_cancelled_error_logs_warning(self):
        """CancelledError should log a warning before propagating."""
        mock_engine = MagicMock()
        mock_engine.url = "postgresql+asyncpg://user:pass@localhost:5432/test_db"

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tp:
            mock_instance = MagicMock()
            mock_tp.return_value = mock_instance
            mock_instance.run_async = AsyncMock(side_effect=asyncio.CancelledError())

            with patch("data.persistence.db_migrator.logger") as mock_logger:
                with pytest.raises(asyncio.CancelledError):
                    await DatabaseMigrator._run_alembic_upgrade(mock_engine)

                # Verify warning was logged
                mock_logger.warning.assert_called_once()
                assert "cancelled" in mock_logger.warning.call_args[0][0].lower()
