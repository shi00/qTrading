# Contributing to AStockScreener

感谢你考虑为 AStockScreener 做贡献！本文档分为三部分：人类贡献者指南、开发环境与命令参考、实现规范手册。

> **AI 编程助手注意**：[CLAUDE.md](./CLAUDE.md) 是项目宪法（红线、架构边界、交互准则），每次会话自动加载。本文件第三部分「实现规范手册」承接宪法中移出的代码模板与详细规范，需要时按需查阅。

## 目录

- [第一部分：人类贡献者指南](#第一部分人类贡献者指南)
  - [行为准则](#行为准则)
  - [如何贡献](#如何贡献)
  - [Pull Request 流程](#pull-request-流程)
  - [代码审查与合并](#代码审查与合并)
- [第二部分：开发环境与命令参考](#第二部分开发环境与命令参考)
  - [前置要求](#前置要求)
  - [安装步骤](#安装步骤)
  - [数据库设置](#数据库设置)
  - [常用开发与测试命令](#常用开发与测试命令)
  - [交付前 DoD 自检清单](#交付前-dod-自检清单)
  - [代码风格基础](#代码风格基础)
  - [提交信息规范](#提交信息规范)
- [第三部分：实现规范手册](#第三部分实现规范手册)
  - [AI 助手方法论与项目概览](#ai-助手方法论与项目概览)
  - [单例模式实现模板](#单例模式实现模板)
  - [策略模式实现模板](#策略模式实现模板)
  - [Polars 向量化策略基类](#polars-向量化策略基类)
  - [AI 策略混入](#ai-策略混入)
  - [DAO 模式](#dao-模式)
  - [数据同步架构](#数据同步架构)
  - [TaskManager 任务生命周期](#taskmanager-任务生命周期)
  - [配置管理、质量门控、性能监控](#配置管理质量门控性能监控)
  - [MVVM 表现层](#mvvm-表现层)
  - [Flet 0.85.3 (V1) API 关键约束](#flet-0853-v1-api-关键约束)
  - [类型标注与 Pyright 规则](#类型标注与-pyright-规则)
  - [日志规范](#日志规范)
  - [异步编程规范](#异步编程规范)
  - [数据库操作规范](#数据库操作规范)
  - [错误处理标准模式](#错误处理标准模式)
  - [测试规范](#测试规范)
  - [CI/CD 流水线与门禁](#cicd-流水线与门禁)
  - [语言切换响应 (I18n Hot Reload) — 附录 A 命令式存量整改对照](#语言切换响应-i18n-hot-reload)
  - [响应式布局规范 (Responsive Layout) — 附录 B 命令式存量整改对照](#响应式布局规范-responsive-layout)
  - [标准开发工作流 (How-To)](#标准开发工作流-how-to)
  - [排查典型问题](#排查典型问题)
  - [已知架构技术债 (Known Technical Debt)](#已知架构技术债-known-technical-debt)

---

# 第一部分：人类贡献者指南

## 行为准则

本项目采用贡献者公约作为行为准则。参与此项目即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

如果你发现了 bug，请通过 [GitHub Issues](https://github.com/shi00/qTrading/issues) 提交。提交前请：

1. 搜索现有 issues，确认没有被报告过
2. 按以下清单提供信息：
   - 问题描述
   - 复现步骤
   - 期望行为
   - 实际行为
   - 环境信息（操作系统、Python 版本等）

### 提出新功能

欢迎提出新功能建议！请在 Issue 中详细描述：

- 功能描述
- 使用场景
- 可能的实现方案

### 代码复用与避免重复造轮子

在开始编写新代码前，请务必遵循**复用优先**原则，避免重复造轮子：
1. **优先复用工程已有代码**：开发新功能前，先全局搜索项目中是否已有类似的工具函数、基础类、UI 组件或业务逻辑。
2. **优先使用成熟开源库**：若需引入常见的基础功能，优先采用业界广泛使用、维护活跃的稳定开源库，而非自行实现。
3. **避免无谓的封装**：如果已有成熟库提供了所需功能，请直接使用，不要对其进行单薄的二次封装，除非这种封装能带来明显的业务价值（如统一鉴权、异常转换等）。

### 提交代码

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## Pull Request 流程

### 提交前自检

1. 确保所有测试通过（单元、集成、E2E 视变更范围）
2. 确保代码覆盖率达标（整体 ≥ 85%，单文件 ≥ 80%）
3. 运行 `pre-commit run --all-files`
4. 更新相关文档（README / CLAUDE.md / CONTRIBUTING.md / SECURITY.md 视变更范围）

### PR 描述模板

提交 PR 时，GitHub 会自动加载项目预设的模板内容。
为了避免文档与实际模板内容的漂移不一致，请直接查阅实际文件：[`.github/PULL_REQUEST_TEMPLATE.md`](./.github/PULL_REQUEST_TEMPLATE.md)。

> **注意**：提交 PR 时无需手动复制该模板，只需在 GitHub 自动生成的草稿基础上如实勾选与填写。请务必确认满足“提交前自检清单（强制全部核对）”项。

## 代码审查与合并

### 审查要求

- 所有 PR 需要至少一位 reviewer 批准
- 某些关键路径（如 `data/persistence/`、`strategies/`）需要 CODEOWNERS 批准
- 解决所有 review 意见后才能合并

### 合并策略

我们使用 **Merge Queue** 确保合并安全：

1. PR 获得批准后，点击 "Ready for review" → "Merge when ready"
2. 系统会自动将 PR 加入合并队列
3. 在队列中会与 main 最新代码组合后重新运行 CI
4. 通过后自动合并

---

# 第二部分：开发环境与命令参考

## 前置要求

- Python 3.13+
- PostgreSQL 16+
- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (依赖管理工具)

## 安装步骤

```bash
# 克隆仓库
git clone https://github.com/shi00/qTrading.git
cd qTrading

# 创建虚拟环境
uv venv
.venv\Scripts\activate  # Windows
# 或 source .venv/bin/activate  # Linux/macOS

# 安装依赖
uv pip install --system -r requirements.txt
uv pip install --system -r requirements-optional.txt
uv pip install --system -r requirements-dev.txt

# 安装 pre-commit hooks
pre-commit install

# 运行测试验证环境
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

> 项目使用 7 个 pre-commit hook (Ruff lint/format、裸 `type: ignore` 检测、requirements 同步、版本一致性校验)，详见 `.pre-commit-config.yaml` 或 [Pre-commit Hooks](#pre-commit-hooks)。

## 数据库设置

> [!NOTE]
> 项目的数据库命名约定如下：
> - **项目名**：`AStockScreener`
> - **本地生产/开发库**：`astock_screener`（使用 `createdb astock_screener` 创建，由 Alembic 迁移驱动）
> - **本地集成测试库**：`test_astock`（由测试配置自动加载与清空，详见 [测试规范](#测试规范)）

```bash
# 创建数据库
createdb astock_screener

# 运行迁移
python -m alembic upgrade head
```

## 常用开发与测试命令

```bash
# 格式化与静态检查
python -m ruff check . --fix
python -m ruff format .
python -m pyright

# 运行测试
python -m pytest tests/unit/ -v --tb=short -m "not slow"
python -m pytest tests/integration/ -n auto -v --tb=short
python -m pytest tests/e2e/ -v --tb=short

# 覆盖率
python -m pytest tests/ --cov --cov-report=term-missing --cov-report=json
python scripts/check_per_file_coverage.py

# 数据库与安全
python -m alembic upgrade head
python -m alembic check
python -m alembic downgrade base
python -m alembic upgrade head
python scripts/run_pip_audit.py --requirements requirements.txt requirements-optional.txt --allowlist .security/audit-allowlist.yml --sources pypi osv

# 依赖同步 (通常由 pre-commit 自动触发)
uv pip compile --universal --no-emit-index-url pyproject.toml -o requirements.txt
uv pip compile --universal --no-emit-index-url --extra dev pyproject.toml -o requirements-dev.txt
uv pip compile --universal --no-emit-index-url --extra optional pyproject.toml -o requirements-optional.txt

# Pre-commit
pre-commit run --all-files

# 启动应用
python main.py
```

## 交付前 DoD 自检清单

每次提交前对照以下清单自检：

- [ ] Ruff lint + format 通过（`ruff check .` + `ruff format --check .`）
- [ ] Pyright 无新增 error
- [ ] 相关单测通过（见下方「变更类型 → 最小验证子集」）
- [ ] 无裸 `# type: ignore`（均带 `[reason]`，pre-commit 强制拦截）
- [ ] 新增 `# NOTE(lazy):` 三要素齐全（简化内容 / ceiling / upgrade）
- [ ] 对照 CLAUDE.md §3 红线逐条自查，无违规

### 变更类型 → 最小验证子集

| 改动范围 | 至少运行 |
|---------|----------|
| 仅 `ui/` | ruff + pyright + `tests/unit/ui/` |
| `data/` DAO/模型 | 上述 + `tests/integration/`（需 DB）+ 若涉 schema 则 `alembic check` |
| `strategies/` | ruff + pyright + `tests/unit/` 对应策略用例 |
| 依赖变更 | 编辑 `pyproject.toml` → pre-commit 自动同步 `requirements*.txt` + `pip-audit` |

## 代码风格基础

### Python 代码规范

- 行宽：120 字符
- 缩进：4 空格
- 引号：双引号
- 使用 Python 3.13+ 语法（`X | None` 而非 `Optional[X]`）

### 工具

我们使用以下工具确保代码质量：

- **Ruff**: Lint 和格式化（规则 `F, E, W, UP, B, SIM, BLE`，忽略 `E501, E402, SIM102, SIM105, SIM108, SIM117, BLE001`）
- **Pyright**: 静态类型检查（`basic` 模式，配置见 `pyrightconfig.json`）
- **pytest**: 测试框架

### 运行检查

```bash
# Lint 检查
ruff check .

# 格式化
ruff format .

# 类型检查
pyright

# 运行测试
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

### 类型注解

- 所有公共函数必须有类型注解
- 使用 `# type: ignore[错误码]  # 原因` 格式抑制类型错误
- 禁止裸 `# type: ignore`（pre-commit 会拦截，对应 [CLAUDE.md R3](./CLAUDE.md#31--绝对禁止)）

## 提交信息规范

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### 类型

| 类型 | 描述 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响功能） |
| `refactor` | 重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具相关 |
| `ci` | CI 配置相关 |

### 示例

```
feat(strategy): add MACD crossover strategy

- Add MACD calculation using Polars
- Implement signal generation logic
- Add unit tests for edge cases

Closes #123
```

---

# 第三部分：实现规范手册

> 本部分承接 [CLAUDE.md](./CLAUDE.md) 宪法中移出的代码模板、详细规范与工作流步骤，供开发人员与 AI 编码助手按需查阅。

## AI 助手方法论与项目概览

> 对应 [CLAUDE.md §1.3 / §1.5 / §2 / §4.1](./CLAUDE.md)。宪法中保留方法论核心原则与决策红线，本节承接被下沉的方法论背景、详细示例、完整技术栈表与完整目录树，供需要深入查阅时使用。

### 极简设计方法论背景（Lazy Ladder）

CLAUDE.md §1.3 的 6 步「极简决策顺序」是对 [Ponytail](https://github.com/DietrichGebert/ponytail) 的 Lazy Ladder 方法论的简化落地。原版 Ponytail 7 层中，「平台原生能力」与「已装第三方依赖」两步针对 Python 桌面应用合并为第 4 步以简化决策。

**Lazy Ladder 运行规则详解**：

- 决策顺序在理解问题后运行，不替代理解。先读代码、追踪真实流程，再选最简路径。
- 极简决策是快速反射，不是研究过程——多个层级都可行时，选最高（最懒）的层级，第一个有效懒方案就是对的。
- 两个 stdlib 方案代码量相同时，选边界情况正确的那个；懒意味着写更少代码，不是选更脆弱的算法。

**过度抽象的具体判定**（必须拒绝的形态）：

- 单实现的接口（仅为「未来扩展」定义的接口）
- 单产品的工厂（只有一个具体产品的工厂）
- 永不变化的配置（无运行时变更需求的可配置项）
- 单调用的层（仅被调用一次的抽象层）
- 单次使用的辅助函数独立模块（应内联或归并到调用方）

### 目标驱动与测试驱动示例

CLAUDE.md §1.5 保留「非平凡逻辑必须验证」与「交付收尾原则」，本节承接测试驱动思维示例与多步规划模板。

**测试驱动思维**：将每个开发任务转换为可验证的目标：

- "添加输入校验" → "编写针对无效输入的测试并使其通过"。
- "修复 Bug" → "先编写能稳定复现该 Bug 的测试，再修复代码使测试通过"。
- "重构逻辑" → "确保重构前后的测试均完全通过"。

**多步规划模板**：对于复杂或多步骤的任务，必须在动手前输出简要的步骤与验证清单：

```text
1. [步骤A] → 验证: [具体检查点/命令]
2. [步骤B] → 验证: [具体检查点/命令]
```

### 项目完整技术栈

**AStockScreener** 是一个本地化智能 A 股量化选股桌面应用，基于 Python 3.13+。以下为完整技术栈表（CLAUDE.md §2 仅保留一句话概述与高风险领域提示）：

| 维度 | 技术选型 |
|------|---------|
| **UI 框架** | Flet 0.85.3 / Flet 1.0 (Flutter 驱动桌面应用，dataclass 控件 + 单线程 async UI 模型) |
| **计算引擎** | Polars (策略层向量化) + Pandas (DAO 层 / 数据同步层) |
| **数据库** | PostgreSQL 16 + SQLAlchemy 2.0 (asyncpg) |
| **数据迁移** | Alembic (自动检测、幂等迁移、CI 强制验证 upgrade → downgrade → upgrade) |
| **AI 推理** | LiteLLM (多家云端供应商统一网关) / llama-cpp-python (本地 GGUF) |
| **数据源** | Tushare Pro (核心) + Akshare (补充) |
| **任务调度** | APScheduler + 自研 `TaskManager` (优先级、持久化、UI 通知) |
| **HTTP 客户端** | requests + httpx (异步) + urllib3 |
| **代码质量** | Ruff (Linter + Formatter) + Pyright (类型检查) |
| **配置验证** | Pydantic (AppConfig 模型验证 + 默认值管理) |
| **CI/CD** | GitHub Actions (Linux + Windows 双平台，含 Windows E2E、PyInstaller 打包、依赖同步 PR) |
| **依赖管理** | uv (`pyproject.toml` → `requirements*.txt`，`--universal` 跨平台锁定，pre-commit 自动同步) |

### 完整目录结构

CLAUDE.md §4.1 仅保留分层架构的依赖方向与禁止反向依赖规则，本节给出完整目录树：

```text
core/             ← 架构核心层 (i18n，不依赖任何其他层)
app/              ← 引导层 (bootstrap: 启动初始化、服务编排，仅 main.py 调用)
data/             ← 数据层 (DAO、同步策略、外部数据源、领域服务、缓存管理)
  ├── cache/             缓存管理器 (CacheManager 单例，DAO 统一入口、引擎管理)
  ├── persistence/       持久化 (DAOs、ORM 模型、数据库管理/迁移/配置、质量门控、配置/状态/元数据/复盘服务)
  ├── domain_services/   领域服务 (交易日历、市场数据、离线日历快照、交易成本)
  ├── external/          外部数据源 (Tushare 客户端、新闻抓取)
  ├── sync/              数据同步策略 (历史数据、财务数据、股东数据、宏观数据)
  ├── mixins/            数据层混入 (交易日历混入、健康检查混入)
  ├── data_processor.py  数据处理与日历服务 (DataProcessor 单例)
  ├── data_dictionary.py 数据字典 (表定义与元数据配置)
  └── constants.py       数据层常量定义
services/         ← 应用服务层 (AI 服务、任务管理器、本地模型管理、新闻订阅服务)
strategies/       ← 策略层 (选股策略、AI 策略混入、Polars 向量化基类、Prompt 模板)
ui/               ← 表现层 (MVVM: Views + ViewModels + Components + Theme + i18n 桥接)
utils/            ← 工具层 (配置、安全、线程池、限流、日志、调度、代理、性能监控)
tests/            ← 测试目录 (unit/ 单元测试, integration/ 集成测试, e2e/ 端到端测试)
scripts/          ← 工具脚本 (覆盖率检查、安全审计、依赖同步等)
locales/          ← 国际化资源文件
man/              ← 架构专题文档 (数据库账号分离、表分区策略)
```

**同层内文件合并原则**：在不违反分层架构的前提下，同一职责的多个小函数可合并到一个文件，不为单次使用的辅助函数创建独立模块。但跨层合并禁止（如 `data/` 与 `ui/` 不可合并）。

## 单例模式实现模板

> 对应 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式)。

使用 `@register_singleton` 装饰器统一管理单例生命周期：

```python
import threading
from utils.singleton_registry import register_singleton

@register_singleton
class MyService:
    _instance = None
    _initialized = False
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        # ... 初始化逻辑 ...
        self._initialized = True

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    @classmethod
    def _atexit_cleanup(cls):
        """Optional: invoked by singleton_registry's centralized atexit handler."""
        if cls._instance is not None:
            # 释放外部资源 (线程池、连接、文件句柄等)
            ...
```

**设计准则：依赖注入优先**

新增单例须支持依赖注入/可注入时钟：构造函数应接收可选的 config/clock 注入参数，默认走生产实现（ConfigHandler/time.monotonic），测试可传 fake。这样无需替换 sys.modules 或全局 patch。

```python
def __init__(self, *, config=None, clock=None):
    self._config = config  # None → 走 ConfigHandler
    self._clock = clock or time.monotonic  # None → 走 time.monotonic
```

## 策略模式实现模板

> 对应 [CLAUDE.md §6.1](./CLAUDE.md#6-设计模式索引)。

```python
from strategies.base_strategy import BaseStrategy, register_strategy
from strategies.utils import StrategyContext

@register_strategy("my_strategy")
class MyStrategy(BaseStrategy):
    required_context_keys: tuple[str, ...] = ("screening_data",)
    required_tables: tuple[str, ...] = ("daily_quotes",)
    required_history_days = 60

    def __init__(self):
        super().__init__(name_key="strategy_my", desc_key="strategy_my_desc")

    async def filter(self, context: StrategyContext):
        # 策略逻辑：返回过滤后的 DataFrame
        ...
```

- **策略入口**: `strategies/all_strategies.py` 通过导入触发 `@register_strategy`，由 `_STRATEGY_REGISTRY` 统一暴露。
- **策略 API**: 依赖声明 (`required_context_keys`/`required_tables`/`required_history_days`/`required_apis`)、动态参数 (`get_parameters()`)、动态描述 (`get_dynamic_description()`)、依赖检查 (`check_dependencies()`) — 详见 `strategies/base_strategy.py`。
- **新增策略流程**: 见 [标准开发工作流](#3-新增一个策略)。

## Polars 向量化策略基类

> 对应 [CLAUDE.md §6.2](./CLAUDE.md#6-设计模式索引)。

继承 `PolarsBaseStrategy` 使用 Polars LazyFrame 进行高性能向量化计算。
`PolarsBaseStrategy` 同时继承了 `AIStrategyMixin`，Polars 过滤后自动进入 AI 分析阶段（可通过 `enable_ai_analysis = False` 关闭）：

```python
from strategies.polars_base import PolarsBaseStrategy
from data.persistence.quality_gate import QualityTier

class MyPolarsStrategy(PolarsBaseStrategy):
    # 注：如需覆盖默认质量等级，应在类属性中定义 required_quality_tier = QualityTier.GOLD，而非在方法上加装饰器。
    required_quality_tier = QualityTier.SILVER

    def _filter_logic(self, lf: pl.LazyFrame, context: StrategyContext) -> pl.LazyFrame:
        return lf.filter(pl.col("pct_chg") > 5.0)
```

> 注：上述类属性模式适用于 `PolarsBaseStrategy` 子类。非 `PolarsBaseStrategy` 子类（如 `OversoldStrategy` 继承 `BaseStrategy` + `AIStrategyMixin`）可使用 `@require_quality` 装饰器。

## AI 策略混入

> 对应 [CLAUDE.md §6.3](./CLAUDE.md#6-设计模式索引)。

`strategies/ai_mixin.py` 的 `AIStrategyMixin` 类提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：

- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量、Prompt 安全防护 (`utils/prompt_guard.py`)
- Prompt 模板集中在 `strategies/strategy_prompts.py`，响应校验在 `strategies/prompt_validator.py`

## DAO 模式

> 对应 [CLAUDE.md §6.4](./CLAUDE.md#6-设计模式索引)。

所有数据访问通过 `BaseDao` 子类，统一提供：

- `_read_db()` — 原生 SQL 读取，返回 DataFrame
- `_read_db_select()` — SQLAlchemy Core 查询 (**推荐**，防注入)
- `_write_db()` — 单条写入 (⚠️ `is_many=True` 已废弃，使用会触发 `DeprecationWarning`)
- `_save_upsert()` — 批量 UPSERT (**推荐**，基于 `pg_insert` + `ON CONFLICT`)
- `chunked_in_query()` — 分块 IN 查询 (避免参数上限)

**DAO 继承体系**: `BaseDao` → 具体子类见 `data/persistence/daos/` 目录

## 数据同步架构

> 对应 [CLAUDE.md §6.5](./CLAUDE.md#6-设计模式索引)。

`data/sync/` 下按数据类别组织同步策略：

- `base.py` — 同步基础定义 (`SyncContext` 依赖注入容器、`SyncResult` 结果数据类、`ISyncStrategy` 策略接口，含取消支持)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表结构、同步配置、质量监控配置。

## TaskManager 任务生命周期

> 对应 [CLAUDE.md §6.6](./CLAUDE.md#6-设计模式索引)。

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
```

- 任务通过 `submit_task()` 提交，传入 `coroutine_factory` (无参可调用对象，返回 coroutine)
- 使用 `update_progress(progress)` 报告进度 (0.0-1.0)，内置节流避免 UI 风暴
- 工作协程内部使用 `is_cancelled()` 检测取消信号 (用户主动取消 / 应用退出)
- 任务持久化到本地，重启后 `RUNNING` 状态会被回填为 `INTERRUPTED`

## 配置管理、质量门控、性能监控

> 对应 [CLAUDE.md §6.7](./CLAUDE.md#6-设计模式索引)。

### 配置管理

`ConfigHandler` 使用读写锁 (`rwlock.RWLockFair`) 保护并发访问。敏感信息优先使用 `keyring`，降级到 AES-GCM 加密文件 (`utils/security_utils.py`)。

### 数据质量门控

使用 `@require_quality(QualityTier.SILVER)` 确保只有数据质量达标才执行逻辑。质量分层: `CRITICAL(0)` → `BRONZE(1)` → `SILVER(2)` → `GOLD(3)`。`STRICT_QUALITY_GATE` 环境变量控制严格模式（默认开启，设为 `false` 关闭）。

### 性能监控装饰器

`utils/log_decorators.py` 提供：

- `@log_async_operation(operation_name="fetch_data", threshold_ms=500)` — 异步操作日志 + 性能监控 + 自动脱敏
- `@track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` — 纯性能追踪 (轻量)
- `@log_ui_action(component_name="Settings", action_type="Click")` — UI 交互埋点
- `AsyncOperationLogger` — 复杂流程分段日志上下文管理器
- **取舍**: 同一函数只挂一个性能装饰器，优先选 `@log_async_operation` (功能更完整)。

**标准性能红线 (`PerfThreshold`)**: 具体数值见 `utils/log_decorators.py`，涵盖内存计算/DB单查询/外部网络/DB批量IO/AI推理/全局初始化六类场景。

## MVVM 表现层

> 对应 [CLAUDE.md §3.2 UI 模型（强制）](./CLAUDE.md#32--强制要求)；声明式渲染细则见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范)。

采用 MVVM + 声明式渲染复合范式：MVVM 负责架构分层，声明式负责 UI 渲染模型。`View = f(ViewModel.state)`，用户事件调 `ViewModel.command()`，VM 更新 state 后 View 自动重渲染。

### 三层职责

| 层 | 职责 | 禁止 |
|----|------|------|
| **View** (`ui/views/`, `@ft.component`) | 读 state 渲染控件树、事件调 commands | 持有业务状态、`did_mount`/`will_unmount`、`self.update()`、`UserControl`、`PageRefMixin` |
| **ViewModel** (`ui/viewmodels/`) | 持有业务状态、调 services/strategies/data；暴露不可变 `state` snapshot + `commands` 方法 | import flet、持有 Flet 控件、`page.update()`/`control.update()`、感知 locale |
| **Component** (`ui/components/`) | 可复用无状态控件（图表、对话框、虚拟表格、Toast） | 耦合具体业务 |
| **Theme** (`ui/theme.py`) | 亮/暗主题切换，颜色/字体 token 集中管理 | — |
| **i18n** (`ui/i18n.py`) | 对 `core.i18n` 的 UI 层薄封装，提供 Flet 文本绑定 | — |

### ViewModel 形态契约

```python
from collections.abc import Callable
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Message:
    """带参数的 i18n 消息：VM 产出 (key, params)，View 按当前 locale 渲染。"""
    key: str
    params: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class Row:
    """行数据 frozen dataclass；tuple[Row, ...] 保证 state 不可变。"""
    code: str
    name: str
    score: float

@dataclass(frozen=True)
class ScreenerState:
    rows: tuple[Row, ...]       # 不可变；DataFrame 转 tuple[Row, ...]，禁止 tuple[dict, ...]
    status: Message             # 带 params 的 i18n 消息
    loading: bool

class ScreenerViewModel:
    @property
    def state(self) -> ScreenerState:
        return ScreenerState(rows=tuple(...), status=Message(...), loading=...)

    async def run(self) -> None: ...                  # command（异步）
    def select_strategy(self, key: str) -> None: ...  # command（同步）

    def subscribe(self, callback: Callable[[ScreenerState], None]) -> Callable[[], None]:
        """订阅 state 变更；返回退订函数。hook 用此注册，_notify 调用时触发。"""
        ...

    def _notify(self) -> None:
        """内部状态变更后调用；遍历订阅者 callback(self.state)。不持有 View 引用。"""
        ...

    def dispose(self) -> None: ...   # 可选：卸载时清理资源
```

- `state` 必须不可变（frozen dataclass / NamedTuple / tuple）；内部状态变更后返回新 snapshot
- `state` 字段不得用 `dict` / `list` / `DataFrame` 等可变类型；行数据用 `tuple[Row, ...]`（Row 为 frozen dataclass），DataFrame 在 VM 内部转换为 Row tuple
- i18n 消息用 `Message(key, params)`，View 渲染时 `I18n.get(msg.key, **msg.params)`；VM 只产出 key+params，不感知当前 locale
- `commands` 即 VM 实例方法，稳定引用；异步 command 在 View 事件处理器 `await`
- VM 通过 `subscribe(callback) -> unsub` 暴露可观察性；`_notify()` 调用所有注册 callback，传入新 state snapshot；VM 不持有 View 引用，订阅关系由 `use_viewmodel` hook 建立

### 桥接 hook 契约

View 通过 `use_viewmodel(factory) -> (state, commands)` 消费 ViewModel：

```python
import flet as ft
from core.i18n import I18n
from ui.hooks import use_viewmodel          # 待建基础设施，见 CLAUDE.md §3.3
from ui.viewmodels.screener_view_model import ScreenerViewModel

@ft.component
def ScreenerView():
    state, vm = use_viewmodel(ScreenerViewModel)   # 首次渲染实例化 + 订阅 _notify

    async def on_run(e):
        await vm.run()    # command -> _notify -> state 更新 -> 自动重渲染

    return ft.Column([
        ft.Text(I18n.get(state.status.key, **state.status.params)),  # Message 渲染
        ft.Button(I18n.get("run"), on_click=on_run),
    ])
```

`use_viewmodel` 契约（实现为待建基础设施，登记于 [CLAUDE.md §3.3](./CLAUDE.md#33--已知技术债与架构限制-known-limitations)）：

- 首次渲染：调 `factory()` 实例化 VM，调 `vm.subscribe(set_state)` 注册（保存返回的 unsub），返回 `(vm.state, vm)`
- `_notify` 触发：VM 遍历订阅者调 `callback(self.state)`，hook 注册的 callback 即 `set_state(vm.state)`，触发重渲染
- 卸载：调 unsub 退订 + `vm.dispose()`（若 VM 实现）
- `factory` 必须是无参 callable；DI 参数在 factory 闭包里完成（如 `lambda: ScreenerViewModel(dep1, dep2)` 或 `functools.partial`），VM 的 `__init__` 接受 DI 参数，不在构造函数里隐式获取全局状态（遵循 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式) DI 原则）

### 存量技术债

[ui/viewmodels/](./ui/viewmodels/) 下 7 个 ViewModel 使用 `on_update`/`on_log` 回调注入（见 [screener_view_model.py](./ui/viewmodels/screener_view_model.py)），属过渡形态；命令式 View 用 `did_mount`/`will_unmount`/`self.update()`/`PageRefMixin`。两者触及时迁到 state snapshot + commands + `use_viewmodel` 目标范式（见 [CLAUDE.md §3.3](./CLAUDE.md#33--已知技术债与架构限制-known-limitations)）。新代码不得沿用回调注入范式。

## Flet 0.85.3 (V1) API 关键约束

> 相关：[CLAUDE.md §2](./CLAUDE.md#2-项目概览) 技术栈表（Flet 0.85.3）、[CLAUDE.md §3.1 R16](./CLAUDE.md#31-绝对禁止)（V1 单线程 async 模型对 UI 阻塞更敏感）。

### 演进方向

项目已从 Flet 0.28.3 (V0) 升级到 0.85.3 (V1，Flet 1.0 alpha/beta)。当前代码库保留少量 V0→V1 兼容垫片与 V1 渲染管线永久方案（见下文「兼容垫片使用规则」），**新开发的 UI 代码必须朝原生 V1 方式演进**，遵循以下原则：

- **不得引入新的 V0 兼容垫片**（如 `hasattr(page, "open")` 双路径、`getattr(e, "delta_x", 0)` 兼容取值等）
- **新控件优先用 V1 原生机制**：通过挂载到 `page.controls` 后由 `parent` 链访问 `page`，而非直接 `self.page = page` 赋值
- **新代码使用 V1 API 形态**：`ft.Button` 而非 `ElevatedButton`；对话框用 `page.show_dialog()`/`page.pop_dialog()` 而非 `page.dialog=`/`page.open()`
- **历史代码不强制重写**（§1.4 微创），仅在功能改动时顺带迁移

### 强制 API 约束（Breaking Changes）

V1 引入的 breaking changes 已通过 `pyright` 与运行期 TypeError/AttributeError 兜底，但部分项为**静默回归**（无异常），开发时必须主动遵守：

| # | 类别 | V0（禁止） | V1（必须） | 检测方式 |
|---|------|----------|----------|---------|
| 1 | 应用入口 | `ft.app(target=main)` | `ft.run(main=main)` | 运行期 |
| 2 | 窗口 resize | `page.on_resized = ...` | `page.on_resize = ...` | 运行期（静默失效） |
| 3 | 对话框显示 | `page.open(x)` / `page.dialog = x` | `page.show_dialog(x)` | AttributeError |
| 4 | 对话框关闭 | `page.close(x)` | `page.pop_dialog()` | AttributeError |
| 5 | FilePicker | `FilePicker(on_result=...)` + `overlay.append` | `page.services.append(picker)` + `await picker.pick_files()` | 运行期 |
| 6 | 图表控件 | `ft.LineChart(...)` | `import flet_charts as fch` → `fch.LineChart(...)` | ImportError |
| 7 | 图像 fit 枚举 | `ft.ImageFit.CONTAIN` | `ft.BoxFit.CONTAIN` | AttributeError |
| 8 | 图像 src | `ft.Image(src_base64=...)` | `ft.Image(src=b64_str)`（直接 base64） | TypeError |
| 9 | 按钮文本 | `Button(text="x")` / `btn.text = ...` | `Button(content="x")` / `btn.content = ...`（位置参数仍可） | TypeError |
| 10 | 弃用按钮 | `ft.ElevatedButton(...)` | `ft.Button(...)`（无警告但仍建议迁移） | 无（静默） |
| 11 | 滚动间隔 | `on_scroll_interval=100` | `scroll_interval=100` | 运行期（静默失效） |
| 12 | 样式 helper | `ft.padding.only(...)` / `ft.alignment.center` | `ft.Padding.only(...)` / `ft.Alignment.CENTER` | AttributeError |
| 13 | Dropdown 事件 | `Dropdown(on_change=...)` | `Dropdown(on_select=...)` | TypeError |
| 14 | TextField 字段 | `focus_border_color=...` | `focused_border_color=...` | TypeError |
| 15 | Tabs 构造 | `ft.Tabs(tabs=[ft.Tab(text=..., content=...)])` | `ft.Tabs(length=N, content=ft.Column([ft.TabBar(tabs=[ft.Tab(label=...)]), ft.TabBarView(controls=[...])]))` | TypeError |
| 16 | 拖拽增量 | `e.delta_x` | `e.primary_delta`（主路径），`e.local_delta.x`（回退） | **静默回归**（恒 0） |
| 17 | 窗口图标 | `page.window_icon` | `page.window.icon` | AttributeError |
| 18 | 控件 page 属性 | `self.page = page` 直接赋值 | 通过 `parent` 链访问；若必须在挂载前引用 page，继承 [`PageRefMixin`](./ui/v1_compat.py) | AttributeError |
| 19 | 本地存储 | `page.client_storage` | `page.shared_preferences` | AttributeError |
| 20 | 控件 update | 未挂载时 `control.update()` 静默返回 | 未挂载抛 `RuntimeError`（测试代码由 `mock_flet._install_v1_compat_control_page_mock()` 全局桩兼容） | RuntimeError |
| 21 | 窗口方法 | `page.window.destroy()`（同步） | `await page.window.destroy()`（V1 协程） | 运行期（RuntimeWarning: coroutine never awaited） |

> **⚠️ 桌面关闭事件不可用 `page.on_close`**：`page.on_close` 在会话关闭/超时断开时触发，**非**用户点击窗口关闭按钮。桌面端关闭拦截必须用 `page.window.prevent_close = True` + `page.window.on_event`（监听 `ft.WindowEventType.CLOSE`），见 `main.py` 的窗口事件处理器。此为 V1 正确实现，非 V0 遗留。

> **来源说明**：第 8 项（`src_base64` → `src`）与第 16 项（`delta_x` → `primary_delta`）来自 Flet 官方 issue #5238（V1 breaking changes 汇总）。

### 兼容垫片使用规则

以下 V0→V1 兼容垫片为本次升级新增，**仅限历史代码使用**。新代码应优先采用 V1 原生方式，避免依赖垫片。

| 垫片 | 位置 | 用途 | 新代码策略 |
|------|------|------|----------|
| `PageRefMixin` | [`ui/v1_compat.py`](./ui/v1_compat.py) | 覆盖 `ft.Control.page` 只读 property，使 5 个历史控件（`AppLayout`/`TaskCenterView`/`FailoverConfigPanel`/`ProviderCredentialDialog`/`ResizableSplitter`）可读写 `control.page` | 新控件应通过挂载到 `page.controls` 后由 V1 原生 `parent` 链访问 `page`；若必须在挂载前引用 page（如注册回调、读取 `page.theme_mode`），才允许继承 `PageRefMixin` |
| `_install_v1_compat_control_page_mock()` | [`tests/unit/ui/mock_flet.py`](./tests/unit/ui/mock_flet.py) | 全局 monkey-patch `ft.Control.page`/`update`，使测试代码可注入 mock_page 且未挂载 `update()` 静默返回 | 新测试代码沿用现有桩；待测试基础设施整体重构为 V1 原生模式后再移除 |

> **V1 永久方案（非垫片）**：[`refresh_dropdown_options()`](./ui/i18n.py)（`ui/i18n.py` 的 `refresh_dropdown_options()` 函数）不是兼容垫片，而是 V1 渲染管线的永久解决方案。V1 `Prop` 描述符的值相等优化导致 `DropdownButton` 在批量 `page.update()` 中不触发 rebuild，此行为是 V1 固有特性而非临时 bug，故该函数需长期保留。i18n 热重载场景的 Dropdown 必须使用本函数。

> 每项垫片均遵循 [CLAUDE.md §3.3](./CLAUDE.md#33--已知技术债与架构限制-known-limitations) `# NOTE(lazy):` 标记规范。

### V1 声明式 UI 开发规范

> 宪法 [CLAUDE.md §3.2 UI 模型（强制）](./CLAUDE.md#32--强制要求) 的唯一实现细则。
> 命令式存量（`class X(ft.Container)` + `did_mount`/`will_unmount` + 手动 `self.update()`）一律视为技术债，整改对照见 [语言切换响应（附录 A 命令式存量整改对照）](#语言切换响应-i18n-hot-reload) 与 [响应式布局规范（附录 B 命令式存量整改对照）](#响应式布局规范-responsive-layout)。

切到 Flet 0.85.3 后，新增 View/Panel/Component 必须采用声明式 `@ft.component` + 官方 hooks 写法。API 已对 `flet==0.85.3` 实测可用（见下方签名核实）。

#### 1. 关注点对照（命令式作废 → 声明式要求）

| 关注点 | 命令式旧写法（作废） | 声明式要求（宪法标准） |
|--------|------|------|
| 组件定义 | `class X(ft.Container): __init__/super()` | `@ft.component` 函数返回控件树 |
| 状态 | 实例属性 + 手动 `self.update()` | `use_state` 状态变更自动重渲染 |
| 生命周期/副作用 | `did_mount`/`will_unmount` | `use_effect(setup, dependencies, cleanup)` |
| i18n 热切换 | `I18n.subscribe`/`unsubscribe` + `refresh_locale` + 手动刷新 | locale 作为声明式状态源，切换自动重渲染（不再手动订阅/刷新） |
| 下拉刷新 | `refresh_dropdown_options` 两步 update 绕过 | 状态驱动重建 options，绕过随之删除 |
| 响应式 | `handle_resize` 鸭子分发 + 断点手算 | 窗口尺寸作为 state/observable + `ResponsiveRow`，状态驱动布局 |
| page 引用 | `PageRefMixin` 覆写只读 `control.page` | 组件内经官方上下文机制或事件 `e.page` 获取，垫片删除 |
| ViewModel 消费 | `on_update`/`on_log` 回调注入 + View 持有 VM | `use_viewmodel(factory) -> (state, commands)`，View 只读 state + 调 commands（见 [MVVM 表现层](#mvvm-表现层)） |

#### 2. `@ft.component` 标准模板

```python
import flet as ft
from core.i18n import I18n

@ft.component
def MetricCard(label_key: str):
    # 声明式状态：值变更自动重渲染，无需手动 update()
    value, set_value = ft.use_state(0)

    # 副作用：挂载/卸载/依赖变更时执行；返回值作为 cleanup（卸载时自动调用）
    def setup():
        # 订阅 locale，切换时调用 set_value 触发重渲染
        sub_id = I18n.subscribe(lambda: set_value(lambda v: v + 0))  # 触发重渲染
        return lambda: I18n.unsubscribe(sub_id)  # 卸载时自动退订
    ft.use_effect(setup, dependencies=[label_key])

    return ft.Container(
        content=ft.Column([
            ft.Text(I18n.get(label_key)),
            ft.Text(str(value)),
        ]),
    )
```

#### 3. `use_state` / `use_effect` API（已对 `flet==0.85.3` 实测）

- `ft.use_state(initial) -> (value, setter)`：类似 React `useState`。`setter` 接受新值，或接受接收前值返回新值的函数。
- `ft.use_effect(setup, dependencies=None, cleanup=None)`：
  - `setup` 为普通函数，可返回 cleanup 函数，或通过 `cleanup` 参数单独提供。
  - `dependencies` 缺省时只在初次渲染运行；指定时按依赖变化重跑；cleanup 在重跑前与卸载时执行。
  - hooks 必须在 `@ft.component` 渲染上下文内调用，独立调用抛 `RuntimeError: No current renderer`。
- `ft.component(fn)` 装饰器：把函数标记为组件，返回值即控件树根节点。

#### 4. i18n / 响应式声明式实现

- **i18n**：locale 作为声明式状态源。组件通过 `use_state` 订阅 `I18n` 的 locale 变化（或在父组件统一管理 locale state，子组件经 props 接收），切换时自动重渲染。**不再**手动 `subscribe`/`refresh_locale`。**ViewModel state 不含 locale**——VM 只产出 i18n key（如 `"screener.run"`），View 渲染时按当前 locale 解析；locale 切换由 View 层独立状态源驱动重渲染，不需要 VM 参与或通知。
- **响应式**：窗口尺寸作为 `use_state`（由根组件订阅 `page.on_resize` 更新），通过 props 下发；视图内用 `ResponsiveRow` + `col` 配置，状态驱动布局。**不再**实现 `handle_resize` 鸭子分发。
- **下拉刷新**：options 由 state 派生，`use_state` 触发重建即自动绕过 V1 `Prop.__set__` 值相等优化。`refresh_dropdown_options()` 工具函数在声明式下不再需要，存量命令式控件改造后随之删除。

#### 5. ViewModel 消费（MVVM 桥接）

View 消费 ViewModel 必须经 `use_viewmodel(factory) -> (state, commands)` hook，**不得**直接 `vm = SomeViewModel()` 实例化或注入回调。完整契约与形态见 [MVVM 表现层](#mvvm-表现层)。

```python
import flet as ft
from core.i18n import I18n
from ui.hooks import use_viewmodel          # 待建基础设施，见 CLAUDE.md §3.3
from ui.viewmodels.screener_view_model import ScreenerViewModel

@ft.component
def ScreenerView():
    state, vm = use_viewmodel(ScreenerViewModel)   # state 不可变 snapshot；vm 即 commands

    async def on_run(e):
        await vm.run()    # command -> _notify -> state 更新 -> 自动重渲染

    # View 只做两件事：读 state 渲染、事件调 commands
    return ft.Column([
        ft.Text(I18n.get(state.status.key, **state.status.params)),  # Message 渲染
        ft.Button(I18n.get("run"), on_click=on_run),
    ])
```

要点：

- View 只做两件事：读 `state` 渲染控件树、事件调 `vm.command()`
- VM 不得出现在 View 的 `use_state`/`use_effect` 之外的任何地方；不持有 VM 引用做副作用
- `use_viewmodel` 未实现前，新 UI 开发被阻塞——必须先实现/扩展本 hook 再写 View（见 [CLAUDE.md §3.3](./CLAUDE.md#33--已知技术债与架构限制-known-limitations)）
- 现有 7 个 ViewModel 的 `on_update`/`on_log` 回调注入属待迁移技术债，新代码不得沿用

#### 6. 迁移约束

- 旧控件**不做机械批量迁移**（§1.4 微创）；仅在因功能改动已触及某控件时，可顺带迁到声明式。
- `ft.run(before_main=...)` 属可选优化，YAGNI，暂不强制。
- async 窗口/控件方法必须 `await`。
- 命令式 `@ft.control`/`@dataclass` + `did_mount`/`will_unmount` 写法属存量技术债，不再用于新代码。

### 依赖管理

- `flet` / `flet-desktop` / `flet-charts` 三个独立包，均锁定 `==0.85.3`（见 [`pyproject.toml`](./pyproject.toml) 的 `dependencies` 中 `flet`/`flet-desktop`/`flet-charts` 三项）
- `flet-charts` 是 V1 拆分出的图表控件独立包，新增图表控件必须 `import flet_charts as fch`
- 版本锁定策略：`==` 精确锁定，避免 minor 版本间的 API 漂移（V1 处于 alpha/beta 阶段）
- 升级 Flet 版本时，三个包必须同步升级

### PyInstaller 打包

[`AStockScreener.spec`](./AStockScreener.spec) 的 `hiddenimports` 列表必须含 `flet` / `flet_desktop` / `flet_charts` 三项：

- `flet_charts` 是 V1 新增的独立模块，遗漏会导致打包产物 `import flet_charts` 报 ImportError
- `flet_core` / `flet_desktop` 在 V1 已合并入 `flet`，但保守保留 `flet_desktop` 以兼容桌面打包路径
- 新增 flet 相关 import 时，同步检查 spec 文件的 `hiddenimports` 是否覆盖

### Flet 版本升级文档协同机制

- `CLAUDE.md` 不记录具体 Flet API 细节，只记录升级时必须遵守的验证原则、红线与架构边界。
- `CONTRIBUTING.md` 是 Flet API 约束、UI 开发范式、兼容垫片与测试模板的唯一细节源，必须随 `pyproject.toml` 中锁定的 Flet 版本同步更新。
- 每次升级 Flet 小版本或大版本，必须完成：
  1. 核对官方 breaking changes / deprecations；
  2. 运行最小 UI 验证：启动、窗口关闭、dialog、resize、i18n 热重载、一个 V1 控件样例；
  3. 更新 `CONTRIBUTING.md` 的 Flet 章节与对应验证清单；
  4. 仅当升级影响红线、架构边界或 AI 行为规则时，才同步修改 `CLAUDE.md`。
- 禁止在两份文档中重复维护同一 Flet API 细节；长期规范引用用符号锚点，不用硬编码行号。

## 类型标注与 Pyright 规则

> 对应 [CLAUDE.md §5.2](./CLAUDE.md#52-类型标注)。

- **类型检查器**: Pyright (`basic` 模式，版本见 `.github/workflows/ci_cd.yml`；完整配置见 `pyrightconfig.json`，优先级高于 `pyproject.toml`)
- **关键 Pyright 规则**:

| 规则 | 级别 | 说明 |
|------|------|------|
| `reportCallIssue` | `error` | 函数调用类型不匹配必须修复 |
| `reportOptionalOperand` | `error` | Optional 值参与运算必须先判空 |
| `reportOptionalIterable` | `error` | Optional 值用于迭代必须先判空 |
| `reportGeneralTypeIssues` | `error` | 通用类型问题必须修复 |
| `reportMissingImports` | `error` | 缺失导入必须修复 |
| `reportOptionalMemberAccess` | `warning` | Optional 成员访问应判空 |
| `reportArgumentType` | `warning` | 参数类型不匹配应修复 |
| `reportAttributeAccessIssue` | `warning` | 属性访问问题应修复 |
| `reportOptionalSubscript` | `warning` | Optional 值下标访问应判空 |

- **`type: ignore` 必须带理由** (pre-commit 强制拦截裸 `# type: ignore`):

  ```python
  # ✅ 正确
  task._coroutine_gen = None  # type: ignore[assignment]

  # ❌ 错误 (pre-commit 会拒绝)
  task._coroutine_gen = None  # type: ignore
  ```

## 日志规范

> 对应 [CLAUDE.md §5.4](./CLAUDE.md#54-日志规范)。

- 使用 `logging.getLogger(__name__)` 获取模块级 logger。
- 日志前缀格式: `[ClassName]` 或 `[ModuleName]`，便于过滤。
- **日志级别选择**:
  - `DEBUG` — 性能采样、细粒度执行轨迹 (生产默认不输出)
  - `INFO` — 关键状态变迁 (服务启动、连接建立、任务完成)
  - `WARNING` — 慢查询、慢写入、降级路径、关机期间的连接错误、可恢复异常
  - `ERROR` — 操作失败但不影响进程
  - `CRITICAL` — 系统级失败 (`MemoryError`、磁盘满)、数据完整性问题
- **关机期间** 的连接错误 (`no active connection` / `database is closed` / `ConnectionDoesNotExistError`) 必须降级为 `warning`，避免污染日志。
- **UI 交互埋点** 使用专用 `UILogger.log_action()` 类方法或 `@log_ui_action` 装饰器，自动写入 `ui.action` logger 通道。
- **敏感参数** 必须经 `DataSanitizer.sanitize_args()` 或 `DataSanitizer.sanitize_error()` 脱敏后再记录。
- **Correlation ID** 涉及跨模块的请求链路追踪，使用 `utils/correlation.py` 提供的工具串联日志。

## 异步编程规范

> 对应 [CLAUDE.md §5.5](./CLAUDE.md#55-异步编程规范)。

- **asyncio 模式**: 全项目使用 `asyncio` 驱动异步。
- **线程安全**: UI 回调可能来自线程池，使用 `loop.call_soon_threadsafe()` 转移到事件循环。
- **线程池分离**: IO 密集型使用 `TaskType.IO`，CPU 密集型 (NumPy/Pandas 等 GIL 释放型) 使用 `TaskType.CPU`；纯 Python CPU 密集任务应使用 `ProcessPoolExecutor` (项目暂无)。
- **CancelledError 必须传播**: 永远 `raise` 不吞没，否则破坏优雅停机 (对应 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止))。
- **事件循环绑定对象**: 使用 `utils.loop_local` 的 `get_loop_local()` / `del_loop_local()` / `clear_all_loop_locals()` 管理 `asyncio.Event`、`asyncio.Lock` 等绑定到特定事件循环的对象，避免跨循环死锁 (对应 [CLAUDE.md R11](./CLAUDE.md#31--绝对禁止))。
- **`asyncio.gather`** 涉及失败可恢复场景使用 `return_exceptions=True`，并在调用方逐个分类异常。
- **不要在 `__init__`** 中调用 `asyncio.create_task()`，会绑定到错误的事件循环；改为提供 `async def initialize()` 方法。

## 数据库操作规范

> 对应 [CLAUDE.md §5.6](./CLAUDE.md#56-数据库操作规范)。

- **异步引擎**: 使用 `asyncpg` 驱动 (通过 SQLAlchemy asyncio)。
- **参数占位符**: 使用 `$1, $2, ...` (asyncpg 原生占位符，非 `%s`) (对应 [CLAUDE.md R4](./CLAUDE.md#31--绝对禁止))。
- **批量写入**: 使用 `_save_upsert()` (基于 `ON CONFLICT DO UPDATE`，内置分块，大小见 `base_dao.py`) (对应 [CLAUDE.md R8](./CLAUDE.md#31--绝对禁止))。
- **分块 IN 查询**: 使用 `chunked_in_query()` 避免 PostgreSQL 参数上限 (分块大小见 `base_dao.py`)。
- **引擎状态检查**: DAO 操作前必须确认引擎仍可用；关机/释放后继续访问时应抛出或传播 `EngineDisposedError`，调用方按关机降级处理。
- **维护锁**: DAO 操作前 `await self._get_maintenance_event().wait()` 等待维护完成 (基类已自动处理)。
- **慢查询阈值**: 见 `base_dao.py` 配置 (基类自动告警，无需手动埋点)。
- **DB 异常应在 DAO 层处理**: 业务层只接收 `EngineDisposedError` 和业务异常，不应直接捕获 `asyncpg.*Error`。

## 错误处理标准模式

> 对应 [CLAUDE.md §5.7](./CLAUDE.md#57-错误处理模式)。

```python
# ✅ 标准异常处理模式
try:
    result = await some_operation()
except asyncio.CancelledError:
    logger.warning("[Module] Cancelled during shutdown.")
    raise  # R2: 必须传播
except EngineDisposedError:
    logger.warning("[Module] Engine disposed, skipping operation.")
    return  # 优雅降级
except Exception as e:
    error_info = classify_error(e, context="general")     # 返回 dict: code / message_key [/ format_args / should_retry]
    severity = classify_severity(e, context="general")    # 返回: system / recoverable / operational
    if severity == "system":
        logger.critical(f"[Module] SYSTEM-LEVEL failure: {e}", exc_info=True)
        raise  # 系统级错误必须上抛
    elif severity == "recoverable":
        logger.warning(f"[Module] Recoverable error ({error_info['code']}): {e}")
    else:
        logger.error(f"[Module] Operational error: {e}", exc_info=True)
```

**错误分类上下文** (`classify_error` 的 `context` 参数):

- `"token"` — Tushare Token 验证错误
- `"llm"` — LLM API 调用错误 (区分永久错误 / 瞬态可重试错误，返回 `should_retry` 字段)
- `"db"` — 数据库连接 / 认证错误
- `"chart"` — 图表渲染错误
- `"general"` — 通用错误 (默认)

**UI 层错误展示** 使用 `get_error_message(error_info)` 把 `message_key` 翻译为本地化文案。

## 测试规范

> 对应 [CLAUDE.md §7](./CLAUDE.md#7-测试规范索引)。

### 测试架构

分为 `unit/` (单元测试, 纯逻辑隔离), `integration/` (集成测试, 依赖 PostgreSQL), `e2e/` (端到端测试)。

测试标记 (定义在 `pyproject.toml [tool.pytest.ini_options]`):

- `@pytest.mark.unit` — 单元测试
- `@pytest.mark.integration` — 集成测试
- `@pytest.mark.database` — 需要数据库连接
- `@pytest.mark.ai` — 涉及 AI 服务或模型调用
- `@pytest.mark.e2e` — 端到端测试
- `@pytest.mark.slow` — 慢速测试 (真实 sleep、大量 IO)
- `@pytest.mark.network` — 需要真实网络访问
- `@pytest.mark.no_auto_mock` — 跳过 `mock_external_services` autouse fixture (用于测试外部服务自身)

### 测试编写规则

- **单例隔离**: 单元测试（`tests/unit/`）由 `tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 自动重置所有注册单例。集成测试和 e2e 测试不自动重置单例，需手动管理。需精细控制单例初始化状态时（如测试 `__init__` 重复初始化防护），可使用 `singleton_state` 上下文管理器：

  ```python
  from tests.conftest import singleton_state

  with singleton_state(TaskManager, extra_attrs=["_initialized"]):
      mgr = TaskManager()
      # 测试逻辑...
  # 自动恢复原始单例状态
  ```

- **外部服务 Mock**: 单元测试由 `mock_external_services` autouse fixture 自动 mock 外部网络调用 (`NewsFetcher`/`ReviewManager`)。测试自身模块需跳过 mock 时，在文件顶部声明 `pytestmark = pytest.mark.no_auto_mock`。

- **Mock 规范**: `keyring` 和 `litellm` 在 `tests/conftest.py` 中全局 mock (session 别，`pytest_configure` 早期拦截)，每个测试后清理状态。
- **异步测试**: 使用 `pytest-asyncio`，`asyncio_mode = "auto"` 自动处理 (`async def test_xxx()` 即可)。
- **事件循环策略**: Windows 使用 `WindowsSelectorEventLoopPolicy`，loop scope 为 `session` 级。*(注：这引入了测试态特有的已知泄漏问题，详见本手册底部的「已知架构技术债」)*
- **配置隔离**: 测试使用临时配置文件 (`tempfile.mkdtemp`)，通过 `pytest_configure` 在 import 之前重写 `utils.config_handler.CONFIG_FILE`。
- **DB 隔离**: 集成测试连接 `test_astock` 数据库 (CI 通过 service container 启动 PostgreSQL 16)，通过 `TEST_DB_*` 环境变量配置。

### 覆盖率要求

> [!NOTE]
> 覆盖率阈值的单一事实源位于 `pyproject.toml`。
> - **整体覆盖率**：具体数值见 `pyproject.toml` 中的 `fail_under`（目前为 ≥ 85%）
> - **单文件覆盖率**：具体数值见 `pyproject.toml` 中的 `per_file_minimum`（目前为 ≥ 80%，由 `scripts/check_per_file_coverage.py` 强制检查）
> - **覆盖率源**：`core`, `app`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）
> - **覆盖率排除行**：`pragma: no cover`、`if __name__ == "__main__"`、`if TYPE_CHECKING:`、`raise NotImplementedError`、`...`

## CI/CD 流水线与门禁

> 对应 [CLAUDE.md §8](./CLAUDE.md#8-cicd-门禁索引)。

GitHub Actions 双平台验证 (`.github/workflows/ci_cd.yml`)，PR/主干质量门禁包括：

1. **Fast Ruff Check & Format** (Python 3.13 + 3.14 experimental)
2. **Pre-commit Hooks** (Ruff、格式化、裸 `type: ignore`、requirements 同步)
3. **Security Audit** (`scripts/run_pip_audit.py`，扫描 `requirements.txt`、`requirements-optional.txt`、`requirements-dev.txt`，使用 `.security/audit-allowlist.yml`)
4. **Pyright Type Check** (版本见 `ci_cd.yml`，`continue-on-error: false`)
5. **Alembic Migration** (`upgrade head` → `alembic check` → `downgrade base` → `upgrade head`)
6. **Unit & Integration Tests** (Linux/Windows unit，Linux integration)
7. **Windows E2E Tests** (`tests/e2e/`，Chromium + PostgreSQL)
8. **Per-File (≥ 80%) & Overall Coverage (≥ 85%)**
9. **requirements*.txt 漂移处理** (`requirements-drift` job 检测到 main 分支漂移时，由 `update-requirements` job 创建同步 PR)

发布流程: 打 `v*.*.*` tag → 触发 `build-windows` job → PyInstaller 打包 CPU/CUDA 两个变体 → smoke test → Inno Setup 制作安装包 → GitHub Release 发布。

**其他 workflow**: CodeQL 静态安全分析 (`codeql.yml`)、密钥泄露扫描 (`gitleaks.yml`)、自动化 Release PR (`release-please.yml`)、依赖更新机器人 (`renovate.yml`)、OpenSSF Scorecard 安全评分 (`scorecard.yml`)。

### Pre-commit Hooks

本项目使用 7 个 pre-commit hook (定义在 `.pre-commit-config.yaml`，含 Ruff lint/format、裸 `type: ignore` 检测、requirements 同步、版本一致性校验)，提交前必须全部通过。

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链

## 语言切换响应 (I18n Hot Reload)

> 对应 [CLAUDE.md §5.8](./CLAUDE.md#58-语言切换响应规范-i18n-hot-reload)。

> ⚠️ **本节为附录 A：命令式存量整改对照，仅供改造期查阅，不作为新代码依据。**
> 新 UI 必须采用声明式 `@ft.component` + `use_state`/`use_effect`（locale 作为状态源自动重渲染），详见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范)。本节描述的 `I18n.subscribe`/`refresh_locale`/手动 `update()` 等命令式写法属技术债，存量改造后随之删除。

程序运行后动态切换语言时，所有 UI 控件必须正确刷新。新增/修改 UI 视图或组件时，必须遵守以下 9 条规范。

### 判定决策树

按顺序自问，命中则需遵守对应子规范：

```
我的视图/组件展示 I18n.get() 文案吗？
├─ 是 → 规范 1（订阅机制）+ 规范 9（异常降级与判空）
├─ 有 ft.Dropdown 且 options 含 I18n.get() 文案吗？
│   └─ 是 → 规范 4（options 重建保留 value）
├─ 有内联 ft.Text/ft.IconButton 使用 I18n.get() 吗？
│   └─ 是 → 规范 5（实例属性提取）
├─ 含子 panel/子组件吗？
│   ├─ 是 → 规范 6（子组件级联与状态保留）
│   └─ 子组件是否满足「永久子组件模式豁免」三条件？
│       └─ 是 → 子组件可不自行订阅（规范 1 例外），父级联调用其 update_locale
├─ 是延迟挂载或缓存的视图吗？（_view_cache/tab_contents）
│   └─ 是 → 规范 7（生命周期兜底）
├─ 使用 MetaDataManager.get_table_alias/get_column_alias 吗？
│   └─ 是 → 规范 8（缓存失效）
└─ refresh_locale 需要重算数据吗？
    └─ 是 → 规范 3（纯 UI 操作，复用 VM 缓存）
```

### 9 条规范

1. **订阅机制**：展示 i18n 文案的 View/Component 必须在 `did_mount` 中调用 `I18n.subscribe(callback)` 并将返回的 `subscription_id` 保存为实例属性（如 `self._locale_subscription_id`），在 `will_unmount` 中调用 `I18n.unsubscribe(subscription_id)`。`subscribe` 默认 `sync_immediately=True`，订阅时立即触发一次回调，保证延迟挂载组件用当前 locale 渲染。

   **例外（永久子组件模式豁免）**：若子组件同时满足以下三个条件，可不自行订阅 locale 变更，由父视图级联调用其 `update_locale`/`refresh_locale` 完成刷新：
   - (1) 子组件在父 `__init__` 中创建，整个生命周期不被重建；
   - (2) 父 `refresh_locale`/`_on_locale_change` 显式级联调用子组件的 `update_locale`/`refresh_locale`；
   - (3) 子组件不会被独立挂载（如不在页面顶层独立使用）。

   **不适用本豁免的场景**：
   - 若父视图在 `refresh_locale` 中重建子组件（如 `OnboardingWizard`/`AIBrainTab` 的重建子 panel 模式），必须遵守规范 6（旧 panel `will_unmount` + 新 panel 订阅或父级联），不适用本豁免。
   - 本豁免仅针对规范 1（订阅机制）。子组件的 `update_locale`/`refresh_locale` 实现仍必须遵守规范 4（options 重建）、规范 5（实例属性提取）、规范 8（MetaDataManager 缓存失效）、规范 9（异常降级与判空）。
   - **不确定是否满足豁免条件时，默认订阅。**

   适用本豁免的组件清单：`NewsFeed`、`MarketDashboard`、`BacktestConfigPanel`、`BacktestResultPanel`、`SQLConsoleTab`、`TableViewerTab`、`HealthScoreCard`/`MetricTile`/`KeyMetricsGrid`/`CoverageDetailTable`（health_report_dialog 内子组件）、`DashboardCard`/`SettingRow`/`MetricCard`/`ActionChip`/`StatusBadge`/`SectionHeader`（settings_widgets 哑组件）。

2. **回调命名与签名**：回调方法统一命名为 `refresh_locale`（视图层）或 `_on_locale_change`（已在用的旧组件可保留），**方法签名无参数**（`def refresh_locale(self):` / `def _on_locale_change(self):`）。`I18n.subscribe` 是零参调用，带参签名中的 `new_locale` 参数永远是默认值（死参数）。

3. **纯 UI 操作**：`refresh_locale` 必须是纯 UI 操作，**禁止网络 IO / 数据库查询**；只能刷新控件字段值（`text`/`label`/`tooltip`/`hint_text`/`options` 等）。需要重算的数据（如日期格式化）应复用 ViewModel 缓存，不触发新请求。

4. **下拉框 options 同步**：所有 `ft.Dropdown` 的 `options` 中如果包含 `I18n.get()` 翻译文案，`refresh_locale` 必须重建 `options` 列表（**保留当前 `value`**），不能只刷新 `label`。枚举型 options（如主题、日志级别、积分档位）必须重建；语言选择器 options 也需重建以保持一致性。

   **⚠️ 必须使用 `refresh_dropdown_options()` 工具函数（Flet 已知坑）**：V1 改用 `Prop` 描述符（V0 的 `_set_attr_internal` 已删除），`Prop.__set__` 在 `old == value` 时跳过赋值（值相等优化）。在批量 `page.update()` 中，`value` 从 X→None→X 的最终值等于原值，前端只收到最终值，`DropdownButton` 不触发 rebuild，闭合态选中项显示文本不刷新。

   必须使用 `ui.i18n.refresh_dropdown_options(dropdown, new_options)` 工具函数，它通过分两步 `control.update()` 解决：

   ```python
   from ui.i18n import refresh_dropdown_options

   # 正确写法：使用工具函数
   refresh_dropdown_options(self.theme_dropdown, [
       ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),
       ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),
   ])
   ```

   原理：工具函数内部先提交 `value=None` + 新 `options`，通过 `control.update()` 立即发送到前端（清除选中项显示）；再提交 `value=saved`，再次 `control.update()` 使前端用新 options 的 text 更新显示。`control.update()` 未挂载时抛 `RuntimeError`（V1）/ `AttributeError`（V0 兼容），被工具函数 catch 后属性仍标记 dirty，后续 `page.update()` 兜底。

   **边界情况**：当 `saved_value` 为 `None`（用户未选中）时，闭合态本就无文本需刷新，但套用本工具函数也无副作用；当 `saved_value` 为空字符串 `""` 时本方案失效（两次赋值均被短路），实际项目 option key 应非空，若出现需单独处理。

   **禁止的手动写法**（以下写法在批量 `page.update()` 中无效）：

   - `dropdown.value = dropdown.value`（自赋值）
   - `saved = dropdown.value; dropdown.value = None; ...; dropdown.value = saved`（saved_value 模式，不调用 `control.update()` 时无效）

5. **实例属性提取**：内联在 `ft.Row`/`ft.Column` 中且 `tooltip`/`text` 来自 `I18n.get()` 的控件（如 `ft.IconButton`、`ft.Text`），必须提取为实例属性（`self.xxx_btn`/`self.xxx_text`），否则 `refresh_locale` 无法引用。`MetricCard`/`ActionChip`/`StatusBadge` 等复合组件通过 `set_label`/`set_text` 方法刷新。

6. **子组件级联与状态保留**：父视图的 `refresh_locale` 必须级联调用子组件的 `refresh_locale`（若存在）或 `_on_locale_change`。包含子 panel 的视图（如 `OnboardingWizard`、`AIBrainTab`、`BacktestView`）在重建子 panel 前必须先调用旧 panel 的 `will_unmount`，取消其 I18n 订阅避免泄漏；**重建后必须恢复旧 panel 的用户输入状态**（如表单值、选中项），避免用户输入丢失。

7. **生命周期兜底**：延迟挂载或缓存的视图（如 `AppLayout._view_cache`、`SettingsView.tab_contents`）可能在未挂载时错过 I18n 通知。切换到此类视图时（`_on_tab_click`/`_execute_tab_switch`）必须显式调用 `refresh_locale` 兜底，确保文案与当前 locale 一致。

8. **MetaDataManager 缓存失效**：涉及表/列别名（`get_table_alias`/`get_column_alias`）的视图（如 `DataExplorerView`、`ScreenerView`），`refresh_locale` 必须先调用 `MetaDataManager.invalidate_cache()` 让别名缓存失效，再重建表头/列定义。

9. **异常降级与判空**：`refresh_locale` 与子组件的 `_on_locale_change` 整个方法体必须用 `try/except` 包裹（捕获 `Exception`，`CancelledError` 按 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止) 传播），异常降级为 `logger.warning`，不得抛出。末尾必须用 `if self.page: self.update()` 判空更新，避免组件未挂载时抛异常。

### 反模式（禁止）

- ❌ 在 `refresh_locale` 中调用网络请求 / 数据库查询 / 重型 CPU 计算
- ❌ 只刷新 `Dropdown.label` 而不重建 `options`
- ❌ 重建 `options` 时不保留当前 `value`
- ❌ `dropdown.value = dropdown.value` 自赋值刷新（被 V1 `Prop.__set__` 值相等优化短路，无效）
- ❌ 手动 `value = None; options = [...]; value = saved` 不调用 `control.update()`（批量 `page.update()` 只发送最终值，闭合态选中项文本不刷新）
- ❌ 不使用 `refresh_dropdown_options()` 工具函数重建含 i18n 文案的 Dropdown options
- ❌ 重建子 panel 前不调用旧 panel 的 `will_unmount`
- ❌ 重建子 panel 后不恢复用户输入状态（如表单值、选中项）
- ❌ 内联 `ft.Text(I18n.get(...))` 不提取为实例属性，导致无法刷新
- ❌ 视图未订阅 I18n，依赖父视图级联调用（除满足规范 1「永久子组件模式豁免」三条件的组件外，如 `SQLConsoleTab`/`TableViewerTab`/`NewsFeed`/`MarketDashboard`/`BacktestConfigPanel`/`BacktestResultPanel`）
- ❌ 延迟挂载视图切换时不调用 `refresh_locale` 兜底
- ❌ 涉及表/列别名的视图未调用 `MetaDataManager.invalidate_cache()` 就重建表头
- ❌ `refresh_locale` 未用 `try/except` 包裹或末尾未判空 `self.page`

### 标准 View 模板

包含订阅、Dropdown options 重建、try/except、判空：

```python
import logging

import flet as ft

from ui.i18n import I18n, refresh_dropdown_options

logger = logging.getLogger(__name__)


class MyView(ft.Container):
    def __init__(self, page):
        super().__init__()
        self.app_page = page
        self.expand = True
        self._locale_subscription_id: object | None = None

        # 提取为实例属性（规范 5）
        self.title_text = ft.Text(I18n.get("my_view_title"))
        self.action_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip=I18n.get("my_view_refresh"),
            on_click=self._on_action,
        )

        # Dropdown（规范 4：options 含 I18n 文案）
        self.size_dropdown = ft.Dropdown(
            label=I18n.get("my_view_page_size"),
            value="20",
            options=[
                ft.dropdown.Option("10", text=f"10 {I18n.get('unit_per_page')}"),
                ft.dropdown.Option("20", text=f"20 {I18n.get('unit_per_page')}"),
                ft.dropdown.Option("50", text=f"50 {I18n.get('unit_per_page')}"),
            ],
        )

        self.content = ft.Column([self.title_text, self.size_dropdown, self.action_btn])

        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    def _on_mount(self):
        # 规范 1：订阅并保存 subscription_id
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def _on_unmount(self):
        # 规范 1：取消订阅
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    async def _on_action(self, e: ft.ControlEvent):
        """UI 按钮点击事件处理示例"""
        # [防阻塞规范 (CLAUDE.md R16)] 
        # Flet 支持 async def 事件处理器。对于耗时的 IO/CPU 操作，必须提交到 ThreadPoolManager，避免阻塞 UI 渲染。
        from utils.thread_pool import ThreadPoolManager, TaskType
        
        self.action_btn.disabled = True
        self.update()
        
        try:
            # 耗时操作必须抛到外部线程池执行，例如：
            # result = await ThreadPoolManager.run_async(TaskType.IO, my_service.fetch_data)
            pass
        except Exception as err:
            logger.error("Action failed", exc_info=True)
            # 处理错误并展示 toast...
        finally:
            self.action_btn.disabled = False
            if self.page:  # [防崩溃规范] 异步结束更新UI前必须判空，防止组件在等待期间已被卸载
                self.update()

    def refresh_locale(self):
        # 规范 9：整个方法体 try/except 包裹
        try:
            # 规范 5：刷新实例属性
            self.title_text.value = I18n.get("my_view_title")
            self.action_btn.tooltip = I18n.get("my_view_refresh")

            self.size_dropdown.label = I18n.get("my_view_page_size")
            # 规范 4：使用 refresh_dropdown_options 重建 options（分步 control.update() 强制刷新）
            try:
                per_page = I18n.get("unit_per_page")
                refresh_dropdown_options(
                    self.size_dropdown,
                    [ft.dropdown.Option(k, text=f"{k} {per_page}") for k in ("10", "20", "50")],
                )
            except Exception as rebuild_err:
                logger.debug(f"[MyView] size_dropdown options rebuild skipped: {rebuild_err}")

            # 规范 6：级联子组件（若有）
            # child_refresh = getattr(self.child_panel, "refresh_locale", None)
            # if callable(child_refresh):
            #     child_refresh()

            # 规范 9：判空更新
            if self.page:
                self.update()
        except Exception as e:
            logger.warning(f"[MyView] refresh_locale failed: {e}")
```

### 重建子 panel 保留用户输入（规范 6 实战要点）

```python
def _rebuild_steps_after_locale_change(self):
    """语言切换后重建子面板，保留用户输入状态"""
    # 1. 取消旧面板的 I18n 订阅
    for panel_attr in ("database_panel", "tushare_panel"):
        old_panel = getattr(self, panel_attr, None)
        if old_panel and hasattr(old_panel, "will_unmount"):
            try:
                old_panel.will_unmount()
            except Exception as e:
                logger.debug("Panel cleanup failed: %s", e)

    # 2. 重建前提取旧面板的用户输入
    saved_enabled = getattr(self.schedule_enabled, "value", True) if hasattr(self, "schedule_enabled") else True
    saved_time = getattr(self.schedule_time, "value", None) if hasattr(self, "schedule_time") else None

    # 3. 创建新面板
    self._init_schedule_controls()

    # 4. 恢复用户输入
    if hasattr(self, "schedule_enabled"):
        self.schedule_enabled.value = saved_enabled
    if hasattr(self, "schedule_time") and saved_time is not None:
        self.schedule_time.value = saved_time
```

### 测试要求（语言切换场景必须在用例中覆盖）

- 订阅/取消订阅配对测试（`did_mount` 订阅 + `will_unmount` 取消）
- `refresh_locale` 异常降级测试（mock I18n.get 抛异常，断言不抛出 + `logger.warning` 被调用）
- Dropdown options 重建后 `value` 保留断言
- 重建子 panel 后用户输入状态保留断言
- 涉及 MetaDataManager 的视图必须断言 `invalidate_cache()` 被调用

## 响应式布局规范 (Responsive Layout)

> 对应 [CLAUDE.md §5.9](./CLAUDE.md#59-响应式布局规范-responsive-layout)。

> ⚠️ **本节为附录 B：命令式存量整改对照，仅供改造期查阅，不作为新代码依据。**
> 新 UI 必须采用声明式 `@ft.component` + `use_state`（窗口尺寸作为 state + `ResponsiveRow` 状态驱动布局），详见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范)。本节描述的 `handle_resize` 鸭子分发/手动 `update()` 等命令式写法属技术债，存量改造后随之删除。

本规范确保应用在 1280px (最小窗口) 到 4K (3840px) 的各种分辨率下均能提供良好体验。新增/修改 UI 视图或组件时必须遵守以下 9 条规范。

### 背景与约束

- 项目仅桌面端 (Flet 0.85.3)，`main.py` 设置 `page.window.min_width = 1280`、`min_height = 720`、默认 `width = 1280`。
- 内容区净宽 = 窗口宽度 − nav_rail (展开 180 / 折叠 80) − divider (1) − body padding (40)。
- `AppLayout` 已实现 `page.on_resize` 的 100ms 防抖分发：通过鸭子类型调用 `current_view.handle_resize(width, height)` (见 `ui/app_layout.py` 的 `_handle_resize`)。
- **Flet 0.85.3 API 关键约束**：
  - 正确的事件属性名为 `page.on_resize`（不带 d），**不是** `on_resized`。
  - `WindowResizeEvent` 携带实时 `width` / `height` 属性。
  - `page.width` / `page.window.width` 仅在页面连接时 (`fetch_page_details_async`) 更新一次，**resize 事件中不会刷新**，返回过时值或 0。因此 `handle_resize` 必须通过参数接收实时尺寸，**禁止**在 `handle_resize` 内读取 `self.page.width`。
- Web 模式 (`_is_web_mode()`) 下跳过窗口约束，但 resize 分发机制仍然生效。

### 断点分级表

基于 `handle_resize` 接收的 `width` 参数 (来自 `WindowResizeEvent.width`) 的 4 级断点，新增到 `AppStyles` (见 `ui/theme.py`)：

| 断点常量 | 阈值 (px) | 典型场景 | 内容区净宽 (nav 展开) |
|---------|-----------|---------|---------------------|
| `BREAKPOINT_COMPACT` | `< 1200` | ⚠️ `min_width=1280` 下不可达 | 979 ~ 1059 |
| `BREAKPOINT_STANDARD` | `1200 ~ 1599` | 默认窗口 1280，1080p | 1059 ~ 1379 |
| `BREAKPOINT_WIDE` | `1600 ~ 2399` | 2K 显示器 | 1379 ~ 2179 |
| `BREAKPOINT_ULTRA_WIDE` | `≥ 2400` | 4K / 带鱼屏 | ≥ 2179 |

> **注意**：Flet 内置 `ResponsiveRow` 断点 (xs<576, sm≥576, md≥768, lg≥992, xl≥1200) 在 `min_width=1280` 约束下，xs/sm/md 基本触发不到，实际有效的是 lg/xl。本项目断点常量用于 `handle_resize()` 中的条件判断，与 `ResponsiveRow` 的 col 配置互补。
>
> # NOTE(lazy): BREAKPOINT_COMPACT (< 1200) 在 min_width=1280 下不可达. ceiling: 窗口宽度恒 ≥ 1280. upgrade: 调整断点阈值或合并 compact 到 standard 时需同步更新测试边界值.
>
> **设计选择**：断点基于 `WindowResizeEvent.width`（窗口总宽度）而非内容区净宽，这是有意的设计。同一窗口宽度下 nav 折叠/展开会改变内容区净宽，但断点保持稳定，避免 nav 切换导致侧栏宽度跳变。nav 折叠带来的额外空间由 `expand=True` 的主内容区自然吸收。

### 9 条规范

#### 规范 1：断点分级 — 视图必须感知当前窗口尺寸

新增视图时，在 `handle_resize(width, height)` 中通过 `width` 参数 (来自 `WindowResizeEvent.width`) 读取实时窗口宽度，对照断点表调整布局。**禁止**读取 `self.page.width` (仅连接时更新，resize 时返回过时值)。

```python
# ui/theme.py — AppStyles 类中新增
BREAKPOINT_COMPACT = 1200      # < 此值视为紧凑模式
BREAKPOINT_STANDARD = 1600     # < 此值视为标准模式
BREAKPOINT_ULTRA_WIDE = 2400   # ≥ 此值视为超宽屏

@staticmethod
def get_breakpoint(page_width: int | None) -> str:
    """返回当前断点级别: 'compact' | 'standard' | 'wide' | 'ultra_wide'。"""
    if page_width is None or page_width < AppStyles.BREAKPOINT_COMPACT:
        return "compact"
    if page_width < AppStyles.BREAKPOINT_STANDARD:
        return "standard"
    if page_width < AppStyles.BREAKPOINT_ULTRA_WIDE:
        return "wide"
    return "ultra_wide"
```

#### 规范 2：侧栏动态宽度 — 禁止固定像素宽度

包含侧栏 (sidebar) 的视图 (如 `BacktestView`、`ScreenerView`) 必须根据断点动态计算侧栏宽度，禁止硬编码固定值。

| 断点 | 侧栏宽度 | 理由 |
|------|---------|------|
| compact (<1200) | 280px | ⚠️ 不可达（min_width=1280）；参考：979px 内容区，侧栏 280px 后主区 699px |
| standard (1200~1599) | 340px | 内容区 ~1059px，主区保留 707px |
| wide/ultra_wide (≥1600) | 380px | 内容区充足，侧栏可放宽 |

```python
# ui/theme.py — AppStyles 类中新增
SIDEBAR_WIDTH_COMPACT = 280
SIDEBAR_WIDTH_STANDARD = 340
SIDEBAR_WIDTH_WIDE = 380

@staticmethod
def get_sidebar_width(page_width: int | None) -> int:
    """根据窗口宽度返回合适的侧栏宽度。"""
    breakpoint = AppStyles.get_breakpoint(page_width)
    if breakpoint == "compact":
        return AppStyles.SIDEBAR_WIDTH_COMPACT
    if breakpoint == "standard":
        return AppStyles.SIDEBAR_WIDTH_STANDARD
    return AppStyles.SIDEBAR_WIDTH_WIDE
```

#### 规范 3：handle_resize 实现 — 所有视图必须实现

`AppLayout` 已通过鸭子类型调用 `current_view.handle_resize()`。**所有视图必须实现此方法**，即使为空方法也必须存在并注释 `# No responsive adjustment needed`，以表明已评估响应式需求。这与 I18n 规范"所有展示文案的视图必须订阅"对称。

以下布局类型的视图必须在 `handle_resize` 中实现实际逻辑：

- 左右分栏 (如 config_panel + result_panel) → 动态调整侧栏宽度
- 侧栏显隐切换 (如 history_tree) → 刷新主内容区
- 表格视口刷新 (如虚拟表格) → 重新计算可见行数
- 图表/数据密集区域 → 根据高度调整最小高度或可见行数
- 任何依赖窗口尺寸的动态布局

**标准模板**：

```python
def handle_resize(self, width: float = 0, height: float = 0) -> None:
    """窗口尺寸变化时调整布局。由 AppLayout 防抖后调用 (约 100ms)。

    Args:
        width: 当前窗口宽度 (来自 WindowResizeEvent.width)，0 表示未知
            (如 nav 折叠触发的内部 resize，此时视图应保留当前布局)
        height: 当前窗口高度 (来自 WindowResizeEvent.height)，0 表示未知
    """
    if not width:
        # width=0 表示是 nav 折叠等内部触发，无新尺寸；保留当前布局即可
        return
    try:
        new_sidebar = AppStyles.get_sidebar_width(width)
        # 仅在宽度实际变化时更新，避免无意义 refresh
        sidebar_container = self._get_sidebar_container()  # 视图自行实现获取逻辑
        if sidebar_container.width != new_sidebar:
            sidebar_container.width = new_sidebar
            sidebar_container.update()  # 局部更新，不用 self.update()
    except Exception as e:
        logger.debug("[%s] handle_resize skipped: %s", self.__class__.__name__, e)
```

**性能约束**（必须遵守）：

- **禁止重建 content**：`handle_resize` 内禁止 `self.content = self._build_content()`，只能修改已有控件属性 (如 `.width`、`.visible`)。重建 content 会销毁所有子控件状态 (滚动位置、输入值、选中态)。
- **禁止 IO/CPU 密集操作**：`handle_resize` 在 resize 防抖后同步调用，阻塞 UI 线程。禁止查询数据库、加载文件、执行策略计算。
- **局部更新优先**：仅调用发生变化的子控件的 `.update()`，避免 `self.update()` 触发整树 diff。复杂视图 (如 BacktestView 含图表+表格) 尤其重要。
- **幂等性**：相同 `page.width` 多次调用必须产生相同结果，`handle_resize` 不得有副作用 (如计数器自增、状态修改)。

**已实现**：`ScreenerView.handle_resize` (刷新表格视口)。
**待补实现**：`BacktestView`、`DataExplorerView`、`HomeView` (空方法)、`TaskCenterView` (空方法)、`SettingsView` (空方法)。

#### 规范 4：控件宽度策略 — expand 优先，禁止裸硬编码

| 场景 | 允许 | 禁止 |
|------|------|------|
| 容器内自适应控件 | `expand=True` | `width=200` 等裸数字 |
| 侧栏内表单控件 | `AppStyles.COL_HALF` / `COL_FULL` 配合 `ResponsiveRow` | `width=AppStyles.CONTROL_WIDTH_MD` |
| Dialog / 弹窗内控件 | `AppStyles.CONTROL_WIDTH_LG` 等常量 | 裸数字 (但 Dialog 场景尺寸相对固定，可放宽) |
| 图标/进度条等装饰性元素 | 固定小尺寸 (如 `width=20`) | — |

**例外**：`AppStyles.CONTROL_WIDTH_*` 常量可用于**顶层全宽 Row** 中的独立控件——即该 Row 不嵌套在任何固定/动态宽度容器 (侧栏、卡片面板) 内，宽度由 body 内容区直接决定。例如 `BacktestView` 的 `strategy_dropdown` 位于左右分栏之上的全宽行，`width=CONTROL_WIDTH_LG` 合理。嵌套在侧栏内的 Row 不属于顶层全宽 Row。

#### 规范 5：ResponsiveRow 强制配置 col — 禁止无 col 的 ResponsiveRow

`ft.ResponsiveRow` 必须为每个子 `Column`/`Container` 指定 `col` 参数，使用 `AppStyles.COL_*` 常量。

```python
# ✅ 正确
ft.ResponsiveRow(
    [ft.Column([control], col=AppStyles.COL_HALF)],
)

# ❌ 错误 — 退化为纵向 Column，浪费横向空间
ft.ResponsiveRow([ft.Column([control])])
```

**侧栏 (固定/动态宽度容器) 内**：只能使用 `COL_HALF` 或 `COL_FULL`，禁止 `COL_THIRD`/`COL_QUARTER` (侧栏宽度不足以支撑 3+ 列)。

#### 规范 6：scroll 兜底 — 作为最后防线而非主要手段

- 工具栏等横向密集区域应在 `ft.Row` 上设置 `scroll=ft.ScrollMode.AUTO` 作为兜底。
- 但 **scroll 不得作为掩盖布局缺陷的手段**：若控件累计宽度经常超过容器宽度，应优先改用 `ResponsiveRow` 或 `wrap=True`。
- `scroll=ft.ScrollMode.AUTO` 的 `ft.Column` 必须设置 `padding=ft.Padding.only(right=8)` 避免内容与滚动条重叠。

#### 规范 7：触发时机完整性 — 任何改变内容区宽度的操作都必须分发 resize

`AppLayout._handle_resize()` 是 resize 事件的唯一分发入口。**任何改变内容区可用宽度的操作**，不仅是窗口拖拽，都必须触发 `schedule_resize()` 分发，否则视图会基于过时的 `page.width` 渲染。

当前状态：

| 操作 | 状态 | 说明 |
|------|------|------|
| 窗口拖拽 | ✅ 已修复 | `page.on_resize` → `schedule_resize(width, height)` → 100ms 防抖 → `handle_resize(width, height)` |
| **nav_rail 折叠/展开** (`_toggle_nav`) | ✅ 已修复 | `_toggle_nav` 末尾调用 `schedule_resize()` (复用缓存尺寸) |
| **tab 切换挂载新视图** | ✅ 已修复 | `_execute_tab_switch` 中 `refresh_locale` 后追加 `handle_resize` 兜底 |
| **i18n 语言切换** | ✅ 已修复 | `_on_locale_change` 末尾调用 `schedule_resize()` 重新验证布局 |
| 侧栏显隐切换 (如 `ScreenerView` history_tree) | 视图内部处理 | 视图内部处理可接受 |

**实现方式** (已完成)：

```python
# main.py — on_resize 回调，传递实时尺寸
async def _on_resize(e):
    # ...
    width = getattr(e, "width", 0) or 0
    height = getattr(e, "height", 0) or 0
    layout.schedule_resize(width, height)

page.on_resize = _on_resize  # 注意：是 on_resize (不带 d)

# ui/app_layout.py — _toggle_nav 末尾
def _toggle_nav(self, e):
    # ... 现有折叠/展开逻辑 ...
    self.schedule_resize()  # 复用缓存尺寸，不传新参数

# ui/app_layout.py — _execute_tab_switch 中，refresh_locale 之后
if hasattr(new_view, "handle_resize"):
    new_view.handle_resize(self._current_width, self._current_height)

# ui/app_layout.py — _on_locale_change 末尾
self.schedule_resize()  # 语言切换后重新验证布局
```

#### 规范 8：高度维度 — 对高度敏感的视图必须响应 `page.height`

规范 1-7 仅关注宽度，但 `min_height=720` 下多个视图存在高度维度问题：

- `BacktestView` 配置面板内容溢出 (scroll 兜底，但用户需大量滚动)
- `DataExplorerView` 表格每页行数固定，低高度下只能看到 3-4 行
- 图表区在低高度下被压缩到不可读

**要求**：对高度敏感的视图 (含图表、表格、长表单) 必须在 `handle_resize` 中同时处理 `height` 参数：

```python
def handle_resize(self, width: float = 0, height: float = 0) -> None:
    if not width:
        return
    try:
        # 宽度维度
        new_sidebar = AppStyles.get_sidebar_width(width)
        if self._sidebar_container.width != new_sidebar:
            self._sidebar_container.width = new_sidebar
            self._sidebar_container.update()
        # 高度维度 (对高度敏感的视图)
        if height and height < 720:  # 紧凑高度
            self._table_page_size = 10  # 减少可见行数
        elif height:
            self._table_page_size = 20
    except Exception as e:
        logger.debug("[%s] handle_resize skipped: %s", self.__class__.__name__, e)
```

**判定标准**：视图包含以下元素之一即为"高度敏感"：
- `ft.DataTable` / 虚拟表格 (行数受高度影响)
- 图表组件 (最小可读高度)
- 超过 5 个表单项的长表单 (低高度下需折叠或分页)

#### 规范 9：i18n 与响应式交互 — 语言切换后必须重新验证布局

语言切换后文案长度变化 (中文"调仓频率"4 字 vs 英文"Rebalance Frequency"18 字符)，可能导致之前不溢出的布局突然溢出。

**要求**：`refresh_locale()` 完成 content 重建后，若视图实现了 `handle_resize`，必须一并调用以重新验证布局。

**实现方式** (已完成)：在 `AppLayout` 的 i18n 订阅回调中，`refresh_locale` 之后追加 `schedule_resize`：

```python
# ui/app_layout.py — _on_locale_change 末尾
def _on_locale_change(self):
    # ... 现有 refresh_locale 逻辑 ...
    # 语言切换后文案长度变化可能导致布局溢出，触发 resize 重新验证布局
    self.schedule_resize()  # 复用缓存尺寸
```

> **注意**：`refresh_locale` 重建 content 后控件树变化，`handle_resize` 中对控件属性的引用必须基于新 content。若 `handle_resize` 通过索引访问控件 (如 `self.content.controls[4].controls[0]`)，需确保索引在重建后仍然有效。推荐在视图上保存控件引用 (如 `self._sidebar_container`) 而非依赖索引。

### 判定决策树

按顺序自问，命中则需遵守对应子规范：

```
我的视图/组件包含布局控件吗？
├─ 有左右分栏 (sidebar + main) 吗？
│   └─ 是 → 规范 2 (侧栏动态宽度) + 规范 3 (handle_resize)
├─ 有 ft.ResponsiveRow 吗？
│   └─ 是 → 规范 5 (强制配置 col)
├─ ResponsiveRow 在侧栏/固定宽度容器内吗？
│   └─ 是 → 只用 COL_HALF / COL_FULL (规范 5)
├─ 有硬编码 width=数字 的控件吗？
│   └─ 是 → 改为 expand=True 或 AppStyles 常量 (规范 4)
├─ 有 scroll=ft.ScrollMode.AUTO 的 Column 吗？
│   └─ 是 → 设置 padding=ft.Padding.only(right=8) (规范 6)
├─ 有改变内容区宽度的操作 (nav 折叠、tab 切换) 吗？
│   └─ 是 → 规范 7 (触发时机完整性)
├─ 含表格/图表/长表单等高度敏感元素吗？
│   └─ 是 → 规范 8 (高度维度)
├─ 视图展示 i18n 文案吗？
│   └─ 是 → 规范 9 (i18n 与响应式交互)
└─ 视图在 960×640 最小窗口下验证过吗？
    └─ 是 → 确认无溢出、无截断、无重叠 (规范 1 断点验证)
```

### 标准 View 检查清单

新增/修改视图时，对照此清单逐项确认：

- [ ] 视图在 1280×720 最小窗口下无横向溢出、无纵向截断 (内容区净宽 ~1059px)
- [ ] 视图在 1280×800 默认窗口下布局合理 (内容区净宽 ~1059px)
- [ ] 视图在 1920×1080 宽屏下不出现内容过度拉伸
- [ ] 视图已实现 `handle_resize()` (含分栏布局的须有实际逻辑，纯纵向的须有空方法)
- [ ] `handle_resize` 遵守性能约束 (无重建 content、无 IO、局部更新、幂等)
- [ ] 侧栏宽度通过 `AppStyles.get_sidebar_width()` 动态计算，非硬编码
- [ ] 所有 `ResponsiveRow` 子元素已配置 `col` 参数 (使用 `AppStyles.COL_*` 常量)
- [ ] 侧栏内的 `ResponsiveRow` 只使用 `COL_HALF` 或 `COL_FULL`
- [ ] 无裸 `width=数字` 硬编码 (Dialog 场景除外)
- [ ] `scroll=ft.ScrollMode.AUTO` 的 Column 已设置右侧 padding 避让滚动条
- [ ] 控件在 `refresh_locale()` 重建 content 后，Container 层级样式 (bgcolor/border 等) 不丢失
- [ ] `handle_resize` 中控件引用基于实例属性 (如 `self._sidebar_container`)，不依赖 content 索引

### 现有视图合规状态 (截至 2026-06-29)

| 视图 | 合规状态 | 待修复项 |
|------|---------|---------|
| `HomeView` | ⚠️ 部分 | 未实现 `handle_resize` (需补空方法) |
| `ScreenerView` | ⚠️ 部分 | 参数面板 3×`width=200` 无 wrap；已实现 `handle_resize` |
| `BacktestView` | ❌ 不合规 | `expand=1/2` 比例分栏；Slider `width=200` 硬编码；未实现 `handle_resize` |
| `DataExplorerView` | ⚠️ 部分 | 工具栏累计 ~840px 硬编码 (有 scroll 兜底)；新闻 cell `width=400` 反模式；未实现 `handle_resize`；高度敏感未处理 |
| `TaskCenterView` | ⚠️ 部分 | 双重 padding (body 20px + view 20px)；未实现 `handle_resize` (需补空方法) |
| `SettingsView` | ⚠️ 部分 | 未实现 `handle_resize` (需补空方法) |
| `MarketDashboard` | ❌ 不合规 | `ResponsiveRow` 无 col 配置，4 张卡退化为纵向堆叠 |
| `OnboardingWizard` | ❌ 不合规 | `ResponsiveRow` 无 col 配置，6 张卡纵向堆叠 |
| `AppLayout` | ✅ 已修复 | `on_resize` 事件注册正确；`schedule_resize` 缓存并传递实时尺寸；`_toggle_nav` 触发 resize；tab 切换有 `handle_resize` 兜底；i18n 回调触发 resize |

### 测试要求

新增/修改视图的响应式布局时，必须编写以下测试：

1. **断点函数单元测试**：`AppStyles.get_breakpoint()` 和 `get_sidebar_width()` 的边界值覆盖 (1279/1280/1199/1200/1599/1600/2399/2400/None)。
2. **handle_resize 单元测试**：调用 `handle_resize(width, height)` 传入各断点值 (1279/1280/1199/1200/1599/1600/2399/2400)，断言侧栏 Container 的 `width` 属性变化正确；断言相同参数多次调用结果不变 (幂等性)；断言 `width=0` 时提前返回不修改布局。
3. **handle_resize 性能约束测试**：mock 后断言 `handle_resize` 内未调用 `self.content = ...` (重建 content)、未调用数据库/文件 IO 方法。
4. **handle_resize 异常降级测试**：mock 控件引用为 None 或抛异常，断言 `handle_resize` 不抛出 (降级为 `logger.debug`)。
5. **空方法验证**：纯纵向视图 (HomeView 等) 必须断言 `hasattr(view, "handle_resize")` 为 True，即使为空方法。
6. **1280×720 最小窗口布局验证** (手工或 E2E)：确认无横向溢出、无控件截断、无滚动条与控件重叠。

## 标准开发工作流 (How-To)

### 1. 新增一张数据表

1. 在 `data/persistence/models.py` 中添加 SQLAlchemy ORM 模型 (继承 `Base`)。
2. 在 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 中注册：表名 → 同步配置、质量监控配置、依赖关系。
3. 运行 `python -m alembic revision --autogenerate -m "add xxx table"`，**人工检查** 生成的迁移文件。
4. 运行 `python -m alembic upgrade head` 验证。
5. 若需要 DAO 访问，参考[新增一个 DAO](#2-新增一个-dao)。

### 2. 新增一个 DAO

1. 在 `data/persistence/daos/` 下创建 `xxx_dao.py`，继承 `BaseDao`。
2. 实现读写方法，**只用** `_read_db_select` / `_save_upsert` / `chunked_in_query`，禁止裸 SQL 字符串拼接。
3. 在 `data/cache/cache_manager.py` 的 `CacheManager.__init__` 中实例化：`self.xxx_dao = XxxDao(self.engine)`。
4. 在 `CacheManager._create_engine` 中更新 `.engine` 引用：`self.xxx_dao.engine = self.engine`。
5. 在 `tests/unit/` 下编写对应单测，使用 mock engine 隔离 DB。

### 3. 新增一个策略

1. 在 `strategies/` 下创建 `xxx_strategy.py`。
2. 使用 `@register_strategy("key")` 装饰器注册；继承 `BaseStrategy` (普通) 或 `PolarsBaseStrategy` (向量化)。
3. 声明 `required_context_keys` / `required_tables` / `required_history_days`。
4. 若需访问 LLM，使用 `AIStrategyMixin` 混入；Prompt 添加到 `strategies/strategy_prompts.py`。继承 `PolarsBaseStrategy` 时已自带 AI 阶段（可通过 `enable_ai_analysis = False` 关闭）。
5. 在 `strategies/all_strategies.py` 的 `_import_all_strategies()` 中导入该模块以触发自动注册。
6. 在 `locales/` 添加 `strategy_xxx` / `strategy_xxx_desc` 等 i18n key。
7. 在 `tests/unit/` 下编写单测。

### 4. 新增一个 UI 视图

1. 在 `ui/views/` 下创建 `xxx_view.py`，View 只构建控件树。
2. 在 `ui/viewmodels/` 下创建对应 ViewModel，持有业务状态、调用 services/data 层。
3. 在 `ui/app_layout.py` 中注册新标签页 (如需)。
4. UI 事件回调使用 `@log_ui_action` 装饰器埋点。
5. 异步耗时操作必须通过 `ThreadPoolManager.run_async()` 或 `TaskManager.submit_task()` 提交。
6. 若视图展示 i18n 文案，必须遵守 [语言切换响应 (I18n Hot Reload)](#语言切换响应-i18n-hot-reload)。
7. 若视图含分栏布局或依赖窗口尺寸，必须实现 `handle_resize()` 并遵守 [响应式布局规范 (Responsive Layout)](#响应式布局规范-responsive-layout)。对照标准 View 检查清单逐项确认。

### 5. 新增一个外部数据源

1. 在 `data/external/` 下创建客户端模块，封装第三方 SDK 或 HTTP API。
2. 使用 `utils/rate_limiter.py` 提供的限流器避免触发对方风控。
3. 网络错误必须用 `classify_error(e, context="general")` 分类，自动处理重试。
4. 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)`。
5. 若需走代理，使用 `utils/proxy_manager.py`。

### 6. 新增与升级依赖

1. **编辑依赖配置**：
   - 编辑 `pyproject.toml`：
     - 运行时依赖加到 `[project] dependencies`
     - 开发依赖加到 `[project.optional-dependencies] dev`
     - 可选依赖加到 `[project.optional-dependencies] optional`
   - 若要升级已有依赖，可运行 `uv lock --upgrade` 更新锁文件。
2. **生成与编译 `requirements*.txt`**：
   - **自动化生成**：在 `git commit` 时，本地 pre-commit 钩子会自动运行 `uv pip compile` 重新编译所有的 `requirements*.txt`。
   - **手动即时生成（用于本地即时升级调试）**：若在 commit 前需要使升级或新依赖立即在本地生效，请手动编译：
     ```bash
     uv pip compile --universal --no-emit-index-url pyproject.toml -o requirements.txt
     uv pip compile --universal --no-emit-index-url --extra dev pyproject.toml -o requirements-dev.txt
     uv pip compile --universal --no-emit-index-url --extra optional pyproject.toml -o requirements-optional.txt
     ```
3. **本地安装新依赖**：运行以下命令将编译后的依赖同步到本地环境：
   ```bash
   uv pip install --system -r requirements.txt -r requirements-dev.txt
   # 如需可选功能：
   uv pip install --system -r requirements-optional.txt
   ```

### 7. 新增回测配置

1. 在 `strategies/backtest/config.py` 中定义回测参数 (`BacktestConfig`)。
2. 在 `strategies/backtest/adapter.py` 中适配待回测的策略。
3. 通过 `services/backtest_service.py` 的 `run_backtest()` 启动。
4. 结果通过 `BacktestDAO` 持久化，由 `ui/views/backtest_view.py` 展示。

### 8. 新增一个单例

1. 使用 `@register_singleton` 装饰器注册类（代码模板见[单例模式实现模板](#单例模式实现模板)）。
2. 实现 `_reset_singleton()` 类方法 (测试隔离必须)。
3. 实例创建必须受 `threading.Lock` 保护 (优先在 `__new__` 中持锁)。
4. 支持 `_initialized` 标志防止重复初始化。
5. 如需进程退出清理，实现 `_atexit_cleanup()` 类方法。
6. 在 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式) 的单例列表中补充新单例名称。
7. 在 `tests/unit/` 下编写单测；常规隔离由 `_reset_all_singletons` autouse fixture 自动处理，需精细控制单例初始化状态时使用 `singleton_state` 上下文管理器。

## 排查典型问题

| 现象 | 可能原因 | 排查点 |
|------|---------|--------|
| 测试间状态污染 | 单例未注册到 `singleton_registry` | 检查 `@register_singleton`；需精细控制时用 `singleton_state` 包裹并检查 `extra_attrs` |
| `RuntimeError: no running event loop` | 跨循环使用同步原语 | 改用 `get_loop_local` (对应 [CLAUDE.md R11](./CLAUDE.md#31--绝对禁止)) |
| `EngineDisposedError` | 关机期间继续访问 DB | 在调用方捕获并降级，或检查 `_disposed` 早退 |
| 慢查询告警 | SQL 缺索引 / 数据量过大 / N+1 | 看 `[ClassName] Slow Read/Write` 日志，结合 `EXPLAIN` |
| Pyright 报错但运行时正常 | Optional 未判空 / 泛型推断失败 | 用 `assert x is not None` 收窄类型，或显式标注 |
| Ruff `UP*` 报错 | 使用了过时语法 | 跑 `ruff check . --fix` 自动升级 |
| Tushare 限流 | 短时调用过多 | 看 `utils/rate_limiter.py` 配置；考虑加缓存 |
| 优雅停机卡住/超时 | `CancelledError` 被吞没 | 搜索 `except asyncio.CancelledError` 后无 `raise`；参见 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止) |

## 已知架构技术债 (Known Technical Debt)

项目开发演进过程中产生了一些需要明确跟踪的技术债与设计限制，请在排查深层问题时参考：

| 级别 | 问题描述 | 产生背景与现状 | 期望的最终解法 |
|------|---------|---------------|--------------|
| **P1-2** | **Windows 测试事件循环泄露** | Windows 使用 `WindowsSelectorEventLoopPolicy` 时测试 loop scope 妥协为 `session` 级，导致 `asyncio.Event/Lock` 跨测试泄漏。当前依赖 `reset_loop_local_cache` fixture 维持隔离。 | 中期应将 Windows 测试作用域降级回 `function` 彻底修复，降级后删除该隔离 fixture (见探测用例 `test_infra_loop_isolation.py`)。 |
| **P3** | **`MAX_CONTENT_WIDTH` 代码未实现** | 响应式规范 7（max_width）已从强制规范移出登记为技术债。`ui/app_layout.py` 未实现居中容器（`body_wrapper`）与 `MAX_CONTENT_WIDTH` 宽度逻辑。 | 独立后续任务：实现 `body_wrapper` 居中容器与 `MAX_CONTENT_WIDTH` 逻辑，配套窗口宽度场景测试（4K / 2K / 1080p）。当前状态：独立后续任务。 |
| **P3** | **命令式 UI 存量需整改为声明式** | 现有 UI 全量为命令式（`class X(ft.Container)` + 手动 `self.update()` + `did_mount`/`will_unmount`）。宪法 [§3.2 UI 模型（强制）](./CLAUDE.md#32--强制要求) 已确立声明式 `@ft.component` 为唯一合法模型，命令式视为技术债。 | 独立重构主线：按视图切分逐个改造为 `@ft.component` + `use_state`/`use_effect`；i18n 九规范、响应式九规范、`PageRefMixin`、`refresh_dropdown_options` 等命令式绕过随改造删除。整改工作量大，作为独立任务排期。当前状态：独立重构任务。 |
| **P3** | **doc-lint 自动化未实现** | F3/F4 类数字/一致性漂移靠人工检视才发现，缺自动化校验。 | 新增 `scripts/check_docs_consistency.py` pre-commit/CI 脚本，覆盖 markdown 锚点死链校验、"N 条规范"数字一致性、红线自动化声明与 `.pre-commit-config.yaml`/`pyproject.toml` 交叉校验。当前状态：待实现，独立任务。 |

---

## 获取帮助

- **GitHub Issues**: 提问或报告问题
- **Email**: louis2sin@gmail.com

---

再次感谢你的贡献！
