"""统一模型管理执行 / 发布类型与异常（S1 交付，ADR-0014 §4 公共契约）。

S3 起下沉至 ``core/``（ADR-0014 §4）：本模块为 data（AIUsageDao 账本）与
services（Executor / Recorder / 配置服务）跨层共享的纯类型契约，仅依赖
stdlib，符合 CLAUDE.md §4.2「多层引用的基础设施提升到 core」准入。

类型约定（ADR-0014 §4）：
- 全部 frozen dataclass + slots；容器一律 tuple / Mapping 只读视图，不发布可变 dict / list；
- 配置 schema 从 ``utils.config_models`` 消费，本模块不反向进入 utils 存储层；
- ConfigurationSaveOutcome 三态与数据库 persisted 状态（PersistenceOutcome）不混用；
- ``asyncio.CancelledError`` 始终传播，本模块任何异常不得包装取消。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal, Protocol
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# 异常契约
# ---------------------------------------------------------------------------


class ModelOutputValidationError(ValueError):
    """业务输出校验失败（§6.1）：validate_output 抛出，Executor 按失败表格切换。"""


class ModelConfigurationConflictError(RuntimeError):
    """revision 冲突（§5.1）：磁盘 revision 与 expected 不符，旧配置仍有效。"""

    def __init__(self, expected_revision: int, current_revision: int | None) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(f"model_management revision conflict: expected {expected_revision}, disk {current_revision}")


class ModelConfigurationCorruptError(RuntimeError):
    """配置磁盘损坏 / 校验失败（§5.1）：禁止静默落默认，由调用方显式处置。"""


class MigrationTransitionError(RuntimeError):
    """非法迁移状态变更（§12.2）：未取屏障 / 越 phase / 非 owner 接管 / seal 不一致。"""


class LegacyDispatchBlockedError(RuntimeError):
    """旧链在 draining / sealed / active 被停接或拒绝（§12.2 D2）。

    ``admission`` 取 ``evaluate_legacy_admission`` 的判定值
    （``"draining_stop"`` / ``"refused"``），调用方按「AI 功能明确不可用」处置，
    不得降级伪装成功（R21）。

    F05 fail-closed 扩展值 ``"read_failed"``：分派 / 准入配置读取过程异常
    （配置损坏、锁失败、IO 超时等）时授权状态未知，三入口 helper 与批量
    准入以该值阻断旧链并传出（``from`` 原始读取异常），不降级放行。
    """

    def __init__(self, admission: str, message: str | None = None) -> None:
        self.admission = admission
        super().__init__(message or f"legacy dispatch blocked (admission={admission})")


class LegacySealedError(RuntimeError):
    """旧链记账写入被月度封口拒绝（S6 D6 写入口）：封口后 app_state 基线冻结。

    ``month`` 为被拒月份（``YYYY-MM``）；迟到结算差额经 DAO
    ``apply_monthly_delta``（``legacy_tail`` 分组）兜底入账，本异常不得被
    ``except Exception`` 日志分支吞没，也不得降级伪装成功（R21）。
    """

    def __init__(self, month: str, message: str | None = None) -> None:
        self.month = month
        super().__init__(message or f"legacy ledger month {month} is sealed; write rejected")


# ---------------------------------------------------------------------------
# 配置保存三态（ADR-0014 §4）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigurationSaveOutcome:
    """配置保存结果三态：committed / not_committed / commit_unknown。

    替换前确定失败为 not_committed；os.replace 后回执丢失 / 取消 / 缓存更新失败
    不得据异常推断未提交，按相同锁顺序重读磁盘凭回执确认或保持 commit_unknown；
    结果明确前不自动重试、不轮换凭据、不回滚。
    """

    status: Literal["committed", "not_committed", "commit_unknown"]
    current_revision: int | None = None
    committed_revision: int | None = None
    last_config_commit: Mapping[str, Any] | None = None


# ---------------------------------------------------------------------------
# 快照族（§3.3）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelManagementSnapshot:
    """已提交非敏感配置快照（get_snapshot 产出）。"""

    snapshot_id: str
    config_revision: int
    migration_state: Literal["pending", "active"]
    connections: tuple[Mapping[str, Any], ...]
    scenario_routes: tuple[Mapping[str, Any], ...]
    usage_policy: Mapping[str, Any]
    security_revision: int
    created_at: str
    # 目标级外发授权 grant（§4.3）：设置与向导的「场景检查区」共用授权摘要的事实源；
    # 仅承载展示所需的非敏感字段（目标指纹 / 用途 / 撤销时间），不含任何秘密。
    security_grants: tuple[Mapping[str, Any], ...] = ()
    # 当前授权范围版本（§4.3 grant.scope_version 同域，F01）：安全状态自身的范围
    # 世代，独立于 security_revision（后者随每次安全命令递增，与「范围是否匹配」
    # 无关）。分派快照冻结该值供 authorize_attempt 匹配 grant；范围轮转命令
    # （未来交付）递增它使旧世代 grant 全部失配。
    security_scope_version: int = 1
    # §12.2 切换屏障只读视图（S6 D2）：draining / sealed 迁移期状态；None = 未开启迁移
    migration_transition: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    """一次请求 / 批量 run 冻结的执行快照（§3.3）：普通保存只影响后续请求。"""

    snapshot_id: str
    run_id: str | None
    config_revision: int
    routes: tuple[Mapping[str, Any], ...]  # scenario_id → 有序候选 + route_revision + 连接修订
    connections: tuple[Mapping[str, Any], ...]  # 连接定义（含价格与文件摘要引用）
    business_contract_versions: Mapping[str, str]  # task_id → prompt / 契约版本
    security_scope_version: int
    created_at: str


# ---------------------------------------------------------------------------
# 完整 turn / stream 协议（设计正本 §7.1，M01）
# ---------------------------------------------------------------------------


class ModelTurnProtocolError(ValueError):
    """完整 turn/stream 协议失败（§7.1）：分片未闭合 / 工具配对非法 / 非法 JSON。

    服务边界不把该异常当作可切换候选的质量失败——它是协议事实，调用方须停止
    派发或交由既有错误分类处理。
    """

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


FINISH_REASONS: Final[tuple[str, ...]] = ("final", "tool_calls", "refused", "length", "error")
FinishReason = Literal["final", "tool_calls", "refused", "length", "error"]
MESSAGE_ROLES: Final[tuple[str, ...]] = ("system", "user", "assistant", "tool")


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters_json: str
    schema_revision: str


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    media_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelTurnRequest:
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...]
    output_contract: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelTurnResult:
    message: ModelMessage
    finish_reason: FinishReason


@dataclass(frozen=True, slots=True)
class ModelStreamDelta:
    sequence: int
    kind: Literal["text", "tool_call", "finish"]
    call_index: int | None = None
    call_id: str | None = None
    name: str | None = None
    text: str | None = None
    arguments_fragment: str | None = None
    finish_reason: FinishReason | None = None


def _invalid(code: str, field_path: str) -> ModelTurnProtocolError:
    return ModelTurnProtocolError(code, field_path)


def _strict_arguments_object(arguments_json: object, field_path: str) -> dict[str, Any]:
    """完整工具参数：必须是 JSON 对象，禁重复键 / 非有限数（§7.1）。"""
    if not isinstance(arguments_json, str):
        raise _invalid("invalid_arguments_json", field_path)

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise _invalid("duplicate_key", f"{field_path}.{key}")
            seen[key] = value
        return seen

    def _constant(name: str) -> object:
        raise _invalid("non_finite_number", f"{field_path}.{name}")

    try:
        parsed = json.loads(
            arguments_json,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=Decimal,
        )
    except ModelTurnProtocolError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError, InvalidOperation) as error:
        raise _invalid("invalid_arguments_json", field_path) from error
    if not isinstance(parsed, dict):
        raise _invalid("arguments_not_object", field_path)
    for key, value in parsed.items():
        if isinstance(value, Decimal) and not value.is_finite():
            raise _invalid("non_finite_number", f"{field_path}.{key}")
    return parsed


def _validate_media_refs(media_refs: object, field_path: str) -> None:
    if not isinstance(media_refs, tuple):
        raise _invalid("invalid_media_refs", field_path)
    for index, ref in enumerate(media_refs):
        if not isinstance(ref, str) or not ref:
            raise _invalid("invalid_media_ref", f"{field_path}[{index}]")


def validate_model_message(message: ModelMessage, field_path: str = "$.message") -> None:
    """单条消息结构与工具调用/返回配对形状（§7.1）。"""
    if not isinstance(message, ModelMessage):
        raise _invalid("invalid_message", field_path)
    if message.role not in MESSAGE_ROLES:
        raise _invalid("invalid_role", f"{field_path}.role")
    if message.content is not None and not isinstance(message.content, str):
        raise _invalid("invalid_content", f"{field_path}.content")
    _validate_media_refs(message.media_refs, f"{field_path}.media_refs")
    if not isinstance(message.tool_calls, tuple):
        raise _invalid("invalid_tool_calls", f"{field_path}.tool_calls")
    for index, call in enumerate(message.tool_calls):
        if not isinstance(call, ToolCall):
            raise _invalid("invalid_tool_call", f"{field_path}.tool_calls[{index}]")
        if not call.call_id or not isinstance(call.call_id, str):
            raise _invalid("invalid_call_id", f"{field_path}.tool_calls[{index}].call_id")
        if not call.name or not isinstance(call.name, str):
            raise _invalid("invalid_tool_name", f"{field_path}.tool_calls[{index}].name")
        _strict_arguments_object(call.arguments_json, f"{field_path}.tool_calls[{index}].arguments_json")

    if message.role == "assistant":
        if message.tool_call_id is not None:
            raise _invalid("tool_call_id_on_assistant", f"{field_path}.tool_call_id")
        if message.content is None and not message.tool_calls:
            raise _invalid("empty_assistant_message", field_path)
    elif message.role == "tool":
        if message.tool_calls:
            raise _invalid("tool_calls_on_tool_message", f"{field_path}.tool_calls")
        if not message.tool_call_id:
            raise _invalid("missing_tool_call_id", f"{field_path}.tool_call_id")
        if message.content is None:
            raise _invalid("empty_tool_content", f"{field_path}.content")
    else:
        if message.tool_calls:
            raise _invalid("tool_calls_on_non_assistant", f"{field_path}.tool_calls")
        if message.tool_call_id is not None:
            raise _invalid("tool_call_id_on_non_tool", f"{field_path}.tool_call_id")
        if message.content is None:
            raise _invalid("empty_message_content", f"{field_path}.content")


def validate_model_turn_request(turn: ModelTurnRequest) -> None:
    """请求整体校验：消息配对、工具定义与输出契约（§7.1）。"""
    if not isinstance(turn, ModelTurnRequest):
        raise _invalid("invalid_turn_request", "$")
    if not isinstance(turn.messages, tuple) or not turn.messages:
        raise _invalid("empty_messages", "$.messages")
    if not isinstance(turn.tools, tuple):
        raise _invalid("invalid_tools", "$.tools")
    if not isinstance(turn.output_contract, str) or not turn.output_contract:
        raise _invalid("missing_output_contract", "$.output_contract")
    if isinstance(turn.max_output_tokens, bool) or not isinstance(turn.max_output_tokens, int):
        raise _invalid("invalid_max_output_tokens", "$.max_output_tokens")
    if turn.max_output_tokens <= 0:
        raise _invalid("non_positive_max_output_tokens", "$.max_output_tokens")

    available_calls: set[str] = set()
    returned_calls: set[str] = set()
    for index, message in enumerate(turn.messages):
        validate_model_message(message, f"$.messages[{index}]")
        if message.role == "assistant":
            for call in message.tool_calls:
                if call.call_id in available_calls:
                    raise _invalid("duplicate_call_id", f"$.messages[{index}].tool_calls")
                available_calls.add(call.call_id)
        elif message.role == "tool":
            assert message.tool_call_id is not None
            if message.tool_call_id not in available_calls:
                raise _invalid("tool_result_without_call", f"$.messages[{index}].tool_call_id")
            if message.tool_call_id in returned_calls:
                raise _invalid("duplicate_tool_result", f"$.messages[{index}].tool_call_id")
            returned_calls.add(message.tool_call_id)

    names: set[str] = set()
    for index, tool in enumerate(turn.tools):
        if not isinstance(tool, ToolDefinition):
            raise _invalid("invalid_tool_definition", f"$.tools[{index}]")
        if not tool.name or not isinstance(tool.name, str):
            raise _invalid("invalid_tool_name", f"$.tools[{index}].name")
        if tool.name in names:
            raise _invalid("duplicate_tool_name", f"$.tools[{index}].name")
        names.add(tool.name)
        if not isinstance(tool.description, str):
            raise _invalid("invalid_tool_description", f"$.tools[{index}].description")
        if not isinstance(tool.schema_revision, str) or not tool.schema_revision:
            raise _invalid("missing_schema_revision", f"$.tools[{index}].schema_revision")
        _strict_arguments_object(tool.parameters_json, f"$.tools[{index}].parameters_json")


def validate_model_turn_result(result: ModelTurnResult) -> None:
    """响应整体校验：assistant 消息 + finish_reason 形状（§7.1）。"""
    if not isinstance(result, ModelTurnResult):
        raise _invalid("invalid_turn_result", "$")
    if result.finish_reason not in FINISH_REASONS:
        raise _invalid("invalid_finish_reason", "$.finish_reason")
    validate_model_message(result.message, "$.message")
    if result.message.role != "assistant":
        raise _invalid("result_message_not_assistant", "$.message.role")
    if result.finish_reason == "final":
        if result.message.content is None:
            raise _invalid("final_without_content", "$.message.content")
        if result.message.tool_calls:
            raise _invalid("final_with_tool_calls", "$.message.tool_calls")
    elif result.finish_reason == "tool_calls":
        if not result.message.tool_calls:
            raise _invalid("tool_calls_without_calls", "$.message.tool_calls")
    else:
        if result.message.tool_calls:
            raise _invalid("terminal_turn_with_tool_calls", "$.message.tool_calls")


def assemble_model_stream(deltas: Sequence[ModelStreamDelta]) -> ModelTurnResult:
    """按 sequence 组装完整响应；未闭合分支/首个 finish 之后片段均为协议错误。

    ``finish`` 只允许出现一次且必须是最后一个增量；工具分片按 ``call_index``
    合并，结束后校验 ID / 名称 / 完整 JSON 参数。调用方只能在返回结果后派发
    工具，未闭合流不得提前授权（§7.1）。
    """
    expected_sequence = 1
    finish: ModelStreamDelta | None = None
    text_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}

    for index, delta in enumerate(deltas):
        if not isinstance(delta, ModelStreamDelta):
            raise _invalid("invalid_stream_delta", f"$.deltas[{index}]")
        if finish is not None:
            raise _invalid("fragment_after_finish", f"$.deltas[{index}]")
        if isinstance(delta.sequence, bool) or not isinstance(delta.sequence, int):
            raise _invalid("invalid_sequence", f"$.deltas[{index}].sequence")
        if delta.sequence != expected_sequence:
            raise _invalid("sequence_gap", f"$.deltas[{index}].sequence")
        expected_sequence += 1

        if delta.kind == "text":
            if not isinstance(delta.text, str) or not delta.text:
                raise _invalid("invalid_text_delta", f"$.deltas[{index}].text")
            if (
                delta.call_index is not None
                or delta.call_id is not None
                or delta.name is not None
                or delta.arguments_fragment is not None
                or delta.finish_reason is not None
            ):
                raise _invalid("mixed_text_delta", f"$.deltas[{index}]")
            text_parts.append(delta.text)
        elif delta.kind == "tool_call":
            if isinstance(delta.call_index, bool) or not isinstance(delta.call_index, int) or delta.call_index < 0:
                raise _invalid("invalid_call_index", f"$.deltas[{index}].call_index")
            if delta.text is not None or delta.finish_reason is not None:
                raise _invalid("mixed_tool_delta", f"$.deltas[{index}]")
            entry = calls.setdefault(delta.call_index, {"call_id": None, "name": None, "fragments": []})
            if delta.call_id is not None:
                if entry["call_id"] not in (None, delta.call_id):
                    raise _invalid("call_id_mismatch", f"$.deltas[{index}].call_id")
                entry["call_id"] = delta.call_id
            if delta.name is not None:
                if entry["name"] not in (None, delta.name):
                    raise _invalid("tool_name_mismatch", f"$.deltas[{index}].name")
                entry["name"] = delta.name
            entry["fragments"].append(delta.arguments_fragment or "")
        elif delta.kind == "finish":
            if delta.finish_reason not in FINISH_REASONS:
                raise _invalid("invalid_finish_reason", f"$.deltas[{index}].finish_reason")
            if (
                delta.call_index is not None
                or delta.call_id is not None
                or delta.name is not None
                or delta.text is not None
                or delta.arguments_fragment is not None
            ):
                raise _invalid("mixed_finish_delta", f"$.deltas[{index}]")
            finish = delta
        else:
            raise _invalid("invalid_delta_kind", f"$.deltas[{index}].kind")

    if finish is None or finish.finish_reason is None:
        raise _invalid("stream_not_finished", "$.deltas")

    tool_calls: list[ToolCall] = []
    for call_index in sorted(calls):
        entry = calls[call_index]
        if not entry["call_id"] or not entry["name"]:
            raise _invalid("incomplete_tool_call", f"$.tool_calls[{call_index}]")
        arguments_json = "".join(entry["fragments"])
        _strict_arguments_object(arguments_json, f"$.tool_calls[{call_index}].arguments_json")
        tool_calls.append(ToolCall(call_id=entry["call_id"], name=entry["name"], arguments_json=arguments_json))

    content = "".join(text_parts)
    result = ModelTurnResult(
        message=ModelMessage(
            role="assistant",
            content=content if content else None,
            tool_calls=tuple(tool_calls),
        ),
        finish_reason=finish.finish_reason,
    )
    validate_model_turn_result(result)
    return result


class StreamingInferenceAdapter(Protocol):
    """可选流式适配器协议（M01）：与 InferenceAdapter 同执行器、同账本。

    ``on_delta`` 是服务层回调，不进入 core 请求；无消费者时调用方仍须返回
    完整 ``AdapterResponse``。
    """

    async def invoke_stream(
        self,
        request: InferenceRequest,
        resolved_connection: ResolvedConnection,
        deadline: float,
        attempt_context: AttemptContext,
        *,
        on_delta: Any,
    ) -> AdapterResponse: ...


# ---------------------------------------------------------------------------
# 请求与连接解析（§6.1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """统一推理请求：deadline 为单调时钟截止值，不是各候选可复用的超时秒数。"""

    scenario_id: str
    task_id: str
    invocation_id: str
    payload: Mapping[str, Any]
    deadline: float
    run_id: str | None = None
    # §7.1 末尾兼容字段：旧文本请求不提供 turn（保持既有构造/消费不变）。
    turn: ModelTurnRequest | None = None


@dataclass(frozen=True, slots=True)
class ResolvedConnection:
    """连接解析结果：凭据版本已由租约持有，本类型不含秘密本身（§5.2）。"""

    connection_id: str
    connection_revision: int
    adapter_kind: Literal["litellm_chat", "gguf_chat", "gguf_sentiment"]
    provider: str
    model: str
    api_base: str | None
    adapter_options: Mapping[str, Any]
    context_window: int | None
    concurrency_limit: int
    credential_ref: str | None
    credential_version: str | None


# ---------------------------------------------------------------------------
# 尝试与许可（§4.3 / §6.2）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """尝试归属上下文：owner 隔离与租约恢复的最小身份（§9.2）。"""

    owner_session_id: str
    owner_epoch: int
    invocation_id: str
    attempt_id: str


@dataclass(frozen=True, slots=True)
class AttemptPermit:
    """单次尝试许可：取得即尝试开始的线性化点，不能用于后续排队 / 重试 / 备用。

    F06：许可绑定实际执行对象——``credential_version`` 为撤销校验所依据的
    快照冻结凭据版本（与出站解析 ``credential_ref`` 同源：
    ``keyring:model-connection-{credential_version}``）；``None`` 表示
    无版本化凭据（ref 缺失或 ``env:`` 形态），无撤销校验对象。
    """

    attempt_id: str
    connection_id: str
    security_revision: int
    granted_at: str
    credential_version: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    """候选决策记录：条件跳过 / 拒绝 / 切换理由，不建实际 attempt。"""

    task_id: str
    candidate_index: int
    connection_id: str | None
    decision: Literal["skipped", "rejected", "attempted", "switched", "succeeded", "failed"]
    reason: str


# ---------------------------------------------------------------------------
# 适配器响应与业务输出（§6.1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NormalizedLLMUsage:
    """五字段用量归一化（§9.1）：input_total = uncached + cache_read + cache_write。

    字段为非负整数或 None，缺失缓存字段不默认 0。
    """

    input_total_tokens: int | None
    uncached_input_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True, slots=True)
class AdapterResponse:
    """适配器返回：内容、程序提供的用量、实际模型与运行元数据。"""

    content: str | None
    usage: NormalizedLLMUsage | None
    actual_model: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # §7.1 末尾兼容字段：旧文本响应不提供 turn（旧消费者无需流字段）。
    turn: ModelTurnResult | None = None


class InferenceAdapter(Protocol):
    """统一推理适配器协议（§6.1，检视 F02）：ScenarioExecutor 消费的唯一适配器形状。

    结构化协议（无需显式继承）：组装方（app 组合根 / 测试替身）只需提供带
    ``invoke`` 方法的对象即可；注册裸函数为适配器即违反本协议，由
    :func:`ensure_inference_adapter` 在组装期拒绝（而非执行期 AttributeError）。
    """

    async def invoke(
        self,
        request: InferenceRequest,
        resolved_connection: ResolvedConnection,
        deadline: float,
        attempt_context: AttemptContext,
    ) -> AdapterResponse: ...


def ensure_inference_adapter(kind: str, adapter: object) -> None:
    """组装期协议校验（检视 F02）：``adapter`` 缺可调用 ``.invoke`` 即 TypeError。

    供适配器工厂（bootstrap 组合根）与 ScenarioExecutor 注入点共用同一校验语义，
    杜绝「函数 / 对象」两种隐式调用规则并存。

    Raises:
        TypeError: 适配器不满足 :class:`InferenceAdapter` 协议。
    """
    if not callable(getattr(adapter, "invoke", None)):
        raise TypeError(
            f"adapter for kind {kind!r} violates InferenceAdapter protocol "
            f"(missing callable .invoke): {type(adapter).__name__}"
        )


# ---------------------------------------------------------------------------
# 外发目标与执行位置（§4.3，F01 分派授权身份的规范来源）
# ---------------------------------------------------------------------------

# 会真实外发数据的适配器类型（§4.3：HTTP 目标即使在局域网也需匹配已确认目标），
# 执行位置需 egress grant。
EGRESS_ADAPTER_KINDS: Final[tuple[str, ...]] = ("litellm_chat",)
# 已知设备内本地、不产生网络出站的适配器类型（gguf_*）：显式豁免清单，
# 与 ResolvedConnection.adapter_kind 的 Literal 白名单一一对应。
LOCAL_ADAPTER_KINDS: Final[tuple[str, ...]] = ("gguf_chat", "gguf_sentiment")


def adapter_kind_requires_egress(adapter_kind: str) -> bool:
    """该适配器类型的执行是否产生网络外发（F01：许可按真实执行位置要求 egress grant）。

    default-deny（fail-closed）三分支：已知出站 kind（EGRESS_ADAPTER_KINDS）
    → True；已知本地 kind（LOCAL_ADAPTER_KINDS）→ False；未知 kind（含新增
    未登记类型）→ True，一律要求 egress grant——新增外发类型漏登记时禁止
    未授权外发；新增本地 kind 须显式登记 LOCAL_ADAPTER_KINDS 方可豁免。
    """

    if adapter_kind in EGRESS_ADAPTER_KINDS:
        return True
    # 仅显式登记的本地 kind 豁免；未知 kind 一并落入 default-deny
    # （宁可多要求 grant，不可漏授权，fail-closed）。
    return adapter_kind not in LOCAL_ADAPTER_KINDS


def normalize_egress_target(api_base: object) -> str | None:
    """规范化外发目标（§4.3 canonical）：scheme / host 小写、IDNA host、显式有效端口、完整 base path。

    拒绝 userinfo / query / fragment（明确非法目标）；``api_base`` 缺失、无 http(s) scheme、
    host 非法或端口越界一律返回 ``None``——不臆造目标（R21）。

    IPv6 字面量（T1）：host 含 ``:`` 时按 RFC 3986 §3.2.2 以方括号重组（``urlsplit().hostname``
    已去括号并小写），且跳过 IDNA——IDNA 仅适用域名标签，对 IP 字面量无意义，
    对非 ASCII 垃圾还会伪造 punycode「域名」身份。非法 bracketed host 由
    ``urlsplit`` 校验拒绝（ValueError → ``None``）。
    """
    if not isinstance(api_base, str):
        return None
    raw = api_base.strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    # `username`/`password` 判空用 is not None：空 userinfo（如 `https://@host`）的
    # 属性值为 `""` 而非 None，按 docstring「拒绝 userinfo」必须一并拒绝。
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return None
    host = parts.hostname
    if not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    else:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 0 < port <= 65535:
        return None
    return f"{scheme}://{host}:{port}{parts.path.rstrip('/')}"


@dataclass(frozen=True, slots=True)
class ValidatedBusinessOutput:
    """业务校验通过的输出（validate_output 产出）。"""

    output: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioExecutionResult:
    """场景执行终态：字段级输出与候选决策，不混用配置保存状态。"""

    scenario_id: str
    invocation_id: str
    status: Literal["succeeded", "partial", "failed", "cancelled"]
    outputs: tuple[Mapping[str, Any], ...]
    candidate_decisions: tuple[CandidateDecision, ...]
    actual_usage: tuple[NormalizedLLMUsage, ...]
    # §7.1 turn 协议：完整响应按尝试顺序投影（旧文本路径为空）。
    turns: tuple[ModelTurnResult, ...] = ()


# ---------------------------------------------------------------------------
# 情绪打分（§6.1 NewsSentimentScorer.score_for_connection）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoredText:
    """单条情绪打分结果：label / 概率三元组 / 数值分。"""

    text_id: str
    label: Literal["利空", "中性", "利好"] | None
    probabilities: tuple[float, float, float] | None  # (利空, 中性, 利好)
    score: float | None


@dataclass(frozen=True, slots=True)
class ScoringOutcome:
    """情绪打分终态（§6.1）：CancelledError 传播，不包装成 unavailable。

    metadata（S2）：运行元数据（processed_count / 逐条 input_tokens / 逐条
    truncation 标记等），只读 Mapping 或 None；不承载业务语义字段，缺失不伪装。
    """

    status: Literal["success", "partial", "unavailable", "invalid_output", "timeout", "worker_error"]
    results: tuple[ScoredText, ...]
    reason: str | None
    usage: NormalizedLLMUsage | None
    metadata: Mapping[str, Any] | None = None


# ---------------------------------------------------------------------------
# 预算与落账（§9.2 / §10）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    """预算预留标识（§9.2）：持久事实源为 ai_invocation.reservations。"""

    reservation_id: str
    task_id: str
    candidate_index: int
    retry_index: int
    amount_cny: str | None  # 十进制字符串，None = usage_only / 无金额预留
    state: Literal["reserved", "dispatched", "held_unconfirmed", "settled", "released"]
    attempt_id: str | None
    owner_session_id: str
    owner_epoch: int


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """尝试记录（§6.1）：携带 owner 身份；用量 / 状态由 Executor 收敛提交。"""

    attempt_id: str
    invocation_id: str
    connection_id: str
    owner_session_id: str
    owner_epoch: int
    status: Literal["dispatched", "succeeded", "failed", "cancelled", "unknown"]
    usage: NormalizedLLMUsage | None


@dataclass(frozen=True, slots=True)
class PersistenceOutcome:
    """落账结果（§6.1）：明确 persisted 与脱敏错误码，不与配置保存状态混用。"""

    persisted: bool
    error_code: str | None = None


# ---------------------------------------------------------------------------
# 单次测试上下文（§4.4，MM-09）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyntheticTestContext:
    """未保存连接的单次测试上下文：冻结草稿身份，不可复用。"""

    test_operation_id: str
    invocation_id: str
    host_identity: str
    draft_connection_id: str
    draft_connection_revision: int
    draft_fingerprint: str
    task_id: str
    credential_version: str | None
    config_revision: int  # 配置归属：新鲜读取的 config_revision（§4.4）
    purpose: Literal["synthetic_test"] = "synthetic_test"


@dataclass(frozen=True, slots=True)
class SyntheticTestConsent:
    """明确点击产生的单次操作许可：不写 security_state.grants、不改配置 revision。

    仅受控测试入口接受；业务入口不能接受测试许可。
    """

    test_operation_id: str
    host_identity: str
    target_fingerprint: str
    draft_fingerprint: str
    scope_version: int
    deadline: float
    purpose: Literal["synthetic_test"] = "synthetic_test"
