# 第四轮终审报告 — 方案A 全域覆盖终审

> 审计时间：2026-03-18 16:30  
> 审计范围：上轮7个问题复验 + **终极全域扫描**（新增覆盖 `historical.py`/`macro.py`/`holder.py`/`scheduler_service.py`/`screener_dao.py`/`sync_dao.py`）  
> 本轮方法：逆向类型追踪 — 从 DAO 层参数定义反推所有调用点的类型兼容性

---

## 一、上轮修复验证

| # | 问题 | 修复状态 |
|---|------|---------|
| P0-#14 | `financial.py:207-208` strptime 接收 date | ✅ 改用 `parse_date()` |
| P1-#15 | `data_processor.py:309` strptime 接收 datetime | ✅ 加 `isinstance` 防御 |
| P1-#16 | `financial.py:378` 同上 | ✅ 加 `isinstance` 防御 |
| P1-#17 | `data_processor.py:478` date vs str 比较 | ✅ 改用 `now.date()` 比较 |
| P2-#18 | `data_processor.py:354` YYYYMMDD 写入 Date 列 | ✅ 改用 `get_now().date()` |
| P2-#19 | `chart_utils.py:86` 冗余 `.astype(str)` | ✅ 已删除 |
| P2-#20 | `data_processor.py:500/504` 类型不一致 | ✅ 改用原生 date 对象 |

**上轮修复结论：7/7 全票通过** ✅

---

## 二、🆕 新发现问题

### 🟡 P1-#21：`macro.py:134/201` date 对象赋给 `start_date`，后续与 YYYYMMDD 字符串比较

```python
# macro.py:130-134 (_sync_shibor_daily)
all_dates = await self.context.processor.get_trade_dates(...)  # ← 返回 [date, date, ...]
start_date = all_dates[-(250 * years)]   # ← date 对象

# macro.py:159
if start_date > today:  # ← date 对象 > "20230101" 字符串 → TypeError!
```

> [!WARNING]
> **仅在 `latest` 为空（首次初始化）路径触发**。`start_date` 从 `get_trade_dates()` 的返回列表中取出，是原生 `date` 对象；而 `today = get_now().strftime("%Y%m%d")` 是字符串。Python 3 中 `date > str` → `TypeError`。
> 
> 同样的模式存在于 `_sync_index_weights`（L198-211）的首次初始化路径。

**修复**：
```python
today = get_now().date()  # 改用 date 对象
```

---

### 🟡 P1-#22：`financial.py:451` YYYYMMDD 字符串写入 `sync_status.last_data_date`（Date 列）

```python
# financial.py:451
await self.context.cache.update_sync_status("financial_reports", day_str, total_saved)
# day_str = "20230101" (YYYYMMDD 字符串)
```

> [!WARNING]
> `sync_status.last_data_date` 是 `Column(Date)`。`sync_dao.update_sync_status` 通过原始 SQL 的 `$3` 直传到 asyncpg。asyncpg 的 `DATE` 类型参数 **仅接受** `datetime.date` 对象，**不接受** 字符串。
> 
> 同一文件 L513 也有同样问题（`_sync_corporate_actions_by_date` 中传入 `date_str`）。

**修复**：
```python
from datetime import datetime
await self.context.cache.update_sync_status(
    "financial_reports", datetime.strptime(day_str, "%Y%m%d").date(), total_saved
)
```

---

### 🟠 P2-#23：`macro.py:146` `parse_date(str(latest), "%Y%m%d")` 冗余

```python
last_dt = parse_date(str(latest), "%Y%m%d")
```

> [!NOTE]
> `parse_date` 已升级支持直接接收 date 对象。`str(latest)` + 传 `"%Y%m%d"` fmt 参数是多余的。虽然 `parse_date` 内部的自动格式检测会覆盖 `"%Y%m%d"` 参数（识别到 `"-"` 后切换为 `"%Y-%m-%d"`），**功能正确但代码不清晰**。
> 
> 同样存在于 `macro.py:213`。

**修复**（可选）：
```python
last_dt = parse_date(latest)  # 直接传原生对象
```

---

## 三、已审查通过的全部模块清单

| # | 模块 | 行数 | 评估 | 备注 |
|---|------|------|------|------|
| 1 | `models.py` | 598 | ✅ | 27表全覆盖 Date/DateTime |
| 2 | `base_dao.py` | ~280 | ✅ | `_save_upsert` + `_prepare_records` tz剥离完备 |
| 3 | `sync_dao.py` | 58 | ✅ | `.replace(tzinfo=None)` |
| 4 | `stock_dao.py` | ~260 | ✅ | 冗余注入已清理 |
| 5 | `financial_dao.py` | ~200 | ✅ | |
| 6 | `macro_dao.py` | ~70 | ✅ | `.replace(tzinfo=None)` |
| 7 | `quote_dao.py` | ~350 | ✅ | `.astype(str)` 已删除 |
| 8 | `screener_dao.py` | 234 | ✅ | 走 `_save_upsert` 安全 |
| 9 | `cache_manager.py` | ~800 | ✅ | `parse_date` 兜底 |
| 10 | `data_processor.py` | 786 | ✅ | `.date()` 比较已修复 |
| 11 | `data_quality.py` | 181 | ✅ | `.dt.date` 统一比较 |
| 12 | `review_manager.py` | 315 | ✅ | `get_now().date()` |
| 13 | `health_mixin.py` | 592 | ✅ | `parse_date()` 兜底 |
| 14 | `calendar_mixin.py` | 226 | ✅ | `to_date()` 全防御 |
| 15 | `financial.py` (sync) | 732 | 🟡 | L451 需修 |
| 16 | `historical.py` (sync) | 499 | ✅ | date 对象直传 Date 列 |
| 17 | `macro.py` (sync) | 260 | 🟡 | L134/159/201 需修 |
| 18 | `holder.py` (sync) | 197 | ✅ | YYYYMMDD 字符串全程 |
| 19 | `oversold_strategy.py` | 225 | ✅ | isinstance 三分支 |
| 20 | `screener_view.py` | 1416 | ✅ | isinstance + strftime |
| 21 | `data_view.py` | ~1000 | ✅ | isinstance 防御 |
| 22 | `chart_utils.py` | 161 | ✅ | `.astype(str)` 已删 |
| 23 | `tushare_client.py` | 735 | ✅ | strftime 兼容 |
| 24 | `offline_calendar.py` | 80 | ✅ | isinstance 兼容 |
| 25 | `time_utils.py` | 38 | ✅ | `parse_date` 多类型 |
| 26 | `scheduler_service.py` | 393 | ✅ | 仅用 `.strftime` 生成字符串 |

---

## 四、修复优先级排序

| 优先级 | # | 文件:行号 | 工作量 | 影响 |
|--------|---|----------|--------|------|
| 🟡 P1 | #21 | `macro.py:120,159,187,199` | ~4行 | 首次初始化 Shibor/IndexWeight 崩溃 |
| 🟡 P1 | #22 | `financial.py:451,513` | ~2行 | 增量同步 checkpoint 写入报错 |
| 🟠 P2 | #23 | `macro.py:146,213` | ~2行 | 功能正确但代码不清晰 |

---

## 五、累积评分追踪

| 板块 | R1 | R2 | R3 | R4 | 趋势 |
|------|-----|-----|-----|-----|------|
| ORM / 元数据 | 10 | 10 | 10 | 10 | ⬜ |
| DAO 核心 | 9 | 10 | 10 | 10 | ⬜ |
| 子DAO | 4 | 9 | 10 | 10 | ⬜ |
| 策略层 | 10 | 10 | 10 | 10 | ⬜ |
| UI 层 | 7 | 10 | 10 | 10 | ⬜ |
| JSON 序列化 | 10 | 10 | 10 | 10 | ⬜ |
| 健康检查 | — | 3 | 9 | 10 | 🟢 ↑ |
| Review/缓存 | — | 5 | 9 | 10 | 🟢 ↑ |
| 同步策略层 | — | — | 4 | **8** | 🟢 ↑↑ |
| 数据编排层 | — | — | 5 | **9** | 🟢 ↑↑ |
| 调度/服务层 | — | — | — | **10** | 🟢 NEW |

**综合评分：8.7 → 9.3** 🎯

---

## 六、总结

```mermaid
graph LR
    R1["R1: 7/10<br/>DAO+UI重大缺陷"] --> R2["R2: 8.3/10<br/>health/review新域暴露"]
    R2 --> R3["R3: 8.7/10<br/>sync策略+编排层暴露"]
    R3 --> R4["R4: 9.3/10<br/>仅剩3个低优先级"]
    style R4 fill:#26A69A,color:#fff
```

> [!TIP]
> 经过4轮迭代审计，代码库从首轮的多处 P0 崩溃风险提升到目前仅剩 **3个 P1/P2 低优先级问题**。核心 DAO、UI、策略层全部达到 10/10。剩余问题集中在 `macro.py` 和 `financial.py` 的边缘路径，修复总量约 8 行代码。
> 
> **建议**：修复后可认为方案A全面落地完成，进入正常维护阶段。
