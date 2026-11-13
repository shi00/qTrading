# 治理规则与任务路由（Governance README）

> **角色**：本目录承载治理机器可读正本（[redlines.yml](./redlines.yml) / [exceptions.yml](./exceptions.yml) / [canonical-topics.yml](./canonical-topics.yml) / [ruleset-changelog.md](./ruleset-changelog.md) 等），本 README 为治理入口，承载**规则参考 / 任务路由 / 维护入口**三视图（检视 §4「docs/governance/README.md」）。
> 「适用规则视图」与「任务路由」两节由 `scripts/docs_generated.py` 按封闭注册表从作者源（redlines.yml / canonical-topics.yml）受控生成，一致性由 `check_docs_generated` 门禁守护，**勿手工修改生成区块**；长期编写/变更/维护流程唯一正本见 [docs/guides/documentation-maintenance.md](../guides/documentation-maintenance.md)。

## 规则参考

红线的唯一编辑源是 [redlines.yml](./redlines.yml)（R 身份/标题/判据/类型/coverage/self_check）；例外范围/生效/移除的唯一编辑源是 [exceptions.yml](./exceptions.yml)；规则集版本与实质期限单源是 [ruleset-changelog.md](./ruleset-changelog.md)。下表为全部红线的适用规则视图（**受控生成**，判据语义正本仍为 redlines.yml 与 CLAUDE.md §3.1，本表不复制判据正文）：

<!-- generated:governance-readme-rules -->
| 红线 | 标题 | 规则类型 | 自动化覆盖 |
|------|------|---------|-----------|
| R1 | 架构越界 | EXCEPTIONABLE | partial |
| R2 | 异常吞没 | INVARIANT | partial |
| R3 | 模糊压制 | INVARIANT | full |
| R4 | SQL 注入 | INVARIANT | partial |
| R5 | 僵尸引擎操作 | EXCEPTIONABLE | none |
| R6 | 过时类型注解 | NEW_CODE | full |
| R7 | 测试状态污染 | INVARIANT | partial |
| R8 | 废弃 API | NEW_CODE | full |
| R9 | 敏感信息泄露 | INVARIANT | partial |
| R10 | 硬编码密钥 | INVARIANT | partial |
| R11 | 跨循环复用同步原语 | NEW_CODE | partial |
| R12 | 未注册数据表 | NEW_CODE | partial |
| R13 | 未注册 DAO | NEW_CODE | partial |
| R14 | 未注册策略 | NEW_CODE | partial |
| R15 | 未注册单例 | NEW_CODE | partial |
| R16 | UI 阻塞主循环 | NEW_CODE | partial |
| R17 | 保留字作字段 | NEW_CODE | none |
| R18 | 未隔离开发 | WORKFLOW | none |
| R19 | 未配套测试的业务逻辑变更 | NEW_CODE | partial |
| R20 | 单位未核对的量纲比较 | NEW_CODE | partial |
| R21 | 缺失值伪装 | NEW_CODE | partial |
| R22 | 水位线单调性 | NEW_CODE | partial |
| R23 | 裸 UI token | NEW_CODE | partial |
| R24 | 时点正确性 | NEW_CODE | partial |
<!-- /generated -->

规则类型（`INVARIANT` / `DEFAULT` / `NEW_CODE` / `MIGRATION_TARGET` / `WORKFLOW` / `EXCEPTIONABLE`）的适用范围与豁免方式见 CLAUDE.md §3.1「规则类型（P2-11）」；EX 条目专项只引用不手写原因。

## 任务路由

任务身份与入口的唯一编辑源是 [canonical-topics.yml](./canonical-topics.yml)；CLAUDE.md §1.8 决策树是其正文绑定（DOC-04，行尾 route 锚逐行校验）。下表按相同 canonical/section 合并展示全部主题、保全部 topic id（**受控生成**）：

<!-- generated:governance-readme-topics -->
| 主题 id | 任务类型（title） | canonical 正本 | 章节 |
|---------|------------------|----------------|------|
| requirement | 新增业务功能 / 需求澄清 | [requirements/USER_REQUIREMENTS.md](../../requirements/USER_REQUIREMENTS.md) | — |
| strategy | 新增/修改策略 | [docs/patterns/strategy-template.md](../patterns/strategy-template.md) | — |
| polars-strategy | 新增向量化策略（Polars） | [docs/patterns/polars-vectorized-strategy.md](../patterns/polars-vectorized-strategy.md) | — |
| ai-strategy-mixin | 新增 AI 策略混入 | [docs/patterns/ai-strategy-mixin.md](../patterns/ai-strategy-mixin.md) | — |
| dao | 新增/修改 DAO 或数据表 | [docs/patterns/dao-pattern.md](../patterns/dao-pattern.md) | — |
| data-sync | 新增/修改数据同步 | [docs/patterns/data-sync.md](../patterns/data-sync.md) | — |
| application-service | 新增/修改应用服务 | [docs/patterns/application-service.md](../patterns/application-service.md) | — |
| task-manager | 修改任务生命周期 / TaskManager | [docs/patterns/task-manager.md](../patterns/task-manager.md) | — |
| ui-view, ui-layout, ui-component, i18n | 新增/修改 UI 视图 / 修改 UI 布局/响应式 / 新增/修改 UI 组件 / 修改 i18n 文案 | [docs/flet/README.md](../flet/README.md) | — |
| viewmodel | 新增/修改 ViewModel | [docs/patterns/mvvm.md](../patterns/mvvm.md) | — |
| exception-handling | 修改异常处理 | [CONTRIBUTING.md](../../CONTRIBUTING.md) | 错误处理标准模式 |
| bug-fix | 修复 bug / 排查问题 | [docs/bug-fix/core-protocol.md](../bug-fix/core-protocol.md) | — |
| code-review | AI 代码检视 / PR review | [docs/reviews/ai-review.md](../reviews/ai-review.md) | — |
| ai-service | 新增/修改 AI 服务 / LLM 集成 | [docs/patterns/ai-service.md](../patterns/ai-service.md) | — |
| singleton | 修改单例 / 资源生命周期 | [docs/architecture/singleton-lifecycle.md](../architecture/singleton-lifecycle.md) | — |
| performance, config | 性能优化 / 性能阈值调整 / 修改配置项 | [docs/patterns/config-quality-perf.md](../patterns/config-quality-perf.md) | — |
| ci-deps | 调整 CI / 依赖升级 | [docs/guides/ci-cd.md](../guides/ci-cd.md) | — |
| backtest | 新增/修改回测 | [docs/patterns/backtest-correctness.md](../patterns/backtest-correctness.md) | — |
| testing | 新增测试 | [docs/guides/testing.md](../guides/testing.md) | 测试编写模板 |
| e2e-testing | 新增/修改 E2E 测试 | [docs/guides/testing.md](../guides/testing.md) | — |
| git | Git 操作 / 分支 / worktree / 创建 PR / 创建 Issue | [docs/guides/git-workflow.md](../guides/git-workflow.md) | — |
| embedded-pg | 内置 PostgreSQL 离线维护 / 数据恢复 | [docs/guides/how-to.md](../guides/how-to.md) | 9. 内置 PostgreSQL 离线维护 |
| maintenance | 运行期排障 / 维护类问题 | [docs/guides/how-to.md](../guides/how-to.md) | 12. 运行期排障 |
| architecture-design | 架构设计 / 公共契约 / 跨层范式 | [docs/adr/0001-record-architecture-decisions.md](../adr/0001-record-architecture-decisions.md) | — |
| governance-docs | 修改治理文档 / 规则 | [docs/adr/0002-document-layering.md](../adr/0002-document-layering.md) | — |
| release | 版本发布 / Release 管理 | [docs/guides/ci-cd.md](../guides/ci-cd.md) | 版本发布流程与 Release 管理 |
| packaging | 打包分发 / PyInstaller 构建 | [docs/guides/dependency-management.md](../guides/dependency-management.md) | PyInstaller 打包 |
| fallback | 未列出的任务类型 | [CLAUDE.md](../../CLAUDE.md) | — |
<!-- /generated -->

## 维护入口

- **长期维护流程唯一正本**：[docs/guides/documentation-maintenance.md](../guides/documentation-maintenance.md)（L01—L12 控制合同 / 六类变更协议 / E01—E12 演习合同，ADR-0018）。
- **一致性检查**：`python scripts/check_docs_consistency.py --check --scope all`（无参数等价；scope=document/governance/source 按变更映射裁剪）。
- **生成块更新**：`python scripts/check_docs_consistency.py --update`（封闭注册表：AGENTS.md 最小安全集/最小验证命令、CLAUDE.md 顶部摘要/自查清单、docs/README.md 文件级目录/ADR 索引、本 README 规则参考/任务路由；更新后自动跑 all 检查）。
- **治理 ID 溯源**：[governance-ids.md](./governance-ids.md)；**规则集变更记录**：[ruleset-changelog.md](./ruleset-changelog.md)；**检查迁移映射**：[checker-migration-map.md](./checker-migration-map.md)。
- **例外移除条件季度复核**：逐条判断 `exceptions.yml` 中 `removal_trigger` 是否已满足（checker 只校验 `expires_at` 日期与字段完备性，不代替该语义判定），节律与处置见 [documentation-maintenance.md](../guides/documentation-maintenance.md) §9。
