import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from data.persistence.db_migrator import DatabaseMigrator


class TestDatabaseMigratorInitDb:
    @pytest.mark.asyncio
    async def test_fresh_database_no_alembic_no_old_schema(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()

        def _sync_check(c):
            return False, False

        mock_conn.run_sync = AsyncMock(return_value=(False, False))
        mock_engine.connect = MagicMock(return_value=mock_conn)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            await DatabaseMigrator.init_db(mock_engine)
            mock_tpm_instance.run_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_legacy_database_with_old_schema(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock(return_value=(False, True))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            await DatabaseMigrator.init_db(mock_engine)
            mock_tpm_instance.run_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_alembic_already_present(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock(return_value=(True, True))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            await DatabaseMigrator.init_db(mock_engine)

    @pytest.mark.asyncio
    async def test_connection_error_still_runs_upgrade(self):
        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(side_effect=Exception("connection error"))

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(return_value=None)
            await DatabaseMigrator.init_db(mock_engine)

    @pytest.mark.asyncio
    async def test_upgrade_failure_raises(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.run_sync = AsyncMock(return_value=(False, False))
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.connect = MagicMock(return_value=mock_conn)

        with patch("data.persistence.db_migrator.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            mock_tpm_instance.run_async = AsyncMock(side_effect=RuntimeError("upgrade failed"))
            with pytest.raises(RuntimeError, match="upgrade failed"):
                await DatabaseMigrator.init_db(mock_engine)
