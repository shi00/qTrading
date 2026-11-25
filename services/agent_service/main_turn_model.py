"""主回合生产模型端口（F01/F03/F16/F21；复用受治理模型调用链）。

设计边界：

- **不另造推理链**：复用 ``agent_research`` 场景路由（``resolve_executor``）与
  既有 ``GatewayModel`` 聊天回合桥；授权/预算/账本仍由执行器与模型桥承担，
  本模块不复制、不改写任何授权或记账契约。
- **零研究动作**：回合 Agent 不注册任何工具，也不提供委派端口——讨论/计划按
  构造上不可能产生取数、检索、MCP 研究调用或叶派发。
- **模式约束**：讨论（及执行模式的直答）只产出直答；计划只产出可读计划步骤，
  模型无法自选模式。
- **fail-closed**：场景路由不可用、携带未许可资料、或模型输出为空/非法时抛
  类型化错误并零外发；``CancelledError`` 原样传播（R2）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol
from collections.abc import Callable

from pydantic import BaseModel, Field

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    Mode,
    json_doc,
    stable_id,
)
from services.agent_service.leaf_runtime import AGENT_RESEARCH_SCENARIO_ID, AGENT_TOOL_SCHEMA_VERSION
from services.agent_service.loop import LoopAction, TurnContext, TurnDecision
from services.agent_service.main_turn import MainTurnRequest
from utils.sanitizers import DataSanitizer

__all__ = ["build_governed_turn_factory", "run_governed_turn"]

logger = logging.getLogger(__name__)

MAIN_TURN_AGENT_NAME: Final[str] = "qtrading-main-turn"


class _AnswerDraft(BaseModel):
    """讨论/直答输出：只允许直接回答文本。"""

    answer: str = Field(min_length=1)


class _PlanStepDraft(BaseModel):
    """计划步骤：可读正文 + 显式依赖（不猜默认依赖）。"""

    id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class _PlanDraft(BaseModel):
    """计划输出：摘要 + 步骤（可读计划与可执行节点表同源）。"""

    summary: str = ""
    steps: list[_PlanStepDraft] = Field(default_factory=list)


class _ClarificationDraft(BaseModel):
    """澄清输出：一个可读问题 + 候选选项 + 影响说明（选项可为空集）。"""

    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    impact: str = ""


class _TurnEnvelope(BaseModel):
    """主回合唯一输出信封：``answer`` / ``plan`` / ``clarification`` 恰有其一。

    用单一信封而非 union：模型无法靠「两个都填」表达歧义，结构校验失败即
    类型化拒绝，不把非法结构伪造成直答（R21）。
    """

    answer: _AnswerDraft | None = None
    plan: _PlanDraft | None = None
    clarification: _ClarificationDraft | None = None


class _ResolvedRoute(Protocol):
    @property
    def executor(self) -> Any: ...

    @property
    def snapshot(self) -> Any: ...


#: 场景路由解析（测试注入替身；生产走 ``resolve_executor``）。
RouteResolver = Callable[[], Any]
#: 模型桥工厂（测试注入替身；生产为 ``GatewayModel``）。
ModelFactory = Callable[..., Any]
#: 单次受治理调用（测试注入替身；生产为 pydantic-ai ``Agent``）。
AgentInvoker = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class _TurnPrompt:
    """一次回合的指令与提示（模式固定，模型不能改写）。"""

    instructions: str
    prompt: str
    output: type[BaseModel]


_DISCUSS_INSTRUCTIONS: Final[str] = (
    "你是本地投研桌面应用的讨论助手。只依据用户问题与回合提示中已提供的资料直接回答；"
    "本次不取数、不检索、不调用任何外部服务，不得声称已取数或已执行研究。"
    "资料不足时明确说明缺少什么，不编造数据或结论。"
    "输出信封三选一：answer 直接回答；clarification 在问题本身不明确时提出一个澄清问题"
    "（给出可选 options 与 impact），不要用猜测代替提问。"
)
_PLAN_INSTRUCTIONS: Final[str] = (
    "你是本地投研桌面应用的规划助手。把用户问题拆成用户可阅读、可确认的计划步骤；"
    "本次只产出计划，不取数、不检索、不调用任何外部服务，不执行任何步骤。"
    "每个步骤必须给出唯一 id、可读 content，并以 depends_on 引用已存在的步骤 id；"
    "步骤之间应可独立理解，不要输出代码或工具调用。"
    "输出信封三选一：plan 交付计划；clarification 在问题本身不明确时提出一个澄清问题"
    "（给出可选 options 与 impact），不要用假设的计划代替提问。"
)


def _turn_prompt(request: MainTurnRequest) -> _TurnPrompt:
    if not isinstance(request.mode, Mode):
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid turn mode", request.identity.execution_id)
    prompt = request.prompt_text
    if request.clarification_answers:
        # 上一轮澄清的答复只在本轮注入一次（由受理入口消费，不重复取）。
        clarified = "\n".join(
            f"用户已澄清：{fact.question_text} → {fact.answer_text}" for fact in request.clarification_answers
        )
        prompt = f"{prompt}\n\n已澄清信息（以此为准）：\n{clarified}"
    if request.mode is Mode.PLAN:
        return _TurnPrompt(instructions=_PLAN_INSTRUCTIONS, prompt=prompt, output=_TurnEnvelope)
    # 讨论与执行模式的直答共用「只回答、不行动」指令（执行动作由派发链负责）。
    return _TurnPrompt(instructions=_DISCUSS_INSTRUCTIONS, prompt=prompt, output=_TurnEnvelope)


async def _resolve_route() -> _ResolvedRoute | None:
    from services.ai_service.scenario_dispatch import resolve_executor

    return await resolve_executor(AGENT_RESEARCH_SCENARIO_ID)


async def _invoke_agent(
    model: Any,
    *,
    instructions: str,
    prompt: str,
    output_type: type[BaseModel],
) -> Any:
    """受治理单轮调用：无工具、无委派、固定指令与结构化输出。"""

    from pydantic_ai import Agent

    agent: Agent[None, Any] = Agent(
        model,
        name=MAIN_TURN_AGENT_NAME,
        output_type=output_type,
        defer_model_check=True,
        retries=0,
        instructions=instructions,
        tools=(),
    )
    result = await agent.run(prompt)
    return result.output


def _answer_decision(identity: Any, output: Any) -> TurnDecision:
    answer = getattr(output, "answer", None)
    if not isinstance(answer, str) or not answer.strip():
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "empty main turn answer", identity.execution_id)
    return TurnDecision(action=LoopAction.ANSWER, text=answer)


def _plan_decision(identity: Any, output: Any) -> TurnDecision:
    summary = getattr(output, "summary", "")
    steps = getattr(output, "steps", ())
    if not isinstance(summary, str):
        summary = ""
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        step_id = getattr(step, "id", None)
        content = getattr(step, "content", None)
        depends_on = getattr(step, "depends_on", ())
        if not isinstance(step_id, str) or not step_id or not isinstance(content, str) or not content.strip():
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                f"invalid plan step at index {index}",
                identity.execution_id,
            )
        normalized.append(
            {
                "id": step_id,
                "content": content,
                "depends_on": [item for item in depends_on if isinstance(item, str) and item],
            }
        )
    if not normalized:
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "empty plan steps", identity.execution_id)
    readable_lines = [line for line in (summary.strip(),) if line]
    readable_lines.extend(f"{index}. {step['content']}" for index, step in enumerate(normalized, start=1))
    return TurnDecision(
        action=LoopAction.PLAN,
        text="\n".join(readable_lines),
        # 可读计划与可执行节点表共用同一份步骤，不另造结构。
        structured_fields=json_doc({"steps": normalized}),
    )


def _clarification_decision(identity: Any, draft: Any) -> TurnDecision:
    """澄清提案 → ``CLARIFY``（问题文本 + 选项 + 影响；空问题即拒绝）。"""

    question = getattr(draft, "question", None)
    if not isinstance(question, str) or not question.strip():
        raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "empty clarification question", identity.execution_id)
    raw_options = getattr(draft, "options", ())
    options = (
        [item.strip() for item in raw_options if isinstance(item, str) and item.strip()]
        if isinstance(raw_options, (list, tuple))
        else []
    )
    impact = getattr(draft, "impact", "")
    return TurnDecision(
        action=LoopAction.CLARIFY,
        text=question,
        structured_fields=json_doc(
            {
                "question": question,
                "options": options,
                "impact": impact if isinstance(impact, str) else "",
            }
        ),
    )


def _envelope_decision(identity: Any, output: Any, mode: Mode) -> TurnDecision:
    """单一信封 → 回合提案：恰好一个载荷，且与模式相符；不伪造直答。"""

    answer = getattr(output, "answer", None)
    plan = getattr(output, "plan", None)
    clarification = getattr(output, "clarification", None)
    provided = sum(1 for value in (answer, plan, clarification) if value is not None)
    if provided != 1:
        raise AgentServiceError(
            AgentErrorCode.OPERATION_UNKNOWN,
            "main turn output must carry exactly one payload",
            identity.execution_id,
        )
    if clarification is not None:
        # 两种模式都允许澄清：问题不明确时先问，不产出直答/计划。
        return _clarification_decision(identity, clarification)
    if mode is Mode.PLAN:
        if plan is None:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, "plan turn requires a plan payload", identity.execution_id
            )
        return _plan_decision(identity, plan)
    if answer is None:
        raise AgentServiceError(
            AgentErrorCode.OPERATION_UNKNOWN, "discussion turn requires an answer payload", identity.execution_id
        )
    return _answer_decision(identity, answer)


async def run_governed_turn(
    request: MainTurnRequest,
    context: TurnContext,
    *,
    resolve_route: RouteResolver = _resolve_route,
    build_model: ModelFactory | None = None,
    invoke_agent: AgentInvoker = _invoke_agent,
) -> TurnDecision:
    """一次主回合的受治理模型调用（讨论/计划零研究动作，fail-closed）。"""

    identity = request.identity
    prompt = _turn_prompt(request)
    if context.observations:
        # 讨论/计划只有可达的读资料观测；如实带入提示，不当作新的行动许可。
        observations = "\n".join(observation.text for observation in context.observations if observation.text)
        if observations:
            prompt = _TurnPrompt(
                instructions=prompt.instructions,
                prompt=f"{prompt.prompt}\n\n已获资料观测：\n{observations}",
                output=prompt.output,
            )
    if request.source_refs:
        # 主回合没有资料许可通道：携带资料即零外发（不放行未许可发送）。
        raise AgentServiceError(
            AgentErrorCode.CONFIG_UNAVAILABLE,
            "attached sources require data consent, zero model dispatch",
            identity.execution_id,
        )
    route = await resolve_route()
    if route is None:
        raise AgentServiceError(
            AgentErrorCode.CONFIG_UNAVAILABLE,
            "agent_research route unavailable, zero model dispatch",
            identity.execution_id,
        )
    binding: dict[str, Any] = {
        "scenario_id": AGENT_RESEARCH_SCENARIO_ID,
        "task_id": identity.node_id,
    }
    factory: ModelFactory = build_model if build_model is not None else _default_model_factory
    model = factory(
        route.executor,
        binding,
        route.snapshot,
        tool_schema_version=AGENT_TOOL_SCHEMA_VERSION,
        call_identity=stable_id(
            "main_turn",
            [identity.user_scope, identity.execution_id, identity.round_id, identity.node_id],
        ),
    )
    try:
        output = await invoke_agent(
            model,
            instructions=prompt.instructions,
            prompt=prompt.prompt,
            output_type=prompt.output,
        )
    except asyncio.CancelledError:
        raise
    except AgentServiceError:
        raise
    except Exception as exc:
        logger.warning(
            "[MainTurnModel] governed turn failed, no fabricated answer (execution=%s): %s",
            identity.execution_id,
            DataSanitizer.sanitize_error(exc),
        )
        raise AgentServiceError(
            AgentErrorCode.OPERATION_UNKNOWN,
            f"main turn model failed: {type(exc).__name__}",
            identity.execution_id,
        ) from exc
    return _envelope_decision(identity, output, request.mode)


def _default_model_factory(*args: Any, **kwargs: Any) -> Any:
    from services.agent_service.model import GatewayModel

    return GatewayModel(*args, **kwargs)


def build_governed_turn_factory(
    *,
    resolve_route: RouteResolver = _resolve_route,
    build_model: ModelFactory | None = None,
    invoke_agent: AgentInvoker = _invoke_agent,
) -> Callable[[MainTurnRequest], Callable[[TurnContext], Any]]:
    """生产主回合端口工厂：组合根注入后，每次回合按请求文本执行受治理调用。"""

    def _factory(request: MainTurnRequest) -> Callable[[TurnContext], Any]:
        async def _turn(context: TurnContext) -> TurnDecision:
            return await run_governed_turn(
                request,
                context,
                resolve_route=resolve_route,
                build_model=build_model,
                invoke_agent=invoke_agent,
            )

        return _turn

    return _factory
