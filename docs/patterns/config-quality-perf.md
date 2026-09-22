# 配置管理、质量门控、性能监控

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §3.2（质量门控、`@log_async_operation` 强制）与 §1.5（目标驱动与验证）；实现细则见本节。

### 配置管理

`ConfigHandler` 使用读写锁 (`rwlock.RWLockFair`) 保护并发访问。敏感信息优先使用 `keyring`，降级到 AES-GCM 加密文件 (`utils/security_utils.py`)。

### 数据质量门控

使用 `@require_quality(QualityTier.SILVER)` 确保只有数据质量达标才执行逻辑。质量分层: `CRITICAL(0)` → `BRONZE(1)` → `SILVER(2)` → `GOLD(3)`。`STRICT_QUALITY_GATE` 环境变量控制严格模式（默认开启，设为 `false` 关闭）。

**区间完整性门控（`require_continuous_window=True`，D2-9）**：仅当策略声明逻辑需要**连续数据窗口**（如基于 MA/RSI 等滚动窗口指标、且窗口内任一天缺失都会使结果失真）时，在 `@require_quality` 上追加该参数。语义：等级达标后再做区间完整性检查——依据 `processor._scan_missing_dates`（`run_quality_scan` 采样的缺失交易日代理证据），若采样检测到任何缺失交易日，即便等级达标也抛 `QualityGateError` 拦截，防止「策略所需窗口恰好缺某天」仍放行。默认 `False`，不改变既有策略行为。定义见 `data/persistence/quality_gate.py`（`require_quality` 的 `require_continuous_window` 关键字参数）；真实引用点：`strategies/oversold_strategy.py` 的 `async def filter` 上 `@require_quality(QualityTier.SILVER, require_continuous_window=True)`（配合 `required_tables=("daily_quotes",)` 的 per-table 门控）。向量化 `PolarsBaseStrategy` 走 `required_quality_tier` 类属性，不适用本参数。

**`data/sync/` 层豁免说明**：`data/sync/` 作为数据同步入口，负责从外部 API（Tushare / AKShare 等）拉取并写入原始数据，是质量门控的数据**来源**而非消费方；下游 `strategies/` 与 `services/` 强制通过 `@require_quality` / `required_quality_tier` 声明所需质量等级后再消费 `data/sync/` 产出的数据。因此 `data/sync/` 层不声明 `required_quality_tier`，避免数据生产者自我断言其产出质量造成职责混淆。

### 性能监控装饰器

`utils/log_decorators.py` 提供：

- `@log_async_operation(operation_name="fetch_data", threshold_ms=500)` — 异步操作日志 + 性能监控 + 自动脱敏
- `@track_performance(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)` — 纯性能追踪 (轻量)
- `@log_ui_action(component_name="Settings", action_type="Click")` — UI 交互埋点
- `AsyncOperationLogger` — 复杂流程分段日志上下文管理器
- **取舍**: 同一函数只挂一个性能装饰器，优先选 `@log_async_operation` (功能更完整)。

**标准性能红线 (`PerfThreshold`)**: 具体数值见 `utils/log_decorators.py`，涵盖内存计算/DB单查询/外部网络/DB批量IO/AI推理/全局初始化六类场景。

### 线程池任务类型与计算适用判据 (CON-16)

`ThreadPoolManager` 提供 `TaskType.IO` 与 `TaskType.CPU`：
- `TaskType.IO`：网络、数据库、磁盘等阻塞 IO 操作（高并发，由连接池与背压预算硬钳制，见 `utils/config/db.py::get_max_io_workers()`）。
- `TaskType.CPU`：**仅适用于释放 Python GIL 的密集计算**（如 Polars 表达式、NumPy 数组计算、C 扩展等）。
- **纯 Python 密集循环**：纯 Python 循环在线程池内会产生严重 GIL 争抢，不仅无法利用多核，还会争抢事件循环线程导致 UI 卡顿。桌面应用模型下不引入多进程池（避免进程间序列化开销与打包复杂度）；**所有 CPU 密集型计算必须改用 Polars 向量化表达**。

### 金额/数量列阈值比较（R20）

调整或新增涉及金额、数量列（`north_money` / `net_amount` / `amount` / `total_mv` / `circ_mv` / `vol`）的
阈值、筛选或比较时，必须经 `threshold_in_data_unit()`（`strategies/utils.py`）统一单位换算后再比较；
各列数据单位声明见 `data/constants.py` 的 `HSGT_COLUMN_UNITS` / `TOP_LIST_COLUMN_UNITS`，并按需用
`get_column_unit()` / `get_column_unit_source()` 显式取单位。直接按字面数值比较会因单位差（如北向
资金以万元计）产生量纲偏差，违反 R20（见 CLAUDE.md §3.1）。

> **操作指引**：修改配置项或调整性能相关阈值，入口见 [CLAUDE.md §1.8 决策树](../../CLAUDE.md#18-任务类型--必读文件-决策树)（「修改配置项 / 性能优化 / 阈值调整」均路由到本节）；完整新增策略/DAO 的落地流程见 [docs/guides/how-to.md](../guides/how-to.md)。

---

## 完成判定（canonical 入口）

- 性能阈值/配置项改动已同步 `data/constants.py` 单位事实源与本文档描述（含 R20 量纲比较指针）
- 新增配置项已评估质量门控影响，未绕开 `@require_quality` 等既有门控
- 性能监控装饰器与线程池任务类型判据（CON-16）已对照

_最小验证命令：_ 改动 `data/`/`strategies/` → ruff + pyright + `tests/unit/` 对应用例；
        文档改动 → `python scripts/check_docs_consistency.py`。
