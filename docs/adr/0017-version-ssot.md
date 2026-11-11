# ADR-0017: 版本号单一正本（Version SSOT）收敛

- 状态：已接受（Accepted）
- 日期：2026-11-11
- 决策人：架构维护者
- 关联：ADR-0006（AGENTS 纯指针）、ADR-0015（指纹驱动发布）、DOC-13、F5、[docs/guides/ci-cd.md](../guides/ci-cd.md)

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
| SECURITY.md 支持版本表 | 保留 major.minor 通配行（`0.11.x`），仅 minor 升级人工更新（安全策略惯例，verify-versions Check 4 守护） |
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
