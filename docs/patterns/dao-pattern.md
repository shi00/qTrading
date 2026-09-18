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

## 路由（必读 / 条件触发 / 完成判定）

> 本文件是被 [CLAUDE.md §1.8](../../CLAUDE.md#18-任务类型--必读文件-决策树) 路由的「新增/修改 DAO 或数据表」canonical 入口。判断信息见下，具体步骤以 [docs/guides/how-to.md](../guides/how-to.md) 为准，本文件不复制步骤正文。

### 必读

新增或修改 DAO / 数据表任务，至少阅读：

1. [CLAUDE.md](../../CLAUDE.md) §3.1：R4 / R5 / R8 / R12 / R13 / R17（数据库红线）与 §4.1（data 分层边界）
2. [docs/guides/how-to.md](../guides/how-to.md)「1. 新增一张数据表」与「2. 新增一个 DAO」（流程正本）
3. [docs/governance/redlines.yml](../governance/redlines.yml)：R12 / R13 / R17 的 `rule_type` 与豁免语义
4. `data/persistence/daos/base_dao.py` 与 `data/persistence/daos/` 下既有 DAO 样例

### 条件触发

- 新增或修改**数据表**：必读 [how-to.md](../guides/how-to.md#1-新增一张数据表)；涉及 schema 变更须生成 Alembic 迁移（`upgrade head` 验证）
- 新增 **DAO**：必读 [how-to.md](../guides/how-to.md#2-新增一个-dao)；登记进 `data/cache/dao_registry.py` 的 `_DAO_REGISTRY`（R13）并在 `data/cache/cache_manager.py` 的 `CacheManager.__init__` 显式实例化，engine 由 `sync_engines()` 统一驱动
- 新增表只改 `models.py` 忘记更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` → 触发 R12
- 表名 / 列名用数字开头、含特殊字符或 SQL 保留字 → 触发 R17（必须用 ORM `name=` 映射，禁拼接该列名裸 SQL）
- 批量写入 → 必须 `_save_upsert()`（R8，`_write_db` 不提供批量参数）
- 读取 → 优先 `_read_db_select()`（SQLAlchemy Core，防注入）；仅需原生 SQL 特性时才退用 `_read_db`

### 完成判定

- 数据表：`models.py` 建模 + `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册 + 必要时 Alembic 迁移（`upgrade head` 与 `python -m alembic check` 通过）
- DAO：继承 `BaseDao`、在 `_DAO_REGISTRY` 登记、`CacheManager.__init__` 实例化、`sync_engines()` 同步 engine
- 单测：`tests/unit/` 下使用 mock engine 隔离 DB
- 门禁：`redline-check`（R12 / R13）、`pyright`、相关单测通过
