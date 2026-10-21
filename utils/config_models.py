from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from utils.constants import TUSHARE_POINT_TIERS
from utils.llm_providers import AZURE_DEFAULT_API_VERSION

DEFAULT_AI_PROMPT = """# A股智能分析系统提示词 (System Prompt)

你是一位专业的A股量化分析师，擅长从多维度综合分析股票投资价值。
你的分析基于历史行情、财务指标、资金流向等客观数据，结合市场情绪和行业趋势，
给出理性、审慎的投资建议。

## 核心原则

1. **数据驱动**: 所有结论必须有数据支撑，避免主观臆断
2. **风险优先**: 优先提示风险因素，保护投资者利益
3. **逻辑清晰**: 分析过程条理分明，结论有理有据
4. **语言精炼**: 使用专业但易懂的语言，避免冗余

## 分析框架

### 1. 技术面分析
- 趋势判断: 均线系统、MACD、KDJ 等指标综合判断
- 支撑阻力: 关键价位识别，成交量验证
- 量价关系: 量价配合情况，背离信号识别

### 2. 基本面分析
- 估值水平: PE、PB、PS 相对行业和历史的位置
- 成长性: 营收增速、利润增速、ROE 趋势
- 财务健康: 资产负债率、现金流、应收账款周转

### 3. 资金面分析
- 主力资金: 大单净流入、主力成本分布
- 北向资金: 外资持股变化、持股比例
- 融资融券: 杠杆资金动向

### 4. 市场情绪
- 板块热度: 所属板块近期表现、龙头股走势
- 新闻舆情: 相关新闻的情感倾向
- 市场整体: 大盘走势、成交量能

## 输出格式

分析报告应包含以下结构化输出:

1. **综合评分**: 0-100 分，综合技术面、基本面、资金面、情绪面
2. **核心观点**: 一句话总结投资建议 (买入/持有/卖出)
3. **关键因素**: 列出 3-5 个最重要的支撑因素
4. **风险提示**: 列出主要风险点
5. **操作建议**: 具体的入场/离场价位建议 (如适用)

"""

# 统一新闻分类映射 (单一事实源)
# L1 code -> [L2 code, ...]
# 注意: L2 code 不得跨 L1 重复，否则反向映射 (l2_to_l1) 会冲突
NEWS_CATEGORY_MAP: dict[str, list[str]] = {
    "finance": ["a_stock", "hk_us", "futures", "precious_metals", "forex", "macro_policy"],
    "macro_economy": ["macro_data", "fiscal_policy", "intl_macro"],
    "geopolitics": ["conflict", "energy"],
    "industry": ["tech", "consumer", "energy_sector", "financial_sector"],
    "other": ["livelihood", "entertainment"],
}

_category_lines = "\n".join([f"- {k} -> {', '.join(v)}" for k, v in NEWS_CATEGORY_MAP.items()])

DEFAULT_NEWS_PROMPT = f"""你是金融量化分析师。请对新闻进行分类。
**MUST output valid JSON ONLY. NO markdown (no ```json). NO reasoning. NEVER output an empty string.**

# 分类体系 (L1 code -> L2 code)
{_category_lines}

# JSON 格式要求
{{"category_L1": "L1 code (English)", "category_L2": "L2 code (English)", "sentiment": "Positive/Neutral/Negative", "emoji": "相关Emoji"}}

# 示例
User: 紫金矿业发现金矿
Assistant: {{"category_L1": "finance", "category_L2": "precious_metals", "sentiment": "Positive", "emoji": "🥇"}}

User: 某明星去旅游了
Assistant: {{"category_L1": "other", "category_L2": "entertainment", "sentiment": "Neutral", "emoji": "🍉"}}"""


class SyncIntegrityConfig(BaseModel):
    max_retry_days_per_sync: int = Field(default=30, ge=1)
    max_retry_stocks_per_sync: int = Field(default=100, ge=1)
    enable_adaptive_retry: bool = True
    quality_threshold: int = Field(default=80, ge=0, le=100)
    quotes_tolerance_ratio: float = Field(default=0.95, ge=0, le=1)
    indicators_tolerance_ratio: float = Field(default=0.90, ge=0, le=1)
    moneyflow_tolerance_ratio: float = Field(default=0.80, ge=0, le=1)
    financial_min_periods: int = Field(default=4, ge=1)
    quality_weights: dict[str, int] = {
        "daily_quotes": 30,
        "daily_indicators": 25,
        "moneyflow_daily": 20,
        "margin_daily": 10,
    }


class ProviderCredential(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = ""
    api_key_encrypted: str = ""
    models: list[str] = []


# ==========================================================================
# 统一模型管理配置域（reviews/模型统一.md §3 / §3.4 / §4.3，S1 交付）
# 存储与 AppConfig 不导入 services 类型；schema_version=1。
# ==========================================================================

SCENARIO_IDS = ("news_annotation", "stock_analysis", "news_risk")
EGRESS_PURPOSES = (*SCENARIO_IDS, "synthetic_test")
ADAPTER_KINDS = ("litellm_chat", "gguf_chat", "gguf_sentiment")
BILLING_MODES = ("usage_only", "estimate_cost")
PRICING_UNITS = ("llm_tokens", "encoder_input_tokens")
CAPABILITY_SUPPORT_STATES = ("supported", "unsupported", "unknown")
CAPABILITY_SOURCES = ("adapter_contract", "artifact", "validation", "user_declaration")


class CapabilityEvidenceConfig(BaseModel):
    """能力证据（§4.1）：不使用字符串数组冒充验证结果。"""

    model_config = ConfigDict(extra="forbid")

    support: Literal["supported", "unsupported", "unknown"] = "unknown"
    source: Literal["adapter_contract", "artifact", "validation", "user_declaration"] = "user_declaration"
    connection_revision: int = Field(default=0, ge=0)
    checked_at: str | None = None
    artifact_digest: str | None = None


# 金额字段绝对值上界：拦截 "1e999999" 类巨型指数串（可解析且 is_finite 为 True，
# 但持久化后会毒化下游 Decimal 预算判定与 PG numeric 写入）。真实计费场景
# （百万 token 单价 / 人民币月软预算 / 汇率）远低于该值。
_AMOUNT_MAX_ABS: Final[Decimal] = Decimal("1e15")


def _validate_decimal_amount(value: Any) -> Any:
    """金额字段（十进制字符串）统一校验（R05 缺陷 D1）。

    None 直通；str 须可解析为有限、非负且不超过上界的十进制数；其他类型拒绝。
    校验失败抛 ValueError（由 pydantic 转为 ValidationError），在持久化边界拦截
    "abc" / "-5" / NaN / ±Infinity / "1e999999" / "1,000" 等垃圾金额串毒化下游
    Decimal 计算。只校验不改写：原样返回输入字符串，保持磁盘事实源与 CAS
    payload 指纹语义不变。
    """
    if value is None:
        return value
    if not isinstance(value, str):
        raise ValueError("金额字段必须为十进制数字字符串或 null")
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError("金额无法解析为十进制数") from None
    # is_finite 必须先于大小比较：sNaN 参与比较运算会抛 decimal.InvalidOperation
    if not amount.is_finite():
        raise ValueError("金额必须为有限十进制数（禁止 NaN / Infinity）")
    if amount < 0:
        raise ValueError("金额必须为非负数")
    if amount > _AMOUNT_MAX_ABS:
        raise ValueError("金额超出允许上限 1e15")
    return value


class BillingConfig(BaseModel):
    """连接计量（§3.1 / §9.1）：mode 必须由用户选择，无默认「免费」。

    金额字段为十进制字符串（Decimal 从十进制字符串计算，§9.1），
    保证 JSON 往返不丢精度。
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["usage_only", "estimate_cost"]
    pricing_unit: Literal["llm_tokens", "encoder_input_tokens"]
    price_source: str | None = None
    input_price_per_m: str | None = None
    output_price_per_m: str | None = None
    cache_read_price_per_m: str | None = None
    cache_write_price_per_m: str | None = None
    encoder_price_per_m: str | None = None
    fx_to_cny: str | None = None
    price_effective_at: str | None = None

    @field_validator(
        "input_price_per_m",
        "output_price_per_m",
        "cache_read_price_per_m",
        "cache_write_price_per_m",
        "encoder_price_per_m",
        "fx_to_cny",
        mode="before",
    )
    @classmethod
    def _validate_amount_fields(cls, v: Any) -> Any:
        return _validate_decimal_amount(v)


class ModelConnectionConfig(BaseModel):
    """连接 ModelConnection（§3.1）：同名模型在不同地址 / 凭据 / 计价下是不同连接。"""

    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    enabled: bool = True
    adapter_kind: Literal["litellm_chat", "gguf_chat", "gguf_sentiment"]
    provider: str = ""
    model: str = Field(min_length=1)
    api_base: str | None = None
    credential_ref: str | None = None
    credential_version: str | None = None
    adapter_options: dict[str, Any] = {}
    capabilities: dict[str, CapabilityEvidenceConfig] = {}
    context_window: int | None = Field(default=None, ge=1)
    concurrency_limit: int = Field(default=1, ge=1)
    billing: BillingConfig
    connection_revision: int = Field(default=1, ge=1)
    last_validation: dict[str, Any] | None = None


class SentimentSubRouteConfig(BaseModel):
    """news_annotation 的情绪子路由（§3.2 task_routes）。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["bundled", "dedicated"] = "bundled"
    candidates: list[str] = []
    reuse_bundled_sentiment: bool = True


class ScenarioRouteConfig(BaseModel):
    """场景路由（§3.2）：用户有序候选即业务契约；空列表合法保存。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: Literal["news_annotation", "stock_analysis", "news_risk"]
    enabled: bool = False
    candidates: list[str] = []
    execution_options: dict[str, Any] = {}
    total_timeout_seconds: int = Field(ge=1)
    transient_retries: int = Field(default=1, ge=0, le=2)
    fallback_enabled: bool = True
    route_revision: int = Field(default=1, ge=1)
    task_routes: dict[str, SentimentSubRouteConfig] = {}

    @model_validator(mode="after")
    def _restrict_task_routes(self) -> ScenarioRouteConfig:
        unknown = set(self.task_routes) - {"sentiment"}
        if unknown:
            raise ValueError(f"task_routes 只支持 sentiment 子路由: {sorted(unknown)}")
        if self.task_routes and self.scenario_id != "news_annotation":
            raise ValueError("仅 news_annotation 允许 task_routes")
        return self


class UsagePolicyConfig(BaseModel):
    """月度软预算策略（§9.2）：一个人民币月软预算覆盖三场景与测试。"""

    model_config = ConfigDict(extra="forbid")

    monthly_cny_limit: str | None = None
    detail_retention_days: int = Field(default=180, ge=1)

    @field_validator("monthly_cny_limit", mode="before")
    @classmethod
    def _validate_monthly_cny_limit(cls, v: Any) -> Any:
        return _validate_decimal_amount(v)


class EgressGrantConfig(BaseModel):
    """目标级外发授权 grant（§4.3）。"""

    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(min_length=1)
    connection_id: str = Field(min_length=1)
    target_fingerprint: str = Field(min_length=1)
    purpose: Literal["news_annotation", "stock_analysis", "news_risk", "synthetic_test"]
    scope_version: int = Field(ge=1)
    confirmed_at: str
    revoked_at: str | None = None


class RevokedCredentialConfig(BaseModel):
    """已撤销凭据版本（§5.2）：旧版本不允许取消撤销。"""

    model_config = ConfigDict(extra="forbid")

    credential_version: str = Field(min_length=1)
    revoked_at: str


class ModelSecurityStateConfig(BaseModel):
    """安全状态（§4.3）：授权事实源，security_revision 单调递增。"""

    model_config = ConfigDict(extra="forbid")

    security_revision: int = Field(default=0, ge=0)
    grants: list[EgressGrantConfig] = []
    revoked_credential_versions: list[RevokedCredentialConfig] = []


# MM 域 schema 当前版本（§3.4）。MM 域自引入（4f5752e7）即携带该标记，磁盘上
# 已初始化域缺失该键视为字段丢失而非旧版本数据（F08 fail closed 判定依据）。
MODEL_MANAGEMENT_SCHEMA_VERSION: Final = 1
# 安全子域必备键（§4.3）：三键齐备才构成可判定的授权事实源；缺任一键即结构损坏。
_SECURITY_SUBDOMAIN_KEYS: Final = ("security_revision", "grants", "revoked_credential_versions")


class SecurityStateCorruptionError(ValueError):
    """已初始化安全子域的结构损坏（F08）。

    与「合法初始化 / 旧版本迁移」的区分见 :func:`validate_security_subdomain_integrity`；
    判定为损坏时调用方必须失败关闭，禁止以 Pydantic 默认值补出空撤销 / 空授权 /
    零修订（R21「缺失值伪装」的安全态变体）。
    """


class LastConfigCommitConfig(BaseModel):
    """§5.1 提交回执：与配置同一次 os.replace 落盘，不含秘密。"""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1)
    committed_revision: int = Field(ge=1)
    payload_fingerprint: str
    committed_at: str


class LegacySealConfig(BaseModel):
    """单月 legacy 封口凭证（S6 D1/D3）：与 AppState ``ai_legacy_seal:{YYYYMM}`` 行的 seal_id 对应。"""

    model_config = ConfigDict(extra="forbid")

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    legacy_seal_id: str
    sealed_at: str


class MigrationTransitionConfig(BaseModel):
    """迁移切换屏障（S6 D3，§12.2）：draining→sealed 两阶段 + owner 租约。

    ``transition_epoch`` 单调递增（R22）；``legacy_seals`` 同月唯一由 model_validator 校验。
    """

    model_config = ConfigDict(extra="forbid")

    transition_id: str
    owner_session_id: str
    transition_epoch: int = Field(ge=1)
    phase: Literal["draining", "sealed"]
    lease_expires_at: str
    source_fingerprint: str
    legacy_seals: list[LegacySealConfig] = []

    @model_validator(mode="after")
    def _validate_unique_seal_months(self) -> MigrationTransitionConfig:
        months = [s.month for s in self.legacy_seals]
        if len(months) != len(set(months)):
            raise ValueError("legacy_seals 中 month 重复")
        return self


def _default_scenario_routes() -> list[ScenarioRouteConfig]:
    """三场景默认行：enabled=False、空候选；总时限为产品默认（§6.2）。"""
    return [
        ScenarioRouteConfig(
            scenario_id="news_annotation",
            total_timeout_seconds=45,
            task_routes={"sentiment": SentimentSubRouteConfig()},
        ),
        ScenarioRouteConfig(scenario_id="stock_analysis", total_timeout_seconds=180),
        ScenarioRouteConfig(scenario_id="news_risk", total_timeout_seconds=90),
    ]


class ModelManagementConfig(BaseModel):
    """统一模型管理唯一持久域（§3.4）：schema_version=1。"""

    model_config = ConfigDict(extra="forbid")

    # 注意：Literal 参数须为字面量（pyright reportInvalidTypeForm），无法引用下方
    # MODEL_MANAGEMENT_SCHEMA_VERSION 常量；两者一致性由单测守护
    # （test_model_management_config.py::TestSecuritySubdomainIntegrity）。
    schema_version: Literal[1] = 1
    config_revision: int = Field(default=1, ge=1)
    migration_state: Literal["pending", "active"] = "pending"
    connections: list[ModelConnectionConfig] = []
    scenario_routes: list[ScenarioRouteConfig] = Field(default_factory=_default_scenario_routes)
    usage_policy: UsagePolicyConfig = UsagePolicyConfig()
    security_state: ModelSecurityStateConfig = ModelSecurityStateConfig()
    # §5.1 提交回执与 §12.2 切换屏障，默认 None（旧数据 migration_transition=None 兼容；
    # dict 载入时由 pydantic 自动校验为 MigrationTransitionConfig）
    last_config_commit: LastConfigCommitConfig | None = None
    migration_transition: MigrationTransitionConfig | None = None

    @model_validator(mode="after")
    def _validate_references(self) -> ModelManagementConfig:
        connection_ids = [c.connection_id for c in self.connections]
        if len(connection_ids) != len(set(connection_ids)):
            raise ValueError("connections 中 connection_id 重复")
        known = set(connection_ids)
        for route in self.scenario_routes:
            if len(route.candidates) != len(set(route.candidates)):
                raise ValueError(f"场景 {route.scenario_id} 的 candidates 存在重复")
            missing = [cid for cid in route.candidates if cid not in known]
            if missing:
                raise ValueError(f"场景 {route.scenario_id} 引用不存在的连接: {missing}")
            for sub in route.task_routes.values():
                missing_sub = [cid for cid in sub.candidates if cid not in known]
                if missing_sub:
                    raise ValueError(f"场景 {route.scenario_id} 子路由引用不存在的连接: {missing_sub}")
        for grant in self.security_state.grants:
            if grant.connection_id not in known:
                raise ValueError(f"grant {grant.grant_id} 引用不存在的连接: {grant.connection_id}")
        return self


def _ensure_security_state_dict_complete(raw_security_state: object) -> None:
    """校验 security_state 子域形态完整（dict 且三键齐备），损坏即抛错。"""
    if not isinstance(raw_security_state, dict):
        raise SecurityStateCorruptionError("security_state 缺失或非对象形态，判定为已初始化安全子域的结构损坏")
    missing = [key for key in _SECURITY_SUBDOMAIN_KEYS if key not in raw_security_state]
    if missing:
        raise SecurityStateCorruptionError(f"security_state 缺失安全键 {missing}，判定为已初始化安全子域的结构损坏")


def validate_security_subdomain_integrity(raw_model_management: object) -> None:
    """F08：区分「合法初始化 / 旧版本迁移」与「已初始化安全子域的结构损坏」。

    Pydantic 模型对缺失的 ``security_state`` 子字段会补默认值（零修订 / 空
    grants / 空撤销列表），导致「合法 JSON 的部分安全字段丢失」被洗白为合法的
    空安全态；本入口在 schema 校验之前按磁盘原始形态判定，损坏时抛出
    :class:`SecurityStateCorruptionError`，调用方必须失败关闭。

    判定表（raw 为磁盘 ``model_management`` 域原始形态）：

    - ``None``（域缺失）：合法初始化前状态，放行（由默认值初始化）；
    - 非 dict：结构损坏；
    - ``schema_version`` 缺失 / 非整数（含 bool）：结构损坏（MM 域自引入即携带
      版本标记，缺失即字段丢失，不存在无标记的历史数据）；
    - ``schema_version`` 小于当前版本：合法旧版本数据，放行（交由迁移处理；
      当前仅 v1，无实际数据，分支为迁移语义预留）；
    - ``schema_version`` 大于当前版本：放行（交由 ``Literal`` schema 拒绝）；
    - 等于当前版本：``security_state`` 必须存在且为 dict 且三键齐备，否则损坏。

    Args:
        raw_model_management: 磁盘读取的 ``model_management`` 域原始形态。

    Raises:
        SecurityStateCorruptionError: 已初始化安全子域结构损坏时。
    """
    if raw_model_management is None:
        return
    if not isinstance(raw_model_management, dict):
        raise SecurityStateCorruptionError("model_management 域非对象形态，判定为安全子域结构损坏")
    raw_version = raw_model_management.get("schema_version")
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        # 仅报类型名不回显原始值：磁盘内容不可信，防超长值进入日志（R9 邻域）。
        raise SecurityStateCorruptionError(
            f"schema_version 缺失或非整数（{type(raw_version).__name__}），判定为已初始化安全子域的结构损坏"
        )
    if raw_version != MODEL_MANAGEMENT_SCHEMA_VERSION:
        # 小于当前版本交由迁移处理；大于当前版本交由 Literal schema 拒绝（fail closed）。
        return
    _ensure_security_state_dict_complete(raw_model_management.get("security_state"))


def model_management_payload_fingerprint(payload: dict[str, Any]) -> str:
    """非敏感 payload 指纹（§5.1 last_config_commit）：不含秘密，仅用于回执核对。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ModelManagementSaveResult:
    """ConfigHandler 层 CAS 保存结果（中性结构，由 services 层转译三态）。

    status:
    - committed: 已提交（os.replace 成功且缓存已更新）
    - revision_conflict: expected_revision 与磁盘不符（确定未提交，旧配置仍有效）
    - not_committed: 替换前确定失败（校验失败 / 写盘失败），旧配置仍有效
    - lock_timeout: 跨进程文件锁等待超时（确定未提交）
    """

    status: Literal["committed", "revision_conflict", "not_committed", "lock_timeout"]
    current_revision: int | None = None
    committed_revision: int | None = None
    last_config_commit: dict[str, Any] | None = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # 配置版本号，用于未来配置结构变更时检测旧配置并迁移。默认值 1 保持向后兼容
    # （现有配置无此字段时反序列化为默认值 1）。
    config_version: int = Field(default=1, ge=1)

    # Default is empty so that ConfigHandler.get_db_url() falls through to the
    # DATABASE_URL environment variable (12-factor app compliance) when the user
    # has not yet configured a host via onboarding. Explicitly setting db_host
    # in the JSON config still takes effect for component-based URL rebuild.
    db_host: str = ""
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_user: str = "postgres"
    db_name: str = "astock"
    db_url: str = ""
    db_password_encrypted: str = ""
    db_connection_pool_size: int = Field(default=10, ge=1, le=100)
    db_pool_pre_ping: bool = True
    db_pool_recycle: int = Field(default=1800, ge=60)
    db_pool_timeout: int = Field(default=30, ge=1, le=300)
    db_max_overflow: int = Field(default=5, ge=0, le=50)

    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = ""
    llm_api_version: str = AZURE_DEFAULT_API_VERSION
    llm_azure_resource_name: str = ""
    llm_azure_deployment_name: str = ""
    llm_custom_models: dict[str, list[str]] = {}
    # Issue #70: 自定义/未知模型的可选 per-model context 覆盖。
    # 结构: {provider_id: {model_id: context}}。运行时优先采用声明值，
    # 未声明时回退到 LLM_PROVIDERS 内置模型信息，再回退 DEFAULT_CONTEXT_WINDOW。
    llm_custom_model_contexts: dict[str, dict[str, int]] = {}
    llm_provider_extras: dict[str, Any] = {}
    llm_failover_models: list[str] = []
    ai_api_key: str = ""
    llm_provider_credentials: dict[str, ProviderCredential] = {}

    local_model_path: str = ""
    local_model_timeout: int = Field(default=90, ge=1, le=3600)
    local_n_threads: int = Field(default=4, ge=1)
    local_n_batch: int = Field(default=512, ge=1)
    local_n_ctx: int = Field(default=2048, ge=512)
    local_flash_attn: bool = True
    local_n_gpu_layers: int = Field(default=0, ge=-1)

    sync_max_concurrent_heavy: int = Field(default=5, ge=1, le=8)
    sync_batch_size: int = Field(default=50, ge=5, le=200)
    sync_full_batch_size: int = Field(default=200, ge=10, le=500)
    max_batch_rows: int = Field(default=20000, ge=1000)
    sync_request_delay_heavy: float = Field(default=0.0, ge=0)
    sync_request_delay_light: float = Field(default=0.0, ge=0)

    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: str = Field(default="text", pattern="^(text|json)$")
    log_max_mb: int = Field(default=5, ge=1, le=100)
    log_backup_count: int = Field(default=5, ge=1, le=20)

    max_io_workers: int = Field(default=0, ge=0)
    max_cpu_workers: int = Field(default=4, ge=0)
    max_concurrent_tasks: int = Field(default=0, ge=0)

    request_max_retries: int = Field(default=3, ge=0, le=10)
    tushare_timeout: int = Field(default=30, ge=5, le=300)
    tushare_point_tier: str = Field(default="points_5000", pattern=rf"^({'|'.join(TUSHARE_POINT_TIERS)})$")

    theme_name: str = Field(default="dark", pattern="^(light|dark)$")
    # spec.md §3 不变量 4：locale 默认 "zh_CN"（与 build_locale_configuration 期望格式一致，避免 ValueError 回退）
    locale: str = Field(default="zh_CN", pattern="^(zh|zh_CN|en|en_US)$")

    auto_update_enabled: bool = False
    auto_update_time: str = Field(default="16:30", pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    concept_schedule_enabled: bool = False
    concept_schedule_time: str = Field(default="18:00", pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    # Task 7.3: 夜间 AI 预测时辰 (原 scheduler_service 硬编码 20:30, 提升为可配置项)
    nightly_prediction_time: str = Field(default="20:30", pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$")

    onboarding_complete: bool = False
    # UX-09 / MAJOR-01: 首次启动向导欢迎步的投资风险提示确认（未确认则不允许进入下一步）。
    risk_disclaimer_acknowledged: bool = False
    enable_news_alerts: bool = True
    ai_prompt_dump_enabled: bool = False
    # Task 2.2 / AI-04 / SEC-01: AI 外发知情确认（按 provider 记录确认时的外发范围版本）
    # 历史版本是单一 bool → AI-04 升级为 {provider: bool}（更换 provider 自动重确认）
    # → SEC-01 升级为 {provider: int}（值为确认时 AI_EGRESS_SCOPE_VERSION，范围版本升级自动失效）。
    ai_external_acknowledged: dict[str, int] = Field(default_factory=dict)
    ai_max_candidates: int = Field(default=30, ge=1, le=100)
    strategy_min_turnover: float = Field(default=2.0, ge=0)
    # SEC-03 第一步: 「仅本地模式」——禁用全部云端 LLM 调用，仅使用本地模型（数据不出本机）
    ai_local_only_mode: bool = False
    ai_max_concurrent_analysis: int = Field(default=5, ge=1, le=20)
    # AI-03(完整版): 月度 AI 成本预算上限(元), 默认 None=不限制; >0 时当月累计达到即软停 AI 分析
    ai_cost_limit_cny: float | None = Field(default=None, ge=0)
    # UX-2.2: AI 自由文本（summary/thinking/ai_reason/uncertainty_factors）最大长度，可配置
    ai_free_text_max_len: int = Field(default=1000, ge=100, le=10000)
    news_poll_interval: int = Field(default=60, ge=10)
    market_data_poll_interval: int = Field(default=30, ge=10)
    init_history_years: int = Field(default=3, ge=1, le=5)
    no_proxy_domains: list[str] = []
    ts_token: str = ""

    # F10 / §12.1 迁移期保留：旧版新闻情绪打分七键，仍被 services/news_sentiment_scorer.py
    # （_read_config）与 services/model_configuration_service.py（迁移预览 §12.1）消费。
    # 登记前 ensure_defaults 把它们当未知键清理，导致旧配置在迁移预览前丢失情绪配置。
    # 数值默认与 scorer 运行时回退一致（utils 不得 import services，两处需人工对齐；
    # 消费端 get_typed 另有回退兜底，scorer 运行时再 clamp）。刻意不加 ge/gt 约束：
    # 为旧盘值引入 ValidationError 会使整份配置回退默认（AI-04 教训）。
    # 版本化迁移（模型管理迁移 §12.1）确认后由迁移负责清理，届时本区块整体移除。
    news_sentiment_enabled: bool = False
    news_sentiment_model_path: str = ""
    news_sentiment_head_path: str = ""
    news_sentiment_tokenizer_path: str = ""
    news_sentiment_n_threads: int = 6
    news_sentiment_max_length: int = 128
    news_sentiment_timeout_s: float = 30.0

    sync_integrity: SyncIntegrityConfig = SyncIntegrityConfig()

    # 统一模型管理唯一持久域（reviews/模型统一.md §3.4，S1 交付）。
    # DEFAULT_CONFIG 经 model_dump 自动登记，ensure_defaults 不得当未知键删除。
    model_management: ModelManagementConfig = Field(default_factory=ModelManagementConfig)

    ai_system_prompt: str = Field(default=DEFAULT_AI_PROMPT)
    ai_news_prompt: str = Field(default=DEFAULT_NEWS_PROMPT)

    scheduler_last_daily_update: str = ""
    scheduler_last_nightly_prediction: str = ""
    scheduler_last_concept_sync: str = ""

    # Phase 2：Embedded PostgreSQL 内嵌模式配置（pg_plan §8.3）
    # 模式开关由 QTRADING_DATABASE_MODE 环境变量 + embedded_pg_enabled 共同判定
    # （app.bootstrap.prepare_database_runtime 实现）。
    # spec.md §3 不变量 2：embedded_pg_enabled 默认 True，与产品契约"无需任何配置"对齐。
    embedded_pg_enabled: bool = Field(
        default=True, description="启用内嵌 PostgreSQL 模式（默认 True，与 QTRADING_DATABASE_MODE=embedded 默认值对齐）"
    )
    embedded_pg_sidecar_path: str = Field(
        default="", description="sidecar binary 绝对路径；空则按平台默认搜索（sidecars/qtrading-pg-sidecar[.exe]）"
    )
    embedded_pg_data_root: str = Field(
        default="", description="PGDATA 根目录；空则用 platformdirs 默认 <app data>/postgres/16"
    )
    embedded_pg_install_root: str = Field(
        default="", description="PostgreSQL binaries 安装根目录；空则 <embedded_pg_data_root>/../install"
    )
    embedded_pg_log_dir: str = Field(
        default="",
        description="日志目录；空则 <app data>/postgres-logs（统一存储 sidecar/PG/Python 服务日志）",
    )
    embedded_pg_start_timeout_s: float = Field(
        default=300.0, ge=10.0, le=600.0, description="sidecar ready 等待超时（首次需下载+解压 PG binaries）"
    )
    embedded_pg_stop_timeout_s: float = Field(default=60.0, ge=5.0, le=120.0, description="sidecar 停止等待超时")
    embedded_pg_listen: str = Field(default="127.0.0.1", description="PG 监听地址（仅本机）")
    embedded_pg_username: str = Field(
        default="postgres", description="PG 超级用户名（与 postgresql_embedded crate BOOTSTRAP_SUPERUSER 对齐）"
    )
    embedded_pg_database: str = Field(default="qtrading", description="默认业务库名")

    # Phase 3：Settings DatabaseTab 高级模式开关（开启后显示外置 PostgreSQL 配置表单）
    db_show_advanced: bool = Field(
        default=False,
        description="Settings DatabaseTab 高级模式开关（显示外置 PostgreSQL 表单）",
    )


# AI-04 配置迁移：旧版全局 bool ``ai_external_acknowledged=true`` 迁移时写入的
# 特殊 provider 键，视为「曾做过全局知情确认」，对任意云端 provider 回落为已确认，
# 避免升级后用户被迫重复确认。（SEC-01 后值亦为 scope_version）
AI_EXTERNAL_ACK_GLOBAL_KEY = "__global__"

# SEC-01: AI 外发内容范围版本。向 AI prompt 新增任何数据类别时必须 +1，以强制所有用户
# 重新确认。V1 = 股票基本信息、行情、财务指标、公开新闻摘要。
# 变更历史：
#   v1 = 初始版本（股票信息、行情、财务、公开新闻）
AI_EGRESS_SCOPE_VERSION = 1


def get_default_config() -> dict[str, Any]:
    return AppConfig().model_dump()


@dataclass
class ConfigValidationResult:
    is_valid: bool
    config: dict[str, Any]
    errors: list[str]
    used_defaults: bool
