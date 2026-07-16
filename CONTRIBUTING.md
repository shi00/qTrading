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
  - [Git 工作流与分支策略](#git-工作流与分支策略)
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
  - [标准开发工作流 (How-To)](#标准开发工作流-how-to)
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

我们使用 **Merge Queue** 确保合并安全（合并方式为 **Squash Merge**，保持 main 历史干净，详见下节「Git 工作流与分支策略」）：

1. PR 获得批准后，点击 "Ready for review" → "Merge when ready"
2. 系统会自动将 PR 加入合并队列
3. 在队列中会与 main 最新代码组合后重新运行 CI
4. 通过后自动合并

## Git 工作流与分支策略

> 对应 [CLAUDE.md §3.1 R18](./CLAUDE.md#31--绝对禁止)。本节承接宪法中关于 git 隔离开发的强制要求，提供分支模型、命名规范、worktree 标准流程。与 superpowers `using-git-worktrees` / `finishing-a-development-branch` skill 联动。

### 分支模型：GitHub Flow

项目采用 **GitHub Flow**：单一长期分支 `main` + 短命 feature 分支。不引入 Git Flow 的 `develop`/`release`/`hotfix` 多长期分支（违反 YAGNI）。

```text
main (受保护，禁止直接 push)
  ├── feature/strategy-macd   ── PR ── Squash Merge ──┐
  ├── fix/dao-memory-leak     ── PR ── Squash Merge ──┤
  └── refactor/cache-layer    ── PR ── Squash Merge ──┘
                                                       ↓
                                                    main 推进
```

例外：发布冻结期可临时使用 `release/<version>` 分支，发布后立即删除，不长期保留。

### 分支命名规范

格式：`<type>/<scope>-<short-desc>`，`type` 复用「提交信息规范」类型，保持一致：

| 类型 | 用途 | 示例 |
|------|------|------|
| `feature/` | 新功能 | `feature/strategy-macd` |
| `fix/` | Bug 修复 | `fix/dao-memory-leak` |
| `refactor/` | 重构（不改行为） | `refactor/cache-layer` |
| `docs/` | 文档更新（多文件） | `docs/api-reference` |
| `test/` | 测试补强 | `test/dao-coverage` |
| `chore/` | 构建/工具/依赖 | `chore/upgrade-flet` |
| `perf/` | 性能优化 | `perf/data-loader-batch` |
| `ci/` | CI 配置 | `ci/add-coverage-check` |

约束：
- 全小写，单词用 `-` 分隔，禁用下划线/中文
- 长度 ≤ 50 字符
- 语义清晰可检索

### Worktree 强制使用

**强制场景**（违反即 R18）：
- 新特性开发（涉及新增文件或多文件改动）
- 重构任务（跨文件结构调整）
- 实验性探索（不确定是否合并的工作）
- AI 助手驱动的多步骤实现任务（配合 superpowers `using-git-worktrees` skill 自动触发）

**豁免场景**：
- 单文件文档纯改（如仅改 README.md 一处）
- 单行修复（typo、配置值修正）
- bug 复现脚本（临时一次性代码）
- 已在 `.worktrees/<branch>/` 内的开发（已满足隔离）

### 标准工作流

完整生命周期（与 superpowers skill 对齐）：

```bash
# 1. 创建 worktree（默认 .worktrees/<branch-name>/，已在 .gitignore）
git worktree add .worktrees/feature-strategy-macd -b feature/strategy-macd
cd .worktrees/feature-strategy-macd

# 2. 项目 setup（Python 项目）
uv sync                                    # 或 pip install -e .
pre-commit install                         # 安装 git hooks

# 3. 基线测试验证（确保起点干净）
ruff check . && ruff format --check . && pyright
python -m pytest tests/unit/ -v -m "not slow"

# 4. 开发（遵循 TDD：先写测试，再写实现，每步 commit）
#    提交遵循「提交信息规范」+ 原子提交原则（见下节）

# 5. 完成后跑完整门禁
pre-commit run --all-files
python -m pytest tests/unit/ -v -m "not slow"

# 6. 推送并创建 PR
git push -u origin feature/strategy-macd
gh pr create --title "feat(strategy): add MACD crossover" --body "..."

# 7. PR 通过 Merge Queue 合并后，清理 worktree（回主工作区执行）
cd ../..  # 回主仓库根
git worktree remove .worktrees/feature-strategy-macd
git worktree prune
git branch -d feature/strategy-macd       # Squash Merge 后本地分支可删
```

> 详细决策流程（merge/PR/keep/discard 四选项）由 superpowers `finishing-a-development-branch` skill 提供，AI 助手在任务完成时自动触发。

### 原子提交

每次提交应满足：
- **可独立构建**：任意 commit checkout 后都能通过 `ruff check` + `pyright`
- **单一职责**：一次提交只做一件事（新功能 / 修复 / 重构二选一，不混合）
- **测试先行**：实现代码与对应测试同一 commit 提交（TDD RED→GREEN 中的 GREEN 步）
- **可回滚**：单个 commit 可通过 `git revert` 安全回滚，不影响其他功能

反例（禁止）：
```bash
# ❌ 混合多事
git commit -m "feat: add MACD + fix login bug + rename utils"
# ❌ 实现与测试分离
git commit -m "feat: add MACD"          # 只有实现
git commit -m "test: add MACD tests"    # 测试滞后
# ❌ 编译失败的中间态
git commit -m "wip: refactor in progress"
```

### 长期分支禁令（软规范）

Feature 分支存活建议 ≤ 7 天。超期需评估：
- 拆分为更小的 PR 分批合并
- 或显式标注为长任务，在 PR 描述中说明原因

避免长期分支与 main 漂移过大导致合并冲突爆炸。

### 与现有流程的关系

| 流程 | 文档位置 | 与本节关系 |
|------|---------|-----------|
| Conventional Commits | 「提交信息规范」 | 分支 type 与 commit type 对齐 |
| PR 流程 | 「Pull Request 流程」 | worktree 完成后进入 PR 阶段 |
| Merge Queue + Squash | 「合并策略」 | 本节规定的合并方式 |
| CODEOWNERS | 「代码审查与合并」 | 关键路径强制审查 |
| pre-commit | 「代码风格基础」 | 提交前质量门禁 |

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

> 项目使用 11 个 pre-commit hook (Ruff lint/format、裸 `type: ignore` 检测、禁止 `IsolatedAsyncioTestCase`、requirements 同步、版本一致性校验、文档一致性校验、红线自动化校验、import-linter 架构守护)，详见 `.pre-commit-config.yaml` 或 [Pre-commit Hooks](#pre-commit-hooks)。

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
| **UI 框架** | Flet 0.85.3（V1 API line / Flet 1.0 alpha-beta 阶段）+ flet-desktop 0.85.3 + flet-charts 0.85.3 (Flutter 驱动桌面应用，dataclass 控件 + 单线程 async UI 模型) |
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

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.2（R14 `@register_strategy` 强制）；实现模板见本节。

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

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.2（数据质量门控强制）；实现模板见本节。

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

> 宪法依据：CLAUDE.md §4.1（strategies 分层）、§3.1 R9/R10（敏感信息与硬编码密钥红线）；实现模板见本节。

`strategies/ai_mixin.py` 的 `AIStrategyMixin` 类提供 AI 增强能力，混入到策略类中实现 LLM 驱动的智能选股：

- 构建结构化 Prompt → 调用 LLM → 解析结构化响应
- 支持云端 (LiteLLM) 和本地 (llama-cpp-python) 双模式
- 内置重试、超时、Token 计量、Prompt 安全防护 (`utils/prompt_guard.py`)
- Prompt 模板集中在 `strategies/strategy_prompts.py`，响应校验在 `strategies/prompt_validator.py`

## DAO 模式

> 宪法依据：CLAUDE.md §4.1（data 分层）、§3.1 R4/R5/R8/R12/R13/R17（数据库红线）；实现模板见本节。

所有数据访问通过 `BaseDao` 子类，统一提供：

- `_read_db()` — 原生 SQL 读取，返回 DataFrame
- `_read_db_select()` — SQLAlchemy Core 查询 (**推荐**，防注入)
- `_write_db()` — 单条写入 (批量写入请使用 `_save_upsert()`，`CacheManager.write_db` 已移除 `is_many` 参数)
- `_save_upsert()` — 批量 UPSERT (**推荐**，基于 `pg_insert` + `ON CONFLICT`)
- `chunked_in_query()` — 分块 IN 查询 (避免参数上限)

**DAO 继承体系**: `BaseDao` → 具体子类见 `data/persistence/daos/` 目录

## 数据同步架构

> 宪法依据：CLAUDE.md §4.1（data 分层）、§3.1 R2（取消传播红线）；实现架构见本节。

`data/sync/` 下按数据类别组织同步策略：

- `base.py` — 同步基础定义 (`SyncContext` 依赖注入容器、`SyncResult` 结果数据类、`ISyncStrategy` 策略接口，含取消支持)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表结构、同步配置、质量监控配置。

## TaskManager 任务生命周期

> 宪法依据：CLAUDE.md §4.3（单例）、§3.2（ThreadPoolManager 强制）；实现细则见本节。

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
```

- 任务通过 `submit_task()` 提交，传入 `coroutine_factory` (无参可调用对象，返回 coroutine)
- 使用 `update_progress(progress)` 报告进度 (0.0-1.0)，内置节流避免 UI 风暴
- 工作协程内部使用 `is_cancelled()` 检测取消信号 (用户主动取消 / 应用退出)
- 任务持久化到本地，重启后 `RUNNING` 状态会被回填为 `INTERRUPTED`

## 配置管理、质量门控、性能监控

> 宪法依据：CLAUDE.md §3.2（质量门控、`@log_async_operation` 强制）与 §1.5（目标驱动与验证）；实现细则见本节。

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
from ui.hooks import use_viewmodel          # 已实现，见 ui/hooks.py
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

`use_viewmodel` 契约（已实现，见 [ui/hooks.py](./ui/hooks.py)）：

- 首次渲染：调 `factory()` 实例化 VM，调 `vm.subscribe(set_state)` 注册（保存返回的 unsub），返回 `(vm.state, vm)`
- `_notify` 触发：VM 遍历订阅者调 `callback(self.state)`，hook 注册的 callback 即 `set_state(vm.state)`，触发重渲染
- 卸载：调 unsub 退订 + `vm.dispose()`（若 VM 实现）
- `factory` 必须是无参 callable；DI 参数在 factory 闭包里完成（如 `lambda: ScreenerViewModel(dep1, dep2)` 或 `functools.partial`），VM 的 `__init__` 接受 DI 参数，不在构造函数里隐式获取全局状态（遵循 [CLAUDE.md §4.3](./CLAUDE.md#43-单例模式) DI 原则）

### 存量技术债

[ui/viewmodels/](./ui/viewmodels/) 下 7 个 ViewModel 已全部重写为 state snapshot + commands + `use_viewmodel` 目标范式。新代码必须沿用此范式，不得使用 `on_update`/`on_log` 回调注入。

## Flet 0.85.3 (V1) API 关键约束

> 相关：[CLAUDE.md §2](./CLAUDE.md#2-项目概览) 技术栈表（Flet 0.85.3）、[CLAUDE.md §3.1 R16](./CLAUDE.md#31--绝对禁止)（V1 单线程 async 模型对 UI 阻塞更敏感）。

### 演进方向

项目已从 Flet 0.28.3 (V0) 升级到 0.85.3 (V1，Flet 1.0 alpha/beta)。**项目策略：全面拥抱 V1 声明式，所有命令式 UI 代码全面重写，不保留兼容垫片**。遵循以下原则：

- **不得引入任何 V0 兼容垫片**（如 `hasattr(page, "open")` 双路径、`getattr(e, "delta_x", 0)` 兼容取值等）
- **全面采用 V1 原生机制**：通过挂载到 `page.controls` 后由 `parent` 链访问 `page`，而非 `PageRefMixin` 覆写
- **全面使用 V1 API 形态**：`ft.Button` 而非 `ElevatedButton`；对话框用 `page.show_dialog()`/`page.pop_dialog()` 而非 `page.dialog=`/`page.open()`
- **历史命令式代码已全面重写**：所有 `class X(ft.Container)` + `did_mount`/`will_unmount` + `self.update()` + `PageRefMixin` + `on_update`/`on_log` 回调注入的代码，已全部重写为 `@ft.component` + `use_viewmodel` 声明式范式
- **兼容垫片已删除**：`PageRefMixin` 与 `mock_flet` 测试桩在依赖代码重写完成后已删除（见下文「兼容垫片使用规则」）

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
| 18 | 控件 page 属性 | `self.page = page` 直接赋值 | 通过 `parent` 链访问；声明式组件内经 `ft.context.page` 或事件 `e.page` 获取（`PageRefMixin` 已删除，新代码禁用） | AttributeError |
| 19 | 本地存储 | `page.client_storage` | `page.shared_preferences` | AttributeError |
| 20 | 控件 update | 未挂载时 `control.update()` 静默返回 | 未挂载抛 `RuntimeError`（测试代码由 `conftest._v1_page_compat` fixture 兼容） | RuntimeError |
| 21 | 窗口方法 | `page.window.destroy()`（同步） | `await page.window.destroy()`（V1 协程） | 运行期（RuntimeWarning: coroutine never awaited） |

> **⚠️ 桌面关闭事件不可用 `page.on_close`**：`page.on_close` 在会话关闭/超时断开时触发，**非**用户点击窗口关闭按钮。桌面端关闭拦截必须用 `page.window.prevent_close = True` + `page.window.on_event`（监听 `ft.WindowEventType.CLOSE`），见 `main.py` 的窗口事件处理器。此为 V1 正确实现，非 V0 遗留。

> **来源说明**：第 8 项（`src_base64` → `src`）与第 16 项（`delta_x` → `primary_delta`）来自 Flet 官方 issue #5238（V1 breaking changes 汇总）。

### 兼容垫片使用规则（已全部删除）

V0→V1 兼容垫片（PageRefMixin / 旧 mock 全局桩）已全部删除。测试侧改用 `conftest._v1_page_compat` fixture 兼容未挂载控件的 `update()`/`page` 访问。

> **`refresh_dropdown_options()` 状态**：已在 Phase R.4.1 删除。声明式 UI 下 options 由 state 派生，`use_state` 触发重建即自动绕过 V1 `Prop.__set__` 值相等优化，该函数不再需要。

### V1 声明式 UI 开发规范

> 宪法 [CLAUDE.md §3.2 UI 模型（强制）](./CLAUDE.md#32--强制要求) 的唯一实现细则。
> 命令式存量（`class X(ft.Container)` + `did_mount`/`will_unmount` + 手动 `self.update()`）已全部重写为声明式范式（见下方「关注点对照」与 [MVVM 表现层](#mvvm-表现层)）。

切到 Flet 0.85.3 后，新增 View/Panel/Component 必须采用声明式 `@ft.component` + 官方 hooks 写法。API 已对 `flet==0.85.3` 实测可用（见下方签名核实）。

#### 1. 关注点对照（命令式作废 → 声明式要求）

| 关注点 | 命令式旧写法（作废） | 声明式要求（宪法标准） |
|--------|------|------|
| 组件定义 | `class X(ft.Container): __init__/super()` | `@ft.component` 函数返回控件树 |
| 状态 | 实例属性 + 手动 `self.update()` | `use_state` 状态变更自动重渲染 |
| 生命周期/副作用 | `did_mount`/`will_unmount` | `use_effect(setup, dependencies, cleanup)` |
| i18n 热切换 | `I18n.subscribe`/`unsubscribe` + `refresh_locale` + 手动刷新 | locale 作为声明式状态源，切换自动重渲染（不再手动订阅/刷新） |
| 下拉刷新 | ~~`refresh_dropdown_options` 两步 update 绕过~~（已删除） | 状态驱动重建 options，自动绕过 |
| 响应式 | `handle_resize` 鸭子分发 + 断点手算 | 窗口尺寸作为 state/observable + `ResponsiveRow`，状态驱动布局 |
| page 引用 | `PageRefMixin` 覆写只读 `control.page` | 组件内经官方上下文机制或事件 `e.page` 获取，垫片已删除 |
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
- **下拉刷新**：options 由 state 派生，`use_state` 触发重建即自动绕过 V1 `Prop.__set__` 值相等优化。`refresh_dropdown_options()` 工具函数已在 Phase R.4.1 删除（声明式下不再需要）。

#### 5. ViewModel 消费（MVVM 桥接）

View 消费 ViewModel 必须经 `use_viewmodel(factory) -> (state, commands)` hook，**不得**直接 `vm = SomeViewModel()` 实例化或注入回调。完整契约与形态见 [MVVM 表现层](#mvvm-表现层)。

```python
import flet as ft
from core.i18n import I18n
from ui.hooks import use_viewmodel          # 已实现，见 ui/hooks.py
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
- `use_viewmodel` hook 已实现（见 [ui/hooks.py](./ui/hooks.py)），新 UI 必须通过本 hook 消费 ViewModel
- 7 个 ViewModel 已全部迁移为 `use_viewmodel` 范式，新代码必须沿用

#### 6. 迁移约束

- **所有命令式 UI 代码已全面重写为声明式**：所有 `class X(ft.Container)` + `did_mount`/`will_unmount` + `self.update()` + `PageRefMixin` + `on_update`/`on_log` 回调注入的代码，已全部重写为 `@ft.component` + `use_viewmodel` 声明式范式。
- `ft.run(before_main=...)` 属可选优化，YAGNI，暂不强制。
- async 窗口/控件方法必须 `await`。
- 命令式 `@ft.control`/`@dataclass` + `did_mount`/`will_unmount` 写法已全面重写完成，命令式控件已删除。

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

### 通用 Flet v1 开发参考

> 宪法依据：CLAUDE.md §5 索引指向本节；本节不重复 API 细节，仅声明引用关系与优先级。

项目规范的 Flet 知识聚焦于**项目专属约束**（上文 21 行 breaking changes 表、V1 声明式 UI 规范、兼容垫片、依赖管理、PyInstaller、升级协同）。通用 Flet v1 概念（路由 `ft.Router`、Services 用法、`SharedPreferences`/`Clipboard`/`StoragePaths`/`FilePicker`、`use_state`/`use_effect`/`use_ref`/`use_dialog`/`create_context` 基础 Hooks、`yield` 中间进度反馈、资源管理、构建打包、性能与错误处理通用模式等）见 [`man/flet-best-practices.md`](./man/flet-best-practices.md)（通用 Flet v1 开发参考手册，API 声明已对 `flet==0.85.3` 实测核实）。

**优先级（冲突时前者覆盖后者）**：

1. [CLAUDE.md](./CLAUDE.md)（红线 R1~R18、架构边界、交互准则）
2. 本文件（CONTRIBUTING.md，项目实现规范）
3. [`man/flet-best-practices.md`](./man/flet-best-practices.md)（通用 Flet v1 参考）

**项目专属约束覆盖通用手册的 8 处分叉**（查阅通用手册时须以下表项目规范为准）：

| 维度 | 通用手册 | 项目规范（优先） |
|------|---------|----------------|
| 适用范围 | Web/移动/桌面通用 | 仅桌面端（`page.window.min_width=1280`） |
| UI 模型 | 裸 `use_state`/`use_effect` 组件 | MVVM + `use_viewmodel` hook（CLAUDE.md §3.2 强制；`use_viewmodel` 已实现，见 [ui/hooks.py](./ui/hooks.py)） |
| 异步线程 | `asyncio.to_thread` / `page.run_thread` | `ThreadPoolManager.run_async(TaskType.IO/CPU)`（CLAUDE.md §3.1 R16 红线） |
| API 约束表 | 通用手册 §17 迁移表 | 本节上文 21 行 breaking changes 表（含检测方式，实测对齐 0.85.3） |
| 版本锁定 | `flet==0.85.3` + charts 解析版本 | `flet`/`flet-desktop`/`flet-charts` 三包全锁 `==0.85.3`（见 [依赖管理](#依赖管理)） |
| 响应式断点 | xs/sm/md/lg/xl/xxl 576~1400 | compact/standard/ultra_wide 1200/1600/2400（见 [`ui/theme.py`](./ui/theme.py) 的 `AppStyles` 断点常量） |
| 桌面打包 | `flet pack`（通用手册 §13.5） | PyInstaller（[`AStockScreener.spec`](./AStockScreener.spec)，见 [PyInstaller 打包](#pyinstaller-打包)） |
| Dialog 管理 | `ft.use_dialog()` Hook（通用手册 §10.1，声明式唯一推荐） | `page.show_dialog()`/`page.pop_dialog()`（V0→V1 迁移完成态；声明式重写已完成） |

通用手册中 Web/移动专属内容（WASM/CDN、APK/IPA 构建、`SafeArea`、Cupertino `adaptive`、移动端 `NavigationBar` 等）项目桌面端不适用，仅作背景知识。

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

> 宪法依据：CLAUDE.md §3.1 R7（测试状态污染红线）与 §1.5（目标驱动与验证）；实现细则以本节为准。

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

> 宪法依据：CLAUDE.md §3.2（pre-commit、Alembic 迁移、质量门控强制）；实现细则以本节为准。

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

本项目使用 11 个 pre-commit hook (定义在 `.pre-commit-config.yaml`，含 Ruff lint/format、裸 `type: ignore` 检测、禁止 `IsolatedAsyncioTestCase`、requirements 同步、版本一致性校验、文档一致性校验、红线自动化校验、import-linter 架构守护)，提交前必须全部通过。

### 数据库迁移

如果修改了数据库模型：

1. 确保创建了新的 Alembic 迁移
2. 迁移必须可逆（实现 `upgrade` 和 `downgrade`）
3. CI 会验证 `upgrade → check → downgrade base → upgrade head` 链

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

1. 先确认 `ui.hooks.use_viewmodel` 是否已满足当前 ViewModel 消费需求；若未满足，先实现/扩展该 hook（见 [MVVM 表现层](#mvvm-表现层)）。
2. 在 `ui/viewmodels/` 下创建对应 ViewModel：暴露不可变 state snapshot、commands、`subscribe(callback) -> unsub`，禁止 import Flet、禁止持有 Flet 控件、禁止调 `page.update()`/`control.update()`。
3. 在 `ui/views/` 下创建 `@ft.component` 声明式 View：只读取 state 渲染控件树，只在事件中调用 commands；禁止 `did_mount`/`will_unmount`/`self.update()`/`UserControl`/`PageRefMixin`（见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范)）。
4. i18n 文案由 VM 输出 key + params，View 按当前 locale 渲染；locale 变化作为 View 层声明式状态源触发重渲染（见 [V1 声明式 UI 开发规范](#v1-声明式-ui-开发规范) 中的 i18n 状态驱动规则）。
5. 响应式布局优先使用声明式 state / props / `ResponsiveRow`，禁止新增 `handle_resize` 鸭子分发式命令式代码。
6. 若需注册新标签页，再修改 `ui/app_layout.py`。
7. UI 事件中的同步 IO/CPU 密集任务必须通过 `ThreadPoolManager.run_async()` 或 `TaskManager.submit_task()` 提交，避免阻塞 Flet 主循环（对应 CLAUDE.md §3.1 R16）。
8. UI 事件回调使用 `@log_ui_action` 装饰器埋点。
9. 按 [变更类型 → 最小验证子集](#变更类型--最小验证子集) 运行 UI 相关验证。

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
| **P1-2** | **Windows 测试事件循环泄露** | 已解决（unit 降 function / integration-e2e 保留 session）。`asyncio_default_test_loop_scope` 从 `session` 降为 `function`，`_stores` 通过 `WeakKeyDictionary` 自动隔离；`_fallback_store` 由精准化的 `_reset_loop_local_fallback` fixture 清理。性能基准对比见 `docs/loop-scope-perf-baseline.md`（function 272.37s vs session 267.07s，回归 1.97%）。 | 已解决。测试顺序污染（日志配置泄漏）也已解决：`_reset_logging_state` fixture 重置 `Logger.disabled` 实例属性、`logging.disable`、named logger level，确保 caplog 跨测试不污染。 |
| **P3** | **doc-lint 自动化第二阶段部分实现（3a 已落地，3b/3c 判定为过度工程）** | 第一阶段已实现：`scripts/check_docs_consistency.py` 覆盖 markdown 锚点死链校验、CLAUDE.md 版本与 `pyproject.toml` 一致、pre-commit hook 数量一致性，已接入 `.pre-commit-config.yaml` `docs-consistency` hook。第二阶段 3a（`NOTE(lazy):` 三要素格式检查）已落地（Phase 5.1），扩展 `check_note_lazy_format()` 函数扫描所有 `.py` 文件。五视角评审判定 3b（红线 R1~R18 编号 append-only 检查）与 3c（"强制状态"与实际 hook/CI job 映射检查）为过度工程，不做。 | 仅保留 3a 守护。upgrade 触发条件：红线违规频发或 CI 自动化专项迭代时重新评估 3b/3c。 |
| **P3** | **strategies/ 层 38 处 except Exception 已标记 NOTE(lazy)（ceiling 动态化），待评估统一走 classify_error** | T34 P3 登记技术债：strategies/ 层 49 处 except Exception 中，P0 必修 5 处 + P2 优化 7 处已完成，剩余 38 处已合理日志的 except Exception 用 `# NOTE(lazy):` 标记保留。Phase 6.1 杠杆评估决策"不引入 `_handle_strategy_exception(e, context)` 统一入口"（YAGNI：`classify_error` 0/38 调用；过度抽象：5 正交维度需 7 字段 context；11 处差异化状态修改无法覆盖）。Phase 6.3 完成 38 处 ceiling 动态化（按方法分组描述，不再硬编码数值）。R9 合规修复已完成（16 处 `str(e)` → `DataSanitizer.sanitize_error(e)`）。评估报告见 `docs/strategy-exception-leverage.md`（本地）。 | 策略层重构或新增策略时统一走 `classify_error` + `classify_severity`。upgrade 触发条件：策略层重构时。E-3 决策：保持现状（NOTE(lazy) 标记已记录 upgrade 条件），强行接入 classify_error 会改变 system severity → raise 行为，违反 §1.4「不做无益重构」。 |
| **P3** | **utils/ 层 39 处 except Exception 已标记 NOTE(lazy)，待评估统一走 classify_error** | R3 场景遗漏检视发现：utils/ 层 spec 任务列表外的 40 处 except Exception（config_handler 28 + exception_hooks 3 + logger 3 + singleton_registry 3 + diagnostics 2 + time_utils 1）。已统一标记 39 处 `# NOTE(lazy):`（三要素齐全：简化内容 + ceiling + upgrade），`time_utils.py:80` 已剔除（无业务消费方语义，调查时判定不适合标记）。40 处中 0 处适合走 `classify_error`（均无业务消费方，属基础设施兜底或合理降级）。 | 后续 utils 层异常处理统一改造时重新评估是否引入 `classify_error`。upgrade 触发条件：utils 层异常处理统一改造时。 |
| **P3** | **红线自动化覆盖部分实现（R1/R4/R12/R13/R14/R15 已落地，R16 暂缓）** | CLAUDE.md §3.1 中 R1/R4/R12/R13/R14/R15/R16 标注「可自动化待实现」。R1 已由 import-linter 契约守护（4 条禁止导入契约）。R4/R12/R13/R14/R15 已由 `scripts/check_redlines.py` 实现，接入 `.pre-commit-config.yaml` `redline-check` hook（44 个单元测试守护）。R16（UI 阻塞）暂缓：AST 扫描误报风险高，需更精确的事件处理器识别逻辑。 | R16 暂缓。upgrade 触发条件：R16 误报控制方案成熟或红线违规频发时重新评估。 |
| **P3** | **litellm 上游限制 `Requires-Python <3.14`** | litellm 1.83.8+（含项目锁定的 1.91.0 及最新 1.92.0）声明 `Requires-Python >=3.10,<3.14`。Python 3.14 下 `uv pip install -r requirements.txt` 因 litellm 无法安装而失败，导致 ci-checks 整个 job 全链路中断（依赖安装 → pre-commit → pip-audit → pyright → 测试 → 迁移 → 覆盖率均无法运行）。lint-fast 仅跑 ruff 不装项目依赖，技术上不受影响，但项目无法在 3.14 运行，lint 预警无实际价值，故一并移除。降级到 1.83.7 不可接受（丢失 aiohttp CVE-2026-47265/CVE-2026-34993 修复）。 | litellm 发布解除 `<3.14` 限制的版本时：① 升级 `pyproject.toml` 的 litellm 版本；② 重新 `uv pip compile` 生成 requirements*.txt；③ 将 3.14 加入 lint-fast 与 ci-checks matrix。upgrade 触发条件：litellm 解除 `<3.14` 限制。 |

---

## 获取帮助

- **GitHub Issues**: 提问或报告问题
- **Email**: louis2sin@gmail.com

---

再次感谢你的贡献！
