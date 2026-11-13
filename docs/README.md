# Project Documentation Index

本目录承载从 CONTRIBUTING.md 渐进式披露拆分出的专项深入文档。

## 目录结构

- [guides/](./guides/) — 流程类指南（Git workflow / 测试 / CI/CD / 依赖管理 / How-To / FinBERT 模型产线）
- [guides/documentation-maintenance.md](./guides/documentation-maintenance.md) — 文档长期编写、变更与维护流程唯一正本（ADR-0018；L01—L12 控制合同 / 六类变更协议 / E01—E12 演习合同）
- [architecture/](./architecture/) — 架构类深入（单例生命周期模板等，含 TushareClient 特殊说明）
- [agent-contract.md](./architecture/agent-contract.md) — 智能体权限例外与允许操作矩阵（ADR-0016 已批准；运行期工具仍关闭）
- [agent-scenario-ownership.md](./architecture/agent-scenario-ownership.md) — 智能体场景归属与调用方契约（W01-B 已批准；不改变当前运行路径）
- [patterns/](./patterns/) — 模式类深入（DAO / 策略 / 数据同步 / MVVM 等，data-sync.md 含 Tushare Syncer 设计模式）
- [flet/](./flet/) — Flet UI/UX 设计、声明式 API、无障碍、项目差异、MCP 核验、升级与 CanvasKit E2E 避坑入口
- [debt/](./debt/) — 已知架构技术债（以 `known-technical-debt.md` 条目为准）
- [adr/](./adr/) — 架构决策记录（ADR），ADR 索引见下方「ADR 索引」小节
- [governance/](./governance/) — 治理类机器可读文件（如 redlines.yml / exceptions.yml / canonical-topics.yml）
- [governance/README.md](./governance/README.md) — 治理入口：规则参考 / 任务路由 / 维护入口（规则参考与任务路由视图受控生成）
- [reviews/](./reviews/) — AI 代码检视指南（核心协议 + 稳定规则 ID + 专项 Profile + schema/policy 分离 + evals 评测集）
- [bug-fix/](./bug-fix/) — AI 问题修复指南（核心协议 + 专项 Profile + 附录，三层拆分）

## 文件级目录索引（受控生成）

> 本节与「ADR 索引」由 `scripts/docs_generated.py` 按封闭注册表从作者源（文件自身标题/角色）生成：
> 更新用 `python scripts/check_docs_consistency.py --update`，一致性由 `check_docs_generated` 门禁守护，**勿手工修改生成区块**。正式文件逐篇可发现（含 .md/.yml/.yaml/.json）即本索引的验收语义。

<!-- generated:docs-readme-file-index -->
| 文档 | 标题 / 角色 |
|------|--------------|
| [adr/0001-record-architecture-decisions.md](./adr/0001-record-architecture-decisions.md) | ADR-0001: Record Architecture Decisions |
| [adr/0002-document-layering.md](./adr/0002-document-layering.md) | ADR-0002: 文档架构分层决策 |
| [adr/0003-overturn-3b-3c-deferral.md](./adr/0003-overturn-3b-3c-deferral.md) | ADR-0003: 推翻「3b/3c 过度工程不做」决策 |
| [adr/0004-redlines-yaml-format.md](./adr/0004-redlines-yaml-format.md) | ADR-0004: redlines.yml 格式选择 YAML |
| [adr/0005-enforcement-mapping-check.md](./adr/0005-enforcement-mapping-check.md) | ADR-0005: 落地 3c enforcement 字段映射检查 |
| [adr/0006-override-agents-pure-pointer.md](./adr/0006-override-agents-pure-pointer.md) | ADR-0006: AGENTS.md 从纯指针改为最小安全集 + 指针 |
| [adr/0007-screening-history-override-pk.md](./adr/0007-screening-history-override-pk.md) | ADR-0007: screening_history 唯一键改覆盖语义 |
| [adr/0008-no-ai-execution.md](./adr/0008-no-ai-execution.md) | ADR-0008: 不给 AI 执行权，仅输出评分和文本 |
| [adr/0009-ai-snapshot-replay.md](./adr/0009-ai-snapshot-replay.md) | ADR-0009: AI 结论快照回放（AI Effect Attribution） |
| [adr/0010-screening-history-append-only.md](./adr/0010-screening-history-append-only.md) | ADR-0010: screening_history 唯一键改回 append-only（含 run_id） |
| [adr/0011-litellm-router-failover.md](./adr/0011-litellm-router-failover.md) | ADR-0011: 手写 failover 循环迁移至 litellm.Router（L4） |
| [adr/0012-news-sentiment-inference-paradigm.md](./adr/0012-news-sentiment-inference-paradigm.md) | ADR-0012: 新闻情绪打分的本地推理范式（CLS embedding + numpy 分类头） |
| [adr/0013-runtime-selection-final-gguf.md](./adr/0013-runtime-selection-final-gguf.md) | ADR-0013: 新闻情绪打分推理运行时选型终局（保持 GGUF，ORT / OpenVINO 双 PoC FAIL） |
| [adr/0014-unified-model-scheduling.md](./adr/0014-unified-model-scheduling.md) | ADR-0014: 统一模型管理的场景调度契约（ScenarioExecutor 独占候选调度） |
| [adr/0015-fingerprint-driven-release.md](./adr/0015-fingerprint-driven-release.md) | ADR-0015: FinBERT 产线指纹驱动发布（内容寻址去重） |
| [adr/0016-agent-permission-exception.md](./adr/0016-agent-permission-exception.md) | ADR-0016: 智能体有限权限例外与允许操作矩阵 |
| [adr/0017-version-ssot.md](./adr/0017-version-ssot.md) | ADR-0017: 版本号单一正本（Version SSOT）收敛 |
| [adr/0018-documentation-single-source.md](./adr/0018-documentation-single-source.md) | ADR-0018: 文档体系单写入源与长期治理（Documentation Single Source） |
| [architecture/agent-contract.md](./architecture/agent-contract.md) | 智能体权限例外与允许操作矩阵（ADR-0016 操作契约） |
| [architecture/agent-scenario-ownership.md](./architecture/agent-scenario-ownership.md) | 智能体场景归属与调用方契约（W01-B 已批准） |
| [architecture/overview.md](./architecture/overview.md) | 架构原则总览（架构正文唯一正本） |
| [architecture/singleton-lifecycle.md](./architecture/singleton-lifecycle.md) | 单例模式实现模板 |
| [bug-fix/appendix.md](./bug-fix/appendix.md) | AI 问题修复附录 |
| [bug-fix/core-protocol.md](./bug-fix/core-protocol.md) | AI 问题修复核心协议 |
| [bug-fix/profiles.md](./bug-fix/profiles.md) | AI 问题修复专项 Profile |
| [debt/known-technical-debt.md](./debt/known-technical-debt.md) | 已知架构技术债 (Known Technical Debt) |
| [flet/accessibility-baseline.md](./flet/accessibility-baseline.md) | UI 可访问性最低标准 |
| [flet/api-verification-template.md](./flet/api-verification-template.md) | Flet API 核验记录模板 |
| [flet/canvaskit-rendering-e2e-guide.md](./flet/canvaskit-rendering-e2e-guide.md) | Flutter Web CanvasKit 渲染行为与 E2E 定位指南 |
| [flet/mcp-usage.md](./flet/mcp-usage.md) | Flet MCP 使用规范 |
| [flet/project-differences.md](./flet/project-differences.md) | Flet V1 项目差异与高风险 API |
| [flet/README.md](./flet/README.md) | Flet 开发文档入口 |
| [flet/ui-ux-best-practices.md](./flet/ui-ux-best-practices.md) | Flet 桌面 UI/UX 最佳实践 |
| [flet/upgrade-checklist.md](./flet/upgrade-checklist.md) | Flet V1 升级检查清单 |
| [flet/v1-api-constraints.md](./flet/v1-api-constraints.md) | Flet V1 API 关键约束 |
| [governance/canonical-topics.yml](./governance/canonical-topics.yml) | 主题 → canonical 正本映射 (CLAUDE.md §1.8 决策树的机器可读镜像) |
| [governance/checker-migration-map.md](./governance/checker-migration-map.md) | 42 项文档检查迁移映射表（D5） |
| [governance/exceptions.yml](./governance/exceptions.yml) | 例外注册表 (CLAUDE.md §3.1 硬规则的集中例外清单) |
| [governance/governance-ids.md](./governance/governance-ids.md) | 治理 ID 对照表 (Governance ID Glossary) |
| [governance/README.md](./governance/README.md) | 治理规则与任务路由（Governance README） |
| [governance/redlines.yml](./governance/redlines.yml) | 红线机器可读映射 (CLAUDE.md §3.1 表格的 YAML 镜像) |
| [governance/ruleset-changelog.md](./governance/ruleset-changelog.md) | 规则集版本变更日志 (Ruleset Changelog) |
| [guides/ci-cd.md](./guides/ci-cd.md) | CI/CD 流水线与门禁 |
| [guides/dependency-management.md](./guides/dependency-management.md) | 依赖管理与 PyInstaller 打包 |
| [guides/documentation-maintenance.md](./guides/documentation-maintenance.md) | 文档长期编写、变更与维护流程（documentation-maintenance） |
| [guides/finbert-pipeline.md](./guides/finbert-pipeline.md) | FinBERT 模型产线流水线机制 |
| [guides/git-workflow.md](./guides/git-workflow.md) | Git 工作流与分支策略 |
| [guides/how-to.md](./guides/how-to.md) | 标准开发工作流 (How-To) |
| [guides/testing.md](./guides/testing.md) | 测试规范 |
| [patterns/ai-service.md](./patterns/ai-service.md) | AI/LLM 服务（ai-service）canonical 正本 |
| [patterns/ai-strategy-mixin.md](./patterns/ai-strategy-mixin.md) | AI 策略混入 |
| [patterns/application-service.md](./patterns/application-service.md) | 应用服务层（application-service）canonical 正本 |
| [patterns/backtest-correctness.md](./patterns/backtest-correctness.md) | 回测与选股的正确性（Backtest Correctness） |
| [patterns/config-quality-perf.md](./patterns/config-quality-perf.md) | 配置管理、质量门控、性能监控 |
| [patterns/dao-pattern.md](./patterns/dao-pattern.md) | DAO 模式 |
| [patterns/data-sync.md](./patterns/data-sync.md) | 数据同步架构 |
| [patterns/mvvm.md](./patterns/mvvm.md) | MVVM 表现层 |
| [patterns/polars-vectorized-strategy.md](./patterns/polars-vectorized-strategy.md) | Polars 向量化策略基类 |
| [patterns/strategy-template.md](./patterns/strategy-template.md) | 策略模式实现模板 |
| [patterns/task-manager.md](./patterns/task-manager.md) | TaskManager 任务生命周期 |
| [README.md](./README.md) | Project Documentation Index |
| [reviews/ai-review.md](./reviews/ai-review.md) | AI 代码检视核心协议 |
| [reviews/appendix.md](./reviews/appendix.md) | AI 代码检视附录 |
| [reviews/evals/false-positives/case-001.md](./reviews/evals/false-positives/case-001.md) | 误报 #001：Flet 框架自动转义被误报 XSS |
| [reviews/evals/insufficient-info/case-001.md](./reviews/evals/insufficient-info/case-001.md) | 信息不足 #001：状态转换缺契约无法判断 |
| [reviews/evals/known-defects/case-001.md](./reviews/evals/known-defects/case-001.md) | 已知缺陷 #001：asyncpg 查询使用 %s 占位符 |
| [reviews/evals/prompt-injection/case-001.md](./reviews/evals/prompt-injection/case-001.md) | 提示注入 #001：代码注释中的恶意指令 |
| [reviews/evals/README.md](./reviews/evals/README.md) | AI 代码检视评测集 |
| [reviews/evals/scenario-gaps/case-001.md](./reviews/evals/scenario-gaps/case-001.md) | 场景遗漏 #001：消息处理缺幂等保护 |
| [reviews/quality-dimensions.md](./reviews/quality-dimensions.md) | 通用质量维度 |
| [reviews/README.md](./reviews/README.md) | 检视方法论索引 (Review Index) |
| [reviews/review-policy.yaml](./reviews/review-policy.yaml) | AI 代码检视门禁策略 |
| [reviews/review-profiles/ai-ml-llm.md](./reviews/review-profiles/ai-ml-llm.md) | AI、ML、LLM 与非确定性系统专项 Profile |
| [reviews/review-profiles/cli-iac.md](./reviews/review-profiles/cli-iac.md) | CLI、脚本与基础设施即代码专项 Profile |
| [reviews/review-profiles/database-migration.md](./reviews/review-profiles/database-migration.md) | 数据库与迁移专项 Profile |
| [reviews/review-profiles/frontend-mobile.md](./reviews/review-profiles/frontend-mobile.md) | 前端与移动端专项 Profile |
| [reviews/review-profiles/library-sdk-plugin.md](./reviews/review-profiles/library-sdk-plugin.md) | 库、SDK 与插件专项 Profile |
| [reviews/review-profiles/messaging-data-pipeline.md](./reviews/review-profiles/messaging-data-pipeline.md) | 消息、任务与数据管道专项 Profile |
| [reviews/review-profiles/project-profile.md](./reviews/review-profiles/project-profile.md) | AStockScreener 项目 Profile |
| [reviews/review-profiles/README.md](./reviews/review-profiles/README.md) | AI 代码检视专项 Profile 索引 |
| [reviews/review-profiles/third-party-rpa.md](./reviews/review-profiles/third-party-rpa.md) | 第三方集成与浏览器自动化专项 Profile |
| [reviews/review-profiles/web-api.md](./reviews/review-profiles/web-api.md) | Web/API 专项 Profile |
| [reviews/review-result.schema.json](./reviews/review-result.schema.json) | — |
| [reviews/scenario-completeness.md](./reviews/scenario-completeness.md) | 场景完整性与遗漏 |
<!-- /generated -->

## 文档层次

文档权威按主题确定正本，而非按目录层级全局覆盖。目录仅作组织用途：

1. CLAUDE.md — 项目宪法（AI 自动加载，红线/架构边界/交互准则）
2. CONTRIBUTING.md — 入口索引 + 最小命令 + PR 流程
3. docs/ — 专项深入文档（本目录）
4. man/ — 专题深度文档（database-account-separation / table-partitioning-strategy 为规划/设计文档、非现行实现；flet-best-practices 为指向 docs/flet/ 的 stub）

按主题权威正本见 [CLAUDE.md](../CLAUDE.md) §1「文档权威性（按主题正本）」；冲突时先按主题确定正本，再以正本裁决。

## Tushare 文档索引

Tushare 相关文档分散在多个章节，按主题索引如下：

| 主题 | 文档位置 |
|------|---------|
| 单例生命周期与特殊说明 | [architecture/singleton-lifecycle.md](./architecture/singleton-lifecycle.md#tushareclient-特殊说明) |
| Syncer 设计模式（限流/质量门控/错误处理/取消传播） | [patterns/data-sync.md](./patterns/data-sync.md#tushare-syncer-设计模式) |
| 集成工作流简述 | [guides/how-to.md](./guides/how-to.md#51-tushare-集成工作流简述) |
| 配置说明（token 获取/积分档位/降级行为） | [README.md](../README.md#配置-tushare-数据源) |
| Token 安全（存储/脱敏/熔断/静态守护） | [SECURITY.md](../SECURITY.md#tushare-token-security) |
| 已知技术债 | [debt/known-technical-debt.md](./debt/known-technical-debt.md) |
| 红线自动化守护 | `scripts/check_redlines.py` 的 `check_R_tushare_token_log`（R9 红线专属守护） |

## FinBERT 模型产线文档索引

FinBERT 新闻情绪模型产线相关文档分散在多个章节，按主题索引如下（产线机制总览正本为 [guides/finbert-pipeline.md](./guides/finbert-pipeline.md)）：

| 主题 | 文档位置 |
|------|---------|
| 产线机制总览（流水线阶段 / 指纹路由 / 双 Gate / 发布与闭环） | [guides/finbert-pipeline.md](./guides/finbert-pipeline.md) |
| 本地推理范式（CLS embedding + numpy 分类头） | [adr/0012-news-sentiment-inference-paradigm.md](./adr/0012-news-sentiment-inference-paradigm.md) |
| 推理运行时选型终局（GGUF） | [adr/0013-runtime-selection-final-gguf.md](./adr/0013-runtime-selection-final-gguf.md) |
| 指纹驱动发布决策（内容寻址去重） | [adr/0015-fingerprint-driven-release.md](./adr/0015-fingerprint-driven-release.md) |
| 模型产物获取操作（Release 下载 / tokenizer） | [guides/how-to.md「13. 获取新闻情绪模型产物」](./guides/how-to.md#13-获取新闻情绪模型产物finbert-gguf-v-release) |
| 运行时集成（AI 服务 / 新闻情绪打分） | [patterns/ai-service.md](./patterns/ai-service.md) |
| workflow 与脚本实现细节 | `.github/workflows/finbert-train.yml` + `scripts/`（脚本清单见 [guides/ci-cd.md](./guides/ci-cd.md)） |

## ADR 索引（受控生成）

各 ADR 的 Status 以文件头部为准，本表仅作发现入口，不复制状态语义。表内容由
`scripts/docs_generated.py` 从 `docs/adr/*.md` 文件名与 H1 标题生成（编号自动补齐，
勿手工修改生成区块）：

<!-- generated:docs-readme-adr-index -->
| 编号 | 标题（文件 H1 正本） | 文档 |
|------|------|------|
| ADR-0001 | Record Architecture Decisions | [0001-record-architecture-decisions.md](./adr/0001-record-architecture-decisions.md) |
| ADR-0002 | 文档架构分层决策 | [0002-document-layering.md](./adr/0002-document-layering.md) |
| ADR-0003 | 推翻「3b/3c 过度工程不做」决策 | [0003-overturn-3b-3c-deferral.md](./adr/0003-overturn-3b-3c-deferral.md) |
| ADR-0004 | redlines.yml 格式选择 YAML | [0004-redlines-yaml-format.md](./adr/0004-redlines-yaml-format.md) |
| ADR-0005 | 落地 3c enforcement 字段映射检查 | [0005-enforcement-mapping-check.md](./adr/0005-enforcement-mapping-check.md) |
| ADR-0006 | AGENTS.md 从纯指针改为最小安全集 + 指针 | [0006-override-agents-pure-pointer.md](./adr/0006-override-agents-pure-pointer.md) |
| ADR-0007 | screening_history 唯一键改覆盖语义 | [0007-screening-history-override-pk.md](./adr/0007-screening-history-override-pk.md) |
| ADR-0008 | 不给 AI 执行权，仅输出评分和文本 | [0008-no-ai-execution.md](./adr/0008-no-ai-execution.md) |
| ADR-0009 | AI 结论快照回放（AI Effect Attribution） | [0009-ai-snapshot-replay.md](./adr/0009-ai-snapshot-replay.md) |
| ADR-0010 | screening_history 唯一键改回 append-only（含 run_id） | [0010-screening-history-append-only.md](./adr/0010-screening-history-append-only.md) |
| ADR-0011 | 手写 failover 循环迁移至 litellm.Router（L4） | [0011-litellm-router-failover.md](./adr/0011-litellm-router-failover.md) |
| ADR-0012 | 新闻情绪打分的本地推理范式（CLS embedding + numpy 分类头） | [0012-news-sentiment-inference-paradigm.md](./adr/0012-news-sentiment-inference-paradigm.md) |
| ADR-0013 | 新闻情绪打分推理运行时选型终局（保持 GGUF，ORT / OpenVINO 双 PoC FAIL） | [0013-runtime-selection-final-gguf.md](./adr/0013-runtime-selection-final-gguf.md) |
| ADR-0014 | 统一模型管理的场景调度契约（ScenarioExecutor 独占候选调度） | [0014-unified-model-scheduling.md](./adr/0014-unified-model-scheduling.md) |
| ADR-0015 | FinBERT 产线指纹驱动发布（内容寻址去重） | [0015-fingerprint-driven-release.md](./adr/0015-fingerprint-driven-release.md) |
| ADR-0016 | 智能体有限权限例外与允许操作矩阵 | [0016-agent-permission-exception.md](./adr/0016-agent-permission-exception.md) |
| ADR-0017 | 版本号单一正本（Version SSOT）收敛 | [0017-version-ssot.md](./adr/0017-version-ssot.md) |
| ADR-0018 | 文档体系单写入源与长期治理（Documentation Single Source） | [0018-documentation-single-source.md](./adr/0018-documentation-single-source.md) |
<!-- /generated -->
