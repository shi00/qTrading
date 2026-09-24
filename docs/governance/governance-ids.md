# 治理 ID 对照表 (Governance ID Glossary)

> **目的**：CLAUDE.md / AGENTS.md / 脚本注释中高频出现治理 ID（`P2-07` / `DOC-04` / `review01-A2` / `GDR-06` 等），但指向的检视报告正文多为本地 gitignored 产物（见 [docs/reviews/README.md](../reviews/README.md)「路径说明」），新会话无法解析。本表提供「ID → 一句话含义」的对照，消除上下文噪声；读者无需解析即可理解条款，需要溯源时按表查询。
>
> **状态取值**：`使用中`（当前文档引用）/ `历史`（仅出现在已归档轮次）。
>
> **维护规则**：新增治理 ID 时必须在下方表格登记一行（由 `check_governance_id_glossary` 门禁守护受检治理文档中出现的 ID——`CHECKED_DOCS` 全集（排除 `CHANGELOG.md` 自动生成发布日志与 `Plans.md` 本地计划）加 `docs/governance/*.yml` 机器可读治理文件；`.py`（`scripts/` + `tests/`）扫描自 DS-02 起以 WARNING 渐进部署）。来源轮次不可考的标注「本地检视报告（gitignored）」，不臆造。
>
> **编号格式规范（DS-02）**：治理 ID 中的数字序号统一采用两位前导零形式（如 `P1-04` / `P2-09` / `UX-03`，不用 `P1-4` / `UX-3`）。存量代码中存在的单位数写法（如 `P1-4`、`UX-2`）视为同一发现的无前导零别名，在表格中以独立行登记并在「一句话含义」注明「即 P1-04 的无前导零别名」后即被门禁放行。`-99` 后缀（如 `DOC-99` / `P9-99` / `UIX-99`）为门禁测试夹具 ID，**不得登记**。新增使用治理 ID 时须用规范形，不引入新的三/多位数写法。

## ID 对照表

| ID | 一句话含义 | 来源轮次 | 落地位置 | 状态 |
|----|-----------|---------|---------|------|
| P1-01 | 宪法绝对规则与已批准例外必须集中登记，例外唯一注册入口 | 文档体系检视 P1 系列 | [exceptions.yml](./exceptions.yml) | 使用中 |
| P2-03 | 架构守护范围：import-linter 契约（layers + 补充 forbidden） + AST 静态测试互补 | 文档体系检视 P2 系列 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| P2-06 | 受检 markdown 递归发现 + 显式排除清单（_build_doc_excludes） | 文档体系检视 P2 系列 | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
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
| GOV-04 | 检视结论结构化登记（docs/reviews/findings/）：报告正文 gitignored 导致结论系统性丢失，结论 id/判据入库可溯源 | 文档体系检视（GOV 系列） | [findings/README.md](../reviews/findings/README.md) / [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GOV-07 | ruleset-changelog 规则集复核状态为规则性约定（不写日期快照），动态状态由轮次表与三份治理文档承载 | 文档体系检视（GOV 系列） | [ruleset-changelog.md](./ruleset-changelog.md) | 使用中 |
| GOV-10 | R20 报告模式补齐升级期限四要素（误报率阈值 / 复核期限 / 责任人 / 翻转触发），消除「无期限渐进部署永久停留 WARNING」反模式 | 文档体系检视（GOV 系列） | [ruleset-changelog.md](./ruleset-changelog.md)「R20 报告模式升级期限」 | 使用中 |
| UX-07 | 全系统 UI/UX 专项审视轮次 | UX 专项检视 | [docs/reviews/README.md](../reviews/README.md) | 历史 |
| GDR-06 | 治理文档不得硬编码 Flet 补丁版本号（SHALL NOT `x.y.z` 形式） | 文档体系检视（GDR 系列，2026-09-08） | [CLAUDE.md](../../CLAUDE.md) §3.2 | 使用中 |
| GDR-09 | 治理 ID 对新会话不可解析 → 建立对照表 + 体系入口声明 | 文档体系检视（GDR 系列，2026-09-08） | [governance-ids.md](./governance-ids.md) / [CLAUDE.md](../../CLAUDE.md) 头部 | 使用中 |
| review01-A2 | 夜间预测编排下沉到 services/scheduled_jobs（消除 utils→strategies→services 循环） | review01 架构分层与依赖治理 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| review01-A6 | 两条 MAJOR 结构性循环通过依赖注入消除，不再靠延迟 import 压制 | review01 架构分层与依赖治理 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| review03-C11 | CacheManager↔BaseDao 循环由中立模块 engine_provider.py 切断 | review03 数据层与持久化 | [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| review08-D1 | 新闻抓取两公开方法去重：抽取 _fetch_stock_news_core 共享内核，publish_time 统一为 UTC tz-naive（消除 8h 口径偏差） | review08 数据层与外部依赖 | [news_fetcher.py](../../data/external/news_fetcher.py) | 使用中 |
| D6-1 | utils/scheduler_service → data.domain_services.offline_calendar 日历降级例外（EX-0016） | 本地检视报告（gitignored） | [exceptions.yml](./exceptions.yml) EX-0016 | 使用中 |
| D6-2 | 同 D6-1（D6-1/D6-2 日历降级修复） | 本地检视报告（gitignored） | [exceptions.yml](./exceptions.yml) EX-0016 | 使用中 |
| GDR-01 | 补录 utils 横切叶子契约（"R1: utils must not import business layers"）ignore_imports 现存 17 条 R1 例外（EX-0001~EX-0016，含 D6-1/D6-2 日历降级 EX-0016；EX-0019 由 REVIEW-06 TO-04 增补） | 文档体系检视（GDR 系列） | [exceptions.yml](./exceptions.yml) | 使用中 |
| TO-04 | utils/scheduler_service 幂等键写入经 engine_provider 查引擎状态（R5 守卫，R1 例外 EX-0019） | REVIEW-06 任务编排与运行时韧性 | [exceptions.yml](./exceptions.yml) EX-0019 | 使用中 |
| GDR-10 | AGENTS.md 最小安全集排除非 INVARIANT 红线，R1/R16 已有独立自动化兜底故不收录 | 文档体系检视（GDR 系列，2026-09-08） | [0006-override-agents-pure-pointer.md](../adr/0006-override-agents-pure-pointer.md) | 使用中 |
| GDR-12 | ADR 决策文档文件级索引完整性检查（CONTRIBUTING.md 登记全部 docs/adr/*.md） | 文档体系检视（GDR 系列，2026-09-08） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GDR-13 | 书名号式章节引用一致性检查（`<文档路径>「<章节名>」`） | 文档体系检视（GDR 系列，2026-09-08） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| DOC-14 | 检视报告「发现清单」状态列随轮次表维护，不回溯改写已归档报告 | 文档体系·AI 可执行性专项（DOC 系列） | [docs/reviews/README.md](../reviews/README.md) | 使用中 |
| P0-1 | 单文件巨石结构违反渐进式披露原则，需分层治理 | 文档体系检视（P0 系列） | [0002-document-layering.md](../adr/0002-document-layering.md) | 使用中 |
| P1-1 | 同 P0-1：单文件巨石结构问题 | 文档体系检视（P1 系列） | [0002-document-layering.md](../adr/0002-document-layering.md) | 使用中 |
| P1-04 | UX-06 冷启动验证（生产模式全页构造 proxy 成本基线） | UX 专项检视 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| P1-06 | R5 由 INVARIANT 降为 EXCEPTIONABLE 分类修正（方案1；[exceptions.yml](./exceptions.yml) EX-0017/EX-0018 引用。注：原与「UX-06 冷启动验证」共用 P1-04 编号（同名异义双义登记），2026-09-22 GOV-06 拆分为独立编号 P1-06，P1-04 不再复用） | 本地检视报告（gitignored） | [exceptions.yml](./exceptions.yml) EX-0017/EX-0018 | 使用中 |
| P1-05 | 文档权威按主题确定正本，而非按目录层级全局覆盖 | 文档体系检视（P1 系列） | [CLAUDE.md](../../CLAUDE.md) §1 / [flet/v1-api-constraints](../flet/v1-api-constraints.md) | 使用中 |
| P1-2 | CI 自动化专项迭代（红线机器可读映射落地触发条件） | 文档体系检视（P1 系列） | [0003-overturn-3b-3c-deferral.md](../adr/0003-overturn-3b-3c-deferral.md) | 使用中 |
| P1-3 | Flet 绝对化表述分层（「新代码禁止/契约守护」规则例外集中登记） | 文档体系检视（P1 系列） | [flet/v1-api-constraints.md](../flet/v1-api-constraints.md) | 使用中 |
| P2-2 | 引入极简 ADR 目录沉淀不可逆/持续影响架构决策 | 文档体系检视（P2 系列） | [0001-record-architecture-decisions.md](../adr/0001-record-architecture-decisions.md) | 使用中 |
| P2-4 | UI 可访问性最低标准章节（新增 UI 控件必须满足） | 文档体系检视（P2 系列） | [flet/accessibility-baseline.md](../flet/accessibility-baseline.md) | 使用中 |
| P3-02 | canonical_for 链接语义正本元数据字段 | 文档体系检视（P3 系列） | [singleton-lifecycle.md](../architecture/singleton-lifecycle.md) / [mvvm.md](../patterns/mvvm.md) | 使用中 |
| P3-04 | ruleset_version 无变更日志，需极简 ruleset-changelog.md 逐次登记 | 文档体系检视（P3 系列） | [ruleset-changelog.md](./ruleset-changelog.md) | 使用中 |
| UX-01 | 导航深链协议（TOPIC_NAVIGATE） | UX 专项检视 | [flet/ui-ux-best-practices.md](../flet/ui-ux-best-practices.md) | 使用中 |
| UX-04 | 导航深链协议（与 UX-01 同源） | UX 专项检视 | [flet/ui-ux-best-practices.md](../flet/ui-ux-best-practices.md) | 使用中 |
| UX-05 | 复盘聚合统计（策略汇总展开区：按 (strategy_name, benchmark_code) 分组的日序列统计/样本分级） | UX 专项检视 | [review_stats_service.py](../../data/domain_services/review_stats_service.py) | 使用中 |
| UX-06 | 冷启动到导航可交互端到端 SLA 未实测，以 proxy 构成基线替代 | UX 专项检视 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review03-C13 | 数据库连接生命周期契约 | review03 数据层与持久化 | [patterns/data-sync.md](../patterns/data-sync.md) | 使用中 |
| review03-C19 | embedded_pg TimeoutExpired 包装为 EmbeddedPgMaintenanceError 分类处理 | review03 数据层与持久化 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review05-E11 | config_handler↔db_config_service 循环打破；db 逻辑按域拆分迁至 utils/config/db.py | review05 配置与依赖治理 | [exceptions.yml](./exceptions.yml) | 使用中 |
| review05-E15 | toast_manager 去单例化薄壳复核（非事实单例，R7 隔离改 conftest autouse fixture） | review05 配置与依赖治理 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review07-G5 | tests/ 非 attr-defined `# type: ignore` 存量 226 处无 human reason（渐进升级） | review07 治理与门禁审计 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| review07-G20 | R16 部分守护：VM `__init__` 构造已注册单例检测实现 | review07 治理与门禁审计 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| UIX-01 | pubsub 多订阅者语义红线：同一 topic `unsubscribe_topic` 会移除整个 session 该 topic 的全部 handler | 本地检视报告（gitignored） | [mvvm.md](../patterns/mvvm.md) | 使用中 |
| UIX-03 | 订阅在 mount effect 阶段才建立，消除首帧通知丢失 | 本地检视报告（gitignored） | [hooks.py](../../ui/hooks.py) | 使用中 |
| UIX-05 | UI 层四种状态机制并存时"什么状态放哪里"的正本归属约定 | 本地检视报告（gitignored） | [mvvm.md](../patterns/mvvm.md) | 使用中 |
| UIX-07 | 复杂 ViewModel 拆分的 Mixin 组合模式（C3 / UIX-07） | 本地检视报告（gitignored） | [mvvm.md](../patterns/mvvm.md) | 使用中 |
| UIX-04 | dependencies 用 resolved_vm 对象身份，替代恒为 `[]` 的 deps（换实例重订阅） | 本地检视报告（gitignored） | [hooks.py](../../ui/hooks.py) | 使用中 |
| UIX-09 | ViewModel 可观察协议薄门面（组合化契约强化） | 本地检视报告（gitignored） | [observable_mixin.py](../../ui/viewmodels/observable_mixin.py) | 使用中 |
| UIX-10 | `@ft.component` 渲染函数顶层不得执行 logger/print 副作用（须迁入 use_effect 或事件回调） | 本地检视报告（gitignored） | [check_redlines.py](../../scripts/check_redlines.py) | 使用中 |
| UIX-12 | 字段级错误提示（`ft.TextField(error=...)` 原生插槽程序化关联） | 本地检视报告（gitignored） | [backtest_config_panel.py](../../ui/components/backtest/backtest_config_panel.py) | 使用中 |
| UIX-13 | toast 迁移到 ToastManager + 响应式断点结构性批次（M12 / UIX-13） | 本地检视报告（gitignored） | [accessibility-baseline.md](../flet/accessibility-baseline.md) | 使用中 |
| UIX-14 | 声明式三态组件（空态/错误态/加载态）统一复用，消除手工复制 | 本地检视报告（gitignored） | [state_views.py](../../ui/components/state_views.py) | 使用中 |
| UIX-16 | 未引用 i18n key 的 ratchet baseline 门禁（基线计数只降不升） | 本地检视报告（gitignored） | [test_i18n_keys_completeness.py](../../tests/unit/test_i18n_keys_completeness.py) | 使用中 |
| UIX-17 | VM locale remediation——placeholder 卡 error 的 i18n fallback，去硬编码中文默认值 | 本地检视报告（gitignored） | [test_uix_17_vm_locale_remediation.py](../../tests/unit/ui/viewmodels/test_uix_17_vm_locale_remediation.py) | 使用中 |
| UIX-18 | e2e_ids.py 已声明 public 常量/方法未引用或未标注 `# reserved` 即报错 | 本地检视报告（gitignored） | [check_e2e_anchors.py](../../scripts/check_e2e_anchors.py) | 使用中 |
| GDR-03 | 未登记的日期前缀检视报告文件须提示「轮次表未登记」并指引落根目录 | 文档体系检视（GDR 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GDR-04 | requirement 主题正本存在且能被 `_extract_decision_tree_targets` 决策树提取 | 文档体系检视（GDR 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GDR-05 | 真实 redlines.yml 中 R9 的 enforcement 含 check_redlines.py 与安全扫描（N1/N4 断言） | 文档体系检视（GDR 系列） | [test_docs_consistency.py](../../tests/unit/test_docs_consistency.py) | 使用中 |
| GDR-07 | `_GITIGNORED_ARTIFACT_DIRS` 仅含真实 gitignored 目录；EX 引用语料豁免目录 | 文档体系检视（GDR 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GDR-08 | release/packaging 主题存在，决策树映射与合并白名单正确绑定 | 文档体系检视（GDR 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| GDR-11 | 校验 CLAUDE.md §4.2 声明的 core/ 模块清单与实际 core/*.py 文件一致 | 文档体系检视（GDR 系列） | [check_docs_consistency.py](../../scripts/check_docs_consistency.py) | 使用中 |
| review08-D3 | LIMIT_ 概念管线把股票名当概念名写入并泄漏给所有概念消费方；修正语义：get_concepts 前缀过滤 + 停写 + clear_all_limit_concepts 重命名 | review08 AKShare 开源组件复用专项检视 | [stock_dao.py](../../data/persistence/daos/stock_dao.py) / [concept_sync.py](../../data/sync/concept_sync.py) | 使用中 |
| review01-A4 | 引擎生命周期与 DAO 注册清单自 CacheManager 拆分为 EngineManager / DaoRegistry 组合对象 | review01 架构分层与依赖治理 | [engine_manager.py](../../data/cache/engine_manager.py) | 使用中 |
| review01-A7 | 应用会话入口自 main.py 迁移至 app/application.py（main.py 瘦身收敛） | review01 架构分层与依赖治理 | [application.py](../../app/application.py) | 使用中 |
| review01-A8 | 启动编排提取为 ApplicationSession 上下文，分阶段 partial_state 供调用方决定回滚粒度 | review01 架构分层与依赖治理 | [application.py](../../app/application.py) | 使用中 |
| review01-A9 | per-session 服务初始化状态（替代 bootstrap 模块级 flag，多 session 下安全） | review01 架构分层与依赖治理 | [startup_controller.py](../../app/startup_controller.py) | 使用中 |
| review01-A13 | 收口全项目异常类型标注到统一入口（error_classifier 默认归 operational） | review01 架构分层与依赖治理 | [error_classifier.py](../../utils/error_classifier.py) | 使用中 |
| review03-C1 | 读路径块级失败显式化（fail-fast，禁止 `_read_db` 默认 suppress_errors=True 吞错） | review03 数据层与持久化 | [base_dao.py](../../data/persistence/daos/base_dao.py) | 使用中 |
| review03-C2 | 超大批量 `_save_upsert` 每块独立事务（UPSERT 幂等，重跑安全） | review03 数据层与持久化 | [base_dao.py](../../data/persistence/daos/base_dao.py) | 使用中 |
| review03-C4 | `_read_db_select` max_rows 安全阀（结果行数超限抛 ValueError） | review03 数据层与持久化 | [base_dao.py](../../data/persistence/daos/base_dao.py) | 使用中 |
| review03-C7 | 消除 f-string 拼 SQL；选股/行情 SQL 改静态模板 + 确定性占位符 | review03 数据层与持久化 | [screener_dao.py](../../data/persistence/daos/screener_dao.py) | 使用中 |
| review03-C9 | 数据字典不得存在空 columns 字段（删除优于空壳残留） | review03 数据层与持久化 | [test_data_dictionary.py](../../tests/unit/test_data_dictionary.py) | 使用中 |
| review03-C12 | 写入失败默认不再吞错（suppress_errors=False 保底，data 写丢失不可静默） | review03 数据层与持久化 | [cache_manager.py](../../data/cache/cache_manager.py) | 使用中 |
| review03-C14 | 共享交易日回退链：日历服务→已同步行情最大日期→显式抛 TradeDateUnavailableError | review03 数据层与持久化 | [trade_calendar_service.py](../../data/domain_services/trade_calendar_service.py) | 使用中 |
| review03-C15 | 生产构建（非 E2E/DEBUG）下 STRICT_QUALITY_GATE=false 拒绝启动 | review03 数据层与持久化 | [bootstrap.py](../../app/bootstrap.py) | 使用中 |
| review03-C16 | E2E 模式判定统一收口到 utils/app_env（单一事实来源） | review03 数据层与持久化 | [app_env.py](../../utils/app_env.py) | 使用中 |
| review03-C18 | embedded PG start/stop 状态变迁统一由 RLock 串行化（避免双 Popen） | review03 数据层与持久化 | [service.py](../../data/persistence/embedded_postgres/service.py) | 使用中 |
| review05-E1 | 异常类型未命中 isinstance/type 分支、依赖字符串匹配时须留 debug 日志 | review05 配置与依赖治理 | [error_classifier.py](../../utils/error_classifier.py) | 使用中 |
| review05-E3 | 结构化异常基类 AppError（ErrorInfo，语义由异常自身携带，免事后推断） | review05 配置与依赖治理 | [errors.py](../../core/errors.py) | 使用中 |
| review05-E8 | 兜底裸 token 检测/部分遮蔽（无 key=/URL/JSON 前缀的最后防线） | review05 配置与依赖治理 | [sanitizers.py](../../utils/sanitizers.py) | 使用中 |
| review05-E9 | sanitize_paths 对文本中 Windows/Unix 路径脱敏（traceback 路径替换为 `<PATH>`） | review05 配置与依赖治理 | [sanitizers.py](../../utils/sanitizers.py) | 使用中 |
| review05-E18 | DAO 三原语慢操作阈值上收到 log_decorators（单一权威源，即 Q-P2-7） | review05 配置与依赖治理 | [log_decorators.py](../../utils/log_decorators.py) | 使用中 |
| review05-E19 | 进程内运行时指标聚合 MetricsRegistry（成功/失败计数，诊断包导出） | review05 配置与依赖治理 | [metrics.py](../../utils/metrics.py) | 使用中 |
| review07-G1 | 未确认 AI 外发政策的具名 fixture（安全门控未确认路径专项覆盖） | review07 治理与门禁审计 | [test_ai_mixin.py](../../tests/unit/test_ai_mixin.py) | 使用中 |
| review07-G2 | 同类用例参数化合并保留原断言强度（删除焊死私有方法签名的 inspect.signature 断言） | review07 治理与门禁审计 | [test_base_dao.py](../../tests/unit/test_base_dao.py) | 使用中 |
| review07-G3 | 弱断言 baseline 下降 KPI（每季度降≥10%，WARNING 不阻断） | review07 治理与门禁审计 | [scan_weak_assertions.py](../../scripts/scan_weak_assertions.py) | 使用中 |
| review07-G9 | mock 真实外部边界（而非 mock 业务方法），使被测业务逻辑真实走查 | review07 治理与门禁审计 | [conftest.py](../../tests/conftest.py) | 使用中 |
| review07-G11 | `_reset_singleton` 改行为断言，不焊死内部 key 名 | review07 治理与门禁审计 | [test_singletons_isolation.py](../../tests/unit/test_singletons_isolation.py) | 使用中 |
| review07-G14 | 分层覆盖率达标率统计（不阻断，CI 观测分层门禁差距） | review07 治理与门禁审计 | [check_per_file_coverage.py](../../scripts/check_per_file_coverage.py) | 使用中 |
| review07-G18 | R4 补充检测：业务层「SQL 关键字开头+%s」字面量 + f-string SQL 模板 | review07 治理与门禁审计 | [check_redlines.py](../../scripts/check_redlines.py) | 使用中 |
| review07-G19 | 单例识别条件扩展（DAO 注册引擎同步不可漏改，与 R13 描述一致） | review07 治理与门禁审计 | [check_redlines.py](../../scripts/check_redlines.py) | 使用中 |
| BIZ-04 | AI 策略无法被回测验证：回测默认 disable_ai，AI 结论与实盘不可比（评审报告 01-requirement-closure.md §2.4，本地 gitignored；ADR-0009 引用） | 本地检视报告（gitignored） | [0009-ai-snapshot-replay.md](../adr/0009-ai-snapshot-replay.md) | 使用中 |
| BT-03 | R21 变体：可信度元数据在持久化边界丢失（`data_warnings` / `failed_signal_dates` / 配置快照等已知不可信信号落库后被丢弃，UI 渲染为「无问题」） | review03 业务语义与可信度 | [CLAUDE.md](../../CLAUDE.md) R21 / [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| AI-02 | 业务语义字段（`score` / `ai_score` / `confidence` 等）的缺失表示：必须用 `None`/哨兵，禁止填充业务上合法的具体值（检视报告 04 AI-02，缺陷根因见 R21） | 本地检视报告（gitignored） | [CONTRIBUTING.md](../../CONTRIBUTING.md)「业务语义字段的缺失表示」 | 使用中 |
| E4 | OSS 检视：import-linter 6 条手工 forbidden 契约重构为 1 条 layers + 2 条 forbidden（AST 静态测试随之退役，例外注册表路径校验保留） | OSS 检视（E 系列） | [pyproject.toml](../../pyproject.toml) / [CLAUDE.md](../../CLAUDE.md) §4.1 | 使用中 |
| DAT-07 | 回测不可复现：财报无修订历史，UPSERT 覆盖使历史回测结果随时间漂移（属结论可信度边界，非代码欠债） | review03 数据层与持久化 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| DAT-08 | 回测前视：申万行业为当前快照，`sw_industry_member` 主键不含日期，跨分类调整期回测存在前视（含存量污染 `industry_tushare`） | review03 数据层与持久化 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| BT-05 | 回测路径 `_BacktestQualityProxy` 硬编码 GOLD 绕过数据质量门控（第一步显式化，第二步待设计） | review03 业务语义与可信度 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| CON-04 | subprocess 不可取消长任务停机交互：terminate 仅终止 sidecar 包装 / dump 中断残留不自动清理 / restore 拒绝取消 | review02 | [known-technical-debt.md](../debt/known-technical-debt.md) | 使用中 |
| DATA-01 | 已知金额列裸比较未做单位换算（北向资金 `north_money` 错 100 倍）——催生 R20 统一换算入口 | business-review-2026-09-11（业务检视） | [redlines.yml](./redlines.yml) R20 / [prototype_business_redlines.py](../../scripts/prototype_business_redlines.py) | 使用中 |
| DATA-02 | 同 DATA-01（龙虎榜 `net_amount` 错 10000 倍等），统一经 `threshold_in_data_unit()` 换算后再比较 | business-review-2026-09-11（业务检视） | [redlines.yml](./redlines.yml) R20 | 使用中 |
| SYNC-01 | 水位线（checkpoint）写入非单调，断点续传水位可能回退——催生 R22 单调性红线 | business-review-2026-09-11（业务检视） | [redlines.yml](./redlines.yml) R22 | 使用中 |
