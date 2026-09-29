# TaskManager 任务生命周期

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.3（单例）、§3.2（ThreadPoolManager 强制）；实现细则见本节。

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
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
- 任务持久化到本地，重启后 `RUNNING` 状态会被回填为 `INTERRUPTED`

> **操作指引**：TaskManager 后台任务的完整触发与编排方式见 [docs/guides/how-to.md](../guides/how-to.md) 与 [data-sync.md](./data-sync.md) 的落地路径；新增异步任务须遵循 CLAUDE.md §3.1 R2（取消传播）。

## 完成判定（canonical 入口）

_最小验证命令：_ 按改动实际触及的层运行 CONTRIBUTING「变更类型 → 最小验证子集」；TaskManager 逻辑改动后须 `redline-check`（R2/R11/R16）+ 相关单测。

- 任务状态机（`QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED`、重启 `RUNNING → INTERRUPTED` 回填）与 `submit_task()` 签名、`unique_key` 去重语义符合本文档；
- 协程内同步阻塞经 `ThreadPoolManager.run_async()` 提交（R16）；取消信号用 `threading.Event`（非 `asyncio.Event` 类属性，R11）；
- 编排边界正确：`TaskManager`（任务）/ `ThreadPoolManager`（同步阻塞）/ `SchedulerService`（定时）各司其职；
- 门禁：`redline-check`、`ruff`、相关单测通过。
