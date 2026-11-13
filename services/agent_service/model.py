"""Agent 模型桥（B-02-4，DD §10.1 / HANDOFF §6.2-6.3）：GatewayModel。

pydantic-ai 固定框架（pydantic-ai-slim==2.66.0）与 ScenarioExecutor 聊天链的
唯一桥接层（§2 扩展清单：模型桥唯一性）：

- **GatewayModel**（Model 子类）：``prepare_request`` / ``prepare_messages``
  沿 SDK 基类（超类钩子语义，不旁路）；SDK 消息 → core ``ChatTurnRequest``
  一次投影（§6.3 映射，未识别 part 经 ``ModelTurnProtocolError`` 明确拒绝，
  zero outbound，不裁剪 R21）；经 ``execute_chat_turn`` 执行完整模型回合；
  ``ChatTurnOutcome`` → ``ModelResponse`` 一次转出（拒绝保真、费用缺失保
  None、SDK 默认 usage=0 不倒灌账本——账本事实由 Recorder 经
  ``execute_chat_turn`` 承载，本层 usage 仅 SDK 记账语义）。
- **GatewayStreamedResponse**（StreamedResponse 子类）：``ChatStreamEvent`` →
  SDK 流事件经 ``_parts_manager`` 穷举分发（pydantic-ai 模型适配器规范：每个
  part 类型独立方法、一处穷举分发、未知类型显式报错）；有界通道（SQ-02
  「实际 delta 有界事件」——``asyncio.Queue(maxsize=...)``，生产侧 on_event
  await 即背压）；``close_stream`` 取消并 join 生产任务（§6.4-1 每回合一个
  生产任务拥有 ``execute_chat_turn`` 至结算；取消 join 后继续传播
  CancelledError，R2）。
- **身份与拒绝语义**：``model_call_id`` 经 ``provider_response_id`` 承载（SDK
  请求追踪字段）+ 账本 invocation（invocation_id=model_call_id）双通道稳定
  关联（HANDOFF §6.1 L197）；refused 完整回合以 ``UnexpectedModelBehavior``
  抛出（携带 refusal 原文），已公开分片后失败保 partial/unknown（§6.4-5，
  不拼不同 attempt）——拒绝不换候选规避由 Executor 承载（DD §10.1）。
- **ResolveModelId 能力**（仅 ``qtrading:bound``）依赖 Harness
  ``ModelResolutionContext``，随 D-01 capabilities 接线（§2）；本模块只承载
  模型实例与映射契约。

model_name 固定 ``qtrading:bound``（选型别名事实，不伪装具体模型）；实际模型
经响应 ``actual_model`` → ``ModelResponse.model_name`` 承载。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Final, Protocol, cast

from core.model_management_types import (
    AssistantToolCall,
    ChatMessage,
    ChatStreamEvent,
    ChatTurnOutcome,
    ChatTurnRequest,
    ChatTurnResponse,
    ExecutionSnapshot,
    FunctionToolSpec,
    FinishReason,
    ModelTurnProtocolError,
    NormalizedLLMUsage,
    TextContentPart,
)
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    ModelResponseStreamEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

logger = logging.getLogger(__name__)

#: 选型别名（ResolveModelId 仅解析该别名，HANDOFF §6.2）
BOUND_MODEL_ALIAS: Final[str] = "qtrading:bound"

# 流通道有界深度（SQ-02「实际 delta 有界事件」）：生产侧 on_event await 受阻
# 即对适配器产生背压；消费侧 get 逐事件取出。有界值不承载语义，仅需有限。
_CHANNEL_MAXSIZE: Final[int] = 256

#: 已知但设计明示不进入公开桥的响应 part（R21 允许丢弃：设计 §10.1「Thinking
#: 不进入公开」）；其余未知 part 一律 ModelTurnProtocolError 明确拒绝。
_SKIPPED_RESPONSE_PART_KINDS: Final[tuple[str, ...]] = ("thinking",)

#: 流事件工具分片的 vendor id 前缀（_parts_manager 按 vendor id 聚合增量）
_TOOL_VENDOR_PREFIX: Final[str] = "tool"

#: _TurnChannel 内部哨兵：仅表示「关闭唤醒」，不是队列项
_TURN_CLOSED: Final[object] = object()


class ChatTurnExecutor(Protocol):
    """Executor 最小结构协议（模型桥不感知 Executor 全貌，DI 可测）。"""

    async def execute_chat_turn(
        self,
        request: ChatTurnRequest,
        snapshot: ExecutionSnapshot,
        *,
        binding: Mapping[str, Any],
        on_event: Any = None,
    ) -> ChatTurnOutcome: ...


# ---------------------------------------------------------------------------
# SDK 消息 → core ChatTurnRequest 投影（§6.3，未识别 part 明确拒绝）
# ---------------------------------------------------------------------------


def _reject_part(part: Any, path: str) -> ModelTurnProtocolError:
    kind = getattr(part, "part_kind", type(part).__name__)
    return ModelTurnProtocolError("unknown_part", f"{path}.{kind}")


def _tool_args_to_json(args: str | Mapping[str, Any]) -> str:
    """ToolCallPart.args（str | dict）→ 闭合 JSON 对象字符串（§10.1）。

    str 原样承载（闭合校验由核心 ``ensure_valid`` / 适配器把关）；Mapping 经
    json 序列化（ensure_ascii=False 保中文、sort_keys 确定性）。
    """
    if isinstance(args, str):
        return args
    return json.dumps(args, ensure_ascii=False, sort_keys=True)


def _content_to_text(content: Any, path: str) -> str:
    """ToolReturnPart.content（str | 任意 JSON 值）→ 文本投影。

    str 原样；Mapping / Sequence（非 str/bytes）按 JSON 序列化；标量
    （bool/int/float）文本化；其余类型明确拒绝（不裁剪，R21）。``None`` →
    ""（工具执行成功无返回的合法形态——空不是缺失）。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping | Sequence) and not isinstance(content, (str, bytes)):
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    if isinstance(content, bool | int | float):
        return json.dumps(content)
    raise _reject_part(content, f"{path}.tool_return.content")


def _map_request_parts(message: ModelRequest, path: str) -> tuple[list[str], list[ChatMessage]]:
    """ModelRequest parts → (instructions 增量, 聊天消息增量)，保序。

    - SystemPromptPart → instructions（保序追加，§6.3「SystemPromptPart/
      instructions 保持顺序」）；
    - UserPromptPart：str 与 TextContent 按文本处理（不能非 str 一律当图片，
      §6.3）；BinaryContent（PNG/JPEG binary 引用）随 E-02 附件装配接入——
      当前明确拒绝（zero outbound，NOTE(lazy) 见模块尾）；
    - ToolReturnPart / RetryPromptPart：tool_call_id 严格配对映射为 tool 角色；
      RetryPromptPart 无 tool_call_id（输出校验重试反馈）映射为 user 文本；
    - 未知 part：ModelTurnProtocolError（zero outbound，不裁剪 R21）。
    """
    instructions: list[str] = []
    messages: list[ChatMessage] = []
    for index, part in enumerate(message.parts):
        part_path = f"{path}[{index}]"
        match part:
            case SystemPromptPart(content=content):
                instructions.append(content)
            case UserPromptPart(content=content):
                if isinstance(content, str):
                    text = content
                elif isinstance(content, TextContent):
                    text = content.content
                else:
                    # BinaryContent 等非文本内容：明确拒绝，不静默降级为图片
                    raise _reject_part(part, f"{part_path}.user_prompt.content")
                messages.append(ChatMessage(role="user", content_parts=(TextContentPart(text=text),)))
            case ToolReturnPart(tool_call_id=call_id, content=content, tool_name=name):
                if not call_id:
                    raise ModelTurnProtocolError("invalid_field", f"{part_path}.tool_return.tool_call_id")
                messages.append(
                    ChatMessage(
                        role="tool",
                        content_parts=(TextContentPart(text=_content_to_text(content, part_path)),),
                        tool_call_id=call_id,
                        metadata={"tool_name": name} if name else {},
                    )
                )
            case RetryPromptPart(content=content, tool_call_id=call_id, tool_name=name):
                text = _content_to_text(content, part_path)
                if call_id:
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content_parts=(TextContentPart(text=text),),
                            tool_call_id=call_id,
                            metadata={"tool_name": name} if name else {},
                        )
                    )
                else:
                    messages.append(ChatMessage(role="user", content_parts=(TextContentPart(text=text),)))
            case _:
                raise _reject_part(part, part_path)
    return instructions, messages


def _map_response_history(message: ModelResponse, path: str) -> ChatMessage:
    """ModelResponse（assistant 历史）→ ChatMessage(role="assistant")。

    - TextPart → TextContentPart；ToolCallPart → AssistantToolCall（严格配对
      tool_call_id）；ThinkingPart → 跳过（设计明示 Thinking 不进入公开桥，
      §10.1——已知丢弃语义，非未知字段静默丢失）；
    - 其余 part（NativeToolCallPart / CompactionPart 等）：明确拒绝；
    - tool-only assistant 消息（无 text）合法（§6.3 content=None 语义）：
      ``assistant_tool_calls`` 承载事实，content_parts 允许空。
    """
    content_parts: list[TextContentPart] = []
    tool_calls: list[AssistantToolCall] = []
    for index, part in enumerate(message.parts):
        part_path = f"{path}[{index}]"
        if part.part_kind in _SKIPPED_RESPONSE_PART_KINDS:
            continue
        match part:
            case TextPart(content=content):
                content_parts.append(TextContentPart(text=content))
            case ToolCallPart(tool_name=name, args=args, tool_call_id=call_id):
                if not call_id:
                    raise ModelTurnProtocolError("invalid_field", f"{part_path}.tool_call.tool_call_id")
                tool_calls.append(
                    AssistantToolCall(
                        call_id=call_id,
                        name=name,
                        arguments_json=_tool_args_to_json(cast("str | dict[str, Any]", args)),
                    )
                )
            case _:
                raise _reject_part(part, part_path)
    return ChatMessage(
        role="assistant",
        content_parts=tuple(content_parts),
        assistant_tool_calls=tuple(tool_calls),
    )


def _map_tool_spec(spec: ToolDefinition, *, output_tool: bool, schema_version: str) -> FunctionToolSpec:
    """ToolDefinition → FunctionToolSpec：完整 json_schema + 准确 schema 版本。

    ``outer_typed_dict_key`` / ``strict`` / ``metadata`` 等 SDK 侧控制字段不进
    公开桥（§6.3 工具白名单五元组：name/description/json_schema/schema_version/
    output_tool）。
    """
    return FunctionToolSpec(
        name=spec.name,
        description=spec.description or "",
        json_schema=spec.parameters_json_schema or {},
        schema_version=schema_version,
        output_tool=output_tool,
    )


def _deadline_from_settings(model_settings: Mapping[str, Any]) -> float:
    """回合冻结绝对 deadline（单调时钟）：ModelSettings['timeout']（秒）投影。

    timeout / deadline 区分（§6.3）：SDK ``timeout`` 为回合预算来源，桥层换算
    为单调时钟绝对值；缺失 / 元组形态 / 非正数明确拒绝（zero outbound，
    不臆造默认预算，R21）。
    """
    timeout = model_settings.get("timeout")
    if not isinstance(timeout, int | float) or isinstance(timeout, bool) or timeout <= 0:
        raise ModelTurnProtocolError("invalid_field", "$.settings.timeout")
    return asyncio.get_running_loop().time() + float(timeout)


def _frozen_params(model_settings: Mapping[str, Any]) -> dict[str, Any]:
    """冻结采样参数：settings 全量透传（timeout 已消费为 deadline，剔除）。

    未知键原样保留（不裁剪）；适配器 / 供应商对不支持的键自行报错（§10.1
    参数完整性优先，桥层不代裁剪）。
    """
    return {key: value for key, value in model_settings.items() if key != "timeout"}


def build_chat_turn_request(
    messages: Sequence[ModelMessage],
    model_settings: Mapping[str, Any],
    model_request_parameters: ModelRequestParameters,
    *,
    tool_schema_version: str,
    model_call_id: str | None = None,
) -> ChatTurnRequest:
    """SDK 消息 → core ``ChatTurnRequest`` 一次投影（§6.3）。

    - 消息保序映射（未识别 part 明确拒绝 zero outbound，R21 不裁剪）；
    - 工具：``function_tools`` → 权限工具，``output_tools`` → 结构 final
      （output_tool=True）；``output_object``（native/prompted 模式）→
      ``output_schema``；
    - 冻结：``frozen_params`` 全量透传（timeout 消费为 deadline）、
      ``max_output_tokens`` 取 ``max_tokens``、deadline 取单调时钟换算；
    - ``model_call_id`` 缺省生成（uuid4 hex，一回合一身份）。

    Raises:
        ModelTurnProtocolError: 未识别 part / 非法 tool_call_id / timeout 缺失
            或畸形 / schema 版本缺失（zero outbound，R21）。
    """
    if not tool_schema_version:
        raise ModelTurnProtocolError("invalid_field", "$.tool_schema_version")
    instructions: list[str] = []
    chat_messages: list[ChatMessage] = []
    for index, message in enumerate(messages):
        message_path = f"$.messages[{index}]"
        match message:
            case ModelRequest():
                msg_instructions, msg_messages = _map_request_parts(message, message_path)
                instructions.extend(msg_instructions)
                chat_messages.extend(msg_messages)
            case ModelResponse():
                chat_messages.append(_map_response_history(message, message_path))
            case _:
                raise _reject_part(message, message_path)
    # 请求级 instructions（prepare_request 已装配 instruction_parts：静态
    # Agent(instructions=...) 与动态部分保序）；无独立 instructions 字段（§6.3
    # SystemPromptPart/instructions 保序由 messages 内 SystemPromptPart +
    # instruction_parts 共同承载）
    for part in model_request_parameters.instruction_parts or []:
        instructions.append(part.content)

    output_tools = tuple(
        _map_tool_spec(spec, output_tool=True, schema_version=tool_schema_version)
        for spec in model_request_parameters.output_tools
    )
    function_tools = tuple(
        _map_tool_spec(spec, output_tool=False, schema_version=tool_schema_version)
        for spec in model_request_parameters.function_tools
    )
    output_object = model_request_parameters.output_object
    request = ChatTurnRequest(
        model_call_id=model_call_id or uuid.uuid4().hex,
        messages=tuple(chat_messages),
        instructions=tuple(instructions),
        tools=(*function_tools, *output_tools),
        output_schema=output_object.json_schema if output_object is not None else None,
        frozen_params=_frozen_params(model_settings),
        max_output_tokens=model_settings.get("max_tokens"),
        deadline=_deadline_from_settings(model_settings),
    )
    request.ensure_valid()
    return request


# ---------------------------------------------------------------------------
# core 回合事实 → SDK 响应（§6.3：拒绝保真、费用缺失保 None）
# ---------------------------------------------------------------------------

#: core FinishReason（§10.1：final/tool_calls/refused/length/error）→ SDK FinishReason
#: （stop/length/content_filter/tool_call/error）；refused → content_filter 保真
_FINISH_REASON_MAP: Final[dict[str, str]] = {
    "final": "stop",
    "length": "length",
    "tool_calls": "tool_call",
    "refused": "content_filter",
    "error": "error",
}


def map_finish_reason(finish_reason: FinishReason) -> str:
    """core finish_reason → SDK FinishReason；未知值明确拒绝（不伪装）。"""
    mapped = _FINISH_REASON_MAP.get(finish_reason)
    if mapped is None:
        raise ModelTurnProtocolError("invalid_field", f"$.finish_reason.{finish_reason}")
    return mapped


def map_usage(usage: NormalizedLLMUsage | None) -> RequestUsage:
    """NormalizedLLMUsage → SDK ``RequestUsage``（仅 SDK 记账语义）。

    账本事实由 Recorder 经 ``execute_chat_turn`` 承载——SDK 默认 0 **不倒灌
    账本**（§6.3）；核心字段 None（未计量）在 SDK 层以 0 + unmeasured 标记
    承载，不进入任何落账路径。
    """
    if usage is None:
        return RequestUsage(unmeasured_requests=1)
    details: dict[str, int] = {}
    if usage.cache_read_tokens is not None:
        details["cache_read_tokens"] = usage.cache_read_tokens
    if usage.cache_write_tokens is not None:
        details["cache_write_tokens"] = usage.cache_write_tokens
    return RequestUsage(
        input_tokens=usage.input_total_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        details=details,
    )


def map_response_parts(response: ChatTurnResponse) -> tuple[ModelResponsePart, ...]:
    """ChatTurnResponse 事实 → SDK parts（最终文本一次转出，§6.3）。

    text 与 tool_calls 均为事实原样承载（可并存）；``structured_output`` 单独
    存在（无 text / tool_calls）时按确定性 JSON 文本承载（prompted 模式消费方
    按最终文本解析）。
    """
    parts: list[ModelResponsePart] = []
    if response.text:
        parts.append(TextPart(content=response.text))
    for call in response.tool_calls:
        parts.append(
            ToolCallPart(
                tool_name=call.name,
                args=call.arguments_json,
                tool_call_id=call.call_id,
            )
        )
    if not parts and response.structured_output is not None:
        parts.append(TextPart(content=json.dumps(response.structured_output, ensure_ascii=False, sort_keys=True)))
    return tuple(parts)


# ---------------------------------------------------------------------------
# 流通道（owner-loop 有界队列；跨 loop 直接 await 禁止 §10.1）
# ---------------------------------------------------------------------------


class _TurnChannel:
    """生产者（execute_chat_turn 任务）与消费者（SDK 流迭代）之间的有界通道。

    - ``push``：on_event 回调（生产侧 await 受阻即背压）；
    - ``get``：消费侧逐事件取出——先取尽已入队事件，通道关闭后投递错误
      （一次性）或返回 None（哨兵语义经关闭事件承载，不占队列槽位）；
    - ``close``：**同步非阻塞**——登记终态事实并唤醒阻塞读者（R-B02-1：旧实现
      在队列满时 ``await put(None)`` 会在消费方停读时永久挂死关闭方）；关闭后
      生产侧以取消唤醒（close_stream 路径）；
    - ``join``：join 生产任务——已投递错误静默回收，未投递错误就地抛出
      （CancelledError 始终传播，R2）。

    仅在 owner 事件循环内使用（§10.1：不跨 loop 直接 await Queue）。
    ``producer`` 由 ``request_stream`` 在任务创建后回填（装配期瞬态 None，
    同一 owner 循环内顺序执行，无并发窗口）。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_CHANNEL_MAXSIZE)
        self._closed = False
        self._closed_event = asyncio.Event()
        self.producer: asyncio.Task[None] | None = None
        self._outcome: ChatTurnOutcome | None = None
        self._error: BaseException | None = None
        self._error_delivered = False

    async def push(self, event: ChatStreamEvent) -> None:
        await self._queue.put(event)

    def close(self, *, outcome: ChatTurnOutcome | None = None, error: BaseException | None = None) -> None:
        """登记终态并唤醒阻塞读者（同步非阻塞；幂等登记以最后调用为准）。"""
        self._outcome = outcome
        self._error = error
        self._closed = True
        self._closed_event.set()

    async def get(self) -> ChatStreamEvent | None:
        while True:
            try:
                return cast(ChatStreamEvent, self._queue.get_nowait())
            except asyncio.QueueEmpty:
                pass
            if self._closed:
                # 关闭且取尽：一次性投递错误，此后干净结束（§6.4-4 终态事实保真）
                if self._error is not None and not self._error_delivered:
                    self._error_delivered = True
                    raise self._error
                return None
            item = await self._wait_get_or_close()
            if item is not _TURN_CLOSED:
                return cast(ChatStreamEvent, item)
            # 关闭唤醒：回到顶部重查残留事件，再按关闭语义结束

    async def _wait_get_or_close(self) -> Any:
        """空时等待事件或关闭唤醒（先完成者生效；二者同时完成以取件优先）。"""
        get_task = asyncio.ensure_future(self._queue.get())
        closed_task = asyncio.ensure_future(self._closed_event.wait())
        try:
            done, _ = await asyncio.wait({get_task, closed_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (get_task, closed_task):
                if not task.done():
                    task.cancel()
        if get_task in done and not get_task.cancelled():
            return get_task.result()
        return _TURN_CLOSED

    @property
    def outcome(self) -> ChatTurnOutcome | None:
        return self._outcome

    async def join(self) -> None:
        """join 生产任务：回收任务结果；未投递错误就地抛出（R2 取消传播）。"""
        producer = self.producer
        if producer is None:
            return
        try:
            await producer
        except asyncio.CancelledError:
            raise
        except BaseException:
            if self._error is not None and self._error_delivered:
                return  # 已就地向消费方投递，不再二次抛出
            raise


@dataclass
class GatewayStreamedResponse(StreamedResponse):
    """聊天回合流响应（B-02-4）：``ChatStreamEvent`` → SDK 流事件穷举分发。"""

    channel: _TurnChannel
    model_call_id: str
    _timestamp: datetime = field(default_factory=lambda: datetime.now(UTC), init=False)
    # 声明计量追踪选择（SDK StreamedResponse._usage_received 契约：供应商可省略
    # usage 的流以 False 默认声明此字段选择加入——流结束未上报任何 usage 时，
    # SDK usage 属性追加 unmeasured_requests=1 标记，费用缺失不伪 0，§6.3/R21）。
    # 检视 S-B02-7 曾判为死字段，经 SDK 源码核验为误判（models/__init__.py
    # 「opts in by declaring this field with a False default」），保留不删。
    _usage_received: bool | None = field(default=False, init=False, repr=False, compare=False)

    @property
    def model_name(self) -> str:
        return BOUND_MODEL_ALIAS

    @property
    def provider_name(self) -> str | None:
        return "qtrading"

    @property
    def provider_url(self) -> str | None:
        return None

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        # model_call_id 经 provider_response_id 承载（SDK 请求追踪通道；账本
        # 关联经 invocation=model_call_id 双通道稳定，HANDOFF §6.1 L197）
        self.provider_response_id = self.model_call_id
        while True:
            item = await self.channel.get()
            if item is None:
                break
            match item.kind:
                case "text":
                    # TextPartDelta 穷举分发（pydantic-ai 模型适配器规范）
                    if item.text_delta is None:
                        raise ModelTurnProtocolError("invalid_field", "$.text_delta")
                    for event in self._parts_manager.handle_text_delta(vendor_part_id=None, content=item.text_delta):
                        yield event
                case "tool_call_delta":
                    # SDK 语义：tool_name / tool_call_id 为建件事实（tool_name
                    # 是增量拼接），仅在建件时携带；后续分片传 None 避免重复
                    # 拼接（重复名缺陷），args 增量始终承载
                    vendor_id = (_TOOL_VENDOR_PREFIX, item.call_index)
                    existing = self._parts_manager.get_part_by_vendor_id(vendor_id)
                    event = self._parts_manager.handle_tool_call_delta(
                        vendor_part_id=vendor_id,
                        tool_name=None if existing is not None else item.name,
                        args=item.arguments_fragment,
                        tool_call_id=None if existing is not None else item.call_id,
                    )
                    if event is not None:
                        yield event
                case "tool_call_closed":
                    # 闭合 JSON 经增量片段累积承载（适配器契约：closed == 片段
                    # 拼接，B-02-1 _consume_chat_stream 校验）；仅当无任何片段
                    # 时按闭合参数整块建件
                    vendor_id = (_TOOL_VENDOR_PREFIX, item.call_index)
                    if self._parts_manager.get_part_by_vendor_id(vendor_id) is None:
                        yield self._parts_manager.handle_tool_call_part(
                            vendor_part_id=vendor_id,
                            tool_name=item.name or "",
                            args=item.closed_arguments_json,
                            tool_call_id=item.call_id,
                        )
                case "finish":
                    # §6.4-4 分别确认：finish（协议事实，缺失即协议错误）/
                    # usage（缺失保 None → unmeasured 标记）/ 流关闭（通道
                    # 哨兵）/ 结算（账本事实，随 outcome 由 Recorder 承载）
                    if item.finish_reason is None:
                        raise ModelTurnProtocolError("invalid_field", "$.finish_reason")
                    self.finish_reason = cast(Any, map_finish_reason(item.finish_reason))
                    if item.usage is not None:
                        self._add_usage(map_usage(item.usage), reported=True)
                case _:
                    # 未识别流事件：明确失败（外发事实已存在——确认失败收敛，
                    # 不伪装成功，R21）
                    raise ModelTurnProtocolError("unknown_part", f"$.stream_event.{item.kind}")
        # 流关闭确认（§6.4-4）：终态事实保真——失败回合（拒绝 / 分片后失败）
        # 就地抛出（分片已公开，不拼不同 attempt），不伪装干净结束
        outcome = self.channel.outcome
        if outcome is not None and outcome.status == "failed":
            response = outcome.response
            if response is not None:
                raise UnexpectedModelBehavior(f"chat turn refused by provider: {response.refusal}", response.refusal)
            raise UnexpectedModelBehavior(f"chat turn failed: {outcome.error_code}", outcome.partial_text)

    async def close_stream(self) -> None:
        """消费取消路径（AgentStream.cancel）：中止生产任务并 join。

        生产任务取消沿 Executor F19 受限清理收敛后传播 CancelledError（R2）；
        未投递错误就地抛出。
        """
        if self.channel.producer is not None and not self.channel.producer.done():
            self.channel.producer.cancel()
        await self.channel.join()


class GatewayModel(Model):
    """聊天回合固定框架模型（B-02-4，§10.1）。

    DI：executor（聊天执行边界）/ binding（受理冻结模型绑定）/ snapshot（派发
    时点冻结执行快照）/ tool_schema_version（工具 schema 版本事实，装配侧提供）。
    """

    # 桥层语义：全部通用 settings 原样透传 frozen_params（适配器 / 供应商对
    # 不支持键自行报错）；不声明任何「忽略」键，SDK 通用告警口径不适用
    unsupported_settings: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        executor: ChatTurnExecutor,
        binding: Mapping[str, Any],
        snapshot: ExecutionSnapshot,
        *,
        tool_schema_version: str,
        profile: ModelProfile | None = None,
    ) -> None:
        super().__init__(settings=None, profile=profile)
        self._executor = executor
        self._binding = binding
        self._snapshot = snapshot
        self._tool_schema_version = tool_schema_version

    # Model 抽象面 ---------------------------------------------------------

    @property
    def model_name(self) -> str:
        return BOUND_MODEL_ALIAS

    @property
    def system(self) -> str:
        return "qtrading"

    @property
    def provider(self) -> None:
        return None

    @property
    def profile(self) -> ModelProfile:
        # 无供应商解析机制的固定桥：显式默认 profile（supports_tools 等按
        # DEFAULT_PROFILE 缺省语义），不触碰 genai-prices / provider 注册表
        return ModelProfile()

    def _provider_model_profile(self) -> ModelProfile:
        return ModelProfile()

    # 回合执行 -------------------------------------------------------------

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> ModelResponse:
        """完整模型回合（非流）：一次投影 + execute_chat_turn + 一次转出。"""
        resolved_settings, params = self.prepare_request(model_settings, model_request_parameters)
        prepared = self.prepare_messages(messages, params)
        request = build_chat_turn_request(
            prepared, resolved_settings or {}, params, tool_schema_version=self._tool_schema_version
        )
        outcome = await self._executor.execute_chat_turn(request, self._snapshot, binding=self._binding, on_event=None)
        return self._response_from_outcome(outcome)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[GatewayStreamedResponse]:
        """流式回合（Agent 准入必需，§10.2）：每回合一个生产任务（§6.4-1）。

        生产任务拥有 ``execute_chat_turn`` 至结算；有界通道背压（SQ-02）；
        消费退出 / 取消兜底 join（CancelledError 继续传播，R2）。
        """
        resolved_settings, params = self.prepare_request(model_settings, model_request_parameters)
        prepared = self.prepare_messages(messages, params)
        request = build_chat_turn_request(
            prepared, resolved_settings or {}, params, tool_schema_version=self._tool_schema_version
        )
        channel = _TurnChannel()
        streamed = GatewayStreamedResponse(
            channel=channel,
            model_call_id=request.model_call_id,
            model_request_parameters=params,
        )
        producer = asyncio.create_task(self._run_turn(request, streamed))
        channel.producer = producer
        try:
            yield streamed
        finally:
            # 消费侧退出（正常读完 / 异常 / 取消）兜底：中止未完成生产任务并
            # join——未投递错误就地抛出，CancelledError 继续传播（R2）
            if channel.producer is not None and not channel.producer.done():
                channel.producer.cancel()
            await channel.join()

    async def _run_turn(self, request: ChatTurnRequest, streamed: GatewayStreamedResponse) -> None:
        """生产任务（§6.4-1）：拥有 execute_chat_turn 至结算，事件推通道。"""
        channel = streamed.channel
        try:
            outcome = await self._executor.execute_chat_turn(
                request, self._snapshot, binding=self._binding, on_event=channel.push
            )
        except BaseException as e:  # 关闭唤醒读者后由 join 按 R2 分流
            channel.close(error=e)
            raise
        else:
            channel.close(outcome=outcome)

    def _response_from_outcome(self, outcome: ChatTurnOutcome) -> ModelResponse:
        """ChatTurnOutcome → ModelResponse（拒绝保真、失败如实抛出）。"""
        if outcome.status == "succeeded" and outcome.response is not None:
            response = outcome.response
            finish = map_finish_reason(response.finish_reason)
            if finish == "content_filter":
                # 拒绝保真（§6.3）：拒绝语义与原文一次转出——SDK 惯例以
                # UnexpectedModelBehavior 抛出，账本事实已由 Recorder 承载
                raise UnexpectedModelBehavior(f"chat turn refused by provider: {response.refusal}", response.refusal)
            return ModelResponse(
                parts=map_response_parts(response),
                usage=map_usage(response.usage),
                model_name=response.actual_model,
                model_id=BOUND_MODEL_ALIAS,
                provider_response_id=outcome.model_call_id,
                finish_reason=cast(Any, finish),
            )
        # 失败终态（transport failed / 非流路径零公开分片）：如实抛出稳定码，
        # 不伪造响应（R21）；工作流侧经 DBOS 重试语义接续
        raise UnexpectedModelBehavior(f"chat turn failed: {outcome.error_code}", outcome.partial_text)


# NOTE(lazy): BinaryContent（PNG/JPEG）→ ImageContentPart 的 binary_ref 注册
# 通道随 E-02 附件装配接入（资源票据/C-04 承载）。ceiling: 当前图片内容在
# 桥层明确拒绝（zero outbound）。upgrade: E-02 附件装配落地时接入 binary_ref
# 解析并删除拒绝分支。
