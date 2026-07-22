# 数据同步架构

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.1（data 分层）、§3.1 R2（取消传播红线）；实现架构见本节。

`data/sync/` 下按数据类别组织同步策略：

- `base.py` — 同步基础定义 (`SyncContext` 依赖注入容器、`SyncResult` 结果数据类、`ISyncStrategy` 策略接口，含取消支持)
- `historical.py` — 历史行情同步
- `financial.py` — 财务报告同步
- `holder.py` — 股东数据同步
- `macro.py` — 宏观数据同步

所有同步通过 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` 注册表驱动，包含表结构、同步配置、质量监控配置。

## Tushare Syncer 设计模式

`data/sync/` 下所有 syncer 通过 `TushareClient` 单例（见 [singleton-lifecycle.md](../architecture/singleton-lifecycle.md#单例模式实现模板)）拉取数据，统一遵循以下设计模式：

### 数据流向

```
Tushare API  →  TushareClient（限流 + 重试 + token 熔断）
            →  ISyncStrategy.sync()（断点续传 + 分块）
            →  BaseDao._save_upsert()（批量 upsert）
            →  quality_gate（数据质量评分）
```

### 限流与重试（C5）

- `TushareClient` 内置 `TokenBucket` 限流器，按积分档位（120/2000/5000/10000/15000）区分 QPS 上限，配置见 `data/constants.py` 的 `TUSHARE_POINT_TIERS`。
- 网络错误与限流错误自动重试（指数退避 + jitter），重试上限由 `TushareClient` 配置控制；超阈值后通过 `classify_error()` 分类并触发慢操作告警。
- 外部 IO 方法挂 `@log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` 触发性能监控。

### 质量门控（C15）

- syncer 写入前必须经过 `@require_quality(QualityTier.X)` 装饰器指定所需质量等级；普通策略用装饰器，向量化 `PolarsBaseStrategy` 通过类属性 `required_quality_tier` 覆盖默认等级。
- 同步完成后由 `QuoteDAO.get_sync_quality_score()` 评估单日数据同步质量分数（基于相对基准法），低于阈值时标记该日为不完整，下次同步会自动补齐。
- 跨源一致性校验（Tier 3 Gold）由 `data/persistence/data_quality.py` 与 `quality_gate.py` 负责，详情见 [config-quality-perf.md](./config-quality-perf.md)。

### 错误处理（C16）

- 所有 syncer 的 `except` 块必须遵循 CLAUDE.md §3.1 R2：`except asyncio.CancelledError: raise`，禁止吞没取消异常。
- `TushareAPIPermissionError` 由 syncer 捕获并跳过对应 API（更新 UI capability 指示器），不阻塞其他 API 同步。
- token 认证失败触发全局熔断：`_token_invalid` 标志置 True 后所有 API 调用 fast-fail，避免无效 token 下每个 API 独立重试刷屏。`set_token()` 重置标志恢复。
  - 该熔断标志的跨路径同步问题见 [known-technical-debt.md](../debt/known-technical-debt.md) P3-Tushare-Token-Invalid-Race。
- 外部 IO 异常必须经 `classify_error(e, context="general")` 分类后按严重度选择日志级别；敏感数据（token/密码）必须经 `DataSanitizer` 脱敏。

### 取消传播（C18）

- `SyncContext.cancel_event` 作为依赖注入容器传递到 syncer，syncer 在分块循环中检查 `cancel_event.is_set()` 主动退出。
- syncer 主动退出时必须 `raise asyncio.CancelledError`（或让其向上传播），由 `TaskManager` 统一处理任务状态转换。
- `ThreadPoolManager.run_async()` 包装的同步阻塞段也需响应取消（通过 `cancel_event` 协作式取消，非强制 kill）。
