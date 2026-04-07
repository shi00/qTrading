# 数据同步完整性增强 — 修复后复审报告

> 复审时间：2026-04-07
> 对比基准：上一轮检视报告（18 个问题）

---

## ✅ 已确认修复的问题 (12/18)

| 原ID | 问题 | 修复方式 | 验证状态 |
|:----:|------|----------|:--------:|
| C1 | CacheManager 代理方法截断参数 | [cache_manager.py:1064-1082](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/cache/cache_manager.py#L1064-L1082) 透传 `min_periods, sync_version, table_name` | ✅ 完整修复 |
| C2 | `CacheManager.get_instance()` / `get_all_stock_codes()` 不存在 | [prompt_validator.py:67-74](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L67-L74) 改为 `CacheManager()` + `get_stock_basic()` | ✅ 完整修复 |
| C3 | 模块级 `field()` 调用导致 import crash | [prompt_validator.py:136](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L136) 改为 `DECLARATIONS: list[DataDeclaration] = []` | ✅ 完整修复 |
| H1 | SyncResult key 类型混乱 | [base.py:46-47](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/base.py#L46-L47) 类型注解统一为 `dict[datetime.date, ...]`，merge 中 normalize 保留防御性转换 | ✅ 完整修复 |
| H3 | SQL 注入白名单动态生成 | [quote_dao.py:14-43](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L14-L43) 添加 `_SAFE_TABLE_NAMES` 硬编码 frozenset，`_get_default_synced_tables` 做交集 | ✅ 完整修复 |
| H4 | 日期类型不一致导致 quality_results.get(date) miss | [historical.py:190-203](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L190-L203) 添加 `normalize_date` + `to_date_key` 双向转换函数 | ✅ 完整修复 |
| H5 | 闭包变量名 `result` 冲突 | [historical.py:398](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L398) 参数名改为 `sync_result`，[L654](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L654) 内部函数正确引用 | ✅ 完整修复 |
| M2 | LOW_FREQUENCY_TABLES 硬编码与 tolerance_map 矛盾 | [quote_dao.py:12](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L12) 移除 `index_daily`, `index_dailybasic`, `moneyflow_hsgt`；[L829-832](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L829-L832) 改为根据 tolerance < 0.5 动态判断低频 | ✅ 优雅修复 |
| M3 | quality_weights fallback 自相矛盾 | [quote_dao.py:875](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L875) 改为统一 `quality_weights.get(table, 5)` | ✅ 完整修复 |
| M5 | SyncResult.to_summary() 返回类型矛盾 | [base.py:99-129](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/base.py#L99-L129) 保留 `to_summary() -> str`，新增 `to_dict() -> dict` | ✅ 完整修复 |
| M6 | prompt_validator 穿透到 `_read_db` | [prompt_validator.py:132-133](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L132-L133) 改为调用 `cache.check_table_has_data(table_name)` | ✅ 设计正确 |
| L3 | fina_audit 替代 fina_indicator 缺注释 | [financial_dao.py:505-507](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/financial_dao.py#L505-L507) 添加了说明注释 | ✅ 完整修复 |

---

## 🔴 致命问题 — 新发现 (2)

### C-NEW-1: `CacheManager.check_table_has_data()` 方法不存在

**文件**: [prompt_validator.py:133](file:///d:/workspace/Quantitative%20Trading/astock_screener/strategies/prompt_validator.py#L133) → [cache_manager.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/cache/cache_manager.py)

```python
# prompt_validator.py L128-133
async def check_table_has_data(table_name: str) -> bool:
    from data.cache.cache_manager import CacheManager
    cache = CacheManager()
    return await cache.check_table_has_data(table_name)  # ❌ CacheManager 没有这个方法！
```

**验证**: 搜索整个 `cache_manager.py`（1083 行），**没有** `check_table_has_data` 方法定义。上一轮报告的 M6 修复建议是"在 CacheManager 中添加 `check_table_has_data` 代理方法"，但实际修复只改了 `prompt_validator.py` 的调用方，**没在 CacheManager 中添加对应方法**。

**Runtime 影响**: 10 个 `DataDeclaration`（审计意见、分红记录、质押比例、宏观经济等）的 injector 全部会抛出 `AttributeError`，**Prompt 声明校验功能依然瘫痪**。

**修复方案**: 在 `CacheManager` 中添加：

```python
async def check_table_has_data(self, table_name: str) -> bool:
    """检查指定表是否有数据（安全白名单校验）"""
    from data.persistence.daos.quote_dao import _SAFE_TABLE_NAMES
    
    if table_name not in _SAFE_TABLE_NAMES:
        logger.warning(f"[CacheManager] Invalid table name rejected: {table_name}")
        return False
    
    try:
        df = await self._read_db(f"SELECT 1 FROM {table_name} LIMIT 1")
        return df is not None and not df.empty
    except Exception:
        return False
```

---

### C-NEW-2: `get_incomplete_financial_stocks` SQL 引用不存在的列 `s.table_name`

**文件**: [financial_dao.py:554](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/financial_dao.py#L554)

```sql
WHERE s.sync_version = $1
  AND s.table_name = $2        -- ❌ 这一列在 stock_sync_status 表中不存在！
  AND (f.periods IS NULL OR f.periods < $3)
```

**Schema 验证**: `stock_sync_status` 表的 ORM 模型（[models.py:451-457](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/models.py#L451-L457)）只有 4 列：

| 列名 | 类型 |
|------|------|
| `ts_code` | String (PK) |
| `step4_completed_at` | DateTime |
| `sync_version` | Integer |
| `updated_at` / `created_at` | DateTime |

**不存在 `table_name` 列**。这是上一轮修复 C1（代理透传 `table_name` 参数）时引入的回归 bug — 参数传进来了，但 SQL 用了不存在的列。

**Runtime 影响**: `get_incomplete_financial_stocks()` 在实际执行时会抛出 PostgreSQL 错误 `column s.table_name does not exist`。由于 `except` 捕获后返回空集 `set()`，**财务数据完整性检查被静默跳过**，所有半残股票都不会被重新同步。

**修复方案**: 移除 `table_name` 参数和对应的 SQL WHERE 条件（因为 `stock_sync_status` 本身就是 Step4 的状态表，不区分具体 table）：

```python
# financial_dao.py
async def get_incomplete_financial_stocks(
    self, min_periods: int = 4, sync_version: int = 1
) -> set:
    try:
        df = await self._read_db(
            """
            SELECT s.ts_code
            FROM stock_sync_status s
            LEFT JOIN (
                SELECT ts_code, COUNT(DISTINCT end_date) as periods
                FROM financial_reports
                GROUP BY ts_code
            ) f ON s.ts_code = f.ts_code
            WHERE s.sync_version = $1
              AND (f.periods IS NULL OR f.periods < $2)
            """,
            (sync_version, min_periods),
        )
        ...

# cache_manager.py — 同步移除 table_name 参数
async def get_incomplete_financial_stocks(
    self, min_periods: int = 4, sync_version: int = 1
) -> set:
    return await self.financial_dao.get_incomplete_financial_stocks(
        min_periods, sync_version
    )
```

---

## 🟡 中危问题 — 遗留 (2)

### M1-遗留: Config key 名称映射仍然不一致

**文件**: [config_handler.py:1048](file:///d:/workspace/Quantitative%20Trading/astock_screener/utils/config_handler.py#L1048) vs [historical.py:172](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L172)

上一轮报告指出 config 返回 `quality_score_threshold`，L172 用 `quality_score_threshold` 读取。修复后 config 返回 `quality_threshold`（L1048），historical.py 也改为 `quality_threshold`（L172）。**名称已对齐，此问题已修复**。✅

### M4-遗留: `financial.py` 中 `incomplete_stocks` 双重 set()

**文件**: [financial.py:183](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/financial.py#L183)

```python
synced_stocks = set(synced_stocks) - incomplete_stocks
```

**状态**: 逻辑正确（`synced_stocks` 已是 `set`，`incomplete_stocks` 也是 `set`，`set() - set` 可行）。多余的 `set()` 转换只是冗余，**不影响功能**。标记为 **已接受 (Won't Fix)**。

---

## 🟢 低危 — 遗留/新发现 (3)

### L1: 日期规范化代码已提取

[quote_dao.py:62-78](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/persistence/daos/quote_dao.py#L62-L78) 已提取 `_normalize_trade_date()` 工具函数。✅

### L-NEW-1: `historical.py` 中 `date.strftime` 可能对 `str` 类型失败

**文件**: [historical.py:296](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/historical.py#L296)

```python
date.strftime("%Y%m%d")
```

虽然 `trade_dates` 通常来自交易日历返回的 `datetime.date`，但如果 `get_trade_dates()` 返回字符串列表，`strftime` 会抛出 `AttributeError`。目前有 `normalize_date()` 辅助函数保证了大部分路径安全，但主循环中的 `sync_one_day(date)` 直接传入了原始 `date`。

**风险等级**: 低。交易日历契约保证返回 `datetime.date`。

### L-NEW-2: `SyncResult.merge()` 中 normalize 两次自身

**文件**: [base.py:68-73](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/base.py#L68-L73)

```python
self.quality_scores = {
    normalize_date_key(k): v for k, v in self.quality_scores.items()
}
```

每次 merge 时会重新 normalize `self` 的所有 key。如果已经是 `datetime.date` 类型，这是纯冗余操作。在 scores 字典很大时有 O(n) 开销。

**风险等级**: 低。实际场景中 merge 次数有限。

---

## 📊 修复状态总览

| 严重性 | 上轮数量 | 已修复 | 新增 | 当前剩余 |
|:------:|:--------:|:------:|:----:|:--------:|
| 🔴 致命 | 3 | 3 | **2** | **2** |
| 🟠 高危 | 5 | 5 | 0 | 0 |
| 🟡 中危 | 6 | 5 | 0 | 0 |
| 🟢 低危 | 4 | 2 | 2 | 3 |
| **合计** | **18** | **15** | **4** | **5** |

---

## 🚨 必须立即修复

```
阻塞级 (不修复则上线后必定出错):
├── C-NEW-1: CacheManager 缺少 check_table_has_data() → Prompt 声明校验全线 crash
└── C-NEW-2: SQL 引用 stock_sync_status.table_name 列不存在 → 财务完整性检查静默失效
```

> [!IMPORTANT]
> 这两个新发现的致命问题都是上一轮修复引入的**回归 bug**。C-NEW-1 是 M6 修复不完整（改了调用方没改被调用方），C-NEW-2 是 C1 修复过度（透传了表结构不支持的参数）。
>
> 建议修复后运行以下验证：
> ```bash
> # 验证 prompt_validator 可以成功 import
> python -c "from strategies.prompt_validator import DECLARATIONS; print(f'OK: {len(DECLARATIONS)} declarations')"
> 
> # 验证 SQL 不报错（需要数据库连接）
> pytest tests/test_historical_sync_integrity.py -k "incomplete_financial" -v
> ```
