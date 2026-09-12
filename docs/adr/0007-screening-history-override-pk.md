# ADR-0007: screening_history 唯一键改覆盖语义

> Status: Accepted
> Date: 2026-09-12
> Owner: 架构维护者

## Context

`screening_history` 唯一约束原为 `(run_id, ts_code)`，而每次执行选股/预测都会生成全新的 `run_id`（`review_manager.save_results` / `ui/viewmodels/ai_stream_mixin.py` / `services/scheduled_jobs/nightly_prediction.py`）。因此同一天对同一股票用同一策略多次运行（用户反复改参重跑）会在表中产生多条记录。下游复盘查询 `get_pending_predictions` / `get_learning_context` 将它们当作独立预测样本统计，导致 UN-04（可信验证研究方法）的统计口径被污染：样本重复计数，且偏差系统性偏向用户反复调试的策略。

检视报告 LIFE-03 提出两种方案：复盘入池层 `DISTINCT ON` 去重（低风险，保留全部快照），或改主键为新运行覆盖旧运行（统计口径最干净，丢弃中间快照）。经方案检视（第 2 轮对抗检视）确认 `DISTINCT ON` 方案与现有历史树按 run_id 分组的展示模型冲突较小，但用户最终决策采用**改主键覆盖**，明确接受「丢弃中间快照」的代价与历史树分组语义调整。

## Decision

将 `screening_history` 唯一键由 `(run_id, ts_code)` 改为 `(trade_date, strategy_name, ts_code)`，新运行以覆盖语义落库：

1. 保留 `id` 自增主键（供 `screening_thinking.history_id` 外键稳定引用、复盘 T+1/T+5 回填 WHERE 定位）。
2. `run_id` 降级为普通列（仅作批次展示），不再是唯一性维度；覆盖时该值更新为最新一次运行的 run_id。
3. `_save_upsert` 的 `pk_columns` 改为三个字段。
4. 历史树 `get_history_tree` 由「按 run_id 分组」改为「按 (trade_date, strategy_name) 聚合」，run_id 取该组合最新代表值；点击节点按 (trade_date, strategy_name) 载入当前保留快照。
5. Alembic 迁移 0020：先清理存量重复（保留 created_at 最新行，screening_thinking 经 FK CASCADE 级联），再替换唯一约束。`upgrade` 的去重删除不可逆，`downgrade` 仅重建旧约束、无法恢复被删行；同一 `created_at` 的兜底去重按 `id` 保留最小行，避免逐对互删清空整组。
6. 覆盖时显式将 `review_status` 置 `PENDING`：若被覆盖行此前已复盘（COMPLETED），其 `prediction_result` / `alpha` 等 computed 列不在本次写入列而保留，但状态重置为 PENDING 重新进入待复盘，保证复盘基于当日最新快照（覆盖即需重复盘）。

## Consequences

**正向**
- 复盘统计（UN-04）口径干净：同一策略同日同股票仅一个样本，重复重跑不虚增分母、不引入系统性偏好偏差。
- 无需在统计查询层引入 `DISTINCT ON`（PostgreSQL 方言）分散维护。

**负向**
- 丢失同日同策略多次运行的历史中间快照（用户已明确接受）。
- 历史树不再按 run_id 展示「每次运行批次」，而是按「日 × 策略」聚合展示当前保留快照；run_id 的批次追溯语义降级。
- 涉及数据库迁移，破坏性操作需随 PR 做迁移回归（upgrade head / downgrade base / upgrade head）。

## Alternatives

- **复盘入池层 `DISTINCT ON` 去重**：保留全部快照、零 schema 变更、零 run_id 破坏，成本最低。被用户否决：仍会遗留冗余记录且统计口径依赖查询层方言。
- **保留多快照 + SUPERSEDED 终态**：同类，未采纳。
- **不处理**：统计口径持续失真（UN-04），不可接受。