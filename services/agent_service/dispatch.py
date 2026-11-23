"""研究运行链派发接线（R2-I01）：受理回执 → 持久父 workflow。

语义边界：

- **只投递已可靠保存的受理**：``commit_state`` 非 ``COMMITTED`` 一律零派发；
- **门闩关闭即零派发**：宿主未 launch（配置门闩关闭）或关闭链已关派发时返回
  ``submitted=False`` 与原因，受理事实保持原样（不把「未派发」写成「已派发」）；
- **门闩关闭/计划缺失/投递失败都不产生研究执行**；``CancelledError`` 原样传播
  （R2），其余失败如实进 ``reason``，不伪造已派发（R21）；
- **重复派发幂等**：host 入口以稳定 ``workflow_id`` + ``return-existing`` 启动，
  同身份重放返回原执行，不重复启动研究。

计划来源由调用方注入（模型提交并经 DAG 准入校验后保存的计划版，属派发前
准入职责）；结果读回经注入端口读取持久 workflow 的可核对状态与输出。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Set as AbstractSet
from dataclasses import dataclass
from typing import Any, Final

from core.agent_types import (
    CommitState,
    Mode,
    PlanSpecProposal,
    SubmissionReceipt,
    derive_round_id,
    plan_spec_digest,
)
from services.agent_service.runtime import DispatchGate
from services.agent_service.workflows import admit_research_plan, build_research_payload
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

#: 零派发原因：门闩关闭（未 launch 或关闭链已关派发）。
DISPATCH_CLOSED: Final[str] = "dispatch_closed"
#: 零派发原因：派发前计划不可得（未提交/未保存计划版）。
PLAN_UNAVAILABLE: Final[str] = "plan_unavailable"
#: 零派发原因：受理事实未提交，不得投递。
NOT_COMMITTED: Final[str] = "not_committed"
#: 零派发原因：非执行模式（讨论/计划/直答）不得启动研究操作（F21 / R2-I08）。
MODE_RESEARCH_DISABLED: Final[str] = "mode_research_disabled"
#: 零派发原因：计划准入无角色能力策略来源（不以提案自述能力自授权，R2-I09）。
PLAN_ADMISSION_UNCONFIGURED: Final[str] = "plan_admission_unconfigured"
#: 零派发原因：角色能力来源读取失败（如实记录脱敏原因，不伪造准入策略）。
CAPABILITY_POLICY_UNAVAILABLE: Final[str] = "capability_policy_unavailable"

#: 计划来源端口：``(user_scope, execution_id) -> 已保存计划版``；不可得返回 None。
PlanSpecProvider = Callable[[str, str], Awaitable[PlanSpecProposal | None]]
#: 角色能力来源端口：``(user_scope, execution_id) -> 本次请求冻结的手选策略``。
#: 返回 ``None`` 表示不可判定（未选择/无绑定角色），派发 fail-closed。
RoleCapabilityProvider = Callable[[str, str], Awaitable[Mapping[str, AbstractSet[str]] | None]]

#: 父 workflow 启动端口（同步宿主入口须由实现经受管线程，不堵 UI，R16）。
RootWorkflowSubmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ResearchWorkflowResult:
    """持久 workflow 的可核对结果：原生状态 + 成功时的可读回输出。"""

    workflow_id: str
    state: str
    output: dict[str, Any] | None


#: 结果读回端口：按稳定 workflow 身份读原生状态与输出（不等待、不推断）。
RootWorkflowResultReader = Callable[[str], Awaitable[ResearchWorkflowResult]]


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """一次派发尝试的回执：``submitted`` 只表示已投递到持久 workflow。"""

    execution_id: str
    workflow_id: str
    submitted: bool
    reason: str | None = None
    round_id: str | None = None


class ResearchDispatcher:
    """受理后派发入口（组合根注入；无构建即无派发能力）。"""

    def __init__(
        self,
        *,
        gate: DispatchGate,
        submit: RootWorkflowSubmitter,
        plan_provider: PlanSpecProvider | None = None,
        read_result: RootWorkflowResultReader | None = None,
        role_capabilities: Mapping[str, AbstractSet[str]] | None = None,
        capability_provider: RoleCapabilityProvider | None = None,
    ) -> None:
        self._gate = gate
        self._submit = submit
        self._plan_provider = plan_provider
        self._read_result = read_result
        self._role_capabilities = role_capabilities
        self._capability_provider = capability_provider

    async def dispatch(
        self, receipt: SubmissionReceipt, *, user_scope: str, mode: Mode | None = None
    ) -> DispatchOutcome:
        """把已可靠保存的受理投递给父研究 workflow；不可派发时如实零派发。

        ``mode`` 由调用方传入受理时冻结的模式：讨论/计划/直答只交付答案或计划，
        **不启动研究操作**（F21 / R2-I08）。``None`` 保持既有调用语义（未提供模式
        时不额外收窄），生产受理入口始终传入冻结模式。
        """

        execution_id = receipt.execution_id
        if receipt.commit_state is not CommitState.COMMITTED:
            # 未提交 / 提交未知都不得投递，且两者保持可区分（INV-07 不折叠 unknown）
            return DispatchOutcome(
                execution_id,
                receipt.workflow_id,
                False,
                f"{NOT_COMMITTED}:{receipt.commit_state.value}",
            )
        if mode is not None and mode is not Mode.EXECUTE:
            logger.info(
                "[ResearchDispatch] non-execute mode, zero dispatch (execution=%s workflow=%s mode=%s)",
                execution_id,
                receipt.workflow_id,
                mode.value,
            )
            return DispatchOutcome(
                execution_id,
                receipt.workflow_id,
                False,
                f"{MODE_RESEARCH_DISABLED}:{mode.value}",
            )
        if self._gate.dispatch_closed():
            logger.info(
                "[ResearchDispatch] dispatch closed, zero dispatch (execution=%s workflow=%s)",
                execution_id,
                receipt.workflow_id,
            )
            return DispatchOutcome(execution_id, receipt.workflow_id, False, DISPATCH_CLOSED)
        plan: PlanSpecProposal | None
        if self._plan_provider is None:
            plan = None
        else:
            try:
                plan = await self._plan_provider(user_scope, execution_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[ResearchDispatch] plan provider failed, zero dispatch (execution=%s workflow=%s): %s",
                    execution_id,
                    receipt.workflow_id,
                    DataSanitizer.sanitize_error(exc),
                )
                return DispatchOutcome(
                    execution_id,
                    receipt.workflow_id,
                    False,
                    f"plan_unavailable:{type(exc).__name__}",
                )
        if plan is None:
            logger.warning(
                "[ResearchDispatch] plan unavailable, zero dispatch (execution=%s workflow=%s)",
                execution_id,
                receipt.workflow_id,
            )
            return DispatchOutcome(execution_id, receipt.workflow_id, False, PLAN_UNAVAILABLE)

        round_id = derive_round_id(
            user_scope=user_scope,
            execution_id=execution_id,
            workflow_id=receipt.workflow_id,
        )
        if self._role_capabilities is None and self._capability_provider is None:
            # 无角色能力策略即无准入判据：不以提案自述能力自授权（R2-I09）。
            logger.warning(
                "[ResearchDispatch] plan admission policy missing, zero dispatch (execution=%s workflow=%s)",
                execution_id,
                receipt.workflow_id,
            )
            return DispatchOutcome(execution_id, receipt.workflow_id, False, PLAN_ADMISSION_UNCONFIGURED)
        policy, policy_error = await self._resolve_role_capabilities(user_scope, execution_id)
        if policy is None:
            logger.warning(
                "[ResearchDispatch] plan admission policy unavailable, zero dispatch (execution=%s workflow=%s): %s",
                execution_id,
                receipt.workflow_id,
                policy_error or PLAN_ADMISSION_UNCONFIGURED,
            )
            return DispatchOutcome(
                execution_id,
                receipt.workflow_id,
                False,
                policy_error or PLAN_ADMISSION_UNCONFIGURED,
            )
        try:
            plan_digest = plan_spec_digest(plan)
            # 派发前准入：计划版绑定 + 任务依赖图/角色/能力校验（非法图零派发）。
            admit_research_plan(plan, plan_digest=plan_digest, role_capabilities=policy)
            payload = build_research_payload(
                plan,
                execution_id=execution_id,
                round_id=round_id,
                plan_digest=plan_digest,
                role_capabilities=policy,
            )
        except Exception as exc:
            logger.warning(
                "[ResearchDispatch] plan payload invalid, zero dispatch (execution=%s workflow=%s): %s",
                execution_id,
                receipt.workflow_id,
                DataSanitizer.sanitize_error(exc),
            )
            return DispatchOutcome(execution_id, receipt.workflow_id, False, f"plan_invalid:{type(exc).__name__}")
        try:
            await self._submit(receipt.workflow_id, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[ResearchDispatch] submit failed, zero dispatch (execution=%s workflow=%s): %s",
                execution_id,
                receipt.workflow_id,
                DataSanitizer.sanitize_error(exc),
            )
            return DispatchOutcome(execution_id, receipt.workflow_id, False, f"submit_failed:{type(exc).__name__}")
        return DispatchOutcome(execution_id, receipt.workflow_id, True, None, round_id)

    async def _resolve_role_capabilities(
        self, user_scope: str, execution_id: str
    ) -> tuple[Mapping[str, AbstractSet[str]] | None, str | None]:
        """按请求解析角色能力策略：优先逐请求来源，回退构造期静态映射。

        逐请求来源抛错时返回 ``(None, 原因)``（零派发）；``CancelledError`` 原样
        传播（R2）。不做「读取失败即视为无限制」的降级（R21）。
        """

        provider = self._capability_provider
        if provider is None:
            return self._role_capabilities, None
        try:
            policy = await provider(user_scope, execution_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return None, f"{CAPABILITY_POLICY_UNAVAILABLE}:{type(exc).__name__}"
        if policy is None:
            return None, None
        return policy, None

    async def read_result(self, workflow_id: str) -> ResearchWorkflowResult:
        """读回派发结果的持久事实（原生状态 + 成功输出）；未接线即显式拒绝。"""

        reader = self._read_result
        if reader is None:
            raise RuntimeError("research workflow result reader not wired")
        return await reader(workflow_id)
