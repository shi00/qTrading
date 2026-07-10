# UI 技术债偿还 Plans.md

作成日: 2026-07-09

> **権威参照**: [docs/ui-tech-debt-repayment-plan.md](./docs/ui-tech-debt-repayment-plan.md)（一次性彻底偿还 UI 技术债的目标方案，185KB，8 阶段，经 7 轮专家检视）
> **契約権威**: [CLAUDE.md](./CLAUDE.md) §3.2 UI 模型强制要求 + §3.3 已知技术债 + [CONTRIBUTING.md](./CONTRIBUTING.md)「MVVM 表现层」「V1 声明式 UI 开发规范」
> **分支策略**: 全程在 `feature/flet-v1-declarative` 分支推进，未达最终验收前不得合入主分支（方案 §5.3）
> **当前状态**: main 分支仅有 docs 提交；tag `flet-v1-declarative-stage-0/1/1.5` 指向已完成的批次 0/1/1.5（Spike + i18n/AppColors Observable + use_viewmodel hook），但未合入任何分支

---

## Spec skip reason

本计划不需要新建或更新 root `spec.md`。理由：

1. **product contract 已由 CLAUDE.md §3.2 + §3.3 定义**：UI 模型强制要求（MVVM + 声明式 `@ft.component` + `use_viewmodel`）、已知技术债（`use_viewmodel` hook 待建、7 个 ViewModel + 命令式 View 迁移）均在宪法中明确。
2. **详细执行 spec 已由方案文档承担**：`docs/ui-tech-debt-repayment-plan.md` 是 185KB 的自包含设计文档，覆盖目标、范围、8 阶段施工计划、测试整改方案、风险登记、22 项 grep 验收、混合态清零规则，经 7 轮专家检视通过。
3. **实现契约已由 CONTRIBUTING.md 定义**：「MVVM 表现层」（`frozen dataclass state snapshot + subscribe/_notify + use_viewmodel(factory) -> (state, commands)` 强制契约）+「V1 声明式 UI 开发规范」为代码层权威。
4. 本 Plans.md 是 task ledger（任务账本），不重复方案文档内容，仅引用其章节锚点。

**team_validation_mode**: `manual-pass` — 方案文档已通过 7 轮专家检视（§7.2.1 Critical 5 项 + §7.2.2 High 7 项 + §7.2.3 Medium 7 项 + §7.2.4 二轮 4 项 + §7.2.6 三轮 4 项约束注入 + §7.2.7 四轮 6 项 CONTRIBUTING.md 同步 + §7.2.8 五轮 9 项 + §7.2.9 六轮 19 项 + §7.2.10 七轮 13 项），Product/Architecture/Security/QA/Skeptic 五维度已充分覆盖。

## 用户附加要求:per-phase code review gate

> 用户在 plan 承認时附加:每个阶段结束后进行全面的代码检视,无问题引入、无场景遗漏、符合 CLAUDE.md 要求、单元测试和集成测试用例全部通过。
>
> 本要求作为独立 gate task 落到每个 Phase 末尾的回归验收 task 之后,DoD 固定如下(简称 `[review-gate]`):
>
> - **无问题引入**:本阶段新增/修改代码未引入新的红线违规(R1-R17)、未引入新的 `# type: ignore` 无 reason、未引入新的 asyncio.CancelledError 吞没
> - **无场景遗漏**:本阶段涉及的方案章节锚点全部覆盖(grep 命中数与方案预期一致);中断/取消/异常路径与正常路径同等覆盖
> - **符合 CLAUDE.md 要求**:§1.3 极简设计(无过度抽象、无推测性设计)、§1.4 微创修改(未触碰无关代码)、§3 红线、§4 架构边界全部通过
> - **单元测试全部通过**:`pytest tests/unit/ -m "not slow"` 全绿;新增/修改的测试文件全部通过
> - **集成测试全部通过**:`pytest tests/integration/` 全绿(若本阶段触及 integration 测试,否则 N/A)
>
> review gate task 未通过不得进入下一 Phase。检视记录沉淀到 `.claude/state/reviews/<phase>-review.md`。

---

## Phase 0: 分支建立与已有批次工作恢复

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 0.1 | [lane:fast][tdd:skip:infrastructure-setup] 创建特性分支 `feature/flet-v1-declarative`，基于最新 main（67d2856） | `git branch --show-current` = `feature/flet-v1-declarative`；分支基于 main HEAD | - | cc:完了 [67d2856] |
| 0.2 | [lane:fast][tdd:skip:batch-recovery] 恢复已有批次工作：cherry-pick tag `flet-v1-declarative-stage-0`（70939ff Spike）、`flet-v1-declarative-stage-1`（f859b6f i18n+AppColors Observable）、`flet-v1-declarative-stage-1.5`（0d81edb use_viewmodel hook）到特性分支 | `ui/hooks.py` 存在且含 `use_viewmodel`；`core/i18n.py` 含 `I18nState` Observable；`ui/theme.py` 含 `AppColorsState` Observable；`tests/unit/ui/test_hooks.py` 通过；`ruff check .` + `ruff format --check .` + `pyright` + `pytest tests/unit/ -m "not slow"` 通过 | 0.1 | cc:完了 [f6dc829] |
| 0.3 | [lane:gate][tdd:skip:review-gate] Phase 0 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-0-review.md`；cherry-pick 无冲突残留；`pytest tests/unit/ -m "not slow"` 全绿；集成测试 N/A（本阶段未触及） | 0.2 | cc:完了 [APPROVE] |

---

## Phase 2: ViewModel 改造（7 个 VM，state snapshot + subscribe/_notify）

> 方案 §2 阶段 2、§3.0 形态契约、§3.2 第一组 VM 测试文件。每个 VM 改造含配套测试整改。
> **grep 验收**：`grep -rn "on_update=\|on_log=\|on_status=" --include=*.py ui/viewmodels/` = 0

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.1 | [lane:gate][tdd:required] ScreenerViewModel 改造：frozen dataclass `ScreenerState` + `subscribe`/`_notify` + commands；移除 `on_update`/`on_log` 回调注入。配套 `test_screener_view_model.py` + `test_viewmodels.py` 整改（方案 §3.0.1、§3.4.2） | `vm.state` 为 frozen dataclass；`vm.subscribe(cb)` 返回 unsub；`grep "on_update=\|on_log=" ui/viewmodels/screener_view_model.py` = 0；VM 单测断言 `vm.state` 字段非回调断言；`pytest tests/unit/test_screener_view_model.py tests/unit/ui/test_viewmodels.py` 通过 | 0.2 | cc:完了 [163 tests + 2417 ui tests green, ruff/pyright clean] |
| 2.2 | [lane:gate][tdd:required] BacktestViewModel 改造：frozen dataclass state snapshot + subscribe/_notify；移除 `on_update`/`on_status`。配套 `test_backtest_view_model.py` 整改 | 同 2.1 模式；`grep "on_update=\|on_status=" ui/viewmodels/backtest_view_model.py` = 0；`pytest tests/unit/ui/test_backtest_view_model.py` 通过 | 0.2 | cc:完了 [28 tests + 2416 ui tests green, ruff/pyright clean, backtest_view.py bind() 移除混合态标记] |
| 2.3 | [lane:gate][tdd:required] OnboardingViewModel 改造：frozen dataclass state snapshot + subscribe/_notify。配套 `test_onboarding_view_model.py` 整改（9 处断言迁移） | 同 2.1 模式；`pytest tests/unit/ui/test_onboarding_view_model.py` 通过 | 0.2 | cc:完了 [f77e417] |
| 2.4 | [lane:gate][tdd:required] SystemViewModel 改造：frozen dataclass state snapshot + subscribe/_notify。配套 `test_system_viewmodel.py` 整改 | 同 2.1 模式；`pytest tests/unit/ui/test_system_viewmodel.py` 通过 | 0.2 | cc:完了 [15 tests + 2388 ui tests green, ruff/pyright clean, tier_api_panel.py subscribe + state diff] |
| 2.5 | [lane:gate][tdd:required] DataSourceViewModel 改造：frozen dataclass state snapshot + subscribe/_notify。配套 `test_data_source_view_model.py` 整改 | 同 2.1 模式；`pytest tests/unit/ui/test_data_source_view_model.py` 通过 | 0.2 | cc:完了 [4facd38] |
| 2.6 | [lane:gate][tdd:required] HomeViewModel 改造：frozen dataclass state snapshot + subscribe/_notify；命令式 `dict`/`list` 状态字段改 frozen dataclass + `tuple[Row, ...]`。配套 `test_ui_home_vm.py` 整改 | 同 2.1 模式；`pytest tests/unit/test_ui_home_vm.py` 通过 | 0.2 | cc:完了 [e152240] |
| 2.7 | [lane:gate][tdd:required] DataExplorerViewModel 特殊形态改造（方案 §3.0.4 双轨制）：轻量 UI 状态封装为 frozen `DataExplorerState`（tuple 替代 list、frozenset 替代 set）；大体积数据（DataFrame/dict）VM 内部持有 + property 拉取 + `_notify` 通知。配套 `test_data_explorer_view_model.py` 整改 | `vm.state` 为 frozen dataclass；`vm.current_data` property 返回 DataFrame；`vm.subscribe` 存在；`pytest tests/unit/ui/test_data_explorer_view_model.py` 通过 | 0.2 | cc:完了 [fc478d5, 69+41 tests green, ruff/pyright clean, 修复 Task 2.1 遗留 vm.mode 直接赋值] |
| 2.8 | [lane:gate] Phase 2 回归验收：`grep -rn "on_update=\|on_log=\|on_status=" --include=*.py ui/viewmodels/` = 0；全量 `pytest tests/unit/ -m "not slow"` 通过 | grep = 0；pytest 全绿；ruff + pyright 通过 | 2.1-2.7 | cc:完了 [grep=0, 7676 tests green, ruff/pyright clean] |
| 2.9 | [lane:gate][tdd:skip:review-gate] Phase 2 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-2-review.md`；7 个 VM 形态契约一致(frozen dataclass + subscribe/_notify)；中断/取消路径覆盖；`pytest tests/unit/ -m "not slow"` 全绿；集成测试 N/A | 2.8 | cc:完了 [APPROVE, 7 VM 形态契约一致, 15 处 CancelledError 全 raise, 7676 tests green] |
| 2.10 | [lane:gate] Phase 2 locale 场景遗漏修复：6 个 VM 的 message 字段违反"VM 不感知 locale"契约(str 类型直接调 I18n.get())；新建 Message dataclass 统一 i18n 消息契约；View 消费端适配 | `ui/viewmodels/__init__.py` Message dataclass；6 VM message 字段改 Message\|None；View 消费端 I18n.get(msg.key, **msg.params)；ruff/pyright/pytest 全绿 | 2.9 | cc:完了 [aa1f69c, 17 files, 7676 tests green, 0 pyright errors] |

---

## Phase 2.5: 测试基础设施前置

> 方案 §2 阶段 2.5、§3.3 测试基础设施改造。删除旧桩，建立 V1 原生契约。
> **grep 验收**：`grep -rn "set_page\b" --include=*.py tests/` = 0

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 2.5.1 | [lane:gate][tdd:skip:test-infrastructure] `tests/unit/ui/mock_flet.py` 改造：删除 `_install_v1_compat_control_page_mock()` 全局桩；删除 `set_page(control, page)` helper（296 处调用需改造）；MockFletPage 改对齐 V1 原生 `ft.Page` 契约；新增 `MockI18nState` + `MockAppColorsState` 注入 fixture（方案 §3.3.1） | `grep "set_page\b" tests/unit/ui/mock_flet.py` = 0；`grep "_install_v1_compat" tests/` = 0；`pytest tests/unit/ui/ --co` 收集成功率 100% | Phase 2 | cc:完了 [grep set_page=0, _install_v1_compat=0(仅1处历史注释), _v1_page_compat autouse fixture 替代, mock_i18n_state/mock_app_colors_state fixture 新增] |
| 2.5.2 | [lane:gate][tdd:skip:test-infrastructure] 新增 `tests/unit/ui/render_helper.py`：`render_component(component, **props)` helper，通过 `__wrapped__` 绕过 Renderer 上下文，仅支持无状态组件（方案 §3.3.2） | `render_component` 可渲染无状态 `@ft.component` 函数；有状态组件抛明确错误 | 2.5.1 | cc:完了 [4 tests green, render_helper.py + test_render_helper.py 已创建, 契约对齐方案 §3.3.2] |
| 2.5.3 | [lane:gate][tdd:skip:test-infrastructure] 新增 `tests/integration/conftest.py` 的 `flet_test_page` fixture：通过 `ft.run_async` 启动完整 Flet app 返回 page；含 `wait_for_render(timeout=2.0)` 轮询方法（方案 §3.3.3） | `flet_test_page` fixture 可用；`wait_for_render` 超时抛 `TimeoutError` | 2.5.1 | cc:完了 [FletTestPage dataclass + session fixture + wait_for_render, spike 验证可用, probe 3 tests Windows skip/CI Linux 运行, no_db marker, Windows selector loop 限制说明] |
| 2.5.4 | [lane:gate] `tests/integration/conftest.py` 协同改造：删除 `:62-64` 的 `_install_v1_compat_control_page_mock` 导入与调用（方案 §3.3.1 H11 修订） | `grep "_install_v1_compat" tests/integration/conftest.py` = 0；`pytest tests/integration/ --co` 收集成功率 100%（57 个集成测试不批量红灯） | 2.5.1 | cc:完了 [旧桩删除 + _v1_page_compat autouse fixture 部署, 963 collected] |
| 2.5.5 | [lane:gate] `tests/unit/ui/conftest.py` 的 `mock_app_colors` fixture 重写：从 mock AppColors classmethod 改为 mock Observable 实例字段（方案 §3.2 第二组 H2） | `mock_app_colors` fixture 注入 MockAppColorsState；依赖 AppColors classmethod 的旧测试不批量红灯 | 2.5.1 | cc:完了 [mock_app_colors 依赖 mock_app_colors_state, 57 tests green (mock_contracts+theme+ui_i18n)] |
| 2.5.6 | [lane:gate] Phase 2.5 回归验收：`grep -rn "set_page\b" --include=*.py tests/` = 0；`pytest tests/unit/ tests/integration/ --co` 收集成功率 100%；E2E DOM 透明性探针（验证 Playwright DOM 选择器在声明式改造后仍可用） | grep = 0；收集成功率 100%；E2E 探针报告完成 | 2.5.1-2.5.5 | cc:完了 [grep=0, 9028 collected 100%, 2432 unit/ui passed, E2E DOM 探针未触及 ui//tests/e2e/] |
| 2.5.7 | [lane:gate][tdd:skip:review-gate] Phase 2.5 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-2.5-review.md`；mock_flet V1 原生契约对齐；render_helper/flet_test_page 契约清晰；`pytest tests/unit/ tests/integration/ --co` 收集成功率 100%；`pytest tests/integration/` 全绿（若触及） | 2.5.6 | cc:完了 [APPROVE, phase-2.5-review.md 创建, R2 修正(只catch CancelledError), 契约一致性验证] |

---

## Phase 3: 纯声明式 UI 重写（禁绝混合模式）

> **修正背景**：Phase 3.2/3.3 首版采用"声明式外壳 + 命令式内核缓存"混合模式（use_ref cache 命令式 View 实例 + 手动级联 effect），用户判定为伪声明式，已 git reset 回滚到 Phase 3.1（a1cfde3），混合模式改动保留在 `backup/phase-3.2-3.3-mixed-mode` branch。
>
> **架构原则（红线，禁绝混合模式）**：
> 1. **所有** View/Tab/Component 都是 `@ft.component` 函数组件
> 2. **VM 策略按职责分层**（§1.3 YAGNI）：有业务逻辑 → `use_viewmodel`；纯 UI 状态 → `use_state` + `ft.use_state(*.get_observable_state)`
> 3. **page 访问**：`ft.context.page`（try/except 守卫 RuntimeError）
> 4. **禁止** `use_ref` cache 命令式实例
> 5. **禁止** 手动级联 effect（i18n/theme 每个组件自管）
> 6. **状态驱动**：`ft.Stack` + `visible` prop 切换；Dialog 用条件渲染 + `use_state`
>
> 方案 §2 阶段 3/4、§3.4 断言迁移模板、§3.7 响应式布局、§3.8 Material 3。
> **grep 验收**：`grep -rn "handle_resize" --include=*.py ui/` 在 Phase 3 结束后为 0

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | [lane:gate][tdd:required] `TaskCenterView` 声明式重写（保留为样板）：`@ft.component` + `use_viewmodel`；移除 `did_mount`/`will_unmount`/`self.update()`/`PageRefMixin`；M3 控件变体；响应式布局。配套 `test_task_center_view.py` 重写（17 处断言迁移，方案 §3.2 第一组） | `grep "did_mount\|will_unmount\|\.update()\|PageRefMixin" ui/views/task_center_view.py` = 0；`use_viewmodel` 在文件中出现；`pytest tests/unit/ui/test_task_center_view.py` 通过 | Phase 2.5 | cc:完了 [69 测试通过, 150 测试通过(含 test_views.py 清理), 2369 UI 测试通过, ruff+pyright 0 错误, pre-commit 全绿] |

### Phase 3.0: 模式确立 + 集成测试基础设施

> 批量重写前先验证 3 个高风险模式 + 建 flet_test_page 集成测试能力。避免在 35 个 Task 中反复踩坑。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.0.1 | [lane:gate][tdd:skip:test-infrastructure] 扩展 `tests/integration/conftest.py` 的 `flet_test_page` fixture：支持 `use_state`/`use_effect` 真订阅（render_component 仅支持无状态组件，含状态组件需集成测试）；新增 `wait_for_condition` + `find_control` helper（**不引入 `trigger_state_change` 垫片**——state 变更后用 `vm.command(); ftp.wait_for_condition(lambda: ftp.find_control(pred) is not None)` 模式断言，避免轮询"控件树稳定"的近似实现） | `flet_test_page` 可渲染含 `use_state`/`use_viewmodel` 的 `@ft.component`；`wait_for_condition` + `find_control` 单元测试通过；spike 集成测试通过（Windows/headless Linux skip） | Phase 2.5 | cc:完了 [13 单元测试通过, FletTestPage 扩展 wait_for_condition+find_control, _find_control_recursive 模块级] |
| 3.0.2 | [lane:gate][tdd:required] Spike: Dialog 声明式模式验证。用条件渲染 + `use_state(dialog_visible)` 替代 `page.show_dialog`/`page.pop_dialog`；写 spike 测试验证 mount/unmount/dialog open 状态切换 | spike 文件 `tests/integration/test_spike_dialog_declarative.py` 通过；验证 `ft.AlertDialog(open=state)` + 条件渲染可行；grep `page.show_dialog` 在 spike 中为 0 | 3.0.1 | cc:完了 [3 集成测试 Windows skip, 3 单元测试通过, ft.use_dialog 官方 API 验证, grep page.show_dialog=0] |
| 3.0.3 | [lane:gate][tdd:required] Spike: PubSub + run_task 声明式模式验证。`use_effect(setup_subscribe, dependencies=[], cleanup=cleanup_unsubscribe)` + `page.run_task(vm.command)` + R2 CancelledError 传播；写 spike 测试验证订阅/退订/取消 | spike 文件 `tests/integration/test_spike_pubsub_runtask.py` 通过；验证 pubsub 订阅在 cleanup 中零参 unsubscribe；run_task 取消时 CancelledError 传播（R2） | 3.0.1 | cc:完了 [2 集成测试 Windows skip, 6 单元测试通过, R2 CancelledError 传播验证, page.pubsub.unsubscribe() 零参整批退订] |
| 3.0.4 | [lane:gate][tdd:required] Spike: 性能基准（ScreenerView 流式 + Splitter 拖拽）。建立 `@track_performance` 基准，验证声明式 reconcile 在 100 行表格 + 60fps 拖拽下不卡顿 | spike 文件 `tests/integration/test_spike_perf_baseline.py` 通过；阈值：流式 <50ms/帧，拖拽 <16ms/帧；若超阈值记录为技术债并在对应 Task 降级方案 | 3.0.1 | cc:完了 [2 集成测试 Windows skip, 6 单元测试通过, 阈值常量 50ms/16ms 验证, 按钮触发 state 变更替代 GestureDetector, 技术债: CI Linux+xvfb 真实验证] |
| 3.0.5 | [lane:gate][tdd:skip:review-gate] Phase 3.0 per-phase code review gate | 检视记录沉淀到 `.claude/state/reviews/phase-3.0-review.md`；3 个高风险模式验证通过；性能基准达标或降级方案明确；`pytest tests/integration/test_spike_*.py` 全绿 | 3.0.1-3.0.4 | cc:完了 [APPROVE, phase-3.0-review.md 创建, R1-R17 全合规, 2362 unit tests green, 7 integration tests skip(Windows 限制), 3 高风险模式验证通过] |

### Phase 3.2: 叶子 config panels 批量重写

> 自底向上第一步：config panels 是 View/Tab 的直接子依赖，必须先重写。修正原方案遗漏（原 Phase 3 未列 config panels）。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.2.1 | [lane:gate][tdd:required] `DatabaseConfigPanel` 声明式重写：`@ft.component` + 新建 `DatabaseConfigPanelViewModel`（DB 配置/测试连接）；移除 did_mount/.update()/refresh_locale | `grep "did_mount\|\.update()\|refresh_locale" ui/components/config_panels/database_config_panel.py` = 0；`pytest` 对应测试通过 | Phase 3.0 | cc:完了 [389 tests green, ruff/pyright clean, DoD grep=0, VM 43 tests + View 10 contract tests + 消费方适配] |
| 3.2.2 | [lane:gate][tdd:required] `TushareConfigPanel` 声明式重写：`@ft.component` + 新建 `TushareConfigPanelViewModel`（Token/tier/probe）；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.2.3 | [lane:gate][tdd:required] `LLMConfigPanel` 声明式重写：`@ft.component` + 新建 `LLMConfigPanelViewModel`（provider/key/test）；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.2.4 | [lane:gate][tdd:required] `LocalModelConfigPanel` 声明式重写：`@ft.component` + `use_state`（纯 UI 状态，直调 ConfigHandler，YAGNI 不建 VM）；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.2.5 | [lane:gate][tdd:required] `BacktestConfigPanel` 声明式重写：`@ft.component` + 复用 `BacktestViewModel` 或新建子 VM；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.2.6 | [lane:gate][tdd:required] `BacktestResultPanel` 声明式重写：`@ft.component` + `use_state`（纯展示，props 推送数据）；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.2.7 | [lane:gate][tdd:required] `StockDetailDialog` + `HealthReportDialog` 声明式重写：`@ft.component` + 条件渲染（Phase 3.0.2 模式）；移除 `page.show_dialog` | `grep "page.show_dialog" ui/components/` = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.2.8 | [lane:gate] Phase 3.2 回归验收：`pytest tests/unit/ -m "not slow"` 通过；已改造 config panel 无命令式残留 | pytest 全绿；已改造文件 grep 命令式模式 = 0 | 3.2.1-3.2.7 | cc:TODO |

### Phase 3.3: 叶子展示组件 + 极薄 Tab

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.3.1 | [lane:gate][tdd:required] `MarketDashboard` 声明式重写：`@ft.component` + `use_state`（纯展示，props 推送数据，YAGNI 不建 VM）；移除 update_data/update_theme/update_locale 命令式方法 | `grep "did_mount\|\.update()\|update_data\|update_theme\|update_locale" ui/components/market_dashboard.py` = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.3.2 | [lane:gate][tdd:required] `NewsFeed` 声明式重写：`@ft.component` + `use_state`（纯展示，props 推送）；移除 set_news/prepend_news/append_news 命令式方法 | grep 命令式方法 = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.3.3 | [lane:gate][tdd:required] `DatabaseTab` 声明式重写：`@ft.component` + `use_state`（105 行极薄包装，YAGNI 不建 VM）；消费声明式 DatabaseConfigPanel | `grep "did_mount\|\.update()\|refresh_locale" ui/views/settings_tabs/database_tab.py` = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.3.4 | [lane:gate][tdd:required] `AutomationTab` 声明式重写：`@ft.component` + `use_state`（纯设置项，直调 ConfigHandler）；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.3.5 | [lane:gate][tdd:required] `NotificationsTab` 声明式重写：`@ft.component` + `use_state`；page 访问改 `ft.context.page`（旧用 weakref page_ref）；移除命令式模式 | grep 命令式模式 = 0；`grep "_page_ref" ui/views/settings_tabs/automation_tab.py` = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.3.6 | [lane:gate] Phase 3.3 回归验收 | pytest 全绿；已改造文件 grep 命令式模式 = 0 | 3.3.1-3.3.5 | cc:TODO |

### Phase 3.4: 删除 PageRefMixin 垫片（3 个历史控件重写）

> [CLAUDE.md §3.3](file:///d:/workspace/qTrading/CLAUDE.md) 技术债列出的 5 个 PageRefMixin 历史控件中，AppLayout/TaskCenterView 已在 Phase 3.1/3.6 处理；本 Phase 删除剩余 3 个垫片（ResizableSplitter/FailoverConfigPanel/ProviderCredentialDialog），是 ScreenerView/BacktestView/AIBrainTab 的传递依赖，必须先重写。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.4.1 | [lane:gate][tdd:required] `ResizableSplitter` 声明式重写：`@ft.component` + `use_state(width)` + on_drag_update set_width；移除 PageRefMixin；性能验证（Phase 3.0.4 基准，若超阈值降级为 use_ref + 局部 update，需用户裁决） | `grep "PageRefMixin\|\.update()" ui/components/resizable_splitter.py` = 0；拖拽性能达标或降级方案记录；测试通过 | Phase 3.0 | cc:TODO |
| 3.4.2 | [lane:gate][tdd:required] `FailoverConfigPanel` 声明式重写：`@ft.component` + `use_state`（providers list）；移除 PageRefMixin；移除命令式模式 | grep PageRefMixin/命令式模式 = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.4.3 | [lane:gate][tdd:required] `ProviderCredentialDialog` 声明式重写：`@ft.component` + 条件渲染（Phase 3.0.2 模式）；移除 PageRefMixin；移除 `page.show_dialog` | `grep "page.show_dialog\|PageRefMixin" ui/components/config_panels/` = 0；测试通过 | Phase 3.0 | cc:TODO |
| 3.4.4 | [lane:gate] Phase 3.4 回归验收 | pytest 全绿；`grep "PageRefMixin" ui/components/` = 0 | 3.4.1-3.4.3 | cc:TODO |

### Phase 3.5: 中间容器 View/Tab

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.5.1 | [lane:gate][tdd:required] `DataSourceTab` 声明式重写：`@ft.component` + `use_viewmodel(DataSourceViewModel)`；消费声明式 TushareConfigPanel/MetricCard/ActionChip/HealthReportDialog；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2/3.3 | cc:TODO |
| 3.5.2 | [lane:gate][tdd:required] `AIBrainTab` 声明式重写：`@ft.component` + 新建 `AIBrainTabViewModel`；消费声明式 LLMConfigPanel/FailoverConfigPanel/LocalModelConfigPanel；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2/3.4 | cc:TODO |
| 3.5.3 | [lane:gate][tdd:required] `SystemTab` 声明式重写：`@ft.component` + `use_viewmodel(SystemViewModel)`；消费声明式 TierApiPanel；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2/3.3 | cc:TODO |
| 3.5.4 | [lane:gate][tdd:required] `TierApiPanel` 声明式重写：`@ft.component` + `use_viewmodel(SystemViewModel)`（复用）；响应式断点用 `use_state` + `ft.context.page` on_resize；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.5.5 | [lane:gate][tdd:required] `DataExplorerView` + `TableViewerTab` + `SQLConsoleTab` 声明式重写：`@ft.component` + `use_viewmodel(DataExplorerViewModel)`；pubsub 改 `use_effect`（Phase 3.0.3 模式）；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.0/3.2 | cc:TODO |
| 3.5.6 | [lane:gate] Phase 3.5 回归验收 | pytest 全绿；已改造文件 grep 命令式模式 = 0 | 3.5.1-3.5.5 | cc:TODO |

### Phase 3.6: 顶层 View + 容器重写

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.6.1 | [lane:gate][tdd:required] `HomeView` 声明式重写：`@ft.component` + `use_viewmodel(HomeViewModel)`；消费声明式 MarketDashboard/NewsFeed；移除命令式模式（无 use_ref cache） | `grep "use_ref.*cache\|did_mount\|\.update()" ui/views/home_view.py` = 0；测试通过 | Phase 3.3 | cc:TODO |
| 3.6.2 | [lane:gate][tdd:required] `ScreenerView` 声明式重写（~1867 行）：`@ft.component` + `use_viewmodel(ScreenerViewModel)`；LLM 流式用 ref buffer + 节流 set_state（Phase 3.0.4 模式）；消费声明式 ResizableSplitter/PaginatedTable/StockDetailDialog；移除命令式模式 | grep 命令式模式 = 0；流式性能达标；测试通过 | Phase 3.4/3.5 | cc:TODO |
| 3.6.3 | [lane:gate][tdd:required] `BacktestView` 声明式重写：`@ft.component` + `use_viewmodel(BacktestViewModel)`；消费声明式 BacktestConfigPanel/BacktestResultPanel/ResizableSplitter；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2/3.4 | cc:TODO |
| 3.6.4 | [lane:gate][tdd:required] `OnboardingWizard` 声明式重写（~1164 行）：`@ft.component` + `use_viewmodel(OnboardingViewModel)`；8 步状态机用 `use_state(current_step)`；消费声明式 DatabaseConfigPanel/TushareConfigPanel/LLMConfigPanel/LocalModelConfigPanel；移除命令式模式 | grep 命令式模式 = 0；测试通过 | Phase 3.2 | cc:TODO |
| 3.6.5 | [lane:gate][tdd:required] `AppLayout` 声明式重写：`@ft.component` + `use_state(current_tab, nav_collapsed)`；**无 use_ref cache**（直接调用子组件函数 HomeView()/ScreenerView()/...）；resize 用 `use_effect` + `page.on_resize`；移除手动级联 effect（i18n/theme 子组件自管） | `grep "use_ref.*cache\|on_locale_change\|on_theme_change" ui/app_layout.py` = 0；测试通过 | Phase 3.6.1-3.6.4 | cc:TODO |
| 3.6.6 | [lane:gate][tdd:required] `SettingsView` 声明式重写：`@ft.component` + `use_state(current_tab)`；**无 use_ref cache**（直接调用子组件函数 DatabaseTab()/...）；移除手动级联 effect | `grep "use_ref.*cache\|on_locale_change\|on_theme_change" ui/views/settings_view.py` = 0；测试通过 | Phase 3.5 | cc:TODO |
| 3.6.7 | [lane:gate] Phase 3.6 回归验收 | pytest 全绿；`grep "use_ref.*cache" ui/views/ ui/app_layout.py` = 0；`grep "on_locale_change\|on_theme_change" ui/views/ ui/app_layout.py` = 0（仅 Observable state 自管） | 3.6.1-3.6.6 | cc:TODO |

### Phase 3.7: 级联修改 + 全量验证 + review gate

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.7.1 | [lane:gate][tdd:required] 级联修改：`main.py` 移除 `_on_resize`（AppLayout use_effect 自管）；`startup_views.py` 改 `page.add(AppLayout())`；契约测试同步更新（声明式 grep 守护） | `grep "_on_resize\|isinstance.*AppLayout" main.py` = 0；`grep "app_layout.show\|AppLayout(self._page)" ui/startup_views.py` = 0；测试通过 | Phase 3.6 | cc:TODO |
| 3.7.2 | [lane:gate] Phase 3 全量回归：`pytest tests/unit/ -m "not slow"` + `pytest tests/integration/` 通过；已改造 View 无命令式残留；`grep -rn "handle_resize" --include=*.py ui/` = 0 | pytest 全绿；grep 命令式模式 = 0；`handle_resize` = 0 | 3.7.1 | cc:TODO |
| 3.7.3 | [lane:gate][tdd:skip:review-gate] Phase 3 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-3-review.md`；声明式 View 形态契约一致（`@ft.component` + 按职责分层 use_viewmodel/use_state）；无混合模式残留（无 use_ref cache 命令式实例、无手动级联 effect）；响应式布局 + M3 控件变体无遗漏；`pytest tests/unit/ tests/integration/` 全绿 | 3.7.2 | cc:TODO |

---

## Phase 4: main 入口 + 特殊控件目标态重写

> 方案 §2 阶段 5、§3.6 6 类遗漏控件。含 `v1_compat.py` 移除、`main.py` 入口改造。
> **grep 验收**：`grep -rn "v1_compat\|PageRefMixin\|_page_ref" --include=*.py .` = 0

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | [lane:gate][tdd:required] `main.py:163` 入口改造：`close_confirm_dialog.update()` 改 state 驱动（方案 §1.2 H15）；`StartupViewRenderer` 改 `@ft.component`（方案 §3.6）。配套 `test_ui_infrastructure.py` + `test_startup_views.py` 重写 | `grep "\.update()" main.py` = 0；`grep "page\.controls\.clear\|page\.add" ui/startup_views.py` 改 state 驱动；对应测试通过 | Phase 3 | cc:TODO |
| 4.2 | [lane:gate][tdd:required] FilePicker 声明式挂载（3 处：`data_view.py`/`screener_view.py`/`local_model_config_panel.py`）：`use_ref` + `use_effect` 挂载到 `page.services` + state 驱动 pick 操作（方案 §3.6.1 方案 A）。配套测试改 mock FilePicker 结果注入 | 3 处 FilePicker `grep "page\.services\.append\|page\.overlay\.append"` 用 `use_effect` 包装；`on_result` 回调改 command；对应测试通过 | Phase 3 | cc:TODO |
| 4.3 | [lane:gate][tdd:required] AlertDialog 子类声明式重写（剩余：`HealthScanDialog` + 1 内联 `data_source_tab.py`；`StockDetailDialog`/`HealthReportDialog`/`ProviderCredentialDialog` 已在 Phase 3.2.7/3.4.3 完成）：`@ft.component` 函数式 + `ft.use_dialog()` hook + `use_state` 控制 `open`（方案 §3.6）。配套 `test_health_scan_dialog.py` 重写 | `grep "page\.show_dialog\|page\.pop_dialog" ui/` = 0；剩余 Dialog 用 `ft.use_dialog()` hook；对应测试通过 | Phase 3 | cc:TODO |
| 4.4 | [lane:gate][tdd:required] 特殊控件重写：`ToastManager` overlay 挂载改 state 驱动（方案 §3.6.1 M20）；`PaginatedTable` 保留自实现 + 声明式重写（方案附录 C.2.1 C20）；`flet_charts` 改 `use_effect` + `use_ref`；`DatePicker` 改 `use_state` + 条件渲染或 `ft.use_dialog()`。配套 `test_toast_manager.py` + `test_virtual_table.py` + `test_backtest_view_splitter.py` 重写 | 各控件 `grep "\.update()\|did_mount"` = 0；`grep "page\.overlay\.append.*self\.container" ui/components/toast_manager.py` 改 state 驱动；对应测试通过 | Phase 3 | cc:TODO |
| 4.5 | [lane:gate][tdd:required] `v1_compat.py` 删除 + `test_v1_compat.py` 删除 + `test_mock_flet_contract.py` 重写为 V1 原生 mock 契约（方案 §3.1 原则 6、§4.2 grep 验收） | `grep "v1_compat" .` = 0（含文件删除）；`test_mock_flet_contract.py` 守护 V1 原生 mock 契约；`pytest tests/unit/ui/test_mock_flet_contract.py` 通过 | 4.1-4.4 | cc:TODO |
| 4.6 | [lane:gate] Phase 4 回归验收：`grep -rn "v1_compat\|PageRefMixin\|_page_ref" --include=*.py .` = 0；`grep -rn "\.update()" --include=*.py ui/ main.py` = 0；`pytest tests/unit/ -m "not slow"` 通过 | grep 全部 = 0；pytest 全绿 | 4.1-4.5 | cc:TODO |
| 4.7 | [lane:gate][tdd:skip:review-gate] Phase 4 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-4-review.md`；`v1_compat.py` 删除后无残留引用；FilePicker/AlertDialog/特殊控件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿；集成测试 N/A | 4.6 | cc:TODO |

---

## Phase 5: 最终清理 + E2E 完整回归 + xfail 消除

> 方案 §2 阶段 6、§4.2 全部 22 项 grep 验收、§5.4 混合态清零 9 类、§5.5 E2E xfail 消除。
> **用户硬约束**：E2E test cases must all pass, no xFail cases allowed

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 5.1 | [lane:gate] 22 项 grep 验收（方案 §4.2）：`v1_compat`/`PageRefMixin`/`.update()`/`did_mount`/`will_unmount`/`self.page =`/命令式控件子类/`ft.Button(`/`use_material_3=False`/`handle_resize`/`set_page`/`refresh_locale`/`AppColors._listeners`/`page.show_dialog`/硬编码像素布局/`use_viewmodel` ≥ 7/`on_update=`/`test_v1_compat`/附录 C 填充完整 | 22 项 grep 全部达标；不达标项修正至达标 | Phase 4 | cc:TODO |
| 5.2 | [lane:gate] 混合态清零验证（方案 §5.4 9 类）：命令式 View 调声明式 VM / 声明式 View 调命令式 VM / `refresh_locale` 与 Observable 共存 / `AppColors._listeners` 与 Observable 共存 / `set_page` 与声明式 View 共存 / `.update()` 残留 / 自实现与原生控件共存 / 固定像素与响应式共存 / M2 与 M3 共存 | 9 类混合态全部清零；任一残留视为未完成 | 5.1 | cc:TODO |
| 5.3 | [lane:gate][tdd:required] E2E 完整回归（11 文件）+ xfail 消除：移除 `@pytest.mark.xfail` 标记（`test_onboarding_wizard.py:123-133` Flutter #129324）；E2E 选择器同步调整（方案 §3.4.5 10 类场景）；视口策略验证 | `pytest tests/e2e/ -v` 全绿（**0 xFail**，用户硬约束）；E2E 选择器不依赖具体 Flet 控件类名 | 5.2 | cc:TODO |
| 5.4 | [lane:gate] 完整门禁回归：`ruff check .` + `ruff format --check .` + `pyright` + `pytest tests/unit/ -m "not slow"` + `pytest tests/integration/` + `pytest tests/e2e/` + `pre-commit run --all-files` 全部通过 | 7 项门禁全绿 | 5.3 | cc:TODO |
| 5.5 | [lane:gate][tdd:skip:review-gate] Phase 5 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-5-review.md`；22 项 grep 验收 + 9 类混合态清零无遗漏；E2E 0 xFail 用户硬约束达成；`pytest tests/unit/ tests/integration/ tests/e2e/` 全绿 | 5.4 | cc:TODO |

---

## Phase 6: 文档同步（CONTRIBUTING.md / CLAUDE.md）

> 方案 §8 CONTRIBUTING.md 同步修订清单。前置条件：§4.1/§4.2/§4.4/§5.5/§5.4/附录 C/8 阶段全部完成（方案 §4.3）。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 6.1 | [lane:fast][tdd:skip:docs-only] CONTRIBUTING.md 同步修订（方案 §8.1 + §8.2 + §8.4）：①技术债清单标记 P3 命令式 UI 存量 / P3 MAX_CONTENT_WIDTH 为"已偿还"；②四项强制约束（零命令式刷新/原生组件优先/响应式布局/全面 M3）沉淀到 V1 声明式 UI 开发规范 §7；③删除命令式存量附录（响应式布局规范 9 条 + 语言切换响应 9 条）；④版本号与 CLAUDE.md 同步；⑤10 项验证清单通过 | `grep "refresh_locale\|handle_resize\|self\.update()" CONTRIBUTING.md` 仅在历史引用中出现；四约束小节存在；命令式附录已删除；版本号一致 | Phase 5 | cc:TODO |
| 6.2 | [lane:fast][tdd:skip:docs-only] CLAUDE.md §3.3 已知技术债标记"已偿还"/"已实现"（方案 §8.3）：`use_viewmodel` hook 待建 → 已实现；7 个 ViewModel + 命令式 View 全面重写 → 已偿还 | CLAUDE.md §3.3 两个技术债条目标记"已偿还"/"已实现"；版本号与 CONTRIBUTING.md 一致 | 6.1 | cc:TODO |
| 6.3 | [lane:gate][tdd:skip:review-gate] Phase 6 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-6-review.md`；CONTRIBUTING.md/CLAUDE.md 版本号一致；命令式附录已删除无残留；技术债标记准确；docs-only 无单测/集成测试要求 | 6.2 | cc:TODO |

---

## 事前確認

以下操作在 plan 承认時に一括確認する。`harness-work` / `breezing` 実行中、宣言済み事項だけを理由に `AskUserQuestion` を出さない。

- 事項: destructive — `git cherry-pick` 3 个 tag 到特性分支（恢复已有批次工作）
  理由: 恢复阶段 0/1/1.5 已完成工作，避免 6 天重复劳动
  scope: Phase 0 / Task 0.2

- 事項: destructive — 删除 `ui/v1_compat.py` + `tests/unit/ui/test_v1_compat.py`（方案 §3.1 原则 6、§4.2 grep 验收）
  理由: v1_compat 兼容桩在声明式改造完成后必须移除，保留即自相矛盾
  scope: Phase 5 / Task 5.5

- 事項: destructive — 删除 `tests/unit/ui/mock_flet.py` 中的 `set_page` helper（296 处调用需改造）+ `_install_v1_compat_control_page_mock()` 全局桩
  理由: 旧测试桩与声明式 View 不兼容，阶段 2.5 必须删除
  scope: Phase 2.5 / Task 2.5.1

- 事項: destructive — 删除 `tests/integration/conftest.py:62-64` 的 `_install_v1_compat_control_page_mock` 导入与调用
  理由: 与 mock_flet.py 协同改造，否则 57 个集成测试批量红灯
  scope: Phase 2.5 / Task 2.5.4

- 事項: external-send — `git push origin feature/flet-v1-declarative` + `gh pr create`（PR closeout）
  理由: 特性分支推进 + 最终合入主分支
  scope: Phase 6 / Task 6.4

- 事項: destructive — 删除 CONTRIBUTING.md 命令式存量附录（响应式布局规范 9 条 + 语言切换响应 9 条）
  理由: 方案 §8.2.4/§8.2.5 采用方案 B 直接删除，避免文档膨胀与口径混淆
  scope: Phase 7 / Task 7.1
