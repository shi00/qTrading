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

#: 启动隔离的「在途」原生状态（HANDOFF §8.4 第 3 条：PENDING/ENQUEUED/DELAYED）。
STARTUP_ACTIVE_STATUSES: Final[tuple[str, ...]] = (
    WorkflowStatusString.PENDING.value,
    WorkflowStatusString.ENQUEUED.value,
    WorkflowStatusString.DELAYED.value,
)

#: 原生终态（取消复查只需确认不再处于在途状态）。
STARTUP_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    status.value
    for status in (
        WorkflowStatusString.SUCCESS,
        WorkflowStatusString.ERROR,
        WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED,
        WorkflowStatusString.CANCELLED,
    )
)

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
    client: WorkflowControlClient,
    registry: RecoveryRegistry,
) -> ManualRecoveryReceipt:
    """手动恢复用户明确选择的精确集合（HANDOFF §8.4 第 5 条）。

    先由 DAO 事务授予代次（不合格项进 ``blocked``，其 workflow 不返回），再逐个
    ``resume`` 返回的精确原生集合；未选集合（B）与用户终止轮次保持隔离。
    单个 resume 失败如实进 ``resume_failures`` 并使 ``ready=False``，不以其余
    成功项冒充完整恢复。
    """

    grant = await registry.grant_recovery_epoch(
        RecoveryGrantRequest(
            user_scope=user_scope,
            execution_ids=tuple(execution_ids),
            owner_epoch=owner_epoch,
            run_epoch=run_epoch,
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
    return ManualRecoveryReceipt(
        user_scope=user_scope,
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
        granted_execution_ids=grant.granted_execution_ids,
        resumed_workflow_ids=tuple(resumed),
        blocked=grant.blocked,
        resume_failures=tuple(failures),
        ready=not failures,
    )


@dataclass(frozen=True)
class StartupOutcome:
    """启动编排结果：``handle`` 仅在隔离``ready=True``并 launch 后非空。"""

    handle: Any
    isolation: StartupIsolationReceipt | None
    failure: str | None


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
) -> StartupOutcome:
    """启动编排接线（§8.4 第 1-4 条）：组装 → 启动隔离 → 就绪才 launch。

    - 组装失败（含双迁移中间失败）：**不隔离、不 launch、不建/留宿主对象**，
      原历史与原迁移状态保持（§10.6）；
    - 隔离未就绪：关闭已组装宿主（释放 DBOS/Client/Engine）并返回回执，
      调用方可重试，不 launch；
    - 隔离就绪：``launch`` 一次（``listen_queues([])`` 由 launch 入口负责）。

    ``CancelledError`` 继续传播，传播前释放已组装宿主（R2：不吞取消、不留资源）。
    """

    from services.agent_service.runtime import (
        RUNTIME_DB_NAME,
        AgentRuntimeSettings,
        assemble_agent_runtime,
        launch_agent_runtime,
    )

    assert isinstance(settings, AgentRuntimeSettings)
    assemble = assemble_func or assemble_agent_runtime
    launch = launch_func or launch_agent_runtime
    try:
        handle = assemble(settings)
    except Exception as exc:  # 组装失败：不隔离、不 launch（0 派发）
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
        handle.close()
        raise
    if not receipt.ready:
        handle.close()
        return StartupOutcome(handle=None, isolation=receipt, failure=None)
    launch(handle)
    return StartupOutcome(handle=handle, isolation=receipt, failure=None)


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
    "ISOLATION_PAGE_SIZE",
    "STARTUP_ACTIVE_STATUSES",
    "STARTUP_TERMINAL_STATUSES",
    "STARTUP_WORKFLOW_NAMES",
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
