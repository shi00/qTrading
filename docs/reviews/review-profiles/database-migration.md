# 数据库与迁移专项 Profile

> 加载方式：由 [ai-review.md §6](../ai-review.md#6-风险信号--专项-profile-触发表) 触发。只补充通用维度的增量风险。

## 检视要点

- 线上数据量下 DDL 的锁和耗时；
- 新旧应用与 schema 的兼容窗口；
- 回填能否暂停、重启、限速和幂等；
- 约束、索引和真实查询计划；
- 迁移失败后的中间状态；
- 回滚或前向修复路径。

## 项目特定（AStockScreener）

- schema 变更必须生成 Alembic 迁移，验证 `upgrade head` + `alembic check`（CI 验证 `downgrade base` → `upgrade head`）
- 项目红线：定义见 [CLAUDE.md §3.1](../../../CLAUDE.md#31--绝对禁止)；本域高频红线 R4 / R8 / R12 / R17（非穷举），其中 R17 无完整自动门禁，人工追查步骤见 [project-profile.md](./project-profile.md) 的「红线自查步骤」。
