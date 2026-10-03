# AGENTS.md — AStockScreener 跨工具规则入口

> **对应版本**：0.11.0（产品版本，与 pyproject.toml 一致）<!-- x-release-please-version -->
> **元数据**（P2-07 统一格式，规则集版本与产品版本分离，与 CLAUDE.md 一致）：
> - owner: 架构维护者
> - ruleset_version: 1.9.0（规则集版本，与 CLAUDE.md 同步，规则变更时递增）
> - last_reviewed: 2026-09-24（与 CLAUDE.md 同步）
> - review_triggers: 红线新增/变更、Flet 升级、检视报告发布时
> - canonical_for: 跨工具红线最小安全集（导出镜像，非语义正本）

本文件为跨工具自动加载的规则入口（P3-03）。**修改任何代码前先读 [CLAUDE.md](./CLAUDE.md)（项目宪法）§3 红线与 §1.8 任务路由，再按该路由展开必读文件；未读取前只做只读调查。**

- **语言约定**：始终使用简体中文回复。
- **只读默认**：回答/诊断默认只读，文件或外部状态修改须用户明确授权（对应 CLAUDE.md §1.0 / §1.1）。

下方为跨工具红线最小安全集，由 [redlines.yml](./docs/governance/redlines.yml) 生成的导出镜像（由 `check_agents_md_sync` 门禁守护，见 CONTRIBUTING.md「文档一致性校验」）。组成规则：redlines.yml 中全部 `rule_type: INVARIANT` 红线，另加 R18（`rule_type: WORKFLOW`，守护工作区整洁）。正本以 CLAUDE.md §3.1 / redlines.yml 为准，本区块 **SHALL NOT 手工修改**，如需变更请改正本后同步：

<!-- generated:redlines-invariant -->
- R2：异常吞没 — 吞没 asyncio.CancelledError (必须 raise 以配合优雅停机)
- R3：模糊压制 — 使用 # type: ignore 时不带 [error-code]（人类理由写在方括号外，格式 # type: ignore[错误码]  # 原因；pre-commit 强制拦截）
- R4：SQL 注入 — 在 asyncpg 原生查询中使用 %s 占位符（必须用 $1, $2, ...）；亦禁止 SQL 字面量赋值、f-string 拼接 SQL、text(f...) f-string 形态及测试文件内同类写法（SQL 必须参数化，或经 text() 常量承载）
- R7：测试状态污染 — 单例未隔离 (单元测试由 tests/unit/conftest.py 的 _reset_all_singletons autouse fixture 自动重置注册单例；需精细控制单例初始化状态时使用 tests/conftest.py 的 singleton_state 上下文管理器)
- R9：敏感信息泄露 — 日志/异常消息直接打印明文 Token / API Key / 密码 / 个人信息 (必须经 DataSanitizer 脱敏)
- R10：硬编码密钥 — 在代码或测试中硬编码 API Key / DB 密码 (必须从 keyring 或环境变量读取)
- R18：未隔离开发 — 新特性、重构、跨多文件修改任务未启用 git worktree 隔离即在主工作区开发（豁免：单文件文档纯改、单行修复、bug 复现脚本、.worktrees/ 内已有隔离）
<!-- /generated -->

> **入选规则（治理决策）**：本区块组成规则 = [redlines.yml](./docs/governance/redlines.yml) 中全部 `rule_type: INVARIANT` 红线，另加 R18（`rule_type: WORKFLOW`，守护工作区整洁，跨工具一致适用）；不含其余 WORKFLOW / `仅人工评审` 红线（如 R11/R17，其为 `NEW_CODE` 类，不构成跨工具无条件不变量）。区块内容以本文件上方生成的 `<!-- generated:redlines-invariant -->` 区块为唯一数据源（由 `check_agents_md_sync` 门禁守护，禁止手工改）；红线上调为 INVARIANT 或新增守护工作区整洁的红线时，仅需改正本 [redlines.yml](./docs/governance/redlines.yml) 再同步生成区块，本段为规则描述、不随清单逐条复制。

任务类型 → 必读正本的完整路由见 [docs/governance/canonical-topics.yml](./docs/governance/canonical-topics.yml)（机器可读，每类任务一个 canonical 入口）。

实现规范、代码模板与工作流步骤见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 最小验证命令

命令正本为 [CONTRIBUTING.md](./CONTRIBUTING.md)「常用开发与测试命令」（本区块为生成镜像，勿手工修改）：

<!-- generated:min-verify-commands -->
- **变更相关门禁**（提交/PR 前，顺序与 `.github/workflows/ci_cd.yml` 一致）：`ruff check .` → `ruff format --check .` → `pre-commit run --all-files` → `pyright` → `python -m pytest tests/unit/ -v --tb=short`
- **最小验证子集**（按变更范围裁剪，勿全量跑）：见 [CONTRIBUTING.md](./CONTRIBUTING.md#变更类型--最小验证子集)
- **仅 Markdown / 治理文档改动**：`python scripts/check_docs_consistency.py` + `python -m pytest tests/unit/test_docs_consistency.py`
- **不得声称未运行项已通过**；无法运行的验证需说明原因
<!-- /generated -->
