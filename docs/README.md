# Project Documentation Index

本目录承载从 CONTRIBUTING.md 渐进式披露拆分出的专项深入文档。

## 目录结构

- [guides/](./guides/) — 流程类指南（Git workflow / 测试 / CI/CD / 依赖管理 / How-To）
- [architecture/](./architecture/) — 架构类深入（单例生命周期模板等，含 TushareClient 特殊说明）
- [patterns/](./patterns/) — 模式类深入（DAO / 策略 / 数据同步 / MVVM 等，data-sync.md 含 Tushare Syncer 设计模式）
- [flet/](./flet/README.md) — Flet UI/UX 设计、声明式 API、无障碍、项目差异、MCP 核验、升级与 CanvasKit E2E 避坑入口
- [debt/](./debt/) — 已知架构技术债（含 Tushare 相关条目：P3-Tushare-Token-Invalid-Race / P3-Tushare-Client-Lazy-Markers）
- [adr/](./adr/) — 架构决策记录（ADR）
- [governance/](./governance/) — 治理类机器可读文件（如 redlines.yml / exceptions.yml / canonical-topics.yml）
- [task-plans/](./task-plans/) — 任务计划（按需归档，示例见 ux-12-backtest-chart-context-plan.md）
- [reviews/](./reviews/) — AI 代码检视指南（核心协议 + 稳定规则 ID + 专项 Profile + schema/policy 分离 + evals 评测集）
- [bug-fix/](./bug-fix/) — AI 问题修复指南（核心协议 + 专项 Profile + 附录，三层拆分）

## 文档层次

文档权威按主题确定正本，而非按目录层级全局覆盖。目录仅作组织用途：

1. CLAUDE.md — 项目宪法（AI 自动加载，红线/架构边界/交互准则）
2. CONTRIBUTING.md — 入口索引 + 最小命令 + PR 流程
3. docs/ — 专项深入文档（本目录）
4. man/ — 专题深度文档（database-account-separation / table-partitioning-strategy / flet-best-practices stub）

按主题权威正本见 [CLAUDE.md](../CLAUDE.md) §1「文档权威性（按主题正本）」；冲突时先按主题确定正本，再以正本裁决。

## Tushare 文档索引

Tushare 相关文档分散在多个章节，按主题索引如下：

| 主题 | 文档位置 |
|------|---------|
| 单例生命周期与特殊说明 | [architecture/singleton-lifecycle.md](./architecture/singleton-lifecycle.md#tushareclient-特殊说明) |
| Syncer 设计模式（限流/质量门控/错误处理/取消传播） | [patterns/data-sync.md](./patterns/data-sync.md#tushare-syncer-设计模式) |
| 集成工作流简述 | [guides/how-to.md](./guides/how-to.md#51-tushare-集成工作流简述) |
| 配置说明（token 获取/积分档位/降级行为） | [README.md](../README.md#41-配置-tushare-数据源) |
| Token 安全（存储/脱敏/熔断/静态守护） | [SECURITY.md](../SECURITY.md#tushare-token-security) |
| 已知技术债 | [debt/known-technical-debt.md](./debt/known-technical-debt.md)（P3-Tushare-Token-Invalid-Race / P3-Tushare-Client-Lazy-Markers） |
| 红线自动化守护 | `scripts/check_redlines.py` 的 `check_R_tushare_token_log`（R9 红线专属守护） |
