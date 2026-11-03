"""D11-R02：任务重试与「当前 DB 目标」绑定关系的证据性测试。

结论（本文件固化的契约，均来自既有生产代码，不臆造 API）：

1. ``TaskManager`` 的持久化写入在「执行时」经 ``CacheManager._instance`` 取当前引擎，
   历史任务本身**不持久化**任何 DB 目标指纹（``task_history`` 无 URL/DSN/目标列，
   ``AppTask`` 也无相关字段）。因此同目标重启后 retry 落在同一引擎；切换目标后
   旧历史任务的 retry 落在**新目标**，旧目标不会新增该任务的行。
2. 仓库**不存在** ``generation`` / ``engine_generation`` API（已核对
   ``data/cache/cache_manager.py``、``data/cache/engine_manager.py``、
   ``data/persistence/engine_provider.py``）。R5 语义由 ``engine_provider`` 的
   「已释放引擎集合」承载：dispose 登记不可逆，旧引擎上的 DAO 操作抛
   ``EngineDisposedError``；新引擎是不同对象，天然可用（以对象身份替代代际计数）。
3. 已知风险（本文件以契约测试暴露，不在本分支修复）：快照在队列中被调度期间若
   目标被切换，``_persist_snapshot`` 在真正写库时按**新目标**路由，旧目标的
   历史任务快照会被写入新目标。真实 PG 侧的验证见
   ``tests/integration/test_task_db_target_fingerprint_pg.py``（CI 提供 PG）。
"""

import asyncio
import inspect
import json
from dataclasses import fields

import pandas as pd
import pytest

from data.cache.cache_manager import CacheManager
from data.cache.engine_manager import EngineManager
from data.persistence import engine_provider
from data.persistence.daos.base_dao import BaseDao, EngineDisposedError
from services.task_manager import AppTask, TaskManager, TaskStatus
from utils.time_utils import get_now, to_utc_for_db

pytestmark = pytest.mark.unit

# task_history 的写入列顺序（与 ``TaskManager._persist_snapshot`` 的 INSERT 参数顺序一致）。
# 该常量仅用于 fake 缓存记录参数，不代表生产 schema 允许新增目标指纹列。
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

_FACTORY_KEY = "d11_r02_probe"


class _FakeEngine:
    """仅承载身份语义的假引擎（不连接任何数据库）。"""

    def __init__(self, name: str):
        self.name = name


class _FakeTaskDB:
    """按「目标」隔离的 task_history 假存储，记录每次写入的引擎身份。"""

    def __init__(self, name: str):
        self.name = name
        self.engine = _FakeEngine(name)
        self.rows: dict[str, dict] = {}
        self.writes: list[tuple[str, tuple]] = []
        self.write_engine_names: list[str] = []

    async def write_db(self, sql: str, params: tuple | None = None, *, suppress_errors: bool = False):
        verb = (sql.strip().split(None, 1)[0] if sql.strip() else "").upper()
        self.writes.append((verb, tuple(params or ())))
        self.write_engine_names.append(self.engine.name)
        if verb == "INSERT" and params and len(params) == len(_TASK_HISTORY_COLUMNS):
            row = dict(zip(_TASK_HISTORY_COLUMNS, params, strict=True))
            self.rows[row["id"]] = row
        elif verb == "UPDATE" and params and params[0] == TaskStatus.INTERRUPTED.value:
            for row in self.rows.values():
                if row.get("status") in (TaskStatus.RUNNING.value, TaskStatus.QUEUED.value):
                    row["status"] = TaskStatus.INTERRUPTED.value
                    row["description"] = params[1]
        # DELETE（init_db 的 30 天清理）对本测试无意义，保留全部行
        return None

    async def read_db(self, sql: str, params: tuple | None = None) -> pd.DataFrame:
        return pd.DataFrame(list(self.rows.values()))

    def seed_failed_task(self, task_id: str, kwargs: dict | None = None) -> None:
        """预置一条重启后可见的 FAILED 历史任务（模拟上一会话写入旧目标）。"""
        now = to_utc_for_db(get_now())
        self.rows[task_id] = {
            "id": task_id,
            "name": "probe",
            "task_type": "test",
            "status": TaskStatus.FAILED.value,
            "progress": 0.0,
            "description": "failed",
            "error": "probe failure",
            "result": None,
            "created_at": now,
            "started_at": now,
            "completed_at": now,
            "unique_key": None,
            "factory_key": _FACTORY_KEY,
            "retry_kwargs": json.dumps(kwargs or {"probe": "x"}),
            "persist_seq": 1,
        }


class _CacheManagerBinding:
    """替换 ``data.cache.cache_manager.CacheManager``：既可调用也暴露 ``_instance``。"""

    def __init__(self, cache: _FakeTaskDB):
        self._instance = cache

    def __call__(self) -> _FakeTaskDB:
        return self._instance


class _FakeThreadPool:
    """TaskManager.__init__ 仅读取 ``cpu_pool_max_workers``。"""

    cpu_pool_max_workers = 8
    io_pool_max_workers = 4


@pytest.fixture(autouse=True)
def _mock_thread_pool(monkeypatch):
    monkeypatch.setattr("services.task_manager.ThreadPoolManager", _FakeThreadPool)


@pytest.fixture(autouse=True)
def _clean_engine_provider():
    engine_provider.reset_engine_provider()
    yield
    engine_provider.reset_engine_provider()


@pytest.fixture(autouse=True)
def _registered_probe_factory():
    TaskManager.register_retryable_factory(_FACTORY_KEY, _failing_factory)
    yield
    TaskManager._RETRYABLE_FACTORIES.pop(_FACTORY_KEY, None)


async def _failing_factory(task_id: str, **kwargs):
    raise RuntimeError(f"probe failure for {task_id}")


def _bind_cache(monkeypatch, cache: _FakeTaskDB) -> _CacheManagerBinding:
    binding = _CacheManagerBinding(cache)
    monkeypatch.setattr("data.cache.cache_manager.CacheManager", binding)
    return binding


async def _wait_terminal(mgr: TaskManager, task_id: str, timeout_s: float = 5.0) -> AppTask:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        task = mgr._tasks.get(task_id)
        if task is not None and task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        ):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach a terminal status within {timeout_s}s")


async def _flush(mgr: TaskManager) -> None:
    await mgr.flush_persistence(timeout_s=5.0)
    # 让 call_soon_threadsafe 投递的写入任务有机会执行完
    await asyncio.sleep(0)


def _snapshot(task_id: str, *, status: TaskStatus = TaskStatus.FAILED, factory_key: str | None = _FACTORY_KEY) -> tuple:
    now = to_utc_for_db(get_now())
    retry_kwargs = json.dumps({"probe": "x"}) if factory_key else None
    return (
        task_id,
        "probe",
        "test",
        status.value,
        0.0,
        "desc",
        "",
        None,
        now,
        now,
        now,
        None,
        factory_key,
        retry_kwargs,
        1,
    )


class TestSameTargetRetry:
    @pytest.mark.asyncio
    async def test_retry_after_restart_uses_same_target(self, monkeypatch):
        """同目标：submit → 失败落库 → 重启（init_db）→ retry，读写均为同一引擎/目标。"""
        target_a = _FakeTaskDB("A")
        _bind_cache(monkeypatch, target_a)

        first = TaskManager()
        await first.init_db()
        original_id = first.submit_task(
            name="probe",
            task_type="test",
            coroutine_factory=_failing_factory,
            factory_key=_FACTORY_KEY,
            unique_key="d11_r02_unique",
        )
        assert original_id is not None
        failed = await _wait_terminal(first, original_id)
        assert failed.status == TaskStatus.FAILED
        await _flush(first)
        assert target_a.rows[original_id]["status"] == TaskStatus.FAILED.value

        # 模拟进程重启：单例重置 + init_db 从同一目标读回历史
        TaskManager._reset_singleton()
        TaskManager.register_retryable_factory(_FACTORY_KEY, _failing_factory)
        second = TaskManager()
        await second.init_db()
        restored = next(h for h in second._history if h.id == original_id)
        assert restored.status == TaskStatus.FAILED
        assert restored._coroutine_factory is not None

        retry_id = second.retry_task(original_id)
        assert retry_id is not None and retry_id != original_id
        await _wait_terminal(second, retry_id)
        await _flush(second)

        assert original_id in target_a.rows
        assert retry_id in target_a.rows
        assert set(target_a.write_engine_names) == {"A"}


class TestTargetSwitchRetry:
    @pytest.mark.asyncio
    async def test_retry_after_target_switch_writes_new_target_only(self, monkeypatch):
        """切换目标后 retry 历史任务：写入新目标，旧目标无新增行。"""
        target_a = _FakeTaskDB("A")
        target_a.seed_failed_task("old-task")
        binding = _bind_cache(monkeypatch, target_a)

        mgr = TaskManager()
        await mgr.init_db()
        restored = next(h for h in mgr._history if h.id == "old-task")
        assert restored.status == TaskStatus.FAILED

        # 目标切换：当前受管缓存换成新目标；TaskManager 内存中的历史任务保持不变
        target_b = _FakeTaskDB("B")
        binding._instance = target_b
        a_rows_before = dict(target_a.rows)
        a_writes_before = len(target_a.writes)

        retry_id = mgr.retry_task("old-task")
        assert retry_id is not None
        await _wait_terminal(mgr, retry_id)
        await _flush(mgr)

        assert retry_id in target_b.rows
        assert retry_id not in target_a.rows
        assert target_a.rows == a_rows_before
        assert len(target_a.writes) == a_writes_before
        assert set(target_b.write_engine_names) == {"B"}

    @pytest.mark.asyncio
    async def test_inflight_snapshot_routes_to_target_active_at_flush_time(self, monkeypatch):
        """契约/风险：快照在写库时才解析当前目标，切换后旧任务快照会落到新目标。"""
        target_a = _FakeTaskDB("A")
        binding = _bind_cache(monkeypatch, target_a)
        mgr = TaskManager()

        pending_write = mgr._persist_snapshot(_snapshot("inflight-task"))
        # 写入尚未执行时切换目标（等价于队列积压期间 DB 目标被替换）
        target_b = _FakeTaskDB("B")
        binding._instance = target_b
        await pending_write

        assert "inflight-task" in target_b.rows
        assert "inflight-task" not in target_a.rows


class TestPersistenceContract:
    def test_task_and_persisted_schema_have_no_db_target_fingerprint(self):
        """契约：历史任务不持久化 DB 目标指纹（无新 schema，仅固化现状）。"""
        field_names = {f.name for f in fields(AppTask)}
        assert not {
            name
            for name in field_names
            if name.startswith("db_") or "fingerprint" in name or name in {"db_url", "dsn", "engine_generation"}
        }

        source = inspect.getsource(TaskManager._persist_snapshot).lower()
        for marker in ("db_url", "database_url", "dsn", "fingerprint", "db_target", "engine_generation"):
            assert marker not in source

    def test_retry_kwargs_serialize_only_business_kwargs(self):
        """契约：retry_kwargs 只承载业务参数，不含任何目标标识。"""
        task = AppTask(factory_key=_FACTORY_KEY)
        task._coroutine_kwargs = {"probe": "x"}  # 测试直接固化单源字段
        serialized = TaskManager._serialize_retry_kwargs(task)
        assert serialized is not None
        assert json.loads(serialized) == {"probe": "x"}


class TestEngineGenerationContract:
    def test_no_engine_generation_api_exists(self):
        """契约：仓库不存在 generation API；R5 由「已释放引擎集合」+ 对象身份承载。"""
        assert not hasattr(EngineManager, "generation")
        assert not hasattr(CacheManager, "generation")
        assert not hasattr(AppTask, "engine_generation")

    def test_disposed_engine_rejected_and_new_engine_accepted(self):
        """dispose 后旧引擎操作被拒；新引擎是不同对象，天然可用（无代际自增 API）。"""
        old_engine = _FakeEngine("old")
        new_engine = _FakeEngine("new")

        engine_provider.set_engine(old_engine)
        engine_provider.mark_disposed(True)

        assert engine_provider.is_disposed(old_engine) is True
        with pytest.raises(EngineDisposedError):
            BaseDao(old_engine)._check_engine("probe")

        engine_provider.set_engine(new_engine)
        assert engine_provider.is_disposed(new_engine) is False
        assert engine_provider.is_disposed() is False
        BaseDao(new_engine)._check_engine("probe")  # 不抛异常

        # 释放不可逆：即使新引擎已接管，旧引擎仍被永久拒绝（替代代际递增语义）
        assert engine_provider.is_disposed(old_engine) is True
        with pytest.raises(EngineDisposedError):
            BaseDao(old_engine)._check_engine("probe")
