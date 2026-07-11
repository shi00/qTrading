# Phase 2 Code Review Gate

**Phase**: 2 — ViewModel 改造(7 个 VM,state snapshot + subscribe/_notify)
**Review date**: 2026-07-09
**Reviewer**: Lead (harness-work solo mode)
**Verdict**: APPROVE

---

## 1. 范围

Phase 2 包含 9 个 task(Task 2.1-2.9),改造 7 个 ViewModel 为 frozen dataclass state snapshot + subscribe/_notify 范式:

| Task | VM | Commit | 测试 |
|------|-----|--------|------|
| 2.1 | ScreenerViewModel | 58c7758 | 163 tests |
| 2.2 | BacktestViewModel | 1ab486a | 28 tests |
| 2.3 | OnboardingViewModel | f77e417 | 9 处断言迁移 |
| 2.4 | SystemViewModel | a732eb3 | 15 tests |
| 2.5 | DataSourceViewModel | 4facd38 | — |
| 2.6 | HomeViewModel | e152240 | — |
| 2.7 | DataExplorerViewModel(双轨制) | fc478d5 | 69+41 tests |
| 2.8 | Phase 2 回归验收 | d04016b | grep=0, 7676 green |
| 2.9 | 本 review gate | — | — |

---

## 2. 红线违规检查(R1-R17)

### R1 架构越界
- `grep "^from ui\.|^import ui\." ui/viewmodels/`: 0 matches ✓
- VM 层不导入 ui 层,符合 §4.1 分层架构

### R2 异常吞没(asyncio.CancelledError)
- 15 处 `except asyncio.CancelledError` 全部 `raise`(逐一验证):
  - screener_view_model.py:363 → L369 raise ✓
  - onboarding_view_model.py:440 → L442 raise ✓
  - home_view_model.py:128/213 → L129/214 raise ✓
  - data_source_view_model.py:220/264/317/426 → L224/270/323/428 raise ✓
  - data_explorer_view_model.py:166/202/266/308/352/389 → 全部 raise ✓
  - backtest_view_model.py:218 → L219 raise ✓

### R3 模糊压制(# type: ignore 无 reason)
- 7 处 `type: ignore` 全部带 reason:
  - `[arg-type]` / `[assignment]` / `[untyped]` ✓

### R6 过时类型注解(Union[X, Y] / Optional[X])
- `grep "Union\[|Optional\[" ui/viewmodels/`: 0 matches ✓

### R11 跨循环复用同步原语(asyncio.Event/Lock 作为类属性)
- `grep "asyncio\.Event|asyncio\.Lock" ui/viewmodels/`: 0 matches ✓

### 其他红线(R4/R5/R7-R10/R12-R17)
- Phase 2 只改 VM 层,不涉及 SQL/DAO/单例注册/策略注册/数据表/UI 事件处理器 ✓
- R16 UI 阻塞主循环: N/A(VM 层不包含 Flet 事件处理器)

---

## 3. 形态契约一致性(7 个 VM)

| VM | frozen dataclass | subscribe | _notify | _set_state | on_update=/on_log=/on_status= |
|----|:---:|:---:|:---:|:---:|:---:|
| ScreenerViewModel | ✓ L27 | ✓ L119 | ✓ L133 | ✓ L139 | 0 |
| BacktestViewModel | ✓ L30 | ✓ L88 | ✓ L98 | ✓ L103 | 0 |
| OnboardingViewModel | ✓ L144 | ✓ L195 | ✓ L204 | ✓ L209 | 0 |
| SystemViewModel | ✓ L23 | ✓ L68 | ✓ L77 | ✓ L82 | 0 |
| DataSourceViewModel | ✓ L33 | ✓ L121 | ✓ L130 | ✓ L135 | 0 |
| HomeViewModel | ✓ L18 | ✓ L62 | ✓ L72 | ✓ L79 | 0 |
| DataExplorerViewModel | ✓ L28 | ✓ L108 | ✓ L118 | ✓ L125 | 0 |

- `grep "on_update=|on_log=|on_status=" ui/viewmodels/`: 0 matches ✓
- 7 个 VM 全部符合 CONTRIBUTING.md「MVVM 表现层」契约

### 双轨制验证(DataExplorerViewModel,方案 §3.0.4)
- 轻量 UI 状态(17 字段)封装为 frozen `DataExplorerState`:标量 + tuple/frozenset 集合 + dual-track versions ✓
- 大体积数据(`_current_data: DataFrame` / `_sql_result: dict`)VM 内部持有,通过 `current_data`/`sql_result` property 拉取 ✓
- `query_data`/`execute_sql` 成功后递增 `data_version`/`sql_result_version` 通知 View 拉取 ✓

---

## 4. 符合 CLAUDE.md 要求

### §1.3 极简设计
- 7 个 VM 改造仅引入必要的 state/subscribe/_notify/_set_state,无过度抽象 ✓
- DataExplorerViewModel 双轨制是方案 §3.0.4 明确要求的形态,非推测性设计 ✓
- View API 适配(data_view.py)采用最小化修改:属性访问改为 `vm.state.xxx`,直接赋值改为调用 VM 方法,不添加 subscribe(声明式重写留待 Task 4.3)✓

### §1.4 微创修改
- 各 VM 改造仅触及 VM 文件 + 配套测试 + 直接依赖的 View 文件 ✓
- 修复 Task 2.1 遗留(`test_news_subscription_viewmodel.py` `vm.mode` 直接赋值)属于同类隐患排查(§1.7 举一反三)✓
- 未顺手删除 `test_concurrency_audit.py` 中的死代码(`vm.mode = "REALTIME"` / `vm.on_log = MagicMock()`),仅在回复中指出 ✓

### §3.2 UI 模型强制要求
- 7 个 VM 全部符合 MVVM + 声明式渲染复合范式(frozen state snapshot + subscribe/_notify)✓
- VM 不 import flet/不持有 Flet 控件/不调 page.update() ✓

### §3.3 已知技术债
- 7 个 ViewModel + 命令式 View 全面重写:VM 改造已完成(Phase 2),View 声明式重写待 Phase 3-4 ✓
- `use_viewmodel` hook:Phase 1.5 已实现,Phase 3+ View 重写时消费 ✓

### §4 架构边界
- VM 层(ui/viewmodels/)不导入 ui 层 ✓
- VM 层可导入 services/strategies/data/utils(依赖方向正确)✓

---

## 5. 测试验证

### 单元测试
- `pytest tests/unit/ -m "not slow"`: **7676 passed**, 382 deselected, 5 warnings(资源警告,非错误), 45 subtests passed, 198s ✓
- 各 VM 配套测试全部通过:
  - test_screener_view_model.py + test_viewmodels.py: 163 tests ✓
  - test_backtest_view_model.py: 28 tests ✓
  - test_onboarding_view_model.py: 9 处断言迁移 ✓
  - test_system_viewmodel.py: 15 tests ✓
  - test_data_source_view_model.py ✓
  - test_ui_home_vm.py ✓
  - test_data_explorer_view_model.py: 69 tests ✓
  - test_data_view.py: 41 tests ✓

### pyright
- 变更文件检查(`pyright ui/viewmodels/data_explorer_view_model.py ui/views/data_view.py tests/unit/ui/test_data_explorer_view_model.py tests/unit/ui/test_data_view.py`): **0 errors, 41 warnings**(多为既有的 reportOptionalMemberAccess/reportAttributeAccessIssue,非新引入)✓
- 新引入的 3 处 `reportArgumentType` warning(data_view.py set_table/set_filter 参数 `str | None` → `str`)是 Flet Dropdown.value 类型契约差异,用户交互回调中 value 不可能为 None,加 `or ""` 属于防御不可能场景(违反 §1.3),保留 warning ✓

### ruff
- `ruff check .`: All checks passed ✓
- `ruff format --check .`: 493 files already formatted ✓

### 集成测试
- N/A(Phase 2 未触及 integration 测试)

---

## 6. 无场景遗漏

### 方案章节锚点覆盖
- §2 阶段 2(ViewModel 改造): 7 个 VM 全部完成 ✓
- §3.0 形态契约: frozen dataclass + subscribe/_notify 全部符合 ✓
- §3.0.1 VM 改造契约: state snapshot + commands + subscribe ✓
- §3.0.4 双轨制: DataExplorerViewModel 双轨制完成 ✓
- §3.2 第一组/第二组 VM 测试文件: 全部整改完成 ✓

### 中断/取消/异常路径覆盖
- 所有 VM 的 `except asyncio.CancelledError` 块都 `raise`(R2)✓
- 异常路径:各 VM 的 `except Exception` 使用 `classify_error()` + 日志 + state 更新(符合错误处理标准模式)✓
- DataExplorerViewModel 的 6 处 CancelledError 覆盖:init_tables/load_table_schema/query_data/query_count/export_data/execute_sql ✓

### 6.1 locale 场景遗漏修复(Phase 2 code review 补充)

**发现**:Phase 2 code review 深入检视时发现 6 个 VM 的 message 字段用 `str` 类型并直接调 `I18n.get()` 翻译后放入 state,违反 CONTRIBUTING.md「MVVM 表现层」契约("VM 只产出 Message(key, params),不感知 locale")。

**修复**(commit `aa1f69c`):
- 新建 `ui/viewmodels/__init__.py`:`Message` dataclass(`key: str` + `params: dict[str, Any]`)
- 6 个 VM 改造:
  - ScreenerVM:`status_message: str` → `Message | None`,10 处赋值改 `Message()`
  - BacktestVM:`status_message`/`progress_message: str` → `Message | None`
  - OnboardingVM:`sync_progress_message: str` → `Message | None`
  - DataSourceVM:`progress_message: str` → `Message | None`,`_emit_snack` dual-track 改造
  - DataExplorerVM:`error_message: str` → `Message | None`,5 处提取 `error_info["message_key"]` 构造 Message
  - SystemVM/HomeVM:无 message 字段,无需改造
- View 消费端适配:
  - `onboarding_wizard.py`:`_on_vm_sync_progress` 签名 `message: str` → `Message | None`,渲染时 `I18n.get(msg.key, **msg.params)`
  - `data_source_tab.py`:`_on_vm_progress_update` + `_on_vm_show_snack` 同上
  - `backtest_view.py`:`_on_vm_status`/`_on_vm_progress` 为死代码(未绑定 subscribe),Phase 4 重写,无需适配
- 保留的 `I18n.get()` 调用(非 state 字段):
  - `return I18n.get(...)`(TaskManager 任务结果返回值)
  - `TaskManager.submit_task(name=I18n.get(...), task_type=I18n.get(...))` 参数
  - `raise Exception(I18n.get(...))` 异常消息
  - `self._tm.update_progress(task_id, x, I18n.get(...))` progress 参数
- 嵌套 i18n 处理:`Message("key", {"name": I18n.get(strategy.name_key)})` + `# NOTE(lazy)` 标记(ceiling: Phase 2 locale 修复;upgrade: Phase 3-4 View 声明式重写)
- DataExplorerVM `_sql_result.error` 字段:保留 `get_error_message()` + `# NOTE(lazy)` 标记(dual-track 大体积数据,非 state 字段)

**验证**:
- ruff check + format: 通过
- pyright: 0 errors(13 pre-existing warnings,无新引入)
- pytest tests/unit/(not slow): 7676 passed(含 8 个 test_data_source_tab.py 失败测试已适配)
- pre-commit: 8 hooks 通过

---

## 7. 排查清单(§1.7 举一反三)

Task 2.1 遗留的 `vm.mode` 直接赋值问题(根因:mode 从直接属性改为 state 字段后,部分测试未适配):

| 文件 | 行 | 问题 | 处理 |
|------|-----|------|------|
| test_news_subscription_viewmodel.py:94 | `vm.mode = "HISTORY"` | 导致测试失败 | 已修复为 `vm._set_state(mode="HISTORY")` |
| test_concurrency_audit.py:388 | `vm.mode = "REALTIME"` | 死代码(mode 默认即 REALTIME) | 指出不删(§1.4) |
| test_concurrency_audit.py:408 | `vm.mode = "REALTIME"` | 同上 | 指出不删(§1.4) |
| test_concurrency_audit.py:385,405 | `vm.on_log = MagicMock()` | 死代码(on_log 回调已移除) | 指出不删(§1.4) |

---

## 8. 结论

**Verdict: APPROVE**

Phase 2 的 7 个 ViewModel 改造全部完成,形态契约一致(frozen dataclass + subscribe/_notify),无红线违规引入,分层架构合规,全量单元测试通过(7676 passed),变更文件 pyright 0 errors。DataExplorerViewModel 双轨制(方案 §3.0.4)正确实现。

**Phase 2 code review 补充**:发现并修复 locale 场景遗漏(6 个 VM 的 message 字段违反"VM 不感知 locale"契约),引入 `Message(key, params)` dataclass 统一 i18n 消息契约,View 消费端适配完成。修复后全量验证通过(ruff/pyright/pytest/pre-commit)。

可进入 Phase 2.5(测试基础设施前置)。
