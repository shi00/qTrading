# TradeCalendarService 设计方案

## 一、背景与问题

### 1.1 当前架构问题

| 问题 | 现状 | 影响 |
|------|------|------|
| **入口分散** | 4个不同的调用入口 | 使用混乱、难以维护 |
| **职责不清** | DAO/Mixin/OfflineCalendar 职责重叠 | 代码重复、逻辑不一致 |
| **缺乏工具方法** | 常用功能需自行实现 | 开发效率低、易出错 |
| **在线/离线分离** | 两套独立实现 | 行为可能不一致 |

### 1.2 现有实现分布

| 文件 | 方法 | 职责 | 问题 |
|------|------|------|------|
| `stock_dao.py` | `get_trade_cal()`, `count_trade_days()`, `get_start_date_by_trade_days()` | 数据库查询 | 仅底层操作，无业务逻辑 |
| `calendar_mixin.py` | `get_trade_dates()`, `ensure_trade_cal()`, `get_latest_trade_date()` | 自动补齐 + 查询 | 依赖 DataProcessor，难以独立使用 |
| `cache_manager.py` | 代理方法 | 代理 DAO | 无增值逻辑 |
| `offline_calendar.py` | `is_trading_day()`, `get_trade_dates()` | 离线模式 | 无数据库支持，功能有限 |
| `tushare_client.py` | `get_trade_cal()`, `is_trading_day()` | API 调用 | 仅用于同步，有缓存但有限 |

### 1.3 核心问题分析

```
问题1: 调用链过长
业务代码 → CacheManager → StockDao → Database
         → DataProcessor → CalendarMixin → CacheManager → StockDao

问题2: 功能分散
- is_trading_day() 在 3 个地方有实现
- get_trade_dates() 在 4 个地方有实现
- 缺少 get_prev_trade_date() 等常用方法

问题3: 在线/离线不一致
- OfflineCalendar 使用 pandas_market_calendars
- 数据库使用 Tushare 数据
- 两套数据可能不同步
```

---

## 二、架构设计

### 2.1 整体架构

```
┌────────────────────────────────────────────────────────────────────┐
│                        TradeCalendarService                        │
│                        (data/services/trade_calendar_service.py)   │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                      公共 API (统一入口)                      │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │  基础查询:                                                    │ │
│  │  • is_trading_day(date) → bool                              │ │
│  │  • get_trade_dates(start, end) → List[date]                 │ │
│  │  • count_trade_days(start, end) → int                       │ │
│  │                                                              │ │
│  │  日期计算:                                                    │ │
│  │  • get_start_date_by_trade_days(end, n) → date              │ │
│  │  • get_prev_trade_date(date) → date                         │ │
│  │  • get_next_trade_date(date) → date                         │ │
│  │  • get_latest_trade_date() → date                           │ │
│  │                                                              │ │
│  │  批量操作:                                                    │ │
│  │  • get_trade_dates_batch(ranges) → Dict[range, List[date]]  │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                      内部实现                                 │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │  优先级: Database > Offline (pandas_market_calendars)        │ │
│  │                                                              │ │
│  │  缓存策略:                                                    │ │
│  │  • 内存缓存: 最近 30 天交易日 (TTL 5分钟)                     │ │
│  │  • 自动补齐: 数据库缺失时从 Tushare 拉取                      │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
└───────────────────────────┬────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │  StockDao  │  │  Offline   │  │  Tushare   │
     │  (主数据源) │  │  Calendar  │  │  Client    │
     │            │  │  (备用)    │  │  (同步)    │
     └────────────┘  └────────────┘  └────────────┘
```

### 2.2 文件结构

```
data/
├── services/
│   └── trade_calendar_service.py   # 新增: 统一服务
├── daos/
│   └── stock_dao.py                # 保留: 底层数据库操作
├── mixins/
│   └── calendar_mixin.py           # 保留: DataProcessor 集成
└── offline_calendar.py             # 保留: 离线备用
```

---

## 三、API 设计

### 3.1 类定义

```python
# data/services/trade_calendar_service.py

class TradeCalendarService:
    """
    统一交易日历服务。
    
    设计原则:
    1. 单一入口: 所有交易日历相关操作统一通过此类
    2. 优雅降级: 数据库不可用时自动切换到离线模式
    3. 智能缓存: 热点数据内存缓存，减少数据库压力
    4. 自动补齐: 数据缺失时自动从 Tushare 拉取
    """
    
    def __init__(self, cache_manager, tushare_client):
        self._cache = cache_manager
        self._api = tushare_client
        self._offline = OfflineCalendar
        self._mem_cache = {}  # 内存缓存
        self._cache_ttl = 300  # 5分钟
```

### 3.2 基础查询方法

#### is_trading_day(date) → bool

```python
async def is_trading_day(self, date) -> bool:
    """
    判断是否为交易日。
    
    Args:
        date: 日期 (date/datetime/str)
    
    Returns:
        bool: 是否为交易日
    
    示例:
        >>> await service.is_trading_day("2024-03-21")
        True
        >>> await service.is_trading_day("2024-03-23")  # 周六
        False
    """
```

#### get_trade_dates(start_date, end_date) → List[date]

```python
async def get_trade_dates(self, start_date, end_date) -> List[date]:
    """
    获取日期范围内的所有交易日。
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
    
    Returns:
        List[date]: 交易日列表 (升序)
    
    示例:
        >>> await service.get_trade_dates("2024-03-18", "2024-03-22")
        [date(2024, 3, 18), date(2024, 3, 19), date(2024, 3, 20), 
         date(2024, 3, 21), date(2024, 3, 22)]
    """
```

#### count_trade_days(start_date, end_date) → int

```python
async def count_trade_days(self, start_date, end_date) -> int:
    """
    计算日期范围内的交易日数量。
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
    
    Returns:
        int: 交易日数量
    
    示例:
        >>> await service.count_trade_days("2024-03-18", "2024-03-22")
        5
    """
```

### 3.3 日期计算方法

#### get_start_date_by_trade_days(end_date, trade_days) → date

```python
async def get_start_date_by_trade_days(self, end_date, trade_days: int) -> date:
    """
    根据交易日数量计算起始日期。
    
    Args:
        end_date: 结束日期
        trade_days: 交易日数量
    
    Returns:
        date: 起始日期
    
    示例:
        >>> await service.get_start_date_by_trade_days("2024-03-21", 120)
        date(2023, 9, 15)  # 120个交易日前
    """
```

#### get_prev_trade_date(date) → date

```python
async def get_prev_trade_date(self, date) -> date:
    """
    获取指定日期的上一个交易日。
    
    Args:
        date: 参考日期
    
    Returns:
        date: 上一个交易日
    
    示例:
        >>> await service.get_prev_trade_date("2024-03-21")
        date(2024, 3, 20)
        >>> await service.get_prev_trade_date("2024-03-18")  # 周一
        date(2024, 3, 15)  # 上周五
    """
```

#### get_next_trade_date(date) → date

```python
async def get_next_trade_date(self, date) -> date:
    """
    获取指定日期的下一个交易日。
    
    Args:
        date: 参考日期
    
    Returns:
        date: 下一个交易日
    
    示例:
        >>> await service.get_next_trade_date("2024-03-21")
        date(2024, 3, 22)
        >>> await service.get_next_trade_date("2024-03-22")  # 周五
        date(2024, 3, 25)  # 下周一
    """
```

#### get_latest_trade_date() → date

```python
async def get_latest_trade_date(self) -> date:
    """
    获取最近的交易日。
    
    规则:
    - 当前时间 < 15:00 → 返回上一个交易日
    - 当前时间 >= 15:00 → 返回今天 (如果是交易日) 或上一个交易日
    
    Returns:
        date: 最近交易日
    
    示例:
        # 假设今天是 2024-03-21 (周四) 14:00
        >>> await service.get_latest_trade_date()
        date(2024, 3, 20)  # 昨天的数据已完整
        
        # 假设今天是 2024-03-21 (周四) 16:00
        >>> await service.get_latest_trade_date()
        date(2024, 3, 21)  # 今天的数据已完整
    """
```

### 3.4 批量操作方法

#### get_trade_dates_batch(ranges) → Dict

```python
async def get_trade_dates_batch(
    self, 
    ranges: List[Tuple[date, date]]
) -> Dict[Tuple[date, date], List[date]]:
    """
    批量获取多个日期范围的交易日。
    
    优化: 合并为单次数据库查询，减少 IO 次数。
    
    Args:
        ranges: 日期范围列表 [(start1, end1), (start2, end2), ...]
    
    Returns:
        Dict: {范围: 交易日列表}
    
    示例:
        >>> ranges = [(date(2024, 3, 1), date(2024, 3, 5)),
        ...           (date(2024, 3, 10), date(2024, 3, 15))]
        >>> await service.get_trade_dates_batch(ranges)
        {(date(2024, 3, 1), date(2024, 3, 5)): [...],
         (date(2024, 3, 10), date(2024, 3, 15)): [...]}
    """
```

---

## 四、可行性评估

### 4.1 技术可行性

| 维度 | 评估 | 说明 |
|------|------|------|
| **数据源** | ✅ 可行 | 已有 `trade_cal` 表 + `OfflineCalendar` |
| **API 设计** | ✅ 可行 | 所有方法均可基于现有 DAO 实现 |
| **缓存机制** | ✅ 可行 | 可复用 `calendar_mixin.py` 的 TTL 缓存逻辑 |
| **自动补齐** | ✅ 可行 | 可复用 `ensure_trade_cal()` 逻辑 |

### 4.2 迁移影响

| 影响范围 | 文件数 | 迁移策略 |
|----------|--------|----------|
| **策略层** | 3 | `oversold_strategy.py`, `ai_mixin.py` 改用新服务 |
| **数据层** | 4 | `calendar_mixin.py` 内部调用新服务，对外接口不变 |
| **UI 层** | 2 | `data_source_tab.py`, `onboarding_wizard.py` 无需修改 |
| **测试** | 3 | 新增服务测试，现有测试保持兼容 |

### 4.3 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|----------|
| **接口变更** | 中 | 保留现有接口，新服务作为推荐入口 |
| **性能影响** | 低 | 内存缓存 + 批量查询优化 |
| **数据一致性** | 低 | Database 优先，Offline 作为备用 |

---

## 五、实施计划

### Phase 1: 创建服务 (低风险)

**目标**: 创建 TradeCalendarService 基础框架

**任务**:
1. 新建 `data/services/trade_calendar_service.py`
2. 实现核心方法 (基于现有 DAO)
3. 添加单元测试 `tests/test_trade_calendar_service.py`

**验收标准**:
- [ ] 所有核心方法实现完成
- [ ] 单元测试覆盖率 > 80%
- [ ] 离线模式正常工作

### Phase 2: 集成到 DataProcessor (低风险)

**目标**: 将服务集成到现有架构

**⚠️ 架构决策**: 不在 CacheManager 中创建 TradeCalendarService，而是由 DataProcessor 持有。

**理由**:
- CacheManager 是纯粹的 DAO Facade，不应依赖 TushareClient
- 避免循环依赖和层级混乱
- 保持分层架构的清晰性

**⛔ 关键约束**: Python 禁止 `async def __init__`，必须在同步 `__init__` 中进行依赖注入。

```python
# data_processor.py 修正后
class DataProcessor(HealthCheckMixin, CalendarMixin):
    def __init__(self):
        # 现有初始化...
        # 挂载服务（仅依赖注入，不执行任何 I/O 操作）
        self.trade_calendar = TradeCalendarService(self.cache, self.api)
```

**任务**:
1. 在 `DataProcessor.__init__()` 中添加 `trade_calendar` 属性（同步依赖注入）
2. 创建 `TradeCalendarService(self.cache, self.api)` 实例
3. 保留 CacheManager 现有代理方法 (兼容性)

**验收标准**:
- [ ] DataProcessor.trade_calendar 可用
- [ ] 现有代码无需修改即可运行
- [ ] CacheManager 保持纯粹 DAO Facade 定位
- [ ] DataProcessor.__init__ 保持同步

### Phase 3: 迁移调用方 (中风险)

**目标**: 将现有代码迁移到新服务

**任务**:
1. `oversold_strategy.py` 改用新服务
2. `ai_mixin.py` 改用新服务
3. `scheduler_service.py` 改用新服务（⚠️ 核心后台依赖）
   - 移除 `TushareClient.is_trading_day()` 调用
   - 改用 `await dp.trade_calendar.is_trading_day()`
   - 不再需要 `ThreadPoolManager` 包装（新服务原生异步）
4. `calendar_mixin.py` 作为 Facade 代理（平滑过渡）
   ```python
   # data/mixins/calendar_mixin.py (过渡期代码)
   import warnings
   
   class CalendarMixin:
       async def get_latest_trade_date(self):
           warnings.warn("Use dp.trade_calendar instead", DeprecationWarning)
           return await self.trade_calendar.get_latest_trade_date()
   ```

**验收标准**:
- [ ] 所有策略测试通过
- [ ] scheduler_service.py 迁移完成
- [ ] 性能无明显下降
- [ ] 旧接口发出废弃警告但正常工作

### Phase 4: 清理冗余 (低风险)

**目标**: 清理冗余代码，完善文档

**任务**:
1. 标记旧方法为 `@deprecated`
2. 更新相关文档
3. 后续版本移除冗余代码

**验收标准**:
- [ ] 文档更新完成
- [ ] 废弃警告正常显示

---

## 六、工作量估算

| 阶段 | 工作内容 | 预估时间 |
|------|----------|----------|
| Phase 1 | 创建服务 + 测试 | 2 小时 |
| Phase 2 | 集成 DataProcessor | 0.5 小时 |
| Phase 3 | 迁移调用方 (含 scheduler_service) | 1.5 小时 |
| Phase 4 | 清理冗余 | 0.5 小时 |
| **总计** | | **4.5 小时** |

---

## 七、决策记录

| # | 决策点 | 决策 | 理由 |
|---|--------|------|------|
| 1 | 服务位置 | `data/services/` | 符合分层架构，与现有 `market_data_service.py` 一致 |
| 2 | 缓存策略 | 内存缓存 (TTL 5分钟) + 并发锁 | 性能优化，防止缓存击穿 |
| 3 | 迁移策略 | 渐进迁移 | 降低风险，保持兼容性 |
| 4 | 旧接口处理 | Facade 代理 + 废弃警告 | 兼容性，给用户迁移时间 |
| 5 | 服务持有者 | DataProcessor (非 CacheManager) | 保持 CacheManager 纯粹性，避免循环依赖 |
| 6 | 空查询处理 | 占位缓存 (None + TTL) | 防止重复请求 Tushare，避免频控惩罚 |
| 7 | 初始化方式 | 同步 `__init__` 依赖注入 | Python 禁止 `async def __init__` |
| 8 | 数据落盘 | API 获取后必须入库 | 避免缓存穿透，减少 Tushare API 调用 |
| 9 | scheduler_service | 纳入迁移计划 | 消除 TushareClient 中的重复日历逻辑 |

---

## 八、实现细节补充

### 8.1 缓存并发保护

```python
class TradeCalendarService:
    def __init__(self, cache_manager, tushare_client):
        self._cache = cache_manager
        self._api = tushare_client
        self._offline = OfflineCalendar
        self._mem_cache = {}
        self._cache_ttl = 300
        self._cache_lock = asyncio.Lock()  # 防止缓存击穿
    
    async def get_latest_trade_date(self):
        cache_key = "latest_trade_date"
        
        # 检查缓存
        if cache_key in self._mem_cache:
            entry = self._mem_cache[cache_key]
            if time.time() - entry["ts"] < self._cache_ttl:
                return entry["val"]
        
        # 加锁防止并发穿透
        async with self._cache_lock:
            # 双重检查
            if cache_key in self._mem_cache:
                entry = self._mem_cache[cache_key]
                if time.time() - entry["ts"] < self._cache_ttl:
                    return entry["val"]
            
            # 实际查询
            result = await self._fetch_latest_trade_date()
            self._mem_cache[cache_key] = {"ts": time.time(), "val": result}
            return result
```

### 8.2 日期类型统一防御

```python
def _to_date(self, d) -> datetime.date:
    """
    统一日期类型转换。
    服务入口统一转换，内部流转全部使用 datetime.date。
    """
    if d is None:
        return None
    if isinstance(d, datetime.date) and not isinstance(d, datetime.datetime):
        return d
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, str):
        return parse_date(d.replace("-", "")).date()
    raise ValueError(f"无法将 {type(d)} 转换为 date")
```

### 8.3 空查询占位缓存

```python
async def _ensure_trade_cal(self, start_date, end_date):
    cache_key = f"ensure_{start_date}_{end_date}"
    
    # 检查占位缓存 (包括 None 值)
    if cache_key in self._mem_cache:
        return self._mem_cache[cache_key]["val"]  # 可能是 None
    
    # 查询数据
    df = await self._api.get_trade_cal(start_date, end_date)
    
    if df is None or df.empty:
        # 空结果也缓存，避免重复请求
        self._mem_cache[cache_key] = {
            "ts": time.time(),
            "val": None,
            "ttl": 3600  # 空结果缓存 1 小时
        }
        return None
    
    # 正常缓存
    await self._cache.save_trade_cal(df)
    self._mem_cache[cache_key] = {
        "ts": time.time(),
        "val": True,
        "ttl": self._cache_ttl
    }
    return True
```

### 8.4 数据落盘（DB Sync）- ⛔ 关键修正

**问题**: 原设计只读不写，导致每次重启都会对 Tushare 产生缓存穿透。

**修正**: API 层获取数据后，必须异步入库。

```python
async def get_trade_dates(self, start_date, end_date) -> List[date]:
    """
    获取日期范围内的所有交易日。
    优先级: Database -> Tushare API -> Offline
    """
    start_date = self._to_date(start_date)
    end_date = self._to_date(end_date)
    
    # 1. 尝试从数据库获取
    df = await self._cache.get_trade_cal(start_date, end_date, is_open=1)
    if df is not None and not df.empty:
        return sorted(pd.to_datetime(df["cal_date"]).dt.date.tolist())
    
    # 2. 从 Tushare API 获取
    df = await self._api.get_trade_cal(start_date, end_date)
    if df is not None and not df.empty:
        # ⛔ 关键: 数据落盘，避免下次穿透
        await self._cache.save_trade_cal(df)
        return sorted(pd.to_datetime(df["cal_date"]).dt.date.tolist())
    
    # 3. 离线模式兜底
    return self._offline.get_trade_dates(start_date, end_date)
```

### 8.5 scheduler_service.py 迁移示例

**迁移前** (使用 TushareClient 同步方法):
```python
# scheduler_service.py 现状
from data.tushare_client import TushareClient
from utils.thread_pool import ThreadPoolManager, TaskType

client = TushareClient()
is_trading = await ThreadPoolManager().run_async(
    TaskType.IO, client.is_trading_day, today
)
```

**迁移后** (使用新服务原生异步):
```python
# scheduler_service.py 迁移后
from data.data_processor import DataProcessor

dp = DataProcessor()
# 新服务原生异步，无需 ThreadPool 包装
is_trading = await dp.trade_calendar.is_trading_day(today)
```

---

## 九、使用示例

### 9.1 基础用法

```python
from data.cache_manager import CacheManager
from data.tushare_client import TushareClient
from data.services.trade_calendar_service import TradeCalendarService

# 初始化
cache = CacheManager()
api = TushareClient()
calendar = TradeCalendarService(cache, api)

# 判断交易日
is_trade = await calendar.is_trading_day("2024-03-21")

# 获取交易日列表
dates = await calendar.get_trade_dates("2024-03-01", "2024-03-31")

# 获取最近交易日
latest = await calendar.get_latest_trade_date()

# 根据交易日计算起始日期
start = await calendar.get_start_date_by_trade_days(latest, 120)
```

### 9.2 在策略中使用 (推荐方式)

```python
# oversold_strategy.py

async def _math_filter(self, context, rsi_period, rsi_threshold):
    dp = context.get("data_processor")
    
    # 通过 DataProcessor 访问服务 (推荐)
    end_date = await dp.trade_calendar.get_latest_trade_date()
    start_date = await dp.trade_calendar.get_start_date_by_trade_days(end_date, 120)
    
    # 获取历史数据
    history_df = await dp.cache.get_daily_quotes(
        ts_code_list=valid_codes,
        start_date=start_date,
        end_date=end_date,
    )
```

### 9.3 兼容方式 (已废弃)

```python
# 旧方式: 直接调用 CacheManager 方法 (已废弃，但保持兼容)
dates = await cache.get_trade_cal(start_date, end_date, is_open=1)

# 旧方式: 通过 DataProcessor.get_trade_dates() (已废弃)
dates = await dp.get_trade_dates(start_date, end_date)
```

---

## 十、附录

### A. 相关文件

| 文件 | 说明 |
|------|------|
| `data/daos/stock_dao.py` | 底层数据库操作 |
| `data/mixins/calendar_mixin.py` | DataProcessor 日历混入 |
| `data/offline_calendar.py` | 离线日历实现 |
| `data/tushare_client.py` | Tushare API 客户端 |
| `data/cache_manager.py` | 缓存管理器 |

### B. 数据表结构

```sql
-- trade_cal 表
CREATE TABLE trade_cal (
    cal_date     DATE PRIMARY KEY,  -- 日期
    exchange     VARCHAR,           -- 交易所 (SSE/SZSE)
    is_open      INTEGER,           -- 是否交易日 (1=是, 0=否)
    pretrade_date DATE              -- 上一交易日
);

-- 索引
CREATE INDEX idx_trade_cal_is_open ON trade_cal(is_open);
CREATE INDEX idx_trade_cal_date_range ON trade_cal(cal_date);
```

### C. 参考资源

- [Tushare 交易日历 API](https://tushare.pro/document/2?doc_id=26)
- [pandas_market_calendars 文档](https://pypi.org/project/pandas-market-calendars/)
