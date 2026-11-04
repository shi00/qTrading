"""D04-F06 取消路径单元测试：迁移 DDL 线程池取消传播与 schema 标记生命周期。

不依赖真实数据库，覆盖：
- ``DatabaseMigrator._run_alembic_upgrade`` 在 CancelledError 下重新抛出（R2），
  且取消发生在版本校验之前（fake engine 的 ``connect()`` 会显式失败）；
- ``CacheManager.init_db`` 取消时不把 ``_schema_initialized`` 置为 True，
  下次启动仍会重新执行迁移，而不是把未完成/中断的 schema 当成已初始化。

注：``_run_alembic_upgrade`` 的 R2 传播在 ``tests/unit/test_db_migrator.py`` 已有同向用例；
本文件补充「取消不得污染 schema 初始化标记」的关闭语义，二者不冲突。
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
# 本文件使用最小替身对象（不构造完整 CacheManager/AsyncEngine），pyright 无法验证替身兼容性。

import asyncio

import pytest
from sqlalchemy.engine import URL

import data.persistence.db_migrator as db_migrator_module
from data.cache.cache_manager import CacheManager
from data.persistence.db_migrator import DatabaseMigrator


class _CancellingThreadPoolManager:
    """替身线程池：模拟停机时 DDL 线程被取消。"""

    def __init__(self) -> None:
        pass

    async def run_async(self, task_type: object, func: object) -> None:
        raise asyncio.CancelledError


class _ExplodingEngine:
    """只暴露 url；若取消后仍继续版本校验，connect() 会让测试失败。"""

    url = URL.create("postgresql+asyncpg", host="localhost", database="d04_f06_unit")

    def connect(self) -> None:
        raise AssertionError("取消后不得继续执行版本校验")


@pytest.mark.asyncio
async def test_run_alembic_upgrade_reraises_cancelled_error_before_version_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_migrator_module, "ThreadPoolManager", _CancellingThreadPoolManager)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await DatabaseMigrator._run_alembic_upgrade(_ExplodingEngine())

    assert isinstance(exc_info.value, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_cache_manager_keeps_schema_flag_false_when_migration_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _cancel_init(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(DatabaseMigrator, "init_db", _cancel_init)

    cache_manager = CacheManager.__new__(CacheManager)
    cache_manager.engine = object()
    cache_manager._schema_initialized = False

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await cache_manager.init_db(auto_migrate=True)

    assert isinstance(exc_info.value, asyncio.CancelledError)
    assert cache_manager._schema_initialized is False
