# ADR-0002: 文档架构分层决策

> Status: Accepted
> Date: 2026-07-17
> Owner: 架构维护者
> Supersedes: CONTRIBUTING.md 历史版本中「3b/3c 过度工程不做」决策的部分前提（3b 由 ADR-0003 单独推翻）

## Context

AStockScreener 项目文档在 Phase 2 渐进式披露拆分前存在以下漂移：

- CLAUDE.md 单文件承载宪法 + 红线 + 架构边界 + 交互准则 + 完整模板 + 实现细则，行数膨胀，AI 自动加载上下文成本高
- CONTRIBUTING.md 单文件承载人类贡献者指南 + 命令参考 + 实现规范手册（含 9 个章节的 Flet V1 API 约束、单例模板、策略模板等），1358+ 行
- man/flet-best-practices.md 与 CONTRIBUTING.md Flet 章节内容部分重叠，引用语义漂移
- docs/ 目录被 `.gitignore` 排除，无法承载正式文档
- 测试 `TestNoDeadDocsLinks` 主动禁止被跟踪 markdown 引用 docs/ 路径

文档体系检视报告（P0-1 / P1-1）指出，这种单文件巨石结构违反渐进式披露原则，且无法支撑后续治理（机器可读红线映射、ADR 目录等）。

## Decision

采用四层文档架构，职责边界严格分离：

| 层级 | 文件 / 目录 | 职责 | 阅读对象 | 自动加载 |
|------|------------|------|---------|---------|
| **宪法层** | `CLAUDE.md` | 红线 R1~R18 / 架构边界 / AI 交互准则 / 任务决策树 / 验证命令 / 反幻觉护栏 | AI 助手（每次会话）+ 工程师 | AI 自动加载 |
| **入口索引层** | `CONTRIBUTING.md` | 人类贡献者指南 + 命令参考 + 实现规范入口索引（章节 stub 指向 docs/） | 工程师 | 否 |
| **专项深入层** | `docs/{guides,architecture,patterns,flet,debt,adr,governance}/` | 流程指南 / 架构深入 / 模式深入 / Flet 约束 / 技术债 / ADR / 治理文件 | 按需查阅 | 否 |
| **专题深度层** | `man/` | 专题深度文档（如 man/flet-best-practices.md 已改为薄 stub 指向 docs/flet/） | 按需查阅 | 否 |

**职责边界约束**：
- CLAUDE.md §3（红线）/ §4（架构边界）/ §1.8（决策树）/ §1.9（验证命令）/ §1.10（反幻觉护栏）必须保留本体；§5 索引指向 docs/。
- CONTRIBUTING.md 不重复 CLAUDE.md 的红线与架构边界；只保留入口索引 + 人类贡献者命令 + 简短规范。
- docs/ 各子目录按类别归类，跨目录交叉引用用相对路径 + 锚点。
- man/ 不再承载 Flet 内容（改为 stub），其他 man/ 专题保留。
- precedence: CLAUDE.md > CONTRIBUTING.md > docs/ > man/（冲突时前者覆盖后者）。

## Consequences

- **正向**：AI 自动加载的 CLAUDE.md 保持精简（仅宪法核心），上下文成本可控；CONTRIBUTING.md 648 行（≤ 800 行目标）；docs/ 渐进式披露，按需查阅；机器可读治理文件（如 redlines.yml）有独立目录 docs/governance/。
- **负向**：跨文件引用增加，锚点死链风险上升；维护者需同时关注 4 层文档的一致性。
- **缓解**：`scripts/check_docs_consistency.py` 守护锚点死链 + 相对链接死链 + 版本一致性 + pre-commit hook 数量一致性 + Flet 版本漂移 + NOTE(lazy) 三要素；`tests/unit/test_docs_consistency.py` + `tests/unit/test_docs_canonical_examples.py` 契约测试守护；pre-commit `docs-consistency` hook 在每次提交时强制校验。

## Alternatives

- **单文件巨结构（保持 Phase 2 前状态）**：拒绝。CLAUDE.md 行数膨胀导致 AI 上下文成本不可控；CONTRIBUTING.md 1358+ 行违反渐进式披露；docs/ 被 gitignore 排除导致正式文档无处安放。
- **三层架构（取消 man/）**：拒绝。man/ 下其他专题（database-account-separation / table-partitioning-strategy）仍在使用，强行合并到 docs/ 会破坏历史引用；改为保留 man/ 但 flet-best-practices.md 退化为 stub 指向 docs/flet/。
- **两层架构（CLAUDE.md + docs/，取消 CONTRIBUTING.md 入口索引）**：拒绝。CONTRIBUTING.md 是 GitHub 默认入口，新贡献者第一站；取消会破坏发现性。
