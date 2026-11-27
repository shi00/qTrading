"""叶 Agent 主体（DD §3.5 / §10.1 / §10.3；SQ-02）。

职责：把一次叶执行从「冻结模型桥 + 叶上下文」推进为「已可靠保存的成果 + 终态事实」。

- **单次运行、无工具**：本分片只构造 pydantic-ai ``Agent``（``defer_model_check=True``、
  ``retries=0``、空工具集）并执行一次 ``run``；工具循环 / DeferredToolRequests 续跑
  属独立分片，空工具集是合法形态（子角色空工具集仍为空）；
- **成果即已保存版本**：``ResearchResult`` 不是模型流末块——本模块把最终文本经
  既有 ``AgentDao.save_research_result`` 单事务保存，只有保存确认（
  ``final_output_saved=True``）才返回 ``complete``；
- **边界消费**：``ResearchBoundary`` 装入 Agent ``capabilities``，每次模型请求前按
  最新来源事实与上下文窗口判定（I65 的生产消费方）；窗口取真实授权连接，其余
  候选窗口取最严者，并预留输出 token；
- **角色 Prompt 单一来源**：叶指令 = 设置页已保存的「子研究角色」默认正文 + 用户
  追加（运行装配时一次解析并冻结），不保留与本设置无关的内置指令旁路；自定义角色
  按冻结的 ``(role_id, role_version)`` 定义取正文，不回落预置正文；
- **不伪造**：输入指纹 / 归属事实 / 实际模型名 / 非空文本任一不可得即如实失败
  （R21）；``CancelledError`` 原样传播（R2）。

不在本模块：DAG 准入、工具授权、摘要压缩、账本结算、UI。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, cast

from pydantic_ai import Agent, DeferredToolRequests, UnexpectedModelBehavior
from pydantic_ai.agent import EventStreamHandler
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import RunTimedOut
from pydantic_ai.messages import (
    FinalResultEvent,
    FunctionToolCallEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ArtifactReadQuery,
    ResearchResult,
    ResearchResultDraft,
    RoleDefinition,
    is_digest,
)
from core.model_management_types import ModelTurnProtocolError
from services.agent_service.capability_wiring import build_leaf_boundary
from services.agent_service.context_compaction import build_leaf_compaction
from services.agent_service.leaf_runtime import (
    AGENT_RESEARCH_SCENARIO_ID,
    AdmissionCountingToolExecutor,
    LeafAgentRunner,
    LeafDeadlineExhaustedError,
    LeafDeadlineUnavailableError,
    LeafExecutionContext,
    LeafModelBinding,
    require_leaf_turn_timeout,
)
from services.agent_service.model import GatewayModel, ModelTurnRefusedError
from services.agent_service.tool_loop import ToolLoopPorts, run_agent_tool_loop
from services.agent_service.tool_runtime import ToolRunScope, ToolRuntimeFactory
from services.agent_service.workflows import NodeOutcome

logger = logging.getLogger(__name__)

__all__ = [
    "LEAF_AGENT_NAME",
    "LEAF_ATTRIBUTION_UNAVAILABLE",
    "LEAF_BINDING_REVISION_STALE",
    "LEAF_BINDING_REVISION_UNAVAILABLE",
    "LEAF_DEADLINE_EXHAUSTED",
    "LEAF_EMPTY_RESULT",
    "LEAF_INPUT_DIGEST_MISSING",
    "LEAF_MODEL_FAILED",
    "LEAF_MODEL_PROTOCOL_FAILED",
    "LEAF_MODEL_REFUSED",
    "LEAF_MODEL_REF_UNAVAILABLE",
    "LEAF_POLICY_DENIED",
    "LEAF_PREDECESSOR_ARTIFACT_UNAVAILABLE",
    "LEAF_RESULT_KIND",
    "LEAF_RESULT_SAVE_FAILED",
    "LEAF_RESULT_SAVE_UNCONFIRMED",
    "LEAF_ROLE_PROMPT_KEY",
    "LEAF_ROLE_PROMPT_UNAVAILABLE",
    "LEAF_TIMEOUT_UNAVAILABLE",
    "LEAF_TOOL_BUDGET_EXCEEDED",
    "LEAF_TOOL_RESULT_NOT_SAVED",
    "LEAF_TOOL_SETUP_FAILED",
    "DaoLeafAttributionReader",
    "DaoLeafResultStore",
    "LeafAgentDeps",
    "LeafAttributionFacts",
    "LeafEventHandler",
    "LeafResultStore",
    "LeafStreamEvent",
    "RolePromptIdentity",
    "RolePromptReader",
    "build_leaf_agent_runner",
    "run_leaf_agent",
]

#: 叶 Agent 名称（设计 §10.1 固定装配名）。
LEAF_AGENT_NAME: Final[str] = "qtrading_research"
#: 叶成果类别（研究答复，不冒充股票 final/candidate；按角色映射属后续分片）。
LEAF_RESULT_KIND: Final[str] = "explanation"

#: 叶消费的设置页研究角色键：叶是「子研究角色」（默认正文 + 用户追加的唯一来源）。
LEAF_ROLE_PROMPT_KEY: Final[str] = "sub"

#: 失败码（NodeOutcome.error_code；稳定前缀 ``leaf_*``）。
LEAF_INPUT_DIGEST_MISSING: Final[str] = "leaf_input_digest_missing"
LEAF_ATTRIBUTION_UNAVAILABLE: Final[str] = "leaf_attribution_unavailable"
LEAF_BINDING_REVISION_UNAVAILABLE: Final[str] = "leaf_binding_revision_unavailable"
LEAF_BINDING_REVISION_STALE: Final[str] = "leaf_binding_revision_stale"
LEAF_PREDECESSOR_ARTIFACT_UNAVAILABLE: Final[str] = "leaf_predecessor_artifact_unavailable"
LEAF_MODEL_REF_UNAVAILABLE: Final[str] = "leaf_model_ref_unavailable"
LEAF_EMPTY_RESULT: Final[str] = "leaf_empty_result"
LEAF_MODEL_FAILED: Final[str] = "leaf_model_failed"
LEAF_POLICY_DENIED: Final[str] = "leaf_policy_denied"
LEAF_RESULT_SAVE_FAILED: Final[str] = "leaf_result_save_failed"
#: 保存确认丢失且按原成果身份无法证明已保存（未知，不固化失败，R21）。
LEAF_RESULT_SAVE_UNCONFIRMED: Final[str] = "leaf_result_save_unconfirmed"
LEAF_ROLE_PROMPT_UNAVAILABLE: Final[str] = "leaf_role_prompt_unavailable"
LEAF_TOOL_SETUP_FAILED: Final[str] = "leaf_tool_setup_failed"
LEAF_TOOL_BUDGET_EXCEEDED: Final[str] = "tool_budget_exceeded"
LEAF_TOOL_RESULT_NOT_SAVED: Final[str] = "tool_result_not_saved"
#: 绑定期限不可核验 / 已耗尽（不发起任何模型外发，不用默认预算兜底）。
LEAF_TIMEOUT_UNAVAILABLE: Final[str] = "leaf_timeout_unavailable"
LEAF_DEADLINE_EXHAUSTED: Final[str] = "leaf_deadline_exhausted"
#: 桥层协议事实失败（如截断流无 finish 确认 / 收尾未核）：不保存成果（R21）。
LEAF_MODEL_PROTOCOL_FAILED: Final[str] = "leaf_model_protocol_failed"
#: 提供方确定内容拒绝（完成的模型回合，Executor 已 halt 且保住用量）：与协议 /
#: 普通模型失败分类不同，不保存成果；拒绝原文不进入终态 / 日志（R9/R21）。
LEAF_MODEL_REFUSED: Final[str] = "leaf_model_refused"


#: 叶模型回合公开事件的受治理类别（不含思考、工具参数、供应商载荷）。
LeafStreamEventKind = Literal["text_delta", "tool_call", "finish"]


@dataclass(frozen=True, slots=True)
class LeafStreamEvent:
    """叶模型回合的受治理公开事件。

    只承载展示所需事实：``text_delta`` 为最终将被保存的正文分片，``tool_name``
    仅为已公开的工具名；不含模型调用身份 / attempt / 工具参数 / 思考内容 /
    供应商载荷（不泄漏内部状态）。
    """

    kind: LeafStreamEventKind
    sequence: int
    text_delta: str | None = None
    tool_name: str | None = None


#: 事件回调（可返回可等待对象，消费方 await 透传背压；异常被隔离，不影响成果）。
LeafEventHandler = Callable[[LeafStreamEvent], object]


@dataclass(frozen=True, slots=True)
class RolePromptIdentity:
    """一次叶请求冻结的角色 Prompt 身份：合成正文 + 已保存版本（缺省为默认版本）。

    正文按设置页同一合同合成（默认只读正文 + 用户追加），在运行装配时**一次性**
    解析并冻结进 ``Agent(instructions=...)``，不在回合间重读设置。
    """

    text: str
    version: str


#: 角色 Prompt 读取端口（生产读设置页已保存的默认+追加；测试可注入替身）。
RolePromptReader = Callable[[str], RolePromptIdentity]


def _read_saved_role_prompt(
    role_key: str,
    *,
    definition: RoleDefinition | None = None,
    expected_version: str | None = None,
) -> RolePromptIdentity:
    """读角色 Prompt：预置角色读设置页保存值，自定义角色取冻结定义正文。

    预置角色（``main`` / ``sub``）走原逻辑：默认正文 + 用户追加，缺键按默认，
    不伪造内容。自定义角色必须传入冻结定义 ``definition``，且状态为 ``active``、
    版本与 ``expected_version``（调用方冻结的 ``role_version``，若可拿到）一致，
    否则按 ``CONFIG_UNAVAILABLE`` 显式失败——不回落预置正文、不读设置页。

    追加中的占位符（如 ``{company}``）在叶内**不**替换：叶没有对应真实取数上下文，
    替换即伪造取值；设置页的变量预览仅用于核对。
    """

    from utils.config_handler import ConfigHandler
    from utils.config.llm import RESEARCH_ROLE_KEYS
    from utils.config_models import ROLE_PROMPT_DEFAULTS

    if role_key not in RESEARCH_ROLE_KEYS:
        if definition is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "custom role definition missing", role_key)
        identity = f"{role_key}@{definition.role_version}"
        if definition.status != "active":
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "custom role definition not active", identity)
        if expected_version is not None and definition.role_version != expected_version:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE, "custom role definition version mismatch", identity
            )
        if definition.role_id != role_key:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                f"custom role definition role_id mismatch: {role_key} vs {definition.role_id}",
                identity,
            )
        return RolePromptIdentity(text=definition.prompt_body, version=definition.role_version)

    default_text, default_version = ROLE_PROMPT_DEFAULTS[role_key]
    append = ConfigHandler.get_ai_role_prompt_append(role_key)
    version = ConfigHandler.get_ai_role_prompt_version(role_key)
    text = default_text if not append.strip() else f"{default_text}\n\n{append}"
    return RolePromptIdentity(text=text, version=version or default_version)


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


def _leaf_instructions(context: LeafExecutionContext, prompt: RolePromptIdentity) -> str:
    """叶指令：设置页已保存的角色 Prompt（默认 + 追加）+ 本节点冻结身份。

    不再使用与本设置无关的内置指令旁路：角色正文/追加只从单一来源（设置页保存值）
    读取，并在运行装配时冻结；节点身份（role_key / target）只补充本节点事实。
    """

    return f"{prompt.text}\n节点角色：{context.node.role_key}；节点目标：{context.node.target}"


async def _resolve_role_prompt(reader: RolePromptReader | None) -> RolePromptIdentity:
    """解析并冻结本次运行的角色 Prompt（同步配置读取经 IO 线程池，R16）。"""

    if reader is not None:
        return reader(LEAF_ROLE_PROMPT_KEY)
    from utils.thread_pool import TaskType, ThreadPoolManager

    return await ThreadPoolManager().run_async(TaskType.IO, _read_saved_role_prompt, LEAF_ROLE_PROMPT_KEY)


def _leaf_prompt(context: LeafExecutionContext, predecessor_summaries: tuple[str, ...] = ()) -> str:
    """叶用户提示：节点目标 + 已核定前置成果摘要（不注入未获历史或无关会话）。"""

    prompt = f"请完成研究节点目标：{context.node.target}"
    if predecessor_summaries:
        prompt += "\n\n已核定的前置成果摘要：\n" + "\n".join(predecessor_summaries)
    return prompt


def _split_artifact_ref(artifact_ref: str) -> tuple[str, int]:
    """``artifact_id@version`` → 精确身份；形状不符即拒绝（不猜版本）。"""

    artifact_id, _, version_text = artifact_ref.partition("@")
    if not artifact_id or not version_text.isdigit():
        raise ValueError("predecessor artifact ref is not 'artifact_id@version'")
    return artifact_id, int(version_text)


async def _read_predecessor_artifacts(
    dao: Any, context: LeafExecutionContext, facts: LeafAttributionFacts
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """读取并核对本轮前置成果（同名轮次/同输入版/正文可用）。

    任一前置不可读、跨执行/轮次/会话、输入版不一致或正文不可用即抛错——叶零
    外发（不以后继无前置成果的猜测继续，R21）。返回 ``(引用表, 摘要表)``。
    """

    refs: list[str] = []
    summaries: list[str] = []
    for node_id, artifact_ref in context.predecessor_artifacts:
        artifact_id, artifact_version = _split_artifact_ref(artifact_ref)
        read = await dao.read_artifact(
            ArtifactReadQuery(
                artifact_id=artifact_id,
                artifact_version=artifact_version,
                execution_id=context.execution_id,
                round_id=context.round_id,
            ),
            user_scope=facts.user_scope,
        )
        if (
            read.session_id != facts.session_id
            or read.execution_id != context.execution_id
            or read.round_id != context.round_id
            or read.input_revision != facts.input_revision
            or not read.body_available
        ):
            raise ValueError(f"predecessor artifact unavailable for node {node_id}")
        refs.append(artifact_ref)
        if read.summary:
            summaries.append(f"[{node_id}] {read.summary}")
    return tuple(refs), tuple(summaries)


def _actual_model_ref(result: Any) -> str | None:
    """从运行消息中取真实模型名（最后一个非空 ``ModelResponse.model_name``）。"""

    for message in reversed(result.all_messages()):
        if not isinstance(message, ModelResponse):
            continue
        model_name = message.model_name
        if isinstance(model_name, str) and model_name:
            return model_name
    return None


def _authorized_connection_id(binding: LeafModelBinding) -> str | None:
    """冻结绑定里的真实授权连接（资料许可绑定的 connection_id）；无则 ``None``。"""

    consent = binding.binding.get("data_consent")
    if not isinstance(consent, Mapping):
        return None
    value = consent.get("connection_id")
    return value if isinstance(value, str) and value else None


def _leaf_stream_event(event: Any, sequence: int) -> LeafStreamEvent | None:
    """SDK 流事件 → 受治理公开事件；未列入展示面的事件一律不透出。"""

    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return LeafStreamEvent(kind="text_delta", sequence=sequence, text_delta=event.part.content)
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return LeafStreamEvent(kind="text_delta", sequence=sequence, text_delta=event.delta.content_delta)
    if isinstance(event, FunctionToolCallEvent):
        return LeafStreamEvent(kind="tool_call", sequence=sequence, tool_name=event.part.tool_name)
    if isinstance(event, FinalResultEvent):
        return LeafStreamEvent(kind="finish", sequence=sequence)
    return None


def _event_stream_handler(on_event: LeafEventHandler) -> EventStreamHandler[LeafAgentDeps]:
    """公开事件回调装配：回调异常隔离（记录类型名后继续），取消照常传播（R2）。

    回调只具观察语义：失败不得改变模型回合与成果事实；可等待返回值按背压 await。
    """

    sequence = 0

    async def _handler(_run_context: Any, stream: Any) -> None:
        nonlocal sequence
        async for event in stream:
            mapped = _leaf_stream_event(event, sequence + 1)
            if mapped is None:
                continue
            sequence += 1
            try:
                outcome = on_event(mapped)
                if inspect.isawaitable(outcome):
                    await outcome
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[LeafAgent] event callback failed (kind=%s): %s",
                    mapped.kind,
                    type(e).__name__,
                )

    return _handler


def _failed(context: LeafExecutionContext, code: str) -> NodeOutcome:
    """叶失败终态（不写成果、不伪造 complete）。"""

    return NodeOutcome(node_id=context.node.node_id, state="failed", error_code=code)


def _is_confirmed_result(saved: object, draft: ResearchResultDraft) -> bool:
    """保存确认：已保存成果须报告 ``final_output_saved`` 且身份与本叶草稿逐项一致。

    身份核对按原成果稳定身份（同用户域/执行/轮/节点/输入版/输入指纹），避免把
    他叶成果或旧输入版的成果当成本叶成果（R21）。
    """

    return (
        isinstance(saved, ResearchResult)
        and saved.final_output_saved is True
        and saved.user_scope == draft.user_scope
        and saved.execution_id == draft.execution_id
        and saved.round_id == draft.round_id
        and saved.node_id == draft.node_id
        and saved.input_revision == draft.input_revision
        and saved.actual_input_digest == draft.actual_input_digest
    )


async def _lookup_saved_result(store: LeafResultStore, draft: ResearchResultDraft) -> ResearchResult | None:
    """提交确认丢失后按原成果身份查回（同身份同摘要幂等重放，零新版本）。

    ``save_research_result`` 以 ``(user_scope, execution, round, node, result_kind)``
    派生稳定成果身份并按 ``result_digest`` 幂等：原事务已提交时返回原成果版本
    （不新增版本、不重复写事件）；原事务确未提交时补写本次成果（仍只有一行，
    不产生重复成果）。以同一 draft 重放即「按原成果身份查回」，不重跑模型。
    返回 ``None`` = 无法证明（重放再次异常或返回未确认/身份不符）。
    """

    try:
        saved = await store.save_research_result(draft)
    except asyncio.CancelledError:
        raise
    except AgentServiceError as e:
        logger.warning(
            "[LeafAgent] research result identity lookup rejected: %s (execution=%s node=%s)",
            e.code.value,
            draft.execution_id,
            draft.node_id,
        )
        return None
    except Exception as e:
        logger.warning(
            "[LeafAgent] research result identity lookup failed: %s (execution=%s node=%s)",
            type(e).__name__,
            draft.execution_id,
            draft.node_id,
        )
        return None
    if not _is_confirmed_result(saved, draft):
        return None
    return saved


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
    tool_runtime_factory: ToolRuntimeFactory | None = None,
    role_prompt_reader: RolePromptReader | None = None,
    on_event: LeafEventHandler | None = None,
) -> NodeOutcome:
    """执行一次叶 Agent 回合并保存成果；失败如实返回 ``failed``（R21）。

    ``tool_runtime_factory=None`` 保持无工具单轮（第 2 项已验证行为）；显式注入时
    在归属事实可知后构造 runtime，并走 Deferred 工具循环。``role_prompt_reader``
    缺省时读设置页已保存的角色 Prompt（默认正文 + 追加），读不到即失败不放行。

    期限：每个模型回合的 ``ModelSettings['timeout']`` 与 SDK 运行期限都取自绑定
    冻结绝对期限的剩余秒数（``require_leaf_turn_timeout``）；期限不可核验 / 已耗尽
    即零外发失败，不以默认预算继续（R21）。
    ``on_event`` 为受治理公开事件回调（观察语义，异常隔离）；提供时经 SDK 真实
    流路径驱动，截断（无 finish 确认）或收尾未核按协议失败收敛，不保存成果。
    """

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
        require_leaf_turn_timeout(binding)
    except LeafDeadlineUnavailableError:
        logger.warning(
            "[LeafAgent] binding deadline unavailable, leaf fails without outbound (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_TIMEOUT_UNAVAILABLE)
    except LeafDeadlineExhaustedError:
        logger.warning(
            "[LeafAgent] binding deadline exhausted, leaf fails without outbound (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_DEADLINE_EXHAUSTED)

    try:
        role_prompt = await _resolve_role_prompt(role_prompt_reader)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[LeafAgent] role prompt unavailable: %s (execution=%s round=%s node=%s)",
            type(e).__name__,
            context.execution_id,
            context.round_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_ROLE_PROMPT_UNAVAILABLE)
    if not role_prompt.text.strip():
        logger.warning(
            "[LeafAgent] role prompt empty, leaf fails without outbound (execution=%s round=%s node=%s)",
            context.execution_id,
            context.round_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_ROLE_PROMPT_UNAVAILABLE)
    logger.debug(
        "[LeafAgent] role prompt frozen (role=%s version=%s execution=%s node=%s)",
        LEAF_ROLE_PROMPT_KEY,
        role_prompt.version,
        context.execution_id,
        context.node.node_id,
    )

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

    # 应用轮次核对：父冻结的输入版必须与权威事实一致（不一致 = 旧载荷/错轮次）。
    if context.input_revision is None:
        logger.warning(
            "[LeafAgent] binding input revision missing, leaf fails without outbound (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_BINDING_REVISION_UNAVAILABLE)
    if facts.input_revision != context.input_revision:
        logger.warning(
            "[LeafAgent] binding input revision stale, leaf fails without outbound (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_BINDING_REVISION_STALE)

    try:
        parent_artifact_refs, predecessor_summaries = await _read_predecessor_artifacts(dao, context, facts)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[LeafAgent] predecessor artifacts unavailable: %s (execution=%s node=%s)",
            type(e).__name__,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_PREDECESSOR_ARTIFACT_UNAVAILABLE)

    boundary = build_leaf_boundary(
        dao,
        snapshot=binding.snapshot,
        scenario_id=AGENT_RESEARCH_SCENARIO_ID,
        connection_id=_authorized_connection_id(binding),
    )
    scope = ToolRunScope(
        user_scope=facts.user_scope,
        session_id=facts.session_id,
        execution_id=context.execution_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
    )
    tool_runtime = None
    if tool_runtime_factory is not None:
        try:
            tool_runtime = tool_runtime_factory(scope)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[LeafAgent] tool runtime setup failed: %s (execution=%s node=%s)",
                type(e).__name__,
                context.execution_id,
                context.node.node_id,
            )
            return _failed(context, LEAF_TOOL_SETUP_FAILED)
    tools = tool_runtime.tools if tool_runtime is not None else ()
    # 长上下文压缩：预算取真实授权连接窗口并预留输出；摘要回合复用本叶
    # 绑定模型（同一账本），摘要期限取本回合剩余冻结期限。
    compaction = build_leaf_compaction(
        binding,
        scenario_id=AGENT_RESEARCH_SCENARIO_ID,
        timeout_seconds=lambda: require_leaf_turn_timeout(binding),
    )
    capabilities: tuple[AbstractCapability[Any], ...] = (
        (boundary, compaction) if compaction is not None else (boundary,)
    )
    agent: Agent[LeafAgentDeps, Any] = Agent(
        model,
        name=LEAF_AGENT_NAME,
        deps_type=LeafAgentDeps,
        output_type=[str, DeferredToolRequests],
        defer_model_check=True,
        retries=0,
        instructions=_leaf_instructions(context, role_prompt),
        tools=tools,
        capabilities=capabilities,
    )
    deps = LeafAgentDeps(
        user_scope=facts.user_scope,
        execution_id=context.execution_id,
        session_id=facts.session_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
    )
    unknown_operation_refs: tuple[str, ...] = ()

    def _turn_model_settings() -> dict[str, Any]:
        """本回合设置：桥层期限与 SDK 运行期限同取绑定冻结期限的剩余秒数。"""

        return {"timeout": require_leaf_turn_timeout(binding)}

    event_stream_handler = _event_stream_handler(on_event) if on_event is not None else None
    try:
        if tool_runtime is None:
            settings = _turn_model_settings()
            result = await agent.run(
                _leaf_prompt(context, predecessor_summaries),
                deps=deps,
                model_settings=settings,
                timeout=settings["timeout"],
                event_stream_handler=event_stream_handler,
            )
        else:
            loop_result = await run_agent_tool_loop(
                agent,
                _leaf_prompt(context, predecessor_summaries),
                deps=deps,
                ports=ToolLoopPorts(
                    executor=AdmissionCountingToolExecutor(
                        tool_runtime, dao=dao, user_scope=facts.user_scope, execution_id=context.execution_id
                    )
                ),
                turn_model_settings=_turn_model_settings,
                event_stream_handler=event_stream_handler,
            )
            result = loop_result.run_result
            unknown_operation_refs = loop_result.unknown_operation_refs
    except asyncio.CancelledError:
        raise
    except (LeafDeadlineUnavailableError, LeafDeadlineExhaustedError) as e:
        code = LEAF_TIMEOUT_UNAVAILABLE if isinstance(e, LeafDeadlineUnavailableError) else LEAF_DEADLINE_EXHAUSTED
        logger.warning(
            "[LeafAgent] leaf deadline guard tripped: %s (execution=%s node=%s)",
            code,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, code)
    except RunTimedOut:
        logger.warning(
            "[LeafAgent] leaf run timed out at frozen deadline (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_DEADLINE_EXHAUSTED)
    except ModelTurnProtocolError as e:
        logger.warning(
            "[LeafAgent] leaf model turn protocol failure: %s (execution=%s node=%s)",
            e.code,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_MODEL_PROTOCOL_FAILED)
    except ModelTurnRefusedError:
        # 确定拒绝（完成的模型回合）：与协议 / 普通失败区分；拒绝原文只留在
        # 异常 body，不写入日志 / 终态（R9/R21），用量已由 Executor 落账
        logger.warning(
            "[LeafAgent] leaf model turn refused by provider policy (execution=%s node=%s)",
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_MODEL_REFUSED)
    except AgentServiceError as e:
        if e.code is AgentErrorCode.BUDGET_BLOCKED:
            code = LEAF_TOOL_BUDGET_EXCEEDED
        elif e.code is AgentErrorCode.OPERATION_UNKNOWN and e.sanitized_message == "tool_result_not_saved":
            code = LEAF_TOOL_RESULT_NOT_SAVED
        else:
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
        unknown_operation_refs=unknown_operation_refs,
        ledger_refs=(),
        plan_version=None,
        parent_artifact_refs=parent_artifact_refs,
        source_refs=context.node.source_refs,
    )
    store = DaoLeafResultStore(dao)
    try:
        saved: object = await store.save_research_result(draft)
    except asyncio.CancelledError:
        raise
    except AgentServiceError as e:
        # 明确拒绝（执行/轮次/会话不符、清理 fence）：该事务提交前已回滚，成果
        # 确证未保存，如实报失败；不查回、不补写（提交前确证回滚不在丢确认内）。
        logger.warning(
            "[LeafAgent] research result save rejected: %s (execution=%s node=%s)",
            e.code.value,
            context.execution_id,
            context.node.node_id,
        )
        return _failed(context, LEAF_RESULT_SAVE_FAILED)
    except Exception as e:
        # 提交确认丢失（提交可能已生效）：按原成果身份查回，不把未知固化为失败。
        logger.warning(
            "[LeafAgent] research result save confirmation lost: %s (execution=%s node=%s)",
            type(e).__name__,
            context.execution_id,
            context.node.node_id,
        )
        saved = await _lookup_saved_result(store, draft)
        if saved is None:
            logger.warning(
                "[LeafAgent] research result save unconfirmed after identity lookup; leaf keeps unknown "
                "(execution=%s node=%s)",
                context.execution_id,
                context.node.node_id,
            )
            return NodeOutcome(
                node_id=context.node.node_id,
                state="unknown",
                error_code=LEAF_RESULT_SAVE_UNCONFIRMED,
                input_digest=digest,
            )
    if not _is_confirmed_result(saved, draft):
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


def build_leaf_agent_runner(
    dao: Any | None = None,
    *,
    tool_runtime_factory: ToolRuntimeFactory | None = None,
    role_prompt_reader: RolePromptReader | None = None,
    on_event: LeafEventHandler | None = None,
) -> LeafAgentRunner:
    """生产叶主体工厂：显式 DAO 优先，缺省惰性取共享 ``CacheManager``。

    ``on_event`` 为装配期注入的公开事件观察者（受治理、异常隔离）；接线前可为
    ``None``，不改变既有行为。
    """

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
        return await run_leaf_agent(
            model,
            context,
            binding,
            dao=resolved,
            tool_runtime_factory=tool_runtime_factory,
            role_prompt_reader=role_prompt_reader,
            on_event=on_event,
        )

    return _runner
