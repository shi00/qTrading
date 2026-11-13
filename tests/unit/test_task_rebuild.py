"""services/task_rebuild.py 单测 (LIFE-01, FR-UX-006)。

覆盖 `_rebuild_init_historical_sync` 重建工厂的完整逻辑路径：
- 正常重放（数据处理器返回报告 → 返回成功 Message）
- 报告为 None（同步失败 → InitSyncError，任务进入可重试 FAILED 状态）
- 进度上报被拒（update_progress 返回 False → 抛 CancelledError 配合优雅停机 R2）
- 进度非法值（total=0 / current=None）跳过上报，不误抛取消
并验证模块 import 时的工厂自注册副作用（即使单例已复位，reload 后仍能注册）。
另含 D11-T03 全链测试：真实 `submit_task` 持久化 JSON kwargs → 重启 `init_db`
反序列化并回填 factory → `retry_task` 以原 kwargs 重放/失败，不依赖真实 PostgreSQL。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from core.i18n import Message
from data import data_processor
from data.sync.errors import InitSyncError
from services import task_rebuild  # noqa: F401  # 触发模块自注册副作用
from services.task_manager import (
    TERMINAL_STATUSES,
    AppTask,
    TaskCancelledError,
    TaskManager,
    TaskStatus,
)

SAMPLE_KWARGS = {"quick": False, "days": 250}


def _install_fake_processor(monkeypatch: pytest.MonkeyPatch, initialize_impl):
    """以实时可控的初始化实现替换 data_processor.DataProcessor 单例构造点。

    ``_rebuild_init_historical_sync`` 在函数体内 ``from data.data_processor import
    DataProcessor`` 惰性取类并构造，因此替换模块属性即可拦截其构造与初始化调用。
    ``is_cancelled`` 默认返回 False（取消信号未置位），供取消映射用例覆写。
    """
    fake = MagicMock()
    fake.initialize_system = AsyncMock(side_effect=initialize_impl)
    fake.is_cancelled = MagicMock(return_value=False)
    monkeypatch.setattr(data_processor, "DataProcessor", lambda: fake)
    return fake


_TIMEOUT_S = 5.0

# task_history 的 INSERT 列顺序必须与 services/task_manager.py::_persist_snapshot 一致。
_TASK_HISTORY_COLUMNS = (
    "id",
    "name",
    "task_type",
    "status",
    "progress",
    "description",
    "error",
    "result",
    "created_at",
    "started_at",
    "completed_at",
    "unique_key",
    "factory_key",
    "retry_kwargs",
    "persist_seq",
)


class _FakeTaskHistoryStore:
    """内存版 task_history：只实现 TaskManager 实际发出的三条 SQL 语义。

    替代真实 PostgreSQL，使 ``submit_task`` 的持久化快照与 ``init_db`` 的回填/读取
    走生产代码路径（``_persist_snapshot`` / ``init_db``），而非 mock 掉这些入口。
    """

    def __init__(self, rows: list[dict] | None = None):
        self.rows: dict[str, dict] = {str(r["id"]): dict(r) for r in (rows or [])}
        self.queries: list[tuple[str, tuple | list | None]] = []

    async def write_db(self, sql, params=None, **_kwargs):
        self.queries.append((sql, params))
        if sql.startswith("UPDATE task_history SET status = $1"):
            # init_db 启动回填：上次会话遗留的 RUNNING/QUEUED → INTERRUPTED
            for row in self.rows.values():
                if row["status"] in (TaskStatus.RUNNING.value, TaskStatus.QUEUED.value):
                    row["status"] = params[0]
                    row["description"] = params[1]
        elif sql.startswith("INSERT INTO task_history"):
            assert params is not None  # INSERT 快照必带参数；收窄类型供 pyright
            row = dict(zip(_TASK_HISTORY_COLUMNS, params, strict=True))
            existing = self.rows.get(row["id"])
            if existing is None or existing["persist_seq"] < row["persist_seq"]:
                self.rows[row["id"]] = row
        # 30 天 DELETE 清理：内存替身无需实现（不改变断言数据）

    async def read_db(self, _sql, _params=None):
        if not self.rows:
            return pd.DataFrame()
        ordered = sorted(self.rows.values(), key=lambda r: str(r.get("created_at")), reverse=True)
        return pd.DataFrame(ordered)


def _install_fake_cache(monkeypatch: pytest.MonkeyPatch, store: _FakeTaskHistoryStore):
    """替换 data.cache.cache_manager.CacheManager（init_db 与 _persist_snapshot 的构造点）。"""
    import data.cache.cache_manager as cache_module

    class _FakeCacheManager:
        _instance = store

        def __new__(cls):
            return store

    monkeypatch.setattr(cache_module, "CacheManager", _FakeCacheManager)


def _subscribe_terminal(mgr: TaskManager, task_id: str) -> tuple[asyncio.Event, object]:
    """订阅任务通知，终态（COMPLETED/FAILED/...）时置位 Event，替代随机 sleep。"""
    done = asyncio.Event()

    def _on_update(tasks: list[AppTask]) -> None:
        task = next((t for t in tasks if t.id == task_id), None)
        if task is not None and task.status in TERMINAL_STATUSES:
            done.set()

    mgr.subscribe(_on_update)
    return done, _on_update


@contextlib.asynccontextmanager
async def _running_task_session(monkeypatch: pytest.MonkeyPatch, kwargs: dict):
    """真实 ``submit_task`` 启动一个阻塞任务，暴露 RUNNING 时的 DB 持久化行。

    工厂由受控 ``asyncio.Event`` 阻塞，退出上下文时释放并等待终态，避免悬挂任务。
    """
    store = _FakeTaskHistoryStore()
    _install_fake_cache(monkeypatch, store)
    mgr = TaskManager()
    mgr._db_ready = True
    mgr._loop = asyncio.get_running_loop()

    started = asyncio.Event()
    release = asyncio.Event()
    captured: dict = {}

    async def _factory(task_id, **received):
        captured["task_id"] = task_id
        captured["kwargs"] = received
        started.set()
        await release.wait()
        return Message("sys_init_success")

    task_id = mgr.submit_task(
        name="init_sync",
        task_type="Data",
        coroutine_factory=_factory,
        cancellable=True,
        unique_key="init_sync_unique",
        factory_key=task_rebuild.INIT_HISTORICAL_SYNC_KEY,
        **kwargs,
    )
    assert task_id is not None
    await asyncio.wait_for(started.wait(), timeout=_TIMEOUT_S)
    await mgr.flush_persistence()
    running_row = dict(store.rows[task_id])
    try:
        yield mgr, store, task_id, running_row, captured
    finally:
        done, callback = _subscribe_terminal(mgr, task_id)
        release.set()
        await asyncio.wait_for(done.wait(), timeout=_TIMEOUT_S)
        mgr.unsubscribe(callback)
        await mgr.flush_persistence()


def _restart_with_row(monkeypatch: pytest.MonkeyPatch, row: dict) -> tuple[TaskManager, _FakeTaskHistoryStore]:
    """模拟进程重启：丢弃旧单例，按生产 import 副作用注册工厂，再装入崩溃现场行。"""
    TaskManager._reset_singleton()
    TaskManager.register_retryable_factory(
        task_rebuild.INIT_HISTORICAL_SYNC_KEY,
        task_rebuild._rebuild_init_historical_sync,
    )
    store = _FakeTaskHistoryStore([row])
    _install_fake_cache(monkeypatch, store)
    return TaskManager(), store


async def test_module_import_registers_factory():
    """模块 import 即注册 INIT_HISTORICAL_SYNC_KEY（reload 保证复位后仍可断言）。"""
    importlib.reload(task_rebuild)
    factory = TaskManager._RETRYABLE_FACTORIES.get(task_rebuild.INIT_HISTORICAL_SYNC_KEY)
    assert callable(factory)
    assert factory is task_rebuild._rebuild_init_historical_sync


async def test_rebuild_success_reports_and_returns_success(monkeypatch: pytest.MonkeyPatch):
    """正常路径：进度上报被接受，返回 sys_init_success。

    同时覆盖 _progress 的非法值短路（total=0 / current=None 不上报不抛错）。
    """

    def initialize_impl(progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback(5, 10, Message("init_sync_market_snapshot"))  # 正常占比
        progress_callback(1, 0, Message("irrelevant"))  # total=0 → 跳过
        progress_callback(None, 10, Message("irrelevant"))  # current=None → 跳过
        assert kwargs == SAMPLE_KWARGS
        return {"ok": True}

    fake = _install_fake_processor(monkeypatch, initialize_impl)
    # 真实 TaskManager 对不存在的任务 update_progress 返回 False；此处模拟"任务仍 RUNNING，
    # 进度被接受"的语义，仅走正常上报路径。
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: True)

    result = await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert isinstance(result, Message)
    assert result.key == "sys_init_success"
    fake.initialize_system.assert_awaited_once()


async def test_rebuild_none_report_raises_init_sync_error(monkeypatch: pytest.MonkeyPatch):
    """同步无返回报告视为失败：抛 InitSyncError 使任务进入可重试 FAILED。"""

    def initialize_impl(progress_callback=None, **kwargs):
        return None

    _install_fake_processor(monkeypatch, initialize_impl)

    with pytest.raises(InitSyncError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert exc_info.value.args[0].key == "ds_init_fail_generic"


async def test_rebuild_degraded_base_dimension_is_retryable(monkeypatch: pytest.MonkeyPatch):
    """UIR-F14-002：基础维度（申万行业/名称历史）降级时 initialization 返回 None，
    重建工厂转为可重试 InitSyncError，不宣称初始化完成。"""

    def initialize_impl(progress_callback=None, **kwargs):
        if progress_callback is not None:
            progress_callback(1, 100, Message("init_sync_sw_industry"))
            progress_callback(9, 100, Message("init_sync_name_change"))
        return None

    _install_fake_processor(monkeypatch, initialize_impl)
    # 任务仍 RUNNING：进度被接受，才能走到「报告为 None」的降级分支
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: True)

    with pytest.raises(InitSyncError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert exc_info.value.args[0].key == "ds_init_fail_generic"


async def test_rebuild_progress_rejected_raises_cancelled(monkeypatch: pytest.MonkeyPatch):
    """进度上报被 TaskManager 拒绝（任务已取消/终止）→ 抛 CancelledError (R2)。"""

    def initialize_impl(progress_callback=None, **kwargs):
        assert progress_callback is not None
        progress_callback(5, 10, Message("init_sync_market_snapshot"))
        return {"ok": True}

    _install_fake_processor(monkeypatch, initialize_impl)
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: False)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    assert "task cancelled" in str(exc_info.value)


async def test_submit_persists_json_roundtrippable_raw_kwargs(monkeypatch: pytest.MonkeyPatch):
    """a) 真实 submit_task 持久化的 retry_kwargs 是原始 kwargs 的 JSON 往返值。

    断言只含 days/quick，不含闭包、配置快照或 DB 目标——这些不可 JSON 序列化的
    对象一旦混入，崩溃后 init_db 无法反序列化。
    """
    async with _running_task_session(monkeypatch, SAMPLE_KWARGS) as (_mgr, _store, task_id, running_row, captured):
        assert captured["task_id"] == task_id
        assert captured["kwargs"] == SAMPLE_KWARGS  # 生产 submit 原样传给工厂
        assert running_row["status"] == TaskStatus.RUNNING.value
        assert running_row["factory_key"] == task_rebuild.INIT_HISTORICAL_SYNC_KEY
        assert running_row["unique_key"] == "init_sync_unique"

        raw = running_row["retry_kwargs"]
        assert isinstance(raw, str)
        assert json.loads(raw) == SAMPLE_KWARGS
        assert json.dumps(json.loads(raw), sort_keys=True) == json.dumps(SAMPLE_KWARGS, sort_keys=True)
        assert set(json.loads(raw)) == {"days", "quick"}


async def test_submit_non_json_kwargs_disables_crash_retry(monkeypatch: pytest.MonkeyPatch):
    """a) 契约：非 JSON 可序列化 kwargs 不落 retry_kwargs，崩溃重试自然禁用。"""
    sentinel = object()
    async with _running_task_session(monkeypatch, {"days": 250, "callback": sentinel}) as (
        mgr,
        _store,
        task_id,
        running_row,
        captured,
    ):
        assert captured["kwargs"]["callback"] is sentinel
        assert running_row["factory_key"] == task_rebuild.INIT_HISTORICAL_SYNC_KEY
        # _serialize_retry_kwargs 捕获 TypeError 后返回 None（不臆造降级/不写半截 JSON）
        assert running_row["retry_kwargs"] is None

        TaskManager.register_retryable_factory(
            task_rebuild.INIT_HISTORICAL_SYNC_KEY,
            task_rebuild._rebuild_init_historical_sync,
        )
        rehydrated = AppTask(id=task_id, status=TaskStatus.INTERRUPTED)
        mgr._rehydrate_retry_info(rehydrated, running_row)
        assert rehydrated._coroutine_factory is None
        assert rehydrated.is_retryable is False


async def test_restart_init_db_rehydrates_factory_and_retry_uses_original_kwargs(monkeypatch: pytest.MonkeyPatch):
    """b)+c) 崩溃现场 RUNNING 行 → init_db 置 INTERRUPTED/回填 factory → retry 成功。

    关键断言：新 task_id、复用 unique_key、工厂收到的是 DB 反序列化的原 kwargs
    （days=250/quick=False），而非当前配置重算结果。
    """
    async with _running_task_session(monkeypatch, SAMPLE_KWARGS) as (_mgr, _store, old_id, running_row, _captured):
        assert running_row["status"] == TaskStatus.RUNNING.value

    mgr, store = _restart_with_row(monkeypatch, running_row)
    await mgr.init_db()

    # b) 生产 UPDATE 已执行，内存 DB 中 RUNNING → INTERRUPTED；历史行被反序列化回填
    assert any(sql.startswith("UPDATE task_history SET status = $1") for sql, _ in store.queries)
    assert store.rows[old_id]["status"] == TaskStatus.INTERRUPTED.value
    assert len(mgr._history) == 1
    historical = mgr._history[0]
    assert historical.id == old_id
    assert historical.status == TaskStatus.INTERRUPTED
    assert historical.factory_key == task_rebuild.INIT_HISTORICAL_SYNC_KEY
    assert historical.unique_key == "init_sync_unique"
    assert historical._coroutine_kwargs == SAMPLE_KWARGS
    assert historical._coroutine_factory is task_rebuild._rebuild_init_historical_sync
    assert historical.is_retryable is True

    received: dict = {}

    def initialize_impl(progress_callback=None, **kwargs):
        received.update(kwargs)
        assert progress_callback is not None
        progress_callback(5, 10, Message("init_sync_market_snapshot"))
        return {"ok": True}

    fake = _install_fake_processor(monkeypatch, initialize_impl)

    # c) retry_task 走真实 submit_task → 新 id、复用 unique_key、注册工厂被执行
    new_id = mgr.retry_task(old_id)
    assert new_id is not None
    assert new_id != old_id

    done, callback = _subscribe_terminal(mgr, new_id)
    await asyncio.wait_for(done.wait(), timeout=_TIMEOUT_S)
    mgr.unsubscribe(callback)
    await mgr.flush_persistence()

    retried = mgr.get_task(new_id)
    assert retried is not None
    assert retried.status == TaskStatus.COMPLETED
    assert retried.progress == 1.0
    assert retried.unique_key == "init_sync_unique"
    assert retried.factory_key == task_rebuild.INIT_HISTORICAL_SYNC_KEY
    assert received == SAMPLE_KWARGS
    fake.initialize_system.assert_awaited_once()

    # 重试链本身仍可再次崩溃恢复：新行重新持久化同一 factory_key/kwargs
    persisted = store.rows[new_id]
    assert persisted["status"] == TaskStatus.COMPLETED.value
    assert persisted["unique_key"] == "init_sync_unique"
    assert json.loads(persisted["retry_kwargs"]) == SAMPLE_KWARGS


async def test_restart_retry_factory_failure_marks_failed_and_retryable(monkeypatch: pytest.MonkeyPatch):
    """d) 注册工厂抛 InitSyncError → 任务 FAILED 且 is_retryable 为 True。"""
    async with _running_task_session(monkeypatch, SAMPLE_KWARGS) as (_mgr, _store, old_id, running_row, _captured):
        pass

    mgr, store = _restart_with_row(monkeypatch, running_row)
    await mgr.init_db()
    # 初始化返回 None → _rebuild_init_historical_sync 抛 InitSyncError
    _install_fake_processor(monkeypatch, lambda progress_callback=None, **kwargs: None)

    new_id = mgr.retry_task(old_id)
    assert new_id is not None and new_id != old_id

    done, callback = _subscribe_terminal(mgr, new_id)
    await asyncio.wait_for(done.wait(), timeout=_TIMEOUT_S)
    mgr.unsubscribe(callback)
    await mgr.flush_persistence()

    retried = mgr.get_task(new_id)
    assert retried is not None
    assert retried.status == TaskStatus.FAILED
    assert retried.is_retryable is True
    assert retried._coroutine_factory is task_rebuild._rebuild_init_historical_sync
    assert store.rows[new_id]["status"] == TaskStatus.FAILED.value


async def test_restart_unregistered_factory_key_is_not_retryable(monkeypatch: pytest.MonkeyPatch):
    """e) 未注册 factory_key 的历史任务不误显示为可重试，retry_task 返回 None。"""
    async with _running_task_session(monkeypatch, SAMPLE_KWARGS) as (_mgr, _store, old_id, running_row, _captured):
        crash_row = dict(running_row)
        crash_row["factory_key"] = "unknown_retry_key"

    mgr, _store = _restart_with_row(monkeypatch, crash_row)
    await mgr.init_db()

    historical = mgr._history[0]
    assert historical.id == old_id
    assert historical.status == TaskStatus.INTERRUPTED
    assert historical.factory_key == "unknown_retry_key"
    assert historical._coroutine_factory is None
    assert historical.is_retryable is False
    assert mgr.retry_task(old_id) is None


# --- C14 取消边界（ADR-0018 承接）：协作取消映射取消终态，不得算 COMPLETED/FAILED ---


async def test_rebuild_cooperative_cancel_raises_task_cancelled(monkeypatch: pytest.MonkeyPatch):
    """C14：processor 取消信号置位且报告为 None → TaskCancelledError（取消终态）。

    修复前：一律抛 InitSyncError → 任务被算 FAILED，用户主动取消显示为失败假象。
    """

    def initialize_impl(progress_callback=None, **kwargs):
        return None

    fake = _install_fake_processor(monkeypatch, initialize_impl)
    fake.is_cancelled = MagicMock(return_value=True)  # 协作取消信号置位
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: True)

    with pytest.raises(TaskCancelledError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    # 强断言：携带取消终态语义的 Message（任务中心渲染为取消描述）
    assert exc_info.value.description.key == "task_cancelled_desc"


async def test_rebuild_clean_cancel_flag_none_report_stays_init_sync_error(monkeypatch: pytest.MonkeyPatch):
    """C14 对照：取消信号未置位的 None 报告（降级 / 部分完成）保持 InitSyncError。

    降级路径仍映射 FAILED（可重试补齐），不与协作取消混同。
    """

    def initialize_impl(progress_callback=None, **kwargs):
        return None

    _install_fake_processor(monkeypatch, initialize_impl)  # is_cancelled 默认 False
    monkeypatch.setattr(TaskManager, "update_progress", lambda self, *a, **k: True)

    with pytest.raises(InitSyncError) as exc_info:
        await task_rebuild._rebuild_init_historical_sync("task-1", **SAMPLE_KWARGS)

    # 强断言：降级路径仍走通用失败文案（与取消文案严格区分）
    assert exc_info.value.args[0].key == "ds_init_fail_generic"


async def test_task_cancelled_error_maps_to_cancelled_terminal(monkeypatch: pytest.MonkeyPatch):
    """C14 协议：worker 抛 TaskCancelledError → 任务终态 CANCELLED（≠ COMPLETED）。

    TaskManager 用异常协议识别协作取消，不做业务类型 if-elif 枚举扩张。
    """
    store = _FakeTaskHistoryStore()
    _install_fake_cache(monkeypatch, store)
    mgr = TaskManager()
    mgr._db_ready = True
    mgr._loop = asyncio.get_running_loop()

    async def _cancelled_factory(task_id, **kwargs):
        raise TaskCancelledError(Message("task_cancelled_desc"))

    task_id = mgr.submit_task(
        name="c14_cancel",
        task_type="Data",
        coroutine_factory=_cancelled_factory,
        cancellable=True,
    )
    assert task_id is not None
    done, callback = _subscribe_terminal(mgr, task_id)
    await asyncio.wait_for(done.wait(), timeout=_TIMEOUT_S)
    mgr.unsubscribe(callback)
    await mgr.flush_persistence()

    task = mgr.get_task(task_id)
    assert task is not None
    assert task.status == TaskStatus.CANCELLED
    assert task.status != TaskStatus.COMPLETED  # C14：取消不得被算完成
    assert task.progress < 1.0  # 不伪设完整进度
    assert task.is_retryable is False  # CANCELLED 不在可重试集合（用户放弃意图）
    assert store.rows[task_id]["status"] == TaskStatus.CANCELLED.value


async def test_restart_retry_cooperative_cancel_maps_to_cancelled_terminal(monkeypatch: pytest.MonkeyPatch):
    """C14 全链：崩溃恢复重试期间用户协作取消 → 终态 CANCELLED，持久化一致。"""
    async with _running_task_session(monkeypatch, SAMPLE_KWARGS) as (_mgr, _store, old_id, running_row, _captured):
        pass

    mgr, store = _restart_with_row(monkeypatch, running_row)
    await mgr.init_db()

    fake = _install_fake_processor(monkeypatch, lambda progress_callback=None, **kwargs: None)
    fake.is_cancelled = MagicMock(return_value=True)

    new_id = mgr.retry_task(old_id)
    assert new_id is not None and new_id != old_id

    done, callback = _subscribe_terminal(mgr, new_id)
    await asyncio.wait_for(done.wait(), timeout=_TIMEOUT_S)
    mgr.unsubscribe(callback)
    await mgr.flush_persistence()

    retried = mgr.get_task(new_id)
    assert retried is not None
    assert retried.status == TaskStatus.CANCELLED  # 取消 ≠ COMPLETED / FAILED
    assert retried.progress < 1.0
    assert store.rows[new_id]["status"] == TaskStatus.CANCELLED.value
