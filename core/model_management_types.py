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

from collections.abc import Mapping
from dataclasses import dataclass, field
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

# 会真实外发数据的适配器类型（§4.3：HTTP 目标即使在局域网也需匹配已确认目标）；
# gguf_* 为设备内本地模型，不产生网络出站。
EGRESS_ADAPTER_KINDS: Final[tuple[str, ...]] = ("litellm_chat",)


def adapter_kind_requires_egress(adapter_kind: str) -> bool:
    """该适配器类型的执行是否产生网络外发（F01：许可按真实执行位置要求 egress grant）。"""

    return adapter_kind in EGRESS_ADAPTER_KINDS


def normalize_egress_target(api_base: object) -> str | None:
    """规范化外发目标（§4.3 canonical）：scheme / host 小写、IDNA host、显式有效端口、完整 base path。

    拒绝 userinfo / query / fragment（明确非法目标）；``api_base`` 缺失、无 http(s) scheme、
    host 非法或端口越界一律返回 ``None``——不臆造目标（R21）。

    注意：ui/viewmodels/model_connections_view_model.py 暂持同实现副本（本函数为
    canonical 正本；该文件由并行会话占用暂禁改，后续统一为 import 本函数）。
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
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    host = parts.hostname
    if not host:
        return None
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
