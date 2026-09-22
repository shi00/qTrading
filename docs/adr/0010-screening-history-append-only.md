# ADR-0010: screening_history 唯一键改回 append-only（含 run_id）

> Status: Accepted
> Date: 2026-09-22
> Owner: 架构维护者
> Supersedes: ADR-0007（唯一键改覆盖语义 → 反转回 append-only）

## Context

ADR-0007 将 `screening_history` 唯一键由 `(run_id, ts_code)` 改为
`(trade_date, strategy_name, ts_code)`，采用覆盖语义：同一天同一策略对同一股票
多次运行（用户反复改参重跑）时，新运行覆盖旧运行，保留最新快照。

覆盖语义解决了一个真实问题（复盘统计 UN-04 的重复计数 / 系统性偏好偏差），但
其代价在后续检视（REVIEW-05 RV-03）中被重新评估后不再可接受：

1. **同日重跑覆盖销毁研究记录**：用户当天用 PE 上限 20 跑一次、再用 15 跑一次是
   最自然的调参探索方式，但 `params_snapshot` / `ai_reason` / `thinking`（AI 当时
   的推理过程）被第二次运行整体覆盖，无法追溯「当时为什么选这个」。
2. **孤儿记录残留**：只在第一次运行入选的股票残留一条行，其 `params_snapshot`
   是第二次的参数，但该股并不满足第二次参数——记账假象。
3. **跨日重跑的「状态与标签矛盾」**：覆盖把已定稿的 `review_status=COMPLETED`
   打回 `PENDING`，但 `alpha` / `prediction_result` 因是 computed 列而保留旧值，
   产生「状态待复盘、却带已定稿标签」的矛盾行。
4. `get_strategy_review_stats` 的 `DISTINCT ON ... ORDER BY run_id DESC` 成为死逻辑：
   唯一键既已保证组内单行，去重永远只有一个候选——需求意图与实现分叉的信号。

## Decision

将 `screening_history` 唯一键改为 `(trade_date, strategy_name, ts_code, run_id)`，
研究记录恢复 append-only 语义：

1. **唯一键加入 `run_id`**：当日当策略当代码的每次运行独立成行，
   `params_snapshot` / `ai_reason` / `thinking` 完整保留、互不覆盖。
2. **历史树按 `(trade_date, strategy_name, run_id)` 分组**：用户看到「今天跑了 3 次」
   并分别打开；展示代表值取 `MAX(id)`（id 单调递增 = 最近一次，run_id 随机 hex
   不能代表时间序）。
3. **统计口径显式决策**：`get_strategy_review_stats` / `get_ai_attribution_stats`
   取**最近一次运行**（`DISTINCT ON (trade_date, strategy_name, ts_code) ORDER BY
   ... id DESC`），保持与覆盖语义下相同的「当前展示快照」语义——这是显式决策而非
   副作用；`DISTINCT ON` 从死逻辑变真逻辑。
4. **AI 学习样本去重**：`get_learning_context` 按 3 键去重取最近运行，防止同日同股
   多 run 重复取样挤占 few-shot 多样性。
5. **Alembic 迁移 0033**：删 3 键约束、建 4 键约束。`downgrade` 按 3 键去重（保留
   id 最大者，对齐 0024 先例）再建 3 键约束——去重即丢弃旧 run 行（数据损失，
   注释明确）。

## Consequences

**正向**
- 研究记录可完整追溯：同日多次参数运行的历史快照 / AI 推理过程 / thinking 全保留。
- 消除孤儿记录与「状态 PENDING 带旧标签」矛盾行。
- 统计去重口径从隐性副作用变为显式决策（取最近一次运行）。

**负向**
- 表体积随重跑次数增长（无自动保留策略，日常单策略单日运行量级下可控；若后续
  重跑频率显著上升需补保留策略，登记为技术债候选）。
- `downgrade` 不可逆（重建 3 键约束前按 3 键去重，丢弃旧 run 行）。

## Alternatives

- **维持覆盖语义，最低限度修补**（覆盖时不重置已定稿标签 / 覆盖前归档）：不解决
  调参探索场景的研究记录销毁问题，方案检视否决。
- **唯一键保持 3 键 + 查询层 `DISTINCT ON` 去重**（ADR-0007 曾考虑的方案）：保留
  全部快照、零 schema 变更，但复盘入池层统计仍依赖方言与分散维护，且快照追溯
  语义仍不显式，未采纳。