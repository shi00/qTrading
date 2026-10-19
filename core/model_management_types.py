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
from typing import Any, Literal

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
    """单次尝试许可：取得即尝试开始的线性化点，不能用于后续排队 / 重试 / 备用。"""

    attempt_id: str
    connection_id: str
    security_revision: int
    granted_at: str


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
