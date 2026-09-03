# TaskManager 任务生命周期

> 来源：从 CONTRIBUTING.md 迁移

> 宪法依据：CLAUDE.md §4.3（单例）、§3.2（ThreadPoolManager 强制）；实现细则见本节。

```text
QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED
                 ↘ INTERRUPTED (应用异常退出)
```

- 任务通过 `submit_task()` 提交，传入 `coroutine_factory` (无参可调用对象，返回 coroutine)
- 使用 `update_progress(progress)` 报告进度 (0.0-1.0)，内置节流避免 UI 风暴
- 工作协程内部使用 `is_cancelled()` 检测取消信号 (用户主动取消 / 应用退出)
- 任务持久化到本地，重启后 `RUNNING` 状态会被回填为 `INTERRUPTED`

> **操作指引**：TaskManager 后台任务的完整触发与编排方式见 [docs/guides/how-to.md](../guides/how-to.md) 与 [data-sync.md](./data-sync.md) 的落地路径；新增异步任务须遵循 CLAUDE.md §3.1 R2（取消传播）。
