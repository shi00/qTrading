# 规则集版本变更日志 (Ruleset Changelog)

> **目的**：`CLAUDE.md` / `AGENTS.md` 元数据块中 `ruleset_version` 与产品版本分离（P2-07 统一格式），但此版本号无配套变更记录，导致「版本号退化为一个数字」、无处可查每次递增改了什么（AI文档检视 P3-04）。本文件提供 ruleset_version 递增的最小还原日志。
>
> **记录规则**：`ruleset_version` 在 `docs/governance` 治理正本（CLAUDE.md / AGENTS.md / 相关 docs）中每次递增时，在本文件变更记录表**顶部追加一行**，列格式为 `ruleset_version | 变更日期 | 变更摘要`。仅登记版本递增，不登记产品版本、不复制红线表全文。
>
> **维护约束**：顶行版本号由 `scripts/check_docs_consistency.py::check_ruleset_changelog_version()` 自动守护，必须与 `CLAUDE.md` 元数据块 `ruleset_version` 一致（不一致时 docs 一致性门禁 FAIL）。涉及红线变更的递增必须同时在 [redlines.yml](./redlines.yml) 与 [exceptions.yml](./exceptions.yml) 留痕。

## 变更记录

| ruleset_version | 变更日期 | 变更摘要 |
|-----------------|----------|---------|
| 1.7.0 | 2026-09-17 | R20 第一阶段报告模式落地：check_redlines.py 新增 check_R20（warning 输出 stderr 不阻断），redlines.yml R20 登记 checks/enforcement，automation_coverage none→partial（语义仍以人工评审为准，误报率达标后评估升级为拦截） |
| 1.6.0 | 2026-09-14 | 初版快照登记（ruleset-changelog.md 引入时所在版本；历史 1.3.1→1.6.0 的变更未回溯，自本版本起记录） |

## 治理 ID 存量 WARNING 清零期限

`scripts/check_docs_consistency.py::check_governance_id_glossary()`（检查项 19）对 `scripts/` 与 `tests/`
的 `.py` 注释中**未登记治理 ID** 输出 WARNING（渐进部署、不阻断），脚本注释与文档均声明「存量清零后翻转
ERROR」，但此前无清零期限或责任人，存在「无期限渐进部署永久停留 WARNING」的反模式（文档体系检视 F-11）。
现记录如下约束：

- **期限**：2026-12-31（Q4 末）。
- **责任人**：架构维护者。
- **翻转触发**：届期若存量未清零，须将对应 WARNING 升级为 ERROR（阻断门禁）并作为独立检视项复核；若存量
  提前清零则立即翻转。
- **现状基准**：2026-09-20 检视实录约 68 条（`scripts/` 与 `tests/` 的 `.py` 注释），检查脚本注释基线预估约 100 条。

## 规则集复核状态

`ruleset_version` 元数据块中 `review_triggers` 写明「检视报告发布时」复核，但本变更日志此前仅登记版本递增、
无复核状态（文档体系检视 F-11）。截至 2026-09-20：`docs/reviews/README.md` 轮次表有 review03 / review06 /
文档体系·AI 可执行性 三轮处于 `进行中`，`last_reviewed` 停在 2026-09-17，二者未闭环。本批文档体系检视
（F-01~F-13）整改全部落地后，应同步复核规则集是否需随治理变更递增 `ruleset_version`，并更新三方
（CLAUDE.md / CONTRIBUTING.md / AGENTS.md）`last_reviewed`（须不早于本文件顶行变更日期，DS-05）。