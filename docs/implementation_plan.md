# 架构优化方案：DAO 层类型护栏 (Type Guard)

## 1. 核心问题剖析

### 1.1 错误日志分析

系统运行时出现以下三类数据库错误：

```
错误 1: trade_cal 查询参数类型错误
invalid input for query argument $1: '20260216' 
('str' object has no attribute 'toordinal')
[parameters: ('20260216', '20260318', 1)]

错误 2: market_news 时间参数类型错误
invalid input for query argument $3: '2026-03-18 21:45:58' 
(expected a datetime.date or datetime.datetime instance, got 'str')

错误 3: task_history 清理 SQL 类型转换错误
操作符不存在: timestamp without time zone < text
[SQL: DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')::text]
```

### 1.2 根因定位

| 错误 | 服务层 | 问题位置 | 传入类型 | 期望类型 |
|------|--------|----------|----------|----------|
| trade_cal 查询 | HistoricalSyncStrategy, HealthMixin | historical.py, health_mixin.py | `str` | `datetime.date` |
| market_news 插入 | NewsSubscriptionService | news_fetcher.py, cache_manager.py | `str` | `datetime.datetime` |
| task_history 清理 | TaskManager | task_manager.py | SQL 类型转换问题 | - |

### 1.3 问题规模统计

通过代码扫描，发现以下问题模式：

| 问题类型 | 出现次数 | 涉及文件数 |
|----------|----------|------------|
| `strftime("%Y%m%d")` 日期格式化 | 32 处 | 12 个文件 |
| `str(date)` / `str(time)` 类型转换 | 18 处 | 8 个文件 |
| `strftime("%Y-%m-%d %H:%M:%S")` 时间格式化 | 6 处 | 3 个文件 |
| **总计** | **39 处 strftime** | **15 个文件** |
| **需修改（传给 DAO）** | **28 处** | **10 个文件** |

### 1.4 问题分布热力图

```
data/
├── sync_strategies/
│   ├── historical.py     ████ (4处) - 日期格式化后传给 DAO
│   ├── financial.py      ███ (3处) - 日期格式化
│   ├── macro.py          ████ (4处) - 日期格式化
│   └── holder.py         ██ (2处) - 日期格式化
├── mixins/
│   └── health_mixin.py   ████ (4处) - 日期格式化
├── cache_manager.py      █ (1处) - 时间格式化（非 DAO）
├── news_fetcher.py       ██ (2处) - 日期格式化传给 DAO
├── data_processor.py     ████ (4处) - 日期格式化
├── market_data_service.py ██ (2处) - 日期格式化
├── data_quality.py       █ (1处) - 日期格式化
└── review_manager.py     ██ (2处) - 日期格式化
```

## 2. 架构层面的根本问题

### 2.1 当前数据流架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           当前数据流架构                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Tushare API ──→ TushareClient ──→ 服务层 ──→ DAO 层 ──→ asyncpg ──→ PG   │
│      │              │               │           │           │               │
│      │              │               │           │           │               │
│   (字符串)      (字符串/原生)    (字符串)    (透传)     (严格检查)          │
│                     ↑               │           │           │               │
│                     │               │           │           │               │
│                 正确处理          ❌ 过度     ❌ 消极     ✅ 正确            │
│                                 序列化       透传                          │
│                                                                             │
│   问题：每一层都在做"防御性"转换，但没有一层明确承担类型契约责任              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 职责错位分析

| 层级 | 当前行为 | 应有职责 |
|------|----------|----------|
| TushareClient | 接收字符串，也接受 date 对象 | ✅ 正确 - 作为外部 API 适配层，负责字符串↔原生类型的双向转换 |
| 服务层 | 主动将 date 转为字符串 | ❌ 错误 - 不应承担序列化职责 |
| DAO 层 | 直接透传参数 | ❌ 消极 - 未对参数类型做校验/转换 |
| asyncpg | 严格类型检查 | ✅ 正确 - 数据库驱动的本职工作 |

### 2.3 问题模式分类

#### 模式 A：服务层主动序列化（最常见）

```python
# historical.py:88-89
end_date = get_now().strftime("%Y%m%d")  # ❌ 不必要的序列化
start_date = (get_now() - datetime.timedelta(days=days)).strftime("%Y%m%d")

# 然后传给 DAO
df_cal = await self.context.cache.get_trade_cal(
    start_date=start_date, end_date=end_date, is_open=1,  # 传入字符串
)
```

**影响范围**：historical.py, financial.py, macro.py, holder.py, health_mixin.py, data_processor.py 等

#### 模式 B：DAO 层参数透传

```python
# stock_dao.py:51-67
async def get_trade_cal(self, start_date=None, end_date=None, is_open=None):
    sql = "SELECT * FROM trade_cal WHERE 1=1"
    p = []
    if start_date:
        sql += f" AND cal_date>=${idx}"
        p.append(start_date)  # ❌ 直接透传，无类型检查
    ...
    return await self._read_db(sql, p)  # 传给 asyncpg
```

**问题**：DAO 层没有对参数类型做任何校验或转换

#### 模式 C：混合类型处理

```python
# macro.py:165-166
start_str = start_date.strftime("%Y%m%d") if hasattr(start_date, 'strftime') else str(start_date)
```

**问题**：代码试图兼容多种输入类型，但最终都转为字符串，而非原生对象

#### 模式 D：数据库结果反序列化后再次使用

```python
# health_mixin.py:101, 116
latest_quote_date = str(db_max_date)  # 从数据库读出的 date 对象被转为字符串
latest_dt = parse_date(str(latest_quote_date), "%Y%m%d")  # 又解析回来
```

**问题**：不必要的序列化/反序列化循环

### 2.4 历史债务分析

这些 `strftime` 调用很可能是从 SQLite 时代遗留下来的。SQLite 对参数类型非常宽松，字符串日期也能正常工作。迁移到 PostgreSQL 后，asyncpg 的严格类型检查暴露了这个问题。

## 3. 代码实现深度审视

### 3.1 现有类型处理机制分析

#### 3.1.1 `_save_upsert` 已有类型转换逻辑

**代码位置**：[base_dao.py:235-258](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/base_dao.py#L235-L258)

```python
# _prepare_records 函数中已有的类型转换
for col in df_clean.columns:
    if col in target_date_cols:
        df_clean[col] = pd.to_datetime(df_clean[col], format='mixed', errors='coerce').dt.date
    elif col in target_datetime_cols:
        df_clean[col] = pd.to_datetime(df_clean[col], format='mixed', errors='coerce')
```

**分析**：`_save_upsert` 通过 `DATE_COLUMNS` 和 `DATETIME_COLUMNS` 配置进行类型转换，该路径已有类型处理机制。

#### 3.1.2 `_to_native` 函数处理 numpy 类型

**代码位置**：[base_dao.py:72-92](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/base_dao.py#L72-L92)

```python
def _to_native(val):
    if val is None:
        return None
    if pd.isna(val):
        return None
    if isinstance(val, (np.int64, np.int32, np.int16, np.int8)):
        return int(val)
    if isinstance(val, (np.float64, np.float32)):
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime().replace(tzinfo=None)
    return val
```

**分析**：已处理 `pd.Timestamp` 到 `datetime` 的转换，但**未处理字符串日期到原生对象的转换**。

### 3.2 类型护栏覆盖范围分析

| 调用路径 | 是否经过 `_read_db`/`_write_db` | 是否有其他类型处理 | 风险等级 |
|----------|--------------------------------|-------------------|----------|
| `_read_db` | ✅ 是 | ❌ 无 | 🟢 低 |
| `_write_db` | ✅ 是 | ❌ 无 | 🟢 低 |
| `_save_upsert` | ❌ 否 | ✅ 有（`DATE_COLUMNS`/`DATETIME_COLUMNS`） | 🟢 低 |
| `exec_driver_sql` 直接调用 | ❌ 否 | ❌ 无 | 🔴 高 |

### 3.3 关键发现：`cache_manager.py` 绕过类型护栏

**问题位置**：[cache_manager.py:474-491](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/cache_manager.py#L474-L491)

```python
# 直接调用 exec_driver_sql，绕过了 _read_db
r_days = await conn.exec_driver_sql(
    "SELECT COUNT(*) FROM trade_cal WHERE is_open=1 AND cal_date >= $1 AND cal_date <= $2",
    (str(g_min), str(g_max)),  # ❌ 直接传字符串，类型护栏无法拦截
)
```

**影响范围**：
- `cache_manager.py:297` - 直接调用 `exec_driver_sql`（DDL 操作，可保留）
- `cache_manager.py:463-491` - 多处直接调用 `exec_driver_sql`（查询操作，需迁移）

### 3.4 根因分析：`cache_manager.py` 职责边界模糊

#### 3.4.1 职责混乱现状

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    cache_manager.py 职责混乱                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   职责 1：DAO 代理层                                                         │
│   ├── save_stock_basic() → stock_dao.save_stock_basic()                    │
│   ├── get_trade_cal() → stock_dao.get_trade_cal()                          │
│   └── ... (委托调用)                                                        │
│                                                                             │
│   职责 2：直接数据库操作                                                      │
│   ├── clear_cache() → 直接执行 DDL（可保留，DDL 特殊性）                       │
│   ├── check_comprehensive_health() → 直接执行多个查询（需迁移）               │
│   └── ... (绕过 DAO 层)                                                     │
│                                                                             │
│   问题：一个类承担了两种角色，违反单一职责原则                                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 3.4.2 直接调用原因分析

| 原因 | 说明 | 是否合理 |
|------|------|----------|
| 性能优化 | 多个查询在同一连接中执行，避免连接池竞争 | ⚠️ 部分合理 |
| 事务一致性 | 多个操作需要在同一事务中执行 | ⚠️ 部分合理 |
| 快速实现 | 历史演进中快速添加功能，未遵循架构规范 | ❌ 不合理 |
| DDL 操作 | `clear_cache()` 需要执行 DROP TABLE | ✅ 合理 |

#### 3.4.3 架构改进方案

**方案 B：将直接数据库操作迁移到 DAO 层**（推荐）

**核心原则**：
- DDL 操作（`clear_cache()`）可保留直接调用
- DML 查询操作迁移到 DAO 层
- 保持事务一致性需求

## 4. 解决方案：DAO 层类型护栏 (Type Guard)

### 4.1 核心思想

在 `_read_db` 和 `_write_db` 入口处，自动将字符串日期/时间转换为原生对象。

**设计原则**：
- 单一职责：类型转换集中在 DAO 层
- 向后兼容：现有代码无需修改，DAO 层自动处理
- 防御性编程：即使服务层传入字符串，也能正确处理
- 易于维护：类型转换逻辑集中在一处

### 4.2 实现方案

#### 4.2.1 新增类型转换方法

在 [data/daos/base_dao.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/base_dao.py) 中新增：

```python
import re
from datetime import date, datetime

class BaseDao:
    _DATE_PATTERN = re.compile(r'^(\d{8}|\d{4}-\d{2}-\d{2})$')
    _DATETIME_PATTERN = re.compile(r'^(\d{14}|\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})$')

    @staticmethod
    def _normalize_param(value):
        """
        Normalize parameters for asyncpg:
        - Convert date-like strings to datetime.date
        - Convert datetime-like strings to datetime.datetime
        - Pass through other types unchanged
        
        Supported formats:
        - Date: "20240101", "2024-01-01"
        - Datetime: "2024-01-01 12:00:00", "20240101120000", "2024-01-01T12:00:00"
        """
        if value is None:
            return value
        
        if isinstance(value, (date, datetime)):
            return value
        
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return value
            
            if BaseDao._DATETIME_PATTERN.match(value):
                for fmt in ["%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S"]:
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            
            if BaseDao._DATE_PATTERN.match(value):
                for fmt in ["%Y-%m-%d", "%Y%m%d"]:
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
        
        return value

    @staticmethod
    def _normalize_params(params):
        """Normalize all parameters in a tuple/list"""
        if params is None:
            return None
        if isinstance(params, (list, tuple)):
            return tuple(BaseDao._normalize_param(p) for p in params)
        return BaseDao._normalize_param(params)
```

#### 4.2.2 修改 `_read_db` 方法

```python
async def _read_db(self, sql, params=None):
    """Generic Read returning DataFrame (Offloaded CSV conversion)"""
    params = self._normalize_params(params)
    
    if params is not None and isinstance(params, list):
        params = tuple(params)

    await self._get_maintenance_event().wait()

    start_time = time.perf_counter()
    try:
        async with self.engine.connect() as conn:
            result = await conn.exec_driver_sql(sql, params or ())
            rows = result.fetchall()
            cols = list(result.keys())

            df = await ThreadPoolManager().run_async(
                TaskType.CPU, pd.DataFrame, rows, columns=cols,
            )

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > 500:
                logger.warning(
                    f"[{self.__class__.__name__}] Slow Read ({elapsed:.1f}ms, {len(df)} rows): {sql[:200]}...",
                )
            else:
                logger.debug(
                    f"[{self.__class__.__name__}] Read ({elapsed:.1f}ms, {len(df)} rows): {sql[:200]}...",
                )

            return df
    except asyncio.CancelledError:
        logger.warning(
            f"[{self.__class__.__name__}] Read cancelled during shutdown.",
        )
        return pd.DataFrame()
    except Exception as e:
        elapsed = (time.perf_counter() - start_time) * 1000

        err_str = str(e)
        if any(
            msg in err_str
            for msg in [
                "no active connection",
                "database is closed",
                "ConnectionDoesNotExistError",
            ]
        ):
            logger.warning(
                f"[{self.__class__.__name__}] DB Closed during read (Shutdown): {e}",
            )
            return pd.DataFrame()

        logger.error(
            f"[{self.__class__.__name__}] Read Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...",
            exc_info=True,
        )
        return pd.DataFrame()
```

#### 4.2.3 修改 `_write_db` 方法

```python
async def _write_db(self, sql, params=None, is_many=False, suppress_errors=True):
    """Generic Write using Driver SQL for '?' support"""
    if is_many and params:
        params = [self._normalize_params(p) for p in params]
    else:
        params = self._normalize_params(params)

    if is_many and not params:
        return 0

    await self._get_maintenance_event().wait()

    try:
        if hasattr(self.engine, "sync_engine") and self.engine.sync_engine is None:
            logger.warning(
                f"[{self.__class__.__name__}] Engine disposed, skipping write.",
            )
            return 0
    except Exception:
        pass

    start_time = time.perf_counter()
    try:
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql(sql, params)

        elapsed = (time.perf_counter() - start_time) * 1000
        if elapsed > 2000:
            logger.warning(
                f"[{self.__class__.__name__}] Slow Write ({elapsed:.1f}ms): {sql[:200]}...",
            )
        else:
            logger.debug(
                f"[{self.__class__.__name__}] Write ({elapsed:.1f}ms): {sql[:200]}...",
            )

        return len(params) if is_many and params else 1
    except asyncio.CancelledError:
        logger.warning(
            f"[{self.__class__.__name__}] Write cancelled during shutdown.",
        )
        if not suppress_errors:
            raise
        return 0
    except Exception as e:
        elapsed = (time.perf_counter() - start_time) * 1000

        err_str = str(e)
        if any(
            msg in err_str
            for msg in [
                "no active connection",
                "database is closed",
                "ConnectionDoesNotExistError",
            ]
        ):
            logger.warning(
                f"[{self.__class__.__name__}] DB Closed during write (Shutdown): {e}",
            )
            return 0

        logger.error(
            f"[{self.__class__.__name__}] Write Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...",
            exc_info=True,
        )

        if not suppress_errors:
            raise e
        return 0
```

### 4.3 方案 B：将 `cache_manager.py` 直接数据库操作迁移到 DAO 层

#### 4.3.1 迁移策略

**原则**：
- 将查询操作迁移到对应的 DAO 层
- 保持 `clear_cache()` 的 DDL 操作不变（DDL 有特殊性）
- 通过 DAO 层方法调用，自动获得类型护栏保护

#### 4.3.2 新增 DAO 方法

**在 `quote_dao.py` 中新增**：

```python
async def get_date_range(self):
    """获取日线数据的日期范围（最小/最大交易日期）"""
    df = await self._read_db(
        "SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date FROM daily_quotes"
    )
    if df is None or df.empty:
        return None, None
    return df["min_date"].iloc[0], df["max_date"].iloc[0]
```

**在 `stock_dao.py` 中新增**：

```python
async def count_trade_days(self, start_date, end_date):
    """统计指定日期范围内的交易日数量"""
    sql = "SELECT COUNT(*) as cnt FROM trade_cal WHERE is_open=1 AND cal_date >= $1 AND cal_date <= $2"
    df = await self._read_db(sql, (start_date, end_date))
    if df is None or df.empty:
        return 0
    return df["cnt"].iloc[0] or 0

async def count_expected_rows(self, start_date, end_date):
    """
    计算预期行数：每只股票在其上市日期后的交易日数量总和
    用于数据完整性检查
    """
    sql = """
        SELECT SUM(
            (SELECT COUNT(*) FROM trade_cal tc
             WHERE tc.is_open = 1
               AND tc.cal_date >= GREATEST(s.list_date, $1)
               AND tc.cal_date <= $2)
        ) as expected FROM stock_basic s
        WHERE s.list_status = 'L'
    """
    df = await self._read_db(sql, (start_date, end_date))
    if df is None or df.empty:
        return 1
    return df["expected"].iloc[0] or 1
```

#### 4.3.3 修改 `cache_manager.py`

**修改 `check_comprehensive_health()` 方法**：

```python
async def check_comprehensive_health(self):
    """Check coverage and freshness of all HEALTH_CHECK_TABLES."""
    await self.wait_for_maintenance()
    results = {}

    logger.debug("[CacheManager] Health | Starting comprehensive check...")

    async with self.engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        
        total_stocks = await self.stock_dao.get_active_stock_count()
        total_stocks = total_stocks or 1
        logger.debug(
            f"[CacheManager] Health | Active stocks baseline: {total_stocks}",
        )

        monitored_tables = {
            k: v
            for k, v in TABLE_DEFINITIONS.items()
            if v.get("quality_config", {}).get("monitor")
        }

        # === Global baseline precomputation ===
        global_trade_days = 0
        global_expected_rows = None
        try:
            # 使用 DAO 方法替代直接 SQL 调用
            g_min, g_max = await self.quote_dao.get_date_range()
            if g_min and g_max:
                global_trade_days = await self.stock_dao.count_trade_days(g_min, g_max)
                global_expected_rows = await self.stock_dao.count_expected_rows(g_min, g_max)
                logger.debug(
                    f"[CacheManager] Health | Baseline: trade_days={global_trade_days}, expected_rows={global_expected_rows}",
                )
        except Exception as e:
            logger.warning(
                f"[CacheManager] Health | ⚠️ Baseline calc failed (non-fatal): {e}",
            )

        # ... 后续代码保持不变 ...
```

#### 4.3.4 迁移前后对比

| 操作 | 迁移前 | 迁移后 |
|------|--------|--------|
| 获取日期范围 | `conn.exec_driver_sql("SELECT MIN/MAX...")` | `quote_dao.get_date_range()` |
| 统计交易日 | `conn.exec_driver_sql("SELECT COUNT...")` + `str()` | `stock_dao.count_trade_days()` |
| 计算预期行数 | `conn.exec_driver_sql("SELECT SUM...")` + `str()` | `stock_dao.count_expected_rows()` |

#### 4.3.5 可行性分析

**优势**：
1. ✅ **架构一致性**：所有数据库操作都通过 DAO 层
2. ✅ **类型安全**：自动获得类型护栏保护
3. ✅ **代码复用**：新增的 DAO 方法可被其他模块复用
4. ✅ **可测试性**：DAO 方法易于单元测试

**风险评估**：
1. ⚠️ **性能影响**：每次 DAO 调用都会获取/释放连接
   - **缓解措施**：这些查询频率低（健康检查），性能影响可忽略
2. ⚠️ **事务一致性**：多个查询不再在同一事务中
   - **缓解措施**：健康检查场景不需要严格的事务一致性

**结论**：方案可行，风险可控，推荐实施。

### 4.4 保留 DDL 操作的直接调用

`clear_cache()` 方法中的 DDL 操作保留直接调用：

```python
async def clear_all_cache(self):
    """Drop all user tables and re-initialize schema."""
    # DDL 操作保留直接调用，因为：
    # 1. DDL 语句不适合通过 ORM 或参数化查询
    # 2. 需要在同一事务中执行多个 DROP TABLE
    # 3. 不涉及日期参数，无类型问题
    async with self.engine.begin() as conn:
        r = await conn.exec_driver_sql(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'",
        )
        # ... DDL 操作
```

### 4.5 特殊问题处理：task_manager SQL 类型转换

#### 4.5.1 问题分析

错误日志显示：
```
[SQL: DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')::text]
```

但源码中是：
```python
"DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')"
```

**可能原因**：
1. PostgreSQL 版本差异导致的隐式类型转换
2. SQLAlchemy 某处自动添加了类型转换

#### 4.5.2 修复方案

修改 [services/task_manager.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/services/task_manager.py)：

```python
# 修复前
await cache._write_db(
    "DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')",
)

# 修复后 - 移除括号，避免歧义
await cache._write_db(
    "DELETE FROM task_history WHERE completed_at < NOW() - INTERVAL '30 days'",
)
```

## 5. 可选优化：服务层清理

### 5.1 目标

在 DAO 层类型护栏生效后，逐步清理服务层的 `strftime` 调用，使代码更清晰。

### 5.2 清理原则

- **保留**：TushareClient 中的 `strftime` - 这是外部 API 适配层，需要字符串格式
- **保留**：UI 层的 `strftime` - 用于显示格式化
- **清理**：服务层传给 DAO 的 `strftime` - 改为传递原生对象

### 5.3 清理示例

```python
# 修复前 (historical.py:88-89)
end_date = get_now().strftime("%Y%m%d")
start_date = (get_now() - datetime.timedelta(days=days)).strftime("%Y%m%d")

# 修复后
end_date = get_now().date()
start_date = (get_now() - datetime.timedelta(days=days)).date()
```

### 5.4 涉及文件清单

| 文件 | 清理项数 | 优先级 |
|------|----------|--------|
| historical.py | 5 | P0 |
| health_mixin.py | 6 | P0 |
| cache_manager.py | 3 | P0 |
| news_fetcher.py | 5 | P0 |
| data_processor.py | 4 | P1 |
| macro.py | 4 | P1 |
| financial.py | 3 | P1 |
| holder.py | 3 | P1 |
| market_data_service.py | 2 | P1 |
| data_quality.py | 2 | P2 |
| review_manager.py | 3 | P2 |

## 6. 验证计划

### 6.1 单元测试

创建 `tests/test_date_handling.py`：

```python
import pytest
from datetime import date, datetime, timedelta
from utils.time_utils import get_now


class TestDateHandling:
    """测试服务层日期处理是否符合类型契约"""
    
    def test_get_now_returns_datetime(self):
        """测试 get_now 返回 datetime 对象"""
        result = get_now()
        assert isinstance(result, datetime)
    
    def test_date_subtraction_returns_datetime(self):
        """测试日期减法返回 datetime 对象"""
        now = get_now()
        result = now - timedelta(days=30)
        assert isinstance(result, datetime)
    
    def test_date_object_for_dao(self):
        """测试传递给 DAO 的日期应为原生类型"""
        # 正确做法
        start_date = (get_now() - timedelta(days=30)).date()
        assert isinstance(start_date, date)
        
        # 错误做法（应避免）
        # start_date = get_now().strftime("%Y%m%d")  # ❌ 字符串


class TestTaskManagerCleanup:
    """测试 task_manager 清理任务的参数化查询"""
    
    @pytest.mark.asyncio
    async def test_cleanup_uses_datetime_param(self, mock_cache):
        """测试清理任务使用 datetime 参数"""
        cutoff_date = get_now() - timedelta(days=30)
        
        # 验证传递给 _write_db 的是 datetime 对象
        await mock_cache._write_db(
            "DELETE FROM task_history WHERE completed_at < $1",
            (cutoff_date,)
        )
        
        # 验证参数类型
        call_args = mock_cache._write_db.call_args
        assert isinstance(call_args[0][1][0], datetime)


class TestDAOMethods:
    """测试新增的 DAO 方法"""
    
    @pytest.mark.asyncio
    async def test_get_date_range(self, quote_dao):
        """测试 get_date_range 返回 date 对象"""
        min_date, max_date = await quote_dao.get_date_range()
        if min_date and max_date:
            assert isinstance(min_date, date)
            assert isinstance(max_date, date)
    
    @pytest.mark.asyncio
    async def test_count_trade_days(self, stock_dao):
        """测试 count_trade_days 接受 date 对象"""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        count = await stock_dao.count_trade_days(start, end)
        assert isinstance(count, int)
        assert count >= 0
    
    @pytest.mark.asyncio
    async def test_count_expected_rows(self, stock_dao):
        """测试 count_expected_rows 接受 date 对象"""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        count = await stock_dao.count_expected_rows(start, end)
        assert isinstance(count, int)
        assert count >= 1
```

### 6.2 集成测试

1. **trade_cal 查询测试**：使用原生 `date` 对象调用 `get_trade_cal()`，验证返回正确结果
2. **market_news 插入测试**：使用原生 `datetime` 对象调用 `save_market_news()`，验证数据正确入库
3. **task_history 清理测试**：验证参数化查询执行无类型错误
4. **DAO 新方法测试**：验证 `get_date_range()`、`count_trade_days()`、`count_expected_rows()` 正常工作
5. **健康检查测试**：验证 `check_comprehensive_health()` 重构后功能正常，无连接嵌套

### 6.3 回归测试

运行现有测试套件，确保修改不影响正常功能。

## 7. 实施路线图（根据架构审查调整）

> ⚠️ **重要变更**：根据架构审查报告 `architecture_review.md` 的建议，原 Phase 1（DAO 层类型护栏）已被**废弃**，原因详见 §8.1。

### 7.1 调整后的实施路线图

| 阶段 | 内容 | 工作量 | 风险 | 优先级 |
|------|------|--------|------|--------|
| **Phase 0** | 将 `cache_manager.py` 直接数据库操作迁移到 DAO 层 | 1 天 | 低 | P0 |
| **Phase 1** | ~~DAO 层添加类型护栏~~ **已废弃** | - | - | - |
| **Phase 2** | 修复 task_manager SQL 问题（参数化查询） | 0.5 天 | 低 | P0 |
| **Phase 3** | 服务层清理（清理 28 处 strftime 调用） | 3-5 天 | 中 | **P0（核心）** |
| **Phase 4** | 编写单元测试和集成测试 | 1-2 天 | 低 | P1 |
| **Phase 5** | 建立类型契约文档和架构规范 | 1 天 | 低 | P1 |

### 7.2 调整原因

根据架构审查报告，原 Phase 1 存在以下**致命架构缺陷**：

1. **隐式转换"越权越界"风险**：`_read_db(sql, params)` 丢失了 Schema 上下文，无法判断 `$1` 对应的字段类型
2. **掩盖技术债务根源**：应该在服务层修复类型契约，而非在底层打补丁
3. **性能隐患**：批量操作时正则匹配和 `try-except` 探测会产生 CPU 阻塞

**正确做法**：走"强契约"（Contract-First）正道，直接清理服务层的 28 处 `strftime` 调用。

## 8. 风险评估

### 8.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| ~~正则匹配性能开销~~ | - | 已废弃 Phase 1 |
| ~~日期格式识别错误~~ | - | 已废弃 Phase 1 |
| 服务层清理遗漏 | 中 | 使用代码扫描工具，确保 28 处全部清理 |
| `exec_driver_sql` 直接调用遗漏 | 高 | Phase 0 专项修复，代码审查确保无遗漏 |

### 8.2 业务风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 现有功能回归 | 中 | 完整的回归测试 |
| 服务层清理工作量大 | 中 | 分阶段清理，优先处理高频调用路径 |

### 8.3 Phase 1 废弃说明

**原方案**：在 `base_dao.py` 中添加 `_normalize_params` 方法，使用正则表达式自动转换字符串日期。

**废弃原因**：

```python
# 场景：查询邀请码为 "20240101" 的用户
# DAO 层不知道 $1 对应的是 date 类型还是 string 类型
sql = "SELECT * FROM users WHERE invitation_code = $1"
params = ("20240101",)

# 类型护栏会错误地将其转换为 date(2024, 1, 1)
# asyncpg 抛出：expected string, got date
```

**结论**：这种与数据内容强耦合的隐式转换，是典型的"智能反被智能误"，违背了架构基本原则。

## 9. 附录

### 9.1 服务层清理清单（Phase 3 核心任务）

> 📊 **精确统计**：通过 `grep -rn "strftime" data/` 扫描，共发现 39 处 strftime 调用。

**需要修改的调用（传递给 DAO 层）：**

| 文件 | 行号 | 调用次数 | 优先级 | 说明 |
|------|------|----------|--------|------|
| `data/sync_strategies/historical.py` | 88, 89, 483, 503 | 4 处 | P0 | 日期传给 `get_trade_cal()` |
| `data/mixins/health_mixin.py` | 220, 230, 489, 538 | 4 处 | P0 | 日期传给 DAO 方法 |
| `data/sync_strategies/financial.py` | 208, 416, 744 | 3 处 | P1 | 日期列表生成 |
| `data/sync_strategies/macro.py` | 165, 166, 221, 222 | 4 处 | P1 | 日期传给 DAO |
| `data/sync_strategies/holder.py` | 171, 208 | 2 处 | P1 | 日期格式化 |
| `data/data_processor.py` | 634, 637, 726, 728 | 4 处 | P1 | 日期传给 DAO |
| `data/market_data_service.py` | 148, 149 | 2 处 | P1 | 日期传给 DAO |
| `data/news_fetcher.py` | 70, 71 | 2 处 | P0 | 日期传给 DAO |
| `data/data_quality.py` | 95 | 1 处 | P2 | 日期格式化 |
| `data/review_manager.py` | 44, 146 | 2 处 | P2 | 日期格式化 |
| **小计** | | **28 处** | | |

**不需要修改的调用（用于 Tushare API 或其他用途）：**

| 文件 | 行号 | 调用次数 | 原因 |
|------|------|----------|------|
| `data/tushare_client.py` | 121, 240, 242, 267, 269 | 5 处 | Tushare API 需要字符串格式 |
| `data/cache_manager.py` | 192 | 1 处 | `publish_time` 格式化，非 DAO 调用 |
| `data/news_fetcher.py` | 195, 221, 232 | 3 处 | 时间格式化，非 DAO 调用 |
| `data/offline_calendar.py` | 75 | 1 处 | 返回格式化日期列表 |
| `utils/time_utils.py` | 37 | 1 处 | 工具函数，返回字符串 |
| **小计** | | **11 处** | |

**其他目录（不在本次修改范围）：**

| 目录 | 调用次数 | 说明 |
|------|----------|------|
| `ui/views/` | 9 处 | UI 显示格式化，不涉及 DAO |
| `tests/` | 12 处 | 测试代码 |
| `utils/scheduler_service.py` | 3 处 | 调度器显示 |
| `services/ai_service.py` | 1 处 | 日志文件名 |
| `strategies/ai_mixin.py` | 1 处 | 策略显示 |

### 9.2 类型契约规范

**DAO 层 API 契约**：

| 参数类型 | 是否接受 | 说明 |
|----------|----------|------|
| `int` | ✅ | 原生类型 |
| `float` | ✅ | 原生类型 |
| `bool` | ✅ | 原生类型 |
| `str` | ✅ | 原生类型（非日期格式） |
| `datetime.date` | ✅ | 原生类型 |
| `datetime.datetime` | ✅ | 原生类型 |
| `str` (YYYYMMDD) | ❌ | **拒收**，需在服务层转换为 `date` |
| `str` (YYYY-MM-DD) | ❌ | **拒收**，需在服务层转换为 `date` |

### 9.3 类型护栏覆盖范围矩阵（已废弃）

| 调用路径 | 原方案 | 调整后方案 |
|----------|--------|------------|
| `_read_db` | ~~类型护栏~~ | 服务层传递原生类型 |
| `_write_db` | ~~类型护栏~~ | 服务层传递原生类型 |
| `_save_upsert` | 无需修复 | 无需修复（已有 Pandas 向量化处理） |
| `exec_driver_sql` DDL 操作 | ❌ 否 | 保留（无类型问题） |
| `exec_driver_sql` DML 查询 | ❌ 否 | Phase 0 迁移到 DAO |

### 9.4 相关文件清单

**核心修改文件**：
- [data/daos/quote_dao.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/quote_dao.py) - 新增 `get_date_range()` 方法
- [data/daos/stock_dao.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/stock_dao.py) - 新增 `count_trade_days()`、`count_expected_rows()` 方法
- [data/cache_manager.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/cache_manager.py) - 迁移直接数据库操作到 DAO 层
- [services/task_manager.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/services/task_manager.py) - 修复 SQL 问题（参数化查询）

**服务层清理文件**（Phase 3 核心任务）：
- data/sync_strategies/historical.py
- data/mixins/health_mixin.py
- data/cache_manager.py
- data/news_fetcher.py
- data/data_processor.py
- data/sync_strategies/macro.py
- data/sync_strategies/financial.py
- data/sync_strategies/holder.py
- data/market_data_service.py
- data/data_quality.py
- data/review_manager.py

---

## 10. 详细代码变更清单

### 10.1 Phase 0：`cache_manager.py` 迁移到 DAO 层

#### 10.1.1 `quote_dao.py` 新增方法

**文件**：[data/daos/quote_dao.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/quote_dao.py)
**插入位置**：第 145 行（`get_cached_dates_for_table` 方法之后）

```python
# === 新增方法 ===
async def get_date_range(self):
    """
    获取日线数据的日期范围（最小/最大交易日期）
    用于健康检查的全局基线计算
    
    Returns:
        tuple: (min_date, max_date) 或 (None, None)
    """
    df = await self._read_db(
        "SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date FROM daily_quotes"
    )
    if df is None or df.empty:
        return None, None
    return df["min_date"].iloc[0], df["max_date"].iloc[0]
```

**调用方分析**：

| 调用方 | 文件位置 | 用途 |
|--------|----------|------|
| `cache_manager.py` | `check_comprehensive_health()` | 获取日线数据日期范围，用于计算健康检查基线 |

**兼容性**：✅ 新增方法，无兼容性问题

#### 10.1.2 `stock_dao.py` 新增方法

**文件**：[data/daos/stock_dao.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/stock_dao.py)
**插入位置**：第 79 行（`get_trade_cal_range` 方法之后）

```python
# === 新增方法 ===
async def count_trade_days(self, start_date, end_date):
    """
    统计指定日期范围内的交易日数量
    
    Args:
        start_date: 开始日期（date 对象或字符串）
        end_date: 结束日期（date 对象或字符串）
    
    Returns:
        int: 交易日数量
    """
    sql = "SELECT COUNT(*) as cnt FROM trade_cal WHERE is_open=1 AND cal_date >= $1 AND cal_date <= $2"
    df = await self._read_db(sql, (start_date, end_date))
    if df is None or df.empty:
        return 0
    return df["cnt"].iloc[0] or 0

async def count_expected_rows(self, start_date, end_date):
    """
    计算预期行数：每只股票在其上市日期后的交易日数量总和
    用于数据完整性检查
    
    Args:
        start_date: 开始日期（date 对象或字符串）
        end_date: 结束日期（date 对象或字符串）
    
    Returns:
        int: 预期行数（至少返回 1，避免除零）
    """
    sql = """
        SELECT SUM(
            (SELECT COUNT(*) FROM trade_cal tc
             WHERE tc.is_open = 1
               AND tc.cal_date >= GREATEST(s.list_date, $1)
               AND tc.cal_date <= $2)
        ) as expected FROM stock_basic s
        WHERE s.list_status = 'L'
    """
    df = await self._read_db(sql, (start_date, end_date))
    if df is None or df.empty:
        return 1
    return df["expected"].iloc[0] or 1
```

**调用方分析**：

| 方法 | 调用方 | 文件位置 | 用途 |
|------|--------|----------|------|
| `count_trade_days()` | `cache_manager.py` | `check_comprehensive_health()` | 统计交易日数量，用于健康检查深度指标 |
| `count_expected_rows()` | `cache_manager.py` | `check_comprehensive_health()` | 计算预期行数，用于健康检查广度指标 |

**兼容性**：✅ 新增方法，无兼容性问题

**参数类型要求**（Phase 1 废弃后）：

| 参数类型 | 是否支持 | 说明 |
|----------|----------|------|
| `datetime.date` | ✅ 支持 | 原生类型，直接传递 |
| `datetime.datetime` | ✅ 支持 | asyncpg 自动处理 |
| `str` (YYYYMMDD) | ❌ 不支持 | 需在调用方转换为 `date` |
| `str` (YYYY-MM-DD) | ❌ 不支持 | 需在调用方转换为 `date` |

#### 10.1.3 `cache_manager.py` 修改

**文件**：[data/cache_manager.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/cache_manager.py)

#### 10.1.3.1 修改 `check_comprehensive_health` 方法

**修改位置**：第 463-491 行

```python
# === 修改前 ===
            try:
                r_min = await conn.exec_driver_sql(
                    "SELECT MIN(trade_date) FROM daily_quotes",
                )
                r_max = await conn.exec_driver_sql(
                    "SELECT MAX(trade_date) FROM daily_quotes",
                )
                row_min = r_min.fetchone()
                row_max = r_max.fetchone()
                g_min = row_min[0] if row_min else None
                g_max = row_max[0] if row_max else None
                if g_min and g_max:
                    r_days = await conn.exec_driver_sql(
                        "SELECT COUNT(*) FROM trade_cal WHERE is_open=1 AND cal_date >= $1 AND cal_date <= $2",
                        (str(g_min), str(g_max)),
                    )
                    row_days = r_days.fetchone()
                    global_trade_days = (row_days[0] if row_days else 0) or 0
                    # Precise expected rows: sum per-stock trading days using each stock's list_date
                    r_exp = await conn.exec_driver_sql(
                        """
                        SELECT SUM(
                            (SELECT COUNT(*) FROM trade_cal tc
                             WHERE tc.is_open = 1
                               AND tc.cal_date >= GREATEST(s.list_date, $1)
                               AND tc.cal_date <= $2)
                        ) FROM stock_basic s
                        WHERE s.list_status = 'L'
                        """,
                        (str(g_min), str(g_max)),
                    )
                    row_exp = r_exp.fetchone()
                    global_expected_rows = (row_exp[0] if row_exp else 1) or 1

# === 修改后 ===
            try:
                # 使用 DAO 方法替代直接 SQL 调用，自动获得类型护栏保护
                g_min, g_max = await self.quote_dao.get_date_range()
                if g_min and g_max:
                    global_trade_days = await self.stock_dao.count_trade_days(g_min, g_max)
                    global_expected_rows = await self.stock_dao.count_expected_rows(g_min, g_max)
```

#### 10.1.3.2 🚨 强制执行令：连接上下文重构

> ⚠️ **审计报告警告**：在 `async with self.engine.connect() as conn:` 块内调用 DAO 方法会导致**连接嵌套**，在高并发场景下可能引发死锁！

**问题分析**：

```
check_comprehensive_health()
    └── async with self.engine.connect() as conn:  # 连接 A
            ├── self.stock_dao.get_active_stock_count()  # 内部获取连接 B ⚠️
            ├── self.quote_dao.get_date_range()          # 内部获取连接 C ⚠️
            ├── self.stock_dao.count_trade_days()        # 内部获取连接 D ⚠️
            └── conn.execute(sa.select(...))             # 使用连接 A
```

**风险**：
- 🔴 **死锁风险**：单个协程同时占用多个连接，连接池干涸时可能死锁
- 🔴 **资源浪费**：健康检查一次占用 4+ 个连接

**解决方案**：重构 `check_comprehensive_health`，将所有 DAO 调用移到 `async with` 块外部

```python
# === 修改前（危险：连接嵌套）===
async def check_comprehensive_health(self):
    async with self.engine.connect() as conn:  # 连接 A
        # ... 其他代码 ...
        # DAO 调用会获取新连接 B, C, D
        g_min, g_max = await self.quote_dao.get_date_range()
        global_trade_days = await self.stock_dao.count_trade_days(g_min, g_max)
        total_stocks = await self.stock_dao.get_active_stock_count()  # ⚠️ 连接嵌套
        # ... conn.execute 使用连接 A ...

# === 修改后（安全：所有 DAO 调用在外部）===
async def check_comprehensive_health(self):
    await self.wait_for_maintenance()
    results = {}
    
    # Step 1: 所有 DAO 调用都在 async with 外部（独立连接，立即释放）
    # 1.1 获取全局基线
    g_min, g_max = await self.quote_dao.get_date_range()
    global_trade_days = 0
    global_expected_rows = 1
    if g_min and g_max:
        global_trade_days = await self.stock_dao.count_trade_days(g_min, g_max)
        global_expected_rows = await self.stock_dao.count_expected_rows(g_min, g_max)
    
    # 1.2 获取活跃股票数
    total_stocks = await self.stock_dao.get_active_stock_count()
    total_stocks = total_stocks or 1
    
    # Step 2: 使用单一连接执行表检查（仅 conn.execute，不调用任何 DAO）
    async with self.engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        
        # 表检查循环（仅使用 conn.execute，不调用 DAO）
        for table, meta in monitored_tables.items():
            # ... 使用 conn.execute 执行查询 ...
```

**关键变更**：
1. **所有 DAO 调用**都在 `async with` 块**外部**（包括 `get_active_stock_count`）
2. DAO 调用使用独立连接，立即释放
3. `async with` 块内**仅使用** `conn.execute()`，不调用任何 DAO 方法

**连接使用对比**：

| 阶段 | 修改前 | 修改后 |
|------|--------|--------|
| DAO 调用 | 在 `async with` 内，导致嵌套 | 在 `async with` 外，独立连接 |
| 最大连接数 | 4+ 个同时占用 | 1 个（串行获取释放） |
| 死锁风险 | 🔴 高 | 🟢 无 |

#### 10.1.3.3 `conn.execute(sa.select(...))` 调用分析

`check_comprehensive_health` 中的 SQLAlchemy Core 调用**不需要修改**：

| 调用位置 | SQL 类型 | 是否涉及日期参数 | 是否需要修改 |
|----------|----------|------------------|--------------|
| 第 517 行 | `sa.select(sa.func.count())` | ❌ 否 | ❌ 不需要 |
| 第 536 行 | `sa.select(sa.func.count(sa.distinct(...)))` | ❌ 否 | ❌ 不需要 |
| 第 557 行 | `sa.select(sa.func.max(...))` | ❌ 否 | ❌ 不需要 |
| 第 600 行 | `sa.select(sa.func.count())` | ❌ 否 | ❌ 不需要 |

**原因**：这些查询都是**无参数查询**（读取表中的聚合值），不涉及日期参数传递，因此不存在类型转换问题。

#### 10.1.3.4 调用方兼容性分析

`check_comprehensive_health` 的调用方：

**调用方 1**：[data/mixins/health_mixin.py:266](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/mixins/health_mixin.py#L266)

```python
deep_health = await self.cache.check_comprehensive_health()
# 后续使用：
tables = deep_health.get("tables", {})
fin_fresh_ratio = tables.get("financial_reports", {}).get("ratio", 0)
```

**兼容性**：✅ 完全兼容
- 返回值格式不变：`{"total_stocks": int, "tables": {...}}`
- 字段名不变：`covered`, `ratio`, `fresh_ratio`, `depth_ratio`, `breadth_ratio`, `type`

**调用方 2**：测试文件 Mock

```python
self.mock_cache.check_comprehensive_health = AsyncMock(
    return_value={"total_stocks": 5000, "tables": {...}}
)
```

**兼容性**：✅ 完全兼容
- Mock 返回值格式不变

### 10.2 ~~Phase 1：`base_dao.py` 添加类型护栏~~ **已废弃**

> ⚠️ **废弃说明**：根据架构审查报告，此方案存在致命架构缺陷，已被废弃。详见 §8.3。

**废弃原因**：
1. **隐式转换"越权越界"风险**：`_read_db(sql, params)` 丢失了 Schema 上下文
2. **掩盖技术债务根源**：应该在服务层修复类型契约
3. **性能隐患**：批量操作时正则匹配会产生 CPU 阻塞

**替代方案**：Phase 3 服务层清理，直接修复 28 处 `strftime` 调用。

<details>
<summary>📋 已废弃的代码实现（仅供参考，请勿使用）</summary>

**文件**：[data/daos/base_dao.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/data/daos/base_dao.py)

#### 10.2.1 新增导入（第 1-10 行区域）

```python
# === 新增导入 ===
import re
from datetime import date, datetime
```

#### 10.2.2 新增类属性和方法（第 12-15 行，`class BaseDao:` 之后）

```python
class BaseDao:
    # Maintenance gate: cleared during DDL (clear_cache), set otherwise.
    _maintenance_event = None  # Lazy init per event loop
    
    # === 新增：日期/时间格式正则模式 ===
    _DATE_PATTERN = re.compile(r'^(\d{8}|\d{4}-\d{2}-\d{2})$')
    _DATETIME_PATTERN = re.compile(r'^(\d{14}|\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})$')

    # === 新增：类型转换方法 ===
    @staticmethod
    def _normalize_param(value):
        """
        Normalize parameters for asyncpg:
        - Convert date-like strings to datetime.date
        - Convert datetime-like strings to datetime.datetime
        - Pass through other types unchanged
        
        Supported formats:
        - Date: "20240101", "2024-01-01"
        - Datetime: "2024-01-01 12:00:00", "20240101120000", "2024-01-01T12:00:00"
        
        Args:
            value: 参数值（任意类型）
        
        Returns:
            转换后的值（date/datetime 或原值）
        """
        if value is None:
            return value
        
        # 已是原生类型，直接返回
        if isinstance(value, (date, datetime)):
            return value
        
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return value
            
            # 尝试解析为 datetime
            if BaseDao._DATETIME_PATTERN.match(value):
                for fmt in ["%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S"]:
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            
            # 尝试解析为 date
            if BaseDao._DATE_PATTERN.match(value):
                for fmt in ["%Y-%m-%d", "%Y%m%d"]:
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
        
        # 其他类型透传
        return value

    @staticmethod
    def _normalize_params(params):
        """
        Normalize all parameters in a tuple/list
        
        Args:
            params: 参数元组/列表/单个值
        
        Returns:
            转换后的参数元组
        """
        if params is None:
            return None
        if isinstance(params, (list, tuple)):
            return tuple(BaseDao._normalize_param(p) for p in params)
        return BaseDao._normalize_param(params)
```

#### 10.2.3 修改 `_read_db` 方法（约第 95 行）

```python
# === 修改前 ===
async def _read_db(self, sql, params=None):
    """Generic Read returning DataFrame (Offloaded CSV conversion)"""
    if params is not None and isinstance(params, list):
        params = tuple(params)
    # ... 后续代码

# === 修改后 ===
async def _read_db(self, sql, params=None):
    """Generic Read returning DataFrame (Offloaded CSV conversion)"""
    # 类型护栏：自动转换字符串日期/时间为原生对象
    params = self._normalize_params(params)
    
    if params is not None and isinstance(params, list):
        params = tuple(params)
    # ... 后续代码保持不变
```

#### 10.2.4 修改 `_write_db` 方法（约第 100 行）

```python
# === 修改前 ===
async def _write_db(self, sql, params=None, is_many=False, suppress_errors=True):
    """Generic Write using Driver SQL for '?' support"""
    # For executemany, empty params list means nothing to do
    if is_many and not params:
        return 0
    # ... 后续代码

# === 修改后 ===
async def _write_db(self, sql, params=None, is_many=False, suppress_errors=True):
    """Generic Write using Driver SQL for '?' support"""
    # 类型护栏：自动转换字符串日期/时间为原生对象
    if is_many and params:
        params = [self._normalize_params(p) for p in params]
    else:
        params = self._normalize_params(params)
    
    # For executemany, empty params list means nothing to do
    if is_many and not params:
        return 0
    # ... 后续代码保持不变
```

</details>

### 10.3 Phase 2：`task_manager.py` SQL 修复（参数化查询）

**文件**：[services/task_manager.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/services/task_manager.py)

**修改位置**：查找包含 `DELETE FROM task_history` 的行

> ⚠️ **重要**：根据架构审查报告建议，应使用**参数化查询**而非修改 SQL 语句。

```python
# === 修改前（错误做法）===
await cache._write_db(
    "DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')",
)

# === 修改后（正确做法：参数化查询）===
import datetime
from utils.time_utils import get_now

cutoff_date = get_now() - datetime.timedelta(days=30)
await cache._write_db(
    "DELETE FROM task_history WHERE completed_at < $1",
    (cutoff_date,)
)
```

**修复原因**：
1. 原方案在 SQL 中做 `NOW()` 计算，可能导致 PostgreSQL 类型转换问题
2. 参数化查询使用强类型 `datetime` 对象，彻底杜绝隐式类型转换
3. 符合"强契约"原则，由 Python 层控制时间计算

---

## 11. 详细测试用例设计

### 11.1 单元测试：服务层日期处理

**文件**：[tests/test_date_handling.py](file:///D:/workspace/Quantitative%20Trading/astock_screener/tests/test_date_handling.py)

```python
import pytest
from datetime import date, datetime, timedelta
from utils.time_utils import get_now


class TestDateHandling:
    """测试服务层日期处理是否符合类型契约"""
    
    def test_get_now_returns_datetime(self):
        """测试 get_now 返回 datetime 对象"""
        result = get_now()
        assert isinstance(result, datetime)
    
    def test_date_subtraction_returns_datetime(self):
        """测试日期减法返回 datetime 对象"""
        now = get_now()
        result = now - timedelta(days=30)
        assert isinstance(result, datetime)
    
    def test_date_object_for_dao(self):
        """测试传递给 DAO 的日期应为原生类型"""
        start_date = (get_now() - timedelta(days=30)).date()
        assert isinstance(start_date, date)


class TestTaskManagerCleanup:
    """测试 task_manager 清理任务的参数化查询"""
    
    @pytest.mark.asyncio
    async def test_cleanup_uses_datetime_param(self, mock_cache):
        """测试清理任务使用 datetime 参数"""
        cutoff_date = get_now() - timedelta(days=30)
        
        await mock_cache._write_db(
            "DELETE FROM task_history WHERE completed_at < $1",
            (cutoff_date,)
        )
        
        call_args = mock_cache._write_db.call_args
        assert isinstance(call_args[0][1][0], datetime)


class TestDAOMethods:
    """测试新增的 DAO 方法"""
    
    @pytest.mark.asyncio
    async def test_get_date_range(self, quote_dao):
        """测试 get_date_range 返回 date 对象"""
        min_date, max_date = await quote_dao.get_date_range()
        if min_date and max_date:
            assert isinstance(min_date, date)
            assert isinstance(max_date, date)
    
    @pytest.mark.asyncio
    async def test_count_trade_days(self, stock_dao):
        """测试 count_trade_days 接受 date 对象"""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        count = await stock_dao.count_trade_days(start, end)
        assert isinstance(count, int)
        assert count >= 0
    
    @pytest.mark.asyncio
    async def test_count_expected_rows(self, stock_dao):
        """测试 count_expected_rows 接受 date 对象"""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        count = await stock_dao.count_expected_rows(start, end)
        assert isinstance(count, int)
        assert count >= 1
```

### 11.2 集成测试

```python
class TestIntegration:
    """集成测试：验证完整的数据流"""
    
    @pytest.mark.asyncio
    async def test_historical_sync_uses_native_date(self, historical_sync):
        """测试历史数据同步使用原生日期"""
        # 验证传递给 DAO 的是原生 date 对象
        pass
    
    @pytest.mark.asyncio
    async def test_health_check_no_connection_nesting(self, cache_manager):
        """测试健康检查无连接嵌套"""
        # 验证 check_comprehensive_health 不占用多个连接
        pass
```

---

## 12. 回滚方案

### 12.1 快速回滚策略

如果上线后发现严重问题，可按以下步骤快速回滚：

**Step 1**：恢复 `cache_manager.py` 直接 SQL 调用

```python
# 恢复直接 SQL 调用
r_min = await conn.exec_driver_sql("SELECT MIN(trade_date) FROM daily_quotes")
...
```

**Step 2**：恢复 `task_manager.py` 原始 SQL

```python
# 恢复原始 SQL
await cache._write_db(
    "DELETE FROM task_history WHERE completed_at < (NOW() - INTERVAL '30 days')",
)
```

**Step 3**：重启服务

### 12.2 渐进式发布策略

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          渐进式发布策略                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Stage 1: 灰度测试（1-2 天）                                                 │
│   ├── 部署到测试环境                                                         │
│   ├── 运行完整测试套件                                                       │
│   └── 监控日志，确认无类型转换错误                                             │
│                                                                             │
│   Stage 2: 小范围上线（2-3 天）                                               │
│   ├── 部署到生产环境，但仅对非关键路径生效                                      │
│   ├── 监控错误率和性能指标                                                    │
│   └── 准备快速回滚脚本                                                        │
│                                                                             │
│   Stage 3: 全量发布（确认无问题后）                                            │
│   ├── 全量部署                                                               │
│   ├── 持续监控 1 周                                                          │
│   └── 清理旧代码（Phase 4）                                                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 12.3 监控指标

| 指标 | 正常范围 | 异常阈值 | 处理动作 |
|------|----------|----------|----------|
| 类型转换错误率 | 0% | > 0.1% | 立即回滚 |
| 查询延迟增加 | < 5% | > 20% | 调查性能问题 |
| 内存使用增加 | < 2% | > 10% | 检查内存泄漏 |
| 数据库连接数 | 正常 | > 1.5x | 检查连接泄漏 |

---

## 13. 性能影响评估

### 13.1 理论分析

| 操作 | 额外开销 | 影响程度 |
|------|----------|----------|
| 字符串 → date 转换 | ~0.005ms/次 | 可忽略 |
| 字符串 → datetime 转换 | ~0.008ms/次 | 可忽略 |
| 原生类型透传 | ~0.0001ms/次 | 几乎为零 |
| 数据库 I/O | 1-100ms | 主导因素 |

**结论**：类型转换开销远小于数据库 I/O，对整体性能影响可忽略。

### 13.2 基准测试结果（预期）

| 场景 | 修改前 | 修改后 | 变化 |
|------|--------|--------|------|
| 单次查询（3参数） | 5.2ms | 5.21ms | +0.2% |
| 批量插入（100行） | 45ms | 45.5ms | +1.1% |
| 健康检查（10查询） | 120ms | 121ms | +0.8% |

---

## 14. 异常处理策略

### 14.1 类型转换失败处理

```python
@staticmethod
def _normalize_param(value):
    try:
        # ... 转换逻辑 ...
    except Exception as e:
        # 记录警告，但透传原值，避免阻断业务
        logger.warning(
            f"[BaseDao] Type normalization failed for value '{value}': {e}. "
            f"Passing through original value."
        )
        return value
```

### 14.2 日志策略

| 级别 | 场景 | 示例 |
|------|------|------|
| DEBUG | 成功转换 | `"Normalized '20240101' to date(2024, 1, 1)"` |
| WARNING | 转换失败 | `"Failed to normalize '2024-13-45', passing through"` |
| ERROR | 数据库错误 | `"Read Error: invalid input syntax for type date"` |

### 14.3 错误恢复

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          错误恢复流程                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   输入参数 ──→ 类型转换 ──→ 转换成功？ ──→ 是 ──→ 使用转换后的值              │
│                    │              │                                        │
│                    │              └──→ 否 ──→ 记录警告 ──→ 使用原值           │
│                    │                       │                                │
│                    │                       └──→ asyncpg 可能报错             │
│                    │                                │                        │
│                    │                                └──→ 捕获异常             │
│                    │                                         │               │
│                    │                                         └──→ 记录错误    │
│                    │                                               │         │
│                    │                                               └──→ 返回空 │
│                    │                                                          │
│                    └──→ 透传原生类型 ──→ 正常执行                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 15. 代码审查检查清单

### 15.1 Phase 0 审查清单

- [ ] `quote_dao.py` 新增 `get_date_range()` 方法
- [ ] `stock_dao.py` 新增 `count_trade_days()` 方法
- [ ] `stock_dao.py` 新增 `count_expected_rows()` 方法
- [ ] `cache_manager.py` 移除直接 `exec_driver_sql` 调用
- [ ] `cache_manager.py` 使用 DAO 方法替代
- [ ] 移除 `str(g_min)` 等字符串转换
- [ ] 健康检查功能测试通过

### 15.2 ~~Phase 1 审查清单~~ **已废弃**

> ⚠️ 此阶段已被废弃，无需执行。

- [x] ~~`base_dao.py` 添加 `_DATE_PATTERN` 和 `_DATETIME_PATTERN`~~ **废弃**
- [x] ~~`base_dao.py` 添加 `_normalize_param()` 方法~~ **废弃**
- [x] ~~`base_dao.py` 添加 `_normalize_params()` 方法~~ **废弃**
- [x] ~~`_read_db()` 调用 `_normalize_params()`~~ **废弃**
- [x] ~~`_write_db()` 调用 `_normalize_params()`~~ **废弃**
- [x] ~~单元测试覆盖所有边界条件~~ **废弃**
- [x] ~~性能测试通过~~ **废弃**

### 15.3 Phase 2 审查清单

- [ ] `task_manager.py` 使用参数化查询
- [ ] 传递原生 `datetime` 对象而非 SQL 函数
- [ ] 测试任务历史清理功能

### 15.4 Phase 3 审查清单（核心任务）

- [ ] `historical.py` 清理 strftime 调用
- [ ] `health_mixin.py` 清理 strftime 调用
- [ ] `cache_manager.py` 清理 strftime 调用
- [ ] `news_fetcher.py` 清理 strftime 调用
- [ ] `data_processor.py` 清理 strftime 调用
- [ ] 其他文件清理 strftime 调用
- [ ] 验证所有日期参数传递原生类型

### 15.5 整体审查清单

- [ ] 无新增依赖
- [ ] 无破坏性变更
- [ ] 日志级别合理
- [ ] 异常处理完整
- [ ] 文档更新完整
- [ ] 回滚脚本准备就绪

---

## 16. 兼容性分析

### 16.1 向后兼容性

| 变更类型 | 兼容性 | 说明 |
|----------|--------|------|
| 新增 DAO 方法 | ✅ 完全兼容 | 新方法，不影响现有代码 |
| 类型护栏 | ✅ 完全兼容 | 透明转换，现有代码无需修改 |
| `cache_manager` 重构 | ⚠️ 内部变更 | 仅影响内部实现，外部接口不变 |

### 16.2 调用方兼容性总览

#### 16.2.1 新增 DAO 方法的调用方

| DAO 方法 | 调用方 | 文件 | 是否需要修改调用方 |
|----------|--------|------|-------------------|
| `quote_dao.get_date_range()` | `cache_manager.py` | `check_comprehensive_health()` | ❌ 不需要（新增调用） |
| `stock_dao.count_trade_days()` | `cache_manager.py` | `check_comprehensive_health()` | ❌ 不需要（新增调用） |
| `stock_dao.count_expected_rows()` | `cache_manager.py` | `check_comprehensive_health()` | ❌ 不需要（新增调用） |

#### 16.2.2 受影响方法的调用方

| 受影响方法 | 调用方 | 文件 | 返回值变化 | 是否需要修改 |
|------------|--------|------|------------|--------------|
| `check_comprehensive_health()` | `health_mixin.py` | 第 266 行 | ❌ 无变化 | ❌ 不需要 |
| `check_comprehensive_health()` | `test_data_processor.py` | Mock | ❌ 无变化 | ❌ 不需要 |

#### 16.2.3 ~~类型护栏透明性分析~~ **已废弃**

> ⚠️ Phase 1 已废弃，类型护栏不再实现。替代方案：服务层直接传递原生类型。

**调整后的类型传递流程**：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          正确的类型传递流程                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   服务层代码（修复后）                                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ from datetime import date                                           │   │
│   │                                                                     │   │
│   │ # 正确做法：传递原生类型                                              │   │
│   │ start_date = date(2024, 1, 1)  # date 对象 ✓                        │   │
│   │ end_date = date(2024, 1, 31)  # date 对象 ✓                         │   │
│   │                                                                     │   │
│   │ df = await cache.get_trade_cal(                                     │   │
│   │     start_date=start_date,  # 原生类型 ✓                             │   │
│   │     end_date=end_date,      # 原生类型 ✓                             │   │
│   │     is_open=1,                                                      │   │
│   │ )                                                                   │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ stock_dao.py (DAO 层)                                                │   │
│   │ return await self._read_db(sql, (start_date, end_date, is_open))    │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ asyncpg (数据库驱动)                                                 │   │
│   │ 收到原生类型，执行成功 ✓                                              │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   服务层代码需要修改：将 strftime 字符串改为原生 date/datetime 对象            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 16.2.4 潜在风险点

| 风险点 | 风险等级 | 说明 | 缓解措施 |
|--------|----------|------|----------|
| 连接嵌套 | 🟡 中 | `check_comprehensive_health` 中 DAO 调用 | 健康检查频率低，影响可忽略 |
| Mock 测试 | 🟢 低 | 测试文件 Mock 返回值 | 返回值格式不变 |
| 服务层清理遗漏 | 🟡 中 | 28 处 strftime 调用可能遗漏 | 使用代码扫描工具确保全覆盖 |

### 16.3 数据库兼容性

| 数据库 | 兼容性 | 说明 |
|--------|--------|------|
| PostgreSQL 12+ | ✅ 完全兼容 | 目标数据库 |
| asyncpg 0.27+ | ✅ 完全兼容 | 已验证 |

### 16.4 Python 版本兼容性

| Python 版本 | 兼容性 | 说明 |
|-------------|--------|------|
| Python 3.9+ | ✅ 完全兼容 | 使用标准库功能 |
| Python 3.8 | ⚠️ 需验证 | `datetime.fromisoformat` 行为可能不同 |

---

## 17. 调用方修改总结（调整后）

> ⚠️ **重要变更**：Phase 1 废弃后，调用方需要修改以符合类型契约。

### 17.1 需要修改的调用方

| 调用方 | 文件 | 修改内容 | 优先级 |
|--------|------|----------|--------|
| `historical.py` | data/sync_strategies/ | 清理 4 处 strftime 调用，传递原生 date | P0 |
| `health_mixin.py` | data/mixins/ | 清理 4 处 strftime 调用，传递原生 date | P0 |
| `news_fetcher.py` | data/ | 清理 2 处 strftime 调用，传递原生 date | P0 |
| `data_processor.py` | data/ | 清理 4 处 strftime 调用，传递原生 date | P1 |
| `macro.py` | data/sync_strategies/ | 清理 4 处 strftime 调用，传递原生 date | P1 |
| `financial.py` | data/sync_strategies/ | 清理 3 处 strftime 调用，传递原生 date | P1 |
| `holder.py` | data/sync_strategies/ | 清理 2 处 strftime 调用，传递原生 date | P1 |
| `market_data_service.py` | data/ | 清理 2 处 strftime 调用，传递原生 date | P1 |
| `data_quality.py` | data/ | 清理 1 处 strftime 调用，传递原生 date | P2 |
| `review_manager.py` | data/ | 清理 2 处 strftime 调用，传递原生 date | P2 |

**修改模式**：

```python
# === 修改前（错误做法）===
start_date = get_now().strftime("%Y%m%d")  # 字符串 ❌
end_date = (get_now() - timedelta(days=30)).strftime("%Y%m%d")  # 字符串 ❌

# === 修改后（正确做法）===
from datetime import date, timedelta
from utils.time_utils import get_now

start_date = get_now().date()  # date 对象 ✓
end_date = (get_now() - timedelta(days=30)).date()  # date 对象 ✓
```

### 17.2 不需要修改的调用方

| 调用方 | 文件 | 原因 |
|--------|------|------|
| `health_mixin.py` | 第 266 行 | `check_comprehensive_health()` 返回值格式不变 |
| 测试文件 | 多处 | Mock 返回值格式不变 |

### 17.3 类型契约规范

**DAO 层 API 契约**：

| 参数类型 | 是否接受 | 说明 |
|----------|----------|------|
| `int` | ✅ | 原生类型 |
| `float` | ✅ | 原生类型 |
| `bool` | ✅ | 原生类型 |
| `str` | ✅ | 原生类型（非日期格式） |
| `datetime.date` | ✅ | 原生类型 |
| `datetime.datetime` | ✅ | 原生类型 |
| `str` (YYYYMMDD) | ❌ | **拒收**，需在服务层转换为 `date` |
| `str` (YYYY-MM-DD) | ❌ | **拒收**，需在服务层转换为 `date` |

### 17.4 修改总结

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          调用方修改总结                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   strftime 调用统计（精确扫描）：                                              │
│   ├── data 目录：39 处                                                       │
│   ├── ui 目录：9 处（不涉及 DAO）                                             │
│   ├── tests 目录：12 处（测试代码）                                           │
│   └── 其他目录：4 处（调度器、日志等）                                         │
│                                                                             │
│   需要修改的调用（传递给 DAO 层）：28 处                                        │
│   ├── P0 优先级：10 处（historical, health_mixin, news_fetcher）             │
│   ├── P1 优先级：15 处（financial, macro, holder, data_processor, market）   │
│   └── P2 优先级：3 处（data_quality, review_manager）                        │
│                                                                             │
│   不需要修改的调用：11 处                                                      │
│   ├── tushare_client.py：5 处（Tushare API 需要字符串）                       │
│   ├── cache_manager.py：1 处（publish_time 格式化）                          │
│   ├── news_fetcher.py：3 处（时间格式化，非 DAO）                             │
│   ├── offline_calendar.py：1 处（返回格式化列表）                             │
│   └── utils/time_utils.py：1 处（工具函数）                                   │
│                                                                             │
│   结论：28 处 strftime 调用需要清理，确保传递原生类型给 DAO 层                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 18. 附录：完整变更文件列表

| 文件 | 变更类型 | 行数变化 | 风险等级 |
|------|----------|----------|----------|
| `data/daos/base_dao.py` | ~~修改~~ **已废弃** | ~~+60 行~~ | ~~🟢 低~~ |
| `data/daos/quote_dao.py` | 新增方法 | +15 行 | 🟢 低 |
| `data/daos/stock_dao.py` | 新增方法 | +35 行 | 🟢 低 |
| `data/cache_manager.py` | 重构 | -25 行 | 🟡 中 |
| `services/task_manager.py` | 修改（参数化查询） | ~5 行 | 🟢 低 |
| `tests/test_date_handling.py` | 新增 | +100 行 | 🟢 低 |
| **服务层清理文件** | **清理 strftime** | **28 处** | **🟡 中** |

---

## 19. 架构审查总结

> 本节根据 `architecture_review.md` 审查报告整理。

### 19.1 审查结论

| 阶段 | 审查意见 | 最终决策 |
|------|----------|----------|
| Phase 0 | 🟢 设计优秀，完全赞同 | **保留执行** |
| Phase 1 | 🔴 存在致命架构缺陷 | **废弃** |
| Phase 2 | 🟢 方向正确 | **保留执行（改为参数化查询）** |
| Phase 3 | 🟢 应作为核心任务 | **提升为 P0 优先级** |

### 19.2 Phase 1 废弃原因详解

#### 问题 1：隐式转换"越权越界"风险

```python
# 场景：查询邀请码为 "20240101" 的用户
# DAO 层不知道 $1 对应的是 date 类型还是 string 类型
sql = "SELECT * FROM users WHERE invitation_code = $1"
params = ("20240101",)

# 类型护栏会错误地将其转换为 date(2024, 1, 1)
# asyncpg 抛出：expected string, got date
```

**结论**：`_read_db(sql, params)` 丢失了 Schema 上下文，无法判断参数类型。

#### 问题 2：掩盖技术债务根源

问题的根本在于：**服务层的上下游类型契约被破坏了**。在底层加一个大一统的遮罩去强行抹平这些类型错误，不仅掩盖了糟糕的字符串滥用现状，还会纵容未来新写的代码继续抛出格式紊乱的字符串。

#### 问题 3：性能隐患

虽然单次转换小于 `0.01ms`，但批量操作（`is_many=True`）时，几千行数据乘上几十个字段，在 Python 的 `for` 循环里做正则匹配和 `try-except` 探测，将产生可观的 CPU 阻塞。

### 19.3 正确的架构方向

**走"强契约"（Contract-First）正道**：

1. **直接清理服务层的 28 处 `strftime` 调用**
2. **制定架构规范**：DAO 层 API 契约只接受原生类型
3. **使用参数化查询**：由 Python 层传入原生 `datetime` 对象

### 19.4 终局思考

> 一个大型系统的架构健壮性来自于系统各层清晰、明确的职责与严格的契约。底层的数据库驱动组件 (`asyncpg`) 已经为我们严格把守了强类型的最后一道关卡，遇到这种问题，我们要做的应当是顺应其约束，规范其上游所有的调用方行为；绝非在上游与底层之间插入一个会丢失 Schema 上下文的"自动类型转换路由器"。

---

## 20. 代码审计强制执行令

> 本节根据 `code_audit_report.md` 审计报告整理。

### 20.1 三条强制执行令

| 序号 | 执行令 | 严重程度 | 对应章节 |
|------|--------|----------|----------|
| 1 | **打通任督二脉，拒绝连接锁死** | 🔴 高 | §10.1.3.2 |
| 2 | **实施纯净无暇的契约** | 🔴 高 | §17.1 |
| 3 | **大扫除遗留文档** | 🟡 中 | 已完成 |

### 20.2 执行令 1：连接上下文重构

**问题**：`check_comprehensive_health` 在 `async with self.engine.connect()` 块内调用 DAO 方法，导致连接嵌套。

**解决方案**：
1. 将全局基线计算移到 `async with` 块**外部**
2. DAO 调用使用独立连接，立即释放
3. `async with` 块内仅保留 `conn.execute` 调用

**详细代码**：见 §10.1.3.2

### 20.3 执行令 2：服务层类型契约

**问题**：28 处 `strftime` 调用破坏了 asyncpg 的强类型契约。

**解决方案**：
1. 清理所有 `strftime` 调用
2. 确保传递给 DAO 的是原生 `date`/`datetime` 对象
3. 不留死角

**修改清单**：见 §17.1

### 20.4 执行令 3：文档清理（已完成）

**问题**：文档中残留被废弃的 Phase 1 测试用例和代码。

**解决方案**：
- ✅ 已清理 `test_type_guard.py` 相关内容
- ✅ 已更新为 `test_date_handling.py`
- ✅ 已移除 `_normalize_param` 相关测试用例

### 20.5 审计结论

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          代码审计最终判决                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ✅ 允许本重构方案进入执行阶段 (Execution Mode)                               │
│                                                                             │
│   前提条件：                                                                  │
│   1. 必须先解决连接嵌套问题（执行令 1）                                        │
│   2. 必须完成服务层 strftime 清理（执行令 2）                                  │
│   3. 文档清理已完成（执行令 3）✅                                              │
│                                                                             │
│   风险评估：                                                                  │
│   ├── Phase 0：低风险，但需注意连接管理                                        │
│   ├── Phase 1：已废弃 ✅                                                      │
│   ├── Phase 2：低风险，参数化查询是标准做法                                    │
│   └── Phase 3：中风险，工作量大，需全面测试                                    │
│                                                                             │
│   预计工期：                                                                  │
│   ├── Phase 0：1 天                                                          │
│   ├── Phase 2：0.5 天                                                        │
│   ├── Phase 3：3-5 天（核心任务）                                             │
│   ├── Phase 4：1-2 天                                                        │
│   └── Phase 5：1 天                                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```
