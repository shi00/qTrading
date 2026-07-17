# Contributing to AStockScreener

感谢你考虑为 AStockScreener 做贡献！本文档分为三部分：人类贡献者指南、开发环境与命令参考、实现规范手册。

> **AI 编程助手注意**：[CLAUDE.md](./CLAUDE.md) 是项目宪法（红线、架构边界、交互准则），每次会话自动加载。本文件第三部分「实现规范手册」承接宪法中移出的代码模板与详细规范，需要时按需查阅。
>
> **对应版本**：0.9.0，最后校对：2026-07-15（与 [CLAUDE.md](./CLAUDE.md) 保持一致）

## 目录

- [第一部分：人类贡献者指南](#第一部分人类贡献者指南)
  - [行为准则](#行为准则)
  - [如何贡献](#如何贡献)
  - [Pull Request 流程](#pull-request-流程)
  - [代码审查与合并](#代码审查与合并)
  - [Git 工作流与分支策略](./docs/guides/git-workflow.md)
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
  - [单例模式实现模板](./docs/architecture/singleton-lifecycle.md)
  - [策略模式实现模板](./docs/patterns/strategy-template.md)
  - [Polars 向量化策略基类](./docs/patterns/polars-vectorized-strategy.md)
  - [AI 策略混入](./docs/patterns/ai-strategy-mixin.md)
  - [DAO 模式](./docs/patterns/dao-pattern.md)
  - [数据同步架构](./docs/patterns/data-sync.md)
  - [TaskManager 任务生命周期](./docs/patterns/task-manager.md)
  - [配置管理、质量门控、性能监控](./docs/patterns/config-quality-perf.md)
  - [MVVM 表现层](./docs/patterns/mvvm.md)
  - [Flet V1 API 关键约束](#flet-v1-api-关键约束)
  - [类型标注与 Pyright 规则](#类型标注与-pyright-规则)
  - [日志规范](#日志规范)
  - [异步编程规范](#异步编程规范)
  - [数据库操作规范](#数据库操作规范)
  - [错误处理标准模式](#错误处理标准模式)
  - [测试规范](./docs/guides/testing.md)
  - [CI/CD 流水线与门禁](./docs/guides/ci-cd.md)
  - [标准开发工作流 (How-To)](./docs/guides/how-to.md)
  - [排查典型问题](#排查典型问题)
  - [已知架构技术债 (Known Technical Debt)](#已知架构技术债-known-technical-debt)

---

# 第一部分：人类贡献者指南

## 行为准则

本项目采用贡献者公约作为行为准则。参与此项目即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

如果你发现了 bug，请通过 [Bug 报告模板](https://github.com/shi00/qTrading/issues/new?template=bug_report.yml) 提交（空白 issue 已禁用，模板位于 [`.github/ISSUE_TEMPLATE/bug_report.yml`](./.github/ISSUE_TEMPLATE/bug_report.yml)）。提交前请先搜索现有 issues 确认无重复，并按模板填写：问题描述、复现步骤、期望/实际行为、环境信息、影响层与严重度。

### 提出新功能

欢迎提出新功能建议！请通过 [功能请求模板](https://github.com/shi00/qTrading/issues/new?template=feature_request.yml) 提交（模板位于 [`.github/ISSUE_TEMPLATE/feature_request.yml`](./.github/ISSUE_TEMPLATE/feature_request.yml)）。新功能需符合 [CLAUDE.md §1.3 极简设计](./CLAUDE.md#13-极简设计-simplicity-first)（YAGNI 优先）。提问与讨论请前往 [GitHub Discussions](https://github.com/shi00/qTrading/discussions)。

### 代码复用与避免重复造轮子

在开始编写新代码前，请务必遵循**复用优先**原则，避免重复造轮子：
1. **优先复用工程已有代码**：开发新功能前，先全局搜索项目中是否已有类似的工具函数、基础类、UI 组件或业务逻辑。
2. **优先使用成熟开源库**：若需引入常见的基础功能，优先采用业界广泛使用、维护活跃的稳定开源库，而非自行实现。
3. **避免无谓的封装**：如果已有成熟库提供了所需功能，请直接使用，不要对其进行单薄的二次封装，除非这种封装能带来明显的业务价值（如统一鉴权、异常转换等）。

### 提交代码

1. Fork 本仓库
2. 使用 worktree 隔离开发（对应 [CLAUDE.md §3.1 R18](./CLAUDE.md#31--绝对禁止)，详见 [Git 工作流与分支策略](#git-工作流与分支策略) 中的「Worktree 强制使用」与「标准工作流」）。**禁止在主工作区直接 `git checkout -b` 开发新特性或跨文件修改**
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

我们使用 **Merge Queue** 确保合并安全（合并方式为 **Squash Merge**，保持 main 历史干净，详见下节「Git 工作流与分支策略」）：

1. PR 获得批准后，点击 "Ready for review" → "Merge when ready"
2. 系统会自动将 PR 加入合并队列
3. 在队列中会与 main 最新代码组合后重新运行 CI
4. 通过后自动合并

## Git 工作流与分支策略

> 本节已迁移到 [docs/guides/git-workflow.md](./docs/guides/git-workflow.md)。

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

# 在项目 venv 内安装依赖（不要使用 --system，否则会装到系统 Python 与 venv 意图冲突）
uv pip install -r requirements.txt
uv pip install -r requirements-optional.txt
uv pip install -r requirements-dev.txt

# 安装 pre-commit hooks
pre-commit install

# 运行测试验证环境
python -m pytest tests/unit/ -v --tb=short -m "not slow"
```

> 项目使用 pre-commit hooks（Ruff lint/format、裸 `type: ignore` 检测、禁止 `IsolatedAsyncioTestCase`、requirements 同步、版本一致性校验、文档一致性校验、红线自动化校验、import-linter 架构守护），hook 数量见 [`.pre-commit-config.yaml`](./.pre-commit-config.yaml)，亦见 [Pre-commit Hooks](./docs/guides/ci-cd.md#pre-commit-hooks)。
>
> **新特性开发请使用 worktree 隔离**，避免在主工作区直接开发（对应 [CLAUDE.md §3.1 R18](./CLAUDE.md#31--绝对禁止)，详见 [Git 工作流与分支策略](#git-工作流与分支策略) 中的「Worktree 强制使用」）。

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
| **UI 框架** | Flet V1（版本见 [`pyproject.toml`](./pyproject.toml)，含 `flet` / `flet-desktop` / `flet-charts` 三包；Flet 1.0 alpha/beta 阶段，Flutter 驱动桌面应用，dataclass 控件 + 单线程 async UI 模型） |
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

> 本节已迁移到 [docs/architecture/singleton-lifecycle.md](./docs/architecture/singleton-lifecycle.md)。

## 策略模式实现模板

> 本节已迁移到 [docs/patterns/strategy-template.md](./docs/patterns/strategy-template.md)。

## Polars 向量化策略基类

> 本节已迁移到 [docs/patterns/polars-vectorized-strategy.md](./docs/patterns/polars-vectorized-strategy.md)。

## AI 策略混入

> 本节已迁移到 [docs/patterns/ai-strategy-mixin.md](./docs/patterns/ai-strategy-mixin.md)。

## DAO 模式

> 本节已迁移到 [docs/patterns/dao-pattern.md](./docs/patterns/dao-pattern.md)。

## 数据同步架构

> 本节已迁移到 [docs/patterns/data-sync.md](./docs/patterns/data-sync.md)。

## TaskManager 任务生命周期

> 本节已迁移到 [docs/patterns/task-manager.md](./docs/patterns/task-manager.md)。

## 配置管理、质量门控、性能监控

> 本节已迁移到 [docs/patterns/config-quality-perf.md](./docs/patterns/config-quality-perf.md)。

## MVVM 表现层

> 本节已迁移到 [docs/patterns/mvvm.md](./docs/patterns/mvvm.md)。

## Flet V1 API 关键约束

> 本节已迁移到 [docs/flet/v1-api-constraints.md](./docs/flet/v1-api-constraints.md)。本节作为 CONTRIBUTING.md 入口索引保留，详细 API 约束、声明式组件契约、V1 声明式 UI 开发规范、兼容垫片使用规则、升级协同机制、例外清单等均位于 docs/flet/ 下子文档。

**docs/flet/ 子文档清单**：

- [v1-api-constraints.md](./docs/flet/v1-api-constraints.md) — Flet V1 API 关键约束（V0→V1 迁移 API 表、声明式组件内 API 契约、V1 声明式 UI 开发规范、兼容垫片使用规则、升级协同机制、例外清单）
- [project-differences.md](./docs/flet/project-differences.md) — 项目相对 Flet 官方默认的分叉点与项目验证过的高风险 API（含 R16 UI 阻塞红线）
- [upgrade-checklist.md](./docs/flet/upgrade-checklist.md) — Flet 版本升级时的验证步骤与文档同步要求
- [api-verification-template.md](./docs/flet/api-verification-template.md) — Flet API 核验记录模板（P1-4 整改新增）
- [accessibility-baseline.md](./docs/flet/accessibility-baseline.md) — UI 可访问性最低标准（P2-4 整改新增）

> 相关：[CLAUDE.md §2](./CLAUDE.md#2-项目概览) 技术栈表、[CLAUDE.md §3.1 R16](./CLAUDE.md#31--绝对禁止)（V1 单线程 async 模型对 UI 阻塞更敏感）。Flet 锁定版本见 [`pyproject.toml`](./pyproject.toml)。

## 类型标注与 Pyright 规则

> 宪法依据：CLAUDE.md §3.1 R6（过时类型注解红线）、§1.10（反幻觉护栏）；实现细则以本节为准。

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

> 宪法依据：CLAUDE.md §3.1 R9（敏感信息泄露红线）；实现细则以本节为准。

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

> 宪法依据：CLAUDE.md §3.1 R2/R11/R16（取消传播、loop-local、UI 阻塞红线）与 §3.2（ThreadPoolManager 强制）；实现细则以本节为准。

- **asyncio 模式**: 全项目使用 `asyncio` 驱动异步。
- **线程安全**: UI 回调可能来自线程池，使用 `loop.call_soon_threadsafe()` 转移到事件循环。
- **线程池分离**: IO 密集型使用 `TaskType.IO`，CPU 密集型 (NumPy/Pandas 等 GIL 释放型) 使用 `TaskType.CPU`；纯 Python CPU 密集任务应使用 `ProcessPoolExecutor` (项目暂无)。
- **CancelledError 必须传播**: 永远 `raise` 不吞没，否则破坏优雅停机 (对应 [CLAUDE.md R2](./CLAUDE.md#31--绝对禁止))。
- **事件循环绑定对象**: 使用 `utils.loop_local` 的 `get_loop_local()` / `del_loop_local()` / `clear_all_loop_locals()` 管理 `asyncio.Event`、`asyncio.Lock` 等绑定到特定事件循环的对象，避免跨循环死锁 (对应 [CLAUDE.md R11](./CLAUDE.md#31--绝对禁止))。
- **`asyncio.gather`** 涉及失败可恢复场景使用 `return_exceptions=True`，并在调用方逐个分类异常。
- **不要在 `__init__`** 中调用 `asyncio.create_task()`，会绑定到错误的事件循环；改为提供 `async def initialize()` 方法。

## 数据库操作规范

> 宪法依据：CLAUDE.md §3.1 R4/R5/R8/R17（SQL 注入、僵尸引擎、废弃 API、保留字红线）与 §3.2（`_save_upsert` 强制）；实现细则以本节为准。

- **异步引擎**: 使用 `asyncpg` 驱动 (通过 SQLAlchemy asyncio)。
- **参数占位符**: 使用 `$1, $2, ...` (asyncpg 原生占位符，非 `%s`) (对应 [CLAUDE.md R4](./CLAUDE.md#31--绝对禁止))。
- **批量写入**: 使用 `_save_upsert()` (基于 `ON CONFLICT DO UPDATE`，内置分块，大小见 `base_dao.py`) (对应 [CLAUDE.md R8](./CLAUDE.md#31--绝对禁止))。
- **分块 IN 查询**: 使用 `chunked_in_query()` 避免 PostgreSQL 参数上限 (分块大小见 `base_dao.py`)。
- **引擎状态检查**: DAO 操作前必须确认引擎仍可用；关机/释放后继续访问时应抛出或传播 `EngineDisposedError`，调用方按关机降级处理。
- **维护锁**: DAO 操作前 `await self._get_maintenance_event().wait()` 等待维护完成 (基类已自动处理)。
- **慢查询阈值**: 见 `base_dao.py` 配置 (基类自动告警，无需手动埋点)。
- **DB 异常应在 DAO 层处理**: 业务层只接收 `EngineDisposedError` 和业务异常，不应直接捕获 `asyncpg.*Error`。

## 错误处理标准模式

> 宪法依据：CLAUDE.md §3.1 R2（异常吞没红线）与 §3.2（`classify_error` 强制）；实现细则以本节为准。

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

**优雅降级例外**：当 `severity == "system"` 但当前处于关闭流程、降级路径或基础设施兜底场景时（如窗口 destroy 失败、keyring fallback、日志系统初始化失败），可不 `raise`，但须满足以下条件之一：① 场景明确无法重试或 raise 无意义（如已处于 shutdown 流程）；② 降级路径已提供合理兜底返回值（如策略空结果、AI 计算降级文案）；③ 基础设施层兜底（如 exception_hooks/logger 不适合走 classify）。此类场景应添加注释说明不 raise 的理由。

## 测试规范

> 本节已迁移到 [docs/guides/testing.md](./docs/guides/testing.md)。
>
> 覆盖率源（事实源 [`pyproject.toml`](./pyproject.toml) `[tool.coverage.run] source`）：`core`, `app`, `data`, `services`, `strategies`, `utils`, `ui`, `config`, `main`（排除 `tests/`, `scripts/`, `data/tiktoken_cache/`）。整体覆盖率 ≥ 85%，单文件 ≥ 80%（由 `scripts/check_per_file_coverage.py` 强制检查）。

## CI/CD 流水线与门禁

> 本节已迁移到 [docs/guides/ci-cd.md](./docs/guides/ci-cd.md)。

## 标准开发工作流 (How-To)

> 本节已迁移到 [docs/guides/how-to.md](./docs/guides/how-to.md)。

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

项目开发演进过程中产生了一些需要明确跟踪的技术债与设计限制，请在排查深层问题时参考。已解决事项（Windows 测试事件循环泄露、V0 兼容垫片删除、声明式迁移收官等）不再列入本表，仅在相关章节的活动规范中标注当前允许/禁止形态。

| 级别 | 问题描述 | 产生背景与现状 | 期望的最终解法 |
|------|---------|---------------|--------------|
| **P3** | **doc-lint 自动化第二阶段部分实现（3a 已落地，3b/3c 判定为过度工程）** | 第一阶段已实现：`scripts/check_docs_consistency.py` 覆盖 markdown 锚点死链校验、CLAUDE.md 版本与 `pyproject.toml` 一致、pre-commit hook 数量一致性，已接入 `.pre-commit-config.yaml` `docs-consistency` hook。第二阶段 3a（`NOTE(lazy):` 三要素格式检查）已落地，扩展 `check_note_lazy_format()` 函数扫描所有 `.py` 文件。五视角评审判定 3b（红线 R1~R18 编号 append-only 检查）与 3c（"强制状态"与实际 hook/CI job 映射检查）为过度工程，不做。 | 仅保留 3a 守护。upgrade 触发条件：红线违规频发或 CI 自动化专项迭代时重新评估 3b/3c。 |
| **P3** | **strategies/ 层 except Exception 已标记 NOTE(lazy)（ceiling 动态化），待评估统一走 classify_error** | strategies/ 层 except Exception 中 P0/P2 必修项已完成，剩余已合理日志的 except Exception 用 `# NOTE(lazy):` 标记保留。Phase 6.1 杠杆评估决策"不引入 `_handle_strategy_exception(e, context)` 统一入口"（YAGNI：`classify_error` 调用稀少；过度抽象：5 正交维度需 7 字段 context；多处差异化状态修改无法覆盖）。Phase 6.3 完成 ceiling 动态化（按方法分组描述，不再硬编码数值）。R9 合规修复已完成（`str(e)` → `DataSanitizer.sanitize_error(e)`）。评估报告见 `docs/strategy-exception-leverage.md`（本地）。 | 策略层重构或新增策略时统一走 `classify_error` + `classify_severity`。upgrade 触发条件：策略层重构时。E-3 决策：保持现状（NOTE(lazy) 标记已记录 upgrade 条件），强行接入 classify_error 会改变 system severity → raise 行为，违反 §1.4「不做无益重构」。 |
| **P3** | **utils/ 层 except Exception 已标记 NOTE(lazy)，待评估统一走 classify_error** | R3 场景遗漏检视发现：utils/ 层 except Exception（分布在 config_handler / exception_hooks / logger / singleton_registry / diagnostics 等）。已统一标记 `# NOTE(lazy):`（三要素齐全：简化内容 + ceiling + upgrade），`time_utils.py:80` 已剔除（无业务消费方语义，调查时判定不适合标记）。0 处适合走 `classify_error`（均无业务消费方，属基础设施兜底或合理降级）。 | 后续 utils 层异常处理统一改造时重新评估是否引入 `classify_error`。upgrade 触发条件：utils 层异常处理统一改造时。 |
| **P3** | **红线自动化覆盖部分实现（R1/R4/R12/R13/R14/R15 已落地，R16 暂缓）** | CLAUDE.md §3.1 中 R1/R4/R12/R13/R14/R15/R16 标注「可自动化待实现」。R1 已由 import-linter 契约守护（4 条禁止导入契约）。R4/R12/R13/R14/R15 已由 `scripts/check_redlines.py` 实现，接入 `.pre-commit-config.yaml` `redline-check` hook（单元测试守护，数量见 CI 日志）。R16（UI 阻塞）暂缓：AST 扫描误报风险高，需更精确的事件处理器识别逻辑。 | R16 暂缓。upgrade 触发条件：R16 误报控制方案成熟或红线违规频发时重新评估。 |
| **P3** | **litellm 上游限制 `Requires-Python <3.14`** | litellm 1.83.8+（含项目锁定的 1.91.0 及最新 1.92.0）声明 `Requires-Python >=3.10,<3.14`。Python 3.14 下 `uv pip install -r requirements.txt` 因 litellm 无法安装而失败，导致 `ci-checks` 整个 job 全链路中断（依赖安装 → pre-commit → pip-audit → pyright → 测试 → 迁移 → 覆盖率均无法运行）。当前状态：`lint-fast` job matrix 仍含 `['3.13', '3.14']`，其中 `3.14` 标记 `experimental: true` 并 `continue-on-error`，仅跑 `ruff check` + `ruff format --check`，不安装项目依赖；完整测试矩阵（`ci-checks` 等）仅运行 Python `3.13`。降级到 1.83.7 不可接受（丢失 aiohttp CVE-2026-47265/CVE-2026-34993 修复）。 | litellm 发布解除 `<3.14` 限制的版本时：① 升级 `pyproject.toml` 的 litellm 版本；② 重新 `uv pip compile` 生成 requirements*.txt；③ 将 `3.14` 从 `lint-fast` 的 experimental 矩阵项转为完整测试矩阵项。upgrade 触发条件：litellm 解除 `<3.14` 限制。 |
| **P3** | **AIStrategyMixin（1890 行）高度内聚，context builder 拆分推迟评估** | `strategies/ai_mixin.py` 的 `AIStrategyMixin` 当前 1890 行，承载 AI 策略 context 构建与决策调用，方法间共享状态密集、内聚度高。Phase 评审判定当前规模未触发可维护性红线（无跨策略复制粘贴、无重复 context 拼接逻辑），强行拆分会引入跨模块状态传递抽象，违反 §1.3「拒绝过度抽象」。 | 未来 AI 策略重构或新增 AI 策略类型时再评估拆分 context builder。验收标准：① context builder 拆为独立模块/类（如 `AIContextBuilder`）；② 拆分后 AI 策略行为不变（现有 AI 策略单测 + 集成测试全过）；③ 新增 context builder 单测覆盖核心分支。upgrade 触发条件：AI 策略重构或新增 AI 策略类型时。 |
| **P3** | **TushareClient（1852 行）capability probe 部分约 400 行可独立拆分** | `data/external/tushare_client.py` 的 `TushareClient` 当前 1852 行，其中 capability probe（能力探测）部分约 400 行与主客户端职责正交，可独立拆分。当前未拆分原因：probe 完成后需通知上层刷新配置/缓存，缺少回调通知机制；强行拆分会引入循环依赖或全局状态。Phase 评审判定为推迟优化（YAGNI：当前 probe 部分稳定，无频繁变更）。 | capability probe 频繁变更或新增 probe 维度时拆分。验收标准：① probe 部分拆为独立模块/类（如 `TushareCapabilityProbe`）；② 引入回调通知机制（probe 完成/失败通知 `TushareClient` 上层）；③ 拆分后 `TushareClient` 单测全过；④ 新增 probe 模块单测覆盖各 API 探测分支。upgrade 触发条件：capability probe 频繁变更或新增 probe 维度时。 |
| **P3** | **CacheManager（1109 行）作为薄包装 facade 不主动拆分** | `data/cache/cache_manager.py` 的 `CacheManager` 当前 1109 行，但属薄包装 facade：方法多为对 DAO 实例/引擎生命周期的转发，无独立业务逻辑。Phase 评审判定按 §1.3「拒绝过度抽象」与 §4.3「单例模式」不主动拆分——拆分薄包装 facade 会增加间接调用层而无实质价值。 | 不主动拆分。仅在 facade 表面出现责任漂移（非缓存职责混入）或新增多个非薄包装逻辑时重新评估。验收标准：① 评估时确认仍是薄包装 facade（无业务逻辑泄漏到 facade）；② 如确有拆分必要，按职责拆分并保留 facade 兼容性（不破坏调用方）；③ `CacheManager` 单测 + `_reset_singleton` 隔离测试全过。upgrade 触发条件：facade 出现责任漂移或新增非薄包装逻辑时。 |
| **P3** | **R13 自动化增强（_create_engine/close 维度检查）为可选优化** | `scripts/check_redlines.py` 当前 R13 守护已落地：检查新增 DAO 在 `CacheManager.__init__` 中实例化。但 R13 红线全条款还要求「在 `_create_engine` 中更新 `.engine` 引用」与 close 路径正确释放，这两维度未做静态检查。Phase 评审判定为可选优化：① `_create_engine` 与 close 维度检查需 AST 解析方法体（复杂度高于 `__init__` 实例化检查）；② R5「僵尸引擎操作」运行时已有人工评审兜底；③ 当前未发生该维度漏报。 | 可选优化，不主动实施。新增 DAO 未在 `_create_engine` 更新 `.engine` 引用或未在 close 中正确释放导致 R5 问题时实施。验收标准：① `check_redlines.py` 增加 `_create_engine` / close 维度的 AST 检查；② 新增对应单元测试（正例 + 反例）；③ CI `redline-check` hook 通过。upgrade 触发条件：R13 这两维度出现漏报或 R5 僵尸引擎问题频发时。 |
| **P3** | **R16 AST 守卫维持暂缓，依赖 @log_async_operation 运行时检测** | CLAUDE.md §3.1 R16「UI 阻塞主循环」标注「可自动化待实现（AST 检查，暂缓：误报风险高）」。Phase 评审决策维持暂缓：① AST 扫描难以精确识别 Flet 事件处理器边界（onClick/on_change 等回调 vs 普通方法），误报率不可控；② 项目已有 `@log_async_operation(threshold_ms=PerfThreshold.XXX)` / `@track_performance()` 运行时检测慢操作告警，R16 漏报会被运行时告警捕获。本条与上表「红线自动化覆盖部分实现」条目互补：前者讲整体进度，本条专门记录 R16 维持暂缓的决策依据。 | 维持暂缓。误报控制方案成熟或 R16 运行时检测漏报频发时重新评估。验收标准：① AST 守卫接入 pre-commit `redline-check` hook；② 误报率 < 5%（在现有事件处理器样本上验证）；③ 不漏报已知的同步阻塞事件处理器；④ 新增对应单元测试（正例 + 反例 + 边界）。upgrade 触发条件：R16 误报控制方案成熟或运行时检测漏报频发时。 |
| **P3-WinE2E-Skip** | **Windows E2E 关键路径 skip（onboarding DB 成功 / settings log level 切换）** | Windows 平台 Flet/Playwright CanvasKit textbox 渲染 + 向导状态隔离 + snackbar 时序 + select_dropdown 性能问题，导致 `test_wizard_db_validation_success` 和 `test_settings_log_level_switch` 两个 E2E 用例在 Windows 下不稳定（30+ 分钟耗时或超时）。当前用 `@pytest.mark.skipif(sys.platform == "win32", ...)` 单层装饰器跳过（已清理历史双重 skip），并在 `tests/integration/` 补集成测试覆盖 ViewModel/service 路径。 | ① Flet 升级到修复 CanvasKit textbox 渲染问题的版本；② 引入 headless 浏览器方案替代 CanvasKit；③ Playwright snackbar 时序问题在上游修复。upgrade 触发条件：Flet/Playwright 升级或 Windows E2E 稳定性方案落地时重新评估。 |

---

## 获取帮助

- **GitHub Issues**: 提问或报告问题
- **Email**: louis2sin@gmail.com

---

再次感谢你的贡献！
