# qTrading

> 极速、隐私优先的本地化 A 股 AI 量化选股与深度分析平台

[![CI/CD](https://github.com/shi00/qTrading/actions/workflows/ci_cd.yml/badge.svg)](https://github.com/shi00/qTrading/actions/workflows/ci_cd.yml)

**qTrading**（产品名 *AStockScreener*）将 **高性能 Polars 向量化计算引擎** 与 **大语言模型 (LLM)** 深度结合，打通从"海量指标毫秒级初筛"到"AI 逻辑深度回顾、策略自进化"的全链路投研流程。

- **毫秒级全市场筛选**：L1 数学策略 + L2 AI 深度思维的双层漏斗，速度与深度兼得。
- **自进化 AI 闭环**：自动跟踪实际回报、计算 Alpha，把历史经验注入后续筛选，越用越准。
- **隐私优先**：核心投研可完全离线运行，凭证加密存储，数据不出本地。
- **工业级质量底座**：三级数据质量网关 + 断点续传 + 交易成本精确建模的向量化回测。

---

## 🚀 核心特性

### 🧠 漏斗式智能选股
1. **L1 数学策略** — 基于 Polars 惰性求值，毫秒级过滤全市场技术面（超跌、动量）、基本面（PE/ROE/净利增长）与情绪面信号，参数实时可调。
2. **L2 AI 深度思维** — 对候选股进行 AI 审读，UI 流式展示思维链，自动聚合个股新闻、龙虎榜、北向资金，生成深度分析报告。

### 🔄 自进化 AI 闭环
- 自动跟踪 **T+1/T+5 实际回报**，计算相对基准（CSI300/上证指数）的 **Alpha 收益**。
- 标记"成功案例"与"失误陷阱"，将历史经验动态注入后续 Prompt。

### 🛡️ 工业级数据质量网关
Bronze（可用性）→ Silver（连续性/时效性）→ Gold（跨源一致性）三级校验，且数据不达标时相关策略自动降级规避。

### 📊 数据同步完整性
- 相对基准法自动计算每日同步质量分，支持断点续传、增量同步、退市股票精确处理、批量查询优化（消除 N+1）。

### 🔧 数据库迁移自动化
基于 Alembic，启动时自动检测版本、幂等执行、向后兼容。

### 🔒 隐私优先设计
- **本地 AI 推理**：支持 GGUF 模型，核心逻辑不离本地；智能内存卸载、并发锁、推理超时熔断。
- **全本地存储**：流水、报告、配置存于本地 PostgreSQL。
- **安全凭证**：Token 用系统 Keyring 或 AES-GCM 加密存储，密钥自动备份恢复。

### 🎨 现代桌面交互
- 响应式 Flet 架构，主题热切换，虚拟化表格流畅展示 5000+ 数据行。
- 弹性任务中心（IO/CPU 线程池分离、断点续传）、中英双语。

### 📈 向量化回测
Polars 引擎毫秒级全市场回测；内置夏普/回撤/Alpha-Beta/胜率等 10+ 指标；支持等权/市值加权/信号排名加权仓位；精确建模佣金、印花税、滑点。

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| **前端框架** | [Flet](https://flet.dev/) (Flutter 驱动) |
| **计算引擎** | [Polars](https://pola.rs/) + Pandas |
| **数据库** | [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy 2.0](https://www.sqlalchemy.org/) |
| **数据迁移** | [Alembic](https://alembic.sqlalchemy.org/) |
| **AI 推理** | 10 家云端 LLM 供应商 + 自定义 / [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地) |
| **LLM 网关** | [LiteLLM](https://github.com/BerriAI/litellm) |
| **数据源** | [Tushare Pro](https://tushare.pro/) (核心行情) + [Akshare](https://akshare.akfamily.xyz/) (补充) |
| **任务调度** | [APScheduler](https://apscheduler.readthedocs.io/) |
| **代码质量** | [Ruff](https://docs.astral.sh/ruff/) (Linter + Formatter) |
| **CI/CD** | GitHub Actions |

---

## 🏗️ 项目架构

采用清晰的分层架构（核心 → 引导 → 数据 → 服务 → 策略 → UI + 横切 utils），上层仅依赖下层：

```
qTrading/
├── main.py         # 应用入口，服务编排与生命周期管理
├── harness.toml    # Harness 工具协作配置（CLAUDE.md 为唯一权威，本文件不优先于红线/架构边界）
├── package.json    # CI 类型检查工具 pyright 的 npm 声明（配合 package-lock.json 锁定版本）
├── core/           # 核心层：国际化、Prompt 基础模板
├── app/            # 引导层：启动初始化、服务编排
├── data/           # 数据层：缓存、领域服务、外部数据源、DAO、同步策略、数据质量
├── strategies/     # 策略层：超跌反弹/基本面/市场突破/AI 策略 + 向量化回测框架
├── services/       # 服务层：AI 网关、任务调度、本地模型、新闻洞察、内置 PG 维护
├── ui/             # 表现层 (MVVM)：7 标签页（市场/选股/回测/数据/任务/设置/自选股）、组件、视图模型
├── utils/          # 工具层：配置/安全/线程池/限流/优雅退出
├── tests/          # 测试层：单元 + 集成测试（资产索引见 docs/guides/testing.md）
├── alembic/        # 数据库迁移（0001~0028）
├── locales/        # 国际化资源（中/英）
├── assets/         # 静态资源
├── hooks/          # 项目级 Harness hook 覆盖（默认空，沿用插件默认）
└── .security/      # 安全审计忽略清单（pip-audit 漏洞白名单）
```

### 系统架构图

```mermaid
graph TB
    subgraph PRESENTATION["<b>表现层 Presentation</b>"]
        direction LR
        APP["🖥️ Flet Desktop App"]
        VM["🧩 ViewModels<br/>MVVM 数据绑定"]
        APP --> VM
    end

    subgraph APPLICATION["<b>应用服务层 Application</b>"]
        direction LR
        AI["🤖 AIService<br/>LiteLLM 多模型网关"]
        TASK["📋 TaskManager<br/>IO/CPU 线程池分离"]
        LOCAL["💻 LocalModelManager<br/>GGUF 本地推理"]
        SCHED["⏰ SchedulerService<br/>定时数据同步"]
    end

    subgraph DOMAIN["<b>领域层 Domain</b>"]
        direction LR
        STG["📊 Strategies<br/>超跌 | 基本面 | 市场突破 | AI 驱动"]
        POLARS["⚡ Polars 惰性求值引擎"]
        STG --> POLARS
    end

    subgraph INFRA["<b>基础设施层 Infrastructure</b>"]
        direction LR
        DAOS["📝 17 个业务 DAO + Base<br/>Stock | Quote | Financial | Holder<br/>Macro | Market | Screener | Sync<br/>Backtest | Express | PledgeDetail<br/>ShareFloat | StkHoldertrade | StkLimit<br/>SwIndustry | TopInst | Watchlist"]
        SYNC["🔄 Sync Strategies<br/>断点续传 | 质量评分"]
        QUALITY["🛡️ Quality Gate<br/>Bronze → Silver → Gold"]
        REVIEW["🔁 ReviewManager<br/>AI 回顾闭环"]
    end

    subgraph EXTERNAL["<b>外部依赖 External</b>"]
        direction LR
        TUSHARE["📡 Tushare Pro"]
        AKSHARE["📡 Akshare"]
        CLOUD_LLM["☁️ Cloud LLMs"]
        GGUF["📁 GGUF 本地模型"]
        PG[("🐘 PostgreSQL")]
    end

    subgraph CROSS["<b>横切关注点 Cross-cutting</b>"]
        direction LR
        CONFIG["⚙️ ConfigHandler"]
        SECURITY["🔐 SecurityUtils<br/>AES-GCM"]
        SHUTDOWN["🛑 ShutdownCoordinator"]
    end

    VM --> AI
    VM --> TASK
    AI --> CLOUD_LLM
    AI --> LOCAL --> GGUF
    STG --> DAOS
    DAOS --> PG
    SYNC --> TUSHARE
    SYNC --> AKSHARE
    SCHED --> SYNC
    REVIEW --> PG
```

---

## 📄 快速开始

### 环境要求
- Python 3.13+

### 安装

```bash
git clone https://github.com/shi00/qTrading.git
cd qTrading

# 推荐使用 uv
uv venv && .venv\Scripts\activate   # Windows
uv pip install -r requirements.txt

# 或使用 pip
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

### 配置数据库

qTrading 提供两种模式：

- **内置模式（默认，推荐新用户）**：应用自带 PostgreSQL 16.14.0 sidecar，首次启动自动准备本地数据库，零配置。
- **外置模式（高级）**：连接自建的 PostgreSQL 16.14.0+，在 `.env` 或环境变量配置 `DATABASE_URL`：

```bash
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock_screener
```

### 运行

```bash
python main.py
```

首次启动按 Onboarding 向导配置 Tushare Token 即可开始使用。如需 AI 分析，可在设置中选择任一家支持的云端 LLM 供应商（DeepSeek / OpenAI / Anthropic / 智谱 / 通义千问 等 10 家 + 自定义），或下载 GGUF 模型放入 `ai_models/` 进行本地推理。

### 配置 Tushare 数据源

- **Token 获取**：在 [Tushare Pro](https://tushare.pro/) 注册后，于「个人主页 → 接口 TOKEN」复制。供 Onboarding 向导或「设置 → 数据源」粘贴。Token 经系统 Keyring 加密存储（无 Keyring 时回退 AES-GCM 加密文件），不入数据库、不写日志。
- **积分档位**：Tushare 按 token 积分（120/2000/5000/10000/15000）开放不同 API 与 QPS。应用启动时会探测可访问的 API 集，未授权项在 UI 灰显并跳过。
- **降级行为**：单个 API 积分不足时自动跳过，不阻塞整体；token 认证失败则全局熔断 fast-fail，需重新设置后恢复。

---

## 🧪 测试

双层测试金字塔：单元测试 + 集成测试（数量随开发演进，统计见 [docs/guides/testing.md](docs/guides/testing.md) 或用 `pytest --collect-only -q`）。CI 强制 **单文件 ≥80% + diff coverage ≥80%**；**整体 ≥85% 为本地目标**（`pyproject.toml` 的 `fail_under = 85` 未在 CI 路径启用）。

```bash
# 全部 / 单元 / 集成
python -m pytest tests/ -v
python -m pytest tests/unit/ -v
python -m pytest tests/integration/ -v

# 覆盖率
python -m pytest tests/ --cov --cov-report=term-missing --cov-fail-under=85
```

| 层级 | 目录 | 覆盖内容 |
|------|------|----------|
| **Unit** | `tests/unit/` | 纯逻辑：AI 服务、策略、DAO、配置、工具类、边界条件、回测模块 |
| **Integration** | `tests/integration/` | 组件协作：数据同步、迁移、回顾系统、任务调度、回测全流程 |

| 覆盖维度 | 说明 |
|----------|------|
| **覆盖目标** | `core/` `app/` `data/` `services/` `strategies/` `utils/` `ui/` `config/` `main/` 9 个核心模块 |
| **门禁阈值** | **单文件 ≥80%**（CI 强制，`check_per_file_coverage.py`）+ **diff coverage ≥80%**（CI 强制）；**整体 ≥85%** 为本地目标（CI 未启用 `fail_under`） |
| **排除项** | tiktoken 缓存、离线日历数据、辅助脚本 |

---

## 📊 设计亮点

### 策略自动注册 + 数据质量门控

```python
@register_strategy("oversold")
class OversoldStrategy(BaseStrategy, AIStrategyMixin):
    required_context_keys: tuple[str, ...] = ("screening_data",)

    @require_quality(QualityTier.SILVER)  # 数据质量不达标自动规避
    async def filter(self, context: StrategyContext): ...
```

### 多供应商 LLM 网关

```python
from utils.llm_providers import LLM_PROVIDERS

# 10 家云端供应商 + 自定义统一配置
params = AIService._build_litellm_params(llm_config, messages)
# → {"model": "deepseek/deepseek-v4-pro", "api_key": "...", ...}
```

### 向量化回测框架

```python
from strategies.backtest.engine import VectorBacktestEngine
from strategies.backtest.config import BacktestConfig

config = BacktestConfig(
    start_date="2023-01-01",
    end_date="2024-01-01",
    initial_capital=1_000_000,
    commission_rate=0.0003,
    slippage=0.001,
)
result = await VectorBacktestEngine(config).run(strategy, data_provider)
print(f"Sharpe: {result.metrics.sharpe_ratio:.2f}")
print(f"Max Drawdown: {result.metrics.max_drawdown:.2%}")
```

### AI 回顾闭环

```python
from data.persistence.review_manager import ReviewManager

rm = ReviewManager()
await rm.run_review()
# → 计算 T+1/T+5 实际回报与 Alpha，标记成功/失败案例，产出后续筛选的经验上下文
```

---

## 🔧 开发指南

```bash
# 代码检查 / 格式化 (Ruff)
python -m ruff check .
python -m ruff format .

# Pre-commit
pip install pre-commit && pre-commit install
```

更多贡献规范见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [.github](.github/)。

---

## 📝 License

[MIT](LICENSE)

---

*Powered by Local AI & High-Performance Quant Logic | Built with ❤️*