import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from data.persistence.db_migrator import DatabaseMigrator


def _make_engine(has_alembic=False, has_old_schema=False, connect_error=None):
    mock_engine = MagicMock()
    if connect_error:
        mock_engine.connect = MagicMock(side_effect=connect_error)
        return mock_engine
    mock_conn = AsyncMock()
    mock_conn.run_sync = AsyncMock(return_value=(has_alembic, has_old_schema))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_engine.connect = MagicMock(return_value=mock_conn)
    return mock_engine


class TestDatabaseMigratorInitDb:
    @pytest.mark.asyncio
    async def test_fresh_database_no_alembic_no_old_schema(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=False)
        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance

            call_count = 0

            async def mock_run_async(task_type, func, *args, **kwargs):
                nonlocal call_count
                if call_count == 0:
                    # First call is get_head_revision
                    call_count += 1
                    return "abc123"
                elif call_count == 1:
                    # Second call is run_alembic_upgrade
                    call_count += 1
                    func()
                    return None

            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            # Patch _get_current_revision to return the same as head so no migration needed
            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="abc123")):
                await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                # Should call run_async once (only get_head_revision)
                assert call_count == 1

    @pytest.mark.asyncio
    async def test_legacy_database_with_old_schema(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=True)
        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance

            call_count = 0

            async def mock_run_async(task_type, func, *args, **kwargs):
                nonlocal call_count
                if call_count == 0:
                    call_count += 1
                    return "abc123"
                elif call_count == 1:
                    call_count += 1
                    func()
                    return None

            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="abc123")):
                await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)

    @pytest.mark.asyncio
    async def test_alembic_already_present(self):
        mock_engine = _make_engine(has_alembic=True, has_old_schema=True)
        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance

            call_count = 0

            async def mock_run_async(task_type, func, *args, **kwargs):
                nonlocal call_count
                if call_count == 0:
                    call_count += 1
                    return "abc123"

            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value="abc123")):
                await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)

    @pytest.mark.asyncio
    async def test_connection_error_still_runs_upgrade(self):
        mock_engine = _make_engine(connect_error=Exception("connection error"))
        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
            # Should have called run_alembic_upgrade
            assert mock_tpm_instance.run_async.called

    @pytest.mark.asyncio
    async def test_upgrade_failure_raises(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=False)
        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance

            call_count = 0

            async def mock_run_async(task_type, func, *args, **kwargs):
                nonlocal call_count
                if call_count == 0:
                    call_count += 1
                    return "abc123"
                elif call_count == 1:
                    raise RuntimeError("upgrade failed")

            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                with pytest.raises(RuntimeError, match="upgrade failed"):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)


class TestDatabaseMigratorAlembicUpgrade:
    @pytest.mark.asyncio
    async def test_legacy_db_stamps_baseline(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=True)

        calls = []

        async def mock_run_async(task_type, func, *args, **kwargs):
            calls.append(func.__name__)
            if len(calls) == 1:
                # First call is get_head_revision
                return "abc123"
            elif len(calls) == 2:
                # Second call is run_alembic_upgrade - execute the function
                func()
                return None

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with (
                patch("data.persistence.db_migrator.command") as mock_command,
                patch("data.persistence.db_migrator.ScriptDirectory") as mock_sd,
                patch("data.persistence.db_migrator.Config"),
            ):
                mock_revision = MagicMock()
                mock_revision.down_revision = None
                mock_revision.revision = "abc123"
                mock_sd_instance = MagicMock()
                mock_sd.from_config.return_value = mock_sd_instance
                mock_sd_instance.walk_revisions.return_value = [mock_revision]

                with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                    mock_command.stamp.assert_called_once()
                    mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_legacy_db_no_baseline_revision(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=True)

        calls = []

        async def mock_run_async(task_type, func, *args, **kwargs):
            calls.append(func.__name__)
            if len(calls) == 1:
                return "abc123"
            elif len(calls) == 2:
                func()
                return None

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with (
                patch("data.persistence.db_migrator.command") as mock_command,
                patch("data.persistence.db_migrator.ScriptDirectory") as mock_sd,
                patch("data.persistence.db_migrator.Config"),
            ):
                mock_sd_instance = MagicMock()
                mock_sd.from_config.return_value = mock_sd_instance
                mock_sd_instance.walk_revisions.return_value = []

                with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                    mock_command.stamp.assert_not_called()
                    mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_fresh_db_no_stamp_needed(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=False)

        calls = []

        async def mock_run_async(task_type, func, *args, **kwargs):
            calls.append(func.__name__)
            if len(calls) == 1:
                return "abc123"
            elif len(calls) == 2:
                func()
                return None

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with (
                patch("data.persistence.db_migrator.command") as mock_command,
                patch("data.persistence.db_migrator.ScriptDirectory"),
                patch("data.persistence.db_migrator.Config"),
            ):
                with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                    mock_command.stamp.assert_not_called()
                    mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_alembic_present_no_stamp_needed(self):
        mock_engine = _make_engine(has_alembic=True, has_old_schema=True)

        calls = []

        async def mock_run_async(task_type, func, *args, **kwargs):
            calls.append(func.__name__)
            if len(calls) == 1:
                return "abc123"
            elif len(calls) == 2:
                func()
                return None

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with (
                patch("data.persistence.db_migrator.command") as mock_command,
                patch("data.persistence.db_migrator.ScriptDirectory"),
                patch("data.persistence.db_migrator.Config"),
            ):
                with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                    mock_command.stamp.assert_not_called()
                    mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    async def test_legacy_db_baseline_with_down_revision(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=True)

        calls = []

        async def mock_run_async(task_type, func, *args, **kwargs):
            calls.append(func.__name__)
            if len(calls) == 1:
                return "root_rev"
            elif len(calls) == 2:
                func()
                return None

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with (
                patch("data.persistence.db_migrator.command") as mock_command,
                patch("data.persistence.db_migrator.ScriptDirectory") as mock_sd,
                patch("data.persistence.db_migrator.Config"),
            ):
                rev1 = MagicMock()
                rev1.down_revision = "parent"
                rev1.revision = "child_rev"
                rev2 = MagicMock()
                rev2.down_revision = None
                rev2.revision = "root_rev"
                mock_sd_instance = MagicMock()
                mock_sd.from_config.return_value = mock_sd_instance
                mock_sd_instance.walk_revisions.return_value = [rev1, rev2]

                with patch.object(DatabaseMigrator, "_get_current_revision", AsyncMock(return_value=None)):
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                    mock_command.stamp.assert_called_once()
                    call_args = mock_command.stamp.call_args
                    assert call_args[0][1] == "root_rev"


class TestDatabaseMigratorGetRevision:
    @pytest.mark.asyncio
    async def test_get_current_revision_returns_revision(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()

        def _sync_get_rev(c):
            return "abc12345"

        mock_conn.run_sync = AsyncMock(return_value="abc12345")
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        result = await DatabaseMigrator._get_current_revision(mock_engine)
        assert result == "abc12345"

    @pytest.mark.asyncio
    async def test_get_current_revision_returns_none_on_error(self):
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=Exception("connection error"))

        result = await DatabaseMigrator._get_current_revision(mock_engine)
        assert result is None

    @pytest.mark.asyncio
    async def test_init_db_logs_revision_after_upgrade(self):
        mock_engine = _make_engine(has_alembic=False, has_old_schema=False)

        calls = []

        async def mock_run_async(task_type, func, *args, **kwargs):
            calls.append(func.__name__)
            if len(calls) == 1:
                return "rev_xyz"
            elif len(calls) == 2:
                func()
                return None

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=mock_run_async)

            with (
                patch("data.persistence.db_migrator.command"),
                patch("data.persistence.db_migrator.ScriptDirectory"),
                patch("data.persistence.db_migrator.Config"),
                patch.object(DatabaseMigrator, "_get_current_revision", new_callable=AsyncMock, return_value="rev_xyz"),
            ):
                with patch("data.persistence.db_migrator.logger") as mock_logger:
                    await DatabaseMigrator.init_db(mock_engine, auto_migrate=True)
                    mock_logger.info.assert_called()
                    # Find the info log that has "rev_xyz"
                    found = False
                    for call in mock_logger.info.call_args_list:
                        if "rev_xyz" in str(call.args):
                            found = True
                            break
                    assert found
