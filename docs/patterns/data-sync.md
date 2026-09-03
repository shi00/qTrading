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

- **`data/sync` 层豁免说明**：`data/sync/` 作为数据同步入口，职责是拉取外部数据并落库（写入 `quality_gate` 评分所需的源数据），自身不直接消费业务数据决策；下游策略层（`strategies/`）强制 `@require_quality` 门控，故 `data/sync` 层不声明 `required_quality_tier`、不挂 `@require_quality` 装饰器。质量评分由 `QuoteDAO.get_sync_quality_score()` 在 syncer 落库后异步评估，syncer 仅负责把数据写入 `*_quality_score` 表供后续策略消费。
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

### 数据库连接生命周期（review03-C13 契约）

**断连后必须显式 `CacheManager.init_db()`**：若 PostgreSQL 进程整体不可用（内置 PG 崩溃、外部 PG 重启），连接池的 `pool_pre_ping=True` 只能处理"连接失效"（池内单个连接被服务端关闭），**无法**处理"数据库进程不可用"。恢复后必须由调用方显式调用 `CacheManager.init_db()` 重建引擎与连接池。

- **不实现自动重连**：自动重连会引入重连风暴风险，且会掩盖真实故障（PG 崩溃是异常事件，应让用户看到明确错误并触发诊断流程）。
- **失败表现**：断连期间 DAO 操作抛 `EngineDisposedError`（R5）或连接池超时异常，经 `classify_error` 分类后按严重度记录；不应静默吞没。
- **UI 提示映射**：连接失败/`EngineDisposedError` 应映射为可操作用户提示（如"数据库连接已断开，点击重新连接"），而非通用错误——该映射属错误反馈路径（报告 05 范围）。

> **操作指引**：新增/修改数据同步源的完整步骤见 [docs/guides/how-to.md](../guides/how-to.md)「5. 新增一个外部数据源」与「5.1 Tushare 集成工作流（简述）」；新增同步表前须更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS`，并遵循本节 CLAUDE.md §3.1 R2（取消传播）与 §3.2（质量门控）约束。
