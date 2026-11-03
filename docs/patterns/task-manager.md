# TaskManager 任务生命周期

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.3（单例）、§3.2（ThreadPoolManager 强制）；实现细则见本节。

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
      ↘        ↘ INTERRUPTED (应用异常退出：残留 RUNNING / QUEUED 启动时回填)
```

- 任务通过 `submit_task()` 提交，`name` 与 `task_type` 为必填位置参数，`coroutine_factory` 是接收 `task_id` 关键字参数（并可透传 `**kwargs`）的可调用对象（返回 coroutine），可选参数含 `cancellable` / `unique_key` / `factory_key` / `exclusive_group`。`unique_key` 承载任务去重语义：同一 key 重复提交会被同步拦截并返回 `None`，任务结束（无论成功/失败/取消）后 key 释放可重新提交。最小调用样例：

  ```python
  task_id = task_manager.submit_task(
      name="daily_sync",
      task_type="sync",
      coroutine_factory=lambda task_id: do_daily_sync(task_id),
      unique_key="daily_sync",
  )
  # submit_task 返回 str | None：unique_key 冲突（重复提交已被同步拦截）或无事件循环时返回 None
  if task_id is None:
      logger.info("[daily_sync] skipped: duplicate unique_key or no event loop")
      return
  ```
- `unique_key` 冲突时 `submit_task` 返回 `None`（见 `services/task_manager.py::submit_task`），调用方必须判空，避免对 `None` 继续 `update_progress` / 挂回调。
- **共享互斥分组 `exclusive_group`（D7-5/MINOR-03）**：为「写同一批行情表」的任务（`daily_sync` / `daily_sync_catchup` / `system_init_sync`）传入共享分组名常量 `EXCLUSIVE_GROUP_MARKET_SYNC`（`services/task_manager.py`），同组任务串行执行、跨组与未分组任务不受影响。语义与边界：
  - 组锁为 **loop-local**（经 `utils/loop_local.py::get_loop_local` 获取，R11：不得作为类属性跨循环复用），在并发信号量**外层**获取——同组排队任务不占用并发许可，避免挤占其它任务的可运行名额；
  - 组锁在任务正常结束、抛异常、被取消、停机（`cancel_all_running_async`）四条路径均经 `async with` 释放；`reload_config` 只重置信号量（`task_manager_semaphore`）、**刻意不重置组锁**（否则出现「旧锁持有者 + 新锁持有者并发」）；`_reset_singleton` 会随单例重置一并清理组锁；
  - `exclusive_group` **仅进程内有效、不持久化**（无 DB 列）：崩溃重启后经 `retry_task` 重建的任务会丢失组归属，此为该能力的已知边界；
  - 看门狗（`SchedulerService._watch_config_changes`）在同组任务运行/排队中或 `cache_clear` 运行期间跳过本轮补偿检查（下个周期重判）；misfire 路径不加此守卫。
- 使用 `update_progress(progress)` 报告进度 (0.0-1.0)，内置节流避免 UI 风暴
- 工作协程内部使用 `is_cancelled()` 检测取消信号 (用户主动取消 / 应用退出)
- 任务持久化到数据库（`task_history` 表，经 `services/task_manager.py::_persist_task` 异步写库）；应用重启时 `init_db()` 会把上次会话遗留的 `RUNNING` 与 `QUEUED` 任务统一回填为 `INTERRUPTED`，用户可经 `retry_task` 重新发起

> **操作指引**：TaskManager 后台任务的完整触发与编排方式见 [docs/guides/how-to.md](../guides/how-to.md) 与 [data-sync.md](./data-sync.md) 的落地路径；新增异步任务须遵循 CLAUDE.md §3.1 R2（取消传播）。

## 崩溃重试契约（D11-Q01 固化）

> 本节把历史待确认问题 D11-Q01 固化为显式契约：**只描述现状，不扩大 schema、不改变行为**。符号均指 `services/task_manager.py` 中的实现。

**持久化内容**（`_persist_task` / `_persist_task_async` → `_persist_snapshot`，DDL 见 `alembic/versions/0025_add_crash_retry_columns.py`、模型见 `data/persistence/models.py::TaskHistory`）：

- `factory_key`：可重建工厂的注册键（经 `register_retryable_factory` 登记），重启后按键从注册表回填 `_coroutine_factory`；未命中注册表则保持不可重试；
- `retry_kwargs`：`submit_task(**kwargs)` 的原始关键字参数经 `_serialize_retry_kwargs` 做 `json.dumps` 序列化，**仅支持 JSON 标量/容器**（字符串、数值、布尔、null、list、dict，tuple 退化为 list）。仅当 `factory_key` 非空才写入；不可序列化时写 NULL，重启后该任务不可重试（进程内仍可重试，因内存中保留 factory 与 kwargs）；
- `unique_key`：原始去重键，重试时复用原键，使重试任务与其它提交路径共享同一去重占位（`retry_task`，LIFE-01）；
- `status` / `progress` / `description`：任务状态与展示信息（`Message` 经 `_serialize_msg_field` 持久化）；另持久化 `name` / `task_type` / `error` / `result` 与时间戳，供历史面板展示。

**不持久化内容**（无对应 DB 列，重启后不可恢复）：

- `exclusive_group`：组归属仅进程内有效。`init_db` 回载的历史任务 `exclusive_group=None`，经 `retry_task` 重建的任务**不受原组约束**，业务须在提交侧自行去重/互斥；同一进程内重试（任务仍在内存）会由 `retry_task` 透传原组名，组语义保持到重启为止（见上文 D7-5 条目）。
- `cancellable`：`AppTask` 默认 `False`，`init_db` 构造历史任务时不回填，故重启回载的任务恒为 `False`；同一进程内重试则透传原内存值。
- 原配置快照（数据源/同步等运行期配置）与 DB 目标/引擎指纹：均不持久化。

**重试语义**：

- `retry_task` 用持久化的 `factory_key` 查回注册工厂，并以持久化的原 `retry_kwargs` + **当前配置 / 当前 DB 目标**重新执行；内置重建工厂见 `services/task_rebuild.py`，运行期读取当前单例（如 `DataProcessor`）。
- 因此重启后配置或 DB 目标发生变化，会改变重试任务的实际行为与写入目标——这是**有意契约**（避免按陈旧配置重放而与当前用户设置不一致）。
- 可重试状态仅 `FAILED` / `INTERRUPTED`（`_RETRYABLE_STATUSES`）；`COMPLETED` / `CANCELLED` 不可重试（`CANCELLED` 代表用户主动放弃意图）。`is_retryable` 还要求已回填可执行工厂，否则 UI 不展示重试入口。

**变更门槛**：新增持久化字段（如组归属、`cancellable`、配置快照）必须同时提供 DB schema 迁移与产品决策，不得静默扩大本契约。

**失败可观察性**：重试经 `submit_task` 创建**新的 task_id**，原历史行保留（状态/错误/进度不被覆盖），用户可在任务中心同时看到失败记录与重试任务；任务不存在、状态不可重试或无 factory 时 `retry_task` 返回 `None` 并记录警告，`unique_key` 仍被其它活动任务持有或无事件循环时则由 `submit_task` 返回 `None`（去重契约优先）。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」；TaskManager 逻辑改动后须 `redline-check`（R2/R11/R16）+ 相关单测。

- 任务状态机（`QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED`、重启时 `RUNNING`/`QUEUED` → `INTERRUPTED` 回填）与 `submit_task()` 签名、`unique_key` 去重语义符合本文档；
- 协程内同步阻塞经 `ThreadPoolManager.run_async()` 提交（R16）；取消信号用 `threading.Event`（非 `asyncio.Event` 类属性，R11）；
- 编排边界正确：`TaskManager`（任务）/ `ThreadPoolManager`（同步阻塞）/ `SchedulerService`（定时）各司其职；
- 门禁：`redline-check`、`ruff`、相关单测通过。
