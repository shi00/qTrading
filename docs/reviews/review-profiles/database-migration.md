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
- R8 批量写入必须用 `_save_upsert()`，禁止 `_write_db(is_many=True)`
- R12 新增表必须更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS`
- R17 禁止 SQL 保留字作字段名，必须用 ORM `name=` 映射
- R4 asyncpg 原生查询必须用 `$1, $2, ...` 占位符，禁止 `%s`
