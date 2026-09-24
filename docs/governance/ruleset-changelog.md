# 规则集版本变更日志 (Ruleset Changelog)

> **目的**：`CLAUDE.md` / `AGENTS.md` 元数据块中 `ruleset_version` 与产品版本分离（P2-07 统一格式），但此版本号无配套变更记录，导致「版本号退化为一个数字」、无处可查每次递增改了什么（AI文档检视 P3-04）。本文件提供 ruleset_version 递增的最小还原日志。
>
> **记录规则**：`ruleset_version` 在 `docs/governance` 治理正本（CLAUDE.md / AGENTS.md / 相关 docs）中每次递增时，在本文件变更记录表**顶部追加一行**，列格式为 `ruleset_version | 变更日期 | 变更摘要`。仅登记版本递增，不登记产品版本、不复制红线表全文。
>
> **维护约束**：顶行版本号由 `scripts/check_docs_consistency.py::check_ruleset_changelog_version()` 自动守护，必须与 `CLAUDE.md` 元数据块 `ruleset_version` 一致（不一致时 docs 一致性门禁 FAIL）。涉及红线变更的递增必须同时在 [redlines.yml](./redlines.yml) 与 [exceptions.yml](./exceptions.yml) 留痕。

## 变更记录

| ruleset_version | 变更日期 | 变更摘要 |
|-----------------|----------|---------|
| 1.9.0 | 2026-09-24 | 新增 R24「时点正确性」红线（报告模式）：任何进入策略或回测的数据取数时点不得晚于被决策交易日，使用「当前快照」类维度参与历史区间计算即违规；check_redlines.py 新增 check_R24（warning 不阻断），redlines.yml/CLAUDE.md §3.1 同步登记；新增 docs/patterns/backtest-correctness.md 为 backtest 主题 canonical 正本（时点正确性/幸存者偏差/复权口径/财报修订/结论可信度边界），DAT-07/DAT-08/BT-05 迁入其「已知限制」 |
| 1.8.0 | 2026-09-21 | R13 语义简化（OSS-03）：删除 _DAO_REGISTRY 显式注册清单，改为 CacheManager.__init__ 显式实例化 + sync_engines() 按类型发现（isinstance BaseDao）；CLAUDE.md/redlines.yml R13 描述与 enforcement 同步（补回 CI-test 静态契约维度），how-to.md/dao-pattern.md 登记步骤同步删除 |
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

## R20 报告模式升级期限

`check_redlines.py::check_R20`（R20 单位未核对的量纲比较）当前为报告模式（warning 输出 stderr 不阻断），
升级条件此前写作「实测误报率达标后评估升级为拦截」，与「治理 ID 存量 WARNING 清零期限」同型——无日期、
无责任人、无量化阈值，存在「无期限渐进部署永久停留 WARNING」的反模式（文档体系检视 GOV-10）。现记录如下约束：

- **误报率阈值**：连续 2 次全量扫描误报 ≤ 2 条（全量扫描指 CI 每次运行 `scripts/check_redlines.py` 对
  `strategies/` 全树的 R20 检查；误报 = 非 `north_money`/`net_amount`/`amount`/`total_mv`/`circ_mv`/`vol`
  列或调用链上已含 `threshold_in_data_unit()` 换算却被告警的命中）。
- **复核期限**：2026-12-31（Q4 末）。
- **责任人**：架构维护者。
- **翻转触发**：届期若误报率未达标或未评估，须将对应 WARNING 升级为 ERROR（阻断门禁）并作为独立检视项
  复核；若提前达标则立即评估升级为拦截。
- **现状基准**：R20 自 2026-09-17（ruleset 1.7.0）进入报告模式以来以 warning 运行，尚无系统性误报统计，
  首次达标评估应在 2026-12-31 前完成。

## R24 报告模式升级期限

`check_redlines.py::check_R24`（R24 时点正确性）当前为报告模式（warning 输出 stderr 不阻断），
沿用 R20 已走通的流程。按「R20 报告模式升级期限」同型记录四要素（避免「无期限渐进部署永久停留
WARNING」的反模式）：

- **误报率阈值**：连续 2 次全量扫描误报 ≤ 3 条（全量扫描指 CI 每次运行 `scripts/check_redlines.py` 对
  `strategies/` 全树的 R24 检查；误报 = 「当前快照」维度标识命中但该处并未参与跨期历史区间计算，
  例如同日截面行业统计、AI prompt 上下文注入等在 `trade_date` 内自洽的使用）。
- **复核期限**：2026-12-31（Q4 末）。
- **责任人**：架构维护者。
- **翻转触发**：届期若误报率未达标或未评估，须将对应 WARNING 升级为 ERROR（阻断门禁）并作为独立检视项
  复核；若提前达标则立即评估升级为拦截（并相应放宽 `automation_coverage` 说明）。
- **现状基准**：R24 自 2026-09-24（ruleset 1.9.0）进入报告模式时，`strategies/` 全树基线命中约 5 处
  （`oversold_strategy.py` 的 `industry_sw_l2` 列 3 处、`ai_context/auxiliary.py` 的 `sw_industry` 键 2 处）——
  均为同日截面/prompt 上下文用途、非跨期前视候选，作为首次达标评估（2026-12-31 前完成）的误报样本。
  `data/` 层已知的跨期前视点（`screener_dao` 行业 `LATERAL` 子查询）属 DAT-08② 已文档化的「已知限制」，
  不在当前 check_R24 扫描范围（其准确性依赖人工评审，诚实降级范围见 [docs/patterns/backtest-correctness.md](../patterns/backtest-correctness.md)）。

## 规则集复核状态

`ruleset_version` 元数据块中 `review_triggers` 写明「检视报告发布时」复核。复核流程（GOV-07，规则性约定，不写日期快照）：

1. 检视报告发布后，由**架构维护者**对照 `docs/reviews/README.md` 轮次表的「进行中」轮次与未关闭发现摘要，评估治理变更是否需要递增 `ruleset_version`；
2. 若递增：按本文件「记录规则」在变更记录表顶部登记，并同步三方（CLAUDE.md / CONTRIBUTING.md / AGENTS.md）`last_reviewed`（须不早于本文件顶行变更日期，DS-05）与 `ruleset_version`；
3. 动态状态（轮次进度、`last_reviewed` 现值、未关闭发现）由 `docs/reviews/README.md` 轮次表与三份治理文档元数据块单一承载，本段落不复制。