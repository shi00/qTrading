"""Agent 运行时宿主组装与 owner-loop 桥（设计 §10.6 / HANDOFF §8.1-§8.3）。

职责：
1. 宿主组装（C-05）：在存储层迁移就绪后组装宿主对象并保证关闭顺序——
   1. 双迁移（``data.persistence.agent_migrator.run_agent_runtime_migrations``：迁移+
      运行角色最小授权+版本守卫，独立迁移引擎 ≤2 连接，先于任何宿主对象；中间失败
      不 launch/不建对象/保历史与原迁移状态）；
   2. ``runtime_engine``（pool 4 / overflow 0 / timeout 3 / hide_parameters，psycopg 同步）
      与 ``control_engine``（pool 1，禁额外监听；运行期增量连接合计 7 条，见
      ``AGENT_RUNTIME_CONNECTION_BUDGET``）；
   3. ``DBOS(config)``：注入 runtime_engine、``run_migrations=False``（launch 改为验证
      模式）、``enable_otlp=False``、``max_executor_threads=4``、``use_listen_notify=True``、
      ``sharding=False``、真实 ``application_version``、每进程唯一 ``executor_id``；
   4. ``SQLAlchemyDatasource.create(engine=runtime_engine, schema="agent_ds",
      run_migrations=False)``（verify_migrations 模式）；
   5. ``DBOSClient(system_database_engine=control_engine, use_listen_notify=False,
      retry_connection_errors=False)``（HANDOFF §8.3：禁监听、禁无限连接重试）；
2. owner-loop 桥（D-01，§8.2）：owner loop 句柄登记与跨线程提交
   （``OwnerLoopBridge``）、稳定 operation 在途/完成登记、跨 loop 流通道。

明确不做：
- **不 launch**：默认 launch 会将所属 pending workflows 重新入队且无公开关闭开关
  （HANDOFF §8.4 needs-spike）；隔离方案真 PG/真进程实证通过后经
  ``launch_agent_runtime``（listen_queues([]) 先于 launch）启动。

关闭顺序（HANDOFF §8.3 destroy 契约）：``DBOS.destroy``（工作收敛）→
``DBOSClient.destroy`` → 双引擎 dispose；幂等。

SQL 合规（R4）：本模块不执行任何 SQL（迁移/授权均在 data 层，静态 text() 常量）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol, TypeVar, cast

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, SQLAlchemyDatasource

from data.persistence.agent_migrator import (
    AgentRuntimeMigrationError,
    run_agent_runtime_migrations,
)

# 设计冻结值（HANDOFF §8.1 第 6/7 条、§8.3）：连接预算与线程/监听配置。
RUNTIME_DB_NAME = "qtrading-agent-runtime"
#: 运行期增量连接上限（HANDOFF §8.1/§8.3：唯一 Agent 实例最多 7 条）。
#: 组成（检视 R12）：4 运行池 + 1 脱池 LISTEN + 1 控制池 + 1 实例锁 = 7；四类
#: 所有权各自独立，不靠注释把实例锁并进 LISTEN 或控制池。
AGENT_RUNTIME_CONNECTION_BUDGET: Final[int] = 7
RUNTIME_ENGINE_POOL_SIZE: Final[int] = 4
#: DBOS 脱池 LISTEN 保留 1 条（SDK 以 ``raw_connection()+detach()`` 长期占用，
#: 不占 runtime 池配额，故必须在预算内单列）。
DBOS_LISTEN_CONNECTION_RESERVE: Final[int] = 1
#: 控制池 1 条（DBOSClient 与清理共享；实例锁另占独立 1 条，见 recovery.py）。
CONTROL_ENGINE_POOL_SIZE: Final[int] = 1
#: 实例锁独立单连接保留 1 条（``recovery.AdvisoryLockInstanceGuard`` 持有会话级
#: advisory lock，独立引擎单连接，不与其他池共享）。
INSTANCE_GUARD_CONNECTION_RESERVE: Final[int] = 1
DBOS_MAX_EXECUTOR_THREADS = 4
DBOS_SCHEMA = "dbos"
AGENT_DS_SCHEMA = "agent_ds"
PSYCGRP_APPLICATION_NAME = "qtrading_agent_runtime"
# HANDOFF §8.1「同步 Engine 均为 Psycopg，设置有限 statement/idle-in-transaction
# 超时」：注入自定义 system_database_engine 后 SDK 不再兜底（其
# sys_db_idle_transaction_timeout_sec 明示不作用于注入引擎），须由本模块显式设置。
# 契约未定具体值（低风险自行决定）：系统库事务均为短语句（upsert/claim），
# statement 30s / idle-in-transaction 60s 上限远超正常路径且能防悬挂占死 4+2 池。
RUNTIME_STATEMENT_TIMEOUT_MS = 30000
RUNTIME_IDLE_TX_TIMEOUT_MS = 60000
_ENGINE_SESSION_OPTIONS = (
    f"-c statement_timeout={RUNTIME_STATEMENT_TIMEOUT_MS} "
    f"-c idle_in_transaction_session_timeout={RUNTIME_IDLE_TX_TIMEOUT_MS}"
)


@dataclass(frozen=True)
class AgentRuntimeSettings:
    """宿主组装参数。

    Attributes:
        system_database_url: 同步 psycopg 运行库 URL（含密码，仅内存传递，不入日志）。
        application_version: 真实工作流代码版本（ADR-0017：pyproject version 唯一正本），
            禁止随机值规避重放检查（HANDOFF §8.3）。
        executor_id: 每进程执行 epoch（HANDOFF §8.4 第 4 条）；缺省每进程生成唯一值。
    """

    system_database_url: str
    application_version: str
    executor_id: str | None = None


@dataclass
class AgentRuntimeHandle:
    """组装产物：持有全部宿主对象引用与关闭入口（未 launch）。

    注意：``close()`` 调用类级 ``DBOS.destroy()`` 销毁全局单例——进程内同时存在
    多个 handle 时先 close 者会使其余 handle 失效（单实例契约，§8.3「唯一 Agent
    实例」）；如需并存须先改用实例级销毁。
    """

    runtime_engine: sa.Engine
    control_engine: sa.Engine
    dbos: DBOS
    datasource: SQLAlchemyDatasource
    client: DBOSClient
    executor_id: str
    _launched: bool = False
    _dbos_closed: bool = False
    _client_closed: bool = False
    _engines_disposed: bool = False

    @property
    def launched(self) -> bool:
        """宿主是否已 launch（启动隔离 needs-spike 通过前经本入口恒 False）。"""
        return self._launched

    @property
    def closed(self) -> bool:
        """三类资源是否全部关闭/释放（部分失败时为 False，供关闭门闩复核）。"""
        return self._dbos_closed and self._client_closed and self._engines_disposed

    def close(self) -> None:
        """按契约顺序关闭（按资源粒度幂等，失败可重试）。

        逐项记录成功：DBOS.destroy → Client.destroy → 双引擎 dispose。任一抛错
        时对应标记保持 False，``closed`` 为 False，调用方可重试（不得因一次失败
        就永久视为已关闭——否则关闭门闩会把「未关」误报成就绪，R21）。
        """
        if not self._dbos_closed:
            DBOS.destroy()
            self._dbos_closed = True
        if not self._client_closed:
            self.client.destroy()
            self._client_closed = True
        if not self._engines_disposed:
            self.runtime_engine.dispose()
            self.control_engine.dispose()
            self._engines_disposed = True


def assemble_agent_runtime(settings: AgentRuntimeSettings) -> AgentRuntimeHandle:
    """组装 Agent 运行时宿主（不 launch）。

    双迁移失败时异常原样传播，不创建任何引擎/宿主对象（§10.6：不 launch、不补投、
    保历史与原迁移状态）。迁移成功后的任一阶段失败，按**已取得对象**逆序收口
    （Client → DBOS → 双引擎，见 ``_close_partial_assembly``），原始异常不被清理
    异常掩盖（检视 R08）。

    Args:
        settings: 组装参数（URL/application_version/executor_id）。

    Returns:
        未 launch 的宿主句柄；启动须经 ``launch_agent_runtime``（隔离 spike 通过后）。

    Raises:
        AgentRuntimeMigrationError: 双迁移/授权/版本守卫失败。
    """
    run_agent_runtime_migrations(settings.system_database_url)

    executor_id = settings.executor_id or _new_executor_id()
    runtime_engine = _create_runtime_engine(settings.system_database_url)
    try:
        control_engine = _create_control_engine(settings.system_database_url)
    except BaseException:
        # 控制引擎未取得：runtime 引擎仍须收口（检视 R08）
        _dispose_quietly(runtime_engine)
        raise

    dbos: DBOS | None = None
    datasource: SQLAlchemyDatasource | None = None
    client: DBOSClient | None = None
    try:
        dbos = DBOS(
            config={
                "name": RUNTIME_DB_NAME,
                "system_database_url": settings.system_database_url,
                "system_database_engine": runtime_engine,
                "run_migrations": False,
                "enable_otlp": False,
                "max_executor_threads": DBOS_MAX_EXECUTOR_THREADS,
                "use_listen_notify": True,
                "sharding": False,
                "application_version": settings.application_version,
                "executor_id": executor_id,
                # 显式声明（§8.1 组装含此项；SDK 默认值相同，防隐式漂移）
                "dbos_system_schema": DBOS_SCHEMA,
            }
        )
        datasource = SQLAlchemyDatasource.create(
            database_url=settings.system_database_url,
            engine=runtime_engine,
            schema=AGENT_DS_SCHEMA,
            run_migrations=False,
        )
        client = DBOSClient(
            system_database_engine=control_engine,
            dbos_system_schema=DBOS_SCHEMA,
            use_listen_notify=False,
            retry_connection_errors=False,
            # 应用稳定名（HANDOFF §8.3 固定签名；§8.4 归属预检/取消按此过滤）
            application_name=RUNTIME_DB_NAME,
        )
    except BaseException:
        _close_partial_assembly(
            client=client,
            dbos=dbos,
            control_engine=control_engine,
            runtime_engine=runtime_engine,
        )
        raise
    assert dbos is not None and datasource is not None and client is not None
    return AgentRuntimeHandle(
        runtime_engine=runtime_engine,
        control_engine=control_engine,
        dbos=dbos,
        datasource=datasource,
        client=client,
        executor_id=executor_id,
    )


def launch_agent_runtime(handle: AgentRuntimeHandle) -> None:
    """启动宿主（HANDOFF §8.3/§8.4：listen_queues([]) 先于 launch 一次）。

    启动隔离（pending workflows 0 派发）为 needs-spike 验证门（§8.4）；本入口仅在
    隔离方案实证通过后由启动编排调用。
    """
    DBOS.listen_queues([])
    DBOS.launch()
    handle._launched = True  # 句柄内部状态


def _new_executor_id() -> str:
    """每进程唯一 executor epoch（HANDOFF §8.4 第 4 条）。"""
    return f"local-{uuid.uuid4().hex[:12]}"


def _create_runtime_engine(url: str) -> sa.Engine:
    """runtime 引擎：pool 4 / overflow 0 / timeout 3 / hide_parameters（§8.1 第 6 条）。

    会话级 statement/idle-in-transaction 超时见 ``_ENGINE_SESSION_OPTIONS``（注入
    引擎后 SDK 不兜底，HANDOFF §8.1）。
    """
    return _create_engine(
        url,
        pool_size=RUNTIME_ENGINE_POOL_SIZE,
        max_overflow=0,
        pool_timeout=3,
        hide_parameters=True,
        connect_args={
            "application_name": PSYCGRP_APPLICATION_NAME,
            "options": _ENGINE_SESSION_OPTIONS,
        },
    )


def _create_control_engine(url: str) -> sa.Engine:
    """control 引擎：pool 1 / overflow 0 / hide_parameters（§8.1 第 7 条，禁额外监听）。

    1 条而非 HANDOFF §8.1 的 2 条：7 条运行期增量预算须容纳 §8.4 的独立实例锁
    连接（检视 R12），故控制面 2 条中 1 条由实例锁占用、控制池保留 1 条。

    会话级超时同 runtime 引擎（``_ENGINE_SESSION_OPTIONS``）。
    """
    return _create_engine(
        url,
        pool_size=CONTROL_ENGINE_POOL_SIZE,
        max_overflow=0,
        pool_timeout=3,
        hide_parameters=True,
        connect_args={
            "application_name": PSYCGRP_APPLICATION_NAME,
            "options": _ENGINE_SESSION_OPTIONS,
        },
    )


def _create_engine(url: str, **engine_kwargs: Any) -> sa.Engine:
    """引擎构建 seam（单元测试注入点）。"""
    return sa.create_engine(url, **engine_kwargs)


def _dispose_quietly(engine: sa.Engine) -> None:
    """组装半途失败时的兜底释放：dispose 异常不掩盖原始异常。"""
    try:
        engine.dispose()
    # 兜底清理不得掩盖组装原始异常
    except Exception:  # R2_ALLOWED: 只捕获 Exception，CancelledError 不属其中
        pass


def _close_partial_assembly(
    *,
    client: DBOSClient | None,
    dbos: DBOS | None,
    control_engine: sa.Engine,
    runtime_engine: sa.Engine,
) -> None:
    """组装后续阶段失败时按已取得对象逆序收口（检视 R08）。

    所有权判定（按锁定版本 SDK 源码核实，非猜测）：

    - **Client**：``DBOSClient.__init__`` 注入 ``control_engine`` 且
      ``use_listen_notify=False``——不自建引擎、不起监听线程；构造抛错即对象未取得，
      失败连接回池由 ``control_engine.dispose()`` 承担。已取得则 ``destroy()``
      （注入引擎不被其释放，仍由下方 dispose 收口）。
    - **DBOS**：构造成功即占用**进程级全局单例**，未 launch 时无执行器/系统库连接/
      后台线程；不销毁会让再次组装复用旧实例（``DBOS(config)`` 返回既有实例并忽略
      新配置），故按单实例契约调用公开 ``DBOS.destroy()``（与
      ``AgentRuntimeHandle.close`` 同一契约）。
    - **Datasource**：``SQLAlchemyDatasource.create`` 注入 ``runtime_engine``
      （``created_engine=False``），自身不持连接/线程；SDK 将其登记进
      ``DBOSRegistry.datasources`` 且无公开反注册 API，登记项不持有连接、仅被
      rewind 使用，连接随 ``runtime_engine.dispose()`` 释放（不据此宣称连接泄漏）。

    清理异常一律不得掩盖组装原始异常（R2：只捕 Exception，取消照常传播）。
    """
    if client is not None:
        try:
            client.destroy()
        except Exception:  # R2_ALLOWED: 只捕获 Exception，CancelledError 不属其中
            pass
    if dbos is not None:
        try:
            DBOS.destroy()
        except Exception:  # R2_ALLOWED: 只捕获 Exception，CancelledError 不属其中
            pass
    _dispose_quietly(control_engine)
    _dispose_quietly(runtime_engine)


# ---------------------------------------------------------------------------
# owner-loop 桥（HANDOFF §8.2 段 1-2 / DD §10.6）
# ---------------------------------------------------------------------------

_T = TypeVar("_T")

OperationState = Literal["in_flight", "completed", "failed", "cancelled"]

#: 已结算 operation 条目的默认保留上限（检视 R17）：超出即按登记顺序淘汰最旧
#: 已结算条目并释放其 cf/结果/异常引用；在途条目不淘汰（数量由真实并发决定）。
_SETTLED_OPERATION_CACHE_LIMIT: Final = 256

#: 流通道内部哨兵：仅表示「关闭唤醒」，不是队列项（``OwnerStreamChannel``）
_WAIT_CLOSED: Final[object] = object()

#: 流通道内部哨兵：表示「阻塞中的 put 已完成」（非队列项）
_PUT_DONE: Final[object] = object()


class UnknownOperationError(RuntimeError):
    """按稳定 operation 身份观察时未登记（明确拒绝，不静默等待）。"""

    def __init__(self, operation_id: str) -> None:
        super().__init__(f"operation not registered: {operation_id}")
        self.operation_id = operation_id


class OperationEvictedError(UnknownOperationError):
    """该稳定身份曾完成登记，但进程内条目已被有界缓存淘汰（检视 R17）。

    结果/异常引用已随条目释放，故不冒充原回执：调用方须按持久 operation 身份
    回读原事实，不得据内存缓存重发 IO（零新 IO）。继承 ``UnknownOperationError``
    以便「进程内已无该事实」的调用方统一分支，同时保留明确区分。
    """

    def __init__(self, operation_id: str, state: OperationState) -> None:
        RuntimeError.__init__(self, f"operation registration evicted ({state}): {operation_id}")
        self.operation_id = operation_id
        self.state = state


class ChannelClosedError(RuntimeError):
    """流通道已关闭后的生产（终止路径唤醒阻塞写者，§8.2 段 3）。"""


class BridgeSubmissionsClosedError(RuntimeError):
    """桥提交已冻结：关闭前置门闩开始后拒绝新增 owner IO。

    冻结只拒绝新提交；已提交（含尚未起跑）的 IO 仍按原契约完成，并由
    ``inflight_task_count`` 计入关闭门闩，故 ``ready`` 回执之后不会再起跑新 IO
    （检视 R09）。
    """

    def __init__(self) -> None:
        super().__init__("owner loop bridge submissions are closed")


@dataclass(frozen=True)
class OperationHandle:
    """operation 登记快照（state 为 start 时点事实，非实时投影）。"""

    operation_id: str
    state: OperationState


@dataclass
class _OperationEntry:
    """operation 登记条目（进程内事实，不代替持久 operation 与 owner fence）。"""

    operation_id: str
    cf: concurrent.futures.Future[Any]
    state: OperationState = "in_flight"


class OwnerLoopBridge:
    """owner-loop 桥（HANDOFF §8.2）。

    循环归属（§8.2 段 1）：owner loop（应用主循环）持有 Executor/Recorder/DAO/
    异步客户端等真实资源；DBOS loop 经本桥请求 owner 执行，不直接 await
    owner-loop 对象。

    提交与等候语义（§8.2 段 1）：

    - ``run_coroutine_threadsafe`` 提交——owner 回调（loop 内 ``ensure_future``）
      才创建实际 IO 任务；
    - ``await asyncio.shield(asyncio.wrap_future(...))`` 等候——waiter 取消只撤
      waiter（shield 实现仅解除回调，不取消 inner），owner 侧任务继续运行至
      完成（"wait 取消原 IO 继续结算"）；
    - 返回值纪律：factory 须返回跨线程可传的不可变事实（请求/事件/回执），不得
      返回 Engine/客户端/锁/Queue；
    - owner 内直接 await，本桥不使用阻塞 ``future.result()``。

    强引用（shield 文档：事件循环仅弱引用任务）：桥持在途任务强引用集合
    （``_tasks``），任务结束后移除。

    稳定 operation（§8.2 段 2）：``start_operation`` 以稳定身份幂等登记在途/
    完成任务，重复观察连接原任务；观察者取消只撤观察者；明确终止另走控制命令。
    进程内登记不代替持久 operation 及 owner fence——重启不据内存缓存重发。

    有界保留（检视 R17）：已结算条目按登记顺序最多保留 ``settled_cache_limit``
    条，超出淘汰并释放其 ``cf``/结果/异常引用（在途条目不淘汰）。被淘汰身份保留
    紧凑 tombstone，重复 start/observe 抛 ``OperationEvictedError``（零新 IO）；
    tombstone 同样有界，更早身份等同从未登记，须由调用方按持久 operation 回读。
    """

    def __init__(
        self,
        owner_loop: asyncio.AbstractEventLoop,
        *,
        settled_cache_limit: int = _SETTLED_OPERATION_CACHE_LIMIT,
    ) -> None:
        if settled_cache_limit < 1:
            raise ValueError("settled_cache_limit must be >= 1")
        self._owner_loop = owner_loop
        self._tasks: set[asyncio.Task[Any]] = set()
        self._operations: dict[str, _OperationEntry] = {}
        #: 已结算 operation 的保留上限（见类文档「有界保留」）。
        self._settled_cache_limit = settled_cache_limit
        #: 被淘汰身份的紧凑终态（有界；不持 cf/结果/异常引用）。
        self._evicted_operations: dict[str, OperationState] = {}
        self._registry_lock = threading.Lock()
        #: 已提交但尚未在 owner 循环起跑的提交数（与 ``_tasks`` 共同构成在途集合；
        #: 受 ``_registry_lock`` 保护，关闭门闩据此判定「已提交/在途」是否为 0）。
        self._pending_submissions = 0
        #: 提交冻结标志（关闭前置门闩置位后拒绝新提交）。
        self._submissions_frozen = False

    @classmethod
    def for_running_loop(cls) -> OwnerLoopBridge:
        """在 owner 事件循环内登记实际 loop 句柄（§8.2「在 owner loop 记录」）。"""
        return cls(asyncio.get_running_loop())

    # -- 跨线程提交 --------------------------------------------------------

    async def run_owner(self, factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
        """跨线程提交 owner 协程并等候完成（一次性提交，无登记）。

        factory 在 owner 循环内被调用（owner 回调才创建实际 IO 任务）；本协程被
        取消时仅撤 waiter，owner 侧任务继续运行至完成。

        Raises:
            BridgeSubmissionsClosedError: 提交已冻结（关闭门闩开始后拒绝新 IO）。
        """

        # factory 必须在 owner 循环内调用（§8.2 段 1「owner 回调才创建实际 IO
        # 任务」）：包装协程的 body 在 owner 循环执行，factory() 遂在 owner 线程
        # 求值；若直接在此处调用 factory()，协程对象会在调用线程创建。
        async def _bootstrap() -> _T:
            return await self._managed(factory())

        self._begin_submission()
        coro = _bootstrap()
        try:
            cf = asyncio.run_coroutine_threadsafe(coro, self._owner_loop)
        except BaseException:
            coro.close()
            self._end_pending_submission()
            raise
        return await asyncio.shield(self._wrap_concurrent(cf))

    async def observe_operation(self, operation_id: str) -> Any:
        """按稳定身份观察 operation（重复观察连接原任务；取消只撤观察者）。

        结果类型编译期未知（观察方自行约束其不可变事实类型），故返回 ``Any``。

        Raises:
            UnknownOperationError: 该身份未登记。
            OperationEvictedError: 该身份曾被登记，但条目已被有界缓存淘汰
                （结果/异常已释放，须回读持久 operation 事实）。
        """
        with self._registry_lock:
            entry = self._operations.get(operation_id)
            evicted_state = None if entry is not None else self._evicted_operations.get(operation_id)
        if entry is None:
            if evicted_state is not None:
                raise OperationEvictedError(operation_id, evicted_state)
            raise UnknownOperationError(operation_id)
        return await asyncio.shield(self._wrap_concurrent(entry.cf))

    def start_operation(
        self,
        operation_id: str,
        factory: Callable[[], Coroutine[Any, Any, _T]],
    ) -> OperationHandle:
        """登记并启动稳定 operation（幂等）。

        同身份已登记（在途/已完成/失败/取消）时返回原登记快照，不重复执行
        factory（同键同规范输入查原事实）；观察经 ``observe_operation`` 连接
        原任务。登记已因有界缓存淘汰时抛 ``OperationEvictedError``，不重发
        factory（零新 IO，须回读持久 operation 事实）。

        Raises:
            BridgeSubmissionsClosedError: 新身份在提交冻结后拒绝（已登记身份仍
                返回原快照，供关闭期间的观察连接）。
            OperationEvictedError: 该身份曾被登记，但条目已被有界缓存淘汰。
        """
        with self._registry_lock:
            entry = self._operations.get(operation_id)
            if entry is None:
                evicted_state = self._evicted_operations.get(operation_id)
                if evicted_state is not None:
                    raise OperationEvictedError(operation_id, evicted_state)
                if self._submissions_frozen:
                    raise BridgeSubmissionsClosedError()
                self._pending_submissions += 1
                coro = self._run_operation(operation_id, factory)
                try:
                    cf = asyncio.run_coroutine_threadsafe(coro, self._owner_loop)
                except BaseException:
                    coro.close()
                    self._pending_submissions -= 1
                    raise
                entry = _OperationEntry(operation_id=operation_id, cf=cf)
                self._operations[operation_id] = entry
                self._prune_settled_locked()
        return OperationHandle(operation_id=operation_id, state=entry.state)

    def freeze_submissions(self) -> bool:
        """冻结新提交（关闭前置门闩第一步，幂等）。

        返回是否为本次调用完成冻结（``False`` 表示此前已冻结）。冻结与提交在
        ``_registry_lock`` 上串行，故冻结后读到的在途数已覆盖「已提交未起跑」；
        冻结后 ``run_owner`` / ``start_operation`` 的新身份一律拒绝（检视 R09）。
        """
        with self._registry_lock:
            if self._submissions_frozen:
                return False
            self._submissions_frozen = True
            return True

    def inflight_task_count(self) -> int:
        """当前在途 owner 任务数（关闭门闩的「实际 IO 未收敛」判据）。

        统计桥登记的任务（``run_owner``/``start_operation`` 主体）与已提交但
        尚未在 owner 循环起跑的提交；已完成任务在 ``finally`` 中移除。须在
        owner 循环上下文读取（与任务登记同线程）。
        """

        with self._registry_lock:
            pending = self._pending_submissions
        return len(self._tasks) + pending

    # -- 内部 ---------------------------------------------------------------

    def _begin_submission(self) -> None:
        """登记一次提交（冻结后拒绝；与冻结同锁串行）。"""
        with self._registry_lock:
            if self._submissions_frozen:
                raise BridgeSubmissionsClosedError()
            self._pending_submissions += 1

    def _end_pending_submission(self) -> None:
        """提交已在 owner 循环起跑（任务入 ``_tasks`` 后调用，避免计数空窗）。"""
        with self._registry_lock:
            self._pending_submissions -= 1

    async def _managed(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """run_owner 内层：登记在途任务强引用，结束移除。"""
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        self._end_pending_submission()
        try:
            return await coro
        finally:
            self._tasks.discard(task)

    async def _run_operation(
        self,
        operation_id: str,
        factory: Callable[[], Coroutine[Any, Any, _T]],
    ) -> _T:
        """operation 主体（owner loop 内执行）：factory 结果/异常/取消如实结算。"""
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        self._end_pending_submission()
        try:
            result = await factory()
        except asyncio.CancelledError:
            self._settle_operation(operation_id, "cancelled")
            raise
        except BaseException:
            self._settle_operation(operation_id, "failed")
            raise
        else:
            self._settle_operation(operation_id, "completed")
            return result
        finally:
            self._tasks.discard(task)

    def _settle_operation(self, operation_id: str, state: OperationState) -> None:
        with self._registry_lock:
            entry = self._operations.get(operation_id)
            if entry is not None:
                entry.state = state
            self._prune_settled_locked()

    def _prune_settled_locked(self) -> None:
        """淘汰最旧的已结算条目，保持在途条目与有界内存（检视 R17）。

        调用方须持有 ``_registry_lock``。只淘汰已结算（completed/failed/cancelled）
        条目：在途条目的结果/异常尚未产出，淘汰会让 ``start_operation`` 重发 IO。
        淘汰时其 ``cf``（连同结果/异常引用）随条目一起释放，身份以紧凑 tombstone
        保留（``OperationEvictedError``，零新 IO）。
        """
        settled = sum(1 for entry in self._operations.values() if entry.state != "in_flight")
        if settled <= self._settled_cache_limit:
            return
        for operation_id in list(self._operations):
            if settled <= self._settled_cache_limit:
                break
            entry = self._operations[operation_id]
            if entry.state == "in_flight":
                continue
            del self._operations[operation_id]
            self._remember_tombstone_locked(operation_id, entry.state)
            settled -= 1

    def _remember_tombstone_locked(self, operation_id: str, state: OperationState) -> None:
        """登记被淘汰身份的紧凑终态（有界：超出上限按登记顺序丢弃最旧项）。

        被丢弃的身份等同「从未登记」——持久 operation 去重/回读是调用方责任
        （进程内登记不代替持久 operation，§8.2 段 2）。
        """
        self._evicted_operations[operation_id] = state
        while len(self._evicted_operations) > self._settled_cache_limit:
            del self._evicted_operations[next(iter(self._evicted_operations))]

    @staticmethod
    def _wrap_concurrent(cf: concurrent.futures.Future[Any]) -> asyncio.Future[Any]:
        """concurrent future → 本循环 future（shield 的 inner 必须是 future 本身）。

        持消费回调防 waiter 取消后「exception was never retrieved」噪声（shield
        取消路径会解除自身回调）。
        """
        wrapped = asyncio.wrap_future(cf)
        wrapped.add_done_callback(_consume_settled)
        return wrapped


def _consume_settled(fut: asyncio.Future[Any]) -> None:
    """标记结算异常已被检索（结果/异常/取消三态均安全）。"""
    if not fut.cancelled():
        fut.exception()


# ---------------------------------------------------------------------------
# 跨 loop 流通道（HANDOFF §8.2 段 3）
# ---------------------------------------------------------------------------


class OwnerStreamChannel[T]:
    """owner-loop 有界流通道（§8.2 段 3）。

    - 通道（Queue/Event）在 owner loop 内创建并绑定；``put``/``get``/``close``
      仅限 owner 循环调用（直接跨 loop 访问明确拒绝）；
    - DBOS 消费者经 ``bridge_get`` 桥接 ``get``（不跨 loop 直接 await Queue）；
    - 有界（maxsize>=1），满时真实阻塞生产；关闭唤醒阻塞读写；
    - 终止顺序：``close``（唤醒）→ 取消并 join 生产者（调用者持有生产任务）；
      join 后 CancelledError 继续传播（R2）。

    通道为进程内对象；关闭后已入队项仍可取尽（取尽后返回 None），错误/结算
    事实由上层以事件或异常承载（本原语不携带语义载荷）。
    """

    def __init__(self, bridge: OwnerLoopBridge, *, maxsize: int) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("OwnerStreamChannel must be created in the owner event loop") from exc
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1 (bounded channel)")
        self._bridge = bridge
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._closed_event = asyncio.Event()  # R11_ALLOWED: __init__ 强制 get_running_loop，实例只在 owner 循环内使用
        self._closed = False

    # -- owner 循环侧 -------------------------------------------------------

    async def put(self, item: T) -> None:
        """投递事件（owner 循环内）：满时真实阻塞；关闭后明确拒绝。"""
        self._check_owner_loop()
        if self._closed:
            raise ChannelClosedError("channel closed")
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                pass
            if self._closed:
                raise ChannelClosedError("channel closed while producer blocked")
            outcome = await self._wait_put_or_close(item)
            if outcome is _WAIT_CLOSED:
                raise ChannelClosedError("channel closed while producer blocked")
            # _PUT_DONE：内层 put 任务已投递该项，直接返回（再 put_nowait 会重复投递）
            return

    async def get(self) -> T | None:
        """取事件（owner 循环内）：先取尽已入队项，关闭且取尽后返回 None。"""
        self._check_owner_loop()
        while True:
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            if self._closed:
                return None
            item = await self._wait_get_or_close()
            if item is not _WAIT_CLOSED:
                return cast(T, item)
            # 关闭唤醒：回到顶部重查残留项，再按关闭返回

    def close(self) -> None:
        """关闭通道（owner 循环内调用）：唤醒阻塞读写；幂等。"""
        self._check_owner_loop()
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()

    # -- 跨 loop 消费侧 -----------------------------------------------------

    async def bridge_get(self) -> T | None:
        """DBOS 消费者跨 loop 取事件（§8.2：经桥提交，不跨 loop await Queue）。

        消费者取消只撤 waiter（shield 语义）；owner 侧取事件任务继续至取得
        事件或关闭唤醒。waiter 已取消时取到的事件不回投（流已中止——终止路径
        经 close + join 生产者收敛）。
        """
        return await self._bridge.run_owner(self.get)

    # -- 内部 ---------------------------------------------------------------

    def _check_owner_loop(self) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("owner-loop object accessed outside its event loop") from exc
        if current is not self._loop:
            raise RuntimeError("owner-loop object accessed from a foreign loop")

    async def _wait_put_or_close(self, item: T) -> object:
        """满时等待空位或关闭唤醒（先完成者生效；二者同时完成以投递优先）。"""
        put_task = asyncio.ensure_future(self._queue.put(item))
        closed_task = asyncio.ensure_future(self._closed_event.wait())
        try:
            await asyncio.wait({put_task, closed_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (put_task, closed_task):
                if not task.done():
                    task.cancel()
        # done() 而非 wait 返回集：put 可能在 wait 返回后、此处判定前完成投递
        if put_task.done() and not put_task.cancelled():
            put_task.result()
            return _PUT_DONE
        return _WAIT_CLOSED

    async def _wait_get_or_close(self) -> Any:
        """空时等待事件或关闭唤醒（先完成者生效；二者同时完成以取件优先）。"""
        get_task = asyncio.ensure_future(self._queue.get())
        closed_task = asyncio.ensure_future(self._closed_event.wait())
        try:
            await asyncio.wait({get_task, closed_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (get_task, closed_task):
                if not task.done():
                    task.cancel()
        # done() 而非 wait 返回集：取件可能已投递但完成步晚于 close 完成
        if get_task.done() and not get_task.cancelled():
            return get_task.result()
        return _WAIT_CLOSED


# ---------------------------------------------------------------------------
# 关闭前置门闩（HANDOFF §8.5：prepare_close 在 watchdog/do_cleanup/destroy/
# 换配置之前，返回带原关闭命令身份、ready、阻塞原因与 unknown 关联的回执）
# ---------------------------------------------------------------------------

#: 阻塞原因 wire 值（稳定；组合为 tuple，失败可核、不隐藏）。
CLOSE_BLOCK_DISPATCH_NOT_CLOSED: Final = "dispatch_not_closed"
CLOSE_BLOCK_IO_IN_FLIGHT: Final = "io_in_flight"
CLOSE_BLOCK_FACTS_SAVE_FAILED: Final = "close_facts_save_failed"
CLOSE_BLOCK_FACTS_NOT_SAVED: Final = "close_facts_not_saved"
CLOSE_BLOCK_RUNTIME_CLOSE_FAILED: Final = "runtime_close_failed"
CLOSE_BLOCK_RUNTIME_CLOSE_INCOMPLETE: Final = "runtime_close_incomplete"
CLOSE_BLOCK_RESIDUAL_IO: Final = "residual_io_unsettled"
CLOSE_BLOCK_CLOSE_IDENTITY_MISMATCH: Final = "close_identity_mismatch"


@dataclass(frozen=True)
class CloseCommandIdentity:
    """关闭命令身份（原命令，重试沿此身份；不把「已请求关闭」当就绪）。"""

    user_scope: str
    close_command_id: str


def _same_close_identity(left: CloseCommandIdentity, right: CloseCommandIdentity) -> bool:
    """完整身份比较（user_scope + close_command_id；跨域同 command ID 不相等）。"""
    return left.user_scope == right.user_scope and left.close_command_id == right.close_command_id


@dataclass(frozen=True)
class CloseFactOutcome:
    """结束/unknown 保存结果（``unknown_associations`` 为仍需保留的未知关联）。"""

    saved: bool
    unknown_associations: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstanceGuardEvidence:
    """唯一执行锁证据：``acquired`` 与 ``prior_writer_exited`` 都为真才可继续。"""

    acquired: bool
    prior_writer_exited: bool
    detail: str


class InstanceGuard(Protocol):
    """启动唯一执行锁端口（应用单实例机制；拿锁成功即证明旧写者已退出）。"""

    async def acquire(self) -> InstanceGuardEvidence: ...

    def release(self) -> None: ...


@dataclass(frozen=True)
class CloseReadinessReceipt:
    """关闭就绪回执（不可变）。

    ``ready=True`` 须同时满足：派发已关闭、桥登记的在途 owner 任务为 0（含
    已提交但尚未起跑的提交）、结束/unknown 已可靠保存、DBOS/Client 已关闭且
    运行库 Engine 已释放。
    ``ready=False`` 时本类不触碰 Recorder/业务 Engine/内置 PG（由调用方保持
    旧配置与恢复能力，HANDOFF §8.5）。
    """

    command: CloseCommandIdentity
    ready: bool
    blocking_reasons: tuple[str, ...]
    unknown_associations: tuple[str, ...]
    inflight_task_count: int
    checked_at: str
    residual_io: tuple[str, ...] = ()
    residual_io_probed: bool = False


class DispatchGate(Protocol):
    """应用派发门闩（关闭新派发；幂等）。"""

    def close_dispatch(self) -> bool:
        """关闭派发；返回此刻是否已关闭（失败返回 False，不假装成功）。"""
        ...

    def dispatch_closed(self) -> bool:
        """派发是否已关闭。"""
        ...


class CloseFactSink(Protocol):
    """结束/unknown 持久化端口（服务层实现；门闩只核结果，不写业务事实）。"""

    async def save_end_or_unknown(self, command: CloseCommandIdentity) -> CloseFactOutcome: ...


#: 同步阻塞段执行器（默认受管线程池；测试注入直接执行）。
CloseBlockingRunner = Callable[[Callable[[], None]], Awaitable[None]]


def _thread_pool_runner() -> CloseBlockingRunner:
    """默认受管线程执行器（HANDOFF §10.6：destroy/dispose 不堵 UI）。"""

    from utils.thread_pool import TaskType, ThreadPoolManager

    async def run(fn: Callable[[], None]) -> None:
        await ThreadPoolManager().run_async(TaskType.IO, fn)

    return run


class AgentRuntime:
    """Agent 运行时关闭编排（HANDOFF §8.5 前置门闩）。

    由 app 层注入统一关闭编排，覆盖窗口关闭/断连/升级退出/正常退出/数据库
    切换；必须在 ``start_watchdog``、``do_cleanup``、窗口 destroy 或旧配置替换
    **之前**调用 ``prepare_close``。不得仅注册为 ShutdownCoordinator 普通动态
    cleanup callback（协调器在 critical 回调失败后仍继续清理）。

    关闭顺序（§8.5）：冻结桥提交 → 关派发 → 核 IO 退出 → 保存结束/unknown →
    关闭前重核 IO 集合 → DBOS/Client 关闭并释放运行库引擎 → 返回就绪回执。
    Recorder.aclose、业务引擎与内置 PG
    由调用方在就绪后按原协调顺序关闭，本类不触碰。
    """

    def __init__(
        self,
        *,
        handle: AgentRuntimeHandle,
        bridge: OwnerLoopBridge,
        dispatch_gate: DispatchGate,
        fact_sink: CloseFactSink,
        blocking_runner: CloseBlockingRunner | None = None,
        residual_io_probe: Callable[[], tuple[str, ...]] | None = None,
        instance_guard: InstanceGuard | None = None,
    ) -> None:
        self._handle = handle
        self._bridge = bridge
        self._dispatch_gate = dispatch_gate
        self._fact_sink = fact_sink
        self._blocking_runner = blocking_runner or _thread_pool_runner()
        #: 残余 IO 探针（SDK 流/MCP worker/同步事务等未走桥登记的收敛证据；
        #: 返回非空名称集即视为未收敛，门闩不 ready）。经桥登记的 owner 任务由
        #: ``inflight_task_count`` 覆盖；未登记的长期任务必须由调用方经此探针上报。
        self._residual_io_probe = residual_io_probe
        #: 唯一执行锁：就绪关闭完成后释放，供同进程数据库切换/重启带内接管。
        self._instance_guard = instance_guard
        self._receipt: CloseReadinessReceipt | None = None
        #: 进行中的关闭链（同一时刻至多一条；并发调用共享其结果，检视 R10）。
        self._close_task: asyncio.Task[CloseReadinessReceipt] | None = None
        #: 进行中关闭链的原命令完整身份（异身份并发调用不得复用他人关闭链）。
        self._close_chain_command: CloseCommandIdentity | None = None

    async def prepare_close(self, command: CloseCommandIdentity) -> CloseReadinessReceipt:
        """关闭前置门闩（幂等；``ready=True`` 后按原完整身份重试返回原回执）。

        身份为 ``(user_scope, close_command_id)`` 全量比较：跨 user_scope 的同
        command ID 不得复用旧回执或他人进行中的关闭链（检视 R10）。并发调用经
        单条关闭链串行执行阶段——同身份调用共享该链结果，异身份调用立即得到
        身份不匹配回执，不重复保存事实/关闭资源。

        ``CancelledError`` 继续传播（R2）；普通异常转成阻塞原因并保持资源
        可用（可重试），不进入清理/强退路径。
        """

        if not command.user_scope or not command.close_command_id:
            raise ValueError("close command identity must be non-empty")
        if self._receipt is not None and self._receipt.ready:
            if _same_close_identity(command, self._receipt.command):
                # 原关闭已完成：按原完整身份重试返回原回执（不重复 destroy）
                return self._receipt
            # 不同身份不得复用旧回执冒充本次关闭（返回原身份与显式阻塞码）
            return self._make_receipt(
                self._receipt.command,
                reasons=[CLOSE_BLOCK_CLOSE_IDENTITY_MISMATCH],
                unknown=self._receipt.unknown_associations,
                inflight=0,
                residual=self._receipt.residual_io,
            )

        task = self._close_task
        if task is not None and not task.done():
            # 已有关闭链在途：同身份并发调用共享该链（取消只撤 waiter）；异身份
            # （含跨 user_scope 同 command ID）不得搭便车，立即返回身份不匹配。
            chain_command = self._close_chain_command
            if chain_command is None or not _same_close_identity(command, chain_command):
                return self._make_receipt(
                    chain_command or command,
                    reasons=[CLOSE_BLOCK_CLOSE_IDENTITY_MISMATCH],
                    unknown=(),
                    inflight=self._bridge.inflight_task_count(),
                )
            return await asyncio.shield(task)

        # 新建关闭链：同步登记（此前无 await，故并发调用不会各自建链）。
        task = asyncio.create_task(self._run_close_chain(command))
        self._close_task = task
        self._close_chain_command = command
        return await asyncio.shield(task)

    async def _run_close_chain(self, command: CloseCommandIdentity) -> CloseReadinessReceipt:
        """关闭链主体（单条串行；就绪回执在此落定，供并发同身份调用共享）。"""

        reasons: list[str] = []
        # 冻结新提交先于在途核验：冻结与提交在桥内同一把锁上串行，故此处读到的
        # 在途数已覆盖「已提交未起跑」（检视 R09）。
        self._bridge.freeze_submissions()
        if not self._dispatch_gate.close_dispatch():
            reasons.append(CLOSE_BLOCK_DISPATCH_NOT_CLOSED)
        inflight = self._bridge.inflight_task_count()
        if inflight:
            reasons.append(CLOSE_BLOCK_IO_IN_FLIGHT)
        residual = self._residual_io_probe() if self._residual_io_probe is not None else ()
        if residual:
            reasons.append(f"{CLOSE_BLOCK_RESIDUAL_IO}:{','.join(residual)}")
        if reasons:
            return self._make_receipt(command, reasons=reasons, unknown=(), inflight=inflight, residual=residual)

        try:
            outcome = await self._fact_sink.save_end_or_unknown(command)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._make_receipt(
                command, reasons=[CLOSE_BLOCK_FACTS_SAVE_FAILED], unknown=(), inflight=inflight, residual=residual
            )
        if not outcome.saved:
            return self._make_receipt(
                command,
                reasons=[CLOSE_BLOCK_FACTS_NOT_SAVED],
                unknown=outcome.unknown_associations,
                inflight=inflight,
                residual=residual,
            )

        # 保存事实是 await 边界：期间可能有已提交任务在 owner 循环起跑。关闭资源
        # 前重核在途集合（含新起跑与残余 IO），未收敛即不关闭、不报就绪（检视 R09）。
        inflight = self._bridge.inflight_task_count()
        residual = self._residual_io_probe() if self._residual_io_probe is not None else ()
        recheck: list[str] = []
        if inflight:
            recheck.append(CLOSE_BLOCK_IO_IN_FLIGHT)
        if residual:
            recheck.append(f"{CLOSE_BLOCK_RESIDUAL_IO}:{','.join(residual)}")
        if recheck:
            return self._make_receipt(
                command,
                reasons=recheck,
                unknown=outcome.unknown_associations,
                inflight=inflight,
                residual=residual,
            )

        try:
            await self._blocking_runner(self._handle.close)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._make_receipt(
                command,
                reasons=[CLOSE_BLOCK_RUNTIME_CLOSE_FAILED],
                unknown=outcome.unknown_associations,
                inflight=inflight,
                residual=residual,
            )
        if not self._handle.closed:
            # 部分资源未关闭/未释放（如 destroy 中途失败）：不得报就绪（R21）
            return self._make_receipt(
                command,
                reasons=[CLOSE_BLOCK_RUNTIME_CLOSE_INCOMPLETE],
                unknown=outcome.unknown_associations,
                inflight=inflight,
                residual=residual,
            )

        receipt = self._make_receipt(
            command, reasons=[], unknown=outcome.unknown_associations, inflight=inflight, residual=residual
        )
        self._receipt = receipt
        if self._instance_guard is not None:
            # 就绪（DBOS/Client 已关、运行库引擎已释放）后才释放唯一执行锁
            self._instance_guard.release()
        return receipt

    def _make_receipt(
        self,
        command: CloseCommandIdentity,
        *,
        reasons: list[str],
        unknown: tuple[str, ...],
        inflight: int,
        residual: tuple[str, ...] = (),
    ) -> CloseReadinessReceipt:
        return CloseReadinessReceipt(
            command=command,
            ready=not reasons,
            blocking_reasons=tuple(reasons),
            unknown_associations=unknown,
            inflight_task_count=inflight,
            checked_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            residual_io=residual,
            residual_io_probed=self._residual_io_probe is not None,
        )


__all__ = [
    "AGENT_DS_SCHEMA",
    "CLOSE_BLOCK_DISPATCH_NOT_CLOSED",
    "CLOSE_BLOCK_FACTS_NOT_SAVED",
    "CLOSE_BLOCK_FACTS_SAVE_FAILED",
    "CLOSE_BLOCK_IO_IN_FLIGHT",
    "CLOSE_BLOCK_RUNTIME_CLOSE_FAILED",
    "DBOS_SCHEMA",
    "AgentRuntime",
    "AgentRuntimeHandle",
    "AgentRuntimeMigrationError",
    "AgentRuntimeSettings",
    "BridgeSubmissionsClosedError",
    "ChannelClosedError",
    "CloseBlockingRunner",
    "CloseCommandIdentity",
    "CloseFactOutcome",
    "CloseFactSink",
    "CloseReadinessReceipt",
    "DispatchGate",
    "OperationEvictedError",
    "OperationHandle",
    "OperationState",
    "OwnerLoopBridge",
    "OwnerStreamChannel",
    "UnknownOperationError",
    "assemble_agent_runtime",
    "launch_agent_runtime",
]
