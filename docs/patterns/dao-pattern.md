# DAO 模式

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（data 分层）、§3.1 R4/R5/R8/R12/R13/R17（数据库红线）；实现模板见本节。

所有数据访问通过 `BaseDao` 子类，统一提供：

- `_read_db()` — 原生 SQL 读取，返回 DataFrame
- `_read_db_select()` — SQLAlchemy Core 查询 (**推荐**，防注入)
- `_write_db()` — 单条写入 (批量写入请使用 `_save_upsert()`，`CacheManager.write_db` 已移除 `is_many` 参数)
- `_save_upsert()` — 批量 UPSERT (**推荐**，基于 `pg_insert` + `ON CONFLICT`)
- `chunked_in_query()` — 分块 IN 查询 (避免参数上限)

**DAO 继承体系**: `BaseDao` → 具体子类见 `data/persistence/daos/` 目录

> **操作指引**：新增/修改 DAO 或数据表的完整步骤见 [docs/guides/how-to.md](../guides/how-to.md)「1. 新增一张数据表」与「2. 新增一个 DAO」；新增数据表前须更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS`（CLAUDE.md §3.1 R12）并在 DAO 中注册（R13），涉及 schema 变更须生成 Alembic 迁移。
