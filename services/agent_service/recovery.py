"""启动隔离与手动恢复（HANDOFF §8.4；设计 §10.6 启动隔离/恢复合同行）。

职责边界：

- **启动隔离**：在 ``launch`` 之前，用 ``Client`` 读取「应用登记集合 ∪ 原生在途
  集合」→ **先固定快照**（分页完成后才取消，避免边取消边漏项）→ 保存隔离命令
  与原状态 → 关闭应用派发门闩 → 精确取消（``cancel_children=False``）→ 复查
  全部成功。任一环节失败/未知都返回 ``ready=False`` 且不进入 launch（
  ``listen_queues([])`` 不是禁用默认恢复的开关，HANDOFF §8.4）。
- **手动恢复**：只对用户明确选择的精确集合授予本进程 ``owner_epoch``/
  ``run_epoch`` 并逐个 ``resume``；A 不动 B，用户终止不复活，不可恢复终态明确
  拒绝（``blocked``），不静默跳过。

明确不做：

- 不 ``launch``（启动编排在隔离 ``ready=True`` 后另行调用）；
- 不判定业务终态、不修改业务事实（除门闩/代次字段，均由 DAO 事务落库）；
- 不吞 ``asyncio.CancelledError``（R2）：取消继续传播，只有普通异常转成
  ``ready=False`` 的阻塞原因（失败可核，不假装成功）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

import sqlalchemy as sa
from dbos import WorkflowStatus, WorkflowStatusString

from core.agent_types import (
    DispatchCloseFact,
    DispatchCloseRequest,
    RecoveryExecution,
    RecoveryGrantFact,
    RecoveryGrantRequest,
    StartupIsolationRecord,
    json_doc,
    stable_id,
)
from services.agent_service.runtime import (
    RUNTIME_DB_NAME,
    AgentRuntimeSettings,
    InstanceGuard,
    InstanceGuardEvidence,
    assemble_agent_runtime,
    launch_agent_runtime,
)

#: 启动隔离的「在途」原生状态（HANDOFF §8.4 第 3 条：PENDING/ENQUEUED/DELAYED）。
STARTUP_ACTIVE_STATUSES: Final[tuple[str, ...]] = (
    WorkflowStatusString.PENDING.value,
    WorkflowStatusString.ENQUEUED.value,
    WorkflowStatusString.DELAYED.value,
)

#: 原生终态（取消复查只需确认不再处于在途状态）。
#: 应用自有 workflow 名（D-02 发布名；隔离按名 + 应用版本过滤）。
STARTUP_WORKFLOW_NAMES: Final[tuple[str, ...]] = ("agent_research", "agent_leaf")

#: 分页/批量上限（固定集合后再取消的关键：先读满全部分页）。
ISOLATION_PAGE_SIZE: Final[int] = 100

#: 应用登记集合里原生行缺失时的快照标记（如实记录「无原生行」，不当作成功态）。
ABSENT_NATIVE_STATE: Final[str] = "absent"


@dataclass(frozen=True)
class StartupIsolationReceipt:
    """启动隔离回执（不可变；同时作为隔离命令的回执载荷保存）。

    ``ready=True`` 表示：隔离命令与原状态已保存、应用派发门闩已关闭、快照内
    全部原生 workflow 已不在途。``blocking_reasons`` 为阻塞码（稳定 wire 值），
    ``unverified_workflow_ids`` 为复查仍未见终态的原生 ID。
    """

    command_id: str
    user_scope: str
    ready: bool
    registered_workflow_ids: tuple[str, ...]
    isolated_workflow_ids: tuple[str, ...]
    original_states: tuple[tuple[str, str], ...]
    blocking_reasons: tuple[str, ...]
    unverified_workflow_ids: tuple[str, ...]

    def to_wire(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "user_scope": self.user_scope,
            "ready": self.ready,
            "registered_workflow_ids": list(self.registered_workflow_ids),
            "isolated_workflow_ids": list(self.isolated_workflow_ids),
            "original_states": [list(item) for item in self.original_states],
            "blocking_reasons": list(self.blocking_reasons),
            "unverified_workflow_ids": list(self.unverified_workflow_ids),
        }


@dataclass(frozen=True)
class ManualRecoveryReceipt:
    """手动恢复回执（不可变）：只报精确集合的 resume 结果与被拒原因。"""

    user_scope: str
    owner_epoch: int
    run_epoch: int
    granted_execution_ids: tuple[str, ...]
    resumed_workflow_ids: tuple[str, ...]
    blocked: tuple[tuple[str, str], ...]
    resume_failures: tuple[tuple[str, str], ...]
    blocking_reasons: tuple[str, ...]
    ready: bool


class WorkflowControlClient(Protocol):
    """原生控制面（``DBOSClient`` 实例；测试可注入假实现）。"""

    async def list_workflows_async(
        self,
        *,
        workflow_ids: list[str] | None = None,
        status: str | list[str] | None = None,
        name: str | list[str] | None = None,
        application_name: str | list[str] | None = None,
        app_version: str | list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        load_input: bool = True,
        load_output: bool = True,
    ) -> list[WorkflowStatus]: ...

    async def cancel_workflows_async(self, workflow_ids: list[str], *, cancel_children: bool = False) -> None: ...

    async def resume_workflows_async(self, workflow_ids: list[str]) -> Any: ...


class RecoveryRegistry(Protocol):
    """应用登记面（``AgentDao``；按 §9.2/§9.5 事务语义实现）。"""

    async def list_recovery_executions(self, user_scope: str) -> tuple[RecoveryExecution, ...]: ...

    async def mark_dispatch_closed(self, request: DispatchCloseRequest) -> DispatchCloseFact: ...

    async def save_startup_isolation(self, record: StartupIsolationRecord) -> str: ...

    async def finalize_startup_isolation(
        self, *, user_scope: str, isolation_command_id: str, receipt_payload: str
    ) -> None: ...

    async def grant_recovery_epoch(self, request: RecoveryGrantRequest) -> RecoveryGrantFact: ...


async def run_startup_isolation(
    *,
    user_scope: str,
    isolation_id: str,
    target_id: str,
    client: WorkflowControlClient,
    registry: RecoveryRegistry,
    application_name: str,
    app_version: str,
    workflow_names: Sequence[str] = STARTUP_WORKFLOW_NAMES,
) -> StartupIsolationReceipt:
    """执行启动隔离（HANDOFF §8.4 第 2-3 条），返回不可变回执。

    顺序固定：读登记集合 → 原生分页快照（读满才停）→ 固定并保存隔离命令与
    原状态 → 关闭应用派发门闩 → 精确取消 → 复查。任一步普通异常即
    ``ready=False`` 并停止后续步骤（不取消、不 launch）；``CancelledError``
    继续传播（R2）。

    Args:
        user_scope: 用户域（与 DAO 同域校验）。
        isolation_id: 本次启动隔离命令的稳定 ID（客户端命令键）。
        target_id: 命令目标身份（应用实例，如运行库名 + 启动批次）。
        client: 原生控制面（禁监听/禁无限重试的 ``DBOSClient``）。
        registry: 应用登记面（``AgentDao``）。
        application_name: 运行库应用稳定名（HANDOFF §8.3 固定签名）。
        app_version: 真实工作流代码版本（隔离只处理同版本在途集合）。
        workflow_names: 应用自有 workflow 名（默认 D-02 发布名）。

    Returns:
        隔离回执；``ready=False`` 时调用方不得 launch。
    """

    for value, name in (
        (user_scope, "user_scope"),
        (isolation_id, "isolation_id"),
        (target_id, "target_id"),
        (application_name, "application_name"),
        (app_version, "app_version"),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be non-empty text")

    command_id = stable_id("command", [user_scope, target_id, isolation_id])

    try:
        registered = await registry.list_recovery_executions(user_scope)
    except Exception as exc:  # 登记读取失败 = 无法证明隔离集合完整，不得取消/launch
        return _blocked_receipt(command_id, user_scope, "registry_read_failed", exc)
    registered_ids = tuple(sorted({item.workflow_id for item in registered if item.workflow_id is not None}))

    try:
        native_states = await _snapshot_native_workflows(
            client,
            application_name=application_name,
            app_version=app_version,
            workflow_names=workflow_names,
        )
    except Exception as exc:  # 分页失败：集合不完整（§8.4 第 3 条）
        return _blocked_receipt(command_id, user_scope, "native_listing_failed", exc)

    snapshot_ids = tuple(sorted(set(native_states) | set(registered_ids)))
    original_states = tuple(
        (workflow_id, native_states.get(workflow_id, ABSENT_NATIVE_STATE)) for workflow_id in snapshot_ids
    )
    snapshot_doc = json_doc(
        {
            "workflow_ids": list(snapshot_ids),
            "registered_workflow_ids": list(registered_ids),
            "original_states": [list(item) for item in original_states],
        }
    )

    try:
        saved_id = await registry.save_startup_isolation(
            StartupIsolationRecord(
                user_scope=user_scope,
                client_command_id=isolation_id,
                target_id=target_id,
                snapshot=snapshot_doc,
                receipt_payload=json_doc({"phase": "prepared"}),
            )
        )
    except Exception as exc:  # 原状态未保存：不取消（否则崩溃后无从核对原关联）
        return _blocked_receipt(command_id, user_scope, "isolation_record_unsaved", exc)
    if saved_id != command_id:  # 身份派生漂移属实现缺陷，按未就绪处理
        return _blocked_receipt(command_id, user_scope, "isolation_command_identity_mismatch", None)

    try:
        await registry.mark_dispatch_closed(DispatchCloseRequest(user_scope=user_scope, workflow_ids=registered_ids))
    except Exception as exc:  # 应用派发门闩未关闭：不取消（§10.6「并核应用派发fence」）
        return await _finalize_blocked(
            registry,
            command_id=command_id,
            user_scope=user_scope,
            reason="dispatch_fence_unsaved",
            exc=exc,
            registered_ids=registered_ids,
            snapshot_ids=snapshot_ids,
            original_states=original_states,
        )

    if snapshot_ids:
        try:
            await client.cancel_workflows_async(list(snapshot_ids), cancel_children=False)
        except Exception as exc:  # 取消失败/未知：不得 launch
            return await _finalize_blocked(
                registry,
                command_id=command_id,
                user_scope=user_scope,
                reason="cancel_failed",
                exc=exc,
                registered_ids=registered_ids,
                snapshot_ids=snapshot_ids,
                original_states=original_states,
            )

    try:
        verified_states = await _list_states_by_id(client, snapshot_ids)
    except Exception as exc:  # 复查失败：无法证明 0 在途
        return await _finalize_blocked(
            registry,
            command_id=command_id,
            user_scope=user_scope,
            reason="cancel_verification_failed",
            exc=exc,
            registered_ids=registered_ids,
            snapshot_ids=snapshot_ids,
            original_states=original_states,
        )

    active_ids = tuple(
        workflow_id for workflow_id in snapshot_ids if verified_states.get(workflow_id) in STARTUP_ACTIVE_STATUSES
    )
    receipt = StartupIsolationReceipt(
        command_id=command_id,
        user_scope=user_scope,
        ready=not active_ids,
        registered_workflow_ids=registered_ids,
        isolated_workflow_ids=snapshot_ids,
        original_states=original_states,
        blocking_reasons=() if not active_ids else ("cancel_unverified",),
        unverified_workflow_ids=active_ids,
    )
    return await _finalize_receipt(registry, receipt)


async def resume_selected_workflows(
    *,
    user_scope: str,
    execution_ids: Sequence[str],
    owner_epoch: int,
    run_epoch: int,
    application_version: str,
    client: WorkflowControlClient,
    registry: RecoveryRegistry,
) -> ManualRecoveryReceipt:
    """手动恢复用户明确选择的精确集合（HANDOFF §8.4 第 5 条）。

    先由 DAO 事务核定（不可恢复终态/未核状态/用户终止/无活动轮/版本不兼容/
    未收口 operation/代次不前进 → ``blocked``，其 workflow 不返回），再逐个
    ``resume`` 返回的精确原生集合；未选集合（B）与用户终止轮次保持隔离。
    单个 resume 失败如实进 ``resume_failures`` 并使 ``ready=False``；全部候选被拒
    （零授予）同样 ``ready=False``（不把「什么都没恢复」当恢复成功）。
    """

    grant = await registry.grant_recovery_epoch(
        RecoveryGrantRequest(
            user_scope=user_scope,
            execution_ids=tuple(execution_ids),
            owner_epoch=owner_epoch,
            run_epoch=run_epoch,
            application_version=application_version,
        )
    )
    resumed: list[str] = []
    failures: list[tuple[str, str]] = []
    for workflow_id in grant.workflow_ids:
        try:
            await client.resume_workflows_async([workflow_id])
        except Exception as exc:
            failures.append((workflow_id, type(exc).__name__))
        else:
            resumed.append(workflow_id)
    blocking_reasons: list[str] = []
    if not grant.granted_execution_ids:
        blocking_reasons.append("no_granted_execution")
    return ManualRecoveryReceipt(
        user_scope=user_scope,
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
        granted_execution_ids=grant.granted_execution_ids,
        resumed_workflow_ids=tuple(resumed),
        blocked=grant.blocked,
        resume_failures=tuple(failures),
        blocking_reasons=tuple(blocking_reasons),
        ready=not failures and not blocking_reasons,
    )


#: 单实例执行锁键（PG advisory lock；会话断开/进程崩溃自动释放）。
INSTANCE_LOCK_KEY: Final[int] = 0x7174726D

#: 同步阻塞段执行器（返回函数值；默认受管线程池）。
BlockingRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


class AdvisoryLockInstanceGuard:
    """PG advisory lock 单实例执行锁（HANDOFF §8.4 第 1 条最小实现）。

    独立**单连接**引擎持有会话级 advisory lock：旧实例正常退出或崩溃时 PG
    自动释放锁，故 ``pg_try_advisory_lock`` 成功即同时证明「获得唯一执行权」
    与「旧写者已退出/可安全接管」（不是用 CANCELLED 冒充退出，§8.6）。

    连接预算（§8.1「4+1+2 及原应用池」）：本持久连接即其中 ``+1``（实例锁），
    不新增第 8 条；连接仅在启动后常驻、关闭就绪后 release。
    """

    def __init__(self, system_database_url: str, *, blocking_runner: BlockingRunner | None = None) -> None:
        self._system_database_url = system_database_url
        self._blocking_runner = blocking_runner or _default_blocking_runner
        self._engine: Any = None
        self._connection: Any = None

    async def acquire(self) -> InstanceGuardEvidence:
        if self._connection is not None:
            return InstanceGuardEvidence(acquired=True, prior_writer_exited=True, detail="already-held")

        def _try_lock() -> bool:
            self._engine = sa.create_engine(
                _sync_database_url(self._system_database_url),
                pool_size=1,
                max_overflow=0,
                pool_timeout=3,
                connect_args={"application_name": "qtrading_agent_instance_guard"},
            )
            self._connection = self._engine.connect()
            return bool(
                self._connection.execute(
                    sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": INSTANCE_LOCK_KEY}
                ).scalar()
            )

        try:
            acquired = bool(await self._blocking_runner(_try_lock))
        except Exception as exc:  # 锁服务不可达：不得假设可安全接管
            self.release()
            return InstanceGuardEvidence(acquired=False, prior_writer_exited=False, detail=type(exc).__name__)
        if not acquired:
            self.release()
            return InstanceGuardEvidence(acquired=False, prior_writer_exited=False, detail="lock-held")
        return InstanceGuardEvidence(acquired=True, prior_writer_exited=True, detail="acquired")

    def release(self) -> None:
        connection, engine = self._connection, self._engine
        self._connection = None
        self._engine = None
        for target in (connection, engine):
            if target is None:
                continue
            try:
                target.close() if target is connection else target.dispose()
            except Exception:  # 释放兜底：不得掩盖启动/关闭主流程异常
                pass


async def _default_blocking_runner(fn: Callable[[], Any]) -> Any:
    from utils.thread_pool import TaskType, ThreadPoolManager

    return await ThreadPoolManager().run_async(TaskType.IO, fn)


def _sync_database_url(url: str) -> str:
    """统一为同步 psycopg URL（实例锁连接；asyncpg 驱动仅测试注入时出现）。"""

    parsed = sa.make_url(url)
    if parsed.drivername.endswith("+asyncpg"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


@dataclass(frozen=True)
class StartupOutcome:
    """启动编排结果：``handle`` 仅在隔离``ready=True``并 launch 后非空。"""

    handle: Any
    isolation: StartupIsolationReceipt | None
    failure: str | None
    instance_guard: Any = None


async def start_agent_runtime(
    settings: Any,
    *,
    user_scope: str,
    isolation_id: str,
    registry: RecoveryRegistry,
    target_id: str | None = None,
    application_name: str | None = None,
    assemble_func: Callable[[Any], Any] | None = None,
    launch_func: Callable[[Any], None] | None = None,
    instance_guard: InstanceGuard | None = None,
    blocking_runner: BlockingRunner | None = None,
) -> StartupOutcome:
    """启动编排接线（§8.4 第 1-4 条）：唯一执行锁 → 组装 → 启动隔离 → 就绪才 launch。

    - **唯一执行锁缺失/旧写者未退出**：不组装、不隔离、不 launch（§8.4 第 1 条
      「否则关闭持续写入协调器、停止新增…禁止新提交」）；PG advisory lock 拿锁
      成功本身即证明旧实例已退出（§8.6：CANCELLED 不证明写者退出）；
    - 组装失败（含双迁移中间失败）：**不隔离、不 launch、不建/留宿主对象**，
      原历史与原迁移状态保持（§10.6）；
    - 隔离未就绪：关闭已组装宿主（释放 DBOS/Client/Engine）并返回回执，
      调用方可重试，不 launch；
    - 隔离就绪：``launch`` 一次（``listen_queues([])`` 由 launch 入口负责）。

    组装/launch/宿主关闭均经 ``blocking_runner``（默认 ThreadPoolManager.IO，
    HANDOFF §10.6：受管线程，不堵 UI）。``CancelledError`` 继续传播，传播前
    释放已组装宿主与实例锁（R2：不吞取消、不留资源）。
    """

    if not isinstance(settings, AgentRuntimeSettings):
        raise TypeError("settings must be AgentRuntimeSettings")
    assemble = assemble_func or assemble_agent_runtime
    launch = launch_func or launch_agent_runtime
    runner = blocking_runner or _default_blocking_runner
    guard = instance_guard or AdvisoryLockInstanceGuard(settings.system_database_url, blocking_runner=runner)

    evidence = await guard.acquire()
    if not evidence.acquired or not evidence.prior_writer_exited:
        guard.release()
        return StartupOutcome(
            handle=None, isolation=None, failure=f"instance_guard_blocked:{evidence.detail}", instance_guard=None
        )

    def _abort_handle(handle: Any) -> None:
        try:
            handle.close()
        except Exception:  # 清理兜底：保留原始失败原因
            pass
        guard.release()

    try:
        handle = await runner(lambda: assemble(settings))
    except Exception as exc:  # 组装失败：不隔离、不 launch（0 派发）
        guard.release()
        return StartupOutcome(handle=None, isolation=None, failure=f"assemble_failed:{type(exc).__name__}")

    try:
        receipt = await run_startup_isolation(
            user_scope=user_scope,
            isolation_id=isolation_id,
            target_id=target_id or RUNTIME_DB_NAME,
            client=handle.client,
            registry=registry,
            application_name=application_name or RUNTIME_DB_NAME,
            app_version=settings.application_version,
        )
    except asyncio.CancelledError:
        await runner(lambda: _abort_handle(handle))
        raise
    if not receipt.ready:
        await runner(lambda: _abort_handle(handle))
        return StartupOutcome(handle=None, isolation=receipt, failure=None)

    try:
        await runner(lambda: launch(handle))
    except asyncio.CancelledError:
        await runner(lambda: _abort_handle(handle))
        raise
    except Exception as exc:
        await runner(lambda: _abort_handle(handle))
        return StartupOutcome(handle=None, isolation=receipt, failure=f"launch_failed:{type(exc).__name__}")
    return StartupOutcome(handle=handle, isolation=receipt, failure=None, instance_guard=guard)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


async def _snapshot_native_workflows(
    client: WorkflowControlClient,
    *,
    application_name: str,
    app_version: str,
    workflow_names: Sequence[str],
) -> dict[str, str]:
    """读满全部分页后返回 ``{workflow_id: 原生状态}``（固定集合后才取消）。"""

    states: dict[str, str] = {}
    offset = 0
    while True:
        page = await client.list_workflows_async(
            status=list(STARTUP_ACTIVE_STATUSES),
            name=list(workflow_names),
            application_name=application_name,
            app_version=app_version,
            limit=ISOLATION_PAGE_SIZE,
            offset=offset,
            load_input=False,
            load_output=False,
        )
        for item in page:
            states[item.workflow_id] = str(item.status)
        if len(page) < ISOLATION_PAGE_SIZE:
            return states
        offset += len(page)


async def _list_states_by_id(client: WorkflowControlClient, workflow_ids: Sequence[str]) -> dict[str, str]:
    """按精确 ID 查回原生状态（分批；无行即不在结果中，不伪终态）。"""

    states: dict[str, str] = {}
    for start in range(0, len(workflow_ids), ISOLATION_PAGE_SIZE):
        chunk = list(workflow_ids[start : start + ISOLATION_PAGE_SIZE])
        page = await client.list_workflows_async(
            workflow_ids=chunk,
            limit=len(chunk),
            load_input=False,
            load_output=False,
        )
        for item in page:
            states[item.workflow_id] = str(item.status)
    return states


def _receipt(
    command_id: str,
    user_scope: str,
    *,
    ready: bool,
    registered_ids: tuple[str, ...],
    snapshot_ids: tuple[str, ...],
    original_states: tuple[tuple[str, str], ...],
    blocking_reasons: tuple[str, ...],
    unverified_ids: tuple[str, ...] = (),
) -> StartupIsolationReceipt:
    return StartupIsolationReceipt(
        command_id=command_id,
        user_scope=user_scope,
        ready=ready,
        registered_workflow_ids=registered_ids,
        isolated_workflow_ids=snapshot_ids,
        original_states=original_states,
        blocking_reasons=blocking_reasons,
        unverified_workflow_ids=unverified_ids,
    )


def _blocked_receipt(
    command_id: str,
    user_scope: str,
    reason: str,
    exc: BaseException | None,
) -> StartupIsolationReceipt:
    """早停回执：只记录阻塞码与异常类型名（不含消息正文，R9）。"""

    reasons = (reason,) if exc is None else (reason, f"{reason}:{type(exc).__name__}")
    return _receipt(
        command_id,
        user_scope,
        ready=False,
        registered_ids=(),
        snapshot_ids=(),
        original_states=(),
        blocking_reasons=reasons,
    )


async def _finalize_blocked(
    registry: RecoveryRegistry,
    *,
    command_id: str,
    user_scope: str,
    reason: str,
    exc: BaseException | None,
    registered_ids: tuple[str, ...],
    snapshot_ids: tuple[str, ...],
    original_states: tuple[tuple[str, str], ...],
) -> StartupIsolationReceipt:
    """取消/复查阶段失败：回填阻塞回执并保持 ``ready=False``。"""

    receipt = _receipt(
        command_id,
        user_scope,
        ready=False,
        registered_ids=registered_ids,
        snapshot_ids=snapshot_ids,
        original_states=original_states,
        blocking_reasons=(reason,) if exc is None else (reason, f"{reason}:{type(exc).__name__}"),
    )
    return await _finalize_receipt(registry, receipt)


async def _finalize_receipt(registry: RecoveryRegistry, receipt: StartupIsolationReceipt) -> StartupIsolationReceipt:
    """回填隔离命令回执；回填失败则本回执不可作为 launch 依据（ready=False）。"""

    try:
        await registry.finalize_startup_isolation(
            user_scope=receipt.user_scope,
            isolation_command_id=receipt.command_id,
            receipt_payload=json_doc(receipt.to_wire()),
        )
    except Exception as exc:
        return replace(
            receipt,
            ready=False,
            blocking_reasons=(*receipt.blocking_reasons, "isolation_receipt_unsaved", type(exc).__name__),
        )
    return receipt


#: 便于启动编排注入的类型别名（保持调用侧简洁）。
StartupIsolationRunner = Callable[..., Awaitable[StartupIsolationReceipt]]


__all__ = [
    "ABSENT_NATIVE_STATE",
    "INSTANCE_LOCK_KEY",
    "ISOLATION_PAGE_SIZE",
    "STARTUP_ACTIVE_STATUSES",
    "STARTUP_WORKFLOW_NAMES",
    "AdvisoryLockInstanceGuard",
    "BlockingRunner",
    "InstanceGuard",
    "InstanceGuardEvidence",
    "ManualRecoveryReceipt",
    "RecoveryRegistry",
    "StartupIsolationReceipt",
    "StartupIsolationRunner",
    "StartupOutcome",
    "WorkflowControlClient",
    "resume_selected_workflows",
    "run_startup_isolation",
    "start_agent_runtime",
]
