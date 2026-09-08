# 治理 ID 对照表 (Governance ID Glossary)

> **目的**：CLAUDE.md / AGENTS.md / 脚本注释中高频出现治理 ID（`P2-07` / `DOC-04` / `review01-A2` / `GDR-06` 等），但指向的检视报告正文多为本地 gitignored 产物（见 [docs/reviews/README.md](../reviews/README.md)「路径说明」），新会话无法解析。本表提供「ID → 一句话含义」的对照，消除上下文噪声；读者无需解析即可理解条款，需要溯源时按表查询。
>
> **状态取值**：`使用中`（当前文档引用）/ `历史`（仅出现在已归档轮次）。
>
> **维护规则**：新增治理 ID 时必须在下方表格登记一行（由 `check_governance_id_glossary` 门禁守护自动加载文档中出现的 ID）；来源轮次不可考的标注「本地检视报告（gitignored）」，不臆造。

## ID 对照表

| ID | 一句话含义 | 来源轮次 | 落地位置 | 状态 |
|----|-----------|---------|---------|------|
| P1-01 | 宪法绝对规则与已批准例外必须集中登记，例外唯一注册入口 | 文档体系检视 P1 系列 | [exceptions.yml](./exceptions.yml) | 使用中 |
| P2-03 | 架构守护范围：import-linter 6 条契约 + AST 静态测试互补 | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| P2-06 | 受检 markdown 递归发现 + 显式排除清单（_DOC_EXCLUDES） | 文档体系检视 P2 系列 | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| P2-07 | 治理文档元数据统一格式（owner/ruleset_version/review_triggers/canonical_for） | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) / [AGENTS.md](../../AGENTS.md) 元数据块 | 使用中 |
| P2-11 | 红线规则类型划分（INVARIANT/DEFAULT/NEW_CODE/MIGRATION_TARGET/WORKFLOW/EXCEPTIONABLE） | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) §3.1 | 使用中 |
| P2-12 | 主题 → canonical 正本映射（任务决策树机器可读镜像） | 文档体系检视 P2 系列 | [canonical-topics.yml](./canonical-topics.yml) / [CLAUDE.md](../../CLAUDE.md) §1.8 | 使用中 |
| P2-13 | R18 执行决策树（先识别对象再判定隔离） | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) §3.1 | 使用中 |
| P2-16 | 跨平台命令策略（文档只描述命令目的，不绑定 shell） | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) §1.9 | 使用中 |
| P2-17 | 架构设计/公共契约任务路由（满足 ADR-0001 触发条件时新增 ADR） | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) §1.8 / [canonical-topics.yml](./canonical-topics.yml) | 使用中 |
| P3-03 | AGENTS.md 定位为跨工具自动加载的规则入口 | 文档体系检视 P3 系列 | [AGENTS.md](../../AGENTS.md) | 使用中 |
| DOC-01 | 规则集元数据一致性（ruleset_version / last_reviewed 三文档同步） | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-04 | 决策树与其机器可读镜像双向一致（CLAUDE.md §1.8 ↔ canonical-topics.yml） | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-05 | canonical 入口承担条件路由责任（声明 workflow 的入口必须含指向该 workflow 的链接） | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-07 | 文档索引全覆盖 + 检视方法论文档登记 | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-08 | AGENTS.md 生成区块与 redlines.yml 渲染一致 | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) / [ADR-0006](../adr/0006-override-agents-pure-pointer.md) | 使用中 |
| DOC-09 | 治理 id 引用一致性（EX-\d{4} 双向：注册表 ↔ 消费文档） | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-11 | 文档索引全覆盖（docs/** 无孤儿文档） | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-13 | 孤儿治理文件纳入元数据/索引/白名单（AGENTS.md 收编） | 文档体系·AI 可执行性专项（DOC 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) / [ADR-0006](../adr/0006-override-agents-pure-pointer.md) | 使用中 |
| GOV-01 | 宪法不得引用未登记例外（EX 引用必须落在注册表） | 文档体系检视（GOV 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| UX-07 | 全系统 UI/UX 专项审视轮次 | UX 专项检视 | [docs/reviews/README.md](../reviews/README.md) | 历史 |
| GDR-06 | 治理文档不得硬编码 Flet 补丁版本号（SHALL NOT `x.y.z` 形式） | 文档体系检视（GDR 系列，2026-09-08） | [CLAUDE.md](../../CLAUDE.md) §3.2 | 使用中 |
| GDR-09 | 治理 ID 对新会话不可解析 → 建立对照表 + 体系入口声明 | 文档体系检视（GDR 系列，2026-09-08） | [governance-ids.md](./governance-ids.md) / [CLAUDE.md](../../CLAUDE.md) 头部 | 使用中 |
| review01-A2 | 夜间预测编排下沉到 services/scheduled_jobs（消除 utils→strategies→services 循环） | review01 架构分层与依赖治理 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| review01-A6 | 两条 MAJOR 结构性循环通过依赖注入消除，不再靠延迟 import 压制 | review01 架构分层与依赖治理 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| review03-C11 | CacheManager↔BaseDao 循环由中立模块 engine_provider.py 切断 | review03 数据层与持久化 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
