# 大表分区策略

> STATUS: Planning（第一阶段评估完成，未实施）

## 背景

随着数据积累，部分表将达到百万甚至千万级行数。未分区的表会导致：

1. **VACUUM 性能下降** - 长时间运行的 VACUUM 操作可能阻塞写入
2. **索引膨胀** - B-tree 索引效率降低
3. **缓存利用率低** - 历史数据占用缓冲池但访问频率低
4. **备份恢复慢** - 单一大表文件备份恢复时间长

## 数据量估算

| 表名 | 年增长量 | 10年预估 | 30年预估 |
|------|----------|----------|----------|
| daily_quotes | ~130万行 | ~1300万行 | ~4000万行 |
| daily_indicators | ~130万行 | ~1300万行 | ~4000万行 |
| moneyflow_daily | ~130万行 | ~1300万行 | ~4000万行 |
| screening_history | ~5-50万行 | ~500万行 | ~1500万行 |

## 分区策略

### 推荐方案：按月范围分区

PostgreSQL 10+ 支持声明式分区，对应用层透明。

```sql
-- 示例：按月分区 daily_quotes
-- 注：PostgreSQL 分区表要求 PRIMARY KEY / UNIQUE 约束必须包含所有分区键列。
-- 此处 (ts_code, trade_date) 已含分区键 trade_date，可声明为 PRIMARY KEY；
-- 若分区键变更（如改为按 ts_code HASH 分区），需同步调整 PRIMARY KEY。
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
    updated_at TIMESTAMP,
    created_at TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date)
) PARTITION BY RANGE (trade_date);

-- 创建月度分区
CREATE TABLE daily_quotes_2026_01 PARTITION OF daily_quotes
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE daily_quotes_2026_02 PARTITION OF daily_quotes
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
```

### 使用 pg_partman 自动管理

```sql
-- 安装扩展
CREATE EXTENSION pg_partman;

-- 配置自动分区
SELECT partman.create_parent(
    p_parent_table := 'public.daily_quotes',
    p_control := 'trade_date',
    p_type := 'native',
    p_interval := '1 month',
    p_premake := 3  -- 提前创建 3 个未来分区
);
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

1. 在现有表旁创建新的分区表
2. 分批迁移数据（低流量时段）
3. 验证应用查询兼容性
4. 对比性能指标

### 第三阶段：维护

1. 配置 pg_partman 自动创建分区
2. 设置历史数据保留策略（可选）
3. 监控分区大小和查询性能

## 需要分区的表

| 优先级 | 表名 | 分区键 | 原因 |
|--------|------|--------|------|
| 高 | daily_quotes | trade_date（月） | 最大事实表 |
| 高 | daily_indicators | trade_date（月） | 大型事实表 |
| 中 | moneyflow_daily | trade_date（月） | 增长型事实表 |
| 中 | northbound_holding | trade_date（月） | 增长型事实表 |
| 低 | screening_history | created_at（月） | 中等增长 |

## 冷数据归档（可选）

对于 5 年以上的历史数据：

1. **分离旧分区** - 从主表分离但保持可查询
2. **迁移冷存储** - 导出为 Parquet 文件或归档数据库
3. **压缩存储** - 使用 pg_compress 或外部压缩
4. **备份确认** - 确保归档数据已备份

```sql
-- 分离旧分区（数据仍可查询）
ALTER TABLE daily_quotes DETACH PARTITION daily_quotes_2018_01;

-- 或删除旧分区（备份后）
DROP TABLE daily_quotes_2018_01;
```

## 性能考量

### 优势

- **查询加速** - 分区裁剪减少扫描范围
- **维护高效** - VACUUM、ANALYZE、REINDEX 在小分区上执行
- **并行查询** - PostgreSQL 可并行扫描分区
- **归档便捷** - 旧分区可直接分离或删除

### 权衡

- **文件数量增加** - 每个分区是独立的表文件
- **查询规划开销** - 分区越多，规划时间越长
- **约束排除依赖** - 查询必须包含分区键才能裁剪

### 最佳实践

1. 保持分区数量合理（数百个，而非数千个）
2. 查询条件包含分区键以触发裁剪
3. 在每个分区上创建索引（自动继承）
4. 监控分区大小，必要时调整分区间隔

## 监控查询

```sql
-- 检查分区大小
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE tablename LIKE 'daily_quotes_%'
ORDER BY tablename;

-- 检查分区裁剪效果
EXPLAIN ANALYZE 
SELECT * FROM daily_quotes 
WHERE trade_date = '2026-01-15';
```

## 相关文档

- [PostgreSQL 分区文档](https://www.postgresql.org/docs/current/ddl-partitioning.html)
- [pg_partman 扩展](https://github.com/pgpartman/pg_partman)
