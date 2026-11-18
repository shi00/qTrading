"""叶运行链装配（R2-I01）：叶 runner 安装 + GatewayModel 工厂注入。

本模块只做**组装期接线**：

- 叶 runner 登记到 :class:`~services.agent_service.workflows.LeafRuntimeRegistry`
  （未安装时 ``require`` 明确拒绝，不以占位执行器冒充真实叶执行）；
- 模型桥经 :func:`build_gateway_model` 构造：``call_identity`` 固定取叶 workflow
  稳定身份（:func:`~services.agent_service.workflows.leaf_workflow_id`），回合调用号
  再由 :func:`~services.agent_service.model.derive_model_call_id` 确定性派生
  （R2-I41：恢复重放复得同一 ``model_call_id``，不新造随机身份）；
- 模型绑定解析 / 叶 Agent 执行以可注入端口表达：生产默认解析 ``agent_research``
  场景路由（未配置路由即显式失败、不静默换模型），叶 Agent 主体由后续执行分片
  接线；当前默认主体如实返回 ``failed``（错误码 ``leaf_agent_unavailable``），
  不伪造 ``complete`` 或空成果（R21）。

不在本模块：DAG 准入校验（派发前 ``validate_plan_spec_proposal``）、叶成果落库、
主会话/UI 研究界面。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from core.model_management_types import ExecutionSnapshot
from pydantic_ai.profiles import ModelProfile

from services.agent_service.model import ChatTurnExecutor, GatewayModel
from services.agent_service.workflows import (
    LeafExecutionContext,
    LeafRunner,
    LeafRuntimeRegistry,
    NodeOutcome,
    leaf_workflow_id,
)

logger = logging.getLogger(__name__)

#: 研究叶所属模型场景（模型配置路由键；未启用/无候选即 fail-closed 零模型外发）。
AGENT_RESEARCH_SCENARIO_ID: Final[str] = "agent_research"

#: 叶执行错误码：装配默认叶 Agent 尚未接线（不伪造成功/空成果）。
LEAF_AGENT_UNAVAILABLE: Final[str] = "leaf_agent_unavailable"

# NOTE(lazy): 工具 schema 版本以装配常量承载. ceiling: 当前工具 schema 只有 v1，无独立版本来源. upgrade: 工具 schema 版本化落地（MCP/Skill schema 自带版本）时改从来源读取.
AGENT_TOOL_SCHEMA_VERSION: Final[str] = "v1"


@dataclass(frozen=True, slots=True)
class LeafModelBinding:
    """一次叶执行的冻结模型绑定（执行器 + 派发时点快照 + 受理绑定）。"""

    executor: ChatTurnExecutor
    snapshot: ExecutionSnapshot
    binding: Mapping[str, Any]
    tool_schema_version: str = AGENT_TOOL_SCHEMA_VERSION
    profile: ModelProfile | None = None


#: 叶身份 → 冻结模型绑定（生产实现按场景路由解析；不可用即抛错，零模型外发）。
ModelBindingResolver = Callable[[LeafExecutionContext], Awaitable[LeafModelBinding]]

#: 叶 Agent 主体：给定模型桥与叶上下文产出叶终态事实（成果落库属执行分片）。
LeafAgentRunner = Callable[[GatewayModel, LeafExecutionContext], Awaitable[NodeOutcome]]


@dataclass(frozen=True, slots=True)
class LeafRuntimeWiring:
    """叶运行链装配参数（组合根可注入替身；缺省走生产默认）。"""

    model_binding: ModelBindingResolver
    agent_runner: LeafAgentRunner


async def resolve_agent_model_binding(context: LeafExecutionContext) -> LeafModelBinding:
    """生产默认模型绑定：按 ``agent_research`` 场景路由取执行器与冻结快照。

    路由未启用/无候选/调度服务未组装 → 明确失败（不静默换模型、零外发）。
    """

    from services.ai_service.scenario_dispatch import resolve_executor

    dispatch = await resolve_executor(AGENT_RESEARCH_SCENARIO_ID)
    if dispatch is None:
        raise RuntimeError(f"{AGENT_RESEARCH_SCENARIO_ID} route unavailable (zero model dispatch)")
    return LeafModelBinding(
        executor=dispatch.executor,
        snapshot=dispatch.snapshot,
        binding={"scenario_id": AGENT_RESEARCH_SCENARIO_ID, "task_id": context.node.node_id},
    )


async def unavailable_leaf_agent(model: GatewayModel, context: LeafExecutionContext) -> NodeOutcome:
    """默认叶 Agent 主体：执行分片未接线时如实报失败，不伪造成果（R21）。"""

    logger.warning(
        "[LeafRuntime] leaf agent runner not wired, leaf fails without outbound (execution=%s round=%s node=%s)",
        context.execution_id,
        context.round_id,
        context.node.node_id,
    )
    return NodeOutcome(node_id=context.node.node_id, state="failed", error_code=LEAF_AGENT_UNAVAILABLE)


def default_leaf_runtime() -> LeafRuntimeWiring:
    """生产默认装配：真实场景路由解析 + 如实失败的叶 Agent 主体。"""

    return LeafRuntimeWiring(model_binding=resolve_agent_model_binding, agent_runner=unavailable_leaf_agent)


def leaf_call_identity(context: LeafExecutionContext) -> str:
    """叶执行的持久执行身份（R2-I41：同一步骤重放复得同一调用号）。"""

    return leaf_workflow_id(
        execution_id=context.execution_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
    )


def build_gateway_model(context: LeafExecutionContext, binding: LeafModelBinding) -> GatewayModel:
    """GatewayModel 工厂：模型桥身份只由叶持久执行身份派生（禁随机身份）。"""

    return GatewayModel(
        binding.executor,
        binding.binding,
        binding.snapshot,
        tool_schema_version=binding.tool_schema_version,
        call_identity=leaf_call_identity(context),
        profile=binding.profile,
    )


def build_leaf_runner(*, model_binding: ModelBindingResolver, agent_runner: LeafAgentRunner) -> LeafRunner:
    """组合叶 runner：解析冻结绑定 → 构造模型桥 → 交叶 Agent 主体。"""

    async def _run(context: LeafExecutionContext) -> NodeOutcome:
        resolved = await model_binding(context)
        model = build_gateway_model(context, resolved)
        return await agent_runner(model, context)

    return _run


def install_leaf_runtime(wiring: LeafRuntimeWiring | None = None) -> None:
    """在生产组合根安装叶 runner（重复安装以最后一次为准，单实例契约）。"""

    runtime = wiring or default_leaf_runtime()
    LeafRuntimeRegistry.install(
        build_leaf_runner(model_binding=runtime.model_binding, agent_runner=runtime.agent_runner)
    )
    logger.info("[LeafRuntime] leaf runner installed (call identity derived from leaf workflow id)")
