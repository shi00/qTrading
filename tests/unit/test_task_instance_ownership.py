"""D05-N02：第二实例不得把首实例仍在跑的任务回填为 INTERRUPTED。

契约（本文件固化）：

1. ``InstanceOwnershipLock`` 用 PG 会话级 advisory lock 表达「业务库唯一存活执行者」；
   取不到即返回 ``False``，且不留下连接/引擎（不占用池、不残留锁）。
2. ``TaskManager.init_db`` 在任何业务写库（stale 回填 UPDATE、30 天 DELETE）之前取锁；
   取不到即抛 ``InstanceOwnershipConflictError``，**不执行任何写库**，也不反序列化历史，
   首实例任务行/状态/实际执行均不受影响。
3. 取到锁即保持到单例重置/进程退出（会话锁随连接关闭释放），故同进程重启仍能接管。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import sqlalchemy as sa

from data.persistence.instance_lock import (
    TASK_OWNERSHIP_LOCK_KEY,
    InstanceOwnershipConflictError,
    InstanceOwnershipLock,
)
from services import task_manager as task_manager_module
from services.task_manager import TaskManager, TaskStatus, _resolve_business_db_url

pytestmark = pytest.mark.unit

_DB_URL = "postgresql+asyncpg://probe:pw@localhost:5432/probe_db"


class _FakeThreadPool:
    cpu_pool_max_workers = 8
    io_pool_max_workers = 4


@pytest.fixture(autouse=True)
def _mock_thread_pool(monkeypatch):
    monkeypatch.setattr("services.task_manager.ThreadPoolManager", _FakeThreadPool)


class _FakeSyncConnection:
    def __init__(self, *, invalidate_exc: Exception | None = None) -> None:
        self.invalidated = False
        self.invalidate_exc = invalidate_exc

    def invalidate(self) -> None:
        self.invalidated = True
        if self.invalidate_exc is not None:
            raise self.invalidate_exc


class _FakeSyncEngine:
    def __init__(self, *, dispose_exc: Exception | None = None) -> None:
        self.disposed: bool | None = None
        self.dispose_exc = dispose_exc

    def dispose(self, close: bool = True) -> None:
        self.disposed = close
        if self.dispose_exc is not None:
            raise self.dispose_exc


class _FakeResult:
    def __init__(self, value: bool) -> None:
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _FakeConnection:
    def __init__(self, *, granted: bool, exc: BaseException | None = None, close_exc: Exception | None = None) -> None:
        self.granted = granted
        self.exc = exc
        self.close_exc = close_exc
        self.ping_exc: BaseException | None = None
        self.closed = False
        self.sync_connection = _FakeSyncConnection()

    async def execute(self, _statement, _params=None) -> _FakeResult:
        if self.ping_exc is not None:
            raise self.ping_exc
        if self.exc is not None:
            raise self.exc
        return _FakeResult(self.granted)

    async def close(self) -> None:
        self.closed = True
        if self.close_exc is not None:
            raise self.close_exc


class _FakeEngine:
    def __init__(
        self,
        connection: _FakeConnection | None = None,
        *,
        connect_exc: BaseException | None = None,
        dispose_exc: Exception | None = None,
    ) -> None:
        self.connection = connection
        self.connect_exc = connect_exc
        self.dispose_exc = dispose_exc
        self.disposed = False
        self.sync_engine = _FakeSyncEngine()

    async def connect(self) -> _FakeConnection:
        if self.connect_exc is not None:
            raise self.connect_exc
        assert self.connection is not None
        return self.connection

    async def dispose(self) -> None:
        self.disposed = True
        if self.dispose_exc is not None:
            raise self.dispose_exc


def _install_engine(monkeypatch, engine: _FakeEngine) -> list[dict]:
    """替换锁模块的引擎工厂，并记录建连参数（证明独立单连接预算）。"""

    created: list[dict] = []

    def _factory(_url, **kwargs):
        created.append(kwargs)
        return engine

    monkeypatch.setattr("data.persistence.instance_lock.create_async_engine", _factory)
    return created


class TestInstanceOwnershipLock:
    @pytest.mark.asyncio
    async def test_acquire_granted_holds_single_connection_and_is_idempotent(self, monkeypatch):
        connection = _FakeConnection(granted=True)
        engine = _FakeEngine(connection)
        created = _install_engine(monkeypatch, engine)

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is True
        assert await lock.acquire() is True  # 已持有：不重复建连
        assert len(created) == 1
        assert created[0]["pool_size"] == 1  # 独立单连接，不占业务池
        assert created[0]["max_overflow"] == 0
        assert connection.closed is False
        assert TASK_OWNERSHIP_LOCK_KEY.bit_length() <= 63

    @pytest.mark.asyncio
    async def test_acquire_denied_releases_everything(self, monkeypatch):
        connection = _FakeConnection(granted=False)
        engine = _FakeEngine(connection)
        _install_engine(monkeypatch, engine)

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is False
        assert connection.closed is True
        assert engine.disposed is True

    @pytest.mark.asyncio
    async def test_connect_failure_disposes_engine_and_propagates(self, monkeypatch):
        engine = _FakeEngine(connect_exc=OSError("pg down"))
        _install_engine(monkeypatch, engine)

        lock = InstanceOwnershipLock(_DB_URL)
        with pytest.raises(OSError, match="pg down"):
            await lock.acquire()
        assert engine.disposed is True

    @pytest.mark.asyncio
    async def test_execute_cancellation_closes_connection_and_propagates(self, monkeypatch):
        cancelled = asyncio.CancelledError()
        connection = _FakeConnection(granted=True, exc=cancelled)
        engine = _FakeEngine(connection)
        _install_engine(monkeypatch, engine)

        lock = InstanceOwnershipLock(_DB_URL)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await lock.acquire()
        assert exc_info.value is cancelled
        assert connection.closed is True
        assert engine.disposed is True

    @pytest.mark.asyncio
    async def test_release_errors_do_not_mask_denied_outcome(self, monkeypatch):
        connection = _FakeConnection(granted=False, close_exc=OSError("close failed"))
        engine = _FakeEngine(connection, dispose_exc=OSError("dispose failed"))
        _install_engine(monkeypatch, engine)

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is False

    @pytest.mark.asyncio
    async def test_force_release_invalidates_connection_and_disposes_engine(self, monkeypatch):
        connection = _FakeConnection(granted=True)
        engine = _FakeEngine(connection)
        _install_engine(monkeypatch, engine)

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is True
        lock.force_release()
        assert connection.sync_connection.invalidated is True
        assert engine.sync_engine.disposed is True
        # 已释放后可重新建连（同进程重启/重试接管）
        assert await lock.acquire() is True

    def test_force_release_without_connection_is_noop(self):
        InstanceOwnershipLock(_DB_URL).force_release()  # 未持有时不得抛错

    @pytest.mark.asyncio
    async def test_force_release_swallows_release_errors(self, monkeypatch):
        connection = _FakeConnection(granted=True)
        engine = _FakeEngine(connection)
        _install_engine(monkeypatch, engine)
        connection.sync_connection.invalidate_exc = OSError("invalidate failed")
        engine.sync_engine.dispose_exc = OSError("dispose failed")

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is True
        lock.force_release()  # 释放失败不得向调用方抛错（进程退出仍由 PG 回收会话锁）

    @pytest.mark.asyncio
    async def test_stale_held_connection_is_not_trusted(self, monkeypatch):
        stale = _FakeConnection(granted=True)
        engines = [_FakeEngine(stale), _FakeEngine(_FakeConnection(granted=True))]
        monkeypatch.setattr("data.persistence.instance_lock.create_async_engine", lambda *_a, **_k: engines.pop(0))

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is True
        stale.ping_exc = RuntimeError("connection lost")
        # 持锁连接失活 → 不得冒充持有：先释放旧连接，再重新取证
        assert await lock.acquire() is True
        assert stale.sync_connection.invalidated is True
        assert engines == []

    @pytest.mark.asyncio
    async def test_liveness_cancellation_propagates(self, monkeypatch):
        connection = _FakeConnection(granted=True)
        _install_engine(monkeypatch, _FakeEngine(connection))

        lock = InstanceOwnershipLock(_DB_URL)
        assert await lock.acquire() is True
        cancelled = asyncio.CancelledError()
        connection.ping_exc = cancelled
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await lock.acquire()
        assert exc_info.value is cancelled


class _StubLock:
    def __init__(self, *, granted: bool) -> None:
        self.granted = granted
        self.released = 0

    async def acquire(self) -> bool:
        return self.granted

    def force_release(self) -> None:
        self.released += 1


def _fake_cache() -> MagicMock:
    cache = MagicMock()
    cache.write_db = AsyncMock()
    cache.read_db = AsyncMock(return_value=pd.DataFrame())
    return cache


async def _init_with_cache(mgr: TaskManager, cache: MagicMock, **kwargs) -> None:
    with patch("data.cache.cache_manager.CacheManager", return_value=cache):
        await mgr.init_db(**kwargs)


class TestTaskManagerOwnershipGate:
    @pytest.mark.asyncio
    async def test_second_instance_stops_before_any_business_write(self):
        cache = _fake_cache()
        mgr = TaskManager()

        with pytest.raises(InstanceOwnershipConflictError, match="另一个应用实例正在使用同一数据库"):
            await _init_with_cache(mgr, cache, ownership_lock=_StubLock(granted=False))

        # 第二实例：任何业务写库（stale 回填 / 30 天清理）都不得发生，历史也不反序列化
        cache.write_db.assert_not_called()
        cache.read_db.assert_not_called()
        assert mgr._db_ready is False

    @pytest.mark.asyncio
    async def test_sole_owner_still_backfills_stale_tasks(self):
        cache = _fake_cache()
        mgr = TaskManager()

        await _init_with_cache(mgr, cache, ownership_lock=_StubLock(granted=True))

        backfill_sql, backfill_params = cache.write_db.call_args_list[0].args
        assert "status IN ('RUNNING', 'QUEUED')" in backfill_sql
        assert backfill_params[0] == TaskStatus.INTERRUPTED.value
        assert mgr._db_ready is True

    @pytest.mark.asyncio
    async def test_default_path_builds_lock_from_engine_url(self, monkeypatch):
        cache = _fake_cache()
        cache.engine = MagicMock()
        cache.engine.url = sa.make_url(_DB_URL)
        seen: dict[str, str] = {}

        class _SpyLock(InstanceOwnershipLock):
            def __init__(self, database_url: str) -> None:
                seen["url"] = database_url
                super().__init__(database_url)

            async def acquire(self) -> bool:
                return True

        monkeypatch.setattr("data.persistence.instance_lock.InstanceOwnershipLock", _SpyLock)
        mgr = TaskManager()
        try:
            await _init_with_cache(mgr, cache)
            assert seen["url"] == _DB_URL
            assert isinstance(mgr._ownership_lock, _SpyLock)
        finally:
            TaskManager._reset_singleton()

    @pytest.mark.asyncio
    async def test_default_path_conflict_stops_without_write(self, monkeypatch):
        cache = _fake_cache()
        cache.engine = MagicMock()
        cache.engine.url = sa.make_url(_DB_URL)

        class _RefusingLock(InstanceOwnershipLock):
            def __init__(self, database_url: str) -> None:
                super().__init__(database_url)

            async def acquire(self) -> bool:
                return False

        monkeypatch.setattr("data.persistence.instance_lock.InstanceOwnershipLock", _RefusingLock)
        mgr = TaskManager()
        with pytest.raises(InstanceOwnershipConflictError, match="另一个应用实例正在使用同一数据库"):
            await _init_with_cache(mgr, cache)
        cache.write_db.assert_not_called()
        assert mgr._ownership_lock is None

    @pytest.mark.asyncio
    async def test_unresolvable_engine_keeps_legacy_backfill(self):
        cache = _fake_cache()  # MagicMock engine：非 SQLAlchemy 引擎（测试替身）
        assert _resolve_business_db_url(cache) is None

        mgr = TaskManager()
        await _init_with_cache(mgr, cache)
        assert cache.write_db.call_args_list[0].args[0].startswith("UPDATE task_history SET status = $1")

    def test_reset_singleton_releases_ownership_lock(self):
        mgr = TaskManager()
        stub = _StubLock(granted=True)
        mgr._ownership_lock = stub
        TaskManager._reset_singleton()
        assert stub.released == 1

    def test_atexit_cleanup_releases_ownership_lock(self):
        mgr = TaskManager()
        stub = _StubLock(granted=True)
        mgr._ownership_lock = stub
        with patch.object(task_manager_module, "is_graceful_shutdown_completed", return_value=False):
            TaskManager._atexit_cleanup()
        assert stub.released == 1
        TaskManager._reset_singleton()
