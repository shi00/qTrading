"""D11-R02 真实 PostgreSQL 侧证据：任务 retry 与「当前 DB 目标」的绑定关系。

覆盖（与 ``tests/unit/services/test_task_db_target_fingerprint.py`` 互补）：

1. 同一目标：submit → 失败落库 → 重启（init_db 读回历史）→ retry，读写都落在同一库；
2. 切换 DB 目标（两个独立测试库）后 retry 旧历史任务：新任务行只出现在新目标，
   旧目标行数与新任务 id 均无变化；
3. 契约：``task_history`` 无目标指纹列，历史任务本身不携带目标信息
   （生产 schema 未新增列；本测试只读断言，不改 schema）。

本沙箱无 PostgreSQL：fixture 连接失败时 ``pytest.skip``，不静默吞 CI 失败
（CI 提供 PG 时本文件必须真实执行）。
"""

import asyncio
import uuid
from dataclasses import dataclass

import asyncpg
import pytest
import pytest_asyncio
import sqlalchemy as sa

from data.cache.cache_manager import CacheManager
from data.persistence.db_url_override import override_db_url
from services.task_manager import TaskManager, TaskStatus
from tests._helpers import build_db_urls, create_test_engine, get_pg_connection_params

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

# 常量 DDL 模板 + PostgreSQL format(%I) 引用库名，避免源码级 SQL 拼接（R4）。
_DDL_CREATE_DB = "CREATE DATABASE %I"
_DDL_DROP_DB = "DROP DATABASE IF EXISTS %I WITH (FORCE)"
_SQL_COUNT_TASKS = sa.text("SELECT count(*) FROM task_history")
_SQL_TASK_BY_ID = sa.text("SELECT count(*) FROM task_history WHERE id = :task_id")

_FACTORY_KEY = "d11_r02_pg_probe"


async def _execute_quoted_ddl(conn: asyncpg.Connection, template: str, db_name: str) -> None:
    ddl = await conn.fetchval("SELECT format($1::text, $2::text)", template, db_name)
    assert isinstance(ddl, str)
    await conn.execute(ddl)


async def _failing_factory(task_id: str, **kwargs):
    raise RuntimeError(f"probe failure for {task_id}")


async def _wait_terminal(mgr: TaskManager, task_id: str, timeout_s: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        task = mgr._tasks.get(task_id)
        if task is not None and task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        ):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach a terminal status within {timeout_s}s")


@dataclass
class _PgTargets:
    url_a: str
    url_b: str
    db_names: tuple[str, str]


@pytest_asyncio.fixture(loop_scope="function")
async def pg_targets():
    """创建两个真实隔离库（A=旧目标，B=新目标）；无 PG 时 skip。"""
    params = get_pg_connection_params()
    suffix = uuid.uuid4().hex[:8]
    names = (f"d11r02_a_{suffix}", f"d11r02_b_{suffix}")

    try:
        conn = await asyncpg.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database="postgres",
            timeout=5,
        )
    except Exception:
        pytest.skip("需要真实 PostgreSQL（CI 提供）；本沙箱未提供")

    created: list[str] = []
    try:
        for name in names:
            await _execute_quoted_ddl(conn, _DDL_CREATE_DB, name)
            created.append(name)
        _, url_a = build_db_urls(params, names[0])
        _, url_b = build_db_urls(params, names[1])
        yield _PgTargets(url_a=url_a, url_b=url_b, db_names=names)
    finally:
        for name in created:
            try:
                await _execute_quoted_ddl(conn, _DDL_DROP_DB, name)
            except Exception:
                # 清理失败不改变测试结论（CI 的 PG 服务随 job 重建）
                pass
        await conn.close()


async def _count_tasks(engine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(_SQL_COUNT_TASKS)).scalar_one())


async def _task_exists(engine, task_id: str) -> bool:
    async with engine.connect() as conn:
        count = (await conn.execute(_SQL_TASK_BY_ID, {"task_id": task_id})).scalar_one()
    return int(count) > 0


@pytest.mark.asyncio
async def test_retry_history_task_binds_to_current_db_target(pg_targets: _PgTargets):
    """真实 PG：同目标 retry 落同库；切目标后旧任务 retry 只写新库。"""
    probe_engine_a = create_test_engine(pg_targets.url_a)
    cache_a = None
    cache_b = None
    try:
        TaskManager.register_retryable_factory(_FACTORY_KEY, _failing_factory)

        # --- 目标 A：首次提交失败 → 重启（init_db）→ retry 仍在 A ---
        with override_db_url(pg_targets.url_a):
            CacheManager._reset_singleton()
            cache_a = CacheManager()
            await cache_a.init_db(auto_migrate=True)

        TaskManager._reset_singleton()
        manager = TaskManager()
        await manager.init_db()

        original_id = manager.submit_task(
            name="probe",
            task_type="test",
            coroutine_factory=_failing_factory,
            factory_key=_FACTORY_KEY,
            unique_key="d11_r02_pg_unique",
        )
        assert original_id is not None
        await _wait_terminal(manager, original_id)
        await manager.flush_persistence(timeout_s=10.0)

        # 重启：单例重置后从 A 读回历史
        TaskManager._reset_singleton()
        TaskManager.register_retryable_factory(_FACTORY_KEY, _failing_factory)
        manager = TaskManager()
        await manager.init_db()
        restored = next(h for h in manager._history if h.id == original_id)
        assert restored.status == TaskStatus.FAILED

        retry_on_a = manager.retry_task(original_id)
        assert retry_on_a is not None
        await _wait_terminal(manager, retry_on_a)
        await manager.flush_persistence(timeout_s=10.0)

        assert await _task_exists(probe_engine_a, original_id)
        assert await _task_exists(probe_engine_a, retry_on_a)

        # --- 切换到目标 B（不重启 TaskManager，历史任务仍留在内存）---
        await cache_a.close()
        CacheManager._reset_singleton()
        cache_a = None
        with override_db_url(pg_targets.url_b):
            cache_b = CacheManager()
            await cache_b.init_db(auto_migrate=True)

        a_count_before = await _count_tasks(probe_engine_a)
        retry_on_b = manager.retry_task(original_id)
        assert retry_on_b is not None
        await _wait_terminal(manager, retry_on_b)
        await manager.flush_persistence(timeout_s=10.0)

        assert cache_b.engine is not None
        assert await _task_exists(cache_b.engine, retry_on_b)
        assert not await _task_exists(probe_engine_a, retry_on_b)
        assert await _count_tasks(probe_engine_a) == a_count_before
    finally:
        for cache in (cache_a, cache_b):
            if cache is not None:
                try:
                    await cache.close()
                except Exception:
                    pass
        TaskManager._reset_singleton()
        TaskManager._RETRYABLE_FACTORIES.pop(_FACTORY_KEY, None)
        CacheManager._reset_singleton()
        await probe_engine_a.dispose()


@pytest.mark.asyncio
async def test_task_history_schema_has_no_db_target_fingerprint(pg_targets: _PgTargets):
    """真实 PG schema 契约：task_history 不含 DB 目标/指纹列（未新增 schema）。"""
    with override_db_url(pg_targets.url_a):
        CacheManager._reset_singleton()
        cache = CacheManager()
        await cache.init_db(auto_migrate=True)
    try:
        engine = cache.engine
        assert engine is not None
        sync_engine = engine.sync_engine
        columns = await asyncio.to_thread(lambda: sa.inspect(sync_engine).get_columns("task_history"))
        names = {col["name"].lower() for col in columns}
        forbidden = {"db_url", "database_url", "dsn", "db_target", "target_fingerprint", "engine_generation"}
        assert names & forbidden == set()
        # 仅固化既有列集合的关键字段存在性，不锁定完整列清单
        assert {"id", "status", "factory_key", "retry_kwargs"}.issubset(names)
    finally:
        try:
            await cache.close()
        except Exception:
            pass
        CacheManager._reset_singleton()
