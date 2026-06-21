"""CacheManager close → init_db 重初始化回归测试。"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.mark.unit
class TestCacheManagerLifecycle:
    @staticmethod
    def _get_cm_cls():
        from data.cache.cache_manager import CacheManager

        return CacheManager

    @pytest.mark.asyncio
    async def test_close_resets_schema_initialized(self):
        """close() 后 _schema_initialized 应重置为 False。"""
        from data.cache.cache_manager import CacheManager

        cm = CacheManager()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        cm.engine = mock_engine
        cm._schema_initialized = True

        await cm.close()
        assert cm.engine is None
        assert cm._schema_initialized is False

    @pytest.mark.asyncio
    async def test_init_db_recovers_when_engine_is_none(self):
        """engine=None 时 init_db 应重建引擎，不因 _schema_initialized 跳过。"""
        from data.cache.cache_manager import CacheManager

        cm = CacheManager()
        cm._schema_initialized = True
        cm.engine = None

        fake_url = "postgresql+asyncpg://test:test@localhost/test"
        with (
            patch.object(cm, "_get_connection_string", return_value=fake_url),
            patch.object(cm, "_create_engine") as mock_create,
            patch("data.persistence.db_migrator.DatabaseMigrator") as mock_migrator,
        ):
            mock_migrator.init_db = AsyncMock()
            await cm.init_db()

        mock_create.assert_called_once_with(fake_url)
