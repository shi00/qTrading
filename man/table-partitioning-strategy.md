# 大表分区策略

> STATUS: Planning（第一阶段评估完成，未实施）
>
> 适用版本：PostgreSQL 16.14.0+（内置 sidecar 16.14.0，外置模式同版本下限，见 [CONTRIBUTING](../CONTRIBUTING.md)「项目完整技术栈」）。声明式分区自 10 起可用，`DETACH PARTITION ... CONCURRENTLY` 自 14 起可用
>
> 读者：数据库/数据工程人员（DBA）；所有 schema 变更须以 Alembic 迁移交付，见「前置条件与风险」

## 前置条件与风险

本文为规划/设计文档，**不是可照抄执行的运维手册**。按本文实施前必须确认：

1. **执行账号**：DDL 使用迁移账号 `astock_admin`，应用账号 `astock_app` 无 DDL 权限（见 [Database Account Separation](./database-account-separation.md)）。
2. **迁移通道**：schema 由 Alembic 管理（`alembic/versions/`，当前 head 为 `0041`），`DbMigrator` 启动时执行 `upgrade head`。分区改造必须写成新的 Alembic revision；直接手工 DDL 会使数据库结构与迁移链分叉，后续 revision 可能在已分区的表上执行失败。
3. **前置备份**：涉及建表、改名切换、DETACH/DROP 的操作，必须先 `pg_dump -Fc` 备份目标表并记录 `COUNT(*)`，用于回滚与迁移后校验。
4. **锁与窗口**：`CREATE TABLE ... PARTITION OF`、`ATTACH/DETACH PARTITION`、`ALTER TABLE ... RENAME` 默认在父表上取 `ACCESS EXCLUSIVE` 锁（`DETACH ... CONCURRENTLY` 除外），会阻塞该表读写；须安排在低峰或停机窗口，并评估长事务。
5. **不可逆操作**：`DROP TABLE <分区>` 直接删除该分区数据，事务提交后无法用 SQL 撤销，只能从备份恢复。

## 背景

随着数据积累，部分表将达到百万甚至千万级行数。未分区的表会导致：

1. **autovacuum 回收不及时** - 单表膨胀后 autovacuum 单轮周期变长、死元组峰值升高（普通 `VACUUM` 不阻塞读写，仅 `VACUUM FULL` 持排他锁）
2. **索引与缓存效率下降** - 索引随表线性增长，常驻内存的索引/数据页比例下降，随机访问 I/O 增加
3. **缓存利用率低** - 历史数据占用缓冲池但访问频率低
4. **备份恢复慢** - 单表越大，`pg_dump`/恢复与校验耗时越长

## 数据量估算

| 表名 | 年增长量 | 10年预估 | 30年预估 |
|------|----------|----------|----------|
| daily_quotes | ~130万行 | ~1300万行 | ~4000万行 |
| daily_indicators | ~130万行 | ~1300万行 | ~4000万行 |
| moneyflow_daily | ~130万行 | ~1300万行 | ~4000万行 |
| northbound_holding | 未估算（随沪深港通标的名单变化） | — | — |
| screening_history | ~5-50万行 | ~500万行 | ~1500万行 |

估算口径：日频行情类表按「约 5400 只标的 × 约 242 个交易日 ≈ 130 万行/年」推算；`screening_history` 按每日每次运行命中股票数（数十至数百）与运行频次推算，故给区间。行数为量级估算，实施前应以 `COUNT(*)` 实测校准。

## 分区策略

### 推荐方案：按月范围分区

PostgreSQL 声明式分区对**查询**透明，但不覆盖**写入越界**：分区表没有任何匹配分区时，`INSERT` 会报 `no partition of relation "daily_quotes" found for row`。因此必须为存量数据与未来写入范围预先建好分区（或用 DEFAULT 分区兜底），否则同步任务会直接失败。

```sql
-- 示例：按月分区 daily_quotes（DDL 常量，无动态拼接；执行前确认已备份）
-- 该示例用于新父表（如 daily_quotes_part）或新库；库中已存在同名 daily_quotes 时会报 already exists，勿直接执行
-- 注：PostgreSQL 分区表要求 PRIMARY KEY / UNIQUE 约束必须包含全部分区键列。
-- 此处 (ts_code, trade_date) 已含分区键 trade_date，可声明为 PRIMARY KEY；
-- 若分区键变更（如改为按 ts_code HASH 分区），需同步调整 PRIMARY KEY。
-- updated_at / created_at 的 DEFAULT 必须与 models.py 的 server_default=now() 一致，
-- 否则应用省略该列写入时会落 NULL。
CREATE TABLE daily_quotes (
    ts_code TEXT,
    trade_date DATE,
    open NUMERIC(12,4),
    high NUMERIC(12,4),
    low NUMERIC(12,4),
    close NUMERIC(12,4),
    pre_close NUMERIC(12,4),
    change NUMERIC(12,4),
    pct_chg NUMERIC(8,4),
    vol BIGINT,
    amount NUMERIC(20,4),
    adj_factor NUMERIC(20,12),
    updated_at TIMESTAMP DEFAULT now(),
    created_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 父表索引会为每个分区自动创建子索引，缺失会退化 (trade_date, ts_code) 查询
CREATE INDEX ix_daily_quotes_date_code ON daily_quotes (trade_date, ts_code);

-- 创建月度分区（以下两行为示例；实施时必须覆盖库内最早 trade_date 至未来 3 个月）
CREATE TABLE daily_quotes_2026_01 PARTITION OF daily_quotes
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE daily_quotes_2026_02 PARTITION OF daily_quotes
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- 过渡期兜底：避免漏建月份导致写入失败
-- 注意：DEFAULT 分区内的数据不参与分区裁剪；且后续 ATTACH 新分区时需扫描 DEFAULT 分区校验边界
CREATE TABLE daily_quotes_default PARTITION OF daily_quotes DEFAULT;
```

### 使用 pg_partman 自动管理

手工建分区与 `create_parent` 二选一：已按上节手工建过子分区的父表不要再调用 `create_parent`（命名与边界冲突会导致配置失败）；父表存在 DEFAULT 分区时，`create_parent` 需额外处理 DEFAULT 分区内已落数据，否则新分区建好后仍会留下无法裁剪的数据。

```sql
-- 前置：需要超级用户与随附的扩展文件；内置 sidecar 是否随附 pg_partman 须先核实，
-- 未随附时只能改用外部 PostgreSQL 或手写分区 DDL。
CREATE SCHEMA IF NOT EXISTS partman;
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;

-- 配置自动分区（参数以 pg_partman 4.x 为例；5.x 参数名有调整，须对照所用版本文档）
-- p_start_partition 必须覆盖存量数据最早日期，否则该日期之前的数据没有对应分区。
SELECT partman.create_parent(
    p_parent_table := 'public.daily_quotes',
    p_control := 'trade_date',
    p_type := 'native',
    p_interval := '1 month',
    p_premake := 3,  -- 提前创建 3 个未来分区
    p_start_partition := '2026-01-01'  -- 按实际最早数据日期填写
);
```

`create_parent` 只负责「建/管分区」，**不会迁移存量数据**。自动建分区还依赖周期维护，否则缓冲用尽后写入仍会失败：

```sql
-- 方式一：调度调用（pg_cron / 系统定时任务，每日一次）
-- 4.x 为 run_maintenance_proc()；5.x 为 run_maintenance()，以所用版本为准
SELECT partman.run_maintenance_proc();

-- 方式二：后台 worker（4.x 提供；需在 postgresql.conf 设置后重启）
-- shared_preload_libraries = 'pg_partman_bgw'
-- partman.bgw_config 中配置分析的库与间隔
```

### 分区裁剪优势

查询时 PostgreSQL 自动跳过无关分区：

```sql
-- 此查询仅扫描 2026 年 1-3 月的分区
SELECT * FROM daily_quotes
WHERE trade_date BETWEEN '2026-01-01' AND '2026-03-31';
```

## 实施计划

### 第一阶段：评估（当前）

- [x] 识别需要分区的表
- [x] 估算数据增长率
- [x] 设计分区策略

### 第二阶段：实施（数据量达到 ~1000 万行时）

1. 写入新的 Alembic revision（含建分区父表、索引、分区 DDL），保证与 `models.py` 一致
2. 在现有表旁创建分区父表 `daily_quotes_part`，按存量最早月份至未来 3 个月批量预建分区。**索引/约束命名须避让旧表**：`ALTER TABLE ... RENAME` 不会重命名索引与约束，旧表的 `ix_daily_quotes_date_code`、`daily_quotes_pkey` 仍在，新父表直接建同名对象会报 already exists（可先改用 `_part` 后缀名，切换后再 `ALTER INDEX ... RENAME`）
3. 分批迁移数据（低流量时段，按 `trade_date` 区间分批 `INSERT ... SELECT`，每批提交并校验行数）
4. 校验：`COUNT(*)` 与 `SUM` 抽样、索引存在性、应用查询返回一致
5. 切换：在维护窗口内重命名（`daily_quotes` → `daily_quotes_old`，`daily_quotes_part` → `daily_quotes`），执行 `ANALYZE`
6. 观察期：至少保留原表一个同步周期，对比查询计划与实际耗时
7. 确认后再删除 `daily_quotes_old`（删除前再次备份）

**回滚**：切换后若出现写入失败或性能退化，立即反向重命名（`daily_quotes` → `daily_quotes_part`，`daily_quotes_old` → `daily_quotes`），并回退对应 Alembic revision。观察期内**不得**删除原表或提前 `DROP` 任何分区；回滚前提是原表未被写入（切换窗口内应停写或双写，否则切换后新写入的数据需回灌）。

### 第三阶段：维护

1. 配置 pg_partman 自动创建分区，并调度维护命令（版本对应命令见上节；未调度等于没配置）
2. 历史数据保留策略须经产品/研究负责人确认后再执行（见「冷数据归档」）
3. 监控分区大小和查询性能

## 需要分区的表

| 优先级 | 表名 | 分区键 | 原因 |
|--------|------|--------|------|
| 高 | daily_quotes | trade_date（月） | 最大事实表 |
| 高 | daily_indicators | trade_date（月） | 大型事实表 |
| 中 | moneyflow_daily | trade_date（月） | 增长型事实表 |
| 中 | northbound_holding | trade_date（月） | 增长型事实表 |
| 低 | screening_history | trade_date（月） | 中等增长；改造需同步调整主键/外键（见下） |

> **screening_history 特别说明**：该表主键是 `id`（BIGINT 自增），`created_at` 无索引，且热点查询（历史树、待复盘池、统计）全部按 `trade_date` 过滤；若按 `created_at` 分区，这些查询完全无法裁剪，只会增加规划开销。但改为按 `trade_date` 分区需要：主键改为 `(trade_date, id)` 或放弃主键；唯一键 `uq_screening_history_dat_strategy_code_run` 已含 `trade_date` 可保留；`screening_thinking.history_id` 对 `screening_history.id` 的级联外键无法只引用单列，需扩展为复合键或改由应用层保证级联。因此该表优先级低，须单独设计迁移方案。

## 冷数据归档（可选）

对于 5 年以上的历史数据。**归档不是删除的同义词**：先归档并校验，再决定是否删除；保留期须由产品/研究负责人确认。

1. **备份确认** - `pg_dump -Fc -t daily_quotes_2018_01` 导出并校验可恢复
2. **分离旧分区** - 从主表分离，数据仍以独立表可查询（PG16 可用 `CONCURRENTLY` 降低锁影响）
3. **迁移冷存储** - 导出为 Parquet 或归档数据库；压缩使用 `pg_dump -Fc`、Parquet（ZSTD）或文件系统压缩（`pg_compress` 非 PostgreSQL 官方扩展，本文无法核实其可用性，勿作为方案依赖）
4. **删除旧分区** - 仅在归档校验通过且过了观察期后执行

```sql
-- 1) 先备份并校验（在 shell 中执行，不在事务内）
-- pg_dump -Fc -t daily_quotes_2018_01 -f daily_quotes_2018_01.dump "$DATABASE_URL"

-- 2) 分离旧分区：不阻塞读写的并发方式（PG14+）
--    注意：CONCURRENTLY 不能在事务块内执行，不能写进 Alembic 迁移；须作为独立运维步骤执行
ALTER TABLE daily_quotes DETACH PARTITION daily_quotes_2018_01 CONCURRENTLY;

-- 分离后该表仍可查询，但不再受父表约束/索引管理；若要重新挂回需满足分区边界
-- ALTER TABLE daily_quotes ATTACH PARTITION daily_quotes_2018_01
--     FOR VALUES FROM ('2018-01-01') TO ('2018-02-01');

-- 3) 删除旧分区：不可逆！执行前确认已备份、归档校验通过、无外键/视图依赖
-- DROP TABLE daily_quotes_2018_01;
```

> 删除分区会永久丢失该时段历史数据。`screening_history` 属 append-only 研究记录（RV-03），是复盘统计与 AI 学习样本的来源，删除旧分区前必须评估对历史回看与模型学习的影响。

## 性能考量

### 优势

- **查询加速** - 分区裁剪减少扫描范围
- **维护高效** - autovacuum/ANALYZE/REINDEX 按分区独立执行，单次处理的数据量更小
- **并行查询** - 计划允许时可并行扫描多个分区（受 `max_parallel_workers_per_gather` 等配置约束）
- **归档便捷** - 旧分区可直接分离（或校验后删除）

### 权衡

- **文件数量增加** - 每个分区是独立的表文件（含各自的索引）
- **查询规划开销** - 分区越多，规划时间越长；未裁剪的查询会逐分区生成计划
- **分区裁剪有前提** - 查询条件必须包含分区键（对 `trade_date` 做函数运算、类型不匹配会失效）；应用使用预处理语句时，generic plan 可能无法裁剪，需关注执行计划
- **DDL 锁** - 建/挂/摘分区默认取 `ACCESS EXCLUSIVE` 锁，维护操作需窗口
- **跨分区约束受限** - 分区表上的主键/唯一约束必须包含分区键；引用分区表的外键也需指向包含分区键的唯一约束

### 最佳实践

1. 按单表控制分区数量（月分区 × 30 年 ≈ 360 个/表；5 张表合计约 1800 个），并实测规划开销
2. 查询条件包含分区键以触发裁剪
3. 在父表上建索引，子分区自动继承（不要在单个分区上单独建）
4. 监控分区大小，必要时调整分区间隔（如改为按年分区）

## 监控查询

```sql
-- 按分区树检查大小（覆盖任意命名的分区；DETACH 后的表不在树内）
SELECT
    c.relname,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_partition_tree('daily_quotes') AS t
JOIN pg_class c ON c.oid = t.relid
ORDER BY pg_total_relation_size(c.oid) DESC;

-- 检查分区裁剪效果：计划中应只出现命中的分区
EXPLAIN ANALYZE
SELECT * FROM daily_quotes
WHERE trade_date = '2026-01-15';
```

## 相关文档

- [PostgreSQL 分区文档](https://www.postgresql.org/docs/current/ddl-partitioning.html)
- [pg_partman 扩展](https://github.com/pgpartman/pg_partman)
- [Database Account Separation](./database-account-separation.md)（DDL 账号与最小权限）
