"""启动隔离与手动恢复（HANDOFF §8.4；设计 §10.6 启动隔离/恢复合同行）。

职责边界：

- **启动隔离**：在 ``launch`` 之前，用 ``Client`` 读取「应用登记集合 ∪ 原生在途
  集合」→ **先固定快照**（分页完成后才取消，避免边取消边漏项）→ 保存隔离命令
  与原状态 → 关闭应用派发门闩 → 精确取消（``cancel_children=False``）→ 复查
  全部成功。任一环节失败/未知都返回 ``ready=False`` 且不进入 launch（
  ``listen_queues([])`` 不是禁用默认恢复的开关，HANDOFF §8.4）。
- **手动恢复**：只对用户明确选择的精确集合授予本进程 ``owner_epoch``/
  ``run_epoch``；随后由本模块**解析必要叶**（已授予 root 的直属、中断未结案的
  叶 workflow），先复接叶再复接 root；A 不动 B，成功叶零重做，用户终止不复活，
  不可恢复终态明确拒绝（``blocked``），不静默跳过。

**版本谓词贯穿取消集**：原生列举与登记项补读都带同一 ``application_name`` /
``app_version`` / 名称谓词；无法证明与本进程同应用同版本的登记项**不进入取消
集**（否则会误杀别的源码/制品版本的在途执行），只在快照与回执中如实标记
``version-unproven``。取消集只含在途状态（``STARTUP_ACTIVE_STATUSES``）的原生
行，已终态（含用户终止、恢复耗尽）零改写；复查只认显式终态，**缺行不等于已
退出**（``unverified_workflow_ids`` 如实阻塞）。

**业务库归属贯穿隔离集**：登记面是**当前业务库**（``AgentDao``）的投影，而运行库
在同实例内固定复用，原生在途行可能残留别的业务库（切库前的 N）执行。取消集只收
「登记归属可证」的原生行——直接登记项，或已登记 root 的直属/传递子项；登记父项
须在原生列举中证明同应用同版本才可作归属依据，否则其子项随父按
``version-unproven`` 处理、不取消。无主（未登记且父级归属不可证）的在途行一律
零改写并 ``ready=False``（``ownership_unproven``），避免切库后误清理/误恢复旧业务库
执行；手动恢复同样以登记 root + 其直属叶为归属判据。

明确不做：

- 不 ``launch``（启动编排在隔离 ``ready=True`` 后另行调用）；
- 不判定业务终态、不修改业务事实（除门闩/代次字段，均由 DAO 事务落库）；
- 不吞 ``asyncio.CancelledError``（R2）：取消继续传播，只有普通异常转成
  ``ready=False`` 的阻塞原因（失败可核，不假装成功）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

import sqlalchemy as sa
from dbos import WorkflowStatus, WorkflowStatusString

from core.agent_types import (
    NON_RECOVERABLE_DISPLAY_STATES,
    RECOVERY_UNVERIFIED_DISPLAY_STATES,
    ROUND_STOP_REASON_USER_TERMINATION,
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
    INSTANCE_GUARD_CONNECTION_RESERVE,
    RUNTIME_DB_NAME,
    AgentRuntimeSettings,
    InstanceGuard,
    InstanceGuardEvidence,
    assemble_agent_runtime,
    launch_agent_runtime,
)
from services.agent_service.workflows import LEAF_WORKFLOW_NAME, RESEARCH_WORKFLOW_NAME
from utils.loop_local import get_loop_local

#: 启动隔离的「在途」原生状态（HANDOFF §8.4 第 3 条：PENDING/ENQUEUED/DELAYED；
#: dbos 3.7.0 引入 SUSPENDED，未终结的挂起执行同属在途，须在启动时取消）。
STARTUP_ACTIVE_STATUSES: Final[tuple[str, ...]] = (
    WorkflowStatusString.PENDING.value,
    WorkflowStatusString.ENQUEUED.value,
    WorkflowStatusString.DELAYED.value,
    WorkflowStatusString.SUSPENDED.value,
)

#: 原生终态（取消复查只需确认不再处于在途状态）。
#: 应用自有 workflow 名（D-02 发布名；隔离按名 + 应用版本过滤）。
STARTUP_WORKFLOW_NAMES: Final[tuple[str, ...]] = (RESEARCH_WORKFLOW_NAME, LEAF_WORKFLOW_NAME)

#: 手动恢复须随 root 一并复接的叶子原生状态：中断未结案（在途或已取消）。
#: ``SUCCESS`` 叶零重做；``ERROR`` / ``MAX_RECOVERY_ATTEMPTS_EXCEEDED`` 叶已是
#: 已投影失败成果（R04），重跑会重复业务副作用，故均不纳入必要叶集合。
RECOVERY_RESUMABLE_LEAF_STATUSES: Final[tuple[str, ...]] = (
    WorkflowStatusString.PENDING.value,
    WorkflowStatusString.ENQUEUED.value,
    WorkflowStatusString.DELAYED.value,
    WorkflowStatusString.SUSPENDED.value,
    WorkflowStatusString.CANCELLED.value,
)

#: 分页/批量上限（固定集合后再取消的关键：先读满全部分页）。
ISOLATION_PAGE_SIZE: Final[int] = 100

#: 登记项无法证明与本次启动同应用/同版本时的快照标记（不纳入取消集）。
VERSION_UNPROVEN: Final[str] = "version-unproven"

#: 取消复查可接受的显式终态；仍在途或缺行都不算「已退出」。
TERMINAL_WORKFLOW_STATUSES: Final[tuple[str, ...]] = (
    WorkflowStatusString.SUCCESS.value,
    WorkflowStatusString.ERROR.value,
    WorkflowStatusString.CANCELLED.value,
    WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED.value,
)


@dataclass(frozen=True)
class StartupIsolationReceipt:
    """启动隔离回执（不可变；同时作为隔离命令的回执载荷保存）。

    ``ready=True`` 表示：隔离命令与原状态已保存、应用派发门闩已关闭、快照内
    全部原生 workflow 已不在途。``blocking_reasons`` 为阻塞码（稳定 wire 值），
    ``unverified_workflow_ids`` 为复查仍未见终态的原生 ID。
    ``cancelled_workflow_ids`` 为真正进入取消集的精确集合（只含在途行）；
    ``unproven_version_workflow_ids`` 为登记但无法证明同应用同版本（或其子项）而
    未进入取消集的项（登记项快照原状态标 ``version-unproven``）；
    ``unproven_ownership_workflow_ids`` 为原生在途但无法证明属于当前业务库（未
    登记且父级归属不可证）的项：零改写并阻塞 launch。
    """

    command_id: str
    user_scope: str
    ready: bool
    registered_workflow_ids: tuple[str, ...]
    isolated_workflow_ids: tuple[str, ...]
    original_states: tuple[tuple[str, str], ...]
    blocking_reasons: tuple[str, ...]
    unverified_workflow_ids: tuple[str, ...]
    cancelled_workflow_ids: tuple[str, ...] = ()
    unproven_version_workflow_ids: tuple[str, ...] = ()
    unproven_ownership_workflow_ids: tuple[str, ...] = ()

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
            "cancelled_workflow_ids": list(self.cancelled_workflow_ids),
            "unproven_version_workflow_ids": list(self.unproven_version_workflow_ids),
            "unproven_ownership_workflow_ids": list(self.unproven_ownership_workflow_ids),
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
        parent_workflow_id: str | list[str] | None = None,
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

    顺序固定：读登记集合 → 原生分页快照（读满才停）→ 按登记 ID 带同一版本谓词
    补读被过滤项（无法证明版本者不取消、如实标记）→ 按**当前业务库登记归属**解析
    取消集（未登记且父级不可证的原生在途行零改写并阻塞）→ 固定并保存隔离命令与
    原状态 → 关闭应用派发门闩 → 精确取消（仅限已归属在途行）→ 复查显式终态。任一
    步普通异常即 ``ready=False`` 并停止后续步骤（不取消、不 launch）；
    ``CancelledError`` 继续传播（R2）。

    Args:
        user_scope: 用户域（与 DAO 同域校验）。
        isolation_id: 本次启动隔离命令的稳定 ID（客户端命令键）。
        target_id: 命令目标身份（应用实例，如运行库名 + 启动批次）。
        client: 原生控制面（禁监听/禁无限重试的 ``DBOSClient``）。
        registry: 应用登记面（``AgentDao``，必须是**当前业务库**的投影；其登记项
            是归属判据的唯一来源，运行库原生行可能残留旧业务库执行）。
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
        active_states = await _snapshot_native_workflows(
            client,
            application_name=application_name,
            app_version=app_version,
            workflow_names=workflow_names,
        )
    except Exception as exc:  # 分页失败：集合不完整（§8.4 第 3 条）
        return _blocked_receipt(command_id, user_scope, "native_listing_failed", exc)
    native_states = dict(active_states)

    # 在途列举按名/版本/在途状态过滤，登记项若已处于终态不会被列入。补读必须带
    # **同一版本谓词**：只有能证明「同应用、同版本」的登记项才有资格进入取消集；
    # 无法证明者不取消（否则会误杀别的源码/制品版本的在途执行），按
    # ``VERSION_UNPROVEN`` 如实标记并列入回执。
    filtered_registered = tuple(item for item in registered_ids if item not in native_states)
    unproven_registered: tuple[str, ...] = ()
    if filtered_registered:
        try:
            scoped_states = await _list_states_by_id(
                client,
                filtered_registered,
                application_name=application_name,
                app_version=app_version,
                workflow_names=workflow_names,
            )
        except Exception as exc:  # 补读失败：无法证明登记项归属/已结案，不取消/不 launch
            return _blocked_receipt(command_id, user_scope, "registered_state_read_failed", exc)
        unproven_registered = tuple(sorted(set(filtered_registered) - set(scoped_states)))
        native_states = {**scoped_states, **native_states}

    # 归属判据：登记面是当前业务库投影，运行库原生行可能来自切库前的旧业务库。
    # 只有「直接登记」或「已登记且版本可证的 root 的子项」进入取消集；无主在途行
    # 零改写并阻塞 launch（N06：切库后不得清理/恢复旧业务库执行）。
    owned_ids, version_unproven_ids, unproven_ownership = _resolve_ownership(
        active_states=active_states,
        proven_states=native_states,
        registered_ids=registered_ids,
        unproven_version_ids=unproven_registered,
    )

    snapshot_ids = tuple(sorted(set(native_states) | set(registered_ids)))
    original_states = tuple(
        (
            workflow_id,
            VERSION_UNPROVEN
            if workflow_id in version_unproven_ids and workflow_id in registered_ids
            else native_states[workflow_id].status,
        )
        for workflow_id in snapshot_ids
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
            unproven_version_ids=tuple(sorted(version_unproven_ids)),
        )

    # 取消只发在途行：DBOS 的 cancel 会把非 SUCCESS/ERROR 行（含恢复耗尽终态）
    # 统一改写成 CANCELLED，故已终态/用户终止项绝不能进入取消集；归属不可证或
    # 版本不可证的原生行也不进入（零改写，交回执如实报告）。
    cancel_ids = tuple(
        sorted(
            workflow_id
            for workflow_id, state in active_states.items()
            if workflow_id in owned_ids and state.status in STARTUP_ACTIVE_STATUSES
        )
    )
    if cancel_ids:
        try:
            await client.cancel_workflows_async(list(cancel_ids), cancel_children=False)
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
                cancelled_ids=cancel_ids,
                unproven_version_ids=tuple(sorted(version_unproven_ids)),
            )

    try:
        verified_states = await _list_states_by_id(client, cancel_ids)
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
            cancelled_ids=cancel_ids,
            unproven_version_ids=tuple(sorted(version_unproven_ids)),
        )

    # 只认显式终态：缺行可能来自清理删除/读取缺口，不能当作「原在途已退出」。
    unverified_ids = tuple(
        workflow_id
        for workflow_id in cancel_ids
        if _native_status(verified_states.get(workflow_id)) not in TERMINAL_WORKFLOW_STATUSES
    )
    blocking_reasons: list[str] = []
    if unverified_ids:
        blocking_reasons.append("cancel_unverified")
    if unproven_ownership:
        # 无主在途行仍可能被 launch 的恢复管线接管：缺归属证据不得宣称 0 派发
        blocking_reasons.append("ownership_unproven")
    receipt = StartupIsolationReceipt(
        command_id=command_id,
        user_scope=user_scope,
        ready=not blocking_reasons,
        registered_workflow_ids=registered_ids,
        isolated_workflow_ids=snapshot_ids,
        original_states=original_states,
        blocking_reasons=tuple(blocking_reasons),
        unverified_workflow_ids=unverified_ids,
        cancelled_workflow_ids=cancel_ids,
        unproven_version_workflow_ids=tuple(sorted(version_unproven_ids)),
        unproven_ownership_workflow_ids=tuple(sorted(unproven_ownership)),
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

    顺序固定为「先核叶终态 → 再提交授权 → 再开派发 → 才复接」：

    1. 只读取登记投影并解析每个选择项的 root workflow；登记缺行/无有效轮即
       ``preflight_incomplete``（不授予、不开派发、不部分恢复）；
    2. 按 root 解析**必要叶**（直属、``RECOVERY_RESUMABLE_LEAF_STATUSES`` 内、
       未结案的叶 workflow）并核对原生终态；列举失败即不可证明必要全集，
       fail-closed（不授予，派发门保持关闭）；
    3. 再提交 DAO 授权事务（同事务写 ``owner_epoch``/``run_epoch`` 并重开
       ``dispatch_closed``），候选不合格理由按 DAO 权威事实进 ``blocked``；
    4. 只复接已授予 root 下的必要叶，叶未可靠接续时该 root 不推进；
    5. root 最后复接。

    **原授权精确查回**：重试沿用同一 ``(owner_epoch, run_epoch)`` 且派发门闩未关闭
    时，登记投影中已持有该代次的选择项即视为原授权（不重复授予新代次，R22），
    未被该授权覆盖的项（含派发门闩已关闭者）如实进 ``blocked``，不会被静默改授
    新代次或静默复活。叶先于 root 复接：避免父先
    运行、其尚未落盘的观察把仍处取消态的叶记为已取消（``ended``）而永久阻塞
    后继；已落盘的观察记录仍按 DBOS 重放语义还原，本函数不伪造也不改写。成功
    叶与已定失败叶不重跑（零重做）。未选集合（B）不列举、不扩入。

    单个 resume 失败如实进 ``resume_failures`` 并使 ``ready=False``；必要叶列举
    失败视为「无法证明必要全集」，不部分恢复（``leaf_resolution_failed``）；
    全部候选被拒（零授予）同样 ``ready=False``（不把「什么都没恢复」当恢复成功）。
    """

    selected_ids = tuple(sorted(set(execution_ids)))
    if not selected_ids:
        return _not_ready_receipt(
            user_scope=user_scope,
            owner_epoch=owner_epoch,
            run_epoch=run_epoch,
            reasons=("no_granted_execution",),
        )
    try:
        registered = await registry.list_recovery_executions(user_scope)
    except Exception as exc:  # 登记投影不可读：无法证明选择项现状，不授予/不派发
        return _not_ready_receipt(
            user_scope=user_scope,
            owner_epoch=owner_epoch,
            run_epoch=run_epoch,
            reasons=("recovery_state_unreadable", f"recovery_state_unreadable:{type(exc).__name__}"),
        )
    by_id = {item.execution_id: item for item in registered}
    unresolved = tuple(
        (execution_id, "workflow_unknown")
        for execution_id in selected_ids
        if execution_id not in by_id or by_id[execution_id].workflow_id is None
    )
    if unresolved:
        return _not_ready_receipt(
            user_scope=user_scope,
            owner_epoch=owner_epoch,
            run_epoch=run_epoch,
            blocked=unresolved,
            reasons=("preflight_incomplete",),
        )
    selected = tuple(by_id[execution_id] for execution_id in selected_ids)
    # 原授权仍有效的判定：代次身份匹配且派发门闩未被关闭（关闭即视为已停止，
    # 需新代次重新授权才能重开，不得凭旧代次静默复活）。
    authorized = tuple(
        item
        for item in selected
        if item.owner_epoch == owner_epoch and item.run_epoch == run_epoch and not item.dispatch_closed
    )
    if authorized:
        # 原授权可按 (owner_epoch, run_epoch) 从登记投影精确查回：不重复授予新代次
        granted_execution_ids = tuple(item.execution_id for item in authorized)
        workflow_ids = tuple(sorted({item.workflow_id for item in authorized if item.workflow_id is not None}))
        blocked = tuple(
            (item.execution_id, _authorization_mismatch_reason(item)) for item in selected if item not in authorized
        )
        try:
            leaf_map = await _list_necessary_leaf_ids(client, workflow_ids)
        except Exception as exc:  # 必要叶全集不可证明：不部分恢复（否则中断叶仍留取消态）
            return _not_ready_receipt(
                user_scope=user_scope,
                owner_epoch=owner_epoch,
                run_epoch=run_epoch,
                granted=granted_execution_ids,
                blocked=blocked,
                reasons=("leaf_resolution_failed", f"leaf_resolution_failed:{type(exc).__name__}"),
            )
    else:
        candidate_roots = tuple(sorted({item.workflow_id for item in selected if item.workflow_id is not None}))
        try:
            # 先核对必要叶终态（只读）：不可核即不授予、不开派发门
            leaf_map = await _list_necessary_leaf_ids(client, candidate_roots)
        except Exception as exc:
            return _not_ready_receipt(
                user_scope=user_scope,
                owner_epoch=owner_epoch,
                run_epoch=run_epoch,
                reasons=("leaf_resolution_failed", f"leaf_resolution_failed:{type(exc).__name__}"),
            )
        grant = await registry.grant_recovery_epoch(
            RecoveryGrantRequest(
                user_scope=user_scope,
                execution_ids=selected_ids,
                owner_epoch=owner_epoch,
                run_epoch=run_epoch,
                application_version=application_version,
            )
        )
        granted_execution_ids = grant.granted_execution_ids
        workflow_ids = grant.workflow_ids
        blocked = grant.blocked
        if not granted_execution_ids:
            return _not_ready_receipt(
                user_scope=user_scope,
                owner_epoch=owner_epoch,
                run_epoch=run_epoch,
                blocked=blocked,
                reasons=("no_granted_execution",),
            )

    resumed: list[str] = []
    failures: list[tuple[str, str]] = []
    failed_roots: set[str] = set()
    # 已授予 root 必须已有叶终态核对结果；缺失即视为未核，不得直接推进父（fail-closed）
    for root_id in workflow_ids:
        if root_id not in leaf_map:
            failed_roots.add(root_id)
            failures.append((root_id, "leaf_state_unverified"))
    for root_id in workflow_ids:
        if root_id in failed_roots:
            continue
        for leaf_id in leaf_map.get(root_id, ()):
            try:
                await client.resume_workflows_async([leaf_id])
            except Exception as exc:
                failures.append((leaf_id, type(exc).__name__))
                failed_roots.add(root_id)
            else:
                resumed.append(leaf_id)
    blocking_reasons: list[str] = []
    if failed_roots:
        # 必要叶未可靠接续：对应 root 不得推进（父先跑会把仍取消的叶记成已结案）
        blocking_reasons.append("necessary_leaf_not_resumed")
    for root_id in workflow_ids:
        if root_id in failed_roots:
            continue
        try:
            await client.resume_workflows_async([root_id])
        except Exception as exc:
            failures.append((root_id, type(exc).__name__))
        else:
            resumed.append(root_id)
    return ManualRecoveryReceipt(
        user_scope=user_scope,
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
        granted_execution_ids=granted_execution_ids,
        resumed_workflow_ids=tuple(resumed),
        blocked=blocked,
        resume_failures=tuple(failures),
        blocking_reasons=tuple(blocking_reasons),
        ready=not failures and not blocking_reasons,
    )


def _not_ready_receipt(
    *,
    user_scope: str,
    owner_epoch: int,
    run_epoch: int,
    reasons: tuple[str, ...],
    granted: tuple[str, ...] = (),
    blocked: tuple[tuple[str, str], ...] = (),
) -> ManualRecoveryReceipt:
    """未开派发/未复接的回执（如实报阻塞码与已拒项，不伪造恢复成功）。"""

    return ManualRecoveryReceipt(
        user_scope=user_scope,
        owner_epoch=owner_epoch,
        run_epoch=run_epoch,
        granted_execution_ids=granted,
        resumed_workflow_ids=(),
        blocked=blocked,
        resume_failures=(),
        blocking_reasons=reasons,
        ready=False,
    )


def _authorization_mismatch_reason(item: RecoveryExecution) -> str:
    """原授权集合外的选择项按登记现状给精确拒绝码（不改写任何事实）。"""

    if item.stop_reason == ROUND_STOP_REASON_USER_TERMINATION:
        return "user_termination"
    if item.display_state in NON_RECOVERABLE_DISPLAY_STATES:
        return "already_terminal"
    if item.display_state in RECOVERY_UNVERIFIED_DISPLAY_STATES:
        return "state_unverified"
    if item.dispatch_closed:
        return "dispatch_closed"
    return "authorization_mismatch"


#: 单实例执行锁键（PG advisory lock；会话断开/进程崩溃自动释放）。
INSTANCE_LOCK_KEY: Final[int] = 0x7174726D

#: 同步阻塞段执行器（返回函数值；默认受管线程池）。
BlockingRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


class AdvisoryLockInstanceGuard:
    """PG advisory lock 单实例执行锁（HANDOFF §8.4 第 1 条最小实现）。

    独立**单连接**引擎持有会话级 advisory lock：旧实例正常退出或崩溃时 PG
    自动释放锁，故 ``pg_try_advisory_lock`` 成功即同时证明「获得唯一执行权」
    与「旧写者已退出/可安全接管」（不是用 CANCELLED 冒充退出，§8.6）。

    连接预算（HANDOFF §8.1「4+1+2=7」与 §8.4 唯一执行锁，检视 R12）：运行期增量
    共 7 条 = 运行池 4 + 脱池 LISTEN 1 + 控制池 1 + **本实例锁 1**。本锁是独立
    第 4 类所有权（独立引擎、独立单连接），不并入 LISTEN 或控制池；早前「实例锁
    即 4+1+2 中的 +1」属把两类连接混记，已按实际所有权拆开。连接仅在启动后
    常驻、关闭就绪后 release。
    """

    def __init__(self, system_database_url: str, *, blocking_runner: BlockingRunner | None = None) -> None:
        self._system_database_url = system_database_url
        self._blocking_runner = blocking_runner or _default_blocking_runner
        self._engine: Any = None
        self._connection: Any = None
        # 独立的成功状态：只有 ``pg_try_advisory_lock`` 真正返回真才置位，
        # 不得用「已有连接」推断已持有（R11 检视项）。
        self._acquired = False

    @staticmethod
    def _serialization_lock() -> asyncio.Lock:
        """拿锁阶段串行化（loop-local，R11：不把 asyncio.Lock 存为跨循环类属性）。"""

        return get_loop_local("agent_instance_guard_acquire", asyncio.Lock)

    async def acquire(self) -> InstanceGuardEvidence:
        # 串行：并发的第二次 acquire 必须等首次真实查询（可能阻塞在连接/线程池）结束，
        # 不得凭 `_try_lock` 中途写入的连接冒充 already-held。
        async with self._serialization_lock():
            if self._acquired:
                if await self._held_connection_alive():
                    return InstanceGuardEvidence(acquired=True, prior_writer_exited=True, detail="already-held")
                # 连接失活：会话级 advisory lock 已随连接释放，旧状态不得冒充持有。
                self.release()
                return InstanceGuardEvidence(acquired=False, prior_writer_exited=False, detail="connection-lost")
            return await self._acquire_now()

    async def _acquire_now(self) -> InstanceGuardEvidence:
        def _try_lock() -> bool:
            self._engine = sa.create_engine(
                _sync_database_url(self._system_database_url),
                pool_size=INSTANCE_GUARD_CONNECTION_RESERVE,
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
            outcome = await _run_owned_blocking(self._blocking_runner, _try_lock)
        except Exception as exc:  # 锁服务不可达：不得假设可安全接管
            self.release()
            return InstanceGuardEvidence(acquired=False, prior_writer_exited=False, detail=type(exc).__name__)
        if outcome.cancel is not None:
            # 拿锁线程已真实退出：先释放它可能建立的连接（会话锁随之释放）再传播取消（R06）
            self.release()
            raise outcome.cancel
        if not bool(outcome.value):
            self.release()
            return InstanceGuardEvidence(acquired=False, prior_writer_exited=False, detail="lock-held")
        self._acquired = True
        return InstanceGuardEvidence(acquired=True, prior_writer_exited=True, detail="acquired")

    async def _held_connection_alive(self) -> bool:
        """复核已持有连接是否仍可用；失活即不得冒充持有（会话锁已随连接释放）。"""

        def _probe() -> bool:
            connection = self._connection
            if connection is None:
                return False
            connection.execute(sa.text("SELECT 1"))
            return True

        try:
            outcome = await _run_owned_blocking(self._blocking_runner, _probe)
        except Exception:  # 探针执行器不可用：按失活处理，不得据此宣称持有
            return False
        if outcome.cancel is not None:
            self.release()
            raise outcome.cancel
        return bool(outcome.value)

    def release(self) -> None:
        connection, engine = self._connection, self._engine
        self._connection = None
        self._engine = None
        self._acquired = False
        for target in (connection, engine):
            if target is None:
                continue
            try:
                target.close() if target is connection else target.dispose()
            # 释放兜底：不得掩盖启动/关闭主流程异常
            except Exception:  # R2_ALLOWED: 只捕获 Exception，CancelledError 不属其中
                pass


async def _default_blocking_runner(fn: Callable[[], Any]) -> Any:
    from utils.thread_pool import TaskType, ThreadPoolManager

    return await ThreadPoolManager().run_async(TaskType.IO, fn)


@dataclass(frozen=True)
class _OwnedBlockingResult:
    """同步阻塞段结果：``cancel`` 非空即等待方被取消，且 ``value`` 所属 worker 已真实退出。"""

    value: Any = None
    cancel: BaseException | None = None


async def _run_owned_blocking(runner: BlockingRunner, fn: Callable[[], Any]) -> _OwnedBlockingResult:
    """线程池执行同步段；取消时先等 worker 真实退出并带回其产物（R06）。

    ``ThreadPoolManager.run_async`` 的取消只取消等待、不停止 worker（executor 固有
    语义）。直接 ``await runner(...)`` 会让宿主编排立即进入清理，而仍在拿锁/组装的
    线程稍后产出无主资源。这里把等待包成独立 task：取消到达后继续等到 worker 真实
    退出，再把「产物 + 取消」一同交还调用方；调用方须先收口 ``value`` 再
    ``raise cancel``（R2：取消不得被吞）。
    """

    worker = asyncio.ensure_future(runner(fn))
    try:
        return _OwnedBlockingResult(value=await asyncio.shield(worker))
    except asyncio.CancelledError as cancel:
        await _wait_worker_exit(worker)
        return _OwnedBlockingResult(value=_worker_value(worker), cancel=cancel)


async def _wait_worker_exit(worker: asyncio.Future[Any]) -> None:
    """等待 worker 退出；后续取消不打断等待，结果/异常一律消费。"""

    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
        except Exception:  # worker 自身失败：退出即达成目的，原因由调用方另行处理
            break
    if not worker.cancelled():
        worker.exception()  # 消费异常，避免 "Task exception was never retrieved"


def _worker_value(worker: asyncio.Future[Any]) -> Any:
    """取已退出 worker 的返回值；取消/失败一律 ``None``（调用方按无产物收口）。"""

    if worker.cancelled():
        return None
    try:
        return worker.result()
    except Exception:
        return None


def _sync_database_url(url: str) -> str:
    """统一为同步 psycopg URL（实例锁连接；asyncpg 驱动仅测试注入时出现）。"""

    parsed = sa.make_url(url)
    if parsed.drivername.endswith("+asyncpg"):
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


@dataclass(frozen=True)
class StartupOutcome:
    """启动编排结果：``handle`` 仅在隔离``ready=True``并 launch 后非空。

    ``unclosed_handle`` 仅在**关闭失败**时非空：此时唯一执行锁保持持有
    （``instance_guard`` 非空）且 ``failure`` 含 ``close_failed:<异常类型>``；
    调用方须用该句柄重试 ``close()``（按资源粒度幂等，只补未完成项），全部
    关闭后再释放锁。锁未释放期间新的启动必须被阻止，不得由新实例接管可能
    仍存活的旧宿主资源。
    """

    handle: Any
    isolation: StartupIsolationReceipt | None
    failure: str | None
    instance_guard: Any = None
    unclosed_handle: Any = None


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
    HANDOFF §10.6：受管线程，不堵 UI）。``CancelledError`` 继续传播，但**先等在途
    同步 worker 真实退出并收口其产物**（取消只取消等待、不停止线程，R06）：拿锁被
    取消即释放连接、组装被取消即关闭已产出宿主、launch 被取消即关闭宿主，再传播
    取消（R2：不吞取消、不留无主资源）。

    **关闭失败一律保锁**：隔离未就绪/launch 失败时若 ``handle.close()`` 失败
    （如 ``DBOS.destroy`` 中途失败），不释放唯一执行锁，并保留句柄于
    ``unclosed_handle`` 供调用方重试关闭；取消路径同样保锁（句柄无法随结果
    返回，锁即阻塞事实），确保旧宿主资源未收口时新实例不得接管。
    """

    if not isinstance(settings, AgentRuntimeSettings):
        raise TypeError("settings must be AgentRuntimeSettings")
    assemble = assemble_func or assemble_agent_runtime
    launch = launch_func or launch_agent_runtime
    runner = blocking_runner or _default_blocking_runner
    guard = instance_guard or AdvisoryLockInstanceGuard(settings.system_database_url, blocking_runner=runner)

    try:
        evidence = await guard.acquire()
    except asyncio.CancelledError:
        # 拿锁等待被取消：acquire 已等在途 worker 退出，这里保底释放连接（R06）
        guard.release()
        raise
    if not evidence.acquired or not evidence.prior_writer_exited:
        guard.release()
        return StartupOutcome(
            handle=None, isolation=None, failure=f"instance_guard_blocked:{evidence.detail}", instance_guard=None
        )

    def _abort_handle(handle: Any) -> str | None:
        """关闭未上线的宿主：只有确认关闭成功才释放唯一执行锁。

        返回 ``None`` 表示已全部关闭并释放锁；返回异常类型名表示关闭失败——
        此时**保锁**（旧宿主资源可能仍存活，新实例不得接管），由调用方保留
        句柄重试 ``close()``（``AgentRuntimeHandle.close`` 按资源粒度幂等）。
        """

        try:
            handle.close()
        except Exception as exc:  # 关闭失败：保锁、保句柄、报告阻塞事实（可重试）
            return type(exc).__name__
        guard.release()
        return None

    def _aborted_outcome(
        receipt: StartupIsolationReceipt, failure: str | None, close_failure: str | None
    ) -> StartupOutcome:
        """未 launch 的收口结果：关闭失败时不丢句柄/不丢锁（可重试关闭）。"""

        if close_failure is None:
            return StartupOutcome(handle=None, isolation=receipt, failure=failure)
        prefix = f"{failure};" if failure else ""
        return StartupOutcome(
            handle=None,
            isolation=receipt,
            failure=f"{prefix}close_failed:{close_failure}",
            instance_guard=guard,
            unclosed_handle=handle,
        )

    async def _abort(handle: Any) -> str | None:
        """收口已组装宿主（含实例锁）；取消不打断收口，关闭失败保锁（R06/R07）。

        返回 ``None`` 表示已全部关闭并释放锁；返回异常类型名表示关闭失败（保锁、
        由调用方保留句柄重试关闭）。取消已到达时先等 worker 真实退出，再传播取消。
        """

        try:
            outcome = await _run_owned_blocking(runner, lambda: _abort_handle(handle))
        except Exception as exc:  # 收口兜底：按关闭失败保锁，不掩盖原始取消/失败原因（R2）
            return type(exc).__name__
        if outcome.cancel is not None:
            raise outcome.cancel
        return outcome.value

    try:
        assembled = await _run_owned_blocking(runner, lambda: assemble(settings))
    except Exception as exc:  # 组装失败：不隔离、不 launch（0 派发）
        guard.release()
        return StartupOutcome(handle=None, isolation=None, failure=f"assemble_failed:{type(exc).__name__}")
    if assembled.cancel is not None:
        # 同步组装已真实退出：先收口其产物再传播取消，不留无主宿主（R06）
        if assembled.value is None:
            guard.release()
        else:
            await _abort(assembled.value)
        raise assembled.cancel
    handle = assembled.value

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
        # 取消继续传播（R2）；关闭失败时保锁（锁即阻塞事实，新实例不得接管）
        await _abort(handle)
        raise
    if not receipt.ready:
        close_failure = await _abort(handle)
        return _aborted_outcome(receipt, None, close_failure)

    try:
        launched = await _run_owned_blocking(runner, lambda: launch(handle))
    except Exception as exc:
        close_failure = await _abort(handle)
        return _aborted_outcome(receipt, f"launch_failed:{type(exc).__name__}", close_failure)
    if launched.cancel is not None:
        # launch 已真实执行完：关闭宿主再传播取消，不留下已 launch 的宿主（R06）
        await _abort(handle)
        raise launched.cancel
    return StartupOutcome(handle=handle, isolation=receipt, failure=None, instance_guard=guard)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _NativeWorkflowState:
    """原生行快照：状态 + 父 workflow 身份（归属解析用；空表示 root/未记录）。"""

    status: str
    parent_workflow_id: str | None = None


def _native_status(state: _NativeWorkflowState | None) -> str | None:
    """取原生状态；缺行返回 ``None``（不伪终态）。"""

    return None if state is None else state.status


def _resolve_ownership(
    *,
    active_states: Mapping[str, _NativeWorkflowState],
    proven_states: Mapping[str, _NativeWorkflowState],
    registered_ids: Sequence[str],
    unproven_version_ids: Sequence[str],
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """按业务库登记归属解析原生在途集合（N06）。

    登记面来自当前业务库（``AgentDao``），原生运行库在同实例内固定复用，故原生
    在途行可能属于切库前的另一个业务库。归属判据（与手动恢复一致）：

    - 直接登记项——只有同时在 ``proven_states``（原生列举报回、已证同应用同版本）
      才可作归属依据，避免把别的制品版本登记项当父级；
    - 已归属 root 的直属/传递子项（``parent_workflow_id`` 链）；
    - 版本不可证登记项的子项随父按版本不可证处理。

    返回 ``(可清理集合, 版本不可证集合, 归属不可证集合)``：后两类均零改写；归属
    不可证项阻塞 launch，避免旧业务库执行被误清理或经 launch 恢复管线误恢复。
    """

    registered = set(registered_ids)
    proven = set(proven_states)
    version_unproven = set(unproven_version_ids)
    owned = registered & proven
    changed = True
    while changed:
        changed = False
        for workflow_id, state in active_states.items():
            if workflow_id in owned or workflow_id in version_unproven:
                continue
            parent_id = state.parent_workflow_id
            if parent_id in owned:
                owned.add(workflow_id)
                changed = True
            elif parent_id in version_unproven:
                version_unproven.add(workflow_id)
                changed = True
    unproven = frozenset(set(active_states) - owned - version_unproven)
    return frozenset(owned & set(active_states)), frozenset(version_unproven), unproven


async def _snapshot_native_workflows(
    client: WorkflowControlClient,
    *,
    application_name: str,
    app_version: str,
    workflow_names: Sequence[str],
) -> dict[str, _NativeWorkflowState]:
    """读满全部分页后返回 ``{workflow_id: 原生状态/父身份}``（固定集合后才取消）。"""

    states: dict[str, _NativeWorkflowState] = {}
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
            states[item.workflow_id] = _NativeWorkflowState(str(item.status), item.parent_workflow_id)
        if len(page) < ISOLATION_PAGE_SIZE:
            return states
        offset += len(page)


async def _list_states_by_id(
    client: WorkflowControlClient,
    workflow_ids: Sequence[str],
    *,
    application_name: str | None = None,
    app_version: str | None = None,
    workflow_names: Sequence[str] | None = None,
) -> dict[str, _NativeWorkflowState]:
    """按精确 ID 查回原生状态/父身份（分批；无行即不在结果中，不伪终态）。

    给 ``application_name`` / ``app_version`` / ``workflow_names`` 时带同一版本谓词
    （登记项补读必须与原生列举同谓词）；不给时按纯身份读（取消后复查用）。
    """

    states: dict[str, _NativeWorkflowState] = {}
    for start in range(0, len(workflow_ids), ISOLATION_PAGE_SIZE):
        chunk = list(workflow_ids[start : start + ISOLATION_PAGE_SIZE])
        page = await client.list_workflows_async(
            workflow_ids=chunk,
            application_name=application_name,
            app_version=app_version,
            name=None if workflow_names is None else list(workflow_names),
            limit=len(chunk),
            load_input=False,
            load_output=False,
        )
        for item in page:
            states[item.workflow_id] = _NativeWorkflowState(str(item.status), item.parent_workflow_id)
    return states


async def _list_necessary_leaf_ids(
    client: WorkflowControlClient, root_workflow_ids: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """解析每个 root 的「必要叶」原生 ID（HANDOFF §8.4 第 5 条）。

    必要叶 = 该 root 直属（``parent_workflow_id``）、名为叶 workflow、且处于
    ``RECOVERY_RESUMABLE_LEAF_STATUSES``（中断未结案）的子执行。成功叶零重做，
    已定失败叶（``ERROR``/恢复耗尽）保留其失败成果，均不纳入。只列举给定
    root 的直属子代，未选研究不被扩入。归属判据与启动隔离一致：root 取自当前
    业务库登记面，叶只认这些已归属 root 的直属子项，不按全局原生名扩大集合。
    分页读满后返回 ``root → 稳定排序叶集合``
    （保留父叶从属关系，供授权后按已授予 root 精确过滤）；列举异常冒泡给调用
    方（无法证明全集时不得部分恢复，``CancelledError`` 继续传播）。
    """

    leaf_map: dict[str, tuple[str, ...]] = {}
    for root_id in root_workflow_ids:
        leaf_ids: set[str] = set()
        offset = 0
        while True:
            page = await client.list_workflows_async(
                parent_workflow_id=root_id,
                name=LEAF_WORKFLOW_NAME,
                status=list(RECOVERY_RESUMABLE_LEAF_STATUSES),
                limit=ISOLATION_PAGE_SIZE,
                offset=offset,
                load_input=False,
                load_output=False,
            )
            for item in page:
                leaf_ids.add(item.workflow_id)
            if len(page) < ISOLATION_PAGE_SIZE:
                break
            offset += len(page)
        leaf_map[root_id] = tuple(sorted(leaf_ids))
    return leaf_map


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
    cancelled_ids: tuple[str, ...] = (),
    unproven_version_ids: tuple[str, ...] = (),
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
        cancelled_workflow_ids=cancelled_ids,
        unproven_version_workflow_ids=unproven_version_ids,
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
    cancelled_ids: tuple[str, ...] = (),
    unproven_version_ids: tuple[str, ...] = (),
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
        cancelled_ids=cancelled_ids,
        unproven_version_ids=unproven_version_ids,
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
    "INSTANCE_LOCK_KEY",
    "ISOLATION_PAGE_SIZE",
    "RECOVERY_RESUMABLE_LEAF_STATUSES",
    "STARTUP_ACTIVE_STATUSES",
    "STARTUP_WORKFLOW_NAMES",
    "TERMINAL_WORKFLOW_STATUSES",
    "VERSION_UNPROVEN",
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
