# ADR-0017: 版本号单一正本（Version SSOT）收敛

- 状态：已接受（Accepted）
- 日期：2026-11-11
- 决策人：架构维护者
- 关联：ADR-0006（AGENTS 纯指针）、ADR-0015（指纹驱动发布）、DOC-13、F5、[docs/guides/ci-cd.md](../guides/ci-cd.md)
- 勘误（2026-11-24）：SECURITY.md 支持版本改为不携带版本字面量的策略表述，verify-versions Check 4 改为结构校验，详见文末 Errata。

## 背景与问题

0.11.5 发版过程中，一次常规版本号变更需要同步修改 8 个文件（pyproject.toml、
.release-please-manifest.json、CHANGELOG.md、CLAUDE.md、AGENTS.md、CONTRIBUTING.md、
harness.toml、installer.iss）。逐文件人工修改易错、易漂移，且需要三重门禁
（verify-versions / docs-consistency / release-please extra-files）互相兜底副本一致性——
校验的存在本身证明副本不该存在。

## 决策

**产品版本的唯一正本（SSOT）为 `pyproject.toml [project].version`**，运行时经
`importlib.metadata.version()` 读取。其余位置不再携带产品版本字面量：

| 位置 | 处置 |
|------|------|
| CLAUDE.md / AGENTS.md / CONTRIBUTING.md 顶部的对应版本声明 | 改为指向 pyproject.toml 的指针句，不再含版本数字 |
| harness.toml `[project].version` | 删除字段（无任何运行时消费方） |
| installer.iss `MyAppVersion` 兜底默认 | 固定 `0.0.0-local`（Inno `#ifndef` 守护），CI/发布构建始终经 `/DMyAppVersion=${{ github.ref_name }}` 注入 |
| SECURITY.md 支持版本表 | 保留 major.minor 通配行（`0.11.x`），仅 minor 升级人工更新（安全策略惯例，verify-versions Check 4 守护）；**历史决策，2026-11-24 Errata 已取代为不携带版本字面量** |
| release-please `extra-files` | 全部移除（不再有可自动改写的版本字面量文件） |

配套门禁同步删除：verify-versions 原 Check 1/10/11（installer / AGENTS 对应版本 /
harness），docs-consistency 检查项 2（对应版本比对）；pre-commit verify-versions hook
白名单相应收窄。

## 后果

- 常规 patch 发布仅需 release-please 自动产出（pyproject.toml + manifest + CHANGELOG.md），
  人工触点收敛为「合并 Release PR → 推 tag」。
- `0.0.0-local` 兜底使本地裸跑 ISCC 仍可编译，但产物版本不可信（仅限本地调试，CI 冒烟同样注入版本）。
- 版本展示类文档（README 徽章等）不在此列，其消费方为外部渲染，不构成维护负担。
- 后续若引入 hatch-vcs 动态版本（tag 为唯一源），可在此基础上继续收敛，pyproject 亦不再写版本。

## 备选方案（否决理由）

- **维持 extra-files 全量副本 + 三重门禁**：每次发版 8 文件易错面不减，门禁复杂度随副本数线性增长。
- **hatch-vcs 立即切换**：PyInstaller / ISCC / sidecar 校验链全部需适配，改造成本高，留作后续演进。

## Errata / 勘误

> 2026-11-24：SECURITY.md 支持版本表去字面量化

- 决策表「SECURITY.md 支持版本表」行保留为决策时点快照；自本 Errata 起改为**不携带
  版本字面量**的策略表述（最新 minor 系列及其 patch 受支持，更早系列不再修复，以
  [Releases](https://github.com/shi00/qTrading/releases) 页为准）。`verify_versions.py`
  Check 4 改写为结构校验（要求 `## Supported Versions` 章节存在，并禁止重新引入版本
  字面量表格行），检查编号保留不重排；旧 `TestSecurityVersion` 与
  `test_check_security_supported_version_fail_closed_on_missing_doc` 删除，fail-closed
  由新的 `test_check_security_policy_fail_closed_on_missing_doc` 承接，并新增允许例与
  通配 / 精确 / `< minor` / 章节缺失反例。
- 原因：通配行 `0.x.x` 没有自动写者——release-please generic updater 只匹配完整 semver，
  `x-release-please-minor` 只替换行内首个整数；而 `Docs Consistency Check` 又在 Release PR
  上强制该行等于新 major.minor，使每次 minor bump 的 Release PR 必然失败并依赖人工修改
  bot 分支，release-please 重跑还会覆盖。去字面量化后该类失败不再复发，Check 4 保留为
  结构校验，防止版本表被重新手工引入；人工触点恢复为「合并 Release PR → 推 tag」。
