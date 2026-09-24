# 检视轮次索引 (Review Index)

> **目的**：让既有检视方法论与轮次清单对新会话一跳可达（结论正文为本地 gitignored 产物，不入仓库，见「路径说明」），避免重复检视、重复给出已存在的结论（沿 [CLAUDE.md](../../CLAUDE.md) §1.8「AI 代码检视 / PR review」→ [ai-review.md](./ai-review.md) → 本文件）。
>
> **状态取值**：`进行中` / `已归档` / `已消化`（已消化 = 该轮发现已全部落入修复队列并关闭）。
> **目录职责边界**：`docs/reviews/` 仅承载检视**方法论**（ai-review.md / appendix.md / quality-dimensions.md / scenario-completeness.md / review-profiles/ / evals/）与检视**结论结构化登记**（findings/，见 [findings/README.md](./findings/README.md)，GOV-04）；检视**报告产物**（正文+长证据）一律落根目录 `reviews/`（gitignored，不入 git）。报告正文 **SHALL NOT** 落 `docs/reviews/<slug>/`。正本见 ADR-0002 Errata。
>
> **路径说明**：报告正文落 `reviews/` 根的为**本地检视工作产物，已被 `.gitignore` 排除、不入 git**（见 [.gitignore](../../.gitignore) `/reviews/`），仅在新会话本地工作区可读；已丢失报告的轮次仅保留 ID 供追溯，不复原正文。
>
> **落盘策略（结论 + 判据 + ≤20 行关键证据片段入库，H4）**：纯「正文 gitignored + 一句话摘要」的信息密度不足以支撑复用——新会话读到摘要既不知当前实现长什么样，也不知道前一轮试过什么。故每轮检视的**结论**按 [review-result.schema.json](./review-result.schema.json) 的字段语义落 `docs/reviews/findings/<round>.md`（或 `.json`），字段为 `id` / `ruleId` / `severity` / `location` / `summary` / `disposition` / 可验证判据，**并允许每条发现附 ≤20 行关键证据片段**（代码位置 + 最小复现）；**原始长日志、大段代码、报告正文不入库**（正文仍落根目录 `reviews/`，gitignored）。该边界与 ADR-0002 Errata 一致：SHALL NOT 约束的是报告正文，不含结论登记与其中受限长度的证据片段。

## 方法论文档

检视方法论资产登记于此（新增顶层方法论文档须同时在本文件登记，见 `check_reviews_index_completeness` 门禁）：

- [ai-review.md](./ai-review.md) — AI 代码检视指南（核心协议 + 稳定规则 ID + 专项 Profile + schema/policy 分离 + evals 评测集）
- [appendix.md](./appendix.md) — 检视附录（模板 / 术语 / 深度阅读）
- [quality-dimensions.md](./quality-dimensions.md) — 检视质量维度定义
- [scenario-completeness.md](./scenario-completeness.md) — Evals 场景完整性方法论
- [review-policy.yaml](./review-policy.yaml) — 检视策略机器可读定义（字段语义与 ai-review.md 对齐）
- [review-result.schema.json](./review-result.schema.json) — 检视结论机器可读 JSON Schema（见文末引用）
- [findings/README.md](./findings/README.md) — 检视结论结构化登记索引（GOV-04：结论 id/disposition/判据入库可溯源，正文仍 gitignored）

## 轮次清单

| 轮次 | 日期 | 范围 / 对象 | 报告路径 | 状态 |
|------|------|------------|---------|------|
| review01 | 2026-08-24 | 架构分层与依赖治理（六层依赖 + 上帝类） | `reviews/01-架构分层与依赖治理.md`（本地） | 已归档 |
| review02 | 2026-08-24 | 异步并发与资源生命周期（R2/R11/R16） | `reviews/02-异步并发与资源生命周期.md`（本地） | 已归档 |
| review03 | 2026-09-02/03 | 数据层与持久化（R4/R5/R8/R12/R17） | `reviews/03-data persist.md` 等（本地） | 进行中 |
| review04 | 2026-08-24 | UI 表现层与 MVVM 合规 | `reviews/04-UI表现层与MVVM合规.md`（本地） | 已归档 |
| review05 | 2026-08-24 | 错误处理 / 配置 / 单例治理 / 可观测性 | `reviews/05-错误处理配置与可观测性.md`（本地） | 已归档 |
| review06 | 2026-08-24 | 安全 / 外部集成 / 供应链（R9/R10） | `reviews/06-安全与供应链.md`（本地） | 进行中 |
| review07 | 2026-08-24 | 测试体系 / CI 门禁 / 文档治理 | `reviews/07-测试体系与工程治理.md`（本地） | 已归档 |
| review08 | 2026-09-23 | AKShare 开源组件复用专项（重复造轮子 / akshare 行为 / 未补位 / 项目自身重复，13 项发现） | `reviews/akshare检视.md`（本地） | 进行中 |
| business-review-2026-09-11 | 2026-09-11 | 业务检视（最高发三类失效模式：量纲 / 缺失伪装 / 水位线；结论：新增 R20/R21/R22 三条红线；11 项缺陷回归集见 `scripts/prototype_business_redlines.py` docstring 与 `_REGRESSION_TARGETS`） | 报告正文未落盘（gitignored 目录内亦无），正本仅存于 [prototype_business_redlines.py](../../scripts/prototype_business_redlines.py) docstring | 已消化 |
| M3~M12 | 2026-08 | UI 基础设施 / 表现层模块专项检视 | 报告已丢失，ID 出现在 [known-technical-debt.md](../debt/known-technical-debt.md) | 已归档 |
| UX-07 | 2026-08-06 | 全系统 UI/UX 专项审视 | `reviews/UX-reviews.md`（本地） | 已归档 |
| 文档体系 | 2026-08-13 | CLAUDE.md 文档体系对抗性深度检视（GOV 系列） | `reviews/文档体系检视.md`（本地） | 已归档 |
| 文档体系·AI 可执行性 | 2026-09-03 | 文档体系「AI 可执行性」专项（DOC-01~14，修复分批跟踪） | 本仓库 `docs/**` 改修分支（见各 PR） | 进行中 |

> 报告正文若同步产出机器可读结论，遵循 [review-result.schema.json](./review-result.schema.json)；发现清单的「状态」列（未修复 / 已修复 / 已接受）随 DOC-14 第 1 步在本索引轮次表维护，不回溯改写已归档报告。

## 未关闭发现摘要（进行中轮次）

> **维护规则（DS-04）**：轮次表状态为「进行中 / 已归档」的轮次，其**未关闭发现**在此按「发现 ID + 一句话 + 状态 + 正本链接 + 可验证判据（F-05）」逐条登记，供新会话一跳核对，避免重复检视或漏掉未关闭项；与 DOC-14 约定同源。仅登记未关闭项，不重复已修复/已接受发现，不复制证据正文与代码细节。本摘要由该轮次对应检视/修复的执行者续维护，发现关闭时从下表删除。正本链接优先指向 `findings/` 结论登记（[findings/](./findings/)，H4），无结论登记时指向 gitignored 的本地报告路径（见「路径说明」）；**可验证判据**写清"满足什么条件即可视为关闭"，并附**可机械执行的验证命令或检查点**（grep/检查位置），供 AI 不依赖丢失报告也能自行判定。

| 轮次 | 发现 ID | 状态 | 一句话摘要 | 正本链接 | 可验证判据 |
|------|---------|------|-----------|---------|-----------|
| review03 | C4 | 部分 | `_read_db`/`_read_db_select` 已提供 `max_rows` 安全阀，但多数全表扫描调用点未强制设置，数据增长后可能一次性载入全量 | 见轮次表 | 全部全表扫描调用点已显式指定 `max_rows` 且无兜底无限制读取，即视为关闭；检查点：`rg -n "max_rows" data/persistence/daos/` 逐个核对全表扫描调用点是否显式传 `max_rows` |
| review03 | C7 | 未修复 | `screener_dao` 仍有查询用 f-string 拼列名 / `ORDER BY`（`screening_history`），非注入风险，待迁移 SQLAlchemy Core | 见轮次表 | `data/` 内 `screening_history` 相关查询已迁 SQLAlchemy Core，无 f-string 拼列名/`ORDER BY` |
| review03 | C9 | 未修复 | 数据字典半数表 `columns` 为空，列级一致性仅由 ORM 兜底，待「删 columns / 补全」产品决策 | 见轮次表 | 已作产品决策（删 `columns` 空表或全量补全）且执行完毕；或决策明确接受当期现状；检查点：`rg -n "columns" data/data_dictionary.py` 仍存在 `columns` 为空条目的表即未关闭 |
| review06 | F9 | 未修复 | 外部数据（Tushare/Akshare）入库前缺 schema 校验，脏数据可静默影响选股决策 | [findings/review06.md](./findings/review06.md) | 外部数据入库路径已挂行级 schema 校验（非法行拒绝/隔离并告警），无未校验直写路径；验证：`rg -n -e validate_and_quarantine -e _validate_schema data/external data/sync` 命中取数→入库链上的校验调用 |
| review06 | F12 | 未修复 | prompt 注入防御为正则黑名单，对新闻正文植入指令固有局限，需结构化边界 + 输出侧交叉校验 | [findings/review06.md](./findings/review06.md) | 已引入结构化边界（外部内容包裹为不可执行区域）+ 输出侧交叉校验，不再单靠正则黑名单；验证：`rg -n -e 外部资料 -e EXTERNAL_CONTENT utils/prompt_guard.py services/ai_service` 命中结构化边界模板 |
| review06 | F13 | 未修复 | LLM 输出影响选股排序，仅校验部分字段，缺完整输出 schema 校验与 AI 免责声明 | [findings/review06.md](./findings/review06.md) | LLM 输出已做完整 schema 校验，且 UI 对 AI 排序结果展示 AI 免责声明；验证：`rg -n -e 非投资建议 -e 免责声明 -e disclaimer ui/ services/ai_service` 命中免责声明渲染 |
| review06 | F10 | 未修复 | 重试机制在外部服务故障时放大请求压力，建议加熔断器 | [findings/review06.md](./findings/review06.md) | 外部服务调用链已加熔断/退避限流，故障时不无限重试放大 QPS；验证：`rg -n -e circuit -e breaker -e 熔断 data/ services/ utils/` 命中，且 `tushare_client._handle_api_call` 存在「连续 N 次失败后短路」计数分支 |
| review06 | F16 | 未修复 | 备份恢复路径仅做存在性检查，建议校验 pg_dump magic header 并展示元信息 | [findings/review06.md](./findings/review06.md) | 备份恢复前已校验 pg_dump 文件 magic header 并在 UI 展示版本/元信息；验证：`rg -n -e PGDMP -e magic -e header ui/viewmodels/backup_restore_view_model.py` 命中恢复前校验 |
| review06 | F18 | 未修复 | sidecar 路径可通过配置指向任意二进制，低优先级防误配置项 | `reviews/06-安全与供应链.md`（本地） | sidecar 路径已有配置校验/白名单或非管理员不可覆盖，防误指向任意二进制 |
| review06 | F19 | 未修复 | 数据库备份文件未加密，建议 UI 提示勿放共享目录 + 可选加密备份 | [findings/review06.md](./findings/review06.md) | UI 已有存储位置提示，并提供可选加密备份能力（任一启用即视为关闭）；验证：`rg -n -e 未加密 -e 云同步 -e 共享目录 ui/ docs/` 命中 UI 提示文案 |
| review08 | C1 | 未修复 | Tushare 积分不足时直接 skip 功能，akshare 免费同类接口（涨跌停池/龙虎榜/两融/北向/个股资金流）闲置未用；报告已深化为「补足而非替换」，需 ADR 定夺 | [findings/review08.md](./findings/review08.md) | 已出 ADR 并完成试点（权限降级 → 切源 → 来源标注两侧可区分）；前置：F9（review06）关闭 + 候选接口联网可用性验证 + D3 结论 |
| 文档体系·AI 可执行性 | （DOC 系列） | 关闭中 | 修复分批落地见各 PR；未关闭项按 DOC-14 在本索引维护，已落地 ID 见 [governance-ids.md](../governance/governance-ids.md)「使用中」 | docs/reviews/README.md / 各 PR | 对应 DOC ID 均已在 [governance-ids.md](../governance/governance-ids.md) 登记为落地，且对应 chip 校验通过 |