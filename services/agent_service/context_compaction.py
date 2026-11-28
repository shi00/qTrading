"""长上下文预算、摘要压缩与工具配对卸载（F25 / AC15 / J07 / QAC03 / NFR-P05）。

本模块补齐「主/子长会话有界输入」的实现缺失，边界如下：

- **预算来自真实窗口事实**：可用输入预算按既有 :func:`resolve_leaf_context_windows`
  解析（路由候选 → 连接 ``context_window``），再预留输出 token。窗口不可知时预算
  显式为 ``None``（R21），不猜默认窗口、不以硬编码值冒充；
- **工具调用/结果同进同出**：选择摘要区间时按 ``tool_call_id`` 逐一核对配对；
  切点会让配对落到两侧的边界一律不可切，向前回退仍无安全点则如实报不可切，
  不产生半截配对的下游非法请求；
- **摘要经既有模型适配器入口**：摘要回合复用被压缩那次运行自己的 ``GatewayModel``
  （``ctx.model``），共用同一账本/权限/窗口治理；本模块不构造模型、不硬编码模型 id；
- **谱系可核**：成功压缩记录来源跨度（消息序号 + 身份）、生成时刻、所用模型绑定与
  摘要摘要值（sha256），不记录摘要正文到日志；
- **失败不静默**：超时 / 无模型 / 空响应 / 截断 / 任何异常 → 保留原文，显式返回
  ``compressed=False`` 与失败原因，不静默截断、不伪造摘要（R21）；
- **禁用与继承**：配置可显式关闭压缩；子会话默认不继承父摘要正文，只继承可核
  谱系引用（:class:`SummaryLineage` 本身不含正文）；
- **长会话保纠正（J07）**：用户明确纠正以 pin 标记（harness 公共 ``pin()``）投递，
  压缩后原文重新注入，不被摘要吞掉。

不在本模块：持久化摘要/谱系（归运行装配与 DAO）、UI 呈现、真实模型摘要质量验收。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import RunTimedOut
from pydantic_ai_harness.compaction import estimate_context_tokens, is_pinned, pin, reinject_pinned
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.tools import RunContext

from core.agent_types import AgentServiceError, is_digest, sha256_hex
from core.model_management_types import ExecutionSnapshot, ModelTurnProtocolError
from services.agent_service.capability_wiring import resolve_leaf_context_windows
from services.ai_service.token_budget import OUTPUT_RESERVE_TOKENS
from utils.error_classifier import classify_error, classify_severity

__all__ = [
    "COMPACTION_BOUNDARY_UNCUTTABLE",
    "COMPACTION_BUDGET_INSUFFICIENT",
    "COMPACTION_BUDGET_UNKNOWN",
    "COMPACTION_DISABLED",
    "COMPACTION_NOTHING_TO_SUMMARIZE",
    "COMPACTION_SUMMARY_EMPTY",
    "COMPACTION_SUMMARY_FAILED",
    "COMPACTION_SUMMARY_MODEL_UNBOUND",
    "COMPACTION_SUMMARY_NO_MODEL",
    "COMPACTION_SUMMARY_SEGMENT_OVER_WINDOW",
    "COMPACTION_SUMMARY_TIMEOUT",
    "COMPACTION_SUMMARY_TIMEOUT_UNAVAILABLE",
    "COMPACTION_SUMMARY_TRUNCATED",
    "COMPACTION_WITHIN_BUDGET",
    "CompactionBudget",
    "CompactionConfig",
    "CompactionOutcome",
    "CompactionPlan",
    "ContextCompaction",
    "SummaryLineage",
    "compact_messages",
    "has_user_correction",
    "inherit_summary_lineage",
    "is_tool_pair_safe_cut",
    "mark_user_correction",
    "plan_compaction",
    "resolve_compaction_budget",
    "summarize_segment_within_window",
]

logger = logging.getLogger(__name__)

#: 关闭压缩时返回的原因码（显式配置，不是失败）。
COMPACTION_DISABLED: Final[str] = "disabled"
#: 估算输入未超出可用预算：本轮不需要压缩。
COMPACTION_WITHIN_BUDGET: Final[str] = "within_budget"
#: 窗口事实不可知：预算显式 unknown，不做压缩决策（R21）。
COMPACTION_BUDGET_UNKNOWN: Final[str] = "budget_unknown"
#: 窗口小于输出预留：可用输入预算为 0，压缩也无意义。
COMPACTION_BUDGET_INSUFFICIENT: Final[str] = "budget_insufficient"
#: 历史过短，没有可摘要的消息。
COMPACTION_NOTHING_TO_SUMMARIZE: Final[str] = "nothing_to_summarize"
#: 所有候选切点都会拆散工具调用/结果配对：该边界不可切。
COMPACTION_BOUNDARY_UNCUTTABLE: Final[str] = "boundary_uncuttable"
#: 待摘要片段超过摘要模型自身窗口上限：不发出必然超窗的摘要请求。
COMPACTION_SUMMARY_SEGMENT_OVER_WINDOW: Final[str] = "summary_segment_over_window"
#: 摘要回合拿不到可用模型（未绑定）。
COMPACTION_SUMMARY_NO_MODEL: Final[str] = "summary_no_model"
#: 摘要回合的期限不可核验（不以臆造默认预算外发）。
COMPACTION_SUMMARY_TIMEOUT_UNAVAILABLE: Final[str] = "summary_timeout_unavailable"
#: 摘要回合超时。
COMPACTION_SUMMARY_TIMEOUT: Final[str] = "summary_timeout"
#: 摘要响应为空（空白不算摘要）。
COMPACTION_SUMMARY_EMPTY: Final[str] = "summary_empty"
#: 摘要响应被截断（finish_reason=length）。
COMPACTION_SUMMARY_TRUNCATED: Final[str] = "summary_truncated"
#: 摘要回合未能核验所用模型绑定（不伪造谱系）。
COMPACTION_SUMMARY_MODEL_UNBOUND: Final[str] = "summary_model_unbound"
#: 摘要回合其它异常（按 ``classify_error`` 细分日志，原因码保持稳定）。
COMPACTION_SUMMARY_FAILED: Final[str] = "summary_failed"

#: 我们的摘要 part 的模型不可见标记（不参与出站投影）。
_SUMMARY_METADATA_KEY: Final[str] = "qtrading.agent.compaction.summary.v1"

_SUMMARY_INSTRUCTIONS: Final[str] = (
    "你是本地投研桌面应用的上下文摘要助手。目标是用一份可继续工作的摘要替代较早的对话历史，"
    "必须保留继续任务所需的全部事实，不要复述已完成动作。"
)

_SUMMARY_PROMPT: Final[str] = """\
下面这段对话将被你的摘要替代，请承载继续任务所需的全部信息。

按以下小节组织，没有内容的小节可以省略：
## 目标
用户的总体目标与仍然有效的要求、约束或偏好。
## 关键决定
已做出的选择与理由，避免被重新讨论。
## 已用资料与标识
涉及的文件、路径、接口、参数、来源标识等，按原文引用。
## 当前状态
已完成与进行中的工作。
## 下一步
完成任务的直接动作。
## 未解决问题
未解决的疑问或阻塞。

只输出摘要正文，不要前言，不要 Markdown 代码围栏。

<messages>
{messages}
</messages>"""

_INCREMENTAL_INSTRUCTION: Final[str] = (
    "已经提供上一次压缩的摘要（<previous-summary>）。请在此基础上更新：保留仍然成立的内容，"
    "删除已过期的内容，合并新事实，并保持相同的小节结构。"
)


# ---------------------------------------------------------------------------
# 配置 / 预算
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompactionConfig:
    """压缩策略配置。

    - ``enabled=False``：显式关闭压缩（运行装配因此不装该能力，不影响主回合）；
    - ``keep_messages``：压缩后原样保留的尾部消息数（至少 1，最新消息永远保留）。
    """

    enabled: bool = True
    keep_messages: int = 6

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("compaction enabled must be bool")
        if isinstance(self.keep_messages, bool) or not isinstance(self.keep_messages, int) or self.keep_messages < 1:
            raise ValueError("keep_messages must be an int >= 1")


@dataclass(frozen=True, slots=True)
class CompactionBudget:
    """一次运行可用的输入预算（窗口事实 - 输出预留）。

    ``context_window_tokens=None`` 表示本候选窗口不可知：``available_input_tokens``
    同样为 ``None``，压缩能力据此拒绝决策（R21），不用其它候选窗口替代，也不猜
    默认窗口。窗口小于输出预留时可用预算为 0（预算不足，明确不可压缩）。
    """

    context_window_tokens: int | None
    candidate_context_windows: tuple[int, ...] = ()
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS

    def __post_init__(self) -> None:
        if self.context_window_tokens is not None:
            value = self.context_window_tokens
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("context_window_tokens must be a positive int or None")
        if isinstance(self.output_reserve_tokens, bool) or not isinstance(self.output_reserve_tokens, int):
            raise ValueError("output_reserve_tokens must be an int")
        if self.output_reserve_tokens < 0:
            raise ValueError("output_reserve_tokens must be non-negative")
        for window in self.candidate_context_windows:
            if isinstance(window, bool) or not isinstance(window, int) or window < 1:
                raise ValueError("candidate_context_windows entries must be positive ints")

    def strictest_context_window_tokens(self) -> int | None:
        """所有候选可用窗口中最严者；本候选窗口未知 → ``None``（拒绝决策）。"""

        if self.context_window_tokens is None:
            return None
        return min((self.context_window_tokens, *self.candidate_context_windows))

    def available_input_tokens(self) -> int | None:
        """可用输入预算（窗口 - 输出预留）；窗口未知 → ``None``，不足 → 0。"""

        window = self.strictest_context_window_tokens()
        if window is None:
            return None
        return max(window - self.output_reserve_tokens, 0)


def resolve_compaction_budget(
    snapshot: ExecutionSnapshot,
    *,
    scenario_id: str,
    connection_id: str | None = None,
    output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS,
) -> CompactionBudget:
    """按既有窗口解析计算压缩预算（路由候选 → 连接 ``context_window``）。

    窗口解析结果原样保留：任一环节缺失或非法 → ``None``（能力据此显式
    ``budget_unknown``），不回退默认窗口。
    """

    window, candidate_windows = resolve_leaf_context_windows(
        snapshot, scenario_id=scenario_id, connection_id=connection_id
    )
    return CompactionBudget(
        context_window_tokens=window,
        candidate_context_windows=tuple(candidate_windows),
        output_reserve_tokens=output_reserve_tokens,
    )


# ---------------------------------------------------------------------------
# 工具配对边界与摘要区间
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """摘要区间计划：``cutoff`` 之前（不含）的消息会被摘要。"""

    cutoff: int | None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.cutoff is None:
            if not isinstance(self.reason, str) or not self.reason:
                raise ValueError("an uncuttable plan requires a reason")
            return
        if isinstance(self.cutoff, bool) or not isinstance(self.cutoff, int) or self.cutoff < 1:
            raise ValueError("cutoff must be a positive int or None")


def is_tool_pair_safe_cut(messages: Sequence[ModelMessage], cutoff: int) -> bool:
    """在 ``cutoff`` 处切分是否不拆散任何工具调用/结果配对。

    配对按 ``tool_call_id`` 逐一匹配（同一 id 在后续回合复用时取最近一次调用）；
    以下任一情况即视为该边界不可切（切分后会留下供应商拒绝的半截配对）：

    - 调用与结果落到切点两侧；
    - 保留区间内出现孤立 ``ToolReturnPart``（无匹配调用，含空 ``tool_call_id``）；
    - 保留区间内残留未闭合 ``ToolCallPart``（全历史都无对应结果）；
    - ``RetryPromptPart`` 与其工具调用被切点隔断（有 id 按配对判；无 id 的重试反馈
      归属于其前最近一条含工具调用的 ``ModelResponse``）。
    """

    if cutoff <= 0 or cutoff >= len(messages):
        return True
    pending: dict[str, int] = {}
    last_response_with_call: int | None = None
    for index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            has_call = False
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_call_id:
                    pending[part.tool_call_id] = index
                    has_call = True
            if has_call:
                last_response_with_call = index
            continue
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                call_id = part.tool_call_id
                call_index = pending.pop(call_id, None) if call_id else None
                if call_index is None:
                    if index >= cutoff:
                        return False
                    continue
                if (call_index < cutoff) != (index < cutoff):
                    return False
                continue
            if not isinstance(part, RetryPromptPart):
                continue
            call_id = part.tool_call_id
            if not call_id:
                # 无 id 的重试反馈归属于其前最近一条工具调用响应；隔断即不可切。
                if index >= cutoff and last_response_with_call is not None and last_response_with_call < cutoff:
                    return False
                continue
            call_index = pending.pop(call_id, None)
            if call_index is None:
                if index >= cutoff:
                    return False
                continue
            if (call_index < cutoff) != (index < cutoff):
                return False
    # 残留未闭合的工具调用：保留区间内的会被外发而永远等不到结果。
    return all(call_index < cutoff for call_index in pending.values())


def summarize_segment_within_window(messages: Sequence[ModelMessage], cutoff: int, budget: CompactionBudget) -> bool:
    """待摘要片段是否不超过摘要模型自身的上下文窗口上限。

    摘要回合复用被压缩运行的同一模型绑定，窗口事实与压缩预算同源；窗口不可知即
    无法核定（``False``，调用方拒绝放行，不猜默认窗口，R21）。
    """

    window = budget.strictest_context_window_tokens()
    if window is None:
        return False
    return estimate_context_tokens(messages[:cutoff]) <= window


def plan_compaction(messages: Sequence[ModelMessage], *, keep_messages: int) -> CompactionPlan:
    """选择摘要区间：从「保留尾部 ``keep_messages`` 条」向前回退到配对安全切点。

    没有任何安全切点（例如唯一可切处会拆散配对）时返回 ``boundary_uncuttable``，
    调用方必须放弃本次压缩，而不是产生半截配对的历史。
    """

    if isinstance(keep_messages, bool) or not isinstance(keep_messages, int) or keep_messages < 1:
        raise ValueError("keep_messages must be an int >= 1")
    if len(messages) <= keep_messages:
        return CompactionPlan(cutoff=None, reason=COMPACTION_NOTHING_TO_SUMMARIZE)
    target = len(messages) - keep_messages
    for cutoff in range(target, 0, -1):
        if is_tool_pair_safe_cut(messages, cutoff):
            return CompactionPlan(cutoff=cutoff)
    return CompactionPlan(cutoff=None, reason=COMPACTION_BOUNDARY_UNCUTTABLE)


# ---------------------------------------------------------------------------
# 用户明确纠正（J07：压缩后原文保留）
# ---------------------------------------------------------------------------


def mark_user_correction(text: str) -> UserPromptPart:
    """把用户明确纠正投递为 pin 标记 part：压缩后原文重新注入，不被摘要吞掉。"""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("a user correction must carry non-empty text")
    return pin(text)


def has_user_correction(message: ModelMessage) -> bool:
    """消息是否承载用户明确纠正（pin 标记）。"""

    return isinstance(message, ModelRequest) and any(is_pinned(part) for part in message.parts)


# ---------------------------------------------------------------------------
# 谱系
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SummaryLineage:
    """一次压缩摘要的可核谱系（不含摘要正文，可安全下传子会话）。

    ``source_message_ids`` 逐条承载被摘要消息的身份（``kind#index``，有 ``run_id``
    时追加 ``@run_id``）；``summary_digest`` 为摘要正文的 sha256，用于核对摘要
    是否被替换，而不暴露正文。
    """

    source_start_index: int
    source_end_index: int
    source_message_ids: tuple[str, ...]
    created_at: str
    model_binding_ref: str
    summary_digest: str

    def __post_init__(self) -> None:
        if isinstance(self.source_start_index, bool) or not isinstance(self.source_start_index, int):
            raise ValueError("source_start_index must be an int")
        if self.source_start_index < 0:
            raise ValueError("source_start_index must be non-negative")
        if isinstance(self.source_end_index, bool) or not isinstance(self.source_end_index, int):
            raise ValueError("source_end_index must be an int")
        if self.source_end_index <= self.source_start_index:
            raise ValueError("source_end_index must be greater than source_start_index")
        if len(self.source_message_ids) != self.source_end_index - self.source_start_index:
            raise ValueError("source_message_ids must cover the declared span exactly")
        if any(not isinstance(item, str) or not item for item in self.source_message_ids):
            raise ValueError("source_message_ids entries must be non-empty text")
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ValueError("created_at must be non-empty ISO 8601 text")
        try:
            datetime.fromisoformat(self.created_at)
        except ValueError:
            raise ValueError("created_at must be ISO 8601 text") from None
        if not isinstance(self.model_binding_ref, str) or not self.model_binding_ref:
            raise ValueError("model_binding_ref must be non-empty text")
        if not is_digest(self.summary_digest):
            raise ValueError("summary_digest must be a lowercase sha256 digest")


def inherit_summary_lineage(
    parent: Sequence[SummaryLineage], *, inherit_body: bool = False
) -> tuple[SummaryLineage, ...]:
    """子会话继承父摘要谱系的策略：默认只继承可核引用，永不继承正文。

    ``SummaryLineage`` 本身不含正文，因此默认路径天然满足「子会话不继承父摘要
    正文」；显式请求继承正文是配置错误，直接拒绝（不静默降级）。
    """

    if inherit_body:
        raise ValueError("child sessions never inherit the parent summary body; inherit lineage refs only")
    return tuple(parent)


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompactionOutcome:
    """一次压缩决策的结果（失败时 ``messages`` 原样保留）。

    ``compressed=True`` 必须携带谱系；``compressed=False`` 必须携带原因码——调用方
    据此显式呈现「本轮未压缩」，不得渲染为已压缩（R21）。
    """

    messages: tuple[ModelMessage, ...]
    compressed: bool
    reason: str | None = None
    lineage: SummaryLineage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.compressed, bool):
            raise ValueError("compressed must be bool")
        if self.compressed:
            if self.lineage is None:
                raise ValueError("a compressed outcome requires lineage")
            if self.reason is not None:
                raise ValueError("a compressed outcome must not carry a failure reason")
            return
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("an uncompressed outcome requires a reason code")
        if self.lineage is not None:
            raise ValueError("an uncompressed outcome must not carry lineage")


# ---------------------------------------------------------------------------
# 摘要生成（复用被压缩运行的绑定模型）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SummaryResult:
    summary: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.summary is None) == (self.reason is None):
            raise ValueError("summary result carries exactly one of summary or reason")


def _text_of_content(content: object) -> str:
    """UserPromptPart 内容 → 文本；非文本内容以显式标记承载（不静默丢弃）。"""

    if isinstance(content, str):
        return content
    if isinstance(content, TextContent):
        return content.content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, TextContent):
                parts.append(item.content)
            elif isinstance(item, BinaryContent):
                parts.append("[非文本内容]")
            else:
                parts.append("[未知内容]")
        return "\n".join(parts)
    return "[非文本内容]"


def _render_transcript(messages: Sequence[ModelMessage]) -> str:
    """把待摘要消息渲染为逐条文本（不截断工具返回，R21）。"""

    lines: list[str] = []
    for index, message in enumerate(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    lines.append(f"[system#{index}] {part.content}")
                elif isinstance(part, UserPromptPart):
                    lines.append(f"[user#{index}] {_text_of_content(part.content)}")
                elif isinstance(part, ToolReturnPart):
                    lines.append(f"[tool_return#{index}:{part.tool_name}] {part.model_response_str()}")
                elif isinstance(part, RetryPromptPart):
                    lines.append(f"[retry_feedback#{index}] {part.model_response()}")
            continue
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart):
                    lines.append(f"[assistant#{index}] {part.content}")
                elif isinstance(part, ToolCallPart):
                    args = part.args if isinstance(part.args, str) else json.dumps(part.args, ensure_ascii=False)
                    lines.append(f"[tool_call#{index}:{part.tool_name}] {args}")
    return "\n".join(lines)


def _previous_summary(messages: Sequence[ModelMessage]) -> str | None:
    """历史中最近的自家摘要正文（增量更新锚点）。"""

    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart) and _is_own_summary_part(part):
                return _text_of_content(part.content)
    return None


def _is_own_summary_part(part: object) -> bool:
    if not isinstance(part, UserPromptPart) or isinstance(part.content, str):
        return False
    return any(
        isinstance(item, TextContent)
        and isinstance(item.metadata, dict)
        and item.metadata.get(_SUMMARY_METADATA_KEY) is True
        for item in part.content
    )


def _is_prior_summary_message(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(_is_own_summary_part(part) for part in message.parts)


def _model_binding_ref(model: Model, response: ModelResponse) -> str | None:
    """所用模型绑定：优先供应商回报的真实模型名，其次模型实例自带名称。"""

    if isinstance(response.model_name, str) and response.model_name:
        return response.model_name
    value = getattr(model, "model_name", None)
    return value if isinstance(value, str) and value else None


def _valid_timeout(timeout_seconds: object) -> float | None:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
        return None
    value = float(timeout_seconds)
    return value if value > 0 else None


async def _generate_summary(
    model: Model | None,
    to_summarize: Sequence[ModelMessage],
    *,
    previous_summary: str | None,
    timeout_seconds: float | None,
) -> tuple[_SummaryResult, ModelResponse | None]:
    """用被压缩运行自己的模型写摘要；任何失败都返回原因，不抛给调用方。"""

    if model is None:
        return _SummaryResult(reason=COMPACTION_SUMMARY_NO_MODEL), None
    timeout = _valid_timeout(timeout_seconds)
    if timeout is None:
        return _SummaryResult(reason=COMPACTION_SUMMARY_TIMEOUT_UNAVAILABLE), None

    prompt = _SUMMARY_PROMPT.format(messages=_render_transcript(to_summarize))
    if previous_summary is not None:
        prompt = (
            f"{prompt}\n\n{_INCREMENTAL_INSTRUCTION}\n\n<previous-summary>\n{previous_summary}\n</previous-summary>"
        )
    agent: Agent[None, str] = Agent(
        model,
        name="qtrading_context_summary",
        output_type=str,
        defer_model_check=True,
        retries=0,
        instructions=_SUMMARY_INSTRUCTIONS,
        tools=(),
    )
    try:
        result = await agent.run(prompt, model_settings={"timeout": timeout}, timeout=timeout)
    except asyncio.CancelledError:
        raise
    except (TimeoutError, RunTimedOut):
        return _SummaryResult(reason=COMPACTION_SUMMARY_TIMEOUT), None
    except ModelTurnProtocolError as exc:
        logger.warning("[Compaction] summary turn protocol failure: %s", exc.code)
        return _SummaryResult(reason=COMPACTION_SUMMARY_FAILED), None
    except AgentServiceError as exc:
        logger.warning("[Compaction] summary turn denied/failed: %s", exc.code.value)
        return _SummaryResult(reason=COMPACTION_SUMMARY_FAILED), None
    except Exception as exc:
        classified = classify_error(exc, "agent_compaction")
        severity = classify_severity(exc, "agent_compaction")
        log = logger.error if severity == "system" else logger.warning
        log(
            "[Compaction] summary turn failed (code=%s class=%s): keeping original history",
            classified.get("code", "unknown"),
            type(exc).__name__,
        )
        return _SummaryResult(reason=COMPACTION_SUMMARY_FAILED), None

    response = result.response
    if response.finish_reason == "length":
        return _SummaryResult(reason=COMPACTION_SUMMARY_TRUNCATED), response
    summary = result.output.strip() if isinstance(result.output, str) else ""
    if not summary:
        return _SummaryResult(reason=COMPACTION_SUMMARY_EMPTY), response
    return _SummaryResult(summary=summary), response


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def _leading_system_parts(messages: Sequence[ModelMessage]) -> list[SystemPromptPart]:
    """取历史最前的连续 system prompt（压缩后仍需作为指令保留）。"""

    collected: list[SystemPromptPart] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            break
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                collected.append(part)
                continue
            return collected
    return collected


def _summary_message(summary: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=[TextContent(summary, metadata={_SUMMARY_METADATA_KEY: True})])])


def _message_identity(index: int, message: ModelMessage) -> str:
    kind = message.kind if isinstance(message.kind, str) else type(message).__name__
    run_id = getattr(message, "run_id", None)
    suffix = f"@{run_id}" if isinstance(run_id, str) and run_id else ""
    return f"{kind}#{index}{suffix}"


def _assemble_compacted(original: Sequence[ModelMessage], cutoff: int, summary: str) -> list[ModelMessage]:
    """摘要 + 原样尾部 + system 指令；丢弃旧摘要避免叠加，pin 纠正重新注入。"""

    standing = _leading_system_parts(original[:cutoff])
    preserved = [message for message in original[cutoff:] if not _is_prior_summary_message(message)]
    assembled: list[ModelMessage] = []
    if standing:
        assembled.append(ModelRequest(parts=standing))
    assembled.append(_summary_message(summary))
    assembled.extend(preserved)
    return reinject_pinned(original, assembled)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def compact_messages(
    model: Model | None,
    messages: Sequence[ModelMessage],
    *,
    budget: CompactionBudget,
    config: CompactionConfig | None = None,
    estimated_input_tokens: int | None = None,
    timeout_seconds: float | None = None,
    model_binding_ref: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> CompactionOutcome:
    """按预算与配对安全决定是否摘要；失败保留原文并显式给出原因。

    ``estimated_input_tokens`` 缺省按历史估算（优先供应商回报的真实用量，缺失时按
    文本启发式）；预算未知时不做压缩决策（``budget_unknown``，R21）。摘要失败一律
    返回原文，不静默截断。
    """

    policy = config or CompactionConfig()
    original = tuple(messages)
    if not policy.enabled:
        return CompactionOutcome(messages=original, compressed=False, reason=COMPACTION_DISABLED)
    available = budget.available_input_tokens()
    if available is None:
        return CompactionOutcome(messages=original, compressed=False, reason=COMPACTION_BUDGET_UNKNOWN)
    if available <= 0:
        return CompactionOutcome(messages=original, compressed=False, reason=COMPACTION_BUDGET_INSUFFICIENT)
    estimated = estimated_input_tokens if estimated_input_tokens is not None else estimate_context_tokens(original)
    if isinstance(estimated, bool) or not isinstance(estimated, int) or estimated < 0:
        raise ValueError("estimated_input_tokens must be a non-negative int or None")
    if estimated <= available:
        return CompactionOutcome(messages=original, compressed=False, reason=COMPACTION_WITHIN_BUDGET)

    plan = plan_compaction(original, keep_messages=policy.keep_messages)
    if plan.cutoff is None:
        return CompactionOutcome(messages=original, compressed=False, reason=plan.reason)

    summarize_messages = original[: plan.cutoff]
    if not summarize_segment_within_window(original, plan.cutoff, budget):
        return CompactionOutcome(messages=original, compressed=False, reason=COMPACTION_SUMMARY_SEGMENT_OVER_WINDOW)
    summary_result, response = await _generate_summary(
        model,
        summarize_messages,
        previous_summary=_previous_summary(original),
        timeout_seconds=timeout_seconds,
    )
    if summary_result.summary is None or response is None:
        return CompactionOutcome(messages=original, compressed=False, reason=summary_result.reason)

    binding_ref = model_binding_ref or (_model_binding_ref(model, response) if model is not None else None)
    if binding_ref is None:
        logger.warning("[Compaction] summary model binding unavailable; keeping original history")
        return CompactionOutcome(messages=original, compressed=False, reason=COMPACTION_SUMMARY_MODEL_UNBOUND)

    summary = summary_result.summary
    created_at = (now or (lambda: datetime.now(UTC)))().isoformat()
    lineage = SummaryLineage(
        source_start_index=0,
        source_end_index=plan.cutoff,
        source_message_ids=tuple(_message_identity(index, message) for index, message in enumerate(summarize_messages)),
        created_at=created_at,
        model_binding_ref=binding_ref,
        summary_digest=sha256_hex(summary),
    )
    assembled = _assemble_compacted(original, plan.cutoff, summary)
    logger.info(
        "[Compaction] history compacted (summarized=%d kept=%d model=%s)",
        plan.cutoff,
        len(assembled) - plan.cutoff,
        binding_ref,
    )
    return CompactionOutcome(messages=tuple(assembled), compressed=True, lineage=lineage)


# ---------------------------------------------------------------------------
# 能力装配（每次模型请求前按预算决策）
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class ContextCompaction(AbstractCapability[Any]):
    """模型请求前的上下文压缩能力：超预算才摘要，失败保留原文。

    - ``budget``：真实窗口解析出的可用输入预算（未知即不决策，R21）；
    - ``timeout_seconds``：摘要回合的期限来源（生产取叶绑定冻结期限的剩余秒数，
      不在构造期冻结，避免长回合耗尽后仍按旧值外发）；
    - ``on_outcome``：压缩结果观察口（供运行装配记录「本轮未压缩」的显式原因）。

    只改写 ``ctx.messages`` 与本次请求上下文；不捕获 ``CancelledError``（R2）。
    """

    budget: CompactionBudget
    timeout_seconds: Callable[[], float]
    config: CompactionConfig = field(default_factory=CompactionConfig)
    on_outcome: Callable[[CompactionOutcome], None] | None = None
    now: Callable[[], datetime] | None = None
    id: str | None = "context_compaction"

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        messages: list[ModelMessage] = list(ctx.messages)
        model = ctx.model if isinstance(ctx.model, Model) else None
        outcome = await compact_messages(
            model,
            messages,
            budget=self.budget,
            config=self.config,
            timeout_seconds=self.timeout_seconds(),
            now=self.now,
        )
        if self.on_outcome is not None:
            self.on_outcome(outcome)
        if not outcome.compressed:
            if outcome.reason is not None and outcome.reason.startswith("summary_"):
                logger.warning("[Compaction] history left uncompressed: %s", outcome.reason)
            elif outcome.reason not in (COMPACTION_WITHIN_BUDGET, COMPACTION_DISABLED):
                logger.debug("[Compaction] no compression decision: %s", outcome.reason)
            return request_context
        compacted = list(outcome.messages)
        ctx.messages[:] = compacted
        return replace(request_context, messages=compacted)


# ---------------------------------------------------------------------------
# 叶运行装配入口
# ---------------------------------------------------------------------------


class _LeafBindingSource(Protocol):
    """叶冻结绑定最小面（快照 + 受理绑定；避免与 leaf_runtime 形成模块级环）。"""

    @property
    def snapshot(self) -> ExecutionSnapshot: ...

    @property
    def binding(self) -> Mapping[str, Any]: ...


def build_leaf_compaction(
    binding: _LeafBindingSource,
    *,
    scenario_id: str,
    timeout_seconds: Callable[[], float],
    config: CompactionConfig | None = None,
    on_outcome: Callable[[CompactionOutcome], None] | None = None,
) -> ContextCompaction | None:
    """叶 Agent 构造处的压缩装配入口：``enabled=False`` 时返回 ``None``（不装能力）。

    预算取该叶真实授权连接（资料许可绑定 connection）的窗口，其余候选窗口一并交给
    预算取最严者；窗口不可知时预算显式 unknown，由能力按「不决策」处理（R21）。
    """

    policy = config or CompactionConfig()
    if not policy.enabled:
        return None
    connection_id: str | None = None
    consent = binding.binding.get("data_consent")
    if isinstance(consent, Mapping):
        value = consent.get("connection_id")
        if isinstance(value, str) and value:
            connection_id = value
    budget = resolve_compaction_budget(binding.snapshot, scenario_id=scenario_id, connection_id=connection_id)
    return ContextCompaction(
        budget=budget,
        config=policy,
        timeout_seconds=timeout_seconds,
        on_outcome=on_outcome,
    )
