# 数据同步完整性增强 — 深度代码检视报告

> 检视范围：对照 `docs/data_sync_integrity_enhancement.md` v8.0 设计文档，逐文件覆盖实现代码
> 检视维度：调用链完整性、数据一致性、运行时安全、性能风险

---

## 🔴 致命问题 (3)

### C1: `CacheManager.get_incomplete_financial_stocks` 丢失参数 — 导致检查形同虚设

**文件**: [cache_manager.py:1033-1045](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/cache/cache_manager.py#L1033-L1045)

**问题**: CacheManager 的代理方法只传递 `min_periods`，但 `FinancialDao.get_incomplete_financial_stocks` 需要 `sync_version` 和 `table_name` 参数。

```python
# cache_manager.py L1033-1045 — 代理方法
async def get_incomplete_financial_stocks(self, min_periods: int = 4) -> set:
    return await self.financial_dao.get_incomplete_financial_stocks(min_periods)
    # ❌ 丢失 sync_version 和 table_name 参数
```

```python
# financial_dao.py L521-526 — 实际方法
async def get_incomplete_financial_stocks(
    self, min_periods: int = 4, sync_version: int = 1, table_name: str = "financial_reports"
) -> set:
```

**调用链影响**: `financial.py L174` → `cache.get_incomplete_financial_stocks(MIN_PERIODS)` — 虽然当前默认值恰好对齐，但代理层将参数截断的做法隐藏了可配置性，**如果 FinancialDao 端修改了默认值，调用方完全无法感知**。

**修复方案**: CacheManager 代理方法透传所有参数：

```python
async def get_incomplete_financial_stocks(
    self, min_periods: int = 4, sync_version: int = 1, table_name: str = "financial_reports"
) -> set:
    return await self.financial_dao.get_incomplete_financial_stocks(
        min_periods, sync_version, table_name
    )
```

---

### C2: `check_multi_period_data` 调用不存在的方法 `CacheManager.get_instance()` / `get_all_stock_codes()`

**文件**: [prompt_validator.py:66-70](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L66-L70)

**问题**: 

1. `CacheManager.get_instance()` — CacheManager 使用 `__new__` 单例模式，**不存在 `get_instance()` 类方法**。正确写法是 `CacheManager()`。
2. `cache.get_all_stock_codes()` — CacheManager **不存在此方法**。应该用 `cache.get_stock_basic()` 取得 DataFrame 后提取 `ts_code` 列。
3. `cache.get_financial_reports(ts_code)` (L113) — CacheManager **也不存在此方法**。

**Runtime 影响**: `check_multi_period_data`、`check_field_exists` 函数在实际调用时会立即抛出 `AttributeError`，**Prompt 声明校验功能完全瘫痪**。

**修复方案**:

```python
cache = CacheManager()  # 单例模式直接实例化

all_stocks_df = await cache.get_stock_basic()
all_stocks = all_stocks_df["ts_code"].tolist() if not all_stocks_df.empty else []

# check_field_exists 中
df = await cache.get_financial_reports_history(ts_code, periods=1)
```

---

### C3: 模块级变量 `DECLARATIONS` 使用 `field(default_factory=list)` 导致 `TypeError`

**文件**: [prompt_validator.py:152](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L152)

```python
DECLARATIONS: list[DataDeclaration] = field(default_factory=list)  # L152
# ...
DECLARATIONS = _init_declarations()  # L211 — 被覆盖
```

**问题**: `dataclasses.field()` 只能在 `@dataclass` 装饰器内部使用。在模块级使用会立即抛出 `TypeError: field() should be called within a class decorated with @dataclass`。虽然 L211 覆盖了它，但 **L152 的赋值会在模块导入时先执行，导致整个模块 import 失败**。

**修复方案**: 删除 L152 或改为 `DECLARATIONS: list[DataDeclaration] = []`。

---

## 🟠 高危问题 (5)

### H1: `SyncResult.merge()` 实现与文档设计严重偏离 — key 可能类型混乱

**文件**: [base.py:50-98](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/base.py#L50-L98)

**问题**: 

1. 文档设计（附录D.2.1）明确 `quality_scores` key 统一为 `str`（YYYYMMDD），但实际 `SyncResult` 用 `dict`（无类型约束），且 `merge()` 方法中的 `normalize_date_key()` 尝试将 str key 解析回 `datetime.date`，**与文档方向完全相反**。

2. `historical.py L109`: `result.quality_scores[date] = quality.get("score", 0)` — 这里的 `date` 来自 `get_bulk_sync_quality_scores` 返回值的 key，是 `datetime.date` 类型。但文档设计要求用 str。

**后果**: 当两个 SyncResult merge 时，同一天的数据可能存 `datetime.date(2024,1,1)` 和 `"20240101"` 两个 key，**质量评分被重复存储或丢失**。

**修复方案**: 统一为一种类型。推荐在 `get_bulk_sync_quality_scores` 返回时就统一 key 为 `datetime.date`，并在 `merge()` 中移除冗余的 normalize 逻辑。

---

### H2: `get_bulk_expected_stock_counts` 笛卡尔积 JOIN 缺少性能防护

**文件**: [quote_dao.py:650-697](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L650-L697)

**问题**: CTE 查询 `trading_days LEFT JOIN stock_basic` 在 3 年范围内：
- trading_days ≈ 750 行
- stock_basic ≈ 5500 行
- LEFT JOIN 产生 750 × 5500 ≈ 412 万行中间结果，然后 GROUP BY 聚合

虽然文档声称有 `idx_stock_basic_dates` 索引，但 **LEFT JOIN 条件 `s.list_date <= t.trade_date AND ... s.delist_date > t.trade_date` 是范围条件上的 OR 逻辑，索引效果有限**。在首次冷查询或数据库缓存失效时，此查询可能耗时 5-15 秒。

**修复方案**: 
1. 添加复合索引 `(list_status, list_date, delist_date)` 覆盖查询条件
2. 考虑对大范围查询做分批（每年一段），避免单次产出过大中间结果集
3. 添加查询超时保护

---

### H3: `get_bulk_table_counts` SQL 注入防御不完整

**文件**: [quote_dao.py:590-602](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L590-L602)

**问题**: 虽然有白名单检查 `if table_name not in allowed_tables`，但 `allowed_tables` 来自 `_get_default_synced_tables()`，而这个函数读取 `HistoricalSyncStrategy.SYNCED_TABLES`（一个 class variable）。如果该列表被运行时篡改（例如通过配置注入），就会绕过防御。

**更重要的是**：同一文件中的 `verify_data_integrity`（L198-304）和 `check_data_exists`（L67-97）使用同样的模式，但 `check_data_exists` 直接用 `f"SELECT 1 as val FROM {table}"` 拼接——虽然有白名单，但**白名单本身是动态生成的，不是硬编码的安全白名单**。

**修复方案**: 将 `_get_default_synced_tables()` 的结果与一个硬编码的已知安全表名集合做交集验证：

```python
_SAFE_TABLE_NAMES = frozenset({
    "daily_quotes", "daily_indicators", "moneyflow_daily", ...
})

def _get_default_synced_tables() -> list[str]:
    ...
    return [t for t in _DEFAULT_SYNCED_TABLES if t in _SAFE_TABLE_NAMES]
```

---

### H4: 日期类型不一致导致 `dates_to_verify` 与 `quality_results` 之间 key miss

**文件**: [historical.py:199-228](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L199-L228)

**问题**: 

1. `dates_to_verify` 从 `trade_dates` 中筛选，`trade_dates` 的元素类型来自 `get_trade_dates()`，可能是 `datetime.date` 或 `str`（取决于交易日历实现）。
2. `quality_results` 从 `get_bulk_sync_quality_scores` 返回，key 经过 `normalized_results` 处理可能是 `datetime.date`。
3. L213: `quality = quality_results.get(date)` — 如果 `date` 是 `str` 而 `quality_results` 的 key 是 `datetime.date`，**永远 get 不到，质量检查被静默跳过**。

**后果**: 断点续传的质量验证永远通过，低质量数据不会被重新同步 — **核心功能失效**。

**修复方案**: 在 `quality_results.get(date)` 前统一类型：

```python
for date in dates_to_verify:
    normalized_date = date if isinstance(date, datetime.date) else datetime.datetime.strptime(str(date), "%Y%m%d").date()
    quality = quality_results.get(normalized_date)
```

---

### H5: `verify_data_integrity` 内部引用了外层 `result`（闭包变量名冲突）

**文件**: [historical.py:632-644](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L632-L644)

```python
def verify_data_integrity(key: str, result_data: typing.Any):
    ...
    if result is not None:  # L643 — 这个 result 是哪个？
        result.warnings.append(warning_msg)
```

**问题**: `verify_data_integrity` 是 `sync_daily_market_snapshot` 的内部函数，L643 的 `result` 引用外层通过参数传入的 `result: "SyncResult | None"`。但参数名 `result` 与内部变量 `result_data` 以及外层循环中其他名为 result 的变量混淆，**如果 `result=None`（默认值），会静默吞噬所有数据完整性警告**。

**修复方案**: 将参数名改为更明确的 `sync_result`，并在闭包中显式捕获：

```python
async def sync_daily_market_snapshot(self, trade_date, force=False, sync_result=None):
    ...
    def verify_data_integrity(key, result_data):
        ...
        if sync_result is not None:
            sync_result.warnings.append(warning_msg)
```

---

## 🟡 中危问题 (6)

### M1: `get_sync_integrity_config` 返回的 key 与调用方不对齐

**文件**: [config_handler.py:1032-1056](file:///d:/workspace/Quantitative%20Trading/astock_screener/utils/config_handler.py#L1032-L1056)

**问题**: 方法返回 `quality_score_threshold`（从 `sync_integrity.quality_threshold` 读取），但 `historical.py L172` 调用时用 `sync_integrity_config.get("quality_score_threshold", 80)`。**虽然当前能对齐，但 config 的 JSON key 是 `quality_threshold`，代码的 Python key 是 `quality_score_threshold`，存在两层映射，极易在后续维护中断裂**。

**修复方案**: 统一命名，推荐 JSON key 和 Python dict key 保持一致。

---

### M2: `LOW_FREQUENCY_TABLES` 硬编码集合与 `table_tolerance_map` 不同步

**文件**: [quote_dao.py:12](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L12) vs [quote_dao.py:743-756](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L743-L756)

```python
# L12 — 硬编码的低频表集合
LOW_FREQUENCY_TABLES = {"limit_list", "suspend_d", "top_list", "block_trade", "index_daily", "index_dailybasic", "moneyflow_hsgt"}

# L743-756 — table_tolerance_map 中对同样的表设置了容差（但被 LOW_FREQUENCY_TABLES 短路跳过）
"index_daily": 0.95,      # 这个容差永远不会生效！
"index_dailybasic": 0.95, # 同上
"moneyflow_hsgt": 0.95,   # 同上
```

**问题**: `index_daily`、`index_dailybasic`、`moneyflow_hsgt` 被放入 `LOW_FREQUENCY_TABLES` 后，它们在质量评分中永远 `passed=True, ratio=1.0`。但这些表的容差设置为 0.95，说明设计者期望它们被正常评估。**代表设计者意图与实现矛盾**。

**修复方案**: 从 `LOW_FREQUENCY_TABLES` 中移除非低频表（`index_daily`, `index_dailybasic`, `moneyflow_hsgt`），或从 `table_tolerance_map` 中移除这些表的配置以消除歧义。

---

### M3: `quality_weights` 配置不覆盖非权重表 — 评分可能为 0

**文件**: [quote_dao.py:828-841](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L828-L841)

```python
weight = quality_weights.get(table, 10 if table == "daily_quotes" else 5)
```

**问题**: 配置中的 `quality_weights` 只有 4 个表（`daily_quotes`, `daily_indicators`, `moneyflow_daily`, `margin_daily`），fallback 逻辑用 `10 if table == "daily_quotes" else 5`。但如果 `quality_weights` 配置中**有** `daily_quotes` key（值为 30），L835 不会命中 fallback，所以这段 fallback 逻辑的条件判断是**自相矛盾**的。

更严重的是，如果所有表都在 `LOW_FREQUENCY_TABLES` 中（极端情况），`valid_tables` 可能为 0，导致 `total_weight = 0`，评分逻辑被跳过，`score` 保持为 0。

**修复方案**: 在 fallback 中使用 `quality_weights.get(table, 5)` 统一默认权重，移除对 `daily_quotes` 的特殊判断。

---

### M4: `financial.py` 中 `incomplete_stocks` 类型不一致

**文件**: [financial.py:184](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/financial.py#L184)

```python
synced_stocks = set(synced_stocks) - set(incomplete_stocks)
```

**问题**: `synced_stocks` 来自 `get_completed_step4_stocks()`，返回 `set`。`incomplete_stocks` 来自 `get_incomplete_financial_stocks()`，也返回 `set`。双重 `set()` 构造是冗余的，但**如果 `synced_stocks` 或 `incomplete_stocks` 中的元素类型不一致（一个返回 str 类型的 ts_code，另一个返回某种 wrapped 类型），差集运算可能静默丢失匹配**。

**修复方案**: 确保两个 DAO 方法返回的 ts_code 类型一致（都是 `str`），并移除冗余的 `set()` 转换。

---

### M5: `SyncResult.to_summary()` 返回类型偏离文档设计

**文件**: [base.py:99-114](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/base.py#L99-L114)

**问题**: 文档设计（6.1节）的 `to_summary()` 返回 `str`（可读摘要），但实际实现返回格式化的 `str`（`" | ".join(parts)`），与附录D.2.1中设计的返回 `dict` 不同。代码和文档自相矛盾。

**修复方案**: 确定一种返回类型。推荐保留当前 `str` 实现不变，但在文档附录中移除 `dict` 版本。如果下游需要结构化数据，另加 `to_dict()` 方法。

---

### M6: `check_table_has_data` 直接访问 `cache.quote_dao._read_db` — 破坏封装

**文件**: [prompt_validator.py:146](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L146)

```python
df = await cache.quote_dao._read_db(f"SELECT 1 FROM {table_name} LIMIT 1")
```

**问题**: Strategies 层直接穿透到 DAO 的 private 方法 `_read_db`，**完全绕过 CacheManager 的维护锁和封装**。如果同时发生 schema migration，可能读到不一致状态。

**修复方案**: 在 CacheManager 中添加 `check_table_has_data(table_name)` 代理方法，或使用 `cache._read_db()` 公共方法。

---

## 🟢 低危问题 (4)

### L1: 日期规范化代码重复 3 次

[quote_dao.py:609-620](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L609-L620), [quote_dao.py:683-694](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L683-L694), [historical.py:190-193](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L190-L193)

抽取为 `_normalize_trade_date(val) -> datetime.date` 工具函数。

### L2: `get_bulk_sync_quality_scores` 函数内 `from utils.config_handler import ConfigHandler` 使用延迟导入

[quote_dao.py:726](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L726)

在 DAO 方法内部使用延迟导入虽然避免了循环依赖，但每次调用都有微小的 import 检查开销。考虑在文件顶部用 `TYPE_CHECKING` 或在 `__init__` 中引入。

### L3: `verify_stock_financial_integrity` 检查 `fina_audit` 而非设计文档中的 `fina_indicator`

[financial_dao.py:505-513](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/financial_dao.py#L505-L513)

文档附录D.2.4已确认 `fina_indicator` 合并到了 `financial_reports`，但实际实现改为检查 `fina_audit`。这**不影响功能**但与文档原始 `tables_to_check = ["financial_reports", "fina_indicator"]` 不同，建议在注释中说明变更原因。

### L4: `SyncResult.quality_scores` 和 `expected_bases` 类型注解为 `dict` 缺少泛型

[base.py:46-48](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/base.py#L46-L48)

建议改为 `dict[datetime.date, int]` 以提供类型安全。

---

## 📊 问题汇总

| 严重性 | 数量 | 影响 |
|:------:|:----:|------|
| 🔴 致命 | 3 | C2/C3 会直接导致 import 或运行时 crash；C1 隐藏参数导致功能脆弱 |
| 🟠 高危 | 5 | H1/H4 可能导致核心质量检测功能静默失效；H2 可能导致生产环境慢查询 |
| 🟡 中危 | 6 | 配置歧义、类型混淆、封装破坏 |
| 🟢 低危 | 4 | 代码重复、类型注解、延迟导入 |

## 建议修复优先级

```
立即修复（阻塞发布）:
├── C2: prompt_validator.py — 不存在的方法调用（会 crash）
├── C3: prompt_validator.py — 模块级 field() 调用（会 crash）
└── H4: historical.py — 日期类型不一致（质量检测失效）

第一轮修复:
├── C1: cache_manager.py — 代理方法参数透传
├── H1: base.py — 统一 quality_scores key 类型
├── H5: historical.py — 闭包变量名冲突
└── M6: prompt_validator.py — 封装破坏

第二轮修复:
├── H2: quote_dao.py — 添加复合索引
├── H3: quote_dao.py — 硬编码安全白名单
├── M1-M5: 配置命名统一、LOW_FREQUENCY_TABLES 对齐等
└── L1-L4: 代码整理
```
