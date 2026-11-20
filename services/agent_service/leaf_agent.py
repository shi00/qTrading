"""叶 Agent 主体（DD §3.5 / §10.1 / §10.3；SQ-02）。

职责：把一次叶执行从「冻结模型桥 + 叶上下文」推进为「已可靠保存的成果 + 终态事实」。

- **单次运行、无工具**：本分片只构造 pydantic-ai ``Agent``（``defer_model_check=True``、
  ``retries=0``、空工具集）并执行一次 ``run``；工具循环 / DeferredToolRequests 续跑
  属独立分片，空工具集是合法形态（子角色空工具集仍为空）；
- **成果即已保存版本**：``ResearchResult`` 不是模型流末块——本模块把最终文本经
  既有 ``AgentDao.save_research_result`` 单事务保存，只有保存确认（
  ``final_output_saved=True``）才返回 ``complete``；
- **边界消费**：``ResearchBoundary`` 装入 Agent ``capabilities``，每次模型请求前按
  最新来源事实与上下文窗口判定（I65 的生产消费方）；
- **不伪造**：输入指纹 / 归属事实 / 实际模型名 / 非空文本任一不可得即如实失败
  （R21）；``CancelledError`` 原样传播（R2）。

不在本模块：DAG 准入、工具授权、摘要压缩、账本结算、UI。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from pydantic_ai import Agent, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ResearchResult,
    ResearchResultDraft,
    is_digest,
)
from services.agent_service.capability_wiring import build_leaf_boundary
from services.agent_service.leaf_runtime import (
    AGENT_RESEARCH_SCENARIO_ID,
    LeafAgentRunner,
    LeafExecutionContext,
    LeafModelBinding,
)
from services.agent_service.model import GatewayModel
from services.agent_service.workflows import NodeOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "LEAF_AGENT_NAME",
    "LEAF_ATTRIBUTION_UNAVAILABLE",
    "LEAF_EMPTY_RESULT",
    "LEAF_INPUT_DIGEST_MISSING",
    "LEAF_MODEL_FAILED",
    "LEAF_MODEL_REF_UNAVAILABLE",
    "LEAF_POLICY_DENIED",
    "LEAF_RESULT_KIND",
    "LEAF_RESULT_SAVE_FAILED",
    "DaoLeafAttributionReader",
    "DaoLeafResultStore",
    "LeafAgentDeps",
    "LeafAttributionFacts",
    "LeafResultStore",
    "build_leaf_agent_runner",
    "run_leaf_agent",
]

#: 叶 Agent 名称（设计 §10.1 固定装配名）。
LEAF_AGENT_NAME: Final[str] = "qtrading_research"
#: 叶成果类别（研究答复，不冒充股票 final/candidate；按角色映射属后续分片）。
LEAF_RESULT_KIND: Final[str] = "explanation"

#: 失败码（NodeOutcome.error_code；稳定前缀 ``leaf_*``）。
LEAF_INPUT_DIGEST_MISSING: Final[str] = "leaf_input_digest_missing"
LEAF_ATTRIBUTION_UNAVAILABLE: Final[str] = "leaf_attribution_unavailable"
LEAF_MODEL_REF_UNAVAILABLE: Final[str] = "leaf_model_ref_unavailable"
LEAF_EMPTY_RESULT: Final[str] = "leaf_empty_result"
LEAF_MODEL_FAILED: Final[str] = "leaf_model_failed"
LEAF_POLICY_DENIED: Final[str] = "leaf_policy_denied"
LEAF_RESULT_SAVE_FAILED: Final[str] = "leaf_result_save_failed"

# NOTE(lazy): 叶 instructions 使用内置最小模板. ceiling: 未接角色/Prompt 资产服务，所有角色共用同一指令.
# upgrade: 角色 Prompt 资产接线（R02/E 系列）时改为按 role_key + 版本读取.


@dataclass(frozen=True, slots=True)
class LeafAgentDeps:
    """叶运行的纯引用依赖（边界能力经 ``RunContext.deps`` 取 user_scope/execution_id）。"""

    user_scope: str
    execution_id: str
    session_id: str
    round_id: str
    node_id: str


@dataclass(frozen=True, slots=True)
class LeafAttributionFacts:
    """叶成果的权威归属事实（来自 ``AgentDao.read_leaf_dispatch_facts``）。"""

    user_scope: str
    session_id: str
    input_revision: int


class LeafAttributionReader(Protocol):
    """归属事实只读端口（生产为 ``AgentDao``）。"""

    async def read(self, execution_id: str, round_id: str) -> LeafAttributionFacts: ...


class LeafResultStore(Protocol):
    """成果可靠保存端口（生产为 ``AgentDao.save_research_result``）。"""

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult: ...


class DaoLeafAttributionReader:
    """``AgentDao.read_leaf_dispatch_facts`` 的端口适配（不新增 DAO 方法）。"""

    def __init__(self, dao: Any) -> None:
        self._dao = dao

    async def read(self, execution_id: str, round_id: str) -> LeafAttributionFacts:
        facts = await self._dao.read_leaf_dispatch_facts(execution_id, round_id)
        return LeafAttributionFacts(
            user_scope=facts.user_scope,
            session_id=facts.session_id,
            input_revision=int(facts.input_revision),
        )


class DaoLeafResultStore:
    """``AgentDao.save_research_result`` 的端口适配（保存事实由 DAO 核定）。"""

    def __init__(self, dao: Any) -> None:
        self._dao = dao

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult:
        return cast(ResearchResult, await self._dao.save_research_result(draft))


def _leaf_instructions(context: LeafExecutionContext) -> str:
    """叶指令（最小内置模板；不承诺已完成目标，不虚构资料）。"""

    return (
        "你是单层研究子代理，只能完成当前节点目标，不能委派其他 Agent，也不能调用工具。\n"
        "只依据已提供的输入与公开事实作答；不得臆造数据、来源或时点。\n"
        "如资料不足、口径不可比或存在缺口，必须在答复中明确说明限制与缺口，不得以推测补齐。\n"
        f"节点角色：{context.node.role_key}；节点目标：{context.node.target}"
    )


def _leaf_prompt(context: LeafExecutionContext) -> str:
    """叶用户提示（只含节点目标，不注入未获准的历史或无关会话）。"""

    return f"请完成研究节点目标：{context.node.target}"


def _actual_model_ref(result: Any) -> str | None:
    """从运行消息中取真实模型名（最后一个非空 ``ModelResponse.model_name``）。"""

    for message in reversed(result.all_messages()):
        if not isinstance(message, ModelResponse):
            continue
        model_name = message.model_name
        if isinstance(model_name, str) and model_name:
            return model_name
    return None


def _failed(context: LeafExecutionContext, code: str) -> NodeOutcome:
    """叶失败终态（不写成果、不伪造 complete）。"""

    return NodeOutcome(node_id=context.node.node_id, state="failed", error_code=code)


def _resolve_agent_dao(dao: Any | None) -> Any | None:
    """未显式注入时惰性取共享 ``CacheManager().agent_dao``（不新建引擎）。"""

    if dao is not None:
        return dao
    from data.cache.cache_manager import CacheManager

    return getattr(CacheManager(), "agent_dao", None)


async def run_leaf_agent(
    model: GatewayModel,
    context: LeafExecutionContext,
    binding: LeafModelBinding,
    *,
    dao: Any,
    result_kind: str = LEAF_RESULT_KIND,
) -> NodeOutcome:
    """执行一次叶 Agent 回合并保存成果；失败如实返回 ``failed``（R21）。"""

    digest = context.input_digest
    if digest is None or not is_digest(digest):
        logger.warning(
            "[LeafAgent] input digest missing, leaf fails without save (execution=%s round=%s node=%s)",
            context.execution_id,
            context.round_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_INPUT_DIGEST_MISSING)

    try:
        facts = await DaoLeafAttributionReader(dao).read(context.execution_id, context.round_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[LeafAgent] attribution facts unavailable: %s (execution=%s round=%s node=%s)",
            type(e).__name__,
            context.execution_id,
            context.round_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_ATTRIBUTION_UNAVAILABLE)

    boundary = build_leaf_boundary(
        dao,
        snapshot=binding.snapshot,
        scenario_id=AGENT_RESEARCH_SCENARIO_ID,
    )
    agent: Agent[LeafAgentDeps, str] = Agent(
        model,
        name=LEAF_AGENT_NAME,
        deps_type=LeafAgentDeps,
        output_type=str,
        defer_model_check=True,
        retries=0,
        instructions=_leaf_instructions(context),
        tools=(),
        capabilities=(boundary,),
    )
    deps = LeafAgentDeps(
        user_scope=facts.user_scope,
        execution_id=context.execution_id,
        session_id=facts.session_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
    )
    try:
        result = await agent.run(_leaf_prompt(context), deps=deps)
    except asyncio.CancelledError:
        raise
    except AgentServiceError as e:
        code = LEAF_POLICY_DENIED if e.code is AgentErrorCode.POLICY_DENIED else f"leaf_{e.code.value}"
        logger.warning(
            "[LeafAgent] leaf model turn denied/failed: %s (execution=%s node=%s)",
            e.code.value,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, code)
    except UnexpectedModelBehavior as e:
        logger.warning(
            "[LeafAgent] leaf model turn failed: %s (execution=%s node=%s)",
            type(e).__name__,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_MODEL_FAILED)

    text = result.output if isinstance(result.output, str) else ""
    if not text.strip():
        logger.warning(
            "[LeafAgent] empty final text, leaf fails without save (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_EMPTY_RESULT)
    model_ref = _actual_model_ref(result)
    if model_ref is None:
        logger.warning(
            "[LeafAgent] actual model name unavailable, leaf fails without save (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_MODEL_REF_UNAVAILABLE)

    draft = ResearchResultDraft(
        user_scope=facts.user_scope,
        session_id=facts.session_id,
        execution_id=context.execution_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
        input_revision=facts.input_revision,
        actual_input_digest=digest,
        actual_model_ref=model_ref,
        result_kind=result_kind,
        summary=None,
        key_points=(),
        body_text=text,
        structured_fields=None,
        object_rows=(),
        evidence_links=(),
        source_available_state="unknown",
        coverage=None,
        missing_fields=(),
        failed_signal_dates=(),
        data_warnings=(),
        limitations=(),
        verification_state="unverified",
        execution_state="complete",
        goal_coverage="unknown",
        convergence_confirmed=False,
        unknown_operation_refs=(),
        ledger_refs=(),
        plan_version=None,
        parent_artifact_refs=(),
        source_refs=(),
    )
    try:
        saved = await DaoLeafResultStore(dao).save_research_result(draft)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[LeafAgent] research result save failed: %s (execution=%s node=%s)",
            type(e).__name__,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_RESULT_SAVE_FAILED)
    if not isinstance(saved, ResearchResult) or saved.final_output_saved is not True:
        logger.warning(
            "[LeafAgent] research result save not confirmed (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_RESULT_SAVE_FAILED)
    return NodeOutcome(
        node_id=context.node.node_id,
        state="complete",
        error_code=None,
        artifact_ref=f"{saved.artifact_id}@{saved.artifact_version}",
        input_digest=digest,
    )


def build_leaf_agent_runner(dao: Any | None = None) -> LeafAgentRunner:
    """生产叶主体工厂：显式 DAO 优先，缺省惰性取共享 ``CacheManager``。"""

    async def _runner(
        model: GatewayModel,
        context: LeafExecutionContext,
        binding: LeafModelBinding,
    ) -> NodeOutcome:
        try:
            resolved = _resolve_agent_dao(dao)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[LeafAgent] agent dao unavailable: %s (execution=%s node=%s)",
                type(e).__name__,
                context.execution_id,
                context.node.node_id,
            )
            resolved = None
        if resolved is None:
            logger.warning(
                "[LeafAgent] agent dao unavailable, leaf fails without outbound (execution=%s node=%s)",
                context.execution_id,
                context.node.node_id,
            )
            return _failed(context, LEAF_ATTRIBUTION_UNAVAILABLE)
        return await run_leaf_agent(model, context, binding, dao=resolved)

    return _runner
