# 架构设计原则

> 本文档总结了项目的核心架构设计原则，所有代码修改、方案设计必须遵循此文档。
> 
> 最后更新：2026-03-19

---

## 一、分层架构

### 1.1 架构层次

```
┌─────────────────────────────────────────────────────────────┐
│  UI 层 (ui/)                                                 │
│  - 用户界面组件                                               │
│  - 事件处理                                                  │
│  - 调用服务层                                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  服务层 (services/)                                          │
│  - 业务逻辑处理                                               │
│  - 任务调度                                                  │
│  - 调用 DAO 层                                               │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  DAO 层 (data/daos/)                                         │
│  - 数据访问抽象                                               │
│  - SQL 执行                                                  │
│  - 类型转换                                                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  数据源层 (data/tushare_client.py)                           │
│  - 外部 API 调用                                              │
│  - 字段映射                                                  │
│  - 数据获取                                                  │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 层次职责

| 层次 | 职责 | 禁止行为 |
|------|------|----------|
| UI 层 | 用户交互、事件处理、调用服务层 | 直接访问 DAO、直接操作数据库 |
| 服务层 | 业务逻辑、任务调度、调用 DAO 层 | 直接执行 SQL、手动格式化日期 |
| DAO 层 | 数据访问、SQL 执行、类型转换 | 调用外部 API、处理业务逻辑 |
| 数据源层 | API 调用、字段映射、数据获取 | 处理业务逻辑、直接写入数据库 |

---

## 二、字段映射原则

### 2.1 统一映射机制

**原则**：所有外部 API 字段映射必须在 `TushareClient._COLUMN_RENAMES` 中统一处理。

**正确示例**：
```python
# tushare_client.py
_COLUMN_RENAMES = {
    "cn_cpi": {"month": "period", "nt_val": "cpi"},
    "cn_ppi": {"month": "period", "ppi_yoy": "ppi"},
    "cn_m": {"month": "period"},
}
```

**错误示例**：
```python
# macro.py - 业务层不应手动重命名
df = df.rename(columns={"month": "period"})  # ❌ 违反单一职责原则
```

### 2.2 映射优先级

1. 数据源层统一映射（优先）
2. DAO 层类型转换（次要）
3. 服务层业务处理（最后）

---

## 三、时区处理原则

### 3.1 时区策略

| 场景 | 时区状态 | 处理方式 |
|------|----------|----------|
| 内存中的 datetime | **时区感知** (CST) | 使用 `get_now()` 获取 |
| 写入数据库 | **时区无关** (naive) | `replace(tzinfo=None)` |
| 从数据库读取 | **时区感知** (CST) | `replace(tzinfo=CST_TZ)` |

### 3.2 标准模式

```python
# 创建：使用 get_now() 获取时区感知 datetime
from utils.time_utils import get_now, CST_TZ
created_at = get_now()  # 时区感知

# 写入：移除时区信息
params = (..., created_at.replace(tzinfo=None), ...)

# 读取：恢复时区信息
dt = datetime.datetime.fromisoformat(str(val))
if dt.tzinfo is None:
    dt = dt.replace(tzinfo=CST_TZ)
```

### 3.3 禁止行为

```python
# ❌ 禁止：混合使用时区感知和时区无关的 datetime
sorted(tasks, key=lambda t: t.created_at)  # 可能导致 TypeError

# ❌ 禁止：直接使用 datetime.now()
datetime.datetime.now()  # 无时区信息

# ✅ 正确：使用 get_now()
get_now()  # 返回时区感知 datetime
```

---

## 四、日期类型传递原则

### 4.1 类型传递规范

| 层次 | 传递类型 | 说明 |
|------|----------|------|
| 服务层 → DAO 层 | `datetime.date` / `datetime.datetime` | 原生 Python 对象 |
| DAO 层 → 数据库 | 自动转换 | asyncpg 支持原生类型 |
| 数据源层 → 服务层 | `datetime.date` / `datetime.datetime` | 原生 Python 对象 |

### 4.2 禁止行为

```python
# ❌ 禁止：服务层手动格式化日期字符串
date_str = date.strftime("%Y%m%d")  # 违反职责分离
await dao.save_data(date_str)

# ❌ 禁止：使用 strftime 传递给 DAO
await dao.save_data(start_date.strftime("%Y-%m-%d"))

# ✅ 正确：传递原生日期对象
await dao.save_data(start_date)  # date 对象
```

### 4.3 数据源层格式化

```python
# tushare_client.py - 数据源层负责格式化
if isinstance(v, (datetime.date, datetime.datetime)):
    formatted_kwargs[k] = v.strftime("%Y%m%d")  # ✅ 正确位置
```

---

## 五、数据库操作原则

### 5.1 访问控制

**原则**：所有数据库操作必须通过 DAO 层，禁止绕过 DAO 直接访问数据库。

**正确示例**：
```python
# 服务层调用 DAO
from data.daos import StockDao
dao = StockDao(engine)
await dao.save_stock_basic(df)
```

**错误示例**：
```python
# ❌ 禁止：服务层直接执行 SQL
await conn.execute("INSERT INTO stock_basic ...")
```

### 5.2 参数化查询

**原则**：所有 SQL 查询必须使用参数化，禁止字符串拼接。

**正确示例**：
```python
# 使用参数化查询
sql = "SELECT * FROM stock_basic WHERE ts_code = $1"
await conn.exec_driver_sql(sql, (ts_code,))
```

**错误示例**：
```python
# ❌ 禁止：字符串拼接
sql = f"SELECT * FROM stock_basic WHERE ts_code = '{ts_code}'"
```

### 5.3 UPSERT 模式

```python
# 使用 _save_upsert 处理重复数据
await self._save_upsert(
    df, "table_name", columns, pk_columns=["id"],
)
```

---

## 六、异步处理原则

### 6.1 线程池使用

| 任务类型 | 线程池 | 说明 |
|----------|--------|------|
| CPU 密集型 | `TaskType.CPU` | pandas 操作、数据转换 |
| IO 密集型 | `TaskType.IO` | HTTP 请求、文件操作 |

```python
from utils.thread_pool import ThreadPoolManager, TaskType

# CPU 密集型操作
result = await ThreadPoolManager().run_async(
    TaskType.CPU, pd.DataFrame, rows, columns=cols,
)
```

### 6.2 异步上下文

```python
# ✅ 正确：使用 async with 管理连接
async with self.engine.begin() as conn:
    await conn.execute(stmt, records)

# ❌ 禁止：手动管理连接生命周期
conn = await self.engine.connect()
await conn.execute(stmt)
await conn.close()  # 可能遗漏
```

---

## 七、错误处理原则

### 7.1 异常传播

| 层次 | 处理方式 |
|------|----------|
| 数据源层 | 捕获并重试，记录日志 |
| DAO 层 | 捕获并记录，可选传播 |
| 服务层 | 捕获并处理，通知用户 |
| UI 层 | 显示错误信息 |

### 7.2 关键异常

```python
# CancelledError 必须传播
except asyncio.CancelledError:
    logger.warning("Operation cancelled")
    raise  # ✅ 必须传播

# 其他异常可捕获处理
except Exception as e:
    logger.error(f"Error: {e}")
    return None  # 或 raise
```

---

## 八、代码质量原则

### 8.1 单一职责

每个模块、类、函数只负责一件事：

- `TushareClient`：API 调用 + 字段映射
- `BaseDao`：数据库操作抽象
- `MacroSyncStrategy`：宏观指标同步逻辑

### 8.2 DRY 原则

避免重复代码：

- 字段映射集中在 `_COLUMN_RENAMES`
- 类型转换集中在 `BaseDao._prepare_data_params`
- 时区处理集中在 `time_utils.py`

### 8.3 防御性编程

```python
# 空值检查
if df is None or df.empty:
    return 0

# 类型检查
if isinstance(val, (datetime.date, datetime.datetime)):
    ...

# 异常处理
try:
    ...
except Exception as e:
    logger.error(f"Error: {e}")
```

---

## 九、检查清单

每次代码修改前，请确认：

- [ ] 是否遵循分层架构？
- [ ] 字段映射是否在数据源层统一处理？
- [ ] 时区处理是否一致（感知/无关）？
- [ ] 日期是否以原生对象传递？
- [ ] 数据库操作是否通过 DAO 层？
- [ ] SQL 是否使用参数化查询？
- [ ] 异步操作是否正确使用线程池？
- [ ] 异常是否正确处理和传播？

---

## 十、参考文件

| 文件 | 说明 |
|------|------|
| [tushare_client.py](../data/tushare_client.py) | 数据源层实现 |
| [base_dao.py](../data/daos/base_dao.py) | DAO 层基类 |
| [time_utils.py](../utils/time_utils.py) | 时区处理工具 |
| [models.py](../data/models.py) | 数据库模型定义 |
