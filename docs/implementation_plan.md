# 数据健康检查修复方案

基于代码审计发现的 4 个问题，结合 **Tushare Pro 2100 积分**和系统实际同步流程给出修复方案。

---

## 问题全景与优先级

| # | 问题 | 严重性 | 影响范围 | 优先级 |
|---|---|---|---|---|
| 1 | 深度检查用策略全局最大值代替用户配置 | 🔴 高 | 健康状态误判（黄/红灯） | P0 |
| 2 | TaskManager 去重竞态导致重复任务 | 🟡 中 | 重复 DB 扫描、日志噪音 | P1 |
| 3 | UI 入口缺乏幂等保护 | 🟡 中 | 与 #2 相关联 | P1 |
| 4 | 稀疏表覆盖率日志噪音 | 🟢 低 | 仅影响日志可读性 | P2 |

---

## 修复 1 (P0)：深度检查 — 改用配置基线

### 问题根因

[health_mixin.py:294-318](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/mixins/health_mixin.py#L294-L318) 遍历 `_STRATEGY_REGISTRY` 提取所有策略的 `required_history_days` 的最大值作为 `max_required`。

**关键事实（代码验证）：**

| 策略 | `required_history_days` 定义 |
|---|---|
| `OversoldStrategy` | `ConfigHandler.get_init_history_years() * 250` (动态) |
| `AISelectionStrategy` | `ConfigHandler.get_init_history_years() * 250` (动态) |
| `PolarsBaseStrategy` 子类 (7个) | 继承 `BaseStrategy.required_history_days = 0` (静态) |

当用户配置 `init_history_years=3` 时，`max_required = 3 * 250 = 750`。

但 `health_mixin.py:298` 中通过 `obj = cls()` 实例化策略来读取属性。这里存在一个微妙问题：**`required_history_days` 在 `OversoldStrategy` 和 `AISelectionStrategy` 中被定义为 `@property`**，而 `BaseStrategy` 中是类变量 `int = 0`。

```python
# health_mixin.py:300 — 当前代码
days = obj.required_history_days
if not isinstance(days, property) and isinstance(days, (int, float)):
    max_required = max(max_required, int(days))
```

`isinstance(days, property)` 检查在**实例属性访问后**是多余的 — 通过 `obj.required_history_days` 访问 `@property` 装饰器时，Python 会执行 getter 并返回 `int` 值（不会返回 `property` 对象）。所以这个卫语句永远不会生效，`days` 总是 `int`。

**结论**：如果 `ConfigHandler.get_init_history_years()` 返回 3，那么 `max_required = 750`。用户日志中的 1500 天说明**当时配置可能是 6 年**，或存在其他未提交的策略。

> [!IMPORTANT]
> 无论如何，从架构正确性角度，健康检查的深度基线不应该间接依赖策略类的实例化。这既有性能开销（每次实例化所有策略），又有耦合风险（新策略的副作用可能影响健康检查）。

### 修复方案

**直接从 `ConfigHandler` 读取配置基线**，彻底消除策略遍历：

#### [MODIFY] [health_mixin.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/mixins/health_mixin.py)

替换 L294-L318：

```python
            # --- Depth & Breadth: Config-driven evaluation ---
            from utils.config_handler import ConfigHandler

            config_years = ConfigHandler.get_init_history_years()
            # A股实际交易日约 243天/年，配置用 250 作为工程近似
            max_required = config_years * 250

            missing_depth = []
            actual_trade_days = deep_health.get("global_trade_days", 0)
            if max_required > 0 and actual_trade_days < max_required * 0.95:
                missing_depth = [t for t in critical_tables if tables.get(t, {}).get("depth_ratio") is not None]
                if missing_depth:
                    if data_status == "green":
                        data_status = "yellow"
                    reasons.append(
                        I18n.get("health_depth_warning").format(
                            count=len(missing_depth),
                            required=max_required,
                            actual=actual_trade_days,
                        ),
                    )
```

同时移除顶部的 import：

```diff
-from strategies.base_strategy import _STRATEGY_REGISTRY
```

### 可行性分析

- **改动范围**：1 个文件，约 15 行
- **风险**：极低。`ConfigHandler.get_init_history_years()` 已在 `check_data_health()` 同一方法内的 L210 被调用过，逻辑成熟稳定
- **Tushare 积分影响**：无，纯计算逻辑变更
- **验证方式**：配置 3 年，同步完成后执行健康检查，确认深度警告不再触发

### 为什么不保留策略遍历作为"上限校验"？

因为 `check_data_health` 是**全局系统诊断**，它的职责是回答"我的数据仓库是否满足我配置的同步目标"。策略对数据的特殊需求属于**策略运行时校验**（已由 `@require_quality` 装饰器处理），两者职责不同。

---

## 修复 2 (P1)：TaskManager 去重竞态

### 问题根因

[task_manager.py:191-213](file:///d:/workspace/Quantitative%20Trading/astock_screener/services/task_manager.py#L191-L213) 中：

```python
def submit_task(self, ...):
    # 检查（读）
    if unique_key:
        for t in self._tasks.values():
            if t.unique_key == unique_key and t.status in (QUEUED, RUNNING):
                return None  # 去重
    
    task = AppTask(...)
    # 延迟注册（写）— 通过 call_soon_threadsafe 推迟到下一个事件循环迭代
    self._loop.call_soon_threadsafe(self._register_and_run, task)
```

读操作（检查 `self._tasks`）是即时的，但写操作（写入 `self._tasks`）被推迟了。两次连续调用之间存在窗口。

### 修复方案

在 `submit_task` 中**当场注册占位**，然后将"启动执行"推迟到事件循环：

#### [MODIFY] [task_manager.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/services/task_manager.py)

```python
    def submit_task(
        self,
        name: str,
        task_type: str,
        coroutine_factory: Callable,
        cancellable: bool = False,
        unique_key: str = None,  # type: ignore
        **kwargs,
    ) -> str | None:
        # Deduplication: reject if a task with same unique_key is already active
        if unique_key:
            for t in self._tasks.values():
                if t.unique_key == unique_key and t.status in (
                    TaskStatus.QUEUED,
                    TaskStatus.RUNNING,
                ):
                    logger.warning(
                        f"[TaskManager] Duplicate task skipped: '{name}' (key={unique_key})",
                    )
                    return None

        task = AppTask(name=name, task_type=task_type, cancellable=cancellable)
        task.unique_key = unique_key
        task._coroutine_gen = lambda t=task: coroutine_factory(task_id=t.id, **kwargs)

        # 🔑 即时注册占位，封堵 call_soon_threadsafe 延迟窗口
        self._tasks[task.id] = task

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._register_and_run, task)
        else:
            # 回滚占位
            self._tasks.pop(task.id, None)
            logger.error(
                f"[TaskManager] Cannot submit task '{name}': no event loop captured.",
            )

        return task.id
```

同时修改 `_register_and_run`，避免重复写入 `self._tasks`：

```python
    def _register_and_run(self, task: AppTask):
        """Finalize task registration and launch runner.
        Task is already in self._tasks (set by submit_task for dedup safety)."""
        task._cancel_event = asyncio.Event()
        # 不再重复写入 self._tasks[task.id] = task
        self._persist_task(task)
        self._notify_subscribers()
        logger.info(f"[TaskManager] Queued task: [{task.id}] {task.name}")

        coro_task = asyncio.create_task(self._task_runner(task.id))
        self._background_tasks.add(coro_task)
        coro_task.add_done_callback(self._background_tasks.discard)
```

### 可行性分析

- **改动范围**：1 个文件，约 5 行变更
- **风险**：低。`self._tasks` 是 Python dict，dict 的读写在 CPython 中受 GIL 保护，不会出现部分写入。但需要注意 `_register_and_run` 中不能再重复 `self._tasks[task.id] = task`
- **Tushare 积分影响**：无
- **验证方式**：快速双击健康检查按钮，确认日志只出现一条 `Queued task`

### 线程安全补充说明

虽然 Flet 的 async `on_click` 在事件循环线程上运行（单线程），但 `submit_task` 也可能从其他入口点被调用（如 `SchedulerService` 的后台线程）。当场写入 `self._tasks` 在 CPython 下是 GIL-safe 的。如果未来迁移到 no-GIL Python，需要加 `threading.Lock`。

---

## 修复 3 (P1)：UI 入口幂等保护

### 问题根因

[data_source_tab.py:355-379](file:///d:/workspace/Quantitative%20Trading/astock_screener/ui/views/settings_tabs/data_source_tab.py#L355-L379)：

```python
async def refresh_health_status(self, e):
    if e is not None:
        UILogger.log_action(...)
    if not self.page:
        return
    # ⚠️ 设置了 disabled，但没有在入口检查它
    self.btn_check_health.disabled = True
    ...
```

两次快速点击进入后，两次都会执行到 `submit_task`。

### 修复方案

在入口添加 `disabled` 卫语句：

#### [MODIFY] [data_source_tab.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/ui/views/settings_tabs/data_source_tab.py)

```python
    async def refresh_health_status(self, e):
        if e is not None:
            UILogger.log_action("DataSourceTab", "Click", "btn_check_health")
        if not self.page:
            return

        # 幂等保护：如果按钮已禁用（上一次检查仍在运行），直接返回
        if self.btn_check_health.disabled:
            return

        # Disable button to indicate processing
        self.btn_check_health.disabled = True
        ...
```

### 可行性分析

- **改动范围**：1 个文件，增加 3 行
- **风险**：极低。`btn_check_health.disabled` 在 `_run_health_check` 的 `finally` 块中被恢复为 `False`
- **注意事项**：当 `e is None`（同步完成后自动触发）时，按钮可能已经被用户手动点击禁用。自动触发场景不应被阻挡，但由于 `submit_task` 的 `unique_key` 机制（修复 2）已提供二次保障，此处行为是安全的

---

## 修复 4 (P2)：稀疏表日志噪音

### 问题根因

[cache_manager.py:607-611](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/cache/cache_manager.py#L607-L611)：

```python
if ratio < 0.1:
    if is_stock_table:
        logger.warning(f"⚠️ Table {table} coverage CRITICAL: {cnt}/{total_stocks}")
```

对所有 `monitor=True` 的 stock 类型表统一使用 `distinct ts_code / total_stocks < 10%` 作为 CRITICAL 告警阈值。

**Tushare Pro 2100 积分的实际数据情况：**

| 表名 | 数据特性 | 2100积分可获取性 | 同步后预期覆盖率 |
|---|---|---|---|
| `fina_forecast` | 事件型，仅发布业绩预告的公司 | ✅ 可用 | ~3-5%（正常） |
| `fina_mainbz` | 按股票逐只拉取(O(Stock)) | ✅ 可用 | >80%（同步完成后） |
| `repurchase` | 事件型，仅发生回购的公司 | ✅ 可用 | ~5-10%（正常） |
| `dividend` | 事件型，仅分红的公司 | ✅ 可用 | ~30-50%（正常） |
| `pledge_stat` | 按股票逐只拉取(O(Stock)) | ✅ 可用 | >60%（同步完成后） |

> [!NOTE]
> `fina_forecast` 和 `repurchase` 的低覆盖率是**业务事实**（不是所有公司都发业绩预告或做回购），不应触发 CRITICAL 警告。但 `fina_mainbz` 在 **首次完整同步后**覆盖率应该很高（因为是 O(Stock) 逐只拉取），如果显示 0% 则说明**同步流程中该步骤可能被跳过或失败**。

### 修复方案

在 `data_dictionary.py` 的 `TABLE_DEFINITIONS` 中为事件型表添加 `sparse: True` 标记，然后在 `cache_manager.py` 中对稀疏表降级日志级别：

#### [MODIFY] [data_dictionary.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/data_dictionary.py)

```python
    "fina_forecast": {
        ...
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        ...
    },
    "repurchase": {
        ...
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        ...
    },
    "dividend": {
        ...
        "quality_config": {"tier": 1, "monitor": True, "sparse": True},
        ...
    },
```

#### [MODIFY] [cache_manager.py](file:///d:/workspace/Quantitative%20Trading/astock_screener/data/cache/cache_manager.py)

将 L607 附近的日志逻辑改为：

```python
                    is_sparse = meta.get("quality_config", {}).get("sparse", False)

                    if ratio < 0.1:
                        if is_sparse:
                            # 稀疏事件表（回购、业绩预告等）低覆盖率是业务常态，降为 DEBUG
                            logger.debug(
                                f"[CacheManager] Health | Table {table} (sparse): {cnt}/{total_stocks} ({ratio:.1%}) — normal for event-driven data",
                            )
                        elif is_stock_table:
                            logger.warning(
                                f"[CacheManager] Health | ⚠️ Table {table} coverage CRITICAL: {cnt}/{total_stocks} ({ratio:.1%})",
                            )
                        else:
                            logger.warning(
                                f"[CacheManager] Health | ⚠️ Table {table} (global) CRITICAL: {cnt} records",
                            )
```

### 可行性分析

- **改动范围**：2 个文件，约 10 行
- **风险**：极低。`sparse` 是新增的可选属性，默认 `False`，对现有逻辑零影响
- **Tushare 积分影响**：无
- **重要说明**：此修复**不改变**健康状态的红黄绿判定逻辑（因为这些表本来就没有 `critical: True`），仅消除日志噪音
- **`fina_mainbz` 为何不标为 sparse**：它是 O(Stock) 同步，完整同步后每只股票都应有数据。如果出现 0% 覆盖率，这是一个**真实的同步异常信号**，应该保留 WARNING

---

## 验证计划

### 自动化验证

```bash
# 1. 运行现有测试，确保无回归
python -m pytest tests/ -x -q

# 2. 如果有健康检查相关测试，单独运行
python -m pytest tests/ -k "health" -v
```

### 手动验证矩阵

| 场景 | 预期结果 | 验证修复 |
|---|---|---|
| 配置 3 年，同步完成后执行健康检查 | 深度项不报警（729 ≥ 750*0.95=712） | 修复 1 |
| 快速双击"健康检查"按钮 | 日志只出现 1 条 `Queued task` | 修复 2+3 |
| 检查完成后日志中 `fina_forecast` / `repurchase` | 无 WARNING，仅 DEBUG | 修复 4 |
| `fina_mainbz` 覆盖率为 0% | 仍然输出 WARNING（非稀疏表） | 修复 4 |

---

## 未纳入本次修复的已知问题

| 问题 | 原因 | 建议 |
|---|---|---|
| `_health_cache` 10秒缓存的并发语义 | 实际影响极小（修复 2+3 已阻止并发调用） | 后续版本观察 |
| `check_data_health` 中 `isinstance(days, property)` 卫语句无效 | 不影响功能，代码清理级别 | 可在修复 1 中顺带移除 |

