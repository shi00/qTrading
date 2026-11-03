# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/动态注入（伪造 CacheManager、替换事件循环实例方法、直接观测私有计数），
# pyright 无法验证替身与生产类型的兼容性，故局部关闭属性访问告警；行为由用例断言验证。

"""D11-R01 动态故障注入：取消传播 / 多 loop 重绑定 / 停机 / 快照失败 / app_state 读取失败 / 在途 DELETE。

所有并发交错由 ``asyncio.Event`` 或 ``threading.Event`` 精确控制，不依赖随机 sleep；
每个用例的等待均有显式上限（< 1s），且测试结束时回收线程/事件循环/后台任务，避免 R7 跨用例污染。
"""

import asyncio
import contextlib
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.task_manager import AppTask, TaskManager, TaskStatus
from utils.scheduler_service import SchedulerService

pytestmark = pytest.mark.unit


class _FakeWriteDbCache:
    """最小 CacheManager 替身：记录 task_history 写入，可注入下一笔写入的失败形态。"""

    engine = None

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.fail_mode: str | None = None

    async def write_db(self, sql, params, **_kwargs):
        if self.fail_mode == "cancel":
            self.fail_mode = None
            raise asyncio.CancelledError()
        if self.fail_mode == "error":
            self.fail_mode = None
            raise RuntimeError("injected db write failure")
        self.rows[params[0]] = params[3]

    async def read_db(self, _sql, *_args, **_kwargs):
        return None


def _snapshot(task_id: str, status: str = "CANCELLED") -> tuple:
    return (task_id, "n", "t", status, 0.5, "", "", None, None, None, None, None, None, None, 1)


class _LoopFakeCache:
    """init_db 专用替身：不触达真实数据库，写入/读取均为空操作。"""

    _instance = None

    async def write_db(self, _sql, _params=None, **_kwargs):
        return None

    async def read_db(self, _sql, *_args, **_kwargs):
        return None


class _LoopFakeCacheManager:
    _instance = _LoopFakeCache()

    def __new__(cls):
        return cls._instance


def _close_loop(loop: asyncio.AbstractEventLoop) -> None:
    """取消并回收 loop 上所有未完成任务后关闭，避免 "Task was destroyed but it is pending" 告警。"""
    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


class TestCancellationPropagation:
    """R2: runner 内用户协程在 await 点被取消，CancelledError 向上传播且 reservation 归零。"""

    @pytest.mark.asyncio
    async def test_cancel_at_await_point_propagates_and_releases_reservations(self):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        cache = _FakeWriteDbCache()
        started = asyncio.Event()
        unwound = asyncio.Event()

        async def _work(task_id: str):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                unwound.set()

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = cache
            tid = mgr.submit_task("cancel-at-await", "test", _work, cancellable=True, unique_key="cancel-key")
            assert tid is not None
            await asyncio.wait_for(started.wait(), timeout=1.0)
            task = mgr.get_task(tid)
            assert task is not None
            assert task.status == TaskStatus.RUNNING
            runner = task._asyncio_task
            assert runner is not None

            mgr.cancel_task(tid)  # 真实入口：call_soon_threadsafe → _cancel_task_impl
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await asyncio.wait_for(runner, timeout=1.0)
            assert isinstance(exc_info.value, asyncio.CancelledError)

            assert unwound.is_set()
            assert task.status == TaskStatus.CANCELLED
            assert task.completed_at is not None
            assert task._asyncio_task is None
            assert "cancel-key" not in mgr._active_keys

            await mgr.flush_persistence(timeout_s=0.5)
            assert mgr._persist_pending_count == 0
            assert mgr._queued_persist == {}
            assert mgr._inflight_persist == {}
            assert mgr._active_producers == set()


class TestMultiLoopRebind:
    """D02-F05 关联：第二个事件循环 init_db 重新绑定后，旧 loop 不再被提交/调度使用。"""

    def test_second_init_db_rebinds_loop_and_old_loop_is_unused(self):
        mgr = TaskManager()
        holder1: dict = {}
        done1 = threading.Event()

        def _init_once() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holder1["loop"] = loop
            try:
                loop.run_until_complete(mgr.init_db())
                origin = loop.call_soon_threadsafe
                calls: list = []

                def _spy(callback, *args):
                    calls.append(callback)
                    return origin(callback, *args)

                loop.call_soon_threadsafe = _spy
                holder1["calls"] = calls
            finally:
                _close_loop(loop)
                asyncio.set_event_loop(None)
                done1.set()

        holder2: dict = {}
        inited2 = threading.Event()

        def _init_and_serve() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            holder2["loop"] = loop
            holder2["release"] = asyncio.Event()
            try:
                loop.run_until_complete(mgr.init_db())
                inited2.set()
                loop.run_forever()
            finally:
                _close_loop(loop)
                asyncio.set_event_loop(None)

        with patch("data.cache.cache_manager.CacheManager", _LoopFakeCacheManager):
            thread1 = threading.Thread(target=_init_once, daemon=True)
            thread1.start()
            assert done1.wait(5.0)
            thread1.join(5.0)
            assert not thread1.is_alive()
            loop1 = holder1["loop"]
            assert loop1.is_closed()
            assert mgr._loop is loop1

            thread2 = threading.Thread(target=_init_and_serve, daemon=True)
            thread2.start()
            try:
                assert inited2.wait(5.0)
                loop2 = holder2["loop"]
                assert mgr._loop is loop2
                assert loop2.is_running()

                started = threading.Event()
                completed = threading.Event()

                async def _work(task_id: str):
                    started.set()
                    await holder2["release"].wait()
                    return "ok"

                tid = mgr.submit_task("rebound", "test", _work, cancellable=True)
                assert tid is not None
                assert started.wait(1.0)
                task = mgr.get_task(tid)
                assert task is not None
                runner = task._asyncio_task
                assert runner is not None

                def _attach_done_callback() -> None:
                    runner.add_done_callback(lambda _t: completed.set())

                loop2.call_soon_threadsafe(_attach_done_callback)
                loop2.call_soon_threadsafe(holder2["release"].set)
                assert completed.wait(1.0)
                assert task.status == TaskStatus.COMPLETED
                assert holder1["calls"] == []  # 旧 loop 未收到任何新调度

                dispatched = threading.Event()

                async def _persist_ok(_snapshot_params) -> None:
                    dispatched.set()

                with patch.object(mgr, "_persist_snapshot", _persist_ok):
                    mgr._queue_persist_snapshot(_snapshot("dispatch-id", "QUEUED"))
                    assert mgr._loop.is_running()
                    assert dispatched.wait(1.0)
                    assert holder1["calls"] == []
            finally:
                with contextlib.suppress(RuntimeError):
                    holder2["loop"].call_soon_threadsafe(holder2["loop"].stop)
                thread2.join(5.0)
                assert not thread2.is_alive()

        # loop2 已关闭：旧 loop 不会被静默复用，提交直接拒绝（返回 None）
        assert mgr._loop is holder2["loop"]
        assert mgr._persist_pending_count == 0  # loop 关闭时未完成的 reservation 全部释放
        assert mgr.submit_task("after-close", "test", _work) is None


class TestShutdownBounded:
    """停机：先取消再持久化；有限等待；超时告警；外层取消不被吞。"""

    @pytest.mark.asyncio
    async def test_cancel_then_persist_is_bounded_and_flush_returns(self):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        cache = _FakeWriteDbCache()
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def _work(task_id: str):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = cache
            tid = mgr.submit_task("shutdown", "test", _work, cancellable=True)
            assert tid is not None
            await asyncio.wait_for(started.wait(), timeout=1.0)

            start = asyncio.get_running_loop().time()
            await mgr.cancel_all_running_async(join_timeout=0.5, persist_timeout=0.5)
            assert asyncio.get_running_loop().time() - start < 1.0

            task = mgr.get_task(tid)
            assert task is not None
            assert task.status == TaskStatus.CANCELLED
            assert cleaned.is_set()
            assert task._asyncio_task is None

            await mgr.flush_persistence(timeout_s=0.5)
            assert mgr._persist_pending_count == 0
            assert mgr._queued_persist == {}
            assert cache.rows.get(tid) == "CANCELLED"

    @pytest.mark.asyncio
    async def test_join_timeout_warns_without_raising(self, caplog):
        mgr = TaskManager()
        mgr._loop = asyncio.get_running_loop()
        started = asyncio.Event()
        hold = asyncio.Event()
        cleanup_entered = asyncio.Event()

        async def _stubborn(task_id: str):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                current = asyncio.current_task()
                assert current is not None
                current.uncancel()
                cleanup_entered.set()
                await hold.wait()
                raise

        tid = mgr.submit_task("stubborn", "test", _stubborn, cancellable=True)
        assert tid is not None
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task = mgr.get_task(tid)
        assert task is not None
        assert task.id == tid
        runner = task._asyncio_task
        assert isinstance(runner, asyncio.Task)

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            caplog.at_level(logging.WARNING, logger="services.task_manager"),
        ):
            mock_cm._instance = _FakeWriteDbCache()
            shutdown = asyncio.create_task(mgr.cancel_all_running_async(join_timeout=0.05, persist_timeout=0.5))
            await asyncio.wait_for(cleanup_entered.wait(), timeout=1.0)
            await asyncio.wait_for(shutdown, timeout=1.0)
        assert any("did not exit" in record.message for record in caplog.records)

        hold.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(runner, timeout=1.0)
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_persist_timeout_warns_and_flush_stays_bounded(self, caplog):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        started = asyncio.Event()
        hang = asyncio.Event()

        async def _work(task_id: str):
            started.set()
            await asyncio.Event().wait()

        async def _hanging_persist(_task) -> None:
            await hang.wait()

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = _FakeWriteDbCache()
            tid = mgr.submit_task("persist-timeout", "test", _work, cancellable=True)
            assert tid is not None
            await asyncio.wait_for(started.wait(), timeout=1.0)

            with (
                patch.object(mgr, "_persist_task_async", side_effect=_hanging_persist),
                caplog.at_level(logging.WARNING, logger="services.task_manager"),
            ):
                start = asyncio.get_running_loop().time()
                await mgr.cancel_all_running_async(join_timeout=0.5, persist_timeout=0.05)
                assert asyncio.get_running_loop().time() - start < 1.0
            assert any("persistence timed out" in record.message for record in caplog.records)

            await mgr.flush_persistence(timeout_s=0.5)
            assert mgr._persist_pending_count == 0
            hang.set()

    @pytest.mark.asyncio
    async def test_outer_cancellation_not_swallowed(self):
        mgr = TaskManager()
        mgr._loop = asyncio.get_running_loop()
        started = asyncio.Event()
        hold = asyncio.Event()
        cleanup_entered = asyncio.Event()

        async def _stubborn(task_id: str):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                current = asyncio.current_task()
                assert current is not None
                current.uncancel()
                cleanup_entered.set()
                await hold.wait()
                raise

        tid = mgr.submit_task("outer-cancel", "test", _stubborn, cancellable=True)
        assert tid is not None
        await asyncio.wait_for(started.wait(), timeout=1.0)
        task = mgr.get_task(tid)
        assert task is not None
        assert task.id == tid
        runner = task._asyncio_task
        assert isinstance(runner, asyncio.Task)

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = _FakeWriteDbCache()
            shutdown = asyncio.create_task(mgr.cancel_all_running_async(join_timeout=10.0, persist_timeout=0.5))
            await asyncio.wait_for(cleanup_entered.wait(), timeout=1.0)
            shutdown.cancel()
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await asyncio.wait_for(shutdown, timeout=1.0)
            assert isinstance(exc_info.value, asyncio.CancelledError)

        hold.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(runner, timeout=1.0)
        assert task.status == TaskStatus.CANCELLED


class TestSnapshotFailure:
    """_persist_snapshot 普通异常/取消：reservation 正确释放、flush 有界、后续写可继续。"""

    @pytest.mark.asyncio
    async def test_general_exception_releases_pending_and_allows_next_write(self):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        cache = _FakeWriteDbCache()
        task = AppTask(name="snapshot-error")
        mgr._tasks[task.id] = task
        cache.fail_mode = "error"

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = cache
            mgr._persist_task(task)
            await mgr.flush_persistence(timeout_s=0.5)
            assert cache.rows == {}
            assert mgr._persist_pending_count == 0
            assert mgr._queued_persist == {}
            assert mgr._inflight_persist == {}

            task.status = TaskStatus.COMPLETED
            mgr._persist_task(task)
            await mgr.flush_persistence(timeout_s=0.5)
        assert cache.rows[task.id] == "COMPLETED"
        assert mgr._persist_pending_count == 0

    @pytest.mark.asyncio
    async def test_cancelled_snapshot_releases_pending_and_allows_next_write(self):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        cache = _FakeWriteDbCache()
        task = AppTask(name="snapshot-cancel")
        mgr._tasks[task.id] = task
        cache.fail_mode = "cancel"

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm._instance = cache
            mgr._persist_task(task)
            await mgr.flush_persistence(timeout_s=0.5)
            assert cache.rows == {}
            assert mgr._persist_pending_count == 0
            assert mgr._queued_persist == {}
            assert mgr._inflight_persist == {}

            task.status = TaskStatus.COMPLETED
            mgr._persist_task(task)
            await mgr.flush_persistence(timeout_s=0.5)
        assert cache.rows[task.id] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_flush_times_out_bounded_instead_of_hanging(self):
        mgr = TaskManager()
        mgr._db_ready = True
        mgr._loop = asyncio.get_running_loop()
        release = asyncio.Event()

        async def _blocked_persist(_snapshot_params) -> None:
            await release.wait()

        with patch.object(mgr, "_persist_snapshot", _blocked_persist):
            mgr._queue_persist_snapshot(_snapshot("blocked-id", "QUEUED"))
            with pytest.raises(TimeoutError) as exc_info:
                await mgr.flush_persistence(timeout_s=0.05)
            assert isinstance(exc_info.value, TimeoutError)
            assert mgr._persist_pending_count == 1
            release.set()
            await mgr.flush_persistence(timeout_s=0.5)
        assert mgr._persist_pending_count == 0


def _make_scheduler() -> SchedulerService:
    with patch("utils.scheduler_service.ConfigHandler") as mock_ch:
        mock_ch.get_setting.return_value = None
        mock_ch.is_auto_update_enabled.return_value = True
        mock_ch.get_auto_update_time.return_value = "16:30"
        mock_ch.get_concept_schedule_time.return_value = "10:00"
        svc = SchedulerService()
    return svc


class TestLoadDbStateDegradation:
    """app_state 读取失败时降级：不崩溃、不误判已完成、_db_state_loaded 语义可重试。"""

    @pytest.mark.asyncio
    async def test_read_failure_degrades_and_can_retry(self):
        svc = _make_scheduler()
        svc._last_update_date = "20260101"
        catch_up_calls: list[tuple] = []

        async def _catch_up(*args, **kwargs):
            catch_up_calls.append((args, kwargs))

        svc._catch_up_missed_updates = _catch_up

        with (
            patch("data.cache.cache_manager.CacheManager._instance", MagicMock(engine=MagicMock())),
            patch(
                "data.persistence.app_state_service.get_app_state",
                AsyncMock(side_effect=RuntimeError("injected app_state read failure")),
            ),
        ):
            await svc._load_db_state()  # 不抛出

        assert svc._db_state_loaded is False  # 失败不置位，后续可重试
        assert svc._last_update_date == "20260101"  # 未误判为已完成/未覆盖旧水位
        assert catch_up_calls == []

        with (
            patch("data.cache.cache_manager.CacheManager._instance", MagicMock(engine=MagicMock())),
            patch(
                "data.persistence.app_state_service.get_app_state",
                AsyncMock(side_effect=["20260613", "20260614", "20260615"]),
            ),
        ):
            await svc._load_db_state()

        assert svc._db_state_loaded is True
        assert svc._last_update_date == "20260613"
        assert len(catch_up_calls) == 1

    @pytest.mark.asyncio
    async def test_cancelled_read_propagates(self):
        """R2: _load_db_state 的 except Exception 不得吞 CancelledError。"""
        svc = _make_scheduler()
        catch_up_calls: list[tuple] = []

        async def _catch_up(*args, **kwargs):
            catch_up_calls.append((args, kwargs))

        svc._catch_up_missed_updates = _catch_up

        with (
            patch("data.cache.cache_manager.CacheManager._instance", MagicMock(engine=MagicMock())),
            patch(
                "data.persistence.app_state_service.get_app_state",
                AsyncMock(side_effect=asyncio.CancelledError()),
            ),
        ):
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await svc._load_db_state()
            assert isinstance(exc_info.value, asyncio.CancelledError)

        assert svc._db_state_loaded is False
        assert catch_up_calls == []


class _DeleteConn:
    def __init__(self, engine: "_DeleteEngine") -> None:
        self._engine = engine

    async def execute(self, targets) -> None:
        ids = tuple(targets)
        self._engine.deleted.append(ids)


class _DeleteBegin:
    def __init__(self, engine: "_DeleteEngine") -> None:
        self._engine = engine

    async def __aenter__(self):
        return _DeleteConn(self._engine)

    async def __aexit__(self, *_exc):
        return False


class _DeleteEngine:
    def __init__(self) -> None:
        self.deleted: list[tuple] = []

    def begin(self) -> _DeleteBegin:
        return _DeleteBegin(self)


def _patch_task_history_ids():
    fake_table = SimpleNamespace(
        __table__=SimpleNamespace(
            c=SimpleNamespace(id=SimpleNamespace(in_=lambda ids: tuple(ids))),
            delete=lambda: SimpleNamespace(where=lambda cond: cond),
        )
    )
    return patch("data.persistence.models.TaskHistory", fake_table)


class TestInFlightDelete:
    """在途 DELETE 超时/引擎不可用：返回 False 且保留 pending 墓碑，不丢墓碑。"""

    @pytest.mark.asyncio
    async def test_inflight_timeout_keeps_pending_then_retry_succeeds(self, monkeypatch):
        monkeypatch.setattr("services.task_manager._DELETION_BARRIER_WAIT_S", 0.05)
        mgr = TaskManager()
        mgr._loop = asyncio.get_running_loop()
        engine = _DeleteEngine()
        cache = _FakeWriteDbCache()
        tid = "inflight-id"

        assert mgr._begin_inflight_persist(tid) is True
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            _patch_task_history_ids(),
        ):
            mock_cm.return_value = MagicMock(engine=engine)
            mock_cm._instance = cache
            assert await mgr._clear_finished_db([tid]) is False

            assert tid in mgr._pending_db_deletions
            assert tid in mgr._tombstones
            assert engine.deleted == []

            mgr._end_inflight_persist(tid)
            assert await mgr._clear_finished_db([tid]) is True
            assert engine.deleted == [(tid,)]
            assert tid not in mgr._pending_db_deletions
            assert tid in mgr._tombstones  # 墓碑保留，继续拒绝迟到快照

            await mgr._persist_snapshot(_snapshot(tid, "CANCELLED"))
            assert cache.rows == {}  # 迟到快照被墓碑拒绝
            assert mgr._inflight_persist == {}

    @pytest.mark.asyncio
    async def test_engine_unavailable_keeps_pending_and_tombstone(self):
        mgr = TaskManager()
        mgr._loop = asyncio.get_running_loop()
        tid = "no-engine-id"

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            _patch_task_history_ids(),
        ):
            mock_cm.return_value = MagicMock(engine=None)
            assert await mgr._clear_finished_db([tid]) is False

        assert tid in mgr._pending_db_deletions
        assert tid in mgr._tombstones
