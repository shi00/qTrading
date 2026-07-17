# Project Documentation Index

本目录承载从 CONTRIBUTING.md 渐进式披露拆分出的专项深入文档。

## 目录结构

- [guides/](./guides/) — 流程类指南（Git workflow / 测试 / CI/CD / 依赖管理 / How-To）
- [architecture/](./architecture/) — 架构类深入（单例生命周期模板等）
- [patterns/](./patterns/) — 模式类深入（DAO / 策略 / 数据同步 / MVVM 等）
- [flet/](./flet/) — Flet V1 项目差异与升级清单
- [debt/](./debt/) — 已知架构技术债
- [adr/](./adr/) — 架构决策记录（ADR）
- [governance/](./governance/) — 治理类机器可读文件（如 redlines.yml）

## 文档层次

1. CLAUDE.md — 项目宪法（AI 自动加载，含红线/架构边界/交互准则）
2. CONTRIBUTING.md — 入口索引 + 最小命令 + PR 流程
3. docs/ — 专项深入文档（本目录）
4. man/ — 专题深度文档

precedence: CLAUDE.md > CONTRIBUTING.md > docs/ > man/
