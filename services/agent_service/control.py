"""执行控制命令族的服务侧补充（第四类 W-01）。

命令类型与状态机正本在 :mod:`core.agent_types`（``ExecutionControlKind`` /
``plan_control_transition`` / ``validate_control_request``），以便 data 层（DAO）
与 core 层共用同一判定而不违反分层（R1）。本模块只承载**依赖工作流类型的**
重试范围计算：``compute_retry_scope`` / ``build_retry_payload`` 属
:mod:`services.agent_service.workflows`，不能下沉到 core。

重试语义：

- 目标只接受可重试叶（``failed`` / ``ended``），未知/完成/进行中显式拒绝；
- ``redo`` 含目标及传递下游，范围外 ``complete`` 叶作为保留前置；
- 范围外前置非完成时由 ``build_retry_payload`` 显式拒绝，不以陈旧/无效前置
  构造新轮（不静默不满足依赖）。

暂停/终止取向（与 ``core.agent_types.NATIVE_CONTROL_NOT_WIRED`` 一致）：

- **不调用原生 cancel/resume**。DBOS 的 ``cancel_workflows_async`` 会把非
  SUCCESS/ERROR 行统一改写为 CANCELLED；``resume_workflows_async`` 只有在
  ``grant_recovery_epoch`` + 叶终态核对之后才可靠（见 ``recovery.py`` 的恢复
  入口注释）。由 UI 直接调用会绕过恢复 epoch 语义，并把仍取消的叶记成已结案。
- 故 **暂停 = 关闭本执行派发门 + 落 ``paused`` 事实**，**终止 = 关闭派发门 +
  轮次结束 + 明确 ``stop_reason``**；两者都**不影响在途调用**，在途步继续执行并
  按其原身份保存成果（终态投影可正常覆盖 ``paused``，不会被暂停吞掉）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.agent_types import (
    RETRYABLE_LEAF_DISPLAY_STATES,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    PlanSpecProposal,
    plan_spec_digest,
)
from services.agent_service.workflows import (
    NodeOutcome,
    RetryScope,
    build_retry_payload,
    compute_retry_scope,
)


class RetryLauncher(Protocol):
    """重试载荷的原生投递端口（未注入时服务只落重试事实，不伪造已重跑）。

    返回 ``(submitted, reason)``：``submitted=False`` 时 ``reason`` 说明未投递
    的真实原因；异常由调用方经 ``classify_error`` 降级为 ``dispatch_state=failed``。
    """

    async def launch(
        self,
        payload: dict[str, object],
        *,
        user_scope: str,
        execution_id: str,
        round_id: str,
    ) -> tuple[bool, str | None]: ...


@dataclass(frozen=True, slots=True)
class RetryPlan:
    """重试范围 + 父载荷（纯计算结果）。"""

    scope: RetryScope
    preserved_outcomes: tuple[NodeOutcome, ...]
    payload: dict[str, object]


def plan_retry(
    plan: PlanSpecProposal,
    *,
    targets: tuple[str, ...],
    node_states: dict[str, str],
    execution_id: str,
    new_round_id: str,
    plan_digest: str | None = None,
) -> RetryPlan:
    """按持久计划与已观察叶状态计算重试范围并构造新轮载荷。

    目标必须是可重试叶（``failed`` / ``ended``）；``redo`` 含目标及传递下游；
    范围外 ``complete`` 叶作为保留前置。任一不满足显式拒绝（不猜、不放行）。
    """

    identity = f"{execution_id}/{new_round_id}"
    if not targets:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "targets must be non-empty", identity)
    if len(set(targets)) != len(targets):
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "targets must be unique", identity)
    unknown = [node_id for node_id in targets if node_id not in node_states]
    if unknown:
        raise AgentServiceError(
            AgentErrorCode.INPUT_INVALID, f"unknown retry target: {','.join(sorted(unknown))}", identity
        )
    not_retryable = [node_id for node_id in targets if node_states.get(node_id) not in RETRYABLE_LEAF_DISPLAY_STATES]
    if not_retryable:
        raise AgentServiceError(
            AgentErrorCode.REVISION_CONFLICT,
            f"target is not retryable: {','.join(sorted(not_retryable))}",
            identity,
        )
    try:
        scope = compute_retry_scope(plan, targets=targets)
    except AgentValidationError as exc:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
    redo_ids = set(scope.redo)
    preserved = tuple(
        NodeOutcome(node_id=node_id, state="complete")
        for node_id in scope.preserved
        if node_id not in redo_ids and node_states.get(node_id) == "complete"
    )
    digest = plan_digest if plan_digest is not None else plan_spec_digest(plan)
    try:
        payload, computed = build_retry_payload(
            plan,
            execution_id=execution_id,
            round_id=new_round_id,
            plan_digest=digest,
            targets=targets,
            preserved_outcomes=preserved,
        )
    except AgentValidationError as exc:
        # 保留前置缺失/陈旧等均属输入不可满足：显式拒绝，不用非法载荷派发。
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
    return RetryPlan(scope=computed, preserved_outcomes=preserved, payload=payload)


__all__ = ["RetryLauncher", "RetryPlan", "plan_retry"]
