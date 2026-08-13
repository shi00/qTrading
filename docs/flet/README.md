# Flet 开发文档入口

> 本目录是 AStockScreener Flet 开发规范的唯一导航入口。
> 具体规则由各专题文档维护，本文件不复制规范正文。
> Owner: UI 维护者
> 复核触发器: 新增/删除/改名 docs/flet/*.md 专题、Flet 依赖版本变化、架构红线/边界变化或 ADR 决策（见 [../adr/](../adr/)）

## 1. 文档权威（按主题正本）

文档权威按主题确定正本，而非按目录层级全局覆盖（见 [CLAUDE.md](../../CLAUDE.md) §1「文档权威性（按主题正本）」）。Flet 相关主题正本如下：

1. 红线 R1~R18、架构边界、交互准则 → [CLAUDE.md](../../CLAUDE.md)
2. 实现与交付规范 → [CONTRIBUTING.md](../../CONTRIBUTING.md)
3. Flet 项目约束 → 本目录各专题（`v1-api-constraints.md` 为声明式 API 约束正本）
4. Flet API 存在性和签名 → 锁定版本源码 / [flet-mcp](./mcp-usage.md) / Flet 官方文档

## 2. AI 任务路由

### 新增或修改 UI 视图

必读：

1. [ui-ux-best-practices.md](./ui-ux-best-practices.md)：页面任务、信息架构与状态设计
2. [v1-api-constraints.md](./v1-api-constraints.md)「V1 声明式 UI 开发规范」：声明式组件与 hooks 契约
3. [accessibility-baseline.md](./accessibility-baseline.md)：无障碍可执行条款
4. [docs/patterns/mvvm.md](../patterns/mvvm.md)：MVVM 表现层契约
5. 对应 ViewModel 和 [ui/app_layout.py](../../ui/app_layout.py)

条件触发：

- 使用不熟悉的 Flet API：[mcp-usage.md](./mcp-usage.md)（对应 [CLAUDE.md §1.10](../../CLAUDE.md#110-反幻觉护栏-ai-特有红线) 反幻觉红线）
- 修改表格、滚动或复杂布局：[project-differences.md](./project-differences.md)
- 修复 UI Bug：[docs/bug-fix/core-protocol.md](../bug-fix/core-protocol.md)
- 工作流：[docs/guides/how-to.md](../guides/how-to.md)「4. 新增一个 UI 视图」

### 修改布局或响应式

必读：

1. [ui-ux-best-practices.md](./ui-ux-best-practices.md)「布局与响应式」
2. [v1-api-constraints.md](./v1-api-constraints.md) 的响应式契约（`ResponsiveRow` + `AppStyles.COL_*`）
3. [project-differences.md](./project-differences.md) 的布局陷阱
4. [ui/theme.py](../../ui/theme.py) (`AppStyles`) 和 [ui/app_layout.py](../../ui/app_layout.py)

### 新增或修改 ViewModel

必读：

1. [docs/patterns/mvvm.md](../patterns/mvvm.md)
2. [v1-api-constraints.md](./v1-api-constraints.md) 的 ViewModel 桥接契约（`use_viewmodel`）
3. 对应 View 和 ViewModel

仅修改纯业务状态时，不强制加载完整 UI/UX 文档。不熟悉的 API 按 [mcp-usage.md](./mcp-usage.md) 核验。

### 修改 i18n 文案

必读：

1. [v1-api-constraints.md](./v1-api-constraints.md)「V1 声明式 UI 开发规范」中的 i18n 状态驱动规则
2. `core/i18n.py` 与 `locales/`（VM 只产出 i18n key，View 按当前 locale 渲染）

### 使用不熟悉的 Flet API

必读：

1. [mcp-usage.md](./mcp-usage.md)
2. [v1-api-constraints.md](./v1-api-constraints.md)
3. [project-differences.md](./project-differences.md)

### 排查 Flet UI Bug

必读：

1. [docs/bug-fix/core-protocol.md](../bug-fix/core-protocol.md)
2. [project-differences.md](./project-differences.md)
3. 与症状相关的专题文档

### 升级 Flet

必读：

1. [upgrade-checklist.md](./upgrade-checklist.md)
2. [mcp-usage.md](./mcp-usage.md)
3. [api-verification-template.md](./api-verification-template.md)
4. [project-differences.md](./project-differences.md)

## 3. UI 评审顺序

PR 评审按以下顺序逐项检查：

1. [ui-ux-best-practices.md](./ui-ux-best-practices.md)：设计与交互
2. [accessibility-baseline.md](./accessibility-baseline.md)：无障碍
3. [v1-api-constraints.md](./v1-api-constraints.md)：声明式实现
4. [project-differences.md](./project-differences.md)：项目回归风险

## 4. 文档职责清单

| 文件 | 唯一职责 |
|------|---------|
| [ui-ux-best-practices.md](./ui-ux-best-practices.md) | UI/UX 设计（信息架构、布局原则、组件选择、表单、页面状态、桌面交互、UX 评审清单） |
| [v1-api-constraints.md](./v1-api-constraints.md) | Flet V1 API 关键约束（V0→V1 迁移表、声明式组件契约、V1 声明式 UI 规范、样式 Token） |
| [accessibility-baseline.md](./accessibility-baseline.md) | UI 无障碍可执行条款（label、对比度、键盘路径、错误可读性） |
| [project-differences.md](./project-differences.md) | 项目相对官方的分叉点与已验证高风险 API（含 R16 UI 阻塞红线） |
| [mcp-usage.md](./mcp-usage.md) | AI 通过 flet-mcp 验证 Flet API 的操作指南（对应反幻觉红线） |
| [upgrade-checklist.md](./upgrade-checklist.md) | Flet 版本升级验证步骤 |
| [api-verification-template.md](./api-verification-template.md) | API 核验记录模板 |
| [canvaskit-rendering-e2e-guide.md](./canvaskit-rendering-e2e-guide.md) | Flutter Web CanvasKit 渲染行为与 E2E 定位避坑 |

## 5. 维护规则

- 一条规则只能有一个权威正文，本文件不复制任何专题规则。
- 新增 `docs/flet/*.md` 时必须在本文件「文档职责清单」登记，否则会被 `check_flet_hub_completeness()` 门禁拦截。
- 删除或改名专题文档时必须同步更新本文件及对应一致性测试。
- 通用 Flet v1 教程（路由、Services、存储、构建打包、响应式布局、控件清单等）直接查阅 [Flet 官方文档](https://docs.flet.dev/)，本目录不再复制，避免与上游漂移。
