"""
Tests for DatabaseMigrator module.

验证数据库迁移模块的正确性，包括全新安装、遗留库兼容、正常升级三种场景。
测试完全使用 Mock，不连接任何真实数据库。
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def mock_engine():
    """创建模拟的 SQLAlchemy 异步引擎"""
    engine = MagicMock()
    conn_mock = AsyncMock()
    engine.connect.return_value.__aenter__.return_value = conn_mock
    return engine, conn_mock


@pytest.fixture
def mock_thread_pool():
    """创建模拟的线程池管理器"""
    with patch("data.db_migrator.ThreadPoolManager") as mock_tp:
        tp_instance = MagicMock()
        tp_instance.run_async = AsyncMock()
        mock_tp.return_value = tp_instance
        yield tp_instance


class TestDatabaseMigrator:
    """测试 DatabaseMigrator 类"""

    @pytest.mark.asyncio
    @patch("data.db_migrator.command")
    @patch("data.db_migrator.ScriptDirectory")
    @patch("data.db_migrator.Config")
    async def test_fresh_install(
        self, mock_config, mock_script, mock_command, mock_thread_pool, mock_engine
    ):
        """测试场景一：全新安装环境（空库）"""
        engine, conn = mock_engine
        conn.run_sync = AsyncMock(return_value=(False, False))

        from data.db_migrator import DatabaseMigrator

        await DatabaseMigrator.init_db(engine)

        args, _ = mock_thread_pool.run_async.call_args
        run_alembic_func = args[1]
        run_alembic_func()

        mock_command.stamp.assert_not_called()
        mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.db_migrator.command")
    @patch("data.db_migrator.ScriptDirectory")
    @patch("data.db_migrator.Config")
    async def test_legacy_schema_backward_compatibility(
        self, mock_config, mock_script, mock_command, mock_thread_pool, mock_engine
    ):
        """测试场景二：老用户向后兼容环境（有旧表，无 alembic_version）"""
        engine, conn = mock_engine
        conn.run_sync = AsyncMock(return_value=(False, True))

        mock_rev1 = MagicMock(revision="origin_base_123", down_revision=None)
        mock_rev2 = MagicMock(revision="latest_456", down_revision="origin_base_123")
        mock_script.from_config.return_value.walk_revisions.return_value = [
            mock_rev2,
            mock_rev1,
        ]

        from data.db_migrator import DatabaseMigrator

        await DatabaseMigrator.init_db(engine)

        args, _ = mock_thread_pool.run_async.call_args
        args[1]()

        mock_command.stamp.assert_called_once()
        stamp_args = mock_command.stamp.call_args[0]
        assert stamp_args[1] == "origin_base_123"
        mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.db_migrator.command")
    @patch("data.db_migrator.ScriptDirectory")
    @patch("data.db_migrator.Config")
    async def test_up_to_date_schema(
        self, mock_config, mock_script, mock_command, mock_thread_pool, mock_engine
    ):
        """测试场景三：正常更新迭代（已有 alembic_version）"""
        engine, conn = mock_engine
        conn.run_sync = AsyncMock(return_value=(True, True))

        from data.db_migrator import DatabaseMigrator

        await DatabaseMigrator.init_db(engine)

        args, _ = mock_thread_pool.run_async.call_args
        args[1]()

        mock_command.stamp.assert_not_called()
        mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.db_migrator.command")
    @patch("data.db_migrator.ScriptDirectory")
    @patch("data.db_migrator.Config")
    async def test_legacy_schema_no_baseline_found(
        self, mock_config, mock_script, mock_command, mock_thread_pool, mock_engine
    ):
        """测试场景四：遗留库但找不到基线版本"""
        engine, conn = mock_engine
        conn.run_sync = AsyncMock(return_value=(False, True))

        mock_script.from_config.return_value.walk_revisions.return_value = []

        from data.db_migrator import DatabaseMigrator

        await DatabaseMigrator.init_db(engine)

        args, _ = mock_thread_pool.run_async.call_args
        args[1]()

        mock_command.stamp.assert_not_called()
        mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.db_migrator.command")
    @patch("data.db_migrator.ScriptDirectory")
    @patch("data.db_migrator.Config")
    async def test_inspection_error_continues(
        self, mock_config, mock_script, mock_command, mock_thread_pool, mock_engine
    ):
        """测试场景五：数据库检查失败时继续执行"""
        engine, conn = mock_engine
        conn.run_sync = AsyncMock(side_effect=Exception("Connection error"))

        from data.db_migrator import DatabaseMigrator

        await DatabaseMigrator.init_db(engine)

        args, _ = mock_thread_pool.run_async.call_args
        args[1]()

        mock_command.upgrade.assert_called_once()

    @pytest.mark.asyncio
    @patch("data.db_migrator.command")
    @patch("data.db_migrator.ScriptDirectory")
    @patch("data.db_migrator.Config")
    async def test_upgrade_error_propagates(
        self, mock_config, mock_script, mock_command, mock_engine
    ):
        """测试场景六：升级失败时抛出异常"""
        engine, conn = mock_engine
        conn.run_sync = AsyncMock(return_value=(False, False))

        with patch("data.db_migrator.ThreadPoolManager") as mock_tp:
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(side_effect=Exception("Upgrade failed"))
            mock_tp.return_value = tp_instance

            from data.db_migrator import DatabaseMigrator

            with pytest.raises(Exception, match="Upgrade failed"):
                await DatabaseMigrator.init_db(engine)
