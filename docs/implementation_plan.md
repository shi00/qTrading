# 修复 financial_reports 多次独立写入导致缺列 WARNING

## 问题背景

全量同步路径 (`_run_full_sync` → `process_one_stock`) 将 4 个 API（income / balancesheet / cashflow / fina_indicator）的返回结果**分别独立** upsert 到 `financial_reports` 表，每次写入时 DataFrame 中缺少其他 API 负责的列，触发大量 WARNING 日志。

此外，`_fetch_comprehensive_financial_data` 方法（增量路径使用）虽然正确地先合并再写入，但**遗漏了 cashflow API 的调用**，导致 `n_cashflow_act` 字段在增量同步路径下永远为 NULL。

## 涉及两个独立问题

| # | 问题 | 影响路径 | 严重度 |
|---|---|---|---|
| 1 | `process_one_stock` 4个API各自独立写入 | 全量同步 | 中（数据最终正确但浪费4倍IO+日志噪音） |
| 2 | `_fetch_comprehensive_financial_data` 缺少 cashflow | 增量同步 + repair | 高（`n_cashflow_act` 永远为NULL） |

## Proposed Changes

### FinancialSyncStrategy

#### [MODIFY] [financial.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/sync/financial.py)

##### 修改 1：`_fetch_comprehensive_financial_data` 增加 cashflow 获取

在 `fetch_indicator` 之后新增 `fetch_cashflow`，并将其加入 `asyncio.gather` 和后续 merge 流程。

**变更点：L654-L773**

```diff
 # P0-4: directly await async API methods
 async def fetch_income():
     ...

 async def fetch_balance():
     ...

 async def fetch_indicator():
     ...

+async def fetch_cashflow():
+    return await api.get_cashflow(
+        ts_code=ts_code,
+        start_date=start_date,
+        end_date=end_date,
+        period=period,
+    )
+
 async def fetch_aux(api_func, save_func, **kwargs) -> int:
     ...

 # Parallel fetch core + aux
 results = await asyncio.gather(
     fetch_income(),
     fetch_balance(),
     fetch_indicator(),
+    fetch_cashflow(),
     *aux_tasks,
     return_exceptions=True,
 )

 # Unpack Core Results
-# results[0-2] are core, results[3-4] are aux (row_counts)
-df_inc, df_bal, df_fina = results[0], results[1], results[2]
+# results[0-3] are core, results[4-5] are aux (row_counts)
+df_inc, df_bal, df_fina, df_cf = results[0], results[1], results[2], results[3]

 # Return aux counts as dict
 aux_counts = {
-    "mainbz": results[3] if isinstance(results[3], int) else 0,
-    "audit": results[4] if isinstance(results[4], int) else 0,
+    "mainbz": results[4] if isinstance(results[4], int) else 0,
+    "audit": results[5] if isinstance(results[5], int) else 0,
 }

 # Core Financial Merging
 dfs = []
 if isinstance(df_inc, pd.DataFrame) and not df_inc.empty:
     ...
 if isinstance(df_bal, pd.DataFrame) and not df_bal.empty:
     ...
 if isinstance(df_fina, pd.DataFrame) and not df_fina.empty:
     ...
+if isinstance(df_cf, pd.DataFrame) and not df_cf.empty:
+    dfs.append(
+        df_cf.sort_values("end_date").drop_duplicates(
+            subset=["end_date"],
+            keep="last",
+        ),
+    )
```

同时更新方法的 docstring：

```diff
-Helper: Fetch and merge Income, Balance Sheet, and Financial Indicators.
+Helper: Fetch and merge Income, Balance Sheet, Cashflow, and Financial Indicators.
```

##### 修改 2：`process_one_stock` 复用 `_fetch_comprehensive_financial_data`

将 `task_specs` 中前 4 项（income/balancesheet/cashflow/indicator 各自独立获取+保存）替换为调用 `_fetch_comprehensive_financial_data` 先合并再一次性保存。

**变更点：L232-L338**

核心逻辑替换如下：

```diff
 async def process_one_stock(ts_code):
     nonlocal completed_count, total_mainbz_rows, total_audit_rows
     if self._shutdown_event.is_set():
         return

     processed = False
     try:
         async with semaphore:
             if self._shutdown_event.is_set():
                 return

             processed = True
             has_error = False

-            # Fetch Helper
-            async def fetch_safe(func, kwargs):
-                ...
-
-            # Task Specs
-            task_specs = [
-                (self.context.api.get_income, self.context.cache.save_financial_reports, 0),
-                (self.context.api.get_balancesheet, self.context.cache.save_financial_reports, 0),
-                (self.context.api.get_cashflow, self.context.cache.save_financial_reports, 0),
-                (self.context.api.get_fina_indicator, self.context.cache.save_financial_reports, 0),
-                (self.context.api.get_fina_audit, self.context.cache.save_fina_audit, 0),
-                (self.context.api.get_fina_mainbz, self.context.cache.save_fina_mainbz, 0),
-            ]
-
-            futures = []
-            for fetch_func, _, arg_type in task_specs:
-                kw = {"ts_code": ts_code}
-                if arg_type == 0:
-                    kw.update(start_date=start_date, end_date=end_date)
-                ...
-                futures.append(fetch_safe(fetch_func, kw))
-
-            results = await asyncio.gather(*futures)
-
-            # Save Results
-            for i, result_data in enumerate(results):
-                if result_data is not None:
-                    save_func = task_specs[i][1]
-                    row_count = await save_func(result_data)
-                    ...
+            try:
+                df_merged, aux_counts = await self._fetch_comprehensive_financial_data(
+                    ts_code,
+                    start_date=start_date,
+                    end_date=end_date,
+                )
+
+                total_mainbz_rows += aux_counts["mainbz"]
+                total_audit_rows += aux_counts["audit"]
+
+                if df_merged is not None and not df_merged.empty:
+                    # 补齐缺失列
+                    for col in FINANCIAL_REPORT_SCHEMA_COLS:
+                        if col not in df_merged.columns:
+                            df_merged[col] = None
+
+                    await self.context.cache.save_financial_reports(
+                        df_merged[FINANCIAL_REPORT_SCHEMA_COLS],
+                    )
+
+            except (AttributeError, NameError, TypeError, ImportError):
+                raise  # Critical errors
+            except Exception as e:
+                has_error = True
+                logger.warning(
+                    f"[FinancialSync] StockSync | ⚠️ Failed for {ts_code}: {e}",
+                )

             if not has_error:
                 await self.context.cache.mark_stock_step4_completed(...)
                 result_accumulator.added += 1
```

> [!IMPORTANT]
> 修改 2 需要在文件头部新增 `FINANCIAL_REPORT_SCHEMA_COLS` 的 import（该 import 当前已存在于 L13）。

## 需要额外添加的 import

**无**。`FINANCIAL_REPORT_SCHEMA_COLS` 已在 L13 导入，`_fetch_comprehensive_financial_data` 是当前类的方法。

## Open Questions

> [!WARNING]
> **`get_cashflow` 的 API 权限问题**：Tushare cashflow 接口是否有独立的积分/频次限制？如果有，需要在 `_fetch_comprehensive_financial_data` 的 `fetch_cashflow` 中增加异常容忍（目前 income/balance/indicator 的异常会导致整个 gather 走 `return_exceptions=True` 分支，已有保护）。请确认你的 Tushare 积分等级是否允许 cashflow 接口调用。

## Verification Plan

### Automated Tests

1. 运行现有单元测试：
   ```bash
   python -m pytest tests/test_data_processor.py::TestDataProcessor::test_sync_financial_reports -v
   ```

2. 回归验证：检查修改后是否仍然能正确同步并写入完整的 21 列数据。

### Manual Verification

1. **日志验证**：修改后运行增量/全量同步，确认不再出现 `Missing columns in dataframe` 的 WARNING
2. **数据验证**：查询 `financial_reports` 表确认 `n_cashflow_act` 列有值（之前增量路径下为全 NULL）
   ```sql
   SELECT COUNT(*) FROM financial_reports WHERE n_cashflow_act IS NOT NULL;
   ```
