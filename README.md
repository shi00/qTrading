# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![CI/CD](https://github.com/shi00/qTrading/actions/workflows/ci_cd.yml/badge.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![Coverage](https://img.shields.io/badge/coverage-85%25%2B-brightgreen)]()
[![UI](https://img.shields.io/badge/UI-Flet-00d2b4)]()
[![Data Engine](https://img.shields.io/badge/Data-Polars-orange)]()
[![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个极速、隐私优先的本地化量化选股与深度分析平台。它通过将 **高性能 Polars 向量化计算引擎** 与 **大语言模型 (LLM)** 深度结合，提供从"海量指标毫秒级初筛"到"AI 逻辑深度回顾"的全链路工业级投研协作能力。

---

## 🚀 核心特性

### 1. 🧠 漏斗式智能选股架构
采用二级联动筛选机制，在处理海量数据的同时提供极高的研报智商：

* **L1 数学策略**: 基于 **Polars 惰性求值**，在毫秒级内完成对全市场股票的技术面（超跌、动量）、基本面（PE/ROE/净利增长）及情绪面过滤。支持多维度参数实时交互调节。
* **L2 AI 深度思维**: 对 L1 选出的候选项进行"人脑化"审读。UI 流式展示思维链；自动聚合个股新闻、龙虎榜、北向资金，生成具有深度见解的分析报告。

### 2. 🔄 自进化 AI 闭环
内置自动化回顾机制，让 AI 越选越准：

* **结果回顾**: 自动跟踪 T+1/T+5 实际回报，计算相对于基准（CSI300/上证指数）的 **Alpha 收益**。
* **经验学习**: 自动标记"成功案例"与"失误陷阱"，并将历史经验动态注入后续筛选的 Prompt 中，实现策略的自进化。

### 3. 🛡️ 工业级数据质量网关
三级严苛校验，确保量化决策底座的绝对可靠：

* **Tier 1 (Bronze)**: 数据可用性检查 — 数据库表存在性、基础数据完整性
* **Tier 2 (Silver)**: 连续性与时效性检查 — 交易日历连续性、数据同步时效
* **Tier 3 (Gold)**: 跨源一致性校验 — 量价波动异常检测、财务指标分发一致性

### 4. 📊 数据同步完整性保障
多维度数据同步质量监控与自动修复机制：

* **质量评分机制**: 基于相对基准法评估每日数据同步质量，自动计算质量分数
* **断点续传**: 智能检测中断点，支持增量同步，避免重复拉取
* **退市股票处理**: 精确计算历史存活股票数，确保质量评分准确性
* **批量查询优化**: N+1 查询优化，批量预取辅助数据，大幅提升分析性能

### 5. 🔧 数据库迁移自动化
基于 Alembic 的自动迁移机制：

* **自动检测**: 应用启动时自动检测数据库版本，按需执行迁移
* **幂等性保证**: 迁移脚本支持重复执行，不会产生副作用
* **向后兼容**: 新字段自动填充默认值，兼容旧版本数据

### 6. 🔒 隐私优先设计

* **本地 AI 推理**: 支持 GGUF 格式模型，实现**核心投研逻辑不离本地**。具备智能内存自动卸载、并发锁控制及推理超时强行熔断保护。
* **全本地存储**: 所有交易流水、分析报告、配置信息均通过本地 PostgreSQL 及加密存储管理。
* **安全凭证**: Token 使用系统 Keyring 或 AES-GCM 加密存储，密钥自动备份恢复。

### 7. 🎨 现代桌面交互设计

* **响应式 Flet 架构**: 组件化桌面应用，支持主题热切换与虚拟化表格，流畅展示 5000+ 数据行。
* **弹性任务中心**: 基于 `ThreadPoolManager` 的高可靠任务调度，IO/CPU 线程池分离，任务状态持久化，支持异常断点续传。
* **国际化支持**: 内置中英文双语切换。

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| **前端框架** | [Flet](https://flet.dev/) (Flutter 驱动) |
| **计算引擎** | [Polars](https://pola.rs/) + Pandas |
| **数据库** | [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy 2.0](https://www.sqlalchemy.org/) |
| **数据迁移** | [Alembic](https://alembic.sqlalchemy.org/) |
| **AI 推理** | 11 家 LLM 供应商 (云端) / [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地) |
| **LLM 网关** | [LiteLLM](https://github.com/BerriAI/litellm) (多模型统一接口) |
| **数据源** | [Tushare Pro](https://tushare.pro/) (核心行情) + [Akshare](https://akshare.akfamily.xyz/) (补充) |
| **任务调度** | [APScheduler](https://apscheduler.readthedocs.io/) |
| **代码质量** | [Ruff](https://docs.astral.sh/ruff/) (Linter + Formatter) |
| **CI/CD** | GitHub Actions |

---

## 🏗️ 项目架构

采用 **领域驱动设计 (DDD)** 原则，清晰分层：

```
astock_screener/
├── main.py                 # 应用入口，服务编排与生命周期管理
├── config.py               # 全局配置（数据库连接、tiktoken 缓存等）
│
├── data/                   # 数据层
│   ├── cache/              # 缓存管理
│   │   └── cache_manager.py    # 单例缓存管理器，DAO 统一入口
│   ├── domain_services/    # 领域服务
│   │   ├── trade_calendar_service.py  # 交易日历服务（三级降级）
│   │   ├── offline_calendar.py        # 离线日历数据
│   │   └── market_data_service.py     # 市场数据后台服务
│   ├── external/           # 外部数据源
│   │   ├── tushare_client.py   # Tushare API 客户端（限流、重试）
│   │   ├── news_fetcher.py     # 新闻抓取服务
│   │   └── news_subscription.py # 新闻订阅服务
│   ├── persistence/        # 持久化层
│   │   ├── daos/           # 数据访问对象
│   │   │   ├── base_dao.py     # 基础 DAO（类型转换、批量写入）
│   │   │   ├── stock_dao.py    # 股票基础数据
│   │   │   ├── quote_dao.py    # 日线行情、质量评分
│   │   │   ├── financial_dao.py # 财务数据
│   │   │   ├── holder_dao.py   # 股东数据
│   │   │   ├── macro_dao.py    # 宏观经济数据
│   │   │   ├── market_dao.py   # 市场指数数据
│   │   │   ├── screener_dao.py # 选股结果存储
│   │   │   └── sync_dao.py     # 同步状态管理
│   │   ├── models.py           # SQLAlchemy ORM 模型
│   │   ├── db_migrator.py      # 数据库迁移管理
│   │   ├── database_manager.py # 数据库连接管理（同步引擎）
│   │   ├── db_config_service.py # 数据库配置服务
│   │   ├── metadata_manager.py # 元数据管理
│   │   ├── review_manager.py   # AI 回顾管理器
│   │   ├── quality_gate.py     # 数据质量门控装饰器
│   │   └── data_quality.py     # 数据质量检查
│   ├── sync/               # 数据同步策略
│   │   ├── base.py             # 同步策略基类 + SyncResult
│   │   ├── historical.py       # 历史行情同步
│   │   ├── financial.py        # 财务数据同步
│   │   ├── holder.py           # 股东数据同步
│   │   └── macro.py            # 宏观数据同步
│   ├── mixins/             # 混入类
│   │   ├── health_mixin.py     # 健康检查
│   │   └── calendar_mixin.py   # 日历工具
│   ├── constants.py        # 常量定义（低频表等）
│   ├── data_dictionary.py  # 数据字典
│   └── data_processor.py   # 数据处理门面类
│
├── strategies/             # 策略层
│   ├── base_strategy.py        # 策略基类 + 自动注册装饰器
│   ├── polars_base.py          # Polars 策略基类
│   ├── ai_mixin.py             # AI 分析混入
│   ├── ai_strategy.py          # AI 策略实现
│   ├── prompt_validator.py     # Prompt 数据声明校验器
│   ├── strategy_prompts.py     # 策略 Prompt 模板
│   ├── oversold_strategy.py    # 超跌反弹策略
│   ├── fundamental.py          # 基本面策略（价值、成长）
│   ├── market.py               # 市场策略（突破、北向）
│   └── all_strategies.py       # 策略注册汇总
│
├── services/               # 服务层
│   ├── ai_service.py           # AI 服务（LiteLLM 多模型网关）
│   ├── local_model_manager.py  # 本地模型管理
│   └── task_manager.py         # 异步任务管理器
│
├── ui/                     # 表现层 (MVVM)
│   ├── app_layout.py           # 主布局（5 标签页导航）
│   ├── i18n.py                 # 国际化引擎
│   ├── theme.py                # 主题管理
│   ├── components/             # 可复用组件
│   │   ├── config_panels/      # 配置面板
│   │   │   ├── database_config_panel.py
│   │   │   ├── llm_config_panel.py
│   │   │   ├── local_model_config_panel.py
│   │   │   └── tushare_config_panel.py
│   │   ├── virtual_table.py    # 虚拟化表格
│   │   ├── toast_manager.py    # 消息提示
│   │   ├── market_dashboard.py # 市场仪表盘
│   │   ├── news_feed.py        # 新闻订阅
│   │   ├── stock_detail_dialog.py
│   │   └── health_report_dialog.py
│   ├── viewmodels/             # 视图模型
│   │   ├── home_view_model.py
│   │   └── screener_view_model.py
│   └── views/                  # 视图
│       ├── home_view.py
│       ├── screener_view.py
│       ├── data_view.py
│       ├── settings_view.py
│       ├── task_center_view.py
│       ├── onboarding_wizard.py
│       └── settings_tabs/
│           ├── ai_brain_tab.py
│           ├── automation_tab.py
│           ├── data_source_tab.py
│           ├── database_tab.py
│           └── system_tab.py
│
├── utils/                  # 工具层
│   ├── config_handler.py       # 配置管理（读写锁）
│   ├── security_utils.py       # 安全工具（AES-GCM）
│   ├── thread_pool.py          # 线程池管理（IO/CPU 分离）
│   ├── rate_limiter.py         # 令牌桶限流器
│   ├── technical_analysis.py   # 技术指标计算
│   ├── llm_providers.py        # LLM 供应商配置（11 家）
│   ├── scheduler_service.py    # 调度服务
│   ├── proxy_manager.py        # 网络代理管理
│   ├── shutdown.py             # 优雅退出协调器
│   ├── logger.py               # 日志配置
│   ├── loop_local.py           # 事件循环本地存储
│   └── log_decorators.py       # 日志装饰器
│
├── tests/                  # 测试层（100+ 文件，~2700 用例）
│   ├── conftest.py             # 全局测试配置
│   ├── unit/                   # 单元测试
│   │   ├── conftest.py
│   │   ├── test_ai_service.py
│   │   ├── test_llm_providers.py
│   │   ├── test_llm_config.py
│   │   ├── test_config_handler.py
│   │   ├── test_cache_manager.py
│   │   ├── test_boundary_conditions.py
│   │   └── ... (80+ 文件)
│   ├── integration/            # 集成测试
│   │   ├── conftest.py
│   │   ├── test_historical_sync_integrity.py
│   │   ├── test_financial_sync_integrity.py
│   │   ├── test_task_manager_ai_service.py
│   │   ├── test_review_round_trip.py
│   │   └── ... (40+ 文件)
│   └── e2e/                    # 端到端测试
│       ├── conftest.py
│       └── test_onboarding_wizard_e2e.py
│
├── assets/                 # 静态资源
│   └── icons/
│       └── providers/          # 供应商图标（11 个）
│
├── alembic/                # 数据库迁移
│   ├── env.py                  # Alembic 环境配置
│   └── versions/               # 迁移脚本
│
├── locales/                # 国际化
│   ├── en_US/strings.json
│   └── zh_CN/strings.json
│
├── scripts/                # 辅助脚本
│
├── .github/                # GitHub 配置
│   └── workflows/
│       └── ci_cd.yml           # CI/CD 工作流
│
├── pyproject.toml          # 项目元数据 & 工具配置
└── requirements.txt        # 依赖清单
```

### 系统架构图

```mermaid
graph TB
    subgraph PRESENTATION["<b>表现层 Presentation</b>"]
        direction LR
        APP["🖥️ Flet Desktop App"]
        TABS["📑 5 标签页导航<br/>市场 | 选股 | 数据 | 任务 | 设置"]
        VM["🧩 ViewModels<br/>MVVM 数据绑定"]
        COMP["🎨 Components<br/>虚拟表格 | 仪表盘 | 新闻订阅 | Toast"]
        APP --> TABS --> VM --> COMP
    end

    subgraph APPLICATION["<b>应用服务层 Application</b>"]
        direction LR
        AI["🤖 AIService<br/>LiteLLM 多模型网关<br/>11 家供应商统一接口"]
        TASK["📋 TaskManager<br/>异步任务调度<br/>IO/CPU 线程池分离"]
        LOCAL["💻 LocalModelManager<br/>GGUF 本地推理<br/>内存管理 | 并发锁"]
        SCHED["⏰ SchedulerService<br/>APScheduler 定时任务<br/>数据同步调度"]
    end

    subgraph DOMAIN["<b>领域层 Domain</b>"]
        direction LR
        REG["📦 StrategyManager<br/>@register_strategy 自动注册"]
        STG["📊 Strategies<br/>超跌反弹 | 基本面 | 市场突破 | AI 驱动"]
        MIX["🧠 AIStrategyMixin<br/>思维链流式输出<br/>Prompt 数据声明校验"]
        POLARS["⚡ Polars 惰性求值引擎<br/>毫秒级全市场向量化计算"]
        REG --> STG --> MIX
        STG --> POLARS
    end

    subgraph INFRA["<b>基础设施层 Infrastructure</b>"]
        direction LR
        CACHE["🗄️ CacheManager<br/>单例缓存 | DAO 统一入口"]
        DAOS["📝 DAOs (9 个)<br/>Stock | Quote | Financial<br/>Holder | Macro | Market<br/>Screener | Sync | Base"]
        SYNC["🔄 Sync Strategies<br/>历史行情 | 财务 | 股东 | 宏观<br/>断点续传 | 质量评分"]
        QUALITY["🛡️ Quality Gate<br/>Bronze → Silver → Gold<br/>三级数据质量校验"]
        REVIEW["🔁 ReviewManager<br/>AI 回顾闭环<br/>Alpha 收益 | 经验注入"]
        CACHE --> DAOS
        SYNC --> DAOS
        QUALITY --> DAOS
        REVIEW --> CACHE
    end

    subgraph EXTERNAL["<b>外部依赖 External</b>"]
        direction LR
        TUSHARE["📡 Tushare Pro"]
        AKSHARE["📡 Akshare"]
        CLOUD_LLM["☁️ Cloud LLMs<br/>DeepSeek | OpenAI | Anthropic<br/>智谱 | 通义千问 | Moonshot<br/>MiniMax | Google | Mistral"]
        GGUF["📁 GGUF 本地模型"]
        PG[("🐘 PostgreSQL<br/>+ Alembic 迁移")]
    end

    subgraph CROSS["<b>横切关注点 Cross-cutting</b>"]
        direction LR
        CONFIG["⚙️ ConfigHandler<br/>读写锁 | Keyring 加密"]
        SECURITY["🔐 SecurityUtils<br/>AES-GCM 加密存储"]
        PROXY["🌐 ProxyManager<br/>智能代理策略"]
        SHUTDOWN["🛑 ShutdownCoordinator<br/>优雅退出 | 看门狗熔断"]
        LOG["📋 Logger<br/>结构化日志 | 性能监控"]
    end

    VM --> AI
    VM --> TASK
    TASK --> STG
    MIX --> AI
    AI --> CLOUD_LLM
    AI --> LOCAL --> GGUF
    STG --> CACHE
    DAOS --> PG
    SYNC --> TUSHARE
    SYNC --> AKSHARE
    SCHED --> SYNC
    REVIEW --> AI
    REVIEW --> PG

    style PRESENTATION fill:#e1f5fe,stroke:#0288d1
    style APPLICATION fill:#e8f5e9,stroke:#388e3c
    style DOMAIN fill:#fff3e0,stroke:#f57c00
    style INFRA fill:#fce4ec,stroke:#c62828
    style EXTERNAL fill:#f3e5f5,stroke:#7b1fa2
    style CROSS fill:#eceff1,stroke:#546e7a
```

> **架构原则**: 严格遵循 **领域驱动设计 (DDD)** 四层架构。上层仅依赖下层，横切关注点（配置、安全、日志、退出）贯穿所有层级。CI/CD 流水线强制 **85% 代码覆盖率** 门禁。

---

## 📄 快速开始

### 1. 环境要求

* Python 3.11+
* PostgreSQL 14+

### 2. 安装

```bash
git clone https://github.com/shi00/qTrading.git
cd qTrading
pip install -r requirements.txt
```

### 3. 配置数据库

创建 `.env` 文件（或设置系统环境变量）：

```bash
# 必需：数据库连接
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock
```

### 4. 运行

```bash
python main.py
```

*首次启动请根据 Onboarding 向导配置您的 Tushare Token。若需启用 AI 分析，请在设置中配置任意支持的 LLM 供应商（DeepSeek / OpenAI / Anthropic / 智谱 / 通义千问 等 11 家），或下载 GGUF 模型到 `ai_models/` 目录进行本地推理。*

---

## 🧪 测试

项目包含 **100+ 测试文件，约 2700 个测试用例**，按三层金字塔结构组织：

```bash
# 运行所有测试
python -m pytest tests/ -v

# 仅单元测试（快速反馈）
python -m pytest tests/unit/ -v

# 仅集成测试
python -m pytest tests/integration/ -v

# 按标记筛选
python -m pytest tests/ -v -m unit
python -m pytest tests/ -v -m integration
python -m pytest tests/ -v -m e2e

# 运行特定模块测试
python -m pytest tests/unit/test_ai_service.py tests/unit/test_llm_providers.py -v
python -m pytest tests/integration/test_historical_sync_integrity.py -v
```

### 测试金字塔

| 层级 | 目录 | 文件数 | 覆盖内容 |
|------|------|--------|----------|
| **Unit** | `tests/unit/` | 80+ | 纯逻辑验证：AI 服务、策略、DAO、配置、工具类、边界条件 |
| **Integration** | `tests/integration/` | 40+ | 组件协作：数据同步完整性、数据库迁移、回顾系统、任务调度 |
| **E2E** | `tests/e2e/` | 1 | 全链路：Onboarding 向导端到端流程 |

### 关键测试覆盖

| 模块 | 代表性测试 | 验证要点 |
|------|-----------|---------|
| AI 服务 | `test_ai_service.py` | 多供应商参数构建、推理模型检测、边界条件（空模型/空 Key） |
| LLM 配置 | `test_llm_providers.py` | 11 家供应商完整性、模型 ID 合法性、上下文窗口有效性 |
| 配置管理 | `test_config_handler.py` | 读写锁安全、加密存储、Keyring 降级 |
| 数据同步 | `test_historical_sync_integrity.py` | 断点续传、质量评分、退市股票处理 |
| 回顾系统 | `test_review_round_trip.py` | AI 回顾全流程、经验注入 |
| 优雅退出 | `test_shutdown.py` | ShutdownCoordinator 超时熔断、步骤失败恢复 |
| 策略层 | `test_strategy_*.py` | 策略逻辑正确性、AI 混入、Prompt 一致性 |
| 边界条件 | `test_boundary_conditions.py` | 空输入、极端值、并发安全 |

### 代码覆盖率

项目在 CI/CD 流水线中强制执行 **≥85% 代码覆盖率门禁**，覆盖范围涵盖全部核心业务模块：

```bash
# 本地运行覆盖率检查
python -m pytest tests/ --cov=data --cov=services --cov=strategies --cov=utils --cov-report=term-missing --cov-fail-under=85
```

| 覆盖维度 | 说明 |
|----------|------|
| **覆盖目标** | `data/` `services/` `strategies/` `utils/` 四个核心模块 |
| **门禁阈值** | **85%** — 低于此值 CI 流水线失败 |
| **排除项** | tiktoken 缓存、离线日历数据、辅助脚本 |
| **报告格式** | terminal-missing（终端逐行展示）+ XML（CI 解析） |
| **PR 自动评论** | 每个 Pull Request 自动计算并评论覆盖率百分比 |

> 当前项目覆盖率稳定在 **85% 以上**，所有 PR 合并前必须通过覆盖率门禁检查。

---

## 📊 设计亮点

### 策略自动注册

```python
@register_strategy("oversold")
class OversoldStrategy(BaseStrategy, AIStrategyMixin):
    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        # 策略逻辑
        pass
```

### 数据质量门控

```python
@require_quality(QualityTier.SILVER)
def _filter_logic(self, lf, context):
    # 只有数据质量达到 SILVER 级别才执行
    pass
```

### 异步性能监控

```python
@log_async_operation(
    operation_name="fetch_market_data",
    threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
)
async def _fetch_market_data(self):
    # 自动记录执行时间，超阈值告警
    pass
```

### 数据同步质量评分

```python
# 评估单个日期的数据同步质量
score = await quote_dao.get_sync_quality_score("20240101")
# 返回: {"score": 95, "expected": 5200, "actual": 4940, "missing": 260}

# 批量评估多个日期
scores = await quote_dao.get_bulk_sync_quality_scores("20240101", "20240131")
```

### 批量查询优化

```python
# 预取辅助数据，避免 N+1 查询
auxiliary_data = await cache.prefetch_auxiliary_data(ts_codes)
# 返回: {ts_code: {"audit": df, "dividend": df, "holders": df, ...}}
```

### 优雅退出协调

```python
from utils.shutdown import ShutdownCoordinator

coordinator = ShutdownCoordinator(page)
coordinator.start_watchdog(10)                        # 10 秒看门狗
cleanup_ok = await coordinator.do_cleanup(
    timeout_s=8.0, step_timeout_s=3.0                 # 分步超时熔断
)
```

### 多供应商 LLM 网关

```python
from utils.llm_providers import LLM_PROVIDERS

# 11 家供应商统一配置
for pid, cfg in LLM_PROVIDERS.items():
    print(f"{cfg['name']}: {len(cfg['models'])} models, base={cfg['base_url']}")

# LiteLLM 自动路由
params = AIService._build_litellm_params(llm_config, messages)
# → {"model": "deepseek/deepseek-v4-pro", "api_key": "...", ...}
```

### AI 回顾闭环

```python
from data.persistence.review_manager import ReviewManager

# 自动跟踪选股结果的实际表现
review = ReviewManager(cache)
await review.run_review(screener_result_id)
# → 计算 Alpha 收益，标记成功/失败案例，注入后续 Prompt
```

---

## 🔧 开发指南

### 代码风格

项目使用 [Ruff](https://docs.astral.sh/ruff/) 进行代码检查和格式化：

```bash
# 检查代码
python -m ruff check .

# 格式化代码
python -m ruff format .
```

### Pre-commit Hooks

项目配置了 pre-commit hooks，在提交时自动执行代码检查：

```bash
pip install pre-commit
pre-commit install
```

---

## 📝 License

MIT License

---

*Powered by Local AI & High-Performance Quant Logic | Built with ❤️*
