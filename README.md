# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![CI/CD](https://github.com/shi00/qTrading/actions/workflows/ci_cd.yml/badge.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
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

* **本地 AI 推理**: 支持 GGUF 格式模型（如 DeepSeek-R1/Llama-3），实现**核心投研逻辑不离本地**。具备智能内存自动卸载、并发锁控制及推理超时强行熔断保护。
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
| **AI 推理** | OpenAI API (云端) / [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地) |
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
├── config.py               # 全局配置（数据库连接等）
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
│   ├── ai_service.py           # AI 服务（OpenAI 兼容）
│   ├── local_model_manager.py  # 本地模型管理
│   └── task_manager.py         # 异步任务管理器
│
├── ui/                     # 表现层 (MVVM)
│   ├── app_layout.py           # 主布局
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
│   ├── llm_providers.py        # LLM 提供商配置
│   ├── scheduler_service.py    # 调度服务
│   └── log_decorators.py       # 日志装饰器
│
├── tests/                  # 测试层
│   ├── conftest.py             # 测试配置
│   ├── helpers.py              # 测试辅助函数
│   ├── test_historical_sync_integrity.py  # 历史同步完整性测试
│   ├── test_financial_sync_integrity.py   # 财务同步完整性测试
│   ├── test_quote_dao.py       # QuoteDao 测试
│   ├── test_financial_dao.py   # FinancialDao 测试
│   ├── test_holder_dao.py      # HolderDao 测试
│   ├── test_macro_dao.py       # MacroDao 测试
│   ├── test_prompt_consistency.py # Prompt 一致性测试
│   ├── test_data_db_migrator.py # 数据库迁移测试
│   └── test_*.py               # 其他单元测试
│
├── alembic/                # 数据库迁移
│   ├── env.py                  # Alembic 环境配置
│   └── versions/               # 迁移脚本
│       ├── f6586a3fccba_initial_schema_v1.py
│       └── a1b2c3d4e5f6_add_n_cashflow_act_and_delist_date.py
│
├── .github/                # GitHub 配置
│   └── workflows/
│       └── ci_cd.yml           # CI/CD 工作流
│
└── locales/                # 国际化
    ├── en_US/strings.json
    └── zh_CN/strings.json
```

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

*首次启动请根据 Onboarding 向导配置您的 Tushare Token。若需启用 AI 分析，请在设置中配置 OpenAI API 或下载 GGUF 模型到 `ai_models/` 目录。*

---

## 🧪 测试

项目包含 **60+ 测试文件**，覆盖核心功能模块：

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行数据同步完整性测试
python -m pytest tests/test_historical_sync_integrity.py tests/test_financial_sync_integrity.py -v

# 运行 DAO 层测试
python -m pytest tests/test_quote_dao.py tests/test_financial_dao.py tests/test_holder_dao.py -v

# 运行数据库迁移测试
python -m pytest tests/test_data_db_migrator.py -v
```

### 测试覆盖范围

| 模块 | 测试文件 | 覆盖内容 |
|------|----------|----------|
| 数据同步完整性 | `test_historical_sync_integrity.py` | 断点续传、质量评分、批量查询 |
| 财务同步完整性 | `test_financial_sync_integrity.py` | N+1 优化、宏观数据注入 |
| DAO 层 | `test_*_dao.py` | SQL 查询、批量操作、参数验证 |
| 数据库迁移 | `test_data_db_migrator.py` | 自动迁移、幂等性 |
| 策略层 | `test_strategy_*.py` | 策略逻辑、AI 混入 |
| 配置管理 | `test_utils_config*.py` | 配置读写、加密存储 |

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
