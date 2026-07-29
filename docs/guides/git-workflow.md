# Git 工作流与分支策略

> 来源：从 CONTRIBUTING.md 迁移

> 对应 [CLAUDE.md §3.1 R18](../../CLAUDE.md#31--绝对禁止)。本节承接宪法中关于 git 隔离开发的强制要求，提供分支模型、命名规范、worktree 标准流程。**使用 git worktree 隔离开发，确保主工作区整洁**。

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
- 多步骤实现任务（含 AI 助手驱动的实现任务）

**豁免场景**：
- 单文件文档纯改（如仅改 README.md 一处）
- 单行修复（typo、配置值修正）
- bug 复现脚本（临时一次性代码）
- 已在 `.worktrees/<branch>/` 内的开发（已满足隔离）

### 标准工作流

完整生命周期（在主仓库根目录起算）：

```bash
# 1. 创建 worktree（默认 .worktrees/<branch-name>/，已在 .gitignore）
git worktree add .worktrees/feature-strategy-macd -b feature/strategy-macd
cd .worktrees/feature-strategy-macd

# 2. 项目 setup（在 worktree 内独立 venv）
uv venv
.venv\Scripts\activate         # Windows；或 source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-dev.txt
pre-commit install             # 安装 git hooks

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
# AI 助手创建 PR 时必须使用 --template 加载模板，禁止手写简化 PR body
gh pr create --title "feat(strategy): add MACD crossover" \
  --template .github/PULL_REQUEST_TEMPLATE.md \
  --base main

# 7. PR 通过 Merge Queue 合并后，清理 worktree（回主工作区执行）
cd ../..  # 回主仓库根
git worktree remove .worktrees/feature-strategy-macd
git worktree prune
git branch -d feature/strategy-macd       # Squash Merge 后本地分支可删
```

### AI 助手创建 PR 标准流程（强制）

**背景**：`gh pr create --body "..."` 不会自动加载 `.github/PULL_REQUEST_TEMPLATE.md`（GitHub Web UI 才会自动加载）。AI 助手使用 `gh pr create` 时必须显式加载模板，否则会生成不合规的简化 PR 描述。

**强制要求**（AI 助手创建 PR 时必须遵守）：

1. **必须使用 `--template` 参数加载 PR 模板**：

   ```bash
   gh pr create --title "<conventional commit title>" \
     --template .github/PULL_REQUEST_TEMPLATE.md \
     --base main
   ```

   此命令会打开编辑器加载模板内容，AI 助手应在模板基础上填写各章节，保留模板结构（标题、勾选项、注释提示）。

2. **禁止手写简化 PR body**：不得用 `--body "简短描述"` 跳过模板。CONTRIBUTING.md「PR 描述模板」中"无需手动复制该模板"的说明仅适用于 GitHub Web UI，不适用于 `gh pr create`。

3. **必须如实勾选模板自检清单**：
   - 「修改类型」勾选对应项，删除无关项
   - 「测试覆盖」按实际验证结果勾选，未运行的验证项不得勾选
   - 「提交前自检清单」逐条核对，未通过项须补齐后再创建 PR
   - 「文档同步校验」按本次变更范围勾选

4. **PR 标题必须遵循 Conventional Commits**：`<type>(<scope>): <description>`，与提交信息规范一致。

5. **关联工单**：如有关联 Issue，在「关联工单/Issue」章节填写 `Closes #XXX` 或 `Relates to #XXX`。

**验证**：PR 创建后，AI 助手应通过 `gh pr view --json body` 确认 PR body 包含模板全部章节。

> 完成开发后，按 PR 反馈决策：merge（合并入 main）/ keep（保留分支继续迭代）/ discard（丢弃分支并清理 worktree）。决策依据为 PR review 结果与剩余迭代计划。

### AI 助手创建 Issue 标准流程（强制）

**背景**：与 PR 模板类似，`gh issue create` 不会自动加载 `.github/ISSUE_TEMPLATE/` 下的模板。项目使用 Issue Forms（.yml）格式（`bug_report.yml` / `feature_request.yml`），`config.yml` 中 `blank_issues_enabled: false` 禁止空白 issue。AI 助手必须显式指定模板，否则会生成不合规的简化 issue。

**强制要求**（AI 助手创建 Issue 时必须遵守）：

1. **必须使用 `--template` 参数加载 Issue 模板**（模板名称对应 `.github/ISSUE_TEMPLATE/*.yml` 的 `name` 字段）：

   ```bash
   # Bug 报告
   gh issue create --title "<简明标题>" --template "🐛 Bug 报告"
   # 功能请求
   gh issue create --title "<简明标题>" --template "✨ 功能请求"
   ```

   此命令会打开编辑器加载模板表单内容，AI 助手应在模板基础上填写各章节，保留模板结构。

2. **禁止手写简化 issue body**：不得用 `--body "简短描述"` 跳过模板，也不得创建空白 issue（`blank_issues_enabled: false`）。

3. **必须如实填写模板表单各字段**：
   - Bug 报告：需包含 Bug 描述、复现步骤、期望行为、实际行为、环境信息
   - 功能请求：需包含动机、提案方案、替代方案

4. **Issue 标题应简明扼要**，描述问题或需求的核心。

5. **关联工单**：如有关联 PR 或其他 issue，在 body 中填写 `Relates to #XXX`。

**验证**：Issue 创建后，AI 助手应通过 `gh issue view <number> --json body` 确认 body 包含模板全部字段。

> 安全漏洞报告请走私密渠道（SECURITY.md / GitHub Security Advisories），不要用公开 issue 报告安全漏洞。

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
