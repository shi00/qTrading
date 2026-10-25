# ADR-0002: 文档架构分层决策

> Status: Partial Superseded by ADR-0005 (3c portion only) and ADR-0006 (AGENTS.md pointer portion)
> Date: 2026-07-17
> Owner: 架构维护者
> Supersedes: CONTRIBUTING.md 历史版本中「3b/3c 过度工程不做」决策的部分前提（3b 由 ADR-0003 单独推翻）
> 注：3c 部分后续由 ADR-0005 推翻（详见 ADR-0005）；AGENTS.md 的纯指针定位后续由 ADR-0006 修改（详见 ADR-0006）；检视报告落盘位置由 2026-09-08 Errata 澄清，检视结论登记机制由 2026-10-23 Errata 废弃（详见职责边界约束）。
> 注（2026-09-16 Errata）：红线编号自本 ADR（记录为 R1~R18）后已扩展至 R23（2026-10-25 核对已至 R24，见 Errata 第 6 条）；下方决策正文中的红线编号为当时快照，当前红线范围一律以 docs/governance/redlines.yml 机器可读正本为准。
> 注（2026-09-24 Errata，已被 2026-10-23 Errata 取代）：检视**结论登记**曾允许每条发现附 ≤20 行关键证据片段（代码位置 + 最小复现），该片段属「结论登记」而非 SHALL NOT 所指的「报告正文」（长证据 / 大段代码）。
> 注（2026-10-23 Errata）：检视**结论结构化登记机制**（docs/reviews/findings/，GOV-04）整体废弃——检视报告正文与结论均不入库，正文一律落根目录 reviews/（gitignored）；未关闭发现迁入 docs/debt/known-technical-debt.md 跟进，详见职责边界约束。

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
- 文档权威按主题确定正本，而非按目录层级全局覆盖（P1-05 修订，见 CLAUDE.md §1「文档权威性（按主题正本）」）；冲突时先按主题确定正本，再以正本裁决。
- 新增/修改任何 `docs/` 文档，必须同步登记到 `docs/README.md` 索引。
- 检视报告分层落盘（Errata 2026-09-08，2026-10-23 修订）：过程性轮次报告正文统一落根目录 `reviews/`（受 `.gitignore` 保护，为本地工作区检视产物，不入版本控制）；仓库内 `docs/reviews/` 仅承载专项方法论资产并在该 README 中登记；检视**结论结构化登记**机制（`docs/reviews/findings/`，GOV-04）于 2026-10-23 废弃——SHALL NOT 范围覆盖报告正文与检视结论（含其证据片段），检视结论与报告均不入库；未关闭发现迁入 `docs/debt/known-technical-debt.md` 跟进。
- 修改宪法（CLAUDE.md §3）红线表，必须同步更新 `docs/governance/redlines.yml` 机器可读镜像（由 `check_redlines_yaml_consistency` 门禁守护）。

## Consequences

- **正向**：AI 自动加载的 CLAUDE.md 保持精简（仅宪法核心），上下文成本可控；CONTRIBUTING.md 648 行（≤ 800 行目标）；docs/ 渐进式披露，按需查阅；机器可读治理文件（如 redlines.yml）有独立目录 docs/governance/。
- **负向**：跨文件引用增加，锚点死链风险上升；维护者需同时关注 4 层文档的一致性。
- **缓解**：`scripts/check_docs_consistency.py` 守护锚点死链 + 相对链接死链 + 版本一致性 + pre-commit hook 数量一致性 + Flet 版本漂移 + NOTE(lazy) 三要素；`tests/unit/test_docs_consistency.py` + `tests/unit/test_docs_canonical_examples.py` 契约测试守护；pre-commit `docs-consistency` hook 在每次提交时强制校验。

## Alternatives

- **单文件巨结构（保持 Phase 2 前状态）**：拒绝。CLAUDE.md 行数膨胀导致 AI 上下文成本不可控；CONTRIBUTING.md 1358+ 行违反渐进式披露；docs/ 被 gitignore 排除导致正式文档无处安放。
- **三层架构（取消 man/）**：拒绝。man/ 下其他专题（database-account-separation / table-partitioning-strategy）仍在使用，强行合并到 docs/ 会破坏历史引用；改为保留 man/ 但 flet-best-practices.md 退化为 stub 指向 docs/flet/。
- **两层架构（CLAUDE.md + docs/，取消 CONTRIBUTING.md 入口索引）**：拒绝。CONTRIBUTING.md 是 GitHub 默认入口，新贡献者第一站；取消会破坏发现性。

## Errata / 勘误

> 2026-10-25 补充（现状核对，不修正 Decision 正文与决策语义）

1. **docs/ 子目录清单（Decision 表「专项深入层」）**：表中 `docs/{guides,architecture,patterns,flet,debt,adr,governance}/` 为决策时点枚举。当前 `docs/README.md` 索引登记 9 个子目录——上列 7 个，另加 `docs/reviews/`（检视方法论资产）与 `docs/bug-fix/`（AI 问题修复指南）；专项深入层的职责定义不变，新增/修改 docs 文档的登记正本仍为 `docs/README.md`。
2. **man/ 两专题的实际状态（Alternatives 第 2 点）**：「仍在使用」指文档保留并被引用，不代表功能已落地——`man/database-account-separation.md` 自述 STATUS 为 Design Guide（仓库未提供自动化账号分离脚本）、`man/table-partitioning-strategy.md` 自述 STATUS 为 Planning（未实施），与 CLAUDE.md §5「规划/设计文档、非现行实现」口径一致；`man/flet-best-practices.md` 仍为 stub 并指向 `docs/flet/README.md`（已核对）。
3. **CONTRIBUTING.md 行数（Consequences 正向）**：决策时点 648 行，2026-10-25 核对为 798 行，仍满足「≤ 800 行」目标，分层结论不受影响。
4. **requirements/ 与 resources/ 的归属**：二者不属本 ADR 四层文档分层对象——`requirements/USER_REQUIREMENTS.md` 为用户需求正本、`resources/` 为静态资源与打包物料，索引见 CONTRIBUTING.md「文档索引」与「完整目录结构」。
5. **「分层」的维度区分**：本 ADR 的「分层」指**文档**分层；CLAUDE.md §4.1 为**代码**分层架构（`app/ → ui/ → strategies/ → services/ → data/ → core/`，`utils/` 为横切关注点），二者维度不同、不冲突；完整目录树以 CONTRIBUTING.md「完整目录结构」为准（CLAUDE.md §4.1 已明确委托）。
6. **红线编号（2026-09-16 备注）**：该备注中的「已扩展至 R23」为当时快照；2026-10-25 核对 `docs/governance/redlines.yml` 已至 R24（R24 时点正确性）。当前红线编号、范围与规则类型一律以 `redlines.yml` 机器可读正本为准。

---

## 完成判定（canonical 入口）

- 文档分层遵循主题正本制，同一主题只保留一个 canonical 入口，未新建第二正本
- 新增治理文档/规则已同步 canonical-topics.yml 与 CLAUDE.md §1.8 决策树（门禁强制双向一致）

_最小验证命令：_ `python scripts/check_docs_consistency.py` + 对应文档契约测试（`tests/unit/test_docs_consistency.py`）。
