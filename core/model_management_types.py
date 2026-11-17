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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol
from urllib.parse import urlsplit

from core.agent_types import AgentValidationError, decode_json_doc

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
class EgressAckPermit:
    """单次外发确认许可（SEC-01，D13-002）：把确认判定绑定到本次尝试 / 连接 / 目标。

    进程级 ``{provider: scope_version}`` 确认记录本身是用户持久同意，不含「哪一次
    尝试」。本类型由 S3 Executor 在许可线性化点签发（``utils.egress_ack``
    ``bind_egress_ack_permit``），随 ``AttemptContext`` 传到云适配器，在凭据解析 /
    网络之前校验：attempt / invocation / owner / 连接 / provider / 模型 / 目标 /
    scope_version / 有效期全部匹配才允许出站，禁止跨 attempt、跨会话或换目标复用
    （缺上下文 fail-closed）。``expires_at`` 为单调时钟口径（``loop.time()``）。
    """

    attempt_id: str
    invocation_id: str
    owner_session_id: str
    owner_epoch: int
    connection_id: str
    provider: str
    model: str
    target_fingerprint: str | None
    scope_version: int
    issued_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """尝试归属上下文：owner 隔离与租约恢复的最小身份（§9.2）。

    ``egress_permit``（D13-002）：本次尝试的外发确认许可。云适配器在出站前校验
    该许可与 ``attempt_id`` / ``invocation_id`` / owner / 连接 / 目标一致；
    ``None`` 表示未绑定（本地模型路径或未签发），云出口据此 fail-closed 拒绝。
    """

    owner_session_id: str
    owner_epoch: int
    invocation_id: str
    attempt_id: str
    egress_permit: EgressAckPermit | None = None


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


# ---------------------------------------------------------------------------
# Agent 聊天 turn / stream 公共协议（设计正本 §10.1，B-01）
# ---------------------------------------------------------------------------

FINISH_REASONS: Final[tuple[str, ...]] = ("final", "tool_calls", "refused", "length", "error")
FinishReason = Literal["final", "tool_calls", "refused", "length", "error"]
CHAT_ROLES: Final[tuple[str, ...]] = ("system", "user", "assistant", "tool")
#: 已选图片分型白名单：仅 PNG/JPEG（§10.1 ContentPart）。
IMAGE_MIME_TYPES: Final[tuple[str, ...]] = ("image/png", "image/jpeg")
#: 流事件封闭分型集合。
STREAM_EVENT_KINDS: Final[tuple[str, ...]] = ("text", "tool_call_delta", "tool_call_closed", "finish")


class ModelTurnProtocolError(ValueError):
    """完整 turn/stream 协议失败（§10.1）：分片未闭合 / 工具配对非法 / 非法 JSON / 未知 part。

    服务边界不把该异常当作可切换候选的质量失败——它是协议事实，调用方须停止
    派发或交由既有错误分类处理；SDK 映射层的 Thinking/Compaction/未知协议
    字段不得静默丢失，一律显式拒绝。
    """

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


def _invalid(code: str, field_path: str) -> ModelTurnProtocolError:
    return ModelTurnProtocolError(code, field_path)


@dataclass(frozen=True, slots=True)
class TextContentPart:
    """文本分型：str 与 TextContent 均按文本处理（§10.1），不 trim。"""

    text: str


@dataclass(frozen=True, slots=True)
class ImageContentPart:
    """已选 PNG/JPEG binary 引用分型：mime 白名单见 IMAGE_MIME_TYPES（§10.1）。"""

    mime: str
    binary_ref: str


#: ContentPart 封闭联合：未知类型明确拒绝，Thinking 不进入公开（无该分型）。
ContentPart = TextContentPart | ImageContentPart


@dataclass(frozen=True, slots=True)
class AssistantToolCall:
    """assistant 发起的工具调用：参数须为闭合 JSON 对象（§10.1）。"""

    call_id: str
    name: str
    arguments_json: str

    def ensure_valid(self) -> None:
        if not self.call_id or not self.name:
            raise _invalid("invalid_field", "$.assistant_tool_calls")
        validate_closed_tool_arguments(self.arguments_json)


def validate_closed_tool_arguments(arguments_json: object) -> dict[str, Any]:
    """完整工具参数校验：闭合 JSON 对象，禁重复键 / 非有限数（§10.1）。

    浮点按 Decimal 精确承载（复用 core.agent_types.decode_json_doc）；
    分片未闭合（非法 JSON）即拒绝——工具参数闭合前不派发。
    """

    if not isinstance(arguments_json, str):
        raise _invalid("invalid_arguments_json", "$")
    try:
        parsed = decode_json_doc(arguments_json)
    except AgentValidationError as exc:
        if exc.code in ("duplicate_key", "non_finite_number"):
            raise _invalid(exc.code, "$") from exc
        raise _invalid("invalid_arguments_json", "$") from exc
    if not isinstance(parsed, dict):
        raise _invalid("invalid_arguments_json", "$")
    return parsed


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """完整聊天消息：role、content_parts、assistant_tool_calls、tool_call_id、来源引用。

    tool-only content=None 合法：assistant 消息 ``content_parts`` 为空且仅含
    ``assistant_tool_calls``；tool 消息必须携带 ``tool_call_id`` 严格配对。
    """

    role: Literal["system", "user", "assistant", "tool"]
    content_parts: tuple[ContentPart, ...]
    assistant_tool_calls: tuple[AssistantToolCall, ...] = ()
    tool_call_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def ensure_valid(self) -> None:
        validate_chat_message(self)


def validate_chat_message(message: ChatMessage) -> None:
    """消息级配对与分型校验（§10.1）；未知 part 类型在封闭联合外即拒绝。"""

    if message.role not in CHAT_ROLES:
        raise _invalid("invalid_field", "$.role")
    if message.role != "assistant" and message.assistant_tool_calls:
        raise _invalid("invalid_pairing", "$.assistant_tool_calls")
    if message.role == "tool" and not message.tool_call_id:
        raise _invalid("invalid_pairing", "$.tool_call_id")
    if message.role != "tool" and message.tool_call_id is not None:
        raise _invalid("invalid_pairing", "$.tool_call_id")
    for index, part in enumerate(message.content_parts):
        path = f"$.content_parts[{index}]"
        if isinstance(part, TextContentPart):
            if not part.text:
                raise _invalid("invalid_part", path)
        elif isinstance(part, ImageContentPart):
            if part.mime not in IMAGE_MIME_TYPES or not part.binary_ref:
                raise _invalid("invalid_part", path)
        else:
            raise _invalid("invalid_part", path)
    for key in message.metadata:
        if not key:
            raise _invalid("invalid_field", "$.metadata")
    for call in message.assistant_tool_calls:
        call.ensure_valid()


@dataclass(frozen=True, slots=True)
class FunctionToolSpec:
    """工具规范：完整 json_schema、准确 schema 版本、output_tool（结构 final）标记。

    ``output_tool=True`` 表示结构化输出 final 工具，不是 MCP 权限工具（§10.1）；
    schema 完整性程序校验由装白名单入口负责，本类型不代校验。
    """

    name: str
    description: str
    json_schema: Mapping[str, Any]
    schema_version: str
    output_tool: bool = False

    def ensure_valid(self) -> None:
        if not self.name or not self.description or not self.schema_version:
            raise _invalid("invalid_field", "$")
        if not isinstance(self.json_schema, Mapping):
            raise _invalid("invalid_field", "$.json_schema")


@dataclass(frozen=True, slots=True)
class ChatTurnRequest:
    """完整聊天回合请求：原 model_call 身份、完整 messages/system/instructions/tools。

    ``instructions`` 为 SystemPromptPart/instructions 保序序列；``output_tools``
    为结构 final；``frozen_params`` 为受理冻结的采样参数；``deadline`` 为单调
    时钟截止值（沿 InferenceRequest 语义）。
    """

    model_call_id: str
    messages: tuple[ChatMessage, ...]
    instructions: tuple[str, ...]
    tools: tuple[FunctionToolSpec, ...]
    output_schema: Mapping[str, Any] | None
    frozen_params: Mapping[str, Any]
    max_output_tokens: int | None
    deadline: float
    output_tools: tuple[FunctionToolSpec, ...] = ()

    def ensure_valid(self) -> None:
        if not self.model_call_id:
            raise _invalid("invalid_field", "$.model_call_id")
        if not self.messages:
            raise _invalid("invalid_field", "$.messages")
        for index, message in enumerate(self.messages):
            try:
                validate_chat_message(message)
            except ModelTurnProtocolError as exc:
                raise ModelTurnProtocolError(exc.code, f"$.messages[{index}]{exc.field_path[1:]}") from exc
        for spec in (*self.tools, *self.output_tools):
            spec.ensure_valid()


@dataclass(frozen=True, slots=True)
class ChatTurnResponse:
    """完整聊天回合响应：text/tool_calls/structured_output、finish/refusal、实际模型。

    ``usage_observed`` 标记是否真实观测到用量；``usage`` 缺失保 None——
    不能把 SDK 默认 usage=0 倒灌账本（§10.1）。``transport_state`` 与结束
    状态分离：ok / failed / unknown（INV-07）。
    """

    text: str | None
    finish_reason: FinishReason
    actual_model: str | None
    usage_observed: bool
    usage: NormalizedLLMUsage | None
    transport_state: Literal["ok", "failed", "unknown"] = "unknown"
    tool_calls: tuple[AssistantToolCall, ...] = ()
    structured_output: Mapping[str, Any] | None = None
    refusal: str | None = None

    def ensure_valid(self) -> None:
        if self.finish_reason not in FINISH_REASONS:
            raise _invalid("invalid_finish", "$.finish_reason")
        if self.usage_observed and self.usage is None:
            raise _invalid("invalid_field", "$.usage")
        if self.finish_reason == "refused" and not self.refusal:
            raise _invalid("invalid_field", "$.refusal")
        if self.finish_reason == "tool_calls" and not self.tool_calls:
            raise _invalid("invalid_field", "$.tool_calls")
        if self.actual_model is not None and not self.actual_model:
            raise _invalid("invalid_field", "$.actual_model")
        for call in self.tool_calls:
            call.ensure_valid()


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    """真实流事件：choice/tool index、稳定 call_id、text/tool delta、finish/usage。

    ``tool_call_delta`` 仅承载累积分片（``closed_arguments_json`` 恒为 None）；
    参数闭合以 ``tool_call_closed`` 事件承载并校验——工具参数闭合前不派发
    （§10.1）。finish 事件的 ``usage`` 缺失保 None。
    """

    sequence: int
    kind: Literal["text", "tool_call_delta", "tool_call_closed", "finish"]
    call_index: int | None = None
    call_id: str | None = None
    name: str | None = None
    text_delta: str | None = None
    arguments_fragment: str | None = None
    closed_arguments_json: str | None = None
    finish_reason: FinishReason | None = None
    usage: NormalizedLLMUsage | None = None

    def ensure_valid(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise _invalid("invalid_field", "$.sequence")
        if self.kind == "text":
            if not isinstance(self.text_delta, str):
                raise _invalid("invalid_field", "$.text_delta")
            return
        if self.kind == "tool_call_delta":
            if self.call_index is None or not self.call_id or not self.name:
                raise _invalid("invalid_field", "$.call_id")
            if not isinstance(self.arguments_fragment, str) or self.closed_arguments_json is not None:
                raise _invalid("invalid_field", "$.arguments_fragment")
            return
        if self.kind == "tool_call_closed":
            if self.call_index is None or not self.call_id or not self.name:
                raise _invalid("invalid_field", "$.call_id")
            if self.closed_arguments_json is None:
                raise _invalid("invalid_field", "$.closed_arguments_json")
            validate_closed_tool_arguments(self.closed_arguments_json)
            return
        if self.kind == "finish":
            if self.finish_reason is None or self.finish_reason not in FINISH_REASONS:
                raise _invalid("invalid_field", "$.finish_reason")
            return
        raise _invalid("invalid_field", "$.kind")


# ---------------------------------------------------------------------------
# 聊天回合适配器协议（HANDOFF §6.1）
# ---------------------------------------------------------------------------


#: 流事件回调（HANDOFF §6.1/§6.4）：可等待；有界通道背压经 await 语义透传。
#: 允许返回 None 或 Awaitable，消费方以 isawaitable 判定后等待。
ChatStreamEventHandler = Callable[[ChatStreamEvent], object]


class ChatInferenceAdapter(Protocol):
    """完整聊天回合适配器协议（HANDOFF §6.1）：云/本地聊天实现；FinBERT 等非聊天
    适配器不接该协议。

    与 :class:`InferenceAdapter`（单 prompt 推理）并列：组装方（app 组合根 /
    测试替身）提供带 ``invoke_chat`` 方法的对象即可，结构化协议无需显式继承。
    ``execute_chat_turn`` 消费本协议形状（B-02-3 接线）。
    """

    async def invoke_chat(
        self,
        request: ChatTurnRequest,
        resolved_connection: ResolvedConnection,
        deadline: float,
        attempt_context: AttemptContext,
        *,
        stream: bool = True,
        response_format: Mapping[str, Any] | None = None,
        on_event: ChatStreamEventHandler | None = None,
    ) -> ChatTurnResponse: ...


# ---------------------------------------------------------------------------
# 完整聊天回合应用终态（HANDOFF §6.1 / DD §10.1，B-02-3）
# ---------------------------------------------------------------------------


#: 回合传输事实状态（INV-07，不把未知折叠为确定失败）：``ok`` = 协议 finish 与
#: 流关闭确认且响应完整；``failed`` = 确认失败（零出站门控拒绝、协议断流等
#: 确认错误）；``unknown`` = 外发已开始但效果无法证明（取消传播口径，F19）。
ChatTurnTransportState = Literal["ok", "failed", "unknown"]


#: 回合结算事实状态：``settled`` = 终态落账确认持久化；``deferred`` = 终态已
#: 提交但持久化未确认（F20 重放队列承接，落账稍后推进）；``unknown`` = 收费
#: 无法证明（外发后取消，预留 held_unconfirmed，F19）；``None`` = 无在途账目
#: 事实（未建 attempt 的零出站失败，无结算维度）。
ChatTurnSettlementState = Literal["settled", "deferred", "unknown"]


@dataclass(frozen=True, slots=True)
class ChatTurnOutcome:
    """完整聊天回合的应用终态（``execute_chat_turn`` 唯一返回契约）。

    - **transport 与 settlement 两维分离**（DD §10.1「transport、settlement、
      unknown 分开」）：传输事实与结算事实互不折叠，各自可表达 unknown
      （INV-07——不把未知伪装成确定终态，R21）。
    - **已公开分片后失败**（§6.4-5）：``partial_text`` 非 None 表示调用方已收到
      的真实分片原样保留、回合尾部内容未知以 ``response=None`` 承载——不换模型
      拼接、不重试换 attempt（``attempt_id`` 绑定原 attempt）。
    - **拒绝回合**（finish_reason=refused）：``status="failed"`` +
      ``transport_state="ok"`` + 完整 ``response`` 承载——拒绝是完成的模型回合
      （传输事实成立），拒绝事实不伪装、不换候选规避（DD §10.1），调用方经
      ``response.refusal`` 读取原文。
    - **身份关联**（HANDOFF：model_call_id → invocation_id → 实际 attempt 稳定
      关联）：``attempt_id=None`` 表示零出站失败（未建任何 attempt）。
    """

    model_call_id: str
    invocation_id: str
    status: Literal["succeeded", "failed"]
    transport_state: ChatTurnTransportState
    settlement_state: ChatTurnSettlementState | None
    attempt_id: str | None
    response: ChatTurnResponse | None
    partial_text: str | None
    error_code: str | None

    def ensure_valid(self) -> None:
        if not self.model_call_id or not self.invocation_id:
            raise _invalid("invalid_field", "$")
        if self.status == "succeeded":
            if self.response is None or self.transport_state != "ok" or self.error_code is not None:
                raise _invalid("invalid_field", "$")
            if self.partial_text is not None:
                raise _invalid("invalid_field", "$.partial_text")
            return
        if self.response is not None and self.partial_text is not None:
            # 拒绝回合（完整 response）与分片后失败（partial_text）互斥——两者
            # 不得同时伪装「既完整又部分」的矛盾终态
            raise _invalid("invalid_field", "$.response")
