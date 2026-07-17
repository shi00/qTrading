# Flet V1 项目差异与升级清单

> 本文件已迁移到 [docs/flet/](../docs/flet/)。本 stub 仅保留用于历史引用兼容，不再维护内容；新增内容请直接编辑 docs/flet/ 下对应文件。

> Owner: UI 维护者
> 复核触发器: Flet 依赖版本变化（pyproject.toml）、关键 API 变化、架构红线/边界变化或 ADR 决策（见 docs/adr/）

## 新位置

- [docs/flet/v1-api-constraints.md](../docs/flet/v1-api-constraints.md) — Flet V1 API 关键约束（V0→V1 迁移 API 表、声明式组件内 API 契约、V1 声明式 UI 开发规范、兼容垫片使用规则、升级协同机制、例外清单）
- [docs/flet/project-differences.md](../docs/flet/project-differences.md) — 项目相对 Flet 官方默认的分叉点与项目验证过的高风险 API（含 R16 UI 阻塞红线）
- [docs/flet/upgrade-checklist.md](../docs/flet/upgrade-checklist.md) — Flet 版本升级时的验证步骤与文档同步要求
- [docs/flet/api-verification-template.md](../docs/flet/api-verification-template.md) — Flet API 核验记录模板（P1-4 整改新增）
- [docs/flet/accessibility-baseline.md](../docs/flet/accessibility-baseline.md) — UI 可访问性最低标准（P2-4 整改新增）

## 优先级

冲突时前者覆盖后者：

1. [CLAUDE.md](../CLAUDE.md)（红线 R1~R18、架构边界、交互准则）
2. [CONTRIBUTING.md](../CONTRIBUTING.md)（项目实现规范入口索引）
3. [docs/flet/](../docs/flet/) 子文档（项目 Flet 差异与升级清单详细实现）

## 通用 Flet 教程

通用 Flet v1 教程（路由、Services、存储、构建打包、移动/Web 适配、响应式布局、控件清单等）请直接查阅 [Flet 官方文档](https://docs.flet.dev/)，本项目不再复制，避免与上游漂移。
