# 应用服务层（application-service）canonical 正本

> **主题**：`application-service`（CLAUDE.md §1.8 决策树与 canonical-topics.yml 登记的「新增/修改应用服务」入口）
> **路由**：新增/修改应用服务相关代码（`services/` 下的非 AI 服务、`utils/scheduler_service.py`、`utils/thread_pool.py` 等跨层编排落点）时必读本文档。
> **性质**：本文档为**引用为主的汇编正本**。应用服务层的权威约束分散在既有正本（基线红线、单例生命周期、TaskManager 生命周期、错误处理标准模式、例外注册表、开发工作流），此处只做索引、组装与判定边界，**不复写权威细节，避免第二正本**。

## 必读（改动前通读）

1. **分层与取消传播基线**：CLAUDE.md §3.1 R1（架构越界，`services/` 不得导入 `strategies/` / `ui/`）、R2（取消传播，`asyncio.CancelledError` 必须 `raise`）、R5（僵尸引擎操作，服务层轮询/回测例外见下方 EX）。
2. **单例 vs 非单例判定**：见 [singleton-lifecycle.md](../architecture/singleton-lifecycle.md) §「非单例服务」——`TaskManager` 注册单例（后台任务编排，`services/task_manager.py`），`BacktestService` 非单例（按需实例化、依赖注入 `CacheManager` + `engine_factory` + `strategy_lookup`，无全局共享状态）。
3. **TaskManager 生命周期**：[task-manager.md](./task-manager.md)。任务状态机 `QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED`、`submit_task()` 签名、`unique_key` 去重、持久化与 `INTERRUPTED` 回填。

## 条件触发（按改动类型定位）

### 1. 单例 vs 非单例的判定标准（升格显式）

- 权威表：`singleton-lifecycle.md` 的「注册单例」与「非单例服务」两张表。
- **判据**：若服务持有全局共享状态、需跨调用点共享上下文、或作为资源生命周期所有者，用 `@register_singleton` + `_reset_singleton`（R15）；若每次调用按需实例化、依赖通过构造器注入、无全局共享状态，则不注册单例（如 `BacktestService`）。
- **新增服务时**：先判定再实现；不确定时优先非单例 + 依赖注入（更易测试隔离，R7）。改单例注册须同步 `singleton-lifecycle.md` 表格与按 CLAUDE.md §4.3。

### 2. 轮询 / 长生命周期任务的停止契约

- **正本**：[exceptions.yml 的 EX-0017](../governance/exceptions.yml)（`R5` 豁免）——`news_subscription_service.py` 的 `_safe_fetch_task` / `_processing_loop` / `_fetch_and_notify` 三处吞没 `EngineDisposedError` 以停止轮询是**合理设计**（引擎释放后停止轮询），非 DAO/维护流程的传播要求。
- **含义**：应用服务层的轮询循环优雅停止不在 R5 之内，**不必**在停机路径传播 `EngineDisposedError`；但非轮询的 DAO/维护流程仍须传播。新增轮询服务时复用此豁免语义，**不要**对既有停轮询吞没行为报违规或"好心"修复。
- 关联技术债：`P3-M9-NewsSubscription-EngineDisposed-Swallowed`（见 [known-technical-debt.md](../debt/known-technical-debt.md)）。
- **回测持久化例外**：[exceptions.yml 的 EX-0018](../governance/exceptions.yml)（`R5` 豁免）——`backtest_service.py` 的 `_persist_result` 捕获持久化失败（含 `EngineDisposedError`）后在 `data_warnings` 追加 `persist_failed: ...` 并返回结果（**不 raise**）是**合理设计**（回测已完成，持久化失败仅告警）；**不要**将其报为 R5 违规或改为 raise。
- 关联技术债：`P3-M9-Backtest-EngineDisposed-Warning-Return`（见 [known-technical-debt.md](../debt/known-technical-debt.md)）。

### 3. TaskManager / SchedulerService / ThreadPoolManager 编排边界

- **关键澄清**（[how-to.md](../guides/how-to.md) 第 4 条第 10 步）：`TaskManager.submit_task()` 是**异步任务编排层，不替代线程池提交**；协程内部的同步阻塞仍须经 `ThreadPoolManager.run_async()`。
- `ThreadPoolManager`（`utils/thread_pool.py`）提供 `TaskType.IO` / `TaskType.CPU`；`TaskType.CPU` 仅适用于释放 GIL 的密集计算（Polars/NumPy），纯 Python 密集循环须改 Polars 向量化（见 [config-quality-perf.md](./config-quality-perf.md) §线程池任务类型与计算适用判据）。
- `SchedulerService`（`utils/scheduler_service.py`）承载定时编排，与 `TaskManager` 的分工见其模块与 how-to 落地路径。
- **判定**：外部动作/定时触发用 `TaskManager`；协程内同步阻塞用 `ThreadPoolManager.run_async()`；周期性定时用 `SchedulerService`。

### 4. 服务层的错误分类与降级边界

- **正本**：CONTRIBUTING.md「错误处理标准模式」——`classify_error` / `classify_severity`；`system` 级上抛，`recoverable` / `operational` 层内处理。
- **止于服务层**：可恢复/操作级错误服务层内降级（`return` 兜底、`warning` 日志），不上抛 UI；
- **上抛 UI 的两条路径**：① **ViewModel 路径**——VM 用 `utils/error_classifier.py` 的 `get_error_message_key()` 把 `classify_error` 的 `message_key` 产出为 `core.i18n.Message`（i18n key，不感知 locale，对应 MVVM 契约，见 [mvvm.md](./mvvm.md)）；② **View 路径**——View 侧才用 `I18n.get` / `get_error_message` 做翻译。AI 改 VM 不得直接用 `get_error_message`（产出字符串会污染 state 的 locale 无关性）。
- 服务层异常分类决定是否影响用户界面，改动异常边界时按此判定，不回写 UI 错误展示逻辑。

### 5. 路由与工作流

完整落地流程（新增/修改应用服务如何接线到启动、调度）：见 [docs/guides/how-to.md](../guides/how-to.md)「11. 新增一个应用服务」与 [data-sync.md](./data-sync.md)。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」对应最小子集；服务层逻辑改动后须 `redline-check` + 相关服务单测 + `python scripts/check_docs_consistency.py`。

- 新增服务已按「单例 vs 非单例」判据选型，单例注册同步更新 `singleton-lifecycle.md` / §1.8 所需登记；
- 轮询停止 / 回测持久化失败按 EX-0017 / EX-0018 豁免语义处理，未误报停轮询吞没或回测持久化不 raise 行为；
- 编排边界正确：`TaskManager`（任务）/ `ThreadPoolManager`（同步阻塞）/ `SchedulerService`（定时）各司其职，协程内同步阻塞经 `run_async()`；
- 异常分类遵循「错误处理标准模式」：system 上抛、可恢复/操作级层内降级；
- 门禁：`redline-check`（R1/R2/R5/R15）、`ruff`、相关单测通过。