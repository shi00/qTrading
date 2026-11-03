"""任务持久化契约的真实 PostgreSQL 证据（D11-T02）。

单测（``tests/unit/test_task_manager.py``，T01）以 mock/替身断言 SQL 形态与内存交错；
本文件在真实 asyncpg/SQLAlchemy 引擎上验证**数据库侧**可观察的行为契约：

1. ``persist_seq`` 乱序守卫：同一 task_id 的高序号终态先落库，低序号 RUNNING 后到，
   ``ON CONFLICT ... WHERE old.persist_seq < EXCLUDED.persist_seq`` 丢弃旧快照；
2. 清理 DELETE 与迟到 INSERT 交错：墓碑/在途写屏障保证 DELETE 后旧快照不得复活；
   同时记录「DELETE 之后 persist_seq 守卫不再保护新 INSERT」的 DB 事实，避免把
   seq 误当全局保护（生产正确性依赖墓碑 + 内存撤销判定）；
3. ``flush_persistence`` 在真实引擎上等待排队/在途写排空；
4. 删除失败（引擎不可用）时 ``_pending_db_deletions`` 保留，恢复后重试成功。

隔离策略：复用 ``tests/integration/conftest.py`` 的 ``test_engine`` 与
``db_schema_ready``（建表 + xdist 按 worker 分库），本文件仅在其上初始化
``CacheManager`` 与 ``TaskManager``，并按 R7 在 teardown 关闭引擎、重置单例。

环境现实：本沙箱无 PostgreSQL。模块级 ``_require_real_postgres`` 先探测连接，
不可达时明确 skip（CI 的 PG service 可达则正常执行）；不静默跳过、不吞 CI 的
鉴权/配置错误（非「不可达」异常直接 raise）。
"""

# pyright: reportPrivateUsage=false
# 本文件按 D11-T02 要求调用 TaskManager 内部持久化/屏障入口（_persist_snapshot /
# _clear_finished_db / _begin_inflight_persist 等），其行为由真实 PG 查询结果验证。

from __future__ import annotations

import asyncio
import datetime
import logging
import time

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text

from data.cache.cache_manager import CacheManager
from data.persistence import engine_provider
from data.persistence.db_url_override import override_db_url
from data.persistence.models import TaskHistory
from services.task_manager import AppTask, TaskManager, TaskStatus
from tests.integration._db_config import (
    TEST_DB_HOST,
    TEST_DB_PASSWORD,
    TEST_DB_PORT,
    TEST_DB_URL,
    TEST_DB_USER,
)

pytestmark = pytest.mark.integration


def _utcnow_naive() -> datetime.datetime:
    """``task_history`` 时间列为 naive UTC（与生产 to_utc_for_db 口径一致）。"""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _snapshot(task_id: str, status: TaskStatus, seq: int, *, progress: float = 0.0) -> tuple:
    """构造与 ``TaskManager._persist_task`` 同形的 15 元快照（末位为 persist_seq）。"""
    return (
        task_id,
        "itest-task",
        "System",
        status.value,
        progress,
        "",
        "",
        None,
        _utcnow_naive(),
        None,
        None,
        None,
        None,
        None,
        seq,
    )


async def _fetch_row(engine, task_id: str):
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT status, persist_seq, progress FROM task_history WHERE id = :tid"),
                {"tid": task_id},
            )
        ).fetchone()


async def _count_rows(engine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM task_history"))).scalar_one())


async def _await_condition(predicate, *, timeout: float = 3.0, interval: float = 0.01) -> None:
    """轮询同步条件，超时抛 AssertionError（避免固定 sleep 造成的慢/ flaky）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("等待条件超时")


@pytest.fixture(scope="module", autouse=True)
def _require_real_postgres():
    """真实 PG 可达性探测：不可达则明确 skip；鉴权/配置错误直接 raise，不掩盖 CI 失败。"""
    loop = asyncio.new_event_loop()
    try:
        conn = loop.run_until_complete(
            asyncpg.connect(
                host=TEST_DB_HOST,
                port=TEST_DB_PORT,
                user=TEST_DB_USER,
                password=TEST_DB_PASSWORD,
                database="postgres",
                timeout=3,
            )
        )
        loop.run_until_complete(conn.close())
    except (OSError, TimeoutError, asyncpg.CannotConnectNowError) as exc:
        pytest.skip(f"需要真实 PostgreSQL（CI 提供）；本沙箱未提供（{exc!r}）")
    finally:
        loop.close()


@pytest_asyncio.fixture
async def persistence_env(test_engine):
    """初始化指向 TEST_DB 的 CacheManager/TaskManager 并清理 task_history（R7 隔离）。"""
    CacheManager._reset_singleton()
    TaskManager._reset_singleton()

    with override_db_url(TEST_DB_URL):
        cache = CacheManager()
        await cache.init_db(auto_migrate=True)
        assert cache.engine is not None

        async with cache.engine.begin() as conn:
            await conn.execute(TaskHistory.__table__.delete())

        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        mgr._tasks = {}

        try:
            yield mgr, cache
        finally:
            try:
                await mgr.flush_persistence(timeout_s=5.0)
            except TimeoutError as exc:
                # 仅测试清理: 用例失败时可能有未释放的排队写，避免掩盖原始失败
                logging.getLogger(__name__).warning("[persistence_env] flush timeout: %s", exc)
            assert cache.engine is not None
            async with cache.engine.begin() as conn:
                await conn.execute(TaskHistory.__table__.delete())
            await cache.close()

    CacheManager._reset_singleton()
    TaskManager._reset_singleton()


class TestPersistSeqOutOfOrder:
    """乱序快照在真实 DB 上的守卫语义。"""

    @pytest.mark.asyncio
    async def test_high_seq_terminal_wins_over_late_low_seq_running(self, persistence_env):
        """高 seq 终态先到、低 seq RUNNING 后到 → DB 行保持终态且 seq 不回退。"""
        mgr, cache = persistence_env
        task_id = "pg-seq-out-of-order"

        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.COMPLETED, 10, progress=1.0))
        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.RUNNING, 3, progress=0.5))

        row = await _fetch_row(cache.engine, task_id)
        assert row is not None, "终态快照必须落库"
        assert row[0] == TaskStatus.COMPLETED.value, "低序号的迟到 RUNNING 不得覆盖终态"
        assert int(row[1]) == 10, "persist_seq 不得回退（DELETE 之前由 WHERE 守卫拒绝旧快照）"
        assert float(row[2]) == 1.0, "progress 也应保持终态快照值"

    @pytest.mark.asyncio
    async def test_equal_seq_is_not_applied(self, persistence_env):
        """纵深语义：同 seq 不满足 ``<``，不推进（重复序号不会重放写）。"""
        mgr, cache = persistence_env
        task_id = "pg-seq-equal"

        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.COMPLETED, 7, progress=1.0))
        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.RUNNING, 7, progress=0.2))

        row = await _fetch_row(cache.engine, task_id)
        assert row is not None
        assert row[0] == TaskStatus.COMPLETED.value
        assert float(row[2]) == 1.0


class TestDeletionBarrierOnRealEngine:
    """清理墓碑/在途写屏障与迟到 INSERT 的真实 DB 交错。"""

    @pytest.mark.asyncio
    async def test_inflight_write_drains_before_delete_then_late_insert_blocked(self, persistence_env, monkeypatch):
        """在途写未归零前 DELETE 不提交；DELETE 后墓碑拒绝迟到快照（零复活）。"""
        mgr, cache = persistence_env
        task_id = "pg-barrier-tombstone"

        # 先落一条终态行（模拟清理前的历史记录）。
        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.COMPLETED, 5, progress=1.0))
        assert await _fetch_row(cache.engine, task_id) is not None

        # 闸门：让一个真实快照停在「已过写库准入、尚未提交」的在途阶段。
        release_write = asyncio.Event()
        write_entered = asyncio.Event()
        original_write = cache.write_db

        async def _gated_write(sql, params=None, *, suppress_errors=False):
            write_entered.set()
            await release_write.wait()
            return await original_write(sql, params, suppress_errors=suppress_errors)

        monkeypatch.setattr(cache, "write_db", _gated_write)

        inflight = asyncio.create_task(mgr._persist_snapshot(_snapshot(task_id, TaskStatus.RUNNING, 6)))
        await asyncio.wait_for(write_entered.wait(), timeout=3.0)
        assert mgr._inflight_persist.get(task_id) == 1, "在途计数应已登记"

        delete_task = asyncio.create_task(mgr._clear_finished_db([task_id]))
        await _await_condition(lambda: task_id in mgr._pending_db_deletions)
        await asyncio.sleep(0.05)
        # 在途写未结束时 DELETE 必须等待（行仍在，删除未完成）。
        assert not delete_task.done(), "在途写未排空前 DELETE 不得提交"
        assert await _fetch_row(cache.engine, task_id) is not None

        release_write.set()
        await asyncio.wait_for(inflight, timeout=5.0)
        assert await asyncio.wait_for(delete_task, timeout=5.0) is True
        assert task_id not in mgr._pending_db_deletions, "DELETE 提交后应移出待重试集合"
        assert await _fetch_row(cache.engine, task_id) is None, "DELETE 后真实行必须消失"

        # 墓碑仍在：迟到快照（更低 seq）不得 INSERT 复活。
        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.CANCELLED, 2))
        assert await _fetch_row(cache.engine, task_id) is None, "墓碑存在时迟到快照必须被拒绝"

    @pytest.mark.asyncio
    async def test_after_delete_older_seq_insert_is_not_blocked(self, persistence_env, monkeypatch):
        """记录 DB 事实：DELETE 之后 persist_seq 守卫不保护新 INSERT（非全局保护）。

        生产正确性依赖「墓碑 + 内存撤销判定」而非 seq 全局保护：行被 DELETE 后
        ``ON CONFLICT`` 无冲突可比较，任何 INSERT（哪怕 seq 更小）都会重建行。
        本用例在真实 PG 上显式记录该事实，避免后续把 seq 当作全局保护。
        """
        mgr, cache = persistence_env
        task_id = "pg-seq-not-global"

        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.COMPLETED, 10, progress=1.0))
        assert await mgr._clear_finished_db([task_id]) is True
        assert await _fetch_row(cache.engine, task_id) is None

        # 模拟墓碑被容量淘汰（生产兜底还依赖 _is_task_tracked_in_memory；此处直击 DB 语义）。
        mgr._tombstones.pop(task_id, None)
        mgr._pending_db_deletions.discard(task_id)

        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.RUNNING, 1, progress=0.1))
        row = await _fetch_row(cache.engine, task_id)
        assert row is not None, "事实记录：DELETE 后低 seq INSERT 会被接受（无旧行可比较）"
        assert row[0] == TaskStatus.RUNNING.value
        assert int(row[1]) == 1


class TestFlushPersistenceOnRealEngine:
    """``flush_persistence`` 等待真实 asyncpg 写在途排空。"""

    @pytest.mark.asyncio
    async def test_flush_waits_until_queued_writes_commit(self, persistence_env):
        """调用 flush 前存在排队写；flush 返回后所有行已在真实 DB 可见且计数归零。"""
        mgr, cache = persistence_env
        task_ids = [f"pg-flush-{i}" for i in range(4)]

        for task_id in task_ids:
            task = AppTask(name=task_id, status=TaskStatus.RUNNING, progress=0.4)
            task.id = task_id
            mgr._tasks[task_id] = task
            mgr._persist_task(task)

        assert mgr._persist_pending_count == len(task_ids), "写入尚未执行时应存在排队计数"

        await mgr.flush_persistence(timeout_s=5.0)

        assert mgr._persist_pending_count == 0, "flush 应等待所有排队写完成"
        assert not mgr._queued_persist, "按 ID 的排队计数应全部释放"
        assert not mgr._inflight_persist, "在途计数应全部归零"
        assert await _count_rows(cache.engine) == len(task_ids), "flush 返回后所有行必须已真实落库"

        rows = [await _fetch_row(cache.engine, task_id) for task_id in task_ids]
        assert all(row is not None for row in rows)
        assert all(row[0] == TaskStatus.RUNNING.value for row in rows)
        assert all(float(row[2]) == 0.4 for row in rows)


class TestDeleteFailureRetention:
    """删除失败/引擎不可用时 pending 保留，恢复后重试成功。"""

    @pytest.mark.asyncio
    async def test_unavailable_engine_keeps_pending_then_retry_deletes(self, persistence_env, monkeypatch):
        """引擎不可用时返回 False 且行保留；引擎恢复后同一 ID 重试删除成功。"""
        mgr, cache = persistence_env
        task_id = "pg-retry-delete"

        await mgr._persist_snapshot(_snapshot(task_id, TaskStatus.COMPLETED, 9, progress=1.0))
        assert await _fetch_row(cache.engine, task_id) is not None

        with monkeypatch.context() as patch_ctx:
            patch_ctx.setattr(engine_provider, "is_disposed", lambda _engine: True)
            assert await mgr._clear_finished_db([task_id]) is False, "引擎不可用不得报告持久清理成功"

        assert task_id in mgr._pending_db_deletions, "失败必须保留待重试 ID"
        assert task_id in mgr._tombstones, "墓碑保留以拒绝迟到写"
        assert await _fetch_row(cache.engine, task_id) is not None, "失败时真实行不得被删除"

        assert await mgr._clear_finished_db([task_id]) is True, "引擎恢复后重试应成功"
        assert task_id not in mgr._pending_db_deletions
        assert await _fetch_row(cache.engine, task_id) is None
