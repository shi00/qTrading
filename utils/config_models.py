from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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

DEFAULT_NEWS_PROMPT = """你是金融量化分析师。请对新闻进行分类。
**MUST output valid JSON ONLY. NO markdown (no ```json). NO reasoning. NEVER output an empty string.**

# 分类体系 (L1 code -> L2 code)
- finance -> a_stock, hk_us, futures, precious_metals, forex, macro_policy
- macro_economy -> macro_data, fiscal_policy, intl_macro
- geopolitics -> conflict, energy
- industry -> tech, consumer, energy_sector, financial_sector
- other -> livelihood, entertainment

# JSON 格式要求
{"category_L1": "L1 code (English)", "category_L2": "L2 code (English)", "sentiment": "Positive/Neutral/Negative", "emoji": "相关Emoji"}

# 示例
User: 紫金矿业发现金矿
Assistant: {"category_L1": "finance", "category_L2": "precious_metals", "sentiment": "Positive", "emoji": "🥇"}

User: 某明星去旅游了
Assistant: {"category_L1": "other", "category_L2": "entertainment", "sentiment": "Neutral", "emoji": "🍉"}"""


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


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # WARNING: Providing a non-empty default for db_host ("127.0.0.1") causes
    # ConfigHandler.get_db_url() to ALWAYS rebuild the database URL from these components,
    # effectively bypassing the DATABASE_URL environment variable fallback unless
    # explicitly overridden with an empty string in the JSON config.
    db_host: str = "127.0.0.1"
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
    local_n_gpu_layers: int = Field(default=0, ge=0)

    sync_max_concurrent_heavy: int = Field(default=3, ge=1, le=10)
    sync_concurrency_light: int = Field(default=20, ge=1)
    max_batch_rows: int = Field(default=20000, ge=1000)
    sync_request_delay_heavy: float = Field(default=0.0, ge=0)
    sync_request_delay_light: float = Field(default=0.0, ge=0)

    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: str = Field(default="text", pattern="^(text|json)$")
    log_max_mb: int = Field(default=5, ge=1, le=100)
    log_backup_count: int = Field(default=5, ge=1, le=20)

    max_io_workers: int = Field(default=16, ge=0)
    max_cpu_workers: int = Field(default=4, ge=0)
    max_concurrent_tasks: int = Field(default=0, ge=0)

    request_max_retries: int = Field(default=3, ge=0, le=10)
    tushare_timeout: int = Field(default=30, ge=5, le=300)
    tushare_api_rate_limit: int = Field(default=200, ge=1, le=10000)
    tushare_point_tier: str = Field(default="custom", pattern="^(free|standard|pro|flagship|custom)$")

    theme_name: str = Field(default="dark", pattern="^(light|dark)$")
    locale: str = Field(default="zh", pattern="^(zh|zh_CN|en|en_US)$")

    auto_update_enabled: bool = False
    auto_update_time: str = Field(default="16:30", pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$")
    doubao_schedule_enabled: bool = False
    doubao_schedule_time: str = Field(default="10:00", pattern="^([01]?[0-9]|2[0-3]):[0-5][0-9]$")

    onboarding_complete: bool = False
    enable_news_alerts: bool = True
    ai_prompt_dump_enabled: bool = False
    ai_max_candidates: int = Field(default=30, ge=1, le=100)
    strategy_min_turnover: float = Field(default=2.0, ge=0)
    ai_max_concurrent_analysis: int = Field(default=5, ge=1, le=20)
    news_poll_interval: int = Field(default=60, ge=10)
    market_data_poll_interval: int = Field(default=30, ge=10)
    init_history_years: int = Field(default=3, ge=1, le=5)
    no_proxy_domains: list[str] = []
    ts_token: str = ""

    sync_integrity: SyncIntegrityConfig = SyncIntegrityConfig()

    ai_system_prompt: str = Field(default=DEFAULT_AI_PROMPT)
    ai_news_prompt: str = Field(default=DEFAULT_NEWS_PROMPT)

    scheduler_last_daily_update: str = ""
    scheduler_last_nightly_prediction: str = ""
    scheduler_last_doubao_refresh: str = ""


@cache
def get_default_config() -> dict[str, Any]:
    return AppConfig().model_dump()


@dataclass
class ConfigValidationResult:
    is_valid: bool
    config: dict[str, Any]
    errors: list[str]
    used_defaults: bool
