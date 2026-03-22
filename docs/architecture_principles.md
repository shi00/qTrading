# 架构设计原则

> 本文档总结了项目的核心架构设计原则，所有代码修改、方案设计必须遵循此文档。
> 
> 最后更新：2026-03-21

---

## 一、分层架构

### 1.1 架构层次

```
┌─────────────────────────────────────────────────────────────┐
│  UI 层 (ui/views, ui/components)                             │
│  - 用户界面组件渲染                                            │
│  - 事件处理                                                  │
│  - 绑定 ViewModel，监听数据更新                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  视图模型 / 协调层 (ui/viewmodels, ui/controllers)              │
│  - 状态管理                                                  │
│  - 通过观察者模式 (Pub/Sub) 订阅后台服务的数据                    │
│  - 隔离 UI 层对业务逻辑的直接阻断式调用                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  服务层 (services/, data/news_subscription.py)                │
│  - 后台轮询、业务逻辑处理与任务调度 (TaskManager)                  │
│  - 提供 add_listener 等发布-订阅接口                           │
│  - 业务流转中调用 DAO 层/外部 API                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  DAO 门面 (data/cache_manager.py)                            │
│  - 聚合具体的具体领域 DAO (StockDao, MarketDao 等)              │
│  - 数据库引擎生命周期、线程锁、Alembic 迁移调度                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  DAO 层与数据源 (data/daos/, data/tushare_client.py)           │
│  - BaseDao (防并发写入、UPSERT 封装、内存序列化)                │
│  - TushareClient (令牌桶限流、线程安全日历缓存、异常/网络超时重试)   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 层次职责

| 层次 | 职责 | 禁止行为 |
|------|------|----------|
| UI 层 | 用户交互、事件处理、数据呈现 | 直接访问 DAO、直接在 UI 线程执行长耗时同步 I/O |
| 视图模型层 | 管理组件局部状态与全局服务订阅，充当视图与服务的桥梁 | 编写数据库游标操作、直接构造/执行 SQL |
| 服务层 | 后台数据同步、数据处理策略、观察者派发、定时任务调度 | 直接拼接或执行 SQL，跨过 CacheManager 调用底层库 |
| DAO 门面层 | `CacheManager` 作为聚合网关，控制数据库 Engine 与迁移生命周期 | 携带复杂的业务判定逻辑 (如新闻文本解析 AI 标签) |
| DAO 层 | `BaseDao` 基类及其派生类，防并发、参数化查询及 UPSERT 抽象 | 调用外部 API、进行网络 I/O |
| 数据源层 | API 限流请求、双检锁(DCL)日历缓存、基础字段清洗重命名 | 处理业务聚合逻辑、直接使用 SQL 写入数据库 |

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

### 6.1 公共线程池原则

**原则**：所有异步任务执行必须使用系统公共线程池 `ThreadPoolManager`，禁止私自创建线程池。

**原因**：
- 统一资源管理，避免线程池泛滥
- 防止资源泄露和竞争
- 便于监控和调优

**正确示例**：
```python
from utils.thread_pool import ThreadPoolManager, TaskType

# ✅ 正确：使用公共线程池
result = await ThreadPoolManager().run_async(
    TaskType.CPU, expensive_computation, data
)
```

**错误示例**：
```python
# ❌ 禁止：私自创建线程池
import concurrent.futures
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
result = await loop.run_in_executor(executor, func)

# ❌ 禁止：直接使用 asyncio 默认线程池
result = await loop.run_in_executor(None, func)  # 绕过公共线程池管理
```

### 6.2 任务类型隔离

**原则**：为了防止大量数据转换和同步网络调用阻塞主 Event Loop，代码必须对不同类型的耗时操作进行物理线程池隔离卸载。

| 任务类型 | 线程池枚举 | 适用场景及要求 |
|----------|--------|------|
| CPU 密集型 | `TaskType.CPU` | Pandas DataFrame 数据构造、过滤，以及 `pd.to_datetime` 日期格式强转等开销极高的运算。 |
| IO 密集型 | `TaskType.IO` | 同步的第三方库调用 (如 Tushare 官方 SDK)、文件读写、Alembic 升级命令调度。 |

**正确示例**：
```python
from utils.thread_pool import ThreadPoolManager, TaskType

# 1. CPU 密集型操作 (如 DAO 层的 Pandas 转换)
result = await ThreadPoolManager().run_async(
    TaskType.CPU, pd.DataFrame, rows, columns=cols,
)

# 2. IO 密集型操作 (如同步的第三方 HTTP Client)
# data_src._handle_api_call 中
result = await loop.run_in_executor(
    ThreadPoolManager().io_pool, functools.partial(func, **kwargs),
)
```

### 6.2 异步上下文与状态锁定

```python
# ✅ 正确：使用 async with 管理数据库生命周期
async with self.engine.begin() as conn:
    await conn.execute(stmt, records)

# ✅ 正确：核心单例必须包含初始化阶段的双重检查锁定机制(DCL)防穿透
class ExampleService:
    _instance = None
    _lock = threading.Lock()
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

# ❌ 禁止：手动管理连接或在多协程共享单例的初期裸奔
conn = await self.engine.connect()
await conn.execute(stmt)
await conn.close()  # 可能因为异常遗漏
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

### 8.3 问题解决原则

**原则**：发现一个问题，必须解决一类问题。

**原因**：
- 根因分析比症状修复更重要
- 避免同类问题反复出现
- 提升代码质量和系统稳定性

**正确做法**：
```python
# 发现：asyncpg 日期类型转换报错
# ❌ 错误：只修复报错的那一行
await dao.save_data(date.strftime("%Y%m%d"))  # 仅修复此处

# ✅ 正确：在 BaseDao 层统一处理日期类型转换
class BaseDao:
    @staticmethod
    def _convert_param_for_asyncpg(val):
        if isinstance(val, str):
            # 统一处理所有字符串日期转换
            if len(val) == 8 and val.isdigit():
                return datetime.date(int(val[:4]), int(val[4:6]), int(val[6:8]))
        return val
```

**实践要点**：
1. **根因分析**：遇到问题时，先分析根本原因，而非仅修复表面症状
2. **全面排查**：发现一处问题，检查整个代码库是否存在同类问题
3. **统一修复**：在合适的抽象层次统一解决，而非分散修复
4. **预防机制**：添加测试用例和代码规范，防止同类问题再次发生

**典型案例**：
| 发现的问题 | 错误修复 | 正确修复 |
|------------|----------|----------|
| asyncpg 日期转换错误 | 仅修复报错处 | BaseDao 统一转换 |
| 测试使用 SQLite | 仅跳过测试 | 统一使用 PostgreSQL |
| 字段映射分散 | 各处手动重命名 | TushareClient 集中映射 |

### 8.4 防御性编程

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

## 九、测试用例原则

### 9.1 测试覆盖要求

**原则**：所有新增代码、修改代码必须配套自动化测试用例。

| 代码类型 | 测试要求 | 覆盖率目标 |
|----------|----------|------------|
| DAO 层方法 | 单元测试 + 集成测试 | ≥ 80% |
| 服务层方法 | 单元测试 + Mock | ≥ 70% |
| 数据源层方法 | 单元测试 + Mock API | ≥ 80% |
| 工具函数 | 单元测试 | ≥ 90% |

### 9.2 测试类型

```
┌─────────────────────────────────────────────────────────────┐
│  单元测试 (tests/unit/)                                      │
│  - 测试单个函数/方法                                          │
│  - 使用 Mock 隔离外部依赖                                     │
│  - 快速执行，无数据库连接                                      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  集成测试 (tests/integration/)                               │
│  - 测试模块间交互                                             │
│  - 使用测试数据库                                             │
│  - 验证数据流完整性                                           │
└─────────────────────────────────────────────────────────────┘
```

### 9.3 测试命名规范

```python
# 测试文件命名：test_<module_name>.py
# 测试类命名：Test<ClassName>
# 测试方法命名：test_<method_name>_<scenario>_<expected_result>

class TestMacroSyncStrategy:
    def test_merge_macro_data_with_valid_data_returns_merged_df(self):
        ...
    
    def test_merge_macro_data_with_empty_cpi_returns_m2_only(self):
        ...
    
    def test_merge_indicator_with_missing_period_column_logs_warning(self):
        ...
```

### 9.4 测试数据管理

```python
# 使用 fixture 管理测试数据
import pytest

@pytest.fixture
def sample_cpi_data():
    """返回模拟的 CPI 数据"""
    return pd.DataFrame({
        "period": ["202401", "202402"],
        "cpi": [101.5, 102.3]
    })

@pytest.fixture
def sample_ppi_data():
    """返回模拟的 PPI 数据"""
    return pd.DataFrame({
        "period": ["202401", "202402"],
        "ppi": [-1.5, -1.2]
    })

def test_merge_macro_data(sample_cpi_data, sample_ppi_data):
    result = MacroSyncStrategy._merge_macro_data(None, sample_cpi_data, sample_ppi_data)
    assert result is not None
    assert "period" in result.columns
```

### 9.5 Mock 使用规范

```python
from unittest.mock import AsyncMock, MagicMock, patch

# Mock 异步方法
@pytest.mark.asyncio
async def test_save_macro_economy():
    dao = MacroDao(engine)
    dao._save_upsert = AsyncMock(return_value=10)
    
    df = pd.DataFrame({"period": ["202401"], "cpi": [101.5]})
    result = await dao.save_macro_economy(df)
    
    assert result == 10
    dao._save_upsert.assert_called_once()

# Mock 外部 API
@patch("data.tushare_client.TushareClient._handle_api_call")
def test_get_cpi_data(mock_api):
    mock_api.return_value = pd.DataFrame({
        "month": ["202401"],
        "nt_val": [101.5]
    })
    # 测试逻辑...
```

### 9.6 测试执行要求

```bash
# 运行所有测试
pytest

# 运行指定模块测试
pytest tests/unit/test_macro.py

# 运行并生成覆盖率报告
pytest --cov=data --cov=services --cov-report=html

# 运行快速测试（跳过集成测试）
pytest -m "not integration"
```

### 9.7 测试检查清单

每次代码提交前，请确认：

- [ ] 新增方法是否有对应测试用例？
- [ ] 修改的方法是否更新了测试用例？
- [ ] 边界条件是否覆盖（空值、异常值）？
- [ ] 测试是否独立（不依赖执行顺序）？
- [ ] 测试命名是否清晰表达意图？
- [ ] 是否使用 Mock 隔离外部依赖？

---

## 十、检查清单

每次代码修改前，请确认：

- [ ] 是否在并发服务启动类中合理套用了防并发初始化的锁隔离？
- [ ] UI 层是否严守通过 ViewModel 与基础服务沟通的限界（而非越级裸持 DAO）？
- [ ] 字段映射是否在数据源层统一处理？
- [ ] 时区处理是否一致（内存使用 CST 感知，读写屏蔽差异）？
- [ ] 日期是否以原生对象传递而未进行魔术字符串格式化？
- [ ] 数据库操作是否完全经由 DAO 并在 `CacheManager` 中获得统一接管？
- [ ] 原始 SQL 以及对 `_write_db`/`_read_db` 的访问代码是否已经从服务层绝迹？
- [ ] SQL 是否使用参数化查询防御注入？
- [ ] 异步任务是否使用公共线程池 `ThreadPoolManager`（禁止私自创建线程池）？
- [ ] 复杂 Pandas 操作与同步 HTTP 调用是否被正确卸载至 `ThreadPoolManager` 的适当工作池？
- [ ] 异常是否正确处理和传播（含网络超时退避与令牌桶容错等机制有效生效）？
- [ ] 发现问题时是否解决了一类问题（根因分析 + 全面排查 + 统一修复）？
- [ ] 新增/修改代码是否有测试用例？

---

## 十一、参考文件

| 文件 | 说明 |
|------|------|
| [tushare_client.py](../data/tushare_client.py) | 数据源层实现 |
| [base_dao.py](../data/daos/base_dao.py) | DAO 层基类 |
| [time_utils.py](../utils/time_utils.py) | 时区处理工具 |
| [models.py](../data/models.py) | 数据库模型定义 |
