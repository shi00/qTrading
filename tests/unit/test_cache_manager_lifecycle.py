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


@pytest.mark.unit
class TestCacheManagerCloseLoopLocalLocks:
    """P3-M5-Close-DelLoopLocal-Risk: close() 生产路径不应删除 loop-local 锁实例。

    背景：close() 内 del_loop_local("cache_maint_event") / del_loop_local("cache_init_lock")
    会导致 web 模式下 close → init_db 重新创建锁实例，破坏并发保护语义
    （旧锁引用与新锁实例不是同一对象）。测试隔离已由 _reset_singleton 处理。
    """

    @pytest.mark.asyncio
    async def test_close_does_not_delete_loop_local_locks(self):
        """close() 后 _init_lock 和 _maintenance_event 应保持同一实例。"""
        from data.cache.cache_manager import CacheManager

        cm = CacheManager()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        cm.engine = mock_engine
        cm._schema_initialized = True

        lock_before = cm._init_lock
        event_before = cm._maintenance_event

        await cm.close()

        # close() 后访问 property 应返回同一实例（未被 del_loop_local 清理）
        assert cm._init_lock is lock_before, (
            "close() 不应删除 _init_lock loop-local 实例；删除后会破坏 web 模式并发保护"
        )
        assert cm._maintenance_event is event_before, (
            "close() 不应删除 _maintenance_event loop-local 实例；删除后会破坏 web 模式并发保护"
        )

    @pytest.mark.asyncio
    async def test_close_preserves_lock_for_subsequent_init_db(self):
        """close() 后 _init_lock 实例保留，后续 init_db() 复用同一锁实例。

        场景：close() 持有 _init_lock 引用执行 dispose；若 close() 内
        del_loop_local("cache_init_lock")，后续 init_db() 会获取新锁实例，
        与 close() 持有的旧锁不是同一对象，破坏 web 模式并发保护。
        本测试验证锁实例一致性（非真正并发互斥，互斥由 asyncio.Lock 内部保证）。
        """
        from data.cache.cache_manager import CacheManager

        cm = CacheManager()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        cm.engine = mock_engine
        cm._schema_initialized = True

        lock_held_by_close = cm._init_lock

        await cm.close()

        # close() 后访问 property 应返回同一实例（未被 del_loop_local 清理）
        # 后续 init_db() 调用将复用此锁，与 close() 持有的锁实例一致
        lock_seen_after_close = cm._init_lock
        assert lock_seen_after_close is lock_held_by_close, (
            "close() 后 _init_lock 应保留同一实例；删除后会破坏 web 模式 close → init_db 锁一致性"
        )

    @pytest.mark.asyncio
    async def test_reset_singleton_still_cleans_loop_local_locks(self):
        """_reset_singleton 仍负责清理 loop-local 锁实例（测试隔离不依赖 close()）。"""
        from data.cache.cache_manager import CacheManager

        cm = CacheManager()
        # 触发 loop-local 锁创建并捕获引用
        old_lock = cm._init_lock

        CacheManager._reset_singleton()

        # _reset_singleton 调用 del_loop_local 后，重新访问 _init_lock 应得到新实例
        # （证明测试隔离仍通过 _reset_singleton 生效，无需 close() 介入）
        cm2 = CacheManager()
        new_lock = cm2._init_lock
        assert new_lock is not old_lock, "_reset_singleton 应清理 loop-local 锁；新实例应获取新锁对象"

    @pytest.mark.asyncio
    async def test_close_idempotent_after_lock_preservation(self):
        """close() 不删锁后，重复调用 close() 仍应正确幂等（_disposed 守护）。"""
        from data.cache.cache_manager import CacheManager

        cm = CacheManager()
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        cm.engine = mock_engine
        cm._schema_initialized = True

        await cm.close()
        # 第二次 close() 应早返回（_disposed=True）
        await cm.close()

        assert cm.engine is None
        assert cm._disposed is True
