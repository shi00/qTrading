# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![CI/CD](https://github.com/shi00/qTrading/actions/workflows/ci_cd.yml/badge.svg)](https://github.com/shi00/qTrading/actions/workflows/ci_cd.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://github.com/shi00/qTrading)
[![Coverage](https://img.shields.io/badge/coverage-85%25%2B-brightgreen)](https://github.com/shi00/qTrading)
[![UI](https://img.shields.io/badge/UI-Flet%200.86.1-00d2b4)](https://github.com/shi00/qTrading)
[![Data Engine](https://img.shields.io/badge/Data-Polars-orange)](https://github.com/shi00/qTrading)
[![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)](https://github.com/shi00/qTrading)

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

### 8. 📈 向量化回测框架

* **高性能引擎**: 基于 Polars 向量化计算，支持毫秒级全市场回测。
* **灵活策略适配**: 通过 `StrategyAdapter` 将选股策略无缝接入回测框架。
* **多维度绩效指标**: 内置夏普比率、最大回撤、Alpha/Beta、胜率等 10+ 核心指标。
* **仓位管理**: 支持等权、市值加权、风险平价等多种仓位分配策略。
* **交易成本建模**: 精确建模佣金、印花税、滑点等真实交易成本。

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| **前端框架** | [Flet](https://flet.dev/) (Flutter 驱动) |
| **计算引擎** | [Polars](https://pola.rs/) + Pandas |
| **数据库** | [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy 2.0](https://www.sqlalchemy.org/) |
| **数据迁移** | [Alembic](https://alembic.sqlalchemy.org/) |
| **AI 推理** | 10 家 LLM 供应商 (云端) + 自定义 / [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地) |
| **LLM 网关** | [LiteLLM](https://github.com/BerriAI/litellm) (多模型统一接口) |
| **数据源** | [Tushare Pro](https://tushare.pro/) (核心行情) + [Akshare](https://akshare.akfamily.xyz/) (补充) |
| **任务调度** | [APScheduler](https://apscheduler.readthedocs.io/) |
| **代码质量** | [Ruff](https://docs.astral.sh/ruff/) (Linter + Formatter) |
| **CI/CD** | GitHub Actions |

---

## 🏗️ 项目架构

采用清晰分层架构（核心/引导/数据/服务/策略/表现 + 横切 utils）：

```
qTrading/
├── main.py                 # 应用入口，服务编排与生命周期管理
├── config.py               # 全局配置（数据库连接、tiktoken 缓存等）
│
├── core/                   # 核心层（架构基础，不依赖任何其他层）
│   ├── i18n.py                 # 国际化引擎（中/英双语）
│   └── prompt_base.py          # Prompt 基础模板
│
├── app/                    # 引导层（服务编排，仅 main.py 调用）
│   ├── bootstrap.py            # 启动初始化、服务编排
│   └── startup_controller.py   # 启动控制器
│
├── data/                   # 数据层
│   ├── cache/              # 缓存管理
│   │   └── cache_manager.py    # 单例缓存管理器，DAO 统一入口
│   ├── domain_services/    # 领域服务
│   │   ├── trade_calendar_service.py  # 交易日历服务（三级降级）
│   │   ├── offline_calendar.py        # 离线日历数据
│   │   ├── market_data_service.py     # 市场数据后台服务
│   │   └── transaction_cost.py        # 交易成本计算服务
│   ├── external/           # 外部数据源
│   │   ├── tushare_client.py        # Tushare API 客户端（限流、重试）
│   │   ├── akshare_concept_client.py # Akshare 概念板块客户端
│   │   └── news_fetcher.py          # 新闻抓取服务
│   ├── persistence/        # 持久化层
│   │   ├── daos/           # 数据访问对象
│   │   │   ├── base_dao.py            # 基础 DAO（类型转换、批量写入）
│   │   │   ├── stock_dao.py           # 股票基础数据
│   │   │   ├── quote_dao.py           # 日线行情、质量评分
│   │   │   ├── financial_dao.py       # 财务数据
│   │   │   ├── holder_dao.py          # 股东数据
│   │   │   ├── macro_dao.py           # 宏观经济数据
│   │   │   ├── market_dao.py          # 市场指数数据
│   │   │   ├── screener_dao.py        # 选股结果存储
│   │   │   ├── sync_dao.py            # 同步状态管理
│   │   │   ├── backtest_dao.py        # 回测结果存储
│   │   │   ├── express_dao.py         # 快报数据
│   │   │   ├── pledge_detail_dao.py   # 股权质押明细
│   │   │   ├── share_float_dao.py     # 限售股解禁数据
│   │   │   ├── stk_holdertrade_dao.py # 股东增减持数据
│   │   │   ├── stk_limit_dao.py       # 涨跌停数据
│   │   │   ├── sw_industry_dao.py     # 申万行业数据
│   │   │   └── top_inst_dao.py        # 龙虎榜机构明细
│   │   ├── models.py           # SQLAlchemy ORM 模型
│   │   ├── db_migrator.py      # 数据库迁移管理
│   │   ├── data_explorer_query_client.py # 数据库连接管理（同步引擎）
│   │   ├── db_config_service.py # 数据库配置服务
│   │   ├── db_url_override.py  # 数据库 URL 覆盖
│   │   ├── metadata_manager.py # 元数据管理
│   │   ├── review_manager.py   # AI 回顾管理器
│   │   ├── app_state_service.py # 应用状态服务
│   │   ├── quality_gate.py     # 数据质量门控装饰器
│   │   └── data_quality.py     # 数据质量检查
│   ├── sync/               # 数据同步策略
│   │   ├── base.py             # 同步策略基类 + SyncResult
│   │   ├── historical.py       # 历史行情同步
│   │   ├── financial.py        # 财务数据同步
│   │   ├── holder.py           # 股东数据同步
│   │   ├── macro.py            # 宏观数据同步
│   │   ├── concept_sync.py     # 概念板块同步
│   │   └── sw_industry.py      # 申万行业同步
│   ├── mixins/             # 混入类
│   │   ├── health_mixin.py     # 健康检查
│   │   └── calendar_mixin.py   # 日历工具
│   ├── constants.py        # 常量定义（低频表等）
│   ├── data_dictionary.py  # 数据字典
│   └── data_processor.py   # 数据处理门面类
│
├── strategies/             # 策略层
│   ├── backtest/               # 向量化回测框架
│   │   ├── adapter.py              # 策略适配器
│   │   ├── engine.py               # 回测引擎核心
│   │   ├── config.py               # 回测参数配置
│   │   ├── data_provider.py        # 数据提供器
│   │   ├── metrics.py              # 绩效指标计算
│   │   ├── portfolio.py            # 组合管理
│   │   ├── position_sizer.py       # 仓位分配策略
│   │   └── report.py               # 回测报告生成
│   ├── base_strategy.py        # 策略基类 + 自动注册装饰器
│   ├── polars_base.py          # Polars 策略基类
│   ├── ai_mixin.py             # AI 分析混入
│   ├── ai_strategy.py          # AI 策略实现
│   ├── prompt_validator.py     # Prompt 数据声明校验器
│   ├── strategy_prompts.py     # 策略 Prompt 模板
│   ├── oversold_strategy.py    # 超跌反弹策略
│   ├── fundamental.py          # 基本面策略（价值、成长）
│   ├── market.py               # 市场策略（突破、北向）
│   ├── utils.py                # 策略工具函数
│   └── all_strategies.py       # 策略注册汇总
│
├── services/               # 服务层
│   ├── ai_service.py           # AI 服务（LiteLLM 多模型网关）
│   ├── local_model_manager.py  # 本地模型管理
│   ├── task_manager.py         # 异步任务管理器
│   ├── news_subscription_service.py # 新闻订阅服务
│   └── backtest_service.py     # 回测服务（策略回测执行、结果管理）
│
├── ui/                     # 表现层 (MVVM)
│   ├── app_layout.py           # 主布局（6 标签页导航：市场 | 选股 | 回测 | 数据 | 任务 | 设置）
│   ├── i18n.py                 # UI 层国际化封装（核心引擎在 core/i18n.py）
│   ├── theme.py                # 主题管理
│   ├── hooks.py                # 声明式 UI hooks（use_viewmodel 等）
│   ├── pubsub_topics.py        # 发布订阅主题定义
│   ├── startup_views.py        # 启动视图
│   ├── components/             # 可复用组件
│   │   ├── config_panels/      # 配置面板
│   │   │   ├── database_config_panel.py
│   │   │   ├── llm_config_panel.py
│   │   │   ├── local_model_config_panel.py
│   │   │   ├── tushare_config_panel.py
│   │   │   └── failover_config_panel.py  # 故障转移配置
│   │   ├── backtest/           # 回测相关组件
│   │   │   ├── backtest_config_panel.py
│   │   │   └── backtest_result_panel.py
│   │   ├── virtual_table.py    # 虚拟化表格
│   │   ├── toast_manager.py    # 消息提示
│   │   ├── market_dashboard.py # 市场仪表盘
│   │   ├── news_feed.py        # 新闻订阅
│   │   ├── chart_utils.py      # 图表工具
│   │   ├── settings_widgets.py # 设置组件
│   │   ├── resizable_splitter.py # 可调整分割器
│   │   ├── _markdown_safe.py   # Markdown 安全渲染
│   │   ├── stock_detail_dialog.py
│   │   └── health_report_dialog.py
│   ├── viewmodels/             # 视图模型
│   │   ├── home_view_model.py
│   │   ├── screener_view_model.py
│   │   ├── backtest_view_model.py
│   │   ├── data_explorer_view_model.py
│   │   ├── data_source_view_model.py
│   │   ├── onboarding_view_model.py
│   │   ├── task_center_view_model.py
│   │   ├── system_viewmodel.py
│   │   ├── database_config_panel_view_model.py
│   │   ├── llm_config_panel_view_model.py
│   │   ├── local_model_config_panel_view_model.py
│   │   ├── tushare_config_panel_view_model.py
│   │   └── failover_config_panel_view_model.py
│   └── views/                  # 视图
│       ├── home_view.py
│       ├── screener_view.py
│       ├── backtest_view.py    # 回测视图
│       ├── data_view.py
│       ├── settings_view.py
│       ├── task_center_view.py
│       ├── onboarding_wizard.py
│       └── settings_tabs/
│           ├── ai_brain_tab.py
│           ├── automation_tab.py
│           ├── data_source_tab.py
│           ├── database_tab.py
│           ├── system_tab.py
│           └── tier_api_panel.py
│
├── utils/                  # 工具层
│   ├── config_handler.py       # 配置管理（读写锁）
│   ├── config_models.py        # 配置数据模型
│   ├── constants.py            # 常量定义
│   ├── security_utils.py       # 安全工具（AES-GCM）
│   ├── thread_pool.py          # 线程池管理（IO/CPU 分离）
│   ├── rate_limiter.py         # 令牌桶限流器
│   ├── technical_analysis.py   # 技术指标计算
│   ├── llm_providers.py        # LLM 供应商配置（10 家 + 自定义）
│   ├── scheduler_service.py    # 调度服务
│   ├── proxy_manager.py        # 网络代理管理
│   ├── shutdown.py             # 优雅退出协调器
│   ├── logger.py               # 日志配置
│   ├── loop_local.py           # 事件循环本地存储
│   ├── log_decorators.py       # 日志装饰器
│   ├── singleton_registry.py   # 单例注册表
│   ├── error_classifier.py     # 错误分类器
│   ├── exception_hooks.py      # 异常钩子
│   ├── correlation.py          # 相关性工具
│   ├── db_utils.py             # 数据库工具
│   ├── diagnostics.py          # 诊断工具
│   ├── async_utils.py          # 异步工具
│   ├── qfq.py                  # 前复权计算
│   ├── prompt_guard.py         # Prompt 安全防护
│   ├── sanitizers.py           # 数据脱敏工具
│   └── time_utils.py           # 时间工具函数
│
├── tests/                  # 测试层（220+ 单元测试文件，55+ 集成测试文件）
│   ├── conftest.py             # 全局测试配置
│   ├── unit/                   # 单元测试
│   │   ├── conftest.py
│   │   ├── test_ai_service.py
│   │   ├── test_llm_providers.py
│   │   ├── test_llm_config.py
│   │   ├── test_config_handler.py
│   │   ├── test_cache_manager.py
│   │   ├── test_boundary_conditions.py
│   │   ├── strategies/         # 策略测试（含回测模块测试）
│   │   ├── data/               # 数据层测试
│   │   ├── ui/                 # UI 层测试
│   │   └── ... (220+ 文件)
│   └── integration/            # 集成测试
│       ├── conftest.py
│       ├── test_historical_sync_integrity.py
│       ├── test_financial_sync_integrity.py
│       ├── test_task_manager_ai_service.py
│       ├── test_review_round_trip.py
│       ├── test_backtest_e2e.py
│       └── ... (55+ 文件)
│
├── assets/                 # 静态资源
│   └── icons/
│       └── providers/          # 供应商图标（11 个）
│
├── alembic/                # 数据库迁移
│   ├── env.py                  # Alembic 环境配置
│   ├── script.py.mako          # 迁移脚本模板
│   ├── README                  # Alembic 说明
│   └── versions/               # 迁移脚本（0001~0015）
│
├── locales/                # 国际化
│   ├── en_US/strings.json
│   └── zh_CN/strings.json
│
├── scripts/                # 辅助脚本
│
├── .github/                # GitHub 配置
│   └── workflows/
│       ├── ci_cd.yml           # CI/CD 主工作流
│       ├── codeql.yml          # CodeQL 安全分析
│       ├── gitleaks.yml        # 密钥泄露扫描
│       ├── release-please.yml  # 自动化 Release PR
│       ├── renovate.yml        # 依赖更新机器人
│       └── scorecard.yml       # OpenSSF 安全评分
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
        TABS["📑 6 标签页导航<br/>市场 | 选股 | 回测 | 数据 | 任务 | 设置"]
        VM["🧩 ViewModels<br/>MVVM 数据绑定"]
        COMP["🎨 Components<br/>虚拟表格 | 仪表盘 | 新闻订阅 | Toast"]
        APP --> TABS --> VM --> COMP
    end

    subgraph APPLICATION["<b>应用服务层 Application</b>"]
        direction LR
        AI["🤖 AIService<br/>LiteLLM 多模型网关<br/>10 家供应商 + 自定义"]
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
        DAOS["📝 DAOs (16 个业务 DAO + Base)<br/>Stock | Quote | Financial | Holder<br/>Macro | Market | Screener | Sync<br/>Backtest | Express | PledgeDetail<br/>ShareFloat | StkHoldertrade | StkLimit<br/>SwIndustry | TopInst | Base"]
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

> **架构原则**: 严格遵循分层架构（core/app/data/services/strategies/ui/utils，详见 CLAUDE.md §4.1）。上层仅依赖下层，横切关注点（配置、安全、日志、退出）贯穿所有层级。CI/CD 流水线强制 **85% 代码覆盖率** 门禁。

---

## 📄 快速开始

### 1. 环境要求

* Python 3.13+
* PostgreSQL 16+（外置模式需要；内置模式由应用自带 PostgreSQL 17，无需用户安装）

### 2. 安装

```bash
git clone https://github.com/shi00/qTrading.git
cd qTrading
uv venv
.venv\Scripts\activate  # Windows
uv pip install --system -r requirements.txt
# 可选依赖（playwright/brotlicffi）：uv pip install --system -r requirements-optional.txt
```

### 3. 配置数据库

qTrading 提供两种数据库模式：

* **内置模式（默认，推荐新用户）**：应用自带 PostgreSQL 17 sidecar，首次启动自动准备本地数据库，无需任何配置。
* **外置模式（高级）**：连接用户自建的 PostgreSQL 16+ 实例，需在 `.env` 文件或环境变量中配置 `DATABASE_URL`。

```bash
# 外置模式才需要配置 DATABASE_URL；内置模式跳过此步
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock_screener
```

### 4. 运行

```bash
python main.py
```

*首次启动请根据 Onboarding 向导配置您的 Tushare Token。若需启用 AI 分析，请在设置中配置任意支持的 LLM 供应商（DeepSeek / OpenAI / Anthropic / 智谱 / 通义千问 等 10 家云端供应商 + 自定义供应商），或下载 GGUF 模型到 `ai_models/` 目录进行本地推理。*

### 4.1 配置 Tushare 数据源

**Token 获取**：在 [Tushare Pro](https://tushare.pro/) 注册账号后，于「个人主页 → 接口 TOKEN」页复制 token；首次启动 Onboarding 向导或「设置 → 数据源」面板均可粘贴配置。Token 通过系统 Keyring 加密存储（无 Keyring 环境回退到 AES-GCM 加密文件），不入数据库、不写入日志。

**积分档位**：Tushare 按 token 积分档位（120/2000/5000/10000/15000）开放不同 API 与 QPS。应用启动时会执行 capability probe 探测当前 token 可访问的 API 集，未授权 API 在 UI 上灰显并跳过同步。积分档位映射配置见 `data/constants.py` 的 `TUSHARE_POINT_TIERS`。

**降级行为**：当某 API 触发 `TushareAPIPermissionError`（积分不足/未授权），syncer 会跳过该 API 并继续同步其他 API，不阻塞整体流程；token 认证失败（`您的token不对`）会触发全局熔断，所有 API 调用 fast-fail，需用户重新设置 token 后恢复。

---

## 🧪 测试

项目采用双层测试金字塔结构，包含 **220+ 单元测试文件，55+ 集成测试文件**：

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

# 运行特定模块测试
python -m pytest tests/unit/test_ai_service.py tests/unit/test_llm_providers.py -v
python -m pytest tests/integration/test_historical_sync_integrity.py -v
```

### 测试金字塔

| 层级 | 目录 | 文件数 | 覆盖内容 |
|------|------|--------|----------|
| **Unit** | `tests/unit/` | 220+ | 纯逻辑验证：AI 服务、策略、DAO、配置、工具类、边界条件、回测模块 |
| **Integration** | `tests/integration/` | 55+ | 组件协作：数据同步完整性、数据库迁移、回顾系统、任务调度、回测全流程 |

### 关键测试覆盖

| 模块 | 代表性测试 | 验证要点 |
|------|-----------|---------|
| AI 服务 | `test_ai_service.py` | 多供应商参数构建、推理模型检测、边界条件（空模型/空 Key） |
| LLM 配置 | `test_llm_providers.py` | 10 家供应商完整性、模型 ID 合法性、上下文窗口有效性 |
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
python -m pytest tests/ --cov --cov-report=term-missing --cov-fail-under=85
```

| 覆盖维度 | 说明 |
|----------|------|
| **覆盖目标** | `core/` `app/` `data/` `services/` `strategies/` `utils/` `ui/` `config/` `main/` 9 个核心模块 |
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
    required_context_keys: tuple[str, ...] = ("screening_data",)

    @require_quality(QualityTier.SILVER)
    async def filter(self, context: StrategyContext):
        # 策略逻辑：返回过滤后的 DataFrame
        ...
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

# 10 家云端供应商 + 自定义供应商统一配置
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

### 向量化回测框架

```python
from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.config import BacktestConfig

# 配置回测参数
config = BacktestConfig(
    start_date="2023-01-01",
    end_date="2024-01-01",
    initial_capital=1_000_000,
    commission_rate=0.0003,  # 万三佣金
    slippage=0.001,          # 千一滑点
)

# 运行回测
engine = VectorBacktestEngine(config)
result = await engine.run(strategy, data_provider)

# 获取绩效指标
metrics = result.metrics
print(f"Sharpe: {metrics.sharpe_ratio:.2f}")
print(f"Max Drawdown: {metrics.max_drawdown:.2%}")
print(f"Alpha: {metrics.alpha:.4f}")
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
