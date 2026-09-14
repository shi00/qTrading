# 规则集版本变更日志 (Ruleset Changelog)

> **目的**：`CLAUDE.md` / `AGENTS.md` 元数据块中 `ruleset_version` 与产品版本分离（P2-07 统一格式），但此版本号无配套变更记录，导致「版本号退化为一个数字」、无处可查每次递增改了什么（AI文档检视 P3-04）。本文件提供 ruleset_version 递增的最小还原日志。
>
> **记录规则**：`ruleset_version` 在 `docs/governance` 治理正本（CLAUDE.md / AGENTS.md / 相关 docs）中每次递增时，在本文件变更记录表**顶部追加一行**，列格式为 `ruleset_version | 变更日期 | 变更摘要`。仅登记版本递增，不登记产品版本、不复制红线表全文。
>
> **维护约束**：由 human review 维护（无自动门禁）。涉及红线变更的递增必须同时在 [redlines.yml](./redlines.yml) 与 [exceptions.yml](./exceptions.yml) 留痕。

## 变更记录

| ruleset_version | 变更日期 | 变更摘要 |
|-----------------|----------|---------|
| 1.6.0 | 2026-09-14 | 初版快照登记（ruleset-changelog.md 引入时所在版本；历史 1.3.1→1.6.0 的变更未回溯，自本版本起记录） |