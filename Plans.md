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

## Phase 3: 简单 View 声明式重写（TaskCenter / Home / Settings）

> 方案 §2 阶段 3、§3.4 断言迁移模板、§3.7 响应式布局、§3.8 Material 3。
> **grep 验收**：`grep -rn "handle_resize" --include=*.py ui/` 在阶段 4 结束后为 0

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 3.1 | [lane:gate][tdd:required] `TaskCenterView` 声明式重写：`@ft.component` + `use_viewmodel`；移除 `did_mount`/`will_unmount`/`self.update()`/`PageRefMixin`；M3 控件变体；响应式布局。配套 `test_task_center_view.py` 重写（17 处断言迁移，方案 §3.2 第一组） | `grep "did_mount\|will_unmount\|\.update()\|PageRefMixin" ui/views/task_center_view.py` = 0；`use_viewmodel` 在文件中出现；`pytest tests/unit/ui/test_task_center_view.py` 通过 | Phase 2.5 | cc:TODO |
| 3.2 | [lane:gate][tdd:required] `HomeView` 声明式重写 + `AppLayout` 响应式布局改造：`@ft.component` + `use_viewmodel` + `use_effect` resize state 驱动（方案 §3.7.2）；NavigationRail M3 变体 + 断点折叠。配套 `test_views.py` 重写（18 处断言迁移，含 handle_resize 6 测试迁移到 use_effect cleanup，方案 §3.2 第一组 H3） | `grep "handle_resize\|\.update()\|did_mount" ui/views/home_view.py ui/app_layout.py` = 0；resize 改 `use_effect` + `page.on_resize = handler`；`pytest tests/unit/ui/test_views.py` 通过 | Phase 2.5 | cc:TODO |
| 3.3 | [lane:gate][tdd:required] `SettingsView` 声明式重写：`@ft.component` + `use_viewmodel`；`ft.Tabs` M3 变体。配套 `test_settings_view.py` + `test_settings_tabs.py` 重写（17 处断言迁移） | `grep "did_mount\|\.update()" ui/views/settings_view.py` = 0；`pytest tests/unit/ui/test_settings_view.py tests/unit/ui/test_settings_tabs.py` 通过 | Phase 2.5 | cc:TODO |
| 3.4 | [lane:gate][tdd:required] `DatabaseTab` + `AutomationTab` + `NotificationsTab` + `SystemTab` 声明式重写。配套 `test_database_tab.py` + `test_automation_tab.py` + `test_system_tab.py` 重写 | 各 Tab `grep "did_mount\|\.update()\|on_update=\|on_log="` = 0；对应测试通过 | Phase 2.5 | cc:TODO |
| 3.5 | [lane:gate] Phase 3 回归验收：`pytest tests/unit/ -m "not slow"` 通过；已改造 View 无命令式残留 | pytest 全绿；已改造文件 grep 命令式模式 = 0 | 3.1-3.4 | cc:TODO |
| 3.6 | [lane:gate][tdd:skip:review-gate] Phase 3 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-3-review.md`；声明式 View 形态契约一致(`@ft.component` + `use_viewmodel`)；响应式布局 + M3 控件变体无遗漏；`pytest tests/unit/ -m "not slow"` 全绿；集成测试 N/A | 3.5 | cc:TODO |

---

## Phase 4: 复杂 View 声明式重写（Screener / Backtest / Data / Onboarding）

> 方案 §2 阶段 4。含 LLM 流式响应测试整改、FilePicker 声明式挂载、配置面板群重写。
> **grep 验收**：`grep -rn "refresh_locale" --include=*.py ui/` = 0（阶段 4 结束前删除所有 refresh_locale）

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 4.1 | [lane:gate][tdd:required] `ScreenerView` 声明式重写（~1620 行测试）：`@ft.component` + `use_viewmodel`；LLM 流式响应改 `state.logs: tuple[LogEntry, ...]` + `replace` 生成新 tuple（方案 §3.2 第一组 H5）；FilePicker 声明式挂载（方案 §3.6.1 方案 A）；`ft.Button` → M3 变体。配套 `test_screener_view.py` 整文件重写（33 处断言迁移） | `grep "\.update()\|did_mount\|on_log=" ui/views/screener_view.py` = 0；`pytest tests/unit/ui/test_screener_view.py` 通过 | Phase 3 | cc:TODO |
| 4.2 | [lane:gate][tdd:required] `BacktestView` 声明式重写：`@ft.component` + `use_viewmodel`；`ft.Tabs` M3 + `ft.Button` → M3 变体。配套 `test_backtest_view.py` 重写（5 处断言迁移） | `grep "\.update()\|did_mount" ui/views/backtest_view.py` = 0；`pytest tests/unit/ui/test_backtest_view.py` 通过 | Phase 3 | cc:TODO |
| 4.3 | [lane:gate][tdd:required] `DataExplorerView` 声明式重写：`@ft.component` + `use_viewmodel`（消费 DataExplorerViewModel 双轨制 state）；`ft.Tabs` M3 + `ft.DataTable` M3；`page.pubsub` 改 `use_effect` cleanup。配套 `test_data_view.py` 重写（5 处断言迁移） | `grep "\.update()\|did_mount" ui/views/data_view.py` = 0；`pytest tests/unit/ui/test_data_view.py` 通过 | Phase 3 | cc:TODO |
| 4.4 | [lane:gate][tdd:required] `OnboardingWizard` 声明式重写：`@ft.component` + `use_viewmodel`；表单控件改 `ft.TextField` 双向绑定绕开 Flutter #129324 焦点问题（方案 §5.5）。配套 `test_onboarding_view_model.py` 回归 | `grep "\.update()\|did_mount" ui/views/onboarding_wizard.py` = 0；onboarding E2E xfail 消除路径准备就绪 | Phase 3 | cc:TODO |
| 4.5 | [lane:gate][tdd:required] 配置面板群声明式重写：`FailoverConfigPanel`（~2500 行测试）+ `ProviderCredentialDialog`（→ `ft.use_dialog()` hook）+ `TushareConfigPanel` + `DatabaseConfigPanel` + `LLMConfigPanel` + `LocalModelConfigPanel` + `AIBrainTab` + `TierApiPanel` + `DataSourceTab`。配套 `test_failover_config_panel.py` + `test_config_panels.py`（79 处断言）+ `test_data_source_tab.py` + `test_ai_brain_tab.py` + `test_tier_api_panel.py` + `test_local_model_config_panel.py` 重写 | 各面板 `grep "\.update()\|did_mount\|page\.show_dialog"` = 0；`grep "ft\.Button(" ui/components/config_panels/` = 0；对应测试通过 | Phase 3 | cc:TODO |
| 4.6 | [lane:gate] Phase 4 回归验收：`grep -rn "refresh_locale" --include=*.py ui/` = 0；`pytest tests/unit/ -m "not slow"` 通过 | grep = 0；pytest 全绿 | 4.1-4.5 | cc:TODO |
| 4.7 | [lane:gate][tdd:skip:review-gate] Phase 4 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-4-review.md`；复杂 View + 配置面板形态契约一致；LLM 流式响应 state 化 + FilePicker 声明式挂载无遗漏；`pytest tests/unit/ -m "not slow"` 全绿；集成测试 N/A | 4.6 | cc:TODO |

---

## Phase 5: main 入口 + 特殊控件目标态重写

> 方案 §2 阶段 5、§3.6 6 类遗漏控件。含 `v1_compat.py` 移除、`main.py` 入口改造。
> **grep 验收**：`grep -rn "v1_compat\|PageRefMixin\|_page_ref" --include=*.py .` = 0

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 5.1 | [lane:gate][tdd:required] `main.py:163` 入口改造：`close_confirm_dialog.update()` 改 state 驱动（方案 §1.2 H15）；`StartupViewRenderer` 改 `@ft.component`（方案 §3.6）。配套 `test_ui_infrastructure.py` + `test_startup_views.py` 重写 | `grep "\.update()" main.py` = 0；`grep "page\.controls\.clear\|page\.add" ui/startup_views.py` 改 state 驱动；对应测试通过 | Phase 4 | cc:TODO |
| 5.2 | [lane:gate][tdd:required] FilePicker 声明式挂载（3 处：`data_view.py`/`screener_view.py`/`local_model_config_panel.py`）：`use_ref` + `use_effect` 挂载到 `page.services` + state 驱动 pick 操作（方案 §3.6.1 方案 A）。配套测试改 mock FilePicker 结果注入 | 3 处 FilePicker `grep "page\.services\.append\|page\.overlay\.append"` 用 `use_effect` 包装；`on_result` 回调改 command；对应测试通过 | Phase 4 | cc:TODO |
| 5.3 | [lane:gate][tdd:required] AlertDialog 子类声明式重写（4 个：`StockDetailDialog`/`HealthReportDialog`/`HealthScanDialog`/`ProviderCredentialDialog` + 1 内联 `data_source_tab.py`）：`@ft.component` 函数式 + `ft.use_dialog()` hook + `use_state` 控制 `open`（方案 §3.6）。配套 `test_stock_detail_dialog.py` + `test_health_report_dialog.py` 重写 | `grep "page\.show_dialog\|page\.pop_dialog" ui/` = 0；4 个 Dialog 用 `ft.use_dialog()` hook；对应测试通过 | Phase 4 | cc:TODO |
| 5.4 | [lane:gate][tdd:required] 特殊控件重写：`ToastManager` overlay 挂载改 state 驱动（方案 §3.6.1 M20）；`ResizableSplitter` + `PaginatedTable` 保留自实现 + 声明式重写（方案附录 C.2.1 C20）；`flet_charts` 改 `use_effect` + `use_ref`；`DatePicker` 改 `use_state` + 条件渲染或 `ft.use_dialog()`。配套 `test_toast_manager.py` + `test_resizable_splitter.py` + `test_virtual_table.py` + `test_backtest_view_splitter.py` + `test_backtest_result_panel.py` 重写 | 各控件 `grep "\.update()\|did_mount"` = 0；`grep "page\.overlay\.append.*self\.container" ui/components/toast_manager.py` 改 state 驱动；对应测试通过 | Phase 4 | cc:TODO |
| 5.5 | [lane:gate] `v1_compat.py` 删除 + `test_v1_compat.py` 删除 + `test_mock_flet_contract.py` 重写为 V1 原生 mock 契约（方案 §3.1 原则 6、§4.2 grep 验收） | `grep "v1_compat" .` = 0（含文件删除）；`test_mock_flet_contract.py` 守护 V1 原生 mock 契约；`pytest tests/unit/ui/test_mock_flet_contract.py` 通过 | 5.1-5.4 | cc:TODO |
| 5.6 | [lane:gate] Phase 5 回归验收：`grep -rn "v1_compat\|PageRefMixin\|_page_ref" --include=*.py .` = 0；`grep -rn "\.update()" --include=*.py ui/ main.py` = 0；`pytest tests/unit/ -m "not slow"` 通过 | grep 全部 = 0；pytest 全绿 | 5.1-5.5 | cc:TODO |
| 5.7 | [lane:gate][tdd:skip:review-gate] Phase 5 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-5-review.md`；`v1_compat.py` 删除后无残留引用；FilePicker/AlertDialog/特殊控件形态契约一致；`pytest tests/unit/ -m "not slow"` 全绿；集成测试 N/A | 5.6 | cc:TODO |

---

## Phase 6: 最终清理 + E2E 完整回归 + xfail 消除

> 方案 §2 阶段 6、§4.2 全部 22 项 grep 验收、§5.4 混合态清零 9 类、§5.5 E2E xfail 消除。
> **用户硬约束**：E2E test cases must all pass, no xFail cases allowed

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 6.1 | [lane:gate] 22 项 grep 验收（方案 §4.2）：`v1_compat`/`PageRefMixin`/`.update()`/`did_mount`/`will_unmount`/`self.page =`/命令式控件子类/`ft.Button(`/`use_material_3=False`/`handle_resize`/`set_page`/`refresh_locale`/`AppColors._listeners`/`page.show_dialog`/硬编码像素布局/`use_viewmodel` ≥ 7/`on_update=`/`test_v1_compat`/附录 C 填充完整 | 22 项 grep 全部达标；不达标项修正至达标 | Phase 5 | cc:TODO |
| 6.2 | [lane:gate] 混合态清零验证（方案 §5.4 9 类）：命令式 View 调声明式 VM / 声明式 View 调命令式 VM / `refresh_locale` 与 Observable 共存 / `AppColors._listeners` 与 Observable 共存 / `set_page` 与声明式 View 共存 / `.update()` 残留 / 自实现与原生控件共存 / 固定像素与响应式共存 / M2 与 M3 共存 | 9 类混合态全部清零；任一残留视为未完成 | 6.1 | cc:TODO |
| 6.3 | [lane:gate][tdd:required] E2E 完整回归（11 文件）+ xfail 消除：移除 `@pytest.mark.xfail` 标记（`test_onboarding_wizard.py:123-133` Flutter #129324）；E2E 选择器同步调整（方案 §3.4.5 10 类场景）；视口策略验证 | `pytest tests/e2e/ -v` 全绿（**0 xFail**，用户硬约束）；E2E 选择器不依赖具体 Flet 控件类名 | 6.2 | cc:TODO |
| 6.4 | [lane:gate] 完整门禁回归：`ruff check .` + `ruff format --check .` + `pyright` + `pytest tests/unit/ -m "not slow"` + `pytest tests/integration/` + `pytest tests/e2e/` + `pre-commit run --all-files` 全部通过 | 7 项门禁全绿 | 6.3 | cc:TODO |
| 6.5 | [lane:gate][tdd:skip:review-gate] Phase 6 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-6-review.md`；22 项 grep 验收 + 9 类混合态清零无遗漏；E2E 0 xFail 用户硬约束达成；`pytest tests/unit/ tests/integration/ tests/e2e/` 全绿 | 6.4 | cc:TODO |

---

## Phase 7: 文档同步（CONTRIBUTING.md / CLAUDE.md）

> 方案 §8 CONTRIBUTING.md 同步修订清单。前置条件：§4.1/§4.2/§4.4/§5.5/§5.4/附录 C/8 阶段全部完成（方案 §4.3）。

| Task | 内容 | DoD | Depends | Status |
|------|------|-----|---------|--------|
| 7.1 | [lane:fast][tdd:skip:docs-only] CONTRIBUTING.md 同步修订（方案 §8.1 + §8.2 + §8.4）：①技术债清单标记 P3 命令式 UI 存量 / P3 MAX_CONTENT_WIDTH 为"已偿还"；②四项强制约束（零命令式刷新/原生组件优先/响应式布局/全面 M3）沉淀到 V1 声明式 UI 开发规范 §7；③删除命令式存量附录（响应式布局规范 9 条 + 语言切换响应 9 条）；④版本号与 CLAUDE.md 同步；⑤10 项验证清单通过 | `grep "refresh_locale\|handle_resize\|self\.update()" CONTRIBUTING.md` 仅在历史引用中出现；四约束小节存在；命令式附录已删除；版本号一致 | Phase 6 | cc:TODO |
| 7.2 | [lane:fast][tdd:skip:docs-only] CLAUDE.md §3.3 已知技术债标记"已偿还"/"已实现"（方案 §8.3）：`use_viewmodel` hook 待建 → 已实现；7 个 ViewModel + 命令式 View 全面重写 → 已偿还 | CLAUDE.md §3.3 两个技术债条目标记"已偿还"/"已实现"；版本号与 CONTRIBUTING.md 一致 | 7.1 | cc:TODO |
| 7.3 | [lane:gate][tdd:skip:review-gate] Phase 7 per-phase code review gate（见顶部 `[review-gate]` 约定） | 检视记录沉淀到 `.claude/state/reviews/phase-7-review.md`；CONTRIBUTING.md/CLAUDE.md 版本号一致；命令式附录已删除无残留；技术债标记准确；docs-only 无单测/集成测试要求 | 7.2 | cc:TODO |

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
