# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。

"""EngineManager 单元测试（review01-A4 Step2 拆分）。

引擎生命周期（创建 / dispose / 连接串解析 / URL 脱敏 / engine_provider 同步）
从 CacheManager 拆出后的独立行为验证。DAO 注册清单测试见
``test_cache_manager_dao_registry.py``。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from data.cache.engine_manager import EngineManager
from data.persistence import engine_provider

pytestmark = pytest.mark.unit


def _reset_provider():
    engine_provider.reset_engine_provider()


class TestEngineManager:
    def test_get_connection_string_from_config_handler(self):
        em = EngineManager()
        with patch("data.cache.engine_manager.ConfigHandler.get_db_url", return_value="postgresql://u:p@h/db"):
            assert em.get_connection_string() == "postgresql://u:p@h/db"

    def test_get_connection_string_fallback_config(self):
        em = EngineManager()
        with (
            patch("data.cache.engine_manager.ConfigHandler.get_db_url", return_value=None),
            patch("data.cache.engine_manager.config.DB_URL", "postgresql://u:p@h/db"),
        ):
            assert em.get_connection_string() == "postgresql://u:p@h/db"

    def test_sanitize_url_hides_password(self):
        result = EngineManager.sanitize_url("postgresql://user:secret@localhost/db")
        assert "secret" not in result
        assert "****" in result

    def test_sanitize_url_empty(self):
        assert EngineManager.sanitize_url("") == "None"

    @pytest.mark.asyncio
    async def test_create_engine_syncs_provider(self):
        _reset_provider()
        em = EngineManager()
        mock_engine = MagicMock()
        with (
            patch("data.cache.engine_manager.create_async_engine", return_value=mock_engine) as mock_create,
            patch("data.cache.engine_manager.get_db_pool_config", return_value={}),
        ):
            em.create_engine("postgresql://u:p@h/db")
        # 强断言：验证 create_async_engine 调用参数
        mock_create.assert_called_once_with(
            "postgresql://u:p@h/db",
            echo=False,
            future=True,
        )
        assert em.engine is mock_engine
        # R5: 引擎创建后 provider 恢复可用（mark_disposed(False)）
        assert engine_provider.is_disposed() is False

    @pytest.mark.asyncio
    async def test_dispose_clears_engine_and_provider_ref(self):
        _reset_provider()
        em = EngineManager()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        em.engine = mock_engine
        engine_provider.set_engine(em.engine)
        await em.dispose()
        mock_engine.dispose.assert_called_once_with()
        assert em.engine is None
        # dispose 后 provider 引擎引用清空（disposed 布尔标记由组合根 CacheManager.close 维护）
        assert engine_provider._engine is None
