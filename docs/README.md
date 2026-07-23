# Project Documentation Index

本目录承载从 CONTRIBUTING.md 渐进式披露拆分出的专项深入文档。

## 目录结构

- [guides/](./guides/) — 流程类指南（Git workflow / 测试 / CI/CD / 依赖管理 / How-To）
- [architecture/](./architecture/) — 架构类深入（单例生命周期模板等，含 TushareClient 特殊说明）
- [patterns/](./patterns/) — 模式类深入（DAO / 策略 / 数据同步 / MVVM 等，data-sync.md 含 Tushare Syncer 设计模式）
- [flet/](./flet/) — Flet V1 项目差异与升级清单
- [debt/](./debt/) — 已知架构技术债（含 Tushare 相关条目：P3-Tushare-Token-Invalid-Race / P3-Tushare-Client-Lazy-Markers）
- [adr/](./adr/) — 架构决策记录（ADR）
- [governance/](./governance/) — 治理类机器可读文件（如 redlines.yml）
- [reviews/](./reviews/) — AI 代码检视指南（核心协议 + 稳定规则 ID + 专项 Profile + schema/policy 分离 + evals 评测集）
- [bug-fix/](./bug-fix/) — AI 问题修复指南（核心协议 + 专项 Profile + 附录，三层拆分）
- [coverage/](./coverage/) — 覆盖率报告（unit-only 阶段性报告，CI 合并报告为最终门禁）

## 文档层次

1. CLAUDE.md — 项目宪法（AI 自动加载，含红线/架构边界/交互准则）
2. CONTRIBUTING.md — 入口索引 + 最小命令 + PR 流程
3. docs/ — 专项深入文档（本目录）
4. man/ — 专题深度文档

precedence: CLAUDE.md > CONTRIBUTING.md > docs/ > man/

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
