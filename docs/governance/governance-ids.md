# 治理 ID 对照表 (Governance ID Glossary)

> **目的**：CLAUDE.md / AGENTS.md / 脚本注释中高频出现治理 ID（`P2-07` / `DOC-04` / `review01-A2` / `GDR-06` 等），但指向的检视报告正文多为本地 gitignored 产物（见 [docs/reviews/README.md](../reviews/README.md)「路径说明」），新会话无法解析。本表提供「ID → 一句话含义」的对照，消除上下文噪声；读者无需解析即可理解条款，需要溯源时按表查询。
>
> **状态取值**：`使用中`（当前文档引用）/ `历史`（仅出现在已归档轮次）。
>
> **维护规则**：新增治理 ID 时必须在下方表格登记一行（由 `check_governance_id_glossary` 门禁守护受检治理文档中出现的 ID——`CHECKED_DOCS` 全集（排除 `CHANGELOG.md` 自动生成发布日志与 `Plans.md` 本地计划）加 `docs/governance/*.yml` 机器可读治理文件）；来源轮次不可考的标注「本地检视报告（gitignored）」，不臆造。

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
| D6-1 | utils/scheduler_service → data.domain_services.offline_calendar 日历降级例外（EX-0016） | 本地检视报告（gitignored） | [exceptions.yml](./exceptions.yml) EX-0016 | 使用中 |
| D6-2 | 同 D6-1（D6-1/D6-2 日历降级修复） | 本地检视报告（gitignored） | [exceptions.yml](./exceptions.yml) EX-0016 | 使用中 |
| GDR-01 | 补录 contract 5 ignore_imports 现存 15 条 R1 例外（EX-0001~EX-0015） | 文档体系检视（GDR 系列） | [exceptions.yml](./exceptions.yml) | 使用中 |
| GDR-10 | AGENTS.md 最小安全集排除非 INVARIANT 红线，R1/R16 已有独立自动化兜底故不收录 | 文档体系检视（GDR 系列，2026-09-08） | [0006-override-agents-pure-pointer.md](../adr/0006-override-agents-pure-pointer.md) | 使用中 |
| GDR-12 | ADR 决策文档文件级索引完整性检查（CONTRIBUTING.md 登记全部 docs/adr/*.md） | 文档体系检视（GDR 系列，2026-09-08） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GDR-13 | 书名号式章节引用一致性检查（`<文档路径>「<章节名>」`） | 文档体系检视（GDR 系列，2026-09-08） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-14 | 检视报告「发现清单」状态列随轮次表维护，不回溯改写已归档报告 | 文档体系·AI 可执行性专项（DOC 系列） | [docs/reviews/README.md](../reviews/README.md) | 使用中 |
| P0-1 | 单文件巨石结构违反渐进式披露原则，需分层治理 | 文档体系检视（P0 系列） | [0002-document-layering.md](../adr/0002-document-layering.md) | 使用中 |
| P1-1 | 同 P0-1：单文件巨石结构问题 | 文档体系检视（P1 系列） | [0002-document-layering.md](../adr/0002-document-layering.md) | 使用中 |
| P1-04 | UX-06 冷启动验证（生产模式全页构造 proxy 成本基线） | UX 专项检视 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| P1-05 | 文档权威按主题确定正本，而非按目录层级全局覆盖 | 文档体系检视（P1 系列） | [CLAUDE.md](../../CLAUDE.md) §1 / [flet/v1-api-constraints](../flet/v1-api-constraints.md) | 使用中 |
| P1-2 | CI 自动化专项迭代（红线机器可读映射落地触发条件） | 文档体系检视（P1 系列） | [0003-overturn-3b-3c-deferral.md](../adr/0003-overturn-3b-3c-deferral.md) | 使用中 |
| P1-3 | Flet 绝对化表述分层（「新代码禁止/契约守护」规则例外集中登记） | 文档体系检视（P1 系列） | [flet/v1-api-constraints.md](../flet/v1-api-constraints.md) | 使用中 |
| P2-2 | 引入极简 ADR 目录沉淀不可逆/持续影响架构决策 | 文档体系检视（P2 系列） | [0001-record-architecture-decisions.md](../adr/0001-record-architecture-decisions.md) | 使用中 |
| P2-4 | UI 可访问性最低标准章节（新增 UI 控件必须满足） | 文档体系检视（P2 系列） | [flet/accessibility-baseline.md](../flet/accessibility-baseline.md) | 使用中 |
| P2-05 | 回测图表语境修复方案任务标识 | 文档体系检视（P2 系列） | [task-plans/ux-12-backtest-chart-context-plan.md](../task-plans/ux-12-backtest-chart-context-plan.md) | 使用中 |
| P3-02 | canonical_for 链接语义正本元数据字段 | 文档体系检视（P3 系列） | [singleton-lifecycle.md](../architecture/singleton-lifecycle.md) / [mvvm.md](../patterns/mvvm.md) | 使用中 |
| UX-01 | 导航深链协议（TOPIC_NAVIGATE） | UX 专项检视 | [flet/ui-ux-best-practices.md](../flet/ui-ux-best-practices.md) | 使用中 |
| UX-04 | 导航深链协议（与 UX-01 同源） | UX 专项检视 | [flet/ui-ux-best-practices.md](../flet/ui-ux-best-practices.md) | 使用中 |
| UX-06 | 冷启动到导航可交互端到端 SLA 未实测，以 proxy 构成基线替代 | UX 专项检视 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| UX-11 | 回测图表 Semantics/锚点经验（摘要/图例 data-testid 独立语义边界） | UX 专项检视 | [task-plans/ux-12-backtest-chart-context-plan.md](../task-plans/ux-12-backtest-chart-context-plan.md) | 使用中 |
| UX-12 | 回测图表语境修复方案 | UX 专项检视 | [task-plans/ux-12-backtest-chart-context-plan.md](../task-plans/ux-12-backtest-chart-context-plan.md) | 使用中 |
| review03-C13 | 数据库连接生命周期契约 | review03 数据层与持久化 | [patterns/data-sync.md](../patterns/data-sync.md) | 使用中 |
| review03-C19 | embedded_pg TimeoutExpired 包装为 EmbeddedPgMaintenanceError 分类处理 | review03 数据层与持久化 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review05-E11 | config_handler↔db_config_service 循环打破；db 逻辑按域拆分迁至 utils/config/db.py | review05 配置与依赖治理 | [exceptions.yml](./exceptions.yml) | 使用中 |
| review05-E15 | toast_manager 去单例化薄壳复核（非事实单例，R7 隔离改 conftest autouse fixture） | review05 配置与依赖治理 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review07-G5 | tests/ 非 attr-defined `# type: ignore` 存量 226 处无 human reason（渐进升级） | review07 治理与门禁审计 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review07-G20 | R16 部分守护：VM `__init__` 构造已注册单例检测实现 | review07 治理与门禁审计 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
