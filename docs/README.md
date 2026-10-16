# Project Documentation Index

本目录承载从 CONTRIBUTING.md 渐进式披露拆分出的专项深入文档。

## 目录结构

- [guides/](./guides/) — 流程类指南（Git workflow / 测试 / CI/CD / 依赖管理 / How-To）
- [architecture/](./architecture/) — 架构类深入（单例生命周期模板等，含 TushareClient 特殊说明）
- [patterns/](./patterns/) — 模式类深入（DAO / 策略 / 数据同步 / MVVM 等，data-sync.md 含 Tushare Syncer 设计模式）
- [flet/](./flet/) — Flet UI/UX 设计、声明式 API、无障碍、项目差异、MCP 核验、升级与 CanvasKit E2E 避坑入口
- [debt/](./debt/) — 已知架构技术债（以 `known-technical-debt.md` 条目为准）
- [adr/](./adr/) — 架构决策记录（ADR），ADR 索引见下方「ADR 索引」小节
- [governance/](./governance/) — 治理类机器可读文件（如 redlines.yml / exceptions.yml / canonical-topics.yml）
- [reviews/](./reviews/) — AI 代码检视指南（核心协议 + 稳定规则 ID + 专项 Profile + schema/policy 分离 + evals 评测集）
- [bug-fix/](./bug-fix/) — AI 问题修复指南（核心协议 + 专项 Profile + 附录，三层拆分）

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

## ADR 索引

各 ADR 的 Status 以文件头部为准，本表仅作发现入口，不复制状态语义。

| 编号 | 标题 | 文档 |
|------|------|------|
| ADR-0001 | 记录架构决策 | [0001-record-architecture-decisions.md](./adr/0001-record-architecture-decisions.md) |
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
| ADR-0015 | FinBERT 产线指纹驱动发布（内容寻址去重，训练输入指纹对比） | [0015-fingerprint-driven-release.md](./adr/0015-fingerprint-driven-release.md) |
